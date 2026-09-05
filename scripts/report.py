#!/usr/bin/env python3
"""Render the tables for one run directory (A/B or fleet).

Reads results/<run>/cells/*.{meta,wrk.txt,lowc.wrk.txt,k6.summary.json} plus
cells.expected and run.flags. Voided and invalid cells are listed, never
averaged. Saturated-cell latency is queue depth restated (Little's law);
per-request p50/p99 comes from the lowc pass each cell runs.
"""

import json
import re
import statistics
import sys
from pathlib import Path

UNIT_MS = {"us": 0.001, "ms": 1.0, "s": 1000.0, "m": 60000.0}
MODE_ORDER = ["dispatcher", "worker", "classic"]
# Everything else in a cell meta is a flag and self-registers in the tables.
DATA_KEYS = {"ref", "mode", "leg", "conns", "busy_server", "busy_loader", "void"}


def wrk_ms(tok):
    m = re.match(r"([0-9.]+)(us|ms|s|m)$", tok or "")
    return float(m.group(1)) * UNIT_MS[m.group(2)] if m else None


def parse_wrk(path):
    txt = path.read_text()
    rate = re.search(r"Requests/sec:\s+([0-9.]+)", txt)
    if not rate:
        return None
    lat = re.search(r"Latency\s+(\S+)\s+\S+\s+\S+", txt)
    p50 = re.search(r"\s50%\s+(\S+)", txt)
    p99 = re.search(r"\s99%\s+(\S+)", txt)
    return {
        "rate": float(rate.group(1)),
        "avg": wrk_ms(lat.group(1)) if lat else None,
        "p50": wrk_ms(p50.group(1)) if p50 else None,
        "p99": wrk_ms(p99.group(1)) if p99 else None,
    }


def parse_k6(path):
    try:
        metrics = json.loads(path.read_text())["metrics"]
    except (ValueError, KeyError):
        return None
    return metrics if "http_reqs" in metrics else None


def load_cells(out):
    cells = {}
    for meta_path in sorted((out / "cells").glob("*.meta")):
        tag = meta_path.stem
        meta = {}
        for line in meta_path.read_text().splitlines():
            k, _, v = line.partition("=")
            meta[k] = v
        wrk_path = out / "cells" / f"{tag}.wrk.txt"
        lowc_path = out / "cells" / f"{tag}.lowc.wrk.txt"
        k6_path = out / "cells" / f"{tag}.k6.summary.json"
        cells[tag] = {
            "meta": meta,
            "wrk": parse_wrk(wrk_path) if wrk_path.exists() else None,
            "lowc": parse_wrk(lowc_path) if lowc_path.exists() else None,
            "k6": parse_k6(k6_path) if k6_path.exists() else None,
        }
    return cells


def classify_cells(out, cells):
    suffixes = {
        "wrk": "wrk.txt",
        "lowc": "lowc.wrk.txt",
        "k6": "k6.summary.json",
    }
    for tag, cell in cells.items():
        cell["artifact_issues"] = {}
        if "void" in cell["meta"]:
            continue
        for artifact, suffix in suffixes.items():
            if cell[artifact] is not None:
                continue
            path = out / "cells" / f"{tag}.{suffix}"
            state = "unparseable" if path.exists() else "missing"
            cell["artifact_issues"][artifact] = f"{state} {artifact} output"


def ms(v):
    return f"{v:.2f}ms" if v is not None else "n/a"


def mode_key(mode):
    return (MODE_ORDER.index(mode), mode) if mode in MODE_ORDER else (len(MODE_ORDER), mode)


def cell_label(meta):
    """(sort key, display label) for the lowc and k6 tables, or None."""
    if "ref" in meta and "mode" in meta:
        return ((0, *mode_key(meta["mode"]), meta["ref"]), f"{meta['mode']} {meta['ref']}")
    if "leg" in meta:
        return ((1, 0, meta["leg"], ""), meta["leg"])
    return None


def flags_of(meta):
    return sorted(k for k in meta if k not in DATA_KEYS)


def median_of(values):
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def width(labels, floor):
    return max(floor, max(map(len, labels)) + 2)


def group_cells(cells, pick, field="wrk"):
    """pick(meta) -> group key or None; only valid measured cells count."""
    groups = {}
    for c in cells.values():
        key = pick(c["meta"])
        if key is None or "void" in c["meta"] or field in c["artifact_issues"]:
            continue
        groups.setdefault(key, []).append(c)
    return groups


def stats_of(group):
    rates = [c["wrk"]["rate"] for c in group]
    med = statistics.median(rates)
    spread = 100.0 * (max(rates) - min(rates)) / med if med else 0.0
    flags = sorted({f for c in group for f in flags_of(c["meta"])})
    return med, spread, flags, len(rates)


