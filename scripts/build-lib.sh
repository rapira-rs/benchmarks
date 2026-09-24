#!/usr/bin/env bash

rustflags_for() {
  if [ "$1" = 1 ]; then echo ""; else echo "-C force-frame-pointers=yes"; fi
}

build_rapira() {
  local src=$1 tag=$2 rf=$3
  (cd "$src" && env \
    RUSTFLAGS="$rf" \
    CARGO_PROFILE_RELEASE_DEBUG=line-tables-only \
    PHP_CONFIG=/usr/bin/php-config \
    CARGO_TARGET_DIR="$HOME/core-target" \
    cargo build --release)
  install -m 0755 "$HOME/core-target/release/rapira" "/opt/bench/bin/rapira-$tag"
}

# The transport ceiling is the rapira_grpc example echo_ceiling. When the
# source has no example, remove the old binary: a ceiling from another ref
# must not stay.
build_ceiling() {
  local src=$1 rf=$2
  if [ ! -f "$src/crates/plugins/grpc/examples/echo_ceiling.rs" ]; then
    rm -f /opt/bench/bin/rapira-ceiling
    echo "NOTE: $src has no echo_ceiling example; rapira-ceiling is not built"
    return 0
  fi
  (cd "$src" && env \
    RUSTFLAGS="$rf" \
    CARGO_PROFILE_RELEASE_DEBUG=line-tables-only \
    PHP_CONFIG=/usr/bin/php-config \
    CARGO_TARGET_DIR="$HOME/core-target" \
    cargo build --release -p rapira_grpc --example echo_ceiling)
  install -m 0755 "$HOME/core-target/release/examples/echo_ceiling" /opt/bench/bin/rapira-ceiling
}
