#!/usr/bin/env bash
# target.sh start TAG SERVER PROCS BINARY_DIR ARGS...
# target.sh stop|probe|mem|log TAG SERVER
# Starts, stops, and inspects one target. box/servers/SERVER.sh starts and stops the server kind.
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/lib.sh"

cmd=${1:?start|stop|probe|mem|log}
tag=${2:?tag}
server=${3:?server}
script=$(dirname "$0")/servers/$server.sh
[ -x "$script" ] || die "unknown server $server"

start() {
  local procs=${1:?processes} bindir=${2:?binary dir} arg file
  local args=()
  shift 2
  for arg in "$@"; do
    args+=("$(expand_path "$arg")")
  done
  "$script" start "$tag" "$procs" "$bindir" "${args[@]}"
  for file in "$BENCH/run/$tag".*; do
    case "$file" in
    *.pid) ;;
    *) if [ -f "$file" ]; then echo "config=$file"; fi ;;
    esac
  done
}

case "$cmd" in
start)
  shift 3
  start "$@"
  ;;
stop)
  "$script" stop "$tag"
  ;;
probe)
  # The worker pids: the children of each process of the target.
  for file in "$BENCH/run/$tag".*.pid; do
    [ -f "$file" ] || continue
    pgrep -P "$(cat "$file")" || true
  done | sort -n | tr '\n' ' ' | sed 's/ $//'
  echo
  log_bytes "$tag"
  ;;
mem)
  pss_kb "$tag"
  ;;
log)
  { cat "$BENCH/log/$tag".*.log 2>/dev/null || true; } | { grep -E 'WARN|ERROR' || true; }
  ;;
*)
  die "unknown command $cmd"
  ;;
esac
