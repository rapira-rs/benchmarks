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


def ok_cell(name, round_no, achieved, p99, rss_kb):
    return {
        "key": f"r{round_no}-{name}",
        "target": {"name": name},
        "round": round_no,
        "status": "ok",
        "flags": {},
        "rate": 250000,
        "achieved_rps": achieved,
        "successful_rps": achieved,
        "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
        "latency_us": {"p50": 700, "p90": p99, "p99": p99, "p999": p99, "max": p99},
        "rss_kb": rss_kb,
        "held": True,
        "cpu": {"server_busy": 60, "loader_busy": 70},
        "loaders": [],
    }


def run_doc(run_id, cells):
    return {
        "id": run_id,
        "rig": {"server_type": "c7a.8xlarge", "loader_type": "c7a.2xlarge", "loader_count": 1, "az": "eu-central-1a"},
        "processes": 32,
        "suite": {"rates": {"hello": 250000}, "duration_s": 60},
        "cells": cells,
        "status": "complete",
        "reasons": [],
    }


A_ID = "20260924T120000Z-ci-0a1b2c3"
B_ID = "20260925T120000Z-ci-4d5e6f7"

# Three rounds. a: req/s median 250000, p99 median 1250 us, RSS median 204800 KiB (200.0 MiB).
# b: req/s median 245000 (-2.0%), p99 median 1450 us (+16.0%), RSS median 215040 KiB (210.0 MiB, +5.0%).
A_RUN = run_doc(A_ID, [
    ok_cell("hello-rapira-worker", 1, 250000.0, 1200, 204800),
    ok_cell("hello-rapira-worker", 2, 249800.0, 1300, 205824),
    ok_cell("hello-rapira-worker", 3, 250100.0, 1250, 204800),
])
B_RUN = run_doc(B_ID, [
    ok_cell("hello-rapira-worker", 1, 245000.0, 1400, 215040),
    ok_cell("hello-rapira-worker", 2, 244000.0, 1500, 215040),
    ok_cell("hello-rapira-worker", 3, 246000.0, 1450, 216064),
])
DELTA_LINE = "hello-rapira-worker  req/s 250000 -> 245000 -2.0%  p99 1.25ms -> 1.45ms +16.0%  rss 200.0 -> 210.0 +5.0%"

IDENTITY_CASES = [
    {"name": "server type differs", "section": "rig", "key": "server_type", "value": "c7a.4xlarge",
     "status": 1, "first": "refused: rig.server_type differs: c7a.8xlarge vs c7a.4xlarge"},
    {"name": "loader type differs", "section": "rig", "key": "loader_type", "value": "c7a.xlarge",
     "status": 1, "first": "refused: rig.loader_type differs: c7a.2xlarge vs c7a.xlarge"},
    {"name": "loader count differs", "section": "rig", "key": "loader_count", "value": 2,
     "status": 1, "first": "refused: rig.loader_count differs: 1 vs 2"},
    {"name": "processes differ", "section": "", "key": "processes", "value": 16,
     "status": 1, "first": "refused: processes differs: 32 vs 16"},
    {"name": "rates differ", "section": "suite", "key": "rates", "value": {"hello": 200000},
     "status": 1, "first": "refused: suite.rates differs: {'hello': 250000} vs {'hello': 200000}"},
    {"name": "duration differs", "section": "suite", "key": "duration_s", "value": 30,
     "status": 1, "first": "refused: suite.duration_s differs: 60 vs 30"},
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

    def test_zero_base_prints_a_dash(self):
        # A target that dies before the RSS read gives 0 KiB; a delta from 0 has no percent.
        a = run_doc(A_ID, [ok_cell("hello-rapira-worker", 1, 250000.0, 1250, 0)])
        b = run_doc(B_ID, [ok_cell("hello-rapira-worker", 1, 250000.0, 1250, 215040)])
        text, status = compare(a, b)
        self.assertEqual(status, 0)
        line = text.splitlines()[3]
        self.assertTrue(line.endswith("rss 0.0 -> 210.0 -"), line)

    def test_target_in_one_run_only(self):
        a = run_doc(A_ID, [
            ok_cell("hello-rapira-worker", 1, 250000.0, 1250, 204800),
            ok_cell("yii3-rapira-dispatcher", 1, 250000.0, 4000, 524288),
        ])
        b = run_doc(B_ID, [
            ok_cell("hello-rapira-worker", 1, 245000.0, 1450, 215040),
            ok_cell("grpc-rapira", 1, 99990.0, 2500, 1048576),
        ])
        text, status = compare(a, b)
        self.assertEqual(status, 0)
        # Names pad to the longest name, yii3-rapira-dispatcher (22 characters).
        self.assertEqual(text.splitlines()[3:], [
            f"{'hello-rapira-worker':<22}  req/s 250000 -> 245000 -2.0%  p99 1.25ms -> 1.45ms +16.0%  rss 200.0 -> 210.0 +5.0%",
            f"{'yii3-rapira-dispatcher':<22}  only in a",
            f"{'grpc-rapira':<22}  only in b",
        ])

    def test_cli_force_flag(self):
        b = changed(B_RUN, "rig", "loader_count", 2)
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
