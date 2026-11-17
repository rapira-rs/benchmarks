"""Data transforms of board/app.js, run under node.

The fixture holds one schema 3 run file and three schema 4 run files, reduced to the fields the board reads.
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

RUN_C, RUN_A, RUN_B, RUN_S = json.loads(FIXTURE.read_text())
SUMMARY_C = RUN_C["summary"]
SUMMARY_A = RUN_A["summary"]
SUMMARY_B = RUN_B["summary"]
# Run c is the first run, so its new build ccccccc is the baseline build. Runs a and b measure it as their base build.
BASELINE_SHA = RUN_C["rapira"]["sha"]
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


def paired(summary):
    """A point of a run that measured the baseline build as its base build: its change is its paired delta."""
    return {**summary, "change_pct": summary["delta_pct"], "paired": True}


def start(summary):
    """The start of a target and a measure: the first new median above zero, in run c, at 0%."""
    return {**summary, "change_pct": 0, "paired": False, "initial": True, "start_sha": BASELINE_SHA, "start_label": "#100", "start_new": summary["new"]}


SERIES_CASES = [
    {
        # Run c has another base build, so its points are the starts. Runs a and b measured the baseline build ccccccc,
        # so their points are their paired deltas. grpc-rapira has 0 pairs in run b, so its points there are gaps.
        # hello-rapira-dispatcher is only in run b, so its points in runs c and a are null. The targets come in name
        # order. A date is the UTC start of the run to the minute.
        "name": "series of a start and paired points, with 0 pairs and a target in one run",
        "runs": [RUN_C, RUN_A, RUN_B],
        "expected": {
            "labels": ["#100", "#101", "bbbbbbb"],
            "dates": ["2026-09-25 01:00 UTC", "2026-09-26 01:00 UTC", "2026-09-27 01:00 UTC"],
            "links": ["https://github.com/rapira-rs/rapira/pull/100", "https://github.com/rapira-rs/rapira/pull/101", COMMIT_B],
            "titles": ["Baseline", "Faster hello", ""],
            "baseline": BASELINE_SHA,
            "bases": ["eeeeeee", "ccccccc", "ccccccc"],
            "rounds": [3, 3, 3],
            "targets": {
                "grpc-rapira": {
                    "capacity": [start(SUMMARY_C["grpc-rapira"]["capacity"]), paired(SUMMARY_A["grpc-rapira"]["capacity"]), None],
                    "p99": [start(SUMMARY_C["grpc-rapira"]["p99"]), paired(SUMMARY_A["grpc-rapira"]["p99"]), None],
                    "rss": [start(SUMMARY_C["grpc-rapira"]["rss"]), paired(SUMMARY_A["grpc-rapira"]["rss"]), None],
                },
                "hello-rapira-dispatcher": {
                    "capacity": [None, None, paired(SUMMARY_B["hello-rapira-dispatcher"]["capacity"])],
                    "p99": [None, None, paired(SUMMARY_B["hello-rapira-dispatcher"]["p99"])],
                    "rss": [None, None, paired(SUMMARY_B["hello-rapira-dispatcher"]["rss"])],
                },
                "hello-rapira-worker": {
                    "capacity": [start(SUMMARY_C["hello-rapira-worker"]["capacity"]), paired(SUMMARY_A["hello-rapira-worker"]["capacity"]), paired(SUMMARY_B["hello-rapira-worker"]["capacity"])],
                    "p99": [start(SUMMARY_C["hello-rapira-worker"]["p99"]), paired(SUMMARY_A["hello-rapira-worker"]["p99"]), paired(SUMMARY_B["hello-rapira-worker"]["p99"])],
                    "rss": [start(SUMMARY_C["hello-rapira-worker"]["rss"]), paired(SUMMARY_A["hello-rapira-worker"]["rss"]), paired(SUMMARY_B["hello-rapira-worker"]["rss"])],
                },
            },
        },
    },
    {
        # An index without schema 3 or schema 4 runs gives an empty board: no labels and no targets.
        "name": "no runs",
        "runs": [],
        "expected": {"labels": [], "dates": [], "links": [], "titles": [], "baseline": "", "bases": [], "rounds": [], "targets": {}},
    },
]

# The series of runs c, a, and b, as the first series case expects it.
SERIES_CAB = SERIES_CASES[0]["expected"]


def point(pairs, delta_pct, min_pct, max_pct, *, is_paired=True):
    """A point of one measure. tone does not read base, new and flags."""
    return {"pairs": pairs, "delta_pct": delta_pct, "min_pct": min_pct, "max_pct": max_pct, "base": 100.0, "new": 104.0, "flags": [], "change_pct": delta_pct, "paired": is_paired}


# A paired point is "better" or "worse" only when it has one pair per round, all its paired deltas are on one side of
# zero (min_pct above zero or max_pct below zero), and |delta_pct| is at least the noise floor. The floors of the A/A
# calibration in NOTES.md: capacity 2.5, p99 3, rss 2 (percent), and p99 19.5 for grpc-rapira. A higher capacity is
# better. A lower p99 and a lower RSS are better. A point between two rigs and all other points are "" (gray).
# The target of the rows that do not test a per-target floor.
HELLO = "hello-rapira-worker"
TONE_CASES = [
    # 3 of 3 pairs, deltas 3.5 to 4.6 above zero, 4.0 >= 2.5.
    {"name": "capacity up above the floor is better", "point": point(3, 4.0, 3.5, 4.6), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": "better"},
    # 3 of 3 pairs, deltas -6.0 to -4.0 below zero, 5.0 >= 2.5.
    {"name": "capacity down above the floor is worse", "point": point(3, -5.0, -6.0, -4.0), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": "worse"},
    # 3 of 3 pairs, deltas -14.0 to -11.0 below zero, 12.5 >= 3.
    {"name": "p99 down above the floor is better", "point": point(3, -12.5, -14.0, -11.0), "target": HELLO, "measure": "p99", "rounds": 3, "expected": "better"},
    # 3 of 3 pairs, deltas 11.0 to 14.0 above zero, 12.5 >= 3.
    {"name": "p99 up above the floor is worse", "point": point(3, 12.5, 11.0, 14.0), "target": HELLO, "measure": "p99", "rounds": 3, "expected": "worse"},
    # 3 of 3 pairs, deltas 2.0 to 4.0 above zero, 3.0 >= 3: a delta equal to the p99 floor counts.
    {"name": "hello p99 at the p99 floor is worse", "point": point(3, 3.0, 2.0, 4.0), "target": HELLO, "measure": "p99", "rounds": 3, "expected": "worse"},
    # 2.9 < 3, the p99 floor, although the deltas 2.0 to 4.0 are above zero.
    {"name": "hello p99 under the p99 floor", "point": point(3, 2.9, 2.0, 4.0), "target": HELLO, "measure": "p99", "rounds": 3, "expected": ""},
    # 3 of 3 pairs, deltas 2.0 to 3.0 above zero, 2.5 >= 2.
    {"name": "rss up above the floor is worse", "point": point(3, 2.5, 2.0, 3.0), "target": HELLO, "measure": "rss", "rounds": 3, "expected": "worse"},
    # 3 of 3 pairs, deltas -3.0 to -2.0 below zero, 2.5 >= 2.
    {"name": "rss down above the floor is better", "point": point(3, -2.5, -3.0, -2.0), "target": HELLO, "measure": "rss", "rounds": 3, "expected": "better"},
    # 1.9 < 2, the RSS floor, although the deltas -2.5 to -1.5 are below zero.
    {"name": "rss under the floor", "point": point(3, -1.9, -2.5, -1.5), "target": HELLO, "measure": "rss", "rounds": 3, "expected": ""},
    # 2 pairs of 3 rounds: one pair was void or dropped, so the point is gray although the deltas are above zero.
    {"name": "2 pairs of 3 rounds", "point": point(2, 4.0, 3.5, 4.6), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": ""},
    # min_pct 0 is not above zero.
    {"name": "smallest delta at zero", "point": point(3, 4.0, 0.0, 6.0), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": ""},
    # max_pct 0 is not below zero.
    {"name": "largest delta at zero", "point": point(3, -12.0, -15.0, 0.0), "target": HELLO, "measure": "p99", "rounds": 3, "expected": ""},
    # The deltas -0.5 to 2.6 are on both sides of zero, although 2.5 >= 2.
    {"name": "deltas on both sides of zero", "point": point(3, 2.5, -0.5, 2.6), "target": HELLO, "measure": "rss", "rounds": 3, "expected": ""},
    # 2.4 < 2.5, the capacity floor, although the deltas 2.0 to 2.9 are above zero.
    {"name": "capacity under the floor", "point": point(3, 2.4, 2.0, 2.9), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": ""},
    # 2.5 >= 2.5: a delta equal to the floor counts.
    {"name": "capacity at the floor counts", "point": point(3, 2.5, 2.0, 3.5), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": "better"},
    # 19.4 < 19.5, the p99 floor of grpc-rapira, although 19.4 is above the p99 floor 3 and the deltas are above zero.
    {"name": "grpc p99 under the grpc floor", "point": point(3, 19.4, 17.0, 21.0), "target": "grpc-rapira", "measure": "p99", "rounds": 3, "expected": ""},
    # 19.5 >= 19.5, deltas 17.0 to 21.0 above zero, and a higher p99 is worse.
    {"name": "grpc p99 at the grpc floor is worse", "point": point(3, 19.5, 17.0, 21.0), "target": "grpc-rapira", "measure": "p99", "rounds": 3, "expected": "worse"},
    # grpc-rapira has its own p99 floor only, so its capacity uses the capacity floor: 2.5 >= 2.5.
    {"name": "grpc capacity uses the capacity floor", "point": point(3, 2.5, 2.0, 3.5), "target": "grpc-rapira", "measure": "capacity", "rounds": 3, "expected": "better"},
    # 1 of 1 pairs in a suite of 1 round, delta -11.0 below zero, 11.0 >= 3.
    {"name": "1 pair in a suite of 1 round", "point": point(1, -11.0, -11.0, -11.0), "target": HELLO, "measure": "p99", "rounds": 1, "expected": "better"},
    # A gap: the run does not have the target, or the measure has 0 pairs.
    {"name": "null point", "point": None, "target": HELLO, "measure": "p99", "rounds": 3, "expected": ""},
    # The deltas would make a paired point worse, but the run measured another base build.
    {"name": "a point between two rigs above every floor is gray", "point": point(3, -15.7, -16.0, -15.0, is_paired=False), "target": "grpc-rapira", "measure": "capacity", "rounds": 3, "expected": ""},
]


def line(text, tone=""):
    """A tooltip line of tooltipLines."""
    return {"text": text, "tone": tone}


# The hover lines of one point: the run, the pull request title when the run has one, the new and the base median in
# the unit of the measure, the delta against the base build of the run with its smallest and largest pair and its pair
# count, and the flags when the point has any. The delta line has the tone of the point. A point between two rigs also
# has the change from the start, with the build, the run label, and the new median of the start, and it is gray.
# The baseline build is ccccccc. The units: capacity in req/s, p99 in ms from us, RSS in MiB from KiB.
TOOLTIP_CASES = [
    {
        # Run c, hello capacity: the start, new 100000 req/s. Its deltas against eeeeeee, +2.6 to +3.4, would give a
        # paired point the tone "better", but the start is a point between two rigs.
        "name": "the start shows the change line and a gray delta",
        "target": "hello-rapira-worker", "measure": "capacity", "index": 0,
        "expected": [
            line("2026-09-25 01:00 UTC - #100"),
            line("Baseline"),
            line("new 100000 vs base 97000 req/s"),
            line("change vs ccccccc in run #100 (100000 req/s) 0.0% (starting point)"),
            line("delta vs eeeeeee +3.0% (min +2.6%, max +3.4%, 3 pairs)"),
        ],
    },
    {
        # Run a, hello capacity: base 100000, new 104000 req/s. 3 of 3 pairs, deltas above zero, 4.0 >= 2.5.
        "name": "capacity, pull request title, better",
        "target": "hello-rapira-worker", "measure": "capacity", "index": 1,
        "expected": [
            line("2026-09-26 01:00 UTC - #101"),
            line("Faster hello"),
            line("new 104000 vs base 100000 req/s"),
            line("delta vs ccccccc +4.0% (min +3.5%, max +4.6%, 3 pairs)", "better"),
        ],
    },
    {
        # Run b, hello p99: 1050 us = 1.05 ms, 1200 us = 1.20 ms. 2 of 3 pairs, so the tone is gray.
        "name": "p99, run without a pull request, 2 pairs",
        "target": "hello-rapira-worker", "measure": "p99", "index": 2,
        "expected": [
            line("2026-09-27 01:00 UTC - bbbbbbb"),
            line("new 1.05 vs base 1.20 ms"),
            line("delta vs ccccccc -12.5% (min -14.0%, max -11.0%, 2 pairs)"),
        ],
    },
    {
        # Run a, hello RSS: 208896 / 1024 = 204.0 MiB, 204800 / 1024 = 200.0 MiB. 3 of 3 pairs, deltas above zero,
        # 2.0 >= 2, and a higher RSS is worse.
        "name": "RSS, worse, one flag",
        "target": "hello-rapira-worker", "measure": "rss", "index": 1,
        "expected": [
            line("2026-09-26 01:00 UTC - #101"),
            line("Faster hello"),
            line("new 204.0 vs base 200.0 MiB"),
            line("delta vs ccccccc +2.0% (min +1.5%, max +2.5%, 3 pairs)", "worse"),
            line("loader_skew"),
        ],
    },
    {
        # Run a, grpc capacity: the deltas -0.8 to 1.2 are on both sides of zero, so the tone is gray.
        "name": "capacity, deltas on both sides of zero, two flags",
        "target": "grpc-rapira", "measure": "capacity", "index": 1,
        "expected": [
            line("2026-09-26 01:00 UTC - #101"),
            line("Faster hello"),
            line("new 150750 vs base 150000 req/s"),
            line("delta vs ccccccc +0.5% (min -0.8%, max +1.2%, 3 pairs)"),
            line("loader_busy, not_saturated"),
        ],
    },
    {
        # Run b, hello dispatcher capacity: 1 of 3 pairs, so the tone is gray.
        "name": "capacity, 1 pair",
        "target": "hello-rapira-dispatcher", "measure": "capacity", "index": 2,
        "expected": [
            line("2026-09-27 01:00 UTC - bbbbbbb"),
            line("new 24000 vs base 25000 req/s"),
            line("delta vs ccccccc -4.0% (min -4.0%, max -4.0%, 1 pair)"),
        ],
    },
]

VISIBLE_CASES = [
    {
        # The board fetches only schema 3 and schema 4 run files, so an entry without the schema key and a smoke entry
        # drop out. Run c has schema 3.
        "name": "old entry and smoke entry dropped, sorted by started",
        "entries": [entry(RUN_B), OLD_ENTRY, entry(RUN_S), entry(RUN_A), entry(RUN_C)],
        "expected": [entry(RUN_C), entry(RUN_A), entry(RUN_B)],
    },
    {
        # A schema 2 run used another method.
        "name": "schema 2 entry dropped",
        "entries": [{**entry(RUN_A), "schema": "rapira-bench-run/2"}, entry(RUN_B)],
        "expected": [entry(RUN_B)],
    },
    {
        "name": "only old entries",
        "entries": [OLD_ENTRY],
        "expected": [],
    },
    {
        "name": "schema 3 and schema 4 entries in order stay the same",
        "entries": [entry(RUN_C), entry(RUN_A), entry(RUN_B)],
        "expected": [entry(RUN_C), entry(RUN_A), entry(RUN_B)],
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
                lines = call_js("tooltipLines", SERIES_CAB, case["target"], case["measure"], case["index"])
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
