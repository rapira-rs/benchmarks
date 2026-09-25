"""The cell sequence and the rate ladder of one suite run."""

import hashlib
import json
import math
import shlex
import time
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from rig import ssh
from rig.flags import cell_flags, cpu_pct, ena_delta, keepalive_flag, parse_snapshot, stage_flags, stage_void
from rig.ladder import MAX_STAGES, PASS_TOLERANCE, RATIO, cell_numbers, evaluate_stage, stage_rates
from rig.merge import merge, parse_result
from rig.registry import Suite, SuiteError, Target, cell_key, plan_cells
from rig.rig import Rig, ensure_ttl
from rig.runfile import RunFile
from rig.ssh import Host, SshError

PORT = 8080
LEAD_S = 3
WARMUP_S = 10
BOX = f"bash {ssh.RIG_DIR}/box"
BENCH_DIR = "/opt/bench"
RAPIRA_SERVERS = ("rapira", "nginx-rapira")
# k6 preallocates the VUs for this latency at the stage rate of one loader.
K6_LATENCY_BUDGET_S = 0.005
# ssh slack over the lead time and the stage duration of one load call.
LOAD_SLACK_S = 60
SNAPSHOT_TIMEOUT_S = 30
# Sleep until the wall clock time in argv[1].
WAIT_PY = "import sys, time; time.sleep(max(0.0, float(sys.argv[1]) - time.time()))"
SUITES_DIR = Path("suites")
FACTS_CMD = (
    "echo kernel=$(uname -r); "
    "echo instance_id=$(cat /sys/devices/virtual/dmi/id/board_asset_tag); "
    "t=$(curl -s -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' http://169.254.169.254/latest/api/token); "
    "m=http://169.254.169.254/latest/meta-data/placement; "
    "echo az=$(curl -s -H \"X-aws-ec2-metadata-token: $t\" $m/availability-zone); "
    "echo placement_group=$(curl -s -H \"X-aws-ec2-metadata-token: $t\" $m/group-name)"
)
# Provisioning writes the wrk2 commit and the k6 version to loader.json.
TOOLS_CMD = (
    "python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); "
    "print(\"wrk2=\" + d[\"wrk2_commit\"]); print(\"k6=\" + d[\"k6_version\"])' "
    f"{BENCH_DIR}/loader.json"
)


class Boxes(Protocol):
    def run(self, host: Host, cmd: str, timeout: float | None = None) -> str: ...
    def run_many(self, jobs: list[tuple[Host, str]], timeout: float | None = None) -> list[str | SshError]: ...
    def copy_from(self, host: Host, remote: str, local: Path) -> None: ...


class SshBoxes:
    """The Boxes of a real rig."""

    def run(self, host: Host, cmd: str, timeout: float | None = None) -> str:
        return ssh.run(host, cmd, timeout=timeout)

    def run_many(self, jobs: list[tuple[Host, str]], timeout: float | None = None) -> list[str | SshError]:
        return ssh.run_many(jobs, timeout=timeout)

    def copy_from(self, host: Host, remote: str, local: Path) -> None:
        ssh.copy_from(host, remote, local)


class CellVoid(Exception):
    """The cell is excluded from every number. The message is the reason."""


def plan_run(rig: Rig, suite: Suite, *, processes: int, loader_threads: int) -> dict:
    """Check the suite against the rig and return the cells, the keys, and the load shape."""
    loaders = len(rig.loaders)
    if suite.connections % (loaders * loader_threads) != 0:
        raise SuiteError(
            f"connections {suite.connections} is not a multiple of {loaders} loaders x {loader_threads} threads"
        )
    cells = plan_cells(suite)
    return {
        "cells": cells,
        "keys": [cell_key(round_no, target) for round_no, target in cells],
        "rates": {app: stage_rates(floor) for app, floor in sorted(suite.floors.items())},
        "conns_per_loader": suite.connections // loaders,
        "loader_threads": loader_threads,
        "processes": processes,
    }


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def binary_dir(target: Target, rapira: dict) -> str:
    """The rapira install directory of a target, or "-" for other servers."""
    if target.server not in RAPIRA_SERVERS:
        return "-"
    if target.binary == "base":
        base = rapira.get("base")
        if not base:
            raise ValueError("the suite needs a base build; provision with REF and BASE_REF")
        return base["dir"]
    return rapira["dir"]


