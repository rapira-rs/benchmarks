"""Load the target registry and a suite file, and plan the cells of a run."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

SERVERS = ("rapira",)
APPS = ("hello", "yii3", "grpc")
MODES = ("worker", "classic", "dispatcher")
PROTOS = ("http1", "grpc")
MIN_DURATION_S = 30


class SuiteError(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    name: str
    server: str
    app: str
    mode: str
    proto: str
    start: tuple[str, ...]
    url: str
    expect: str
    config: str
    method: str = "GET"
    headers: tuple[tuple[str, str], ...] = ()
    body: str | None = None


@dataclass(frozen=True)
class Suite:
    name: str
    rounds: int
    warmup_s: int
    duration_s: int
    smoke: bool
    rates: dict[str, int]
    connections: dict[str, int]
    grpc_streams: int
    targets: tuple[Target, ...]


def _target(name: str, table: dict) -> Target:
    fields = dict(table)
    fields["start"] = tuple(fields.get("start", ()))
    fields["headers"] = tuple(fields.get("headers", {}).items())
    try:
        target = Target(name=name, **fields)
    except TypeError as exc:
        raise SuiteError(f"target {name}: {exc}") from None
    for field, allowed in (("server", SERVERS), ("app", APPS), ("mode", MODES), ("proto", PROTOS)):
        value = getattr(target, field)
        if value not in allowed:
            raise SuiteError(f"target {name}: unknown {field} {value}")
    return target


def load_targets(path: Path) -> dict[str, Target]:
    with path.open("rb") as f:
        tables = tomllib.load(f)
    return {name: _target(name, table) for name, table in tables.items()}


def load_suite(path: Path, targets: dict[str, Target], loader_count: int) -> Suite:
    with path.open("rb") as f:
        doc = tomllib.load(f)
    where = path.name
    if doc["rounds"] < 1:
        raise SuiteError(f"{where}: rounds {doc['rounds']} is under 1")
    if doc["duration_s"] < MIN_DURATION_S:
        raise SuiteError(f"{where}: duration_s {doc['duration_s']} is under {MIN_DURATION_S}")
    seen = set()
    for name in doc["targets"]:
        if name not in targets:
            raise SuiteError(f"{where}: unknown target {name}")
        if name in seen:
            raise SuiteError(f"{where}: target {name} is listed twice")
        seen.add(name)
    chosen = tuple(targets[name] for name in doc["targets"])
    rates = dict(doc["rates"])
    connections = dict(doc["connections"])
    for target in chosen:
        if target.app not in rates:
            raise SuiteError(f"{where}: no rate for app {target.app}")
        if target.proto not in connections:
            raise SuiteError(f"{where}: no connections for proto {target.proto}")
    # Each loader sends its share of the rate and opens its share of the connections.
    for app, rate in rates.items():
        if rate % loader_count != 0:
            raise SuiteError(f"{where}: rate {rate} of app {app} is not a multiple of {loader_count} loaders")
    for proto, count in connections.items():
        if count % loader_count != 0:
            raise SuiteError(f"{where}: connections {count} of proto {proto} is not a multiple of {loader_count} loaders")
    return Suite(
        name=doc["name"],
        rounds=doc["rounds"],
        warmup_s=doc["warmup_s"],
        duration_s=doc["duration_s"],
        smoke=doc.get("smoke", False),
        rates=rates,
        connections=connections,
        grpc_streams=doc["grpc"]["streams"],
        targets=chosen,
    )


def plan_cells(suite: Suite) -> list[tuple[int, Target]]:
    # Round r starts at target index (r - 1) % n and wraps, so every target
    # takes each position once over n rounds.
    n = len(suite.targets)
    return [
        (round_no, suite.targets[(i + round_no - 1) % n])
        for round_no in range(1, suite.rounds + 1)
        for i in range(n)
    ]


def cell_key(round_no: int, target: Target) -> str:
    return f"r{round_no}-{target.name}"


def suite_needs(suite: Suite) -> list[str]:
    """Server kinds, then apps, of the suite targets. Provisioning installs only these."""
    return sorted({target.server for target in suite.targets}) + sorted({target.app for target in suite.targets})
