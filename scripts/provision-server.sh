#!/usr/bin/env bash
# Runs on the server box as user fedora (ssh-driven by `make provision`).
# Env: REF (required; branch, tag, sha, or pr/N), BASE_REF, LEGS (rapira|all),
# PLAIN=1 drops frame pointers (publishable fleet tables).
set -euo pipefail

BASE_REF=${BASE_REF:-main}
REF=${REF:?set REF (branch, tag, sha, or pr/N)}
LEGS=${LEGS:-rapira}
PLAIN=${PLAIN:-0}
CORE_REPO=${CORE_REPO:-https://github.com/rapira-rs/rapira}
FRANKEN_VERSION=${FRANKEN_VERSION:-1.12.4}
RR_VERSION=${RR_VERSION:-2025.1.15}
SWOOLE_VERSION=${SWOOLE_VERSION:-6.2.2}

BENCH=/opt/bench
CORE=$HOME/core
# shellcheck source=scripts/build-lib.sh
. "$(dirname "$0")/build-lib.sh"

echo "==> packages"
pkgs="php-cli php-devel php-embedded php-opcache clang clang-devel gcc make git perf ethtool curl tar python3"
# php-opcache is a separate Fedora package and its silent absence would
# corrupt exactly the classic-vs-fpm comparison; bindgen needs clang-devel.
[ "$LEGS" = all ] && pkgs="$pkgs nginx php-fpm composer autoconf automake libtool openssl-devel"
# shellcheck disable=SC2086
sudo dnf -y install $pkgs
php -r 'exit(ini_get("opcache.enable") ? 0 : 1);' || { echo "ERROR: opcache installed but off"; exit 1; }

echo "==> system knobs"
sudo tee /etc/sysctl.d/90-rapira-bench.conf >/dev/null <<'EOF'
kernel.perf_event_paranoid = -1
kernel.kptr_restrict = 0
net.core.somaxconn = 65535
EOF
sudo sysctl -q -p /etc/sysctl.d/90-rapira-bench.conf
sudo tee /etc/security/limits.d/90-rapira-bench.conf >/dev/null <<'EOF'
fedora soft nofile 1048576
fedora hard nofile 1048576
EOF

# The AMI root is 5 GiB; cloud-init growpart must have expanded it before the
# builds, or cargo dies with ENOSPC after minutes of billed time.
root_kb=$(df -Pk / | awk 'NR==2 {print $2}')
if [ "$root_kb" -lt $((30 * 1024 * 1024)) ]; then
  echo "ERROR: root filesystem is $((root_kb / 1024 / 1024)) GiB; expected >= 30 GiB"
  exit 1
fi

echo "==> rust toolchain"
if [ ! -x "$HOME/.cargo/bin/cargo" ]; then
  curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal
fi
# shellcheck disable=SC1091
. "$HOME/.cargo/env"

sudo install -d -o fedora -g fedora $BENCH $BENCH/bin $BENCH/log $BENCH/run $BENCH/perf $BENCH/fleet

echo "==> core checkout"
if [ ! -d "$CORE/.git" ]; then
  git clone "$CORE_REPO" "$CORE"
fi
git -C "$CORE" fetch origin --tags --prune

resolve_ref() {
  local r=$1
  case "$r" in
  pr/*)
    git -C "$CORE" fetch -q origin "refs/pull/${r#pr/}/head"
    git -C "$CORE" rev-parse FETCH_HEAD
    ;;
  *)
    git -C "$CORE" rev-parse --verify -q "origin/$r^{commit}" 2>/dev/null ||
      git -C "$CORE" rev-parse --verify "$r^{commit}"
    ;;
  esac
}

rustflags=$(rustflags_for "$PLAIN")

build_one() {
  local tag=$1 sha=$2
  local marker=$BENCH/run/build-$tag.marker
  if [ -f "$marker" ] && [ "$(cat "$marker")" = "$sha|$rustflags" ] && [ -x "$BENCH/bin/rapira-$tag" ]; then
    echo "==> rapira-$tag already built at $sha"
    return 0
  fi
  echo "==> build rapira-$tag at $sha"
  git -C "$CORE" checkout -q "$sha"
  build_rapira "$CORE" "$tag" "$rustflags"
  echo "$sha|$rustflags" >"$marker"
}

base_sha=$(resolve_ref "$BASE_REF")
pr_sha=$(resolve_ref "$REF")
# Base first: the pr build then reuses the shared dependency cache.
build_one base "$base_sha"
build_one pr "$pr_sha"

echo "==> metadata"
python3 - "$base_sha" "$pr_sha" "$BASE_REF" "$REF" "$rustflags" <<'PY'
import hashlib, json, platform, subprocess, sys

def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

base_sha, pr_sha, base_ref, ref, rustflags = sys.argv[1:6]
meta = {
    "base_ref": base_ref,
    "base_sha": base_sha,
    "pr_ref": ref,
    "pr_sha": pr_sha,
    "base_sha256": sha256("/opt/bench/bin/rapira-base"),
    "pr_sha256": sha256("/opt/bench/bin/rapira-pr"),
    "base_rustflags": rustflags,
    "pr_rustflags": rustflags,
    "opcache": 1,
    "kernel": platform.release(),
    "php": subprocess.run(["php", "-v"], capture_output=True, text=True).stdout.splitlines()[0],
    "php_embedded": subprocess.run(["rpm", "-q", "php-embedded"], capture_output=True, text=True).stdout.strip(),
}
with open("/opt/bench/meta.json", "w") as f:
    json.dump(meta, f, indent=1)
PY

if [ "$LEGS" = all ]; then
  echo "==> fleet legs"

  if [ ! -x $BENCH/fleet/frankenphp ]; then
    curl -fSL -o $BENCH/fleet/frankenphp \
      "https://github.com/php/frankenphp/releases/download/v$FRANKEN_VERSION/frankenphp-linux-x86_64"
    chmod +x $BENCH/fleet/frankenphp
  fi

  if ! $BENCH/fleet/rr --version >/dev/null 2>&1; then
    curl -fSL "https://github.com/roadrunner-server/roadrunner/releases/download/v$RR_VERSION/roadrunner-$RR_VERSION-linux-amd64.tar.gz" |
      tar -xz -C $BENCH/fleet --strip-components=1 "roadrunner-$RR_VERSION-linux-amd64/rr"
    $BENCH/fleet/rr --version >/dev/null
  fi
  install -d $BENCH/fleet/roadrunner
  install -m 0644 "$HOME"/bench-rig/fleet/roadrunner/worker.php "$HOME"/bench-rig/fleet/roadrunner/composer.json $BENCH/fleet/roadrunner/
  # A committed composer.lock pins the userland stack; without one, versions
  # re-resolve per provision and get recorded in versions.txt instead.
  if [ -f "$HOME"/bench-rig/fleet/roadrunner/composer.lock ]; then
    install -m 0644 "$HOME"/bench-rig/fleet/roadrunner/composer.lock $BENCH/fleet/roadrunner/
  fi
  (cd $BENCH/fleet/roadrunner && composer install --no-dev --quiet)

  # pecl install blocks on interactive configure prompts; build from the pecl
  # tarballs instead. Plain ./configure probes optional deps and skips them.
  pecl_build() { # name url
    local src=$HOME/$1-src
    rm -rf "$src" && mkdir -p "$src"
    curl -fSL "$2" | tar -xz -C "$src" --strip-components=1
    (cd "$src" && phpize -q && ./configure -q && make -s -j"$(nproc)")
  }

  if [ ! -f $BENCH/fleet/swoole.so ]; then
    echo "==> build swoole $SWOOLE_VERSION"
    pecl_build swoole "https://pecl.php.net/get/swoole-$SWOOLE_VERSION.tgz"
    install -m 0644 "$HOME/swoole-src/modules/swoole.so" $BENCH/fleet/swoole.so
  fi

  # Without ext-protobuf the rr workers fall back to a pure-PHP decode path
  # and run about half as fast, silently.
  if ! php -m | grep -qi '^protobuf'; then
    echo "==> build protobuf extension"
    pecl_build protobuf "https://pecl.php.net/get/protobuf"
    sudo install -m 0644 "$HOME/protobuf-src/modules/protobuf.so" "$(php-config --extension-dir)/protobuf.so"
    echo 'extension=protobuf.so' | sudo tee /etc/php.d/40-protobuf.ini >/dev/null
    php -m | grep -qi '^protobuf' || { echo "ERROR: protobuf extension did not load"; exit 1; }
  fi

  # Competitor provenance for the fleet run directory.
  {
    echo "frankenphp $FRANKEN_VERSION"
    echo "roadrunner $RR_VERSION"
    echo "swoole $SWOOLE_VERSION"
    php -r 'echo "protobuf ", phpversion("protobuf"), "\n";'
    (cd $BENCH/fleet/roadrunner && composer show 2>/dev/null | awk '{print $1, $2}')
  } >$BENCH/fleet/versions.txt
fi

echo "==> server provisioned: base=$base_sha pr=$pr_sha legs=$LEGS plain=$PLAIN"
