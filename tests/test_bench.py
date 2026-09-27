import contextlib
import io
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rig.__main__ import main
from rig.bench import counted_s, load_cmd, plan_run, run_suite, sample_jobs, ttl_needed_s
from rig.merge import ERROR_KEYS
from rig.registry import Stage, Suite, SuiteError, Target, load_suite, load_targets, plan_cells
from rig.rig import Rig
from rig.ssh import Host, SshError

SERVER = Host("server", "3.0.0.1", "10.0.0.1")
LOADERS = (Host("loader-1", "3.0.0.2", "10.0.0.2"), Host("loader-2", "3.0.0.3", "10.0.0.3"))
RIG = Rig(SERVER, LOADERS, "c7a.8xlarge", "c7a.2xlarge", "ami-0123", Path("key"))
ROOT = Path(__file__).resolve().parent.parent
# run_suite hashes a suite file into the run file.
SUITE_FILE = ROOT / "suites" / "ci.toml"
NEW = {"ref": "nightly", "sha": "abc1234def", "version": "0.9.0", "build": "nightly", "dir": "/opt/bench/rapira/abc1234", "pr": None}
BASE = {"ref": "cache", "sha": "0f0f0f0aaa", "version": "0.8.1", "build": "cache", "dir": "/opt/bench/rapira/base-0f0f0f0"}
BUILDS = {"new": NEW, "base": BASE}
# The server of the rig has 8 vCPUs and each loader 4.
SERVER_VCPUS = 8
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

# Two loaders. In the rate stage each wrk2 process asks 125000 req/s for 70 s: HELD_WRK2 requests from both is
# 250000 req/s, and SHORT_WRK2 is 16000000 / 70 = 228571 req/s, under 95% of 250000.
# Each h2load process asks 50000 req/s and counts 60 s: HELD_H2LOAD from both is 100000 req/s.
HELD_WRK2 = 8750000
SHORT_WRK2 = 8000000
HELD_H2LOAD = 3000000

RATE_STAGE = Stage(warmup_s=10, duration_s=60, processes=None, rates={"hello-rapira-worker": 250000, "grpc-rapira": 100000})
CAP_STAGE = Stage(warmup_s=5, duration_s=15, processes=2, rates={"hello-rapira-worker": 300000, "grpc-rapira": 300000})


def suite(targets, connections=1000, rounds=1):
    return Suite(
        name="test", rounds=rounds, smoke=False, connections={"http1": connections, "grpc": 100}, grpc_streams=100,
        targets=tuple(targets), stages={"rate": RATE_STAGE, "cap": CAP_STAGE},
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


def load_reply(requests=None, late_ms=0, status=0, calibration_ms=()):
    """A loader that holds its share of the rate, or answers `requests` requests.

    wrk2 prints one calibration line per mean of `calibration_ms` before the result.
    """

    def reply(cmd):
        tool = shlex.split(cmd)[2]
        count = requests if requests is not None else (HELD_H2LOAD if tool == "h2load" else HELD_WRK2)
        lines = "".join(f"  Thread calibration: mean lat.: {mean:.3f}ms, rate sampling interval: 10ms\n" for mean in calibration_ms)
        return lines + result_line(tool, count, late_ms, status)

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
        # target.sh start TAG ...: the rendered config has the cell key as its tag.
        ("server", START): lambda cmd: f"config=/opt/bench/run/{shlex.split(cmd)[3]}.toml\npid=100\n",
        ("server", PIDS): "101 102\n4096\n",
        ("server", STOP): "",
        ("server", LOG): "",
        ("*", WAIT): "",
        ("server", MEM): "204800\n",
        # The server is 50% busy in every window.
        ("server", "bash bench-rig/box/snapshot.sh"): counter(50, 100, "conns 1000 0\n"),
        ("*", PROBE): "",
        ("*", LOAD): load_reply(),
    }
    # Every loader is 60% busy in every window: under LOADER_BUSY.
    for host in LOADERS:
        table[(host.name, "bash bench-rig/box/snapshot.sh")] = counter(60, 100)
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
# The calls of one cell from the start of the target to the log read.
CELL = ["start", "pids"] + ["probe"] * 2 + STAGE + ["probe", "pids", "stop", "log"]

