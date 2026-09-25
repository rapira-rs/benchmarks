import json
import shlex
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from rig.bench import WARMUP_S, binary_dir, load_cmd, plan_run, run_suite
from rig.merge import ERROR_KEYS
from rig.registry import Suite, SuiteError, Target
from rig.rig import Rig
from rig.ssh import Host, SshError

SERVER = Host("server", "3.0.0.1", "10.0.0.1")
LOADERS = tuple(Host(f"loader-{i}", f"3.0.0.{i + 1}", f"10.0.0.{i + 1}") for i in range(1, 5))
RIG = Rig(SERVER, LOADERS, "c7a.8xlarge", "c7a.xlarge", "ami-0123", Path("key"))
RAPIRA = {"ref": "main", "sha": "abc1234def", "version": "0.9.0", "build": "nightly", "dir": "/opt/bench/rapira/abc1234"}
THREADS = 4

WORKER = Target(
    name="hello-rapira-worker", server="rapira", app="hello", mode="worker", proto="http1", binary="pr",
    start=("worker", "@RIG@/apps/hello/worker.php"), url="/?name=you", expect="apps/hello/expect.txt",
    config="servers/rapira/http.toml.tpl",
)
FPM = Target(
    name="hello-php-fpm", server="php-fpm", app="hello", mode="classic", proto="http1", binary=None,
    start=("@RIG@/apps/hello", "fpm.php"), url="/?name=you", expect="apps/hello/expect.txt",
    config="servers/php-fpm/php-fpm.conf.tpl",
)

BOX = "bash bench-rig/box/"
START = BOX + "target.sh start"
PIDS = BOX + "target.sh probe"
STOP = BOX + "target.sh stop"
LOG = BOX + "target.sh log"
PROBE = BOX + "probe.sh"
LOAD = BOX + "load.sh"
SNAPSHOT = BOX + "snapshot.sh"
SAMPLE = "sleep "
FACTS = "echo kernel="

# One stage per loader: 20 s at the floor 10000 split over 4 loaders is 2500 req/s,
# so 50000 requests per loader. The second stage asks 5000 req/s per loader.
FULL = 50000
SHORT = 90000  # 4 x 90000 / 20 s = 18000 req/s, under 95% of 20000


def suite(targets, connections=256):
    return Suite(
        name="test", rounds=1, stage_s=20, connections=connections, smoke=False,
        floors={"hello": 10000}, targets=tuple(targets),
    )


def result_line(requests, late_ms=0):
    body = {
        "tool": "wrk2", "late_ms": late_ms, "duration_us": 20000000, "requests": requests, "bytes": requests * 126,
        "errors": {key: 0 for key in ERROR_KEYS},
        "latency_us": {"mean": 700.0, "p50": 690, "p90": 1100, "p95": 1170, "p99": 1260, "p999": 1350, "max": 2800},
        "requests_per_sec": requests / 20,
    }
    return f"Running 20s test\nRESULT {json.dumps(body)}\n"


def load_reply(late_ms=0):
    """A loader that holds 2500 req/s and reaches 90000 requests at 5000 req/s."""

    def reply(cmd):
        rate = int(shlex.split(cmd)[4])
        return result_line(FULL if rate <= 2500 else SHORT, late_ms)

    return reply


def counter(busy_step, total_step, extra=""):
    """Growing /proc/stat counters: every window between two calls has the same busy percent."""
    state = {"n": 0}

    def reply(cmd):
        state["n"] += 1
        return f"cpu {busy_step * state['n']} {total_step * state['n']}\n{extra}"

    return reply


def replies(overrides):
    table = {
        ("*", FACTS): "kernel=6.17.1\ninstance_id=i-0abc\naz=eu-central-1a\nplacement_group=rapira-bench\nwrk2=44a94c17d8e6a0bac8559b53da76848e430cb7a7\nk6=k6 v2.2.0\n",
        ("server", START): "config=/opt/bench/run/r1-hello-rapira-worker.toml\npid=100\n",
        ("server", PIDS): "101 102\n4096\n",
        ("server", STOP): "",
        ("server", LOG): "",
        ("server", SAMPLE): "cpu 1 2\nconns 256 3\n204800\n",
        # Server 50% busy in every stage.
        ("server", SNAPSHOT): counter(50, 100, "conns 256 0\n"),
        # Loaders 90% busy in every stage.
        ("*", SNAPSHOT): counter(90, 100),
        ("*", PROBE): "",
        ("*", LOAD): load_reply(),
    }
    for host in LOADERS:
        table[(host.name, SNAPSHOT)] = counter(90, 100)
    table.update(overrides)
    return table


