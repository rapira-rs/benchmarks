"""Tests of publishing a run file into a gh-pages checkout."""

import json
import tempfile
import unittest
from pathlib import Path

from rig.publish import publish


def run_doc(run_id, started, status="complete"):
    return {
        "schema": "rapira-bench-run/1",
        "id": run_id,
        "suite": {"name": "ci", "sha256": "ab" * 32},
        "smoke": False,
        "started": started,
        "rapira": {"ref": "main", "sha": "0a1b2c3d4e5f", "version": "0.9.0", "build": "nightly"},
        "cells": [],
        "status": status,
        "reasons": [],
    }


def entry(run_id, started, status="complete"):
    """The index entry of `run_doc(run_id, started, status)`."""
    return {
        "id": run_id,
        "started": started,
        "suite": "ci",
        "rapira_sha": "0a1b2c3d4e5f",
        "rapira_version": "0.9.0",
        "status": status,
        "smoke": False,
    }


RUN = run_doc("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z")

CASES = [
    {
        "name": "no index yet",
        "index": None,
        "runs": [entry("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z")],
    },
    {
        "name": "new run sorts by start time",
        "index": {"schema": "rapira-bench-index/1", "runs": [
            entry("20260924T120000Z-ci-1111111", "2026-09-24T12:00:00Z"),
            entry("20260926T120000Z-ci-2222222", "2026-09-26T12:00:00Z"),
        ]},
        "runs": [
            entry("20260924T120000Z-ci-1111111", "2026-09-24T12:00:00Z"),
            entry("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z"),
            entry("20260926T120000Z-ci-2222222", "2026-09-26T12:00:00Z"),
        ],
    },
    {
        "name": "same id replaces the entry",
        "index": {"schema": "rapira-bench-index/1", "runs": [
            entry("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z", "incomplete"),
        ]},
        "runs": [entry("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z")],
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
                    path = publish(RUN, pages)
                    self.assertEqual(path, pages / "data" / "20260925T120000Z-ci-0a1b2c3.json")
                    self.assertEqual(json.loads(path.read_text()), RUN)
                    index = json.loads((pages / "data" / "index.json").read_text())
                self.assertEqual(index, {"schema": "rapira-bench-index/1", "runs": case["runs"]})


if __name__ == "__main__":
    unittest.main()
