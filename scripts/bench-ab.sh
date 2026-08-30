#!/usr/bin/env bash
# Mac-side A/B driver: rapira@base vs rapira@pr on the server box, load from
# the loader box, one cell (ref x mode) at a time. The measurement window per
# cell lives in remote-lib.sh's measure_cell.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/remote-lib.sh
. scripts/remote-lib.sh

ROUNDS=${ROUNDS:-3}
WORKLOAD=${WORKLOAD:-hello}
# The handler files are the source of truth for the mode set.
MODES=${MODES:-$(cd "php/$WORKLOAD" 2>/dev/null && ls ./*.php 2>/dev/null | sed 's|^\./||; s|\.php$||' | tr '\n' ' ')}
WRK_DURATION=${WRK_DURATION:-15s}
WRK_TIMEOUT=${WRK_TIMEOUT:-5s}
LOWC=${LOWC:-32}
K6_VUS=${K6_VUS:-256}
ALLOW_SAME=${ALLOW_SAME:-0}

[ -f "k6/$WORKLOAD.js" ] || { echo "ERROR: k6/$WORKLOAD.js missing"; exit 1; }
[ -n "${MODES// /}" ] || { echo "ERROR: php/$WORKLOAD/ has no handlers"; exit 1; }

bench_init

OUT=results/$(date -u +%Y%m%dT%H%M%SZ)-$INSTANCE_TYPE-ab
mkdir -p "$OUT/cells"

rssh "$SERVER_PUB" cat /opt/bench/meta.json >"$OUT/server-meta.json"
# Unit-separator fields: rustflags carry spaces and can be empty, and bash
# collapses runs of IFS whitespace (tabs included), which would shift every
# field after an empty one.
IFS=$'\x1f' read -r base_sha pr_sha base_bin pr_bin base_rf pr_rf opcache < <(python3 -c '
import json, sys
m = json.load(open(sys.argv[1]))
print("\x1f".join([m["base_sha"], m["pr_sha"], m["base_sha256"], m["pr_sha256"],
                   m["base_rustflags"], m["pr_rustflags"], str(m.get("opcache", 0))]))
' "$OUT/server-meta.json")

echo "==> A/B: base=$base_sha pr=$pr_sha workload=$WORKLOAD processes=$PROCESSES conns=$WRK_CONNS rounds=$ROUNDS modes=[$MODES]"

# The worst A/B failure is benching the same code twice after a ref fetch
# fell through: it reports a clean "no regression" that means nothing.
if [ "$base_sha" = "$pr_sha" ] || [ "$base_bin" = "$pr_bin" ]; then
  if [ "$ALLOW_SAME" = 1 ]; then
    echo "WARN: base and pr are identical (NULL-RUN calibration)"
    echo "null_run=1" >>"$OUT/run.flags"
  else
    echo "ERROR: base and pr resolve to the same code; pass ALLOW_SAME=1 only for a deliberate calibration run"
    exit 1
  fi
fi

# A build-flag mismatch (make sync PLAIN vs provision) puts the flag cost
# into the delta as if it were the code change.
if [ "$base_rf" != "$pr_rf" ]; then
  echo "WARN: base and pr carry different build flags: base='$base_rf' pr='$pr_rf'"
  echo "asymmetric_build=1" >>"$OUT/run.flags"
fi

# Asserted at provision time and recorded in meta; classic-mode numbers
# would otherwise measure opcache's absence.
[ "$opcache" = 1 ] || { echo "ERROR: the server meta does not confirm opcache; rerun 'make provision'"; exit 1; }

# The full plan, built once: it writes cells.expected before anything runs
# (an aborted run must report INCOMPLETE) and then drives the cell loop, so
# the two can never disagree. Ref order alternates per round: network credits
# and other monotonic drift must not land on one ref only.
plan=()
for round in $(seq 1 "$ROUNDS"); do
  if [ $((round % 2)) -eq 1 ]; then refs="base pr"; else refs="pr base"; fi
  for ref in $refs; do
    for mode in $MODES; do
      plan+=("r$round-$ref-$mode")
    done
  done
done
printf '%s\n' "${plan[@]}" >"$OUT/cells.expected"

ttl_ensure "$(estimate_run_s ${#plan[@]})" "$SERVER_PUB" "$LOADER_PUB"

url="http://$SERVER_PRIV:8080/?name=you"

# Stop the running leg when the driver dies mid-cell, so an abort never
# leaks a server onto :8080.
CUR_TAG=""
CUR_REF=""
cleanup() {
  [ -n "$CUR_TAG" ] && rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh stop $CUR_TAG $CUR_REF" 2>/dev/null || true
}
trap cleanup EXIT

for tag in "${plan[@]}"; do
  rest=${tag#*-}
  ref=${rest%%-*}
  mode=${rest#*-}
  echo "==> $tag (conns=$WRK_CONNS, $WRK_DURATION)"
  : >"$OUT/cells/$tag.meta"
  flag "$tag" ref "$ref"
  flag "$tag" mode "$mode"

  if ! rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh start $ref $mode $PROCESSES $tag $WORKLOAD"; then
    flag "$tag" void "start failed"
    rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh stop $tag $ref" 2>/dev/null || true
    continue
  fi
  CUR_TAG=$tag
  CUR_REF=$ref
  measure_cell "$tag" "$url" "$tag" || true
  rssh "$SERVER_PUB" "bench-rig/scripts/leg.sh stop $tag $ref" || flag "$tag" stop_warn 1
  CUR_TAG=""
done

write_run_meta "rounds=$ROUNDS"

# report.py exits nonzero on an incomplete or broken run; the results still land.
python3 scripts/report.py "$OUT" | tee "$OUT/report.txt" || true
echo
echo "==> results in $OUT"
