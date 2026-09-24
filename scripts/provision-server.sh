#!/usr/bin/env bash
set -euo pipefail

BASE_REF=${BASE_REF:-main}
REF=${REF:?set REF (branch, tag, sha, or pr/N)}
LEGS=${LEGS:-rapira}
PLAIN=${PLAIN:-0}
CORE_REPO=${CORE_REPO:-https://github.com/rapira-rs/rapira}
FRANKEN_VERSION=${FRANKEN_VERSION:-1.12.4}
SYMFONY_SKELETON=${SYMFONY_SKELETON:-^7.3}
LARAVEL_VERSION=${LARAVEL_VERSION:-^12.0}
OCTANE_VERSION=${OCTANE_VERSION:-^2.12}
PROTOBUF_VERSION=${PROTOBUF_VERSION:-5.36.2}
RR_VERSION=${RR_VERSION:-2025.1.15}

BENCH=/opt/bench
CORE=$HOME/core
# shellcheck source=scripts/build-lib.sh
. "$(dirname "$0")/build-lib.sh"

echo "==> packages"
pkgs="php-cli php-devel php-embedded php-opcache clang clang-devel gcc make cmake git perf ethtool curl tar python3"
[ "$LEGS" != rapira ] && pkgs="$pkgs nginx php-fpm composer unzip"
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
build_one base "$base_sha"
build_one pr "$pr_sha"
echo "==> build rapira-ceiling at $pr_sha"
# build_one does not check out a ref whose marker matches.
git -C "$CORE" checkout -q "$pr_sha"
build_ceiling "$CORE" "$rustflags"

echo "==> metadata"
python3 - "$base_sha" "$pr_sha" "$BASE_REF" "$REF" "$rustflags" <<'PY'
import hashlib, json, os, platform, subprocess, sys

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
if os.path.exists("/opt/bench/bin/rapira-ceiling"):
    meta["ceiling_sha256"] = sha256("/opt/bench/bin/rapira-ceiling")
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

  echo "frankenphp $FRANKEN_VERSION" >$BENCH/fleet/versions.txt
fi

if [ "$LEGS" = frameworks ] || [ "$LEGS" = all ]; then
  echo "==> framework apps"
  APPS=$BENCH/fleet/apps
  install -d "$APPS"
  export COMPOSER_NO_INTERACTION=1

  if [ ! -f "$APPS/symfony/public/index.php" ]; then
    rm -rf "$APPS/symfony"
    composer create-project "symfony/skeleton:$SYMFONY_SKELETON" "$APPS/symfony" --no-progress
  fi
  install -d "$APPS/symfony/bench" "$APPS/symfony/src/Controller"
  install -m 0644 "$HOME"/bench-rig/fleet/symfony/bench/worker-rapira.php "$APPS/symfony/bench/"
  install -m 0644 "$HOME"/bench-rig/fleet/symfony/bench/worker-franken.php "$APPS/symfony/public/"
  install -m 0644 "$HOME"/bench-rig/fleet/symfony/index.php "$APPS/symfony/public/index.php"
  install -m 0644 "$HOME"/bench-rig/fleet/symfony/BenchController.php "$APPS/symfony/src/Controller/"
  install -m 0644 "$HOME"/bench-rig/fleet/static/app.css "$APPS/symfony/public/"
  install -m 0644 "$HOME"/bench-rig/fleet/static/tiny.css "$APPS/symfony/public/"
  printf 'APP_ENV=prod\nAPP_DEBUG=0\nAPP_SECRET=8f2f4c9a51e04d0bafd3a7f22c1e6b90\n' >"$APPS/symfony/.env.local"
  (cd "$APPS/symfony" &&
    composer dump-autoload --optimize --classmap-authoritative --quiet &&
    composer dump-env prod --quiet &&
    rm -rf var/cache/prod &&
    php bin/console cache:warmup -q)

  if [ ! -f "$APPS/laravel/public/index.php" ]; then
    rm -rf "$APPS/laravel"
    composer create-project "laravel/laravel:$LARAVEL_VERSION" "$APPS/laravel" --no-progress --no-scripts
  fi
  install -m 0644 "$HOME"/bench-rig/fleet/laravel/web.php "$APPS/laravel/routes/web.php"
  install -m 0644 "$HOME"/bench-rig/fleet/laravel/BenchController.php "$APPS/laravel/app/Http/Controllers/"
  install -d "$APPS/laravel/bench"
  install -m 0644 "$HOME"/bench-rig/fleet/laravel/bench/worker-rapira.php "$APPS/laravel/bench/"
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
  (cd "$APPS/laravel" &&
    composer require "laravel/octane:$OCTANE_VERSION" --no-progress --quiet &&
    php artisan key:generate --force -q &&
    composer dump-autoload --optimize --classmap-authoritative --quiet &&
    php artisan optimize -q)
  install -m 0644 "$APPS/laravel/vendor/laravel/octane/src/Commands/stubs/frankenphp-worker.php" \
    "$APPS/laravel/public/frankenphp-worker.php"

  {
    (cd "$APPS/symfony" && composer show 2>/dev/null | awk '{print "symfony:", $1, $2}')
    (cd "$APPS/laravel" && composer show 2>/dev/null | awk '{print "laravel:", $1, $2}')
  } >>$BENCH/fleet/versions.txt
fi

if [ "$LEGS" = grpc ] || [ "$LEGS" = all ]; then
  echo "==> grpc legs"

  if ! php -r "exit(phpversion('protobuf') === '$PROTOBUF_VERSION' ? 0 : 1);"; then
    echo "==> build ext-protobuf $PROTOBUF_VERSION"
    src=$HOME/protobuf-$PROTOBUF_VERSION
    rm -rf "$src"
    curl -fsSL "https://pecl.php.net/get/protobuf-$PROTOBUF_VERSION.tgz" | tar -xzf - -C "$HOME" "protobuf-$PROTOBUF_VERSION"
    (cd "$src" && phpize && ./configure && make -j"$(nproc)" && sudo make install)
    echo 'extension=protobuf.so' | sudo tee /etc/php.d/40-protobuf.ini >/dev/null
    php -r "exit(phpversion('protobuf') === '$PROTOBUF_VERSION' ? 0 : 1);" ||
      { echo "ERROR: ext-protobuf $PROTOBUF_VERSION is not loaded after the build"; exit 1; }
  fi

  if ! "$BENCH/fleet/rr" --version 2>/dev/null | grep -qF "rr version $RR_VERSION"; then
    echo "==> RoadRunner $RR_VERSION"
    curl -fsSL "https://github.com/roadrunner-server/roadrunner/releases/download/v$RR_VERSION/roadrunner-$RR_VERSION-linux-amd64.tar.gz" |
      tar -xzf - -C "$HOME" "roadrunner-$RR_VERSION-linux-amd64/rr"
    install -m 0755 "$HOME/roadrunner-$RR_VERSION-linux-amd64/rr" "$BENCH/fleet/rr"
  fi

  RRG=$BENCH/fleet/roadrunner-grpc
  install -d "$RRG"
  install -m 0644 "$HOME"/bench-rig/fleet/roadrunner/composer.json "$HOME"/bench-rig/fleet/roadrunner/composer.lock "$RRG/"
  (cd "$RRG" && COMPOSER_NO_INTERACTION=1 composer install --no-dev --no-progress --quiet)

  {
    echo "protobuf $PROTOBUF_VERSION"
    echo "roadrunner $RR_VERSION"
    (cd "$RRG" && composer show 2>/dev/null | awk '{print "roadrunner-grpc:", $1, $2}')
  } >>$BENCH/fleet/versions.txt
fi

echo "==> server provisioned: base=$base_sha pr=$pr_sha legs=$LEGS plain=$PLAIN"
