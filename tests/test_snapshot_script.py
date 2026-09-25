"""Tests for box/snapshot.sh on the local machine, with a fake ip and a fake ethtool."""

import os
import socket
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "box" / "snapshot.sh"

ROUTE = "default via 10.0.0.1 dev ens5 proto dhcp src 10.0.0.9 metric 100"

# ethtool -S output of an ENA device, trimmed. Only the counters whose name
# contains allowance_exceeded become ena lines.
ETHTOOL_STATS = textwrap.dedent("""\
    NIC statistics:
         tx_timeout: 0
         bw_in_allowance_exceeded: 0
         bw_out_allowance_exceeded: 12
         pps_allowance_exceeded: 946
         conntrack_allowance_exceeded: 0
         linklocal_allowance_exceeded: 0
         conntrack_allowance_available: 128270
         queue_0_tx_cnt: 5170
    """)

ENA_LINES = [
    "ena bw_in_allowance_exceeded 0",
    "ena bw_out_allowance_exceeded 12",
    "ena pps_allowance_exceeded 946",
    "ena conntrack_allowance_exceeded 0",
    "ena linklocal_allowance_exceeded 0",
]

# The fake ip prints FAKE_ROUTE. The fake ethtool prints the stats only for "-S ens5".
FAKE_IP = textwrap.dedent("""\
    #!/usr/bin/env bash
    if [ -n "$FAKE_ROUTE" ]; then echo "$FAKE_ROUTE"; fi
    """)
FAKE_ETHTOOL = textwrap.dedent("""\
    #!/usr/bin/env bash
    if [ "$1" != -S ] || [ "$2" != ens5 ]; then echo "fake ethtool: unexpected $*" >&2; exit 1; fi
    cat "$FAKE_STATS"
    """)

SHAPE_CASES = [
    {"name": "server snapshot with a port", "route": ROUTE, "port": True, "ena": ENA_LINES, "conns": True},
    {"name": "loader snapshot without a port", "route": ROUTE, "port": False, "ena": ENA_LINES, "conns": False},
    {"name": "no default route gives no ena lines", "route": "", "port": False, "ena": [], "conns": False},
]

# The counts are for the server side of one TCP connection on a fresh port.
# The side that closes first holds the TIME-WAIT state, so a server-side close
# moves the socket from established to time_wait.
CONNS_CASES = [
    {"name": "open connection is established", "server_closes_first": False, "conns": "conns 1 0"},
    {"name": "server close leaves time_wait", "server_closes_first": True, "conns": "conns 0 1"},
]


class SnapshotScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        for name, text in (("ip", FAKE_IP), ("ethtool", FAKE_ETHTOOL)):
            path = bin_dir / name
            path.write_text(text)
            path.chmod(0o755)
        stats = tmp / "stats.txt"
        stats.write_text(ETHTOOL_STATS)
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_STATS=str(stats))

    def snapshot(self, route, port):
        args = ["bash", str(SNAPSHOT)]
        if port is not None:
            args.append(str(port))
        proc = subprocess.run(args, env=dict(self.env, FAKE_ROUTE=route), capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.splitlines()

    def test_line_shapes(self):
        for case in SHAPE_CASES:
            with self.subTest(name=case["name"]):
                lines = self.snapshot(case["route"], 18080 if case["port"] else None)
                cpu = lines[0].split()
                self.assertEqual(cpu[0], "cpu")
                self.assertEqual(len(cpu), 3)
                busy, total = int(cpu[1]), int(cpu[2])
                self.assertLessEqual(busy, total)
                self.assertGreater(total, 0)
                self.assertEqual([line for line in lines if line.startswith("ena ")], case["ena"])
                conns = [line for line in lines if line.startswith("conns ")]
                self.assertEqual(len(conns), 1 if case["conns"] else 0)
                self.assertEqual(len(lines), 1 + len(case["ena"]) + len(conns))

    def test_conns_counts(self):
        for case in CONNS_CASES:
            with self.subTest(name=case["name"]):
                with socket.create_server(("127.0.0.1", 0)) as listener:
                    port = listener.getsockname()[1]
                    client = socket.create_connection(("127.0.0.1", port))
                    accepted, _ = listener.accept()
                    if case["server_closes_first"]:
                        accepted.close()
                        self.assertEqual(client.recv(1), b"")
                        client.close()
                        time.sleep(0.2)
                    lines = self.snapshot("", port)
                    if not case["server_closes_first"]:
                        accepted.close()
                        client.close()
                self.assertEqual(lines[-1], case["conns"])


if __name__ == "__main__":
    unittest.main()
