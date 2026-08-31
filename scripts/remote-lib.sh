#!/usr/bin/env bash

TF_DIR=terraform
RIG_KEY=$TF_DIR/rig-key.pem
KNOWN_HOSTS=.ssh-known-hosts

SSH_OPTS="-i $RIG_KEY -o User=fedora -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$KNOWN_HOSTS -o ConnectTimeout=5 -o ControlMaster=auto -o ControlPath=.ssh-cm-%h -o ControlPersist=10m -o LogLevel=ERROR"

rig_init() {
  local json
  json=$(terraform -chdir=$TF_DIR output -json 2>/dev/null || echo '{}')
  IFS=$'\t' read -r SERVER_PUB SERVER_PRIV LOADER_PUB INSTANCE_TYPE LOADER_TYPE AMI_ID < <(python3 -c '
import json, sys
o = json.loads(sys.argv[1] or "{}")
print("\t".join(o.get(k, {}).get("value", "") for k in
      ("server_public_ip", "server_private_ip", "loader_public_ip", "instance_type", "loader_instance_type", "ami_id")))
' "$json")
  if [ -z "$SERVER_PUB" ] || [ ! -f "$RIG_KEY" ]; then
    echo "ERROR: no rig (terraform outputs or $RIG_KEY missing); run 'make up' first"
    return 1
  fi
}

rssh() {
  local h=$1
  shift
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "$h" "$@"
}

wait_ssh() {
  local h=$1 i
  for i in $(seq 1 60); do
    rssh "$h" true 2>/dev/null && return 0
    sleep 5
  done
  echo "ERROR: $h not reachable over ssh"
  return 1
}

tree_tar() {
  (
    set -o pipefail
    cd "$1" || exit 1
    comm -23 <(git ls-files -co --exclude-standard | sort) <(git ls-files -d | sort) |
      tar --no-xattrs -czf "$2" -T -
  )
}

stage_rig() {
  local tarball h
  tarball=$(mktemp)
  tree_tar . "$tarball" || { rm -f "$tarball"; return 1; }
  for h in "$@"; do
    rssh "$h" 'rm -rf bench-rig && mkdir -p bench-rig && tar -xzf - -C bench-rig && chmod +x bench-rig/scripts/*.sh' <"$tarball" ||
      { rm -f "$tarball"; return 1; }
  done
  rm -f "$tarball"
}

stage_tree() {
  local tarball
  tarball=$(mktemp)
  tree_tar "$1" "$tarball" || { rm -f "$tarball"; return 1; }
  rssh "$2" "rm -rf $3 && mkdir -p $3 && tar -xzf - -C $3" <"$tarball" || { rm -f "$tarball"; return 1; }
  rm -f "$tarball"
}

remaining_ttl_s() {
  local h=$1 d now
  d=$(rssh "$h" cat /etc/rapira-bench-deadline 2>/dev/null) || { echo 0; return; }
  now=$(date -u +%s)
  echo $((d - now))
}

arm_ttl() {
  rssh "$1" "sudo /usr/local/sbin/rapira-bench-ttl-arm $2"
}

arm_ttl_all() {
  local mins=$1 h
  shift
  for h in "$@"; do arm_ttl "$h" "$mins"; done
}

ttl_ensure() {
  local est=$1 h left mins
  shift
  for h in "$@"; do
    left=$(remaining_ttl_s "$h")
    if [ "$left" -lt "$est" ]; then
      if [ "${AUTO_EXTEND:-1}" = 1 ]; then
        mins=$((est / 60 + 15))
        echo "==> TTL on $h too short (${left}s left, need ~${est}s); extending to $mins min"
        arm_ttl "$h" "$mins"
      else
        echo "ERROR: TTL on $h expires in ${left}s, run needs ~${est}s; extend with 'make extend TTL=...'"
        return 1
      fi
    fi
  done
}

cpu_snap() {
  local h=$1
  rssh "$h" "awk '/^cpu /{busy=\$2+\$3+\$4+\$7+\$8+\$9+\$10; print busy, busy+\$5+\$6}' /proc/stat"
}

cpu_pct() {
  awk -v b1="$1" -v t1="$2" -v b2="$3" -v t2="$4" 'BEGIN { if (t2 <= t1) { print 0; exit } printf "%d", 100 * (b2 - b1) / (t2 - t1) }'
}

ena_snap() {
  local h=$1
  rssh "$h" bench-rig/scripts/ena-check.sh
}

ena_delta() {
  awk 'NR==FNR { a[$1] = $2; next } ($1 in a) && $2 != a[$1] { print $1, $2 - a[$1] }' "$1" "$2"
}

bench_init() {
  dur_s=${WRK_DURATION%s}
  case "$dur_s" in
  '' | *[!0-9]*)
    echo "ERROR: WRK_DURATION must be seconds, like 15s"
    return 1
    ;;
  esac
  WRK_DURATION=${dur_s}s
  rig_init || return 1
  wait_ssh "$SERVER_PUB" || return 1
  wait_ssh "$LOADER_PUB" || return 1
  stage_rig "$SERVER_PUB" "$LOADER_PUB" || return 1
  [ -n "${PROCESSES:-}" ] || PROCESSES=$(rssh "$SERVER_PUB" nproc)
  [ -n "${WRK_THREADS:-}" ] || WRK_THREADS=$(rssh "$LOADER_PUB" nproc)
  if [ -z "${WRK_CONNS:-}" ]; then
    WRK_CONNS=$((250 * PROCESSES))
    [ "$WRK_CONNS" -lt 1000 ] && WRK_CONNS=1000
  fi
  return 0
}

