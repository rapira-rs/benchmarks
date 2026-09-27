import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rig import rig as rigmod
from rig import ssh
from rig.__main__ import main
from rig.registry import Stage, Suite, Target, suite_needs
from rig.rig import Rig
from rig.ssh import Host, SshError

SERVER = Host("server", "3.0.0.1", "10.0.0.1")
LOADER_1 = Host("loader-1", "3.0.0.2", "10.0.0.2")
LOADER_2 = Host("loader-2", "3.0.0.3", "10.0.0.3")


def tf_outputs(loader_public, loader_private):
    values = {
        "server_public_ip": "3.0.0.1",
        "server_private_ip": "10.0.0.1",
        "loader_public_ips": loader_public,
        "loader_private_ips": loader_private,
        "ami_id": "ami-0123",
        "server_instance_type": "c7a.8xlarge",
        "loader_instance_type": "c7a.xlarge",
        "loader_count": len(loader_public),
        "key_file": "./rig-key.pem",
        "placement_group": "rapira-bench",
    }
    return json.dumps({name: {"sensitive": False, "type": "string", "value": value} for name, value in values.items()})


FROM_TERRAFORM_CASES = [
    {
        "name": "two loaders keep the output order",
        "stdout": tf_outputs(["3.0.0.2", "3.0.0.3"], ["10.0.0.2", "10.0.0.3"]),
        "returncode": 0,
        "key": True,
        "loaders": (LOADER_1, LOADER_2),
        "error": None,
    },
    {
        "name": "one loader",
        "stdout": tf_outputs(["3.0.0.2"], ["10.0.0.2"]),
        "returncode": 0,
        "key": True,
        "loaders": (LOADER_1,),
        "error": None,
    },
    {
        "name": "no outputs before make up",
        "stdout": "{}",
        "returncode": 0,
        "key": True,
        "loaders": None,
        "error": "run 'make up' first",
    },
    {
        "name": "terraform fails without state",
        "stdout": "",
        "returncode": 1,
        "key": True,
        "loaders": None,
        "error": "run 'make up' first",
    },
    {
        "name": "key file missing",
        "stdout": tf_outputs(["3.0.0.2"], ["10.0.0.2"]),
        "returncode": 0,
        "key": False,
        "loaders": None,
        "error": "rig-key.pem is missing",
    },
]

REMAINING_TTL_CASES = [
    {"name": "deadline in the future", "reply": "1600\n", "now": 1000.4, "expected": 600},
    {"name": "deadline passed", "reply": "900\n", "now": 1000.0, "expected": -100},
    {"name": "deadline unreadable over ssh", "reply": SshError("server: exit 1"), "now": 1000.0, "expected": 0},
    {"name": "deadline file is not a number", "reply": "\n", "now": 1000.0, "expected": 0},
]

ENSURE_TTL_CASES = [
    {
        "name": "extend only the short host",
        "left": {"server": 600, "loader-1": 100},
        "needed": 300,
        "auto_extend": "1",
        # 300 // 60 + 15 = 20 minutes.
        "armed": [(["loader-1"], 20)],
        "error": None,
    },
    {
        "name": "equal to the need is enough",
        "left": {"server": 300, "loader-1": 300},
        "needed": 300,
        "auto_extend": "1",
        "armed": [],
        "error": None,
    },
    {
        "name": "long run adds the margin to the need",
        "left": {"server": 0, "loader-1": 0},
        "needed": 3000,
        "auto_extend": "1",
        # 3000 // 60 + 15 = 65 minutes on each host.
        "armed": [(["server"], 65), (["loader-1"], 65)],
        "error": None,
    },
    {
        "name": "AUTO_EXTEND=0 makes a short TTL an error",
        "left": {"server": 600, "loader-1": 100},
        "needed": 300,
        "auto_extend": "0",
        "armed": [],
        "error": "TTL on loader-1 expires in 100 s",
    },
]

