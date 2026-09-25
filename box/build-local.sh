#!/usr/bin/env bash
# Build the tree that `make sync` staged in ~/core-sync and install it as the
# rapira binary of the next bench run.
set -euo pipefail

SRC=$HOME/core-sync
DEST=/opt/bench/rapira/local
META=/opt/bench/meta.json

if [ ! -d "$SRC" ]; then
  echo "ERROR: $SRC missing; run 'make sync'"
  exit 1
fi
if [ ! -f "$HOME/.cargo/env" ]; then
  echo "ERROR: the server has no Rust toolchain; provision the rig with REF=<ref> before 'make sync'"
  exit 1
fi
# shellcheck disable=SC1091
. "$HOME/.cargo/env"

(cd "$SRC" && env \
  RUSTFLAGS="" \
  CARGO_PROFILE_RELEASE_DEBUG=line-tables-only \
  PHP_CONFIG=/usr/bin/php-config \
  CARGO_TARGET_DIR="$HOME/core-target" \
  cargo build --release)
install -d "$DEST/bin"
install -m 0755 "$HOME/core-target/release/rapira" "$DEST/bin/rapira"

# The driver selects /opt/bench/rapira/<first 7 characters of sha>, so the sha "local" selects DEST.
python3 - "$META" "$DEST" <<'PY'
import hashlib
import json
import sys

meta_path, dest = sys.argv[1:3]
with open(meta_path) as f:
    meta = json.load(f)
with open(dest + "/bin/rapira", "rb") as f:
    digest = hashlib.sha256(f.read()).hexdigest()
meta["rapira"] = {
    "ref": "local",
    "sha": "local",
    "version": "local",
    "build": "server",
    "asset": None,
    "binary_sha256": digest,
    "rustflags": "",
    "dir": dest,
}
with open(meta_path, "w") as f:
    json.dump(meta, f, indent=1)
    f.write("\n")
PY

echo "==> rapira rebuilt from the synced tree into $DEST"