NO_NUMBERS = {"rate": None, "achieved_rps": None, "successful_rps": None, "errors": None, "latency_us": None, "rss_kb": None, "held": None, "cpu": None}

# One target and one round give 4 cells. The checks read the first cell, r1-base-rate-hello-rapira-worker.
# "loads" counts the load calls of all 4 cells: 2 loaders x 4 cells = 8 when every cell reaches its stage.
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
        "loads": 8,
    },
    {
        # 228571 req/s is under 95% of 250000. Every loader is at 90% and the server at 50%.
        # 90 is at least GENERATOR_BUSY and above LOADER_BUSY.
        "name": "does not hold the rate and is generator bound",
        "overrides": {
            ("*", LOAD): load_reply(SHORT_WRK2),
            ("loader-1", "bash bench-rig/box/snapshot.sh"): counter(90, 100),
            ("loader-2", "bash bench-rig/box/snapshot.sh"): counter(90, 100),
        },
        "status": "ok",
        "reason": None,
        "held": False,
        "achieved_rps": 16000000 / 70,
        "rss_kb": 204800,
        "flags": {"generator_bound": True, "loader_busy": 90},
        "loads": 8,
    },
    {
        # The server is at 50% and every loader at 60%, under GENERATOR_BUSY.
        "name": "status errors at the full rate do not hold",
        "overrides": {("*", LOAD): load_reply(status=5)},
        "status": "ok",
        "reason": None,
        "held": False,
        "achieved_rps": 250000.0,
        "rss_kb": 204800,
        "flags": {"server_unsaturated": True},
        "loads": 8,
    },
    {
        # Every cell fails its first probe, so no cell reaches its stage.
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
        "loads": 8,
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
        "loads": 8,
    },
    {
        # The first cell fails only its probe after the stage. The other cells probe without an error.
        "name": "failed probe after the stage sets died",
        "overrides": {("loader-1", PROBE): ["", SshError("loader-1: exit 1: probe"), ""]},
        "status": "ok",
        "reason": None,
        "held": True,
        "achieved_rps": 250000.0,
        "rss_kb": 204800,
        "flags": {"died": True},
        "loads": 8,
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
        "loads": 8,
    },
]

EVEN_MS = (3.6, 3.6, 3.6, 3.6)
# 4.2 is above 3.6 x 1.15 = 4.14, the median of the other threads plus SKEW_PCT.
SKEWED_MS = (3.6, 3.6, 3.6, 4.2)
SKEW = {"loader": "loader-2", "thread_ms": 4.2, "median_ms": 3.6}

SKEW_CELL_CASES = [
    {
        # The rate cells of wrk2 get the flag. The cap cells of the same output do not.
        "name": "a skewed wrk2 thread on loader-2 flags the rate cells only",
        "targets": [WORKER],
        "overrides": {
            ("loader-1", LOAD): load_reply(calibration_ms=EVEN_MS),
            ("loader-2", LOAD): load_reply(calibration_ms=SKEWED_MS),
        },
        "skew": {
            "r1-base-rate-hello-rapira-worker": SKEW,
            "r1-new-rate-hello-rapira-worker": SKEW,
            "r1-base-cap-hello-rapira-worker": None,
            "r1-new-cap-hello-rapira-worker": None,
        },
        "calibration_ms": [list(EVEN_MS), list(SKEWED_MS)],
    },
    {
        "name": "an even wrk2 calibration has no flag",
        "targets": [WORKER],
        "overrides": {("*", LOAD): load_reply(calibration_ms=EVEN_MS)},
        "skew": {
            "r1-base-rate-hello-rapira-worker": None,
            "r1-new-rate-hello-rapira-worker": None,
            "r1-base-cap-hello-rapira-worker": None,
            "r1-new-cap-hello-rapira-worker": None,
        },
        "calibration_ms": [list(EVEN_MS), list(EVEN_MS)],
    },
    {
        # h2load prints no calibration line.
        "name": "an h2load cell has an empty calibration and no flag",
        "targets": [GRPC],
        "overrides": {},
        "skew": {
            "r1-base-rate-grpc-rapira": None,
            "r1-new-rate-grpc-rapira": None,
            "r1-base-cap-grpc-rapira": None,
            "r1-new-cap-grpc-rapira": None,
        },
        "calibration_ms": [[], []],
    },
]

