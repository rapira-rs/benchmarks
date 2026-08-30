#!/usr/bin/env bash
# Runs on the server box; sourced by provision-server.sh and build-local.sh
# so the build policy has one home. A policy split between the two would put
# the flag cost into the A/B delta as if it were the code change.

# Frame pointers keep every build profilable; PLAIN=1 drops them for
# publishable fleet tables.
rustflags_for() { # plain
  if [ "$1" = 1 ]; then echo ""; else echo "-C force-frame-pointers=yes"; fi
}

build_rapira() { # srcdir tag rustflags
  local src=$1 tag=$2 rf=$3
  (cd "$src" && env \
    RUSTFLAGS="$rf" \
    CARGO_PROFILE_RELEASE_DEBUG=line-tables-only \
    PHP_CONFIG=/usr/bin/php-config \
    CARGO_TARGET_DIR="$HOME/core-target" \
    cargo build --release)
  install -m 0755 "$HOME/core-target/release/rapira" "/opt/bench/bin/rapira-$tag"
}
