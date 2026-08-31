#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/remote-lib.sh
. scripts/remote-lib.sh

WORKLOAD=hello

ROUNDS=${ROUNDS:-3}
LEG_LIST=${LEG_LIST:-$(cd "php/$WORKLOAD" && for f in ./*.php; do m=${f#./}; printf 'rapira-%s ' "${m%.php}"; done)franken fpm rapira-static-hit rapira-static-miss franken-static-hit franken-static-miss}
USER_CHECKS=${CHECKS:-1}
WRK_DURATION=${WRK_DURATION:-15s}
WRK_TIMEOUT=${WRK_TIMEOUT:-5s}
LOWC=${LOWC:-32}
K6_VUS=${K6_VUS:-256}

bench_init

# shellcheck disable=SC2206
legs=($LEG_LIST)
nlegs=${#legs[@]}

instance_type=$INSTANCE_TYPE
OUT=results/$(date -u +%Y%m%dT%H%M%SZ)-$instance_type-fleet
mkdir -p "$OUT/cells"
rssh "$SERVER_PUB" cat /opt/bench/meta.json >"$OUT/server-meta.json"
rssh "$SERVER_PUB" cat /opt/bench/fleet/versions.txt >"$OUT/fleet-versions.txt" 2>/dev/null || true

rustflags=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["pr_rustflags"])' "$OUT/server-meta.json")
[ -n "$rustflags" ] && echo "NOTE: rapira built with '$rustflags'; competitors are plain release builds. Provision with PLAIN=1 for a publishable table."

plan=()
for round in $(seq 1 "$ROUNDS"); do
  for i in $(seq 0 $((nlegs - 1))); do
    plan+=("r$round-${legs[$(((i + round - 1) % nlegs))]}")
  done
done
printf '%s\n' "${plan[@]}" >"$OUT/cells.expected"

ttl_ensure "$(estimate_run_s ${#plan[@]})" "$SERVER_PUB" "$LOADER_PUB"

url="http://$SERVER_PRIV:8080/?name=you"

leg_start() {
  case "$1" in
  rapira-static-*) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start pr worker $PROCESSES $2 $WORKLOAD fleet/rapira-static.toml" ;;
  rapira-*) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start pr ${1#rapira-} $PROCESSES $2 $WORKLOAD" ;;
  franken-static-*) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh start franken $PROCESSES $2" ;;
  *) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh start $1 $PROCESSES $2" ;;
  esac
}

leg_stop() {
  case "$1" in
  rapira-*) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh stop $2 pr" ;;
  franken-static-*) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh stop franken $2" ;;
  *) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh stop $1 $2" ;;
  esac
}

CUR_LEG=""
CUR_TAG=""
cleanup() {
  [ -n "$CUR_TAG" ] && leg_stop "$CUR_LEG" "$CUR_TAG" 2>/dev/null || true
}
trap cleanup EXIT

for tag in "${plan[@]}"; do
  leg=${tag#*-}
  echo "==> $tag"
  : >"$OUT/cells/$tag.meta"
  flag "$tag" leg "$leg"

  case "$leg" in
  *-static-hit)
    cell_url="http://$SERVER_PRIV:8080/app.css"
    CHECKS=0
    ;;
  *)
    cell_url=$url
    CHECKS=$USER_CHECKS
    ;;
  esac

  if ! leg_start "$leg" "$tag"; then
    flag "$tag" void "start failed"
    leg_stop "$leg" "$tag" 2>/dev/null || true
    continue
  fi
  CUR_LEG=$leg
  CUR_TAG=$tag
  case "$leg" in
  rapira-*) measure_cell "$tag" "$cell_url" "$tag" || true ;;
  *) measure_cell "$tag" "$cell_url" || true ;;
  esac
  leg_stop "$leg" "$tag" || flag "$tag" stop_warn 1
  CUR_TAG=""
done

write_run_meta "rounds=$ROUNDS" "legs=$LEG_LIST"

python3 scripts/report.py "$OUT" | tee "$OUT/report.txt" || true
echo
echo "==> results in $OUT"
