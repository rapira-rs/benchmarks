"""Data transforms of board/app.js, run under node.

The fixture holds three schema 3 run files reduced to the fields the board reads.
"""

import json
import shutil
import subprocess
import unittest
from copy import deepcopy
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
SHA_A = RUN_A["rapira"]["sha"]
SHA_B = RUN_B["rapira"]["sha"]
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


def start(record, sha):
    """The baseline point of a target and a measure: the summary record of the run of the build sha."""
    return {**record, "change_pct": 0, "initial": True, "baseline_sha": sha, "baseline_new": record["new"]}


def later(record, baseline, sha):
    """A point after the baseline point: the change of its new median from the new median of the baseline record."""
    return {**record, "change_pct": 100 * (record["new"] / baseline["new"] - 1), "initial": False, "baseline_sha": sha, "baseline_new": baseline["new"]}


SERIES_CASES = [
    {
        # The first run is the baseline run. A point of run b compares its new median with the new median of run a.
        # grpc-rapira has 0 pairs in run b, so its points there are gaps.
        # hello-rapira-dispatcher is only in run b, so its points in run a are null, and its baseline value is in run b.
        # The targets come in name order. A date is the UTC start of the run to the minute.
        "name": "series from the summary with 0 pairs and a target in one run",
        "runs": [RUN_A, RUN_B],
        "expected": {
            "labels": ["#101", "bbbbbbb"],
            "dates": ["2026-09-26 01:00 UTC", "2026-09-27 01:00 UTC"],
            "links": ["https://github.com/rapira-rs/rapira/pull/101", COMMIT_B],
            "titles": ["Faster hello", ""],
            "baseline": SHA_A,
            "bases": ["ccccccc", "aaaaaaa"],
            "rounds": [3, 3],
            "targets": {
                "grpc-rapira": {
                    "capacity": [start(SUMMARY_A["grpc-rapira"]["capacity"], SHA_A), None],
                    "p99": [start(SUMMARY_A["grpc-rapira"]["p99"], SHA_A), None],
                    "rss": [start(SUMMARY_A["grpc-rapira"]["rss"], SHA_A), None],
                },
                "hello-rapira-dispatcher": {
                    "capacity": [None, start(SUMMARY_B["hello-rapira-dispatcher"]["capacity"], SHA_B)],
                    "p99": [None, start(SUMMARY_B["hello-rapira-dispatcher"]["p99"], SHA_B)],
                    "rss": [None, start(SUMMARY_B["hello-rapira-dispatcher"]["rss"], SHA_B)],
                },
                "hello-rapira-worker": {
                    "capacity": [start(SUMMARY_A["hello-rapira-worker"]["capacity"], SHA_A), later(SUMMARY_B["hello-rapira-worker"]["capacity"], SUMMARY_A["hello-rapira-worker"]["capacity"], SHA_A)],
                    "p99": [start(SUMMARY_A["hello-rapira-worker"]["p99"], SHA_A), later(SUMMARY_B["hello-rapira-worker"]["p99"], SUMMARY_A["hello-rapira-worker"]["p99"], SHA_A)],
                    "rss": [start(SUMMARY_A["hello-rapira-worker"]["rss"], SHA_A), later(SUMMARY_B["hello-rapira-worker"]["rss"], SUMMARY_A["hello-rapira-worker"]["rss"], SHA_A)],
                },
            },
        },
    },
    {
        # An index without schema 3 runs gives an empty board: no labels and no targets.
        "name": "no runs",
        "runs": [],
        "expected": {"labels": [], "dates": [], "links": [], "titles": [], "baseline": "", "bases": [], "rounds": [], "targets": {}},
    },
]

# The series of runs a and b, as the first series case expects it.
SERIES_AB = SERIES_CASES[0]["expected"]


def point(pairs, change_pct):
    """A board point with the fields that tone reads."""
    return {"pairs": pairs, "change_pct": change_pct}


