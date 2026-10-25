"""The local build keeps the server and loaders available."""

import contextlib
import io
import os
import unittest
from unittest import mock

from rig.__main__ import main
from rig.rig import DEADLINE_FILE
from tests.test_bench import RIG


SYNC_CASES = [
    {
        "name": "extend every short deadline before the build",
        "deadlines": {"server": 1060, "loader-1": 1060, "loader-2": 1060},
        "auto_extend": "1", "status": 0, "built": True,
        "remaining": {"server": 2580, "loader-1": 2580, "loader-2": 2580},
    },
    {
        "name": "extend loaders when the server has enough time",
        "deadlines": {"server": 10000, "loader-1": 1060, "loader-2": 1060},
        "auto_extend": "1", "status": 0, "built": True,
        "remaining": {"server": 8880, "loader-1": 2580, "loader-2": 2580},
    },
    {
        "name": "a short loader blocks the build when extension is disabled",
        "deadlines": {"server": 10000, "loader-1": 1060, "loader-2": 1060},
        "auto_extend": "0", "status": 1, "built": False,
        "remaining": {"server": 9000, "loader-1": 60, "loader-2": 60},
    },
]


class SyncTest(unittest.TestCase):
    def test_sync_keeps_the_whole_rig_available(self):
        for case in SYNC_CASES:
            with self.subTest(name=case["name"]):
                deadlines = dict(case["deadlines"])
                now = 1000
                built = False

                def run(host, command):
                    nonlocal now, built
                    if command == f"cat {DEADLINE_FILE}":
                        return str(deadlines[host.name])
                    self.assertEqual((host.name, command), ("server", "bash bench-rig/box/build-local.sh"))
                    built = True
                    now += 120
                    return "built\n"

                def run_many(jobs):
                    for host, command in jobs:
                        minutes = int(command.split()[-1])
                        deadlines[host.name] = now + minutes * 60
                    return [""] * len(jobs)

                with mock.patch("rig.__main__.from_terraform", return_value=RIG), \
                        mock.patch("rig.__main__.ssh.stage_tree"), mock.patch("rig.__main__.ssh.stage_dir"), \
                        mock.patch("rig.__main__.ssh.run", side_effect=run), \
                        mock.patch("rig.__main__.ssh.run_many", side_effect=run_many), \
                        mock.patch("rig.rig.time.time", side_effect=lambda: now), \
                        mock.patch.dict(os.environ, {"AUTO_EXTEND": case["auto_extend"]}), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    status = main(["sync"])
                self.assertEqual((status, built), (case["status"], case["built"]))
                self.assertEqual({name: deadline - now for name, deadline in deadlines.items()}, case["remaining"])
