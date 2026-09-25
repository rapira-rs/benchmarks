"""The cell sequence of one suite run: one constant-rate stage per target."""

import hashlib
import json
import shlex
import time
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from rig import ssh
from rig.flags import cell_flags, cpu_pct, ena_delta, keepalive_flag, parse_snapshot, stage_flags, stage_void
from rig.merge import merge, parse_result
from rig.registry import Suite, SuiteError, Target, cell_key, plan_cells
from rig.rig import Rig, ensure_ttl
from rig.runfile import RunFile
from rig.ssh import Host, SshError

PORT = 8080
LEAD_S = 3
# A cell holds its rate when it achieves this share of it without an error.
HELD_TOLERANCE = 0.95
BOX = f"bash {ssh.RIG_DIR}/box"
BENCH_DIR = "/opt/bench"
# ssh slack over the lead time and the load time of one load call.
LOAD_SLACK_S = 60
SNAPSHOT_TIMEOUT_S = 30
# The start, the probes, the stop, and the drain of one cell, for the TTL estimate.
CELL_OVERHEAD_S = 60
TTL_MARGIN_S = 300
# Sleep until the wall clock time in argv[1].
WAIT_PY = "import sys, time; time.sleep(max(0.0, float(sys.argv[1]) - time.time()))"
FACTS_CMD = (
    "echo kernel=$(uname -r); "
    "echo instance_id=$(cat /sys/devices/virtual/dmi/id/board_asset_tag); "
    "t=$(curl -s -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' http://169.254.169.254/latest/api/token); "
    "m=http://169.254.169.254/latest/meta-data/placement; "
    "echo az=$(curl -s -H \"X-aws-ec2-metadata-token: $t\" $m/availability-zone); "
    "echo placement_group=$(curl -s -H \"X-aws-ec2-metadata-token: $t\" $m/group-name)"
)
# Provisioning writes the wrk2 commit and the h2load version to loader.json.
TOOLS_CMD = (
    "python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); "
    "print(\"wrk2=\" + d[\"wrk2_commit\"]); print(\"h2load=\" + d[\"h2load_version\"])' "
    f"{BENCH_DIR}/loader.json"
)
# The measured fields of a cell. A cell that is not ok has null values here.
NUMBER_KEYS = ("rate", "achieved_rps", "successful_rps", "errors", "latency_us", "rss_kb", "held", "cpu")
LATENCY_KEYS = ("p50", "p90", "p99", "p999", "max")


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
    http1 = suite.connections["http1"]
    # wrk2 divides the connections of a process over its threads and drops the remainder.
    if http1 % (loaders * loader_threads) != 0:
        raise SuiteError(f"connections {http1} is not a multiple of {loaders} loaders x {loader_threads} threads")
    cells = plan_cells(suite)
    return {
        "cells": cells,
        "keys": [cell_key(round_no, target) for round_no, target in cells],
        "conns_per_loader": {proto: count // loaders for proto, count in suite.connections.items()},
        "loader_threads": loader_threads,
        "processes": processes,
    }


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def request_args(target: Target) -> list[str]:
    """METHOD, BODY_FILE, and HEADER arguments of probe.sh and load.sh. A relative file is under the staged rig."""
    body = target.body or "-"
    return [target.method, body, *(f"{name}: {value}" for name, value in target.headers)]


def box_cmd(script: str, *args: object) -> str:
    return " ".join([f"{BOX}/{script}", *(shlex.quote(str(arg)) for arg in args)])


def probe_cmd(target: Target, url: str) -> str:
    return box_cmd("probe.sh", url, target.expect, target.proto, *request_args(target))


def load_cmd(target: Target, url: str, epoch: float, rate: int, plan: dict, suite: Suite) -> str:
    """One load.sh call of one loader: h2load for the grpc proto, wrk2 for http1."""
    conns = plan["conns_per_loader"][target.proto]
    if target.proto == "grpc":
        return box_cmd(
            "load.sh", "h2load", f"{epoch:.3f}", rate, plan["loader_threads"], conns, suite.grpc_streams,
            suite.warmup_s, suite.duration_s, url, target.body,
        )
    return box_cmd(
        "load.sh", "wrk2", f"{epoch:.3f}", rate, plan["loader_threads"], conns, suite.warmup_s, suite.duration_s, url,
        *request_args(target),
    )


def counted_s(target: Target, suite: Suite) -> int:
    """The seconds that the requests count of the tool covers: wrk2 counts the whole run, h2load the measured window."""
    if target.proto == "grpc":
        return suite.duration_s
    return suite.warmup_s + suite.duration_s


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
    """SHA-256 of every staged file under apps/, the lock files and the expected bodies included."""
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ssh.tree_files(root)
        if name.startswith("apps/")
    }


