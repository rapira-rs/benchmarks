#!/usr/bin/env bash
# Runs one load process at a shared start time for one stage.
# Prints the tool output with the RESULT line extended by the tool and late_ms fields.
#
#   load.sh wrk2 EPOCH RATE THREADS CONNS DURATION_S URL [METHOD] [BODY_FILE] [HEADER...]
#   load.sh k6 EPOCH RATE VUS DURATION_S URL
#
# EPOCH is a Unix time in seconds and can have a fraction. When EPOCH has passed,
# the tool starts at once and late_ms is the delay in milliseconds.
# A relative BODY_FILE is a path in the staged rig directory.
set -euo pipefail

RIG=$(cd "$(dirname "$0")/.." && pwd)
TREND_STATS="avg,med,p(90),p(95),p(99),p(99.9),max"

usage() {
  echo "usage: load.sh wrk2 EPOCH RATE THREADS CONNS DURATION_S URL [METHOD] [BODY_FILE] [HEADER...]" >&2
  echo "       load.sh k6 EPOCH RATE VUS DURATION_S URL" >&2
  exit 2
}

# wait_epoch EPOCH sleeps until EPOCH and prints the start delay in milliseconds.
wait_epoch() {
  python3 -c '
import sys, time
delay = float(sys.argv[1]) - time.time()
if delay > 0:
    time.sleep(delay)
    print(0)
else:
    print(round(-delay * 1000))
' "$1"
}

# rewrite TOOL LATE_MS FILE prints FILE and adds the tool and late_ms fields to its RESULT line.
rewrite() {
  python3 -c '
import json, sys
tool, late_ms, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
with open(path, errors="replace") as f:
    for line in f:
        if line.startswith("RESULT "):
            doc = json.loads(line[len("RESULT "):])
            line = "RESULT " + json.dumps({"tool": tool, "late_ms": late_ms, **doc}) + "\n"
        sys.stdout.write(line)
' "$1" "$2" "$3"
}

run_wrk2() {
  [ $# -ge 6 ] || usage
  local epoch=$1 rate=$2 threads=$3 conns=$4 duration=$5 url=$6
  shift 6
  local method=GET body=-
  if [ $# -gt 0 ]; then
    method=$1
    shift
  fi
  if [ $# -gt 0 ]; then
    body=$1
    shift
  fi
  case $body in
  - | /*) ;;
  *) body=$RIG/$body ;;
  esac
  local headers
  headers=$(printf '%s\n' "$@")
  late_ms=$(wait_epoch "$epoch")
  # The driver treats a missing RESULT line as an invalid stage, so a tool failure does not stop the script.
  WRK_METHOD=$method WRK_BODY_FILE=$body WRK_HEADERS=$headers \
    wrk2 -t "$threads" -c "$conns" -d "${duration}s" -R "$rate" --latency \
    -s "$RIG/loader/wrk2-report.lua" "$url" >"$out" 2>&1 || true
}

run_k6() {
  [ $# -eq 5 ] || usage
  local epoch=$1 rate=$2 vus=$3 duration=$4 url=$5
  late_ms=$(wait_epoch "$epoch")
  # The driver treats a missing RESULT line as an invalid stage, so a tool failure does not stop the script.
  k6 run --quiet --no-color --summary-trend-stats "$TREND_STATS" \
    -e TARGET="$url" -e RATE="$rate" -e DURATION="$duration" -e VUS="$vus" \
    "$RIG/loader/k6-grpc.js" >"$out" 2>&1 || true
}

[ $# -ge 1 ] || usage
tool=$1
shift
out=$(mktemp)
trap 'rm -f "$out"' EXIT
late_ms=0

case $tool in
wrk2) run_wrk2 "$@" ;;
k6) run_k6 "$@" ;;
*) usage ;;
esac
rewrite "$tool" "$late_ms" "$out"
