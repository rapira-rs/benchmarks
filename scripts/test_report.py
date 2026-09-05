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


if __name__ == "__main__":
    unittest.main()
