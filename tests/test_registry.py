import tempfile
import unittest
from pathlib import Path

from rig.registry import Suite, SuiteError, Target, cell_key, load_suite, load_targets, plan_cells

ROOT = Path(__file__).resolve().parent.parent

VALID_TARGET = """
[grpc-rapira]
server = "rapira"
app = "grpc"
mode = "dispatcher"
proto = "grpc"
start = ["grpc", "@RIG@/apps/grpc/php/dispatcher.php"]
url = "/bench.v1.EchoService/Echo"
expect = "apps/grpc/expect.grpc"
config = "servers/rapira/grpc.toml.tpl"
method = "POST"
body = "apps/grpc/echo.grpc"

[grpc-rapira.headers]
x-note = "a:b"
"""

BASE = 'server = "rapira"\napp = "hello"\nmode = "worker"\nproto = "http1"\nstart = []\nurl = "/"\nexpect = "e"\nconfig = "c"\n'

# Each error case is a valid rapira target with one field changed, added, or removed.
TARGET_ERROR_CASES = [
    {"name": "unknown server", "toml": "[t]\n" + BASE.replace('server = "rapira"', 'server = "frankenphp"'), "message": "unknown server frankenphp"},
    {"name": "unknown app", "toml": "[t]\n" + BASE.replace('app = "hello"', 'app = "symfony"'), "message": "unknown app symfony"},
    {"name": "unknown mode", "toml": "[t]\n" + BASE.replace('mode = "worker"', 'mode = "cgi"'), "message": "unknown mode cgi"},
    {"name": "unknown proto", "toml": "[t]\n" + BASE.replace('proto = "http1"', 'proto = "http2"'), "message": "unknown proto http2"},
    {"name": "binary field of the old registry", "toml": "[t]\n" + BASE + 'binary = "pr"\n', "message": "binary"},
    {"name": "misspelled field", "toml": "[t]\n" + BASE + 'header = "x: 1"\n', "message": "header"},
    {"name": "missing url", "toml": "[t]\n" + BASE.replace('url = "/"\n', ""), "message": "url"},
]


def target(name: str, app: str = "hello", proto: str = "http1") -> Target:
    return Target(
        name=name,
        server="rapira",
        app=app,
        mode="worker",
        proto=proto,
        start=("worker", "@RIG@/apps/hello/worker.php"),
        url="/?name=you",
        expect="apps/hello/expect.txt",
        config="servers/rapira/http.toml.tpl",
    )


REGISTRY = {
    "hello-a": target("hello-a"),
    "hello-b": target("hello-b"),
    "yii3-a": target("yii3-a", app="yii3"),
    "grpc-a": target("grpc-a", app="grpc", proto="grpc"),
}

SUITE_DEFAULTS = {
    "rounds": 1,
    "warmup_s": 10,
    "duration_s": 60,
    "targets": '["hello-a", "yii3-a"]',
    "rates": "hello = 250000\nyii3 = 250000",
    "connections": "http1 = 5000\ngrpc = 100",
    "streams": 100,
}

SUITE_TEMPLATE = """name = "test"
rounds = {rounds}
warmup_s = {warmup_s}
duration_s = {duration_s}
smoke = false
targets = {targets}

[rates]
{rates}

[connections]
{connections}

[grpc]
streams = {streams}
"""

SUITE_ERROR_CASES = [
    {"name": "unknown target", "fields": {"targets": '["hello-a", "hello-z"]'}, "loaders": 1, "message": "unknown target hello-z"},
    {"name": "target listed twice", "fields": {"targets": '["hello-a", "hello-a"]'}, "loaders": 1, "message": "target hello-a is listed twice"},
    {"name": "zero rounds", "fields": {"rounds": 0}, "loaders": 1, "message": "rounds 0 is under 1"},
    {"name": "duration one second under the minimum", "fields": {"duration_s": 29}, "loaders": 1, "message": "duration_s 29 is under 30"},
    {"name": "no rate for an app of a target", "fields": {"rates": "hello = 250000"}, "loaders": 1, "message": "no rate for app yii3"},
    {
        "name": "no connections for the proto of a target",
        "fields": {"targets": '["hello-a", "grpc-a"]', "rates": "hello = 250000\ngrpc = 100000", "connections": "http1 = 5000"},
        "loaders": 1,
        "message": "no connections for proto grpc",
    },
    # 250001 % 2 = 1.
    {"name": "rate not a multiple of two loaders", "fields": {"rates": "hello = 250001\nyii3 = 250000"}, "loaders": 2, "message": "rate 250001 of app hello is not a multiple of 2 loaders"},
    # 249999 % 3 = 0, and 5000 % 3 = 2.
    {"name": "connections not a multiple of three loaders", "fields": {"rates": "hello = 249999\nyii3 = 249999"}, "loaders": 3, "message": "connections 5000 of proto http1 is not a multiple of 3 loaders"},
]

