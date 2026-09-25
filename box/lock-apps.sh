#!/usr/bin/env bash
# Creates the committed files of the apps that provisioning installs from a lock:
# apps/yii3/composer.lock and apps/yii3/expect.json from the commit in apps/yii3/source.toml,
# and apps/grpc/composer.lock from apps/grpc/composer.json.
# Run it on the operator machine with PHP 8.5, Composer, and a Valkey or Redis server on
# 127.0.0.1:6379, for example: docker run --rm -d -p 127.0.0.1:6379:6379 valkey/valkey:9.1.2-alpine
# The Yii3 app reads its route cache from that server when it builds its container.
# Commit the three files after a run.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
export COMPOSER_NO_INTERACTION=1

# source_value KEY prints the value of KEY in apps/yii3/source.toml.
source_value() {
  python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))[sys.argv[2]])' "$ROOT/apps/yii3/source.toml" "$1"
}

check_valkey() {
  if ! python3 -c 'import socket; socket.create_connection(("127.0.0.1", 6379), 2).close()' 2>/dev/null; then
    echo "ERROR: no Valkey on 127.0.0.1:6379; run: docker run --rm -d -p 127.0.0.1:6379:6379 valkey/valkey:9.1.2-alpine" >&2
    exit 1
  fi
}

# composer install without a lock resolves the constraints and writes the lock from the pristine
# composer.json. composer update would also bump the constraints of that file.
# The app requires ext-pdo_pgsql, which the operator machine does not need for the lock.
lock_yii3() {
  local dir=$WORK/yii3 pid
  git clone -q "$(source_value repo)" "$dir"
  git -C "$dir" checkout -q "$(source_value commit)"
  composer --working-dir="$dir" install --no-dev --no-scripts --no-progress --classmap-authoritative --ignore-platform-req=ext-pdo_pgsql
  install -m 0644 "$dir/composer.lock" "$ROOT/apps/yii3/composer.lock"
  php -S 127.0.0.1:8765 -t "$dir/public" "$dir/public/index.php" >"$WORK/php.log" 2>&1 &
  pid=$!
  rm -f "$ROOT/apps/yii3/expect.json"
  for _ in $(seq 1 20); do
    curl -fsS -o "$ROOT/apps/yii3/expect.json" http://127.0.0.1:8765/ 2>/dev/null && break
    sleep 0.5
  done
  kill "$pid" 2>/dev/null || true
  if [ ! -s "$ROOT/apps/yii3/expect.json" ]; then
    cat "$WORK/php.log" >&2
    echo "ERROR: no body from the Yii3 app" >&2
    exit 1
  fi
}

lock_grpc() {
  local dir=$WORK/grpc
  install -d "$dir"
  install -m 0644 "$ROOT/apps/grpc/composer.json" "$dir/"
  composer --working-dir="$dir" update --no-progress --no-install
  install -m 0644 "$dir/composer.lock" "$ROOT/apps/grpc/composer.lock"
}

check_valkey
lock_yii3
lock_grpc
