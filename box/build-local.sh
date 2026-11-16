#!/usr/bin/env bash
# Build the tree that `make sync` staged in ~/core-sync and install it as the
# new build of the next bench run. The base build stays.
set -euo pipefail

SRC=$HOME/core-sync
DEST=/opt/bench/rapira/local
META=/opt/bench/meta.json
# rapira uses the io-uring feature of Tokio, which needs this cfg: https://docs.rs/tokio/latest/tokio/#unstable-features
TOKIO_FLAGS="--cfg tokio_unstable"

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
  RUSTFLAGS="$TOKIO_FLAGS" \
  CARGO_PROFILE_RELEASE_DEBUG=line-tables-only \
  PHP_CONFIG=/usr/bin/php-config \
  CARGO_TARGET_DIR="$HOME/core-target" \
  cargo build --release)
install -d "$DEST/bin"
install -m 0755 "$HOME/core-target/release/rapira" "$DEST/bin/rapira"

# The driver reads the install directory of the new build from "dir" of the new record in
# /opt/bench/meta.json, so the next run uses DEST.
python3 - "$META" "$DEST" "$TOKIO_FLAGS" <<'PY'
import hashlib
import json
import sys

meta_path, dest, rustflags = sys.argv[1:4]
with open(meta_path) as f:
    meta = json.load(f)
with open(dest + "/bin/rapira", "rb") as f:
    digest = hashlib.sha256(f.read()).hexdigest()
meta["new"] = {
    "ref": "local",
    "sha": "local",
    "version": "local",
    "build": "server",
    "asset": None,
    "binary_sha256": digest,
    "rustflags": rustflags,
    "dir": dest,
}
with open(meta_path, "w") as f:
    json.dump(meta, f, indent=1)
    f.write("\n")
PY

echo "==> rapira rebuilt from the synced tree into $DEST"
