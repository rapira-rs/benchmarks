#!/usr/bin/env python3
"""Check proxy evidence collection before benchmark load."""

import os
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
    def run_driver(self, driver, **settings):
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
  esac
}
measure_cell() { printf 'measure %s\n' "$*" >>"$TRACE"; }
write_run_meta() { :; }
''')
        result = subprocess.run(
            ["bash", str(root / "scripts" / driver)],
            env={**os.environ, "TRACE": str(root / "trace"), "ROUNDS": "2", **settings},
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        trace = (root / "trace").read_text().splitlines()
        plan = next((root / "results").glob("*/cells.expected")).read_text().splitlines()
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


if __name__ == "__main__":
    unittest.main()
