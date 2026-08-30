#!/usr/bin/env bash
# Runs on the server box: rebuild the pr binary from ~/core-sync, the local
# working tree that `make sync` uploaded. Benches uncommitted work.
set -euo pipefail

PLAIN=${PLAIN:-0}
SRC=$HOME/core-sync
BENCH=/opt/bench
# shellcheck source=scripts/build-lib.sh
. "$(dirname "$0")/build-lib.sh"

[ -d "$SRC" ] || { echo "ERROR: $SRC missing; run 'make sync'"; exit 1; }
# shellcheck disable=SC1091
. "$HOME/.cargo/env"

rustflags=$(rustflags_for "$PLAIN")
build_rapira "$SRC" pr "$rustflags"
rm -f "$BENCH/run/build-pr.marker"

# pr_rustflags may now differ from the base build; the A/B driver warns on
# the asymmetry.
python3 - "$rustflags" <<'PY'
import hashlib, json, sys
meta = json.load(open("/opt/bench/meta.json"))
meta["pr_ref"] = "local"
meta["pr_sha"] = "local"
meta["pr_sha256"] = hashlib.sha256(open("/opt/bench/bin/rapira-pr", "rb").read()).hexdigest()
meta["pr_rustflags"] = sys.argv[1]
json.dump(meta, open("/opt/bench/meta.json", "w"), indent=1)
PY

echo "==> rapira-pr rebuilt from the synced local tree"