flag() {
  echo "$2=$3" >>"$OUT/cells/$1.meta"
}

wrk_pass() {
  rssh "$LOADER_PUB" "ulimit -n 65536; wrk -t$1 -c$2 -d$3 --timeout $WRK_TIMEOUT --latency '$5'" >"$4" 2>&1 || true
}

k6_pass() {
  local raw=$OUT/cells/$1.k6.raw
  rssh "$LOADER_PUB" "ulimit -n 65536; rm -f /tmp/k6.json; \
    k6 run -e TARGET='$2' -e VUS=$K6_VUS -e DURATION=$WRK_DURATION -e CHECKS=${CHECKS:-1} \
    --summary-trend-stats 'avg,min,med,max,p(90),p(95),p(99)' \
    --summary-export /tmp/k6.json bench-rig/k6/$WORKLOAD.js; \
    echo '===K6-EXPORT==='; cat /tmp/k6.json 2>/dev/null" >"$raw" 2>&1 || true
  awk '/^===K6-EXPORT===$/ { f = 1; next } !f' "$raw" >"$OUT/cells/$1.k6.log"
  awk '/^===K6-EXPORT===$/ { f = 1; next } f' "$raw" >"$OUT/cells/$1.k6.summary.json"
  [ -s "$OUT/cells/$1.k6.summary.json" ] || rm -f "$OUT/cells/$1.k6.summary.json"
  rm -f "$raw"
}