def request_args(target: Target) -> list[str]:
    """METHOD, BODY_FILE, and HEADER arguments of probe.sh and load.sh. A relative file is under the staged rig."""
    body = target.body or "-"
    return [target.method, body, *(f"{name}: {value}" for name, value in target.headers)]


def box_cmd(script: str, *args: object) -> str:
    return " ".join([f"{BOX}/{script}", *(shlex.quote(str(arg)) for arg in args)])


def probe_cmd(target: Target, url: str) -> str:
    return box_cmd("probe.sh", url, target.expect, target.proto, *request_args(target))


def load_cmd(target: Target, url: str, epoch: float, rate: int, plan: dict, duration_s: int) -> str:
    """One load.sh call of one loader."""
    if target.proto == "grpc":
        vus = max(plan["conns_per_loader"], math.ceil(rate * K6_LATENCY_BUDGET_S))
        return box_cmd("load.sh", "k6", f"{epoch:.3f}", rate, vus, duration_s, url)
    return box_cmd(
        "load.sh", "wrk2", f"{epoch:.3f}", rate, plan["loader_threads"], plan["conns_per_loader"], duration_s, url,
        *request_args(target),
    )


def parse_facts(text: str) -> dict:
    facts = {}
    for line in text.splitlines():
        name, sep, value = line.partition("=")
        if sep:
            facts[name] = value
    return facts


def host_facts(boxes: Boxes, rig: Rig) -> dict[str, dict]:
    """Kernel, instance id, AZ, placement group, and on loaders the tool versions."""
    jobs = [(rig.server, FACTS_CMD)] + [(loader, f"{FACTS_CMD}; {TOOLS_CMD}") for loader in rig.loaders]
    facts = {}
    for (host, _), out in zip(jobs, boxes.run_many(jobs, timeout=SNAPSHOT_TIMEOUT_S)):
        if isinstance(out, SshError):
            raise out
        facts[host.name] = parse_facts(out)
    return facts


def server_versions(boxes: Boxes, server: Host) -> dict:
    """The version lines that provisioning recorded and the shared php.ini text."""
    versions = json.loads(boxes.run(server, f"cat {BENCH_DIR}/versions.json"))
    versions["php_ini"] = boxes.run(server, f"cat {BENCH_DIR}/php.ini")
    return versions


def app_hashes(root: Path) -> dict[str, str]:
    """SHA-256 of every staged file under apps/, composer.lock files included."""
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ssh.tree_files(root)
        if name.startswith("apps/")
    }


def suite_record(suite: Suite) -> dict:
    path = SUITES_DIR / f"{suite.name}.toml"
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return {"name": suite.name, "file_sha256": digest, "rounds": suite.rounds, "stage_s": suite.stage_s, "connections": suite.connections}


def target_record(target: Target) -> dict:
    return {**asdict(target), "start": list(target.start), "headers": dict(target.headers)}


def at_time(at: float, cmd: str) -> str:
    """cmd after a wait until the wall clock time at."""
    return f"python3 -c {shlex.quote(WAIT_PY)} {at:.3f} && {cmd}"


def sample_jobs(rig: Rig, epoch: float, stage_s: int, mem: str) -> list[tuple[str, Host, str]]:
    """Snapshot jobs of every box at the stage start, the stage middle, and 0.5 s after the stage end.

    The server snapshots count the connections on PORT. The middle server job also runs mem.
    """
    jobs = []
    for mark, at in (("begin", epoch), ("mid", epoch + stage_s / 2), ("end", epoch + stage_s + 0.5)):
        server_cmd = box_cmd("snapshot.sh", PORT)
        if mark == "mid":
            server_cmd += f" && {mem}"
        jobs.append((mark, rig.server, at_time(at, server_cmd)))
        jobs += [(mark, loader, at_time(at, box_cmd("snapshot.sh"))) for loader in rig.loaders]
    return jobs