class FakeBoxes:
    """Replies by (host name, command prefix); "*" matches any host. A list reply is used in order."""

    def __init__(self, table):
        self.table = table
        self.calls = []
        self.copies = []

    def reply(self, host, cmd):
        self.calls.append((host.name, cmd))
        for name in (host.name, "*"):
            for (key_host, prefix), value in self.table.items():
                if key_host == name and cmd.startswith(prefix):
                    if isinstance(value, list):
                        value = value.pop(0) if len(value) > 1 else value[0]
                    if isinstance(value, BaseException):
                        raise value
                    return value(cmd) if callable(value) else value
        raise AssertionError(f"no reply for {host.name}: {cmd}")

    def run(self, host, cmd, timeout=None):
        return self.reply(host, cmd)

    def run_many(self, jobs, timeout=None):
        results = []
        for host, cmd in jobs:
            try:
                results.append(self.reply(host, cmd))
            except SshError as exc:
                results.append(exc)
        return results

    def copy_from(self, host, remote, local):
        self.copies.append((host.name, remote))
        local.write_text(f"copy of {remote}\n")


def label(cmd):
    if cmd.startswith(LOAD):
        return "warmup" if shlex.split(cmd)[7] == str(WARMUP_S) else "load"
    for prefix, name in ((START, "start"), (PIDS, "pids"), (STOP, "stop"), (LOG, "log"), (PROBE, "probe"),
                         (SNAPSHOT, "snapshot"), (SAMPLE, "sample"), (FACTS, "facts")):
        if cmd.startswith(prefix):
            return name
    return cmd


STAGE = ["snapshot"] * 5 + ["load"] * 4 + ["sample"] + ["snapshot"] * 5

CELL_CASES = [
    {
        "name": "pass then fail",
        "overrides": {},
        "status": "ok",
        "reason": None,
        "held": {"rate": 10000, "stage": 0},
        "peak": 18000.0,
        "stages": 2,
        # The failing stage has every loader at 90% and the server at 50%.
        "flags": {"generator_bound": True},
        "loads": 8,
    },
    {
        "name": "probe mismatch on loader-2 voids before load",
        "overrides": {("loader-2", PROBE): SshError("loader-2: exit 1: probe")},
        "status": "void",
        "reason": "probe mismatch on loader-2",
        "held": None,
        "peak": None,
        "stages": 0,
        "flags": {},
        "loads": 0,
    },
    {
        "name": "missing RESULT from loader-3 voids with the loader name",
        "overrides": {("loader-3", LOAD): "wrk2: panic: bytecode\n"},
        "status": "void",
        "reason": "loader-3",
        "held": None,
        "peak": None,
        "stages": 0,
        "flags": {},
        "loads": 4,
    },
    {
        "name": "loader-1 late by 1500 ms voids",
        "overrides": {("loader-1", LOAD): load_reply(late_ms=1500)},
        "status": "void",
        "reason": "loader-1",
        "held": None,
        "peak": None,
        "stages": 0,
        "flags": {},
        "loads": 4,
    },
    {
        "name": "failed probe after the failing stage sets died",
        "overrides": {("loader-1", PROBE): ["", SshError("loader-1: exit 1: probe")]},
        "status": "ok",
        "reason": None,
        "held": {"rate": 10000, "stage": 0},
        "peak": 18000.0,
        "stages": 2,
        "flags": {"generator_bound": True, "died": True},
        "loads": 8,
    },
    {
        "name": "a WARN line in the rapira log voids",
        "overrides": {("server", LOG): "2026-09-25T10:00:00Z WARN worker restarted\n"},
        "status": "void",
        "reason": "server log: 1 warn or error lines",
        "held": None,
        "peak": None,
        "stages": 2,
        "flags": {"generator_bound": True},
        "loads": 8,
    },
]


