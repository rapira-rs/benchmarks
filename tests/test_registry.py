import tempfile
import unittest
from pathlib import Path

from rig.registry import Suite, SuiteError, Target, cell_key, load_suite, load_targets, plan_cells

ROOT = Path(__file__).resolve().parent.parent

VALID_TARGET = """
[grpc-rapira-grpcweb]
server = "rapira"
app = "grpc"
mode = "dispatcher"
proto = "http1"
binary = "pr"
start = ["grpc", "@RIG@/apps/grpc/php/dispatcher.php"]
url = "/bench.v1.EchoService/Echo"
expect = "apps/grpc/expect.grpcweb"
config = "servers/rapira/grpc.toml.tpl"
method = "POST"
body = "apps/grpc/echo.grpc"

[grpc-rapira-grpcweb.headers]
content-type = "application/grpc-web+proto"
x-grpc-web = "1"
"""

# Each error case is a valid php-fpm, rapira, or frankenphp target with one field changed, added, or removed.
TARGET_ERROR_CASES = [
    {
        "name": "unknown server",
        "toml": '[t]\nserver = "caddy"\napp = "hello"\nmode = "worker"\nproto = "http1"\nstart = []\nurl = "/"\nexpect = "e"\nconfig = "c"\n',
        "message": "unknown server caddy",
    },
    {
        "name": "unknown app",
        "toml": '[t]\nserver = "php-fpm"\napp = "wordpress"\nmode = "classic"\nproto = "http1"\nstart = []\nurl = "/"\nexpect = "e"\nconfig = "c"\n',
        "message": "unknown app wordpress",
    },
    {
        "name": "unknown mode",
        "toml": '[t]\nserver = "php-fpm"\napp = "hello"\nmode = "cgi"\nproto = "http1"\nstart = []\nurl = "/"\nexpect = "e"\nconfig = "c"\n',
        "message": "unknown mode cgi",
    },
    {
        "name": "unknown proto",
        "toml": '[t]\nserver = "php-fpm"\napp = "hello"\nmode = "classic"\nproto = "http2"\nstart = []\nurl = "/"\nexpect = "e"\nconfig = "c"\n',
        "message": "unknown proto http2",
    },
    {
        "name": "rapira target without a binary",
        "toml": '[t]\nserver = "rapira"\napp = "hello"\nmode = "worker"\nproto = "http1"\nstart = []\nurl = "/"\nexpect = "e"\nconfig = "c"\n',
        "message": "binary must be one of pr, base",
    },
    {
        "name": "rapira target with an unknown binary",
        "toml": '[t]\nserver = "nginx-rapira"\napp = "hello"\nmode = "worker"\nproto = "http1"\nbinary = "main"\nstart = []\nurl = "/"\nexpect = "e"\nconfig = "c"\n',
        "message": "binary must be one of pr, base",
    },
    {
        "name": "binary on a server that runs no rapira binary",
        "toml": '[t]\nserver = "frankenphp"\napp = "hello"\nmode = "worker"\nproto = "http1"\nbinary = "pr"\nstart = []\nurl = "/"\nexpect = "e"\nconfig = "c"\n',
        "message": "binary applies only to rapira, nginx-rapira",
    },
    {
        "name": "misspelled field",
        "toml": '[t]\nserver = "php-fpm"\napp = "hello"\nmode = "classic"\nproto = "http1"\nstart = []\nurl = "/"\nexpect = "e"\nconfig = "c"\nheader = "x: 1"\n',
        "message": "header",
    },
    {
        "name": "missing url",
        "toml": '[t]\nserver = "php-fpm"\napp = "hello"\nmode = "classic"\nproto = "http1"\nstart = []\nexpect = "e"\nconfig = "c"\n',
        "message": "url",
    },
]


def target(name: str, app: str = "hello") -> Target:
    return Target(
        name=name,
        server="php-fpm",
        app=app,
        mode="classic",
        proto="http1",
        binary=None,
        start=("@RIG@/apps/hello", "fpm.php"),
        url="/?name=you",
        expect="apps/hello/expect.txt",
        config="servers/php-fpm/php-fpm.conf.tpl",
    )


REGISTRY = {
    "hello-a": target("hello-a"),
    "hello-b": target("hello-b"),
    "symfony-a": target("symfony-a", app="symfony"),
}

SUITE_DEFAULTS = {
    "rounds": 1,
    "stage_s": 20,
    "connections": 256,
    "targets": '["hello-a", "symfony-a"]',
    "floors": "hello = 10000\nsymfony = 10000",
}

SUITE_TEMPLATE = """name = "test"
rounds = {rounds}
stage_s = {stage_s}
connections = {connections}
smoke = false
targets = {targets}

[floors]
{floors}
"""

