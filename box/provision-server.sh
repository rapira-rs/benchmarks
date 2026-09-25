#!/usr/bin/env bash
# Provisions the server box for the targets of one suite.
#
# Environment:
#   NIGHTLY         sha7 of a build on the nightly release of the core repository. REF is then ignored.
#   REF             a ref to build on the box when NIGHTLY is empty: a branch, a tag, a sha, or pr/N.
#   NEEDS           the server kinds and apps of the suite, space separated, from python3 -m rig needs.
#   FRAME_POINTERS  1 builds rapira with frame pointers for a perf session.
#
# Results:
#   /opt/bench/rapira/<sha7>/bin/rapira   the rapira under test
#   /opt/bench/meta.json                  the rapira identity for the run file
#   /opt/bench/versions.json              one version line per runtime
#   /opt/bench/php.ini                    the shared php.ini
#   /opt/bench/apps/yii3                  the Yii3 app with its vendor tree
#   /opt/bench/apps/grpc/vendor           the PHP protobuf runtime of the gRPC app
set -euo pipefail

NIGHTLY=${NIGHTLY:-}
REF=${REF:-}
NEEDS=${NEEDS:-}
FRAME_POINTERS=${FRAME_POINTERS:-0}
CORE_SLUG=${CORE_SLUG:-rapira-rs/rapira}
CORE_REPO=${CORE_REPO:-https://github.com/$CORE_SLUG}

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
  if needs yii3 || needs grpc; then
    pkgs="$pkgs composer unzip git"
  fi
  if needs yii3; then
    # The PHP modules of the platform requirements of the app, and the cache server of its routes.
    pkgs="$pkgs php-mbstring php-xml php-pdo php-pgsql valkey"
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

# write_meta DIR REF SHA VERSION BUILD ASSET RUSTFLAGS writes /opt/bench/meta.json with the rapira under test.
write_meta() {
  python3 - "$@" <<'PY'
import hashlib, json, platform, sys

directory, ref, sha, version, build, asset, rustflags = sys.argv[1:8]
with open(directory + "/bin/rapira", "rb") as f:
    digest = hashlib.sha256(f.read()).hexdigest()
meta = {
    "rapira": {
        "ref": ref,
        "sha": sha,
        "version": version,
        "build": build,
        "asset": asset or None,
        "binary_sha256": digest,
        "rustflags": rustflags if build == "server" else None,
        "dir": directory,
    },
    "kernel": platform.release(),
}
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
  local root_kb sha sha7 version
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
  sha=$(resolve_ref "$REF")
  sha7=$(git -C "$CORE" rev-parse --short=7 "$sha")
  build_one "$BENCH/rapira/$sha7" "$sha" "$rustflags"
  version=$(git -C "$CORE" describe --tags --always "$sha")
  write_meta "$BENCH/rapira/$sha7" "$REF" "$sha" "$version" server "" "$rustflags"
}

# source_value KEY prints the value of KEY in apps/yii3/source.toml.
source_value() {
  python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))[sys.argv[2]])' "$RIG/apps/yii3/source.toml" "$1"
}

install_yii3() {
  local dir=$BENCH/apps/yii3 commit
  commit=$(source_value commit)
  rm -rf "$dir"
  git clone -q "$(source_value repo)" "$dir"
  git -C "$dir" checkout -q "$commit"
  install -m 0644 "$RIG/apps/yii3/composer.lock" "$dir/composer.lock"
  composer --working-dir="$dir" install --no-dev --no-progress --no-scripts --classmap-authoritative
  # The app reads its compiled routes from Valkey when a process builds its container.
  sudo systemctl enable --now valkey
  record_version valkey "$(valkey-server --version)"
  record_version yii3 "$commit"
}

install_grpc_runtime() {
  local dir=$BENCH/apps/grpc
  rm -rf "$dir"
  install -d "$dir"
  install -m 0644 "$RIG/apps/grpc/composer.json" "$RIG/apps/grpc/composer.lock" "$dir/"
  composer --working-dir="$dir" install --no-dev --no-progress
  record_version protobuf "$(python3 -c 'import json, sys; print(next(p["version"] for p in json.load(open(sys.argv[1]))["packages"] if p["name"] == "google/protobuf"))' "$dir/composer.lock")"
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

sudo install -d -o fedora -g fedora "$BENCH" "$BENCH/run" "$BENCH/log" "$BENCH/apps" "$BENCH/rapira"
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
  echo "==> rapira server build of $REF"
  build_server
fi

if needs grpc; then
  echo "==> grpc runtime"
  install_grpc_runtime
fi
if needs yii3; then
  echo "==> yii3"
  install_yii3
fi

echo "==> server provisioned: needs=$NEEDS"
python3 -c 'import json; print(json.dumps(json.load(open("/opt/bench/meta.json"))["rapira"]))'
