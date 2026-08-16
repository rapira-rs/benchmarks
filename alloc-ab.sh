#!/usr/bin/env bash
# Allocator A/B for rapira: one binary per allocator leg built from ../core,
# each driven through this harness's bench-wrk-rapira-worker flow (port guard,
# readiness, graceful stop, reaping).
# Only the default leg (mi-v3) builds today: core hard-wires mimalloc as a
# non-optional dep (core/Cargo.toml) plus an ungated #[global_allocator]
# (core/src/main.rs), and declares no cargo features at all. The mi-v2 and
# snmalloc legs of the 2026-07-21 A/B needed a local patch to BOTH files;
# `--no-default-features` is a no-op now, so a "system" leg would report a
# mimalloc number under a glibc label. Their results are in results/rapira-alloc-*, but
# they are NOT comparable to a run made today: they were taken at --processes 24 (the
# Makefile now hard-codes 32) against the pre-plugin-handler worker API.
# Two probes per leg×round: tiny response and 16 KiB response. A sampler
# records summed Pss (master+workers) and worker-set churn per run.
# Results: results/rapira-alloc-<leg>-r<round>[-16k].{wrk.txt,pss}.
# Knobs: LEGS, ROUNDS, WRK_CONNS, WRK_DURATION (harness defaults: 12t/500c/15s).
# CLOBBER WARNING: run_probe `mv`s results/rapira-worker.wrk.txt into its own tag, so running
# this script CONSUMES the rapira-worker row of the reference wrk table. Re-run `make bench-wrk-rapira-worker
# WRK_CONNS=5000` afterwards to restore it.
set -euo pipefail
cd "$(dirname "$0")"

CORE=../core
LEGS=${LEGS:-"mi-v3"}
# Refuse legs that can no longer be built: build() no-ops for anything outside LEGS, so a
# stale rapira/rapira-<leg> from 2026-07-21 would otherwise be probed silently — and those
# predate the plugin-handler API, so they crash-loop against alloc-worker.php.
for _leg in $LEGS; do
  [ "$_leg" = mi-v3 ] || { echo "ERROR: leg '$_leg' is not buildable from current core (see header)"; exit 1; }
done
ROUNDS=${ROUNDS:-3}
WRK_CONNS=${WRK_CONNS:-500}
WRK_DURATION=${WRK_DURATION:-15s}

trap 'kill $(jobs -p) 2>/dev/null || true' EXIT

build() {
  local leg=$1; shift
  case " $LEGS " in *" $leg "*) ;; *) return 0 ;; esac
  echo "==> build $leg"
  # No PHP prefix pinned: core's build.rs picks up the system php-config from PATH.
  (cd "$CORE" && cargo build --release "$@")
  cp -f "$CORE/target/release/rapira" "rapira/rapira-$leg"
}
build mi-v3
# mi-v2 / snmalloc / system legs removed, for two different reasons: mi-v2 and
# snmalloc passed cargo features (mimalloc-v2, alloc-snmalloc) that no longer exist,
# so they aborted the script under `set -e`; the system leg would have BUILT fine and
# silently reported a mimalloc binary under a glibc label. Restoring any of them means
# patching core, not this file.

pss_sampler() { # $1=outfile — poll master+workers Pss every 2s until the master exits
  local out=$1 pid p v total max=0 last=0 first_set="" last_set="" workers
  for _ in $(seq 300); do [ -f results/rapira-worker.pid ] && break; sleep 0.1; done
  pid=$(cat results/rapira-worker.pid 2>/dev/null) || { echo "no_pid=1" >"$out"; return 0; }
  while kill -0 "$pid" 2>/dev/null; do
    workers=$(pgrep -P "$pid" 2>/dev/null | sort -n | tr '\n' ' ') || true
    total=0
    for p in $pid $workers; do
      v=$(awk '/^Pss:/{print $2}' "/proc/$p/smaps_rollup" 2>/dev/null)
      total=$((total + ${v:-0}))
    done
    [ "$total" -gt "$max" ] && max=$total
    [ "$total" -gt 0 ] && last=$total
    if [ -n "$workers" ]; then
      [ -z "$first_set" ] && first_set=$workers
      last_set=$workers
    fi
    sleep 2
  done
  local churn=stable
  [ "$first_set" != "$last_set" ] && churn=CHANGED
  echo "pss_max_kb=$max pss_last_kb=$last workers=$churn" >"$out"
}

run_probe() { # $1=leg $2=round $3=url $4=suffix ("" | -16k)
  local leg=$1 round=$2 url=$3 suf=$4 tag
  tag="rapira-alloc-$leg-r$round$suf"
  pss_sampler "results/$tag.pss" &
  local sampler=$!
  make bench-wrk-rapira-worker \
    RAPIRA_SRC_BIN="rapira/rapira-$leg" \
    RAPIRA_WORKER_SCRIPT="rapira/alloc-worker.php" \
    WRK_CONNS="$WRK_CONNS" WRK_DURATION="$WRK_DURATION" \
    WRK_URL="$url"
  mv -f results/rapira-worker.wrk.txt "results/$tag.wrk.txt"
  wait "$sampler" 2>/dev/null || true
}

for round in $(seq "$ROUNDS"); do   # interleave legs per round to average out host drift
  for leg in $LEGS; do
    run_probe "$leg" "$round" 'http://127.0.0.1:8080/?name=you' ""
    run_probe "$leg" "$round" 'http://127.0.0.1:8080/?size=16k' "-16k"
  done
done

echo
printf '%-10s %-3s %-5s %12s %10s %10s  %s\n' leg r probe req/s p50 p99 pss
for leg in $LEGS; do
  for round in $(seq "$ROUNDS"); do
    for suf in "" "-16k"; do
      f="results/rapira-alloc-$leg-r$round$suf.wrk.txt"
      [ -f "$f" ] || continue
      probe=tiny; [ -n "$suf" ] && probe=16k
      printf '%-10s %-3s %-5s %12s %10s %10s  %s\n' "$leg" "$round" "$probe" \
        "$(awk '/Requests\/sec/{print $2}' "$f")" \
        "$(awk '$1=="50%"{print $2}' "$f")" \
        "$(awk '$1=="99%"{print $2}' "$f")" \
        "$(cat "results/rapira-alloc-$leg-r$round$suf.pss" 2>/dev/null || echo n/a)"
    done
  done
done
