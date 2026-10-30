import shlex
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rig.pgo import run_pgo
from rig.ssh import SshError
from tests.test_bench import BOX, GRPC, LOAD, RIG, SERVER_VCPUS, STOP, THREADS, WORKER, FakeBoxes, replies, suite

BUILD = BOX + "pgo.sh build"
TRAIN = BOX + "pgo.sh start"
MERGE = BOX + "pgo.sh merge"
PGO_REPLIES = {("server", BUILD): "", ("server", TRAIN): "pid=100\n", ("server", MERGE): ""}

# The rate stage of the test suite: worker 250000 req/s and gRPC 100000 req/s over 2 loaders, 10 s warm-up, 60 s window.
# Each target runs one process per server vCPU, and the merge gets the target names.
TRAINED = [
    ("start", ["pgo-hello-rapira-worker", "rapira", str(SERVER_VCPUS), "worker", "@RIG@/apps/hello/worker.php"]),
    ("load", ["125000", "10", "60"]),
    ("load", ["125000", "10", "60"]),
    ("stop", ["pgo-hello-rapira-worker"]),
    ("start", ["pgo-grpc-rapira", "rapira", str(SERVER_VCPUS), "grpc", "@RIG@/apps/grpc/php/dispatcher.php"]),
    ("load", ["50000", "10", "60"]),
    ("load", ["50000", "10", "60"]),
    ("stop", ["pgo-grpc-rapira"]),
]

PGO_CASES = [
    {
        "name": "every suite target trains the instrumented build",
        "overrides": {},
        "error": None,
        "calls": [("build", [])] + TRAINED + [("merge", ["hello-rapira-worker", "grpc-rapira"])],
        "copies": ["/opt/bench/pgo/merged.profdata", "/opt/bench/pgo/pgo.json"],
    },
    {
        "name": "a failed load stops its target and copies no profile",
        "overrides": {("loader-2", LOAD): SshError("loader-2: exit 1: bash bench-rig/box/load.sh\nERROR: wrk2 exited 1")},
        "error": "wrk2 exited 1",
        "calls": [("build", [])] + TRAINED[:4],
        "copies": [],
    },
    {
        "name": "a profile without worker counts copies no profile",
        "overrides": {("server", MERGE): SshError("server: exit 1: bash bench-rig/box/pgo.sh merge\nERROR: the profile has no counts in check_request")},
        "error": "no counts in check_request",
        "calls": [("build", [])] + TRAINED + [("merge", ["hello-rapira-worker", "grpc-rapira"])],
        "copies": [],
    },
]


def call(cmd):
    """The kind and the checked arguments of one pgo call."""
    args = shlex.split(cmd)
    if cmd.startswith(LOAD):
        # load.sh TOOL EPOCH RATE ...: wrk2 has warm-up and window at 6 and 7, h2load at 7 and 8.
        window = args[7:9] if args[2] == "wrk2" else args[8:10]
        return "load", [args[4], *window]
    for prefix, kind in ((BUILD, "build"), (TRAIN, "start"), (MERGE, "merge"), (STOP, "stop")):
        if cmd.startswith(prefix):
            skip = len(shlex.split(prefix))
            return kind, args[skip:skip + 1] if kind == "stop" else args[skip:]
    raise AssertionError(f"unexpected call: {cmd}")


class RunPgoTest(unittest.TestCase):
    def test_run_pgo(self):
        for case in PGO_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                boxes = FakeBoxes(replies({**PGO_REPLIES, **case["overrides"]}))
                out = Path(tmp)
                with mock.patch("rig.pgo.ensure_ttl"):
                    if case["error"]:
                        with self.assertRaisesRegex(SshError, case["error"]):
                            run_pgo(RIG, suite([WORKER, GRPC]), boxes, out, server_vcpus=SERVER_VCPUS, loader_threads=THREADS)
                    else:
                        run_pgo(RIG, suite([WORKER, GRPC]), boxes, out, server_vcpus=SERVER_VCPUS, loader_threads=THREADS)
                self.assertEqual([call(cmd) for _, cmd in boxes.calls], case["calls"])
                self.assertEqual([remote for _, remote in boxes.copies], case["copies"])

    def test_ttl_covers_the_build_and_every_target(self):
        # The build budget of 900 s, then 10 s warm-up + 60 s window + 20 s overhead per target, then the 300 s margin.
        boxes = FakeBoxes(replies(PGO_REPLIES))
        with tempfile.TemporaryDirectory() as tmp, mock.patch("rig.pgo.ensure_ttl") as ttl:
            run_pgo(RIG, suite([WORKER, GRPC]), boxes, Path(tmp), server_vcpus=SERVER_VCPUS, loader_threads=THREADS)
        ttl.assert_called_once_with(RIG.hosts, 900 + 2 * (10 + 60 + 20) + 300)
