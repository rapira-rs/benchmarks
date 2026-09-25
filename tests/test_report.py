"""Tests of the report tables on synthetic run files."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from rig.__main__ import main
from rig.report import render, target_row

FLOOR = 10000
VOIDED_TITLE = "VOIDED cells (excluded from every number above):"
FOOTER = "Do not publish these tables."


def stage(rate, passed, p50, p99, fail_reason=None):
    out = {"rate": rate, "duration_s": 20, "pass": passed, "latency_us": {"p50": p50, "p99": p99}}
    if not passed:
        out["fail_reason"] = fail_reason
    return out


def ok_cell(name, app, round_no, *, held, held_p99, peak, unloaded_p50, fail_reason, flags=None):
    """An ok cell. With `held` None the first stage fails at the floor."""
    if held is None:
        stages = [stage(FLOOR, False, unloaded_p50, 900000, fail_reason)]
        held_field = None
    else:
        stages = [
            stage(FLOOR, True, unloaded_p50, 1500),
            stage(held, True, 900, held_p99),
            stage(held * 2, False, 5000, 900000, fail_reason),
        ]
        held_field = {"rate": held, "stage": 1}
    return {
        "key": f"r{round_no}-{name}",
        "target": {"name": name, "app": app},
        "round": round_no,
        "status": "ok",
        "flags": flags or {},
        "held": held_field,
        "peak": peak,
        "unloaded": {"p50": unloaded_p50, "p99": stages[0]["latency_us"]["p99"]},
        "stages": stages,
    }


def void_cell(name, app, round_no, reason):
    return {
        "key": f"r{round_no}-{name}",
        "target": {"name": name, "app": app},
        "round": round_no,
        "status": "void",
        "reason": reason,
        "flags": {},
        "held": None,
        "peak": None,
        "unloaded": None,
        "stages": [],
    }


def run_doc(cells, status="complete", reasons=()):
    return {"id": "20260925T120000Z-ci-0a1b2c3", "cells": cells, "status": status, "reasons": list(reasons)}


HELLO_RAPIRA = ok_cell(
    "hello-rapira-worker", "hello", 1,
    held=640000, held_p99=1264, peak=1180234.0, unloaded_p50=689,
    fail_reason="achieved 1180234 req/s under 95% of 1280000",
)
# 1264 us is 1.26 ms, 689 us is 0.69 ms. The spread needs more than one round, so one round prints "-".
HELLO_RAPIRA_ROW = [
    "hello-rapira-worker", "640000", "1180234", "1.26ms", "0.69ms", "1", "-", "-",
    "achieved 1180234 req/s under 95% of 1280000",
]

CASES = [
    {
        "name": "single round",
        "run": run_doc([HELLO_RAPIRA]),
        "rows": [HELLO_RAPIRA_ROW],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "rows sort by app then by peak from high to low",
        "run": run_doc([
            ok_cell("hello-php-fpm", "hello", 1, held=40000, held_p99=3000, peak=60000.0, unloaded_p50=800,
                    fail_reason="achieved 60000 req/s under 95% of 80000"),
            HELLO_RAPIRA,
            ok_cell("grpc-rapira", "grpc", 1, held=80000, held_p99=2500, peak=150000.0, unloaded_p50=400,
                    fail_reason="dropped iterations: 3100"),
        ]),
        "rows": [
            ["grpc-rapira", "80000", "150000", "2.50ms", "0.40ms", "1", "-", "-", "dropped iterations: 3100"],
            HELLO_RAPIRA_ROW,
            ["hello-php-fpm", "40000", "60000", "3.00ms", "0.80ms", "1", "-", "-", "achieved 60000 req/s under 95% of 80000"],
        ],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "three rounds with a void",
        "run": run_doc(
            [
                ok_cell("symfony-rapira-worker", "symfony", 1, held=40000, held_p99=5000, peak=52000.0, unloaded_p50=900,
                        fail_reason="achieved 52000 req/s under 95% of 80000", flags={"worker_churn": True}),
                void_cell("symfony-rapira-worker", "symfony", 2, "probe mismatch on loader-2"),
                ok_cell("symfony-rapira-worker", "symfony", 3, held=40000, held_p99=7000, peak=48000.0, unloaded_p50=1100,
                        fail_reason="achieved 48000 req/s under 95% of 80000"),
            ],
            "incomplete",
            ["r2-symfony-rapira-worker: void: probe mismatch on loader-2"],
        ),
        # Two rounds survive. Peak median (52000 + 48000) / 2 = 50000, spread 100 * 4000 / 50000 = 8.0%.
        # p99 at held median (5000 + 7000) / 2 = 6000 us, unloaded p50 median (900 + 1100) / 2 = 1000 us.
        "rows": [[
            "symfony-rapira-worker", "40000", "50000", "6.00ms", "1.00ms", "2", "8.0%", "worker_churn",
            "achieved 52000 req/s under 95% of 80000; achieved 48000 req/s under 95% of 80000",
        ]],
        "voided": ["r2-symfony-rapira-worker: probe mismatch on loader-2"],
        "footer": ["INCOMPLETE RUN: r2-symfony-rapira-worker: void: probe mismatch on loader-2.", FOOTER],
        "status": 1,
    },
    {
        "name": "generator_bound row with value flags",
        "run": run_doc([ok_cell(
            "hello-rapira-worker", "hello", 1,
            held=640000, held_p99=1264, peak=1180234.0, unloaded_p50=689,
            fail_reason="achieved 1180234 req/s under 95% of 1280000",
            flags={"generator_bound": True, "log_growth": 70000, "ena_throttled": {"pps_allowance_exceeded": 946}},
        )]),
        "rows": [HELLO_RAPIRA_ROW[:7] + [
            "ena_throttled(pps_allowance_exceeded=946),generator_bound,log_growth(70000)",
            "achieved 1180234 req/s under 95% of 1280000",
        ]],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "incomplete run with a missing cell",
        "run": run_doc([HELLO_RAPIRA], "incomplete", ["r1-hello-php-fpm: missing"]),
        "rows": [HELLO_RAPIRA_ROW],
        "voided": [],
        "footer": ["INCOMPLETE RUN: r1-hello-php-fpm: missing.", FOOTER],
        "status": 1,
    },
    {
        "name": "fail reason of a first stage with status errors",
        "run": run_doc([ok_cell("hello-php-fpm", "hello", 1, held=None, held_p99=None, peak=9988.0, unloaded_p50=2400,
                                fail_reason="status errors: 12")]),
        "rows": [["hello-php-fpm", "-", "9988", "-", "2.40ms", "1", "-", "-", "status errors: 12"]],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "mixed rounds near the floor",
        "run": run_doc([
            ok_cell("floor-rapira-worker", "floor", 1, held=None, held_p99=None, peak=9500.0, unloaded_p50=1000,
                    fail_reason="achieved 9500 req/s under 95% of 10000"),
            ok_cell("floor-rapira-worker", "floor", 2, held=None, held_p99=None, peak=9600.0, unloaded_p50=1000,
                    fail_reason="achieved 9600 req/s under 95% of 10000"),
            ok_cell("floor-rapira-worker", "floor", 3, held=10000, held_p99=500, peak=12000.0, unloaded_p50=1000,
                    fail_reason="achieved 12000 req/s under 95% of 20000"),
        ]),
        # Held rate median over all 3 rounds, a None held counted as 0: median(0, 0, 10000) = 0, so held
        # and p99_held print "-". Peak median(9500, 9600, 12000) = 9600, spread 100 * (12000 - 9500) / 9600 = 26.0%.
        "rows": [[
            "floor-rapira-worker", "-", "9600", "-", "1.00ms", "3", "26.0%", "-",
            "achieved 9500 req/s under 95% of 10000; achieved 9600 req/s under 95% of 10000; "
            "achieved 12000 req/s under 95% of 20000",
        ]],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "two of three rounds held the floor",
        "run": run_doc([
            ok_cell("floor-rapira-worker", "floor", 1, held=None, held_p99=None, peak=8000.0, unloaded_p50=900,
                    fail_reason="achieved 8000 req/s under 95% of 10000"),
            ok_cell("floor-rapira-worker", "floor", 2, held=10000, held_p99=400, peak=10000.0, unloaded_p50=1000,
                    fail_reason="achieved 10000 req/s under 95% of 20000"),
            ok_cell("floor-rapira-worker", "floor", 3, held=10000, held_p99=500, peak=12000.0, unloaded_p50=1100,
                    fail_reason="achieved 12000 req/s under 95% of 20000"),
        ]),
        # Held rate median over all 3 rounds, a None held counted as 0: median(0, 10000, 10000) = 10000, so
        # held prints 10000 and p99_held is the median of the p99 of the rounds that held: median(400, 500) = 450.
        # Peak median(8000, 10000, 12000) = 10000, spread 100 * (12000 - 8000) / 10000 = 40.0%.
        "rows": [[
            "floor-rapira-worker", "10000", "10000", "0.45ms", "1.00ms", "3", "40.0%", "-",
            "achieved 8000 req/s under 95% of 10000; achieved 10000 req/s under 95% of 20000; "
            "achieved 12000 req/s under 95% of 20000",
        ]],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "two rounds held at two stage rates",
        "run": run_doc([
            ok_cell("hello-rapira-worker", "hello", 1, held=160000, held_p99=1000, peak=200000.0, unloaded_p50=600,
                    fail_reason="achieved 200000 req/s under 95% of 320000"),
            ok_cell("hello-rapira-worker", "hello", 2, held=320000, held_p99=2000, peak=360000.0, unloaded_p50=800,
                    fail_reason="achieved 360000 req/s under 95% of 640000"),
        ]),
        # The held rate is a stage rate: median_low(160000, 320000) = 160000. p99_held comes from the rounds that
        # held 160000, so it is 1000 us, not the median over both rounds.
        # Peak median(200000, 360000) = 280000, spread 100 * (360000 - 200000) / 280000 = 57.1%.
        "rows": [[
            "hello-rapira-worker", "160000", "280000", "1.00ms", "0.70ms", "2", "57.1%", "-",
            "achieved 200000 req/s under 95% of 320000; achieved 360000 req/s under 95% of 640000",
        ]],
        "voided": [],
        "footer": [],
        "status": 0,
    },
]


def parse(text):
    """The table rows split into 9 fields, the voided lines, and the footer lines."""
    lines = text.splitlines()
    end = lines.index("") if "" in lines else len(lines)
    table = [line.split(None, 8) for line in lines[2:end]]
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
        run = run_doc([HELLO_RAPIRA], "incomplete", ["r1-hello-php-fpm: missing"])
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
