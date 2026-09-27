"""Tests of the base build selection: python3 -m rig base."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from rig.__main__ import main

NEW_SHA = "e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4"
NEW = NEW_SHA[:7]
NO_BASE = "ERROR: no base build: the index has no other non-smoke run; pass the base input\n"


def entry(sha, started, *, smoke=False, schema="rapira-bench-run/3"):
    """An index entry. schema None gives an entry of an older run file, which has no schema key."""
    doc = {
        "id": f"{started}-ci-{sha[:7]}",
        "started": started,
        "suite": "ci",
        "rapira_sha": sha,
        "rapira_version": f"0.8.1-nightly.{sha[:7]}",
        "status": "complete",
        "smoke": smoke,
        "pr": None,
    }
    if schema is not None:
        doc["schema"] = schema
    return doc


BASE_CASES = [
    {
        # The base input wins over the index, also when it names the new build (an A/A run).
        "name": "an explicit base is printed as it is",
        "runs": [entry("ac56141af8c1dadf83cd750b7f522b06155d198d", "2026-09-26T21:08:13Z")],
        "base": NEW,
        "status": 0,
        "stdout": f"{NEW}\n",
        "stderr": "",
    },
    {
        # The entries are out of order on purpose: 2026-09-27 is newer than 2026-09-26, so 4f0846e wins over ac56141.
        "name": "the newest non-smoke entry with another sha",
        "runs": [
            entry("4f0846e4057dbff190163312b6ddb82742f7a791", "2026-09-27T07:36:05Z"),
            entry("ac56141af8c1dadf83cd750b7f522b06155d198d", "2026-09-26T21:08:13Z"),
        ],
        "base": "",
        "status": 0,
        "stdout": "4f0846e\n",
        "stderr": "",
    },
    {
        # The newest entry is a rerun of the new build, so the base is the entry before it.
        "name": "a newest entry with the new sha is skipped",
        "runs": [
            entry("ac56141af8c1dadf83cd750b7f522b06155d198d", "2026-09-26T21:08:13Z"),
            entry(NEW_SHA, "2026-09-27T07:36:05Z"),
        ],
        "base": "",
        "status": 0,
        "stdout": "ac56141\n",
        "stderr": "",
    },
    {
        # A smoke run is not a board point, so it is never a base.
        "name": "a newest smoke entry is skipped",
        "runs": [
            entry("ac56141af8c1dadf83cd750b7f522b06155d198d", "2026-09-26T21:08:13Z"),
            entry("4f0846e4057dbff190163312b6ddb82742f7a791", "2026-09-27T07:36:05Z", smoke=True),
        ],
        "base": "",
        "status": 0,
        "stdout": "ac56141\n",
        "stderr": "",
    },
    {
        # The first schema 3 run takes its base from the entries of the older run files.
        "name": "an entry without a schema key still counts",
        "runs": [
            entry("4f0846e4057dbff190163312b6ddb82742f7a791", "2026-09-26T19:10:13Z"),
            entry("ac56141af8c1dadf83cd750b7f522b06155d198d", "2026-09-26T21:08:13Z", schema=None),
        ],
        "base": "",
        "status": 0,
        "stdout": "ac56141\n",
        "stderr": "",
    },
    {
        # Only the new build and a smoke run are on the board.
        "name": "an index with no other non-smoke entry fails",
        "runs": [
            entry(NEW_SHA, "2026-09-26T21:08:13Z"),
            entry("4f0846e4057dbff190163312b6ddb82742f7a791", "2026-09-27T07:36:05Z", smoke=True),
        ],
        "base": "",
        "status": 1,
        "stdout": "",
        "stderr": NO_BASE,
    },
    {
        # The bench job writes this index when gh-pages has no data/index.json.
        "name": "an empty index fails",
        "runs": [],
        "base": "",
        "status": 1,
        "stdout": "",
        "stderr": NO_BASE,
    },
]


class BaseTest(unittest.TestCase):
    def test_base(self):
        for case in BASE_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                index = Path(tmp) / "index.json"
                index.write_text(json.dumps({"runs": case["runs"]}))
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    status = main(["base", "--index", str(index), "--new", NEW, "--base", case["base"]])
                self.assertEqual((status, out.getvalue(), err.getvalue()), (case["status"], case["stdout"], case["stderr"]))


if __name__ == "__main__":
    unittest.main()
