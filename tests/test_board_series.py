"""The runs of one board series, and the runs that the board loads."""

import unittest
from copy import deepcopy

from tests.test_board_data import BASE_SHA, NODE, RUN_A, RUN_B, call_js

OTHER_SHA = "ddddddd0000000000000000000000000000000d4"


def changed(run, path, value):
    """A copy of the run with one changed value."""
    copy = deepcopy(run)
    node = copy
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return copy


# The board draws the runs that have the same values as the newest run for: the base build, the rounds, the
# connections, the stages with their rates, the instance types, the loader count, the php.ini text, and the app hashes.
# Run a is #101 and run b is bbbbbbb. Run b is the newest run in every row.
KEY_CASES = [
    # Run a and run b differ in the AMI, the kernel, and the PHP version of the new build.
    {"name": "another AMI, kernel, and PHP of the new build keep the run", "runs": [RUN_A, RUN_B], "labels": ["#101", "bbbbbbb"], "baseline": BASE_SHA},
    {"name": "another base build", "runs": [changed(RUN_A, ("base", "sha"), OTHER_SHA), RUN_B], "labels": ["bbbbbbb"], "baseline": BASE_SHA},
    {"name": "another round count", "runs": [changed(RUN_A, ("suite", "rounds"), 5), RUN_B], "labels": ["bbbbbbb"], "baseline": BASE_SHA},
    {"name": "another connection count", "runs": [changed(RUN_A, ("suite", "connections", "http1"), 2000), RUN_B], "labels": ["bbbbbbb"], "baseline": BASE_SHA},
    {"name": "another rate", "runs": [changed(RUN_A, ("suite", "stages", "rate", "rates", "hello-rapira-worker"), 250000), RUN_B], "labels": ["bbbbbbb"], "baseline": BASE_SHA},
    {"name": "another stage duration", "runs": [changed(RUN_A, ("suite", "stages", "cap", "duration_s"), 30), RUN_B], "labels": ["bbbbbbb"], "baseline": BASE_SHA},
    {"name": "another server type", "runs": [changed(RUN_A, ("rig", "server_type"), "c7a.4xlarge"), RUN_B], "labels": ["bbbbbbb"], "baseline": BASE_SHA},
    {"name": "another loader type", "runs": [changed(RUN_A, ("rig", "loader_type"), "c7a.2xlarge"), RUN_B], "labels": ["bbbbbbb"], "baseline": BASE_SHA},
    {"name": "another loader count", "runs": [changed(RUN_A, ("rig", "loader_count"), 4), RUN_B], "labels": ["bbbbbbb"], "baseline": BASE_SHA},
    {"name": "another php.ini text", "runs": [changed(RUN_A, ("servers", "php_ini"), "opcache.enable=0\n"), RUN_B], "labels": ["bbbbbbb"], "baseline": BASE_SHA},
    {"name": "another app hash", "runs": [changed(RUN_A, ("apps", "apps/hello/worker.php"), "22bb"), RUN_B], "labels": ["bbbbbbb"], "baseline": BASE_SHA},
    # The run between run a and run b has another base build. Run a is older than that run and stays.
    {"name": "a run of another base build between two runs of the series", "runs": [RUN_A, changed(RUN_B, ("base", "sha"), OTHER_SHA), RUN_B], "labels": ["#101", "bbbbbbb"], "baseline": BASE_SHA},
    {"name": "the newest run selects the baseline build", "runs": [RUN_A, changed(RUN_B, ("base", "sha"), OTHER_SHA)], "labels": ["bbbbbbb"], "baseline": OTHER_SHA},
]

# The board loads the newest 60 runs.
HISTORY = [{"id": str(i)} for i in range(63)]
HISTORY_CASES = [
    {"name": "63 runs give the newest 60", "entries": HISTORY, "ids": [str(i) for i in range(3, 63)]},
    {"name": "60 runs give all", "entries": HISTORY[:60], "ids": [str(i) for i in range(60)]},
    {"name": "3 runs give all", "entries": HISTORY[:3], "ids": ["0", "1", "2"]},
    {"name": "no runs", "entries": [], "ids": []},
]


@unittest.skipUnless(NODE, "node is not installed")
class SeriesTest(unittest.TestCase):
    def test_series_runs(self):
        for case in KEY_CASES:
            with self.subTest(name=case["name"]):
                series = call_js("targetSeries", case["runs"])
                self.assertEqual(case["labels"], series["labels"])
                self.assertEqual(case["baseline"], series["baseline"])

    def test_newest_runs(self):
        for case in HISTORY_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(case["ids"], [entry["id"] for entry in call_js("newestRuns", case["entries"])])


if __name__ == "__main__":
    unittest.main()
