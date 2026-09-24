#!/usr/bin/env bash
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
build_ceiling "$SRC" "$rustflags"
rm -f "$BENCH/run/build-pr.marker"

python3 - "$rustflags" <<'PY'
import hashlib, json, os, sys
meta = json.load(open("/opt/bench/meta.json"))
meta["pr_ref"] = "local"
meta["pr_sha"] = "local"
meta["pr_sha256"] = hashlib.sha256(open("/opt/bench/bin/rapira-pr", "rb").read()).hexdigest()
meta["pr_rustflags"] = sys.argv[1]
if os.path.exists("/opt/bench/bin/rapira-ceiling"):
    meta["ceiling_sha256"] = hashlib.sha256(open("/opt/bench/bin/rapira-ceiling", "rb").read()).hexdigest()
else:
    meta.pop("ceiling_sha256", None)
json.dump(meta, open("/opt/bench/meta.json", "w"), indent=1)
PY

echo "==> rapira-pr rebuilt from the synced local tree"
