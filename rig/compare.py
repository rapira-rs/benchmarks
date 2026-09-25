"""Deltas of held and peak between two run files."""

from rig.report import pct, rows

# Run fields that must match for a fair comparison, as (section, key). An empty section is the top level.
IDENTITY = (
    ("rig", "server_type"),
    ("rig", "loader_type"),
    ("rig", "loader_count"),
    ("", "processes"),
    ("ladder", "stage_s"),
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


def num(value, digits):
    return f"{value:.{digits}f}" if value is not None else "-"


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
        delta = "-"
        if ra["peak"] and rb["peak"] is not None:
            delta = f"{100.0 * (rb['peak'] - ra['peak']) / ra['peak']:+.1f}%"
        lines.append(
            f"{name:<{w}}  held {num(ra['held'], 0)} -> {num(rb['held'], 0)}"
            f"  peak {num(ra['peak'], 1)} -> {num(rb['peak'], 1)}  {delta}"
            f"  spread {pct(ra['spread'])} / {pct(rb['spread'])}"
        )
    return "\n".join(lines) + "\n", 0
