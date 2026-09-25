"""Stage rates, the pass rule, and the held, peak, and unloaded numbers of a cell."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rig.merge import Merged

RATIO = 2
MAX_STAGES = 20
PASS_TOLERANCE = 0.95

# The error counters in the order of the fail reasons, with the reason label.
_ERROR_REASONS = (
    ("status", "status errors"),
    ("connect", "connect errors"),
    ("read", "read errors"),
    ("write", "write errors"),
    ("timeout", "timeouts"),
    ("dropped", "dropped iterations"),
)


def stage_rates(floor: int) -> list[int]:
    return [floor * RATIO**i for i in range(MAX_STAGES)]


def evaluate_stage(rate: int, merged: Merged) -> tuple[bool, str | None]:
    if merged.achieved_rps < PASS_TOLERANCE * rate:
        return False, f"achieved {int(merged.achieved_rps)} req/s under {PASS_TOLERANCE:.0%} of {rate}"
    for key, label in _ERROR_REASONS:
        if merged.errors[key]:
            return False, f"{label}: {merged.errors[key]}"
    return True, None


def cell_numbers(stages: list[dict]) -> dict:
    held = None
    peak = None
    for index, stage in enumerate(stages):
        if stage["pass"]:
            held = {"rate": stage["rate"], "stage": index}
        else:
            peak = stage["merged"]["successful_rps"]
            break
    unloaded = None
    if stages:
        latency = stages[0]["latency_us"]
        unloaded = {"p50": latency["p50"], "p99": latency["p99"]}
    exhausted = len(stages) == MAX_STAGES and all(stage["pass"] for stage in stages)
    return {"held": held, "peak": peak, "unloaded": unloaded, "ladder_exhausted": exhausted}
