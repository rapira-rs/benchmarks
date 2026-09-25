"""Tests of the snapshot parser and the flag and void rules."""

import unittest

from rig.flags import (
    Snapshot,
    cell_flags,
    cpu_pct,
    ena_delta,
    keepalive_flag,
    parse_snapshot,
    stage_flags,
    stage_void,
)

PARSE_CASES = [
    {
        "name": "server snapshot with two ena counters and conns",
        "text": "cpu 1000 4000\nena bw_in_allowance_exceeded 0\nena pps_allowance_exceeded 12\nconns 256 40\n",
        "cpu": (1000, 4000),
        "ena": {"bw_in_allowance_exceeded": 0, "pps_allowance_exceeded": 12},
        "conns": (256, 40),
    },
    {
        "name": "loader snapshot without conns",
        "text": "cpu 500 2000\nena bw_out_allowance_exceeded 3\n",
        "cpu": (500, 2000),
        "ena": {"bw_out_allowance_exceeded": 3},
        "conns": None,
    },
    {
        "name": "box without ena counters",
        "text": "cpu 7 9\nconns 0 0\n",
        "cpu": (7, 9),
        "ena": {},
        "conns": (0, 0),
    },
]

CPU_CASES = [
    # (190 - 100) busy ticks over (300 - 200) total ticks is 90 percent.
    {"name": "busy 90 of 100 ticks", "before": (100, 200), "after": (190, 300), "pct": 90},
    {"name": "idle window", "before": (100, 200), "after": (100, 300), "pct": 0},
    {"name": "zero total delta", "before": (100, 200), "after": (100, 200), "pct": 0},
    # 849 of 1000 is 84.9 percent. The integer percent is 84, under GENERATOR_BUSY.
    {"name": "84.9 percent is 84", "before": (0, 0), "after": (849, 1000), "pct": 84},
    # 857 of 1000 is 85.7 percent. The integer percent is 85, at GENERATOR_BUSY.
    {"name": "85.7 percent is 85", "before": (0, 0), "after": (857, 1000), "pct": 85},
]

ENA_CASES = [
    {
        "name": "only the changed counter",
        "before": {"bw_in_allowance_exceeded": 0, "pps_allowance_exceeded": 12},
        "after": {"bw_in_allowance_exceeded": 0, "pps_allowance_exceeded": 958},
        "delta": {"pps_allowance_exceeded": 946},
    },
    {
        "name": "no change",
        "before": {"bw_in_allowance_exceeded": 4},
        "after": {"bw_in_allowance_exceeded": 4},
        "delta": {},
    },
    {
        "name": "counter absent before counts from zero",
        "before": {},
        "after": {"conntrack_allowance_exceeded": 7},
        "delta": {"conntrack_allowance_exceeded": 7},
    },
]

STAGE_FLAG_CASES = [
    {
        "name": "passing stage has no flags",
        "server_busy": 50,
        "loader_busy": {"loader-1": 99},
        "passed": True,
        "flags": {},
    },
    {
        "name": "loader at 85 and server at 89 is generator bound",
        "server_busy": 89,
        "loader_busy": {"loader-1": 40, "loader-2": 85},
        "passed": False,
        "flags": {"generator_bound": True},
    },
    {
        "name": "loader above 85 and server at 89 is generator bound",
        "server_busy": 89,
        "loader_busy": {"loader-1": 97, "loader-2": 40},
        "passed": False,
        "flags": {"generator_bound": True},
    },
    {
        "name": "server at 90 with a busy loader has no flags",
        "server_busy": 90,
        "loader_busy": {"loader-1": 85},
        "passed": False,
        "flags": {},
    },
    {
        "name": "server above 90 with idle loaders has no flags",
        "server_busy": 99,
        "loader_busy": {"loader-1": 10},
        "passed": False,
        "flags": {},
    },
    {
        "name": "server at 89 and every loader at 84 is server unsaturated",
        "server_busy": 89,
        "loader_busy": {"loader-1": 84, "loader-2": 84},
        "passed": False,
        "flags": {"server_unsaturated": True},
    },
]

