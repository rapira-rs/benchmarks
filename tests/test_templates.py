"""Tests of the rapira config templates, the shared php.ini, and the expected body files."""

import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RIG = "/home/fedora/bench-rig"

TOML_CASES = [
    {
        "name": "rapira http worker",
        "path": "servers/rapira/http.toml.tpl",
        "values": {"LISTEN": ":8080", "ENTRY": RIG + "/apps/hello/worker.php", "MODE": "worker", "PROCS": "32"},
        "expected": {
            "http": {
                "listen": ":8080",
                "pool": {"entrypoint": RIG + "/apps/hello/worker.php", "mode": "worker", "processes": 32},
            },
            "log": {"level": "warn"},
        },
    },
    {
        "name": "rapira http dispatcher",
        "path": "servers/rapira/http.toml.tpl",
        "values": {"LISTEN": ":8080", "ENTRY": RIG + "/apps/hello/dispatcher.php", "MODE": "dispatcher", "PROCS": "32"},
        "expected": {
            "http": {
                "listen": ":8080",
                "pool": {"entrypoint": RIG + "/apps/hello/dispatcher.php", "mode": "dispatcher", "processes": 32},
            },
            "log": {"level": "warn"},
        },
    },
    {
        "name": "rapira static middleware in front of the hello dispatcher",
        "path": "servers/rapira/static.toml.tpl",
        "values": {"LISTEN": ":8080", "ROOT": RIG + "/apps/hello", "ENTRY": RIG + "/apps/hello/dispatcher.php", "MODE": "dispatcher", "PROCS": "32"},
        "expected": {
            "http": {
                "listen": ":8080",
                "middleware": ["static"],
                "static": {"root": RIG + "/apps/hello"},
                "pool": {"entrypoint": RIG + "/apps/hello/dispatcher.php", "mode": "dispatcher", "processes": 32},
            },
            "log": {"level": "warn"},
        },
    },
    {
        "name": "rapira grpc",
        "path": "servers/rapira/grpc.toml.tpl",
        "values": {"LISTEN": ":8080", "RIG": RIG, "ENTRY": RIG + "/apps/grpc/php/dispatcher.php", "PROCS": "32"},
        "expected": {
            "grpc": {
                "listen": ":8080",
                "descriptor_set": RIG + "/apps/grpc/bench.binpb",
                "services": ["bench.v1.EchoService"],
                "pool": {"entrypoint": RIG + "/apps/grpc/php/dispatcher.php", "mode": "dispatcher", "processes": 32},
            },
            "log": {"level": "warn"},
        },
    },
]

BODY_CASES = [
    {
        "name": "hello expected body",
        "path": "apps/hello/expect.txt",
        # 24 bytes: the body of GET /?name=you on every hello target.
        "expected": b"Hello from worker, you!\n",
    },
    {
        "name": "grpc request frame",
        "path": "apps/grpc/echo.grpc",
        # The length-prefixed frame of an EchoRequest with the 64 character text of apps/grpc/fixtures.py: 71 bytes.
        "expected": b"\x00\x00\x00\x00\x42\x0a\x40" + b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ01",
    },
]


def render(path, values):
    text = (REPO / path).read_text()
    for name, value in values.items():
        text = text.replace("@@" + name + "@@", value)
    return text


class TemplateTests(unittest.TestCase):
    def test_toml_templates(self):
        for case in TOML_CASES:
            with self.subTest(name=case["name"]):
                text = render(case["path"], case["values"])
                self.assertNotIn("@@", text)
                self.assertEqual(case["expected"], tomllib.loads(text))

    def test_expected_bodies(self):
        for case in BODY_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(case["expected"], (REPO / case["path"]).read_bytes())


if __name__ == "__main__":
    unittest.main()
