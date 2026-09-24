#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=scripts/box-lib.sh
. "$(dirname "$0")/box-lib.sh"

FLEET=$BENCH/fleet
RIG=$HOME/bench-rig/fleet

verify_count() {
  if [ "$1" -ne "$2" ]; then
    echo "ERROR: $3 pool is $1, expected $2; parity broken"
    return 1
  fi
}

stop_rapira_nginx() {
  local tag=$1 nginx_pid
  nginx_pid=$(cat "$BENCH/run/$tag.nginx.pid" 2>/dev/null || true)
  if [ -n "$nginx_pid" ] && kill -0 "$nginx_pid" 2>/dev/null; then
    kill -QUIT "$nginx_pid" 2>/dev/null || true
    if ! PORT=8080 wait_port_free 90; then
      echo "WARN: $tag nginx still holds :8080; force-killing"
      pkill -KILL -P "$nginx_pid" 2>/dev/null || true
      kill -KILL "$nginx_pid" 2>/dev/null || true
      sleep 1
    fi
  fi
  rm -f "$BENCH/run/$tag.nginx.pid"
  PORT=8081 LISTEN_HOST=127.0.0.1 "$HOME/bench-rig/scripts/leg.sh" stop "$tag" pr
}

fail_rapira_nginx() {
  local tag=$1 message=$2
  echo "ERROR: $tag $message; last nginx log lines:"
  tail -5 "$BENCH/log/$tag.nginx.log" 2>/dev/null || true
  exit 1
}