def main():
    if len(sys.argv) != 2:
        print("usage: report.py <run-directory>")
        return 2
    out = Path(sys.argv[1])
    if not (out / "cells").is_dir():
        print(f"ERROR: no cells/ in {out}; not a bench run directory")
        return 1
    cells = load_cells(out)
    classify_cells(out, cells)

    expected_path = out / "cells.expected"
    expected = expected_path.read_text().split() if expected_path.exists() else []
    missing = [t for t in expected if t not in cells]
    voided = {t: c["meta"]["void"] for t, c in cells.items() if "void" in c["meta"]}
    invalid = {t: c["artifact_issues"] for t, c in cells.items() if c["artifact_issues"]}
    broken = False

    # Run-level flags (null_run, asymmetric_build, ...) surface verbatim.
    run_flags = out / "run.flags"
    if run_flags.exists():
        for line in run_flags.read_text().split():
            print(f"RUN FLAG: {line}")
        print()

    ab = group_cells(cells, lambda m: (m["ref"], m["mode"]) if "ref" in m and "mode" in m else None)
    planned_modes = sorted(
        {c["meta"]["mode"] for c in cells.values() if "mode" in c["meta"]},
        key=mode_key,
    )
    if planned_modes:
        hdr = f"{'mode':<12} {'base req/s':>12} {'pr req/s':>12} {'delta':>8} {'n b/p':>6} {'spread b/p':>13}  flags"
        print(hdr)
        print("-" * len(hdr))
        for mode in planned_modes:
            b = ab.get(("base", mode))
            p = ab.get(("pr", mode))
            if not b or not p:
                print(f"{mode:<12} no surviving cells for {'base' if not b else 'pr'}; see the voided list")
                broken = True
                continue
            bm, bs, bf, bn = stats_of(b)
            pm, ps, pf, pn = stats_of(p)
            delta = 100.0 * (pm - bm) / bm if bm else 0.0
            flags = ",".join(sorted(set(bf) | set(pf))) or "-"
            print(f"{mode:<12} {bm:>12.0f} {pm:>12.0f} {delta:>+7.1f}% {bn:>3}/{pn:<2} {bs:>5.1f}/{ps:>5.1f}%  {flags}")
        print()

    fleet = group_cells(cells, lambda m: m.get("leg"))
    if fleet:
        w = width(fleet, 18)
        rows = sorted(((leg, *stats_of(group)) for leg, group in fleet.items()), key=lambda r: -r[1])
        hdr = f"{'leg':<{w}} {'req/s (median)':>14} {'n':>3} {'spread':>8}  flags"
        print(hdr)
        print("-" * len(hdr))
        for leg, med, spread, flags, n in rows:
            print(f"{leg:<{w}} {med:>14.0f} {n:>3} {spread:>7.1f}%  {','.join(flags) or '-'}")
        print()

    lowc = group_cells(cells, cell_label, field="lowc")
    if lowc:
        w = width([lbl for (_, lbl) in lowc], 22)
        hdr = f"{'lowc latency (c=low)':<{w}} {'p50':>10} {'p99':>10} {'avg':>10} {'n':>3}"
        print(hdr)
        print("-" * len(hdr))
        for (key, label) in sorted(lowc):
            group = lowc[(key, label)]
            row = {f: median_of(c["lowc"][f] for c in group) for f in ("p50", "p99", "avg")}
            print(f"{label:<{w}} {ms(row['p50']):>10} {ms(row['p99']):>10} {ms(row['avg']):>10} {len(group):>3}")
        print()

    # k6 columns are a latency probe with per-request checks; on a small
    # loader k6 is generator-bound, so its req/s is never a ceiling.
    k6 = group_cells(cells, cell_label, field="k6")
    if k6:
        w = width([lbl for (_, lbl) in k6], 24)
        hdr = f"{'k6 probe (not a ceiling)':<{w}} {'req/s':>10} {'avg':>9} {'p95':>9} {'failed%':>8} {'chk-fail':>9} {'n':>3}"
        print(hdr)
        print("-" * len(hdr))
        for (key, label) in sorted(k6):
            group = [c["k6"] for c in k6[(key, label)]]
            rate = median_of(g["http_reqs"].get("rate") for g in group) or 0.0
            avg = median_of(g.get("http_req_duration", {}).get("avg") for g in group)
            p95 = median_of(g.get("http_req_duration", {}).get("p(95)") for g in group)
            failed = max((g.get("http_req_failed", {}).get("value", 0.0) for g in group), default=0.0) * 100
            # Failed per-request checks are correctness regressions that
            # http_req_failed (status-based) never shows.
            chk = sum(int(g.get("checks", {}).get("fails", 0)) for g in group)
            if chk:
                broken = True
            print(f"{label:<{w}} {rate:>10.0f} {ms(avg):>9} {ms(p95):>9} {failed:>7.2f}% {chk:>9} {len(group):>3}")
            # Scenario submetrics appear when a workload script declares
            # thresholds on http_reqs{scenario:name}; one indented row each.
            scenarios = sorted({
                match.group(1)
                for g in group
                for k in g
                for match in [re.match(r"http_reqs\{scenario:(.+)\}$", k)]
                if match
            })
            for s in scenarios:
                srate = median_of(g.get(f"http_reqs{{scenario:{s}}}", {}).get("rate") for g in group)
                if srate is not None:
                    print(f"{'  ' + s:<{w}} {srate:>10.0f}")
        print()

    if voided:
        print("VOIDED cells (excluded from every number above):")
        for tag, why in sorted(voided.items()):
            print(f"  {tag}: {why}")
        print()
    if invalid:
        print("INVALID cells (excluded from affected tables):")
        for tag, issues in sorted(invalid.items()):
            print(f"  {tag}: {', '.join(issues.values())}")
        print()

    incomplete = []
    if not expected_path.exists():
        incomplete.append("cells.expected is missing")
    elif not expected:
        incomplete.append("cells.expected is empty")
    if missing:
        incomplete.append(f"{len(missing)} planned cells have no result: {' '.join(missing)}")
    if voided:
        incomplete.append(f"{len(voided)} cells were voided")
    if invalid:
        incomplete.append(f"{len(invalid)} cells have missing or unparseable generator output")
    if incomplete:
        print(f"INCOMPLETE RUN: {'; '.join(incomplete)}.")
    if broken:
        print("BROKEN RUN: a mode lost every cell of one ref, or k6 checks failed.")
    if incomplete or broken:
        print("Do not publish these tables.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