def bench(boxes, out, targets, connections=256):
    with mock.patch("rig.bench.ensure_ttl") as ttl:
        path = run_suite(
            RIG, suite(targets, connections), boxes, out, processes=32, run_id="run1", rapira=RAPIRA,
            servers={}, apps={}, loader_threads=THREADS,
        )
    return path, ttl


class RunSuiteTest(unittest.TestCase):
    def test_cells(self):
        for case in CELL_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                boxes = FakeBoxes(replies(case["overrides"]))
                path, _ = bench(boxes, Path(tmp), [WORKER])
                run = json.loads(path.read_text())
                cell = run["cells"][0]
                self.assertEqual(cell["status"], case["status"])
                if case["reason"]:
                    self.assertIn(case["reason"], cell["reason"])
                self.assertEqual(cell["held"], case["held"])
                self.assertEqual(cell["peak"], case["peak"])
                self.assertEqual(len(cell["stages"]), case["stages"])
                self.assertEqual(cell["flags"], case["flags"])
                self.assertEqual(sum(1 for _, cmd in boxes.calls if label(cmd) == "load"), case["loads"])
                self.assertEqual([label(cmd) for _, cmd in boxes.calls][-2:], ["stop", "log"])
                self.assertEqual(run["status"], "complete" if case["status"] == "ok" else "incomplete")

    def test_command_order_and_raw_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({}))
            path, ttl = bench(boxes, Path(tmp), [WORKER])
            expected = (
                ["facts"] * 5 + ["start", "pids"] + ["probe"] * 4 + ["warmup"] * 4 + STAGE + STAGE
                + ["probe", "pids", "stop", "log"]
            )
            self.assertEqual([label(cmd) for _, cmd in boxes.calls], expected)
            self.assertEqual(boxes.calls[5], ("server", "bash bench-rig/box/target.sh start r1-hello-rapira-worker rapira 32 /opt/bench/rapira/abc1234 worker @RIG@/apps/hello/worker.php"))
            # Stage 1 asks 20000 req/s: 5000 per loader, 4 threads, 256 / 4 = 64 connections.
            self.assertEqual(
                shlex.split(boxes.calls[37][1])[4:],
                ["5000", "4", "64", "20", "http://10.0.0.1:8080/?name=you", "GET", "-"],
            )
            # probe.sh resolves a relative expect file under the staged rig.
            self.assertEqual(
                shlex.split(boxes.calls[7][1]),
                ["bash", "bench-rig/box/probe.sh", "http://10.0.0.1:8080/?name=you", "apps/hello/expect.txt", "http1", "GET", "-"],
            )
            raw = path.parent / "raw" / "r1-hello-rapira-worker"
            self.assertEqual(sorted(p.name for p in raw.iterdir()), sorted(
                [f"{stage}-loader-{i}.txt" for stage in (0, 1) for i in range(1, 5)]
                + ["config.toml", "server.log", "snapshots.txt"]
            ))
            self.assertEqual(boxes.copies, [("server", "/opt/bench/run/r1-hello-rapira-worker.toml")])
            # One cell: 1 * (8 * 20 + 60) + 300 seconds.
            ttl.assert_called_once_with([SERVER, *LOADERS], 520)

    def test_interrupt_stops_the_target_and_writes_an_incomplete_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({("loader-2", LOAD): [load_reply(), KeyboardInterrupt()]}))
            with self.assertRaises(KeyboardInterrupt):
                bench(boxes, Path(tmp), [WORKER, FPM])
            run = json.loads((Path(tmp) / "run1" / "run.json").read_text())
            self.assertEqual(run["status"], "incomplete")
            self.assertEqual(run["plan"], ["r1-hello-rapira-worker", "r1-hello-php-fpm"])
            self.assertEqual([(c["key"], c["status"]) for c in run["cells"]], [("r1-hello-rapira-worker", "incomplete")])
            self.assertIn(("server", "bash bench-rig/box/target.sh stop r1-hello-rapira-worker rapira"), boxes.calls)