VOID_CASES = [
    {
        "name": "no loader was shaped",
        "loader_ena": {"loader-1": {}, "loader-2": {}},
        "reason": None,
    },
    {
        "name": "shaped loader names its counter and delta",
        "loader_ena": {"loader-1": {}, "loader-2": {"bw_out_allowance_exceeded": 12}},
        "reason": "loader loader-2 throttled: bw_out_allowance_exceeded=12",
    },
    {
        "name": "two shaped loaders report the first in loader order",
        "loader_ena": {"loader-1": {"pps_allowance_exceeded": 9}, "loader-3": {"pps_allowance_exceeded": 5}},
        "reason": "loader loader-1 throttled: pps_allowance_exceeded=9",
    },
]

KEEPALIVE_CASES = [
    {"name": "growth under the connections", "before": (256, 100), "after": (256, 200), "connections": 256, "flags": {}},
    # 356 - 100 = 256 new TIME-WAIT sockets, equal to the connection count.
    {"name": "growth equal to the connections", "before": (256, 100), "after": (256, 356), "connections": 256, "flags": {}},
    {
        "name": "growth one above the connections",
        "before": (256, 100),
        "after": (256, 357),
        "connections": 256,
        "flags": {"keepalive_broken": {"time_wait_delta": 257, "connections": 256}},
    },
]

CELL_FLAG_CASES = [
    {"name": "stable cell", "pids_before": [11, 12, 13], "pids_after": [11, 12, 13], "log_before": 100, "log_after": 200, "flags": {}},
    {"name": "reordered pid list is not churn", "pids_before": [13, 11, 12], "pids_after": [12, 13, 11], "log_before": 0, "log_after": 0, "flags": {}},
    {"name": "replaced pid is churn", "pids_before": [11, 12, 13], "pids_after": [11, 12, 14], "log_before": 0, "log_after": 0, "flags": {"worker_churn": True}},
    {"name": "lost worker is churn", "pids_before": [11, 12, 13], "pids_after": [11, 12], "log_before": 0, "log_after": 0, "flags": {"worker_churn": True}},
    # 65536 bytes is 64 KiB: at the limit, not above it.
    {"name": "growth at 65536 bytes", "pids_before": [11], "pids_after": [11], "log_before": 1000, "log_after": 66536, "flags": {}},
    {"name": "growth at 65537 bytes", "pids_before": [11], "pids_after": [11], "log_before": 1000, "log_after": 66537, "flags": {"log_growth": 65537}},
    {
        "name": "churn and growth together",
        "pids_before": [11, 12],
        "pids_after": [11, 15],
        "log_before": 1000,
        "log_after": 71000,
        "flags": {"worker_churn": True, "log_growth": 70000},
    },
]


class TestFlags(unittest.TestCase):
    def test_parse_snapshot(self):
        for case in PARSE_CASES:
            with self.subTest(name=case["name"]):
                snap = parse_snapshot(case["text"])
                self.assertEqual(snap, Snapshot(cpu=case["cpu"], ena=case["ena"], conns=case["conns"]))

    def test_cpu_pct(self):
        for case in CPU_CASES:
            with self.subTest(name=case["name"]):
                before = Snapshot(cpu=case["before"], ena={}, conns=None)
                after = Snapshot(cpu=case["after"], ena={}, conns=None)
                self.assertEqual(cpu_pct(before, after), case["pct"])

    def test_ena_delta(self):
        for case in ENA_CASES:
            with self.subTest(name=case["name"]):
                before = Snapshot(cpu=(0, 0), ena=case["before"], conns=None)
                after = Snapshot(cpu=(0, 0), ena=case["after"], conns=None)
                self.assertEqual(ena_delta(before, after), case["delta"])

    def test_stage_flags(self):
        for case in STAGE_FLAG_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(stage_flags(case["server_busy"], case["loader_busy"], case["passed"]), case["flags"])

    def test_stage_void(self):
        for case in VOID_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(stage_void(case["loader_ena"]), case["reason"])

    def test_keepalive_flag(self):
        for case in KEEPALIVE_CASES:
            with self.subTest(name=case["name"]):
                before = Snapshot(cpu=(0, 0), ena={}, conns=case["before"])
                after = Snapshot(cpu=(0, 0), ena={}, conns=case["after"])
                self.assertEqual(keepalive_flag(before, after, case["connections"]), case["flags"])

    def test_cell_flags(self):
        for case in CELL_FLAG_CASES:
            with self.subTest(name=case["name"]):
                got = cell_flags(case["pids_before"], case["pids_after"], case["log_before"], case["log_after"])
                self.assertEqual(got, case["flags"])


if __name__ == "__main__":
    unittest.main()
