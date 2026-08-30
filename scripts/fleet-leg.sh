#!/usr/bin/env bash
# Runs on the server box. Starts and stops one competitor leg (franken, fpm,
# roadrunner, swoole) on :8080. Worker counts are rendered from the PROCS
# argument at start time and verified after startup.
# Start shape: the subshell execs into nohup with all fds redirected on the
# subshell itself, so $! is the server pid and the sshd pipe closes at once.
set -euo pipefail
# shellcheck source=scripts/box-lib.sh
. "$(dirname "$0")/box-lib.sh"

FLEET=$BENCH/fleet
RIG=$HOME/bench-rig/fleet

verify_count() { # actual min max what
  if [ "$1" -lt "$2" ] || [ "$1" -gt "$3" ]; then
    echo "ERROR: $4 pool is $1, expected $2..$3; parity broken"
    return 1
  fi
}

# The common stop shape: signal the pidfile pid, wait for the port, force on
# a timeout, reap by anchored pattern. fpm stays special (two masters).
stop_simple() { # tag signal pattern
  local pid
  pid=$(cat "$BENCH/run/$1.pid" 2>/dev/null || true)
  [ -n "$pid" ] && kill "-$2" "$pid" 2>/dev/null || true
  if ! wait_port_free; then
    echo "WARN: $1 still holds :$PORT; force-killing"
    [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null || true
    sleep 1
  fi
  pkill -KILL -f "$3" 2>/dev/null || true
  rm -f "$BENCH/run/$1.pid"
}

cmd=${1:?start|stop} leg=${2:?leg}

case "$cmd-$leg" in

start-franken)
  procs=${3:?procs} tag=${4:?tag}
  ensure_port_free
  install -d "$FLEET/franken"
  sed -e "s/@@PROCS@@/$procs/" -e "s/@@THREADS@@/$((procs + 1))/" \
    "$RIG/franken/Caddyfile.tpl" >"$FLEET/franken/Caddyfile"
  install -m 0644 "$RIG/franken/index.php" "$FLEET/franken/index.php"
  (cd "$FLEET/franken" && exec nohup ../frankenphp run --config Caddyfile) \
    </dev/null >"$BENCH/log/$tag.server.log" 2>&1 &
  echo $! >"$BENCH/run/$tag.pid"
  wait_port_up franken "$tag"
  grep -q "\"num_threads\":$((procs + 1))" "$BENCH/log/$tag.server.log" ||
    echo "WARN: franken num_threads $((procs + 1)) not confirmed in the log"
  ;;

# franken ignores TERM while draining; TERM, wait, then KILL.
stop-franken)
  stop_simple "${3:?tag}" TERM '[f]rankenphp run'
  ;;

start-fpm)
  procs=${3:?procs} tag=${4:?tag}
  ensure_port_free
  install -d "$FLEET/fpm/run" "$FLEET/fpm/tmp"
  sed "s/@@PROCS@@/$procs/" "$RIG/fpm/php-fpm.conf.tpl" >"$FLEET/fpm/php-fpm.conf"
  install -m 0644 "$RIG/fpm/nginx.conf" "$FLEET/fpm/nginx.conf"
  install -m 0644 "$RIG/fpm/hello.php" "$FLEET/fpm/hello.php"
  (cd "$FLEET/fpm" && exec nohup php-fpm -F -p "$FLEET/fpm" -y php-fpm.conf) \
    </dev/null >"$BENCH/log/$tag.fpm.log" 2>&1 &
  echo $! >"$BENCH/run/$tag.fpm.pid"
  (cd "$FLEET/fpm" && exec nohup nginx -p "$FLEET/fpm" -e stderr -c nginx.conf -g 'daemon off;') \
    </dev/null >"$BENCH/log/$tag.server.log" 2>&1 &
  echo $! >"$BENCH/run/$tag.pid"
  wait_port_up fpm "$tag"
  verify_count "$(pgrep -c -f 'php-fpm: pool bench' || true)" "$procs" "$procs" php-fpm
  ;;

stop-fpm)
  tag=${3:?tag}
  ngx=$(cat "$BENCH/run/$tag.pid" 2>/dev/null || true)
  fpm=$(cat "$BENCH/run/$tag.fpm.pid" 2>/dev/null || true)
  [ -n "$ngx" ] && kill -QUIT "$ngx" 2>/dev/null || true
  [ -n "$fpm" ] && kill -QUIT "$fpm" 2>/dev/null || true
  if ! wait_port_free; then
    echo "WARN: fpm still holds :$PORT; force-killing"
    # Orphaned nginx workers keep serving with an unanchorable cmdline; kill
    # them via the parent before the master.
    [ -n "$ngx" ] && pkill -KILL -P "$ngx" 2>/dev/null || true
    [ -n "$ngx" ] && kill -KILL "$ngx" 2>/dev/null || true
    sleep 1
  fi
  # The fpm master title is 'php-fpm: master process (php-fpm.conf)' and the
  # nginx master cmdline carries the -p prefix path.
  pkill -KILL -f '[p]hp-fpm: master process' 2>/dev/null || true
  pkill -KILL -f '[p]hp-fpm: pool bench' 2>/dev/null || true
  pkill -KILL -f '[n]ginx: master process.*fleet/fpm' 2>/dev/null || true
  rm -f "$BENCH/run/$tag.pid" "$BENCH/run/$tag.fpm.pid"
  ;;

start-roadrunner)
  procs=${3:?procs} tag=${4:?tag}
  ensure_port_free
  sed "s/@@PROCS@@/$procs/" "$RIG/roadrunner/rr.yaml.tpl" >"$FLEET/roadrunner/rr.yaml"
  install -m 0644 "$RIG/roadrunner/worker.php" "$FLEET/roadrunner/worker.php"
  (cd "$FLEET/roadrunner" && exec nohup ../rr serve -c rr.yaml) \
    </dev/null >"$BENCH/log/$tag.server.log" 2>&1 &
  echo $! >"$BENCH/run/$tag.pid"
  wait_port_up roadrunner "$tag"
  verify_count "$(pgrep -c -f 'php worker.php' || true)" "$procs" "$procs" roadrunner
  ;;

stop-roadrunner)
  stop_simple "${3:?tag}" TERM 'php [w]orker.php'
  ;;

start-swoole)
  procs=${3:?procs} tag=${4:?tag}
  ensure_port_free
  (cd "$RIG/swoole" && SWOOLE_WORKERS=$procs exec nohup php -d extension="$FLEET/swoole.so" server.php) \
    </dev/null >"$BENCH/log/$tag.server.log" 2>&1 &
  echo $! >"$BENCH/run/$tag.pid"
  wait_port_up swoole "$tag"
  # Workers plus up to two supervisor processes share the launch cmdline; the
  # split differs per swoole mode.
  verify_count "$(pgrep -c -f '[s]woole\.so server\.php' || true)" "$procs" "$((procs + 2))" swoole
  ;;

stop-swoole)
  stop_simple "${3:?tag}" TERM '[s]woole\.so server\.php'
  ;;

*)
  echo "ERROR: unknown $cmd $leg"
  exit 1
  ;;
esac
