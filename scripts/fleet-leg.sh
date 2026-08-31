#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=scripts/box-lib.sh
. "$(dirname "$0")/box-lib.sh"

FLEET=$BENCH/fleet
RIG=$HOME/bench-rig/fleet

verify_count() {
  if [ "$1" -lt "$2" ] || [ "$1" -gt "$3" ]; then
    echo "ERROR: $4 pool is $1, expected $2..$3; parity broken"
    return 1
  fi
}

stop_simple() {
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

app_dir() {
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
  install -m 0644 "$RIG/static/app.css" "$FLEET/franken/app.css"
  install -m 0644 "$RIG/static/tiny.css" "$FLEET/franken/tiny.css"
  (cd "$FLEET/franken" && exec nohup ../frankenphp run --config Caddyfile) \
    </dev/null >"$BENCH/log/$tag.server.log" 2>&1 &
  echo $! >"$BENCH/run/$tag.pid"
  wait_port_up franken "$tag"
  grep -q "\"num_threads\":$((procs + 1))" "$BENCH/log/$tag.server.log" ||
    echo "WARN: franken num_threads $((procs + 1)) not confirmed in the log"
  ;;

stop-franken | stop-franken-app)
  stop_simple "${3:?tag}" TERM '[f]rankenphp run'
  ;;

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
    [ -n "$ngx" ] && pkill -KILL -P "$ngx" 2>/dev/null || true
    [ -n "$ngx" ] && kill -KILL "$ngx" 2>/dev/null || true
    sleep 1
  fi
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
