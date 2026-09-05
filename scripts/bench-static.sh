#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/remote-lib.sh
. scripts/remote-lib.sh

WORKLOAD=hello

ROUNDS=${ROUNDS:-3}
APPS=${APPS:-hello symfony}
SERVERS=${SERVERS:-rapira-pr rapira-base franken}
KINDS=${KINDS:-hit miss plain}
ASSET=${ASSET:-tiny.css}
USER_CHECKS=${CHECKS:-1}

bench_init

legs=()
for app in $APPS; do
  for srv in $SERVERS; do
    for kind in $KINDS; do
      [ "$kind" = plain ] && [ "$srv" = franken ] && continue
      legs+=("$app-$srv-$kind")
    done
  done
done

fleet_run_init static

plan_rotated_cells "$ROUNDS" "${legs[@]}"
printf '%s\n' "${plan[@]}" >"$OUT/cells.expected"

ttl_ensure "$(estimate_run_s ${#plan[@]})" "$SERVER_PUB" "$LOADER_PUB"

APPDIR=/opt/bench/fleet/apps

leg_start() {
  local app=$1 srv=$2 kind=$3 tag=$4 ref entry config
  case "$srv" in
  rapira-pr) ref="pr" ;;
  rapira-base) ref="base" ;;
  esac
  case "$app" in
  hello)
    entry=$WORKLOAD
    config=fleet/rapira-static.toml
    ;;
  symfony)
    entry=$APPDIR/symfony/bench/worker-rapira.php
    config=fleet/rapira-static-symfony.toml
    ;;
  *)
    echo "ERROR: unknown app $app"
    return 1
    ;;
  esac
  [ "$kind" = plain ] && config=""
  case "$srv" in
  rapira-*) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start $ref worker $PROCESSES $tag $entry $config" ;;
  franken)
    case "$app" in
    hello) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh start franken $PROCESSES $tag" ;;
    symfony) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh start franken-app symfony worker $PROCESSES $tag" ;;
    esac
    ;;
  *)
    echo "ERROR: unknown server $srv"
    return 1
    ;;
  esac
}

leg_stop() {
  local app=$1 srv=$2 tag=$3
  case "$srv" in
  rapira-pr) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh stop $tag pr" ;;
  rapira-base) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh stop $tag base" ;;
  franken)
    case "$app" in
    hello) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh stop franken $tag" ;;
    symfony) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh stop franken-app $tag" ;;
    esac
    ;;
  esac
}

CUR_APP=""
CUR_SRV=""
CUR_TAG=""
cleanup() {
  [ -n "$CUR_TAG" ] && leg_stop "$CUR_APP" "$CUR_SRV" "$CUR_TAG" 2>/dev/null || true
}
trap cleanup EXIT

for tag in "${plan[@]}"; do
  leg=${tag#*-}
  app=${leg%%-*}
  kind=${leg##*-}
  srv=${leg#"$app"-}
  srv=${srv%-"$kind"}
  echo "==> $tag"
  : >"$OUT/cells/$tag.meta"
  flag "$tag" leg "$leg"

  case "$kind" in
  hit)
    cell_url="http://$SERVER_PRIV:8080/$ASSET"
    CHECKS=0
    ;;
  miss | plain)
    cell_url="http://$SERVER_PRIV:8080/?name=you"
    CHECKS=$USER_CHECKS
    ;;
  *)
    flag "$tag" void "unknown kind $kind"
    continue
    ;;
  esac

  if ! leg_start "$app" "$srv" "$kind" "$tag"; then
    flag "$tag" void "start failed"
    leg_stop "$app" "$srv" "$tag" 2>/dev/null || true
    continue
  fi
  CUR_APP=$app
  CUR_SRV=$srv
  CUR_TAG=$tag
  case "$srv" in
  rapira-*) measure_cell "$tag" "$cell_url" "$tag" || true ;;
  *) measure_cell "$tag" "$cell_url" || true ;;
  esac
  leg_stop "$app" "$srv" "$tag" || flag "$tag" stop_warn 1
  CUR_TAG=""
done

write_run_meta "rounds=$ROUNDS" "apps=$APPS" "servers=$SERVERS" "kinds=$KINDS" "asset=$ASSET" \
  "asset_bytes=$(wc -c <"fleet/static/$ASSET" | tr -d ' ')"

report_status=0
python3 scripts/report.py "$OUT" | tee "$OUT/report.txt" || report_status=$?
echo
echo "==> results in $OUT"
exit "$report_status"
