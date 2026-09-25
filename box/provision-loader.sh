#!/usr/bin/env bash
# Provisions a loader box: wrk2 at a pinned commit, h2load from the nghttp2 package, the kernel knobs,
# and a clock check.
#
# Results:
#   /usr/local/bin/wrk2, /usr/bin/h2load
#   /opt/bench/loader.json   the instance id, the wrk2 commit and version line, and the h2load version line
set -euo pipefail

WRK2_COMMIT=${WRK2_COMMIT:-44a94c17d8e6a0bac8559b53da76848e430cb7a7}

BENCH=/opt/bench
BCSAVE=deps/luajit/src/jit/bcsave.lua

build_wrk2() {
  local src=$HOME/wrk2-src
  if [ -x /usr/local/bin/wrk2 ] && [ "$(cat "$BENCH/wrk2.commit" 2>/dev/null)" = "$WRK2_COMMIT" ]; then
    echo "==> wrk2 already built at $WRK2_COMMIT"
    return 0
  fi
  rm -rf "$src"
  git clone -q https://github.com/giltene/wrk2 "$src"
  git -C "$src" checkout -q "$WRK2_COMMIT"
  # The ELF string table of obj/bytecode.o holds a zero byte, the symbol name, and a zero byte.
  # The stock size #symname+1 truncates luaJIT_BC_wrk on current binutils, and every -s script run panics.
  sed -i 's/o\.sect\[3\]\.size = fofs(#symname+1)/o.sect[3].size = fofs(#symname+2)/' "$src/$BCSAVE"
  if ! grep -qF 'o.sect[3].size = fofs(#symname+2)' "$src/$BCSAVE"; then
    echo "ERROR: the $BCSAVE fix did not apply"
    exit 1
  fi
  make -s -C "$src" -j"$(nproc)"
  if ! nm "$src/obj/bytecode.o" | awk '$3 == "luaJIT_BC_wrk" { found = 1 } END { exit !found }'; then
    echo "ERROR: $src/obj/bytecode.o has no luaJIT_BC_wrk symbol"
    exit 1
  fi
  sudo install -m 0755 "$src/wrk" /usr/local/bin/wrk2
  echo "$WRK2_COMMIT" >"$BENCH/wrk2.commit"
}

system_knobs() {
  sudo tee /etc/sysctl.d/90-rapira-bench.conf >/dev/null <<'CONF'
net.ipv4.ip_local_port_range = 1024 65000
CONF
  sudo sysctl -q -p /etc/sysctl.d/90-rapira-bench.conf
  sudo tee /etc/security/limits.d/90-rapira-bench.conf >/dev/null <<'CONF'
fedora soft nofile 1048576
fedora hard nofile 1048576
CONF
}

# The loaders start each stage at a shared wall-clock time, so every box needs a synchronized clock.
check_clock() {
  sudo systemctl enable --now chronyd
  if ! chronyc waitsync 60 0.01 0 1 >/dev/null; then
    echo "ERROR: chrony is not synchronized after 60 s"
    chronyc tracking
    exit 1
  fi
  if ! chronyc tracking | grep -q '^Leap status *: Normal$'; then
    echo "ERROR: the chrony leap status is not Normal"
    chronyc tracking
    exit 1
  fi
}

# instance_id prints the EC2 instance id from the IMDSv2 endpoint.
instance_id() {
  local token
  token=$(curl -fsS -X PUT -H "X-aws-ec2-metadata-token-ttl-seconds: 60" http://169.254.169.254/latest/api/token)
  curl -fsS -H "X-aws-ec2-metadata-token: $token" http://169.254.169.254/latest/meta-data/instance-id
}

write_record() {
  local id wrk2_line h2load_line
  id=$(instance_id)
  # wrk2 --version prints the version line and exits 1.
  wrk2_line=$(wrk2 --version 2>/dev/null | sed -n 1p || true)
  h2load_line=$(h2load --version | sed -n 1p)
  python3 - "$BENCH/loader.json" "$id" "$WRK2_COMMIT" "$wrk2_line" "$h2load_line" <<'PY'
import json, sys

path, instance_id, commit, wrk2, h2load = sys.argv[1:6]
with open(path, "w") as f:
    json.dump({"instance_id": instance_id, "wrk2_commit": commit, "wrk2_version": wrk2, "h2load_version": h2load}, f, indent=1)
    f.write("\n")
PY
}

echo "==> packages"
sudo dnf -y install gcc make git openssl-devel zlib-devel binutils ethtool curl tar diffutils python3 chrony nghttp2
sudo install -d -o fedora -g fedora "$BENCH"
echo "==> system knobs"
system_knobs
echo "==> clock"
check_clock
echo "==> wrk2 $WRK2_COMMIT"
build_wrk2
write_record
echo "==> loader provisioned: $(tr -d '\n' <"$BENCH/loader.json")"
