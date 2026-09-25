#!/usr/bin/env bash
# Provisions the server box for the targets of one suite.
#
# Environment:
#   NIGHTLY         sha7 of a build on the nightly release of the core repository. REF is then ignored.
#   REF, BASE_REF   refs to build on the box when NIGHTLY is empty: a branch, a tag, a sha, or pr/N.
#   NEEDS           the server kinds and apps of the suite, space separated, from python3 -m rig needs.
#   FRAME_POINTERS  1 builds rapira with frame pointers for a perf session.
#
# Results:
#   /opt/bench/rapira/<sha7>/bin/rapira   the rapira under test
#   /opt/bench/rapira/base/bin/rapira     the base build, only for a server build
#   /opt/bench/meta.json                  the rapira identity for the run file
#   /opt/bench/versions.json              one version line per server and runtime
#   /opt/bench/php.ini                    the shared php.ini
#   /opt/bench/bin/frankenphp, /opt/bench/bin/rr
#   /opt/bench/apps/symfony, /opt/bench/apps/laravel, /opt/bench/apps/roadrunner-grpc
set -euo pipefail

NIGHTLY=${NIGHTLY:-}
REF=${REF:-}
BASE_REF=${BASE_REF:-main}
NEEDS=${NEEDS:-}
FRAME_POINTERS=${FRAME_POINTERS:-0}
CORE_SLUG=${CORE_SLUG:-rapira-rs/rapira}
CORE_REPO=${CORE_REPO:-https://github.com/$CORE_SLUG}
FRANKEN_VERSION=${FRANKEN_VERSION:-1.12.7}
PROTOBUF_VERSION=${PROTOBUF_VERSION:-5.36.2}
RR_VERSION=${RR_VERSION:-2025.1.15}

BENCH=/opt/bench
RIG=$HOME/bench-rig
CORE=$HOME/core
export COMPOSER_NO_INTERACTION=1

# needs WORD succeeds when WORD is in NEEDS.
needs() {
  case " $NEEDS " in
  *" $1 "*) return 0 ;;
  *) return 1 ;;
  esac
}

install_packages() {
  local pkgs="php-cli php-opcache ethtool curl tar diffutils python3 chrony"
  if [ -n "$NIGHTLY" ]; then
    # The runtime libraries of the nightly build: the rpm depends list in nfpm.yaml of the core repository.
    pkgs="$pkgs libpq openssl-libs libcurl libxml2 sqlite-libs oniguruma zlib"
  else
    pkgs="$pkgs php-devel php-embedded clang clang-devel gcc make cmake git perf"
  fi
  if needs php-fpm || needs nginx-rapira; then
    pkgs="$pkgs nginx"
  fi
  if needs php-fpm; then
    pkgs="$pkgs php-fpm"
  fi
  if needs symfony || needs laravel || needs roadrunner || needs grpc; then
    pkgs="$pkgs composer unzip git"
  fi
  if needs symfony || needs laravel; then
    pkgs="$pkgs php-mbstring php-xml php-pdo php-process php-sodium"
  fi
  # shellcheck disable=SC2086
  sudo dnf -y install $pkgs
}

system_knobs() {
  sudo tee /etc/sysctl.d/90-rapira-bench.conf >/dev/null <<'CONF'
kernel.perf_event_paranoid = -1
kernel.kptr_restrict = 0
net.core.somaxconn = 65535
CONF
  sudo sysctl -q -p /etc/sysctl.d/90-rapira-bench.conf
  sudo tee /etc/security/limits.d/90-rapira-bench.conf >/dev/null <<'CONF'
fedora soft nofile 1048576
fedora hard nofile 1048576
CONF
}

