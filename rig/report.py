"""The text table of one run file."""

import statistics


def flag_text(name, value):
    """`name` for a true flag, `name(value)` for a flag with a value."""
    if value is True:
        return name
    if isinstance(value, dict):
        return f"{name}({','.join(f'{k}={v}' for k, v in value.items())})"
    return f"{name}({value})"


def target_row(cells: list[dict]) -> dict:
    """The medians over the ok cells of one target, `held` when every cell held, and the union of the flags."""
    return {
        "name": cells[0]["target"]["name"],
        "achieved": statistics.median(c["achieved_rps"] for c in cells),
        "held": all(c["held"] for c in cells),
        "p99": statistics.median(c["latency_us"]["p99"] for c in cells),
        "p50": statistics.median(c["latency_us"]["p50"] for c in cells),
        "rss_kb": statistics.median(c["rss_kb"] for c in cells),
        "n": len(cells),
        "flags": sorted({flag_text(k, v) for c in cells for k, v in c["flags"].items()}),
    }


def rows(run: dict) -> list[dict]:
    """One row per target with ok cells, in the order of the first cell of each target."""
    groups = {}
    for cell in run["cells"]:
        if cell["status"] == "ok":
            groups.setdefault(cell["target"]["name"], []).append(cell)
    return [target_row(cells) for cells in groups.values()]


def num(value):
    return f"{value:.0f}"


def ms(us):
    return f"{us / 1000:.2f}ms"


def mib(kb):
    return f"{kb / 1024:.1f}"


def render(run: dict) -> tuple[str, int]:
    """The report text and the exit status: 1 when the run is incomplete."""
    table = rows(run)
    w = max([len("target")] + [len(r["name"]) for r in table]) + 2
    hdr = f"{'target':<{w}} {'req/s':>8} {'held':>4} {'p99':>9} {'p50':>9} {'RSS MiB':>8} {'n':>3}  flags"
    lines = [hdr, "-" * len(hdr)]
    for r in table:
        held = "yes" if r["held"] else "no"
        lines.append(
            f"{r['name']:<{w}} {num(r['achieved']):>8} {held:>4} {ms(r['p99']):>9} {ms(r['p50']):>9} "
            f"{mib(r['rss_kb']):>8} {r['n']:>3}  {','.join(r['flags']) or '-'}"
        )
    voided = [c for c in run["cells"] if c["status"] == "void"]
    if voided:
        lines += ["", "VOIDED cells (excluded from every number above):"]
        lines += [f"  {c['key']}: {c['reason']}" for c in voided]
    if run["status"] != "complete":
        lines += ["", f"INCOMPLETE RUN: {'; '.join(run['reasons'])}.", "Do not publish these tables."]
        return "\n".join(lines) + "\n", 1
    return "\n".join(lines) + "\n", 0