INTERRUPT_CASES = [
    {"name": "interrupt during the load", "overrides": {("loader-2", LOAD): KeyboardInterrupt()}},
    {"name": "interrupt during target.sh stop", "overrides": {("server", STOP): KeyboardInterrupt()}},
]


# key, build, stage, and processes of the cell record; the processes and the binary dir of target.sh start;
# the rate per loader, the warm-up, and the window of the first load.sh call of the cell.
# The rate stage has 8 processes, one per server vCPU, and the cap stage has CAP_STAGE.processes = 2.
# A loader sends half of the stage rate: 250000 / 2 = 125000 and 300000 / 2 = 150000.
RUN_CELLS_CASES = [
    {
        # Round 1 runs base then new, and the rate pair before the cap pair.
        "name": "one round runs base first",
        "rounds": 1,
        "cells": [
            ("r1-base-rate-hello-rapira-worker", "base", "rate", 8, 250000, ["8", BASE["dir"]], ["125000", "10", "60"]),
            ("r1-new-rate-hello-rapira-worker", "new", "rate", 8, 250000, ["8", NEW["dir"]], ["125000", "10", "60"]),
            ("r1-base-cap-hello-rapira-worker", "base", "cap", 2, 300000, ["2", BASE["dir"]], ["150000", "5", "15"]),
            ("r1-new-cap-hello-rapira-worker", "new", "cap", 2, 300000, ["2", NEW["dir"]], ["150000", "5", "15"]),
        ],
    },
    {
        # Round 2 runs new then base.
        "name": "two rounds alternate the build order",
        "rounds": 2,
        "cells": [
            ("r1-base-rate-hello-rapira-worker", "base", "rate", 8, 250000, ["8", BASE["dir"]], ["125000", "10", "60"]),
            ("r1-new-rate-hello-rapira-worker", "new", "rate", 8, 250000, ["8", NEW["dir"]], ["125000", "10", "60"]),
            ("r1-base-cap-hello-rapira-worker", "base", "cap", 2, 300000, ["2", BASE["dir"]], ["150000", "5", "15"]),
            ("r1-new-cap-hello-rapira-worker", "new", "cap", 2, 300000, ["2", NEW["dir"]], ["150000", "5", "15"]),
            ("r2-new-rate-hello-rapira-worker", "new", "rate", 8, 250000, ["8", NEW["dir"]], ["125000", "10", "60"]),
            ("r2-base-rate-hello-rapira-worker", "base", "rate", 8, 250000, ["8", BASE["dir"]], ["125000", "10", "60"]),
            ("r2-new-cap-hello-rapira-worker", "new", "cap", 2, 300000, ["2", NEW["dir"]], ["150000", "5", "15"]),
            ("r2-base-cap-hello-rapira-worker", "base", "cap", 2, 300000, ["2", BASE["dir"]], ["150000", "5", "15"]),
        ],
    },
]


def bench(boxes, out, targets, connections=1000, threads=THREADS, rounds=1):
    with mock.patch("rig.bench.ensure_ttl") as ttl:
        path = run_suite(
            RIG, suite(targets, connections, rounds), boxes, out, suite_path=SUITE_FILE, run_id="run1", builds=BUILDS,
            servers={}, apps={}, server_vcpus=SERVER_VCPUS, loader_threads=threads,
        )
    return path, ttl


