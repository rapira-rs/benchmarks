"""Tests of the report table on synthetic run files."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from rig.__main__ import main
from rig.report import render

VOIDED_TITLE = "VOIDED cells (their pairs do not count):"
FOOTER = "Do not publish these tables."
HELLO = "hello-rapira-worker"
YII3 = "yii3-rapira-dispatcher"


def entry(pairs, delta_pct, min_pct, max_pct, base, new, flags=()):
    return {"pairs": pairs, "delta_pct": delta_pct, "min_pct": min_pct, "max_pct": max_pct, "base": base, "new": new, "flags": list(flags)}


NULL = entry(0, None, None, None, None, None)


def void_cell(key, reason):
    return {"key": key, "status": "void", "reason": reason}


def run_doc(summary, cells=(), status="complete", reasons=()):
    return {"id": "20260927T120000Z-ci-0a1b2c3", "cells": list(cells), "summary": summary, "status": status, "reasons": list(reasons)}


HELLO_SUMMARY = {
    "capacity": entry(3, 2.0, -1.0, 3.0, 100000.0, 103000.0, ["loader_busy", "not_saturated"]),
    "p99": entry(3, 10.0, -5.0, 10.0, 2000, 1900, ["server_unsaturated"]),
    "rss": entry(3, 1.0, 0.0, 2.0, 102400, 104448),
}
# capacity: 100000.0 and 103000.0 req/s print as 100000 and 103000.
# p99: 2000 us is 2.00 ms, 1900 us is 1.90 ms. rss: 102400 KiB is 100.0 MiB, 104448 KiB is 102.0 MiB.
HELLO_ROWS = [
    [HELLO, "capacity", "3", "+2.0%", "-1.0%", "+3.0%", "100000", "103000", "req/s", "loader_busy,not_saturated"],
    [HELLO, "p99", "3", "+10.0%", "-5.0%", "+10.0%", "2.00", "1.90", "ms", "server_unsaturated"],
    [HELLO, "rss", "3", "+1.0%", "+0.0%", "+2.0%", "100.0", "102.0", "MiB", "-"],
]
NULL_ROWS = [
    [YII3, "capacity", "0", "-", "-", "-", "-", "-", "req/s", "-"],
    [YII3, "p99", "0", "-", "-", "-", "-", "-", "ms", "-"],
    [YII3, "rss", "0", "-", "-", "-", "-", "-", "MiB", "-"],
]
BASE_VOID = [
    void_cell("r1-base-rate-yii3-rapira-dispatcher", "server log: 3 warn or error lines"),
    void_cell("r1-base-cap-yii3-rapira-dispatcher", "server log: 3 warn or error lines"),
]

# width is the longest target name plus 2: hello-rapira-worker has 19 characters, yii3-rapira-dispatcher 22.
CASES = [
    {
        "name": "a complete run with three pairs",
        "width": 21,
        "run": run_doc({HELLO: HELLO_SUMMARY}),
        "rows": HELLO_ROWS,
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        # The rows keep the target order of the summary. A measure with 0 pairs prints dashes.
        # A complete run with void cells lists them and exits 0.
        "name": "a complete run with every base cell of a target void",
        "width": 24,
        "run": run_doc({YII3: {"capacity": NULL, "p99": NULL, "rss": NULL}, HELLO: HELLO_SUMMARY}, BASE_VOID),
        "rows": NULL_ROWS + HELLO_ROWS,
        "voided": [
            "r1-base-rate-yii3-rapira-dispatcher: server log: 3 warn or error lines",
            "r1-base-cap-yii3-rapira-dispatcher: server log: 3 warn or error lines",
        ],
        "footer": [],
        "status": 0,
    },
    {
        # 1 pair: the band is the delta. 250000 us is 250.00 ms. -12.5 prints as -12.5%.
        "name": "one pair and a large p99",
        "width": 21,
        "run": run_doc({HELLO: {
            "capacity": entry(1, -12.5, -12.5, -12.5, 80000.0, 70000.0),
            "p99": entry(1, 25.0, 25.0, 25.0, 200000, 250000),
            "rss": entry(1, 0.0, 0.0, 0.0, 102400, 102400),
        }}),
        "rows": [
            [HELLO, "capacity", "1", "-12.5%", "-12.5%", "-12.5%", "80000", "70000", "req/s", "-"],
            [HELLO, "p99", "1", "+25.0%", "+25.0%", "+25.0%", "200.00", "250.00", "ms", "-"],
            [HELLO, "rss", "1", "+0.0%", "+0.0%", "+0.0%", "100.0", "100.0", "MiB", "-"],
        ],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "an incomplete run",
        "width": 21,
        "run": run_doc({HELLO: HELLO_SUMMARY}, status="incomplete", reasons=["r1-new-cap-grpc-rapira: missing"]),
        "rows": HELLO_ROWS,
        "voided": [],
        "footer": ["INCOMPLETE RUN: r1-new-cap-grpc-rapira: missing.", FOOTER],
        "status": 1,
    },
    {
        "name": "a broken run",
        "width": 24,
        "run": run_doc(
            {YII3: {"capacity": NULL, "p99": NULL, "rss": NULL}},
            [void_cell("r1-new-rate-yii3-rapira-dispatcher", "stop failed: timeout"), void_cell("r1-new-cap-yii3-rapira-dispatcher", "stop failed: timeout")],
            "broken",
            ["yii3-rapira-dispatcher: every new cell is void"],
        ),
        "rows": NULL_ROWS,
        "voided": ["r1-new-rate-yii3-rapira-dispatcher: stop failed: timeout", "r1-new-cap-yii3-rapira-dispatcher: stop failed: timeout"],
        "footer": ["BROKEN RUN: yii3-rapira-dispatcher: every new cell is void.", FOOTER],
        "status": 1,
    },
]


def parse(text):
    """The table rows split into 10 fields, the voided lines, and the footer lines."""
    lines = text.splitlines()
    end = lines.index("") if "" in lines else len(lines)
    table = [line.split(None, 9) for line in lines[2:end]]
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
                self.assertEqual(
                    text.splitlines()[0],
                    f"{'target':<{case['width']}} {'measure':<8} {'pairs':>5} {'delta':>7} {'min':>7} {'max':>7} "
                    f"{'base':>9} {'new':>9} {'unit':<5}  flags",
                )
                table, voided, footer = parse(text)
                self.assertEqual(table, case["rows"])
                self.assertEqual(voided, case["voided"])
                self.assertEqual(footer, case["footer"])
                self.assertEqual(status, case["status"])

    def test_cli_exit_status_of_a_broken_run(self):
        run = run_doc({HELLO: HELLO_SUMMARY}, status="broken", reasons=["hello-rapira-worker: every new cell is void"])
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
