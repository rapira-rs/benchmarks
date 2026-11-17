"""Changes from the baseline build."""

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


# The baseline build is the new build of the first run, A. A run with A as its base build gives its paired delta,
# 100 x (new / base - 1) in these runs. Another run gives 100 x (new / start new - 1), where the start is the first new
# median above zero of the target and the measure.
CASES = [
    # The product of the factors new / base, 95/105 x 88/80 = 0.9952, would give -0.48%.
    {"name": "a run of another base build compares its new median with the first run", "runs": [run("A", "outside", 50, 100), run("B", "X", 105, 95), run("C", "B", 80, 88)], "values": [0, -5, -12], "paired": [False, False, False], "initial": [True, False, False]},
    # 95 / 105 = 0.90476.
    {"name": "a run of the baseline build gives its paired delta", "runs": [run("A", "outside", 50, 100), run("B", "A", 105, 95), run("C", "B", 80, 88)], "values": [0, -9.5238, -12], "paired": [False, True, False], "initial": [True, None, False]},
    # The grpc-rapira p99 medians of #150, #152, #154, #155, and #156. #152 measured #150 as its base build: 5721 / 5940.
    # The others: 6650 / 5798, 5912 / 5798, and 4181 / 5798.
    {"name": "the p99 runs of the board from #150 to #156", "measure": "p99", "runs": [run("A", "outside", 5680, 5798, measure="p99"), run("B", "A", 5940, 5721, measure="p99"), run("C", "B", 5606, 6650, measure="p99"), run("D", "C", 5462, 5912, measure="p99"), run("E", "D", 4222, 4181, measure="p99")], "values": [0, -3.6869, 14.6947, 1.9662, -27.8889], "paired": [False, True, False, False, False], "initial": [True, None, False, False, False]},
    # 102 / 98 = 1.040816.
    {"name": "an A/A rerun of the baseline build is a paired point", "runs": [run("A", "outside", 50, 100), run("A", "A", 98, 102)], "values": [0, 4.0816], "paired": [False, True], "initial": [True, None]},
    {"name": "zero capacity is a complete regression", "runs": [run("A", "outside", 50, 100), run("B", "X", 100, 0)], "values": [0, -100], "paired": [False, False], "initial": [True, False]},
    {"name": "a zero new median cannot be the start", "runs": [run("A", "outside", 50, 0), run("B", "X", 100, 100), run("C", "B", 100, 50)], "values": [-100, 0, -50], "paired": [False, False, False], "initial": [False, True, False], "start": "B"},
    {"name": "missing pairs give a gap", "runs": [run("A", "outside", 50, 100), run("B", "X", 100, 100, pairs=0), run("C", "B", 100, 90)], "values": [0, None, -10], "paired": [False, None, False], "initial": [True, None, False]},
    {"name": "missing pairs in the first run start the measure at its first value", "runs": [run("A", "outside", 50, 100, pairs=0), run("B", "X", 100, 100), run("C", "B", 100, 110)], "values": [None, 0, 10], "paired": [None, False, False], "initial": [None, True, False], "start": "B"},
    {"name": "a paired point needs no start", "runs": [run("A", "outside", 50, 100, pairs=0), run("B", "A", 100, 110)], "values": [None, 10], "paired": [None, True], "initial": [None, None]},
]


@unittest.skipUnless(NODE, "node is not installed")
class BaselineTest(unittest.TestCase):
    def test_changes_from_the_baseline(self):
        for case in CASES:
            with self.subTest(name=case["name"]):
                series = call_js("targetSeries", case["runs"])
                points = series["targets"][HELLO][case.get("measure", "capacity")]
                self.assertEqual(series["baseline"], "A")
                for point, expected, paired, initial in zip(points, case["values"], case["paired"], case["initial"], strict=True):
                    if expected is None:
                        self.assertIsNone(point)
                        continue
                    self.assertAlmostEqual(point["change_pct"], expected, places=3)
                    self.assertEqual(point["paired"], paired)
                    if not paired:
                        self.assertEqual(point["initial"], initial)
                        self.assertEqual(point["start_sha"], case.get("start", "A"))


if __name__ == "__main__":
    unittest.main()
