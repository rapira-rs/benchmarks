"""Tests of loader/h2load-report.py on per-request logs."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "loader" / "h2load-report.py"

NO_ERRORS = {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0}


def line(start_us, status, response_us):
    return f"{start_us}\t{status}\t{response_us}\n"


# The nearest-rank percentile of n sorted values is the value at rank ceil(p / 100 * n), 1-based.
CASES = [
    {
        # Four requests in 60 s: 4 / 60 = 0.067 req/s with 3 decimals. Sorted times 500, 700, 900, 1000:
        # p50 is rank 2 (700), p90 is rank 4 (1000), and the mean is 3100 / 4 = 775. The -1 line is
        # a failed stream: it counts as a request and as a status error, and its time is in the list.
        "name": "contract sample with a failed stream",
        "log": line(1700000000000000, 200, 500) + line(1700000000000100, 200, 900) + line(1700000000000200, -1, 1000) + line(1700000000000300, 200, 700),
        "duration_s": 60,
        "result": {
            "duration_us": 60000000,
            "requests": 4,
            "bytes": 0,
            "errors": NO_ERRORS | {"status": 1},
            "latency_us": {"mean": 775.0, "p50": 700, "p90": 1000, "p95": 1000, "p99": 1000, "p999": 1000, "max": 1000},
            "requests_per_sec": 0.067,
        },
    },
    {
        "name": "empty log",
        "log": "",
        "duration_s": 60,
        "result": {
            "duration_us": 60000000,
            "requests": 0,
            "bytes": 0,
            "errors": NO_ERRORS,
            "latency_us": {"mean": 0, "p50": 0, "p90": 0, "p95": 0, "p99": 0, "p999": 0, "max": 0},
            "requests_per_sec": 0.0,
        },
    },
    {
        # One request: every percentile is that value, and 1 / 30 = 0.033 req/s.
        "name": "one request",
        "log": line(1700000000000000, 200, 420),
        "duration_s": 30,
        "result": {
            "duration_us": 30000000,
            "requests": 1,
            "bytes": 0,
            "errors": NO_ERRORS,
            "latency_us": {"mean": 420.0, "p50": 420, "p90": 420, "p95": 420, "p99": 420, "p999": 420, "max": 420},
            "requests_per_sec": 0.033,
        },
    },
    {
        # A 503 and a 404 are status errors. 1000 requests: p99 is rank 990, p999 is rank 999.
        # The times are 1 to 1000 in reverse order, so the parser must sort them.
        "name": "thousand requests in reverse order with two error statuses",
        "log": "".join(line(1700000000000000 + i, 503 if i == 0 else 404 if i == 1 else 200, 1000 - i) for i in range(1000)),
        "duration_s": 60,
        "result": {
            "duration_us": 60000000,
            "requests": 1000,
            "bytes": 0,
            "errors": NO_ERRORS | {"status": 2},
            "latency_us": {"mean": 500.5, "p50": 500, "p90": 900, "p95": 950, "p99": 990, "p999": 999, "max": 1000},
            "requests_per_sec": 16.667,
        },
    },
    {
        # A blank last line does not count.
        "name": "trailing blank line",
        "log": line(1700000000000000, 200, 100) + "\n",
        "duration_s": 60,
        "result": {
            "duration_us": 60000000,
            "requests": 1,
            "bytes": 0,
            "errors": NO_ERRORS,
            "latency_us": {"mean": 100.0, "p50": 100, "p90": 100, "p95": 100, "p99": 100, "p999": 100, "max": 100},
            "requests_per_sec": 0.017,
        },
    },
]

KEY_ORDER = ["duration_us", "requests", "bytes", "errors", "latency_us", "requests_per_sec"]


class H2loadReportTest(unittest.TestCase):
    def test_result_line(self):
        for case in CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                log = Path(tmp) / "h2load.log"
                log.write_text(case["log"])
                proc = subprocess.run([sys.executable, str(REPORT), str(log), str(case["duration_s"])], capture_output=True, text=True, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                lines = proc.stdout.splitlines()
                self.assertEqual(len(lines), 1)
                self.assertTrue(lines[0].startswith("RESULT "))
                result = json.loads(lines[0][len("RESULT "):])
                self.assertEqual(result, case["result"])
                self.assertEqual(list(result), KEY_ORDER)


if __name__ == "__main__":
    unittest.main()
