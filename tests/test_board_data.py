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
COMMIT_B = "https://github.com/rapira-rs/rapira/commit/bbbbbbb0000000000000000000000000000000b2"


def entry(run):
    """The manifest entry of a run, reduced to the fields that visibleRuns reads."""
    return {"id": run["id"], "started": run["started"], "smoke": run["smoke"]}


SERIES_CASES = [
    {
        # hello, run b: two ok cells, so a value is the median of r1 and r2: p99 (900 + 1100) / 2 = 1000 us,
        # RSS (210944 + 209920) / 2 = 210432 KiB = 205.5 MiB, req/s (240000 + 250100) / 2 = 245050, held only
        # when every cell held, the flags are the union. Run c: the hello cell is incomplete, so nulls.
        # grpc, run b: the cell is void. yii3 has a cell only in run c. The targets come in name order.
        # grpc: 1048576 / 1024 = 1024 MiB, 1150976 / 1024 = 1124 MiB, 2500 us = 2.5 ms, 9000 us = 9 ms. yii3: 524288 / 1024 = 512 MiB, 4000 us = 4 ms.
        "name": "medians over rounds, void and incomplete cells, target in one run",
        "runs": [RUN_A, RUN_B, RUN_C],
        "expected": {
            "labels": ["#101", "bbbbbbb", "#103"],
            "links": ["https://github.com/rapira-rs/rapira/pull/101", COMMIT_B, "https://github.com/rapira-rs/rapira/pull/103"],
            "titles": ["Faster hello", "", "Fix the dispatcher drain"],
            "targets": {
                "grpc-rapira": {
                    "p99_ms": [2.5, None, 9.0],
                    "rss_mib": [1024.0, None, 1124.0],
                    "achieved": [99990, None, 85000],
                    "rate": [100000, None, 100000],
                    "held": [True, None, False],
                    "flags": [[], None, ["server_unsaturated"]],
                },
                "hello-rapira-worker": {
                    "p99_ms": [1.2, 1.0, None],
                    "rss_mib": [200.0, 205.5, None],
                    "achieved": [249800.5, 245050, None],
                    "rate": [250000, 250000, None],
                    "held": [True, False, None],
                    "flags": [[], ["generator_bound"], None],
                },
                "yii3-rapira-dispatcher": {
                    "p99_ms": [None, None, 4.0],
                    "rss_mib": [None, None, 512.0],
                    "achieved": [None, None, 250000],
                    "rate": [None, None, 250000],
                    "held": [None, None, True],
                    "flags": [None, None, []],
                },
            },
        },
    },
    {
        # hello: 204800 / 1024 = 200 MiB, 1200 us = 1.2 ms; grpc as above.
        "name": "one run",
        "runs": [RUN_A],
        "expected": {
            "labels": ["#101"],
            "links": ["https://github.com/rapira-rs/rapira/pull/101"],
            "titles": ["Faster hello"],
            "targets": {
                "grpc-rapira": {"p99_ms": [2.5], "rss_mib": [1024.0], "achieved": [99990], "rate": [100000], "held": [True], "flags": [[]]},
                "hello-rapira-worker": {"p99_ms": [1.2], "rss_mib": [200.0], "achieved": [249800.5], "rate": [250000], "held": [True], "flags": [[]]},
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

LABEL_CASES = [
    {"name": "run with a pull request", "run": RUN_A, "label": "#101", "link": "https://github.com/rapira-rs/rapira/pull/101"},
    {"name": "run without a pull request", "run": RUN_B, "label": "bbbbbbb", "link": COMMIT_B},
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
                # Dict equality ignores the key order, and the board draws the lines in key order.
                self.assertEqual(list(series["targets"]), list(case["expected"]["targets"]))


@unittest.skipUnless(NODE, "node is not installed")
class VisibleRunsTest(unittest.TestCase):
    def test_visible_runs(self):
        for case in VISIBLE_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(call_js("visibleRuns", case["entries"]), case["expected"])


@unittest.skipUnless(NODE, "node is not installed")
class RunLabelTest(unittest.TestCase):
    def test_label_and_link(self):
        for case in LABEL_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(call_js("runLabel", case["run"]), case["label"])
                self.assertEqual(call_js("runLink", case["run"]), case["link"])


if __name__ == "__main__":
    unittest.main()
