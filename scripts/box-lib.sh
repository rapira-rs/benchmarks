#!/usr/bin/env bash

BENCH=/opt/bench
PORT=${PORT:-8080}

port_busy() {
  ss -HltnO "sport = :$PORT" | grep -q .
}

ensure_port_free() {
  if port_busy; then
    echo "ERROR: :$PORT busy; a previous leg leaked:"
    ss -Hltnp "sport = :$PORT" || true
    exit 1
  fi
}

probe_http() {
  curl -sf -m1 -o /dev/null "http://127.0.0.1:$PORT/?name=you"
}

# A gRPC error is HTTP 200 with an empty body. Only the exact reply bytes prove
# that a worker answered.
probe_grpc() {
  curl -sf -m1 --http2-prior-knowledge -H 'content-type: application/grpc' -H 'te: trailers' \
    -H 'grpc-accept-encoding: identity' --data-binary "@$HOME/bench-rig/grpc/echo.grpc" \
    "http://127.0.0.1:$PORT/bench.v1.EchoService/Echo" | cmp -s - "$HOME/bench-rig/grpc/expect.grpc"
}

# wait_answer <name> <tag> <probe_fn> runs the probe until it succeeds.
wait_answer() {
  local i
  for i in $(seq 1 60); do
    "$3" && return 0
    sleep 0.5
  done
  echo "ERROR: $1 never answered; last log lines:"
  tail -5 "$BENCH/log/$2.server.log" 2>/dev/null || true
  return 1
}

wait_port_up() {
  wait_answer "$1" "$2" probe_http
}

wait_grpc_up() {
  wait_answer "$1" "$2" probe_grpc
}

wait_port_free() {
  local i
  for i in $(seq 1 "${1:-60}"); do
    port_busy || return 0
    sleep 0.5
  done
  return 1
}
