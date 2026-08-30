#!/usr/bin/env bash
# Mac-side fleet driver: walk rapira (pr binary, one leg per handler) and the
# competitor legs one at a time, round-interleaved with a rotated leg order so
# no leg is always first or last in a round. Needs `make provision LEGS=all`.
# The measurement window per cell lives in remote-lib.sh's measure_cell.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/remote-lib.sh
. scripts/remote-lib.sh

# The competitor configs serve the hello workload; scenario workloads are an
# A/B (make bench WORKLOAD=...) concern until the fleet grows matching configs.
WORKLOAD=hello

ROUNDS=${ROUNDS:-3}
LEG_LIST=${LEG_LIST:-$(cd "php/$WORKLOAD" && for f in ./*.php; do m=${f#./}; printf 'rapira-%s ' "${m%.php}"; done)franken fpm roadrunner swoole rapira-static-hit rapira-static-miss franken-static-hit franken-static-miss}
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

# The rapira binary carries frame pointers unless it was provisioned with
# PLAIN=1; the prebuilt competitors never do. Stamp it so a published table
# cannot hide the asymmetry.
rustflags=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["pr_rustflags"])' "$OUT/server-meta.json")
[ -n "$rustflags" ] && echo "NOTE: rapira built with '$rustflags'; competitors are plain release builds. Provision with PLAIN=1 for a publishable table."

# The full plan, built once: cells.expected before anything runs, then the
# same array drives the cell loop.
plan=()
for round in $(seq 1 "$ROUNDS"); do
  for i in $(seq 0 $((nlegs - 1))); do
    plan+=("r$round-${legs[$(((i + round - 1) % nlegs))]}")
  done
done
printf '%s\n' "${plan[@]}" >"$OUT/cells.expected"

ttl_ensure "$(estimate_run_s ${#plan[@]})" "$SERVER_PUB" "$LOADER_PUB"

url="http://$SERVER_PRIV:8080/?name=you"

leg_start() { # leg tag
  case "$1" in
  # The static legs run the pr worker with the static middleware config; hit
  # and miss share one server shape and differ only in the request URL.
  rapira-static-*) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start pr worker $PROCESSES $2 $WORKLOAD fleet/rapira-static.toml" ;;
  rapira-*) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start pr ${1#rapira-} $PROCESSES $2 $WORKLOAD" ;;
  franken-static-*) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh start franken $PROCESSES $2" ;;
  *) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh start $1 $PROCESSES $2" ;;
  esac
}

leg_stop() { # leg tag
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

  # Static hit cells fetch the css asset; the k6 body checks describe the
  # hello greeting, so they are off there (status failures still count).
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

# report.py exits nonzero on an incomplete or broken run; the results still land.
python3 scripts/report.py "$OUT" | tee "$OUT/report.txt" || true
echo
echo "==> results in $OUT"
