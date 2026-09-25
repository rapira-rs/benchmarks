"""Tests of the box scripts: start, probe, and stop each server kind, and the byte-exact probe.

The lifecycle tests use /opt/bench and the ports 8080, 8081, and 9000, so they run only in the
disposable image. From the repository root:

    docker build -t rapira-bench-box -f tests/box.Dockerfile tests
    docker run --rm --init -v "$PWD:/repo:ro" rapira-bench-box

Add -e RAPIRA_ASSET_URL=<URL of a nightly tarball> to run the rapira cases with the real binary.
The probe tests run on every host with bash, curl, and cmp.
"""

import os
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BENCH = Path("/opt/bench")
RIG = Path.home() / "bench-rig"
PROCS = 2
TAG = "t1"
ASSET_URL = os.environ.get("RAPIRA_ASSET_URL", "")
ASSET_DIR = Path("/tmp/rapira-asset")
STARTED = BENCH / "run/stub-started"
HELLO = {"path": "/?name=you", "expect": "apps/hello/expect.txt"}

# The stub of "rapira serve CONFIG" and "rr serve -c CONFIG". A copy of python3 named rapira or rr
# runs this file from its working directory, $BENCH/run, so /proc/<pid>/exe is the copy.
STUB = r'''
import os
import re
import signal
import socketserver
import struct
import sys
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

if "-c" in sys.argv:
    text = Path(sys.argv[sys.argv.index("-c") + 1]).read_text()
    listen = re.search(r'listen: "tcp://(.*)"', text).group(1)
    processes = int(re.search(r"num_workers: (\d+)", text).group(1))
    grpc = True
else:
    config = tomllib.loads(Path(sys.argv[-1]).read_text())
    grpc = "grpc" in config
    section = config["grpc"] if grpc else config["http"]
    listen = section["listen"]
    processes = section["pool"]["processes"]
if os.environ.get("BOX_STUB_SHORT") == "1":
    processes -= 1
host, port = listen.rsplit(":", 1)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        name = parse_qs(urlsplit(self.path).query).get("name", ["anonymous"])[0]
        body = f"Hello from worker, {name}!\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class GrpcHandler(socketserver.BaseRequestHandler):
    # A minimal h2c peer: it answers each request stream with the bytes of the Echo reply.
    # https://www.rfc-editor.org/rfc/rfc9113.html#name-frame-format
    def send(self, kind, flags, stream, payload):
        self.request.sendall(struct.pack(">I", len(payload))[1:] + struct.pack(">BBI", kind, flags, stream) + payload)

    def handle(self):
        reply = (Path.home() / "bench-rig/apps/grpc/expect.grpc").read_bytes()
        stream = self.request.makefile("rb")
        stream.read(24)
        self.send(4, 0, 0, b"")
        while header := stream.read(9):
            length = int.from_bytes(header[:3])
            kind, flags, number = struct.unpack(">BBI", header[3:])
            stream.read(length)
            if kind == 4 and not flags & 1:
                self.send(4, 1, 0, b"")
            elif kind in (0, 1) and flags & 1:
                # 0x88 is ":status: 200" of the HPACK static table.
                self.send(1, 4, number, b"\x88")
                self.send(0, 1, number, reply)


server = HTTPServer((host or "0.0.0.0", int(port)), GrpcHandler if grpc else Handler)
Path("/opt/bench/run/stub-started").touch()
if ready := os.environ.get("BOX_STUB_READY"):
    with open(ready) as channel:
        channel.read(1)
children = []


def stop_child(_signum, _frame):
    raise SystemExit(0)


def stop_master(_signum, _frame):
    for child in children:
        try:
            os.kill(child, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for child in children:
        try:
            os.waitpid(child, 0)
        except ChildProcessError:
            pass
    raise SystemExit(0)


for _ in range(processes):
    pid = os.fork()
    if pid == 0:
        signal.signal(signal.SIGINT, stop_child)
        signal.signal(signal.SIGTERM, stop_child)
        server.serve_forever()
    children.append(pid)

signal.signal(signal.SIGINT, stop_master)
signal.signal(signal.SIGTERM, stop_master)
while True:
    signal.pause()
'''

