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


PLAN = ["r1-hello-rapira-worker", "r1-hello-php-fpm"]

STATUS_CASES = [
    {
        "name": "every planned cell ok",
        "cells": [cell("r1-hello-rapira-worker"), cell("r1-hello-php-fpm")],
        "status": "complete",
        "reasons": [],
    },
    {
        "name": "planned cell missing",
        "cells": [cell("r1-hello-rapira-worker")],
        "status": "incomplete",
        "reasons": ["r1-hello-php-fpm: missing"],
    },
    {
        "name": "void cell",
        "cells": [cell("r1-hello-rapira-worker"), cell("r1-hello-php-fpm", "void", "probe mismatch on loader-2")],
        "status": "incomplete",
        "reasons": ["r1-hello-php-fpm: void: probe mismatch on loader-2"],
    },
    {
        "name": "incomplete cell",
        "cells": [cell("r1-hello-rapira-worker", "incomplete"), cell("r1-hello-php-fpm")],
        "status": "incomplete",
        "reasons": ["r1-hello-rapira-worker: incomplete"],
    },
    {
        "name": "unplanned cell",
        "cells": [cell("r1-hello-rapira-worker"), cell("r1-hello-php-fpm"), cell("r2-hello-php-fpm")],
        "status": "incomplete",
        "reasons": ["r2-hello-php-fpm: unplanned cell"],
    },
    {
        "name": "reasons follow the plan order, unplanned cells last",
        "cells": [cell("r9-static-rapira-hit"), cell("r1-hello-php-fpm", "void", "no RESULT line from loader-3")],
        "status": "incomplete",
        "reasons": [
            "r1-hello-rapira-worker: missing",
            "r1-hello-php-fpm: void: no RESULT line from loader-3",
            "r9-static-rapira-hit: unplanned cell",
        ],
    },
]

# The top-level keys in the order of spec section 6.1, with `reasons` after `status`.
TOP_KEYS = [
    "schema", "id", "suite", "smoke", "started", "finished", "rig", "rapira", "servers", "apps",
    "loaders", "ladder", "processes", "plan", "cells", "status", "reasons", "reporter",
]

STAGE = {
    "rate": 10000,
    "duration_s": 20,
    "pass": True,
    "merged": {
        "requests": 199990,
        "successful": 199990,
        "bytes": 25198740,
        "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
        "achieved_rps": 9999.5,
        "successful_rps": 9999.5,
    },
    "latency_us": {"mean": 689.5, "p50": 689, "p90": 1111, "p95": 1175, "p99": 1264, "p999": 1351, "max": 2822},
    "loaders": [],
    "server": {"busy_cpu": 12, "pss_kb": 912384, "established": 256, "time_wait": 0},
}


def new_run_file():
    return RunFile(
        run_id="20260925T120000Z-ci-0a1b2c3",
        suite={"name": "ci", "sha256": "ab" * 32},
        rig={"server_type": "c7a.8xlarge", "loader_type": "c7a.xlarge", "loader_count": 4, "az": "eu-central-1a"},
        rapira={"ref": "main", "sha": "0a1b2c3d", "version": "0.9.0", "build": "nightly"},
        servers={"frankenphp": "FrankenPHP v1.12.7"},
        apps={"apps/hello/worker.php": "cd" * 32},
        loaders=[{"name": "loader-1", "private_ip": "10.0.1.11", "wrk2": "44a94c1", "k6": "2.2.0"}],
        ladder={"stage_s": 20, "ratio": 2, "pass_tolerance": 0.95, "rates": {"hello": [10000, 20000]}},
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
        run.add_cell({"key": "r1-hello-rapira-worker", "status": "ok", "stages": [STAGE]})
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
        # indent=1 puts one space before each top-level key.
        self.assertEqual(text.splitlines()[1], ' "schema": "rapira-bench-run/1",')
        self.assertTrue(text.endswith("}\n"))
        merged = loaded["cells"][0]["stages"][0]["merged"]
        self.assertIsInstance(merged["requests"], int)
        self.assertIsInstance(merged["achieved_rps"], float)
        self.assertIsInstance(loaded["processes"], int)

    def test_finish_with_a_void_cell_is_incomplete(self):
        run = new_run_file()
        run.add_cell({"key": "r1-hello-rapira-worker", "status": "void", "reason": "loader loader-2 throttled: pps_allowance_exceeded=5"})
        doc = run.finish("2026-09-25T12:40:00Z")
        self.assertEqual(doc["finished"], "2026-09-25T12:40:00Z")
        self.assertEqual(doc["status"], "incomplete")
        self.assertEqual(doc["reasons"], ["r1-hello-rapira-worker: void: loader loader-2 throttled: pps_allowance_exceeded=5"])


if __name__ == "__main__":
    unittest.main()
