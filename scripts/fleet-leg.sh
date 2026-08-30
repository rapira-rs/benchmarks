#!/usr/bin/env bash
# Runs on the server box. Starts and stops one competitor leg (franken, fpm)
# on :8080. Worker counts are rendered from the PROCS argument at start time
# and verified after startup.
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

# Framework apps are built on the box by provisioning (LEGS=frameworks).
app_dir() { # framework
  local app=$BENCH/fleet/apps/$1
  [ -f "$app/public/index.php" ] || { echo "ERROR: $app missing; provision with LEGS=frameworks"; exit 1; }
  echo "$app"
}

case "$cmd-$leg" in

start-franken)
  procs=${3:?procs} tag=${4:?tag}
  ensure_port_free
  install -d "$FLEET/franken"
  sed -e "s/@@PROCS@@/$procs/" -e "s/@@THREADS@@/$((procs + 1))/" \
    "$RIG/franken/Caddyfile.tpl" >"$FLEET/franken/Caddyfile"
  install -m 0644 "$RIG/franken/index.php" "$FLEET/franken/index.php"
  # php_server serves existing files before PHP; app.css feeds the static
  # hit leg and is unreachable from the hello and miss URLs.
  install -m 0644 "$RIG/static/app.css" "$FLEET/franken/app.css"
  (cd "$FLEET/franken" && exec nohup ../frankenphp run --config Caddyfile) \
    </dev/null >"$BENCH/log/$tag.server.log" 2>&1 &
  echo $! >"$BENCH/run/$tag.pid"
  wait_port_up franken "$tag"
  grep -q "\"num_threads\":$((procs + 1))" "$BENCH/log/$tag.server.log" ||
    echo "WARN: franken num_threads $((procs + 1)) not confirmed in the log"
  ;;

# franken ignores TERM while draining; TERM, wait, then KILL.
stop-franken | stop-franken-app)
  stop_simple "${3:?tag}" TERM '[f]rankenphp run'
  ;;

# One FrankenPHP framework leg. classic: php_server executes public/index.php
# per request, num_threads is the pool. worker: the app boots once per worker
# thread; num_threads must exceed the worker num.
start-franken-app)
  fw=${3:?framework} mode=${4:?classic|worker} procs=${5:?procs} tag=${6:?tag}
  ensure_port_free
  app=$(app_dir "$fw")
  case "$mode" in
  classic)
    sed -e "s|@@DOCROOT@@|$app/public|" -e "s/@@THREADS@@/$procs/" \
      "$RIG/franken/Caddyfile.app-classic.tpl" >"$app/Caddyfile.bench"
    expect_threads=$procs
    ;;
  worker)
    # The worker file must resolve from php_server, so it lives in public/;
    # provisioning put it there. Octane's MAX_REQUESTS default (1000) would
    # recycle mid-cell, so it is pinned effectively off; 0 means zero
    # requests to octane, never unlimited.
    case "$fw" in
    symfony)
      indexfile=worker-franken.php
      env_lines=""
      ;;
    laravel)
      indexfile=frankenphp-worker.php
      env_lines="env LARAVEL_OCTANE 1;env MAX_REQUESTS 100000000;env APP_DEBUG false"
      ;;
    *)
      echo "ERROR: unknown framework $fw"
      exit 1
      ;;
    esac
    worker=$app/public/$indexfile
    [ -f "$worker" ] || { echo "ERROR: $worker missing; re-run provisioning"; exit 1; }
    awk -v docroot="$app/public" -v threads="$((procs + 1))" -v workerf="$worker" \
      -v procsn="$procs" -v indexf="$indexfile" -v envl="$env_lines" '
      /@@WORKER_ENV@@/ {
        if (envl != "") { n = split(envl, a, ";"); for (i = 1; i <= n; i++) printf "\t\t\t%s\n", a[i] }
        next
      }
      {
        gsub(/@@DOCROOT@@/, docroot); gsub(/@@THREADS@@/, threads)
        gsub(/@@WORKER@@/, workerf); gsub(/@@PROCS@@/, procsn)
        gsub(/@@INDEXFILE@@/, indexf); print
      }' "$RIG/franken/Caddyfile.app-worker.tpl" >"$app/Caddyfile.bench"
    expect_threads=$((procs + 1))
    ;;
  *)
    echo "ERROR: unknown mode $mode"
    exit 1
    ;;
  esac
  (cd "$app" && exec nohup "$FLEET/frankenphp" run --config Caddyfile.bench) \
    </dev/null >"$BENCH/log/$tag.server.log" 2>&1 &
  echo $! >"$BENCH/run/$tag.pid"
  wait_port_up "franken-$fw-$mode" "$tag"
  grep -q "\"num_threads\":$expect_threads" "$BENCH/log/$tag.server.log" ||
    echo "WARN: franken num_threads $expect_threads not confirmed in the log"
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

# One php-fpm framework leg: same two-master shape as the hello fpm leg,
# nginx front-controllering every path to the app's public/index.php.
start-fpm-app)
  fw=${3:?framework} procs=${4:?procs} tag=${5:?tag}
  ensure_port_free
  app=$(app_dir "$fw")
  install -d "$FLEET/fpm/run" "$FLEET/fpm/tmp"
  sed "s/@@PROCS@@/$procs/" "$RIG/fpm/php-fpm.conf.tpl" >"$FLEET/fpm/php-fpm.conf"
  sed "s|@@DOCROOT@@|$app/public|" "$RIG/fpm/nginx.app.conf.tpl" >"$FLEET/fpm/nginx.conf"
  (cd "$FLEET/fpm" && exec nohup php-fpm -F -p "$FLEET/fpm" -y php-fpm.conf) \
    </dev/null >"$BENCH/log/$tag.fpm.log" 2>&1 &
  echo $! >"$BENCH/run/$tag.fpm.pid"
  (cd "$FLEET/fpm" && exec nohup nginx -p "$FLEET/fpm" -e stderr -c nginx.conf -g 'daemon off;') \
    </dev/null >"$BENCH/log/$tag.server.log" 2>&1 &
  echo $! >"$BENCH/run/$tag.pid"
  wait_port_up "fpm-$fw" "$tag"
  verify_count "$(pgrep -c -f 'php-fpm: pool bench' || true)" "$procs" "$procs" php-fpm
  ;;

stop-fpm | stop-fpm-app)
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

*)
  echo "ERROR: unknown $cmd $leg"
  exit 1
  ;;
esac
