"""Tests of the run file writer and the run status rules."""

import json
import tempfile
import unittest
from pathlib import Path

from rig.runfile import SCHEMA, RunFile, run_status


def cell(key, status="ok", reason=None):
    """A cell with the fields that the status rules read."""
    out = {"key": key, "status": status}
    if reason is not None:
        out["reason"] = reason
    return out


PLAN = ["r1-hello-rapira-worker", "r1-grpc-rapira"]

STATUS_CASES = [
    {
        "name": "every planned cell ok",
        "cells": [cell("r1-hello-rapira-worker"), cell("r1-grpc-rapira")],
        "status": "complete",
        "reasons": [],
    },
    {
        "name": "planned cell missing",
        "cells": [cell("r1-hello-rapira-worker")],
        "status": "incomplete",
        "reasons": ["r1-grpc-rapira: missing"],
    },
    {
        "name": "void cell",
        "cells": [cell("r1-hello-rapira-worker"), cell("r1-grpc-rapira", "void", "probe mismatch on loader-1")],
        "status": "incomplete",
        "reasons": ["r1-grpc-rapira: void: probe mismatch on loader-1"],
    },
    {
        "name": "incomplete cell",
        "cells": [cell("r1-hello-rapira-worker", "incomplete"), cell("r1-grpc-rapira")],
        "status": "incomplete",
        "reasons": ["r1-hello-rapira-worker: incomplete"],
    },
    {
        "name": "unplanned cell",
        "cells": [cell("r1-hello-rapira-worker"), cell("r1-grpc-rapira"), cell("r2-grpc-rapira")],
        "status": "incomplete",
        "reasons": ["r2-grpc-rapira: unplanned cell"],
    },
    {
        "name": "reasons follow the plan order, unplanned cells last",
        "cells": [cell("r9-yii3-rapira-dispatcher"), cell("r1-grpc-rapira", "void", "loader loader-1 returned no RESULT line")],
        "status": "incomplete",
        "reasons": [
            "r1-hello-rapira-worker: missing",
            "r1-grpc-rapira: void: loader loader-1 returned no RESULT line",
            "r9-yii3-rapira-dispatcher: unplanned cell",
        ],
    },
]

# The top-level keys in the order of spec section 6.1, with `reasons` after `status`.
TOP_KEYS = [
    "schema", "id", "suite", "smoke", "started", "finished", "rig", "rapira", "servers", "apps",
    "loaders", "processes", "plan", "cells", "status", "reasons", "reporter",
]

CELL = {
    "key": "r1-hello-rapira-worker",
    "target": {"name": "hello-rapira-worker", "app": "hello", "mode": "worker", "proto": "http1"},
    "round": 1,
    "status": "ok",
    "flags": {},
    "rate": 250000,
    "achieved_rps": 249996.6,
    "successful_rps": 249996.6,
    "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
    "latency_us": {"p50": 689, "p90": 1111, "p99": 1264, "p999": 1351, "max": 2822},
    "rss_kb": 912384,
    "held": True,
    "cpu": {"server_busy": 62, "loader_busy": 71},
    "loaders": [],
}

PR = {"number": 59, "url": "https://github.com/rapira-rs/rapira/pull/59", "title": "Faster hello"}


def new_run_file():
    return RunFile(
        run_id="20260925T120000Z-ci-0a1b2c3",
        suite={"name": "ci", "file_sha256": "ab" * 32, "rounds": 1, "warmup_s": 10, "duration_s": 60, "rates": {"hello": 250000}, "connections": {"http1": 5000, "grpc": 100}},
        rig={"server_type": "c7a.8xlarge", "loader_type": "c7a.2xlarge", "loader_count": 1, "az": "eu-central-1a"},
        rapira={"ref": "nightly", "sha": "0a1b2c3d", "version": "0.9.0", "build": "nightly", "pr": PR},
        servers={"php": "PHP 8.5.10 (cli)"},
        apps={"apps/hello/worker.php": "cd" * 32},
        loaders=[{"name": "loader-1", "private_ip": "10.0.1.11", "wrk2": "44a94c1", "h2load": "h2load nghttp2/1.68.0"}],
        processes=32,
        plan=["r1-hello-rapira-worker"],
        smoke=False,
        started="2026-09-25T12:00:00Z",
    )


class TestRunStatus(unittest.TestCase):
    def test_run_status(self):
        for case in STATUS_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(run_status(PLAN, case["cells"]), (case["status"], case["reasons"]))


class TestRunFile(unittest.TestCase):
    def test_written_document_round_trips_with_numbers(self):
        run = new_run_file()
        run.add_cell(CELL)
        doc = run.finish("2026-09-25T12:40:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            run.write(path)
            text = path.read_text()
        loaded = json.loads(text)
        self.assertEqual(loaded, doc)
        self.assertEqual(list(loaded), TOP_KEYS)
        self.assertEqual(loaded["schema"], SCHEMA)
        self.assertEqual((loaded["status"], loaded["reasons"]), ("complete", []))
        self.assertEqual(loaded["rapira"]["pr"], PR)
        # indent=1 puts one space before each top-level key.
        self.assertEqual(text.splitlines()[1], ' "schema": "rapira-bench-run/2",')
        self.assertTrue(text.endswith("}\n"))
        cell = loaded["cells"][0]
        self.assertIsInstance(cell["rate"], int)
        self.assertIsInstance(cell["achieved_rps"], float)
        self.assertIsInstance(cell["held"], bool)
        self.assertIsInstance(loaded["processes"], int)

    def test_finish_with_a_void_cell_is_incomplete(self):
        run = new_run_file()
        run.add_cell({"key": "r1-hello-rapira-worker", "status": "void", "reason": "loader loader-1 throttled: pps_allowance_exceeded=5"})
        doc = run.finish("2026-09-25T12:40:00Z")
        self.assertEqual(doc["finished"], "2026-09-25T12:40:00Z")
        self.assertEqual(doc["status"], "incomplete")
        self.assertEqual(doc["reasons"], ["r1-hello-rapira-worker: void: loader loader-1 throttled: pps_allowance_exceeded=5"])


if __name__ == "__main__":
    unittest.main()
