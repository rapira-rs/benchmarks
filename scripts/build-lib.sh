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
