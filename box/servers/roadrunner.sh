#!/usr/bin/env bash
# roadrunner.sh start TAG PROCS - grpc
# roadrunner.sh stop TAG
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/../lib.sh"

start() {
  local tag=$1 procs=$2 shape=$4
  local bin=$BENCH/bin/rr file=$BENCH/run/$tag.rr.yaml
  [ "$shape" = grpc ] || die "unknown roadrunner shape $shape"
  [ -x "$bin" ] || die "$bin is missing; provision the server"
  [ -f "$GRPC_VENDOR/autoload.php" ] || die "$GRPC_VENDOR is missing; provision the server"
  ensure_port_free
  render "$RIG/servers/roadrunner/grpc.rr.yaml.tpl" "$file" "LISTEN=0.0.0.0:$PORT" "PROCS=$procs" "RIG=$RIG"
  launch rr "$tag" "$bin" serve -c "$file"
  wait_listener "$tag" "$bin"
  wait_grpc_answer "$tag"
  verify_children "$tag" "$pid" "$procs" rr
  echo "pid=$pid"
}

stop() {
  local tag=$1
  # rr replaces a worker that exits, so stop_pid signals rr before it kills the workers.
  stop_pid rr "$tag" TERM 45
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