def cell_calls(calls):
    """The calls of each cell: a cell starts at its target.sh start call."""
    cells = []
    for _, cmd in calls:
        if label(cmd) == "start":
            cells.append([])
        if cells:
            cells[-1].append(cmd)
    return cells


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
                # A void row overrides the reply of every cell, so every new cell of the target is void.
                self.assertEqual(run["status"], "complete" if case["status"] == "ok" else "broken")

    def test_cell_plan(self):
        for case in RUN_CELLS_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                boxes = FakeBoxes(replies({}))
                path, _ = bench(boxes, Path(tmp), [WORKER], rounds=case["rounds"])
                run = json.loads(path.read_text())
                got = []
                for cell, cmds in zip(run["cells"], cell_calls(boxes.calls)):
                    load = next(shlex.split(cmd) for cmd in cmds if label(cmd) == "load")
                    got.append((
                        cell["key"], cell["build"], cell["stage"], cell["processes"], cell["rate"],
                        shlex.split(cmds[0])[5:7], [load[4], load[7], load[8]],
                    ))
                self.assertEqual(got, case["cells"])
                self.assertEqual(run["plan"], [cell[0] for cell in case["cells"]])
                self.assertEqual((run["rapira"], run["base"]), (NEW, BASE))
                self.assertNotIn("processes", run)

    def test_loader_skew(self):
        for case in SKEW_CELL_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                boxes = FakeBoxes(replies(case["overrides"]))
                path, _ = bench(boxes, Path(tmp), case["targets"])
                cells = json.loads(path.read_text())["cells"]
                self.assertEqual({cell["key"]: cell["flags"].get("loader_skew") for cell in cells}, case["skew"])
                for cell in cells:
                    self.assertEqual([entry["calibration_ms"] for entry in cell["loaders"]], case["calibration_ms"])

    def test_ok_cell_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({}))
            path, _ = bench(boxes, Path(tmp), [WORKER])
            cell = json.loads(path.read_text())["cells"][0]
            # 2 x 8750000 requests with no status errors over 70 s: 17500000 / 70 = 250000 successful req/s.
            # Both loaders print the same percentiles, so each maximum is that value as a float.
            # The fake counters step 50 of 100 on the server and 60 of 100 on the loaders: 50% and 60% busy.
            self.assertEqual(cell["successful_rps"], 250000.0)
            self.assertEqual(cell["errors"], {key: 0 for key in ERROR_KEYS})
            self.assertEqual(cell["latency_us"], {"p50": 690.0, "p90": 1100.0, "p99": 1260.0, "p999": 1350.0, "max": 2800.0})
            self.assertEqual(cell["cpu"], {"server_busy": 50, "loader_busy": 60})
            self.assertEqual(
                [(entry["loader"], entry["tool"], entry["requests"], entry["busy_cpu"]) for entry in cell["loaders"]],
                [("loader-1", "wrk2", 8750000, 60), ("loader-2", "wrk2", 8750000, 60)],
            )

    def test_grpc_cell_counts_the_measured_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({}))
            path, _ = bench(boxes, Path(tmp), [GRPC])
            cell = json.loads(path.read_text())["cells"][0]
            # 2 x 3000000 requests over the 60 s window of the rate stage.
            self.assertEqual((cell["status"], cell["held"], cell["achieved_rps"], cell["rate"]), ("ok", True, 100000.0, 100000))
            load = next(cmd for _, cmd in boxes.calls if label(cmd) == "load")
            # 50000 req/s per loader, 4 threads, 100 / 2 = 50 connections, 100 streams, 10 s warm-up, 60 s window.
            self.assertEqual(shlex.split(load)[2:3] + shlex.split(load)[4:], ["h2load", "50000", "4", "50", "100", "10", "60", GRPC_URL, "apps/grpc/echo.grpc"])

    def test_command_order_and_raw_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({}))
            path, ttl = bench(boxes, Path(tmp), [WORKER])
            expected = ["facts"] * 3 + CELL * 4
            self.assertEqual([label(cmd) for _, cmd in boxes.calls], expected)
            self.assertEqual(boxes.calls[3], ("server", "bash bench-rig/box/target.sh start r1-base-rate-hello-rapira-worker rapira 8 /opt/bench/rapira/base-0f0f0f0 worker @RIG@/apps/hello/worker.php"))
            # probe.sh resolves a relative expect file under the staged rig.
            self.assertEqual(shlex.split(boxes.calls[5][1]), ["bash", "bench-rig/box/probe.sh", URL, "apps/hello/expect.txt", "http1", "GET", "-"])
            # 125000 req/s per loader, 4 threads, 1000 / 2 = 500 connections, 10 s warm-up, 60 s window.
            self.assertEqual(shlex.split(boxes.calls[7][1])[4:], ["125000", "4", "500", "10", "60", URL, "GET", "-"])
            raw = path.parent / "raw" / "r1-base-rate-hello-rapira-worker"
            self.assertEqual(sorted(p.name for p in raw.iterdir()), ["config.toml", "load-loader-1.txt", "load-loader-2.txt", "server.log", "snapshots.txt"])
            self.assertEqual(boxes.copies, [
                ("server", "/opt/bench/run/r1-base-rate-hello-rapira-worker.toml"),
                ("server", "/opt/bench/run/r1-new-rate-hello-rapira-worker.toml"),
                ("server", "/opt/bench/run/r1-base-cap-hello-rapira-worker.toml"),
                ("server", "/opt/bench/run/r1-new-cap-hello-rapira-worker.toml"),
            ])
            # 2 rate cells x (10 + 60 + 20) + 2 cap cells x (5 + 15 + 20) + 300 = 560 seconds.
            ttl.assert_called_once_with([SERVER, *LOADERS], 560)

    def test_interrupt_stops_the_target_and_writes_an_incomplete_run(self):
        for case in INTERRUPT_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                boxes = FakeBoxes(replies(case["overrides"]))
                with self.assertRaises(KeyboardInterrupt):
                    bench(boxes, Path(tmp), [WORKER, GRPC])
                run = json.loads((Path(tmp) / "run1" / "run.json").read_text())
                self.assertEqual(run["status"], "incomplete")
                self.assertEqual(run["plan"], [
                    "r1-base-rate-hello-rapira-worker", "r1-new-rate-hello-rapira-worker",
                    "r1-base-cap-hello-rapira-worker", "r1-new-cap-hello-rapira-worker",
                    "r1-base-rate-grpc-rapira", "r1-new-rate-grpc-rapira",
                    "r1-base-cap-grpc-rapira", "r1-new-cap-grpc-rapira",
                ])
                self.assertEqual([(c["key"], c["status"]) for c in run["cells"]], [("r1-base-rate-hello-rapira-worker", "incomplete")])
                self.assertEqual({key: run["cells"][0][key] for key in NO_NUMBERS}, NO_NUMBERS)
                self.assertIn(("server", "bash bench-rig/box/target.sh stop r1-base-rate-hello-rapira-worker rapira"), boxes.calls)


