"""Changes from the baseline run."""

import unittest
from copy import deepcopy

from tests.test_board_data import HELLO, NODE, RUN_A, call_js


def run(sha, base_sha, base, new, *, pairs=3, measure="capacity"):
    """A run of HELLO with one measure: the base median, the new median, and their paired delta."""
    record = deepcopy(RUN_A)
    record["id"] = sha
    record["rapira"]["sha"] = sha
    record["base"]["sha"] = base_sha
    delta = 100 * (new / base - 1) if pairs else None
    record["summary"] = {HELLO: {measure: {
        "pairs": pairs, "delta_pct": delta, "min_pct": delta, "max_pct": delta,
        "base": base if pairs else None, "new": new if pairs else None, "flags": [],
    }}}
    return record


# A value is 100 x (new / baseline value - 1). The baseline value is the first new median above zero of the target
# and the measure.
CASES = [
    # The product of the factors new / base is 95/105 x 88/80 = 0.9952, but the new median of C is 12% under that of A.
    {"name": "compare the new median with the baseline run, not through the base builds", "runs": [run("A", "outside", 50, 100), run("B", "A", 105, 95), run("C", "B", 80, 88)], "values": [0, -5, -12], "initial": [True, False, False]},
    # The grpc-rapira p99 medians of #150, #152, #154, #155, and #156.
    {"name": "a p99 under the baseline p99 is below zero", "measure": "p99", "runs": [run("A", "outside", 5680, 5798, measure="p99"), run("B", "A", 5940, 5721, measure="p99"), run("C", "B", 5606, 6650, measure="p99"), run("D", "C", 5462, 5912, measure="p99"), run("E", "D", 4222, 4181, measure="p99")], "values": [0, -1.328, 14.6947, 1.9662, -27.8889], "initial": [True, False, False, False, False]},
    {"name": "an unknown base build gives a value", "runs": [run("A", "outside", 50, 100), run("B", "unknown", 100, 110)], "values": [0, 10], "initial": [True, False]},
    {"name": "an A/A rerun of the baseline build cannot move the baseline", "runs": [run("A", "outside", 50, 100), run("A", "A", 98, 102)], "values": [0, 2], "initial": [True, False]},
    {"name": "zero capacity is a complete regression", "runs": [run("A", "outside", 50, 100), run("B", "A", 100, 0)], "values": [0, -100], "initial": [True, False]},
    {"name": "a zero new median cannot be the baseline value", "runs": [run("A", "outside", 50, 0), run("B", "A", 100, 100), run("C", "B", 100, 50)], "values": [-100, 0, -50], "initial": [False, True, False], "baseline": "B"},
    {"name": "missing pairs give a gap", "runs": [run("A", "outside", 50, 100), run("B", "A", 100, 100, pairs=0), run("C", "B", 100, 90)], "values": [0, None, -10], "initial": [True, None, False]},
    {"name": "missing pairs in the baseline run start the measure at its first value", "runs": [run("A", "outside", 50, 100, pairs=0), run("B", "A", 100, 100), run("C", "B", 100, 110)], "values": [None, 0, 10], "initial": [None, True, False], "baseline": "B"},
]

HISTORY = [{"id": str(i)} for i in range(63)]
HISTORY_CASES = [
    {"name": "load the baseline run and the newest 60 runs", "entries": HISTORY, "ids": ["0", *[str(i) for i in range(3, 63)]]},
    {"name": "load the baseline run next to a full display", "entries": HISTORY[:61], "ids": [str(i) for i in range(61)]},
    {"name": "load every run of a history shorter than the display", "entries": HISTORY[:3], "ids": ["0", "1", "2"]},
    {"name": "empty history", "entries": [], "ids": []},
]


@unittest.skipUnless(NODE, "node is not installed")
class BaselineTest(unittest.TestCase):
    def test_changes_from_the_baseline(self):
        for case in CASES:
            with self.subTest(name=case["name"]):
                series = call_js("targetSeries", case["runs"])
                points = series["targets"][HELLO][case.get("measure", "capacity")]
                self.assertEqual(series.get("baseline"), "A")
                for point, expected, initial in zip(points, case["values"], case["initial"], strict=True):
                    if expected is None:
                        self.assertIsNone(point)
                    else:
                        self.assertAlmostEqual(point.get("change_pct"), expected, places=3)
                        self.assertEqual(point.get("initial"), initial)
                        self.assertEqual(point.get("baseline_sha"), case.get("baseline", "A"))

    def test_history_dependencies(self):
        for case in HISTORY_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual([entry["id"] for entry in call_js("requiredRuns", case["entries"])], case["ids"])

    def test_display_limit_keeps_the_baseline_run(self):
        runs = [run("A", "outside", 50, 100)] + [run(str(i), "B", 100, 110) for i in range(60)]
        series = call_js("targetSeries", runs)
        self.assertEqual(series.get("baseline"), "A")
        self.assertEqual(len(series["labels"]), 60)
        for point in series["targets"][HELLO]["capacity"]:
            self.assertAlmostEqual(point["change_pct"], 10)
            self.assertFalse(point["initial"])
            self.assertEqual(point["baseline_sha"], "A")
