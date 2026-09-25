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

# The fake tool writes its arguments, the request environment, and the soft descriptor limit to
# FAKE_LOG, writes FAKE_LOG_ROWS to the file after --log-file when that option is present, prints
# the file FAKE_OUTPUT, and exits with FAKE_EXIT.
FAKE_TOOL = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, resource, sys
    names = ("WRK_METHOD", "WRK_BODY_FILE", "WRK_HEADERS")
    with open(os.environ["FAKE_LOG"], "w") as f:
        json.dump({"argv": sys.argv[1:], "env": {n: os.environ.get(n) for n in names},
                   "nofile": resource.getrlimit(resource.RLIMIT_NOFILE)[0]}, f)
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
                # 5000 connections need 5000 descriptors and more; the script raises the soft limit to 65536 before the tool runs.
                self.assertGreaterEqual(fake["nofile"], 5000)

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
        # A fixed sample, independent of REPORTER_RESULT (a 70s stage fixture for LOAD_CASES).
        "name": "contract sample",
        "stats": {"duration": 20000867, "requests": 39989, "bytes": 5038614, "connect": 0, "read": 0, "write": 0,
                  "status": 0, "timeout": 0, "mean": 689.5, "p50": 689, "p90": 1111, "p95": 1175, "p99": 1264,
                  "p999": 1351, "max": 2822},
        "result": {
            "duration_us": 20000867,
            "requests": 39989,
            "bytes": 5038614,
            "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
            "latency_us": {"mean": 689.5, "p50": 689, "p90": 1111, "p95": 1175, "p99": 1264, "p999": 1351,
                           "max": 2822},
            "requests_per_sec": 1999.363,
        },
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
