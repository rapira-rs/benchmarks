"""Tests of the base build of a suite: python3 -m rig base."""

import contextlib
import io
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rig.__main__ import main

ROOT = Path(__file__).resolve().parents[1]

BASE_CASES = [
    {"name": "the base of the suite file", "base": 'base = "abc1234"\n', "status": 0, "stdout": "abc1234\n", "stderr": ""},
    # A suite for local runs has no base key: the operator gives BASE to make up.
    {"name": "a suite file without a base fails", "base": "", "status": 1, "stdout": "", "stderr": "ERROR: suite ci has no base build\n"},
]


def write_suites(directory: Path, base: str) -> None:
    """Write the shipped target registry and the shipped ci suite with the given base line into directory."""
    directory.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "suites/targets.toml", directory / "targets.toml")
    text = re.sub(r"(?m)^base = .*\n", "", (ROOT / "suites/ci.toml").read_text())
    (directory / "ci.toml").write_text(text.replace('name = "ci"\n', 'name = "ci"\n' + base))


class BaseTest(unittest.TestCase):
    def test_base(self):
        for case in BASE_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                suites = Path(tmp)
                write_suites(suites, case["base"])
                out, err = io.StringIO(), io.StringIO()
                with mock.patch("rig.__main__.SUITES_DIR", suites), mock.patch("rig.__main__.TARGETS_FILE", suites / "targets.toml"), \
                        contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    status = main(["base", "--suite", "ci"])
                self.assertEqual((status, out.getvalue(), err.getvalue()), (case["status"], case["stdout"], case["stderr"]))


if __name__ == "__main__":
    unittest.main()
