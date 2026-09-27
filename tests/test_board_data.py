"""Data transforms of board/app.js, run under node.

The fixture holds three schema 3 run files reduced to the fields the board reads.
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

RUN_A, RUN_B, RUN_S = json.loads(FIXTURE.read_text())
SUMMARY_A = RUN_A["summary"]
SUMMARY_B = RUN_B["summary"]
COMMIT_B = "https://github.com/rapira-rs/rapira/commit/bbbbbbb0000000000000000000000000000000b2"

# An index entry of a run file from before schema 3: it has no schema key.
OLD_ENTRY = {
    "id": "20260925T010000Z-ci-ac56141",
    "started": "2026-09-25T01:00:00Z",
    "suite": "ci",
    "rapira_sha": "ac56141000000000000000000000000000000000",
    "rapira_version": "0.8.1-nightly.ac56141",
    "status": "complete",
    "smoke": False,
    "pr": None,
}


def entry(run):
    """The manifest entry of a run, reduced to the fields that visibleRuns reads."""
    return {"id": run["id"], "started": run["started"], "smoke": run["smoke"], "schema": run["schema"]}


SERIES_CASES = [
    {
        # A point is the summary record of one target and one measure in one run, as the run file holds it.
        # grpc-rapira has 0 pairs in run b, so its records there hold nulls and the chart draws gaps.
        # yii3-rapira-dispatcher is only in run b, so its points in run a are null.
        # The targets come in name order. A date is the UTC start of the run to the minute.
        "name": "series from the summary with 0 pairs and a target in one run",
        "runs": [RUN_A, RUN_B],
        "expected": {
            "labels": ["#101", "bbbbbbb"],
            "dates": ["2026-09-26 01:00 UTC", "2026-09-27 01:00 UTC"],
            "links": ["https://github.com/rapira-rs/rapira/pull/101", COMMIT_B],
            "titles": ["Faster hello", ""],
            "rounds": [3, 3],
            "targets": {
                "grpc-rapira": {
                    "capacity": [SUMMARY_A["grpc-rapira"]["capacity"], SUMMARY_B["grpc-rapira"]["capacity"]],
                    "p99": [SUMMARY_A["grpc-rapira"]["p99"], SUMMARY_B["grpc-rapira"]["p99"]],
                    "rss": [SUMMARY_A["grpc-rapira"]["rss"], SUMMARY_B["grpc-rapira"]["rss"]],
                },
                "hello-rapira-worker": {
                    "capacity": [SUMMARY_A["hello-rapira-worker"]["capacity"], SUMMARY_B["hello-rapira-worker"]["capacity"]],
                    "p99": [SUMMARY_A["hello-rapira-worker"]["p99"], SUMMARY_B["hello-rapira-worker"]["p99"]],
                    "rss": [SUMMARY_A["hello-rapira-worker"]["rss"], SUMMARY_B["hello-rapira-worker"]["rss"]],
                },
                "yii3-rapira-dispatcher": {
                    "capacity": [None, SUMMARY_B["yii3-rapira-dispatcher"]["capacity"]],
                    "p99": [None, SUMMARY_B["yii3-rapira-dispatcher"]["p99"]],
                    "rss": [None, SUMMARY_B["yii3-rapira-dispatcher"]["rss"]],
                },
            },
        },
    },
    {
        # An index without schema 3 runs gives an empty board: no labels and no targets.
        "name": "no runs",
        "runs": [],
        "expected": {"labels": [], "dates": [], "links": [], "titles": [], "rounds": [], "targets": {}},
    },
]

# The series of runs a and b, as the first series case expects it.
SERIES_AB = SERIES_CASES[0]["expected"]


def point(pairs, delta_pct, min_pct, max_pct):
    """A summary record of one measure. tone does not read base, new and flags."""
    return {"pairs": pairs, "delta_pct": delta_pct, "min_pct": min_pct, "max_pct": max_pct, "base": 100.0, "new": 104.0, "flags": []}


# A point is "better" or "worse" only when it has one pair per round, its band is strictly on one side of zero,
# and |delta_pct| is at least the noise floor. The floors of board/app.js: capacity 2.5, p99 3, rss 2 (percent), and
# p99 19.5 for grpc-rapira. A higher capacity is better. A lower p99 and a lower RSS are better. All other points are
# "" (gray).
# The target of the rows that do not test a per-target floor.
HELLO = "hello-rapira-worker"
TONE_CASES = [
    # 3 of 3 pairs, band 3.5 to 4.6 above zero, 4.0 >= 2.5.
    {"name": "capacity up above the floor is better", "point": point(3, 4.0, 3.5, 4.6), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": "better"},
    # 3 of 3 pairs, band -6.0 to -4.0 below zero, 5.0 >= 2.5.
    {"name": "capacity down above the floor is worse", "point": point(3, -5.0, -6.0, -4.0), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": "worse"},
    # 3 of 3 pairs, band -14.0 to -11.0 below zero, 12.5 >= 3.
    {"name": "p99 down above the floor is better", "point": point(3, -12.5, -14.0, -11.0), "target": HELLO, "measure": "p99", "rounds": 3, "expected": "better"},
    # 3 of 3 pairs, band 11.0 to 14.0 above zero, 12.5 >= 3.
    {"name": "p99 up above the floor is worse", "point": point(3, 12.5, 11.0, 14.0), "target": HELLO, "measure": "p99", "rounds": 3, "expected": "worse"},
    # 3 of 3 pairs, band 2.0 to 4.0 above zero, 3.0 >= 3: a delta equal to the p99 floor counts.
    {"name": "hello p99 at the p99 floor is worse", "point": point(3, 3.0, 2.0, 4.0), "target": HELLO, "measure": "p99", "rounds": 3, "expected": "worse"},
    # 3 of 3 pairs, band 2.0 to 3.0 above zero, 2.5 >= 2.
    {"name": "rss up above the floor is worse", "point": point(3, 2.5, 2.0, 3.0), "target": HELLO, "measure": "rss", "rounds": 3, "expected": "worse"},
    # 3 of 3 pairs, band -3.0 to -2.0 below zero, 2.5 >= 2.
    {"name": "rss down above the floor is better", "point": point(3, -2.5, -3.0, -2.0), "target": HELLO, "measure": "rss", "rounds": 3, "expected": "better"},
    # 2 pairs of 3 rounds: one pair was void or dropped, so the point is gray although the band is above zero.
    {"name": "2 pairs of 3 rounds", "point": point(2, 4.0, 3.5, 4.6), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": ""},
    # min_pct 0 is not strictly above zero.
    {"name": "band touches zero from above", "point": point(3, 4.0, 0.0, 6.0), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": ""},
    # max_pct 0 is not strictly below zero.
    {"name": "band touches zero from below", "point": point(3, -12.0, -15.0, 0.0), "target": HELLO, "measure": "p99", "rounds": 3, "expected": ""},
    # The band -0.5 to 2.0 crosses zero.
    {"name": "band crosses zero", "point": point(3, 1.5, -0.5, 2.0), "target": HELLO, "measure": "rss", "rounds": 3, "expected": ""},
    # 2.4 < 2.5, the capacity floor, although the band 2.0 to 2.9 is above zero.
    {"name": "delta under the floor", "point": point(3, 2.4, 2.0, 2.9), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": ""},
    # 2.5 >= 2.5: a delta equal to the floor counts.
    {"name": "delta at the floor", "point": point(3, 2.5, 2.0, 3.5), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": "better"},
    # 19.4 < 19.5, the p99 floor of grpc-rapira, although 19.4 is above the p99 floor 3 and the band is above zero.
    {"name": "grpc p99 under the grpc floor", "point": point(3, 19.4, 17.0, 21.0), "target": "grpc-rapira", "measure": "p99", "rounds": 3, "expected": ""},
    # 19.5 >= 19.5, band 17.0 to 21.0 above zero, and a higher p99 is worse.
    {"name": "grpc p99 at the grpc floor is worse", "point": point(3, 19.5, 17.0, 21.0), "target": "grpc-rapira", "measure": "p99", "rounds": 3, "expected": "worse"},
    # grpc-rapira has its own p99 floor only, so its capacity uses the capacity floor: 2.5 >= 2.5.
    {"name": "grpc capacity uses the capacity floor", "point": point(3, 2.5, 2.0, 3.5), "target": "grpc-rapira", "measure": "capacity", "rounds": 3, "expected": "better"},
    # 1 of 1 pairs in a suite of 1 round, band -11.0 to -11.0 below zero, 11.0 >= 3.
    {"name": "1 pair in a suite of 1 round", "point": point(1, -11.0, -11.0, -11.0), "target": HELLO, "measure": "p99", "rounds": 1, "expected": "better"},
    # Every pair of the measure was void, so the summary numbers are null.
    {"name": "0 pairs", "point": {"pairs": 0, "delta_pct": None, "min_pct": None, "max_pct": None, "base": None, "new": None, "flags": []}, "target": HELLO, "measure": "capacity", "rounds": 3, "expected": ""},
    # A target that the run does not have.
    {"name": "null point", "point": None, "target": HELLO, "measure": "p99", "rounds": 3, "expected": ""},
]


def line(text, tone=""):
    """A tooltip line of tooltipLines."""
    return {"text": text, "tone": tone}


# The hover lines of one point: the run, the pull request title when the run has one, the new and the base median
# in the unit of the measure, the delta with its band and pair count in the tone of the point, and the flags when
# the point has any. The units: capacity in req/s, p99 in ms from us, RSS in MiB from KiB.
TOOLTIP_CASES = [
    {
        # Run a, hello capacity: base 100000, new 104000 req/s. 3 of 3 pairs, band above zero, 4.0 >= 2.5.
        "name": "capacity, pull request title, better",
        "target": "hello-rapira-worker", "measure": "capacity", "index": 0,
        "expected": [
            line("2026-09-26 01:00 UTC - #101"),
            line("Faster hello"),
            line("new 104000 vs base 100000 req/s"),
            line("delta +4.0% (min +3.5%, max +4.6%, 3 pairs)", "better"),
        ],
    },
    {
        # Run b, hello p99: 1050 us = 1.05 ms, 1200 us = 1.20 ms. 2 of 3 pairs, so the tone is gray.
        "name": "p99, run without a pull request, 2 pairs",
        "target": "hello-rapira-worker", "measure": "p99", "index": 1,
        "expected": [
            line("2026-09-27 01:00 UTC - bbbbbbb"),
            line("new 1.05 vs base 1.20 ms"),
            line("delta -12.5% (min -14.0%, max -11.0%, 2 pairs)"),
        ],
    },
    {
        # Run a, hello RSS: 208896 / 1024 = 204.0 MiB, 204800 / 1024 = 200.0 MiB. 3 of 3 pairs, band above zero,
        # 2.0 >= 2, and a higher RSS is worse.
        "name": "RSS, worse, one flag",
        "target": "hello-rapira-worker", "measure": "rss", "index": 0,
        "expected": [
            line("2026-09-26 01:00 UTC - #101"),
            line("Faster hello"),
            line("new 204.0 vs base 200.0 MiB"),
            line("delta +2.0% (min +1.5%, max +2.5%, 3 pairs)", "worse"),
            line("loader_skew"),
        ],
    },
    {
        # Run a, grpc capacity: the band -0.8 to 1.2 crosses zero, so the tone is gray.
        "name": "capacity, band across zero, two flags",
        "target": "grpc-rapira", "measure": "capacity", "index": 0,
        "expected": [
            line("2026-09-26 01:00 UTC - #101"),
            line("Faster hello"),
            line("new 150750 vs base 150000 req/s"),
            line("delta +0.5% (min -0.8%, max +1.2%, 3 pairs)"),
            line("loader_busy, not_saturated"),
        ],
    },
    {
        # Run b, yii3 capacity: 1 of 3 pairs, so the tone is gray.
        "name": "capacity, 1 pair",
        "target": "yii3-rapira-dispatcher", "measure": "capacity", "index": 1,
        "expected": [
            line("2026-09-27 01:00 UTC - bbbbbbb"),
            line("new 24000 vs base 25000 req/s"),
            line("delta -4.0% (min -4.0%, max -4.0%, 1 pair)"),
        ],
    },
]

VISIBLE_CASES = [
    {
        # The board fetches only schema 3 run files, so an entry without the schema key and a smoke entry drop out.
        "name": "old entry and smoke entry dropped, sorted by started",
        "entries": [entry(RUN_B), OLD_ENTRY, entry(RUN_S), entry(RUN_A)],
        "expected": [entry(RUN_A), entry(RUN_B)],
    },
    {
        "name": "only old entries",
        "entries": [OLD_ENTRY],
        "expected": [],
    },
    {
        "name": "schema 3 entries in order stay the same",
        "entries": [entry(RUN_A), entry(RUN_B)],
        "expected": [entry(RUN_A), entry(RUN_B)],
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
                # Dict equality ignores the key order, and the board draws the rows in key order.
                self.assertEqual(list(series["targets"]), list(case["expected"]["targets"]))


@unittest.skipUnless(NODE, "node is not installed")
class ToneTest(unittest.TestCase):
    def test_tone(self):
        for case in TONE_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(call_js("tone", case["point"], case["target"], case["measure"], case["rounds"]), case["expected"])


@unittest.skipUnless(NODE, "node is not installed")
class TooltipLinesTest(unittest.TestCase):
    def test_tooltip_lines(self):
        for case in TOOLTIP_CASES:
            with self.subTest(name=case["name"]):
                lines = call_js("tooltipLines", SERIES_AB, case["target"], case["measure"], case["index"])
                self.assertEqual(lines, case["expected"])


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