def run_stage(boxes: Boxes, rig: Rig, suite: Suite, plan: dict, target: Target, cell: dict, cell_dir: Path, index: int, rate: int) -> dict:
    """Run one stage from every loader and return its stage record."""
    server = rig.server
    url = f"http://{server.private_ip}:{PORT}{target.url}"
    per_loader = rate // len(rig.loaders)
    epoch = time.time() + LEAD_S
    loads = [(loader, load_cmd(target, url, epoch, per_loader, plan, suite.stage_s)) for loader in rig.loaders]
    # The middle server sample also reads the memory of the target.
    samples = sample_jobs(rig, epoch, suite.stage_s, box_cmd("target.sh", "mem", cell["key"], target.server))
    results = boxes.run_many(loads + [(host, cmd) for _, host, cmd in samples], timeout=LEAD_S + suite.stage_s + LOAD_SLACK_S)

    taken = {}
    with (cell_dir / "snapshots.txt").open("a") as log:
        for (mark, host, _), out in zip(samples, results[len(loads):]):
            if isinstance(out, SshError):
                raise out
            log.write(f"# stage {index} {mark} {host.name}\n{out}")
            taken[mark, host.name] = out
    before = {host.name: parse_snapshot(taken["begin", host.name]) for host in rig.hosts}
    after = {host.name: parse_snapshot(taken["end", host.name]) for host in rig.hosts}
    sample_lines = taken["mid", server.name].strip().splitlines()
    middle = parse_snapshot("\n".join(sample_lines[:-1]))

    records = {}
    for loader, out in zip(rig.loaders, results):
        text = str(out) if isinstance(out, SshError) else out
        (cell_dir / f"{index}-{loader.name}.txt").write_text(text)
        try:
            records[loader.name] = None if isinstance(out, SshError) else parse_result(out, loader.name)
        except ValueError as exc:
            raise CellVoid(f"stage {index}: {loader.name}: {exc}") from exc
    try:
        merged = merge(records, suite.stage_s)
    except ValueError as exc:
        raise CellVoid(f"stage {index}: {exc}") from exc
    loader_ena = {loader.name: ena_delta(before[loader.name], after[loader.name]) for loader in rig.loaders}
    throttled = stage_void(loader_ena)
    if throttled:
        raise CellVoid(f"stage {index}: {throttled}")

    passed, reason = evaluate_stage(rate, merged)
    server_busy = cpu_pct(before[server.name], after[server.name])
    loader_busy = {loader.name: cpu_pct(before[loader.name], after[loader.name]) for loader in rig.loaders}
    flags = stage_flags(server_busy, loader_busy, passed)
    server_ena = ena_delta(before[server.name], after[server.name])
    if server_ena:
        flags["ena_throttled"] = server_ena
    flags.update(keepalive_flag(before[server.name], after[server.name], suite.connections))
    stage = {
        "rate": rate,
        "duration_s": suite.stage_s,
        "pass": passed,
        "merged": {
            "requests": merged.requests,
            "successful": merged.successful,
            "bytes": merged.bytes,
            "errors": dict(merged.errors),
            "achieved_rps": merged.achieved_rps,
            "successful_rps": merged.successful_rps,
        },
        "latency_us": dict(merged.latency_us),
        "loaders": [
            {
                "loader": record.loader,
                "tool": record.tool,
                "late_ms": record.late_ms,
                "requests": record.requests,
                "bytes": record.bytes,
                "errors": dict(record.errors),
                "latency_us": dict(record.latency_us),
                "requests_per_sec": record.requests_per_sec,
                "busy_cpu": loader_busy[name],
                "ena": loader_ena[name],
            }
            for name, record in records.items()
        ],
        "server": {
            "busy_cpu": server_busy,
            "pss_kb": int(sample_lines[-1]),
            "established": middle.conns[0] if middle.conns else 0,
            "time_wait": middle.conns[1] if middle.conns else 0,
        },
        "flags": flags,
    }
    if not passed:
        stage["fail_reason"] = reason
    return stage


