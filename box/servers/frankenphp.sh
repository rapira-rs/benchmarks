#!/usr/bin/env bash
# frankenphp.sh start TAG PROCS - SHAPE ENTRY DOCROOT [KEY=VALUE...]
# frankenphp.sh stop TAG
# SHAPE is worker, classic, or stock. Each KEY=VALUE becomes an env line of the worker.
# A classic ENTRY is the index file in DOCROOT. A stock ENTRY outside DOCROOT goes into a run copy
# of DOCROOT as index.php, so the assets and the worker file share one document root.
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/../lib.sh"

start() {
  local tag=$1 procs=$2 shape=$4 entry=$5 docroot=$6 threads pair line env_lines=""
  local bin=$BENCH/bin/frankenphp file=$BENCH/run/$tag.Caddyfile
  shift 6
  [ -x "$bin" ] || die "$bin is missing; provision the server"
  [ -f "$entry" ] || die "$entry is missing"
  [ -d "$docroot" ] || die "$docroot is missing"
  case "$shape" in
  worker) threads=$((procs + 1)) ;;
  stock)
    threads=$((procs + 1))
    if [ "$(dirname "$entry")" != "$docroot" ]; then
      rm -rf "$BENCH/run/$tag.docroot"
      mkdir -p "$BENCH/run/$tag.docroot"
      cp -r "$docroot"/. "$BENCH/run/$tag.docroot/"
      cp "$entry" "$BENCH/run/$tag.docroot/index.php"
      docroot=$BENCH/run/$tag.docroot
      entry=$docroot/index.php
    fi
    ;;
  classic)
    threads=$procs
    [ "$(dirname "$entry")" = "$docroot" ] || die "the classic entry $entry is not in $docroot"
    ;;
  *) die "unknown frankenphp shape $shape" ;;
  esac
  for pair in "$@"; do
    case "$pair" in
    *=*) ;;
    *) die "the worker env $pair is not KEY=VALUE" ;;
    esac
    printf -v line '\t\t\tenv %s %s\n' "${pair%%=*}" "${pair#*=}"
    env_lines=$env_lines$line
  done
  ensure_port_free
  render "$RIG/servers/frankenphp/$shape.Caddyfile.tpl" "$file" "LISTEN=:$PORT" "THREADS=$threads" "PROCS=$procs" \
    "ENTRY=$entry" "DOCROOT=$docroot" "INDEX=$(basename "$entry")" "ENV=$env_lines"
  launch frankenphp "$tag" "$bin" run --config "$file"
  wait_listener "$tag" "$bin"
  wait_answer "$tag"
  # FrankenPHP runs its threads in one process. The startup log line gives the thread count.
  grep -q "\"num_threads\":$threads," "$BENCH/log/$tag.frankenphp.log" ||
    fail "$tag" "the log does not confirm num_threads $threads"
  echo "pid=$pid"
}

stop() {
  local tag=$1
  # grace_period in the Caddyfile limits the stop after TERM to 2 s.
  stop_pid frankenphp "$tag" TERM 10
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
