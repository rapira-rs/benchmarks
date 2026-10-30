"""Run workflow scripts locally and evaluate their job conditions."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.test_provision_server import ASSET, SHA, SUMS, VERSION, release

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/bench.yml"

READY_CASES = [
    {"name": "suite and artifacts succeed with normal cleanup", "outcomes": {}, "status": "success", "ready": True, "publish": True},
    {"name": "failed destroy remains visible after successful fallback", "outcomes": {"down": "failure", "nuke": "success"}, "status": "failure", "ready": True, "publish": True},
    {"name": "failed suite blocks publish after fallback", "outcomes": {"suite": "failure", "down": "failure", "nuke": "success"}, "status": "failure", "ready": False, "publish": False},
    {"name": "skipped suite blocks publish", "outcomes": {"suite": "skipped"}, "status": "failure", "ready": False, "publish": False},
    {"name": "failed binary upload blocks publish", "outcomes": {"binary": "failure"}, "status": "failure", "ready": False, "publish": False},
    {"name": "failed run upload blocks publish", "outcomes": {"run": "failure"}, "status": "failure", "ready": False, "publish": False},
    {"name": "missing run file blocks publish", "outcomes": {"run": "missing"}, "status": "failure", "ready": False, "publish": False},
    {"name": "failed raw upload blocks publish", "outcomes": {"raw": "failure"}, "status": "failure", "ready": False, "publish": False},
    {"name": "missing raw evidence blocks publish", "outcomes": {"raw": "missing"}, "status": "failure", "ready": False, "publish": False},
    {"name": "failed fallback blocks publish", "outcomes": {"down": "failure", "nuke": "failure"}, "status": "failure", "ready": False, "publish": False},
    {"name": "failed PGO step stays visible and does not block publish", "outcomes": {"pgo": "failure"}, "status": "failure", "ready": True, "publish": True},
    {"name": "skipped fallback cannot recover failed cleanup", "outcomes": {"down": "failure"}, "status": "failure", "ready": False, "publish": False},
    {"name": "cancelled workflow blocks publish", "outcomes": {}, "status": "cancelled", "ready": True, "publish": False},
]

UP_CASES = [
    {"name": "first zone has capacity", "retry": "0", "zones": ["AZ=eu-central-1a"]},
    {"name": "capacity retry keeps the captured directory", "retry": "1", "zones": ["AZ=eu-central-1a", "AZ=eu-central-1b"]},
]

PAGES_CASES = [
    {"name": "first attempt deploys its own upload", "attempt": 1},
    {"name": "rerun after a deployed attempt deploys its own upload", "attempt": 2},
]

GH = '''
import json, os, shutil, sys
from pathlib import Path

args = sys.argv[1:]
if args[0] == "api":
    if "repos/rapira-rs/rapira/commits/nightly" in args:
        print(os.environ["SHA"])
    elif any("/releases/tags/" in arg for arg in args):
        print(os.environ["ASSET"].replace("abc1234", "def5678") if any(arg.endswith("/binaries") for arg in args) else os.environ["ASSET"])
    elif any("/contents/" in arg for arg in args):
        print('{"runs": []}')
    elif any(arg.endswith("/pulls") for arg in args):
        print("null")
    else:
        sys.exit("unexpected gh api: " + repr(args))
elif args[:2] == ["release", "download"]:
    dest = Path(args[args.index("-D") + 1])
    dest.mkdir(parents=True)
    for index, arg in enumerate(args):
        if arg == "-p":
            shutil.copyfile(Path(os.environ["RELEASE"]) / args[index + 1], dest / args[index + 1])
elif args[:2] == ["release", "upload"]:
    Path("uploaded.json").write_text(json.dumps([arg for arg in args if arg.startswith("binary/")]))
else:
    sys.exit("unexpected gh: " + repr(args))
'''

MAKE = '''
import json, os, sys
from pathlib import Path

path = Path("make.json")
calls = json.loads(path.read_text()) if path.exists() else []
calls.append(sys.argv[1:])
path.write_text(json.dumps(calls))
if os.environ["RETRY"] == "1" and len(calls) == 1:
    print("api error InsufficientInstanceCapacity: no capacity")
    sys.exit(1)
'''


def job(name):
    return re.search(rf"(?ms)^  {name}:\n(.*?)(?=^  \w+:|\Z)", WORKFLOW.read_text()).group(1)


def step(name):
    return re.search(rf"(?ms)^      - name: {re.escape(name)}\n(.*?)(?=^      - |^  \w+:|\Z)", WORKFLOW.read_text()).group(1)


def field(source, name, indent):
    match = re.search(rf"(?m)^{' ' * indent}{re.escape(name)}: (.+)$", source)
    return match.group(1) if match else ""


def script(name):
    block = step(name).split("        run: |\n", 1)[1]
    return textwrap.dedent(re.match(r"(?:          [^\n]*\n|\n)*", block).group(0))


def expression(value, context, status):
    # Evaluate the Boolean subset used by the job outputs and conditions.
    value = value.removeprefix("${{").removesuffix("}}").strip()
    if not value:
        return False
    value = re.sub(r"\b(?:steps|needs)\.[\w.]+", lambda match: repr(context.get(match.group(0), "")), value)
    value = value.replace("always()", "True").replace("cancelled()", repr(status == "cancelled")).replace("success()", repr(status == "success"))
    value = value.replace("&&", " and ").replace("||", " or ")
    value = re.sub(r"!(?!=)", "not ", value)
    return eval(value.strip(), {"__builtins__": {}}, {})


def executable(path, body):
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o755)


class WorkflowTest(unittest.TestCase):
    def test_publish_readiness(self):
        for case in READY_CASES:
            with self.subTest(name=case["name"]):
                outcomes = {"suite": "success", "binary": "success", "run": "success", "raw": "success", "pgo": "success", "down": "success", "nuke": "skipped", **case["outcomes"]}
                for upload, name in {"run": "Upload the run file", "raw": "Upload the raw evidence"}.items():
                    if outcomes[upload] == "missing":
                        outcomes[upload] = "failure" if field(step(name), "if-no-files-found", 10) == "error" else "success"
                steps = {"suite": "Run the ci suite", "binary": "Upload the new binary", "run": "Upload the run file", "raw": "Upload the raw evidence", "pgo": "Make the PGO profile", "down": "Destroy the rig", "nuke": "Remove tagged leftovers"}
                context = {f"steps.{field(step(steps[name]), 'id', 8)}.outcome": outcome for name, outcome in outcomes.items()}
                ready = expression(field(job("bench"), "ready", 6), context, case["status"])
                self.assertEqual(ready, case["ready"])
                context.update({"needs.bench.outputs.ready": str(ready).lower(), "needs.bench.outputs.skip": "false"})
                condition = field(job("publish"), "if", 4)
                # GitHub applies success() when an if condition has no status function.
                has_status = re.search(r"\b(?:always|cancelled|success|failure)\(\)", condition)
                publish = (bool(has_status) or case["status"] == "success") and expression(condition, context, case["status"])
                self.assertEqual(publish, case["publish"])

    def test_resolve_captures_metadata_and_cache_uploads_only_release_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "runner/binary"
            release(root / "release", b"captured binary")
            (root / "bin").mkdir()
            executable(root / "bin/gh", GH)
            env = dict(os.environ, PATH=f"{root / 'bin'}:{os.environ['PATH']}", PYTHONPATH=str(ROOT), SHA=SHA, ASSET=ASSET, RELEASE=str(root / "release"), GITHUB_REPOSITORY="example/benchmarks", INPUT_SHA="", INPUT_VERSION="", INPUT_BASE="def5678", GITHUB_OUTPUT=str(root / "outputs"), RUNNER_TEMP=str(root / "runner"))
            proc = subprocess.run(["bash", "-e", "-c", script("Resolve the nightly and the base commits")], cwd=root, env=env, text=True, capture_output=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue((binary / "nightly.json").is_file(), "the artifact must retain the resolved full commit and asset names")
            self.assertEqual(json.loads((binary / "nightly.json").read_text()), {"sha": SHA, "version": VERSION, "asset": ASSET, "sums": SUMS})
            self.assertEqual((binary / ASSET).read_bytes(), (root / "release" / ASSET).read_bytes())
            shutil.copytree(binary, root / "binary")
            proc = subprocess.run(["bash", "-e", "-c", script("Cache the new binary")], cwd=root, env=env, text=True, capture_output=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(sorted(json.loads((root / "uploaded.json").read_text())), sorted([f"binary/{ASSET}", f"binary/{SUMS}"]))

    def test_capacity_retry_uses_the_captured_directory(self):
        for case in UP_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                executable(root / "make", MAKE)
                env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", RETRY=case["retry"], SHA7="abc1234", BASE7="def5678", RUNNER_TEMP=str(root / "runner"))
                proc = subprocess.run(["bash", "-e", "-c", script("Create and provision the rig")], cwd=root, env=env, capture_output=True, text=True, check=False)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                calls = json.loads((root / "make.json").read_text())
                ups = [args for args in calls if args[0] == "up"]
                self.assertEqual(len(ups), len(case["zones"]))
                for args, zone in zip(ups, case["zones"]):
                    self.assertIn(f"NIGHTLY_DIR={root / 'runner/binary'}", args)
                    self.assertIn(zone, args)

    def test_publish_deploys_the_pages_artifact_of_its_attempt(self):
        # The run keeps the artifacts of earlier attempts, and deploy-pages needs exactly one artifact with its name:
        # https://github.com/actions/deploy-pages/blob/368f82528645a54fb793d4d04e342629a3f51346/src/internal/api-client.js#L79-L92
        # Both actions name the artifact github-pages by default.
        upload = field(step("Upload the Pages site"), "name", 10) or "github-pages"
        deploy = field(step("Deploy the Pages site"), "artifact_name", 10) or "github-pages"
        for case in PAGES_CASES:
            with self.subTest(name=case["name"]):
                artifacts = [upload.replace("${{ github.run_attempt }}", str(attempt)) for attempt in range(1, case["attempt"] + 1)]
                self.assertEqual(artifacts.count(deploy.replace("${{ github.run_attempt }}", str(case["attempt"]))), 1)
