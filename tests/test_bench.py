import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rig.bench import counted_s, load_cmd, plan_run, run_suite, sample_jobs
from rig.merge import ERROR_KEYS
from rig.registry import Suite, SuiteError, Target
from rig.rig import Rig
from rig.ssh import Host, SshError

SERVER = Host("server", "3.0.0.1", "10.0.0.1")
LOADERS = (Host("loader-1", "3.0.0.2", "10.0.0.2"), Host("loader-2", "3.0.0.3", "10.0.0.3"))
RIG = Rig(SERVER, LOADERS, "c7a.8xlarge", "c7a.2xlarge", "ami-0123", Path("key"))
# run_suite hashes a suite file into the run file.
SUITE_FILE = Path(__file__).resolve().parent.parent / "suites" / "ci.toml"
RAPIRA = {"ref": "nightly", "sha": "abc1234def", "version": "0.9.0", "build": "nightly", "dir": "/opt/bench/rapira/abc1234", "pr": None}
THREADS = 4

WORKER = Target(
    name="hello-rapira-worker", server="rapira", app="hello", mode="worker", proto="http1",
    start=("worker", "@RIG@/apps/hello/worker.php"), url="/?name=you", expect="apps/hello/expect.txt",
    config="servers/rapira/http.toml.tpl",
)
GRPC = Target(
    name="grpc-rapira", server="rapira", app="grpc", mode="dispatcher", proto="grpc",
    start=("grpc", "@RIG@/apps/grpc/php/dispatcher.php"), url="/bench.v1.EchoService/Echo",
    expect="apps/grpc/expect.grpc", config="servers/rapira/grpc.toml.tpl", method="POST", body="apps/grpc/echo.grpc",
)
URL = "http://10.0.0.1:8080/?name=you"
GRPC_URL = "http://10.0.0.1:8080/bench.v1.EchoService/Echo"

BOX = "bash bench-rig/box/"
START = BOX + "target.sh start"
PIDS = BOX + "target.sh probe"
STOP = BOX + "target.sh stop"
LOG = BOX + "target.sh log"
MEM = BOX + "target.sh mem"
PROBE = BOX + "probe.sh"
LOAD = BOX + "load.sh"
WAIT = "python3 -c "
FACTS = "echo kernel="

# Two loaders. Each wrk2 process asks 125000 req/s for 70 s: HELD_WRK2 requests from both is
# 250000 req/s, and SHORT_WRK2 is 16000000 / 70 = 228571 req/s, under 95% of 250000.
# Each h2load process asks 50000 req/s and counts 60 s: HELD_H2LOAD from both is 100000 req/s.
HELD_WRK2 = 8750000
SHORT_WRK2 = 8000000
HELD_H2LOAD = 3000000


def suite(targets, connections=5000):
    return Suite(
        name="test", rounds=1, warmup_s=10, duration_s=60, smoke=False,
        rates={"hello": 250000, "grpc": 100000}, connections={"http1": connections, "grpc": 100}, grpc_streams=100,
        targets=tuple(targets),
    )


def result_line(tool, requests, late_ms=0, status=0):
    body = {
        "tool": tool, "late_ms": late_ms, "duration_us": 70000000 if tool == "wrk2" else 60000000,
        "requests": requests, "bytes": requests * 126 if tool == "wrk2" else 0,
        "errors": {key: 0 for key in ERROR_KEYS} | {"status": status},
        "latency_us": {"mean": 700.0, "p50": 690, "p90": 1100, "p95": 1170, "p99": 1260, "p999": 1350, "max": 2800},
        "requests_per_sec": requests / 70,
    }
    return f"Running test\nRESULT {json.dumps(body)}\n"


def load_reply(requests=None, late_ms=0, status=0):
    """A loader that holds its share of the rate, or answers `requests` requests."""

    def reply(cmd):
        tool = shlex.split(cmd)[2]
        count = requests if requests is not None else (HELD_H2LOAD if tool == "h2load" else HELD_WRK2)
        return result_line(tool, count, late_ms, status)

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
        ("*", FACTS): "kernel=6.17.1\ninstance_id=i-0abc\naz=eu-central-1a\nplacement_group=rapira-bench\nwrk2=44a94c17d8e6a0bac8559b53da76848e430cb7a7\nh2load=h2load nghttp2/1.68.0\n",
        ("server", START): "config=/opt/bench/run/r1-hello-rapira-worker.toml\npid=100\n",
        ("server", PIDS): "101 102\n4096\n",
        ("server", STOP): "",
        ("server", LOG): "",
        ("*", WAIT): "",
        ("server", MEM): "204800\n",
        # The server is 50% busy in every window.
        ("server", "bash bench-rig/box/snapshot.sh"): counter(50, 100, "conns 5000 0\n"),
        ("*", PROBE): "",
        ("*", LOAD): load_reply(),
    }
    # Every loader is 90% busy in every window.
    for host in LOADERS:
        table[(host.name, "bash bench-rig/box/snapshot.sh")] = counter(90, 100)
    table.update(overrides)
    return table


