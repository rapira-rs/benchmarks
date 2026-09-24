#!/usr/bin/env python3
"""Regression tests for report publication status."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPORT = Path(__file__).with_name("report.py")
LOWC = """Latency 1.00ms 0.10ms 2.00ms
  50% 0.50ms
  99% 2.00ms
Requests/sec: 50.00
"""

# A real h2load 1.70.0 run against a gRPC Echo server: 616660 succeeded x 91
# bytes of expected reply = 56116060 data bytes.
H2LOAD = """\
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
# The lowc pass has a slower request row, so the tables show which file they read.
LOWC_H2LOAD = H2LOAD.replace(
    "request     :        7us       461us        10us        15us        20us        11us         3us    93.62%",
    "request     :       41us      2.02ms       120us       480us      1.25ms       150us        60us    92.00%",
)
# h2load 1.68 prints "time for request:" and has no median column.
H2LOAD_1_68 = H2LOAD[: H2LOAD.index("                 min")] + """\
                     min         max         mean         sd        +/- sd
time for request:        7us       461us        11us         3us    93.62%
time for connect:       78us        97us        88us         7us    50.00%
time to 1st byte:      258us       292us       275us        16us    50.00%
req/s           :   63730.60    86812.79    77079.54    11473.74    75.00%
"""


def k6_metrics(proto, *, rate=19998.0, med=0.30, p99=1.20, p999=4.00, dropped=0, fails=0):
    """The --summary-export metrics of k6/grpc.js for one protocol, 15 s run."""
    count = int(rate * 15)
    passes = 2 * count - fails
    trend = {"avg": 0.40, "min": 0.10, "med": med, "max": 12.00, "p(90)": 0.60, "p(95)": 0.80, "p(99)": p99, "p(99.9)": p999}
    metrics = {
        "iterations": {"count": count, "rate": rate},
        "dropped_iterations": {"count": dropped, "rate": dropped / 15},
        "checks": {"value": passes / (passes + fails), "passes": passes, "fails": fails},
    }
    if proto == "grpc":
        metrics["grpc_req_duration"] = trend
    else:
        metrics["http_req_duration"] = trend
        metrics["http_reqs"] = {"count": count, "rate": rate}
        metrics["http_req_failed"] = {"value": 0, "passes": 0, "fails": count}
    return metrics


def table_rows(stdout, title):
    """The split rows of the table whose header starts with title, or None."""
    for block in stdout.split("\n\n"):
        lines = block.splitlines()
        if lines and lines[0].startswith(title):
            return [line.split() for line in lines[2:]]
    return None


def issue_lines(stdout):
    return [line.strip() for line in stdout.splitlines() if line.startswith("  cell: ")]


def status_lines(stdout):
    return [line for line in stdout.splitlines() if line.startswith(("INCOMPLETE RUN", "BROKEN RUN"))]