PLAN = {"conns_per_loader": {"http1": 500, "grpc": 50}, "loader_threads": THREADS}

LOAD_CMD_CASES = [
    {
        "name": "wrk2 gets the threads, the connections of one loader, and the request shape",
        "target": WORKER,
        "rate": 125000,
        "plan": PLAN,
        "stage": RATE_STAGE,
        "expected": ["bash", "bench-rig/box/load.sh", "wrk2", "100.000", "125000", "4", "500", "10", "60", URL, "GET", "-"],
    },
    {
        # CAP_STAGE has a 5 s warm-up and a 15 s window.
        "name": "wrk2 gets the warm-up and the window of the cap stage",
        "target": WORKER,
        "rate": 150000,
        "plan": PLAN,
        "stage": CAP_STAGE,
        "expected": ["bash", "bench-rig/box/load.sh", "wrk2", "100.000", "150000", "4", "500", "5", "15", URL, "GET", "-"],
    },
    {
        "name": "h2load gets the streams and the body path under the staged rig",
        "target": GRPC,
        "rate": 50000,
        "plan": PLAN,
        "stage": RATE_STAGE,
        "expected": ["bash", "bench-rig/box/load.sh", "h2load", "100.000", "50000", "4", "50", "100", "10", "60", GRPC_URL, "apps/grpc/echo.grpc"],
    },
    {
        # 25 loaders of 8 vCPUs: 100 gRPC connections give 4 per loader.
        "name": "h2load gets one thread per connection when a loader has fewer connections than threads",
        "target": GRPC,
        "rate": 4000,
        "plan": {"conns_per_loader": {"http1": 200, "grpc": 4}, "loader_threads": 8},
        "stage": RATE_STAGE,
        "expected": ["bash", "bench-rig/box/load.sh", "h2load", "100.000", "4000", "4", "4", "100", "10", "60", GRPC_URL, "apps/grpc/echo.grpc"],
    },
]