class FakeBoxes:
    """Replies by (host name, command prefix); "*" matches any host. A list reply is used in order.

    A command of parts joined by " && " gets the replies of its parts, concatenated.
    """

    def __init__(self, table):
        self.table = table
        self.calls = []
        self.copies = []

    def reply(self, host, cmd):
        self.calls.append((host.name, cmd))
        return "".join(self.part(host, part) for part in cmd.split(" && "))

    def part(self, host, cmd):
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
    for prefix, name in ((LOAD, "load"), (START, "start"), (PIDS, "pids"), (STOP, "stop"), (LOG, "log"), (MEM, "mem"),
                         (PROBE, "probe"), (WAIT, "snapshot"), (FACTS, "facts")):
        if cmd.startswith(prefix):
            return name
    return cmd


# One stage batch: the load of 2 loaders, then the begin and end snapshots of the 3 boxes, then the RSS read.
STAGE = ["load"] * 2 + ["snapshot"] * 6 + ["mem"]

NO_NUMBERS = {"rate": None, "achieved_rps": None, "successful_rps": None, "errors": None, "latency_us": None, "rss_kb": None, "held": None, "cpu": None}

CELL_CASES = [
    {
        "name": "holds the rate",
        "overrides": {},
        "status": "ok",
        "reason": None,
        "held": True,
        "achieved_rps": 250000.0,
        "rss_kb": 204800,
        "flags": {},
        "loads": 2,
    },
    {
        # 228571 req/s is under 95% of 250000. Every loader is at 90% and the server at 50%.
        "name": "does not hold the rate and is generator bound",
        "overrides": {("*", LOAD): load_reply(SHORT_WRK2)},
        "status": "ok",
        "reason": None,
        "held": False,
        "achieved_rps": 16000000 / 70,
        "rss_kb": 204800,
        "flags": {"generator_bound": True},
        "loads": 2,
    },
    {
        "name": "status errors at the full rate do not hold",
        "overrides": {("*", LOAD): load_reply(status=5)},
        "status": "ok",
        "reason": None,
        "held": False,
        "achieved_rps": 250000.0,
        "rss_kb": 204800,
        "flags": {"generator_bound": True},
        "loads": 2,
    },
    {
        "name": "probe mismatch on loader-2 voids before load",
        "overrides": {("loader-2", PROBE): SshError("loader-2: exit 1: bash bench-rig/box/probe.sh\n/home/fedora/bench-rig/apps/hello/expect.txt /tmp/tmp.Xy12 differ: byte 7, line 1")},
        "status": "void",
        "reason": "probe mismatch on loader-2: /home/fedora/bench-rig/apps/hello/expect.txt /tmp/tmp.Xy12 differ: byte 7, line 1",
        "held": None,
        "achieved_rps": None,
        "rss_kb": None,
        "flags": {},
        "loads": 0,
    },
    {
        "name": "missing RESULT from loader-2 voids with the loader name",
        "overrides": {("loader-2", LOAD): "wrk2: panic: bytecode\n"},
        "status": "void",
        "reason": "loader-2",
        "held": None,
        "achieved_rps": None,
        "rss_kb": None,
        "flags": {},
        "loads": 2,
    },
    {
        "name": "loader-1 late by 1500 ms voids",
        "overrides": {("loader-1", LOAD): load_reply(late_ms=1500)},
        "status": "void",
        "reason": "loader-1",
        "held": None,
        "achieved_rps": None,
        "rss_kb": None,
        "flags": {},
        "loads": 2,
    },
    {
        "name": "failed probe after the stage sets died",
        "overrides": {("loader-1", PROBE): ["", SshError("loader-1: exit 1: probe")]},
        "status": "ok",
        "reason": None,
        "held": True,
        "achieved_rps": 250000.0,
        "rss_kb": 204800,
        "flags": {"died": True},
        "loads": 2,
    },
    {
        # The stage completes; the void at the stop clears the numbers.
        "name": "a WARN line in the rapira log voids after the stage",
        "overrides": {("server", LOG): "2026-09-25T10:00:00Z WARN worker restarted\n"},
        "status": "void",
        "reason": "server log: 1 warn or error lines",
        "held": None,
        "achieved_rps": None,
        "rss_kb": None,
        "flags": {},
        "loads": 2,
    },
]

