"""Tests of the paired-delta summary."""

import unittest

from rig.summary import summarize

HELLO = "hello-rapira-worker"
GRPC = "grpc-rapira"
SKEW = {"loader_skew": {"loader": "loader-1", "thread_ms": 4.5, "median_ms": 3.6}}


def rate_cell(round_no, build, p99, rss_kb, flags=None, target=HELLO):
    """An ok rate cell with the numbers that the summary reads."""
    return {
        "key": f"r{round_no}-{build}-rate-{target}", "target": {"name": target}, "round": round_no,
        "build": build, "stage": "rate", "status": "ok", "flags": flags or {},
        "achieved_rps": 60000.0, "latency_us": {"p50": p99 / 2, "p99": p99}, "rss_kb": rss_kb,
    }


def cap_cell(round_no, build, achieved, flags=None, target=HELLO):
    """An ok capacity cell with the numbers that the summary reads."""
    return {
        "key": f"r{round_no}-{build}-cap-{target}", "target": {"name": target}, "round": round_no,
        "build": build, "stage": "cap", "status": "ok", "flags": flags or {},
        "achieved_rps": achieved, "latency_us": {"p50": 900000, "p99": 1800000}, "rss_kb": 102400,
    }


def void_cell(round_no, build, stage, target=HELLO):
    """A void cell. A void cell has no numbers."""
    return {
        "key": f"r{round_no}-{build}-{stage}-{target}", "target": {"name": target}, "round": round_no,
        "build": build, "stage": stage, "status": "void", "reason": "server log: 1 warn or error lines",
        "flags": {}, "achieved_rps": None, "latency_us": None, "rss_kb": None,
    }


def entry(pairs, delta_pct, min_pct, max_pct, base, new, flags=()):
    return {"pairs": pairs, "delta_pct": delta_pct, "min_pct": min_pct, "max_pct": max_pct, "base": base, "new": new, "flags": list(flags)}


NULL = entry(0, None, None, None, None, None)