COUNTED_CASES = [
    # RATE_STAGE: 10 s warm-up and 60 s window. CAP_STAGE: 5 s warm-up and 15 s window.
    {"name": "wrk2 counts the warm-up and the window", "target": WORKER, "stage": RATE_STAGE, "expected": 70},
    {"name": "h2load counts the window only", "target": GRPC, "stage": RATE_STAGE, "expected": 60},
    {"name": "wrk2 counts the warm-up and the window of the cap stage", "target": WORKER, "stage": CAP_STAGE, "expected": 20},
    {"name": "h2load counts the window of the cap stage only", "target": GRPC, "stage": CAP_STAGE, "expected": 15},
]

CI_SUITE = load_suite(SUITE_FILE, load_targets(ROOT / "suites" / "targets.toml"), len(LOADERS))

TTL_CASES = [
    {
        # 3 rounds x 6 targets x 2 builds = 36 cells per stage kind.
        # 36 x (11 + 15 + 20) + 36 x (5 + 15 + 20) + 300 = 1656 + 1440 + 300 = 3396 seconds.
        "name": "the ci suite",
        "suite": CI_SUITE,
        "expected": 3396,
    },
    {
        # 2 rate cells x (10 + 60 + 20) + 2 cap cells x (5 + 15 + 20) + 300 = 560 seconds.
        "name": "one round of one target",
        "suite": suite([WORKER]),
        "expected": 560,
    },
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
    # 1000 / (2 x 4) = 125 connections per wrk2 thread.
    {"name": "1000 over 2 loaders x 4 threads", "connections": 1000, "threads": 4, "error": None, "per_loader": {"http1": 500, "grpc": 50}},
    # 1004 % 8 = 4.
    {"name": "1004 is not a multiple of 2 loaders x 4 threads", "connections": 1004, "threads": 4, "error": "connections 1004", "per_loader": None},
    # 1000 % 16 = 8.
    {"name": "1000 is not a multiple of 2 loaders x 8 threads", "connections": 1000, "threads": 8, "error": "connections 1000", "per_loader": None},
    {"name": "4 is under one connection per thread", "connections": 4, "threads": 4, "error": "connections 4", "per_loader": None},
]

PR = {"number": 59, "url": "https://github.com/rapira-rs/rapira/pull/59", "title": "Faster hello"}
NEW_META = {key: value for key, value in NEW.items() if key != "pr"}

BENCH_COMMAND_CASES = [
    {
        "name": "a meta.json without a base record refuses to start",
        "meta": {"new": NEW_META, "kernel": "6.17.1"},
        "status": 1,
        "stderr": "ERROR: the server has no base build; provision it with BASE=<sha7>\n",
        "builds": None,
    },
    {
        "name": "the new record gets the pull request and the base record stays as it is",
        "meta": {"new": NEW_META, "base": BASE, "kernel": "6.17.1"},
        "status": 0,
        "stderr": "",
        "builds": {"new": {**NEW_META, "pr": PR}, "base": BASE},
    },
]


