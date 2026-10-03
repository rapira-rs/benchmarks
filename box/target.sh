#!/usr/bin/env bash
# target.sh start TAG SERVER PROCS BINARY_DIR MODE ENTRY [CONFIG_TPL]
# target.sh stop|probe|mem|log TAG SERVER
# Starts, stops, and inspects one Rapira target. SERVER must be rapira.
# MODE is worker, classic, dispatcher, or grpc. CONFIG_TPL defaults to the HTTP template.
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/lib.sh"

cmd=${1:?start|stop|probe|mem|log}
tag=${2:?tag}
server=${3:?server}
[ "$server" = rapira ] || die "unknown server $server"

start() {
  local procs=${1:?processes} bindir=${2:?binary dir} mode=$3 entry tpl=${5:-servers/rapira/http.toml.tpl}
  local bin=$bindir/bin/rapira toml=$BENCH/run/$tag.toml
  entry=$(expand_path "$4")
  case "$mode" in
  worker | classic | dispatcher) ;;
  grpc) tpl=servers/rapira/grpc.toml.tpl ;;
  *) die "unknown rapira mode $mode" ;;
  esac
  tpl=$(rig_path "$tpl")
  [ -x "$bin" ] || die "$bin is missing; provision the server"
  [ -f "$entry" ] || die "$entry is missing"
  [ -f "$tpl" ] || die "$tpl is missing"
  ensure_port_free
  render "$tpl" "$toml" "LISTEN=:$PORT" "ENTRY=$entry" "MODE=$mode" "PROCS=$procs" "ROOT=$RIG/apps/hello" "RIG=$RIG"
  launch rapira "$tag" "$bin" serve "$toml"
  wait_listener "$tag" "$bin"
  if [ "$mode" = grpc ]; then
    wait_grpc_answer "$tag"
  else
    wait_answer "$tag"
  fi
  verify_children "$tag" "$pid" "$procs" rapira
  echo "pid=$pid"
  echo "config=$toml"
}

case "$cmd" in
start)
  shift 3
  start "$@"
  ;;
stop)
  # rapira drains on INT.
  stop_pid rapira "$tag" INT 45
  wait_port_free 20 || die "$tag: :$PORT is busy after the stop"
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
  rss_kb "$tag"
  ;;
log)
  { cat "$BENCH/log/$tag".*.log 2>/dev/null || true; } | { grep -E 'WARN|ERROR' || true; }
  ;;
*)
  die "unknown command $cmd"
  ;;
esac
