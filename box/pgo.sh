#!/usr/bin/env bash
# pgo.sh build
# pgo.sh start TAG SERVER PROCS MODE ENTRY [CONFIG_TPL]
# pgo.sh merge TARGET...
# The PGO pass of the new build in /opt/bench/meta.json:
#   build  builds the instrumented rapira into /opt/bench/rapira/pgo.
#   start  starts a target with the instrumented build through target.sh. Its processes write the counters to /opt/bench/pgo/data.
#   merge  merges the counters into /opt/bench/pgo/merged.profdata and writes /opt/bench/pgo/pgo.json.
# https://doc.rust-lang.org/rustc/profile-guided-optimization.html
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/lib.sh"

CORE_REPO=${CORE_REPO:-https://github.com/rapira-rs/rapira}
CORE=$HOME/core
PGO=$BENCH/pgo
DEST=$BENCH/rapira/pgo
TRIPLE=x86_64-unknown-linux-gnu
# Workers leave through _exit, which skips the profile write at exit. Continuous mode (%c) keeps the counters in a
# mapped file that the forked workers share. On Linux it needs counter relocation, and it does not support value profiling:
# https://clang.llvm.org/docs/UsersManual.html#cmdoption-fprofile-continuous
GENERATE_FLAGS="-Cprofile-generate=$PGO/data -Cllvm-args=-runtime-counter-relocation -Cllvm-args=-disable-vp"
PROFILE_FILE="$PGO/data/rapira-%p%c.profraw"
# A build that reads a profile without value sites must also disable value profiling.
USE_FLAGS="-Cprofile-use=<absolute path>/merged.profdata -Cllvm-args=-disable-vp"

new_sha() {
  python3 -c 'import json; print(json.load(open("/opt/bench/meta.json"))["new"]["sha"])'
}

build() {
  local sha
  sha=$(new_sha)
  # The build packages of a REF build in provision-server.sh.
  sudo dnf -y install php-devel php-embedded clang clang-devel gcc make cmake git
  if [ ! -x "$HOME/.cargo/bin/cargo" ]; then
    curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal
  fi
  # shellcheck disable=SC1091
  . "$HOME/.cargo/env"
  if [ ! -d "$CORE/.git" ]; then
    git clone "$CORE_REPO" "$CORE"
  fi
  git -C "$CORE" fetch origin --tags --prune --force
  git -C "$CORE" checkout -q "$sha"
  # The rust-toolchain.toml of the checkout selects the toolchain of the build, so llvm-tools must match it.
  (cd "$CORE" && rustup component add llvm-tools)
  rm -rf "$PGO"
  install -d "$PGO/data"
  # --target keeps the flags out of the build scripts.
  (cd "$CORE" && env \
    RUSTFLAGS="$GENERATE_FLAGS" \
    PHP_CONFIG=/usr/bin/php-config \
    CARGO_TARGET_DIR="$HOME/pgo-target" \
    cargo build --release --locked --bin rapira --target "$TRIPLE")
  install -d "$DEST/bin"
  install -m 0755 "$HOME/pgo-target/$TRIPLE/release/rapira" "$DEST/bin/rapira"
}

merge() {
  local profdata counts
  # shellcheck disable=SC1091
  . "$HOME/.cargo/env"
  profdata=$(cd "$CORE" && rustc --print sysroot)/lib/rustlib/$TRIPLE/bin/llvm-profdata
  "$profdata" merge -o "$PGO/merged.profdata" "$PGO"/data/*.profraw
  # Every HTTP request runs check_request in a worker, so a zero count means that the workers wrote no counters.
  counts=$("$profdata" show --counts --function=check_request "$PGO/merged.profdata")
  grep -q 'Block counts: \[.*[1-9]' <<<"$counts" ||
    die "the profile has no counts in check_request: the workers wrote no counters, or the suite has no HTTP target"
  python3 - "$PGO/pgo.json" "$(new_sha)" "$(cd "$CORE" && rustc -V)" "$("$profdata" --version | grep -m1 -o 'LLVM version .*')" \
    "$GENERATE_FLAGS" "$PROFILE_FILE" "$USE_FLAGS" "$@" <<'PY'
import json, sys

path, sha, rustc, llvm, generate, profile_file, use = sys.argv[1:8]
with open(path, "w") as f:
    json.dump({
        "sha": sha,
        "rustc": rustc,
        "llvm_profdata": llvm,
        "generate": {"rustflags": generate, "llvm_profile_file": profile_file},
        "use": {"rustflags": use},
        "targets": sys.argv[8:],
    }, f, indent=1)
    f.write("\n")
PY
}

cmd=${1:?build|start|merge}
shift
case "$cmd" in
build) build ;;
start)
  LLVM_PROFILE_FILE=$PROFILE_FILE exec bash "$(dirname "$0")/target.sh" start "${1:?tag}" "${2:?server}" "${3:?processes}" "$DEST" "${@:4}"
  ;;
merge) merge "$@" ;;
*) die "unknown command $cmd" ;;
esac