class LoadCmdTest(unittest.TestCase):
    def test_load_cmd(self):
        for case in LOAD_CMD_CASES:
            with self.subTest(name=case["name"]):
                cmd = load_cmd(case["target"], f"http://10.0.0.1:8080{case['target'].url}", 100.0, case["rate"], case["plan"], suite([WORKER, GRPC]), case["stage"])
                self.assertEqual(shlex.split(cmd), case["expected"])

    def test_counted_s(self):
        for case in COUNTED_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(counted_s(case["target"], case["stage"]), case["expected"])

    def test_ttl_needed_s(self):
        for case in TTL_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(ttl_needed_s(case["suite"], plan_cells(case["suite"])), case["expected"])


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
                        plan_run(RIG, s, server_vcpus=SERVER_VCPUS, loader_threads=case["threads"])
                    continue
                plan = plan_run(RIG, s, server_vcpus=SERVER_VCPUS, loader_threads=case["threads"])
                self.assertEqual(plan["conns_per_loader"], case["per_loader"])
                self.assertEqual(plan["processes"], {"rate": 8, "cap": 2})
                self.assertEqual(plan["keys"], [
                    "r1-base-rate-hello-rapira-worker", "r1-new-rate-hello-rapira-worker",
                    "r1-base-cap-hello-rapira-worker", "r1-new-cap-hello-rapira-worker",
                    "r1-base-rate-grpc-rapira", "r1-new-rate-grpc-rapira",
                    "r1-base-cap-grpc-rapira", "r1-new-cap-grpc-rapira",
                ])

    def test_refused_suite_creates_no_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({}))
            with self.assertRaisesRegex(SuiteError, "connections 1000"):
                bench(boxes, Path(tmp), [WORKER], threads=8)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertEqual(boxes.calls, [])


class BenchCommandTest(unittest.TestCase):
    def test_bench_command(self):
        for case in BENCH_COMMAND_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                boxes = FakeBoxes({
                    ("server", "nproc"): "8\n",
                    ("loader-1", "nproc"): "4\n",
                    ("server", "cat /opt/bench/meta.json"): json.dumps(case["meta"]),
                })
                calls = []

                def fake_run_suite(rig, suite, boxes, out_dir, **kwargs):
                    calls.append(kwargs)
                    path = out_dir / kwargs["run_id"] / "run.json"
                    path.parent.mkdir(parents=True)
                    path.write_text(json.dumps({"cells": [], "status": "complete", "reasons": [], "summary": {}}))
                    return path

                stderr = io.StringIO()
                with mock.patch("rig.__main__.from_terraform", return_value=RIG), \
                        mock.patch("rig.__main__.ssh.wait_ssh"), mock.patch("rig.__main__.ssh.stage_tree"), \
                        mock.patch("rig.__main__.SshBoxes", return_value=boxes), \
                        mock.patch("rig.__main__.server_versions", return_value={}), \
                        mock.patch("rig.__main__.app_hashes", return_value={}), \
                        mock.patch("rig.__main__.run_suite", side_effect=fake_run_suite), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
                    status = main([
                        "bench", "--suite", "ci", "--out", tmp,
                        "--pr-number", "59", "--pr-url", PR["url"], "--pr-title", PR["title"],
                    ])
                self.assertEqual((status, stderr.getvalue()), (case["status"], case["stderr"]))
                if case["builds"] is None:
                    self.assertEqual(calls, [])
                    continue
                self.assertEqual(calls[0]["builds"], case["builds"])
                self.assertEqual((calls[0]["server_vcpus"], calls[0]["loader_threads"]), (8, 4))
                # The run id ends with the suite name and the sha7 of the new build.
                self.assertTrue(calls[0]["run_id"].endswith("-ci-abc1234"), calls[0]["run_id"])
