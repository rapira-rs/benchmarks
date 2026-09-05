#!/usr/bin/env python3
"""Run Linux lifecycle tests for the Rapira nginx benchmark leg.

docker run --rm --init -v "$PWD:/repo:ro" -w /repo alpine:3 sh -ceu 'apk add --no-cache bash nginx python3 curl iproute2 procps coreutils >/dev/null; adduser -D -h /home/fedora fedora; install -d -o fedora -g fedora /opt/bench; su fedora -s /bin/sh -c "HOME=/home/fedora RAPIRA_NGINX_TEST=1 python3 /repo/scripts/test_nginx.py"'
"""

import http.client
import os
import shutil
import socket
import subprocess
import threading
import time
import unittest
from pathlib import Path


REPO = Path(
    os.environ.get(
        "RAPIRA_NGINX_TEST_SOURCE",
        Path(__file__).resolve().parents[1],
    )
)
BENCH = Path("/opt/bench")
RIG = Path.home() / "bench-rig"
WORK = Path("/tmp/rapira-nginx-test")
STARTED = BENCH / "run/fake-backend-started"

BACKEND = r'''#!/usr/bin/env python3
import os
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def option(name):
    index = sys.argv.index(name)
    return sys.argv[index + 1]


host, port = option("--listen").rsplit(":", 1)
host = host or "0.0.0.0"
processes = int(option("--processes"))
script = os.path.abspath(sys.argv[-1])
started = "/opt/bench/run/fake-backend-started"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        connection = f"{os.getpid()}:{self.client_address[1]}"
        body = (
            f"path={self.path}\n"
            f"script={script}\n"
            f"worker={os.getpid()}\n"
            f"connection={connection}\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Upstream-Connection", connection)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


server = HTTPServer((host, int(port)), Handler)
Path(started).touch()
time.sleep(0.2)
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
        try:
            server.serve_forever()
        finally:
            server.server_close()
        raise SystemExit(0)
    children.append(pid)

signal.signal(signal.SIGINT, stop_master)
signal.signal(signal.SIGTERM, stop_master)
while True:
    signal.pause()
'''


class NginxLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("RAPIRA_NGINX_TEST") != "1":
            raise unittest.SkipTest(
                "Set RAPIRA_NGINX_TEST=1 in the disposable test container."
            )
        if not Path("/.dockerenv").exists() or os.geteuid() == 0:
            raise RuntimeError(
                "Run this test as a non-root user in a disposable container. "
                "The test uses /opt/bench and fixed ports 8080 and 8081."
            )

    def setUp(self):
        self.force_cleanup()
        BENCH.mkdir(parents=True, exist_ok=True)
        for path in BENCH.iterdir():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        shutil.rmtree(RIG, ignore_errors=True)
        shutil.rmtree(WORK, ignore_errors=True)

        for directory in (
            BENCH / "bin",
            BENCH / "fleet",
            BENCH / "log",
            BENCH / "run",
            RIG / "scripts",
            RIG / "fleet/nginx",
            RIG / "php/hello",
            WORK,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        for name in ("box-lib.sh", "leg.sh", "fleet-leg.sh"):
            shutil.copy2(REPO / "scripts" / name, RIG / "scripts" / name)
        shutil.copy2(
            REPO / "fleet/nginx/rapira.conf.tpl",
            RIG / "fleet/nginx/rapira.conf.tpl",
        )
        shutil.copy2("/usr/bin/python3", BENCH / "bin/rapira-pr")
        (BENCH / "bin/rapira-pr").chmod(0o755)
        (RIG / "php/hello/worker.php").write_text("<?php // fixture\n")
        (WORK / "serve").write_text(BACKEND)
        (WORK / "serve").chmod(0o755)

        self.env = os.environ.copy()
        self.env["HOME"] = str(Path.home())
        self.addCleanup(self.force_cleanup)

    def fleet(self, *arguments, timeout=20):
        return subprocess.run(
            [str(RIG / "scripts/fleet-leg.sh"), *map(str, arguments)],
            cwd=WORK,
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def assert_success(self, result):
        self.assertEqual(
            0,
            result.returncode,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def start(self, tag, processes, workload=None):
        arguments = ["start", "rapira-nginx", processes, tag]
        if workload is not None:
            arguments.append(workload)
        result = self.fleet(*arguments)
        self.assert_success(result)
        backend = int((BENCH / f"run/{tag}.pid").read_text().strip())
        nginx = int((BENCH / f"run/{tag}.nginx.pid").read_text().strip())
        return backend, nginx

    def child_pids(self, parent):
        children = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text().split()
            except (FileNotFoundError, ProcessLookupError):
                continue
            if int(fields[3]) == parent:
                children.append(int(entry.name))
        return sorted(children)

    def wait_for_children(self, parent, count):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            children = self.child_pids(parent)
            if len(children) == count:
                return children
            time.sleep(0.05)
        self.fail(
            f"process {parent} has children {self.child_pids(parent)}, expected {count}"
        )

    def request(self, connection, target):
        connection.request("GET", target)
        response = connection.getresponse()
        body = response.read().decode()
        self.assertEqual(200, response.status, body)
        values = dict(line.split("=", 1) for line in body.splitlines())
        return values, response.getheader("X-Upstream-Connection")

    def assert_port(self, port, listening):
        with socket.socket() as probe:
            probe.settimeout(0.2)
            actual = probe.connect_ex(("127.0.0.1", port)) == 0
        self.assertEqual(listening, actual, f"listener state for port {port}")

    def assert_stopped(self, tag, pids):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(
            Path(f"/proc/{pid}").exists() for pid in pids
        ):
            time.sleep(0.05)
        for pid in pids:
            self.assertFalse(Path(f"/proc/{pid}").exists(), f"process {pid} remains")
        self.assert_port(8080, False)
        self.assert_port(8081, False)
        self.assertFalse((BENCH / f"run/{tag}.pid").exists())
        self.assertFalse((BENCH / f"run/{tag}.nginx.pid").exists())

    def force_cleanup(self):
        for pattern in (
            "/opt/bench/bin/rapira-pr serve",
            "nginx: master process",
            "nginx: worker process",
        ):
            subprocess.run(
                ["pkill", "-KILL", "-f", pattern],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        time.sleep(0.05)

    def test_hello_uses_requested_workers_and_reuses_upstream(self):
        backend, nginx = self.start("hello", 2)
        backend_workers = self.wait_for_children(backend, 2)
        nginx_workers = self.wait_for_children(nginx, 2)
        self.assertEqual(
            [str(BENCH / "bin/rapira-pr")] * 3,
            [os.readlink(f"/proc/{pid}/exe") for pid in [backend, *backend_workers]],
        )

        connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=2)
        self.addCleanup(connection.close)
        first, first_upstream = self.request(connection, "/first?name=one&n=1")
        second, second_upstream = self.request(connection, "/second?name=two&n=2")

        self.assertEqual("/first?name=one&n=1", first["path"])
        self.assertEqual("/second?name=two&n=2", second["path"])
        self.assertEqual(str(RIG / "php/hello/worker.php"), first["script"])
        self.assertEqual(first_upstream, first["connection"])
        self.assertEqual(first_upstream, second_upstream)
        self.assertIn(int(first["worker"]), backend_workers)

        probe = subprocess.run(
            [str(RIG / "scripts/leg.sh"), "probe", "hello"],
            cwd=WORK,
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assert_success(probe)
        probe_lines = probe.stdout.splitlines()
        expected_workers = sorted([*backend_workers, *nginx_workers])
        self.assertEqual([str(pid) for pid in expected_workers], probe_lines[0].split())
        expected_log_bytes = sum(
            (BENCH / f"log/hello.{name}.log").stat().st_size
            for name in ("server", "nginx")
        )
        self.assertEqual(expected_log_bytes, int(probe_lines[1]))

        all_pids = [backend, *backend_workers, nginx, *nginx_workers]
        connection.close()
        self.assert_success(self.fleet("stop", "rapira-nginx", "hello"))
        self.assert_stopped("hello", all_pids)

    def test_absolute_script_is_sent_to_the_backend(self):
        script = WORK / "framework/public/index.php"
        script.parent.mkdir(parents=True)
        script.write_text("<?php // framework fixture\n")
        backend, nginx = self.start("framework", 1, script)
        backend_workers = self.wait_for_children(backend, 1)
        nginx_workers = self.wait_for_children(nginx, 1)

        connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=2)
        self.addCleanup(connection.close)
        response, _ = self.request(connection, "/framework?item=42")
        self.assertEqual("/framework?item=42", response["path"])
        self.assertEqual(str(script), response["script"])

        all_pids = [backend, *backend_workers, nginx, *nginx_workers]
        connection.close()
        self.assert_success(self.fleet("stop", "rapira-nginx", "framework"))
        self.assert_stopped("framework", all_pids)

    def test_busy_backend_port_rejects_before_state_is_written(self):
        with socket.socket() as busy:
            busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            busy.bind(("127.0.0.1", 8081))
            busy.listen()
            result = self.fleet("start", "rapira-nginx", 1, "busy")

        self.assertNotEqual(0, result.returncode, result.stdout)
        self.assertFalse(STARTED.exists())
        self.assertFalse((BENCH / "fleet/nginx/nginx.conf").exists())
        self.assertFalse((BENCH / "run/busy.pid").exists())
        self.assertFalse((BENCH / "run/busy.nginx.pid").exists())
        self.assert_port(8080, False)
        self.assert_port(8081, False)

    def test_invalid_nginx_config_rejects_before_backend_start(self):
        (RIG / "fleet/nginx/rapira.conf.tpl").write_text(
            "events {}\nhttp { invalid_directive; }\n"
        )

        result = self.fleet("start", "rapira-nginx", 2, "invalid")

        self.assertNotEqual(0, result.returncode, result.stdout)
        self.assertFalse(STARTED.exists())
        self.assert_port(8080, False)
        self.assert_port(8081, False)
        self.assertFalse((BENCH / "run/invalid.pid").exists())
        self.assertFalse((BENCH / "run/invalid.nginx.pid").exists())

    def test_nginx_runtime_failure_cleans_the_backend(self):
        release = threading.Event()
        bound = threading.Event()
        errors = []

        def occupy_frontend():
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not STARTED.exists():
                time.sleep(0.005)
            if not STARTED.exists():
                errors.append("the backend did not start")
                return
            try:
                with socket.socket() as busy:
                    busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    busy.bind(("127.0.0.1", 8080))
                    busy.listen()
                    bound.set()
                    release.wait(15)
            except OSError as error:
                errors.append(str(error))

        holder = threading.Thread(target=occupy_frontend, daemon=True)
        holder.start()
        try:
            result = self.fleet("start", "rapira-nginx", 2, "runtime", timeout=20)
        finally:
            release.set()
            holder.join(timeout=2)

        self.assertTrue(bound.is_set(), errors)
        self.assertEqual([], errors)
        self.assertNotEqual(0, result.returncode, result.stdout)
        self.assertTrue(STARTED.exists())
        self.assert_port(8080, False)
        self.assert_port(8081, False)
        self.assertFalse((BENCH / "run/runtime.pid").exists())
        self.assertFalse((BENCH / "run/runtime.nginx.pid").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