RUN_CASES = [
    {"name": "stdout on exit 0", "returncode": 0, "stdout": b"8\n", "stderr": b"", "timeout": False, "result": "8\n", "error": None},
    {"name": "exit 255 names the host and keeps stderr", "returncode": 255, "stdout": b"", "stderr": b"Connection refused\n", "timeout": False, "result": None, "error": "loader-1: exit 255: nproc\nConnection refused"},
    {"name": "stdout replaces an empty stderr", "returncode": 1, "stdout": b"ERROR: port 8080 busy\n", "stderr": b"", "timeout": False, "result": None, "error": "loader-1: exit 1: nproc\nERROR: port 8080 busy"},
    {"name": "stdout comes before stderr", "returncode": 1, "stdout": b"ERROR: server: dnf failed\n", "stderr": b"warning: x\n", "timeout": False, "result": None, "error": "loader-1: exit 1: nproc\nERROR: server: dnf failed\nwarning: x"},
    {"name": "the box ERROR line on stderr is the last line", "returncode": 1, "stdout": b"", "stderr": b"ERROR: t: no listener\n", "timeout": False, "result": None, "error": "loader-1: exit 1: nproc\nERROR: t: no listener"},
    {"name": "timeout", "returncode": 0, "stdout": b"", "stderr": b"", "timeout": True, "result": None, "error": "loader-1: timeout after 5 s: nproc"},
]

NEEDS_CASES = [
    {
        "name": "servers first then apps, each sorted without repeats",
        "targets": [("yii3", "rapira"), ("hello", "rapira"), ("grpc", "rapira")],
        "expected": ["rapira", "grpc", "hello", "yii3"],
    },
    {
        "name": "one rapira target",
        "targets": [("hello", "rapira")],
        "expected": ["rapira", "hello"],
    },
]

PROVISION_CASES = [
    {
        "name": "nightly with a quoted needs list",
        "env": {"NIGHTLY": "abc1234", "REF": "", "BASE": "abc1234", "NEEDS": "rapira hello yii3"},
        "results": ["ok", "ok", "ok"],
        "server_cmd": "NIGHTLY=abc1234 REF='' BASE=abc1234 NEEDS='rapira hello yii3' bash bench-rig/box/provision-server.sh",
        "error": None,
    },
    {
        "name": "a failed loader fails the provisioning",
        "env": {"NIGHTLY": "", "REF": "pr/97", "BASE": "def5678", "NEEDS": "rapira hello"},
        "results": ["ok", "ok", SshError("loader-2: exit 1: bash bench-rig/box/provision-loader.sh")],
        "server_cmd": "NIGHTLY='' REF=pr/97 BASE=def5678 NEEDS='rapira hello' bash bench-rig/box/provision-server.sh",
        "error": "loader-2: exit 1",
    },
]

PROVISION_CLI_CASES = [
    {
        # An A/A run: the base build is the nightly itself.
        "name": "the nightly and the base go to the server env",
        "argv": ["provision", "--ttl", "60", "--needs", "rapira hello", "--nightly", "abc1234", "--base", "abc1234"],
        "status": 0,
        "server_env": {"NIGHTLY": "abc1234", "REF": "", "BASE": "abc1234", "NEEDS": "rapira hello", "FRAME_POINTERS": "0"},
        "stderr": "",
    },
    {
        # A REF build on the box and a base tarball from the cache: the spec allows BASE next to REF.
        "name": "a ref build gets a base from the binaries release",
        "argv": ["provision", "--ttl", "60", "--needs", "rapira hello", "--ref", "pr/97", "--base", "def5678", "--frame-pointers", "1"],
        "status": 0,
        "server_env": {"NIGHTLY": "", "REF": "pr/97", "BASE": "def5678", "NEEDS": "rapira hello", "FRAME_POINTERS": "1"},
        "stderr": "",
    },
    {
        # make provision passes --base "" when BASE is not set.
        "name": "an empty base is refused",
        "argv": ["provision", "--ttl", "60", "--needs", "rapira hello", "--nightly", "abc1234", "--base", ""],
        "status": 1,
        "server_env": None,
        "stderr": "ERROR: set BASE=<sha7>",
    },
    {
        # argparse exits with the status 2 when a required option is missing.
        "name": "a missing base is refused",
        "argv": ["provision", "--ttl", "60", "--needs", "rapira hello", "--nightly", "abc1234"],
        "status": 2,
        "server_env": None,
        "stderr": "the following arguments are required: --base",
    },
]