LIFECYCLE_CASES = [
    {
        "name": "rapira worker",
        "server": "rapira",
        "binary": "pr",
        "args": ["worker", "@RIG@/apps/hello/worker.php"],
        "configs": {"toml": ['listen = ":8080"', f'entrypoint = "{RIG}/apps/hello/worker.php"', 'mode = "worker"', "processes = 2"]},
        "probe": HELLO,
        # The rapira master forks PROCS workers.
        "workers": PROCS,
        "stub_only": False,
    },
    {
        "name": "rapira classic",
        "server": "rapira",
        "binary": "pr",
        "args": ["classic", "@RIG@/apps/hello/classic.php"],
        "configs": {"toml": ['mode = "classic"', f'entrypoint = "{RIG}/apps/hello/classic.php"']},
        "probe": HELLO,
        "workers": PROCS,
        "stub_only": False,
    },
    {
        "name": "rapira dispatcher",
        "server": "rapira",
        "binary": "pr",
        "args": ["dispatcher", "@RIG@/apps/hello/dispatcher.php"],
        "configs": {"toml": ['mode = "dispatcher"', f'entrypoint = "{RIG}/apps/hello/dispatcher.php"']},
        "probe": HELLO,
        "workers": PROCS,
        "stub_only": False,
    },
    {
        "name": "rapira worker on the base binary",
        "server": "rapira",
        "binary": "base",
        "args": ["worker", "@RIG@/apps/hello/worker.php"],
        "configs": {"toml": ['mode = "worker"']},
        "probe": HELLO,
        "workers": PROCS,
        "stub_only": False,
    },
    {
        "name": "rapira static miss goes to the worker",
        "server": "rapira",
        "binary": "pr",
        "args": ["worker", "@RIG@/apps/hello/worker.php", "servers/rapira/static.toml.tpl"],
        "configs": {"toml": ['middleware = ["static"]', f'root = "{RIG}/apps/static"', 'mode = "worker"']},
        "probe": HELLO,
        "workers": PROCS,
        "stub_only": False,
    },
    {
        "name": "rapira grpc",
        "server": "rapira",
        "binary": "pr",
        "args": ["grpc", "@RIG@/apps/grpc/php/dispatcher.php"],
        "configs": {"toml": ["[grpc]", f'descriptor_set = "{RIG}/apps/grpc/bench.binpb"', f'entrypoint = "{RIG}/apps/grpc/php/dispatcher.php"']},
        "probe": None,
        "workers": PROCS,
        # The real gRPC dispatcher needs the protobuf runtime that provisioning installs.
        "stub_only": True,
    },
]

FAIL_CASES = [
    {
        "name": "rapira worker count differs",
        "server": "rapira",
        "binary": "pr",
        "args": ["worker", "@RIG@/apps/hello/worker.php"],
        "env": {"BOX_STUB_SHORT": "1"},
        "hold_before": None,
        "hold_after_start": None,
        "edit": None,
        # The stub forks PROCS - 1 workers.
        "error": "rapira has 1 workers, expected 2",
        "stub_started": True,
        "stub_only": True,
    },
    {
        "name": "rapira target port busy",
        "server": "rapira",
        "binary": "pr",
        "args": ["worker", "@RIG@/apps/hello/worker.php"],
        "env": {},
        "hold_before": 8080,
        "hold_after_start": None,
        "edit": None,
        "error": ":8080 is busy",
        "stub_started": False,
        "stub_only": False,
    },
    {
        "name": "rapira unknown mode",
        "server": "rapira",
        "binary": "pr",
        "args": ["fast", "@RIG@/apps/hello/worker.php"],
        "env": {},
        "hold_before": None,
        "hold_after_start": None,
        "edit": None,
        "error": "unknown rapira mode fast",
        "stub_started": False,
        "stub_only": False,
    },
]


def listening(port):
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def hold(port):
    busy = socket.socket()
    busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    busy.bind(("0.0.0.0", port))
    busy.listen()
    return busy


class BoxLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("BOX_TEST") != "1":
            raise unittest.SkipTest("run this test in the image of tests/box.Dockerfile")
        if os.geteuid() == 0:
            raise RuntimeError("run this test as a user other than root, as on the rig boxes")
        if ASSET_URL and not ASSET_DIR.exists():
            ASSET_DIR.mkdir(parents=True)
            archive, _ = urllib.request.urlretrieve(ASSET_URL)
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():
                    # Drop the top directory of the tarball, as provisioning does.
                    member.name = member.name.partition("/")[2]
                    if member.name:
                        tar.extract(member, ASSET_DIR, filter="tar")

    def setUp(self):
        self.addCleanup(self.force_cleanup)

    def reset(self):
        # Each case starts from an empty /opt/bench and a fresh staged rig, as on a provisioned box.
        self.force_cleanup()
        for path in BENCH.iterdir():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        shutil.rmtree(RIG, ignore_errors=True)
        for directory in ("box", "servers", "apps/hello", "apps/static", "apps/grpc"):
            shutil.copytree(REPO / directory, RIG / directory)
        for directory in ("run", "log", "bin", "rapira"):
            (BENCH / directory).mkdir()
        shutil.copy2(REPO / "servers/php.ini", BENCH / "php.ini")
        (BENCH / "run/serve").write_text(STUB)
        for binary in ("pr", "base"):
            if ASSET_URL:
                (BENCH / "rapira" / binary).symlink_to(ASSET_DIR)
            else:
                (BENCH / "rapira" / binary / "bin").mkdir(parents=True)
                shutil.copy2(os.path.realpath("/usr/bin/python3"), BENCH / "rapira" / binary / "bin/rapira")

    def force_cleanup(self):
        subprocess.run(["pkill", "-KILL", "-u", str(os.getuid()), "-f", "opt/bench|php-fpm|nginx"], check=False)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and self.leftovers():
            time.sleep(0.05)

    def leftovers(self):
        return subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", "opt/bench|php-fpm|nginx"], capture_output=True, text=True).stdout.split()

    def target(self, *arguments, env=None):
        return subprocess.run(
            [str(RIG / "box/target.sh"), *map(str, arguments)],
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            timeout=60,
        )

    def start_arguments(self, case):
        binary = BENCH / "rapira" / case["binary"] if case["binary"] else "-"
        return ["start", TAG, case["server"], PROCS, binary, *case["args"]]

    def assert_success(self, result):
        self.assertEqual(0, result.returncode, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")

    def assert_stopped(self, pids):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(Path(f"/proc/{pid}").exists() for pid in pids):
            time.sleep(0.05)
        self.assertEqual([], [pid for pid in pids if Path(f"/proc/{pid}").exists()])
        self.assertFalse(listening(8080))
        self.assertFalse(listening(8081))
        self.assertEqual([], sorted(path.name for path in (BENCH / "run").glob(f"{TAG}.*.pid")))

    def test_lifecycle(self):
        for case in LIFECYCLE_CASES:
            with self.subTest(name=case["name"]):
                if ASSET_URL and case["stub_only"]:
                    self.skipTest("the case needs the stub")
                self.reset()
                started = self.target(*self.start_arguments(case))
                self.assert_success(started)
                lines = started.stdout.splitlines()
                pid = int(next(line for line in lines if line.startswith("pid="))[4:])
                configs = sorted(line[7:] for line in lines if line.startswith("config="))
                self.assertEqual(sorted(str(BENCH / f"run/{TAG}.{suffix}") for suffix in case["configs"]), configs)
                for suffix, parts in case["configs"].items():
                    text = (BENCH / f"run/{TAG}.{suffix}").read_text()
                    for part in parts:
                        self.assertIn(part, text)
                ss = subprocess.run(["ss", "-Hltnp", "sport = :8080"], capture_output=True, text=True).stdout
                self.assertIn(f"pid={pid},", ss)
                if case["probe"]:
                    probe = subprocess.run(
                        [str(RIG / "box/probe.sh"), "http://127.0.0.1:8080" + case["probe"]["path"], case["probe"]["expect"], "http1"],
                        capture_output=True,
                        text=True,
                    )
                    self.assert_success(probe)
                masters = [int(path.read_text()) for path in (BENCH / "run").glob(f"{TAG}.*.pid")]
                self.assertIn(pid, masters)
                inspected = self.target("probe", TAG, case["server"])
                self.assert_success(inspected)
                workers = [int(value) for value in inspected.stdout.splitlines()[0].split()]
                self.assertEqual(case["workers"], len(workers))
                log_bytes = sum(path.stat().st_size for path in (BENCH / "log").glob(f"{TAG}.*.log"))
                self.assertEqual(log_bytes, int(inspected.stdout.splitlines()[1]))
                memory = self.target("mem", TAG, case["server"])
                self.assert_success(memory)
                self.assertGreater(int(memory.stdout), 0)
                self.assert_success(self.target("stop", TAG, case["server"]))
                self.assert_stopped(masters + workers)

    def test_failed_start_leaves_nothing(self):
        for case in FAIL_CASES:
            with self.subTest(name=case["name"]):
                if ASSET_URL and case["stub_only"]:
                    self.skipTest("the case needs the stub")
                self.reset()
                env = dict(case["env"])
                if case["edit"]:
                    (RIG / case["edit"][0]).write_text(case["edit"][1])
                if case["hold_before"]:
                    with hold(case["hold_before"]):
                        result = self.target(*self.start_arguments(case), env=env)
                elif case["hold_after_start"]:
                    ready = BENCH / "run/stub-ready"
                    os.mkfifo(ready)
                    env["BOX_STUB_READY"] = str(ready)
                    process = subprocess.Popen(
                        [str(RIG / "box/target.sh"), *map(str, self.start_arguments(case))],
                        env={**os.environ, **env},
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline and not STARTED.exists():
                        time.sleep(0.01)
                    with hold(case["hold_after_start"]):
                        # Opening the FIFO for writing blocks until the stub opens it for reading.
                        if STARTED.exists():
                            ready.write_text("1")
                        stdout, stderr = process.communicate(timeout=60)
                    result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
                else:
                    result = self.target(*self.start_arguments(case), env=env)
                self.assertNotEqual(0, result.returncode, result.stdout)
                self.assertIn(case["error"], result.stderr)
                self.assertEqual(case["stub_started"], STARTED.exists())
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and self.leftovers():
                    time.sleep(0.05)
                self.assertEqual([], self.leftovers())
                self.assert_stopped([])


class ReplyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def reply(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.seen = {"method": self.command, "body": self.rfile.read(length), "headers": {name.lower(): value for name, value in self.headers.items()}}
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.server.reply)))
        self.end_headers()
        self.wfile.write(self.server.reply)

    do_GET = reply
    do_POST = reply

    def log_message(self, _format, *_args):
        pass


