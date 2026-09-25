# Constant-rate benchmark implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the rate ladder with one constant-rate stage per target, bench rapira only on six targets (hello in three modes, the dispatcher with the static middleware, the Yii3 app-api on the dispatcher, gRPC echo), report the p99 and the RSS next to the achieved rate, and label the board by the merged pull request.

**Architecture:** The Python driver in `rig/` keeps its box protocol over ssh and its run file discipline; the ladder loop becomes one stage, the cell record becomes flat, and the run file schema moves to `rapira-bench-run/2`. On the loader, `box/load.sh` runs wrk2 for HTTP targets and h2load for the gRPC target, and a new stdlib script turns the h2load per-request log into the same `RESULT` line. Provisioning installs the Yii3 app with Valkey and the pure PHP protobuf runtime, and nothing else. The board draws two charts, p99 and RSS, with one line per target.

**Tech Stack:** Python 3.11 standard library, bash, wrk2, h2load (nghttp2), Terraform, GitHub Actions, Chart.js 4 (vendored), PHP 8.5 and Composer for the Yii3 app.

**Spec:** `docs/superpowers/specs/2026-09-25-constant-rate-design.md`. The plan argues from the spec. The spec was corrected on 2026-09-25 with the facts these tasks rely on: Valkey for the Yii3 app, the h2load log format, the counted window per tool, and the removal of the base build.

**Branch:** Work on `feat/constant-rate` from `main` and open one pull request. The owner allowed direct commits to main for documentation only.

## Global Constraints

- Python: the standard library only, Python 3.11 or later. No new dependencies anywhere. h2load (the `nghttp2` package) and Valkey (the `valkey` package) are Fedora packages, wrk2 stays at its pinned commit.
- Every commit is signed: `git commit -s -S`. Conventional Commits 1.0.0. No `Co-authored-by`, no `Generated with`, no AI attribution in commits, the PR, or comments.
- Tests: flat case tables with a `name` field, `unittest`, `subTest` per case. Expected values come from the spec or are computed by hand in a comment. No smoke tests, no assertion of a default. `make test` runs `python3 -m unittest discover -s tests -t .` and must pass at the end of every task.
- Text: ASD-STE100 Simplified Technical English in every comment, docstring, and document. No em dashes or en dashes anywhere, a colon or a plain dash instead. Docs: one paragraph is one line, one bullet is one line. Comments say what or why, never "previously", "instead of", or numbered steps. No all-caps emphasis.
- Bash scripts stay simple: `set -euo pipefail`, no rarely used features. Python replaces any logic that would need `awk` tricks.
- Words: a "target" is the thing under test, a "cell" is one target in one round, a "stage" is the one load run of a cell. Never "leg", never "ladder" outside the notes about the old method.
- Delete every file, function, target, package, and document line that the six targets do not need. Nothing keeps compatibility with the ladder run format `rapira-bench-run/1`.
- Numbers of the design that every task uses verbatim: HTTP rate 250000 req/s, gRPC rate 100000 req/s, HTTP connections 5000, gRPC connections 100, gRPC streams 100, warm-up 10 s, duration 60 s, `held` tolerance 95%, loader `c7a.2xlarge`, loader count 1, server `c7a.8xlarge`.
- The box protocol of `box/target.sh` (`start TAG SERVER PROCS BINARY_DIR ARGS...`, `stop|probe|mem|log TAG SERVER`) and of `box/probe.sh` do not change. `box/load.sh` changes as Task 2 states.

## Review Focus

- A `-1` status line in the h2load log (a failed stream): it must count as a request with a status error and its time must enter the latency percentiles, so an overloaded gRPC target shows in `held` and in the p99 (Task 1).
- The gRPC row counts 60 s of requests and the HTTP rows 70 s: `achieved_rps` must divide by the window the tool counted, or every gRPC cell reads 86% of its rate and never holds (Task 5).
- A rapira WARN line found after a complete stage: the cell must become void with null numbers, not an ok cell with numbers and a reason (Task 5).
- A manual run without a pull request: the run file carries `pr: null`, the index entry `pr: null`, the board label is the sha and the click opens the commit (Tasks 5, 6, 7).
- A suite rate or connection count that the loader count does not divide: the driver must refuse the suite before it creates a run directory, because a per-loader rate that drops the remainder would silently under-load the target (Task 5).

---

### Task 1: The h2load reporter

**Files:**
- Create: `loader/h2load-report.py`
- Test: `tests/test_h2load_report.py`

**Interfaces:**
- Consumes: the h2load `--log-file` format, three tab separated columns per line: the start time in microseconds since the epoch, the HTTP status or `-1` for a failed stream, the response time in microseconds. h2load writes a line only for a request that ended in the measured window.
- Produces: `python3 loader/h2load-report.py LOG_FILE DURATION_S` prints one line `RESULT {json}` with the keys of `loader/wrk2-report.lua` in the same order: `duration_us`, `requests`, `bytes`, `errors` (`connect`, `read`, `write`, `status`, `timeout`, `dropped`), `latency_us` (`mean`, `p50`, `p90`, `p95`, `p99`, `p999`, `max`), `requests_per_sec`. Task 2 appends this line to the h2load output.

- [ ] **Step 1: Write the failing test**

Create `tests/test_h2load_report.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_h2load_report -v`
Expected: FAIL, every case, because `loader/h2load-report.py` does not exist (the subprocess exits with 2).

- [ ] **Step 3: Write the reporter**

Create `loader/h2load-report.py`:

```python
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

PERCENTILES = (("p50", 50), ("p90", 90), ("p95", 95), ("p99", 99), ("p999", 99.9))


def percentile(sorted_values, pct):
    """The nearest-rank percentile: the value at the 1-based rank ceil(pct / 100 * n)."""
    rank = max(1, math.ceil(pct / 100 * len(sorted_values)))
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_h2load_report -v`
Expected: PASS, 5 subtests.

- [ ] **Step 5: Commit**

```bash
git add loader/h2load-report.py tests/test_h2load_report.py
git commit -s -S -m "feat: report an h2load run as a RESULT line"
```

---

### Task 2: load.sh runs wrk2 with a warm-up and h2load for gRPC

**Files:**
- Modify: `box/load.sh` (rewrite)
- Delete: `loader/k6-grpc.js`
- Test: `tests/test_load.py`

**Interfaces:**
- Consumes: `loader/h2load-report.py LOG_FILE DURATION_S` from Task 1; `loader/wrk2-report.lua` unchanged.
- Produces: `load.sh wrk2 EPOCH RATE THREADS CONNS WARMUP_S DURATION_S URL [METHOD] [BODY_FILE] [HEADER...]` runs wrk2 for `WARMUP_S + DURATION_S` seconds; `load.sh h2load EPOCH RATE THREADS CONNS STREAMS WARMUP_S DURATION_S URL BODY_FILE` runs h2load with `--rps RATE / CONNS`, `--warm-up-time WARMUP_S`, `-D DURATION_S`, the gRPC headers, and appends the `RESULT` line of the reporter. Both print the tool output with the `RESULT` line extended by `tool` and `late_ms`. A failed tool leaves no `RESULT` line. Task 5 builds these command lines.

- [ ] **Step 1: Write the failing tests**

Replace the top of `tests/test_load.py` down to the `LoadScriptTest` class (keep the `Wrk2ReportTest` part of the file as it is) with:

```python
"""Tests for box/load.sh with a fake wrk2 and a fake h2load, and for loader/wrk2-report.lua under luajit."""

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOAD = ROOT / "box" / "load.sh"
REPORT = ROOT / "loader" / "wrk2-report.lua"

URL = "http://10.0.1.5:8080/?name=you"
GRPC_URL = "http://10.0.1.5:8080/bench.v1.EchoService/Echo"
BODY_FILE = str(ROOT / "apps" / "grpc" / "echo.grpc")

# The RESULT line of the rig contract without tool and late_ms, as the wrk2 reporter prints it.
REPORTER_RESULT = {
    "duration_us": 70000867,
    "requests": 17499989,
    "bytes": 2204998614,
    "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
    "latency_us": {"mean": 689.5, "p50": 689, "p90": 1111, "p95": 1175, "p99": 1264, "p999": 1351, "max": 2822},
    "requests_per_sec": 249996.6,
}
HUMAN_LINES = ["Running 70s test @ http://10.0.1.5:8080/?name=you", "  8 threads and 5000 connections"]
TOOL_OUTPUT = "\n".join(HUMAN_LINES + ["RESULT " + json.dumps(REPORTER_RESULT)]) + "\n"
FAILED_OUTPUT = "unable to connect to 10.0.1.5:8080 Connection refused\n"

# The h2load summary has no RESULT line. The RESULT line comes from loader/h2load-report.py on the
# per-request log, which the fake h2load writes from FAKE_LOG_ROWS.
H2LOAD_LINES = ["starting benchmark...", "finished in 70.01s, 1000.00 req/s, 87.89KB/s"]
H2LOAD_OUTPUT = "\n".join(H2LOAD_LINES) + "\n"
# Two requests of 500 and 700 us in a 60 s window: 2 / 60 = 0.033 req/s, p50 is the first value.
H2LOAD_ROWS = "1700000000000000\t200\t700\n1700000000000100\t200\t500\n"
H2LOAD_RESULT = {
    "duration_us": 60000000,
    "requests": 2,
    "bytes": 0,
    "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
    "latency_us": {"mean": 600.0, "p50": 500, "p90": 700, "p95": 700, "p99": 700, "p999": 700, "max": 700},
    "requests_per_sec": 0.033,
}

# The fake tool writes its arguments and the request environment to FAKE_LOG, writes FAKE_LOG_ROWS
# to the file after --log-file when that option is present, prints the file FAKE_OUTPUT, and exits
# with FAKE_EXIT.
FAKE_TOOL = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    names = ("WRK_METHOD", "WRK_BODY_FILE", "WRK_HEADERS")
    with open(os.environ["FAKE_LOG"], "w") as f:
        json.dump({"argv": sys.argv[1:], "env": {n: os.environ.get(n) for n in names}}, f)
    if "--log-file" in sys.argv:
        with open(sys.argv[sys.argv.index("--log-file") + 1], "w") as f:
            f.write(os.environ.get("FAKE_LOG_ROWS", ""))
    with open(os.environ["FAKE_OUTPUT"]) as f:
        sys.stdout.write(f.read())
    sys.exit(int(os.environ["FAKE_EXIT"]))
    """)

# 10 s of warm-up and 60 s of measurement: wrk2 runs 70 s.
WRK2_ARGV = ["-t", "8", "-c", "5000", "-d", "70s", "-R", "250000", "--latency", "-s", str(REPORT), URL]
# 100000 req/s over 100 connections is 1000 req/s per client. LOG stands for the temporary log file.
H2LOAD_ARGV = [
    "-t", "8", "-c", "100", "-m", "100", "--rps", "1000", "--warm-up-time", "10", "-D", "60", "-d", BODY_FILE,
    "-H", "content-type: application/grpc", "-H", "te: trailers", "--log-file", "LOG", GRPC_URL,
]
NO_WRK_ENV = {"WRK_METHOD": None, "WRK_BODY_FILE": None, "WRK_HEADERS": None}

# EPOCH in args is replaced with time.time() + epoch_offset. late_ms is the
# inclusive range the script must report. A future epoch is 0.5 s ahead, which
# is more than the start-up time of bash and python3, so late_ms is 0.
LOAD_CASES = [
    {
        "name": "wrk2 get with a future epoch",
        "args": ["wrk2", "EPOCH", "250000", "8", "5000", "10", "60", URL],
        "epoch_offset": 0.5,
        "output": TOOL_OUTPUT,
        "lines": HUMAN_LINES,
        "argv": WRK2_ARGV,
        "env": {"WRK_METHOD": "GET", "WRK_BODY_FILE": "-", "WRK_HEADERS": ""},
        "result": REPORTER_RESULT,
        "late_ms": (0, 0),
    },
    {
        "name": "wrk2 post with a relative body and two headers, 5 s late",
        "args": ["wrk2", "EPOCH", "250000", "8", "5000", "10", "60", URL, "POST", "apps/grpc/echo.grpc",
                 "content-type: application/grpc", "x-note: a:b"],
        # The epoch is 5 s in the past, so late_ms is 5000 plus the start-up time of the script.
        "epoch_offset": -5,
        "output": TOOL_OUTPUT,
        "lines": HUMAN_LINES,
        "argv": WRK2_ARGV,
        "env": {"WRK_METHOD": "POST", "WRK_BODY_FILE": BODY_FILE, "WRK_HEADERS": "content-type: application/grpc\nx-note: a:b"},
        "result": REPORTER_RESULT,
        "late_ms": (5000, 5999),
    },
    {
        "name": "wrk2 keeps an absolute body path",
        "args": ["wrk2", "EPOCH", "250000", "8", "5000", "10", "60", URL, "POST", "/srv/body.bin"],
        "epoch_offset": 0.5,
        "output": TOOL_OUTPUT,
        "lines": HUMAN_LINES,
        "argv": WRK2_ARGV,
        "env": {"WRK_METHOD": "POST", "WRK_BODY_FILE": "/srv/body.bin", "WRK_HEADERS": ""},
        "result": REPORTER_RESULT,
        "late_ms": (0, 0),
    },
    {
        "name": "h2load grpc stage with a relative body",
        "args": ["h2load", "EPOCH", "100000", "8", "100", "100", "10", "60", GRPC_URL, "apps/grpc/echo.grpc"],
        "epoch_offset": 0.5,
        "output": H2LOAD_OUTPUT,
        "lines": H2LOAD_LINES,
        "argv": H2LOAD_ARGV,
        "env": NO_WRK_ENV,
        "result": H2LOAD_RESULT,
        "late_ms": (0, 0),
    },
]

# The tool fails and prints no RESULT line. load.sh keeps the output and exits 0.
MISSING_RESULT_CASES = [
    {"name": "wrk2 failure", "args": ["wrk2", "0", "250000", "8", "5000", "10", "60", URL]},
    {"name": "h2load failure", "args": ["h2load", "0", "100000", "8", "100", "100", "10", "60", GRPC_URL, BODY_FILE]},
]

USAGE_CASES = [
    {"name": "unknown tool", "args": ["k6", "0", "1", "1", "1", "1", "1", URL]},
    {"name": "wrk2 without a url", "args": ["wrk2", "0", "250000", "8", "5000", "10", "60"]},
    {"name": "h2load without a body file", "args": ["h2load", "0", "100000", "8", "100", "100", "10", "60", GRPC_URL]},
    {"name": "h2load with an extra argument", "args": ["h2load", "0", "100000", "8", "100", "100", "10", "60", GRPC_URL, BODY_FILE, "POST"]},
    {"name": "no arguments", "args": []},
]


class LoadScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        for name in ("wrk2", "h2load"):
            path = bin_dir / name
            path.write_text(FAKE_TOOL)
            path.chmod(0o755)
        self.log = tmp / "fake.json"
        self.output = tmp / "output.txt"
        env = {k: v for k, v in os.environ.items() if not k.startswith("WRK_")}
        env.update(PATH=f"{bin_dir}:{env['PATH']}", FAKE_LOG=str(self.log), FAKE_OUTPUT=str(self.output), FAKE_LOG_ROWS=H2LOAD_ROWS)
        self.env = env

    def load(self, args, output, exit_code):
        self.output.write_text(output)
        env = dict(self.env, FAKE_EXIT=str(exit_code))
        return subprocess.run(["bash", str(LOAD)] + args, env=env, capture_output=True, text=True, timeout=30)

    def test_rewrites_result(self):
        for case in LOAD_CASES:
            with self.subTest(name=case["name"]):
                epoch = time.time() + case["epoch_offset"]
                args = [f"{epoch:.3f}" if a == "EPOCH" else a for a in case["args"]]
                proc = self.load(args, case["output"], 0)
                done = time.time()
                self.assertEqual(proc.returncode, 0, proc.stderr)
                lines = proc.stdout.splitlines()
                self.assertEqual(lines[:-1], case["lines"])
                self.assertTrue(lines[-1].startswith("RESULT "))
                result = json.loads(lines[-1][len("RESULT "):])
                low, high = case["late_ms"]
                self.assertGreaterEqual(result["late_ms"], low)
                self.assertLessEqual(result["late_ms"], high)
                self.assertEqual(result, {"tool": case["args"][0], "late_ms": result["late_ms"], **case["result"]})
                self.assertGreaterEqual(done, epoch)
                fake = json.loads(self.log.read_text())
                argv = fake["argv"]
                if "--log-file" in argv:
                    # The log file is a temporary file that the script removes.
                    index = argv.index("--log-file") + 1
                    self.assertFalse(Path(argv[index]).exists())
                    argv[index] = "LOG"
                self.assertEqual(argv, case["argv"])
                self.assertEqual(fake["env"], case["env"])

    def test_missing_result_keeps_output(self):
        for case in MISSING_RESULT_CASES:
            with self.subTest(name=case["name"]):
                proc = self.load(case["args"], FAILED_OUTPUT, 1)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, FAILED_OUTPUT)

    def test_usage(self):
        for case in USAGE_CASES:
            with self.subTest(name=case["name"]):
                proc = self.load(case["args"], TOOL_OUTPUT, 0)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("usage: load.sh", proc.stderr)
                self.assertEqual(proc.stdout, "")
```

Keep `BODY`, `INIT_HARNESS`, `INIT_CASES`, `DONE_HARNESS`, `DONE_CASES`, and `Wrk2ReportTest` as they are. In `INIT_CASES` and `DONE_CASES` nothing references k6. Delete the `K6_SCRIPT` and `TREND_STATS` constants.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_load -v`
Expected: FAIL. The wrk2 cases fail on the `-d 70s` argument (the script still builds `-d 10s` from the sixth argument), the h2load cases fail with usage, and `h2load with an extra argument` fails because the old script accepts the arguments.

- [ ] **Step 3: Rewrite the script**

Write `box/load.sh`:

```bash
#!/usr/bin/env bash
# Runs one load process at a shared start time for one stage.
# Prints the tool output with the RESULT line extended by the tool and late_ms fields.
#
#   load.sh wrk2 EPOCH RATE THREADS CONNS WARMUP_S DURATION_S URL [METHOD] [BODY_FILE] [HEADER...]
#   load.sh h2load EPOCH RATE THREADS CONNS STREAMS WARMUP_S DURATION_S URL BODY_FILE
#
# EPOCH is a Unix time in seconds and can have a fraction. When EPOCH has passed,
# the tool starts at once and late_ms is the delay in milliseconds.
# wrk2 runs for WARMUP_S plus DURATION_S seconds. Its RESULT line counts the whole run.
# h2load measures DURATION_S seconds after a warm-up of WARMUP_S seconds and sends
# RATE / CONNS requests per second on each connection. Its RESULT line counts the measured window.
# A relative BODY_FILE is a path in the staged rig directory.
set -euo pipefail

RIG=$(cd "$(dirname "$0")/.." && pwd)

usage() {
  echo "usage: load.sh wrk2 EPOCH RATE THREADS CONNS WARMUP_S DURATION_S URL [METHOD] [BODY_FILE] [HEADER...]" >&2
  echo "       load.sh h2load EPOCH RATE THREADS CONNS STREAMS WARMUP_S DURATION_S URL BODY_FILE" >&2
  exit 2
}

# wait_epoch EPOCH sleeps until EPOCH and prints the start delay in milliseconds.
wait_epoch() {
  python3 -c '
import sys, time
delay = float(sys.argv[1]) - time.time()
if delay > 0:
    time.sleep(delay)
    print(0)
else:
    print(round(-delay * 1000))
' "$1"
}

# rewrite TOOL LATE_MS FILE prints FILE and adds the tool and late_ms fields to its RESULT line.
rewrite() {
  python3 -c '
import json, sys
tool, late_ms, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
with open(path, errors="replace") as f:
    for line in f:
        if line.startswith("RESULT "):
            doc = json.loads(line[len("RESULT "):])
            line = "RESULT " + json.dumps({"tool": tool, "late_ms": late_ms, **doc}) + "\n"
        sys.stdout.write(line)
' "$1" "$2" "$3"
}