SUITE_OK_CASES = [
    {
        "name": "minimum duration, a rate for an app no target uses, and two loaders",
        "fields": {"duration_s": 30, "targets": '["yii3-a", "hello-a"]', "rates": "hello = 250000\nyii3 = 250000\ngrpc = 100000"},
        "loaders": 2,
        "expected": Suite(
            name="test",
            rounds=1,
            warmup_s=10,
            duration_s=30,
            smoke=False,
            rates={"hello": 250000, "yii3": 250000, "grpc": 100000},
            connections={"http1": 5000, "grpc": 100},
            grpc_streams=100,
            targets=(REGISTRY["yii3-a"], REGISTRY["hello-a"]),
        ),
    },
]

PLAN_CASES = [
    {
        "name": "one round keeps the suite order",
        "rounds": 1,
        "targets": ("hello-a", "hello-b", "yii3-a"),
        "expected": ["r1-hello-a", "r1-hello-b", "r1-yii3-a"],
    },
    {
        # Round r starts at index (r - 1) % 4: a, then b, then c.
        "name": "three rounds of four targets rotate by one",
        "rounds": 3,
        "targets": ("a", "b", "c", "d"),
        "expected": [
            "r1-a", "r1-b", "r1-c", "r1-d",
            "r2-b", "r2-c", "r2-d", "r2-a",
            "r3-c", "r3-d", "r3-a", "r3-b",
        ],
    },
]

# The ci row set of spec section 4, in suite order.
CI_TARGETS = (
    "hello-rapira-classic",
    "hello-rapira-worker",
    "hello-rapira-dispatcher",
    "hello-rapira-dispatcher-static",
    "yii3-rapira-dispatcher",
    "grpc-rapira",
)


class LoadTargetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, text: str) -> Path:
        path = Path(self.tmp.name) / "targets.toml"
        path.write_text(text)
        return path

    def test_request_shape_becomes_tuples(self):
        got = load_targets(self.write(VALID_TARGET))
        self.assertEqual(
            got,
            {
                "grpc-rapira": Target(
                    name="grpc-rapira",
                    server="rapira",
                    app="grpc",
                    mode="dispatcher",
                    proto="grpc",
                    start=("grpc", "@RIG@/apps/grpc/php/dispatcher.php"),
                    url="/bench.v1.EchoService/Echo",
                    expect="apps/grpc/expect.grpc",
                    config="servers/rapira/grpc.toml.tpl",
                    method="POST",
                    headers=(("x-note", "a:b"),),
                    body="apps/grpc/echo.grpc",
                )
            },
        )

    def test_invalid_target(self):
        for case in TARGET_ERROR_CASES:
            with self.subTest(name=case["name"]):
                with self.assertRaises(SuiteError) as ctx:
                    load_targets(self.write(case["toml"]))
                self.assertIn(case["message"], str(ctx.exception))


class LoadSuiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, fields: dict) -> Path:
        path = Path(self.tmp.name) / "suite.toml"
        path.write_text(SUITE_TEMPLATE.format(**(SUITE_DEFAULTS | fields)))
        return path

    def test_invalid_suite(self):
        for case in SUITE_ERROR_CASES:
            with self.subTest(name=case["name"]):
                with self.assertRaises(SuiteError) as ctx:
                    load_suite(self.write(case["fields"]), REGISTRY, case["loaders"])
                self.assertIn(case["message"], str(ctx.exception))

    def test_valid_suite(self):
        for case in SUITE_OK_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(load_suite(self.write(case["fields"]), REGISTRY, case["loaders"]), case["expected"])


class PlanCellsTest(unittest.TestCase):
    def test_rotation(self):
        for case in PLAN_CASES:
            with self.subTest(name=case["name"]):
                suite = Suite(
                    name="test",
                    rounds=case["rounds"],
                    warmup_s=10,
                    duration_s=60,
                    smoke=False,
                    rates={"hello": 250000},
                    connections={"http1": 5000},
                    grpc_streams=100,
                    targets=tuple(target(name) for name in case["targets"]),
                )
                got = [cell_key(round_no, t) for round_no, t in plan_cells(suite)]
                self.assertEqual(got, case["expected"])


class ShippedSuitesTest(unittest.TestCase):
    def setUp(self):
        self.registry = load_targets(ROOT / "suites/targets.toml")

    def test_ci_rows_match_the_spec(self):
        suite = load_suite(ROOT / "suites/ci.toml", self.registry, 1)
        self.assertEqual(tuple(t.name for t in suite.targets), CI_TARGETS)
        self.assertEqual((suite.rounds, suite.warmup_s, suite.duration_s), (1, 10, 60))
        self.assertEqual(suite.rates, {"hello": 250000, "yii3": 250000, "grpc": 100000})
        self.assertEqual(suite.connections, {"http1": 5000, "grpc": 100})
        self.assertEqual(suite.grpc_streams, 100)

    def test_every_registry_target_is_in_the_ci_suite(self):
        suite = load_suite(ROOT / "suites/ci.toml", self.registry, 1)
        self.assertEqual({t.name for t in suite.targets}, set(self.registry))


if __name__ == "__main__":
    unittest.main()