# The loaders start each stage at a shared wall-clock time, so every box needs a synchronized clock.
check_clock() {
  sudo systemctl enable --now chronyd
  if ! chronyc waitsync 60 0.01 0 1 >/dev/null; then
    echo "ERROR: chrony is not synchronized after 60 s"
    chronyc tracking
    exit 1
  fi
  if ! chronyc tracking | grep -q '^Leap status *: Normal$'; then
    echo "ERROR: the chrony leap status is not Normal"
    chronyc tracking
    exit 1
  fi
}

# record_version NAME TEXT stores one version line in /opt/bench/versions.json.
record_version() {
  python3 - "$BENCH/versions.json" "$1" "$2" <<'PY'
import json, os, sys

path, name, text = sys.argv[1:4]
doc = {}
if os.path.exists(path):
    with open(path) as f:
        doc = json.load(f)
doc[name] = text
with open(path, "w") as f:
    json.dump(doc, f, indent=1, sort_keys=True)
    f.write("\n")
PY
}

# write_meta DIR REF SHA VERSION BUILD ASSET RUSTFLAGS [BASE_DIR BASE_REF BASE_SHA BASE_VERSION]
# writes /opt/bench/meta.json with the rapira under test and the optional base build.
write_meta() {
  python3 - "$@" <<'PY'
import hashlib, json, platform, sys

def record(directory, ref, sha, version, build, asset, rustflags):
    with open(directory + "/bin/rapira", "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return {
        "ref": ref,
        "sha": sha,
        "version": version,
        "build": build,
        "asset": asset or None,
        "binary_sha256": digest,
        "rustflags": rustflags if build == "server" else None,
        "dir": directory,
    }

args = sys.argv[1:]
build, asset, rustflags = args[4], args[5], args[6]
meta = {
    "rapira": record(args[0], args[1], args[2], args[3], build, asset, rustflags),
    "base": None,
    "kernel": platform.release(),
}
if len(args) == 11:
    meta["base"] = record(args[7], args[8], args[9], args[10], build, "", rustflags)
with open("/opt/bench/meta.json", "w") as f:
    json.dump(meta, f, indent=1)
    f.write("\n")
PY
}