def target(app, server):
    return Target(
        name=f"{app}-{server}-worker", server=server, app=app, mode="worker", proto="http1",
        start=(), url="/", expect="apps/hello/expect.txt", config="",
    )


class FromTerraformTest(unittest.TestCase):
    def test_from_terraform(self):
        for case in FROM_TERRAFORM_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                tf_dir = Path(tmp)
                if case["key"]:
                    (tf_dir / "rig-key.pem").write_text("key")
                done = subprocess.CompletedProcess([], case["returncode"], stdout=case["stdout"], stderr="")
                with mock.patch("rig.rig.subprocess.run", return_value=done), mock.patch("rig.rig.ssh.set_key") as set_key:
                    if case["error"]:
                        with self.assertRaisesRegex(RuntimeError, case["error"]):
                            rigmod.from_terraform(tf_dir)
                        continue
                    rig = rigmod.from_terraform(tf_dir)
                self.assertEqual(rig.server, SERVER)
                self.assertEqual(rig.loaders, case["loaders"])
                self.assertEqual((rig.server_type, rig.loader_type, rig.ami_id), ("c7a.8xlarge", "c7a.xlarge", "ami-0123"))
                self.assertEqual(rig.key_file, tf_dir / "rig-key.pem")
                set_key.assert_called_once_with(tf_dir / "rig-key.pem")


class TtlTest(unittest.TestCase):
    def test_remaining_ttl_s(self):
        for case in REMAINING_TTL_CASES:
            with self.subTest(name=case["name"]):
                reply = case["reply"]
                effect = reply if isinstance(reply, Exception) else None
                with mock.patch("rig.rig.ssh.run", return_value=reply, side_effect=effect), mock.patch("rig.rig.time.time", return_value=case["now"]):
                    self.assertEqual(rigmod.remaining_ttl_s(SERVER), case["expected"])

    def test_ensure_ttl(self):
        for case in ENSURE_TTL_CASES:
            with self.subTest(name=case["name"]):
                armed = []
                with mock.patch.dict(os.environ, {"AUTO_EXTEND": case["auto_extend"]}), \
                        mock.patch("rig.rig.remaining_ttl_s", side_effect=lambda host: case["left"][host.name]), \
                        mock.patch("rig.rig.arm_ttl", side_effect=lambda hosts, minutes: armed.append(([h.name for h in hosts], minutes))):
                    if case["error"]:
                        with self.assertRaisesRegex(RuntimeError, case["error"]):
                            rigmod.ensure_ttl([SERVER, LOADER_1], case["needed"])
                    else:
                        rigmod.ensure_ttl([SERVER, LOADER_1], case["needed"])
                self.assertEqual(armed, case["armed"])


