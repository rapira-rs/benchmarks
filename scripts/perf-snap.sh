#!/usr/bin/env bash
set -euo pipefail

case "${1:?prepare|record}" in

prepare)
  if [ ! -x "$HOME/.cargo/bin/inferno-flamegraph" ]; then
    echo "==> inferno (first perf run on this rig)"
    # shellcheck disable=SC1091
    . "$HOME/.cargo/env"
    cargo install -q inferno
  fi
  if ! rpm -q php-embedded-debuginfo >/dev/null 2>&1; then
    sudo dnf -y debuginfo-install php-embedded 2>/dev/null ||
      echo "WARN: php-embedded debuginfo not installed; libphp internals stay unsymbolized"
  fi
  ;;

record)
  dur=${2:?seconds}
  out=/opt/bench/perf
  mkdir -p "$out"
  ev="cycles"
  if perf stat -e cycles true 2>&1 | grep -q '<not supported>'; then
    ev="cpu-clock"
  fi
  echo "perf event: $ev"
  perf record -e "$ev" -a --call-graph fp -F 499 -o "$out/perf.data" -- sleep "$dur"
  perf script -i "$out/perf.data" |
    "$HOME/.cargo/bin/inferno-collapse-perf" |
    "$HOME/.cargo/bin/inferno-flamegraph" >"$out/flame.svg"
  echo "$out/flame.svg"
  ;;

*)
  echo "ERROR: unknown command $1"
  exit 1
  ;;
esac
