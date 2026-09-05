#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/remote-lib.sh
. scripts/remote-lib.sh

WORKLOAD=hello

ROUNDS=${ROUNDS:-3}
FRAMEWORKS=${FRAMEWORKS:-symfony laravel}
SERVERS=${SERVERS:-rapira-pr-worker rapira-pr-classic rapira-base-classic franken-worker franken-classic fpm}

fw_conns_default=${WRK_CONNS:+set}

bench_init
[ "$fw_conns_default" = set ] || WRK_CONNS=$((64 * PROCESSES))

legs=()
for fw in $FRAMEWORKS; do
  for srv in $SERVERS; do
    legs+=("$fw-$srv")
  done
done

fleet_run_init frameworks

plan_rotated_cells "$ROUNDS" "${legs[@]}"
printf '%s\n' "${plan[@]}" >"$OUT/cells.expected"

ttl_ensure "$(estimate_run_s ${#plan[@]})" "$SERVER_PUB" "$LOADER_PUB"

url="http://$SERVER_PRIV:8080/?name=you"
APPS=/opt/bench/fleet/apps

leg_start() {
  local fw=$1 srv=$2 tag=$3 app=$APPS/$1
  case "$srv" in
  rapira-pr-worker) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start pr worker $PROCESSES $tag $app/bench/worker-rapira.php" ;;
  rapira-pr-classic) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start pr classic $PROCESSES $tag $app/public/index.php" ;;
  rapira-base-classic) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start base classic $PROCESSES $tag $app/public/index.php" ;;
  franken-worker | franken-classic) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh start franken-app $fw ${srv#franken-} $PROCESSES $tag" ;;
  fpm) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh start fpm-app $fw $PROCESSES $tag" ;;
  *)
    echo "ERROR: unknown server $srv"
    return 1
    ;;
  esac
}

leg_stop() {
  local srv=$1 tag=$2
  case "$srv" in
  rapira-pr-*) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh stop $tag pr" ;;
  rapira-base-*) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh stop $tag base" ;;
  franken-*) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh stop franken-app $tag" ;;
  fpm) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh stop fpm-app $tag" ;;
  esac
}

CUR_SRV=""
CUR_TAG=""
cleanup() {
  [ -n "$CUR_TAG" ] && leg_stop "$CUR_SRV" "$CUR_TAG" 2>/dev/null || true
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
    leg_stop "$srv" "$tag" 2>/dev/null || true
    continue
  fi
  CUR_SRV=$srv
  CUR_TAG=$tag
  case "$srv" in
  rapira-*) measure_cell "$tag" "$url" "$tag" || true ;;
  *) measure_cell "$tag" "$url" || true ;;
  esac
  leg_stop "$srv" "$tag" || flag "$tag" stop_warn 1
  CUR_TAG=""
done

write_run_meta "rounds=$ROUNDS" "frameworks=$FRAMEWORKS" "servers=$SERVERS"

report_status=0
python3 scripts/report.py "$OUT" | tee "$OUT/report.txt" || report_status=$?
echo
echo "==> results in $OUT"
exit "$report_status"