# stop_leg <tag> <leftover pattern>... stops the pid of the tag, then kills
# the processes that match each pattern, in order.
stop_leg() {
  local tag=$1 pid pattern
  shift
  pid=$(cat "$BENCH/run/$tag.pid" 2>/dev/null || true)
  [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true
  if ! wait_port_free; then
    echo "WARN: $tag still holds :$PORT; force-killing"
    [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null || true
    sleep 1
  fi
  for pattern in "$@"; do
    pkill -KILL -f "$pattern" 2>/dev/null || true
  done
  rm -f "$BENCH/run/$tag.pid"
}

cleanup_rapira_nginx_start() {
  local status=$?
  trap - EXIT
  [ "$status" -eq 0 ] || stop_rapira_nginx "$tag" 2>/dev/null || true
  exit "$status"
}

cmd=${1:?start|stop} leg=${2:?leg}

app_dir() {
  local app=$BENCH/fleet/apps/$1
  [ -f "$app/public/index.php" ] || { echo "ERROR: $app missing; provision with LEGS=frameworks"; exit 1; }
  echo "$app"
}

case "$cmd-$leg" in

start-rapira-nginx)
  procs=${3:?procs} tag=${4:?tag} workload=${5:-hello}
  PORT=8080 ensure_port_free
  PORT=8081 ensure_port_free
  trap cleanup_rapira_nginx_start EXIT
  nginx_bin=$(command -v nginx)
  nginx_bin=$(readlink -f "$nginx_bin")
  install -d "$FLEET/nginx/run" "$FLEET/nginx/tmp"
  sed "s/@@PROCS@@/$procs/g" "$RIG/nginx/rapira.conf.tpl" >"$FLEET/nginx/nginx.conf"
  if ! "$nginx_bin" -t -p "$FLEET/nginx" -e stderr -c nginx.conf; then
    fail_rapira_nginx "$tag" "nginx configuration is invalid"
  fi
  if ! PORT=8081 LISTEN_HOST=127.0.0.1 "$HOME/bench-rig/scripts/leg.sh" start pr worker "$procs" "$tag" "$workload"; then
    fail_rapira_nginx "$tag" "Rapira backend did not start"
  fi
  (cd "$FLEET/nginx" && exec nohup "$nginx_bin" -p "$FLEET/nginx" -e stderr -c nginx.conf -g 'daemon off;') \
    </dev/null >"$BENCH/log/$tag.nginx.log" 2>&1 &
  nginx_pid=$!
  echo "$nginx_pid" >"$BENCH/run/$tag.nginx.pid"

  listener=
  for _ in $(seq 1 60); do
    if ss -HltnpO "sport = :8080" 2>/dev/null | grep -q "pid=$nginx_pid,"; then
      listener=$nginx_pid
      break
    fi
    kill -0 "$nginx_pid" 2>/dev/null || break
    sleep 0.5
  done
  [ "$listener" = "$nginx_pid" ] || fail_rapira_nginx "$tag" "nginx pid $nginx_pid never showed up as a :8080 listener"
  nginx_exe=$(readlink "/proc/$nginx_pid/exe")
  [ "$nginx_exe" = "$nginx_bin" ] || fail_rapira_nginx "$tag" "nginx exe $nginx_exe does not match $nginx_bin"
  PORT=8080 wait_port_up rapira-nginx "$tag" >/dev/null || fail_rapira_nginx "$tag" "nginx never answered"
  rapira_pid=$(cat "$BENCH/run/$tag.pid")
  rapira_workers=$(pgrep -c -P "$rapira_pid" || true)
  nginx_workers=$(pgrep -c -P "$nginx_pid" -f 'nginx: worker process' || true)
  verify_count "$rapira_workers" "$procs" rapira || fail_rapira_nginx "$tag" "Rapira worker count is invalid"
  verify_count "$nginx_workers" "$procs" nginx || fail_rapira_nginx "$tag" "nginx worker count is invalid"
  trap - EXIT
  ;;

stop-rapira-nginx)
  tag=${3:?tag}
  stop_rapira_nginx "$tag"
  ;;

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
  tag=${3:?tag}
  stop_leg "$tag" '[f]rankenphp run'
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
      indexfile="worker-franken.php"
      env_lines=""
      ;;
    laravel)
      indexfile=frankenphp-worker.php
      env_lines=$'\t\t\tenv LARAVEL_OCTANE 1\n\t\t\tenv MAX_REQUESTS 100000000\n\t\t\tenv APP_DEBUG false'
      ;;
    *)
      echo "ERROR: unknown framework $fw"
      exit 1
      ;;
    esac
    worker=$app/public/$indexfile
    [ -f "$worker" ] || { echo "ERROR: $worker missing; re-run provisioning"; exit 1; }
    ENV_LINES="$env_lines" awk -v docroot="$app/public" -v threads="$((procs + 1))" -v workerf="$worker" \
      -v procsn="$procs" -v indexf="$indexfile" '
      /@@WORKER_ENV@@/ {
        if (ENVIRON["ENV_LINES"] != "") print ENVIRON["ENV_LINES"]
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

start-fpm | start-fpm-app)
  ensure_port_free
  case "$leg" in
  fpm)
    docroot=.
    indexfile=hello.php
    procs=${3:?procs}
    tag=${4:?tag}
    readiness=fpm
    install -d "$FLEET/fpm"
    install -m 0644 "$RIG/fpm/hello.php" "$FLEET/fpm/hello.php"
    ;;
  fpm-app)
    fw=${3:?framework}
    procs=${4:?procs}
    tag=${5:?tag}
    app=$(app_dir "$fw")
    docroot=$app/public
    indexfile=index.php
    readiness=fpm-$fw
    ;;
  esac
  install -d "$FLEET/fpm/run" "$FLEET/fpm/tmp"
  sed "s/@@PROCS@@/$procs/" "$RIG/fpm/php-fpm.conf.tpl" >"$FLEET/fpm/php-fpm.conf"
  sed -e "s|@@DOCROOT@@|$docroot|" -e "s|@@INDEXFILE@@|$indexfile|g" \
    "$RIG/fpm/nginx.app.conf.tpl" >"$FLEET/fpm/nginx.conf"
  (cd "$FLEET/fpm" && exec nohup php-fpm -F -p "$FLEET/fpm" -y php-fpm.conf) \
    </dev/null >"$BENCH/log/$tag.fpm.log" 2>&1 &
  echo $! >"$BENCH/run/$tag.fpm.pid"
  (cd "$FLEET/fpm" && exec nohup nginx -p "$FLEET/fpm" -e stderr -c nginx.conf -g 'daemon off;') \
    </dev/null >"$BENCH/log/$tag.server.log" 2>&1 &
  echo $! >"$BENCH/run/$tag.pid"
  wait_port_up "$readiness" "$tag"
  verify_count "$(pgrep -c -f 'php-fpm: pool bench' || true)" "$procs" php-fpm
  ;;

start-rr-grpc)
  procs=${3:?procs} tag=${4:?tag}
  ensure_port_free
  [ -x "$FLEET/rr" ] || { echo "ERROR: $FLEET/rr missing; provision with LEGS=grpc"; exit 1; }
  [ -f "$FLEET/roadrunner-grpc/vendor/autoload.php" ] ||
    { echo "ERROR: $FLEET/roadrunner-grpc/vendor missing; provision with LEGS=grpc"; exit 1; }
  sed -e "s|@@RIG@@|$HOME/bench-rig|g" -e "s/@@PROCS@@/$procs/" \
    "$RIG/roadrunner/grpc.rr.yaml.tpl" >"$BENCH/run/$tag.rr.yaml"
  nohup "$FLEET/rr" serve -c "$BENCH/run/$tag.rr.yaml" </dev/null >"$BENCH/log/$tag.server.log" 2>&1 &
  echo $! >"$BENCH/run/$tag.pid"
  wait_grpc_up rr-grpc "$tag"
  verify_count "$(pgrep -c -f '[r]r-worker.php' || true)" "$procs" rr
  ;;

stop-rr-grpc)
  tag=${3:?tag}
  # Kill rr before the PHP workers, because rr replaces a worker that exits.
  stop_leg "$tag" '[r]r serve' '[r]r-worker.php'
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