# resolve_nightly prints the full sha, the version, the tarball name, and the checksum file name
# of the NIGHTLY build on the nightly release.
resolve_nightly() {
  python3 - "$CORE_SLUG" "$NIGHTLY" <<'PY'
import json, re, sys, urllib.request

slug, sha7 = sys.argv[1], sys.argv[2]


def get(path):
    request = urllib.request.Request("https://api.github.com/repos/" + slug + path, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


# The core Nightly workflow moves the nightly tag to each new build and deletes the assets of older builds.
sha = get("/git/ref/tags/nightly")["object"]["sha"]
if not sha.startswith(sha7):
    sys.exit(f"ERROR: the nightly tag is at {sha[:7]}, not {sha7}; the release has no assets for {sha7}, rerun with NIGHTLY={sha[:7]}")
names = [asset["name"] for asset in get("/releases/tags/nightly")["assets"]]
tarball = re.compile(r"rapira-v(.+-nightly\." + re.escape(sha7) + r")-php8\.5-linux-x86_64\.tar\.gz")
found = [m for m in map(tarball.fullmatch, names) if m]
if len(found) != 1:
    sys.exit(f"ERROR: expected one php8.5 linux x86_64 tarball for {sha7} on the nightly release, found {len(found)}")
version = found[0].group(1)
sums = f"rapira-v{version}-SHA256SUMS.txt"
if sums not in names:
    sys.exit(f"ERROR: {sums} is missing on the nightly release")
print(sha, version, found[0].group(0), sums)
PY
}

install_nightly() {
  local resolved sha version asset sums
  local dir=$BENCH/rapira/$NIGHTLY dl=$HOME/nightly
  resolved=$(resolve_nightly)
  read -r sha version asset sums <<<"$resolved"
  rm -rf "$dl" "$dir"
  install -d "$dl" "$dir"
  curl -fsSL --retry 3 -o "$dl/$asset" "https://github.com/$CORE_SLUG/releases/download/nightly/$asset"
  curl -fsSL --retry 3 -o "$dl/$sums" "https://github.com/$CORE_SLUG/releases/download/nightly/$sums"
  (cd "$dl" && awk -v name="$asset" '$2 == name' "$sums" | sha256sum -c -)
  tar -xzf "$dl/$asset" -C "$dir" --strip-components=1
  if ldd "$dir/bin/rapira" 2>/dev/null | grep -F 'not found'; then
    echo "ERROR: $dir/bin/rapira has missing libraries"
    exit 1
  fi
  write_meta "$dir" nightly "$sha" "$version" nightly "$asset" ""
}

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

# build_one DIR SHA RUSTFLAGS builds rapira at SHA into DIR/bin/rapira and skips a build that is already there.
build_one() {
  local dir=$1 sha=$2 rustflags=$3
  local marker=$dir/build.marker
  if [ -f "$marker" ] && [ "$(cat "$marker")" = "$sha|$rustflags" ] && [ -x "$dir/bin/rapira" ]; then
    echo "==> $dir already built at $sha"
    return 0
  fi
  echo "==> build $dir at $sha"
  git -C "$CORE" checkout -q "$sha"
  (cd "$CORE" && env \
    RUSTFLAGS="$rustflags" \
    CARGO_PROFILE_RELEASE_DEBUG=line-tables-only \
    PHP_CONFIG=/usr/bin/php-config \
    CARGO_TARGET_DIR="$HOME/core-target" \
    cargo build --release)
  install -d "$dir/bin"
  install -m 0755 "$HOME/core-target/release/rapira" "$dir/bin/rapira"
  echo "$sha|$rustflags" >"$marker"
}

build_server() {
  local root_kb base_sha pr_sha pr7 pr_version base_version
  local rustflags=""
  root_kb=$(df -Pk / | awk 'NR==2 {print $2}')
  if [ "$root_kb" -lt $((30 * 1024 * 1024)) ]; then
    echo "ERROR: root filesystem is $((root_kb / 1024 / 1024)) GiB; expected >= 30 GiB"
    exit 1
  fi
  if [ ! -x "$HOME/.cargo/bin/cargo" ]; then
    curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal
  fi
  # shellcheck disable=SC1091
  . "$HOME/.cargo/env"
  if [ ! -d "$CORE/.git" ]; then
    git clone "$CORE_REPO" "$CORE"
  fi
  git -C "$CORE" fetch origin --tags --prune
  if [ "$FRAME_POINTERS" = 1 ]; then
    rustflags="-C force-frame-pointers=yes"
  fi
  base_sha=$(resolve_ref "$BASE_REF")
  pr_sha=$(resolve_ref "$REF")
  pr7=$(git -C "$CORE" rev-parse --short=7 "$pr_sha")
  build_one "$BENCH/rapira/base" "$base_sha" "$rustflags"
  build_one "$BENCH/rapira/$pr7" "$pr_sha" "$rustflags"
  pr_version=$(git -C "$CORE" describe --tags --always "$pr_sha")
  base_version=$(git -C "$CORE" describe --tags --always "$base_sha")
  write_meta "$BENCH/rapira/$pr7" "$REF" "$pr_sha" "$pr_version" server "" "$rustflags" \
    "$BENCH/rapira/base" "$BASE_REF" "$base_sha" "$base_version"
}

install_frankenphp() {
  local current
  current=$("$BENCH/bin/frankenphp" version 2>/dev/null || true)
  case $current in
  *"FrankenPHP v$FRANKEN_VERSION "*) ;;
  *)
    # The glibc build: https://frankenphp.dev/docs/performance/#avoid-musl-in-production-prefer-glibc-builds
    curl -fsSL --retry 3 -o "$BENCH/bin/frankenphp" \
      "https://github.com/php/frankenphp/releases/download/v$FRANKEN_VERSION/frankenphp-linux-x86_64-gnu"
    chmod 0755 "$BENCH/bin/frankenphp"
    ;;
  esac
  if ldd "$BENCH/bin/frankenphp" 2>/dev/null | grep -F 'not found'; then
    echo "ERROR: $BENCH/bin/frankenphp has missing libraries"
    exit 1
  fi
  record_version frankenphp "$("$BENCH/bin/frankenphp" version)"
}