measure_cell() {
  local tag=$1 url=$2 probe_tag=${3:-}

  if ! rssh "$LOADER_PUB" "curl -sf -m2 -o /dev/null '$url' || exit 1; ulimit -n 65536; wrk -t$WRK_THREADS -c$WRK_CONNS -d5s --timeout $WRK_TIMEOUT '$url' >/dev/null 2>&1 || true"; then
    flag "$tag" void "not reachable from the loader"
    return 1
  fi

  local probe0 probe1 w0 w1 log0 log1
  if [ -n "$probe_tag" ]; then
    probe0=$(rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh probe $probe_tag" || true)
    w0=$(sed -n 1p <<<"$probe0")
    log0=$(sed -n 2p <<<"$probe0")
  fi
  ena_snap "$SERVER_PUB" >"$OUT/cells/$tag.ena-server.0" || true
  ena_snap "$LOADER_PUB" >"$OUT/cells/$tag.ena-loader.0" || true
  local cs0 cl0 cs1 cl1
  cs0=$(cpu_snap "$SERVER_PUB" || echo 0 0)
  cl0=$(cpu_snap "$LOADER_PUB" || echo 0 0)

  local tw_base
  tw_base=$(rssh "$SERVER_PUB" "ss -Htan state time-wait '( sport = :8080 )' | wc -l" 2>/dev/null || echo 0)
  (
    sleep $((dur_s / 2 + 1))
    rssh "$SERVER_PUB" "echo \$(ss -Htan state established '( sport = :8080 )' | wc -l) \$(ss -Htan state time-wait '( sport = :8080 )' | wc -l)"
  ) >"$OUT/cells/$tag.conns" 2>/dev/null &
  local sampler=$!

  wrk_pass "$WRK_THREADS" "$WRK_CONNS" "$WRK_DURATION" "$OUT/cells/$tag.wrk.txt" "$url"
  wait "$sampler" 2>/dev/null || true

  cs1=$(cpu_snap "$SERVER_PUB" || echo 0 0)
  cl1=$(cpu_snap "$LOADER_PUB" || echo 0 0)
  ena_snap "$SERVER_PUB" >"$OUT/cells/$tag.ena-server.1" || true
  ena_snap "$LOADER_PUB" >"$OUT/cells/$tag.ena-loader.1" || true
  if [ -n "$probe_tag" ]; then
    probe1=$(rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh probe $probe_tag" || true)
    w1=$(sed -n 1p <<<"$probe1")
    log1=$(sed -n 2p <<<"$probe1")
  fi

  wrk_pass 2 "$LOWC" 10s "$OUT/cells/$tag.lowc.wrk.txt" "$url"

  k6_pass "$tag" "$url"

  # shellcheck disable=SC2086
  local busy_server busy_loader
  busy_server=$(cpu_pct $cs0 $cs1)
  busy_loader=$(cpu_pct $cl0 $cl1)
  flag "$tag" busy_server "$busy_server"
  flag "$tag" busy_loader "$busy_loader"
  [ "$busy_loader" -ge 95 ] && [ "$busy_server" -lt 90 ] && flag "$tag" generator_bound 1
  [ "$busy_server" -lt 90 ] && [ "$busy_loader" -lt 95 ] && flag "$tag" server_unsaturated 1

  if [ -n "$probe_tag" ]; then
    [ "$w0" != "$w1" ] && flag "$tag" worker_churn 1
    [ $((${log1:-0} - ${log0:-0})) -gt 65536 ] && flag "$tag" log_growth $((${log1:-0} - ${log0:-0}))
  fi

  local est tw
  read -r est tw <"$OUT/cells/$tag.conns" 2>/dev/null || true
  if [ -n "${tw:-}" ] && [ $((tw - tw_base)) -gt "$WRK_CONNS" ]; then
    flag "$tag" keepalive_broken "est=$est tw=$tw tw_base=$tw_base"
  fi

  local ena
  ena=$(
    ena_delta "$OUT/cells/$tag.ena-server.0" "$OUT/cells/$tag.ena-server.1" 2>/dev/null
    ena_delta "$OUT/cells/$tag.ena-loader.0" "$OUT/cells/$tag.ena-loader.1" 2>/dev/null
  ) || true
  if [ -n "$ena" ]; then
    echo "WARN: $tag moved ENA allowance counters:"
    echo "$ena"
    flag "$tag" ena_throttled "$(echo "$ena" | tr '\n' ';')"
  fi

  local txt
  txt=$(cat "$OUT/cells/$tag.wrk.txt" 2>/dev/null || true)
  if [[ $txt != *"Requests/sec"* ]]; then
    flag "$tag" void "no wrk output (loader unreachable or wrk failed)"
  elif [[ $txt == *"Non-2xx"* ]]; then
    flag "$tag" void "non-2xx responses: $(grep 'Non-2xx' <<<"$txt")"
  elif [[ $txt == *"Socket errors"* ]]; then
    flag "$tag" void "$(grep 'Socket errors' <<<"$txt")"
  fi
  return 0
}

estimate_run_s() {
  echo $(($1 * (2 * dur_s + 90) + 300))
}

write_run_meta() {
  python3 - "$OUT" "$INSTANCE_TYPE" "$LOADER_TYPE" "$AMI_ID" "$PROCESSES" "$WRK_CONNS" "$WRK_THREADS" "$WRK_DURATION" "$LOWC" "$K6_VUS" "${CHECKS:-1}" "$WORKLOAD" "$@" <<'PY'
import hashlib, json, sys
from pathlib import Path
out, itype, ltype, ami, procs, conns, threads, dur, lowc, vus, checks, workload = sys.argv[1:13]
meta = json.load(open(f"{out}/server-meta.json"))
files = sorted(Path(f"php/{workload}").glob("*.php")) + [Path(f"k6/{workload}.js")]
meta.update({
    "instance_type": itype,
    "loader_instance_type": ltype,
    "ami_id": ami,
    "workload": workload,
    "workload_files": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.exists()},
    "processes": int(procs),
    "wrk_conns": int(conns),
    "wrk_threads": int(threads),
    "wrk_duration": dur,
    "lowc": int(lowc),
    "k6_vus": int(vus),
    "k6_checks": int(checks),
})
for kv in sys.argv[13:]:
    k, _, v = kv.partition("=")
    meta[k] = v
json.dump(meta, open(f"{out}/run-meta.json", "w"), indent=1)
PY
}
