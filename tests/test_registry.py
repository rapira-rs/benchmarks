import tempfile
import unittest
from pathlib import Path

from rig.registry import Stage, Suite, SuiteError, Target, load_suite, load_targets, plan_cells

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

RATE_STAGE = """[stages.rate]
warmup_s = 11
duration_s = 15

[stages.rate.rates]
hello-a = 60000
yii3-a = 16000
"""

CAP_STAGE = """[stages.cap]
warmup_s = 5
duration_s = 15
processes = 2

[stages.cap.rates]
hello-a = 300000
yii3-a = 60000
"""

SUITE_DEFAULTS = {
    "rounds": 1,
    "targets": '["hello-a", "yii3-a"]',
    "connections": "http1 = 1000\ngrpc = 100",
    "streams": 100,
    "stages": RATE_STAGE + "\n" + CAP_STAGE,
}

SUITE_TEMPLATE = """name = "test"
rounds = {rounds}
smoke = false
targets = {targets}

[connections]
{connections}

[grpc]
streams = {streams}

{stages}
"""

SUITE_ERROR_CASES = [
    {"name": "unknown target", "fields": {"targets": '["hello-a", "hello-z"]'}, "loaders": 1, "message": "unknown target hello-z"},
    {"name": "target listed twice", "fields": {"targets": '["hello-a", "hello-a"]'}, "loaders": 1, "message": "target hello-a is listed twice"},
    {"name": "zero rounds", "fields": {"rounds": 0}, "loaders": 1, "message": "rounds 0 is under 1"},
    {
        "name": "no connections for the proto of a target",
        "fields": {"targets": '["hello-a", "grpc-a"]', "connections": "http1 = 1000"},
        "loaders": 1,
        "message": "no connections for proto grpc",
    },
    # 1000 % 3 = 1. The connection check comes before the stage checks.
    {"name": "connections not a multiple of three loaders", "fields": {}, "loaders": 3, "message": "connections 1000 of proto http1 is not a multiple of 3 loaders"},
    {"name": "no cap stage", "fields": {"stages": RATE_STAGE}, "loaders": 1, "message": "suite.toml: the stages are ['rate']; a suite has exactly the stages rate and cap"},
    {
        "name": "a third stage kind",
        "fields": {"stages": RATE_STAGE + "\n" + CAP_STAGE + "\n[stages.warm]\nwarmup_s = 0\nduration_s = 15\n"},
        "loaders": 1,
        "message": "suite.toml: the stages are ['cap', 'rate', 'warm']; a suite has exactly the stages rate and cap",
    },
    # MIN_DURATION_S is 15.
    {
        "name": "rate duration one second under the minimum",
        "fields": {"stages": RATE_STAGE.replace("duration_s = 15", "duration_s = 14") + "\n" + CAP_STAGE},
        "loaders": 1,
        "message": "suite.toml: stages.rate: duration_s 14 is under 15",
    },
    {
        "name": "negative cap warm-up",
        "fields": {"stages": RATE_STAGE + "\n" + CAP_STAGE.replace("warmup_s = 5", "warmup_s = -1")},
        "loaders": 1,
        "message": "suite.toml: stages.cap: warmup_s -1 is negative",
    },
    {
        "name": "processes set in the rate stage",
        "fields": {"stages": RATE_STAGE.replace("duration_s = 15\n", "duration_s = 15\nprocesses = 8\n") + "\n" + CAP_STAGE},
        "loaders": 1,
        "message": "suite.toml: stages.rate: processes is set",
    },
    {
        "name": "cap stage without processes",
        "fields": {"stages": RATE_STAGE + "\n" + CAP_STAGE.replace("processes = 2\n", "")},
        "loaders": 1,
        "message": "suite.toml: stages.cap: processes is missing",
    },
    {
        "name": "cap stage with zero processes",
        "fields": {"stages": RATE_STAGE + "\n" + CAP_STAGE.replace("processes = 2", "processes = 0")},
        "loaders": 1,
        "message": "suite.toml: stages.cap: processes 0 is under 1",
    },
    {
        "name": "no rate for a target in the cap stage",
        "fields": {"stages": RATE_STAGE + "\n" + CAP_STAGE.replace("yii3-a = 60000\n", "")},
        "loaders": 1,
        "message": "suite.toml: stages.cap: no rate for target yii3-a",
    },
    # hello-b is in the registry but not in the suite targets.
    {
        "name": "rate for a name that is not a suite target",
        "fields": {"stages": RATE_STAGE + "hello-b = 60000\n\n" + CAP_STAGE},
        "loaders": 1,
        "message": "suite.toml: stages.rate: a rate for hello-b, which is not a suite target",
    },
    # 60001 % 2 = 1.
    {
        "name": "rate not a multiple of two loaders",
        "fields": {"stages": RATE_STAGE.replace("hello-a = 60000", "hello-a = 60001") + "\n" + CAP_STAGE},
        "loaders": 2,
        "message": "suite.toml: stages.rate: rate 60001 of target hello-a is not a multiple of 2 loaders",
    },
]