def suite_record(suite: Suite, path: Path) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "name": suite.name,
        "file_sha256": digest,
        "rounds": suite.rounds,
        "warmup_s": suite.warmup_s,
        "duration_s": suite.duration_s,
        "rates": dict(suite.rates),
        "connections": dict(suite.connections),
    }


def target_record(target: Target) -> dict:
    return {**asdict(target), "start": list(target.start), "headers": dict(target.headers)}


def at_time(at: float, cmd: str) -> str:
    """cmd after a wait until the wall clock time at."""
    return f"python3 -c {shlex.quote(WAIT_PY)} {at:.3f} && {cmd}"


def sample_jobs(rig: Rig, epoch: float, duration_s: int) -> list[tuple[str, Host, str]]:
    """Snapshot jobs of every box at the start of the measured window and 0.5 s after its end.

    The server snapshots count the connections on PORT.
    """
    jobs = []
    for mark, at in (("begin", epoch), ("end", epoch + duration_s + 0.5)):
        jobs.append((mark, rig.server, at_time(at, box_cmd("snapshot.sh", PORT))))
        jobs += [(mark, loader, at_time(at, box_cmd("snapshot.sh"))) for loader in rig.loaders]
    return jobs


def run_stage(boxes: Boxes, rig: Rig, suite: Suite, plan: dict, target: Target, cell: dict, cell_dir: Path) -> None:
    """Run the load from every loader, read the RSS, and fill the numbers of the cell."""
    server = rig.server
    url = f"http://{server.private_ip}:{PORT}{target.url}"
    rate = suite.rates[target.app]
    per_loader = rate // len(rig.loaders)
    epoch = time.time() + LEAD_S
    loads = [(loader, load_cmd(target, url, epoch, per_loader, plan, suite)) for loader in rig.loaders]
    samples = sample_jobs(rig, epoch + suite.warmup_s, suite.duration_s)
    timeout = LEAD_S + suite.warmup_s + suite.duration_s + LOAD_SLACK_S
    results = boxes.run_many(loads + [(host, cmd) for _, host, cmd in samples], timeout=timeout)

    taken = {}
    with (cell_dir / "snapshots.txt").open("w") as log:
        for (mark, host, _), out in zip(samples, results[len(loads):]):
            if isinstance(out, SshError):
                raise out
            log.write(f"# {mark} {host.name}\n{out}")
            taken[mark, host.name] = out
    before = {host.name: parse_snapshot(taken["begin", host.name]) for host in rig.hosts}
    after = {host.name: parse_snapshot(taken["end", host.name]) for host in rig.hosts}

    records = {}
    for loader, out in zip(rig.loaders, results):
        text = str(out) if isinstance(out, SshError) else out
        (cell_dir / f"load-{loader.name}.txt").write_text(text)
        try:
            records[loader.name] = None if isinstance(out, SshError) else parse_result(out, loader.name)
        except ValueError as exc:
            raise CellVoid(f"{loader.name}: {exc}") from exc
    try:
        merged = merge(records, counted_s(target, suite))
    except ValueError as exc:
        raise CellVoid(str(exc)) from exc
    loader_ena = {loader.name: ena_delta(before[loader.name], after[loader.name]) for loader in rig.loaders}
    throttled = stage_void(loader_ena)
    if throttled:
        raise CellVoid(throttled)

    rss_kb = int(boxes.run(server, box_cmd("target.sh", "mem", cell["key"], target.server), timeout=SNAPSHOT_TIMEOUT_S))
    held = merged.achieved_rps >= HELD_TOLERANCE * rate and not any(merged.errors.values())
    server_busy = cpu_pct(before[server.name], after[server.name])
    loader_busy = {loader.name: cpu_pct(before[loader.name], after[loader.name]) for loader in rig.loaders}
    flags = stage_flags(server_busy, loader_busy, held)
    server_ena = ena_delta(before[server.name], after[server.name])
    if server_ena:
        flags["ena_throttled"] = server_ena
    flags.update(keepalive_flag(before[server.name], after[server.name], suite.connections[target.proto]))
    cell.update({
        "rate": rate,
        "achieved_rps": merged.achieved_rps,
        "successful_rps": merged.successful_rps,
        "errors": dict(merged.errors),
        "latency_us": {key: merged.latency_us[key] for key in LATENCY_KEYS},
        "rss_kb": rss_kb,
        "held": held,
        "cpu": {"server_busy": server_busy, "loader_busy": max(loader_busy.values())},
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
    })
    cell["flags"].update(flags)


