#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=scripts/box-lib.sh
. "$(dirname "$0")/box-lib.sh"

LISTEN_HOST=${LISTEN_HOST:-}

# launch <bin> <tag> <args...> runs "$bin" with args in the background and
# checks that its pid listens on :$PORT. It sets pid. The caller can use fail
# after launch returns.
launch() {
  local bin=$1 tag=$2 listener exe
  shift 2
  if port_busy; then
    echo "WARN: :$PORT busy; reaping leaked rapira legs"
    pkill -KILL -f 'bin/rapira-[a-z]* serve' 2>/dev/null || true
    wait_port_free 20 || { echo "ERROR: :$PORT still busy after the reap; something else holds it"; exit 1; }
  fi
  ulimit -n 65536 || true
  nohup "$bin" "$@" </dev/null >"$BENCH/log/$tag.server.log" 2>&1 &
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
}

case "${1:?start|start-grpc|stop|probe|mem|conns}" in

start)
  ref=${2:?ref} mode=${3:?mode} procs=${4:?processes} tag=${5:?tag} workload=${6:-hello} config=${7:-}
  bin=$BENCH/bin/rapira-$ref
  toml=$BENCH/run/$tag.toml
  case "$workload" in
  /*) script=$workload ;;
  *) script=$HOME/bench-rig/php/$workload/$mode.php ;;
  esac
  [ -x "$bin" ] || { echo "ERROR: $bin missing; run 'make provision'"; exit 1; }
  [ -f "$script" ] || { echo "ERROR: $script missing"; exit 1; }
  if [ -n "$config" ]; then
    [ -f "$HOME/bench-rig/$config" ] || { echo "ERROR: $config missing from the staged rig"; exit 1; }
  fi
  # rapira v0.8.x takes CLI flags. A later rapira takes only a rapira.toml path.
  help=$("$bin" serve --help 2>/dev/null || true)
  case "$help" in
  *--mode*)
    cfgflag=()
    if [ -n "$config" ]; then
      {
        printf '[http]\n'
        sed "s|@@RIG@@|$HOME/bench-rig|g" "$HOME/bench-rig/$config"
      } >"$toml"
      cfgflag=(--config "$toml")
    fi
    launch "$bin" "$tag" serve --mode "$mode" --processes "$procs" --listen "$LISTEN_HOST:$PORT" "${cfgflag[@]}" "$script"
    ;;
  *)
    {
      printf '[http]\nlisten = "%s"\n' "$LISTEN_HOST:$PORT"
      [ -z "$config" ] || sed "s|@@RIG@@|$HOME/bench-rig|g" "$HOME/bench-rig/$config"
      printf '[http.pool]\nentrypoint = "%s"\nmode = "%s"\nprocesses = %s\n' "$script" "$mode" "$procs"
    } >"$toml"
    launch "$bin" "$tag" serve "$toml"
    ;;
  esac
  wait_port_up "$tag" "$tag" >/dev/null || fail "never answered"
  ;;

start-grpc)
  ref=${2:?ref} procs=${3:?processes} tag=${4:?tag}
  bin=$BENCH/bin/rapira-$ref
  toml=$BENCH/run/$tag.toml
  [ -x "$bin" ] || { echo "ERROR: $bin missing; run 'make provision' with LEGS=grpc"; exit 1; }
  for f in grpc/bench.binpb php/grpc/dispatcher.php; do
    [ -f "$HOME/bench-rig/$f" ] || { echo "ERROR: $f missing from the staged rig"; exit 1; }
  done
  sed -e "s|@@LISTEN@@|$LISTEN_HOST:$PORT|" -e "s|@@RIG@@|$HOME/bench-rig|g" -e "s|@@PROCS@@|$procs|" \
    "$HOME/bench-rig/fleet/rapira-grpc.toml.tpl" >"$toml"
  launch "$bin" "$tag" serve "$toml"
  wait_grpc_up "$tag" "$tag" >/dev/null || fail "never answered"
  # The first worker can answer while the master still forks the other workers.
  workers=0
  for _ in $(seq 1 60); do
    workers=$(pgrep -c -P "$pid" || true)
    [ "$workers" -ge "$procs" ] && break
    sleep 0.5
  done
  [ "$workers" -eq "$procs" ] || fail "has $workers workers, expected $procs"
  ;;

mem)
  # Prints the sum of the proportional set size (Pss, kB) of the leg's pid and
  # its children. https://docs.kernel.org/filesystems/proc.html#process-specific-subdirectories
  tag=${2:?tag}
  pid=$(cat "$BENCH/run/$tag.pid")
  for p in "$pid" $(pgrep -P "$pid" || true); do
    cat "/proc/$p/smaps_rollup"
  done | awk '/^Pss:/ { kb += $2 } END { print kb }'
  ;;

conns)
  # Prints one line "<pid> <count>" for each child of the leg's pid that holds
  # established connections on :$PORT.
  tag=${2:?tag}
  pid=$(cat "$BENCH/run/$tag.pid")
  established=$(ss -Htnp state established "( sport = :$PORT )")
  for child in $(pgrep -P "$pid" || true); do
    count=$(printf '%s\n' "$established" | grep -c "pid=$child," || true)
    [ "$count" -eq 0 ] || echo "$child $count"
  done
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
