#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/remote-lib.sh
. scripts/remote-lib.sh

WORKLOAD=hello

ROUNDS=${ROUNDS:-3}
FRAMEWORKS=${FRAMEWORKS:-symfony laravel}
SERVERS=${SERVERS:-rapira-main-worker rapira-main-classic rapira-070-classic franken-worker franken-classic fpm}
WRK_DURATION=${WRK_DURATION:-15s}
WRK_TIMEOUT=${WRK_TIMEOUT:-5s}
LOWC=${LOWC:-32}
K6_VUS=${K6_VUS:-256}
CHECKS=${CHECKS:-1}

fw_conns_default=${WRK_CONNS:+set}

bench_init
[ "$fw_conns_default" = set ] || WRK_CONNS=$((64 * PROCESSES))

legs=()
for fw in $FRAMEWORKS; do
  for srv in $SERVERS; do
    legs+=("$fw-$srv")
  done
done
nlegs=${#legs[@]}

OUT=results/$(date -u +%Y%m%dT%H%M%SZ)-$INSTANCE_TYPE-frameworks
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
APPS=/opt/bench/fleet/apps

leg_start() {
  local fw=$1 srv=$2 tag=$3 app=$APPS/$1
  case "$srv" in
  rapira-main-worker) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start pr worker $PROCESSES $tag $app/bench/worker-rapira.php" ;;
  rapira-main-classic) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start pr classic $PROCESSES $tag $app/public/index.php" ;;
  rapira-070-classic) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start base classic $PROCESSES $tag $app/public/index.php" ;;
  franken-worker | franken-classic) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh start franken-app $fw ${srv#franken-} $PROCESSES $tag" ;;
  fpm) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh start fpm-app $fw $PROCESSES $tag" ;;
  *)
    echo "ERROR: unknown server $srv"
    return 1
    ;;
  esac
}

leg_stop() {
  local srv=$2 tag=$3
  case "$srv" in
  rapira-main-*) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh stop $tag pr" ;;
  rapira-070-*) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh stop $tag base" ;;
  franken-*) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh stop franken-app $tag" ;;
  fpm) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh stop fpm-app $tag" ;;
  esac
}

CUR_FW=""
CUR_SRV=""
CUR_TAG=""
cleanup() {
  [ -n "$CUR_TAG" ] && leg_stop "$CUR_FW" "$CUR_SRV" "$CUR_TAG" 2>/dev/null || true
}
trap cleanup EXIT

for tag in "${plan[@]}"; do
  leg=${tag#*-}
  fw=${leg%%-*}
  srv=${leg#*-}
  echo "==> $tag"
  : >"$OUT/cells/$tag.meta"
  flag "$tag" leg "$leg"

  if ! leg_start "$fw" "$srv" "$tag"; then
    flag "$tag" void "start failed"
    leg_stop "$fw" "$srv" "$tag" 2>/dev/null || true
    continue
  fi
  CUR_FW=$fw
  CUR_SRV=$srv
  CUR_TAG=$tag
  case "$srv" in
  rapira-*) measure_cell "$tag" "$url" "$tag" || true ;;
  *) measure_cell "$tag" "$url" || true ;;
  esac
  leg_stop "$fw" "$srv" "$tag" || flag "$tag" stop_warn 1
  CUR_TAG=""
done

write_run_meta "rounds=$ROUNDS" "frameworks=$FRAMEWORKS" "servers=$SERVERS"

python3 scripts/report.py "$OUT" | tee "$OUT/report.txt" || true
echo
echo "==> results in $OUT"
