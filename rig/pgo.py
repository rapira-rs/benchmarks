"""The PGO pass: an instrumented build of the new build serves the rate stage of each suite target once.

https://doc.rust-lang.org/rustc/profile-guided-optimization.html
"""

import time
from pathlib import Path

from rig.bench import BENCH_DIR, CELL_OVERHEAD_S, LEAD_S, LOAD_SLACK_S, PORT, TTL_MARGIN_S, Boxes, box_cmd, load_cmd, plan_run
from rig.registry import Suite
from rig.rig import Rig, ensure_ttl
from rig.ssh import SshError

PGO_DIR = f"{BENCH_DIR}/pgo"
# The ssh timeout of the instrumented build, the package and toolchain installs included.
BUILD_S = 900
FILES = ("merged.profdata", "pgo.json")


def run_pgo(rig: Rig, suite: Suite, boxes: Boxes, out_dir: Path, *, server_vcpus: int, loader_threads: int) -> None:
    """Build the instrumented rapira, load each target at its rate-stage rate, merge the counters, and copy FILES to out_dir."""
    plan = plan_run(rig, suite, server_vcpus=server_vcpus, loader_threads=loader_threads)
    stage = suite.stages["rate"]
    ensure_ttl(rig.hosts, BUILD_S + len(suite.targets) * (stage.warmup_s + stage.duration_s + CELL_OVERHEAD_S) + TTL_MARGIN_S)
    print("==> pgo build")
    boxes.run(rig.server, box_cmd("pgo.sh", "build"), timeout=BUILD_S)
    for target in suite.targets:
        print(f"==> pgo {target.name}")
        tag = f"pgo-{target.name}"
        boxes.run(rig.server, box_cmd("pgo.sh", "start", tag, target.server, server_vcpus, *target.start))
        try:
            url = f"http://{rig.server.private_ip}:{PORT}{target.url}"
            epoch = time.time() + LEAD_S
            rate = stage.rates[target.name] // len(rig.loaders)
            loads = [(loader, load_cmd(target, url, epoch, rate, plan, suite, stage)) for loader in rig.loaders]
            for out in boxes.run_many(loads, timeout=LEAD_S + stage.warmup_s + stage.duration_s + LOAD_SLACK_S):
                if isinstance(out, SshError):
                    raise out
        finally:
            boxes.run(rig.server, box_cmd("target.sh", "stop", tag, target.server))
    boxes.run(rig.server, box_cmd("pgo.sh", "merge", *(target.name for target in suite.targets)))
    for name in FILES:
        boxes.copy_from(rig.server, f"{PGO_DIR}/{name}", out_dir / name)
