"""The summary table of one run file."""

from rig.summary import MEASURES


def num(value):
    return f"{value:.0f}"


def ms(us):
    return f"{us / 1000:.2f}"


def mib(kb):
    return f"{kb / 1024:.1f}"


# The unit and the value text of each measure. The run file keeps the p99 in us and the RSS in KiB.
UNITS = {"capacity": ("req/s", num), "p99": ("ms", ms), "rss": ("MiB", mib)}


def pct(value):
    return "-" if value is None else f"{value:+.1f}%"


def value_text(measure, value):
    return "-" if value is None else UNITS[measure][1](value)


def render(run: dict) -> tuple[str, int]:
    """The report text and the exit status: 1 when the run is not complete."""
    summary = run["summary"]
    w = max([len("target")] + [len(name) for name in summary]) + 2
    hdr = f"{'target':<{w}} {'measure':<8} {'pairs':>5} {'delta':>7} {'min':>7} {'max':>7} {'base':>9} {'new':>9} {'unit':<5}  flags"
    lines = [hdr, "-" * len(hdr)]
    for name, measures in summary.items():
        for measure in MEASURES:
            e = measures[measure]
            lines.append(
                f"{name:<{w}} {measure:<8} {e['pairs']:>5} {pct(e['delta_pct']):>7} {pct(e['min_pct']):>7} {pct(e['max_pct']):>7} "
                f"{value_text(measure, e['base']):>9} {value_text(measure, e['new']):>9} {UNITS[measure][0]:<5}  {','.join(e['flags']) or '-'}"
            )
    voided = [c for c in run["cells"] if c["status"] == "void"]
    if voided:
        lines += ["", "VOIDED cells (their pairs do not count):"]
        lines += [f"  {c['key']}: {c['reason']}" for c in voided]
    if run["status"] != "complete":
        lines += ["", f"{run['status'].upper()} RUN: {'; '.join(run['reasons'])}.", "Do not publish these tables."]
        return "\n".join(lines) + "\n", 1
    return "\n".join(lines) + "\n", 0
