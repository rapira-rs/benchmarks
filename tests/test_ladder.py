import unittest
from types import SimpleNamespace

from rig.ladder import MAX_STAGES, cell_numbers, evaluate_stage, stage_rates

NO_ERRORS = {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0}


def merged(achieved_rps: float, **errors: int) -> SimpleNamespace:
    # evaluate_stage reads only these two fields of rig.merge.Merged.
    return SimpleNamespace(achieved_rps=achieved_rps, errors=NO_ERRORS | errors)


def stage(rate: int, passed: bool, successful_rps: float, p50: float, p99: float) -> dict:
    return {
        "rate": rate,
        "duration_s": 20,
        "pass": passed,
        "merged": {"successful_rps": successful_rps},
        "latency_us": {"p50": p50, "p90": p99, "p99": p99, "p999": p99, "max": p99},
    }


STAGE_RATES_CASES = [
    {
        # 10000 * 2**19 = 5242880000.
        "name": "hello floor",
        "floor": 10000,
        "first": 10000,
        "second": 20000,
        "last": 5242880000,
    },
    {
        # 5000 * 2**19 = 2621440000.
        "name": "laravel floor",
        "floor": 5000,
        "first": 5000,
        "second": 10000,
        "last": 2621440000,
    },
]

EVALUATE_CASES = [
    {"name": "achieved exactly 95 percent passes", "rate": 10000, "merged": merged(9500.0), "expected": (True, None)},
    {"name": "achieved above the rate passes", "rate": 10000, "merged": merged(10003.5), "expected": (True, None)},
    {
        "name": "achieved 9400 fails",
        "rate": 10000,
        "merged": merged(9400.0),
        "expected": (False, "achieved 9400 req/s under 95% of 10000"),
    },
    {
        # 9499.95 is under 9500; the reason truncates it to 9499.
        "name": "achieved just under 95 percent fails",
        "rate": 10000,
        "merged": merged(9499.95),
        "expected": (False, "achieved 9499 req/s under 95% of 10000"),
    },
    {
        "name": "status errors at the full rate",
        "rate": 10000,
        "merged": merged(10000.0, status=12),
        "expected": (False, "status errors: 12"),
    },
    {"name": "connect errors", "rate": 10000, "merged": merged(10000.0, connect=1), "expected": (False, "connect errors: 1")},
    {"name": "read errors", "rate": 10000, "merged": merged(10000.0, read=3), "expected": (False, "read errors: 3")},
    {"name": "write errors", "rate": 10000, "merged": merged(10000.0, write=2), "expected": (False, "write errors: 2")},
    {"name": "timeouts", "rate": 10000, "merged": merged(10000.0, timeout=4), "expected": (False, "timeouts: 4")},
    {"name": "dropped iterations", "rate": 10000, "merged": merged(10000.0, dropped=7), "expected": (False, "dropped iterations: 7")},
    {
        "name": "a low rate is reported before errors",
        "rate": 20000,
        "merged": merged(12000.0, status=40),
        "expected": (False, "achieved 12000 req/s under 95% of 20000"),
    },
    {
        "name": "status errors are reported before connect errors",
        "rate": 10000,
        "merged": merged(10000.0, connect=5, status=2),
        "expected": (False, "status errors: 2"),
    },
]

CELL_CASES = [
    {
        "name": "no stages",
        "stages": [],
        "expected": {"held": None, "peak": None, "unloaded": None, "ladder_exhausted": False},
    },
    {
        "name": "first stage fails",
        "stages": [stage(10000, False, 6200.5, 900.0, 250000.0)],
        "expected": {
            "held": None,
            "peak": 6200.5,
            "unloaded": {"p50": 900.0, "p99": 250000.0},
            "ladder_exhausted": False,
        },
    },
    {
        "name": "two passes then a fail",
        "stages": [
            stage(10000, True, 10000.0, 700.0, 1200.0),
            stage(20000, True, 20000.0, 710.0, 1500.0),
            stage(40000, False, 31000.25, 90000.0, 400000.0),
        ],
        "expected": {
            "held": {"rate": 20000, "stage": 1},
            "peak": 31000.25,
            "unloaded": {"p50": 700.0, "p99": 1200.0},
            "ladder_exhausted": False,
        },
    },
    {
        # An interrupted cell: every stage passed, but the ladder stopped before the cap.
        "name": "three passes and no fail",
        "stages": [
            stage(10000, True, 10000.0, 700.0, 1200.0),
            stage(20000, True, 20000.0, 710.0, 1500.0),
            stage(40000, True, 40000.0, 720.0, 1800.0),
        ],
        "expected": {
            "held": {"rate": 40000, "stage": 2},
            "peak": None,
            "unloaded": {"p50": 700.0, "p99": 1200.0},
            "ladder_exhausted": False,
        },
    },
    {
        # The last of 20 stages is index 19 at 10000 * 2**19 = 5242880000.
        "name": "every stage passes up to the cap",
        "stages": [stage(10000 * 2**i, True, 10000.0 * 2**i, 650.0, 1100.0) for i in range(20)],
        "expected": {
            "held": {"rate": 5242880000, "stage": 19},
            "peak": None,
            "unloaded": {"p50": 650.0, "p99": 1100.0},
            "ladder_exhausted": True,
        },
    },
]


class StageRatesTest(unittest.TestCase):
    def test_geometric_list(self):
        for case in STAGE_RATES_CASES:
            with self.subTest(name=case["name"]):
                rates = stage_rates(case["floor"])
                self.assertEqual(len(rates), MAX_STAGES)
                self.assertEqual(rates[0], case["first"])
                self.assertEqual(rates[1], case["second"])
                self.assertEqual(rates[-1], case["last"])


class EvaluateStageTest(unittest.TestCase):
    def test_pass_rule(self):
        for case in EVALUATE_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(evaluate_stage(case["rate"], case["merged"]), case["expected"])


class CellNumbersTest(unittest.TestCase):
    def test_held_peak_unloaded(self):
        for case in CELL_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(cell_numbers(case["stages"]), case["expected"])


if __name__ == "__main__":
    unittest.main()