# Each row edits one number in one h2load file of a single proto grpc cell.
H2LOAD_ERROR_CASES = [
    {
        "name": "failed",
        "artifact": "h2load",
        "text": H2LOAD.replace(" 0 failed,", " 1 failed,"),
        "rc": 1,
        "issues": ["cell: h2load request errors"],
        "legs": False,
        "lowc": True,
    },
    {
        "name": "errored",
        "artifact": "h2load",
        "text": H2LOAD.replace(" 0 errored,", " 2 errored,"),
        "rc": 1,
        "issues": ["cell: h2load request errors"],
        "legs": False,
        "lowc": True,
    },
    {
        "name": "timeout",
        "artifact": "h2load",
        "text": H2LOAD.replace(" 0 timeout", " 3 timeout"),
        "rc": 1,
        "issues": ["cell: h2load request errors"],
        "legs": False,
        "lowc": True,
    },
    {
        "name": "3xx",
        "artifact": "h2load",
        "text": H2LOAD.replace(" 0 3xx,", " 1 3xx,"),
        "rc": 1,
        "issues": ["cell: h2load request errors"],
        "legs": False,
        "lowc": True,
    },
    {
        "name": "4xx",
        "artifact": "h2load",
        "text": H2LOAD.replace(" 0 4xx,", " 1 4xx,"),
        "rc": 1,
        "issues": ["cell: h2load request errors"],
        "legs": False,
        "lowc": True,
    },
    {
        "name": "5xx",
        "artifact": "h2load",
        "text": H2LOAD.replace(" 0 5xx", " 1 5xx"),
        "rc": 1,
        "issues": ["cell: h2load request errors"],
        "legs": False,
        "lowc": True,
    },
    {
        "name": "short responses",
        "artifact": "h2load",
        "text": H2LOAD.replace("(56116060) data", "(56115969) data"),
        "rc": 1,
        "issues": ["cell: short responses"],
        "legs": False,
        "lowc": True,
    },
    {
        "name": "lowc failed",
        "artifact": "lowc_h2load",
        "text": LOWC_H2LOAD.replace(" 0 failed,", " 1 failed,"),
        "rc": 1,
        "issues": ["cell: lowc_h2load request errors"],
        "legs": True,
        "lowc": False,
    },
    {
        "name": "boundary surplus",
        "artifact": "h2load",
        "text": H2LOAD.replace("(56116060) data", "(56116151) data"),
        "rc": 0,
        "issues": [],
        "legs": True,
        "lowc": True,
    },
]

OPEN_LOOP_CASES = [
    {
        "name": "failed checks",
        "k6": k6_metrics("grpc", fails=2),
        "issues": [],
        "status": ["BROKEN RUN: a mode lost every cell of one ref, or k6 checks failed."],
        "open_loop": [["rapira-grpc", "19998", "0.30ms", "1.20ms", "4.00ms", "2", "1"]],
    },
    {
        "name": "dropped iterations",
        "k6": k6_metrics("grpc", dropped=5),
        "issues": ["cell: k6 dropped iterations"],
        "status": ["INCOMPLETE RUN: 1 cells have missing or invalid artifacts."],
        "open_loop": None,
    },
]

