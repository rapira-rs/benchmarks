#!/usr/bin/env bash
# Mac-side provisioning driver: waits for both boxes, arms the TTL, stages
# the rig once, then runs the two provision scripts with the loader in
# parallel. Env: REF (required), BASE_REF, LEGS, TTL, PLAIN.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/remote-lib.sh
. scripts/remote-lib.sh

REF=${REF:?set REF (branch, tag, sha, or pr/N), e.g. make provision REF=pr/97}
BASE_REF=${BASE_REF:-main}
LEGS=${LEGS:-rapira}
TTL=${TTL:-60}
PLAIN=${PLAIN:-0}

rig_init

# Per-host preamble in parallel: the boxes are independent.
prep() {
  wait_ssh "$1"
  rssh "$1" cloud-init status --wait >/dev/null
  arm_ttl "$1" "$TTL"
}
prep "$SERVER_PUB" &
sp=$!
prep "$LOADER_PUB" &
lp=$!
wait "$sp"
wait "$lp"

stage_rig "$SERVER_PUB" "$LOADER_PUB"

rssh "$LOADER_PUB" "bash bench-rig/scripts/provision-loader.sh" &
lp=$!
if ! rssh "$SERVER_PUB" "BASE_REF=$BASE_REF REF=$REF LEGS=$LEGS PLAIN=$PLAIN bash bench-rig/scripts/provision-server.sh"; then
  echo "ERROR: provisioning failed; the rig is still billing, fix and rerun 'make provision REF=$REF' or run 'make down'"
  kill "$lp" 2>/dev/null || true
  exit 1
fi
wait "$lp"
echo "==> rig ready; run: make bench"
