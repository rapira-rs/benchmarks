"""Run the server install and fetch functions with local release and Git fixtures."""

import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "box/provision-server.sh"
SHA = "abc1234567890123456789012345678901234567890"
MOVED_SHA = "def5678901234567890123456789012345678901234"
VERSION = "0.1.0-nightly.abc1234"
ASSET = f"rapira-v{VERSION}-php8.5-linux-x86_64.tar.gz"
SUMS = f"rapira-v{VERSION}-SHA256SUMS.txt"

INSTALL_CASES = [
    {"name": "captured release survives a moved nightly tag", "captured": True, "remote_sha": MOVED_SHA, "corrupt": False, "binary": b"captured binary", "success": True, "requests": 0},
    {"name": "captured bytes survive a rebuild of the same commit", "captured": True, "remote_sha": SHA, "corrupt": False, "binary": b"captured binary", "success": True, "requests": 0},
    {"name": "captured archive must match its checksum", "captured": True, "remote_sha": SHA, "corrupt": True, "binary": None, "success": False, "requests": 0},
    {"name": "manual nightly still downloads the current release", "captured": False, "remote_sha": SHA, "corrupt": False, "binary": b"rebuilt binary", "success": True, "requests": 4},
]

RELEASE_COMMANDS = r'''
curl() {
  printf 'curl\n' >> "$HOME/requests"
  local output="" url=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
    -o) output=$2; shift ;;
    https:*) url=$1 ;;
    esac
    shift
  done
  if [ -n "$output" ]; then
    cp "$HOME/remote/${url##*/}" "$output"
  else
    printf '{"object":{"sha":"%s"}}\n' "$REMOTE_SHA"
  fi
}
find_asset() {
  printf 'find_asset\n' >> "$HOME/requests"
  printf '%s %s %s\n' "$VERSION" "$ASSET" "$SUMS"
}
ldd() { return 0; }
record_version() { return 0; }
write_meta() { printf '%s\n' "$@" > "$HOME/meta.args"; }
BENCH=$HOME/installed
install_nightly
'''


def release(directory, binary):
    directory.mkdir()
    with tarfile.open(directory / ASSET, "w:gz") as archive:
        for name, content in {"rapira/bin/rapira": binary, "rapira/share/php/PHP_VERSION.txt": b"8.5.0"}.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    digest = hashlib.sha256((directory / ASSET).read_bytes()).hexdigest()
    (directory / SUMS).write_text(f"{digest}  {ASSET}\n")


def functions():
    # Source the function definitions without the package and system setup.
    return SCRIPT.read_text().split('\nif [ -z "$NIGHTLY" ]', 1)[0]


class ProvisionServerTest(unittest.TestCase):
    def test_nightly_install(self):
        for case in INSTALL_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                captured = home / "captured"
                release(captured, b"captured binary")
                release(home / "remote", b"rebuilt binary")
                (captured / "nightly.json").write_text(json.dumps({"sha": SHA, "version": VERSION, "asset": ASSET, "sums": SUMS}))
                if case["corrupt"]:
                    (captured / ASSET).write_bytes(b"wrong archive")
                env = dict(os.environ, HOME=tmp, NIGHTLY="abc1234", NIGHTLY_DIR=str(captured) if case["captured"] else "", REMOTE_SHA=case["remote_sha"], VERSION=VERSION, ASSET=ASSET, SUMS=SUMS)
                proc = subprocess.run(["bash", "-c", functions() + RELEASE_COMMANDS], env=env, text=True, capture_output=True, check=False)
                self.assertEqual(proc.returncode == 0, case["success"], proc.stdout + proc.stderr)
                requests = (home / "requests").read_text().splitlines() if (home / "requests").exists() else []
                self.assertEqual(len(requests), case["requests"])
                installed = home / "installed/rapira/abc1234/bin/rapira"
                if case["success"]:
                    self.assertEqual(installed.read_bytes(), case["binary"])
                    meta = (home / "meta.args").read_text().splitlines()
                    self.assertEqual(meta[2:7], ["nightly", SHA, VERSION, "nightly", ASSET])
                else:
                    self.assertFalse(installed.exists())

    def test_build_server_updates_a_moved_nightly_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            origin = home / "origin"
            core = home / "core"

            def git(*args):
                return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()

            git("init", "-q", "-b", "main", str(origin))
            git("-C", str(origin), "-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "first")
            git("-C", str(origin), "tag", "nightly")
            git("clone", "-q", str(origin), str(core))
            git("-C", str(origin), "-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "second")
            expected = git("-C", str(origin), "rev-parse", "HEAD")
            git("-C", str(origin), "tag", "-f", "nightly")
            cargo = home / ".cargo/bin/cargo"
            cargo.parent.mkdir(parents=True)
            cargo.touch(mode=0o755)
            (home / ".cargo/env").write_text("")
            commands = r'''
df() { printf 'Filesystem 1024-blocks\nroot 41943040\n'; }
build_one() { printf '%s\n' "$2" > "$HOME/built-sha"; }
php() { printf 'PHP 8.5.0\n'; }
record_version() { return 0; }
write_meta() { return 0; }
build_server
'''
            proc = subprocess.run(["bash", "-c", functions() + commands], env=dict(os.environ, HOME=tmp, REF="nightly", NIGHTLY=""), capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual((home / "built-sha").read_text().strip(), expected)
            self.assertEqual(git("-C", str(core), "rev-parse", "nightly"), expected)