GRPCWEB = Target(
    name="grpc-rapira-grpcweb", server="rapira", app="grpc", mode="dispatcher", proto="http1", binary="pr",
    start=("grpc", "@RIG@/apps/grpc/php/dispatcher.php"), url="/bench.v1.EchoService/Echo",
    expect="apps/grpc/expect.grpcweb", config="servers/rapira/grpc.toml.tpl", method="POST",
    headers=(("content-type", "application/grpc-web+proto"), ("x-grpc-web", "1")), body="apps/grpc/echo.grpc",
)
GRPC = Target(
    name="grpc-rapira", server="rapira", app="grpc", mode="dispatcher", proto="grpc", binary="pr",
    start=("grpc", "@RIG@/apps/grpc/php/dispatcher.php"), url="/bench.v1.EchoService/Echo",
    expect="apps/grpc/expect.grpc", config="servers/rapira/grpc.toml.tpl", method="POST", body="apps/grpc/echo.grpc",
)
GRPC_URL = "http://10.0.0.1:8080/bench.v1.EchoService/Echo"

LOAD_CMD_CASES = [
    {
        "name": "wrk2 gets the request shape and a body path under the staged rig",
        "target": GRPCWEB,
        "rate": 2500,
        "expected": [
            "bash", "bench-rig/box/load.sh", "wrk2", "100.000", "2500", "4", "64", "20", GRPC_URL,
            "POST", "apps/grpc/echo.grpc", "content-type: application/grpc-web+proto", "x-grpc-web: 1",
        ],
    },
    {
        "name": "k6 VUS at a low rate are the connections of one loader",
        "target": GRPC,
        "rate": 2500,
        # 2500 x 0.005 s = 12.5, rounded up to 13 VUs, under the 64 connections of one loader.
        "expected": ["bash", "bench-rig/box/load.sh", "k6", "100.000", "2500", "64", "20", GRPC_URL],
    },
    {
        "name": "k6 VUS at a high rate follow the latency budget",
        "target": GRPC,
        "rate": 40100,
        # 40100 x 0.005 s = 200.5, rounded up to 201 VUs.
        "expected": ["bash", "bench-rig/box/load.sh", "k6", "100.000", "40100", "201", "20", GRPC_URL],
    },
]


class LoadCmdTest(unittest.TestCase):
    def test_load_cmd(self):
        plan = {"conns_per_loader": 64, "loader_threads": THREADS}
        for case in LOAD_CMD_CASES:
            with self.subTest(name=case["name"]):
                cmd = load_cmd(case["target"], GRPC_URL, 100.0, case["rate"], plan, 20)
                self.assertEqual(shlex.split(cmd), case["expected"])


BINARY_DIR_CASES = [
    {
        "name": "base target without a base build",
        "target": replace(WORKER, binary="base"),
        "rapira": {**RAPIRA, "base": None},
        "error": "the suite needs a base build; provision with REF and BASE_REF",
    },
]


class BinaryDirTest(unittest.TestCase):
    def test_binary_dir(self):
        for case in BINARY_DIR_CASES:
            with self.subTest(name=case["name"]):
                with self.assertRaises(ValueError) as ctx:
                    binary_dir(case["target"], case["rapira"])
                self.assertEqual(str(ctx.exception), case["error"])


PLAN_CASES = [
    {"name": "256 over 4 loaders x 4 threads", "connections": 256, "error": None, "per_loader": 64},
    {"name": "128 over 4 loaders x 4 threads", "connections": 128, "error": None, "per_loader": 32},
    {"name": "250 is not a multiple of 16", "connections": 250, "error": "connections 250", "per_loader": None},
    {"name": "8 is under one connection per thread", "connections": 8, "error": "connections 8", "per_loader": None},
]


class PlanRunTest(unittest.TestCase):
    def test_plan_run(self):
        for case in PLAN_CASES:
            with self.subTest(name=case["name"]):
                s = suite([WORKER, FPM], case["connections"])
                if case["error"]:
                    with self.assertRaisesRegex(SuiteError, case["error"]):
                        plan_run(RIG, s, processes=32, loader_threads=THREADS)
                    continue
                plan = plan_run(RIG, s, processes=32, loader_threads=THREADS)
                self.assertEqual(plan["conns_per_loader"], case["per_loader"])
                self.assertEqual(plan["keys"], ["r1-hello-rapira-worker", "r1-hello-php-fpm"])

    def test_refused_suite_creates_no_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({}))
            with self.assertRaises(SuiteError):
                bench(boxes, Path(tmp), [WORKER], connections=250)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertEqual(boxes.calls, [])
