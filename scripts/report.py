#!/usr/bin/env python3
"""Render benchmark tables and reject incomplete or failed measurements."""

import json
import re
import statistics
import sys
from pathlib import Path

UNIT_MS = {"us": 0.001, "ms": 1.0, "s": 1000.0, "m": 60000.0}
MODE_ORDER = ["dispatcher", "worker", "classic"]
# Everything else in a cell meta is a flag and self-registers in the tables.
DATA_KEYS = {"ref", "mode", "leg", "proto", "conns", "expect_len", "pss_kb", "busy_server", "busy_loader", "void"}


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
        "errors": "Non-2xx" in txt or "Socket errors" in txt,
    }


def parse_h2load(path):
    txt = path.read_text()
    fin = re.search(r"^finished in \S+, ([0-9.]+) req/s", txt, re.M)
    # h2load 1.68 prints "time for request:" and has no median column.
    hdr = re.search(r"^\s+min\s+max\s+median\s", txt, re.M)
    if not fin or not hdr:
        return None
    req = re.search(r"(\d+) succeeded, (\d+) failed, (\d+) errored, (\d+) timeout", txt)
    codes = re.search(r"(\d+) 3xx, (\d+) 4xx, (\d+) 5xx", txt)
    data = re.search(r"\((\d+)\) data", txt)
    # Columns: min, max, median, p95, p99, mean, sd, +/- sd.
    row = re.search(r"^request\s+:" + r"\s+(\S+)" * 6, txt, re.M).groups()
    succeeded, failed, errored, timeout = map(int, req.groups())
    s3xx, s4xx, s5xx = map(int, codes.groups())
    return {
        "rate": float(fin.group(1)),
        "succeeded": succeeded,
        "failed": failed,
        "errored": errored,
        "timeout": timeout,
        "s3xx": s3xx,
        "s4xx": s4xx,
        "s5xx": s5xx,
        "data": int(data.group(1)),
        "p50": wrk_ms(row[2]),
        "p99": wrk_ms(row[4]),
        "avg": wrk_ms(row[5]),
        "errors": any((failed, errored, timeout, s3xx, s4xx, s5xx)),
    }


def parse_k6(path):
    try:
        metrics = json.loads(path.read_text())["metrics"]
    except (ValueError, KeyError):
        return None
    if "http_reqs" in metrics:
        return metrics
    # k6/grpc.js: a proto grpc run has no HTTP metrics.
    if "iterations" in metrics and ("grpc_req_duration" in metrics or "http_req_duration" in metrics):
        return metrics
    return None


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
        h2load_path = out / "cells" / f"{tag}.h2load.txt"
        lowc_h2load_path = out / "cells" / f"{tag}.lowc.h2load.txt"
        cells[tag] = {
            "meta": meta,
            "wrk": parse_wrk(wrk_path) if wrk_path.exists() else None,
            "lowc": parse_wrk(lowc_path) if lowc_path.exists() else None,
            "k6": parse_k6(k6_path) if k6_path.exists() else None,
            "h2load": parse_h2load(h2load_path) if h2load_path.exists() else None,
            "lowc_h2load": parse_h2load(lowc_h2load_path) if lowc_h2load_path.exists() else None,
        }
    return cells


def classify_cells(out, cells):
    suffixes = {
        "wrk": "wrk.txt",
        "lowc": "lowc.wrk.txt",
        "k6": "k6.summary.json",
        "h2load": "h2load.txt",
        "lowc_h2load": "lowc.h2load.txt",
    }
    for tag, cell in cells.items():
        cell["artifact_issues"] = {}
        if "void" in cell["meta"]:
            continue
        meta = cell["meta"]
        if not meta.get("leg") and not (meta.get("ref") and meta.get("mode")):
            cell["artifact_issues"]["meta"] = "missing cell identity"
            continue
        proto = meta.get("proto")
        if proto is None:
            required = ["wrk", "lowc", "k6"]
        else:
            # k6 has no h2c client; the http-h1 reference also runs wrk.
            required = ["h2load", "lowc_h2load"]
            if proto != "connect-h2c":
                required.append("k6")
            if proto == "http-h1":
                required.append("wrk")
        for artifact in required:
            data = cell[artifact]
            if data is not None:
                if artifact in ("wrk", "lowc", "h2load", "lowc_h2load") and data["errors"]:
                    cell["artifact_issues"][artifact] = f"{artifact} request errors"
                elif artifact in ("h2load", "lowc_h2load") and data["data"] < data["succeeded"] * int(meta["expect_len"]):
                    cell["artifact_issues"][artifact] = "short responses"
                elif artifact == "k6" and data.get("http_req_failed", {}).get("value", 0) > 0:
                    cell["artifact_issues"][artifact] = "k6 HTTP request failures"
                elif artifact == "k6" and data.get("dropped_iterations", {}).get("count", 0) > 0:
                    cell["artifact_issues"][artifact] = "k6 dropped iterations"
                continue
            path = out / "cells" / f"{tag}.{suffixes[artifact]}"
            state = "unparseable" if path.exists() else "missing"
            cell["artifact_issues"][artifact] = f"{state} {artifact} output"
        if cell["meta"].get("leg", "").endswith("-nginx-worker"):
            for artifact in ("nginx.conf", "nginx.txt"):
                path = out / "cells" / f"{tag}.{artifact}"
                if not path.is_file() or path.stat().st_size == 0:
                    cell["artifact_issues"][artifact] = f"missing or empty {artifact}"


