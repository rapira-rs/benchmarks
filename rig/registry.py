"""Load the target registry and a suite file, and plan the cells of a run."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

SERVERS = ("rapira",)
APPS = ("hello", "yii3", "grpc")
MODES = ("worker", "classic", "dispatcher")
PROTOS = ("http1", "grpc")
MIN_DURATION_S = 15
STAGE_KINDS = ("rate", "cap")
BUILDS = ("base", "new")


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
class Stage:
    warmup_s: int
    duration_s: int
    # None for the rate stage, which runs one process per server vCPU.
    processes: int | None
    # Target name to the total req/s over all loaders.
    rates: dict[str, int]


@dataclass(frozen=True)
class Suite:
    name: str
    rounds: int
    smoke: bool
    connections: dict[str, int]
    grpc_streams: int
    targets: tuple[Target, ...]
    # Exactly the keys of STAGE_KINDS.
    stages: dict[str, Stage]


@dataclass(frozen=True)
class PlannedCell:
    round_no: int
    build: str
    stage: str
    target: Target

    @property
    def key(self) -> str:
        return f"r{self.round_no}-{self.build}-{self.stage}-{self.target.name}"


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


def _stage(where: str, kind: str, table: dict, chosen: tuple[Target, ...], loader_count: int) -> Stage:
    """One [stages.KIND] table of a suite file."""
    at = f"{where}: stages.{kind}"
    if table["duration_s"] < MIN_DURATION_S:
        raise SuiteError(f"{at}: duration_s {table['duration_s']} is under {MIN_DURATION_S}")
    if table["warmup_s"] < 0:
        raise SuiteError(f"{at}: warmup_s {table['warmup_s']} is negative")
    processes = table.get("processes")
    if kind == "rate" and processes is not None:
        raise SuiteError(f"{at}: processes is set; the rate stage runs one process per server vCPU")
    if kind == "cap":
        if processes is None:
            raise SuiteError(f"{at}: processes is missing")
        if processes < 1:
            raise SuiteError(f"{at}: processes {processes} is under 1")
    rates = dict(table["rates"])
    for target in chosen:
        if target.name not in rates:
            raise SuiteError(f"{at}: no rate for target {target.name}")
    names = {target.name for target in chosen}
    for name, rate in rates.items():
        if name not in names:
            raise SuiteError(f"{at}: a rate for {name}, which is not a suite target")
        # Each loader sends its share of the rate.
        if rate % loader_count != 0:
            raise SuiteError(f"{at}: rate {rate} of target {name} is not a multiple of {loader_count} loaders")
    return Stage(warmup_s=table["warmup_s"], duration_s=table["duration_s"], processes=processes, rates=rates)


def load_suite(path: Path, targets: dict[str, Target], loader_count: int) -> Suite:
    with path.open("rb") as f:
        doc = tomllib.load(f)
    where = path.name
    if doc["rounds"] < 1:
        raise SuiteError(f"{where}: rounds {doc['rounds']} is under 1")
    seen = set()
    for name in doc["targets"]:
        if name not in targets:
            raise SuiteError(f"{where}: unknown target {name}")
        if name in seen:
            raise SuiteError(f"{where}: target {name} is listed twice")
        seen.add(name)
    chosen = tuple(targets[name] for name in doc["targets"])
    connections = dict(doc["connections"])
    for target in chosen:
        if target.proto not in connections:
            raise SuiteError(f"{where}: no connections for proto {target.proto}")
    # Each loader opens its share of the connections.
    for proto, count in connections.items():
        if count % loader_count != 0:
            raise SuiteError(f"{where}: connections {count} of proto {proto} is not a multiple of {loader_count} loaders")
    tables = doc.get("stages", {})
    if sorted(tables) != sorted(STAGE_KINDS):
        raise SuiteError(f"{where}: the stages are {sorted(tables)}; a suite has exactly the stages rate and cap")
    return Suite(
        name=doc["name"],
        rounds=doc["rounds"],
        smoke=doc.get("smoke", False),
        connections=connections,
        grpc_streams=doc["grpc"]["streams"],
        targets=chosen,
        stages={kind: _stage(where, kind, tables[kind], chosen, loader_count) for kind in STAGE_KINDS},
    )


def plan_cells(suite: Suite) -> list[PlannedCell]:
    # Round r starts at target index (r - 1) % n and wraps, so every target
    # takes each position once over n rounds. The two cells of a pair run back
    # to back: base first in odd rounds, new first in even rounds. The rate pair
    # of a target runs before its cap pair.
    n = len(suite.targets)
    cells = []
    for round_no in range(1, suite.rounds + 1):
        builds = BUILDS if round_no % 2 else BUILDS[::-1]
        for i in range(n):
            target = suite.targets[(i + round_no - 1) % n]
            for stage in STAGE_KINDS:
                for build in builds:
                    cells.append(PlannedCell(round_no=round_no, build=build, stage=stage, target=target))
    return cells


def suite_needs(suite: Suite) -> list[str]:
    """Server kinds, then apps, of the suite targets. Provisioning installs only these."""
    return sorted({target.server for target in suite.targets}) + sorted({target.app for target in suite.targets})