SUMMARY_CASES = [
    {
        # p99: the deltas are 100 * 100 / 1000 = +10.0, 100 * -100 / 2000 = -5.0 and 100 * 400 / 4000 = +10.0.
        # The median is 10.0, the band is -5.0 to 10.0. The base median of 1000, 2000, 4000 is 2000,
        # the new median of 1100, 1900, 4400 is 1900: the separate medians differ from the median delta.
        # rss: the deltas are 100 * 2048 / 102400 = 2.0, 0.0 and 100 * 2048 / 204800 = 1.0. The median is 1.0.
        # capacity: the deltas are 100 * 3000 / 100000 = 3.0, 100 * -1000 / 100000 = -1.0 and
        # 100 * 4000 / 200000 = 2.0. The median is 2.0. The base median is 100000, the new median 103000.
        # The flags are the sorted flag names of the cells of each stage kind.
        "name": "three pairs give the median delta and the band",
        "cells": [
            rate_cell(1, "base", 1000, 102400), rate_cell(1, "new", 1100, 104448),
            cap_cell(1, "base", 100000.0), cap_cell(1, "new", 103000.0, {"not_saturated": True}),
            rate_cell(2, "new", 1900, 102400, {"server_unsaturated": True}), rate_cell(2, "base", 2000, 102400),
            cap_cell(2, "new", 99000.0), cap_cell(2, "base", 100000.0),
            rate_cell(3, "base", 4000, 204800), rate_cell(3, "new", 4400, 206848),
            cap_cell(3, "base", 200000.0, {"loader_busy": 72}), cap_cell(3, "new", 204000.0),
        ],
        "summary": {HELLO: {
            "capacity": entry(3, 2.0, -1.0, 3.0, 100000.0, 103000.0, ["loader_busy", "not_saturated"]),
            "p99": entry(3, 10.0, -5.0, 10.0, 2000, 1900, ["server_unsaturated"]),
            "rss": entry(3, 1.0, 0.0, 2.0, 102400, 104448, ["server_unsaturated"]),
        }},
    },
    {
        # Round 2 lists the new cell first. The pair is by build, not by position: the round 2 delta is
        # 100 * (950 - 1000) / 1000 = -5.0. With round 1 at +10.0, the median of 2 deltas is their mean, 2.5.
        # The base median is 1000, the new median is (1100 + 950) / 2 = 1025.
        "name": "odd and even rounds pair by build",
        "cells": [
            rate_cell(1, "base", 1000, 102400), rate_cell(1, "new", 1100, 102400),
            rate_cell(2, "new", 950, 102400), rate_cell(2, "base", 1000, 102400),
        ],
        "summary": {HELLO: {
            "capacity": NULL,
            "p99": entry(2, 2.5, -5.0, 10.0, 1000, 1025.0),
            "rss": entry(2, 0.0, 0.0, 0.0, 102400, 102400),
        }},
    },
    {
        # The void base cell of round 2 drops the round 2 pair, and the flag of its new cell with it.
        # Rounds 1 and 3 give +10.0 and 100 * -100 / 2000 = -5.0, the median is 2.5.
        # The base median is (1000 + 2000) / 2 = 1500, the new median is (1100 + 1900) / 2 = 1500.
        "name": "a void cell drops its pair",
        "cells": [
            rate_cell(1, "base", 1000, 102400), rate_cell(1, "new", 1100, 102400),
            rate_cell(2, "new", 5000, 102400, {"generator_bound": True}), void_cell(2, "base", "rate"),
            rate_cell(3, "base", 2000, 102400), rate_cell(3, "new", 1900, 102400),
        ],
        "summary": {HELLO: {
            "capacity": NULL,
            "p99": entry(2, 2.5, -5.0, 10.0, 1500.0, 1500.0),
            "rss": entry(2, 0.0, 0.0, 0.0, 102400, 102400),
        }},
    },
    {
        # loader_skew on the new cell of round 1 and on the base cell of round 3 drops those pairs for p99:
        # only round 2 counts, 100 * -50 / 1000 = -5.0. rss keeps the 3 pairs: 100 * 2048 / 102400 = 2.0,
        # 0.0 and 0.0, the median is 0.0. The rss new median of 104448, 102400, 102400 is 102400.
        "name": "a loader_skew pair counts for rss and not for p99",
        "cells": [
            rate_cell(1, "base", 1000, 102400), rate_cell(1, "new", 1100, 104448, SKEW),
            rate_cell(2, "new", 950, 102400), rate_cell(2, "base", 1000, 102400),
            rate_cell(3, "base", 3000, 102400, SKEW), rate_cell(3, "new", 3300, 102400),
        ],
        "summary": {HELLO: {
            "capacity": NULL,
            "p99": entry(1, -5.0, -5.0, -5.0, 1000, 950),
            "rss": entry(3, 0.0, 0.0, 2.0, 102400, 102400, ["loader_skew"]),
        }},
    },
    {
        # A base cell without its new cell is no pair.
        "name": "no pair gives null numbers",
        "cells": [rate_cell(1, "base", 1000, 102400), cap_cell(1, "base", 100000.0)],
        "summary": {HELLO: {"capacity": NULL, "p99": NULL, "rss": NULL}},
    },
    {
        # Every base cell of grpc-rapira is void, so it has no pair. hello-rapira-worker has one pair per stage kind:
        # capacity 100 * 5000 / 100000 = 5.0, p99 100 * -100 / 1000 = -10.0, rss 0.0.
        # The targets keep the order of their first cell.
        "name": "a target with every base cell void",
        "cells": [
            void_cell(1, "base", "rate", GRPC), rate_cell(1, "new", 2000, 204800, target=GRPC),
            void_cell(1, "base", "cap", GRPC), cap_cell(1, "new", 50000.0, target=GRPC),
            rate_cell(1, "base", 1000, 102400), rate_cell(1, "new", 900, 102400),
            cap_cell(1, "base", 100000.0), cap_cell(1, "new", 105000.0),
        ],
        "summary": {
            GRPC: {"capacity": NULL, "p99": NULL, "rss": NULL},
            HELLO: {
                "capacity": entry(1, 5.0, 5.0, 5.0, 100000.0, 105000.0),
                "p99": entry(1, -10.0, -10.0, -10.0, 1000, 900),
                "rss": entry(1, 0.0, 0.0, 0.0, 102400, 102400),
            },
        },
    },
]


class TestSummarize(unittest.TestCase):
    def test_summarize(self):
        for case in SUMMARY_CASES:
            with self.subTest(name=case["name"]):
                summary = summarize(case["cells"])
                self.assertEqual(summary, case["summary"])
                self.assertEqual(list(summary), list(case["summary"]))


if __name__ == "__main__":
    unittest.main()