PROBE_CASES = [
    {
        "name": "exact body",
        "reply": b"Hello from worker, you!\n",
        "args": [],
        "status": 0,
        "stderr": "",
        "method": "GET",
        "body": b"",
        "headers": {},
    },
    {
        "name": "one byte differs",
        "reply": b"Hello from worker, You!\n",
        "args": [],
        "status": 1,
        # The first 19 bytes are "Hello from worker, ", so byte 20 is the first difference.
        "stderr": " 20, line 1",
        "method": "GET",
        "body": b"",
        "headers": {},
    },
    {
        "name": "short body",
        "reply": b"Hello from worker, ",
        "args": [],
        "status": 1,
        "stderr": "after byte 19",
        "method": "GET",
        "body": b"",
        "headers": {},
    },
    {
        "name": "no answer",
        "reply": None,
        "args": [],
        "status": 1,
        "stderr": "curl failed",
        "method": None,
        "body": None,
        "headers": {},
    },
    {
        "name": "request shape of a Connect JSON target",
        "reply": b"Hello from worker, you!\n",
        "args": ["POST", "apps/grpc/echo.json", "content-type: application/json", "connect-protocol-version: 1"],
        "status": 0,
        "stderr": "",
        "method": "POST",
        # apps/grpc/fixtures.py writes this request body.
        "body": b'{"text":"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ01"}',
        "headers": {"content-type": "application/json", "connect-protocol-version": "1"},
    },
]


class ProbeTests(unittest.TestCase):
    def setUp(self):
        # probe.sh resolves a relative file under $HOME/bench-rig.
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home)
        os.symlink(REPO, Path(home) / "bench-rig")
        self.env = {**os.environ, "HOME": home}

    def probe(self, url, *arguments):
        return subprocess.run(
            [str(REPO / "box/probe.sh"), url, "apps/hello/expect.txt", "http1", *arguments],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_probe(self):
        for case in PROBE_CASES:
            with self.subTest(name=case["name"]):
                server = HTTPServer(("127.0.0.1", 0), ReplyHandler)
                # handle_request returns after 5 s without a request.
                server.timeout = 5
                server.reply = case["reply"]
                server.seen = None
                port = server.server_address[1]
                if case["reply"] is None:
                    server.server_close()
                else:
                    thread = threading.Thread(target=server.handle_request, daemon=True)
                    thread.start()
                result = self.probe(f"http://127.0.0.1:{port}/?name=you", *case["args"])
                if case["reply"] is not None:
                    thread.join(timeout=5)
                    server.server_close()
                self.assertEqual(case["status"], result.returncode, result.stderr)
                self.assertIn(case["stderr"], result.stderr)
                if case["method"]:
                    self.assertEqual(case["method"], server.seen["method"])
                    self.assertEqual(case["body"], server.seen["body"])
                    for name, value in case["headers"].items():
                        self.assertEqual(value, server.seen["headers"][name])


if __name__ == "__main__":
    unittest.main()
