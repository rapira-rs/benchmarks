#!/usr/bin/env python3
"""Print the RESULT line of one h2load run from its per-request log.

    h2load-report.py LOG_FILE DURATION_S

h2load writes one line per request that ended in the measured window: the start time in
microseconds since the epoch, the HTTP status or -1 for a failed stream, and the response time in
microseconds, separated by tabs. The RESULT line has the shape of loader/wrk2-report.lua. h2load
reports no bytes and no connect, read, write, timeout, or dropped counts, so these are 0. A line
whose status is not 200 counts as a status error.
"""

import json
import math
import sys
from fractions import Fraction

PERCENTILES = (("p50", 50), ("p90", 90), ("p95", 95), ("p99", 99), ("p999", Fraction(999, 10)))


def percentile(sorted_values, pct):
    """The nearest-rank percentile: the value at the 1-based rank ceil(pct / 100 * n).

    pct is exact (int or Fraction) so the rank is exact too. A float pct such as 99.9 can put
    the rank a fraction above an integer boundary, and ceil then rounds up to the wrong rank.
    """
    rank = max(1, math.ceil(Fraction(pct) * len(sorted_values) / 100))
    return sorted_values[rank - 1]


def report(lines, duration_s):
    times = []
    status_errors = 0
    for line in lines:
        if not line.strip():
            continue
        _, status, response_us = line.split("\t")
        if int(status) != 200:
            status_errors += 1
        times.append(int(response_us))
    times.sort()
    n = len(times)
    latency = {"mean": sum(times) / n if n else 0}
    for name, pct in PERCENTILES:
        latency[name] = percentile(times, pct) if n else 0
    latency["max"] = times[-1] if n else 0
    return {
        "duration_us": duration_s * 1000000,
        "requests": n,
        "bytes": 0,
        "errors": {"connect": 0, "read": 0, "write": 0, "status": status_errors, "timeout": 0, "dropped": 0},
        "latency_us": latency,
        "requests_per_sec": round(n / duration_s, 3),
    }


def main(argv):
    path, duration_s = argv[1], int(argv[2])
    with open(path) as f:
        lines = f.read().splitlines()
    print("RESULT " + json.dumps(report(lines, duration_s)))


if __name__ == "__main__":
    main(sys.argv)
