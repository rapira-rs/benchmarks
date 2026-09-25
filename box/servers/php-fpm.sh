#!/usr/bin/env bash
# php-fpm.sh start TAG PROCS - DOCROOT INDEX
# php-fpm.sh stop TAG
# nginx listens on :$PORT and sends each request to INDEX in DOCROOT on the php-fpm pool.
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/../lib.sh"

FPM_PORT=9000

start() {
  local tag=$1 procs=$2 docroot=$4 index=$5 nginx fpm
  local fpm_conf=$BENCH/run/$tag.fpm.conf nginx_conf=$BENCH/run/$tag.nginx.conf
  [ -f "$docroot/$index" ] || die "$docroot/$index is missing"
  nginx=$(readlink -f "$(command -v nginx)")
  fpm=$(readlink -f "$(command -v php-fpm)")
  ensure_port_free
  PORT=$FPM_PORT ensure_port_free
  install -d "$BENCH/nginx/tmp" "$BENCH/nginx/run"
  render "$RIG/servers/php-fpm/php-fpm.conf.tpl" "$fpm_conf" "PROCS=$procs"
  render "$RIG/servers/nginx/fpm.conf.tpl" "$nginx_conf" "PROCS=$procs" "LISTEN=$PORT" "DOCROOT=$docroot" "INDEX=$index"
  "$nginx" -t -q -p "$BENCH/nginx" -e stderr -c "$nginx_conf" || die "$tag: the nginx configuration is invalid"
  launch fpm "$tag" "$fpm" -F -y "$fpm_conf" -c "$PHPRC"
  launch nginx "$tag" "$nginx" -p "$BENCH/nginx" -e stderr -c "$nginx_conf" -g 'daemon off;'
  wait_listener "$tag" "$nginx"
  wait_answer "$tag"
  verify_children "$tag" "$(cat "$BENCH/run/$tag.fpm.pid")" "$procs" php-fpm 'php-fpm: pool bench'
  echo "pid=$pid"
}

stop() {
  local tag=$1
  # QUIT is the graceful stop of nginx and of php-fpm.
  stop_pid nginx "$tag" QUIT 45
  stop_pid fpm "$tag" QUIT 45
  wait_port_free 20 || die "$tag: :$PORT is busy after the stop"
  PORT=$FPM_PORT wait_port_free 20 || die "$tag: :$FPM_PORT is busy after the stop"
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
