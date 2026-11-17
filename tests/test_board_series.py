"""The runs of one board series."""

import unittest
from copy import deepcopy

from tests.test_board_data import BASELINE_SHA, NODE, RUN_A, RUN_B, RUN_C, call_js

OTHER_SHA = "ddddddd0000000000000000000000000000000d4"


def changed(run, path, value):
    """A copy of the run with one changed value."""
    copy = deepcopy(run)
    node = copy
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return copy


# The board draws every run, and the baseline build is the new build of the first run. A change of the settings keeps
# the runs before it on the board. Run c is #100 with the new build ccccccc, run a is #101, and run b is bbbbbbb.
SETTINGS_CASES = [
    # Run c and run b differ in the schema, the base build, the AMI, the kernel, and the PHP version of the new build.
    {"name": "another schema, base build, AMI, kernel, and PHP keep the run", "runs": [RUN_C, RUN_B], "labels": ["#100", "bbbbbbb"]},
    {"name": "another base build of the newest run keeps the runs", "runs": [RUN_C, changed(RUN_B, ("base", "sha"), OTHER_SHA)], "labels": ["#100", "bbbbbbb"]},
    {"name": "another round count of the newest run keeps the runs", "runs": [RUN_C, RUN_A, changed(RUN_B, ("suite", "rounds"), 5)], "labels": ["#100", "#101", "bbbbbbb"]},
    {"name": "another rate of the newest run keeps the runs", "runs": [RUN_C, RUN_A, changed(RUN_B, ("suite", "stages", "rate", "rates", "hello-rapira-worker"), 250000)], "labels": ["#100", "#101", "bbbbbbb"]},
    {"name": "another app hash of the newest run keeps the runs", "runs": [RUN_C, RUN_A, changed(RUN_B, ("apps", "apps/hello/worker.php"), "22bb")], "labels": ["#100", "#101", "bbbbbbb"]},
    {"name": "other settings of the first run keep it as the baseline run", "runs": [changed(RUN_C, ("rig", "loader_count"), 4), RUN_A, RUN_B], "labels": ["#100", "#101", "bbbbbbb"]},
]


@unittest.skipUnless(NODE, "node is not installed")
class SeriesTest(unittest.TestCase):
    def test_series_runs(self):
        for case in SETTINGS_CASES:
            with self.subTest(name=case["name"]):
                series = call_js("targetSeries", case["runs"])
                self.assertEqual(case["labels"], series["labels"])
                self.assertEqual(BASELINE_SHA, series["baseline"])


if __name__ == "__main__":
    unittest.main()
