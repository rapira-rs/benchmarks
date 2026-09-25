"""Tests of the comparison of two run files."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from rig.__main__ import main
from rig.compare import compare


def ok_cell(name, app, round_no, held, peak):
    """An ok cell that holds `held` and fails at twice that rate."""
    return {
        "key": f"r{round_no}-{name}",
        "target": {"name": name, "app": app},
        "round": round_no,
        "status": "ok",
        "flags": {},
        "held": {"rate": held, "stage": 1},
        "peak": peak,
        "unloaded": {"p50": 700, "p99": 1500},
        "stages": [
            {"rate": 10000, "pass": True, "latency_us": {"p50": 700, "p99": 1500}},
            {"rate": held, "pass": True, "latency_us": {"p50": 900, "p99": 2000}},
            {"rate": held * 2, "pass": False, "fail_reason": f"achieved {peak:.0f} req/s under 95% of {held * 2}", "latency_us": {"p50": 5000, "p99": 900000}},
        ],
    }


def run_doc(run_id, cells):
    return {
        "id": run_id,
        "rig": {"server_type": "c7a.8xlarge", "loader_type": "c7a.xlarge", "loader_count": 4, "az": "eu-central-1a"},
        "processes": 32,
        "ladder": {"stage_s": 20},
        "cells": cells,
        "status": "complete",
        "reasons": [],
    }


A_ID = "20260924T120000Z-full-0a1b2c3"
B_ID = "20260925T120000Z-full-4d5e6f7"

# Peaks of three rounds. a: median 1180234.5, spread 100 * (1184200 - 1170000) / 1180234.5 = 1.2%.
# b: median 1120001.0, spread 100 * (1125100 - 1115000) / 1120001 = 0.9%.
# Delta 100 * (1120001.0 - 1180234.5) / 1180234.5 = -5.1%.
A_RUN = run_doc(A_ID, [
    ok_cell("hello-rapira-worker", "hello", 1, 640000, 1170000.0),
    ok_cell("hello-rapira-worker", "hello", 2, 640000, 1180234.5),
    ok_cell("hello-rapira-worker", "hello", 3, 640000, 1184200.0),
])
B_RUN = run_doc(B_ID, [
    ok_cell("hello-rapira-worker", "hello", 1, 640000, 1115000.0),
    ok_cell("hello-rapira-worker", "hello", 2, 640000, 1120001.0),
    ok_cell("hello-rapira-worker", "hello", 3, 640000, 1125100.0),
])
DELTA_LINE = "hello-rapira-worker  held 640000 -> 640000  peak 1180234.5 -> 1120001.0  -5.1%  spread 1.2% / 0.9%"

IDENTITY_CASES = [
    {"name": "server type differs", "section": "rig", "key": "server_type", "value": "c7a.4xlarge",
     "status": 1, "first": "refused: rig.server_type differs: c7a.8xlarge vs c7a.4xlarge"},
    {"name": "loader type differs", "section": "rig", "key": "loader_type", "value": "c7a.2xlarge",
     "status": 1, "first": "refused: rig.loader_type differs: c7a.xlarge vs c7a.2xlarge"},
    {"name": "loader count differs", "section": "rig", "key": "loader_count", "value": 3,
     "status": 1, "first": "refused: rig.loader_count differs: 4 vs 3"},
    {"name": "processes differ", "section": "", "key": "processes", "value": 16,
     "status": 1, "first": "refused: processes differs: 32 vs 16"},
    {"name": "stage duration differs", "section": "ladder", "key": "stage_s", "value": 30,
     "status": 1, "first": "refused: ladder.stage_s differs: 20 vs 30"},
    {"name": "availability zone is not identity", "section": "rig", "key": "az", "value": "eu-central-1b",
     "status": 0, "first": f"a: {A_ID}"},
]


def changed(run, section, key, value):
    out = copy.deepcopy(run)
    if section:
        out[section][key] = value
    else:
        out[key] = value
    return out


class TestCompare(unittest.TestCase):
    def test_identity(self):
        for case in IDENTITY_CASES:
            with self.subTest(name=case["name"]):
                b = changed(B_RUN, case["section"], case["key"], case["value"])
                text, status = compare(A_RUN, b)
                self.assertEqual(status, case["status"])
                self.assertEqual(text.splitlines()[0], case["first"])

    def test_refusal_names_the_force_option(self):
        text, _ = compare(A_RUN, changed(B_RUN, "", "processes", 16))
        self.assertEqual(text.splitlines(), ["refused: processes differs: 32 vs 16", "Use --force to compare anyway."])

    def test_force_prints_the_difference_and_the_deltas(self):
        text, status = compare(A_RUN, changed(B_RUN, "rig", "server_type", "c7a.4xlarge"), force=True)
        self.assertEqual(status, 0)
        self.assertEqual(text.splitlines(), [
            "forced: rig.server_type differs: c7a.8xlarge vs c7a.4xlarge",
            f"a: {A_ID}",
            f"b: {B_ID}",
            "",
            DELTA_LINE,
        ])

    def test_delta_line(self):
        text, status = compare(A_RUN, B_RUN)
        self.assertEqual(status, 0)
        self.assertEqual(text.splitlines()[3:], [DELTA_LINE])

    def test_target_in_one_run_only(self):
        a = run_doc(A_ID, [
            ok_cell("hello-rapira-worker", "hello", 1, 640000, 1180234.5),
            ok_cell("static-rapira-hit", "static", 1, 1280000, 1500000.0),
        ])
        b = run_doc(B_ID, [
            ok_cell("hello-rapira-worker", "hello", 1, 640000, 1120001.0),
            ok_cell("grpc-rapira", "grpc", 1, 80000, 150000.0),
        ])
        text, status = compare(a, b)
        self.assertEqual(status, 0)
        # Names pad to the longest name, hello-rapira-worker (19 characters).
        self.assertEqual(text.splitlines()[3:], [
            "hello-rapira-worker  held 640000 -> 640000  peak 1180234.5 -> 1120001.0  -5.1%  spread 0.0% / 0.0%",
            "static-rapira-hit    only in a",
            "grpc-rapira          only in b",
        ])

    def test_cli_force_flag(self):
        b = changed(B_RUN, "rig", "loader_count", 3)
        with tempfile.TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "a.json"
            path_b = Path(tmp) / "b.json"
            path_a.write_text(json.dumps(A_RUN))
            path_b.write_text(json.dumps(b))
            with contextlib.redirect_stdout(io.StringIO()):
                refused = main(["compare", str(path_a), str(path_b)])
                forced = main(["compare", str(path_a), str(path_b), "--force"])
        self.assertEqual((refused, forced), (1, 0))


if __name__ == "__main__":
    unittest.main()