# The nightly tarball ships no PHP headers, so only a server build gets ext-protobuf.
install_protobuf() {
  if php -r "exit(phpversion('protobuf') === '$PROTOBUF_VERSION' ? 0 : 1);"; then
    return 0
  fi
  local src=$HOME/protobuf-$PROTOBUF_VERSION
  rm -rf "$src"
  curl -fsSL "https://pecl.php.net/get/protobuf-$PROTOBUF_VERSION.tgz" | tar -xzf - -C "$HOME" "protobuf-$PROTOBUF_VERSION"
  (cd "$src" && phpize && ./configure && make -j"$(nproc)" && sudo make install)
  echo 'extension=protobuf.so' | sudo tee /etc/php.d/40-protobuf.ini >/dev/null
  if ! php -r "exit(phpversion('protobuf') === '$PROTOBUF_VERSION' ? 0 : 1);"; then
    echo "ERROR: ext-protobuf $PROTOBUF_VERSION is not loaded after the build"
    exit 1
  fi
}

install_roadrunner() {
  local current
  local rrg=$BENCH/apps/roadrunner-grpc
  current=$("$BENCH/bin/rr" --version 2>/dev/null || true)
  case $current in
  *"rr version $RR_VERSION "*) ;;
  *)
    curl -fsSL --retry 3 "https://github.com/roadrunner-server/roadrunner/releases/download/v$RR_VERSION/roadrunner-$RR_VERSION-linux-amd64.tar.gz" |
      tar -xzf - -C "$HOME" "roadrunner-$RR_VERSION-linux-amd64/rr"
    install -m 0755 "$HOME/roadrunner-$RR_VERSION-linux-amd64/rr" "$BENCH/bin/rr"
    ;;
  esac
  rm -rf "$rrg"
  install -d "$rrg"
  install -m 0644 "$RIG/servers/roadrunner/composer.json" "$RIG/servers/roadrunner/composer.lock" "$rrg/"
  composer --working-dir="$rrg" install --no-dev --no-progress
  record_version roadrunner "$("$BENCH/bin/rr" --version)"
}

install_symfony() {
  local dir=$BENCH/apps/symfony
  rm -rf "$dir"
  install -d "$dir"
  install -m 0644 "$RIG/apps/symfony/composer.json" "$RIG/apps/symfony/composer.lock" "$dir/"
  composer --working-dir="$dir" install --no-dev --no-progress --no-scripts
  # Symfony Flex runs its recipes only in create-project. recipes:install creates bin/, config/, public/, and src/.
  composer --working-dir="$dir" recipes:install --force --reset
  install -d "$dir/bench" "$dir/src/Controller"
  install -m 0644 "$RIG/apps/symfony/bench/worker-rapira.php" "$dir/bench/"
  install -m 0644 "$RIG/apps/symfony/bench/worker-franken.php" "$dir/public/"
  install -m 0644 "$RIG/apps/symfony/index.php" "$dir/public/index.php"
  install -m 0644 "$RIG/apps/symfony/BenchController.php" "$dir/src/Controller/"
  install -m 0644 "$RIG/apps/static/app.css" "$RIG/apps/static/tiny.css" "$dir/public/"
  printf 'APP_ENV=prod\nAPP_DEBUG=0\nAPP_SECRET=8f2f4c9a51e04d0bafd3a7f22c1e6b90\n' >"$dir/.env.local"
  (cd "$dir" &&
    composer dump-autoload --optimize --classmap-authoritative --no-dev --quiet &&
    composer dump-env prod --quiet &&
    rm -rf var/cache/prod &&
    php bin/console cache:warmup -q)
}

