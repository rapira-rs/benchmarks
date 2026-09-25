#!/usr/bin/env bash
# Runs one load process at a shared start time for one stage.
# Prints the tool output with the RESULT line extended by the tool and late_ms fields.
#
#   load.sh wrk2 EPOCH RATE THREADS CONNS WARMUP_S DURATION_S URL [METHOD] [BODY_FILE] [HEADER...]
#   load.sh h2load EPOCH RATE THREADS CONNS STREAMS WARMUP_S DURATION_S URL BODY_FILE
#
# EPOCH is a Unix time in seconds and can have a fraction. When EPOCH has passed,
# the tool starts at once and late_ms is the delay in milliseconds.
# wrk2 runs for WARMUP_S plus DURATION_S seconds. Its RESULT line counts the whole run.
# h2load measures DURATION_S seconds after a warm-up of WARMUP_S seconds and sends
# RATE / CONNS requests per second on each connection. Its RESULT line counts the measured window.
# A relative BODY_FILE is a path in the staged rig directory.
set -euo pipefail

RIG=$(cd "$(dirname "$0")/.." && pwd)

usage() {
  echo "usage: load.sh wrk2 EPOCH RATE THREADS CONNS WARMUP_S DURATION_S URL [METHOD] [BODY_FILE] [HEADER...]" >&2
  echo "       load.sh h2load EPOCH RATE THREADS CONNS STREAMS WARMUP_S DURATION_S URL BODY_FILE" >&2
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

# body_path FILE prints FILE with a relative path made absolute under the staged rig.
body_path() {
  case $1 in
  - | /*) printf '%s\n' "$1" ;;
  *) printf '%s\n' "$RIG/$1" ;;
  esac
}

run_wrk2() {
  [ $# -ge 7 ] || usage
  local epoch=$1 rate=$2 threads=$3 conns=$4 warmup=$5 duration=$6 url=$7
  shift 7
  local method=GET body=-
  if [ $# -gt 0 ]; then
    method=$1
    shift
  fi
  if [ $# -gt 0 ]; then
    body=$(body_path "$1")
    shift
  fi
  local headers
  headers=$(printf '%s\n' "$@")
  late_ms=$(wait_epoch "$epoch")
  # The driver treats a missing RESULT line as a void, so a tool failure does not stop the script.
  WRK_METHOD=$method WRK_BODY_FILE=$body WRK_HEADERS=$headers \
    wrk2 -t "$threads" -c "$conns" -d "$((warmup + duration))s" -R "$rate" --latency \
    -s "$RIG/loader/wrk2-report.lua" "$url" >"$out" 2>&1 || true
}

run_h2load() {
  [ $# -eq 9 ] || usage
  local epoch=$1 rate=$2 threads=$3 conns=$4 streams=$5 warmup=$6 duration=$7 url=$8 body
  body=$(body_path "$9")
  late_ms=$(wait_epoch "$epoch")
  # The RESULT line comes from the per-request log, so a failed h2load leaves no RESULT line.
  if h2load -t "$threads" -c "$conns" -m "$streams" --rps "$((rate / conns))" \
    --warm-up-time "$warmup" -D "$duration" -d "$body" \
    -H 'content-type: application/grpc' -H 'te: trailers' --log-file "$log" "$url" >"$out" 2>&1; then
    python3 "$RIG/loader/h2load-report.py" "$log" "$duration" >>"$out"
  fi
}

[ $# -ge 1 ] || usage
tool=$1
shift
out=$(mktemp)
log=$(mktemp)
trap 'rm -f "$out" "$log"' EXIT
late_ms=0

case $tool in
wrk2) run_wrk2 "$@" ;;
h2load) run_h2load "$@" ;;
*) usage ;;
esac
rewrite "$tool" "$late_ms" "$out"