ARTIFACT_CASES = [
    {
        "name": "h2load",
        "proto": "grpc",
        "omit": ("wrk", "h2load"),
        "rc": 1,
        "issues": ["cell: missing h2load output"],
    },
    {
        "name": "lowc_h2load",
        "proto": "grpc",
        "omit": ("wrk", "lowc_h2load"),
        "rc": 1,
        "issues": ["cell: missing lowc_h2load output"],
    },
    {
        "name": "k6 for grpc",
        "proto": "grpc",
        "omit": ("wrk", "k6"),
        "rc": 1,
        "issues": ["cell: missing k6 output"],
    },
    {
        "name": "wrk for http-h1",
        "proto": "http-h1",
        "omit": ("wrk",),
        "rc": 1,
        "issues": ["cell: missing wrk output"],
    },
    {
        "name": "no k6 for connect-h2c",
        "proto": "connect-h2c",
        "omit": ("wrk", "k6"),
        "rc": 0,
        "issues": [],
    },
]


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run_dir = self.new_run("run")

    def new_run(self, name):
        run = Path(self.temp.name) / name
        (run / "cells").mkdir(parents=True)
        return run

    def write_expected(self, *tags, run=None):
        run = run or self.run_dir
        text = "\n".join(tags)
        if tags:
            text += "\n"
        (run / "cells.expected").write_text(text)

    def write_cell(
        self,
        tag="cell",
        *,
        run=None,
        leg="valid",
        rate=100,
        void=None,
        checks=0,
        include_checks=True,
    ):
        run = run or self.run_dir
        meta = f"leg={leg}\n"
        if void is not None:
            meta += f"void={void}\n"
        (run / "cells" / f"{tag}.meta").write_text(meta)

        paths = {
            "wrk": run / "cells" / f"{tag}.wrk.txt",
            "lowc": run / "cells" / f"{tag}.lowc.wrk.txt",
            "k6": run / "cells" / f"{tag}.k6.summary.json",
        }
        paths["wrk"].write_text(f"Requests/sec: {rate:.2f}\n")
        paths["lowc"].write_text(LOWC)
        metrics = {
            "http_reqs": {"rate": 40},
            "http_req_duration": {"avg": 3, "p(95)": 5},
            "http_req_failed": {"value": 0},
        }
        if include_checks:
            metrics["checks"] = {"fails": checks}
        paths["k6"].write_text(json.dumps({"metrics": metrics}))
        return paths

    def write_grpc_cell(
        self,
        tag="cell",
        *,
        run=None,
        leg="rapira-grpc",
        proto="grpc",
        h2load=H2LOAD,
        lowc_h2load=LOWC_H2LOAD,
        k6=None,
        wrk_rate=100,
        pss_kb=None,
        expect_len=91,
        omit=(),
    ):
        run = run or self.run_dir
        meta = f"leg={leg}\nproto={proto}\nconns=16\nexpect_len={expect_len}\n"
        if pss_kb is not None:
            meta += f"pss_kb={pss_kb}\n"
        (run / "cells" / f"{tag}.meta").write_text(meta)

        texts = {
            "h2load": h2load,
            "lowc_h2load": lowc_h2load,
            "k6": json.dumps({"metrics": k6 or k6_metrics(proto)}),
            "wrk": f"Requests/sec: {wrk_rate:.2f}\n",
        }
        paths = {
            "h2load": run / "cells" / f"{tag}.h2load.txt",
            "lowc_h2load": run / "cells" / f"{tag}.lowc.h2load.txt",
            "k6": run / "cells" / f"{tag}.k6.summary.json",
            "wrk": run / "cells" / f"{tag}.wrk.txt",
        }
        for artifact, path in paths.items():
            if artifact not in omit:
                path.write_text(texts[artifact])

    def report(self, run=None):
        return subprocess.run(
            [sys.executable, str(REPORT), str(run or self.run_dir)],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_complete_fleet_report_keeps_numeric_output(self):
        self.write_expected("cell")
        self.write_cell()

        result = self.report()

        self.assertEqual(0, result.returncode, result.stdout)
        rows = [line.split() for line in result.stdout.splitlines() if line.startswith("valid")]
        self.assertEqual(
            [
                ["valid", "100", "1", "0.0%", "-"],
                ["valid", "0.50ms", "2.00ms", "1.00ms", "1"],
                ["valid", "40", "3.00ms", "5.00ms", "0.00%", "0", "1"],
            ],
            rows,
        )

    def test_complete_static_hit_without_checks_metric_passes(self):
        self.write_expected("cell")
        self.write_cell(leg="rapira-static-hit", include_checks=False)

        result = self.report()

        self.assertEqual(0, result.returncode, result.stdout)
        self.assertNotIn("Do not publish", result.stdout)

    def test_missing_plan_is_incomplete(self):
        self.write_cell()

        result = self.report()

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("cells.expected", result.stdout)
        self.assertIn("Do not publish these tables.", result.stdout)

    def test_empty_plan_is_incomplete(self):
        self.write_expected()
        self.write_cell()

        result = self.report()

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("cells.expected", result.stdout)
        self.assertIn("Do not publish these tables.", result.stdout)

    def test_expected_cell_without_meta_is_incomplete(self):
        self.write_expected("missing")

        result = self.report()

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("missing", result.stdout)
        self.assertIn("Do not publish these tables.", result.stdout)

    def test_cell_without_identity_is_excluded_and_incomplete(self):
        for meta in ("", "leg=\n", "ref=pr\n", "mode=worker\n", "ref=\nmode=worker\n", "ref=pr\nmode=\n"):
            with self.subTest(meta=meta):
                self.write_expected("cell")
                self.write_cell(rate=987654)
                (self.run_dir / "cells/cell.meta").write_text(meta)

                result = self.report()

                self.assertEqual(1, result.returncode, result.stdout)
                self.assertIn("cell: missing cell identity", result.stdout)
                self.assertNotIn("987654", result.stdout)
                self.assertNotIn("lowc latency", result.stdout)
                self.assertIn("Do not publish these tables.", result.stdout)

    def test_ref_and_mode_identify_complete_comparison_cells(self):
        self.write_expected("base-cell", "pr-cell")
        for ref in ("base", "pr"):
            self.write_cell(f"{ref}-cell")
            (self.run_dir / "cells" / f"{ref}-cell.meta").write_text(f"ref={ref}\nmode=worker\n")

        result = self.report()

        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn("worker", result.stdout)
        self.assertIn("base req/s", result.stdout)

    def test_missing_required_artifact_is_incomplete(self):
        for artifact in ("wrk", "lowc", "k6"):
            with self.subTest(artifact=artifact):
                run = self.new_run(f"missing-{artifact}")
                self.write_expected("cell", run=run)
                paths = self.write_cell(run=run)
                paths[artifact].unlink()

                result = self.report(run)

                self.assertEqual(1, result.returncode, result.stdout)
                self.assertIn(artifact, result.stdout)
                self.assertIn("Do not publish these tables.", result.stdout)

    def test_unparseable_required_artifact_is_incomplete(self):
        for artifact in ("wrk", "lowc", "k6"):
            with self.subTest(artifact=artifact):
                run = self.new_run(f"unparseable-{artifact}")
                self.write_expected("cell", run=run)
                paths = self.write_cell(run=run)
                paths[artifact].write_text("not generator output\n")

                result = self.report(run)

                self.assertEqual(1, result.returncode, result.stdout)
                self.assertIn(artifact, result.stdout)
                self.assertIn("Do not publish these tables.", result.stdout)

    def test_k6_without_http_reqs_is_incomplete(self):
        self.write_expected("cell")
        paths = self.write_cell()
        paths["k6"].write_text(json.dumps({"metrics": {"http_req_failed": {"value": 0}}}))

        result = self.report()

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("unparseable k6 output", result.stdout)
        self.assertIn("Do not publish these tables.", result.stdout)

    def test_voided_cell_does_not_change_surviving_measurement(self):
        self.write_expected("valid-cell", "void-cell")
        self.write_cell("valid-cell", leg="shared", rate=100)
        paths = self.write_cell("void-cell", leg="shared", rate=900, void="start failed")
        paths["lowc"].unlink()
        paths["k6"].unlink()

        result = self.report()

        self.assertEqual(1, result.returncode, result.stdout)
        rows = [line.split() for line in result.stdout.splitlines() if line.startswith("shared")]
        self.assertEqual(["shared", "100", "1", "0.0%", "-"], rows[0])
        self.assertNotIn("900", result.stdout)
        self.assertIn("void-cell: start failed", result.stdout)
        self.assertNotIn("void-cell: missing", result.stdout)
        self.assertIn("Do not publish these tables.", result.stdout)

    def test_run_with_only_a_voided_cell_is_incomplete(self):
        self.write_expected("void-cell")
        paths = self.write_cell("void-cell", void="start failed")
        for path in paths.values():
            path.unlink()

        result = self.report()

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("void-cell: start failed", result.stdout)
        self.assertNotIn("void-cell: missing", result.stdout)
        self.assertIn("Do not publish these tables.", result.stdout)

    def test_failed_k6_checks_keep_run_broken(self):
        self.write_expected("cell")
        self.write_cell(checks=1)

        result = self.report()

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("BROKEN RUN", result.stdout)
        self.assertIn("Do not publish these tables.", result.stdout)

    def test_wrk_request_errors_prevent_publication(self):
        for artifact in ("wrk", "lowc"):
            for error in ("Non-2xx or 3xx responses: 1", "Socket errors: connect 0, read 1, write 0, timeout 0"):
                with self.subTest(artifact=artifact, error=error):
                    self.write_expected("cell")
                    paths = self.write_cell()
                    paths[artifact].write_text(paths[artifact].read_text() + error + "\n")

                    result = self.report()

                    self.assertEqual(1, result.returncode, result.stdout)
                    self.assertIn("Do not publish these tables.", result.stdout)

    def test_k6_http_failures_prevent_publication_without_checks(self):
        self.write_expected("cell")
        paths = self.write_cell(leg="rapira-static-hit", include_checks=False)
        data = json.loads(paths["k6"].read_text())
        data["metrics"]["http_req_failed"]["value"] = 0.25
        paths["k6"].write_text(json.dumps(data))

        result = self.report()

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Do not publish these tables.", result.stdout)

    def test_proxy_report_requires_configuration_and_build_evidence(self):
        for missing in (None, "nginx.conf", "nginx.txt"):
            for empty in (False, True):
                with self.subTest(artifact=missing, empty=empty):
                    self.write_expected("cell")
                    self.write_cell(leg="rapira-nginx-worker")
                    for artifact in ("nginx.conf", "nginx.txt"):
                        path = self.run_dir / "cells" / f"cell.{artifact}"
                        path.write_text("proxy evidence\n")
                        if artifact == missing:
                            if empty:
                                path.write_text("")
                            else:
                                path.unlink()

                    result = self.report()

                    self.assertEqual(0 if missing is None else 1, result.returncode, result.stdout)
                    if missing is not None:
                        self.assertIn(missing, result.stdout)
                        self.assertIn("Do not publish these tables.", result.stdout)

    def test_complete_grpc_run_renders_three_tables(self):
        self.write_expected("r1-rapira-grpc", "r2-rapira-grpc", "r1-rapira-connect-h2c", "r1-rapira-http-h1")
        self.write_grpc_cell(
            "r1-rapira-grpc",
            pss_kb=4096,
            k6=k6_metrics("grpc", rate=19998.0, med=0.30, p99=1.20, p999=4.00),
            omit=("wrk",),
        )
        self.write_grpc_cell(
            "r2-rapira-grpc",
            pss_kb=6144,
            h2load=H2LOAD.replace("308330.00 req/s", "290000.00 req/s"),
            k6=k6_metrics("grpc", rate=19990.0, med=0.40, p99=1.40, p999=6.00),
            omit=("wrk",),
        )
        self.write_grpc_cell(
            "r1-rapira-connect-h2c",
            leg="rapira-connect-h2c",
            proto="connect-h2c",
            h2load=H2LOAD.replace("308330.00 req/s", "250000.00 req/s"),
            omit=("wrk", "k6"),
        )
        self.write_grpc_cell(
            "r1-rapira-http-h1",
            leg="rapira-http-h1",
            proto="http-h1",
            pss_kb=2048,
            h2load=H2LOAD.replace("308330.00 req/s", "120000.00 req/s"),
            wrk_rate=180000,
            k6=k6_metrics("http-h1", rate=20000.0, med=0.25, p99=0.90, p999=2.50),
        )

        result = self.report()

        self.assertEqual(0, result.returncode, result.stdout)
        # rapira-grpc: median(308330, 290000) = 299165, spread 18330 / 299165
        # = 6.1%, Pss median(4096, 6144) kB / 1024 = 5.0 MiB.
        self.assertEqual(
            [
                ["rapira-grpc", "grpc", "299165", "2", "6.1%", "5.0", "-"],
                ["rapira-connect-h2c", "connect-h2c", "250000", "1", "0.0%", "n/a", "-"],
                ["rapira-http-h1", "(wrk)", "http-h1", "180000", "1", "0.0%", "2.0", "-"],
                ["rapira-http-h1", "http-h1", "120000", "1", "0.0%", "2.0", "-"],
            ],
            table_rows(result.stdout, "grpc legs"),
        )
        # The lowc request row: median 120us, p99 1.25ms, mean 150us.
        self.assertEqual(
            [
                ["rapira-connect-h2c", "0.12ms", "1.25ms", "0.15ms", "1"],
                ["rapira-grpc", "0.12ms", "1.25ms", "0.15ms", "2"],
                ["rapira-http-h1", "0.12ms", "1.25ms", "0.15ms", "1"],
            ],
            table_rows(result.stdout, "grpc lowc latency (c=processes)"),
        )
        # rapira-grpc: median(19998, 19990) = 19994, med median(0.30, 0.40)
        # = 0.35, p99 median(1.20, 1.40) = 1.30, p99.9 median(4, 6) = 5.
        self.assertEqual(
            [
                ["rapira-grpc", "19994", "0.35ms", "1.30ms", "5.00ms", "0", "2"],
                ["rapira-http-h1", "20000", "0.25ms", "0.90ms", "2.50ms", "0", "1"],
            ],
            table_rows(result.stdout, "grpc open loop"),
        )
        self.assertNotIn("lowc latency (c=low)", result.stdout)
        self.assertNotIn("k6 probe", result.stdout)
        self.assertIsNone(table_rows(result.stdout, "leg "))

    def test_h2load_1_68_format_is_incomplete(self):
        self.write_expected("cell")
        self.write_grpc_cell(h2load=H2LOAD_1_68, omit=("wrk",))

        result = self.report()

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertEqual(["cell: unparseable h2load output"], issue_lines(result.stdout))
        self.assertEqual(["INCOMPLETE RUN: 1 cells have missing or invalid artifacts."], status_lines(result.stdout))

    def test_h2load_error_classes(self):
        for row in H2LOAD_ERROR_CASES:
            run = self.new_run(row["name"])
            self.write_expected("cell", run=run)
            self.write_grpc_cell(run=run, omit=("wrk",), **{row["artifact"]: row["text"]})

            result = self.report(run)

            self.assertEqual(row["rc"], result.returncode, row["name"] + result.stdout)
            self.assertEqual(row["issues"], issue_lines(result.stdout), row["name"])
            self.assertEqual(row["legs"], "grpc legs" in result.stdout, row["name"])
            self.assertEqual(row["lowc"], "grpc lowc latency" in result.stdout, row["name"])

    def test_open_loop_failures(self):
        for row in OPEN_LOOP_CASES:
            run = self.new_run(row["name"])
            self.write_expected("cell", run=run)
            self.write_grpc_cell(run=run, k6=row["k6"], omit=("wrk",))

            result = self.report(run)

            self.assertEqual(1, result.returncode, row["name"] + result.stdout)
            self.assertEqual(row["issues"], issue_lines(result.stdout), row["name"])
            self.assertEqual(row["status"], status_lines(result.stdout), row["name"])
            self.assertEqual(row["open_loop"], table_rows(result.stdout, "grpc open loop"), row["name"])

    def test_grpc_artifacts_per_proto(self):
        for row in ARTIFACT_CASES:
            run = self.new_run(row["name"])
            self.write_expected("cell", run=run)
            self.write_grpc_cell(run=run, leg=f"rapira-{row['proto']}", proto=row["proto"], omit=row["omit"])

            result = self.report(run)

            self.assertEqual(row["rc"], result.returncode, row["name"] + result.stdout)
            self.assertEqual(row["issues"], issue_lines(result.stdout), row["name"])

    def test_http_cell_next_to_grpc_cell_keeps_its_tables(self):
        http_only = self.new_run("http-only")
        self.write_expected("cell", run=http_only)
        self.write_cell(run=http_only)
        self.write_expected("cell", "grpc-cell")
        self.write_cell()
        self.write_grpc_cell("grpc-cell", omit=("wrk",))

        expected = self.report(http_only)
        result = self.report()

        self.assertEqual(0, expected.returncode, expected.stdout)
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertTrue(result.stdout.startswith(expected.stdout), result.stdout)
        self.assertEqual(
            [["rapira-grpc", "grpc", "308330", "1", "0.0%", "n/a", "-"]],
            table_rows(result.stdout, "grpc legs"),
        )
        self.assertEqual(
            [["rapira-grpc", "0.12ms", "1.25ms", "0.15ms", "1"]],
            table_rows(result.stdout, "grpc lowc latency (c=processes)"),
        )
        self.assertEqual(
            [["rapira-grpc", "19998", "0.30ms", "1.20ms", "4.00ms", "0", "1"]],
            table_rows(result.stdout, "grpc open loop"),
        )


if __name__ == "__main__":
    unittest.main()