INTERRUPT_CASES = [
    {"name": "interrupt during the load", "overrides": {("loader-2", LOAD): KeyboardInterrupt()}},
    {"name": "interrupt during target.sh stop", "overrides": {("server", STOP): KeyboardInterrupt()}},
]


def bench(boxes, out, targets, connections=5000, threads=THREADS):
    with mock.patch("rig.bench.ensure_ttl") as ttl:
        path = run_suite(
            RIG, suite(targets, connections), boxes, out, suite_path=SUITE_FILE, processes=32, run_id="run1", rapira=RAPIRA,
            servers={}, apps={}, loader_threads=threads,
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
                self.assertEqual(cell["achieved_rps"], case["achieved_rps"])
                self.assertEqual(cell["rss_kb"], case["rss_kb"])
                if case["status"] != "ok":
                    self.assertEqual({key: cell[key] for key in NO_NUMBERS}, NO_NUMBERS)
                self.assertEqual(cell["flags"], case["flags"])
                self.assertEqual(sum(1 for _, cmd in boxes.calls if label(cmd) == "load"), case["loads"])
                self.assertEqual([label(cmd) for _, cmd in boxes.calls][-2:], ["stop", "log"])
                self.assertEqual(run["status"], "complete" if case["status"] == "ok" else "incomplete")

    def test_grpc_cell_counts_the_measured_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({("server", START): "config=/opt/bench/run/r1-grpc-rapira.toml\npid=100\n"}))
            path, _ = bench(boxes, Path(tmp), [GRPC])
            cell = json.loads(path.read_text())["cells"][0]
            # 2 x 3000000 requests over the 60 s window.
            self.assertEqual((cell["status"], cell["held"], cell["achieved_rps"], cell["rate"]), ("ok", True, 100000.0, 100000))
            load = next(cmd for _, cmd in boxes.calls if label(cmd) == "load")
            # 50000 req/s per loader, 4 threads, 100 / 2 = 50 connections, 100 streams, 10 s warm-up, 60 s window.
            self.assertEqual(shlex.split(load)[2:3] + shlex.split(load)[4:], ["h2load", "50000", "4", "50", "100", "10", "60", GRPC_URL, "apps/grpc/echo.grpc"])

    def test_command_order_and_raw_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({}))
            path, ttl = bench(boxes, Path(tmp), [WORKER])
            expected = ["facts"] * 3 + ["start", "pids"] + ["probe"] * 2 + STAGE + ["probe", "pids", "stop", "log"]
            self.assertEqual([label(cmd) for _, cmd in boxes.calls], expected)
            self.assertEqual(boxes.calls[3], ("server", "bash bench-rig/box/target.sh start r1-hello-rapira-worker rapira 32 /opt/bench/rapira/abc1234 worker @RIG@/apps/hello/worker.php"))
            # probe.sh resolves a relative expect file under the staged rig.
            self.assertEqual(shlex.split(boxes.calls[5][1]), ["bash", "bench-rig/box/probe.sh", URL, "apps/hello/expect.txt", "http1", "GET", "-"])
            # 125000 req/s per loader, 4 threads, 5000 / 2 = 2500 connections, 10 s warm-up, 60 s window.
            self.assertEqual(shlex.split(boxes.calls[7][1])[4:], ["125000", "4", "2500", "10", "60", URL, "GET", "-"])
            raw = path.parent / "raw" / "r1-hello-rapira-worker"
            self.assertEqual(sorted(p.name for p in raw.iterdir()), ["config.toml", "load-loader-1.txt", "load-loader-2.txt", "server.log", "snapshots.txt"])
            self.assertEqual(boxes.copies, [("server", "/opt/bench/run/r1-hello-rapira-worker.toml")])
            # One cell: 1 x (10 + 60 + 60) + 300 seconds.
            ttl.assert_called_once_with([SERVER, *LOADERS], 430)

    def test_interrupt_stops_the_target_and_writes_an_incomplete_run(self):
        for case in INTERRUPT_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                boxes = FakeBoxes(replies(case["overrides"]))
                with self.assertRaises(KeyboardInterrupt):
                    bench(boxes, Path(tmp), [WORKER, GRPC])
                run = json.loads((Path(tmp) / "run1" / "run.json").read_text())
                self.assertEqual(run["status"], "incomplete")
                self.assertEqual(run["plan"], ["r1-hello-rapira-worker", "r1-grpc-rapira"])
                self.assertEqual([(c["key"], c["status"]) for c in run["cells"]], [("r1-hello-rapira-worker", "incomplete")])
                self.assertEqual({key: run["cells"][0][key] for key in NO_NUMBERS}, NO_NUMBERS)
                self.assertIn(("server", "bash bench-rig/box/target.sh stop r1-hello-rapira-worker rapira"), boxes.calls)


