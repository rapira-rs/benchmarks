#!/usr/bin/env bash
# nginx-rapira.sh start TAG PROCS BINARY_DIR MODE ENTRY
# nginx-rapira.sh stop TAG
# nginx listens on :$PORT and proxies to rapira on 127.0.0.1:8081.
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/../lib.sh"

BACKEND_PORT=8081
RAPIRA=$(dirname "$0")/rapira.sh

start() {
  local tag=$1 procs=$2 bindir=$3 mode=$4 entry=$5 nginx
  local conf=$BENCH/run/$tag.nginx.conf
  nginx=$(readlink -f "$(command -v nginx)")
  ensure_port_free
  PORT=$BACKEND_PORT ensure_port_free
  install -d "$BENCH/nginx/tmp" "$BENCH/nginx/run"
  render "$RIG/servers/nginx/rapira.conf.tpl" "$conf" "PROCS=$procs" "LISTEN=$PORT"
  "$nginx" -t -q -p "$BENCH/nginx" -e stderr -c "$conf" || die "$tag: the nginx configuration is invalid"
  PORT=$BACKEND_PORT LISTEN_HOST=127.0.0.1 "$RAPIRA" start "$tag" "$procs" "$bindir" "$mode" "$entry" >/dev/null ||
    fail "$tag" "the rapira backend did not start"
  launch nginx "$tag" "$nginx" -p "$BENCH/nginx" -e stderr -c "$conf" -g 'daemon off;'
  wait_listener "$tag" "$nginx"
  wait_answer "$tag"
  verify_children "$tag" "$pid" "$procs" nginx 'nginx: worker process'
  echo "pid=$pid"
}

stop() {
  local tag=$1
  # QUIT is the graceful stop of nginx.
  stop_pid nginx "$tag" QUIT 45
  wait_port_free 20 || die "$tag: :$PORT is busy after the stop"
  PORT=$BACKEND_PORT "$RAPIRA" stop "$tag"
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
