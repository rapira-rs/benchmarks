#!/usr/bin/env bash
# rapira.sh start TAG PROCS BINARY_DIR MODE ENTRY [CONFIG_TPL]
# rapira.sh start TAG PROCS BINARY_DIR grpc ENTRY
# rapira.sh stop TAG
# MODE is worker, classic, or dispatcher. CONFIG_TPL is servers/rapira/http.toml.tpl by default.
# PORT and LISTEN_HOST select the listen address.
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/../lib.sh"

start() {
  local tag=$1 procs=$2 bindir=$3 mode=$4 entry=$5 tpl=${6:-servers/rapira/http.toml.tpl}
  local bin=$bindir/bin/rapira toml=$BENCH/run/$tag.toml
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
  render "$tpl" "$toml" "LISTEN=$LISTEN_HOST:$PORT" "ENTRY=$entry" "MODE=$mode" "PROCS=$procs" "ROOT=$RIG/apps/static" "RIG=$RIG"
  launch rapira "$tag" "$bin" serve "$toml"
  wait_listener "$tag" "$bin"
  if [ "$mode" = grpc ]; then
    wait_grpc_answer "$tag"
  else
    wait_answer "$tag"
  fi
  verify_children "$tag" "$pid" "$procs" rapira
  echo "pid=$pid"
}

stop() {
  local tag=$1
  # rapira drains on INT.
  stop_pid rapira "$tag" INT 45
  wait_port_free 20 || die "$tag: :$PORT is busy after the stop"
}

case "${1:?start|stop}" in
start)
  shift
  start "$@"
  ;;
stop)
  stop "${2:?tag}"
  ;;
*)
  die "unknown command $1"
  ;;
esac