PLAN = {"conns_per_loader": {"http1": 2500, "grpc": 50}, "loader_threads": THREADS}

LOAD_CMD_CASES = [
    {
        "name": "wrk2 gets the threads, the connections of one loader, and the request shape",
        "target": WORKER,
        "rate": 125000,
        "expected": ["bash", "bench-rig/box/load.sh", "wrk2", "100.000", "125000", "4", "2500", "10", "60", URL, "GET", "-"],
    },
    {
        "name": "h2load gets the streams and the body path under the staged rig",
        "target": GRPC,
        "rate": 50000,
        "expected": ["bash", "bench-rig/box/load.sh", "h2load", "100.000", "50000", "4", "50", "100", "10", "60", GRPC_URL, "apps/grpc/echo.grpc"],
    },
]

COUNTED_CASES = [
    {"name": "wrk2 counts the warm-up and the window", "target": WORKER, "expected": 70},
    {"name": "h2load counts the window only", "target": GRPC, "expected": 60},
]

WAIT_ARGV = ["python3", "-c", "import sys, time; time.sleep(max(0.0, float(sys.argv[1]) - time.time()))"]

SAMPLE_JOBS_CASES = [
    {
        "name": "server samples pass the port, loader samples pass no port",
        "epoch": 110.0,
        "duration_s": 60,
        "expected": {
            "server": [
                [*WAIT_ARGV, "110.000", "&&", "bash", "bench-rig/box/snapshot.sh", "8080"],
                [*WAIT_ARGV, "170.500", "&&", "bash", "bench-rig/box/snapshot.sh", "8080"],
            ],
            "loader-1": [
                [*WAIT_ARGV, "110.000", "&&", "bash", "bench-rig/box/snapshot.sh"],
                [*WAIT_ARGV, "170.500", "&&", "bash", "bench-rig/box/snapshot.sh"],
            ],
        },
    },
]

PLAN_CASES = [
    {"name": "5000 over 2 loaders x 4 threads", "connections": 5000, "threads": 4, "error": None, "per_loader": {"http1": 2500, "grpc": 50}},
    {"name": "4000 over 2 loaders x 4 threads", "connections": 4000, "threads": 4, "error": None, "per_loader": {"http1": 2000, "grpc": 50}},
    # 5000 % 16 = 8.
    {"name": "5000 is not a multiple of 2 loaders x 8 threads", "connections": 5000, "threads": 8, "error": "connections 5000", "per_loader": None},
    {"name": "4 is under one connection per thread", "connections": 4, "threads": 4, "error": "connections 4", "per_loader": None},
]


class LoadCmdTest(unittest.TestCase):
    def test_load_cmd(self):
        for case in LOAD_CMD_CASES:
            with self.subTest(name=case["name"]):
                cmd = load_cmd(case["target"], f"http://10.0.0.1:8080{case['target'].url}", 100.0, case["rate"], PLAN, suite([WORKER, GRPC]))
                self.assertEqual(shlex.split(cmd), case["expected"])

    def test_counted_s(self):
        for case in COUNTED_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(counted_s(case["target"], suite([WORKER, GRPC])), case["expected"])


class SampleJobsTest(unittest.TestCase):
    def test_sample_jobs(self):
        for case in SAMPLE_JOBS_CASES:
            with self.subTest(name=case["name"]):
                jobs = sample_jobs(RIG, case["epoch"], case["duration_s"])
                for name, expected in case["expected"].items():
                    self.assertEqual([shlex.split(cmd) for _, host, cmd in jobs if host.name == name], expected)


class PlanRunTest(unittest.TestCase):
    def test_plan_run(self):
        for case in PLAN_CASES:
            with self.subTest(name=case["name"]):
                s = suite([WORKER, GRPC], case["connections"])
                if case["error"]:
                    with self.assertRaisesRegex(SuiteError, case["error"]):
                        plan_run(RIG, s, processes=32, loader_threads=case["threads"])
                    continue
                plan = plan_run(RIG, s, processes=32, loader_threads=case["threads"])
                self.assertEqual(plan["conns_per_loader"], case["per_loader"])
                self.assertEqual(plan["keys"], ["r1-hello-rapira-worker", "r1-grpc-rapira"])

    def test_refused_suite_creates_no_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({}))
            with self.assertRaisesRegex(SuiteError, "connections 5000"):
                bench(boxes, Path(tmp), [WORKER], threads=8)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertEqual(boxes.calls, [])
