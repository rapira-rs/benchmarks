"""Tests of the run file writer and the run status rules."""

import json
import tempfile
import unittest
from pathlib import Path

from rig.runfile import SCHEMA, RunFile, run_status

HELLO = "hello-rapira-worker"
GRPC = "grpc-rapira"


def cell(round_no, build, stage, target, status="ok", reason=None):
    """A cell with the fields that the status rules read."""
    out = {"key": f"r{round_no}-{build}-{stage}-{target}", "target": {"name": target}, "build": build, "status": status}
    if reason is not None:
        out["reason"] = reason
    return out


def all_ok(target):
    """The 4 ok cells of one target in round 1."""
    return [cell(1, "base", "rate", target), cell(1, "new", "rate", target), cell(1, "base", "cap", target), cell(1, "new", "cap", target)]


PLAN = [c["key"] for c in all_ok(HELLO) + all_ok(GRPC)]

STATUS_CASES = [
    {
        "name": "every planned cell ok",
        "cells": all_ok(HELLO) + all_ok(GRPC),
        "status": "complete",
        "reasons": [],
    },
    {
        # A void cell removes only its pair. One new cell of grpc-rapira stays ok, so the run is complete.
        "name": "a run with void cells is complete",
        "cells": [
            cell(1, "base", "rate", HELLO), cell(1, "new", "rate", HELLO),
            cell(1, "base", "cap", HELLO, "void", "probe mismatch on loader-1"), cell(1, "new", "cap", HELLO),
            cell(1, "base", "rate", GRPC), cell(1, "new", "rate", GRPC, "void", "server log: 2 warn or error lines"),
            cell(1, "base", "cap", GRPC), cell(1, "new", "cap", GRPC),
        ],
        "status": "complete",
        "reasons": [],
    },
    {
        # A base build that cannot start voids every base cell. The run publishes with gaps.
        "name": "every base cell void is complete",
        "cells": [
            cell(1, "base", "rate", HELLO, "void", "server log: 1 warn or error lines"), cell(1, "new", "rate", HELLO),
            cell(1, "base", "cap", HELLO, "void", "server log: 1 warn or error lines"), cell(1, "new", "cap", HELLO),
            cell(1, "base", "rate", GRPC, "void", "server log: 1 warn or error lines"), cell(1, "new", "rate", GRPC),
            cell(1, "base", "cap", GRPC, "void", "server log: 1 warn or error lines"), cell(1, "new", "cap", GRPC),
        ],
        "status": "complete",
        "reasons": [],
    },
    {
        "name": "every new cell of a target void is broken",
        "cells": all_ok(HELLO) + [
            cell(1, "base", "rate", GRPC), cell(1, "new", "rate", GRPC, "void", "server log: 2 warn or error lines"),
            cell(1, "base", "cap", GRPC), cell(1, "new", "cap", GRPC, "void", "server log: 2 warn or error lines"),
        ],
        "status": "broken",
        "reasons": ["grpc-rapira: every new cell is void"],
    },
    {
        # The broken reasons follow the order of the first cell of each target.
        "name": "two broken targets",
        "cells": [
            cell(1, "base", "rate", HELLO), cell(1, "new", "rate", HELLO, "void", "stop failed: timeout"),
            cell(1, "base", "cap", HELLO), cell(1, "new", "cap", HELLO, "void", "stop failed: timeout"),
            cell(1, "base", "rate", GRPC), cell(1, "new", "rate", GRPC, "void", "stop failed: timeout"),
            cell(1, "base", "cap", GRPC), cell(1, "new", "cap", GRPC, "void", "stop failed: timeout"),
        ],
        "status": "broken",
        "reasons": ["hello-rapira-worker: every new cell is void", "grpc-rapira: every new cell is void"],
    },
    {
        "name": "planned cell missing",
        "cells": all_ok(HELLO) + all_ok(GRPC)[:3],
        "status": "incomplete",
        "reasons": ["r1-new-cap-grpc-rapira: missing"],
    },
    {
        "name": "incomplete cell",
        "cells": [cell(1, "base", "rate", HELLO, "incomplete", "interrupted")] + all_ok(HELLO)[1:] + all_ok(GRPC),
        "status": "incomplete",
        "reasons": ["r1-base-rate-hello-rapira-worker: incomplete"],
    },
    {
        "name": "unplanned cell",
        "cells": all_ok(HELLO) + all_ok(GRPC) + [cell(2, "base", "rate", GRPC)],
        "status": "incomplete",
        "reasons": ["r2-base-rate-grpc-rapira: unplanned cell"],
    },
    {
        # A missing cell makes the run incomplete, also when every new cell of a target is void.
        "name": "a missing cell wins over a broken target",
        "cells": all_ok(HELLO)[:3] + [
            cell(1, "base", "rate", GRPC), cell(1, "new", "rate", GRPC, "void", "server log: 2 warn or error lines"),
            cell(1, "base", "cap", GRPC), cell(1, "new", "cap", GRPC, "void", "server log: 2 warn or error lines"),
        ],
        "status": "incomplete",
        "reasons": ["r1-new-cap-hello-rapira-worker: missing"],
    },
]