class SshTest(unittest.TestCase):
    def test_ssh_argv(self):
        ssh.set_key(Path("terraform/rig-key.pem"))
        self.assertEqual(ssh.ssh_argv(LOADER_1, "nproc"), [
            "ssh", "-i", "terraform/rig-key.pem", "-o", "User=fedora",
            "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=.ssh-known-hosts",
            "-o", "ControlMaster=auto", "-o", "ControlPath=.ssh-cm-%h", "-o", "ControlPersist=10m",
            "-o", "ConnectTimeout=5", "-o", "LogLevel=ERROR", "3.0.0.2", "nproc",
        ])

    def test_run(self):
        for case in RUN_CASES:
            with self.subTest(name=case["name"]):
                if case["timeout"]:
                    patch = mock.patch("rig.ssh.subprocess.run", side_effect=subprocess.TimeoutExpired("ssh", 5))
                else:
                    done = subprocess.CompletedProcess([], case["returncode"], stdout=case["stdout"], stderr=case["stderr"])
                    patch = mock.patch("rig.ssh.subprocess.run", return_value=done)
                with patch:
                    if case["error"]:
                        with self.assertRaises(SshError) as caught:
                            ssh.run(LOADER_1, "nproc", timeout=5)
                        self.assertEqual(str(caught.exception), case["error"])
                    else:
                        self.assertEqual(ssh.run(LOADER_1, "nproc", timeout=5), case["result"])

    def test_run_many_keeps_job_order_and_errors(self):
        failure = SshError("loader-1: exit 1: nproc")

        def fake_run(host, cmd, *, timeout=None):
            if host is LOADER_1:
                raise failure
            return host.name

        with mock.patch("rig.ssh.run", side_effect=fake_run):
            results = ssh.run_many([(SERVER, "nproc"), (LOADER_1, "nproc"), (LOADER_2, "nproc")])
        self.assertEqual(results, ["server", failure, "loader-2"])

    def test_tree_files_skip_ignored_and_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text("*.log\n")
            (root / "kept.txt").write_text("a")
            (root / "gone.txt").write_text("b")
            subprocess.run(["git", "-C", str(root), "add", ".gitignore", "kept.txt", "gone.txt"], check=True)
            (root / "gone.txt").unlink()
            (root / "new.txt").write_text("c")
            (root / "run.log").write_text("d")
            self.assertEqual(ssh.tree_files(root), [".gitignore", "kept.txt", "new.txt"])


class NeedsTest(unittest.TestCase):
    def test_suite_needs(self):
        for case in NEEDS_CASES:
            with self.subTest(name=case["name"]):
                suite = Suite(
                    name="t", rounds=1, smoke=False, connections={}, grpc_streams=1,
                    targets=tuple(target(app, server) for app, server in case["targets"]),
                    stages={
                        "rate": Stage(kind="rate", warmup_s=11, duration_s=15, processes=None, rates={}),
                        "cap": Stage(kind="cap", warmup_s=5, duration_s=15, processes=2, rates={}),
                    },
                )
                self.assertEqual(suite_needs(suite), case["expected"])


class ProvisionTest(unittest.TestCase):
    def test_provision(self):
        for case in PROVISION_CASES:
            with self.subTest(name=case["name"]):
                rig = Rig(SERVER, (LOADER_1, LOADER_2), "c7a.8xlarge", "c7a.xlarge", "ami-0123", Path("k"))
                jobs = []

                def fake_run_many(batch, *, timeout=None):
                    jobs.append([(host.name, cmd) for host, cmd in batch])
                    if batch[0][1].endswith("provision-server.sh"):
                        return case["results"]
                    return ["" for _ in batch]

                with mock.patch("rig.rig.ssh.wait_ssh"), mock.patch("rig.rig.ssh.stage_tree"), \
                        mock.patch("rig.rig.ssh.run_many", side_effect=fake_run_many), \
                        mock.patch("rig.rig.arm_ttl") as arm:
                    if case["error"]:
                        with self.assertRaisesRegex(SshError, case["error"]):
                            rigmod.provision(rig, ttl_min=60, server_env=case["env"])
                    else:
                        rigmod.provision(rig, ttl_min=60, server_env=case["env"])
                arm.assert_called_once_with([SERVER, LOADER_1, LOADER_2], 60)
                self.assertEqual(jobs[-1], [
                    ("server", case["server_cmd"]),
                    ("loader-1", "bash bench-rig/box/provision-loader.sh"),
                    ("loader-2", "bash bench-rig/box/provision-loader.sh"),
                ])


class ProvisionCommandTest(unittest.TestCase):
    def test_provision_command(self):
        for case in PROVISION_CLI_CASES:
            with self.subTest(name=case["name"]):
                err = io.StringIO()
                with mock.patch("rig.__main__.from_terraform", return_value=mock.sentinel.rig), \
                        mock.patch("rig.__main__.provision") as provision, \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                    try:
                        status = main(case["argv"])
                    except SystemExit as exc:
                        status = exc.code
                self.assertEqual(case["status"], status)
                self.assertIn(case["stderr"], err.getvalue())
                if case["server_env"] is None:
                    provision.assert_not_called()
                else:
                    provision.assert_called_once_with(mock.sentinel.rig, ttl_min=60, server_env=case["server_env"])
