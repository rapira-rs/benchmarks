#!/usr/bin/env bash
# Shared helpers of the box scripts. The scripts source this file.

BENCH=/opt/bench
RIG=$HOME/bench-rig
# The one place of the target port on the boxes.
PORT=8080
# Every PHP process loads the shared php.ini. The gRPC entry loads the protobuf runtime from GRPC_VENDOR.
export PHPRC=$BENCH/php.ini
export GRPC_VENDOR=$BENCH/apps/grpc/vendor

die() {
  echo "ERROR: $*" >&2
  exit 1
}

# expand_path TEXT replaces the @RIG@ and @APPS@ tokens of the target registry.
expand_path() {
  local text=$1
  text=${text//@RIG@/$RIG}
  text=${text//@APPS@/$BENCH/apps}
  printf '%s\n' "$text"
}

# rig_path PATH expands the tokens of PATH and makes a relative PATH absolute under the staged rig.
rig_path() {
  local path
  path=$(expand_path "$1")
  case "$path" in
  /*) printf '%s\n' "$path" ;;
  *) printf '%s\n' "$RIG/$path" ;;
  esac
}

# render TEMPLATE OUTPUT NAME=VALUE... writes TEMPLATE to OUTPUT with each @@NAME@@ replaced by
# VALUE. It fails when a placeholder stays in the output.
render() {
  python3 - "$@" <<'PY'
import sys

template, output = sys.argv[1], sys.argv[2]
with open(template) as source:
    text = source.read()
for pair in sys.argv[3:]:
    name, value = pair.split("=", 1)
    text = text.replace("@@" + name + "@@", value)
if "@@" in text:
    sys.exit(f"ERROR: {template} has a placeholder without a value")
with open(output, "w") as target:
    target.write(text)
PY
}

port_busy() {
  ss -HltnO "sport = :$PORT" | grep -q .
}

ensure_port_free() {
  if port_busy; then
    ss -Hltnp "sport = :$PORT" >&2 || true
    die ":$PORT is busy"
  fi
}

# wait_port_free SECONDS waits until nothing listens on :$PORT.
wait_port_free() {
  for _ in $(seq 1 $(($1 * 2))); do
    port_busy || return 0
    sleep 0.5
  done
  return 1
}

# pids_of TAG prints each pid of the pid files of TAG and the children of that pid, sorted.
pids_of() {
  local file pid
  for file in "$BENCH/run/$1".*.pid; do
    [ -f "$file" ] || continue
    pid=$(cat "$file")
    echo "$pid"
    pgrep -P "$pid" || true
  done | sort -n
}

# log_bytes TAG prints the total size of the logs of TAG.
log_bytes() {
  { cat "$BENCH/log/$1".*.log 2>/dev/null || true; } | wc -c
}

# rss_kb TAG prints the sum of the resident set size in KiB of the processes of TAG.
# https://docs.kernel.org/filesystems/proc.html#process-specific-subdirectories
rss_kb() {
  local pid
  for pid in $(pids_of "$1"); do
    cat "/proc/$pid/status" 2>/dev/null || true
  done | awk '/^VmRSS:/ { kb += $2 } END { print kb + 0 }'
}

# fail TAG MESSAGE prints the last log lines of TAG and then the error, kills the processes of TAG,
# removes its pid files, and exits 1.
fail() {
  local tag=$1 file
  shift
  for file in "$BENCH/log/$tag".*.log; do
    [ -f "$file" ] && tail -5 "$file" >&2
  done
  echo "ERROR: $tag: $*" >&2
  # shellcheck disable=SC2046
  kill -KILL $(pids_of "$tag") 2>/dev/null || true
  # The kernel frees the port a moment after the kill.
  wait_port_free 10 || true
  rm -f "$BENCH/run/$tag".*.pid
  exit 1
}

# launch NAME TAG BIN ARGS... starts BIN with ARGS in the background in $BENCH/run. It writes the
# pid to $BENCH/run/TAG.NAME.pid and the output to $BENCH/log/TAG.NAME.log, and sets pid.
launch() {
  local name=$1 tag=$2 bin=$3
  shift 3
  (
    ulimit -n 65536 2>/dev/null || true
    cd "$BENCH/run" || exit 1
    exec nohup "$bin" "$@"
  ) </dev/null >"$BENCH/log/$tag.$name.log" 2>&1 &
  pid=$!
  echo "$pid" >"$BENCH/run/$tag.$name.pid"
}

# wait_listener TAG BIN waits up to 30 s until the process of the last launch listens on :$PORT.
# It checks that the process runs BIN.
wait_listener() {
  local tag=$1 bin=$2 exe
  for _ in $(seq 1 60); do
    ss -HltnpO "sport = :$PORT" 2>/dev/null | grep -q "pid=$pid," && break
    kill -0 "$pid" 2>/dev/null || fail "$tag" "pid $pid exited before it listened on :$PORT"
    sleep 0.5
  done
  ss -HltnpO "sport = :$PORT" 2>/dev/null | grep -q "pid=$pid," || fail "$tag" "pid $pid does not listen on :$PORT"
  exe=$(readlink "/proc/$pid/exe")
  [ "$exe" = "$(readlink -f "$bin")" ] || fail "$tag" "pid $pid runs $exe, expected $bin"
}

# wait_answer TAG waits up to 30 s until 127.0.0.1:$PORT returns a 2xx response.
wait_answer() {
  local tag=$1
  local deadline=$((SECONDS + 30))
  while [ "$SECONDS" -lt "$deadline" ]; do
    curl -sf -o /dev/null -m 1 "http://127.0.0.1:$PORT/" && return 0
    sleep 0.5
  done
  fail "$tag" "no answer on :$PORT"
}

# wait_grpc_answer TAG waits up to 30 s until the gRPC Echo call on 127.0.0.1:$PORT returns the
# expected bytes. A gRPC error is HTTP 200 with an empty body, so only the reply bytes prove a worker.
wait_grpc_answer() {
  local tag=$1
  local deadline=$((SECONDS + 30))
  while [ "$SECONDS" -lt "$deadline" ]; do
    "$RIG/box/probe.sh" "http://127.0.0.1:$PORT/bench.v1.EchoService/Echo" apps/grpc/expect.grpc grpc POST apps/grpc/echo.grpc >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  fail "$tag" "no gRPC answer on :$PORT"
}

# verify_children TAG PARENT COUNT WHAT waits up to 30 s until PARENT has COUNT children, and fails
# the start when the count differs.
verify_children() {
  local tag=$1 parent=$2 want=$3 what=$4 have=0
  for _ in $(seq 1 60); do
    have=$(pgrep -c -P "$parent" || true)
    [ "$have" -ge "$want" ] && break
    sleep 0.5
  done
  [ "$have" -eq "$want" ] || fail "$tag" "$what has $have workers, expected $want"
}

# stop_pid NAME TAG SIGNAL SECONDS sends SIGNAL to the process of $BENCH/run/TAG.NAME.pid. After
# SECONDS it kills the process and its children with KILL. It removes the pid file.
stop_pid() {
  local name=$1 tag=$2 signal=$3 seconds=$4 file pid tree
  file=$BENCH/run/$tag.$name.pid
  [ -f "$file" ] || return 0
  pid=$(cat "$file")
  tree="$pid $(pgrep -P "$pid" | tr '\n' ' ' || true)"
  kill "-$signal" "$pid" 2>/dev/null || true
  for _ in $(seq 1 $((seconds * 2))); do
    # shellcheck disable=SC2086
    kill -0 $tree 2>/dev/null || break
    sleep 0.5
  done
  # shellcheck disable=SC2086
  kill -KILL $tree 2>/dev/null || true
  for _ in $(seq 1 20); do
    # shellcheck disable=SC2086
    kill -0 $tree 2>/dev/null || break
    sleep 0.5
  done
  # shellcheck disable=SC2086
  if kill -0 $tree 2>/dev/null; then
    die "$tag: a process of $name is alive after KILL"
  fi
  rm -f "$file"
}