# The top-level keys in the order of the Interface Contract.
TOP_KEYS = [
    "schema", "id", "suite", "smoke", "started", "finished", "rig", "rapira", "base", "servers", "apps",
    "loaders", "plan", "cells", "status", "reasons", "summary", "reporter",
]


def run_cell(build, stage, *, achieved, p99, rss_kb, flags=None):
    """An ok round 1 cell of hello-rapira-worker with numbers."""
    return {
        "key": f"r1-{build}-{stage}-{HELLO}",
        "target": {"name": HELLO, "app": "hello", "mode": "worker", "proto": "http1"},
        "round": 1,
        "build": build,
        "stage": stage,
        "processes": 8 if stage == "rate" else 2,
        "status": "ok",
        "flags": flags or {},
        "rate": 60000 if stage == "rate" else 300000,
        "achieved_rps": achieved,
        "successful_rps": achieved,
        "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
        "latency_us": {"p50": p99 // 2, "p90": p99, "p99": p99, "p999": p99, "max": p99},
        "rss_kb": rss_kb,
        "held": stage == "rate",
        "cpu": {"server_busy": 50, "loader_busy": 40},
        "loaders": [],
    }


CELLS = [
    run_cell("base", "rate", achieved=59998.5, p99=1000, rss_kb=204800),
    run_cell("new", "rate", achieved=59999.0, p99=900, rss_kb=215040),
    run_cell("base", "cap", achieved=100000.0, p99=1800000, rss_kb=102400),
    run_cell("new", "cap", achieved=110000.0, p99=1700000, rss_kb=102400, flags={"loader_busy": 72}),
]

# capacity: 100 * 10000 / 100000 = 10.0. p99: 100 * -100 / 1000 = -10.0. rss: 100 * 10240 / 204800 = 5.0.
# One pair per measure, so the band is the delta. The flags are names, without the values.
SUMMARY = {HELLO: {
    "capacity": {"pairs": 1, "delta_pct": 10.0, "min_pct": 10.0, "max_pct": 10.0, "base": 100000.0, "new": 110000.0, "flags": ["loader_busy"]},
    "p99": {"pairs": 1, "delta_pct": -10.0, "min_pct": -10.0, "max_pct": -10.0, "base": 1000, "new": 900, "flags": []},
    "rss": {"pairs": 1, "delta_pct": 5.0, "min_pct": 5.0, "max_pct": 5.0, "base": 204800, "new": 215040, "flags": []},
}}

PR = {"number": 59, "url": "https://github.com/rapira-rs/rapira/pull/59", "title": "Faster hello"}


def new_run_file():
    return RunFile(
        run_id="20260927T120000Z-ci-0a1b2c3",
        suite={
            "name": "ci", "file_sha256": "ab" * 32, "rounds": 1, "connections": {"http1": 1000, "grpc": 100},
            "stages": {
                "rate": {"warmup_s": 11, "duration_s": 15, "processes": None, "rates": {HELLO: 60000}},
                "cap": {"warmup_s": 5, "duration_s": 15, "processes": 2, "rates": {HELLO: 300000}},
            },
        },
        rig={"server_type": "c7a.2xlarge", "loader_type": "c7a.xlarge", "loader_count": 2, "az": "eu-central-1a"},
        rapira={"ref": "nightly", "sha": "0a1b2c3d", "version": "0.9.0", "build": "nightly", "pr": PR},
        base={"ref": "cache", "sha": "ac56141e", "version": "0.8.1", "build": "cache"},
        servers={"php": "PHP 8.5.10 (cli)"},
        apps={"apps/hello/worker.php": "cd" * 32},
        loaders=[{"name": "loader-1", "private_ip": "10.0.1.11", "wrk2": "44a94c1", "h2load": "h2load nghttp2/1.68.0"}],
        plan=[c["key"] for c in CELLS],
        smoke=False,
        started="2026-09-27T12:00:00Z",
    )


class TestRunStatus(unittest.TestCase):
    def test_run_status(self):
        for case in STATUS_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(run_status(PLAN, case["cells"]), (case["status"], case["reasons"]))


class TestRunFile(unittest.TestCase):
    def test_written_document_round_trips_with_the_summary(self):
        run = new_run_file()
        for c in CELLS:
            run.add_cell(c)
        doc = run.finish("2026-09-27T12:40:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            run.write(path)
            text = path.read_text()
        loaded = json.loads(text)
        self.assertEqual(loaded, doc)
        self.assertEqual(list(loaded), TOP_KEYS)
        self.assertEqual(loaded["schema"], SCHEMA)
        self.assertEqual(loaded["finished"], "2026-09-27T12:40:00Z")
        self.assertEqual((loaded["status"], loaded["reasons"]), ("complete", []))
        self.assertEqual(loaded["summary"], SUMMARY)
        self.assertEqual(loaded["rapira"]["pr"], PR)
        self.assertEqual(loaded["base"]["build"], "cache")
        # indent=1 puts one space before each top-level key.
        self.assertEqual(text.splitlines()[1], ' "schema": "rapira-bench-run/3",')
        self.assertTrue(text.endswith("}\n"))


if __name__ == "__main__":
    unittest.main()
