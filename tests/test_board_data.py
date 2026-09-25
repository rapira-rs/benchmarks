"""Data transforms of board/app.js, run under node.

The fixture holds four run files reduced to the fields the board reads.
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

RUN_A, RUN_B, RUN_C, RUN_S = json.loads(FIXTURE.read_text())


def entry(run):
    """The manifest entry of a run, reduced to the fields that visibleRuns reads."""
    return {"id": run["id"], "started": run["started"], "smoke": run["smoke"]}


SERIES_CASES = [
    {
        # hello, run b: two ok cells, so a value is the median of r1 and r2:
        # (900 + 1100) / 2 = 1000 us at 10000 and (1300 + 1500) / 2 = 1400 us at 20000.
        # Only r1 passed 40000. Run c: the hello cell is incomplete, so no points and no flags.
        # symfony, run b: the cell is void. laravel has cells only in run c.
        # A failing stage adds no rate. The apps come in name order.
        "name": "median over rounds, void and incomplete cells, target in one run",
        "runs": [RUN_A, RUN_B, RUN_C],
        "expected": {
            "labels": ["aaaaaaa", "bbbbbbb", "ccccccc"],
            "versions": ["0.8.1-nightly.aaaaaaa", "0.8.1-nightly.bbbbbbb", "0.8.1-nightly.ccccccc"],
            "apps": {
                "hello": {
                    "hello-rapira-worker": {
                        "rates": [10000, 20000, 40000],
                        "p99_ms": {
                            "10000": [0.8, 1.0, None],
                            "20000": [1.2, 1.4, None],
                            "40000": [None, 2.5, None],
                        },
                        "flags": [[], ["generator_bound"], None],
                    },
                },
                "laravel": {
                    "laravel-rapira-worker": {
                        "rates": [5000],
                        "p99_ms": {"5000": [None, None, 4.0]},
                        "flags": [None, None, []],
                    },
                },
                "symfony": {
                    "symfony-rapira-worker": {
                        "rates": [10000, 20000],
                        "p99_ms": {
                            "10000": [2.0, None, 2.1],
                            "20000": [None, None, 3.0],
                        },
                        "flags": [[], None, ["log_growth"]],
                    },
                },
            },
        },
    },
    {
        # The rates of hello and symfony stop at the last passing stage of run a.
        "name": "one run",
        "runs": [RUN_A],
        "expected": {
            "labels": ["aaaaaaa"],
            "versions": ["0.8.1-nightly.aaaaaaa"],
            "apps": {
                "hello": {
                    "hello-rapira-worker": {
                        "rates": [10000, 20000],
                        "p99_ms": {"10000": [0.8], "20000": [1.2]},
                        "flags": [[]],
                    },
                },
                "symfony": {
                    "symfony-rapira-worker": {
                        "rates": [10000],
                        "p99_ms": {"10000": [2.0]},
                        "flags": [[]],
                    },
                },
            },
        },
    },
]

VISIBLE_CASES = [
    {
        "name": "smoke entry dropped, sorted by started",
        "entries": [entry(RUN_B), entry(RUN_S), entry(RUN_A), entry(RUN_C)],
        "expected": [entry(RUN_A), entry(RUN_B), entry(RUN_C)],
    },
    {
        "name": "entries in order without smoke stay the same",
        "entries": [entry(RUN_A), entry(RUN_B), entry(RUN_C)],
        "expected": [entry(RUN_A), entry(RUN_B), entry(RUN_C)],
    },
]

RATE_LABEL_CASES = [
    {"name": "under 1000 is the number", "rate": 500, "expected": "500"},
    {"name": "thousands", "rate": 5000, "expected": "5k"},
    {"name": "tens of thousands", "rate": 10000, "expected": "10k"},
    {"name": "hundreds of thousands", "rate": 640000, "expected": "640k"},
    {"name": "millions with decimals", "rate": 1280000, "expected": "1.28M"},
    {"name": "millions above 2M", "rate": 2560000, "expected": "2.56M"},
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
class TargetSeriesTest(unittest.TestCase):
    def test_target_series(self):
        for case in SERIES_CASES:
            with self.subTest(name=case["name"]):
                series = call_js("targetSeries", case["runs"])
                self.assertEqual(series, case["expected"])
                # Dict equality ignores the key order, and the board draws the apps in key order.
                self.assertEqual(list(series["apps"]), list(case["expected"]["apps"]))


@unittest.skipUnless(NODE, "node is not installed")
class VisibleRunsTest(unittest.TestCase):
    def test_visible_runs(self):
        for case in VISIBLE_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(call_js("visibleRuns", case["entries"]), case["expected"])


@unittest.skipUnless(NODE, "node is not installed")
class RateLabelTest(unittest.TestCase):
    def test_rate_label(self):
        for case in RATE_LABEL_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(call_js("rateLabel", case["rate"]), case["expected"])


if __name__ == "__main__":
    unittest.main()
