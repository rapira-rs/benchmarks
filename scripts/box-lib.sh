#!/usr/bin/env bash
# Shared primitives for the box-side leg scripts. Source, do not execute.

BENCH=/opt/bench
PORT=8080

port_busy() {
  ss -HltnO "sport = :$PORT" | grep -q .
}

# All legs serve byte-identical bodies, so a leaked server would be benched
# under the next leg's name.
ensure_port_free() {
  if port_busy; then
    echo "ERROR: :$PORT busy; a previous leg leaked:"
    ss -Hltnp "sport = :$PORT" || true
    exit 1
  fi
}

wait_port_up() { # leg tag
  local i
  for i in $(seq 1 60); do
    curl -sf -m1 -o /dev/null "http://127.0.0.1:$PORT/?name=you" && return 0
    sleep 0.5
  done
  echo "ERROR: $1 never answered; last log lines:"
  tail -5 "$BENCH/log/$2.server.log" 2>/dev/null || true
  return 1
}

wait_port_free() { # [iterations, 0.5s each]
  local i
  for i in $(seq 1 "${1:-60}"); do
    port_busy || return 0
    sleep 0.5
  done
  return 1
}
