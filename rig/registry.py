"""Load the target registry and a suite file, and plan the cells of a run."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

SERVERS = ("rapira", "frankenphp", "php-fpm", "nginx-rapira", "roadrunner")
APPS = ("hello", "symfony", "laravel", "static", "grpc")
MODES = ("worker", "classic", "dispatcher")
PROTOS = ("http1", "grpc")
BINARIES = ("pr", "base")
# Only these servers run a rapira binary, so only they carry a binary field.
RAPIRA_SERVERS = ("rapira", "nginx-rapira")
MIN_STAGE_S = 12


class SuiteError(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    name: str
    server: str
    app: str
    mode: str
    proto: str
    binary: str | None
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
    stage_s: int
    connections: int
    smoke: bool
    floors: dict[str, int]
    targets: tuple[Target, ...]


def _target(name: str, table: dict) -> Target:
    fields = dict(table)
    fields.setdefault("binary", None)
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
    if target.server in RAPIRA_SERVERS:
        if target.binary not in BINARIES:
            raise SuiteError(f"target {name}: binary must be one of {', '.join(BINARIES)}")
    elif target.binary is not None:
        raise SuiteError(f"target {name}: binary applies only to {', '.join(RAPIRA_SERVERS)}")
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
    if doc["stage_s"] < MIN_STAGE_S:
        raise SuiteError(f"{where}: stage_s {doc['stage_s']} is under {MIN_STAGE_S}")
    if doc["connections"] % loader_count != 0:
        raise SuiteError(f"{where}: connections {doc['connections']} is not a multiple of {loader_count} loaders")
    seen = set()
    for name in doc["targets"]:
        if name not in targets:
            raise SuiteError(f"{where}: unknown target {name}")
        if name in seen:
            raise SuiteError(f"{where}: target {name} is listed twice")
        seen.add(name)
    chosen = tuple(targets[name] for name in doc["targets"])
    floors = dict(doc["floors"])
    for target in chosen:
        if target.app not in floors:
            raise SuiteError(f"{where}: no floor for app {target.app}")
    for app, floor in floors.items():
        if floor % loader_count != 0:
            raise SuiteError(f"{where}: floor {floor} of app {app} is not a multiple of {loader_count} loaders")
    return Suite(
        name=doc["name"],
        rounds=doc["rounds"],
        stage_s=doc["stage_s"],
        connections=doc["connections"],
        smoke=doc.get("smoke", False),
        floors=floors,
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
