#!/usr/bin/env bash
# Creates the committed composer files of the framework apps:
# apps/symfony/composer.json, apps/symfony/composer.lock,
# apps/laravel/composer.json, and apps/laravel/composer.lock.
# Run it on the operator machine with PHP 8.5, the PHP of the server box, and composer.
# Commit the four files after a run.
set -euo pipefail

SYMFONY_SKELETON=${SYMFONY_SKELETON:-symfony/skeleton:^7.3}
LARAVEL_SKELETON=${LARAVEL_SKELETON:-laravel/laravel:v12.12.2}
OCTANE_VERSION=${OCTANE_VERSION:-^2.12}

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
export COMPOSER_NO_INTERACTION=1

# Symfony Flex moves the flex-require list of the skeleton into require during
# the first install, so the lock comes from a full create-project.
lock_symfony() {
  local dir=$WORK/symfony
  composer create-project --no-progress --no-scripts "$SYMFONY_SKELETON" "$dir"
  install -m 0644 "$dir/composer.json" "$dir/composer.lock" "$ROOT/apps/symfony/"
}

# The Laravel app files come from the skeleton package itself. The skeleton
# version goes into extra.bench-skeleton before the lock, so the lock hash
# covers it and box/provision-server.sh creates the same skeleton.
lock_laravel() {
  local dir=$WORK/laravel
  composer create-project --no-progress --no-scripts --no-install "$LARAVEL_SKELETON" "$dir"
  composer --working-dir="$dir" config extra.bench-skeleton "$LARAVEL_SKELETON"
  composer --working-dir="$dir" require --no-update "laravel/octane:$OCTANE_VERSION"
  composer --working-dir="$dir" update --no-progress --no-scripts --no-install
  install -m 0644 "$dir/composer.json" "$dir/composer.lock" "$ROOT/apps/laravel/"
}

lock_symfony
lock_laravel
composer validate --no-check-publish --no-check-all "$ROOT/apps/symfony/composer.json"
composer validate --no-check-publish --no-check-all "$ROOT/apps/laravel/composer.json"