# body_path FILE prints FILE with a relative path made absolute under the staged rig.
body_path() {
  case $1 in
  - | /*) printf '%s\n' "$1" ;;
  *) printf '%s\n' "$RIG/$1" ;;
  esac
}

run_wrk2() {
  [ $# -ge 7 ] || usage
  local epoch=$1 rate=$2 threads=$3 conns=$4 warmup=$5 duration=$6 url=$7
  shift 7
  local method=GET body=-
  if [ $# -gt 0 ]; then
    method=$1
    shift
  fi
  if [ $# -gt 0 ]; then
    body=$(body_path "$1")
    shift
  fi
  local headers
  headers=$(printf '%s\n' "$@")
  late_ms=$(wait_epoch "$epoch")
  # The driver treats a missing RESULT line as a void, so a tool failure does not stop the script.
  WRK_METHOD=$method WRK_BODY_FILE=$body WRK_HEADERS=$headers \
    wrk2 -t "$threads" -c "$conns" -d "$((warmup + duration))s" -R "$rate" --latency \
    -s "$RIG/loader/wrk2-report.lua" "$url" >"$out" 2>&1 || true
}

run_h2load() {
  [ $# -eq 9 ] || usage
  local epoch=$1 rate=$2 threads=$3 conns=$4 streams=$5 warmup=$6 duration=$7 url=$8 body
  body=$(body_path "$9")
  late_ms=$(wait_epoch "$epoch")
  # The RESULT line comes from the per-request log, so a failed h2load leaves no RESULT line.
  if h2load -t "$threads" -c "$conns" -m "$streams" --rps "$((rate / conns))" \
    --warm-up-time "$warmup" -D "$duration" -d "$body" \
    -H 'content-type: application/grpc' -H 'te: trailers' --log-file "$log" "$url" >"$out" 2>&1; then
    python3 "$RIG/loader/h2load-report.py" "$log" "$duration" >>"$out"
  fi
}

[ $# -ge 1 ] || usage
tool=$1
shift
out=$(mktemp)
log=$(mktemp)
trap 'rm -f "$out" "$log"' EXIT
late_ms=0

case $tool in
wrk2) run_wrk2 "$@" ;;
h2load) run_h2load "$@" ;;
*) usage ;;
esac
rewrite "$tool" "$late_ms" "$out"
```

Delete `loader/k6-grpc.js`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_load -v`
Expected: PASS. `Wrk2ReportTest` runs when luajit is installed (it is on the operator machine).

- [ ] **Step 5: Commit**

```bash
git add box/load.sh tests/test_load.py
git rm loader/k6-grpc.js
git commit -s -S -m "feat!: run wrk2 with a warm-up and h2load for the gRPC target"
```

---

### Task 3: Box scripts for rapira only, RSS, and the removal of the other servers and apps

**Files:**
- Modify: `box/lib.sh`, `box/target.sh`, `box/servers/rapira.sh`, `servers/rapira/static.toml.tpl`, `servers/php.ini`, `apps/grpc/fixtures.py`, `apps/grpc/buf.gen.yaml`, `apps/grpc/php/autoload.php`
- Delete: `box/servers/frankenphp.sh`, `box/servers/nginx-rapira.sh`, `box/servers/php-fpm.sh`, `box/servers/roadrunner.sh`, `servers/frankenphp/` (3 files), `servers/nginx/` (2 files), `servers/php-fpm/php-fpm.conf.tpl`, `servers/roadrunner/` (3 files), `apps/symfony/` (6 files), `apps/laravel/` (5 files), `apps/static/` (3 files), `apps/hello/fpm.php`, `apps/hello/frankenphp.php`, `apps/grpc/php/rr-worker.php`, `apps/grpc/php/gen/Bench/V1/EchoServiceInterface.php`, `apps/grpc/echo.bin`, `apps/grpc/echo.json`, `apps/grpc/expect.bin`, `apps/grpc/expect.grpcweb`, `apps/grpc/expect.json`
- Test: `tests/test_box.py`, `tests/test_templates.py`, `tests/box.Dockerfile`

**Interfaces:**
- Consumes: nothing from the other tasks.
- Produces: `box/target.sh mem TAG rapira` prints `rss_kb`, the sum of `VmRSS` over the listener and its children. `GRPC_VENDOR` is `/opt/bench/apps/grpc/vendor` (Task 8 installs it there). The static template gets `ROOT=$RIG/apps/hello`. Task 5 writes the targets against these paths.

- [ ] **Step 1: Write the failing template tests**

Replace `tests/test_templates.py` with:

```python
"""Tests of the rapira config templates, the shared php.ini, and the expected body files."""

import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RIG = "/home/fedora/bench-rig"

TOML_CASES = [
    {
        "name": "rapira http worker",
        "path": "servers/rapira/http.toml.tpl",
        "values": {"LISTEN": ":8080", "ENTRY": RIG + "/apps/hello/worker.php", "MODE": "worker", "PROCS": "32"},
        "expected": {
            "http": {
                "listen": ":8080",
                "pool": {"entrypoint": RIG + "/apps/hello/worker.php", "mode": "worker", "processes": 32},
            },
            "log": {"level": "warn"},
        },
    },
    {
        "name": "rapira http dispatcher of the yii3 app",
        "path": "servers/rapira/http.toml.tpl",
        "values": {"LISTEN": ":8080", "ENTRY": "/opt/bench/apps/yii3/worker-rapira.php", "MODE": "dispatcher", "PROCS": "32"},
        "expected": {
            "http": {
                "listen": ":8080",
                "pool": {"entrypoint": "/opt/bench/apps/yii3/worker-rapira.php", "mode": "dispatcher", "processes": 32},
            },
            "log": {"level": "warn"},
        },
    },
    {
        "name": "rapira static middleware in front of the hello dispatcher",
        "path": "servers/rapira/static.toml.tpl",
        "values": {"LISTEN": ":8080", "ROOT": RIG + "/apps/hello", "ENTRY": RIG + "/apps/hello/dispatcher.php", "MODE": "dispatcher", "PROCS": "32"},
        "expected": {
            "http": {
                "listen": ":8080",
                "middleware": ["static"],
                "static": {"root": RIG + "/apps/hello"},
                "pool": {"entrypoint": RIG + "/apps/hello/dispatcher.php", "mode": "dispatcher", "processes": 32},
            },
            "log": {"level": "warn"},
        },
    },
    {
        "name": "rapira grpc",
        "path": "servers/rapira/grpc.toml.tpl",
        "values": {"LISTEN": ":8080", "RIG": RIG, "ENTRY": RIG + "/apps/grpc/php/dispatcher.php", "PROCS": "32"},
        "expected": {
            "grpc": {
                "listen": ":8080",
                "descriptor_set": RIG + "/apps/grpc/bench.binpb",
                "services": ["bench.v1.EchoService"],
                "pool": {"entrypoint": RIG + "/apps/grpc/php/dispatcher.php", "mode": "dispatcher", "processes": 32},
            },
            "log": {"level": "warn"},
        },
    },
]

BODY_CASES = [
    {
        "name": "hello expected body",
        "path": "apps/hello/expect.txt",
        # 24 bytes: the body of GET /?name=you on every hello target.
        "expected": b"Hello from worker, you!\n",
    },
    {
        "name": "grpc request frame",
        "path": "apps/grpc/echo.grpc",
        # The length-prefixed frame of an EchoRequest with the 64 character text of apps/grpc/fixtures.py: 71 bytes.
        "expected": b"\x00\x00\x00\x00\x42\x0a\x40" + b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ01",
    },
]

# The values of the shared php.ini.
PHP_INI = {
    "opcache.enable": "1",
    "opcache.enable_cli": "1",
    "opcache.validate_timestamps": "0",
    "opcache.jit": "disable",
    "opcache.memory_consumption": "256",
    "memory_limit": "256M",
    "realpath_cache_size": "4096K",
    "realpath_cache_ttl": "600",
    "expose_php": "0",
    "display_errors": "0",
    "log_errors": "1",
    "error_reporting": "E_ALL & ~E_DEPRECATED",
    "error_log": "/dev/stderr",
}


def render(path, values):
    text = (REPO / path).read_text()
    for name, value in values.items():
        text = text.replace("@@" + name + "@@", value)
    return text


class TemplateTests(unittest.TestCase):
    def test_toml_templates(self):
        for case in TOML_CASES:
            with self.subTest(name=case["name"]):
                text = render(case["path"], case["values"])
                self.assertNotIn("@@", text)
                self.assertEqual(case["expected"], tomllib.loads(text))

    def test_php_ini_values(self):
        values = {}
        for line in (REPO / "servers/php.ini").read_text().splitlines():
            if line and not line.startswith(";"):
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip()
        self.assertEqual(PHP_INI, values)

    def test_expected_bodies(self):
        for case in BODY_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(case["expected"], (REPO / case["path"]).read_bytes())


if __name__ == "__main__":
    unittest.main()
```

The old file tested the FrankenPHP, nginx, php-fpm, and RoadRunner templates and the static asset; those files go in this task. The gRPC frame case pins the request body that h2load posts: `apps/grpc/fixtures.py` writes it as the flag byte 0, the 4 byte big-endian length 0x42 (66), and the message (tag 0x0a, length 0x40, the 64 byte text).

- [ ] **Step 2: Run the template tests**

Run: `python3 -m unittest tests.test_templates -v`
Expected: PASS for every case. The rapira templates and the fixtures already have this shape, and no case reads a file that this task deletes, so the test is the guard that the deletions below leave the rapira files whole.

- [ ] **Step 3: Update the box test for rapira only**

In `tests/test_box.py`:

Docstring: replace "start, probe, and stop each server kind" with "start, probe, and stop the rapira server", and "It uses /opt/bench and the ports 8080, 8081, and 9000" with "It uses /opt/bench and the port 8080" (also in the second paragraph of the docstring).

`STUB`: delete the `if "-c" in sys.argv:` branch and its `else:` so the block reads:

```python
config = tomllib.loads(Path(sys.argv[-1]).read_text())
grpc = "grpc" in config
section = config["grpc"] if grpc else config["http"]
listen = section["listen"]
processes = section["pool"]["processes"]
```

Also delete `import re` from the stub and change its comment to `# The stub of "rapira serve CONFIG". A copy of python3 named rapira runs this file from its working directory, $BENCH/run, so /proc/<pid>/exe is the copy.`

Replace `LIFECYCLE_CASES` with:

```python
LIFECYCLE_CASES = [
    {
        "name": "rapira worker",
        "args": ["worker", "@RIG@/apps/hello/worker.php"],
        "configs": {"toml": ['listen = ":8080"', f'entrypoint = "{RIG}/apps/hello/worker.php"', 'mode = "worker"', "processes = 2"]},
        "probe": HELLO,
        # The rapira master forks PROCS workers.
        "workers": PROCS,
        "stub_only": False,
    },
    {
        "name": "rapira classic",
        "args": ["classic", "@RIG@/apps/hello/classic.php"],
        "configs": {"toml": ['mode = "classic"', f'entrypoint = "{RIG}/apps/hello/classic.php"']},
        "probe": HELLO,
        "workers": PROCS,
        "stub_only": False,
    },
    {
        "name": "rapira dispatcher",
        "args": ["dispatcher", "@RIG@/apps/hello/dispatcher.php"],
        "configs": {"toml": ['mode = "dispatcher"', f'entrypoint = "{RIG}/apps/hello/dispatcher.php"']},
        "probe": HELLO,
        "workers": PROCS,
        "stub_only": False,
    },
    {
        "name": "rapira dispatcher with the static middleware misses the root and reaches PHP",
        "args": ["dispatcher", "@RIG@/apps/hello/dispatcher.php", "servers/rapira/static.toml.tpl"],
        "configs": {"toml": ['middleware = ["static"]', f'root = "{RIG}/apps/hello"', 'mode = "dispatcher"']},
        "probe": HELLO,
        "workers": PROCS,
        "stub_only": False,
    },
    {
        "name": "rapira grpc",
        "args": ["grpc", "@RIG@/apps/grpc/php/dispatcher.php"],
        "configs": {"toml": ["[grpc]", f'descriptor_set = "{RIG}/apps/grpc/bench.binpb"', f'entrypoint = "{RIG}/apps/grpc/php/dispatcher.php"']},
        "probe": None,
        "workers": PROCS,
        # The real gRPC dispatcher needs the protobuf runtime that provisioning installs.
        "stub_only": True,
    },
]
```

Replace `FAIL_CASES` with the three rapira cases only (`rapira worker count differs`, `rapira target port busy`, `rapira unknown mode`), each without the `binary` key.

In `BoxLifecycleTests`:
- `reset()`: copy only `("box", "servers", "apps/hello", "apps/grpc")`; create only `("run", "log", "rapira")`; write the stub; create `BENCH / "rapira/pr/bin"` with the python3 copy named `rapira` (or the symlink to `ASSET_DIR` with an asset) for the one binary `pr`; delete the `rr`, `frankenphp`, and `roadrunner-grpc` lines; create `(BENCH / "apps/grpc/vendor").mkdir(parents=True)` and write `(BENCH / "apps/grpc/vendor/autoload.php")` with `"<?php\n"`.
- `force_cleanup()` and `leftovers()`: the pattern is `"opt/bench"` only.
- `start_arguments()`: `return ["start", TAG, "rapira", PROCS, BENCH / "rapira/pr", *case["args"]]`.
- `assert_stopped()`: delete the `listening(8081)` assertion.
- `test_lifecycle()` and `test_failed_start_leaves_nothing()`: pass `"rapira"` where `case["server"]` was used.
- The memory check stays: `self.assertGreater(int(memory.stdout), 0)`; it now reads `VmRSS`.

In `PROBE_CASES` replace the Connect JSON case with:

```python
    {
        "name": "request shape of a POST with a body and a header",
        "reply": b"Hello from worker, you!\n",
        "args": ["POST", "apps/grpc/echo.grpc", "content-type: application/grpc"],
        "status": 0,
        "stderr": "",
        "method": "POST",
        # apps/grpc/fixtures.py writes this request frame.
        "body": (REPO / "apps/grpc/echo.grpc").read_bytes(),
        "headers": {"content-type": "application/grpc"},
    },
```

Replace `tests/box.Dockerfile` with:

```dockerfile
# Disposable image of tests/test_box.py. The test runs the box scripts as the user fedora, as on the
# rig boxes. It uses /opt/bench and the port 8080.
FROM fedora:44
RUN dnf -y install --setopt=install_weak_deps=False python3 curl iproute procps-ng diffutils \
    && dnf clean all
RUN useradd -m fedora && install -d -o fedora -g fedora /opt/bench
USER fedora
ENV HOME=/home/fedora BOX_TEST=1
WORKDIR /repo
CMD ["python3", "-m", "unittest", "tests.test_box", "-v"]
```

- [ ] **Step 4: Change the box scripts**

`box/lib.sh`:
- Line 6 comment becomes `# The one place of the target port on the boxes.`
- Lines 9 to 11 become:

```bash
# Every PHP process loads the shared php.ini. The gRPC entry loads the protobuf runtime from GRPC_VENDOR.
export PHPRC=$BENCH/php.ini
export GRPC_VENDOR=$BENCH/apps/grpc/vendor
```

- Replace the `pss_kb` function with:

```bash
# rss_kb TAG prints the sum of the resident set size in KiB of the processes of TAG.
# https://docs.kernel.org/filesystems/proc.html#process-specific-subdirectories
rss_kb() {
  local pid
  for pid in $(pids_of "$1"); do
    cat "/proc/$pid/status" 2>/dev/null || true
  done | awk '/^VmRSS:/ { kb += $2 } END { print kb + 0 }'
}
```

- In `fail()`, the comment `# A killed FrankenPHP holds the port for a few milliseconds after the signal.` becomes `# The kernel frees the port a moment after the kill.`

`box/target.sh`: the `mem)` branch calls `rss_kb "$tag"`.

`box/servers/rapira.sh`: the header comment line `# MODE is worker, classic, or dispatcher. CONFIG_TPL is servers/rapira/http.toml.tpl by default.` gains a second line `# The static template serves the hello app directory as its root.`; the `render` call passes `"ROOT=$RIG/apps/hello"`.

`servers/rapira/static.toml.tpl`: line 1 becomes `# rapira.toml of the static target. box/servers/rapira.sh renders the placeholders.`

`servers/php.ini`: lines 1 and 2 become `; The php.ini of every PHP process of the rig. rapira loads it through PHPRC. The run file records this text.`

`apps/grpc/fixtures.py`: the docstring becomes `"""Write the gRPC request frame and the expected response frame to apps/grpc/."""`; the `files` dict keeps only `"echo.grpc": (frame(0x00, request), 71)` and `"expect.grpc": (grpc_reply, 91)`; delete the `grpc_reply + frame(0x80, ...)` line and the `echo.bin`, `echo.json`, `expect.bin`, `expect.json` lines.

`apps/grpc/buf.gen.yaml`: delete the `buf.build/community/roadrunner-server-php-grpc` plugin (two lines).

`apps/grpc/php/autoload.php`: line 3 becomes `// GRPC_VENDOR is the directory where provisioning installs apps/grpc/composer.lock. Its` and line 4 stays `// google/protobuf package is the pure-PHP runtime. PHP uses ext-protobuf when the extension is loaded.` shortened to `// google/protobuf package is the pure PHP runtime.`

Delete every file of the "Delete" list of this task with `git rm`.

- [ ] **Step 5: Run the tests**

Run: `make test`
Expected: PASS for `tests.test_templates`, `tests.test_load`, `tests.test_h2load_report`, and the probe tests of `tests.test_box`; the lifecycle tests skip outside the container. The other modules still pass because they do not read the deleted files (`tests/test_registry.py` reads `suites/targets.toml`, which Task 5 changes; it still lists the old targets and still loads).

Run the container test:

```bash
docker build -t rapira-bench-box -f tests/box.Dockerfile tests
docker run --rm --init -v "$PWD:/repo:ro" rapira-bench-box
```

Expected: 5 lifecycle subtests and 3 failed-start subtests pass with the stub.

- [ ] **Step 6: Commit**

```bash
git add -A box servers apps tests
git commit -s -S -m "feat!: keep the rapira box scripts only and read the RSS of a target"
```

---

### Task 4: The Yii3 and gRPC app sources and their locks

**Files:**
- Create: `apps/yii3/source.toml`, `apps/grpc/composer.json`
- Create by `make lock`: `apps/yii3/composer.lock`, `apps/yii3/expect.json`, `apps/grpc/composer.lock`
- Modify: `box/lock-apps.sh` (rewrite), `Makefile` (the `lock` comment)
- Test: `tests/test_templates.py`

**Interfaces:**
- Consumes: nothing from the other tasks.
- Produces: `apps/yii3/source.toml` with `repo` and `commit`; `apps/yii3/composer.lock` and `apps/yii3/expect.json` (65 bytes); `apps/grpc/composer.lock` with `google/protobuf` 5.36.x. Task 5 points the `yii3-rapira-dispatcher` target at `apps/yii3/expect.json`; Task 8 installs from these files.

- [ ] **Step 1: Write the failing test**

Add to `BODY_CASES` in `tests/test_templates.py`:

```python
    {
        "name": "yii3 expected body",
        "path": "apps/yii3/expect.json",
        # 65 bytes: the body of GET / on the Yii3 app-api at the pinned commit, without a trailing newline.
        "expected": b'{"status":"success","data":{"name":"My Project","version":"1.0"}}',
    },
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_templates -v`
Expected: FAIL on `yii3 expected body` with `FileNotFoundError`.

- [ ] **Step 3: Write the sources and the lock script**

Create `apps/yii3/source.toml`:

```toml
# The Yii3 API benchmark app. box/lock-apps.sh and box/provision-server.sh clone this commit.
repo = "https://github.com/Yii3-Benchmarks/app-api"
commit = "0fa2e2a"
```

Create `apps/grpc/composer.json`:

```json
{
    "require": {
        "google/protobuf": "~5.36.0"
    }
}
```

Write `box/lock-apps.sh`:

```bash
#!/usr/bin/env bash
# Creates the committed files of the apps that provisioning installs from a lock:
# apps/yii3/composer.lock and apps/yii3/expect.json from the commit in apps/yii3/source.toml,
# and apps/grpc/composer.lock from apps/grpc/composer.json.
# Run it on the operator machine with PHP 8.5, Composer, and a Valkey or Redis server on
# 127.0.0.1:6379, for example: docker run --rm -d -p 127.0.0.1:6379:6379 valkey/valkey:9.1.2-alpine
# The Yii3 app reads its route cache from that server when it builds its container.
# Commit the three files after a run.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
export COMPOSER_NO_INTERACTION=1

# source_value KEY prints the value of KEY in apps/yii3/source.toml.
source_value() {
  python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))[sys.argv[2]])' "$ROOT/apps/yii3/source.toml" "$1"
}

check_valkey() {
  if ! python3 -c 'import socket; socket.create_connection(("127.0.0.1", 6379), 2).close()' 2>/dev/null; then
    echo "ERROR: no Valkey on 127.0.0.1:6379; run: docker run --rm -d -p 127.0.0.1:6379:6379 valkey/valkey:9.1.2-alpine" >&2
    exit 1
  fi
}

# composer install without a lock resolves the constraints and writes the lock from the pristine
# composer.json. composer update would also bump the constraints of that file.
# The app requires ext-pdo_pgsql, which the operator machine does not need for the lock.
lock_yii3() {
  local dir=$WORK/yii3 pid
  git clone -q "$(source_value repo)" "$dir"
  git -C "$dir" checkout -q "$(source_value commit)"
  composer --working-dir="$dir" install --no-dev --no-scripts --no-progress --classmap-authoritative --ignore-platform-req=ext-pdo_pgsql
  install -m 0644 "$dir/composer.lock" "$ROOT/apps/yii3/composer.lock"
  php -S 127.0.0.1:8765 -t "$dir/public" "$dir/public/index.php" >"$WORK/php.log" 2>&1 &
  pid=$!
  for _ in $(seq 1 20); do
    curl -fsS -o "$ROOT/apps/yii3/expect.json" http://127.0.0.1:8765/ 2>/dev/null && break
    sleep 0.5
  done
  kill "$pid"
  if [ ! -s "$ROOT/apps/yii3/expect.json" ]; then
    cat "$WORK/php.log" >&2
    echo "ERROR: no body from the Yii3 app" >&2
    exit 1
  fi
}

lock_grpc() {
  local dir=$WORK/grpc
  install -d "$dir"
  install -m 0644 "$ROOT/apps/grpc/composer.json" "$dir/"
  composer --working-dir="$dir" update --no-progress --no-install
  install -m 0644 "$dir/composer.lock" "$ROOT/apps/grpc/composer.lock"
}

check_valkey
lock_yii3
lock_grpc
```

In the `Makefile`, the `lock` target keeps `box/lock-apps.sh`; the comment above `sync` stays. No other Makefile change in this task.

- [ ] **Step 4: Run the lock**

```bash
docker run --rm -d --name lock-valkey -p 127.0.0.1:6379:6379 valkey/valkey:9.1.2-alpine
make lock
docker stop lock-valkey
```

Expected: `apps/yii3/composer.lock` (about 170 packages, `yiisoft/yii-runner-rapira` and `rapira/contract` at `dev-master`), `apps/yii3/expect.json` of 65 bytes, `apps/grpc/composer.lock` with `google/protobuf` `v5.36.2`. Check with:

```bash
wc -c apps/yii3/expect.json
python3 -c 'import json; d = json.load(open("apps/yii3/composer.lock")); print(len(d["packages"]), [p["version"] for p in d["packages"] if p["name"] == "yiisoft/yii-runner-rapira"])'
python3 -c 'import json; print([p["version"] for p in json.load(open("apps/grpc/composer.lock"))["packages"] if p["name"] == "google/protobuf"])'
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_templates -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/yii3 apps/grpc/composer.json apps/grpc/composer.lock box/lock-apps.sh tests/test_templates.py
git commit -s -S -m "feat: pin the Yii3 app-api and the protobuf runtime with committed locks"
```

---

### Task 5: The registry, the suite, and the constant-rate driver

**Files:**
- Modify: `rig/registry.py` (rewrite), `rig/bench.py` (rewrite), `rig/merge.py`, `rig/flags.py`, `rig/runfile.py`, `rig/__main__.py`, `rig/__init__.py`, `suites/targets.toml` (rewrite), `suites/ci.toml` (rewrite)
- Delete: `rig/ladder.py`, `suites/full.toml`, `suites/ab.toml`, `tests/test_ladder.py`
- Test: `tests/test_registry.py` (rewrite), `tests/test_bench.py` (rewrite), `tests/test_runfile.py` (rewrite), `tests/test_flags.py`, `tests/test_merge.py`, `tests/test_rig.py`

**Interfaces:**
- Consumes: the `load.sh` command lines of Task 2; `target.sh mem` of Task 3; `apps/yii3/expect.json` of Task 4.
- Produces: `Suite(name, rounds, warmup_s, duration_s, smoke, rates, connections, grpc_streams, targets)` and `Target` without `binary`; the cell record `{key, target, round, status, flags, rate, achieved_rps, successful_rps, errors, latency_us, rss_kb, held, cpu, loaders}` with `reason` on a cell that is not ok and null numbers then; the run file `rapira-bench-run/2` with `rapira.pr`; `run_suite(rig, suite, boxes, out_dir, *, suite_path, processes, run_id, rapira, servers, apps, loader_threads)`; the CLI options `--pr-number`, `--pr-url`, `--pr-title` of `rig bench`. Tasks 6, 7, and 8 read this shape.

- [ ] **Step 1: Write the failing registry test**

Replace `tests/test_registry.py` with:

```python
import tempfile
import unittest
from pathlib import Path

from rig.registry import Suite, SuiteError, Target, cell_key, load_suite, load_targets, plan_cells

ROOT = Path(__file__).resolve().parent.parent

VALID_TARGET = """
[grpc-rapira]
server = "rapira"
app = "grpc"
mode = "dispatcher"
proto = "grpc"
start = ["grpc", "@RIG@/apps/grpc/php/dispatcher.php"]
url = "/bench.v1.EchoService/Echo"
expect = "apps/grpc/expect.grpc"
config = "servers/rapira/grpc.toml.tpl"
method = "POST"
body = "apps/grpc/echo.grpc"

[grpc-rapira.headers]
x-note = "a:b"
"""

BASE = 'server = "rapira"\napp = "hello"\nmode = "worker"\nproto = "http1"\nstart = []\nurl = "/"\nexpect = "e"\nconfig = "c"\n'

# Each error case is a valid rapira target with one field changed, added, or removed.
TARGET_ERROR_CASES = [
    {"name": "unknown server", "toml": "[t]\n" + BASE.replace('server = "rapira"', 'server = "frankenphp"'), "message": "unknown server frankenphp"},
    {"name": "unknown app", "toml": "[t]\n" + BASE.replace('app = "hello"', 'app = "symfony"'), "message": "unknown app symfony"},
    {"name": "unknown mode", "toml": "[t]\n" + BASE.replace('mode = "worker"', 'mode = "cgi"'), "message": "unknown mode cgi"},
    {"name": "unknown proto", "toml": "[t]\n" + BASE.replace('proto = "http1"', 'proto = "http2"'), "message": "unknown proto http2"},
    {"name": "binary field of the old registry", "toml": "[t]\n" + BASE + 'binary = "pr"\n', "message": "binary"},
    {"name": "misspelled field", "toml": "[t]\n" + BASE + 'header = "x: 1"\n', "message": "header"},
    {"name": "missing url", "toml": "[t]\n" + BASE.replace('url = "/"\n', ""), "message": "url"},
]


def target(name: str, app: str = "hello", proto: str = "http1") -> Target:
    return Target(
        name=name,
        server="rapira",
        app=app,
        mode="worker",
        proto=proto,
        start=("worker", "@RIG@/apps/hello/worker.php"),
        url="/?name=you",
        expect="apps/hello/expect.txt",
        config="servers/rapira/http.toml.tpl",
    )


REGISTRY = {
    "hello-a": target("hello-a"),
    "hello-b": target("hello-b"),
    "yii3-a": target("yii3-a", app="yii3"),
    "grpc-a": target("grpc-a", app="grpc", proto="grpc"),
}

SUITE_DEFAULTS = {
    "rounds": 1,
    "warmup_s": 10,
    "duration_s": 60,
    "targets": '["hello-a", "yii3-a"]',
    "rates": "hello = 250000\nyii3 = 250000",
    "connections": "http1 = 5000\ngrpc = 100",
    "streams": 100,
}

SUITE_TEMPLATE = """name = "test"
rounds = {rounds}
warmup_s = {warmup_s}
duration_s = {duration_s}
smoke = false
targets = {targets}

[rates]
{rates}

[connections]
{connections}

[grpc]
streams = {streams}
"""

SUITE_ERROR_CASES = [
    {"name": "unknown target", "fields": {"targets": '["hello-a", "hello-z"]'}, "loaders": 1, "message": "unknown target hello-z"},
    {"name": "target listed twice", "fields": {"targets": '["hello-a", "hello-a"]'}, "loaders": 1, "message": "target hello-a is listed twice"},
    {"name": "zero rounds", "fields": {"rounds": 0}, "loaders": 1, "message": "rounds 0 is under 1"},
    {"name": "duration one second under the minimum", "fields": {"duration_s": 29}, "loaders": 1, "message": "duration_s 29 is under 30"},
    {"name": "no rate for an app of a target", "fields": {"rates": "hello = 250000"}, "loaders": 1, "message": "no rate for app yii3"},
    {
        "name": "no connections for the proto of a target",
        "fields": {"targets": '["hello-a", "grpc-a"]', "rates": "hello = 250000\ngrpc = 100000", "connections": "http1 = 5000"},
        "loaders": 1,
        "message": "no connections for proto grpc",
    },
    # 250001 % 2 = 1.
    {"name": "rate not a multiple of two loaders", "fields": {"rates": "hello = 250001\nyii3 = 250000"}, "loaders": 2, "message": "rate 250001 of app hello is not a multiple of 2 loaders"},
    # 249999 % 3 = 0, and 5000 % 3 = 2.
    {"name": "connections not a multiple of three loaders", "fields": {"rates": "hello = 249999\nyii3 = 249999"}, "loaders": 3, "message": "connections 5000 of proto http1 is not a multiple of 3 loaders"},
]

SUITE_OK_CASES = [
    {
        "name": "minimum duration, a rate for an app no target uses, and two loaders",
        "fields": {"duration_s": 30, "targets": '["yii3-a", "hello-a"]', "rates": "hello = 250000\nyii3 = 250000\ngrpc = 100000"},
        "loaders": 2,
        "expected": Suite(
            name="test",
            rounds=1,
            warmup_s=10,
            duration_s=30,
            smoke=False,
            rates={"hello": 250000, "yii3": 250000, "grpc": 100000},
            connections={"http1": 5000, "grpc": 100},
            grpc_streams=100,
            targets=(REGISTRY["yii3-a"], REGISTRY["hello-a"]),
        ),
    },
]

PLAN_CASES = [
    {
        "name": "one round keeps the suite order",
        "rounds": 1,
        "targets": ("hello-a", "hello-b", "yii3-a"),
        "expected": ["r1-hello-a", "r1-hello-b", "r1-yii3-a"],
    },
    {
        # Round r starts at index (r - 1) % 4: a, then b, then c.
        "name": "three rounds of four targets rotate by one",
        "rounds": 3,
        "targets": ("a", "b", "c", "d"),
        "expected": [
            "r1-a", "r1-b", "r1-c", "r1-d",
            "r2-b", "r2-c", "r2-d", "r2-a",
            "r3-c", "r3-d", "r3-a", "r3-b",
        ],
    },
]

# The ci row set of spec section 4, in suite order.
CI_TARGETS = (
    "hello-rapira-classic",
    "hello-rapira-worker",
    "hello-rapira-dispatcher",
    "hello-rapira-dispatcher-static",
    "yii3-rapira-dispatcher",
    "grpc-rapira",
)


class LoadTargetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, text: str) -> Path:
        path = Path(self.tmp.name) / "targets.toml"
        path.write_text(text)
        return path

    def test_request_shape_becomes_tuples(self):
        got = load_targets(self.write(VALID_TARGET))
        self.assertEqual(
            got,
            {
                "grpc-rapira": Target(
                    name="grpc-rapira",
                    server="rapira",
                    app="grpc",
                    mode="dispatcher",
                    proto="grpc",
                    start=("grpc", "@RIG@/apps/grpc/php/dispatcher.php"),
                    url="/bench.v1.EchoService/Echo",
                    expect="apps/grpc/expect.grpc",
                    config="servers/rapira/grpc.toml.tpl",
                    method="POST",
                    headers=(("x-note", "a:b"),),
                    body="apps/grpc/echo.grpc",
                )
            },
        )

    def test_invalid_target(self):
        for case in TARGET_ERROR_CASES:
            with self.subTest(name=case["name"]):
                with self.assertRaises(SuiteError) as ctx:
                    load_targets(self.write(case["toml"]))
                self.assertIn(case["message"], str(ctx.exception))


class LoadSuiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, fields: dict) -> Path:
        path = Path(self.tmp.name) / "suite.toml"
        path.write_text(SUITE_TEMPLATE.format(**(SUITE_DEFAULTS | fields)))
        return path

    def test_invalid_suite(self):
        for case in SUITE_ERROR_CASES:
            with self.subTest(name=case["name"]):
                with self.assertRaises(SuiteError) as ctx:
                    load_suite(self.write(case["fields"]), REGISTRY, case["loaders"])
                self.assertIn(case["message"], str(ctx.exception))

    def test_valid_suite(self):
        for case in SUITE_OK_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(load_suite(self.write(case["fields"]), REGISTRY, case["loaders"]), case["expected"])


class PlanCellsTest(unittest.TestCase):
    def test_rotation(self):
        for case in PLAN_CASES:
            with self.subTest(name=case["name"]):
                suite = Suite(
                    name="test",
                    rounds=case["rounds"],
                    warmup_s=10,
                    duration_s=60,
                    smoke=False,
                    rates={"hello": 250000},
                    connections={"http1": 5000},
                    grpc_streams=100,
                    targets=tuple(target(name) for name in case["targets"]),
                )
                got = [cell_key(round_no, t) for round_no, t in plan_cells(suite)]
                self.assertEqual(got, case["expected"])


class ShippedSuitesTest(unittest.TestCase):
    def setUp(self):
        self.registry = load_targets(ROOT / "suites/targets.toml")

    def test_ci_rows_match_the_spec(self):
        suite = load_suite(ROOT / "suites/ci.toml", self.registry, 1)
        self.assertEqual(tuple(t.name for t in suite.targets), CI_TARGETS)
        self.assertEqual((suite.rounds, suite.warmup_s, suite.duration_s), (1, 10, 60))
        self.assertEqual(suite.rates, {"hello": 250000, "yii3": 250000, "grpc": 100000})
        self.assertEqual(suite.connections, {"http1": 5000, "grpc": 100})
        self.assertEqual(suite.grpc_streams, 100)

    def test_every_registry_target_is_in_the_ci_suite(self):
        suite = load_suite(ROOT / "suites/ci.toml", self.registry, 1)
        self.assertEqual({t.name for t in suite.targets}, set(self.registry))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Write the failing driver test**

Replace `tests/test_bench.py` with:

```python
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rig.bench import counted_s, load_cmd, plan_run, run_suite, sample_jobs
from rig.merge import ERROR_KEYS
from rig.registry import Suite, SuiteError, Target
from rig.rig import Rig
from rig.ssh import Host, SshError

SERVER = Host("server", "3.0.0.1", "10.0.0.1")
LOADERS = (Host("loader-1", "3.0.0.2", "10.0.0.2"), Host("loader-2", "3.0.0.3", "10.0.0.3"))
RIG = Rig(SERVER, LOADERS, "c7a.8xlarge", "c7a.2xlarge", "ami-0123", Path("key"))
# run_suite hashes a suite file into the run file.
SUITE_FILE = Path(__file__).resolve().parent.parent / "suites" / "ci.toml"
RAPIRA = {"ref": "nightly", "sha": "abc1234def", "version": "0.9.0", "build": "nightly", "dir": "/opt/bench/rapira/abc1234", "pr": None}
THREADS = 4

WORKER = Target(
    name="hello-rapira-worker", server="rapira", app="hello", mode="worker", proto="http1",
    start=("worker", "@RIG@/apps/hello/worker.php"), url="/?name=you", expect="apps/hello/expect.txt",
    config="servers/rapira/http.toml.tpl",
)
GRPC = Target(
    name="grpc-rapira", server="rapira", app="grpc", mode="dispatcher", proto="grpc",
    start=("grpc", "@RIG@/apps/grpc/php/dispatcher.php"), url="/bench.v1.EchoService/Echo",
    expect="apps/grpc/expect.grpc", config="servers/rapira/grpc.toml.tpl", method="POST", body="apps/grpc/echo.grpc",
)
URL = "http://10.0.0.1:8080/?name=you"
GRPC_URL = "http://10.0.0.1:8080/bench.v1.EchoService/Echo"

BOX = "bash bench-rig/box/"
START = BOX + "target.sh start"
PIDS = BOX + "target.sh probe"
STOP = BOX + "target.sh stop"
LOG = BOX + "target.sh log"
MEM = BOX + "target.sh mem"
PROBE = BOX + "probe.sh"
LOAD = BOX + "load.sh"
WAIT = "python3 -c "
FACTS = "echo kernel="

# Two loaders. Each wrk2 process asks 125000 req/s for 70 s: HELD_WRK2 requests from both is
# 250000 req/s, and SHORT_WRK2 is 16000000 / 70 = 228571 req/s, under 95% of 250000.
# Each h2load process asks 50000 req/s and counts 60 s: HELD_H2LOAD from both is 100000 req/s.
HELD_WRK2 = 8750000
SHORT_WRK2 = 8000000
HELD_H2LOAD = 3000000


def suite(targets, connections=5000):
    return Suite(
        name="test", rounds=1, warmup_s=10, duration_s=60, smoke=False,
        rates={"hello": 250000, "grpc": 100000}, connections={"http1": connections, "grpc": 100}, grpc_streams=100,
        targets=tuple(targets),
    )


def result_line(tool, requests, late_ms=0, status=0):
    body = {
        "tool": tool, "late_ms": late_ms, "duration_us": 70000000 if tool == "wrk2" else 60000000,
        "requests": requests, "bytes": requests * 126 if tool == "wrk2" else 0,
        "errors": {key: 0 for key in ERROR_KEYS} | {"status": status},
        "latency_us": {"mean": 700.0, "p50": 690, "p90": 1100, "p95": 1170, "p99": 1260, "p999": 1350, "max": 2800},
        "requests_per_sec": requests / 70,
    }
    return f"Running test\nRESULT {json.dumps(body)}\n"


def load_reply(requests=None, late_ms=0, status=0):
    """A loader that holds its share of the rate, or answers `requests` requests."""

    def reply(cmd):
        tool = shlex.split(cmd)[2]
        count = requests if requests is not None else (HELD_H2LOAD if tool == "h2load" else HELD_WRK2)
        return result_line(tool, count, late_ms, status)

    return reply


def counter(busy_step, total_step, extra=""):
    """Growing /proc/stat counters: every window between two calls has the same busy percent."""
    state = {"n": 0}

    def reply(cmd):
        state["n"] += 1
        return f"cpu {busy_step * state['n']} {total_step * state['n']}\n{extra}"

    return reply


def replies(overrides):
    table = {
        ("*", FACTS): "kernel=6.17.1\ninstance_id=i-0abc\naz=eu-central-1a\nplacement_group=rapira-bench\nwrk2=44a94c17d8e6a0bac8559b53da76848e430cb7a7\nh2load=h2load nghttp2/1.68.0\n",
        ("server", START): "config=/opt/bench/run/r1-hello-rapira-worker.toml\npid=100\n",
        ("server", PIDS): "101 102\n4096\n",
        ("server", STOP): "",
        ("server", LOG): "",
        ("*", WAIT): "",
        ("server", MEM): "204800\n",
        # The server is 50% busy in every window.
        ("server", "bash bench-rig/box/snapshot.sh"): counter(50, 100, "conns 5000 0\n"),
        ("*", PROBE): "",
        ("*", LOAD): load_reply(),
    }
    # Every loader is 90% busy in every window.
    for host in LOADERS:
        table[(host.name, "bash bench-rig/box/snapshot.sh")] = counter(90, 100)
    table.update(overrides)
    return table


class FakeBoxes:
    """Replies by (host name, command prefix); "*" matches any host. A list reply is used in order.

    A command of parts joined by " && " gets the replies of its parts, concatenated.
    """

    def __init__(self, table):
        self.table = table
        self.calls = []
        self.copies = []

    def reply(self, host, cmd):
        self.calls.append((host.name, cmd))
        return "".join(self.part(host, part) for part in cmd.split(" && "))

    def part(self, host, cmd):
        for name in (host.name, "*"):
            for (key_host, prefix), value in self.table.items():
                if key_host == name and cmd.startswith(prefix):
                    if isinstance(value, list):
                        value = value.pop(0) if len(value) > 1 else value[0]
                    if isinstance(value, BaseException):
                        raise value
                    return value(cmd) if callable(value) else value
        raise AssertionError(f"no reply for {host.name}: {cmd}")

    def run(self, host, cmd, timeout=None):
        return self.reply(host, cmd)

    def run_many(self, jobs, timeout=None):
        results = []
        for host, cmd in jobs:
            try:
                results.append(self.reply(host, cmd))
            except SshError as exc:
                results.append(exc)
        return results

    def copy_from(self, host, remote, local):
        self.copies.append((host.name, remote))
        local.write_text(f"copy of {remote}\n")


def label(cmd):
    for prefix, name in ((LOAD, "load"), (START, "start"), (PIDS, "pids"), (STOP, "stop"), (LOG, "log"), (MEM, "mem"),
                         (PROBE, "probe"), (WAIT, "snapshot"), (FACTS, "facts")):
        if cmd.startswith(prefix):
            return name
    return cmd


# One stage batch: the load of 2 loaders, then the begin and end snapshots of the 3 boxes, then the RSS read.
STAGE = ["load"] * 2 + ["snapshot"] * 6 + ["mem"]

NO_NUMBERS = {"rate": None, "achieved_rps": None, "successful_rps": None, "errors": None, "latency_us": None, "rss_kb": None, "held": None, "cpu": None}

CELL_CASES = [
    {
        "name": "holds the rate",
        "overrides": {},
        "status": "ok",
        "reason": None,
        "held": True,
        "achieved_rps": 250000.0,
        "rss_kb": 204800,
        "flags": {},
        "loads": 2,
    },
    {
        # 228571 req/s is under 95% of 250000. Every loader is at 90% and the server at 50%.
        "name": "does not hold the rate and is generator bound",
        "overrides": {("*", LOAD): load_reply(SHORT_WRK2)},
        "status": "ok",
        "reason": None,
        "held": False,
        "achieved_rps": 16000000 / 70,
        "rss_kb": 204800,
        "flags": {"generator_bound": True},
        "loads": 2,
    },
    {
        "name": "status errors at the full rate do not hold",
        "overrides": {("*", LOAD): load_reply(status=5)},
        "status": "ok",
        "reason": None,
        "held": False,
        "achieved_rps": 250000.0,
        "rss_kb": 204800,
        "flags": {"generator_bound": True},
        "loads": 2,
    },
    {
        "name": "probe mismatch on loader-2 voids before load",
        "overrides": {("loader-2", PROBE): SshError("loader-2: exit 1: bash bench-rig/box/probe.sh\n/home/fedora/bench-rig/apps/hello/expect.txt /tmp/tmp.Xy12 differ: byte 7, line 1")},
        "status": "void",
        "reason": "probe mismatch on loader-2: /home/fedora/bench-rig/apps/hello/expect.txt /tmp/tmp.Xy12 differ: byte 7, line 1",
        "held": None,
        "achieved_rps": None,
        "rss_kb": None,
        "flags": {},
        "loads": 0,
    },
    {
        "name": "missing RESULT from loader-2 voids with the loader name",
        "overrides": {("loader-2", LOAD): "wrk2: panic: bytecode\n"},
        "status": "void",
        "reason": "loader-2",
        "held": None,
        "achieved_rps": None,
        "rss_kb": None,
        "flags": {},
        "loads": 2,
    },
    {
        "name": "loader-1 late by 1500 ms voids",
        "overrides": {("loader-1", LOAD): load_reply(late_ms=1500)},
        "status": "void",
        "reason": "loader-1",
        "held": None,
        "achieved_rps": None,
        "rss_kb": None,
        "flags": {},
        "loads": 2,
    },
    {
        "name": "failed probe after the stage sets died",
        "overrides": {("loader-1", PROBE): ["", SshError("loader-1: exit 1: probe")]},
        "status": "ok",
        "reason": None,
        "held": True,
        "achieved_rps": 250000.0,
        "rss_kb": 204800,
        "flags": {"died": True},
        "loads": 2,
    },
    {
        # The stage completes; the void at the stop clears the numbers.
        "name": "a WARN line in the rapira log voids after the stage",
        "overrides": {("server", LOG): "2026-09-25T10:00:00Z WARN worker restarted\n"},
        "status": "void",
        "reason": "server log: 1 warn or error lines",
        "held": None,
        "achieved_rps": None,
        "rss_kb": None,
        "flags": {},
        "loads": 2,
    },
]

INTERRUPT_CASES = [
    {"name": "interrupt during the load", "overrides": {("loader-2", LOAD): KeyboardInterrupt()}},
    {"name": "interrupt during target.sh stop", "overrides": {("server", STOP): KeyboardInterrupt()}},
]


def bench(boxes, out, targets, connections=5000, threads=THREADS):
    with mock.patch("rig.bench.ensure_ttl") as ttl:
        path = run_suite(
            RIG, suite(targets, connections), boxes, out, suite_path=SUITE_FILE, processes=32, run_id="run1", rapira=RAPIRA,
            servers={}, apps={}, loader_threads=threads,
        )
    return path, ttl


class RunSuiteTest(unittest.TestCase):
    def test_cells(self):
        for case in CELL_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                boxes = FakeBoxes(replies(case["overrides"]))
                path, _ = bench(boxes, Path(tmp), [WORKER])
                run = json.loads(path.read_text())
                cell = run["cells"][0]
                self.assertEqual(cell["status"], case["status"])
                if case["reason"]:
                    self.assertIn(case["reason"], cell["reason"])
                self.assertEqual(cell["held"], case["held"])
                self.assertEqual(cell["achieved_rps"], case["achieved_rps"])
                self.assertEqual(cell["rss_kb"], case["rss_kb"])
                if case["status"] != "ok":
                    self.assertEqual({key: cell[key] for key in NO_NUMBERS}, NO_NUMBERS)
                self.assertEqual(cell["flags"], case["flags"])
                self.assertEqual(sum(1 for _, cmd in boxes.calls if label(cmd) == "load"), case["loads"])
                self.assertEqual([label(cmd) for _, cmd in boxes.calls][-2:], ["stop", "log"])
                self.assertEqual(run["status"], "complete" if case["status"] == "ok" else "incomplete")

    def test_grpc_cell_counts_the_measured_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({("server", START): "config=/opt/bench/run/r1-grpc-rapira.toml\npid=100\n"}))
            path, _ = bench(boxes, Path(tmp), [GRPC])
            cell = json.loads(path.read_text())["cells"][0]
            # 2 x 3000000 requests over the 60 s window.
            self.assertEqual((cell["status"], cell["held"], cell["achieved_rps"], cell["rate"]), ("ok", True, 100000.0, 100000))
            load = next(cmd for _, cmd in boxes.calls if label(cmd) == "load")
            # 50000 req/s per loader, 4 threads, 100 / 2 = 50 connections, 100 streams, 10 s warm-up, 60 s window.
            self.assertEqual(shlex.split(load)[2:3] + shlex.split(load)[4:], ["h2load", "50000", "4", "50", "100", "10", "60", GRPC_URL, "apps/grpc/echo.grpc"])

    def test_command_order_and_raw_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({}))
            path, ttl = bench(boxes, Path(tmp), [WORKER])
            expected = ["facts"] * 3 + ["start", "pids"] + ["probe"] * 2 + STAGE + ["probe", "pids", "stop", "log"]
            self.assertEqual([label(cmd) for _, cmd in boxes.calls], expected)
            self.assertEqual(boxes.calls[3], ("server", "bash bench-rig/box/target.sh start r1-hello-rapira-worker rapira 32 /opt/bench/rapira/abc1234 worker @RIG@/apps/hello/worker.php"))
            # probe.sh resolves a relative expect file under the staged rig.
            self.assertEqual(shlex.split(boxes.calls[5][1]), ["bash", "bench-rig/box/probe.sh", URL, "apps/hello/expect.txt", "http1", "GET", "-"])
            # 125000 req/s per loader, 4 threads, 5000 / 2 = 2500 connections, 10 s warm-up, 60 s window.
            self.assertEqual(shlex.split(boxes.calls[7][1])[4:], ["125000", "4", "2500", "10", "60", URL, "GET", "-"])
            raw = path.parent / "raw" / "r1-hello-rapira-worker"
            self.assertEqual(sorted(p.name for p in raw.iterdir()), ["config.toml", "load-loader-1.txt", "load-loader-2.txt", "server.log", "snapshots.txt"])
            self.assertEqual(boxes.copies, [("server", "/opt/bench/run/r1-hello-rapira-worker.toml")])
            # One cell: 1 x (10 + 60 + 60) + 300 seconds.
            ttl.assert_called_once_with([SERVER, *LOADERS], 430)

    def test_interrupt_stops_the_target_and_writes_an_incomplete_run(self):
        for case in INTERRUPT_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                boxes = FakeBoxes(replies(case["overrides"]))
                with self.assertRaises(KeyboardInterrupt):
                    bench(boxes, Path(tmp), [WORKER, GRPC])
                run = json.loads((Path(tmp) / "run1" / "run.json").read_text())
                self.assertEqual(run["status"], "incomplete")
                self.assertEqual(run["plan"], ["r1-hello-rapira-worker", "r1-grpc-rapira"])
                self.assertEqual([(c["key"], c["status"]) for c in run["cells"]], [("r1-hello-rapira-worker", "incomplete")])
                self.assertEqual({key: run["cells"][0][key] for key in NO_NUMBERS}, NO_NUMBERS)
                self.assertIn(("server", "bash bench-rig/box/target.sh stop r1-hello-rapira-worker rapira"), boxes.calls)


PLAN = {"conns_per_loader": {"http1": 2500, "grpc": 50}, "loader_threads": THREADS}

LOAD_CMD_CASES = [
    {
        "name": "wrk2 gets the threads, the connections of one loader, and the request shape",
        "target": WORKER,
        "rate": 125000,
        "expected": ["bash", "bench-rig/box/load.sh", "wrk2", "100.000", "125000", "4", "2500", "10", "60", URL, "GET", "-"],
    },
    {
        "name": "h2load gets the streams and the body path under the staged rig",
        "target": GRPC,
        "rate": 50000,
        "expected": ["bash", "bench-rig/box/load.sh", "h2load", "100.000", "50000", "4", "50", "100", "10", "60", GRPC_URL, "apps/grpc/echo.grpc"],
    },
]

COUNTED_CASES = [
    {"name": "wrk2 counts the warm-up and the window", "target": WORKER, "expected": 70},
    {"name": "h2load counts the window only", "target": GRPC, "expected": 60},
]

WAIT_ARGV = ["python3", "-c", "import sys, time; time.sleep(max(0.0, float(sys.argv[1]) - time.time()))"]

SAMPLE_JOBS_CASES = [
    {
        "name": "server samples pass the port, loader samples pass no port",
        "epoch": 110.0,
        "duration_s": 60,
        "expected": {
            "server": [
                [*WAIT_ARGV, "110.000", "&&", "bash", "bench-rig/box/snapshot.sh", "8080"],
                [*WAIT_ARGV, "170.500", "&&", "bash", "bench-rig/box/snapshot.sh", "8080"],
            ],
            "loader-1": [
                [*WAIT_ARGV, "110.000", "&&", "bash", "bench-rig/box/snapshot.sh"],
                [*WAIT_ARGV, "170.500", "&&", "bash", "bench-rig/box/snapshot.sh"],
            ],
        },
    },
]

PLAN_CASES = [
    {"name": "5000 over 2 loaders x 4 threads", "connections": 5000, "threads": 4, "error": None, "per_loader": {"http1": 2500, "grpc": 50}},
    {"name": "4000 over 2 loaders x 4 threads", "connections": 4000, "threads": 4, "error": None, "per_loader": {"http1": 2000, "grpc": 50}},
    # 5000 % 16 = 8.
    {"name": "5000 is not a multiple of 2 loaders x 8 threads", "connections": 5000, "threads": 8, "error": "connections 5000", "per_loader": None},
    {"name": "4 is under one connection per thread", "connections": 4, "threads": 4, "error": "connections 4", "per_loader": None},
]


class LoadCmdTest(unittest.TestCase):
    def test_load_cmd(self):
        for case in LOAD_CMD_CASES:
            with self.subTest(name=case["name"]):
                cmd = load_cmd(case["target"], f"http://10.0.0.1:8080{case['target'].url}", 100.0, case["rate"], PLAN, suite([WORKER, GRPC]))
                self.assertEqual(shlex.split(cmd), case["expected"])

    def test_counted_s(self):
        for case in COUNTED_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(counted_s(case["target"], suite([WORKER, GRPC])), case["expected"])


class SampleJobsTest(unittest.TestCase):
    def test_sample_jobs(self):
        for case in SAMPLE_JOBS_CASES:
            with self.subTest(name=case["name"]):
                jobs = sample_jobs(RIG, case["epoch"], case["duration_s"])
                for name, expected in case["expected"].items():
                    self.assertEqual([shlex.split(cmd) for _, host, cmd in jobs if host.name == name], expected)


class PlanRunTest(unittest.TestCase):
    def test_plan_run(self):
        for case in PLAN_CASES:
            with self.subTest(name=case["name"]):
                s = suite([WORKER, GRPC], case["connections"])
                if case["error"]:
                    with self.assertRaisesRegex(SuiteError, case["error"]):
                        plan_run(RIG, s, processes=32, loader_threads=case["threads"])
                    continue
                plan = plan_run(RIG, s, processes=32, loader_threads=case["threads"])
                self.assertEqual(plan["conns_per_loader"], case["per_loader"])
                self.assertEqual(plan["keys"], ["r1-hello-rapira-worker", "r1-grpc-rapira"])

    def test_refused_suite_creates_no_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({}))
            with self.assertRaisesRegex(SuiteError, "connections 5000"):
                bench(boxes, Path(tmp), [WORKER], threads=8)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertEqual(boxes.calls, [])
```

- [ ] **Step 3: Write the failing run file test and adjust the flags, merge, and rig tests**

Replace `tests/test_runfile.py` with:

```python
"""Tests of the run file writer and the run status rules."""

import json
import tempfile
import unittest
from pathlib import Path

from rig.runfile import SCHEMA, RunFile, run_status


def cell(key, status="ok", reason=None):
    """A cell with the fields that the status rules read."""
    out = {"key": key, "status": status}
    if reason is not None:
        out["reason"] = reason
    return out


PLAN = ["r1-hello-rapira-worker", "r1-grpc-rapira"]

STATUS_CASES = [
    {
        "name": "every planned cell ok",
        "cells": [cell("r1-hello-rapira-worker"), cell("r1-grpc-rapira")],
        "status": "complete",
        "reasons": [],
    },
    {
        "name": "planned cell missing",
        "cells": [cell("r1-hello-rapira-worker")],
        "status": "incomplete",
        "reasons": ["r1-grpc-rapira: missing"],
    },
    {
        "name": "void cell",
        "cells": [cell("r1-hello-rapira-worker"), cell("r1-grpc-rapira", "void", "probe mismatch on loader-1")],
        "status": "incomplete",
        "reasons": ["r1-grpc-rapira: void: probe mismatch on loader-1"],
    },
    {
        "name": "incomplete cell",
        "cells": [cell("r1-hello-rapira-worker", "incomplete"), cell("r1-grpc-rapira")],
        "status": "incomplete",
        "reasons": ["r1-hello-rapira-worker: incomplete"],
    },
    {
        "name": "unplanned cell",
        "cells": [cell("r1-hello-rapira-worker"), cell("r1-grpc-rapira"), cell("r2-grpc-rapira")],
        "status": "incomplete",
        "reasons": ["r2-grpc-rapira: unplanned cell"],
    },
    {
        "name": "reasons follow the plan order, unplanned cells last",
        "cells": [cell("r9-yii3-rapira-dispatcher"), cell("r1-grpc-rapira", "void", "loader loader-1 returned no RESULT line")],
        "status": "incomplete",
        "reasons": [
            "r1-hello-rapira-worker: missing",
            "r1-grpc-rapira: void: loader loader-1 returned no RESULT line",
            "r9-yii3-rapira-dispatcher: unplanned cell",
        ],
    },
]

# The top-level keys in the order of spec section 6.1, with `reasons` after `status`.
TOP_KEYS = [
    "schema", "id", "suite", "smoke", "started", "finished", "rig", "rapira", "servers", "apps",
    "loaders", "processes", "plan", "cells", "status", "reasons", "reporter",
]

CELL = {
    "key": "r1-hello-rapira-worker",
    "target": {"name": "hello-rapira-worker", "app": "hello", "mode": "worker", "proto": "http1"},
    "round": 1,
    "status": "ok",
    "flags": {},
    "rate": 250000,
    "achieved_rps": 249996.6,
    "successful_rps": 249996.6,
    "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
    "latency_us": {"p50": 689, "p90": 1111, "p99": 1264, "p999": 1351, "max": 2822},
    "rss_kb": 912384,
    "held": True,
    "cpu": {"server_busy": 62, "loader_busy": 71},
    "loaders": [],
}

PR = {"number": 59, "url": "https://github.com/rapira-rs/rapira/pull/59", "title": "Faster hello"}


def new_run_file():
    return RunFile(
        run_id="20260925T120000Z-ci-0a1b2c3",
        suite={"name": "ci", "file_sha256": "ab" * 32, "rounds": 1, "warmup_s": 10, "duration_s": 60, "rates": {"hello": 250000}, "connections": {"http1": 5000, "grpc": 100}},
        rig={"server_type": "c7a.8xlarge", "loader_type": "c7a.2xlarge", "loader_count": 1, "az": "eu-central-1a"},
        rapira={"ref": "nightly", "sha": "0a1b2c3d", "version": "0.9.0", "build": "nightly", "pr": PR},
        servers={"php": "PHP 8.5.10 (cli)"},
        apps={"apps/hello/worker.php": "cd" * 32},
        loaders=[{"name": "loader-1", "private_ip": "10.0.1.11", "wrk2": "44a94c1", "h2load": "h2load nghttp2/1.68.0"}],
        processes=32,
        plan=["r1-hello-rapira-worker"],
        smoke=False,
        started="2026-09-25T12:00:00Z",
    )


class TestRunStatus(unittest.TestCase):
    def test_run_status(self):
        for case in STATUS_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(run_status(PLAN, case["cells"]), (case["status"], case["reasons"]))


class TestRunFile(unittest.TestCase):
    def test_written_document_round_trips_with_numbers(self):
        run = new_run_file()
        run.add_cell(CELL)
        doc = run.finish("2026-09-25T12:40:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            run.write(path)
            text = path.read_text()
        loaded = json.loads(text)
        self.assertEqual(loaded, doc)
        self.assertEqual(list(loaded), TOP_KEYS)
        self.assertEqual(loaded["schema"], SCHEMA)
        self.assertEqual((loaded["status"], loaded["reasons"]), ("complete", []))
        self.assertEqual(loaded["rapira"]["pr"], PR)
        # indent=1 puts one space before each top-level key.
        self.assertEqual(text.splitlines()[1], ' "schema": "rapira-bench-run/2",')
        self.assertTrue(text.endswith("}\n"))
        cell = loaded["cells"][0]
        self.assertIsInstance(cell["rate"], int)
        self.assertIsInstance(cell["achieved_rps"], float)
        self.assertIsInstance(cell["held"], bool)
        self.assertIsInstance(loaded["processes"], int)

    def test_finish_with_a_void_cell_is_incomplete(self):
        run = new_run_file()
        run.add_cell({"key": "r1-hello-rapira-worker", "status": "void", "reason": "loader loader-1 throttled: pps_allowance_exceeded=5"})
        doc = run.finish("2026-09-25T12:40:00Z")
        self.assertEqual(doc["finished"], "2026-09-25T12:40:00Z")
        self.assertEqual(doc["status"], "incomplete")
        self.assertEqual(doc["reasons"], ["r1-hello-rapira-worker: void: loader loader-1 throttled: pps_allowance_exceeded=5"])


if __name__ == "__main__":
    unittest.main()
```

In `tests/test_flags.py`, `STAGE_FLAG_CASES` use the key `"held"` in place of `"passed"`, the first case is named `"a stage that held has no flags"`, and `test_stage_flags` passes `case["held"]`.

In `tests/test_merge.py`, rename the `"stage_s"` key of `MERGE_CASES` to `"window_s"` and pass `case["window_s"]`; the comment of the four-loader case says "over the 20 s window".

In `tests/test_rig.py`:
- `NEEDS_CASES`: the first case has `"targets": [("yii3", "rapira"), ("hello", "rapira"), ("grpc", "rapira")]` and `"expected": ["rapira", "grpc", "hello", "yii3"]`; the second case stays.
- `PROVISION_CASES`: delete `"BASE_REF": "main"` from both `env` dicts; `server_cmd` becomes `"NIGHTLY=abc1234 REF='' NEEDS='rapira hello yii3' bash bench-rig/box/provision-server.sh"` with `NEEDS` `"rapira hello yii3"` in the first env, and `"NIGHTLY='' REF=pr/97 NEEDS='rapira hello' bash bench-rig/box/provision-server.sh"` in the second.
- `target()`: delete `binary=None`.
- `NeedsTest`: the `Suite` is `Suite(name="t", rounds=1, warmup_s=10, duration_s=60, smoke=False, rates={}, connections={}, grpc_streams=1, targets=...)`.

Delete `tests/test_ladder.py`.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_registry tests.test_bench tests.test_runfile tests.test_flags tests.test_merge tests.test_rig 2>&1 | tail -5`
Expected: errors on import (`counted_s` does not exist, `Suite` has no `warmup_s`, `Target` has no `binary` default) and failures in every rewritten case.

- [ ] **Step 5: Write the registry and the suites**

Replace `rig/registry.py` with:

```python
"""Load the target registry and a suite file, and plan the cells of a run."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

SERVERS = ("rapira",)
APPS = ("hello", "yii3", "grpc")
MODES = ("worker", "classic", "dispatcher")
PROTOS = ("http1", "grpc")
MIN_DURATION_S = 30


class SuiteError(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    name: str
    server: str
    app: str
    mode: str
    proto: str
    start: tuple[str, ...]
    url: str
    expect: str
    config: str
    method: str = "GET"
    headers: tuple[tuple[str, str], ...] = ()
    body: str | None = None


@dataclass(frozen=True)
class Suite:
    name: str
    rounds: int
    warmup_s: int
    duration_s: int
    smoke: bool
    rates: dict[str, int]
    connections: dict[str, int]
    grpc_streams: int
    targets: tuple[Target, ...]


def _target(name: str, table: dict) -> Target:
    fields = dict(table)
    fields["start"] = tuple(fields.get("start", ()))
    fields["headers"] = tuple(fields.get("headers", {}).items())
    try:
        target = Target(name=name, **fields)
    except TypeError as exc:
        raise SuiteError(f"target {name}: {exc}") from None
    for field, allowed in (("server", SERVERS), ("app", APPS), ("mode", MODES), ("proto", PROTOS)):
        value = getattr(target, field)
        if value not in allowed:
            raise SuiteError(f"target {name}: unknown {field} {value}")
    return target


def load_targets(path: Path) -> dict[str, Target]:
    with path.open("rb") as f:
        tables = tomllib.load(f)
    return {name: _target(name, table) for name, table in tables.items()}


def load_suite(path: Path, targets: dict[str, Target], loader_count: int) -> Suite:
    with path.open("rb") as f:
        doc = tomllib.load(f)
    where = path.name
    if doc["rounds"] < 1:
        raise SuiteError(f"{where}: rounds {doc['rounds']} is under 1")
    if doc["duration_s"] < MIN_DURATION_S:
        raise SuiteError(f"{where}: duration_s {doc['duration_s']} is under {MIN_DURATION_S}")
    seen = set()
    for name in doc["targets"]:
        if name not in targets:
            raise SuiteError(f"{where}: unknown target {name}")
        if name in seen:
            raise SuiteError(f"{where}: target {name} is listed twice")
        seen.add(name)
    chosen = tuple(targets[name] for name in doc["targets"])
    rates = dict(doc["rates"])
    connections = dict(doc["connections"])
    for target in chosen:
        if target.app not in rates:
            raise SuiteError(f"{where}: no rate for app {target.app}")
        if target.proto not in connections:
            raise SuiteError(f"{where}: no connections for proto {target.proto}")
    # Each loader sends its share of the rate and opens its share of the connections.
    for app, rate in rates.items():
        if rate % loader_count != 0:
            raise SuiteError(f"{where}: rate {rate} of app {app} is not a multiple of {loader_count} loaders")
    for proto, count in connections.items():
        if count % loader_count != 0:
            raise SuiteError(f"{where}: connections {count} of proto {proto} is not a multiple of {loader_count} loaders")
    return Suite(
        name=doc["name"],
        rounds=doc["rounds"],
        warmup_s=doc["warmup_s"],
        duration_s=doc["duration_s"],
        smoke=doc.get("smoke", False),
        rates=rates,
        connections=connections,
        grpc_streams=doc["grpc"]["streams"],
        targets=chosen,
    )


def plan_cells(suite: Suite) -> list[tuple[int, Target]]:
    # Round r starts at target index (r - 1) % n and wraps, so every target
    # takes each position once over n rounds.
    n = len(suite.targets)
    return [
        (round_no, suite.targets[(i + round_no - 1) % n])
        for round_no in range(1, suite.rounds + 1)
        for i in range(n)
    ]


def cell_key(round_no: int, target: Target) -> str:
    return f"r{round_no}-{target.name}"


def suite_needs(suite: Suite) -> list[str]:
    """Server kinds, then apps, of the suite targets. Provisioning installs only these."""
    return sorted({target.server for target in suite.targets}) + sorted({target.app for target in suite.targets})
```

Replace `suites/targets.toml` with:

```toml
# The target registry. The driver reads it with tomllib.
# A name is <app>-rapira-<mode>, with the suffix -static for the static middleware. The gRPC target is grpc-rapira.
# The start arguments follow the box protocol of box/target.sh.
# On the box @RIG@ expands to $HOME/bench-rig and @APPS@ expands to /opt/bench/apps.
# The expect, body, and config paths are relative to the repository root.

[hello-rapira-classic]
server = "rapira"
app = "hello"
mode = "classic"
proto = "http1"
start = ["classic", "@RIG@/apps/hello/classic.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"

[hello-rapira-worker]
server = "rapira"
app = "hello"
mode = "worker"
proto = "http1"
start = ["worker", "@RIG@/apps/hello/worker.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"

[hello-rapira-dispatcher]
server = "rapira"
app = "hello"
mode = "dispatcher"
proto = "http1"
start = ["dispatcher", "@RIG@/apps/hello/dispatcher.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"

# The static middleware serves apps/hello as its root. The request misses the root and reaches PHP.
[hello-rapira-dispatcher-static]
server = "rapira"
app = "hello"
mode = "dispatcher"
proto = "http1"
start = ["dispatcher", "@RIG@/apps/hello/dispatcher.php", "@RIG@/servers/rapira/static.toml.tpl"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/static.toml.tpl"

# The Yii3 app-api at the commit of apps/yii3/source.toml. Its entry point detects the rapira mode.
[yii3-rapira-dispatcher]
server = "rapira"
app = "yii3"
mode = "dispatcher"
proto = "http1"
start = ["dispatcher", "@APPS@/yii3/worker-rapira.php"]
url = "/"
expect = "apps/yii3/expect.json"
config = "servers/rapira/http.toml.tpl"

# The gRPC target calls bench.v1.EchoService/Echo over h2c under h2load.
[grpc-rapira]
server = "rapira"
app = "grpc"
mode = "dispatcher"
proto = "grpc"
start = ["grpc", "@RIG@/apps/grpc/php/dispatcher.php"]
url = "/bench.v1.EchoService/Echo"
expect = "apps/grpc/expect.grpc"
config = "servers/rapira/grpc.toml.tpl"
method = "POST"
body = "apps/grpc/echo.grpc"
```

Replace `suites/ci.toml` with:

```toml
# The per-merge suite. CI runs it after every nightly build of the rapira main branch.
# Every target runs one stage: warmup_s seconds of warm-up, then duration_s seconds measured,
# at the rate of its app, with the connections of its proto.
name = "ci"
rounds = 1
warmup_s = 10
duration_s = 60
smoke = false
targets = [
  "hello-rapira-classic",
  "hello-rapira-worker",
  "hello-rapira-dispatcher",
  "hello-rapira-dispatcher-static",
  "yii3-rapira-dispatcher",
  "grpc-rapira",
]

[rates]
hello = 250000
yii3 = 250000
grpc = 100000

[connections]
http1 = 5000
grpc = 100

[grpc]
streams = 100
```

Delete `suites/full.toml`, `suites/ab.toml`, `rig/ladder.py`, and `tests/test_ladder.py`.

- [ ] **Step 6: Change merge, flags, runfile, and the version**

`rig/merge.py`: the module docstring becomes `"""Parse the RESULT line of a load process and merge the loader records of one stage."""` (unchanged); `merge(records, stage_s)` becomes `merge(records: dict[str, LoaderRecord | None], window_s: int) -> Merged` with the docstring `"""Merge the loader records. window_s is the seconds that the requests counts of the records cover."""`, and the two divisions use `window_s`.

`rig/flags.py`: `stage_flags(server_busy: int, loader_busy: dict[str, int], held: bool) -> dict` with the docstring `"""Review flags of one stage. A stage that held its rate has no flags."""` and the first line `if held or server_busy >= SERVER_BUSY:`.

`rig/runfile.py`: `SCHEMA = "rapira-bench-run/2"`; the class docstring names `rapira-bench-run/2`; `__init__` loses the `ladder` parameter and the `"ladder": ladder,` line.

`rig/__init__.py`: `VERSION = "2"`.

- [ ] **Step 7: Write the driver**

Replace `rig/bench.py` with:

```python
"""The cell sequence of one suite run: one constant-rate stage per target."""

import hashlib
import json
import shlex
import time
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from rig import ssh
from rig.flags import cell_flags, cpu_pct, ena_delta, keepalive_flag, parse_snapshot, stage_flags, stage_void
from rig.merge import merge, parse_result
from rig.registry import Suite, SuiteError, Target, cell_key, plan_cells
from rig.rig import Rig, ensure_ttl
from rig.runfile import RunFile
from rig.ssh import Host, SshError

PORT = 8080
LEAD_S = 3
# A cell holds its rate when it achieves this share of it without an error.
HELD_TOLERANCE = 0.95
BOX = f"bash {ssh.RIG_DIR}/box"
BENCH_DIR = "/opt/bench"
# ssh slack over the lead time and the load time of one load call.
LOAD_SLACK_S = 60
SNAPSHOT_TIMEOUT_S = 30
# The start, the probes, the stop, and the drain of one cell, for the TTL estimate.
CELL_OVERHEAD_S = 60
TTL_MARGIN_S = 300
# Sleep until the wall clock time in argv[1].
WAIT_PY = "import sys, time; time.sleep(max(0.0, float(sys.argv[1]) - time.time()))"
FACTS_CMD = (
    "echo kernel=$(uname -r); "
    "echo instance_id=$(cat /sys/devices/virtual/dmi/id/board_asset_tag); "
    "t=$(curl -s -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' http://169.254.169.254/latest/api/token); "
    "m=http://169.254.169.254/latest/meta-data/placement; "
    "echo az=$(curl -s -H \"X-aws-ec2-metadata-token: $t\" $m/availability-zone); "
    "echo placement_group=$(curl -s -H \"X-aws-ec2-metadata-token: $t\" $m/group-name)"
)
# Provisioning writes the wrk2 commit and the h2load version to loader.json.
TOOLS_CMD = (
    "python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); "
    "print(\"wrk2=\" + d[\"wrk2_commit\"]); print(\"h2load=\" + d[\"h2load_version\"])' "
    f"{BENCH_DIR}/loader.json"
)
# The measured fields of a cell. A cell that is not ok has null values here.
NUMBER_KEYS = ("rate", "achieved_rps", "successful_rps", "errors", "latency_us", "rss_kb", "held", "cpu")
LATENCY_KEYS = ("p50", "p90", "p99", "p999", "max")


class Boxes(Protocol):
    def run(self, host: Host, cmd: str, timeout: float | None = None) -> str: ...
    def run_many(self, jobs: list[tuple[Host, str]], timeout: float | None = None) -> list[str | SshError]: ...
    def copy_from(self, host: Host, remote: str, local: Path) -> None: ...


class SshBoxes:
    """The Boxes of a real rig."""

    def run(self, host: Host, cmd: str, timeout: float | None = None) -> str:
        return ssh.run(host, cmd, timeout=timeout)

    def run_many(self, jobs: list[tuple[Host, str]], timeout: float | None = None) -> list[str | SshError]:
        return ssh.run_many(jobs, timeout=timeout)

    def copy_from(self, host: Host, remote: str, local: Path) -> None:
        ssh.copy_from(host, remote, local)


class CellVoid(Exception):
    """The cell is excluded from every number. The message is the reason."""


def plan_run(rig: Rig, suite: Suite, *, processes: int, loader_threads: int) -> dict:
    """Check the suite against the rig and return the cells, the keys, and the load shape."""
    loaders = len(rig.loaders)
    http1 = suite.connections["http1"]
    # wrk2 divides the connections of a process over its threads and drops the remainder.
    if http1 % (loaders * loader_threads) != 0:
        raise SuiteError(f"connections {http1} is not a multiple of {loaders} loaders x {loader_threads} threads")
    cells = plan_cells(suite)
    return {
        "cells": cells,
        "keys": [cell_key(round_no, target) for round_no, target in cells],
        "conns_per_loader": {proto: count // loaders for proto, count in suite.connections.items()},
        "loader_threads": loader_threads,
        "processes": processes,
    }


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def request_args(target: Target) -> list[str]:
    """METHOD, BODY_FILE, and HEADER arguments of probe.sh and load.sh. A relative file is under the staged rig."""
    body = target.body or "-"
    return [target.method, body, *(f"{name}: {value}" for name, value in target.headers)]


def box_cmd(script: str, *args: object) -> str:
    return " ".join([f"{BOX}/{script}", *(shlex.quote(str(arg)) for arg in args)])


def probe_cmd(target: Target, url: str) -> str:
    return box_cmd("probe.sh", url, target.expect, target.proto, *request_args(target))


def load_cmd(target: Target, url: str, epoch: float, rate: int, plan: dict, suite: Suite) -> str:
    """One load.sh call of one loader: h2load for the grpc proto, wrk2 for http1."""
    conns = plan["conns_per_loader"][target.proto]
    if target.proto == "grpc":
        return box_cmd(
            "load.sh", "h2load", f"{epoch:.3f}", rate, plan["loader_threads"], conns, suite.grpc_streams,
            suite.warmup_s, suite.duration_s, url, target.body,
        )
    return box_cmd(
        "load.sh", "wrk2", f"{epoch:.3f}", rate, plan["loader_threads"], conns, suite.warmup_s, suite.duration_s, url,
        *request_args(target),
    )


def counted_s(target: Target, suite: Suite) -> int:
    """The seconds that the requests count of the tool covers: wrk2 counts the whole run, h2load the measured window."""
    if target.proto == "grpc":
        return suite.duration_s
    return suite.warmup_s + suite.duration_s


def parse_facts(text: str) -> dict:
    facts = {}
    for line in text.splitlines():
        name, sep, value = line.partition("=")
        if sep:
            facts[name] = value
    return facts


def host_facts(boxes: Boxes, rig: Rig) -> dict[str, dict]:
    """Kernel, instance id, AZ, placement group, and on loaders the tool versions."""
    jobs = [(rig.server, FACTS_CMD)] + [(loader, f"{FACTS_CMD}; {TOOLS_CMD}") for loader in rig.loaders]
    facts = {}
    for (host, _), out in zip(jobs, boxes.run_many(jobs, timeout=SNAPSHOT_TIMEOUT_S)):
        if isinstance(out, SshError):
            raise out
        facts[host.name] = parse_facts(out)
    return facts


def server_versions(boxes: Boxes, server: Host) -> dict:
    """The version lines that provisioning recorded and the shared php.ini text."""
    versions = json.loads(boxes.run(server, f"cat {BENCH_DIR}/versions.json"))
    versions["php_ini"] = boxes.run(server, f"cat {BENCH_DIR}/php.ini")
    return versions


def app_hashes(root: Path) -> dict[str, str]:
    """SHA-256 of every staged file under apps/, the lock files and the expected bodies included."""
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ssh.tree_files(root)
        if name.startswith("apps/")
    }


def suite_record(suite: Suite, path: Path) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "name": suite.name,
        "file_sha256": digest,
        "rounds": suite.rounds,
        "warmup_s": suite.warmup_s,
        "duration_s": suite.duration_s,
        "rates": dict(suite.rates),
        "connections": dict(suite.connections),
    }


def target_record(target: Target) -> dict:
    return {**asdict(target), "start": list(target.start), "headers": dict(target.headers)}


def at_time(at: float, cmd: str) -> str:
    """cmd after a wait until the wall clock time at."""
    return f"python3 -c {shlex.quote(WAIT_PY)} {at:.3f} && {cmd}"


def sample_jobs(rig: Rig, epoch: float, duration_s: int) -> list[tuple[str, Host, str]]:
    """Snapshot jobs of every box at the start of the measured window and 0.5 s after its end.

    The server snapshots count the connections on PORT.
    """
    jobs = []
    for mark, at in (("begin", epoch), ("end", epoch + duration_s + 0.5)):
        jobs.append((mark, rig.server, at_time(at, box_cmd("snapshot.sh", PORT))))
        jobs += [(mark, loader, at_time(at, box_cmd("snapshot.sh"))) for loader in rig.loaders]
    return jobs


def run_stage(boxes: Boxes, rig: Rig, suite: Suite, plan: dict, target: Target, cell: dict, cell_dir: Path) -> None:
    """Run the load from every loader, read the RSS, and fill the numbers of the cell."""
    server = rig.server
    url = f"http://{server.private_ip}:{PORT}{target.url}"
    rate = suite.rates[target.app]
    per_loader = rate // len(rig.loaders)
    epoch = time.time() + LEAD_S
    loads = [(loader, load_cmd(target, url, epoch, per_loader, plan, suite)) for loader in rig.loaders]
    samples = sample_jobs(rig, epoch + suite.warmup_s, suite.duration_s)
    timeout = LEAD_S + suite.warmup_s + suite.duration_s + LOAD_SLACK_S
    results = boxes.run_many(loads + [(host, cmd) for _, host, cmd in samples], timeout=timeout)

    taken = {}
    with (cell_dir / "snapshots.txt").open("w") as log:
        for (mark, host, _), out in zip(samples, results[len(loads):]):
            if isinstance(out, SshError):
                raise out
            log.write(f"# {mark} {host.name}\n{out}")
            taken[mark, host.name] = out
    before = {host.name: parse_snapshot(taken["begin", host.name]) for host in rig.hosts}
    after = {host.name: parse_snapshot(taken["end", host.name]) for host in rig.hosts}

    records = {}
    for loader, out in zip(rig.loaders, results):
        text = str(out) if isinstance(out, SshError) else out
        (cell_dir / f"load-{loader.name}.txt").write_text(text)
        try:
            records[loader.name] = None if isinstance(out, SshError) else parse_result(out, loader.name)
        except ValueError as exc:
            raise CellVoid(f"{loader.name}: {exc}") from exc
    try:
        merged = merge(records, counted_s(target, suite))
    except ValueError as exc:
        raise CellVoid(str(exc)) from exc
    loader_ena = {loader.name: ena_delta(before[loader.name], after[loader.name]) for loader in rig.loaders}
    throttled = stage_void(loader_ena)
    if throttled:
        raise CellVoid(throttled)

    rss_kb = int(boxes.run(server, box_cmd("target.sh", "mem", cell["key"], target.server), timeout=SNAPSHOT_TIMEOUT_S))
    held = merged.achieved_rps >= HELD_TOLERANCE * rate and not any(merged.errors.values())
    server_busy = cpu_pct(before[server.name], after[server.name])
    loader_busy = {loader.name: cpu_pct(before[loader.name], after[loader.name]) for loader in rig.loaders}
    flags = stage_flags(server_busy, loader_busy, held)
    server_ena = ena_delta(before[server.name], after[server.name])
    if server_ena:
        flags["ena_throttled"] = server_ena
    flags.update(keepalive_flag(before[server.name], after[server.name], suite.connections[target.proto]))
    cell.update({
        "rate": rate,
        "achieved_rps": merged.achieved_rps,
        "successful_rps": merged.successful_rps,
        "errors": dict(merged.errors),
        "latency_us": {key: merged.latency_us[key] for key in LATENCY_KEYS},
        "rss_kb": rss_kb,
        "held": held,
        "cpu": {"server_busy": server_busy, "loader_busy": max(loader_busy.values())},
        "loaders": [
            {
                "loader": record.loader,
                "tool": record.tool,
                "late_ms": record.late_ms,
                "requests": record.requests,
                "bytes": record.bytes,
                "errors": dict(record.errors),
                "latency_us": dict(record.latency_us),
                "requests_per_sec": record.requests_per_sec,
                "busy_cpu": loader_busy[name],
                "ena": loader_ena[name],
            }
            for name, record in records.items()
        ],
    })
    cell["flags"].update(flags)


def worker_probe(boxes: Boxes, rig: Rig, cell: dict, target: Target) -> tuple[list[int], int]:
    """The sorted worker pids and the log bytes of the target."""
    lines = boxes.run(rig.server, box_cmd("target.sh", "probe", cell["key"], target.server)).splitlines()
    return [int(pid) for pid in lines[0].split()], int(lines[1])


def start_target(boxes: Boxes, rig: Rig, target: Target, cell: dict, cell_dir: Path, processes: int, rapira: dict) -> None:
    """Start the target and copy its rendered configs."""
    cmd = box_cmd("target.sh", "start", cell["key"], target.server, processes, rapira["dir"], *target.start)
    try:
        out = boxes.run(rig.server, cmd)
    except SshError as exc:
        raise CellVoid(f"start failed: {str(exc).splitlines()[-1]}") from exc
    for line in out.splitlines():
        if line.startswith("config="):
            remote = line.removeprefix("config=")
            name = Path(remote).name
            suffix = name.removeprefix(cell["key"] + ".")
            boxes.copy_from(rig.server, remote, cell_dir / f"config.{suffix}")


def measure(boxes: Boxes, rig: Rig, suite: Suite, plan: dict, target: Target, cell: dict, cell_dir: Path, rapira: dict) -> None:
    """Run the cell sequence of spec section 5.1 from the start of the target to the probe after the stage."""
    start_target(boxes, rig, target, cell, cell_dir, plan["processes"], rapira)
    pids_before, log_before = worker_probe(boxes, rig, cell, target)
    url = f"http://{rig.server.private_ip}:{PORT}{target.url}"
    results = boxes.run_many([(loader, probe_cmd(target, url)) for loader in rig.loaders], timeout=SNAPSHOT_TIMEOUT_S)
    for loader, out in zip(rig.loaders, results):
        if isinstance(out, SshError):
            raise CellVoid(f"probe mismatch on {loader.name}: {str(out).splitlines()[-1]}")
    run_stage(boxes, rig, suite, plan, target, cell, cell_dir)
    try:
        boxes.run(rig.loaders[0], probe_cmd(target, url), timeout=SNAPSHOT_TIMEOUT_S)
    except SshError:
        cell["flags"]["died"] = True
    pids_after, log_after = worker_probe(boxes, rig, cell, target)
    cell["flags"].update(cell_flags(pids_before, pids_after, log_before, log_after))


def clear_numbers(cell: dict) -> None:
    for key in NUMBER_KEYS:
        cell[key] = None


def void(cell: dict, reason: str) -> None:
    """Exclude the cell from every number. The first reason wins."""
    if cell["status"] == "ok":
        cell["status"] = "void"
        cell["reason"] = reason
        clear_numbers(cell)


def stop_target(boxes: Boxes, rig: Rig, target: Target, cell: dict, cell_dir: Path) -> None:
    """Stop the target, keep its WARN and ERROR lines, and void the cell when there are any."""
    try:
        boxes.run(rig.server, box_cmd("target.sh", "stop", cell["key"], target.server))
        lines = boxes.run(rig.server, box_cmd("target.sh", "log", cell["key"], target.server))
    except SshError as exc:
        void(cell, f"stop failed: {str(exc).splitlines()[-1]}")
        return
    (cell_dir / "server.log").write_text(lines)
    count = len(lines.splitlines())
    if count:
        void(cell, f"server log: {count} warn or error lines")


def run_cell(boxes: Boxes, rig: Rig, suite: Suite, plan: dict, target: Target, cell: dict, cell_dir: Path, rapira: dict) -> None:
    cell_dir.mkdir(parents=True)
    try:
        measure(boxes, rig, suite, plan, target, cell, cell_dir, rapira)
    except CellVoid as exc:
        void(cell, str(exc))
    except SshError as exc:
        void(cell, f"ssh: {str(exc).splitlines()[0]}")
    finally:
        stop_target(boxes, rig, target, cell, cell_dir)


def new_cell(key: str, target: Target, round_no: int) -> dict:
    cell = {"key": key, "target": target_record(target), "round": round_no, "status": "ok", "flags": {}}
    clear_numbers(cell)
    cell["loaders"] = []
    return cell


def run_suite(rig: Rig, suite: Suite, boxes: Boxes, out_dir: Path, *, suite_path: Path, processes: int, run_id: str,
              rapira: dict, servers: dict, apps: dict, loader_threads: int) -> Path:
    """Run every planned cell and write run.json. Returns the run.json path."""
    plan = plan_run(rig, suite, processes=processes, loader_threads=loader_threads)
    ensure_ttl(rig.hosts, len(plan["cells"]) * (suite.warmup_s + suite.duration_s + CELL_OVERHEAD_S) + TTL_MARGIN_S)
    run_dir = out_dir / run_id
    (run_dir / "raw").mkdir(parents=True)
    facts = host_facts(boxes, rig)
    server_facts = facts[rig.server.name]
    run_file = RunFile(
        run_id=run_id,
        suite=suite_record(suite, suite_path),
        rig={
            "server_type": rig.server_type,
            "loader_type": rig.loader_type,
            "loader_count": len(rig.loaders),
            "az": server_facts.get("az"),
            "ami": rig.ami_id,
            "kernel": server_facts.get("kernel"),
            "placement_group": server_facts.get("placement_group"),
            "server_instance_id": server_facts.get("instance_id"),
        },
        rapira=rapira,
        servers=servers,
        apps=apps,
        loaders=[
            {
                "name": loader.name,
                "instance_id": facts[loader.name].get("instance_id"),
                "private_ip": loader.private_ip,
                "kernel": facts[loader.name].get("kernel"),
                "wrk2": facts[loader.name].get("wrk2"),
                "h2load": facts[loader.name].get("h2load"),
            }
            for loader in rig.loaders
        ],
        processes=processes,
        plan=plan["keys"],
        smoke=suite.smoke,
        started=utc_now(),
    )
    path = run_dir / "run.json"
    try:
        for (round_no, target), key in zip(plan["cells"], plan["keys"]):
            print(f"==> {key}")
            cell = new_cell(key, target, round_no)
            try:
                run_cell(boxes, rig, suite, plan, target, cell, run_dir / "raw" / key, rapira)
            except BaseException:
                if cell["status"] == "ok":
                    cell["status"] = "incomplete"
                    cell["reason"] = "interrupted"
                    clear_numbers(cell)
                raise
            finally:
                run_file.add_cell(cell)
    finally:
        run_file.finish(utc_now())
        run_file.write(path)
    return path
```

- [ ] **Step 8: Change the command line**

In `rig/__main__.py`:

`cmd_bench`: replace the two lines that build `rapira` (`meta = ...` stays) with:

```python
    # The merged pull request of the commit labels the run on the board. A manual run has none.
    pr = None
    if args.pr_number is not None:
        pr = {"number": args.pr_number, "url": args.pr_url, "title": args.pr_title}
    rapira = {**meta["rapira"], "pr": pr}
```

`cmd_provision`: the `env` dict loses `"BASE_REF": args.base_ref,`.

`parser()`: the `bench` parser gains

```python
    p.add_argument("--pr-number", type=int)
    p.add_argument("--pr-url", default="")
    p.add_argument("--pr-title", default="")
```

and the `provision` parser loses `p.add_argument("--base-ref", default="main")`.

- [ ] **Step 9: Run the tests to verify they pass**

Run: `make test`
Expected: PASS, every module. `tests/test_report.py`, `tests/test_compare.py`, and `tests/test_publish.py` still pass on their own synthetic documents (Task 6 rewrites them); `tests/test_board_data.py` still passes (Task 7 rewrites it).

- [ ] **Step 10: Commit**

```bash
git add rig suites tests
git rm rig/ladder.py suites/full.toml suites/ab.toml tests/test_ladder.py
git commit -s -S -m "feat!: measure one constant rate per target and write run files of schema 2"
```

---

### Task 6: Report, compare, and publish on the flat cell record

**Files:**
- Modify: `rig/report.py` (rewrite), `rig/compare.py` (rewrite), `rig/publish.py`
- Test: `tests/test_report.py` (rewrite), `tests/test_compare.py` (rewrite), `tests/test_publish.py`

**Interfaces:**
- Consumes: the cell record and the run file of Task 5.
- Produces: `rig.report.rows(run)` with one row per target (`name`, `achieved`, `held`, `p99`, `p50`, `rss_kb`, `n`, `flags`); `rig.report.ms(us)` and `rig.report.mib(kb)`; the index entry gains `pr`. The CI publish job of Task 8 calls `rig publish` as today.

- [ ] **Step 1: Write the failing report test**

Replace `tests/test_report.py` with:

```python
"""Tests of the report table on synthetic run files."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from rig.__main__ import main
from rig.report import render

VOIDED_TITLE = "VOIDED cells (excluded from every number above):"
FOOTER = "Do not publish these tables."


def ok_cell(name, round_no, *, achieved, held, p50, p99, rss_kb, flags=None):
    return {
        "key": f"r{round_no}-{name}",
        "target": {"name": name},
        "round": round_no,
        "status": "ok",
        "flags": flags or {},
        "rate": 250000,
        "achieved_rps": achieved,
        "successful_rps": achieved,
        "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
        "latency_us": {"p50": p50, "p90": p99, "p99": p99, "p999": p99, "max": p99},
        "rss_kb": rss_kb,
        "held": held,
        "cpu": {"server_busy": 60, "loader_busy": 70},
        "loaders": [],
    }


def void_cell(name, round_no, reason):
    return {
        "key": f"r{round_no}-{name}",
        "target": {"name": name},
        "round": round_no,
        "status": "void",
        "reason": reason,
        "flags": {},
        "rate": None,
        "achieved_rps": None,
        "successful_rps": None,
        "errors": None,
        "latency_us": None,
        "rss_kb": None,
        "held": None,
        "cpu": None,
        "loaders": [],
    }


def run_doc(cells, status="complete", reasons=()):
    return {"id": "20260925T120000Z-ci-0a1b2c3", "cells": cells, "status": status, "reasons": list(reasons)}


HELLO = ok_cell("hello-rapira-worker", 1, achieved=249996.6, held=True, p50=689, p99=1264, rss_kb=204800)
# 249996.6 prints as 249997; 1264 us is 1.26 ms, 689 us is 0.69 ms; 204800 KiB is 200.0 MiB.
HELLO_ROW = ["hello-rapira-worker", "249997", "yes", "1.26ms", "0.69ms", "200.0", "1", "-"]

CASES = [
    {
        "name": "single round",
        "run": run_doc([HELLO]),
        "rows": [HELLO_ROW],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "rows keep the cell order",
        "run": run_doc([
            ok_cell("grpc-rapira", 1, achieved=99990.0, held=True, p50=400, p99=2500, rss_kb=1048576),
            HELLO,
        ]),
        "rows": [["grpc-rapira", "99990", "yes", "2.50ms", "0.40ms", "1024.0", "1", "-"], HELLO_ROW],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "a row that did not hold",
        "run": run_doc([ok_cell("hello-rapira-classic", 1, achieved=228571.4, held=False, p50=9000, p99=250000, rss_kb=307200, flags={"generator_bound": True})]),
        "rows": [["hello-rapira-classic", "228571", "no", "250.00ms", "9.00ms", "300.0", "1", "generator_bound"]],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "three rounds with a void",
        "run": run_doc(
            [
                ok_cell("yii3-rapira-dispatcher", 1, achieved=250000.0, held=True, p50=900, p99=5000, rss_kb=204800, flags={"worker_churn": True}),
                void_cell("yii3-rapira-dispatcher", 2, "probe mismatch on loader-1"),
                ok_cell("yii3-rapira-dispatcher", 3, achieved=240000.0, held=False, p50=1100, p99=7000, rss_kb=210944),
            ],
            "incomplete",
            ["r2-yii3-rapira-dispatcher: void: probe mismatch on loader-1"],
        ),
        # Two rounds survive: req/s median 245000, held no because round 3 did not hold, p99 median 6000 us,
        # p50 median 1000 us, RSS median (204800 + 210944) / 2 = 207872 KiB = 203.0 MiB.
        "rows": [["yii3-rapira-dispatcher", "245000", "no", "6.00ms", "1.00ms", "203.0", "2", "worker_churn"]],
        "voided": ["r2-yii3-rapira-dispatcher: probe mismatch on loader-1"],
        "footer": ["INCOMPLETE RUN: r2-yii3-rapira-dispatcher: void: probe mismatch on loader-1.", FOOTER],
        "status": 1,
    },
    {
        "name": "value flags",
        "run": run_doc([ok_cell(
            "hello-rapira-worker", 1, achieved=249996.6, held=True, p50=689, p99=1264, rss_kb=204800,
            flags={"log_growth": 70000, "ena_throttled": {"pps_allowance_exceeded": 946}},
        )]),
        "rows": [HELLO_ROW[:7] + ["ena_throttled(pps_allowance_exceeded=946),log_growth(70000)"]],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "incomplete run with a missing cell",
        "run": run_doc([HELLO], "incomplete", ["r1-grpc-rapira: missing"]),
        "rows": [HELLO_ROW],
        "voided": [],
        "footer": ["INCOMPLETE RUN: r1-grpc-rapira: missing.", FOOTER],
        "status": 1,
    },
]


def parse(text):
    """The table rows split into 8 fields, the voided lines, and the footer lines."""
    lines = text.splitlines()
    end = lines.index("") if "" in lines else len(lines)
    table = [line.split(None, 7) for line in lines[2:end]]
    voided = []
    if VOIDED_TITLE in lines:
        start = lines.index(VOIDED_TITLE) + 1
        for line in lines[start:]:
            if not line:
                break
            voided.append(line.strip())
    footer = lines[-2:] if lines[-1] == FOOTER else []
    return table, voided, footer


class TestReport(unittest.TestCase):
    def test_render(self):
        for case in CASES:
            with self.subTest(name=case["name"]):
                text, status = render(case["run"])
                table, voided, footer = parse(text)
                self.assertEqual(table, case["rows"])
                self.assertEqual(voided, case["voided"])
                self.assertEqual(footer, case["footer"])
                self.assertEqual(status, case["status"])

    def test_cli_exit_status_of_an_incomplete_run(self):
        run = run_doc([HELLO], "incomplete", ["r1-grpc-rapira: missing"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            path.write_text(json.dumps(run))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                status = main(["report", str(path)])
        self.assertEqual(status, 1)
        self.assertTrue(out.getvalue().endswith(FOOTER + "\n"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Write the failing compare test**

Replace `tests/test_compare.py` with:

```python
"""Tests of the comparison of two run files."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from rig.__main__ import main
from rig.compare import compare


def ok_cell(name, round_no, achieved, p99, rss_kb):
    return {
        "key": f"r{round_no}-{name}",
        "target": {"name": name},
        "round": round_no,
        "status": "ok",
        "flags": {},
        "rate": 250000,
        "achieved_rps": achieved,
        "successful_rps": achieved,
        "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
        "latency_us": {"p50": 700, "p90": p99, "p99": p99, "p999": p99, "max": p99},
        "rss_kb": rss_kb,
        "held": True,
        "cpu": {"server_busy": 60, "loader_busy": 70},
        "loaders": [],
    }


def run_doc(run_id, cells):
    return {
        "id": run_id,
        "rig": {"server_type": "c7a.8xlarge", "loader_type": "c7a.2xlarge", "loader_count": 1, "az": "eu-central-1a"},
        "processes": 32,
        "suite": {"rates": {"hello": 250000}, "duration_s": 60},
        "cells": cells,
        "status": "complete",
        "reasons": [],
    }


A_ID = "20260924T120000Z-ci-0a1b2c3"
B_ID = "20260925T120000Z-ci-4d5e6f7"

# Three rounds. a: req/s median 250000, p99 median 1250 us, RSS median 204800 KiB (200.0 MiB).
# b: req/s median 245000 (-2.0%), p99 median 1450 us (+16.0%), RSS median 215040 KiB (210.0 MiB, +5.0%).
A_RUN = run_doc(A_ID, [
    ok_cell("hello-rapira-worker", 1, 250000.0, 1200, 204800),
    ok_cell("hello-rapira-worker", 2, 249800.0, 1300, 205824),
    ok_cell("hello-rapira-worker", 3, 250100.0, 1250, 204800),
])
B_RUN = run_doc(B_ID, [
    ok_cell("hello-rapira-worker", 1, 245000.0, 1400, 215040),
    ok_cell("hello-rapira-worker", 2, 244000.0, 1500, 215040),
    ok_cell("hello-rapira-worker", 3, 246000.0, 1450, 216064),
])
DELTA_LINE = "hello-rapira-worker  req/s 250000 -> 245000 -2.0%  p99 1.25ms -> 1.45ms +16.0%  rss 200.0 -> 210.0 +5.0%"

IDENTITY_CASES = [
    {"name": "server type differs", "section": "rig", "key": "server_type", "value": "c7a.4xlarge",
     "status": 1, "first": "refused: rig.server_type differs: c7a.8xlarge vs c7a.4xlarge"},
    {"name": "loader type differs", "section": "rig", "key": "loader_type", "value": "c7a.xlarge",
     "status": 1, "first": "refused: rig.loader_type differs: c7a.2xlarge vs c7a.xlarge"},
    {"name": "loader count differs", "section": "rig", "key": "loader_count", "value": 2,
     "status": 1, "first": "refused: rig.loader_count differs: 1 vs 2"},
    {"name": "processes differ", "section": "", "key": "processes", "value": 16,
     "status": 1, "first": "refused: processes differs: 32 vs 16"},
    {"name": "rates differ", "section": "suite", "key": "rates", "value": {"hello": 200000},
     "status": 1, "first": "refused: suite.rates differs: {'hello': 250000} vs {'hello': 200000}"},
    {"name": "duration differs", "section": "suite", "key": "duration_s", "value": 30,
     "status": 1, "first": "refused: suite.duration_s differs: 60 vs 30"},
    {"name": "availability zone is not identity", "section": "rig", "key": "az", "value": "eu-central-1b",
     "status": 0, "first": f"a: {A_ID}"},
]


def changed(run, section, key, value):
    out = copy.deepcopy(run)
    if section:
        out[section][key] = value
    else:
        out[key] = value
    return out


class TestCompare(unittest.TestCase):
    def test_identity(self):
        for case in IDENTITY_CASES:
            with self.subTest(name=case["name"]):
                b = changed(B_RUN, case["section"], case["key"], case["value"])
                text, status = compare(A_RUN, b)
                self.assertEqual(status, case["status"])
                self.assertEqual(text.splitlines()[0], case["first"])

    def test_refusal_names_the_force_option(self):
        text, _ = compare(A_RUN, changed(B_RUN, "", "processes", 16))
        self.assertEqual(text.splitlines(), ["refused: processes differs: 32 vs 16", "Use --force to compare anyway."])

    def test_force_prints_the_difference_and_the_deltas(self):
        text, status = compare(A_RUN, changed(B_RUN, "rig", "server_type", "c7a.4xlarge"), force=True)
        self.assertEqual(status, 0)
        self.assertEqual(text.splitlines(), [
            "forced: rig.server_type differs: c7a.8xlarge vs c7a.4xlarge",
            f"a: {A_ID}",
            f"b: {B_ID}",
            "",
            DELTA_LINE,
        ])

    def test_delta_line(self):
        text, status = compare(A_RUN, B_RUN)
        self.assertEqual(status, 0)
        self.assertEqual(text.splitlines()[3:], [DELTA_LINE])

    def test_target_in_one_run_only(self):
        a = run_doc(A_ID, [
            ok_cell("hello-rapira-worker", 1, 250000.0, 1250, 204800),
            ok_cell("yii3-rapira-dispatcher", 1, 250000.0, 4000, 524288),
        ])
        b = run_doc(B_ID, [
            ok_cell("hello-rapira-worker", 1, 245000.0, 1450, 215040),
            ok_cell("grpc-rapira", 1, 99990.0, 2500, 1048576),
        ])
        text, status = compare(a, b)
        self.assertEqual(status, 0)
        # Names pad to the longest name, yii3-rapira-dispatcher (22 characters).
        self.assertEqual(text.splitlines()[3:], [
            f"{'hello-rapira-worker':<22}  req/s 250000 -> 245000 -2.0%  p99 1.25ms -> 1.45ms +16.0%  rss 200.0 -> 210.0 +5.0%",
            f"{'yii3-rapira-dispatcher':<22}  only in a",
            f"{'grpc-rapira':<22}  only in b",
        ])

    def test_cli_force_flag(self):
        b = changed(B_RUN, "rig", "loader_count", 2)
        with tempfile.TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "a.json"
            path_b = Path(tmp) / "b.json"
            path_a.write_text(json.dumps(A_RUN))
            path_b.write_text(json.dumps(b))
            with contextlib.redirect_stdout(io.StringIO()):
                refused = main(["compare", str(path_a), str(path_b)])
                forced = main(["compare", str(path_a), str(path_b), "--force"])
        self.assertEqual((refused, forced), (1, 0))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Write the failing publish test**

In `tests/test_publish.py`:
- `run_doc(run_id, started, status="complete", pr=None)` sets `"schema": "rapira-bench-run/2"` and `"rapira": {"ref": "nightly", "sha": "0a1b2c3d4e5f", "version": "0.9.0", "build": "nightly", "pr": pr}`.
- `entry(run_id, started, status="complete", pr=None)` gains `"pr": pr` as its last key.
- Add the module constant `PR = {"number": 59, "url": "https://github.com/rapira-rs/rapira/pull/59", "title": "Faster hello"}` and `RUN = run_doc("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z", pr=PR)`.
- In `CASES`, the entries of the published run carry `pr=59`: the first case is `[entry("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z", pr=59)]`, the second case keeps the two other entries without a pr and the published run with `pr=59` in the middle, the third case replaces the incomplete entry with `entry(..., pr=59)`.
- Add a case for a manual run:

```python
    {
        "name": "a run without a pull request has a null pr",
        "index": None,
        "run": run_doc("20260925T130000Z-ci-0a1b2c3", "2026-09-25T13:00:00Z"),
        "runs": [entry("20260925T130000Z-ci-0a1b2c3", "2026-09-25T13:00:00Z")],
    },
```

and give every other case `"run": RUN`; `test_publish` calls `publish(case["run"], pages)` and checks `path == pages / "data" / f"{case['run']['id']}.json"` and that the file round-trips `case["run"]`.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_report tests.test_compare tests.test_publish 2>&1 | tail -5`
Expected: FAIL (`KeyError: 'held'` shape errors in report and compare, a missing `pr` key in the publish entries).

- [ ] **Step 5: Write report, compare, and publish**

Replace `rig/report.py` with:

```python
"""The text table of one run file."""

import statistics


def median_of(values):
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def flag_text(name, value):
    """`name` for a true flag, `name(value)` for a flag with a value."""
    if value is True:
        return name
    if isinstance(value, dict):
        return f"{name}({','.join(f'{k}={v}' for k, v in value.items())})"
    return f"{name}({value})"


def target_row(cells: list[dict]) -> dict:
    """The medians over the ok cells of one target, `held` when every cell held, and the union of the flags."""
    return {
        "name": cells[0]["target"]["name"],
        "achieved": median_of(c["achieved_rps"] for c in cells),
        "held": all(c["held"] for c in cells),
        "p99": median_of(c["latency_us"]["p99"] for c in cells),
        "p50": median_of(c["latency_us"]["p50"] for c in cells),
        "rss_kb": median_of(c["rss_kb"] for c in cells),
        "n": len(cells),
        "flags": sorted({flag_text(k, v) for c in cells for k, v in c["flags"].items()}),
    }


def rows(run: dict) -> list[dict]:
    """One row per target with ok cells, in the order of the first cell of each target."""
    groups = {}
    for cell in run["cells"]:
        if cell["status"] == "ok":
            groups.setdefault(cell["target"]["name"], []).append(cell)
    return [target_row(cells) for cells in groups.values()]


def num(value):
    return f"{value:.0f}" if value is not None else "-"


def ms(us):
    return f"{us / 1000:.2f}ms" if us is not None else "-"


def mib(kb):
    return f"{kb / 1024:.1f}" if kb is not None else "-"


def render(run: dict) -> tuple[str, int]:
    """The report text and the exit status: 1 when the run is incomplete."""
    table = rows(run)
    w = max([len("target")] + [len(r["name"]) for r in table]) + 2
    hdr = f"{'target':<{w}} {'req/s':>8} {'held':>4} {'p99':>9} {'p50':>9} {'RSS MiB':>8} {'n':>3}  flags"
    lines = [hdr, "-" * len(hdr)]
    for r in table:
        held = "yes" if r["held"] else "no"
        lines.append(
            f"{r['name']:<{w}} {num(r['achieved']):>8} {held:>4} {ms(r['p99']):>9} {ms(r['p50']):>9} "
            f"{mib(r['rss_kb']):>8} {r['n']:>3}  {','.join(r['flags']) or '-'}"
        )
    voided = [c for c in run["cells"] if c["status"] == "void"]
    if voided:
        lines += ["", "VOIDED cells (excluded from every number above):"]
        lines += [f"  {c['key']}: {c['reason']}" for c in voided]
    if run["status"] != "complete":
        lines += ["", f"INCOMPLETE RUN: {'; '.join(run['reasons'])}.", "Do not publish these tables."]
        return "\n".join(lines) + "\n", 1
    return "\n".join(lines) + "\n", 0
```

Replace `rig/compare.py` with:

```python
"""Deltas of the achieved rate, the p99, and the RSS between two run files."""

from rig.report import mib, ms, num, rows

# Run fields that must match for a fair comparison, as (section, key). An empty section is the top level.
IDENTITY = (
    ("rig", "server_type"),
    ("rig", "loader_type"),
    ("rig", "loader_count"),
    ("", "processes"),
    ("suite", "rates"),
    ("suite", "duration_s"),
)


def identity_diffs(a: dict, b: dict) -> list[str]:
    diffs = []
    for section, key in IDENTITY:
        va = a[section][key] if section else a[key]
        vb = b[section][key] if section else b[key]
        if va != vb:
            name = f"{section}.{key}" if section else key
            diffs.append(f"{name} differs: {va} vs {vb}")
    return diffs


def delta(va, vb):
    """The change from va to vb in percent, or "-" without two values."""
    if va and vb is not None:
        return f"{100.0 * (vb - va) / va:+.1f}%"
    return "-"


def compare(a: dict, b: dict, *, force: bool = False) -> tuple[str, int]:
    """The delta table and the exit status: 1 when the rig identity differs and `force` is false."""
    diffs = identity_diffs(a, b)
    if diffs and not force:
        lines = [f"refused: {d}" for d in diffs] + ["Use --force to compare anyway."]
        return "\n".join(lines) + "\n", 1
    lines = [f"forced: {d}" for d in diffs] + [f"a: {a['id']}", f"b: {b['id']}", ""]
    rows_a = {r["name"]: r for r in rows(a)}
    rows_b = {r["name"]: r for r in rows(b)}
    names = list(rows_a) + [n for n in rows_b if n not in rows_a]
    w = max(len(n) for n in names) if names else 0
    for name in names:
        ra = rows_a.get(name)
        rb = rows_b.get(name)
        if rb is None:
            lines.append(f"{name:<{w}}  only in a")
            continue
        if ra is None:
            lines.append(f"{name:<{w}}  only in b")
            continue
        lines.append(
            f"{name:<{w}}  req/s {num(ra['achieved'])} -> {num(rb['achieved'])} {delta(ra['achieved'], rb['achieved'])}"
            f"  p99 {ms(ra['p99'])} -> {ms(rb['p99'])} {delta(ra['p99'], rb['p99'])}"
            f"  rss {mib(ra['rss_kb'])} -> {mib(rb['rss_kb'])} {delta(ra['rss_kb'], rb['rss_kb'])}"
        )
    return "\n".join(lines) + "\n", 0
```

In `rig/publish.py`, `index_entry` returns one more key after `"smoke"`:

```python
        "pr": run["rapira"]["pr"]["number"] if run["rapira"]["pr"] else None,
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `make test`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add rig/report.py rig/compare.py rig/publish.py tests/test_report.py tests/test_compare.py tests/test_publish.py
git commit -s -S -m "feat!: report the achieved rate, the p99, and the RSS per target"
```

---

### Task 7: The board with a p99 chart and an RSS chart

**Files:**
- Modify: `board/app.js` (rewrite), `board/index.html`
- Test: `tests/test_board_data.py` (rewrite), `tests/fixtures/board_runs.json` (rewrite)

**Interfaces:**
- Consumes: the run file of Task 5 and the index entry of Task 6.
- Produces: `visibleRuns(entries)`, `runLabel(run)`, `runLink(run)`, and `targetSeries(runs)` exported for node.

- [ ] **Step 1: Write the fixture and the failing test**

Replace `tests/fixtures/board_runs.json` with:

```json
[
 {
  "schema": "rapira-bench-run/2",
  "id": "20260926T010000Z-ci-aaaaaaa",
  "started": "2026-09-26T01:00:00Z",
  "smoke": false,
  "rapira": {"sha": "aaaaaaa0000000000000000000000000000000a1", "version": "0.8.1-nightly.aaaaaaa", "pr": {"number": 101, "url": "https://github.com/rapira-rs/rapira/pull/101", "title": "Faster hello"}},
  "cells": [
   {"key": "r1-hello-rapira-worker", "target": {"name": "hello-rapira-worker"}, "status": "ok", "flags": {}, "rate": 250000, "achieved_rps": 249800.5, "latency_us": {"p50": 300, "p90": 500, "p99": 1200, "p999": 1500, "max": 4000}, "rss_kb": 204800, "held": true},
   {"key": "r1-grpc-rapira", "target": {"name": "grpc-rapira"}, "status": "ok", "flags": {}, "rate": 100000, "achieved_rps": 99990, "latency_us": {"p50": 400, "p90": 900, "p99": 2500, "p999": 5000, "max": 12000}, "rss_kb": 1048576, "held": true}
  ]
 },
 {
  "schema": "rapira-bench-run/2",
  "id": "20260927T010000Z-ci-bbbbbbb",
  "started": "2026-09-27T01:00:00Z",
  "smoke": false,
  "rapira": {"sha": "bbbbbbb0000000000000000000000000000000b2", "version": "v0.8.1-130-gbbbbbbb", "pr": null},
  "cells": [
   {"key": "r1-hello-rapira-worker", "target": {"name": "hello-rapira-worker"}, "status": "ok", "flags": {"generator_bound": true}, "rate": 250000, "achieved_rps": 240000, "latency_us": {"p50": 320, "p90": 550, "p99": 900, "p999": 1600, "max": 4200}, "rss_kb": 210944, "held": false},
   {"key": "r2-hello-rapira-worker", "target": {"name": "hello-rapira-worker"}, "status": "ok", "flags": {}, "rate": 250000, "achieved_rps": 250100, "latency_us": {"p50": 340, "p90": 600, "p99": 1100, "p999": 1900, "max": 4500}, "rss_kb": 209920, "held": true},
   {"key": "r1-grpc-rapira", "target": {"name": "grpc-rapira"}, "status": "void", "reason": "probe mismatch on loader-1", "flags": {}, "rate": null, "achieved_rps": null, "latency_us": null, "rss_kb": null, "held": null}
  ]
 },
 {
  "schema": "rapira-bench-run/2",
  "id": "20260928T010000Z-ci-ccccccc",
  "started": "2026-09-28T01:00:00Z",
  "smoke": false,
  "rapira": {"sha": "ccccccc0000000000000000000000000000000c3", "version": "0.8.1-nightly.ccccccc", "pr": {"number": 103, "url": "https://github.com/rapira-rs/rapira/pull/103", "title": "Fix the dispatcher drain"}},
  "cells": [
   {"key": "r1-hello-rapira-worker", "target": {"name": "hello-rapira-worker"}, "status": "incomplete", "reason": "interrupted", "flags": {}, "rate": null, "achieved_rps": null, "latency_us": null, "rss_kb": null, "held": null},
   {"key": "r1-grpc-rapira", "target": {"name": "grpc-rapira"}, "status": "ok", "flags": {"server_unsaturated": true}, "rate": 100000, "achieved_rps": 85000, "latency_us": {"p50": 2000, "p90": 6000, "p99": 9000, "p999": 20000, "max": 50000}, "rss_kb": 1150976, "held": false},
   {"key": "r1-yii3-rapira-dispatcher", "target": {"name": "yii3-rapira-dispatcher"}, "status": "ok", "flags": {}, "rate": 250000, "achieved_rps": 250000, "latency_us": {"p50": 1800, "p90": 2800, "p99": 4000, "p999": 7000, "max": 15000}, "rss_kb": 524288, "held": true}
  ]
 },
 {
  "schema": "rapira-bench-run/2",
  "id": "20260928T020000Z-ci-sssssss",
  "started": "2026-09-28T02:00:00Z",
  "smoke": true,
  "rapira": {"sha": "sssssss0000000000000000000000000000000s4", "version": "0.8.1-nightly.sssssss", "pr": null},
  "cells": [
   {"key": "r1-hello-rapira-worker", "target": {"name": "hello-rapira-worker"}, "status": "ok", "flags": {}, "rate": 250000, "achieved_rps": 250000, "latency_us": {"p50": 200, "p90": 350, "p99": 500, "p999": 900, "max": 2500}, "rss_kb": 204800, "held": true}
  ]
 }
]
```

Replace `tests/test_board_data.py` with:

```python
"""Data transforms of board/app.js, run under node.

The fixture holds four run files reduced to the fields the board reads.
"""

import json
import shutil
import subprocess
import unittest
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

RUN_A, RUN_B, RUN_C, RUN_S = json.loads(FIXTURE.read_text())
COMMIT_B = "https://github.com/rapira-rs/rapira/commit/bbbbbbb0000000000000000000000000000000b2"


def entry(run):
    """The manifest entry of a run, reduced to the fields that visibleRuns reads."""
    return {"id": run["id"], "started": run["started"], "smoke": run["smoke"]}


SERIES_CASES = [
    {
        # hello, run b: two ok cells, so a value is the median of r1 and r2: p99 (900 + 1100) / 2 = 1000 us,
        # RSS (210944 + 209920) / 2 = 210432 KiB = 205.5 MiB, req/s (240000 + 250100) / 2 = 245050, held only
        # when every cell held, the flags are the union. Run c: the hello cell is incomplete, so nulls.
        # grpc, run b: the cell is void. yii3 has a cell only in run c. The targets come in name order.
        "name": "medians over rounds, void and incomplete cells, target in one run",
        "runs": [RUN_A, RUN_B, RUN_C],
        "expected": {
            "labels": ["#101", "bbbbbbb", "#103"],
            "links": ["https://github.com/rapira-rs/rapira/pull/101", COMMIT_B, "https://github.com/rapira-rs/rapira/pull/103"],
            "titles": ["Faster hello", "", "Fix the dispatcher drain"],
            "targets": {
                "grpc-rapira": {
                    "p99_ms": [2.5, None, 9.0],
                    "rss_mib": [1024.0, None, 1124.0],
                    "achieved": [99990, None, 85000],
                    "rate": [100000, None, 100000],
                    "held": [True, None, False],
                    "flags": [[], None, ["server_unsaturated"]],
                },
                "hello-rapira-worker": {
                    "p99_ms": [1.2, 1.0, None],
                    "rss_mib": [200.0, 205.5, None],
                    "achieved": [249800.5, 245050, None],
                    "rate": [250000, 250000, None],
                    "held": [True, False, None],
                    "flags": [[], ["generator_bound"], None],
                },
                "yii3-rapira-dispatcher": {
                    "p99_ms": [None, None, 4.0],
                    "rss_mib": [None, None, 512.0],
                    "achieved": [None, None, 250000],
                    "rate": [None, None, 250000],
                    "held": [None, None, True],
                    "flags": [None, None, []],
                },
            },
        },
    },
    {
        "name": "one run",
        "runs": [RUN_A],
        "expected": {
            "labels": ["#101"],
            "links": ["https://github.com/rapira-rs/rapira/pull/101"],
            "titles": ["Faster hello"],
            "targets": {
                "grpc-rapira": {"p99_ms": [2.5], "rss_mib": [1024.0], "achieved": [99990], "rate": [100000], "held": [True], "flags": [[]]},
                "hello-rapira-worker": {"p99_ms": [1.2], "rss_mib": [200.0], "achieved": [249800.5], "rate": [250000], "held": [True], "flags": [[]]},
            },
        },
    },
]

VISIBLE_CASES = [
    {
        "name": "smoke entry dropped, sorted by started",
        "entries": [entry(RUN_B), entry(RUN_S), entry(RUN_A), entry(RUN_C)],
        "expected": [entry(RUN_A), entry(RUN_B), entry(RUN_C)],
    },
    {
        "name": "entries in order without smoke stay the same",
        "entries": [entry(RUN_A), entry(RUN_B), entry(RUN_C)],
        "expected": [entry(RUN_A), entry(RUN_B), entry(RUN_C)],
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
                # Dict equality ignores the key order, and the board draws the lines in key order.
                self.assertEqual(list(series["targets"]), list(case["expected"]["targets"]))


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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_board_data -v`
Expected: FAIL (`targetSeries` returns `apps`, `runLabel` is not a function).

- [ ] **Step 3: Write the board**

Replace `board/app.js` with:

```js
"use strict";

// The board draws run files of this schema only.
const RUN_SCHEMA = "rapira-bench-run/2";
// The board loads at most this many runs, the newest ones.
const HISTORY_RUNS = 60;
const COMMITS = "https://github.com/rapira-rs/rapira/commit/";
const PALETTE = ["#2f6fdf", "#d9480f", "#2b8a3e", "#ae3ec9", "#e67700", "#0c8599", "#c2255c", "#5c7cfa"];

function median(values) {
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

// Manifest entries to show, oldest first. Smoke runs are not shown.
function visibleRuns(entries) {
  return entries
    .filter((entry) => !entry.smoke)
    .sort((a, b) => (a.started < b.started ? -1 : a.started > b.started ? 1 : 0));
}

// The x label of a run: the pull request number, or the first 7 characters of the sha without one.
function runLabel(run) {
  return run.rapira.pr ? "#" + run.rapira.pr.number : run.rapira.sha.slice(0, 7);
}

// The page that a click on a point opens: the pull request, or the commit on GitHub.
function runLink(run) {
  return run.rapira.pr ? run.rapira.pr.url : COMMITS + run.rapira.sha;
}

// The values of one target in one run: the medians over its ok cells, or nulls without one.
function runPoint(cells) {
  if (!cells.length) {
    return { p99_ms: null, rss_mib: null, achieved: null, rate: null, held: null, flags: null };
  }
  return {
    p99_ms: median(cells.map((cell) => cell.latency_us.p99)) / 1000,
    rss_mib: median(cells.map((cell) => cell.rss_kb)) / 1024,
    achieved: median(cells.map((cell) => cell.achieved_rps)),
    rate: cells[0].rate,
    held: cells.every((cell) => cell.held),
    flags: Array.from(new Set(cells.flatMap((cell) => Object.keys(cell.flags)))).sort(),
  };
}

// Chart data of every target over the runs, which come in started order. The targets come in name order.
function targetSeries(runs) {
  const names = Array.from(new Set(runs.flatMap((run) => run.cells.map((cell) => cell.target.name)))).sort();
  const targets = {};
  names.forEach((name) => {
    const points = runs.map((run) =>
      runPoint(run.cells.filter((cell) => cell.target.name === name && cell.status === "ok"))
    );
    targets[name] = {
      p99_ms: points.map((point) => point.p99_ms),
      rss_mib: points.map((point) => point.rss_mib),
      achieved: points.map((point) => point.achieved),
      rate: points.map((point) => point.rate),
      held: points.map((point) => point.held),
      flags: points.map((point) => point.flags),
    };
  });
  return {
    labels: runs.map(runLabel),
    links: runs.map(runLink),
    titles: runs.map((run) => (run.rapira.pr ? run.rapira.pr.title : "")),
    targets,
  };
}

function el(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) {
    throw new Error(path + ": HTTP " + response.status);
  }
  return response.json();
}

// One chart of every target over the runs. key is p99_ms or rss_mib; scale is logarithmic or linear.
function drawChart(parent, series, key, axis, unit, scale) {
  const box = el("div");
  box.className = "chart";
  const canvas = el("canvas");
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", axis + " by run");
  box.appendChild(canvas);
  parent.appendChild(box);
  const names = Object.keys(series.targets);
  new Chart(canvas, {
    type: "line",
    data: {
      labels: series.labels,
      datasets: names.map((name, i) => ({
        label: name,
        data: series.targets[name][key],
        borderColor: PALETTE[i % PALETTE.length],
        backgroundColor: PALETTE[i % PALETTE.length],
      })),
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      onClick: (event, elements) => {
        if (elements.length) {
          window.open(series.links[elements[0].index], "_blank", "noopener");
        }
      },
      scales: {
        x: { type: "category" },
        y: { type: scale, title: { display: true, text: axis } },
      },
      plugins: {
        legend: { position: "top" },
        tooltip: {
          callbacks: {
            title: (items) => {
              const i = items[0].dataIndex;
              return series.titles[i] ? series.labels[i] + " " + series.titles[i] : series.labels[i];
            },
            label: (item) => {
              const target = series.targets[item.dataset.label];
              const i = item.dataIndex;
              return (
                item.dataset.label + ": " + item.parsed.y.toFixed(2) + " " + unit + ", " +
                Math.round(target.achieved[i]) + " of " + target.rate[i] + " req/s, held " + (target.held[i] ? "yes" : "no")
              );
            },
            afterLabel: (item) => series.targets[item.dataset.label].flags[item.dataIndex].join(", "),
          },
        },
      },
    },
  });
}

function showError(error) {
  const node = document.getElementById("error");
  node.textContent = String(error);
  node.hidden = false;
}

async function main() {
  const style = getComputedStyle(document.documentElement);
  Chart.defaults.color = style.getPropertyValue("--fg").trim();
  Chart.defaults.borderColor = style.getPropertyValue("--grid").trim();
  const entries = visibleRuns((await fetchJson("data/index.json")).runs).slice(-HISTORY_RUNS);
  const loaded = await Promise.all(entries.map((entry) => fetchJson("data/" + entry.id + ".json")));
  const series = targetSeries(loaded.filter((run) => run.schema === RUN_SCHEMA));
  const root = document.getElementById("charts");
  root.appendChild(el("h2", "p99 latency"));
  drawChart(root, series, "p99_ms", "p99 ms", "ms", "logarithmic");
  root.appendChild(el("h2", "RSS"));
  drawChart(root, series, "rss_mib", "RSS MiB", "MiB", "linear");
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { visibleRuns, runLabel, runLink, targetSeries };
} else {
  document.addEventListener("DOMContentLoaded", () => main().catch(showError));
}
```

In `board/index.html`, the paragraph in `<header>` becomes:

```html
<p>The p99 latency and the RSS of rapira at a constant request rate per target, for each merged pull request on the rapira main branch, from its nightly build. A click on a point opens the pull request. <a href="https://github.com/rapira-rs/benchmarks/blob/main/METHOD.md">How to read these numbers</a>.</p>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_board_data -v`
Expected: PASS, 6 subtests.

- [ ] **Step 5: Look at the page**

Publish the fixture runs into a local pages directory and open the board:

```bash
python3 - <<'PY'
import json
from pathlib import Path
from rig.publish import publish
runs = json.loads(Path("tests/fixtures/board_runs.json").read_text())
for run in runs:
    run.update({"status": "complete", "suite": {"name": "ci"}})
    publish(run, Path("runs/pages"))
PY
make board
```

Expected on http://127.0.0.1:8000: two charts, the x labels `#101`, `bbbbbbb`, `#103`, three lines, gaps where a run has no ok cell, and a click on a point opens the pull request or the commit. Stop the server with Ctrl-C and remove `runs/pages` afterwards.

- [ ] **Step 6: Commit**

```bash
git add board/app.js board/index.html tests/test_board_data.py tests/fixtures/board_runs.json
git commit -s -S -m "feat!: draw the p99 and the RSS per target with the pull request as the label"
```

---

### Task 8: Provisioning for rapira, Yii3, and the gRPC runtime, the rig knobs, and CI

**Files:**
- Modify: `box/provision-server.sh` (rewrite), `box/provision-loader.sh`, `terraform/variables.tf`, `Makefile`, `.github/workflows/bench.yml`
- Test: none in Python. `bash -n` on both scripts, `terraform -chdir=terraform fmt -check`, and a read of the workflow.

**Interfaces:**
- Consumes: `apps/yii3/source.toml`, `apps/yii3/composer.lock`, `apps/grpc/composer.json`, `apps/grpc/composer.lock` of Task 4; `python3 -m rig needs` prints `rapira grpc hello yii3` for the ci suite (Task 5); `rig bench --pr-number --pr-url --pr-title` of Task 5; `GRPC_VENDOR=/opt/bench/apps/grpc/vendor` of Task 3.
- Produces: `/opt/bench/loader.json` with `wrk2_commit`, `wrk2_version`, `h2load_version`; `/opt/bench/versions.json` with `php` and, for the apps of the suite, `valkey`, `yii3`, `protobuf`; `/opt/bench/meta.json` without `base`.

- [ ] **Step 1: Rewrite the server provisioning**

Replace `box/provision-server.sh` with:

```bash
#!/usr/bin/env bash
# Provisions the server box for the targets of one suite.
#
# Environment:
#   NIGHTLY         sha7 of a build on the nightly release of the core repository. REF is then ignored.
#   REF             a ref to build on the box when NIGHTLY is empty: a branch, a tag, a sha, or pr/N.
#   NEEDS           the server kinds and apps of the suite, space separated, from python3 -m rig needs.
#   FRAME_POINTERS  1 builds rapira with frame pointers for a perf session.
#
# Results:
#   /opt/bench/rapira/<sha7>/bin/rapira   the rapira under test
#   /opt/bench/meta.json                  the rapira identity for the run file
#   /opt/bench/versions.json              one version line per runtime
#   /opt/bench/php.ini                    the shared php.ini
#   /opt/bench/apps/yii3                  the Yii3 app with its vendor tree
#   /opt/bench/apps/grpc/vendor           the PHP protobuf runtime of the gRPC app
set -euo pipefail

NIGHTLY=${NIGHTLY:-}
REF=${REF:-}
NEEDS=${NEEDS:-}
FRAME_POINTERS=${FRAME_POINTERS:-0}
CORE_SLUG=${CORE_SLUG:-rapira-rs/rapira}
CORE_REPO=${CORE_REPO:-https://github.com/$CORE_SLUG}

BENCH=/opt/bench
RIG=$HOME/bench-rig
CORE=$HOME/core
export COMPOSER_NO_INTERACTION=1

# needs WORD succeeds when WORD is in NEEDS.
needs() {
  case " $NEEDS " in
  *" $1 "*) return 0 ;;
  *) return 1 ;;
  esac
}

install_packages() {
  local pkgs="php-cli php-opcache ethtool curl tar diffutils python3 chrony"
  if [ -n "$NIGHTLY" ]; then
    # The runtime libraries of the nightly build: the rpm depends list in nfpm.yaml of the core repository.
    pkgs="$pkgs libpq openssl-libs libcurl libxml2 sqlite-libs oniguruma zlib"
  else
    pkgs="$pkgs php-devel php-embedded clang clang-devel gcc make cmake git perf"
  fi
  if needs yii3 || needs grpc; then
    pkgs="$pkgs composer unzip git"
  fi
  if needs yii3; then
    # The PHP modules of the platform requirements of the app, and the cache server of its routes.
    pkgs="$pkgs php-mbstring php-xml php-pdo php-pgsql valkey"
  fi
  # shellcheck disable=SC2086
  sudo dnf -y install $pkgs
}

system_knobs() {
  sudo tee /etc/sysctl.d/90-rapira-bench.conf >/dev/null <<'CONF'
kernel.perf_event_paranoid = -1
kernel.kptr_restrict = 0
net.core.somaxconn = 65535
CONF
  sudo sysctl -q -p /etc/sysctl.d/90-rapira-bench.conf
  sudo tee /etc/security/limits.d/90-rapira-bench.conf >/dev/null <<'CONF'
fedora soft nofile 1048576
fedora hard nofile 1048576
CONF
}

# The loaders start each stage at a shared wall-clock time, so every box needs a synchronized clock.
check_clock() {
  sudo systemctl enable --now chronyd
  if ! chronyc waitsync 60 0.01 0 1 >/dev/null; then
    echo "ERROR: chrony is not synchronized after 60 s"
    chronyc tracking
    exit 1
  fi
  if ! chronyc tracking | grep -q '^Leap status *: Normal$'; then
    echo "ERROR: the chrony leap status is not Normal"
    chronyc tracking
    exit 1
  fi
}

# record_version NAME TEXT stores one version line in /opt/bench/versions.json.
record_version() {
  python3 - "$BENCH/versions.json" "$1" "$2" <<'PY'
import json, os, sys

path, name, text = sys.argv[1:4]
doc = {}
if os.path.exists(path):
    with open(path) as f:
        doc = json.load(f)
doc[name] = text
with open(path, "w") as f:
    json.dump(doc, f, indent=1, sort_keys=True)
    f.write("\n")
PY
}

# write_meta DIR REF SHA VERSION BUILD ASSET RUSTFLAGS writes /opt/bench/meta.json with the rapira under test.
write_meta() {
  python3 - "$@" <<'PY'
import hashlib, json, platform, sys

directory, ref, sha, version, build, asset, rustflags = sys.argv[1:8]
with open(directory + "/bin/rapira", "rb") as f:
    digest = hashlib.sha256(f.read()).hexdigest()
meta = {
    "rapira": {
        "ref": ref,
        "sha": sha,
        "version": version,
        "build": build,
        "asset": asset or None,
        "binary_sha256": digest,
        "rustflags": rustflags if build == "server" else None,
        "dir": directory,
    },
    "kernel": platform.release(),
}
with open("/opt/bench/meta.json", "w") as f:
    json.dump(meta, f, indent=1)
    f.write("\n")
PY
}

# resolve_nightly prints the full sha, the version, the tarball name, and the checksum file name
# of the NIGHTLY build on the nightly release.
resolve_nightly() {
  python3 - "$CORE_SLUG" "$NIGHTLY" <<'PY'
import json, re, sys, urllib.request

slug, sha7 = sys.argv[1], sys.argv[2]


def get(path):
    request = urllib.request.Request("https://api.github.com/repos/" + slug + path, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


# The core Nightly workflow moves the nightly tag to each new build and deletes the assets of older builds.
sha = get("/git/ref/tags/nightly")["object"]["sha"]
if not sha.startswith(sha7):
    sys.exit(f"ERROR: the nightly tag is at {sha[:7]}, not {sha7}; the release has no assets for {sha7}, rerun with NIGHTLY={sha[:7]}")
names = [asset["name"] for asset in get("/releases/tags/nightly")["assets"]]
tarball = re.compile(r"rapira-v(.+-nightly\." + re.escape(sha7) + r")-php8\.5-linux-x86_64\.tar\.gz")
found = [m for m in map(tarball.fullmatch, names) if m]
if len(found) != 1:
    sys.exit(f"ERROR: expected one php8.5 linux x86_64 tarball for {sha7} on the nightly release, found {len(found)}")
version = found[0].group(1)
sums = f"rapira-v{version}-SHA256SUMS.txt"
if sums not in names:
    sys.exit(f"ERROR: {sums} is missing on the nightly release")
print(sha, version, found[0].group(0), sums)
PY
}

install_nightly() {
  local resolved sha version asset sums
  local dir=$BENCH/rapira/$NIGHTLY dl=$HOME/nightly
  resolved=$(resolve_nightly)
  read -r sha version asset sums <<<"$resolved"
  rm -rf "$dl" "$dir"
  install -d "$dl" "$dir"
  curl -fsSL --retry 3 -o "$dl/$asset" "https://github.com/$CORE_SLUG/releases/download/nightly/$asset"
  curl -fsSL --retry 3 -o "$dl/$sums" "https://github.com/$CORE_SLUG/releases/download/nightly/$sums"
  (cd "$dl" && awk -v name="$asset" '$2 == name' "$sums" | sha256sum -c -)
  tar -xzf "$dl/$asset" -C "$dir" --strip-components=1
  if ldd "$dir/bin/rapira" 2>/dev/null | grep -F 'not found'; then
    echo "ERROR: $dir/bin/rapira has missing libraries"
    exit 1
  fi
  write_meta "$dir" nightly "$sha" "$version" nightly "$asset" ""
}

resolve_ref() {
  local r=$1
  case "$r" in
  pr/*)
    git -C "$CORE" fetch -q origin "refs/pull/${r#pr/}/head"
    git -C "$CORE" rev-parse FETCH_HEAD
    ;;
  *)
    git -C "$CORE" rev-parse --verify -q "origin/$r^{commit}" 2>/dev/null ||
      git -C "$CORE" rev-parse --verify "$r^{commit}"
    ;;
  esac
}

# build_one DIR SHA RUSTFLAGS builds rapira at SHA into DIR/bin/rapira and skips a build that is already there.
build_one() {
  local dir=$1 sha=$2 rustflags=$3
  local marker=$dir/build.marker
  if [ -f "$marker" ] && [ "$(cat "$marker")" = "$sha|$rustflags" ] && [ -x "$dir/bin/rapira" ]; then
    echo "==> $dir already built at $sha"
    return 0
  fi
  echo "==> build $dir at $sha"
  git -C "$CORE" checkout -q "$sha"
  (cd "$CORE" && env \
    RUSTFLAGS="$rustflags" \
    CARGO_PROFILE_RELEASE_DEBUG=line-tables-only \
    PHP_CONFIG=/usr/bin/php-config \
    CARGO_TARGET_DIR="$HOME/core-target" \
    cargo build --release)
  install -d "$dir/bin"
  install -m 0755 "$HOME/core-target/release/rapira" "$dir/bin/rapira"
  echo "$sha|$rustflags" >"$marker"
}

build_server() {
  local root_kb sha sha7 version
  local rustflags=""
  root_kb=$(df -Pk / | awk 'NR==2 {print $2}')
  if [ "$root_kb" -lt $((30 * 1024 * 1024)) ]; then
    echo "ERROR: root filesystem is $((root_kb / 1024 / 1024)) GiB; expected >= 30 GiB"
    exit 1
  fi
  if [ ! -x "$HOME/.cargo/bin/cargo" ]; then
    curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal
  fi
  # shellcheck disable=SC1091
  . "$HOME/.cargo/env"
  if [ ! -d "$CORE/.git" ]; then
    git clone "$CORE_REPO" "$CORE"
  fi
  git -C "$CORE" fetch origin --tags --prune
  if [ "$FRAME_POINTERS" = 1 ]; then
    rustflags="-C force-frame-pointers=yes"
  fi
  sha=$(resolve_ref "$REF")
  sha7=$(git -C "$CORE" rev-parse --short=7 "$sha")
  build_one "$BENCH/rapira/$sha7" "$sha" "$rustflags"
  version=$(git -C "$CORE" describe --tags --always "$sha")
  write_meta "$BENCH/rapira/$sha7" "$REF" "$sha" "$version" server "" "$rustflags"
}

# source_value KEY prints the value of KEY in apps/yii3/source.toml.
source_value() {
  python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))[sys.argv[2]])' "$RIG/apps/yii3/source.toml" "$1"
}

install_yii3() {
  local dir=$BENCH/apps/yii3 commit
  commit=$(source_value commit)
  rm -rf "$dir"
  git clone -q "$(source_value repo)" "$dir"
  git -C "$dir" checkout -q "$commit"
  install -m 0644 "$RIG/apps/yii3/composer.lock" "$dir/composer.lock"
  composer --working-dir="$dir" install --no-dev --no-progress --no-scripts --classmap-authoritative
  # The app reads its compiled routes from Valkey when a process builds its container.
  sudo systemctl enable --now valkey
  record_version valkey "$(valkey-server --version)"
  record_version yii3 "$commit"
}

install_grpc_runtime() {
  local dir=$BENCH/apps/grpc
  rm -rf "$dir"
  install -d "$dir"
  install -m 0644 "$RIG/apps/grpc/composer.json" "$RIG/apps/grpc/composer.lock" "$dir/"
  composer --working-dir="$dir" install --no-dev --no-progress
  record_version protobuf "$(python3 -c 'import json, sys; print(next(p["version"] for p in json.load(open(sys.argv[1]))["packages"] if p["name"] == "google/protobuf"))' "$dir/composer.lock")"
}

if [ -z "$NIGHTLY" ] && [ -z "$REF" ]; then
  echo "ERROR: set NIGHTLY=<sha7> or REF=<branch, tag, sha, or pr/N>"
  exit 1
fi

echo "==> packages"
install_packages
echo "==> system knobs"
system_knobs
echo "==> clock"
check_clock

sudo install -d -o fedora -g fedora "$BENCH" "$BENCH/run" "$BENCH/log" "$BENCH/apps" "$BENCH/rapira"
rm -f "$BENCH/versions.json"
install -m 0644 "$RIG/servers/php.ini" "$BENCH/php.ini"
# Fedora PHP reads /etc/php.d after PHPRC, and its 10-opcache.ini there sets other opcache values.
sudo install -m 0644 "$RIG/servers/php.ini" /etc/php.d/99-bench.ini
ini=$(php -c "$BENCH/php.ini" -r 'echo ini_get("opcache.enable_cli"), " ", ini_get("opcache.validate_timestamps"), " ", ini_get("opcache.memory_consumption"), " ", ini_get("display_errors");')
if [ "$ini" != "1 0 256 0" ]; then
  echo "ERROR: effective php.ini values are $ini, expected 1 0 256 0"
  exit 1
fi
record_version php "$(php -v | sed -n 1p)"

if [ -n "$NIGHTLY" ]; then
  echo "==> rapira nightly $NIGHTLY"
  install_nightly
else
  echo "==> rapira server build of $REF"
  build_server
fi

if needs grpc; then
  echo "==> grpc runtime"
  install_grpc_runtime
fi
if needs yii3; then
  echo "==> yii3"
  install_yii3
fi

echo "==> server provisioned: needs=$NEEDS"
python3 -c 'import json; print(json.dumps(json.load(open("/opt/bench/meta.json"))["rapira"]))'
```

- [ ] **Step 2: Change the loader provisioning**

In `box/provision-loader.sh`:
- The header becomes:

```bash
# Provisions a loader box: wrk2 at a pinned commit, h2load from the nghttp2 package, the kernel knobs,
# and a clock check.
#
# Results:
#   /usr/local/bin/wrk2, /usr/bin/h2load
#   /opt/bench/loader.json   the instance id, the wrk2 commit and version line, and the h2load version line
```

- Delete `K6_VERSION=...` and the `install_k6` function.
- `write_record` reads `h2load_line=$(h2load --version | sed -n 1p)` in place of the k6 line, passes it as the fifth argument, and the Python dumps `{"instance_id": instance_id, "wrk2_commit": commit, "wrk2_version": wrk2, "h2load_version": h2load}` with the variable renamed.
- The package line becomes `sudo dnf -y install gcc make git openssl-devel zlib-devel binutils ethtool curl tar diffutils python3 chrony nghttp2`.
- Delete the two lines `echo "==> k6 $K6_VERSION"` and `install_k6`.

- [ ] **Step 3: Change the rig knobs**

In `terraform/variables.tf`:

```hcl
variable "loader_instance_type" {
  description = "One load generator. A c7a.2xlarge has 8 vCPUs and a 3.125 Gbps baseline for the 250000 req/s HTTP rows with 5000 connections and the 100000 req/s gRPC row."
  type        = string
  default     = "c7a.2xlarge"
}

variable "loader_count" {
  description = "The number of loaders. The rate and the connection count of a stage are split evenly over them."
  type        = number
  default     = 1

  validation {
    condition     = var.loader_count >= 1
    error_message = "loader_count must be 1 or more."
  }
}
```

In the `Makefile`:
- `LOADER_TYPE ?= c7a.2xlarge` and `LOADER_COUNT ?= 1`.
- Delete `BASE_REF ?= main`.
- `provision:` runs `python3 -m rig provision --ttl $(TTL) --needs "$$needs" --nightly "$(NIGHTLY)" --ref "$(REF)" --frame-pointers "$(FRAME_POINTERS)"`.
- Above `lock:` add the comment `# Creates the Yii3 and gRPC lock files and the Yii3 expected body. Needs PHP 8.5, Composer, and a Valkey on 127.0.0.1:6379.`

- [ ] **Step 4: Change the workflow**

In `.github/workflows/bench.yml`:
- `timeout-minutes: 110` of the job becomes `60`; the comment above `role-duration-seconds` becomes `# The credentials must last for the whole 60 minute job, the destroy included.`
- The `Create and provision the rig` step keeps its 30 minute timeout.
- In the `Resolve the nightly commit` step, after the `echo "sha7=$sha7" >> "$GITHUB_OUTPUT"` line, add:

```bash
          # The merged pull request of the commit labels the run on the board. A commit without one gets the sha.
          pr=$(gh api "repos/rapira-rs/rapira/commits/$sha/pulls" --jq '[.[] | select(.merged_at != null)][0]')
          if [ -n "$pr" ] && [ "$pr" != null ]; then
            echo "pr_number=$(jq -r .number <<<"$pr")" >> "$GITHUB_OUTPUT"
            echo "pr_url=$(jq -r .html_url <<<"$pr")" >> "$GITHUB_OUTPUT"
            echo "pr_title=$(jq -r .title <<<"$pr")" >> "$GITHUB_OUTPUT"
          fi
```

- The `Run the ci suite` step becomes:

```yaml
      - name: Run the ci suite
        if: steps.resolve.outputs.skip == 'false'
        timeout-minutes: 30
        env:
          PR_NUMBER: ${{ steps.resolve.outputs.pr_number }}
          PR_URL: ${{ steps.resolve.outputs.pr_url }}
          PR_TITLE: ${{ steps.resolve.outputs.pr_title }}
        run: |
          set -euo pipefail
          pr=()
          if [ -n "$PR_NUMBER" ]; then
            pr=(--pr-number "$PR_NUMBER" --pr-url "$PR_URL" --pr-title "$PR_TITLE")
          fi
          python3 -m rig bench --suite ci --out runs "${pr[@]}"
```

- [ ] **Step 5: Check the scripts and the stack**

```bash
bash -n box/provision-server.sh box/provision-loader.sh box/lock-apps.sh box/load.sh
terraform -chdir=terraform fmt -check
python3 -m rig needs --suite ci
make test
```

Expected: no output from `bash -n` and `fmt -check`; `rapira grpc hello yii3`; PASS.

- [ ] **Step 6: Commit**

```bash
git add box/provision-server.sh box/provision-loader.sh terraform/variables.tf Makefile .github/workflows/bench.yml
git commit -s -S -m "feat!: provision rapira with the Yii3 app and Valkey on a one-loader rig"
```

---

### Task 9: Documentation

**Files:**
- Modify: `METHOD.md` (rewrite), `README.md` (rewrite), `docs/operations.md`, `NOTES.md`, `.claude/CLAUDE.md` (local, not tracked)

**Interfaces:**
- Consumes: everything above.
- Produces: the documents that `README.md` links.

- [ ] **Step 1: Rewrite METHOD.md**

Replace `METHOD.md` with:

```markdown
# Benchmark method

This document defines how the rig measures a target, which numbers it reports, when it voids a cell, and how to review a run before publication. [README.md](README.md) gives the overview and [docs/operations.md](docs/operations.md) the operations. [NOTES.md](NOTES.md) keeps dated records.

## Terms

- A target is rapira in one mode with one app, for example `hello-rapira-worker`. `suites/targets.toml` defines every target.
- A cell is one target in one round. Its key is `r<round>-<target>`.
- A stage is the one load run of a cell: a warm-up of `warmup_s` seconds, then `duration_s` seconds measured, at the rate of the app of the target.

## Rig rules

- Run benchmarks only on the EC2 rig of this repository: one server and `LOADER_COUNT` loaders in one cluster placement group.
- Run one target at a time on the server.
- Send all load to the private address of the server.
- Keep the server type, the loader type, the loader count, the Availability Zone, the AMI, the suite, the worker count, the rates, and the duration equal for one comparison. `make compare` refuses a pair with a different rig shape, worker count, rate, or duration.
- Every target runs `PROCESSES` workers. The default is the server CPU count. The start script verifies the worker count after the start, and a different count fails the start.
- Every PHP process uses the shared `servers/php.ini`. The run file records its text.
- The driver runs the cells in rotated order: round r starts at target r of the suite and wraps. No target is always first.
- Pin `AMI` when a result set takes more than one day.

## Load tools

wrk2 loads the HTTP/1.1 targets. h2load loads the gRPC target over h2c. Each loader runs one load process per cell.

These wrk2 facts shape the method:

- `-R` is the total request rate of one process. wrk2 divides it over its threads and connections.
- wrk2 divides `-c` by `-t` with integer division and drops the remainder. The driver therefore requires a connection count that is a multiple of the thread count.
- The first 10 seconds of a run are a calibration window. wrk2 resets the latency histogram after that window but keeps the request count. The warm-up of the suite is 10 seconds, so the reported latency covers the measured window and the request count covers the whole run.
- The reported latency is corrected for coordinated omission.
- The `status` error counter counts responses with a status above 399. The `timeout` counter is a tally per connection that wrk2 takes every 2 seconds. It is not a request count.
- An overloaded target gives no wrk2 errors. The achieved rate falls below the requested rate, and the corrected latency grows to seconds.
- wrk2 sends HTTP/1.1 only.

These h2load facts shape the method:

- `--rps` is the rate per connection. The driver divides the rate of a loader by its connection count. `-m` limits the streams in flight per connection.
- `--warm-up-time` and `-D` set the warm-up and the measured window. h2load counts only the requests that end in the measured window, and writes one line per such request to `--log-file`: the start time, the HTTP status or -1 for a failed stream, and the response time. `loader/h2load-report.py` turns that log into the `RESULT` line.
- h2load does not read the `grpc-status` trailer. A gRPC error inside a 200 response is invisible to the counters. The probes before and after the stage are the correctness check.

## The cell sequence

The driver runs this sequence for each cell:

1. It starts the target on the server and verifies the listener process, the executable, and the worker count.
2. Each loader sends one probe and compares the response body with the expected file byte for byte.
3. The driver sets a start time 3 seconds ahead and starts, in one parallel batch, one load process on each loader and two timed samples on every box: at the start of the measured window and half a second after its end. The busy CPU, the ENA deltas, and the TIME-WAIT growth come from these samples.
4. The driver reads the RSS of the target: the sum of `VmRSS` over the listener and its workers.
5. One loader sends one more probe.
6. The driver stops the target, verifies that its processes are gone, and reads the WARN and ERROR lines of its log.

A load process that starts more than 1000 ms after the start time voids the cell. Provisioning verifies that chrony is synchronized on every box, so the shared start time is valid to much less than one second.

## Rates and connections

The `ci` suite sends 250000 req/s to every HTTP target over 5000 connections and 100000 req/s to the gRPC target over 100 connections with up to 100 streams each. Each loader sends the rate divided by the loader count over the connections divided by the loader count, with one wrk2 thread per vCPU.

## Merge over loaders

For each cell, the driver adds the requests, the bytes, and each error counter of all loaders. It then calculates:

- `achieved_rps`: the requests divided by the seconds the tool counted: `warmup_s` plus `duration_s` for wrk2, `duration_s` for h2load.
- `successful_rps`: the requests minus the status errors, divided by the same seconds.
- Each latency percentile: the maximum over the loaders. This value is an upper bound, not a pooled percentile. The cell keeps the record of each loader, so a reader can see the spread.

## Reported numbers

- `achieved_rps`: the rate the target answered.
- `held`: true when `achieved_rps` is at least 95% of the rate and every error counter is 0. A target that did not hold is not a failure: the row shows the rate it achieved.
- `latency_us`: p50, p90, p99, p99.9, and max over the measured window. The board shows the p99.
- `rss_kb`: the RSS of the rapira process tree at the end of the stage. The board shows it in MiB.

With more than one round, the report shows the median of the ok cells, and `held` only when every ok cell held.

## Flags

Flags carry values. They are review items. They do not make a cell fail.

- `generator_bound`: the cell did not hold, the busy CPU of a loader is 85% or more, and the server is below 90%. The achieved rate is then a floor. State it as "at least" the value.
- `server_unsaturated`: the cell did not hold, the server is below 90%, and every loader is below 85%. The target failed for a reason other than CPU, for example a queue in its worker pool.
- `ena_throttled`: the ENA allowance counters of the server changed during the stage. The flag gives the deltas. Do not use that cell for a throughput claim.
- `keepalive_broken`: the TIME-WAIT count of the server grew by more than the connection count during the stage. The target does not keep connections open.
- `worker_churn`: the worker process list changed during the cell.
- `log_growth`: the server log grew by more than 65536 bytes during the cell. The flag gives the byte count.
- `died`: the probe after the stage failed. The target stopped answering.

## Voids

A void excludes the cell from every number and makes the run incomplete. The numbers of a voided cell are null; its raw directory keeps the evidence. The driver voids a cell when:

- The target does not start, or its worker count differs from `PROCESSES`.
- The probe before the stage does not match the expected response on a loader.
- A loader gives no `RESULT` line, or a `RESULT` line that the driver cannot read. The reason names the loader.
- A loader starts the stage more than 1000 ms late.
- The ENA allowance counters of a loader change during the stage. The network shaped that loader, so the stage does not measure the server.
- rapira logs a WARN or ERROR line during the cell. rapira runs at log level `warn`. The raw directory keeps the lines.
- An ssh command to a box fails during the cell. The probe after the stage is the exception: a failed probe sets the `died` flag.
- The driver cannot stop the target or read its log.

An interrupted run stops the current target and writes the run file with the status `incomplete`. The interrupted cell has the status `incomplete`, and the cells that did not run are listed as missing.

## Review before publication

Publish a result only when all these conditions are true:

- `make bench` and `make report` return status 0.
- The report has no `Do not publish these tables.` line.
- Each reported row has the planned number of rounds.
- No flag changes the stated conclusion. Read the flag rules above for each flag in the row.
- `run.json` contains the expected rapira build, binary SHA-256, AMI, instance types, loader count, worker count, and app hashes.
- The raw files support the values in the report.
- `run.json` records the wrk2 commit and the h2load version of each loader.

If the result depends on a response header or on a server configuration, capture that evidence before the measured run and keep it with the run. The raw directory already keeps the rendered configuration of every target.

## Reading the board

The board on the `gh-pages` branch shows the newest 60 runs. One run is one merged pull request on the rapira main branch, benched from its nightly build.

- The p99 chart shows the p99 latency of every target in milliseconds on a logarithmic scale. The RSS chart shows the RSS of the rapira process tree in MiB.
- The x axis lists the runs in order, labelled with the pull request number. A run without a pull request, for example a manual run, shows the first 7 characters of the rapira sha. A click on a point opens the pull request, or the commit.
- The tooltip shows the achieved rate against the requested rate, whether the target held the rate, and the flags of the cell. Read the flag rules above before you draw a conclusion from a point.
- A voided cell and a run without the target give no point.
- Smoke runs are not shown.

Compare points only when the rig shape is the same. Numbers from before 2026-09-25 come from other methods and are not on the board.

## Server facts

- A target listens on port 8080.
- The static target runs the hello dispatcher behind the static middleware with `apps/hello` as its root. The request misses the root, so the row shows the cost of the middleware on the PHP path. Compare it with the plain dispatcher row of the same run.
- The Yii3 target runs the app-api of `apps/yii3/source.toml` on the dispatcher in its `prod` environment without debug. Its `/` route answers from the application parameters. The route cache lives in the Valkey service of the server. No request touches a database.
- The gRPC target uses the pure PHP protobuf runtime.

## Connection distribution tests

These rules apply to a special test of how a server spreads connections over its workers:

- Pin the reproducer commit and its dependency lock file.
- Restart the server before each comparison cell.
- Record the request count, the CPU use, and the accepted sockets of each worker. The total CPU use does not show the distribution.
- Take the last request counter sample before the load process stops. The stop of a client can cancel the requests in progress.
- For exact connection counts, stop the other clients and match each accepted socket by the client address and source port.
```

- [ ] **Step 2: Rewrite README.md**

Replace `README.md` with:

```markdown
# Rapira benchmarks

The benchmark rig of [rapira](https://github.com/rapira-rs/rapira). It runs on Amazon EC2: one server, one loader, and a constant request rate per target.

## What is tested

Rapira in its classic, worker, and dispatcher modes on a hello app, the dispatcher behind the static middleware, the dispatcher with a Yii3 API app, and a gRPC echo service: six targets, listed in `suites/targets.toml`. The `ci` suite runs them after each nightly build of rapira main.

Each target reports two numbers at its rate: the p99 latency and the RSS of the rapira process tree, next to the rate it achieved. [METHOD.md](METHOD.md) defines the stage, the `held` rule, the flags, and the voids.

## Where the results are

- The board: https://rapira.rs/benchmarks/ (two charts, p99 and RSS, one line per target, the merged pull requests on the x axis).
- The run files: `run.json` and the raw evidence of every CI run, as workflow artifacts and in the `gh-pages` branch under `data/`.
- [NOTES.md](NOTES.md): dated records, including the numbers of the earlier methods.

[docs/operations.md](docs/operations.md) has the commands, the knobs, the cost, and the CI setup.
```

- [ ] **Step 3: Change docs/operations.md**

In `docs/operations.md` replace these parts. Every other line stays.

The "Standard flow" section, from `Bench a Git ref that the server builds from source:` to the paragraph that starts with `Provisioning installs only the servers and apps`, becomes:

```markdown
Bench a Git ref that the server builds from source:

```bash
make up REF=pr/97
make bench
make down
```

`make up` checks the vCPU quota, writes `terraform/rig.auto.tfvars`, applies the Terraform stack, and runs `make provision`. `make bench` runs the suite, writes the run file, and prints the report. `make down` destroys the rig.

`NIGHTLY` is the first 7 characters of the commit SHA of a nightly release asset of `rapira-rs/rapira`. The core Nightly workflow deletes the assets of older builds, so use the SHA of the current `nightly` release. `REF` accepts a branch, a tag, a commit, or `pr/N`. When you set `NIGHTLY`, provisioning ignores `REF`.

Provisioning installs only the apps that the suite uses. Set the same `SUITE` on `make up` and on `make bench`.
```

The software table becomes:

```markdown
| Instance | Condition | Software |
| --- | --- | --- |
| Server | All runs | PHP with opcache, the shared `servers/php.ini`, and the rapira binary: the nightly release asset, or a build of `REF` |
| Server | Yii3 target | The PHP modules `mbstring`, `xml`, `pdo`, and `pgsql`, Composer, the app-api at the commit of `apps/yii3/source.toml` with the dependencies of the committed `composer.lock`, and the Valkey service for its route cache |
| Server | gRPC target | Composer and the pure PHP protobuf runtime of `apps/grpc/composer.lock` |
| Loader | All runs | wrk2 at commit `44a94c1`, h2load from the Fedora `nghttp2` package, and a chrony synchronization check |
```

The "Suites" section becomes:

```markdown
## Suites

`suites/targets.toml` defines every target. A target name is `<app>-rapira-<mode>`, with the suffix `-static` for the static middleware; the gRPC target is `grpc-rapira`. A suite file lists its targets, the number of rounds, the warm-up and the measured duration in seconds, the rate of each app, the connection count of each proto, and the stream count of the gRPC connections.

- `ci` is the per-merge suite: the six targets, one round, 250000 req/s over 5000 connections for the HTTP targets and 100000 req/s over 100 connections for the gRPC target, 60 s measured after a 10 s warm-up.

The driver refuses a suite before it creates a run directory when one of these conditions is true:

- An app of a target has no rate, or a proto of a target has no connection count.
- A rate or a connection count is not a multiple of the loader count.
- `duration_s` is less than 30.
- The HTTP connection count is not a multiple of the loader count times the loader vCPU count.
```

In "Settings": delete the `BASE_REF` sentence from the `REF` line; the `SERVER_TYPE` line becomes `- `SERVER_TYPE` defaults to `c7a.8xlarge`. `LOADER_TYPE` defaults to `c7a.2xlarge`. `LOADER_COUNT` defaults to 1.`; the `FRAME_POINTERS` line stays.

In "Other targets": the `make lock` line becomes `- `make lock` creates `apps/yii3/composer.lock`, `apps/yii3/expect.json`, and `apps/grpc/composer.lock` from the pinned sources. It needs PHP 8.5, Composer, and a Valkey or Redis server on 127.0.0.1:6379 on the operator machine, for example `docker run --rm -d -p 127.0.0.1:6379:6379 valkey/valkey:9.1.2-alpine`.`

In "Results": the `run.json` line becomes `- `run.json`: the run file with the schema `rapira-bench-run/2`. It records the rig, the rapira build and its pull request, the version lines of the server, the php.ini text, the app hashes, the loaders, the suite, and every cell with its rate, its achieved rate, its latency, its RSS, its `held` state, its flags, and the record of each loader.`; the `raw/<cell>/` line becomes `- `raw/<cell>/`: the wrk2 or h2load output of each loader with its `RESULT` line, the rendered rapira config, the WARN and ERROR lines of the server log, and the snapshots.`; the `make compare` paragraph names `rate, or duration` in place of `or stage duration`.

In "Cost and teardown": the first paragraph becomes `The instances use on-demand billing per second. The `ci` suite takes about 10 minutes of cells; with the rig creation, the provisioning, and the destroy a CI run holds the rig for about 30 minutes and costs about $1 with the default rig. The server type is most of the cost.`; the TTL sentence becomes `\`make bench\` extends it from the run estimate: the number of cells times (\`warmup_s\` plus \`duration_s\` plus 60) plus 300 seconds.`

In "CI": `The bench job stops after 110 minutes.` becomes `The bench job stops after 60 minutes.`; after `Then it creates the rig with the S3 backend, provisions it with the nightly asset,` insert `looks up the merged pull request of that commit for the board label,`.

- [ ] **Step 4: Add the NOTES.md entry**

Insert above `## First ladder run, 2026-09-25`:

```markdown
## Method change 2, 2026-09-25

- From this date the rig measures one constant rate per target, as METHOD.md defines: 250000 req/s over 5000 connections for the HTTP targets and 100000 req/s over 100 connections for the gRPC target, 60 s measured after a 10 s warm-up, from one c7a.2xlarge loader against one c7a.8xlarge server. The numbers are the p99 latency and the RSS of the rapira process tree next to the achieved rate.
- The targets are rapira only: hello in the classic, worker, and dispatcher modes, the dispatcher behind the static middleware, the Yii3 app-api on the dispatcher, and gRPC echo. FrankenPHP, php-fpm, nginx, RoadRunner, Symfony, Laravel, the static file rows, the gRPC-Web and Connect rows, k6, the base build, and the `full` and `ab` suites are removed.
- Each run writes `runs/<id>/run.json` with the schema `rapira-bench-run/2`. The ladder run of the same day, `20260925T190409Z-ci-73b9d30`, has the schema 1: its numbers do not compare with the runs after it, and the board does not draw it.
```

In the "Method change, 2026-09-25" section, the first bullet gains the sentence `This method was replaced the same day, see the next section.`

- [ ] **Step 5: Update the local project instructions**

In `.claude/CLAUDE.md` (untracked), the first paragraph becomes: `This repo is the AWS bench rig for rapira: Terraform (one server and N loaders in one placement group) plus the Python operator package `rig/`, which runs one constant-rate stage per target over ssh with wrk2 for the HTTP/1.1 targets and h2load for the gRPC target. What to know:`. The "Words" bullet becomes `- Words: "target" is the thing under test, "cell" is one target in one round, "stage" is its one load run. Never "leg", never "ladder".`

- [ ] **Step 6: Check the documents**

```bash
grep -n -P '[\x{2013}\x{2014}]' METHOD.md README.md docs/operations.md NOTES.md && echo "dashes found" || echo "no dashes"
grep -n -i -E 'k6|frankenphp|php-fpm|roadrunner|symfony|laravel|ladder|BASE_REF|stage_s|floor' README.md docs/operations.md METHOD.md
```

Expected: `no dashes`; the second grep prints nothing from README.md and docs/operations.md, and from METHOD.md nothing. Only NOTES.md keeps these words as history.

- [ ] **Step 7: Commit**

```bash
git add METHOD.md README.md docs/operations.md NOTES.md
git commit -s -S -m "docs: describe the constant-rate method, the six targets, and the board"
```

---

## After the last task

- Open the pull request from `feat/constant-rate` to `main` with a short description of the change: the constant-rate stage, the six rapira targets, h2load for gRPC, the Yii3 app with Valkey, the two-chart board, the schema 2 run file, and the removals.
- Operator steps after the merge, in this order:
  1. `make up REF=<branch with gRPC>` or `make up NIGHTLY=<sha7>`, then `make bench`, then `make report`. Review the run as METHOD.md states. The rapira main nightly has no gRPC until the gRPC branch is merged, so a nightly run voids the `grpc-rapira` cell at the start and stays incomplete; the first complete run needs a `REF` build of that branch, as the first ladder run did.
  2. Publish the first complete run by hand: `python3 -m rig publish --pages-dir <gh-pages clone> runs/<id>/run.json`, copy `board/index.html`, `board/app.js`, `board/style.css`, and `board/chart.umd.js` into the clone, commit, and push. Remove `data/20260925T190409Z-ci-73b9d30.json` and its entry from `data/index.json` in the same commit: the board does not draw a schema 1 run, and the file has no reader.
  3. `make down`.
  4. Start one CI run by hand with `gh workflow run bench.yml -R rapira-rs/benchmarks` and read its log: the resolve step prints the pull request, the bench step ends within 30 minutes.