SUITE_OK_CASES = [
    {
        # 15 s is MIN_DURATION_S, a warm-up of 0 s is valid, and every rate and connection count divides by 2 loaders.
        "name": "minimum duration, zero warm-up, and two loaders",
        "fields": {"targets": '["yii3-a", "hello-a"]', "stages": RATE_STAGE.replace("warmup_s = 11", "warmup_s = 0") + "\n" + CAP_STAGE},
        "loaders": 2,
        "expected": Suite(
            name="test",
            rounds=1,
            smoke=False,
            connections={"http1": 1000, "grpc": 100},
            grpc_streams=100,
            targets=(REGISTRY["yii3-a"], REGISTRY["hello-a"]),
            stages={
                "rate": Stage(kind="rate", warmup_s=0, duration_s=15, processes=None, rates={"hello-a": 60000, "yii3-a": 16000}),
                "cap": Stage(kind="cap", warmup_s=5, duration_s=15, processes=2, rates={"hello-a": 300000, "yii3-a": 60000}),
            },
        ),
    },
]

PLAN_CASES = [
    {
        # Round 1 runs base then new. The rate pair of a target runs before its cap pair.
        "name": "one round of two targets",
        "rounds": 1,
        "targets": ("a", "b"),
        "expected": [
            "r1-base-rate-a", "r1-new-rate-a", "r1-base-cap-a", "r1-new-cap-a",
            "r1-base-rate-b", "r1-new-rate-b", "r1-base-cap-b", "r1-new-cap-b",
        ],
    },
    {
        # Round r starts at target index (r - 1) % 3: a, then b, then c.
        # Odd rounds run base then new, even rounds new then base.
        "name": "three rounds of three targets rotate the targets and alternate the builds",
        "rounds": 3,
        "targets": ("a", "b", "c"),
        "expected": [
            "r1-base-rate-a", "r1-new-rate-a", "r1-base-cap-a", "r1-new-cap-a",
            "r1-base-rate-b", "r1-new-rate-b", "r1-base-cap-b", "r1-new-cap-b",
            "r1-base-rate-c", "r1-new-rate-c", "r1-base-cap-c", "r1-new-cap-c",
            "r2-new-rate-b", "r2-base-rate-b", "r2-new-cap-b", "r2-base-cap-b",
            "r2-new-rate-c", "r2-base-rate-c", "r2-new-cap-c", "r2-base-cap-c",
            "r2-new-rate-a", "r2-base-rate-a", "r2-new-cap-a", "r2-base-cap-a",
            "r3-base-rate-c", "r3-new-rate-c", "r3-base-cap-c", "r3-new-cap-c",
            "r3-base-rate-a", "r3-new-rate-a", "r3-base-cap-a", "r3-new-cap-a",
            "r3-base-rate-b", "r3-new-rate-b", "r3-base-cap-b", "r3-new-cap-b",
        ],
    },
]

# The ci row set of the spec, in suite order.
CI_TARGETS = (
    "hello-rapira-classic",
    "hello-rapira-worker",
    "hello-rapira-dispatcher",
    "hello-rapira-dispatcher-static",
    "yii3-rapira-dispatcher",
    "grpc-rapira",
)

# The stages of suites/ci.toml in the spec.
CI_STAGES = {
    "rate": Stage(
        kind="rate",
        warmup_s=11,
        duration_s=15,
        processes=None,
        rates={
            "hello-rapira-classic": 40000,
            "hello-rapira-worker": 60000,
            "hello-rapira-dispatcher": 60000,
            "hello-rapira-dispatcher-static": 60000,
            "yii3-rapira-dispatcher": 16000,
            "grpc-rapira": 50000,
        },
    ),
    "cap": Stage(
        kind="cap",
        warmup_s=5,
        duration_s=15,
        processes=2,
        rates={
            "hello-rapira-classic": 300000,
            "hello-rapira-worker": 300000,
            "hello-rapira-dispatcher": 300000,
            "hello-rapira-dispatcher-static": 300000,
            "yii3-rapira-dispatcher": 60000,
            "grpc-rapira": 300000,
        },
    ),
}


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
    def test_order(self):
        for case in PLAN_CASES:
            with self.subTest(name=case["name"]):
                targets = tuple(target(name) for name in case["targets"])
                rates = {name: 1000 for name in case["targets"]}
                suite = Suite(
                    name="test",
                    rounds=case["rounds"],
                    smoke=False,
                    connections={"http1": 1000},
                    grpc_streams=100,
                    targets=targets,
                    stages={
                        "rate": Stage(kind="rate", warmup_s=11, duration_s=15, processes=None, rates=rates),
                        "cap": Stage(kind="cap", warmup_s=5, duration_s=15, processes=2, rates=rates),
                    },
                )
                self.assertEqual([cell.key for cell in plan_cells(suite)], case["expected"])


class ShippedSuitesTest(unittest.TestCase):
    def setUp(self):
        self.registry = load_targets(ROOT / "suites/targets.toml")

    def test_ci_rows_match_the_spec(self):
        # The rig has 2 loaders.
        suite = load_suite(ROOT / "suites/ci.toml", self.registry, 2)
        self.assertEqual(tuple(t.name for t in suite.targets), CI_TARGETS)
        self.assertEqual(suite.rounds, 3)
        self.assertEqual(suite.connections, {"http1": 1000, "grpc": 100})
        self.assertEqual(suite.grpc_streams, 100)
        self.assertEqual(suite.stages, CI_STAGES)

    def test_every_registry_target_is_in_the_ci_suite(self):
        suite = load_suite(ROOT / "suites/ci.toml", self.registry, 1)
        self.assertEqual({t.name for t in suite.targets}, set(self.registry))


if __name__ == "__main__":
    unittest.main()
