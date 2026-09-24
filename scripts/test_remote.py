#!/usr/bin/env python3
"""Check proxy evidence collection before benchmark load."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


LIB = Path(__file__).with_name("remote-lib.sh").resolve()


class ProxyEvidenceTests(unittest.TestCase):
    def collect(self, missing=""):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        out = Path(temp.name)
        (out / "cells").mkdir()
        result = subprocess.run(
            ["bash", "-c", r'''
set -eu
. "$LIB"
SERVER_PUB=server
LOADER_PUB=loader
WRK_THREADS=2
WRK_CONNS=8
WRK_TIMEOUT=5s
rssh() {
  local host=$1
  shift
  case "$host:$*" in
  server:cat\ /opt/bench/fleet/nginx/nginx.conf)
    [ "$MISSING" != config ] || return 1
    printf 'rendered nginx configuration\n'
    ;;
  server:*nginx\ -V*)
    [ "$MISSING" != version ] || return 1
    printf 'nginx build and executable hash\n'
    ;;
  loader:*)
    touch "$OUT/warmup-requested"
    return 1
    ;;
  *) return 1 ;;
  esac
}
measure_cell r1-symfony-rapira-pr-nginx-worker http://server:8080/ tag
'''],
            env={**os.environ, "LIB": str(LIB), "OUT": str(out), "MISSING": missing},
            capture_output=True,
            text=True,
        )
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        return out

    def test_proxy_evidence_is_saved_before_load(self):
        out = self.collect()
        prefix = out / "cells" / "r1-symfony-rapira-pr-nginx-worker"
        self.assertTrue(prefix.with_suffix(".nginx.conf").is_file())
        self.assertTrue(prefix.with_suffix(".nginx.txt").is_file())
        self.assertEqual("rendered nginx configuration\n", prefix.with_suffix(".nginx.conf").read_text())
        self.assertEqual("nginx build and executable hash\n", prefix.with_suffix(".nginx.txt").read_text())
        self.assertTrue((out / "warmup-requested").exists())

    def test_missing_proxy_evidence_prevents_load(self):
        for missing in ("config", "version"):
            with self.subTest(missing=missing):
                out = self.collect(missing)
                self.assertFalse((out / "warmup-requested").exists())
                meta = (out / "cells" / "r1-symfony-rapira-pr-nginx-worker.meta").read_text()
                self.assertIn("void=", meta)
                self.assertIn("nginx", meta)


class ProxyDriverTests(unittest.TestCase):
    def run_driver(self, driver, status=0, **settings):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "scripts").mkdir()
        (root / "php/hello").mkdir(parents=True)
        for mode in ("dispatcher", "worker", "classic"):
            (root / f"php/hello/{mode}.php").touch()
        shutil.copyfile(LIB.with_name(driver), root / "scripts" / driver)
        (root / "scripts/report.py").write_text("")
        (root / "scripts/remote-lib.sh").write_text(LIB.read_text() + r'''
bench_init() {
  SERVER_PUB=server SERVER_PRIV=10.0.0.1 LOADER_PUB=loader
  INSTANCE_TYPE=test PROCESSES=2 WRK_CONNS=8 dur_s=1
}
ttl_ensure() { :; }
rssh() {
  printf '%s\n' "$*" >>"$TRACE"
  case "$*" in
  *meta.json*) printf '{"pr_rustflags":""}\n' ;;
  *'h2load --version'*) printf '%s\nk6 v2.2.0\nwrk 4.2.0 [epoll] Copyright (C) 2012 Will Glozer\n' "$H2LOAD_LINE" ;;
  esac
}
measure_cell() { printf 'measure %s\n' "$*" >>"$TRACE"; }
measure_grpc_cell() {
  printf 'measure_grpc_cell' >>"$TRACE"
  printf '\t%s' "$@" >>"$TRACE"
  printf '\n' >>"$TRACE"
}
write_run_meta() { :; }
''')
        result = subprocess.run(
            ["bash", str(root / "scripts" / driver)],
            env={
                **os.environ, "TRACE": str(root / "trace"), "ROUNDS": "2",
                "H2LOAD_LINE": "h2load nghttp2/1.70.0", **settings,
            },
            capture_output=True,
            text=True,
        )
        self.assertEqual(status, result.returncode, result.stdout + result.stderr)
        trace = (root / "trace").read_text().splitlines() if (root / "trace").exists() else []
        expected = list((root / "results").glob("*/cells.expected"))
        plan = expected[0].read_text().splitlines() if expected else []
        return trace, plan

    def test_fleet_proxy_uses_worker_lifecycle_in_rotated_rounds(self):
        trace, plan = self.run_driver("bench-fleet.sh", LEG_LIST="rapira-worker rapira-nginx-worker")
        self.assertEqual([
            "r1-rapira-worker", "r1-rapira-nginx-worker",
            "r2-rapira-nginx-worker", "r2-rapira-worker",
        ], plan)
        for tag in ("r1-rapira-nginx-worker", "r2-rapira-nginx-worker"):
            self.assertIn(f"server bench-rig/scripts/fleet-leg.sh start rapira-nginx 2 {tag} hello", trace)
            self.assertIn(f"server bench-rig/scripts/fleet-leg.sh stop rapira-nginx {tag}", trace)
            self.assertIn(f"measure {tag} http://10.0.0.1:8080/?name=you {tag}", trace)

    def test_framework_proxy_uses_each_application_worker(self):
        trace, plan = self.run_driver("bench-frameworks.sh", SERVERS="rapira-pr-nginx-worker", FRAMEWORKS="symfony laravel")
        self.assertEqual([
            "r1-symfony-rapira-pr-nginx-worker", "r1-laravel-rapira-pr-nginx-worker",
            "r2-laravel-rapira-pr-nginx-worker", "r2-symfony-rapira-pr-nginx-worker",
        ], plan)
        for tag in plan:
            framework = tag.split("-")[1]
            self.assertIn(f"server bench-rig/scripts/fleet-leg.sh start rapira-nginx 2 {tag} /opt/bench/fleet/apps/{framework}/bench/worker-rapira.php", trace)
            self.assertIn(f"server bench-rig/scripts/fleet-leg.sh stop rapira-nginx {tag}", trace)
            self.assertIn(f"measure {tag} http://10.0.0.1:8080/?name=you {tag}", trace)


# h2load 1.70.0 from rapira-bench-loader:local against the gRPC echo ceiling:
# -t2 -c4 -m1 -D 2 with grpc/echo.grpc. Each response is 91 bytes, so data is
# exactly succeeded x 91.
H2LOAD_SAMPLE = """\
starting benchmark...
spawning thread #0: 2 total client(s). Timing-based test with 0s of warm-up time and 2s of main duration for measurements.
spawning thread #1: 2 total client(s). Timing-based test with 0s of warm-up time and 2s of main duration for measurements.
Warm-up started for thread #1.
Warm-up started for thread #0.
progress: 50% of clients started
progress: 100% of clients started
Warm-up phase is over for thread #1.
Main benchmark duration is started for thread #1.
Warm-up phase is over for thread #0.
Main benchmark duration is started for thread #0.
Application protocol: h2c
Main benchmark duration is over for thread #1. Stopping all clients.
Stopped all clients for thread #1
Main benchmark duration is over for thread #0. Stopping all clients.
Stopped all clients for thread #0