def add_stage_flags(into: dict, flags: dict) -> None:
    """Carry the flags of one stage to the cell. ENA deltas add up over the stages."""
    for name, value in flags.items():
        if name == "ena_throttled":
            total = into.setdefault("ena_throttled", {})
            for counter, delta in value.items():
                total[counter] = total.get(counter, 0) + delta
        else:
            into[name] = value


def worker_probe(boxes: Boxes, rig: Rig, cell: dict, target: Target) -> tuple[list[int], int]:
    """The sorted worker pids and the log bytes of the target."""
    lines = boxes.run(rig.server, box_cmd("target.sh", "probe", cell["key"], target.server)).splitlines()
    return [int(pid) for pid in lines[0].split()], int(lines[1])


def start_target(boxes: Boxes, rig: Rig, target: Target, cell: dict, cell_dir: Path, processes: int, rapira: dict) -> None:
    """Start the target and copy its rendered configs."""
    cmd = box_cmd("target.sh", "start", cell["key"], target.server, processes, binary_dir(target, rapira), *target.start)
    try:
        out = boxes.run(rig.server, cmd)
    except SshError as exc:
        raise CellVoid(f"start failed: {str(exc).splitlines()[-1]}") from exc
    for line in out.splitlines():
        if line.startswith("config="):
            remote = line.removeprefix("config=")
            name = Path(remote).name
            suffix = name.removeprefix(cell["key"] + ".")
            boxes.copy_from(rig.server, remote, cell_dir / f"config.{suffix}")


