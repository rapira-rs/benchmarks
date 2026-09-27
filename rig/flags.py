"""Snapshot parsing and the flag and void rules of a stage and a cell."""

import statistics
from dataclasses import dataclass

GENERATOR_BUSY = 85
SERVER_BUSY = 90
# A loader above this busy percent flags loader_busy.
LOADER_BUSY = 70
# A wrk2 thread with a calibration mean more than this percent above the median of the other threads of its loader.
SKEW_PCT = 15
LOG_GROWTH_BYTES = 65536


@dataclass(frozen=True)
class Snapshot:
    """One `box/snapshot.sh` output. `conns` is None on a loader."""

    cpu: tuple[int, int]
    ena: dict[str, int]
    conns: tuple[int, int] | None


def parse_snapshot(text: str) -> Snapshot:
    """Read the cpu, ena, and conns lines of a snapshot."""
    cpu = None
    ena = {}
    conns = None
    for line in text.splitlines():
        parts = line.split()
        if parts[0] == "cpu":
            cpu = (int(parts[1]), int(parts[2]))
        elif parts[0] == "ena":
            ena[parts[1]] = int(parts[2])
        elif parts[0] == "conns":
            conns = (int(parts[1]), int(parts[2]))
    return Snapshot(cpu=cpu, ena=ena, conns=conns)


def cpu_pct(before: Snapshot, after: Snapshot) -> int:
    """Busy percent over the window. Integer division drops the fraction."""
    busy = after.cpu[0] - before.cpu[0]
    total = after.cpu[1] - before.cpu[1]
    if total == 0:
        return 0
    return 100 * busy // total


def ena_delta(before: Snapshot, after: Snapshot) -> dict[str, int]:
    """The counters that changed during the window, with the change."""
    delta = {}
    for name, value in after.ena.items():
        change = value - before.ena.get(name, 0)
        if change:
            delta[name] = change
    return delta


def stage_flags(stage: str, server_busy: int, loader_busy: dict[str, int], held: bool) -> dict:
    """Review flags of one stage of the kind `stage`.

    A rate stage that did not hold names the bottleneck. A cap stage that held is not a capacity.
    """
    flags = {}
    if stage == "cap":
        if held:
            flags["not_saturated"] = True
    elif not held and server_busy < SERVER_BUSY:
        if any(busy >= GENERATOR_BUSY for busy in loader_busy.values()):
            flags["generator_bound"] = True
        else:
            flags["server_unsaturated"] = True
    busiest = max(loader_busy.values())
    if busiest > LOADER_BUSY:
        flags["loader_busy"] = busiest
    return flags


def skew_flag(calibration_ms: dict[str, list[float]]) -> dict:
    """Flag the first wrk2 thread that calibrated more than SKEW_PCT above the median of the other threads of its loader."""
    for loader, means in calibration_ms.items():
        if len(means) < 2:
            continue
        for index, mean in enumerate(means):
            median = statistics.median(means[:index] + means[index + 1:])
            if mean > median * (1 + SKEW_PCT / 100):
                return {"loader_skew": {"loader": loader, "thread_ms": mean, "median_ms": median}}
    return {}


def stage_void(loader_ena: dict[str, dict[str, int]]) -> str | None:
    """The void reason when the network shaped a loader during the stage."""
    for loader, delta in loader_ena.items():
        if delta:
            counter, change = next(iter(delta.items()))
            return f"loader {loader} throttled: {counter}={change}"
    return None


def keepalive_flag(before: Snapshot, after: Snapshot, connections: int) -> dict:
    """Flag a server TIME-WAIT growth above the total connection count."""
    delta = after.conns[1] - before.conns[1]
    if delta > connections:
        return {"keepalive_broken": {"time_wait_delta": delta, "connections": connections}}
    return {}


def cell_flags(pids_before: list[int], pids_after: list[int], log_before: int, log_after: int) -> dict:
    """Review flags of one cell from the probes before and after the load."""
    flags = {}
    if sorted(pids_before) != sorted(pids_after):
        flags["worker_churn"] = True
    growth = log_after - log_before
    if growth > LOG_GROWTH_BYTES:
        flags["log_growth"] = growth
    return flags