def worker_probe(boxes: Boxes, rig: Rig, cell: dict, target: Target) -> tuple[list[int], int]:
    """The sorted worker pids and the log bytes of the target."""
    lines = boxes.run(rig.server, box_cmd("target.sh", "probe", cell["key"], target.server)).splitlines()
    return [int(pid) for pid in lines[0].split()], int(lines[1])


def start_target(boxes: Boxes, rig: Rig, target: Target, cell: dict, cell_dir: Path, processes: int, rapira: dict) -> None:
    """Start the target and copy its rendered configs."""
    cmd = box_cmd("target.sh", "start", cell["key"], target.server, processes, rapira["dir"], *target.start)
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
    """Run the cell sequence of spec section 5.1 from the start of the target to the probe after the stage."""
    start_target(boxes, rig, target, cell, cell_dir, plan["processes"], rapira)
    pids_before, log_before = worker_probe(boxes, rig, cell, target)
    url = f"http://{rig.server.private_ip}:{PORT}{target.url}"
    results = boxes.run_many([(loader, probe_cmd(target, url)) for loader in rig.loaders], timeout=SNAPSHOT_TIMEOUT_S)
    for loader, out in zip(rig.loaders, results):
        if isinstance(out, SshError):
            raise CellVoid(f"probe mismatch on {loader.name}: {str(out).splitlines()[-1]}")
    run_stage(boxes, rig, suite, plan, target, cell, cell_dir)
    try:
        boxes.run(rig.loaders[0], probe_cmd(target, url), timeout=SNAPSHOT_TIMEOUT_S)
    except SshError:
        cell["flags"]["died"] = True
    pids_after, log_after = worker_probe(boxes, rig, cell, target)
    cell["flags"].update(cell_flags(pids_before, pids_after, log_before, log_after))


def clear_numbers(cell: dict) -> None:
    for key in NUMBER_KEYS:
        cell[key] = None


def void(cell: dict, reason: str) -> None:
    """Exclude the cell from every number. The first reason wins."""
    if cell["status"] == "ok":
        cell["status"] = "void"
        cell["reason"] = reason
        clear_numbers(cell)


def stop_target(boxes: Boxes, rig: Rig, target: Target, cell: dict, cell_dir: Path) -> None:
    """Stop the target, keep its WARN and ERROR lines, and void the cell when there are any."""
    try:
        boxes.run(rig.server, box_cmd("target.sh", "stop", cell["key"], target.server))
        lines = boxes.run(rig.server, box_cmd("target.sh", "log", cell["key"], target.server))
    except SshError as exc:
        void(cell, f"stop failed: {str(exc).splitlines()[-1]}")
        return
    (cell_dir / "server.log").write_text(lines)
    count = len(lines.splitlines())
    if count:
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


def new_cell(key: str, target: Target, round_no: int) -> dict:
    cell = {"key": key, "target": target_record(target), "round": round_no, "status": "ok", "flags": {}}
    clear_numbers(cell)
    cell["loaders"] = []
    return cell


def run_suite(rig: Rig, suite: Suite, boxes: Boxes, out_dir: Path, *, suite_path: Path, processes: int, run_id: str,
              rapira: dict, servers: dict, apps: dict, loader_threads: int) -> Path:
    """Run every planned cell and write run.json. Returns the run.json path."""
    plan = plan_run(rig, suite, processes=processes, loader_threads=loader_threads)
    ensure_ttl(rig.hosts, len(plan["cells"]) * (suite.warmup_s + suite.duration_s + CELL_OVERHEAD_S) + TTL_MARGIN_S)
    run_dir = out_dir / run_id
    (run_dir / "raw").mkdir(parents=True)
    facts = host_facts(boxes, rig)
    server_facts = facts[rig.server.name]
    run_file = RunFile(
        run_id=run_id,
        suite=suite_record(suite, suite_path),
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
                "h2load": facts[loader.name].get("h2load"),
            }
            for loader in rig.loaders
        ],
        processes=processes,
        plan=plan["keys"],
        smoke=suite.smoke,
        started=utc_now(),
    )
    path = run_dir / "run.json"
    try:
        for (round_no, target), key in zip(plan["cells"], plan["keys"]):
            print(f"==> {key}")
            cell = new_cell(key, target, round_no)
            try:
                run_cell(boxes, rig, suite, plan, target, cell, run_dir / "raw" / key, rapira)
            except BaseException:
                if cell["status"] == "ok":
                    cell["status"] = "incomplete"
                    cell["reason"] = "interrupted"
                    clear_numbers(cell)
                raise
            finally:
                run_file.add_cell(cell)
    finally:
        run_file.finish(utc_now())
        run_file.write(path)
    return path
