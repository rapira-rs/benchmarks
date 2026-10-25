"""Fixed-baseline changes from recorded build comparisons."""

import unittest
from copy import deepcopy

from tests.test_board_data import HELLO, NODE, RUN_A, call_js


def run(sha, base, delta, *, pairs=3, measure="capacity"):
    record = deepcopy(RUN_A)
    record["id"] = sha
    record["rapira"]["sha"] = sha
    record["base"]["sha"] = base
    record["summary"] = {HELLO: {measure: {
        "pairs": pairs, "delta_pct": delta if pairs else None,
        "min_pct": delta if pairs else None, "max_pct": delta if pairs else None,
        "base": 100, "new": 100 + delta, "flags": [],
    }}}
    return record


# A is the origin. B/A = 1.10 and C/B = 1.10 give C/A = 1.21.
CASES = [
    {"name": "compound changes from the first build", "runs": [run("A", "outside", 50), run("B", "A", 10), run("C", "B", 10)], "values": [0, 10, 21], "estimates": [False, False, True]},
    {"name": "compound latency reductions", "measure": "p99", "runs": [run("A", "outside", 50, measure="p99"), run("B", "A", -10, measure="p99"), run("C", "B", -10, measure="p99")], "values": [0, -10, -19], "estimates": [False, False, True]},
    {"name": "RSS returns to the initial value", "measure": "rss", "runs": [run("A", "outside", 50, measure="rss"), run("B", "A", -20, measure="rss"), run("C", "B", 25, measure="rss")], "values": [0, -20, 0], "estimates": [False, False, True]},
    {"name": "follow the recorded base rather than the preceding point", "runs": [run("A", "outside", 50), run("B", "A", 10), run("C", "A", 20), run("D", "B", 10)], "values": [0, 10, 20, 21], "estimates": [False, False, False, True]},
    {"name": "zero capacity remains a complete regression", "runs": [run("A", "outside", 50), run("B", "A", -100)], "values": [0, -100], "estimates": [False, False]},
    {"name": "missing pairs break the connection until a direct comparison", "runs": [run("A", "outside", 50), run("B", "A", 10, pairs=0), run("C", "B", 10), run("D", "A", 10)], "values": [0, None, None, 10], "estimates": [False, None, None, False]},
    {"name": "an unknown base gives a gap", "runs": [run("A", "outside", 50), run("B", "unknown", 10)], "values": [0, None], "estimates": [False, None]},
    {"name": "an A/A rerun cannot move the origin", "runs": [run("A", "outside", 50), run("A", "A", 5), run("B", "A", 10)], "values": [0, 5, 10], "estimates": [False, False, False]},
    {"name": "an unconnected A/A run cannot establish another origin", "runs": [run("A", "outside", 50), run("B", "B", 5)], "values": [0, None], "estimates": [False, None]},
    {"name": "missing initial pairs keep a gap but permit a direct comparison", "runs": [run("A", "outside", 50, pairs=0), run("B", "A", 10), run("C", "B", 10)], "values": [None, 10, 21], "estimates": [None, False, True]},
]

CHAIN = [{"id": str(i), "rapira_sha": str(i), "base_sha": str(i - 1)} for i in range(63)]
DIRECT = [{**entry, "base_sha": "0"} for entry in CHAIN]
HISTORY_CASES = [
    {"name": "load the whole comparison chain", "entries": CHAIN, "ids": [str(i) for i in range(63)]},
    {"name": "direct comparisons need only the origin and newest 60 runs", "entries": DIRECT, "ids": ["0", *[str(i) for i in range(3, 63)]]},
    {"name": "load an explicit older base outside the display", "entries": [*DIRECT[:-1], {**DIRECT[-1], "base_sha": "1"}], "ids": ["0", "1", *[str(i) for i in range(3, 63)]]},
    {"name": "empty history", "entries": [], "ids": []},
]


@unittest.skipUnless(NODE, "node is not installed")
class BaselineTest(unittest.TestCase):
    def test_changes_from_a(self):
        for case in CASES:
            with self.subTest(name=case["name"]):
                series = call_js("targetSeries", case["runs"])
                measure = case.get("measure", "capacity")
                points = series["targets"][HELLO][measure]
                self.assertEqual(series.get("baseline"), "A")
                for point, expected, estimated in zip(points, case["values"], case["estimates"], strict=True):
                    if expected is None:
                        self.assertIsNone(point)
                    else:
                        self.assertAlmostEqual(point.get("change_pct"), expected)
                        self.assertEqual(point.get("estimated"), estimated)
                        if estimated:
                            self.assertEqual(call_js("tone", point, HELLO, measure, 3), "")

    def test_history_dependencies(self):
        for case in HISTORY_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual([entry["id"] for entry in call_js("requiredRuns", case["entries"])], case["ids"])

    def test_display_limit_keeps_a_and_older_comparisons(self):
        runs = [run("A", "outside", 50), run("B", "A", 10)] + [run(str(i), "B", 10) for i in range(61)]
        series = call_js("targetSeries", runs)
        self.assertEqual(series.get("baseline"), "A")
        self.assertEqual(len(series["labels"]), 60)
        for point in series["targets"][HELLO]["capacity"]:
            self.assertAlmostEqual(point["change_pct"], 21)
            self.assertTrue(point["estimated"])
