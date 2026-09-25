"""Tests for box/load.sh with a fake wrk2 and a fake k6, and for loader/wrk2-report.lua under luajit."""

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
K6_SCRIPT = ROOT / "loader" / "k6-grpc.js"

URL = "http://10.0.1.5:8080/?name=you"
GRPC_URL = "http://10.0.1.5:8080/bench.v1.EchoService/Echo"
TREND_STATS = "avg,med,p(90),p(95),p(99),p(99.9),max"

# The RESULT line of the rig contract without tool and late_ms, as a reporter prints it.
REPORTER_RESULT = {
    "duration_us": 20000867,
    "requests": 39989,
    "bytes": 5038614,
    "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
    "latency_us": {"mean": 689.5, "p50": 689, "p90": 1111, "p95": 1175, "p99": 1264, "p999": 1351, "max": 2822},
    "requests_per_sec": 1999.363,
}
HUMAN_LINES = ["Running 20s test @ http://10.0.1.5:8080/?name=you", "  4 threads and 64 connections"]
TOOL_OUTPUT = "\n".join(HUMAN_LINES + ["RESULT " + json.dumps(REPORTER_RESULT)]) + "\n"
FAILED_OUTPUT = "unable to connect to 10.0.1.5:8080 Connection refused\n"

# The fake tool writes its arguments and the request environment to FAKE_LOG,
# prints the file FAKE_OUTPUT, and exits with FAKE_EXIT.
FAKE_TOOL = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    names = ("WRK_METHOD", "WRK_BODY_FILE", "WRK_HEADERS")
    with open(os.environ["FAKE_LOG"], "w") as f:
        json.dump({"argv": sys.argv[1:], "env": {n: os.environ.get(n) for n in names}}, f)
    with open(os.environ["FAKE_OUTPUT"]) as f:
        sys.stdout.write(f.read())
    sys.exit(int(os.environ["FAKE_EXIT"]))
    """)

WRK2_ARGV = ["-t", "4", "-c", "64", "-d", "20s", "-R", "2500", "--latency", "-s", str(REPORT), URL]
NO_WRK_ENV = {"WRK_METHOD": None, "WRK_BODY_FILE": None, "WRK_HEADERS": None}

# EPOCH in args is replaced with time.time() + epoch_offset. late_ms is the
# inclusive range the script must report. A future epoch is 0.5 s ahead, which
# is more than the start-up time of bash and python3, so late_ms is 0.
LOAD_CASES = [
    {
        "name": "wrk2 get with a future epoch",
        "args": ["wrk2", "EPOCH", "2500", "4", "64", "20", URL],
        "epoch_offset": 0.5,
        "argv": WRK2_ARGV,
        "env": {"WRK_METHOD": "GET", "WRK_BODY_FILE": "-", "WRK_HEADERS": ""},
        "late_ms": (0, 0),
    },
    {
        "name": "wrk2 post with a relative body and two headers, 5 s late",
        "args": ["wrk2", "EPOCH", "2500", "4", "64", "20", URL, "POST", "apps/grpc/echo.grpc",
                 "content-type: application/grpc-web+proto", "x-grpc-web: 1"],
        # The epoch is 5 s in the past, so late_ms is 5000 plus the start-up time of the script.
        "epoch_offset": -5,
        "argv": WRK2_ARGV,
        "env": {
            "WRK_METHOD": "POST",
            "WRK_BODY_FILE": str(ROOT / "apps" / "grpc" / "echo.grpc"),
            "WRK_HEADERS": "content-type: application/grpc-web+proto\nx-grpc-web: 1",
        },
        "late_ms": (5000, 5999),
    },
    {
        "name": "wrk2 keeps an absolute body path",
        "args": ["wrk2", "EPOCH", "2500", "4", "64", "20", URL, "POST", "/srv/body.bin"],
        "epoch_offset": 0.5,
        "argv": WRK2_ARGV,
        "env": {"WRK_METHOD": "POST", "WRK_BODY_FILE": "/srv/body.bin", "WRK_HEADERS": ""},
        "late_ms": (0, 0),
    },
    {
        "name": "k6 grpc stage",
        "args": ["k6", "EPOCH", "4000", "64", "20", GRPC_URL],
        "epoch_offset": 0.5,
        "argv": ["run", "--quiet", "--no-color", "--summary-trend-stats", TREND_STATS,
                 "-e", f"TARGET={GRPC_URL}", "-e", "RATE=4000", "-e", "DURATION=20", "-e", "VUS=64", str(K6_SCRIPT)],
        "env": NO_WRK_ENV,
        "late_ms": (0, 0),
    },
]

# The tool fails and prints no RESULT line. load.sh keeps the output and exits 0.
MISSING_RESULT_CASES = [
    {"name": "wrk2 failure", "args": ["wrk2", "0", "2500", "4", "64", "20", URL]},
    {"name": "k6 failure", "args": ["k6", "0", "4000", "64", "20", GRPC_URL]},
]

USAGE_CASES = [
    {"name": "unknown tool", "args": ["curl", "0", "1", "1", "1", "1", URL]},
    {"name": "wrk2 without a url", "args": ["wrk2", "0", "2500", "4", "64", "20"]},
    {"name": "k6 with an extra argument", "args": ["k6", "0", "4000", "64", "20", GRPC_URL, "POST"]},
    {"name": "no arguments", "args": []},
]


class LoadScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        for name in ("wrk2", "k6"):
            path = bin_dir / name
            path.write_text(FAKE_TOOL)
            path.chmod(0o755)
        self.log = tmp / "fake.json"
        self.output = tmp / "output.txt"
        env = {k: v for k, v in os.environ.items() if not k.startswith("WRK_")}
        env.update(PATH=f"{bin_dir}:{env['PATH']}", FAKE_LOG=str(self.log), FAKE_OUTPUT=str(self.output))
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
                proc = self.load(args, TOOL_OUTPUT, 0)
                done = time.time()
                self.assertEqual(proc.returncode, 0, proc.stderr)
                lines = proc.stdout.splitlines()
                self.assertEqual(lines[:-1], HUMAN_LINES)
                self.assertTrue(lines[-1].startswith("RESULT "))
                result = json.loads(lines[-1][len("RESULT "):])
                low, high = case["late_ms"]
                self.assertGreaterEqual(result["late_ms"], low)
                self.assertLessEqual(result["late_ms"], high)
                self.assertEqual(result, {"tool": case["args"][0], "late_ms": result["late_ms"], **REPORTER_RESULT})
                self.assertGreaterEqual(done, epoch)
                fake = json.loads(self.log.read_text())
                self.assertEqual(fake["argv"], case["argv"])
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


BODY = b"\x00\x00\x00\x00\x03\n\x01x"

# The harness defines the wrk table the way src/wrk.lua does, runs init(), and
# prints the request shape. Header lines are sorted by name.
INIT_HARNESS = textwrap.dedent("""\
    wrk = { method = "GET", headers = {}, body = nil }
    dofile(arg[1])
    init({})
    io.write("method ", wrk.method, "\\n")
    if wrk.body then
      io.write("body ", (wrk.body:gsub(".", function(c) return string.format("%02x", c:byte()) end)), "\\n")
    end
    local names = {}
    for name in pairs(wrk.headers) do names[#names + 1] = name end
    table.sort(names)
    for _, name in ipairs(names) do io.write("header ", name, "=", wrk.headers[name], "\\n") end
    """)

# BODY in env is replaced with the path of a file that holds the bytes of BODY.
INIT_CASES = [
    {"name": "no environment keeps the default request", "env": {}, "lines": ["method GET"]},
    {
        "name": "post with a binary body and headers",
        "env": {
            "WRK_METHOD": "POST",
            "WRK_BODY_FILE": "BODY",
            "WRK_HEADERS": "content-type: application/grpc-web+proto\nx-grpc-web: 1\nx-note: a:b",
        },
        "lines": [
            "method POST",
            "body " + BODY.hex(),
            "header content-type=application/grpc-web+proto",
            "header x-grpc-web=1",
            # Only the first colon splits the name from the value.
            "header x-note=a:b",
        ],
    },
    {
        "name": "dash body file and empty headers mean no body and no headers",
        "env": {"WRK_METHOD": "GET", "WRK_BODY_FILE": "-", "WRK_HEADERS": ""},
        "lines": ["method GET"],
    },
]

DONE_HARNESS = textwrap.dedent("""\
    dofile(arg[1])
    local pct = {{ [50] = {p50}, [90] = {p90}, [95] = {p95}, [99] = {p99}, [99.9] = {p999} }}
    local latency = {{ mean = {mean}, max = {max} }}
    function latency:percentile(p) return pct[p] end
    done({{ duration = {duration}, requests = {requests}, bytes = {bytes},
      errors = {{ connect = {connect}, read = {read}, write = {write}, status = {status}, timeout = {timeout} }} }},
      latency, nil)
    """)

DONE_CASES = [
    {
        # 39989 requests in 20.000867 s is 1999.3633 req/s, printed with 3 decimals.
        "name": "contract sample",
        "stats": {"duration": 20000867, "requests": 39989, "bytes": 5038614, "connect": 0, "read": 0, "write": 0,
                  "status": 0, "timeout": 0, "mean": 689.5, "p50": 689, "p90": 1111, "p95": 1175, "p99": 1264,
                  "p999": 1351, "max": 2822},
        "result": REPORTER_RESULT,
    },
    {
        # An empty histogram gives a NaN mean, and a zero duration gives no rate. Both print as 0.
        "name": "empty histogram and zero duration",
        "stats": {"duration": 0, "requests": 0, "bytes": 0, "connect": 0, "read": 0, "write": 0, "status": 0,
                  "timeout": 0, "mean": "0/0", "p50": 0, "p90": 0, "p95": 0, "p99": 0, "p999": 0, "max": 0},
        "result": {
            "duration_us": 0, "requests": 0, "bytes": 0,
            "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
            "latency_us": {"mean": 0, "p50": 0, "p90": 0, "p95": 0, "p99": 0, "p999": 0, "max": 0},
            "requests_per_sec": 0,
        },
    },
    {
        # 20000 requests in 10 s is 2000 req/s. The error counters pass through.
        "name": "error counters pass through",
        "stats": {"duration": 10000000, "requests": 20000, "bytes": 1000000, "connect": 1, "read": 2, "write": 3,
                  "status": 12, "timeout": 4, "mean": 1500.25, "p50": 1400, "p90": 2000, "p95": 2100, "p99": 2500,
                  "p999": 3000, "max": 4000},
        "result": {
            "duration_us": 10000000, "requests": 20000, "bytes": 1000000,
            "errors": {"connect": 1, "read": 2, "write": 3, "status": 12, "timeout": 4, "dropped": 0},
            "latency_us": {"mean": 1500.25, "p50": 1400, "p90": 2000, "p95": 2100, "p99": 2500, "p999": 3000,
                           "max": 4000},
            "requests_per_sec": 2000,
        },
    },
]


@unittest.skipUnless(shutil.which("luajit"), "luajit is not installed")
class Wrk2ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.body = self.dir / "body.bin"
        self.body.write_bytes(BODY)

    def luajit(self, source, env):
        harness = self.dir / "harness.lua"
        harness.write_text(source)
        clean = {k: v for k, v in os.environ.items() if not k.startswith("WRK_")}
        proc = subprocess.run(["luajit", str(harness), str(REPORT)], env=dict(clean, **env),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_init_sets_request(self):
        for case in INIT_CASES:
            with self.subTest(name=case["name"]):
                env = {k: str(self.body) if v == "BODY" else v for k, v in case["env"].items()}
                self.assertEqual(self.luajit(INIT_HARNESS, env).splitlines(), case["lines"])

    def test_done_prints_result(self):
        for case in DONE_CASES:
            with self.subTest(name=case["name"]):
                out = self.luajit(DONE_HARNESS.format(**case["stats"]), {})
                self.assertTrue(out.startswith("RESULT "))
                self.assertEqual(json.loads(out[len("RESULT "):]), case["result"])


if __name__ == "__main__":
    unittest.main()