SUITE_ERROR_CASES = [
    {"name": "unknown target", "fields": {"targets": '["hello-a", "hello-z"]'}, "loaders": 4, "message": "unknown target hello-z"},
    {"name": "target listed twice", "fields": {"targets": '["hello-a", "hello-a"]'}, "loaders": 4, "message": "target hello-a is listed twice"},
    {"name": "zero rounds", "fields": {"rounds": 0}, "loaders": 4, "message": "rounds 0 is under 1"},
    {"name": "stage one second under the minimum", "fields": {"stage_s": 11}, "loaders": 4, "message": "stage_s 11 is under 12"},
    {"name": "connections not a multiple of the loaders", "fields": {"connections": 258}, "loaders": 4, "message": "connections 258 is not a multiple of 4 loaders"},
    {"name": "no floor for an app of a target", "fields": {"floors": "hello = 10000"}, "loaders": 4, "message": "no floor for app symfony"},
    # 10002 % 4 = 2.
    {"name": "floor not a multiple of four loaders", "fields": {"floors": "hello = 10002\nsymfony = 10000"}, "loaders": 4, "message": "floor 10002 of app hello is not a multiple of 4 loaders"},
    # 255 % 3 = 0, and 10000 % 3 = 1.
    {"name": "floor not a multiple of three loaders", "fields": {"connections": 255}, "loaders": 3, "message": "floor 10000 of app hello is not a multiple of 3 loaders"},
]

SUITE_OK_CASES = [
    {
        "name": "minimum stage and a floor for an app no target uses",
        "fields": {"stage_s": 12, "targets": '["symfony-a", "hello-a"]', "floors": "hello = 10000\nsymfony = 10000\nlaravel = 5000"},
        "loaders": 4,
        "expected": Suite(
            name="test",
            rounds=1,
            stage_s=12,
            connections=256,
            smoke=False,
            floors={"hello": 10000, "symfony": 10000, "laravel": 5000},
            targets=(REGISTRY["symfony-a"], REGISTRY["hello-a"]),
        ),
    },
]

PLAN_CASES = [
    {
        "name": "one round keeps the suite order",
        "rounds": 1,
        "targets": ("hello-a", "hello-b", "symfony-a"),
        "expected": ["r1-hello-a", "r1-hello-b", "r1-symfony-a"],
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

CI_TARGETS = (
    "hello-rapira-worker",
    "hello-rapira-dispatcher",
    "hello-rapira-classic",
    "hello-frankenphp-worker",
    "hello-php-fpm",
    "symfony-rapira-worker",
    "symfony-rapira-classic",
    "symfony-frankenphp-worker",
    "symfony-frankenphp-classic",
    "symfony-php-fpm",
    "laravel-rapira-worker",
    "laravel-frankenphp-worker",
    "static-rapira-hit",
    "static-frankenphp-hit",
    "grpc-rapira",
    "grpc-roadrunner",
)

# The CI row set comes from spec section 4.2. Full adds 12 rows to the 16 CI rows.
# AB runs hello in three modes and Symfony in two modes, each for pr and base: 10 rows.
SHIPPED_SUITE_CASES = [
    {"name": "ci", "file": "suites/ci.toml", "rounds": 1, "count": 16},
    {"name": "full", "file": "suites/full.toml", "rounds": 3, "count": 28},
    {"name": "ab", "file": "suites/ab.toml", "rounds": 3, "count": 10},
]


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
                "grpc-rapira-grpcweb": Target(
                    name="grpc-rapira-grpcweb",
                    server="rapira",
                    app="grpc",
                    mode="dispatcher",
                    proto="http1",
                    binary="pr",
                    start=("grpc", "@RIG@/apps/grpc/php/dispatcher.php"),
                    url="/bench.v1.EchoService/Echo",
                    expect="apps/grpc/expect.grpcweb",
                    config="servers/rapira/grpc.toml.tpl",
                    method="POST",
                    headers=(("content-type", "application/grpc-web+proto"), ("x-grpc-web", "1")),
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
                    stage_s=20,
                    connections=256,
                    smoke=False,
                    floors={"hello": 10000},
                    targets=tuple(target(name) for name in case["targets"]),
                )
                got = [cell_key(round_no, t) for round_no, t in plan_cells(suite)]
                self.assertEqual(got, case["expected"])


class ShippedSuitesTest(unittest.TestCase):
    def setUp(self):
        self.registry = load_targets(ROOT / "suites/targets.toml")

    def test_suite_loads_against_the_registry(self):
        for case in SHIPPED_SUITE_CASES:
            with self.subTest(name=case["name"]):
                suite = load_suite(ROOT / case["file"], self.registry, 4)
                self.assertEqual(suite.name, case["name"])
                self.assertEqual(suite.rounds, case["rounds"])
                self.assertEqual(len(suite.targets), case["count"])

    def test_ci_rows_match_the_spec(self):
        suite = load_suite(ROOT / "suites/ci.toml", self.registry, 4)
        self.assertEqual(tuple(t.name for t in suite.targets), CI_TARGETS)

    def test_every_registry_target_is_in_a_suite(self):
        used = set()
        for case in SHIPPED_SUITE_CASES:
            used |= {t.name for t in load_suite(ROOT / case["file"], self.registry, 4).targets}
        self.assertEqual(used, set(self.registry))


if __name__ == "__main__":
    unittest.main()
