#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/remote-lib.sh
. scripts/remote-lib.sh

LEG=${LEG:-pr}
MODE=${MODE:-worker}
DUR=${DUR:-30}
WORKLOAD=${WORKLOAD:-hello}
WRK_DURATION=${WRK_DURATION:-15s}
WRK_TIMEOUT=${WRK_TIMEOUT:-5s}

bench_init
ttl_ensure $((DUR + 600)) "$SERVER_PUB" "$LOADER_PUB"

tag="perf-$LEG-$MODE"
out=results/$(date -u +%Y%m%dT%H%M%SZ)-perf-$LEG-$MODE
mkdir -p "$out"

rssh "$SERVER_PUB" "bench-rig/scripts/perf-snap.sh prepare"

rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start $LEG $MODE $PROCESSES $tag $WORKLOAD"
trap 'rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh stop $tag $LEG" || true' EXIT

rssh "$LOADER_PUB" "ulimit -n 65536; wrk -t$WRK_THREADS -c$WRK_CONNS -d$((DUR + 10))s --timeout $WRK_TIMEOUT 'http://$SERVER_PRIV:8080/?name=you'" \
  >"$out/wrk.txt" 2>&1 &
loadpid=$!
sleep 5
rssh "$SERVER_PUB" "bench-rig/scripts/perf-snap.sh record $DUR" | tee "$out/perf.log"
wait "$loadpid" || true

rssh "$SERVER_PUB" 'gzip -c /opt/bench/perf/perf.data' >"$out/perf.data.gz"
# shellcheck disable=SC2086
scp $SSH_OPTS "$SERVER_PUB:/opt/bench/perf/flame.svg" "$out/"
echo "==> $out/flame.svg"