# A point is "better" or "worse" only when it has one pair per round and |change_pct| is at least the noise floor.
# The floors of board/app.js: capacity 8, p99 17, rss 4.5 (percent), and p99 34.5 for grpc-rapira. A higher capacity
# is better. A lower p99 and a lower RSS are better. All other points are "" (gray).
# The target of the rows that do not test a per-target floor.
HELLO = "hello-rapira-worker"
TONE_CASES = [
    {"name": "capacity up above the floor is better", "point": point(3, 9.0), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": "better"},
    {"name": "capacity down above the floor is worse", "point": point(3, -9.0), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": "worse"},
    {"name": "capacity at the floor counts", "point": point(3, 8.0), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": "better"},
    {"name": "capacity under the floor", "point": point(3, 7.9), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": ""},
    {"name": "p99 down above the floor is better", "point": point(3, -17.5), "target": HELLO, "measure": "p99", "rounds": 3, "expected": "better"},
    {"name": "hello p99 at the p99 floor is worse", "point": point(3, 17.0), "target": HELLO, "measure": "p99", "rounds": 3, "expected": "worse"},
    {"name": "hello p99 under the p99 floor", "point": point(3, 16.9), "target": HELLO, "measure": "p99", "rounds": 3, "expected": ""},
    {"name": "rss up at the floor is worse", "point": point(3, 4.5), "target": HELLO, "measure": "rss", "rounds": 3, "expected": "worse"},
    {"name": "rss down above the floor is better", "point": point(3, -5.0), "target": HELLO, "measure": "rss", "rounds": 3, "expected": "better"},
    {"name": "rss under the floor", "point": point(3, -4.4), "target": HELLO, "measure": "rss", "rounds": 3, "expected": ""},
    # 34.4 is above the p99 floor 17, but under the p99 floor of grpc-rapira.
    {"name": "grpc p99 under the grpc floor", "point": point(3, 34.4), "target": "grpc-rapira", "measure": "p99", "rounds": 3, "expected": ""},
    {"name": "grpc p99 at the grpc floor is worse", "point": point(3, 34.5), "target": "grpc-rapira", "measure": "p99", "rounds": 3, "expected": "worse"},
    # grpc-rapira has its own p99 floor only, so its capacity uses the capacity floor.
    {"name": "grpc capacity uses the capacity floor", "point": point(3, 8.0), "target": "grpc-rapira", "measure": "capacity", "rounds": 3, "expected": "better"},
    # One pair was void or dropped, so the point is gray although the change is above the floor.
    {"name": "2 pairs of 3 rounds", "point": point(2, 10.0), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": ""},
    {"name": "1 pair in a suite of 1 round", "point": point(1, -20.0), "target": HELLO, "measure": "p99", "rounds": 1, "expected": "better"},
    # The baseline point is 0%.
    {"name": "the baseline point", "point": point(3, 0), "target": HELLO, "measure": "capacity", "rounds": 3, "expected": ""},
    # A gap: the run does not have the target, or the measure has 0 pairs.
    {"name": "null point", "point": None, "target": HELLO, "measure": "p99", "rounds": 3, "expected": ""},
]


def line(text, tone=""):
    """A tooltip line of tooltipLines."""
    return {"text": text, "tone": tone}


# Run b with a hello capacity 10% above that of run a: 114400 / 104000 = 1.1.
SERIES_UP = deepcopy(SERIES_AB)
SERIES_UP["targets"]["hello-rapira-worker"]["capacity"][1] = later({**SUMMARY_B["hello-rapira-worker"]["capacity"], "new": 114400.0}, SUMMARY_A["hello-rapira-worker"]["capacity"], SHA_A)

# The hover lines of one point: the run, the pull request title when the run has one, the change from the baseline
# build with its new median in the tone of the point, the measured base build, the new and the base median of the run
# in the unit of the measure, the paired delta with its band and pair count, and the flags when the point has any.
# The units: capacity in req/s, p99 in ms from us, RSS in MiB from KiB.
TOOLTIP_CASES = [
    {
        # Run a, hello capacity: base 100000, new 104000 req/s. Run a is the baseline run.
        "name": "capacity, pull request title, baseline point with its paired comparison",
        "target": "hello-rapira-worker", "measure": "capacity", "index": 0,
        "expected": [
            line("2026-09-26 01:00 UTC - #101"),
            line("Faster hello"),
            line("change vs aaaaaaa (104000 req/s) 0.0% (starting point)"),
            line("measured base ccccccc"),
            line("new 104000 vs base 100000 req/s"),
            line("paired delta +4.0% (min +3.5%, max +4.6%, 3 pairs)"),
        ],
    },
    {
        # Run b, hello p99: 1050 us = 1.05 ms, as in run a, so the change is 0.0%. The base build of run b gave
        # 1200 us = 1.20 ms on the same instances, so the paired delta is -12.5%.
        "name": "p99, run without a pull request, the paired delta does not move the change",
        "target": "hello-rapira-worker", "measure": "p99", "index": 1,
        "expected": [
            line("2026-09-27 01:00 UTC - bbbbbbb"),
            line("change vs aaaaaaa (1.05 ms) 0.0%"),
            line("measured base aaaaaaa"),
            line("new 1.05 vs base 1.20 ms"),
            line("paired delta -12.5% (min -14.0%, max -11.0%, 2 pairs)"),
        ],
    },
    {
        # Run b with the hello capacity of SERIES_UP: +10.0% from run a, 3 of 3 pairs, 10.0 >= 8. The paired delta
        # has no tone.
        "name": "capacity above the floor, a tone on the change only",
        "series": SERIES_UP,
        "target": "hello-rapira-worker", "measure": "capacity", "index": 1,
        "expected": [
            line("2026-09-27 01:00 UTC - bbbbbbb"),
            line("change vs aaaaaaa (104000 req/s) +10.0%", "better"),
            line("measured base aaaaaaa"),
            line("new 114400 vs base 104000 req/s"),
            line("paired delta -0.5% (min -1.0%, max +0.2%, 3 pairs)"),
        ],
    },
    {
        # Run a, hello RSS: 208896 / 1024 = 204.0 MiB, 204800 / 1024 = 200.0 MiB.
        "name": "RSS, baseline point, one flag",
        "target": "hello-rapira-worker", "measure": "rss", "index": 0,
        "expected": [
            line("2026-09-26 01:00 UTC - #101"),
            line("Faster hello"),
            line("change vs aaaaaaa (204.0 MiB) 0.0% (starting point)"),
            line("measured base ccccccc"),
            line("new 204.0 vs base 200.0 MiB"),
            line("paired delta +2.0% (min +1.5%, max +2.5%, 3 pairs)"),
            line("loader_skew"),
        ],
    },
    {
        "name": "capacity, baseline point, two flags",
        "target": "grpc-rapira", "measure": "capacity", "index": 0,
        "expected": [
            line("2026-09-26 01:00 UTC - #101"),
            line("Faster hello"),
            line("change vs aaaaaaa (150750 req/s) 0.0% (starting point)"),
            line("measured base ccccccc"),
            line("new 150750 vs base 150000 req/s"),
            line("paired delta +0.5% (min -0.8%, max +1.2%, 3 pairs)"),
            line("loader_busy, not_saturated"),
        ],
    },
    {
        # Run b, hello dispatcher capacity: run a does not have the target, so its baseline value is in run b. 1 pair.
        "name": "capacity of a target that starts after the baseline run, 1 pair",
        "target": "hello-rapira-dispatcher", "measure": "capacity", "index": 1,
        "expected": [
            line("2026-09-27 01:00 UTC - bbbbbbb"),
            line("change vs bbbbbbb (24000 req/s) 0.0% (starting point)"),
            line("measured base aaaaaaa"),
            line("new 24000 vs base 25000 req/s"),
            line("paired delta -4.0% (min -4.0%, max -4.0%, 1 pair)"),
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
                lines = call_js("tooltipLines", case.get("series", SERIES_AB), case["target"], case["measure"], case["index"])
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
