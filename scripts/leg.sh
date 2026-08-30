#!/usr/bin/env bash
# Runs on the server box. Starts and stops one rapira leg with identity checks.
# Layout: binaries /opt/bench/bin/rapira-{base,pr}, pidfiles /opt/bench/run,
# logs /opt/bench/log; the PHP workload comes from the staged rig.
set -euo pipefail
# shellcheck source=scripts/box-lib.sh
. "$(dirname "$0")/box-lib.sh"

case "${1:?start|stop|probe}" in

start)
  ref=${2:?ref} mode=${3:?mode} procs=${4:?processes} tag=${5:?tag} workload=${6:-hello} config=${7:-}
  bin=$BENCH/bin/rapira-$ref
  # From the staged rig, so a re-staged workload edit benches fresh without a
  # re-provision, and the wrk and k6 halves always see the same file.
  script=$HOME/bench-rig/php/$workload/$mode.php
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
  nohup "$bin" serve --mode "$mode" --processes "$procs" --listen ":$PORT" "${cfgflag[@]}" "$script" \
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

  # The listener set must contain our pid: SO_REUSEADDR without REUSEPORT
  # means a leaked older server could keep the port while this one dies on
  # EADDRINUSE, and a readiness curl alone would greet the wrong binary.
  # Workers inherit the listener fd, so ss lists master and worker pids; test
  # membership (the trailing comma stops pid=12 matching pid=123).
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

# One round trip for the cell baselines: worker pid set on line 1, log size
# on line 2. Empty worker line when the master or all workers are gone, so
# the driver can flag the cell without aborting the run.
probe)
  tag=${2:?tag}
  pid=$(cat "$BENCH/run/$tag.pid" 2>/dev/null || true)
  { [ -n "$pid" ] && pgrep -P "$pid" 2>/dev/null | sort -n | tr '\n' ' '; } || true
  echo
  wc -c <"$BENCH/log/$tag.server.log" 2>/dev/null || echo 0
  ;;

stop)
  tag=${2:?tag} ref=${3:?ref}
  pid=$(cat "$BENCH/run/$tag.pid" 2>/dev/null || true)
  # Fast path after a failed start: nothing of ours runs, so skip the drain
  # wait that would burn 45s per cell.
  if [ -z "$pid" ] && ! pgrep -f "bin/rapira-$ref serve" >/dev/null 2>&1; then
    rm -f "$BENCH/run/$tag.pid"
    exit 0
  fi
  [ -n "$pid" ] && kill -INT "$pid" 2>/dev/null || true
  # Workers hold the inherited listener while draining; the supervisor default
  # gives them up to 30s, so wait 45s before forcing.
  if ! wait_port_free 90; then
    echo "WARN: $tag still holds :$PORT; force-killing"
    [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null || true
    sleep 1
  fi
  # Workers share the master cmdline; the anchored pattern reaps master and
  # workers of this ref only.
  pkill -KILL -f "bin/rapira-$ref serve" 2>/dev/null || true
  rm -f "$BENCH/run/$tag.pid"
  ;;

*)
  echo "ERROR: unknown command $1"
  exit 1
  ;;
esac
