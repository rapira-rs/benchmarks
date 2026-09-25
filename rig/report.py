"""Text tables of one run file."""

import statistics


def median_of(values):
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def flag_text(name, value):
    """`name` for a true flag, `name(value)` for a flag with a value."""
    if value is True:
        return name
    if isinstance(value, dict):
        return f"{name}({','.join(f'{k}={v}' for k, v in value.items())})"
    return f"{name}({value})"


def target_row(cells: list[dict]) -> dict:
    """The medians, the spread, the flags, and the fail reasons of the ok cells of one target."""
    held = [c for c in cells if c["held"] is not None]
    held_rate = statistics.median_low(c["held"]["rate"] if c["held"] is not None else 0 for c in cells)
    peaks = [c["peak"] for c in cells if c["peak"] is not None]
    peak = statistics.median(peaks) if peaks else None
    fails = []
    for c in cells:
        last = c["stages"][-1]
        if not last["pass"] and last["fail_reason"] not in fails:
            fails.append(last["fail_reason"])
    return {
        "name": cells[0]["target"]["name"],
        "app": cells[0]["target"]["app"],
        "held": held_rate or None,
        "peak": peak,
        "p99_held": median_of(c["stages"][c["held"]["stage"]]["latency_us"]["p99"] for c in held) if held_rate else None,
        "unloaded_p50": median_of(c["unloaded"]["p50"] for c in cells),
        "n": len(cells),
        "spread": 100.0 * (max(peaks) - min(peaks)) / peak if peak and len(peaks) > 1 else None,
        "flags": sorted({flag_text(k, v) for c in cells for k, v in c["flags"].items()}),
        "fails": fails,
    }


def rows(run: dict) -> list[dict]:
    """One row per target with ok cells, sorted by app, then by peak from high to low."""
    groups = {}
    for cell in run["cells"]:
        if cell["status"] == "ok":
            groups.setdefault(cell["target"]["name"], []).append(cell)
    out = [target_row(cells) for cells in groups.values()]
    out.sort(key=lambda r: (r["app"], -(r["peak"] or 0)))
    return out


def num(value):
    return f"{value:.0f}" if value is not None else "-"


def ms(us):
    return f"{us / 1000:.2f}ms" if us is not None else "-"


def pct(value):
    return f"{value:.1f}%" if value is not None else "-"


def render(run: dict) -> tuple[str, int]:
    """The report text and the exit status: 1 when the run is incomplete."""
    table = rows(run)
    w = max([len("target")] + [len(r["name"]) for r in table]) + 2
    flags = {r["name"]: ",".join(r["flags"]) or "-" for r in table}
    fw = max([len("flags")] + [len(f) for f in flags.values()])
    hdr = f"{'target':<{w}} {'held req/s':>10} {'peak req/s':>10} {'p99 at held':>11} {'unloaded p50':>12} {'n':>3} {'spread':>7}  {'flags':<{fw}}  fail"
    lines = [hdr, "-" * len(hdr)]
    for r in table:
        fail = "; ".join(r["fails"]) or "-"
        lines.append(
            f"{r['name']:<{w}} {num(r['held']):>10} {num(r['peak']):>10} {ms(r['p99_held']):>11} "
            f"{ms(r['unloaded_p50']):>12} {r['n']:>3} {pct(r['spread']):>7}  {flags[r['name']]:<{fw}}  {fail}"
        )
    voided = [c for c in run["cells"] if c["status"] == "void"]
    if voided:
        lines += ["", "VOIDED cells (excluded from every number above):"]
        lines += [f"  {c['key']}: {c['reason']}" for c in voided]
    if run["status"] != "complete":
        lines += ["", f"INCOMPLETE RUN: {'; '.join(run['reasons'])}.", "Do not publish these tables."]
        return "\n".join(lines) + "\n", 1
    return "\n".join(lines) + "\n", 0