finished in 2.00s, 308330.00 req/s, 36.18MB/s
requests: 616660 total, 616664 started, 616660 done, 616660 succeeded, 0 failed, 0 errored, 0 timeout
status codes: 616660 2xx, 0 3xx, 0 4xx, 0 5xx
traffic: 72.36MB (75876095) total, 2.94MB (3083764) headers (space savings 95.57%), 53.52MB (56116060) data
                 min         max         median      p95         p99         mean        sd         +/- sd
request     :        7us       461us        10us        15us        20us        11us         3us    93.62%
connect     :       78us        97us        88us        97us        97us        88us         7us    50.00%
TTFB        :      258us       292us       275us       292us       292us       275us        16us    50.00%
req/s       :   63730.60    86812.79    78887.39    86812.79    86812.79    77079.54    11473.74    75.00%
"""

# Each row edits one number in one h2load file. The other file keeps the clean
# sample.
H2LOAD_CASES = [
    {
        "name": "clean",
        "file": "saturated",
        "text": H2LOAD_SAMPLE,
        "void": None,
    },
    {
        "name": "missing output",
        "file": "saturated",
        "text": "",
        "void": "no h2load output (loader unreachable or h2load failed)",
    },
    {
        "name": "failed",
        "file": "saturated",
        "text": H2LOAD_SAMPLE.replace(" 0 failed,", " 1 failed,"),
        "void": "h2load errors: requests: 616660 total, 616664 started, 616660 done, 616660 succeeded, 1 failed, 0 errored, 0 timeout",
    },
    {
        "name": "errored",
        "file": "lowc",
        "text": H2LOAD_SAMPLE.replace(" 0 errored,", " 2 errored,"),
        "void": "h2load errors: requests: 616660 total, 616664 started, 616660 done, 616660 succeeded, 0 failed, 2 errored, 0 timeout",
    },
    {
        "name": "timeout",
        "file": "saturated",
        "text": H2LOAD_SAMPLE.replace(" 0 timeout", " 3 timeout"),
        "void": "h2load errors: requests: 616660 total, 616664 started, 616660 done, 616660 succeeded, 0 failed, 0 errored, 3 timeout",
    },
    {
        "name": "3xx",
        "file": "lowc",
        "text": H2LOAD_SAMPLE.replace(" 0 3xx,", " 1 3xx,"),
        "void": "non-2xx responses: status codes: 616660 2xx, 1 3xx, 0 4xx, 0 5xx",
    },
    {
        "name": "4xx",
        "file": "saturated",
        "text": H2LOAD_SAMPLE.replace(" 0 4xx,", " 1 4xx,"),
        "void": "non-2xx responses: status codes: 616660 2xx, 0 3xx, 1 4xx, 0 5xx",
    },
    {
        "name": "5xx",
        "file": "lowc",
        "text": H2LOAD_SAMPLE.replace(" 0 5xx", " 1 5xx"),
        "void": "non-2xx responses: status codes: 616660 2xx, 0 3xx, 0 4xx, 1 5xx",
    },
    {
        "name": "short data",
        "file": "saturated",
        "text": H2LOAD_SAMPLE.replace("(56116060) data", "(56115969) data"),
        "void": "short responses: data=56115969 succeeded=616660 expect_len=91",
    },
    {
        "name": "boundary surplus",
        "file": "lowc",
        "text": H2LOAD_SAMPLE.replace("(56116060) data", "(56116151) data"),
        "void": None,
    },
]

# The h2load passes are the warm-up, the saturated pass, the lowc warm-up and
# the lowc pass, from WRK_THREADS=2, GRPC_CONNS=8, dur_s=1 and PROCESSES=2.
LOAD_CASES = [
    {
        "name": "connect-h2c has no open loop",
        "proto": "connect-h2c",
        "wire": "h2c",
        "curl": "curl -sf -m2 --http2-prior-knowledge",
        "h2load": ["-t2 -c8 -m1 -D 5", "-t2 -c8 -m1 -D 1", "-t2 -c2 -m1 -D 2", "-t2 -c2 -m1 -D 10"],
        "k6": None,
        "wrk": None,
    },
    {
        "name": "grpc open loop",
        "proto": "grpc",
        "wire": "h2c",
        "curl": "curl -sf -m2 --http2-prior-knowledge",
        "h2load": ["-t2 -c8 -m1 -D 5", "-t2 -c8 -m1 -D 1", "-t2 -c2 -m1 -D 2", "-t2 -c2 -m1 -D 10"],
        "k6": ["-e PROTO=grpc -e RATE=100", "bench-rig/k6/grpc.js"],
        "wrk": None,
    },
    {
        "name": "http-h1 adds the wrk pass",
        "proto": "http-h1",
        "wire": "h1",
        "curl": "curl -sf -m2",
        "h2load": ["-t2 -c8 -m1 -D 5 --h1", "-t2 -c8 -m1 -D 1 --h1", "-t2 -c2 -m1 -D 2 --h1", "-t2 -c2 -m1 -D 10 --h1"],
        "k6": ["-e PROTO=http-h1", "bench-rig/k6/grpc.js"],
        "wrk": ["-c8"],
    },
]

SERVER_LOG_CASES = [
    {
        "name": "one WARN line",
        "server_log": "2026-09-24T10:00:00Z WARN rapira: worker lost the call\n",
        "void": ["server log: 1 warn or error lines"],
        "file": True,
    },
    {
        "name": "empty grep",
        "server_log": "",
        "void": [],
        "file": False,
    },
]

LOWC_DOUBLED_CASES = [
    {
        "name": "one child holds two connections",
        "conns": "11 1\n12 2\n",
        "lowc_doubled": ["1"],
    },
    {
        "name": "each child holds one connection",
        "conns": "11 1\n12 1\n",
        "lowc_doubled": [],
    },
]

SERVER_READ_CASES = [
    {
        "name": "probe tag set",
        "probe": "r1-x",
        "expect_len": ["91"],
        "pss_kb": ["4096"],
        "leg_sh": ["conns", "mem", "probe"],
        "log_grep": 1,
    },
    {
        "name": "no probe tag",
        "probe": "",
        "expect_len": ["91"],
        "pss_kb": [],
        "leg_sh": [],
        "log_grep": 0,
    },
]

# All rows share one layout, so each row also shows that the files of the other
# workload are not hashed.
RUN_META_LAYOUT = ["php/grpc/a.php", "php/grpc/gen/b.php", "k6/grpc.js", "grpc/c.bin", "php/hello/x.php", "k6/hello.js"]

RUN_META_CASES = [
    {
        "name": "grpc",
        "workload": "grpc",
        "files": ["grpc/c.bin", "k6/grpc.js", "php/grpc/a.php", "php/grpc/gen/b.php"],
    },
    {
        "name": "hello",
        "workload": "hello",
        "files": ["k6/hello.js", "php/hello/x.php"],
    },
]


class GrpcCellTests(unittest.TestCase):
    def measure(self, proto="grpc", wire="h2c", probe="r1-x", probe_ok=True,
                saturated=H2LOAD_SAMPLE, lowc=H2LOAD_SAMPLE, conns="", server_log=""):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "out/cells").mkdir(parents=True)
        (root / "grpc").mkdir()
        (root / "grpc/expect.grpc").write_bytes(b"x" * 91)
        result = subprocess.run(
            ["bash", "-c", r'''
set -euo pipefail
. "$LIB"
SERVER_PUB=server LOADER_PUB=loader WRK_THREADS=2 GRPC_CONNS=8 PROCESSES=2 dur_s=1
WRK_DURATION=1s WRK_TIMEOUT=5s OPEN_RATE=100 K6_VUS=4 WORKLOAD=grpc
sleep() { :; }
rssh() {
  local host=$1
  shift
  printf '%s %s\n' "$host" "$*" >>"$TRACE"
  case "$*" in
  *'| cmp -s -'*) [ "$PROBE_OK" = 1 ] ;;
  *h2load*' -D 10 '*) printf '%s' "$LOWC" ;;
  *h2load*) printf '%s' "$SATURATED" ;;
  *'leg.sh mem'*) echo 4096 ;;
  *'leg.sh conns'*) printf '%s' "$CONNS" ;;
  *'grep -E'*)
    printf '%s' "$SERVER_LOG"
    [ -n "$SERVER_LOG" ]
    ;;
  esac
}
measure_grpc_cell r1-x "$PROTO" "$WIRE" http://server:8080/bench.v1.EchoService/Echo "-H 'a: b'" echo.grpc grpc/expect.grpc "$PROBE" || exit $?
'''],
            cwd=root,
            env={
                **os.environ, "LIB": str(LIB), "OUT": str(root / "out"), "TRACE": str(root / "trace"),
                "PROTO": proto, "WIRE": wire, "PROBE": probe, "PROBE_OK": "1" if probe_ok else "0",
                "SATURATED": saturated, "LOWC": lowc, "CONNS": conns, "SERVER_LOG": server_log,
            },
            capture_output=True,
            text=True,
        )
        cells = root / "out/cells"
        meta = [line.partition("=") for line in (cells / "r1-x.meta").read_text().splitlines()]
        trace = (root / "trace").read_text().splitlines()
        return result, cells, meta, trace

    def values(self, meta, key):
        return [v for k, _, v in meta if k == key]

    def test_probe_failure_voids_before_load(self):
        result, _, meta, trace = self.measure(probe_ok=False)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertEqual(["probe: unexpected response"], self.values(meta, "void"))
        self.assertEqual([], [line for line in trace if "h2load" in line])

    def test_h2load_void_rules(self):
        for row in H2LOAD_CASES:
            texts = {"saturated": H2LOAD_SAMPLE, "lowc": H2LOAD_SAMPLE, row["file"]: row["text"]}
            result, _, meta, _ = self.measure(**texts)
            self.assertEqual(0, result.returncode, row["name"] + result.stdout + result.stderr)
            self.assertEqual([row["void"]] if row["void"] else [], self.values(meta, "void"), row["name"])

    def test_passes_per_protocol(self):
        for row in LOAD_CASES:
            result, _, _, trace = self.measure(proto=row["proto"], wire=row["wire"])
            self.assertEqual(0, result.returncode, row["name"] + result.stdout + result.stderr)
            probe = [line for line in trace if "| cmp -s -" in line]
            self.assertEqual(1, len(probe), row["name"])
            self.assertIn(row["curl"] + " -H 'a: b'", probe[0], row["name"])
            passes = [re.search(r"h2load (-t\S+ -c\S+ -m1 -D \d+(?: --h1)?)", line) for line in trace if "h2load" in line]
            self.assertEqual(row["h2load"], [m and m.group(1) for m in passes], row["name"])
            k6 = [line for line in trace if " k6 run " in line]
            wrk = [line for line in trace if " wrk -t" in line]
            self.assertEqual(0 if row["k6"] is None else 1, len(k6), row["name"])
            self.assertEqual(0 if row["wrk"] is None else 1, len(wrk), row["name"])
            for part in row["k6"] or []:
                self.assertIn(part, k6[0], row["name"])
            for part in row["wrk"] or []:
                self.assertIn(part, wrk[0], row["name"])

    def test_server_log_lines_void(self):
        for row in SERVER_LOG_CASES:
            result, cells, meta, _ = self.measure(server_log=row["server_log"])
            self.assertEqual(0, result.returncode, row["name"] + result.stdout + result.stderr)
            self.assertEqual(row["void"], self.values(meta, "void"), row["name"])
            self.assertEqual(row["file"], (cells / "r1-x.server-log.txt").exists(), row["name"])

    def test_lowc_doubled(self):
        for row in LOWC_DOUBLED_CASES:
            result, _, meta, _ = self.measure(conns=row["conns"])
            self.assertEqual(0, result.returncode, row["name"] + result.stdout + result.stderr)
            self.assertEqual(row["lowc_doubled"], self.values(meta, "lowc_doubled"), row["name"])

    def test_server_reads_follow_the_probe_tag(self):
        for row in SERVER_READ_CASES:
            result, _, meta, trace = self.measure(probe=row["probe"])
            self.assertEqual(0, result.returncode, row["name"] + result.stdout + result.stderr)
            self.assertEqual(row["expect_len"], self.values(meta, "expect_len"), row["name"])
            self.assertEqual(row["pss_kb"], self.values(meta, "pss_kb"), row["name"])
            leg_sh = sorted({m.group(1) for m in (re.search(r"leg\.sh (\w+)", line) for line in trace) if m})
            self.assertEqual(row["leg_sh"], leg_sh, row["name"])
            self.assertEqual(row["log_grep"], len([line for line in trace if "grep -E" in line]), row["name"])

    def test_run_meta_hashes_workload_files(self):
        for row in RUN_META_CASES:
            temp = tempfile.TemporaryDirectory()
            self.addCleanup(temp.cleanup)
            root = Path(temp.name)
            for name in RUN_META_LAYOUT:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(name)
            (root / "out").mkdir()
            (root / "out/server-meta.json").write_text("{}")
            result = subprocess.run(
                ["bash", "-c", '. "$LIB"; write_run_meta'],
                cwd=root,
                env={
                    **os.environ, "LIB": str(LIB), "OUT": "out", "INSTANCE_TYPE": "test", "LOADER_TYPE": "test",
                    "AMI_ID": "ami", "PROCESSES": "2", "WRK_CONNS": "8", "WRK_THREADS": "2", "WRK_DURATION": "1s",
                    "LOWC": "32", "K6_VUS": "4", "WORKLOAD": row["workload"],
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, row["name"] + result.stdout + result.stderr)
            meta = json.loads((root / "out/run-meta.json").read_text())
            self.assertEqual(row["files"], sorted(meta["workload_files"]), row["name"])


GRPC_HDRS = "-H 'content-type: application/grpc' -H 'te: trailers' -H 'grpc-accept-encoding: identity'"
CONNECT_HDRS = "-H 'content-type: application/proto' -H 'connect-protocol-version: 1' -H 'accept-encoding: identity'"
ECHO_URL = "http://10.0.0.1:8080/bench.v1.EchoService/Echo"

# One row per leg, in the default LEG_LIST order. run_driver sets PROCESSES=2
# and SERVER_PRIV=10.0.0.1. measure is the measure_grpc_cell argument list
# after the tag: proto, wire, url, hdrs, body, expect and probe tag.
GRPC_DRIVER_CASES = [
    {
        "name": "rapira HTTP dispatcher reference",
        "leg": "rapira-http-h1",
        "start": "server bench-rig/scripts/leg.sh start pr dispatcher 2 {tag} hello",
        "stop": "server bench-rig/scripts/leg.sh stop {tag} pr",
        "config": "server cat /opt/bench/run/{tag}.toml",
        "measure": ["http-h1", "h1", "http://10.0.0.1:8080/?name=you", "", "", "grpc/expect.http", "{tag}"],
    },
    {
        "name": "rapira gRPC over h2c",
        "leg": "rapira-grpc",
        "start": "server bench-rig/scripts/leg.sh start-grpc pr 2 {tag}",
        "stop": "server bench-rig/scripts/leg.sh stop {tag} pr",
        "config": "server cat /opt/bench/run/{tag}.toml",
        "measure": ["grpc", "h2c", ECHO_URL, GRPC_HDRS, "echo.grpc", "grpc/expect.grpc", "{tag}"],
    },
    {
        "name": "rapira gRPC-Web over HTTP/1.1",
        "leg": "rapira-grpcweb-h1",
        "start": "server bench-rig/scripts/leg.sh start-grpc pr 2 {tag}",
        "stop": "server bench-rig/scripts/leg.sh stop {tag} pr",
        "config": "server cat /opt/bench/run/{tag}.toml",
        "measure": [
            "grpcweb-h1", "h1", ECHO_URL, "-H 'content-type: application/grpc-web+proto' -H 'x-grpc-web: 1'",
            "echo.grpc", "grpc/expect.grpcweb", "{tag}",
        ],
    },
    {
        "name": "rapira Connect proto over HTTP/1.1",
        "leg": "rapira-connect-h1",
        "start": "server bench-rig/scripts/leg.sh start-grpc pr 2 {tag}",
        "stop": "server bench-rig/scripts/leg.sh stop {tag} pr",
        "config": "server cat /opt/bench/run/{tag}.toml",
        "measure": ["connect-h1", "h1", ECHO_URL, CONNECT_HDRS, "echo.bin", "grpc/expect.bin", "{tag}"],
    },
    {
        "name": "rapira Connect proto over h2c",
        "leg": "rapira-connect-h2c",
        "start": "server bench-rig/scripts/leg.sh start-grpc pr 2 {tag}",
        "stop": "server bench-rig/scripts/leg.sh stop {tag} pr",
        "config": "server cat /opt/bench/run/{tag}.toml",
        "measure": ["connect-h2c", "h2c", ECHO_URL, CONNECT_HDRS, "echo.bin", "grpc/expect.bin", "{tag}"],
    },
    {
        "name": "rapira Connect JSON over HTTP/1.1",
        "leg": "rapira-connectjson-h1",
        "start": "server bench-rig/scripts/leg.sh start-grpc pr 2 {tag}",
        "stop": "server bench-rig/scripts/leg.sh stop {tag} pr",
        "config": "server cat /opt/bench/run/{tag}.toml",
        "measure": [
            "connectjson-h1", "h1", ECHO_URL,
            "-H 'content-type: application/json' -H 'connect-protocol-version: 1' -H 'accept-encoding: identity'",
            "echo.json", "grpc/expect.json", "{tag}",
        ],
    },
    {
        "name": "RoadRunner gRPC over h2c",
        "leg": "rr-grpc",
        "start": "server bench-rig/scripts/fleet-leg.sh start rr-grpc 2 {tag}",
        "stop": "server bench-rig/scripts/fleet-leg.sh stop rr-grpc {tag}",
        "config": "server cat /opt/bench/run/{tag}.rr.yaml",
        "measure": ["grpc", "h2c", ECHO_URL, GRPC_HDRS, "echo.grpc", "grpc/expect.grpc", "{tag}"],
    },
    {
        "name": "Rust ceiling gRPC over h2c",
        "leg": "ceiling-grpc",
        "start": "server bench-rig/scripts/leg.sh start-grpc ceiling 2 {tag}",
        "stop": "server bench-rig/scripts/leg.sh stop {tag} ceiling",
        "config": "server cat /opt/bench/run/{tag}.toml",
        "measure": ["grpc", "h2c", ECHO_URL, GRPC_HDRS, "echo.grpc", "grpc/expect.grpc", "{tag}"],
    },
    {
        "name": "Rust ceiling Connect proto over HTTP/1.1",
        "leg": "ceiling-connect-h1",
        "start": "server bench-rig/scripts/leg.sh start-grpc ceiling 2 {tag}",
        "stop": "server bench-rig/scripts/leg.sh stop {tag} ceiling",
        "config": "server cat /opt/bench/run/{tag}.toml",
        "measure": ["connect-h1", "h1", ECHO_URL, CONNECT_HDRS, "echo.bin", "grpc/expect.bin", "{tag}"],
    },
]

LOADER_TOOLS = "loader h2load --version 2>&1 | head -1; k6 version | head -1; wrk --version 2>&1 | head -1"


class GrpcDriverTests(unittest.TestCase):
    run_driver = ProxyDriverTests.run_driver

    def test_rotated_plan(self):
        trace, plan = self.run_driver("bench-grpc.sh", LEG_LIST="rapira-grpc rr-grpc ceiling-connect-h1")
        self.assertEqual([
            "r1-rapira-grpc", "r1-rr-grpc", "r1-ceiling-connect-h1",
            "r2-rr-grpc", "r2-ceiling-connect-h1", "r2-rapira-grpc",
        ], plan)
        self.assertEqual(plan, [line.split("\t")[1] for line in trace if line.startswith("measure_grpc_cell\t")])

    def test_default_legs_start_measure_and_stop(self):
        trace, plan = self.run_driver("bench-grpc.sh")
        self.assertEqual([f"r1-{row['leg']}" for row in GRPC_DRIVER_CASES], plan[:len(GRPC_DRIVER_CASES)])
        self.assertEqual(2 * len(GRPC_DRIVER_CASES), len(plan))
        for row in GRPC_DRIVER_CASES:
            tags = [tag for tag in plan if tag.split("-", 1)[1] == row["leg"]]
            self.assertEqual([f"r1-{row['leg']}", f"r2-{row['leg']}"], tags, row["name"])
            for tag in tags:
                lines = [
                    row["start"].format(tag=tag),
                    row["config"].format(tag=tag),
                    "\t".join(["measure_grpc_cell", tag, *(arg.format(tag=tag) for arg in row["measure"])]),
                    row["stop"].format(tag=tag),
                ]
                for line in lines:
                    self.assertIn(line, trace, row["name"])
                positions = [trace.index(line) for line in lines]
                self.assertEqual(sorted(positions), positions, row["name"])

    def test_unknown_leg_exits_before_the_rig(self):
        trace, plan = self.run_driver("bench-grpc.sh", status=1, LEG_LIST="rapira-grpc rr-connect-h1")
        self.assertEqual([], trace)
        self.assertEqual([], plan)

    def test_other_h2load_version_exits_before_the_legs(self):
        trace, plan = self.run_driver("bench-grpc.sh", status=1, H2LOAD_LINE="h2load nghttp2/1.68.0")
        self.assertIn(LOADER_TOOLS, trace)
        self.assertEqual([], [line for line in trace if " start" in line])
        self.assertEqual([], plan)


if __name__ == "__main__":
    unittest.main()