install_laravel() {
  local dir=$BENCH/apps/laravel skeleton
  skeleton=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["extra"]["bench-skeleton"])' "$RIG/apps/laravel/composer.json")
  rm -rf "$dir"
  # The Laravel app files come from the skeleton package. The vendor tree comes from the committed lock.
  composer create-project --no-progress --no-scripts --no-install "$skeleton" "$dir"
  install -m 0644 "$RIG/apps/laravel/composer.json" "$RIG/apps/laravel/composer.lock" "$dir/"
  install -m 0644 "$RIG/apps/laravel/web.php" "$dir/routes/web.php"
  install -m 0644 "$RIG/apps/laravel/BenchController.php" "$dir/app/Http/Controllers/"
  install -d "$dir/bench"
  install -m 0644 "$RIG/apps/laravel/bench/worker-rapira.php" "$dir/bench/"
  cat >"$dir/.env" <<'ENV'
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
ENV
  (cd "$dir" &&
    composer install --no-dev --no-progress --no-scripts &&
    php artisan key:generate --force -q &&
    composer dump-autoload --optimize --classmap-authoritative --no-dev --quiet &&
    php artisan optimize -q)
  install -m 0644 "$dir/vendor/laravel/octane/src/Commands/stubs/frankenphp-worker.php" "$dir/public/frankenphp-worker.php"
}

if [ -z "$NIGHTLY" ] && [ -z "$REF" ]; then
  echo "ERROR: set NIGHTLY=<sha7> or REF=<branch, tag, sha, or pr/N>"
  exit 1
fi

echo "==> packages"
install_packages
echo "==> system knobs"
system_knobs
echo "==> clock"
check_clock

sudo install -d -o fedora -g fedora "$BENCH" "$BENCH/bin" "$BENCH/run" "$BENCH/log" "$BENCH/apps" "$BENCH/rapira"
rm -f "$BENCH/versions.json"
install -m 0644 "$RIG/servers/php.ini" "$BENCH/php.ini"
# Fedora PHP reads /etc/php.d after PHPRC, and its 10-opcache.ini there sets other opcache values.
sudo install -m 0644 "$RIG/servers/php.ini" /etc/php.d/99-bench.ini
ini=$(php -c "$BENCH/php.ini" -r 'echo ini_get("opcache.enable_cli"), " ", ini_get("opcache.validate_timestamps"), " ", ini_get("opcache.memory_consumption"), " ", ini_get("display_errors");')
if [ "$ini" != "1 0 256 0" ]; then
  echo "ERROR: effective php.ini values are $ini, expected 1 0 256 0"
  exit 1
fi
record_version php "$(php -v | sed -n 1p)"

if [ -n "$NIGHTLY" ]; then
  echo "==> rapira nightly $NIGHTLY"
  install_nightly
else
  echo "==> rapira server build of $REF and $BASE_REF"
  build_server
fi

if needs frankenphp; then
  echo "==> frankenphp $FRANKEN_VERSION"
  install_frankenphp
fi
if needs php-fpm; then
  record_version php-fpm "$(php-fpm -v | sed -n 1p)"
fi
if needs php-fpm || needs nginx-rapira; then
  record_version nginx "$(nginx -v 2>&1)"
fi
if needs roadrunner || needs grpc; then
  echo "==> grpc runtimes"
  if [ -z "$NIGHTLY" ]; then
    install_protobuf
  fi
  install_roadrunner
  record_version protobuf "$(php -r 'echo phpversion("protobuf") ?: "ext-protobuf not loaded";')"
fi
if needs symfony; then
  echo "==> symfony"
  install_symfony
fi
if needs laravel; then
  echo "==> laravel"
  install_laravel
fi

echo "==> server provisioned: needs=$NEEDS"
python3 -c 'import json; print(json.dumps(json.load(open("/opt/bench/meta.json"))["rapira"]))'