def measure(boxes: Boxes, rig: Rig, suite: Suite, plan: dict, target: Target, cell: dict, cell_dir: Path, rapira: dict) -> None:
    """Run the cell sequence of spec 5.3 from the start of the target to the probe after the failing stage."""
    start_target(boxes, rig, target, cell, cell_dir, plan["processes"], rapira)
    pids_before, log_before = worker_probe(boxes, rig, cell, target)
    url = f"http://{rig.server.private_ip}:{PORT}{target.url}"
    results = boxes.run_many([(loader, probe_cmd(target, url)) for loader in rig.loaders], timeout=SNAPSHOT_TIMEOUT_S)
    for loader, out in zip(rig.loaders, results):
        if isinstance(out, SshError):
            raise CellVoid(f"probe mismatch on {loader.name}")
    rates = plan["rates"][target.app]
    epoch = time.time() + LEAD_S
    warmup = [(loader, load_cmd(target, url, epoch, rates[0] // len(rig.loaders), plan, WARMUP_S)) for loader in rig.loaders]
    boxes.run_many(warmup, timeout=LEAD_S + WARMUP_S + LOAD_SLACK_S)

    failed = False
    for index, rate in enumerate(rates):
        stage = run_stage(boxes, rig, suite, plan, target, cell, cell_dir, index, rate)
        cell["stages"].append(stage)
        add_stage_flags(cell["flags"], stage["flags"])
        if not stage["pass"]:
            failed = True
            break
    if failed:
        try:
            boxes.run(rig.loaders[0], probe_cmd(target, url), timeout=SNAPSHOT_TIMEOUT_S)
        except SshError:
            cell["flags"]["died"] = True
    pids_after, log_after = worker_probe(boxes, rig, cell, target)
    cell["flags"].update(cell_flags(pids_before, pids_after, log_before, log_after))


def void(cell: dict, reason: str) -> None:
    if cell["status"] == "ok":
        cell["status"] = "void"
        cell["reason"] = reason


def stop_target(boxes: Boxes, rig: Rig, target: Target, cell: dict, cell_dir: Path) -> None:
    """Stop the target, keep its WARN and ERROR lines, and apply the rapira log rule."""
    try:
        boxes.run(rig.server, box_cmd("target.sh", "stop", cell["key"], target.server))
        lines = boxes.run(rig.server, box_cmd("target.sh", "log", cell["key"], target.server))
    except SshError as exc:
        void(cell, f"stop failed: {str(exc).splitlines()[-1]}")
        return
    (cell_dir / "server.log").write_text(lines)
    count = len(lines.splitlines())
    if count and target.server in RAPIRA_SERVERS:
        void(cell, f"server log: {count} warn or error lines")


def run_cell(boxes: Boxes, rig: Rig, suite: Suite, plan: dict, target: Target, cell: dict, cell_dir: Path, rapira: dict) -> None:
    cell_dir.mkdir(parents=True)
    try:
        measure(boxes, rig, suite, plan, target, cell, cell_dir, rapira)
    except CellVoid as exc:
        void(cell, str(exc))
    except SshError as exc:
        void(cell, f"ssh: {str(exc).splitlines()[0]}")
    finally:
        stop_target(boxes, rig, target, cell, cell_dir)
    if cell["status"] == "ok":
        numbers = cell_numbers(cell["stages"])
        cell["held"] = numbers["held"]
        cell["peak"] = numbers["peak"]
        cell["unloaded"] = numbers["unloaded"]
        if numbers["ladder_exhausted"]:
            cell["flags"]["ladder_exhausted"] = True


def run_suite(rig: Rig, suite: Suite, boxes: Boxes, out_dir: Path, *, processes: int, run_id: str, rapira: dict,
              servers: dict, apps: dict, loader_threads: int) -> Path:
    """Run every planned cell and write run.json. Returns the run.json path."""
    plan = plan_run(rig, suite, processes=processes, loader_threads=loader_threads)
    ensure_ttl(rig.hosts, len(plan["cells"]) * (8 * suite.stage_s + 60) + 300)
    run_dir = out_dir / run_id
    (run_dir / "raw").mkdir(parents=True)
    facts = host_facts(boxes, rig)
    server_facts = facts[rig.server.name]
    run_file = RunFile(
        run_id=run_id,
        suite=suite_record(suite),
        rig={
            "server_type": rig.server_type,
            "loader_type": rig.loader_type,
            "loader_count": len(rig.loaders),
            "az": server_facts.get("az"),
            "ami": rig.ami_id,
            "kernel": server_facts.get("kernel"),
            "placement_group": server_facts.get("placement_group"),
            "server_instance_id": server_facts.get("instance_id"),
        },
        rapira=rapira,
        servers=servers,
        apps=apps,
        loaders=[
            {
                "name": loader.name,
                "instance_id": facts[loader.name].get("instance_id"),
                "private_ip": loader.private_ip,
                "kernel": facts[loader.name].get("kernel"),
                "wrk2": facts[loader.name].get("wrk2"),
                "k6": facts[loader.name].get("k6"),
            }
            for loader in rig.loaders
        ],
        ladder={
            "stages": plan["rates"],
            "stage_s": suite.stage_s,
            "ratio": RATIO,
            "max_stages": MAX_STAGES,
            "pass_tolerance": PASS_TOLERANCE,
            "connections": suite.connections,
            "conns_per_loader": plan["conns_per_loader"],
            "loader_threads": loader_threads,
            "lead_s": LEAD_S,
            "warmup_s": WARMUP_S,
        },
        processes=processes,
        plan=plan["keys"],
        smoke=suite.smoke,
        started=utc_now(),
    )
    path = run_dir / "run.json"
    try:
        for (round_no, target), key in zip(plan["cells"], plan["keys"]):
            print(f"==> {key}")
            cell = {
                "key": key, "target": target_record(target), "round": round_no, "status": "ok", "flags": {},
                "held": None, "peak": None, "unloaded": None, "stages": [],
            }
            try:
                run_cell(boxes, rig, suite, plan, target, cell, run_dir / "raw" / key, rapira)
            except BaseException:
                if cell["status"] == "ok":
                    cell["status"] = "incomplete"
                    cell["reason"] = "interrupted"
                raise
            finally:
                run_file.add_cell(cell)
    finally:
        run_file.finish(utc_now())
        run_file.write(path)
    return path