def ms(v):
    return f"{v:.2f}ms" if v is not None else "n/a"


def mode_key(mode):
    return (MODE_ORDER.index(mode), mode) if mode in MODE_ORDER else (len(MODE_ORDER), mode)


def cell_label(meta):
    """(sort key, display label) for the lowc and k6 tables, or None."""
    if "proto" in meta:
        return None
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
        if "meta" in c["artifact_issues"]:
            continue
        key = pick(c["meta"])
        if key is None or "void" in c["meta"] or field in c["artifact_issues"]:
            continue
        groups.setdefault(key, []).append(c)
    return groups


def stats_of(group, field="wrk"):
    rates = [c[field]["rate"] for c in group]
    med = statistics.median(rates)
    spread = 100.0 * (max(rates) - min(rates)) / med if med else 0.0
    flags = sorted({f for c in group for f in flags_of(c["meta"])})
    return med, spread, flags, len(rates)


def latency_table(title, floor, rows, field):
    """Print the median p50, p99 and avg of one latency artifact per (label, cells) row."""
    w = width([label for label, _ in rows], floor)
    hdr = f"{title:<{w}} {'p50':>10} {'p99':>10} {'avg':>10} {'n':>3}"
    print(hdr)
    print("-" * len(hdr))
    for label, group in rows:
        row = {f: median_of(c[field][f] for c in group) for f in ("p50", "p99", "avg")}
        print(f"{label:<{w}} {ms(row['p50']):>10} {ms(row['p99']):>10} {ms(row['avg']):>10} {len(group):>3}")
    print()


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

    fleet = group_cells(cells, lambda m: m.get("leg") if "proto" not in m else None)
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
        latency_table("lowc latency (c=low)", 22, [(label, lowc[(key, label)]) for (key, label) in sorted(lowc)], "lowc")

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

    # The http-h1 reference leg also has a row for the same request under wrk.
    grpc = group_cells(cells, lambda m: (m["leg"], m["proto"]) if "proto" in m else None, field="h2load")
    grpc_wrk = group_cells(cells, lambda m: (f"{m['leg']} (wrk)", m["proto"]) if m.get("proto") == "http-h1" else None)
    rows = []
    for groups, field in ((grpc, "h2load"), (grpc_wrk, "wrk")):
        for (leg, proto), group in groups.items():
            pss = median_of(int(c["meta"]["pss_kb"]) / 1024 if c["meta"].get("pss_kb") else None for c in group)
            rows.append((leg, proto, *stats_of(group, field), pss))
    if rows:
        w = width([r[0] for r in rows], 18)
        hdr = f"{'grpc legs':<{w}} {'proto':<14} {'req/s (median)':>14} {'n':>3} {'spread':>8} {'pss MiB':>8}  flags"
        print(hdr)
        print("-" * len(hdr))
        for leg, proto, med, spread, flags, n, pss in sorted(rows, key=lambda r: -r[2]):
            pss_mib = f"{pss:.1f}" if pss is not None else "n/a"
            print(f"{leg:<{w}} {proto:<14} {med:>14.0f} {n:>3} {spread:>7.1f}% {pss_mib:>8}  {','.join(flags) or '-'}")
        print()

    grpc_lowc = group_cells(cells, lambda m: m["leg"] if "proto" in m else None, field="lowc_h2load")
    if grpc_lowc:
        latency_table("grpc lowc latency (c=processes)", 33, sorted(grpc_lowc.items()), "lowc_h2load")

    # The open loop runs at a fixed rate: the achieved rate and the dropped
    # iterations show whether the server kept up.
    open_loop = group_cells(cells, lambda m: m["leg"] if m.get("proto") not in (None, "connect-h2c") else None, field="k6")
    if open_loop:
        w = width(open_loop, 16)
        hdr = f"{'grpc open loop':<{w}} {'req/s':>10} {'p50':>9} {'p99':>9} {'p99.9':>9} {'dropped':>8} {'chk-fail':>9} {'n':>3}"
        print(hdr)
        print("-" * len(hdr))
        for leg, group in sorted(open_loop.items()):
            k6s = [c["k6"] for c in group]
            trends = [g.get("grpc_req_duration") or g["http_req_duration"] for g in k6s]
            rate = median_of(g["iterations"]["rate"] for g in k6s)
            p50, p99, p999 = (median_of(t[f] for t in trends) for f in ("med", "p(99)", "p(99.9)"))
            dropped = sum(int(g["dropped_iterations"]["count"]) for g in k6s)
            chk = sum(int(g["checks"]["fails"]) for g in k6s)
            if chk:
                broken = True
            print(f"{leg:<{w}} {rate:>10.0f} {ms(p50):>9} {ms(p99):>9} {ms(p999):>9} {dropped:>8} {chk:>9} {len(group):>3}")
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
        incomplete.append(f"{len(invalid)} cells have missing or invalid artifacts")
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
