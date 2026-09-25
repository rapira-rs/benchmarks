"""Data transforms of board/app.js, run under node.

The fixture holds three run files reduced to the fields the board reads.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "board" / "app.js"
FIXTURE = ROOT / "tests" / "fixtures" / "board_runs.json"
NODE = shutil.which("node")

# Loads app.js as a CommonJS module, calls one export with the JSON
# arguments from stdin, and prints the JSON result.
CALL = (
    "const board = require(process.argv[1]);"
    "const args = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
    "process.stdout.write(JSON.stringify(board[process.argv[2]](...args)));"
)

HISTORY_CASES = [
    {
        # Run a: one ok cell. Run b: generator_bound makes the point a floor.
        # Run c: r3 is void, so the median covers r1 and r2 only:
        # (1100000 + 1150000) / 2 = 1125000; held 640000 in both rounds.
        "name": "single rounds, a floor, and a median over surviving rounds",
        "app": "hello",
        "target": "hello-rapira-worker",
        "expected": {
            "peak": [1180234.5, 1300000.0, 1125000.0],
            "held": [640000, 1280000, 640000],
            "floor": [False, True, False],
            "flags": [[], ["generator_bound"], ["log_growth"]],
        },
    },
    {
        # Run b is void: both numbers are gaps.
        # Run c: peaks 110000, 130000, 90000 give the median 110000;
        # held 80000, 80000, 40000 give the median 80000.
        "name": "void cell is a gap and three rounds give the median",
        "app": "symfony",
        "target": "symfony-rapira-worker",
        "expected": {
            "peak": [120500.0, None, 110000.0],
            "held": [80000, None, 80000],
            "floor": [False, False, False],
            "flags": [[], [], []],
        },
    },
    {
        # The target is only in run c, and its first stage failed:
        # held is null and peak is set.
        "name": "target absent from earlier runs and first stage failed",
        "app": "static",
        "target": "static-rapira-hit",
        "expected": {
            "peak": [None, None, 9000.0],
            "held": [None, None, None],
            "floor": [False, False, False],
            "flags": [[], [], []],
        },
    },
    {
        # An incomplete cell is excluded like a void cell.
        "name": "incomplete cell is a gap",
        "app": "grpc",
        "target": "grpc-rapira-grpc",
        "expected": {
            "peak": [150000.0, 140000.0, None],
            "held": [80000, 80000, None],
            "floor": [True, True, False],
            "flags": [["generator_bound"], ["generator_bound"], []],
        },
    },
]

INDEX_ENTRIES = [
    {"id": "b", "started": "2026-09-27T01:00:00Z", "smoke": False},
    {"id": "s", "started": "2026-09-26T12:00:00Z", "smoke": True},
    {"id": "a", "started": "2026-09-26T01:00:00Z", "smoke": False},
]

VISIBLE_CASES = [
    {"name": "smoke runs hidden, oldest first", "show_smoke": False, "expected": ["a", "b"]},
    {"name": "smoke runs shown, oldest first", "show_smoke": True, "expected": ["a", "s", "b"]},
]


def call_js(function, *args):
    out = subprocess.run(
        [NODE, "-e", CALL, str(APP_JS), function],
        input=json.dumps(args),
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, "node is not installed")
class HistorySeriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history = call_js("historySeries", json.loads(FIXTURE.read_text()))

    def test_series(self):
        for case in HISTORY_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(self.history["apps"][case["app"]][case["target"]], case["expected"])

    def test_labels_name_the_commit_and_the_date(self):
        self.assertEqual(
            self.history["labels"],
            ["aaaaaaa 2026-09-26", "bbbbbbb 2026-09-27", "ccccccc 2026-09-28"],
        )

    def test_targets_are_grouped_by_app(self):
        grouped = {app: sorted(targets) for app, targets in self.history["apps"].items()}
        self.assertEqual(
            grouped,
            {
                "hello": ["hello-rapira-worker"],
                "symfony": ["symfony-rapira-worker"],
                "static": ["static-rapira-hit"],
                "grpc": ["grpc-rapira-grpc"],
            },
        )


@unittest.skipUnless(NODE, "node is not installed")
class VisibleRunsTest(unittest.TestCase):
    def test_visible_runs(self):
        for case in VISIBLE_CASES:
            with self.subTest(name=case["name"]):
                runs = call_js("visibleRuns", INDEX_ENTRIES, case["show_smoke"])
                self.assertEqual([run["id"] for run in runs], case["expected"])


if __name__ == "__main__":
    unittest.main()
