"""Tests of the report table on synthetic run files."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from rig.__main__ import main
from rig.report import render

VOIDED_TITLE = "VOIDED cells (excluded from every number above):"
FOOTER = "Do not publish these tables."


def ok_cell(name, round_no, *, achieved, held, p50, p99, rss_kb, flags=None):
    return {
        "key": f"r{round_no}-{name}",
        "target": {"name": name},
        "round": round_no,
        "status": "ok",
        "flags": flags or {},
        "rate": 250000,
        "achieved_rps": achieved,
        "successful_rps": achieved,
        "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
        "latency_us": {"p50": p50, "p90": p99, "p99": p99, "p999": p99, "max": p99},
        "rss_kb": rss_kb,
        "held": held,
        "cpu": {"server_busy": 60, "loader_busy": 70},
        "loaders": [],
    }


def void_cell(name, round_no, reason):
    return {
        "key": f"r{round_no}-{name}",
        "target": {"name": name},
        "round": round_no,
        "status": "void",
        "reason": reason,
        "flags": {},
        "rate": None,
        "achieved_rps": None,
        "successful_rps": None,
        "errors": None,
        "latency_us": None,
        "rss_kb": None,
        "held": None,
        "cpu": None,
        "loaders": [],
    }


def run_doc(cells, status="complete", reasons=()):
    return {"id": "20260925T120000Z-ci-0a1b2c3", "cells": cells, "status": status, "reasons": list(reasons)}


HELLO = ok_cell("hello-rapira-worker", 1, achieved=249996.6, held=True, p50=689, p99=1264, rss_kb=204800)
# 249996.6 prints as 249997; 1264 us is 1.26 ms, 689 us is 0.69 ms; 204800 KiB is 200.0 MiB.
HELLO_ROW = ["hello-rapira-worker", "249997", "yes", "1.26ms", "0.69ms", "200.0", "1", "-"]

CASES = [
    {
        "name": "single round",
        "run": run_doc([HELLO]),
        "rows": [HELLO_ROW],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "rows keep the cell order",
        "run": run_doc([
            ok_cell("grpc-rapira", 1, achieved=99990.0, held=True, p50=400, p99=2500, rss_kb=1048576),
            HELLO,
        ]),
        "rows": [["grpc-rapira", "99990", "yes", "2.50ms", "0.40ms", "1024.0", "1", "-"], HELLO_ROW],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "a row that did not hold",
        "run": run_doc([ok_cell("hello-rapira-classic", 1, achieved=228571.4, held=False, p50=9000, p99=250000, rss_kb=307200, flags={"generator_bound": True})]),
        "rows": [["hello-rapira-classic", "228571", "no", "250.00ms", "9.00ms", "300.0", "1", "generator_bound"]],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "three rounds with a void",
        "run": run_doc(
            [
                ok_cell("yii3-rapira-dispatcher", 1, achieved=250000.0, held=True, p50=900, p99=5000, rss_kb=204800, flags={"worker_churn": True}),
                void_cell("yii3-rapira-dispatcher", 2, "probe mismatch on loader-1"),
                ok_cell("yii3-rapira-dispatcher", 3, achieved=240000.0, held=False, p50=1100, p99=7000, rss_kb=210944),
            ],
            "incomplete",
            ["r2-yii3-rapira-dispatcher: void: probe mismatch on loader-1"],
        ),
        # Two rounds survive: req/s median 245000, held no because round 3 did not hold, p99 median 6000 us,
        # p50 median 1000 us, RSS median (204800 + 210944) / 2 = 207872 KiB = 203.0 MiB.
        "rows": [["yii3-rapira-dispatcher", "245000", "no", "6.00ms", "1.00ms", "203.0", "2", "worker_churn"]],
        "voided": ["r2-yii3-rapira-dispatcher: probe mismatch on loader-1"],
        "footer": ["INCOMPLETE RUN: r2-yii3-rapira-dispatcher: void: probe mismatch on loader-1.", FOOTER],
        "status": 1,
    },
    {
        "name": "value flags",
        "run": run_doc([ok_cell(
            "hello-rapira-worker", 1, achieved=249996.6, held=True, p50=689, p99=1264, rss_kb=204800,
            flags={"log_growth": 70000, "ena_throttled": {"pps_allowance_exceeded": 946}},
        )]),
        "rows": [HELLO_ROW[:7] + ["ena_throttled(pps_allowance_exceeded=946),log_growth(70000)"]],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "incomplete run with a missing cell",
        "run": run_doc([HELLO], "incomplete", ["r1-grpc-rapira: missing"]),
        "rows": [HELLO_ROW],
        "voided": [],
        "footer": ["INCOMPLETE RUN: r1-grpc-rapira: missing.", FOOTER],
        "status": 1,
    },
]


def parse(text):
    """The table rows split into 8 fields, the voided lines, and the footer lines."""
    lines = text.splitlines()
    end = lines.index("") if "" in lines else len(lines)
    table = [line.split(None, 7) for line in lines[2:end]]
    voided = []
    if VOIDED_TITLE in lines:
        start = lines.index(VOIDED_TITLE) + 1
        for line in lines[start:]:
            if not line:
                break
            voided.append(line.strip())
    footer = lines[-2:] if lines[-1] == FOOTER else []
    return table, voided, footer


class TestReport(unittest.TestCase):
    def test_render(self):
        for case in CASES:
            with self.subTest(name=case["name"]):
                text, status = render(case["run"])
                table, voided, footer = parse(text)
                self.assertEqual(table, case["rows"])
                self.assertEqual(voided, case["voided"])
                self.assertEqual(footer, case["footer"])
                self.assertEqual(status, case["status"])

    def test_cli_exit_status_of_an_incomplete_run(self):
        run = run_doc([HELLO], "incomplete", ["r1-grpc-rapira: missing"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            path.write_text(json.dumps(run))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                status = main(["report", str(path)])
        self.assertEqual(status, 1)
        self.assertTrue(out.getvalue().endswith(FOOTER + "\n"))


if __name__ == "__main__":
    unittest.main()
