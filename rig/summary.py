"""The paired-delta summary of one run: per target and measure, the paired deltas of the new build against the base build."""

import statistics

MEASURES = ("capacity", "p99", "rss")

# The stage kind of the cells that give each measure.
MEASURE_STAGE = {"capacity": "cap", "p99": "rate", "rss": "rate"}


def measure_value(measure: str, cell: dict) -> float:
    """The number of one ok cell for one measure."""
    if measure == "capacity":
        return cell["achieved_rps"]
    if measure == "p99":
        return cell["latency_us"]["p99"]
    return cell["rss_kb"]


def counts(measure: str, base: dict, new: dict) -> bool:
    """True when the pair counts for the measure. A p99 pair with loader_skew on either cell does not count."""
    return measure != "p99" or not ("loader_skew" in base["flags"] or "loader_skew" in new["flags"])


def measure_entry(measure: str, pairs: list[tuple[dict, dict]]) -> dict:
    """The entry of one measure over the pairs of one target and one stage kind."""
    counted = [(b, n) for b, n in pairs if counts(measure, b, n)]
    if not counted:
        return {"pairs": 0, "delta_pct": None, "min_pct": None, "max_pct": None, "base": None, "new": None, "flags": []}
    base = [measure_value(measure, b) for b, _ in counted]
    new = [measure_value(measure, n) for _, n in counted]
    deltas = [100.0 * (nv - bv) / bv for bv, nv in zip(base, new)]
    return {
        "pairs": len(counted),
        "delta_pct": statistics.median(deltas),
        "min_pct": min(deltas),
        "max_pct": max(deltas),
        "base": statistics.median(base),
        "new": statistics.median(new),
        "flags": sorted({name for pair in counted for c in pair for name in c["flags"]}),
    }


def summarize(cells: list[dict]) -> dict[str, dict[str, dict]]:
    """One entry per target and measure. A pair is the ok base cell and the ok new cell of one round, target and stage kind."""
    names = []
    builds = {}
    for cell in cells:
        name = cell["target"]["name"]
        if name not in names:
            names.append(name)
        if cell["status"] == "ok":
            builds.setdefault((name, cell["stage"], cell["round"]), {})[cell["build"]] = cell
    return {
        name: {
            measure: measure_entry(measure, [
                (pair["base"], pair["new"])
                for (target, stage, _), pair in builds.items()
                if target == name and stage == MEASURE_STAGE[measure] and "base" in pair and "new" in pair
            ])
            for measure in MEASURES
        }
        for name in names
    }
