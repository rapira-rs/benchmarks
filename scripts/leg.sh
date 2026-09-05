#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=scripts/box-lib.sh
. "$(dirname "$0")/box-lib.sh"

LISTEN_HOST=${LISTEN_HOST:-}

case "${1:?start|stop|probe}" in

start)
  ref=${2:?ref} mode=${3:?mode} procs=${4:?processes} tag=${5:?tag} workload=${6:-hello} config=${7:-}
  bin=$BENCH/bin/rapira-$ref
  case "$workload" in
  /*) script=$workload ;;
  *) script=$HOME/bench-rig/php/$workload/$mode.php ;;
  esac
  [ -x "$bin" ] || { echo "ERROR: $bin missing; run 'make provision'"; exit 1; }
  [ -f "$script" ] || { echo "ERROR: $script missing"; exit 1; }
  cfgflag=()
  if [ -n "$config" ]; then
    [ -f "$HOME/bench-rig/$config" ] || { echo "ERROR: $config missing from the staged rig"; exit 1; }
    cfgflag=(--config "$HOME/bench-rig/$config")
  fi
  if port_busy; then
    echo "WARN: :$PORT busy; reaping leaked rapira legs"
    pkill -KILL -f 'bin/rapira-base serve' 2>/dev/null || true
    pkill -KILL -f 'bin/rapira-pr serve' 2>/dev/null || true
    wait_port_free 20 || { echo "ERROR: :$PORT still busy after the reap; something else holds it"; exit 1; }
  fi
  ulimit -n 65536 || true
  nohup "$bin" serve --mode "$mode" --processes "$procs" --listen "$LISTEN_HOST:$PORT" "${cfgflag[@]}" "$script" \
    </dev/null >"$BENCH/log/$tag.server.log" 2>&1 &
  pid=$!
  echo "$pid" >"$BENCH/run/$tag.pid"

  fail() {
    echo "ERROR: $tag $1; last log lines:"
    tail -5 "$BENCH/log/$tag.server.log" 2>/dev/null || true
    kill -KILL "$pid" 2>/dev/null || true
    rm -f "$BENCH/run/$tag.pid"
    exit 1
  }

  listener=
  for _ in $(seq 1 60); do
    if ss -HltnpO "sport = :$PORT" 2>/dev/null | grep -q "pid=$pid,"; then
      listener=$pid
      break
    fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
  [ "$listener" = "$pid" ] || fail "pid $pid never showed up as a :$PORT listener"
  exe=$(readlink "/proc/$pid/exe")
  [ "$exe" = "$bin" ] || fail "exe $exe does not match $bin"
  wait_port_up "$tag" "$tag" >/dev/null || fail "never answered"
  ;;

probe)
  tag=${2:?tag}
  pid=$(cat "$BENCH/run/$tag.pid" 2>/dev/null || true)
  nginx_pid=$(cat "$BENCH/run/$tag.nginx.pid" 2>/dev/null || true)
  {
    { [ -n "$pid" ] && pgrep -P "$pid" 2>/dev/null; } || true
    { [ -n "$nginx_pid" ] && pgrep -P "$nginx_pid" 2>/dev/null; } || true
  } | sort -n | tr '\n' ' '
  echo
  server_log_bytes=$(wc -c <"$BENCH/log/$tag.server.log" 2>/dev/null || echo 0)
  nginx_log_bytes=0
  if [ -n "$nginx_pid" ]; then
    nginx_log_bytes=$(wc -c <"$BENCH/log/$tag.nginx.log" 2>/dev/null || echo 0)
  fi
  echo "$((server_log_bytes + nginx_log_bytes))"
  ;;

stop)
  tag=${2:?tag} ref=${3:?ref}
  pid=$(cat "$BENCH/run/$tag.pid" 2>/dev/null || true)
  if [ -z "$pid" ] && ! pgrep -f "bin/rapira-$ref serve" >/dev/null 2>&1; then
    rm -f "$BENCH/run/$tag.pid"
    exit 0
  fi
  [ -n "$pid" ] && kill -INT "$pid" 2>/dev/null || true
  if ! wait_port_free 90; then
    echo "WARN: $tag still holds :$PORT; force-killing"
    [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null || true
    sleep 1
  fi
  pkill -KILL -f "bin/rapira-$ref serve" 2>/dev/null || true
  rm -f "$BENCH/run/$tag.pid"
  ;;

*)
  echo "ERROR: unknown command $1"
  exit 1
  ;;
esac
