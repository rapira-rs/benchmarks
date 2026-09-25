"""Tests of publishing a run file into a gh-pages checkout."""

import json
import tempfile
import unittest
from pathlib import Path

from rig.publish import publish


def run_doc(run_id, started, status="complete", pr=None):
    return {
        "schema": "rapira-bench-run/2",
        "id": run_id,
        "suite": {"name": "ci", "sha256": "ab" * 32},
        "smoke": False,
        "started": started,
        "rapira": {"ref": "nightly", "sha": "0a1b2c3d4e5f", "version": "0.9.0", "build": "nightly", "pr": pr},
        "cells": [],
        "status": status,
        "reasons": [],
    }


def entry(run_id, started, status="complete", pr=None):
    """The index entry of `run_doc(run_id, started, status, pr)`."""
    return {
        "id": run_id,
        "started": started,
        "suite": "ci",
        "rapira_sha": "0a1b2c3d4e5f",
        "rapira_version": "0.9.0",
        "status": status,
        "smoke": False,
        "pr": pr,
    }


PR = {"number": 59, "url": "https://github.com/rapira-rs/rapira/pull/59", "title": "Faster hello"}
RUN = run_doc("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z", pr=PR)

CASES = [
    {
        "name": "no index yet",
        "index": None,
        "run": RUN,
        "runs": [entry("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z", pr=59)],
    },
    {
        "name": "new run sorts by start time",
        "index": {"schema": "rapira-bench-index/1", "runs": [
            entry("20260924T120000Z-ci-1111111", "2026-09-24T12:00:00Z"),
            entry("20260926T120000Z-ci-2222222", "2026-09-26T12:00:00Z"),
        ]},
        "run": RUN,
        "runs": [
            entry("20260924T120000Z-ci-1111111", "2026-09-24T12:00:00Z"),
            entry("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z", pr=59),
            entry("20260926T120000Z-ci-2222222", "2026-09-26T12:00:00Z"),
        ],
    },
    {
        "name": "same id replaces the entry",
        "index": {"schema": "rapira-bench-index/1", "runs": [
            entry("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z", "incomplete"),
        ]},
        "run": RUN,
        "runs": [entry("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z", pr=59)],
    },
    {
        "name": "a run without a pull request has a null pr",
        "index": None,
        "run": run_doc("20260925T130000Z-ci-0a1b2c3", "2026-09-25T13:00:00Z"),
        "runs": [entry("20260925T130000Z-ci-0a1b2c3", "2026-09-25T13:00:00Z")],
    },
]


REFUSAL_CASES = [
    {"name": "incomplete run", "status": "incomplete"},
]


class TestPublish(unittest.TestCase):
    def test_refuses_a_run_that_is_not_complete(self):
        for case in REFUSAL_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                pages = Path(tmp)
                run = run_doc("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z", case["status"])
                with self.assertRaises(ValueError):
                    publish(run, pages)
                self.assertFalse((pages / "data").exists())

    def test_publish(self):
        for case in CASES:
            with self.subTest(name=case["name"]):
                with tempfile.TemporaryDirectory() as tmp:
                    pages = Path(tmp)
                    if case["index"] is not None:
                        (pages / "data").mkdir()
                        (pages / "data" / "index.json").write_text(json.dumps(case["index"]))
                    path = publish(case["run"], pages)
                    self.assertEqual(path, pages / "data" / f"{case['run']['id']}.json")
                    self.assertEqual(json.loads(path.read_text()), case["run"])
                    index = json.loads((pages / "data" / "index.json").read_text())
                self.assertEqual(index, {"schema": "rapira-bench-index/1", "runs": case["runs"]})


if __name__ == "__main__":
    unittest.main()
