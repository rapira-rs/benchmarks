import unittest

from rig.merge import LateLoader, LoaderRecord, Merged, MissingLoader, merge, parse_result

# The RESULT line of the contract.
CONTRACT_LINE = (
    'RESULT {"tool": "wrk2", "late_ms": 0, "duration_us": 20000867, "requests": 39989, "bytes": 5038614,'
    ' "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},'
    ' "latency_us": {"mean": 689.5, "p50": 689, "p90": 1111, "p95": 1175, "p99": 1264, "p999": 1351, "max": 2822},'
    ' "requests_per_sec": 1999.363}'
)

WRK2_TEXT = (
    "Running 20s test @ http://10.0.1.10:8080/?name=you\n"
    "  4 threads and 64 connections\n"
    "  Thread calibration: mean lat.: 0.702ms, rate sampling interval: 10ms\n"
    "Requests/sec:   1999.36\n"
    "Transfer/sec:    246.01KB\n"
)

NO_ERRORS = {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0}


def record(loader: str, requests: int, bytes_: int, latency: dict, late_ms: int = 0, **errors: int) -> LoaderRecord:
    return LoaderRecord(
        loader=loader,
        tool="wrk2",
        late_ms=late_ms,
        duration_us=20000867,
        requests=requests,
        bytes=bytes_,
        errors=NO_ERRORS | errors,
        latency_us=latency,
        requests_per_sec=requests / 20,
    )


CONTRACT_RECORD = LoaderRecord(
    loader="loader-1",
    tool="wrk2",
    late_ms=0,
    duration_us=20000867,
    requests=39989,
    bytes=5038614,
    errors=NO_ERRORS,
    latency_us={"mean": 689.5, "p50": 689.0, "p90": 1111.0, "p95": 1175.0, "p99": 1264.0, "p999": 1351.0, "max": 2822.0},
    requests_per_sec=1999.363,
)

PARSE_CASES = [
    {"name": "wrk2 output then the RESULT line", "text": WRK2_TEXT + CONTRACT_LINE + "\n", "expected": CONTRACT_RECORD},
    {
        "name": "the last RESULT line wins",
        "text": CONTRACT_LINE.replace('"requests": 39989', '"requests": 1') + "\n" + WRK2_TEXT + CONTRACT_LINE + "\n",
        "expected": CONTRACT_RECORD,
    },
    {"name": "no RESULT line", "text": WRK2_TEXT, "expected": None},
    {"name": "RESULT inside a line is not a RESULT line", "text": "log: RESULT {}\n", "expected": None},
]

PARSE_ERROR_CASES = [
    {"name": "invalid JSON", "text": "RESULT {\"tool\": \"wrk2\",\n"},
    {"name": "no requests key", "text": CONTRACT_LINE.replace('"requests": 39989, ', "")},
    {"name": "no dropped counter", "text": CONTRACT_LINE.replace(', "dropped": 0', "")},
    {"name": "no p999 percentile", "text": CONTRACT_LINE.replace('"p999": 1351, ', "")},
]

LOADER_1 = CONTRACT_RECORD
LOADER_2 = record(
    "loader-2", 40011, 5041386,
    {"mean": 701.0, "p50": 695.0, "p90": 1120.0, "p95": 1190.0, "p99": 1301.0, "p999": 1402.0, "max": 3010.0},
    late_ms=12,
)
# late_ms at the limit of 1000 ms is valid.
LOADER_3 = record(
    "loader-3", 39800, 5014800,
    {"mean": 650.25, "p50": 670.0, "p90": 1100.0, "p95": 1180.0, "p99": 1250.0, "p999": 1500.0, "max": 2500.0},
    late_ms=1000, status=7, read=2,
)
LOADER_4 = record(
    "loader-4", 40200, 5065200,
    {"mean": 720.0, "p50": 700.0, "p90": 1090.0, "p95": 1170.0, "p99": 1280.0, "p999": 1340.0, "max": 4100.0},
    late_ms=3, connect=1,
)
ZERO = {"mean": 0.0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0, "p999": 0.0, "max": 0.0}

MERGE_CASES = [
    {
        # requests: 39989 + 40011 + 39800 + 40200 = 160000, and 160000 / 20 = 8000 req/s over the 20 s window.
        # bytes: 5038614 + 5041386 + 5014800 + 5065200 = 20160000.
        # successful: 160000 - 7 status errors = 159993.
        # mean: (39989 * 689.5 + 40011 * 701 + 39800 * 650.25 + 40200 * 720) / 160000 = 110444076.5 / 160000.
        # Each percentile is the maximum of the four loaders.
        "name": "four loaders",
        "records": {"loader-1": LOADER_1, "loader-2": LOADER_2, "loader-3": LOADER_3, "loader-4": LOADER_4},
        "window_s": 20,
        "expected": Merged(
            requests=160000,
            successful=159993,
            bytes=20160000,
            errors={"connect": 1, "read": 2, "write": 0, "status": 7, "timeout": 0, "dropped": 0},
            achieved_rps=8000.0,
            successful_rps=159993 / 20,
            latency_us={
                "mean": 110444076.5 / 160000,
                "p50": 700.0,
                "p90": 1120.0,
                "p95": 1190.0,
                "p99": 1301.0,
                "p999": 1500.0,
                "max": 4100.0,
            },
        ),
    },
    {
        "name": "no requests on any loader",
        "records": {"loader-1": record("loader-1", 0, 0, ZERO), "loader-2": record("loader-2", 0, 0, ZERO)},
        "window_s": 20,
        "expected": Merged(
            requests=0,
            successful=0,
            bytes=0,
            errors=NO_ERRORS,
            achieved_rps=0.0,
            successful_rps=0.0,
            latency_us=ZERO,
        ),
    },
]

MERGE_ERROR_CASES = [
    {
        "name": "a loader without a RESULT line",
        "records": {"loader-1": LOADER_1, "loader-2": LOADER_2, "loader-3": None, "loader-4": LOADER_4},
        "error": MissingLoader,
        "message": "loader loader-3 returned no RESULT line",
    },
    {
        "name": "a loader 1001 ms late",
        "records": {
            "loader-1": LOADER_1,
            "loader-2": record("loader-2", 40011, 5041386, LOADER_2.latency_us, late_ms=1001),
            "loader-3": LOADER_3,
            "loader-4": LOADER_4,
        },
        "error": LateLoader,
        "message": "loader loader-2 started 1001 ms late",
    },
]


class ParseResultTest(unittest.TestCase):
    def test_parse(self):
        for case in PARSE_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(parse_result(case["text"], "loader-1"), case["expected"])

    def test_invalid_line(self):
        for case in PARSE_ERROR_CASES:
            with self.subTest(name=case["name"]):
                with self.assertRaises(ValueError):
                    parse_result(case["text"], "loader-1")


class MergeTest(unittest.TestCase):
    def test_merge(self):
        for case in MERGE_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(merge(case["records"], case["window_s"]), case["expected"])

    def test_invalid_stage(self):
        for case in MERGE_ERROR_CASES:
            with self.subTest(name=case["name"]):
                with self.assertRaises(case["error"]) as ctx:
                    merge(case["records"], 20)
                self.assertEqual(str(ctx.exception), case["message"])


if __name__ == "__main__":
    unittest.main()
