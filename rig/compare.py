"""Deltas of the achieved rate, the p99, and the RSS between two run files."""

from rig.report import mib, ms, num, rows

# Run fields that must match for a fair comparison, as (section, key). An empty section is the top level.
IDENTITY = (
    ("rig", "server_type"),
    ("rig", "loader_type"),
    ("rig", "loader_count"),
    ("", "processes"),
    ("suite", "rates"),
    ("suite", "duration_s"),
)


def identity_diffs(a: dict, b: dict) -> list[str]:
    diffs = []
    for section, key in IDENTITY:
        va = a[section][key] if section else a[key]
        vb = b[section][key] if section else b[key]
        if va != vb:
            name = f"{section}.{key}" if section else key
            diffs.append(f"{name} differs: {va} vs {vb}")
    return diffs


def delta(va, vb):
    """The change from va to vb in percent, or "-" when va is 0."""
    if va:
        return f"{100.0 * (vb - va) / va:+.1f}%"
    return "-"


def compare(a: dict, b: dict, *, force: bool = False) -> tuple[str, int]:
    """The delta table and the exit status: 1 when the rig identity differs and `force` is false."""
    diffs = identity_diffs(a, b)
    if diffs and not force:
        lines = [f"refused: {d}" for d in diffs] + ["Use --force to compare anyway."]
        return "\n".join(lines) + "\n", 1
    lines = [f"forced: {d}" for d in diffs] + [f"a: {a['id']}", f"b: {b['id']}", ""]
    rows_a = {r["name"]: r for r in rows(a)}
    rows_b = {r["name"]: r for r in rows(b)}
    names = list(rows_a) + [n for n in rows_b if n not in rows_a]
    w = max(len(n) for n in names) if names else 0
    for name in names:
        ra = rows_a.get(name)
        rb = rows_b.get(name)
        if rb is None:
            lines.append(f"{name:<{w}}  only in a")
            continue
        if ra is None:
            lines.append(f"{name:<{w}}  only in b")
            continue
        lines.append(
            f"{name:<{w}}  req/s {num(ra['achieved'])} -> {num(rb['achieved'])} {delta(ra['achieved'], rb['achieved'])}"
            f"  p99 {ms(ra['p99'])} -> {ms(rb['p99'])} {delta(ra['p99'], rb['p99'])}"
            f"  rss {mib(ra['rss_kb'])} -> {mib(rb['rss_kb'])} {delta(ra['rss_kb'], rb['rss_kb'])}"
        )
    return "\n".join(lines) + "\n", 0
