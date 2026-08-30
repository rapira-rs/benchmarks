#!/usr/bin/env bash
# Runs on the server box as user fedora (ssh-driven by `make provision`).
# Env: REF (required; branch, tag, sha, or pr/N), BASE_REF, LEGS
# (rapira|all|frameworks), PLAIN=1 drops frame pointers (publishable fleet
# tables).
set -euo pipefail

BASE_REF=${BASE_REF:-main}
REF=${REF:?set REF (branch, tag, sha, or pr/N)}
LEGS=${LEGS:-rapira}
PLAIN=${PLAIN:-0}
CORE_REPO=${CORE_REPO:-https://github.com/rapira-rs/rapira}
FRANKEN_VERSION=${FRANKEN_VERSION:-1.12.4}
# 7.3.* packages sit behind composer security advisories that block
# resolution outright; ^7.3 lands on the advisory-clean current stable.
SYMFONY_SKELETON=${SYMFONY_SKELETON:-^7.3}
LARAVEL_VERSION=${LARAVEL_VERSION:-^12.0}
OCTANE_VERSION=${OCTANE_VERSION:-^2.12}

BENCH=/opt/bench
CORE=$HOME/core
# shellcheck source=scripts/build-lib.sh
. "$(dirname "$0")/build-lib.sh"

echo "==> packages"
# cmake: older ref lockfiles build zlib-ng from source through it.
pkgs="php-cli php-devel php-embedded php-opcache clang clang-devel gcc make cmake git perf ethtool curl tar python3"
# php-opcache is a separate Fedora package and its silent absence would
# corrupt exactly the classic-vs-fpm comparison; bindgen needs clang-devel.
[ "$LEGS" != rapira ] && pkgs="$pkgs nginx php-fpm composer unzip"
# Framework extension set for Symfony and Laravel; ctype, curl, fileinfo,
# openssl, session, and tokenizer already live in php-cli/php-common.
[ "$LEGS" = frameworks ] || [ "$LEGS" = all ] && pkgs="$pkgs php-mbstring php-xml php-pdo php-process php-sodium"
# shellcheck disable=SC2086
sudo dnf -y install $pkgs
php -r 'exit(ini_get("opcache.enable") ? 0 : 1);' || { echo "ERROR: opcache installed but off"; exit 1; }

echo "==> system knobs"
sudo tee /etc/sysctl.d/90-rapira-bench.conf >/dev/null <<'EOF'
kernel.perf_event_paranoid = -1
kernel.kptr_restrict = 0
net.core.somaxconn = 65535
EOF
sudo sysctl -q -p /etc/sysctl.d/90-rapira-bench.conf
sudo tee /etc/security/limits.d/90-rapira-bench.conf >/dev/null <<'EOF'
fedora soft nofile 1048576
fedora hard nofile 1048576
EOF

# The AMI root is 5 GiB; cloud-init growpart must have expanded it before the
# builds, or cargo dies with ENOSPC after minutes of billed time.
root_kb=$(df -Pk / | awk 'NR==2 {print $2}')
if [ "$root_kb" -lt $((30 * 1024 * 1024)) ]; then
  echo "ERROR: root filesystem is $((root_kb / 1024 / 1024)) GiB; expected >= 30 GiB"
  exit 1
fi

echo "==> rust toolchain"
if [ ! -x "$HOME/.cargo/bin/cargo" ]; then
  curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal
fi
# shellcheck disable=SC1091
. "$HOME/.cargo/env"

sudo install -d -o fedora -g fedora $BENCH $BENCH/bin $BENCH/log $BENCH/run $BENCH/perf $BENCH/fleet

echo "==> core checkout"
if [ ! -d "$CORE/.git" ]; then
  git clone "$CORE_REPO" "$CORE"
fi
git -C "$CORE" fetch origin --tags --prune

resolve_ref() {
  local r=$1
  case "$r" in
  pr/*)
    git -C "$CORE" fetch -q origin "refs/pull/${r#pr/}/head"
    git -C "$CORE" rev-parse FETCH_HEAD
    ;;
  *)
    git -C "$CORE" rev-parse --verify -q "origin/$r^{commit}" 2>/dev/null ||
      git -C "$CORE" rev-parse --verify "$r^{commit}"
    ;;
  esac
}

rustflags=$(rustflags_for "$PLAIN")

build_one() {
  local tag=$1 sha=$2
  local marker=$BENCH/run/build-$tag.marker
  if [ -f "$marker" ] && [ "$(cat "$marker")" = "$sha|$rustflags" ] && [ -x "$BENCH/bin/rapira-$tag" ]; then
    echo "==> rapira-$tag already built at $sha"
    return 0
  fi
  echo "==> build rapira-$tag at $sha"
  git -C "$CORE" checkout -q "$sha"
  build_rapira "$CORE" "$tag" "$rustflags"
  echo "$sha|$rustflags" >"$marker"
}

base_sha=$(resolve_ref "$BASE_REF")
pr_sha=$(resolve_ref "$REF")
# Base first: the pr build then reuses the shared dependency cache.
build_one base "$base_sha"
build_one pr "$pr_sha"

echo "==> metadata"
python3 - "$base_sha" "$pr_sha" "$BASE_REF" "$REF" "$rustflags" <<'PY'
import hashlib, json, platform, subprocess, sys

def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

base_sha, pr_sha, base_ref, ref, rustflags = sys.argv[1:6]
meta = {
    "base_ref": base_ref,
    "base_sha": base_sha,
    "pr_ref": ref,
    "pr_sha": pr_sha,
    "base_sha256": sha256("/opt/bench/bin/rapira-base"),
    "pr_sha256": sha256("/opt/bench/bin/rapira-pr"),
    "base_rustflags": rustflags,
    "pr_rustflags": rustflags,
    "opcache": 1,
    "kernel": platform.release(),
    "php": subprocess.run(["php", "-v"], capture_output=True, text=True).stdout.splitlines()[0],
    "php_embedded": subprocess.run(["rpm", "-q", "php-embedded"], capture_output=True, text=True).stdout.strip(),
}
with open("/opt/bench/meta.json", "w") as f:
    json.dump(meta, f, indent=1)
PY

if [ "$LEGS" != rapira ]; then
  echo "==> fleet legs"

  if [ ! -x $BENCH/fleet/frankenphp ]; then
    curl -fSL -o $BENCH/fleet/frankenphp \
      "https://github.com/php/frankenphp/releases/download/v$FRANKEN_VERSION/frankenphp-linux-x86_64"
    chmod +x $BENCH/fleet/frankenphp
  fi

  # Competitor provenance for the fleet run directory.
  echo "frankenphp $FRANKEN_VERSION" >$BENCH/fleet/versions.txt
fi

if [ "$LEGS" = frameworks ] || [ "$LEGS" = all ]; then
  echo "==> framework apps"
  APPS=$BENCH/fleet/apps
  install -d "$APPS"
  export COMPOSER_NO_INTERACTION=1

  # Guard on the finished install, not the directory: a failed create-project
  # leaves a partial tree behind.
  if [ ! -f "$APPS/symfony/public/index.php" ]; then
    rm -rf "$APPS/symfony"
    composer create-project "symfony/skeleton:$SYMFONY_SKELETON" "$APPS/symfony" --no-progress
  fi
  # The bench overlay: route table, controller, worker entries, prod env.
  # Re-installed every provision so a rig edit reaches the box without a
  # rebuild of the app. The franken worker twin goes into public/ where
  # php_server can resolve requests to it.
  install -d "$APPS/symfony/bench" "$APPS/symfony/src/Controller"
  install -m 0644 "$HOME"/bench-rig/fleet/symfony/bench/worker-rapira.php "$APPS/symfony/bench/"
  install -m 0644 "$HOME"/bench-rig/fleet/symfony/bench/worker-franken.php "$APPS/symfony/public/"
  install -m 0644 "$HOME"/bench-rig/fleet/symfony/index.php "$APPS/symfony/public/index.php"
  install -m 0644 "$HOME"/bench-rig/fleet/symfony/BenchController.php "$APPS/symfony/src/Controller/"
  printf 'APP_ENV=prod\nAPP_DEBUG=0\nAPP_SECRET=8f2f4c9a51e04d0bafd3a7f22c1e6b90\n' >"$APPS/symfony/.env.local"
  # dump-env spares the classic and fpm legs the per-request Dotenv parse,
  # the standard prod deploy step.
  (cd "$APPS/symfony" &&
    composer dump-autoload --optimize --classmap-authoritative --quiet &&
    composer dump-env prod --quiet &&
    rm -rf var/cache/prod &&
    php bin/console cache:warmup -q)

  if [ ! -f "$APPS/laravel/public/index.php" ]; then
    # --no-scripts skips the skeleton's sqlite create-and-migrate hook; the
    # bench app never touches a database. Discovery runs on the octane
    # require below, once the bench .env is in place.
    rm -rf "$APPS/laravel"
    composer create-project "laravel/laravel:$LARAVEL_VERSION" "$APPS/laravel" --no-progress --no-scripts
  fi
  install -m 0644 "$HOME"/bench-rig/fleet/laravel/web.php "$APPS/laravel/routes/web.php"
  install -m 0644 "$HOME"/bench-rig/fleet/laravel/BenchController.php "$APPS/laravel/app/Http/Controllers/"
  install -d "$APPS/laravel/bench"
  install -m 0644 "$HOME"/bench-rig/fleet/laravel/bench/worker-rapira.php "$APPS/laravel/bench/"
  # No database and no filesystem state per request: array session and cache.
  cat >"$APPS/laravel/.env" <<'EOF'
APP_NAME=bench
APP_ENV=production
APP_DEBUG=false
APP_KEY=
APP_URL=http://localhost:8080
LOG_CHANNEL=stderr
LOG_LEVEL=error
LOG_DEPRECATIONS_CHANNEL=null
SESSION_DRIVER=array
SESSION_LIFETIME=120
CACHE_STORE=array
QUEUE_CONNECTION=sync
BROADCAST_CONNECTION=null
FILESYSTEM_DISK=local
MAIL_MAILER=log
DB_CONNECTION=sqlite
APP_MAINTENANCE_DRIVER=file
BCRYPT_ROUNDS=4
OCTANE_SERVER=frankenphp
EOF
  # The key must exist before config:cache freezes app.key. optimize covers
  # config, events, routes, and views.
  (cd "$APPS/laravel" &&
    composer require "laravel/octane:$OCTANE_VERSION" --no-progress --quiet &&
    php artisan key:generate --force -q &&
    composer dump-autoload --optimize --classmap-authoritative --quiet &&
    php artisan optimize -q)
  # Octane's FrankenPHP entry, self-locating from public/; what
  # octane:install would have copied.
  install -m 0644 "$APPS/laravel/vendor/laravel/octane/src/Commands/stubs/frankenphp-worker.php" \
    "$APPS/laravel/public/frankenphp-worker.php"

  {
    (cd "$APPS/symfony" && composer show 2>/dev/null | awk '{print "symfony:", $1, $2}')
    (cd "$APPS/laravel" && composer show 2>/dev/null | awk '{print "laravel:", $1, $2}')
  } >>$BENCH/fleet/versions.txt
fi

echo "==> server provisioned: base=$base_sha pr=$pr_sha legs=$LEGS plain=$PLAIN"
