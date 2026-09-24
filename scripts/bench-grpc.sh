#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/remote-lib.sh
. scripts/remote-lib.sh

WORKLOAD=grpc

ROUNDS=${ROUNDS:-3}
LEG_LIST=${LEG_LIST:-rapira-http-h1 rapira-grpc rapira-grpcweb-h1 rapira-connect-h1 rapira-connect-h2c rapira-connectjson-h1 rr-grpc ceiling-grpc ceiling-connect-h1}
OPEN_RATE=${OPEN_RATE:-20000}

bench_init
GRPC_CONNS=${GRPC_CONNS:-$((16 * PROCESSES))}
# The saturated passes of this suite use GRPC_CONNS, and the lowc passes use
# PROCESSES. write_run_meta records WRK_CONNS as wrk_conns and LOWC as lowc.
WRK_CONNS=$GRPC_CONNS
LOWC=$PROCESSES

# shellcheck disable=SC2206
legs=($LEG_LIST)

# leg_request <leg> sets proto, wire, url, hdrs, body and expect for
# measure_grpc_cell.
leg_request() {
  local connect_hdrs="-H 'content-type: application/proto' -H 'connect-protocol-version: 1' -H 'accept-encoding: identity'"
  url="http://$SERVER_PRIV:8080/bench.v1.EchoService/Echo"
  case "$1" in
  rapira-http-h1)
    proto=http-h1 wire=h1 hdrs='' body='' expect=grpc/expect.http
    url="http://$SERVER_PRIV:8080/?name=you"
    ;;
  rapira-grpc | rr-grpc | ceiling-grpc)
    proto=grpc wire=h2c body=echo.grpc expect=grpc/expect.grpc
    hdrs="-H 'content-type: application/grpc' -H 'te: trailers' -H 'grpc-accept-encoding: identity'"
    ;;
  rapira-grpcweb-h1)
    proto=grpcweb-h1 wire=h1 body=echo.grpc expect=grpc/expect.grpcweb
    hdrs="-H 'content-type: application/grpc-web+proto' -H 'x-grpc-web: 1'"
    ;;
  rapira-connect-h1 | ceiling-connect-h1)
    proto=connect-h1 wire=h1 body=echo.bin expect=grpc/expect.bin
    hdrs=$connect_hdrs
    ;;
  rapira-connect-h2c)
    proto=connect-h2c wire=h2c body=echo.bin expect=grpc/expect.bin
    hdrs=$connect_hdrs
    ;;
  rapira-connectjson-h1)
    proto=connectjson-h1 wire=h1 body=echo.json expect=grpc/expect.json
    hdrs="-H 'content-type: application/json' -H 'connect-protocol-version: 1' -H 'accept-encoding: identity'"
    ;;
  *)
    echo "ERROR: unknown leg $1"
    return 1
    ;;
  esac
}

leg_start() {
  case "$1" in
  rapira-http-h1) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start pr dispatcher $PROCESSES $2 hello" ;;
  rapira-*) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start-grpc pr $PROCESSES $2" ;;
  ceiling-*) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start-grpc ceiling $PROCESSES $2" ;;
  rr-grpc) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh start rr-grpc $PROCESSES $2" ;;
  esac
}

leg_stop() {
  case "$1" in
  rapira-*) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh stop $2 pr" ;;
  ceiling-*) rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh stop $2 ceiling" ;;
  rr-grpc) rssh "$SERVER_PUB" "bench-rig/scripts/fleet-leg.sh stop rr-grpc $2" ;;
  esac
}

for leg in "${legs[@]}"; do
  leg_request "$leg" || exit 1
done

fleet_run_init grpc

rssh "$LOADER_PUB" 'h2load --version 2>&1 | head -1; k6 version | head -1; wrk --version 2>&1 | head -1' >"$OUT/loader-tools.txt"
if [[ $(head -1 "$OUT/loader-tools.txt") != *nghttp2/1.70.0* ]]; then
  echo "ERROR: loader h2load is not 1.70.0; provision the loader"
  exit 1
fi

plan_rotated_cells "$ROUNDS" "${legs[@]}"
printf '%s\n' "${plan[@]}" >"$OUT/cells.expected"

ttl_ensure "$(estimate_run_s ${#plan[@]})" "$SERVER_PUB" "$LOADER_PUB"

CUR_LEG=""
CUR_TAG=""
cleanup() {
  [ -n "$CUR_TAG" ] && leg_stop "$CUR_LEG" "$CUR_TAG" 2>/dev/null || true
}
trap cleanup EXIT

for tag in "${plan[@]}"; do
  leg=${tag#*-}
  echo "==> $tag"
  leg_request "$leg"
  case "$leg" in
  rr-grpc) config=/opt/bench/run/$tag.rr.yaml ;;
  *) config=/opt/bench/run/$tag.toml ;;
  esac
  : >"$OUT/cells/$tag.meta"
  flag "$tag" leg "$leg"
  flag "$tag" proto "$proto"
  flag "$tag" conns "$GRPC_CONNS"

  if ! leg_start "$leg" "$tag"; then
    flag "$tag" void "start failed"
    leg_stop "$leg" "$tag" 2>/dev/null || true
    continue
  fi
  CUR_LEG=$leg
  CUR_TAG=$tag
  if rssh "$SERVER_PUB" cat "$config" >"$OUT/cells/$tag.config"; then
    measure_grpc_cell "$tag" "$proto" "$wire" "$url" "$hdrs" "$body" "$expect" "$tag" || true
  else
    flag "$tag" void "configuration unavailable"
  fi
  leg_stop "$leg" "$tag" || flag "$tag" stop_warn 1
  CUR_TAG=""
done

# rapira-http-h1 runs php/hello/dispatcher.php, and WORKLOAD=grpc does not hash it.
hello_sha256=$(python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' php/hello/dispatcher.php)
write_run_meta "rounds=$ROUNDS" "legs=$LEG_LIST" "grpc_conns=$GRPC_CONNS" "open_rate=$OPEN_RATE" "loader_tools=$(tr '\n' ';' <"$OUT/loader-tools.txt")" \
  "hello_dispatcher_sha256=$hello_sha256"

report_status=0
python3 scripts/report.py "$OUT" | tee "$OUT/report.txt" || report_status=$?
echo
echo "==> results in $OUT"
exit "$report_status"
