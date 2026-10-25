#!/usr/bin/env bash
# Creates apps/grpc/composer.lock from apps/grpc/composer.json.
# Run it on the operator machine with PHP 8.5 and Composer.
# Commit the lock file after a run.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
export COMPOSER_NO_INTERACTION=1

install -m 0644 "$ROOT/apps/grpc/composer.json" "$WORK/"
composer --working-dir="$WORK" update --no-progress --no-install
install -m 0644 "$WORK/composer.lock" "$ROOT/apps/grpc/composer.lock"
