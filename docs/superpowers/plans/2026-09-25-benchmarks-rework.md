# Benchmarks Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the rapira benchmark rig with a staged rate ladder driven from four small loaders, one run file format, a public board, a FrankenPHP HTTP row set, and a per-merge CI run.

**Architecture:** The operator side is one Python 3 package `rig` (standard library only) that reads a target registry and a suite file, drives the boxes over ssh, runs the ladder, merges the loader records, and writes `run.json`. The boxes run small bash scripts: provision, start and stop a target, probe, snapshot, and run one load process per stage. Terraform creates one server and N loaders; a second stack creates the CI role and the state bucket. A static board on `gh-pages` reads the run files.

**Tech Stack:** Python 3.11+ (stdlib: tomllib, json, subprocess, threading, statistics, unittest), bash, Terraform >= 1.10 with the aws provider ~> 6.0, wrk2 at commit `44a94c17d8e6a0bac8559b53da76848e430cb7a7`, k6 2.2.0, FrankenPHP 1.12.7 (glibc asset), RoadRunner 2025.1.15, PECL protobuf 5.36.2, Chart.js 4 vendored, GitHub Actions with OIDC.

**Spec:** `docs/superpowers/specs/2026-09-25-benchmarks-rework-design.md`

**Branch:** `feat/bench-rework`. Never commit to `main`. Every commit is `git commit -s -S` with a Conventional Commits subject and no AI attribution trailer.

**Pull request:** all 18 tasks land in one pull request from `feat/bench-rework`. Tasks 1 to 15 give a working manual rig; tasks 16 to 18 add the board, the CI bootstrap stack, and the workflows. The publish job commits run data to `gh-pages` with the Actions token, so those commits carry a sign-off only; the owner accepted this.

## Global Constraints

- Operator-side code is Python 3.11 or later, standard library only. Box-side code is bash with `set -euo pipefail`, simple constructs, no arrays of arrays, no `coproc`, no `mapfile` where a `while read` loop does.
- All English in code comments, docs, commit messages, and test names follows ASD-STE100 Simplified Technical English: short sentences, active voice, one instruction per sentence. No em-dashes or en-dashes anywhere; use a dash or a colon. Comments state what or why, never "instead of", "previously", or "deliberately not". No numbered-step comments. No all-caps emphasis.
- Docs never hard-wrap prose: one paragraph is one line, one bullet is one line.
- Tests use `unittest`. Test cases live in flat tables with an explicit `name` field, iterated with `subTest(name=...)`. No loops over raw value lists and no nested loops over input lists. Expected values are derived from `METHOD.md` rules and written before the implementation is read. No trivial tests (a field equals its default) and no smoke tests.
- Dependencies are mainstream only. The only exception is wrk2, pinned by commit and justified in the PR description as the reference coordinated-omission tool that the rig already builds from source like wrk.
- No AWS profile name anywhere. Local runs use the default AWS CLI profile. CI uses the OIDC role. Region `eu-central-1`, AZ default `eu-central-1a`.
- The thing under test is a target. A ladder step is a rate. The word "leg" does not appear in new code or docs.
- Ports: a target listens on 8080. The rapira process behind nginx listens on 127.0.0.1:8081. php-fpm listens on 127.0.0.1:9000. The port 8080 value lives in one place on the operator side (`rig/bench.py: PORT`) and one place on the boxes (`box/lib.sh: PORT`).
- Every target runs `PROCESSES` workers, default the server CPU count. A worker-count mismatch after start fails the start.
- The old drivers, `scripts/remote-lib.sh`, `scripts/leg.sh`, `scripts/fleet-leg.sh`, `scripts/report.py`, `scripts/test_*.py`, `fleet/`, `php/`, `k6/`, and `grpc/` are removed or moved by the tasks that replace them. `results/` stays untouched and stays ignored. New runs go to `runs/`, which is ignored.

## Review Focus

- A loader that returns no `RESULT` line for a stage (ssh drop, wrk2 crash): the stage is invalid and the cell is void with the loader name in the reason; the driver never merges a partial stage. Test in Task 3 (`merge` raises `MissingLoader`) and Task 14 (the cell becomes void).
- A loader that starts a stage late (ssh setup slower than the 3 s lead): `box/load.sh` starts at once and reports `late_ms`; a stage with any loader later than 1000 ms is invalid and voids the cell. Test in Task 3 (`merge` raises `LateLoader`) and Task 11 (`load.sh` prints `late_ms` when the epoch has passed).
- A suite whose floor is not a multiple of the loader count, or whose `stage_s` is under 12, or whose connections are not a multiple of the loader threads: the driver refuses the suite before it creates a run directory. Test in Task 1 (`load_suite` raises `SuiteError`) and Task 14 (`plan_run` checks the connections against the loader vCPU count).
- An interrupted run (Ctrl-C or a lost ssh session while a target runs): the target is stopped and `run.json` is written with `status = "incomplete"` and the planned cells that never ran. Test in Task 14 with a fake ssh layer that raises `KeyboardInterrupt` during a stage.
- A target that answers the probe with the right bytes but returns 404 or 500 under load (a misrouted docroot after the first request): the stage fails on `errors.status`, `peak` counts only successful responses, and the report shows the fail reason. Test in Task 2 (`evaluate_stage` with status errors) and Task 6 (the report row shows `fail: status errors`).

---

## File map

| Path | Responsibility |
| --- | --- |
| `Makefile` | Operator entry points: `up`, `provision`, `bench`, `report`, `compare`, `status`, `extend`, `sync`, `lock`, `down`, `nuke`, `test`. |
| `README.md` | Operations: flow, knobs, cost, teardown, CI setup steps for the owner. |
| `METHOD.md` | Method: ladder, pass rule, flags with thresholds, voids, review checklist, reading the board. |
| `NOTES.md` | Dated records. Gains one entry that marks the method change. |
| `terraform/main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`, `backend.tf`, `cloud-init/bootstrap.sh` | The rig stack: one server, `loader_count` loaders, list outputs, optional S3 backend. |
| `terraform/ci/main.tf`, `variables.tf`, `outputs.tf`, `versions.tf` | The bootstrap stack: OIDC provider, CI role and policy, state bucket. |
| `rig/__init__.py` | Package marker with `VERSION = "1"`. |
| `rig/__main__.py` | CLI: `bench`, `report`, `compare`, `publish`. |
| `rig/registry.py` | Load `suites/targets.toml` and a suite file into `Target` and `Suite`; plan the rotated cells. |
| `rig/ladder.py` | Stage rates, the pass rule, held and peak and unloaded selection. Pure. |
| `rig/merge.py` | Parse `RESULT` lines into `LoaderRecord`; merge records into one stage. Pure. |
| `rig/flags.py` | Parse snapshots; CPU percent; ENA deltas; stage and cell flag rules; void rules. Pure. |
| `rig/runfile.py` | Build and write `run.json`; cell and run status rules. Pure. |
| `rig/ssh.py` | ssh and scp wrappers, parallel execution, staging the tree to the boxes. |
| `rig/rig.py` | `Rig` from terraform outputs, TTL arming and checks. |
| `rig/bench.py` | The cell sequence and the ladder loop; writes raw evidence and the run file. |
| `rig/report.py` | Text tables from a run file; exit status. |
| `rig/compare.py` | Deltas between two run files. |
| `rig/publish.py` | Write `data/<id>.json` and update `index.json` in a gh-pages checkout. |
| `box/lib.sh` | Shared on-box helpers: paths, `PORT`, port checks, probes, launch, wait. |
| `box/target.sh` | Dispatcher: `start`, `stop`, `probe`, `mem`, `log` for a target; calls `box/servers/<server>.sh`. |
| `box/servers/rapira.sh`, `frankenphp.sh`, `php-fpm.sh`, `nginx-rapira.sh`, `roadrunner.sh` | Start and stop one server kind; render its config; verify listener, executable, worker count. |
| `box/snapshot.sh` | Print `cpu`, `ena`, and `conns` lines. |
| `box/probe.sh` | Byte-exact probe of a URL against an expect file. |
| `box/load.sh` | Run one wrk2 or k6 process at a shared start time; print the `RESULT` line. |
| `box/provision-server.sh`, `box/provision-loader.sh` | Provisioning, gated by the suite's needs. |
| `box/lock-apps.sh` | Produce the framework `composer.lock` files. |
| `servers/php.ini` | The shared PHP ini. |
| `servers/rapira/http.toml.tpl`, `static.toml.tpl`, `grpc.toml.tpl` | rapira config templates. |
| `servers/frankenphp/worker.Caddyfile.tpl`, `classic.Caddyfile.tpl`, `stock.Caddyfile.tpl` | FrankenPHP config templates. |
| `servers/php-fpm/php-fpm.conf.tpl`, `servers/nginx/fpm.conf.tpl`, `servers/nginx/rapira.conf.tpl` | php-fpm and nginx templates. |
| `servers/roadrunner/grpc.rr.yaml.tpl`, `servers/roadrunner/composer.json`, `composer.lock` | RoadRunner gRPC config and PHP deps. |
| `apps/hello/classic.php`, `dispatcher.php`, `worker.php`, `frankenphp.php`, `fpm.php`, `expect.txt` | The hello workload, one entry per runtime, one expected body. |
| `apps/static/tiny.css`, `app.css`, `tiny.expect` | Static assets and the expected body of the hit. |
| `apps/symfony/*`, `apps/laravel/*` | Framework app files, controllers, worker entries, `composer.json`, `composer.lock`. |
| `apps/grpc/bench.proto`, `bench.binpb`, `buf.gen.yaml`, `echo.*`, `expect.*`, `php/*`, `fixtures.py` | The gRPC workload. |
| `loader/wrk2-report.lua` | The wrk2 `done()` reporter. |
| `loader/k6-grpc.js` | The k6 gRPC stage script with `handleSummary` printing a `RESULT` line. |
| `suites/targets.toml`, `ci.toml`, `full.toml`, `ab.toml` | Target registry and suites. |
| `board/index.html`, `app.js`, `style.css`, `chart.umd.js`, `VENDOR.md` | The static board. |
| `tests/test_registry.py`, `test_ladder.py`, `test_merge.py`, `test_flags.py`, `test_runfile.py`, `test_report.py`, `test_compare.py`, `test_publish.py`, `test_bench.py`, `test_box.py` | Unit tests and the container test. |
| `.github/workflows/bench.yml` | The bench and publish jobs. |
| `docs/core-dispatch.yml` | The workflow the core repository runs to dispatch a bench. |
| `tests/box.Dockerfile` | The Fedora image of the container test. |
| `tests/fixtures/` | Shared JSON fixtures for the board and report tests. |
| `box/build-local.sh` | Build the local working tree on the server for `make sync`. |
| `terraform/nuke.sh` | Tag-scoped teardown without state. |
| `terraform/backend.tf.s3`, `terraform/s3.tfbackend` | The S3 backend block and its settings, used with `TF_BACKEND=s3`. |

## Interface contract

Every task follows these definitions exactly. A task that needs a name not defined here defines it in its own `Interfaces: Produces` block, and a later task that uses it names it in `Consumes`.

### Registry files

`suites/targets.toml`: one table per target, keyed by the target name.

```toml
[hello-rapira-worker]
server = "rapira"            # rapira | frankenphp | php-fpm | nginx-rapira | roadrunner
app = "hello"                # hello | symfony | laravel | static | grpc
mode = "worker"              # worker | classic | dispatcher
proto = "http1"              # http1 | grpc
binary = "pr"                # pr | base; only for rapira and nginx-rapira
start = ["worker", "@RIG@/apps/hello/worker.php"]   # server-specific arguments, see the box protocol
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"
# optional request shape, used by the gRPC-Web and Connect rows over HTTP/1.1
method = "POST"
body = "apps/grpc/echo.grpc"
[hello-rapira-worker.headers]
content-type = "application/grpc-web+proto"
```

`proto = "http1"` targets run under wrk2, `proto = "grpc"` targets under k6. `method` defaults to `GET`, `headers` to an empty table, `body` to none.

`@RIG@` expands on the box to `$HOME/bench-rig`; `@APPS@` expands to `/opt/bench/apps`.

A suite file:

```toml
name = "ci"
rounds = 1
stage_s = 20
connections = 256            # total over all loaders
smoke = false
targets = ["hello-rapira-worker", "hello-rapira-dispatcher"]

[floors]
hello = 10000
symfony = 10000
laravel = 5000
static = 10000
grpc = 10000
```

### `rig/registry.py`

```python
class SuiteError(ValueError): ...

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

def load_targets(path: Path) -> dict[str, Target]
def load_suite(path: Path, targets: dict[str, Target], loader_count: int) -> Suite
def plan_cells(suite: Suite) -> list[tuple[int, Target]]
def cell_key(round_no: int, target: Target) -> str        # "r1-hello-rapira-worker"
```

`load_suite` raises `SuiteError` when: a target name is unknown; `rounds < 1`; `stage_s < 12`; `connections % loader_count != 0`; a floor is missing for an app used by a target; a floor is not a multiple of `loader_count`. `plan_cells` rotates: round r starts at target index `(r - 1) % n` and wraps, the same rotation as the old drivers.

### `rig/ladder.py`

```python
RATIO = 2
MAX_STAGES = 20
PASS_TOLERANCE = 0.95

def stage_rates(floor: int) -> list[int]           # [floor, floor*2, ..., floor*2**19]
def evaluate_stage(rate: int, merged: Merged) -> tuple[bool, str | None]
def cell_numbers(stages: list[dict]) -> dict
```

`evaluate_stage` returns `(True, None)` when `merged.achieved_rps >= PASS_TOLERANCE * rate` and every counter in `merged.errors` is 0. Otherwise `(False, reason)` where reason is the first that applies: `"achieved <n> req/s under 95% of <rate>"`, `"status errors: <n>"`, `"connect errors: <n>"`, `"read errors: <n>"`, `"write errors: <n>"`, `"timeouts: <n>"`, `"dropped iterations: <n>"`.

`cell_numbers` takes the stage dicts of a cell (the shape of the run file) in order and returns `{"held": {"rate", "stage"} | None, "peak": float | None, "unloaded": {"p50", "p99"} | None, "ladder_exhausted": bool}`. `held` is the last stage with `pass = true`. `peak` is `merged.successful_rps` of the first stage with `pass = false`. `unloaded` comes from stage 0 when it exists. `ladder_exhausted` is true when every stage passed and `len(stages) == MAX_STAGES`.

### `RESULT` line

Every load process prints exactly one line `RESULT {json}` on stdout. The JSON object:

```json
{"tool": "wrk2", "late_ms": 0, "duration_us": 20000867, "requests": 39989, "bytes": 5038614,
 "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
 "latency_us": {"mean": 689.5, "p50": 689, "p90": 1111, "p95": 1175, "p99": 1264, "p999": 1351, "max": 2822},
 "requests_per_sec": 1999.363}
```

`box/load.sh` prints the `late_ms` and `tool` fields by wrapping the reporter output: the reporter prints `RESULT {...}` without them and `load.sh` rewrites the line with `python3 -c` adding both. For k6, `requests` is the iteration count, `errors.status` is the number of failed checks plus failed calls, `errors.dropped` is `dropped_iterations`, and the latency keys come from the `grpc_req_duration` trend with `--summary-trend-stats "avg,med,p(90),p(95),p(99),p(99.9),max"`.

### `rig/merge.py`

```python
class MissingLoader(ValueError): ...      # message names the loader
class LateLoader(ValueError): ...         # message names the loader and late_ms
LATE_LIMIT_MS = 1000
ERROR_KEYS = ("connect", "read", "write", "status", "timeout", "dropped")
PERCENTILE_KEYS = ("p50", "p90", "p95", "p99", "p999", "max")

@dataclass(frozen=True)
class LoaderRecord:
    loader: str
    tool: str
    late_ms: int
    duration_us: int
    requests: int
    bytes: int
    errors: dict[str, int]
    latency_us: dict[str, float]
    requests_per_sec: float

@dataclass(frozen=True)
class Merged:
    requests: int
    successful: int
    bytes: int
    errors: dict[str, int]
    achieved_rps: float
    successful_rps: float
    latency_us: dict[str, float]

def parse_result(text: str, loader: str) -> LoaderRecord | None   # None when no RESULT line
def merge(records: dict[str, LoaderRecord | None], stage_s: int) -> Merged
```

`merge` raises `MissingLoader` for a `None` record and `LateLoader` for `late_ms > LATE_LIMIT_MS`. `achieved_rps = requests / stage_s`, `successful = requests - errors["status"]`, `successful_rps = successful / stage_s`, percentiles are the maximum over loaders, `mean` is the requests-weighted mean.

### Snapshot lines

`box/snapshot.sh [PORT]` prints:

```
cpu <busy> <total>
ena <counter_name> <value>
conns <established> <time_wait>
```

`cpu` from `/proc/stat` (busy = user+nice+system+irq+softirq+steal, total = busy+idle+iowait). One `ena` line per `ethtool -S` counter whose name contains `allowance_exceeded` on the default-route device. The `conns` line only when a port argument is given, counted with `ss` on that local port.

### `rig/flags.py`

```python
GENERATOR_BUSY = 85
SERVER_BUSY = 90
LOG_GROWTH_BYTES = 65536

@dataclass(frozen=True)
class Snapshot:
    cpu: tuple[int, int]
    ena: dict[str, int]
    conns: tuple[int, int] | None

def parse_snapshot(text: str) -> Snapshot
def cpu_pct(before: Snapshot, after: Snapshot) -> int
def ena_delta(before: Snapshot, after: Snapshot) -> dict[str, int]      # only changed counters
def stage_flags(server_busy: int, loader_busy: dict[str, int], passed: bool) -> dict
def stage_void(loader_ena: dict[str, dict[str, int]]) -> str | None
def keepalive_flag(before: Snapshot, after: Snapshot, connections: int) -> dict
def cell_flags(pids_before: list[int], pids_after: list[int], log_before: int, log_after: int) -> dict
```

`stage_flags` returns `{}` when `passed` is true. On a failing stage it returns `{"generator_bound": True}` when any loader is at or above `GENERATOR_BUSY` and the server is below `SERVER_BUSY`, `{"server_unsaturated": True}` when the server is below `SERVER_BUSY` and every loader is below `GENERATOR_BUSY`, else `{}`. `stage_void` returns `"loader <name> throttled: <counter>=<delta>"` for the first loader with a non-empty delta, else `None`. `keepalive_flag` returns `{"keepalive_broken": {"time_wait_delta": d, "connections": c}}` when `d > c`, else `{}`. `cell_flags` returns `worker_churn: True` when the sorted pid lists differ and `log_growth: <bytes>` when the growth exceeds `LOG_GROWTH_BYTES`. The server ENA delta becomes `{"ena_throttled": <delta dict>}` on the stage when non-empty.

### `rig/runfile.py`

```python
SCHEMA = "rapira-bench-run/1"

class RunFile:
    def __init__(self, *, run_id: str, suite: dict, rig: dict, rapira: dict, servers: dict, apps: dict, loaders: list[dict], ladder: dict, processes: int, plan: list[str], smoke: bool, started: str)
    def add_cell(self, cell: dict) -> None
    def finish(self, finished: str) -> dict          # computes status, returns the document
    def write(self, path: Path) -> None              # json.dump with indent=1, sort_keys=False

def run_status(plan: list[str], cells: list[dict]) -> tuple[str, list[str]]
```

The document keys and the cell and stage shapes are the ones in spec section 6.1, with `unloaded` in the cell. The top level also carries `reasons`, the list from `run_status`. The `rapira` section carries `dir` (the install directory on the server) and `base` (the same record for the base build, or null) from `/opt/bench/meta.json`. Each loader record carries `wrk2` as the commit and `k6` as the version line. `run_status` returns `("complete", [])` when every planned key has a cell with `status == "ok"`; otherwise `("incomplete", reasons)` with one reason per missing or void or incomplete cell, for example `"r1-hello-php-fpm: void: probe mismatch on loader-2"`.

### Box protocol

All box scripts live in `$HOME/bench-rig/box/` after staging. `$BENCH = /opt/bench` with `bin/`, `run/`, `log/`, `apps/`, `rapira/<sha7>/`.

`box/target.sh start TAG SERVER PROCS BINARY_DIR ARGS...` starts one target. `BINARY_DIR` is the rapira install directory (`/opt/bench/rapira/<sha7>` or `/opt/bench/rapira/base`) and `-` for other servers. It renders the config into `$BENCH/run/TAG.<ext>`, launches, verifies the listener pid on `PORT`, the executable, and the worker count, and prints `config=<path>` and `pid=<pid>` on success. Exit 1 with `ERROR: <reason>` on failure.

Server-specific `ARGS`:

- `rapira`: `MODE ENTRY [CONFIG_TPL]` with `MODE` in worker, classic, dispatcher; `ENTRY` an expanded path; `CONFIG_TPL` a template path under the staged rig for the http or static shape; the grpc shape uses `grpc ENTRY`.
- `nginx-rapira`: `MODE ENTRY`; nginx on 8080 proxies to rapira on 127.0.0.1:8081.
- `frankenphp`: `SHAPE ENTRY DOCROOT [KEY=VALUE ...]` with `SHAPE` in worker, classic, stock; the key-value pairs become worker `env` lines.
- `php-fpm`: `DOCROOT INDEX`.
- `roadrunner`: `grpc`.

`box/target.sh stop TAG SERVER` stops the target and every child, waits for the port to free, and exits 0 when the pid tree is gone.

`box/target.sh probe TAG SERVER` prints two lines: the sorted worker pids space-separated, and the total log bytes of the target.

`box/target.sh mem TAG SERVER` prints the Pss sum in KiB of the pid tree.

`box/target.sh log TAG SERVER` prints the WARN and ERROR lines of the target log.

`box/probe.sh URL EXPECT_FILE PROTO [METHOD] [BODY_FILE] [HEADER...]` fetches the URL with curl and compares the body with the file byte for byte. `PROTO` is `http1` (HTTP/1.1) or `grpc` (`--http2-prior-knowledge`, method POST, the gRPC headers added by the script). `METHOD` defaults to `GET`, `BODY_FILE` to `-` (no body), and each `HEADER` is `name: value`. Exit 0 on match, 1 on mismatch with the first differing offset on stderr. A relative `EXPECT_FILE` or `BODY_FILE` resolves under `$HOME/bench-rig` on the box, for `probe.sh` and for `load.sh`.

`box/load.sh wrk2 EPOCH RATE THREADS CONNS DURATION_S URL [METHOD] [BODY_FILE] [HEADER...]` sleeps until `EPOCH` (Unix seconds, may have a fraction), runs `wrk2 -t THREADS -c CONNS -d DURATION_Ss -R RATE --latency -s $HOME/bench-rig/loader/wrk2-report.lua URL`, and prints the wrk2 output with the `RESULT` line rewritten to carry `tool` and `late_ms`. When `EPOCH` has passed it starts at once and `late_ms` is the delay. The request shape reaches the Lua script through the environment: `WRK_METHOD`, `WRK_BODY_FILE`, and `WRK_HEADERS` (one `name: value` per line); the script sets `wrk.method`, `wrk.body`, and `wrk.headers` in its `init` phase.

`box/load.sh k6 EPOCH RATE VUS DURATION_S URL` runs `loader/k6-grpc.js` with `ramping-arrival-rate`: a one second ramp from 0 to `RATE`, then `RATE` for the rest of `DURATION_S`, with `preAllocatedVUs = VUS`, and prints the `RESULT` line the same way. The ramp exists because the first call of every VU opens its connection, and a constant rate from the start drops iterations in that window. A 20 s stage achieves about 97.5% of `RATE`.

`box/snapshot.sh [PORT]` as defined under Snapshot lines.

### `rig/ssh.py` and `rig/rig.py`

```python
@dataclass(frozen=True)
class Host:
    name: str            # "server", "loader-1", ...
    public_ip: str
    private_ip: str

class SshError(RuntimeError): ...

def run(host: Host, cmd: str, *, timeout: float | None = None) -> str      # stdout; raises SshError on nonzero
def run_many(jobs: list[tuple[Host, str]], *, timeout: float | None = None) -> list[str | SshError]
def stage_tree(hosts: list[Host]) -> None       # tar of git ls-files to ~/bench-rig on each host
def copy_from(host: Host, remote: str, local: Path) -> None

@dataclass(frozen=True)
class Rig:
    server: Host
    loaders: tuple[Host, ...]
    server_type: str
    loader_type: str
    ami_id: str
    key_file: Path

def from_terraform(tf_dir: Path) -> Rig
def remaining_ttl_s(host: Host) -> int
def arm_ttl(hosts: list[Host], minutes: int) -> None
def ensure_ttl(hosts: list[Host], needed_s: int) -> None     # extends when AUTO_EXTEND=1
```

ssh options: the key from `Rig.key_file`, user `fedora`, `StrictHostKeyChecking=accept-new`, `UserKnownHostsFile=.ssh-known-hosts`, `ControlMaster=auto`, `ControlPath=.ssh-cm-%h`, `ControlPersist=10m`, `ConnectTimeout=5`, `LogLevel=ERROR`.

### `rig/bench.py`

```python
PORT = 8080
LEAD_S = 3
WARMUP_S = 10

class Boxes(Protocol):                     # the seam the tests stub
    def run(self, host: Host, cmd: str, timeout: float | None = None) -> str: ...
    def run_many(self, jobs: list[tuple[Host, str]], timeout: float | None = None) -> list[str | SshError]: ...
    def copy_from(self, host: Host, remote: str, local: Path) -> None: ...

def plan_run(rig: Rig, suite: Suite, *, processes: int, loader_threads: int) -> dict     # validates connections % (loaders*threads), builds the plan
def run_suite(rig: Rig, suite: Suite, boxes: Boxes, out_dir: Path, *, processes: int, run_id: str, rapira: dict, servers: dict, apps: dict, loader_threads: int) -> Path   # returns the run.json path
```

`run_suite` implements spec section 5.3 per cell and writes `raw/<cell>/<stage>-<loader>.txt`, `raw/<cell>/config.*`, `raw/<cell>/server.log`, `raw/<cell>/snapshots.txt`, and `run.json`. On `KeyboardInterrupt` it stops the current target, marks the cell `incomplete`, finishes the run file, and re-raises.

### `rig/report.py`, `rig/compare.py`, `rig/publish.py`

```python
def render(run: dict) -> tuple[str, int]                  # text, exit status (1 when incomplete)
def compare(a: dict, b: dict, *, force: bool = False) -> tuple[str, int]
def publish(run: dict, pages_dir: Path) -> Path           # writes data/<id>.json, updates data/index.json, returns the data path
```

Report columns per target: `target`, `held req/s`, `peak req/s`, `p99 at held`, `unloaded p50`, `n`, `spread`, `flags`. Rows sorted by app then by peak descending. Voided cells listed under `VOIDED cells`. The final line is `Do not publish these tables.` with exit 1 when the run is incomplete.

`compare` refuses when `rig.server_type`, `rig.loader_type`, `rig.loader_count`, `processes`, or `ladder.stage_s` differ, unless `force`. It prints per target: held a, held b, peak a, peak b, delta percent of peak, both spreads.

`data/index.json`: `{"schema": "rapira-bench-index/1", "runs": [{"id", "started", "suite", "rapira_sha", "rapira_version", "status", "smoke"}]}` sorted by `started` ascending.

### Terraform outputs

`server_public_ip`, `server_private_ip`, `loader_public_ips` (list), `loader_private_ips` (list), `ami_id`, `server_instance_type`, `loader_instance_type`, `loader_count`, `key_file`, `placement_group`.

### Makefile knobs

`SERVER_TYPE ?= c7a.8xlarge`, `LOADER_TYPE ?= c7a.xlarge`, `LOADER_COUNT ?= 4`, `AZ ?= eu-central-1a`, `TTL ?= 60`, `REF ?=`, `BASE_REF ?= main`, `NIGHTLY ?=` (the sha7 of the nightly asset; when set, provisioning downloads the asset and `REF` is ignored), `SUITE ?= ci`, `ROUNDS ?=`, `PROCESSES ?=`, `AMI ?=`, `TF_BACKEND ?= local`.

### CLI

```
python3 -m rig bench --suite ci [--rounds N] [--processes N] [--smoke] [--out runs]
python3 -m rig report runs/<id>/run.json
python3 -m rig compare runs/<a>/run.json runs/<b>/run.json [--force]
python3 -m rig publish --pages-dir <dir> runs/<id>/run.json
```

### Test layout

Tests import the package from the repository root: `python3 -m unittest discover -s tests -t .`. Each test module has flat case tables named in upper case with a `name` field per case.

---

## Tasks

### Task 1: Target registry and suite files

**Files:**
- Create: `rig/__init__.py`
- Create: `rig/registry.py`
- Create: `suites/targets.toml`
- Create: `suites/ci.toml`
- Create: `suites/full.toml`
- Create: `suites/ab.toml`
- Create: `tests/__init__.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `VERSION = "1"` in `rig/__init__.py`; `SuiteError(ValueError)`; `Target` and `Suite` frozen dataclasses with the contract fields; `load_targets(path: Path) -> dict[str, Target]`; `load_suite(path: Path, targets: dict[str, Target], loader_count: int) -> Suite`; `plan_cells(suite: Suite) -> list[tuple[int, Target]]`; `cell_key(round_no: int, target: Target) -> str`. Added names: `SERVERS`, `APPS`, `MODES`, `PROTOS`, `BINARIES`, `RAPIRA_SERVERS` (the allowed registry values, tuples of str) and `MIN_STAGE_S = 12` in `rig/registry.py`. `load_targets` raises `SuiteError` for an unknown or missing field, a value outside the allowed values, a missing or unknown `binary` on a rapira or nginx-rapira target, and a `binary` on any other server. The registry file `suites/targets.toml` and the suites `suites/ci.toml` (16 targets, 1 round), `suites/full.toml` (28 targets, 3 rounds), `suites/ab.toml` (10 targets, 3 rounds).

- [ ] **Step 1: Write the failing test**

Create the `tests` directory and the empty package marker `tests/__init__.py`, so that `python3 -m unittest discover -s tests -t .` imports the test modules as `tests.<module>`:

```bash
mkdir -p tests
: > tests/__init__.py
```

Create `tests/test_registry.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_registry -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rig'`

- [ ] **Step 3: Write the implementation**

Create `rig/__init__.py`:

```python
VERSION = "1"
```

Create `rig/registry.py`:

```python
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
    for name in doc["targets"]:
        if name not in targets:
            raise SuiteError(f"{where}: unknown target {name}")
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
```

- [ ] **Step 4: Run the test to verify the loader rules pass**

Run: `python3 -m unittest tests.test_registry -v`
Expected: FAIL with `FAILED (errors=3)`. The 5 tests of `LoadTargetsTest`, `LoadSuiteTest`, and `PlanCellsTest` pass. The 3 tests of `ShippedSuitesTest` stop with `FileNotFoundError` on `suites/targets.toml`, because Step 5 creates the registry and the suite files.

- [ ] **Step 5: Write the registry and the suite files**

Create `suites/targets.toml`:

```toml
# The target registry. The driver reads it with tomllib.
# A name is <app>-<server>-<mode>. A target that runs the base binary adds -base.
# php-fpm and roadrunner have one mode, so their names have no mode.
# A static or grpc name ends with the request variant in the position of the mode.
# The start arguments follow the box protocol of box/target.sh.
# On the box @RIG@ expands to $HOME/bench-rig and @APPS@ expands to /opt/bench/apps.
# The expect, body, and config paths are relative to the repository root.

[hello-rapira-worker]
server = "rapira"
app = "hello"
mode = "worker"
proto = "http1"
binary = "pr"
start = ["worker", "@RIG@/apps/hello/worker.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"

[hello-rapira-dispatcher]
server = "rapira"
app = "hello"
mode = "dispatcher"
proto = "http1"
binary = "pr"
start = ["dispatcher", "@RIG@/apps/hello/dispatcher.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"

[hello-rapira-classic]
server = "rapira"
app = "hello"
mode = "classic"
proto = "http1"
binary = "pr"
start = ["classic", "@RIG@/apps/hello/classic.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"

[hello-rapira-worker-base]
server = "rapira"
app = "hello"
mode = "worker"
proto = "http1"
binary = "base"
start = ["worker", "@RIG@/apps/hello/worker.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"

[hello-rapira-dispatcher-base]
server = "rapira"
app = "hello"
mode = "dispatcher"
proto = "http1"
binary = "base"
start = ["dispatcher", "@RIG@/apps/hello/dispatcher.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"

[hello-rapira-classic-base]
server = "rapira"
app = "hello"
mode = "classic"
proto = "http1"
binary = "base"
start = ["classic", "@RIG@/apps/hello/classic.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"

[hello-nginx-rapira-worker]
server = "nginx-rapira"
app = "hello"
mode = "worker"
proto = "http1"
binary = "pr"
start = ["worker", "@RIG@/apps/hello/worker.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/nginx/rapira.conf.tpl"

[hello-frankenphp-worker]
server = "frankenphp"
app = "hello"
mode = "worker"
proto = "http1"
start = ["worker", "@RIG@/apps/hello/frankenphp.php", "@RIG@/apps/hello"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/frankenphp/worker.Caddyfile.tpl"

[hello-frankenphp-stock]
server = "frankenphp"
app = "hello"
mode = "worker"
proto = "http1"
start = ["stock", "@RIG@/apps/hello/frankenphp.php", "@RIG@/apps/hello"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/frankenphp/stock.Caddyfile.tpl"

[hello-php-fpm]
server = "php-fpm"
app = "hello"
mode = "classic"
proto = "http1"
start = ["@RIG@/apps/hello", "fpm.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/php-fpm/php-fpm.conf.tpl"

[symfony-rapira-worker]
server = "rapira"
app = "symfony"
mode = "worker"
proto = "http1"
binary = "pr"
start = ["worker", "@APPS@/symfony/bench/worker-rapira.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"

[symfony-rapira-classic]
server = "rapira"
app = "symfony"
mode = "classic"
proto = "http1"
binary = "pr"
start = ["classic", "@APPS@/symfony/public/index.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"

[symfony-rapira-worker-base]
server = "rapira"
app = "symfony"
mode = "worker"
proto = "http1"
binary = "base"
start = ["worker", "@APPS@/symfony/bench/worker-rapira.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"

[symfony-rapira-classic-base]
server = "rapira"
app = "symfony"
mode = "classic"
proto = "http1"
binary = "base"
start = ["classic", "@APPS@/symfony/public/index.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"

[symfony-nginx-rapira-worker]
server = "nginx-rapira"
app = "symfony"
mode = "worker"
proto = "http1"
binary = "pr"
start = ["worker", "@APPS@/symfony/bench/worker-rapira.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/nginx/rapira.conf.tpl"

[symfony-frankenphp-worker]
server = "frankenphp"
app = "symfony"
mode = "worker"
proto = "http1"
start = ["worker", "@APPS@/symfony/public/worker-franken.php", "@APPS@/symfony/public"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/frankenphp/worker.Caddyfile.tpl"

[symfony-frankenphp-classic]
server = "frankenphp"
app = "symfony"
mode = "classic"
proto = "http1"
start = ["classic", "@APPS@/symfony/public/index.php", "@APPS@/symfony/public"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/frankenphp/classic.Caddyfile.tpl"

[symfony-frankenphp-stock]
server = "frankenphp"
app = "symfony"
mode = "worker"
proto = "http1"
start = ["stock", "@APPS@/symfony/public/worker-franken.php", "@APPS@/symfony/public"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/frankenphp/stock.Caddyfile.tpl"

[symfony-php-fpm]
server = "php-fpm"
app = "symfony"
mode = "classic"
proto = "http1"
start = ["@APPS@/symfony/public", "index.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/php-fpm/php-fpm.conf.tpl"

[laravel-rapira-worker]
server = "rapira"
app = "laravel"
mode = "worker"
proto = "http1"
binary = "pr"
start = ["worker", "@APPS@/laravel/bench/worker-rapira.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"

[laravel-frankenphp-worker]
server = "frankenphp"
app = "laravel"
mode = "worker"
proto = "http1"
start = ["worker", "@APPS@/laravel/public/frankenphp-worker.php", "@APPS@/laravel/public", "LARAVEL_OCTANE=1", "MAX_REQUESTS=100000000", "APP_DEBUG=false"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/frankenphp/worker.Caddyfile.tpl"

# The static rows serve apps/static. tiny.css is 128 B and app.css is 27 KiB.
# A miss falls through to the hello worker. The plain row runs the hello worker without the static middleware.

[static-rapira-hit]
server = "rapira"
app = "static"
mode = "worker"
proto = "http1"
binary = "pr"
start = ["worker", "@RIG@/apps/hello/worker.php", "@RIG@/servers/rapira/static.toml.tpl"]
url = "/tiny.css"
expect = "apps/static/tiny.expect"
config = "servers/rapira/static.toml.tpl"

[static-rapira-app]
server = "rapira"
app = "static"
mode = "worker"
proto = "http1"
binary = "pr"
start = ["worker", "@RIG@/apps/hello/worker.php", "@RIG@/servers/rapira/static.toml.tpl"]
url = "/app.css"
expect = "apps/static/app.css"
config = "servers/rapira/static.toml.tpl"

[static-rapira-miss]
server = "rapira"
app = "static"
mode = "worker"
proto = "http1"
binary = "pr"
start = ["worker", "@RIG@/apps/hello/worker.php", "@RIG@/servers/rapira/static.toml.tpl"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/static.toml.tpl"

[static-rapira-plain]
server = "rapira"
app = "static"
mode = "worker"
proto = "http1"
binary = "pr"
start = ["worker", "@RIG@/apps/hello/worker.php"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/rapira/http.toml.tpl"

[static-frankenphp-hit]
server = "frankenphp"
app = "static"
mode = "worker"
proto = "http1"
start = ["stock", "@RIG@/apps/hello/frankenphp.php", "@RIG@/apps/static"]
url = "/tiny.css"
expect = "apps/static/tiny.expect"
config = "servers/frankenphp/stock.Caddyfile.tpl"

[static-frankenphp-app]
server = "frankenphp"
app = "static"
mode = "worker"
proto = "http1"
start = ["stock", "@RIG@/apps/hello/frankenphp.php", "@RIG@/apps/static"]
url = "/app.css"
expect = "apps/static/app.css"
config = "servers/frankenphp/stock.Caddyfile.tpl"

[static-frankenphp-miss]
server = "frankenphp"
app = "static"
mode = "worker"
proto = "http1"
start = ["stock", "@RIG@/apps/hello/frankenphp.php", "@RIG@/apps/static"]
url = "/?name=you"
expect = "apps/hello/expect.txt"
config = "servers/frankenphp/stock.Caddyfile.tpl"

# The gRPC rows call bench.v1.EchoService/Echo. The grpc rows run under k6.
# The gRPC-Web and Connect rows use the same rapira gRPC listener over HTTP/1.1 and run under wrk2.

[grpc-rapira]
server = "rapira"
app = "grpc"
mode = "dispatcher"
proto = "grpc"
binary = "pr"
start = ["grpc", "@RIG@/apps/grpc/php/dispatcher.php"]
url = "/bench.v1.EchoService/Echo"
expect = "apps/grpc/expect.grpc"
config = "servers/rapira/grpc.toml.tpl"
method = "POST"
body = "apps/grpc/echo.grpc"

[grpc-roadrunner]
server = "roadrunner"
app = "grpc"
mode = "worker"
proto = "grpc"
start = ["grpc"]
url = "/bench.v1.EchoService/Echo"
expect = "apps/grpc/expect.grpc"
config = "servers/roadrunner/grpc.rr.yaml.tpl"
method = "POST"
body = "apps/grpc/echo.grpc"

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

[grpc-rapira-connect]
server = "rapira"
app = "grpc"
mode = "dispatcher"
proto = "http1"
binary = "pr"
start = ["grpc", "@RIG@/apps/grpc/php/dispatcher.php"]
url = "/bench.v1.EchoService/Echo"
expect = "apps/grpc/expect.bin"
config = "servers/rapira/grpc.toml.tpl"
method = "POST"
body = "apps/grpc/echo.bin"

[grpc-rapira-connect.headers]
content-type = "application/proto"
connect-protocol-version = "1"
accept-encoding = "identity"

[grpc-rapira-connectjson]
server = "rapira"
app = "grpc"
mode = "dispatcher"
proto = "http1"
binary = "pr"
start = ["grpc", "@RIG@/apps/grpc/php/dispatcher.php"]
url = "/bench.v1.EchoService/Echo"
expect = "apps/grpc/expect.json"
config = "servers/rapira/grpc.toml.tpl"
method = "POST"
body = "apps/grpc/echo.json"

[grpc-rapira-connectjson.headers]
content-type = "application/json"
connect-protocol-version = "1"
accept-encoding = "identity"
```

Create `suites/ci.toml`:

```toml
# The per-merge suite. CI runs it after every merge to the rapira main branch.
name = "ci"
rounds = 1
stage_s = 20
connections = 256
smoke = false
targets = [
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
]

[floors]
hello = 10000
symfony = 10000
laravel = 5000
static = 10000
grpc = 10000
```

Create `suites/full.toml`:

```toml
# The manual suite: the CI targets and the additional rows, three rounds.
name = "full"
rounds = 3
stage_s = 20
connections = 256
smoke = false
targets = [
  "hello-rapira-worker",
  "hello-rapira-dispatcher",
  "hello-rapira-classic",
  "hello-nginx-rapira-worker",
  "hello-frankenphp-worker",
  "hello-frankenphp-stock",
  "hello-php-fpm",
  "symfony-rapira-worker",
  "symfony-rapira-classic",
  "symfony-nginx-rapira-worker",
  "symfony-frankenphp-worker",
  "symfony-frankenphp-classic",
  "symfony-frankenphp-stock",
  "symfony-php-fpm",
  "laravel-rapira-worker",
  "laravel-frankenphp-worker",
  "static-rapira-hit",
  "static-rapira-app",
  "static-rapira-miss",
  "static-rapira-plain",
  "static-frankenphp-hit",
  "static-frankenphp-app",
  "static-frankenphp-miss",
  "grpc-rapira",
  "grpc-roadrunner",
  "grpc-rapira-grpcweb",
  "grpc-rapira-connect",
  "grpc-rapira-connectjson",
]

[floors]
hello = 10000
symfony = 10000
laravel = 5000
static = 10000
grpc = 10000
```

Create `suites/ab.toml`:

```toml
# The A/B suite: the rapira pr binary against the base binary on hello and Symfony, three rounds.
# Symfony has no dispatcher entry, so it runs the worker and classic modes only.
name = "ab"
rounds = 3
stage_s = 20
connections = 256
smoke = false
targets = [
  "hello-rapira-worker",
  "hello-rapira-worker-base",
  "hello-rapira-classic",
  "hello-rapira-classic-base",
  "hello-rapira-dispatcher",
  "hello-rapira-dispatcher-base",
  "symfony-rapira-worker",
  "symfony-rapira-worker-base",
  "symfony-rapira-classic",
  "symfony-rapira-classic-base",
]

[floors]
hello = 10000
symfony = 10000
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_registry -v`
Expected: PASS, `Ran 8 tests` and `OK`

- [ ] **Step 7: Commit**

```bash
git add rig/__init__.py rig/registry.py suites/targets.toml suites/ci.toml suites/full.toml suites/ab.toml tests/__init__.py tests/test_registry.py
git commit -s -S -m "feat: add the target registry and the suite files"
```

### Task 2: Ladder rules

**Files:**
- Create: `rig/ladder.py`
- Test: `tests/test_ladder.py`

**Interfaces:**
- Consumes: the `Merged` type of `rig/merge.py` (Task 3) as a type annotation only. `rig/ladder.py` imports it under `typing.TYPE_CHECKING`, so this task runs before Task 3 exists. `evaluate_stage` reads only `merged.achieved_rps` and `merged.errors` (keys `connect`, `read`, `write`, `status`, `timeout`, `dropped`). The test passes a `types.SimpleNamespace` with these two fields.
- Produces: `RATIO = 2`, `MAX_STAGES = 20`, `PASS_TOLERANCE = 0.95`; `stage_rates(floor: int) -> list[int]`; `evaluate_stage(rate: int, merged: Merged) -> tuple[bool, str | None]`; `cell_numbers(stages: list[dict]) -> dict` with the keys `held` (`{"rate", "stage"}` or None), `peak` (float or None), `unloaded` (`{"p50", "p99"}` or None), `ladder_exhausted` (bool). The reason for a low rate is `achieved <n> req/s under 95% of <rate>`, where `<n>` is `int(merged.achieved_rps)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ladder.py`:

```python
import unittest
from types import SimpleNamespace

from rig.ladder import MAX_STAGES, cell_numbers, evaluate_stage, stage_rates

NO_ERRORS = {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0}


def merged(achieved_rps: float, **errors: int) -> SimpleNamespace:
    # evaluate_stage reads only these two fields of rig.merge.Merged.
    return SimpleNamespace(achieved_rps=achieved_rps, errors=NO_ERRORS | errors)


def stage(rate: int, passed: bool, successful_rps: float, p50: float, p99: float) -> dict:
    return {
        "rate": rate,
        "duration_s": 20,
        "pass": passed,
        "merged": {"successful_rps": successful_rps},
        "latency_us": {"p50": p50, "p90": p99, "p99": p99, "p999": p99, "max": p99},
    }


STAGE_RATES_CASES = [
    {
        # 10000 * 2**19 = 5242880000.
        "name": "hello floor",
        "floor": 10000,
        "first": 10000,
        "second": 20000,
        "last": 5242880000,
    },
    {
        # 5000 * 2**19 = 2621440000.
        "name": "laravel floor",
        "floor": 5000,
        "first": 5000,
        "second": 10000,
        "last": 2621440000,
    },
]

EVALUATE_CASES = [
    {"name": "achieved exactly 95 percent passes", "rate": 10000, "merged": merged(9500.0), "expected": (True, None)},
    {"name": "achieved above the rate passes", "rate": 10000, "merged": merged(10003.5), "expected": (True, None)},
    {
        "name": "achieved 9400 fails",
        "rate": 10000,
        "merged": merged(9400.0),
        "expected": (False, "achieved 9400 req/s under 95% of 10000"),
    },
    {
        # 9499.95 is under 9500; the reason truncates it to 9499.
        "name": "achieved just under 95 percent fails",
        "rate": 10000,
        "merged": merged(9499.95),
        "expected": (False, "achieved 9499 req/s under 95% of 10000"),
    },
    {
        "name": "status errors at the full rate",
        "rate": 10000,
        "merged": merged(10000.0, status=12),
        "expected": (False, "status errors: 12"),
    },
    {"name": "connect errors", "rate": 10000, "merged": merged(10000.0, connect=1), "expected": (False, "connect errors: 1")},
    {"name": "read errors", "rate": 10000, "merged": merged(10000.0, read=3), "expected": (False, "read errors: 3")},
    {"name": "write errors", "rate": 10000, "merged": merged(10000.0, write=2), "expected": (False, "write errors: 2")},
    {"name": "timeouts", "rate": 10000, "merged": merged(10000.0, timeout=4), "expected": (False, "timeouts: 4")},
    {"name": "dropped iterations", "rate": 10000, "merged": merged(10000.0, dropped=7), "expected": (False, "dropped iterations: 7")},
    {
        "name": "a low rate is reported before errors",
        "rate": 20000,
        "merged": merged(12000.0, status=40),
        "expected": (False, "achieved 12000 req/s under 95% of 20000"),
    },
    {
        "name": "status errors are reported before connect errors",
        "rate": 10000,
        "merged": merged(10000.0, connect=5, status=2),
        "expected": (False, "status errors: 2"),
    },
]

CELL_CASES = [
    {
        "name": "no stages",
        "stages": [],
        "expected": {"held": None, "peak": None, "unloaded": None, "ladder_exhausted": False},
    },
    {
        "name": "first stage fails",
        "stages": [stage(10000, False, 6200.5, 900.0, 250000.0)],
        "expected": {
            "held": None,
            "peak": 6200.5,
            "unloaded": {"p50": 900.0, "p99": 250000.0},
            "ladder_exhausted": False,
        },
    },
    {
        "name": "two passes then a fail",
        "stages": [
            stage(10000, True, 10000.0, 700.0, 1200.0),
            stage(20000, True, 20000.0, 710.0, 1500.0),
            stage(40000, False, 31000.25, 90000.0, 400000.0),
        ],
        "expected": {
            "held": {"rate": 20000, "stage": 1},
            "peak": 31000.25,
            "unloaded": {"p50": 700.0, "p99": 1200.0},
            "ladder_exhausted": False,
        },
    },
    {
        # An interrupted cell: every stage passed, but the ladder stopped before the cap.
        "name": "three passes and no fail",
        "stages": [
            stage(10000, True, 10000.0, 700.0, 1200.0),
            stage(20000, True, 20000.0, 710.0, 1500.0),
            stage(40000, True, 40000.0, 720.0, 1800.0),
        ],
        "expected": {
            "held": {"rate": 40000, "stage": 2},
            "peak": None,
            "unloaded": {"p50": 700.0, "p99": 1200.0},
            "ladder_exhausted": False,
        },
    },
    {
        # The last of 20 stages is index 19 at 10000 * 2**19 = 5242880000.
        "name": "every stage passes up to the cap",
        "stages": [stage(10000 * 2**i, True, 10000.0 * 2**i, 650.0, 1100.0) for i in range(20)],
        "expected": {
            "held": {"rate": 5242880000, "stage": 19},
            "peak": None,
            "unloaded": {"p50": 650.0, "p99": 1100.0},
            "ladder_exhausted": True,
        },
    },
]


class StageRatesTest(unittest.TestCase):
    def test_geometric_list(self):
        for case in STAGE_RATES_CASES:
            with self.subTest(name=case["name"]):
                rates = stage_rates(case["floor"])
                self.assertEqual(len(rates), MAX_STAGES)
                self.assertEqual(rates[0], case["first"])
                self.assertEqual(rates[1], case["second"])
                self.assertEqual(rates[-1], case["last"])


class EvaluateStageTest(unittest.TestCase):
    def test_pass_rule(self):
        for case in EVALUATE_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(evaluate_stage(case["rate"], case["merged"]), case["expected"])


class CellNumbersTest(unittest.TestCase):
    def test_held_peak_unloaded(self):
        for case in CELL_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(cell_numbers(case["stages"]), case["expected"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_ladder -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rig.ladder'`

- [ ] **Step 3: Write the implementation**

Create `rig/ladder.py`:

```python
"""Stage rates, the pass rule, and the held, peak, and unloaded numbers of a cell."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rig.merge import Merged

RATIO = 2
MAX_STAGES = 20
PASS_TOLERANCE = 0.95

# The error counters in the order of the fail reasons, with the reason label.
_ERROR_REASONS = (
    ("status", "status errors"),
    ("connect", "connect errors"),
    ("read", "read errors"),
    ("write", "write errors"),
    ("timeout", "timeouts"),
    ("dropped", "dropped iterations"),
)


def stage_rates(floor: int) -> list[int]:
    return [floor * RATIO**i for i in range(MAX_STAGES)]


def evaluate_stage(rate: int, merged: Merged) -> tuple[bool, str | None]:
    if merged.achieved_rps < PASS_TOLERANCE * rate:
        return False, f"achieved {int(merged.achieved_rps)} req/s under {PASS_TOLERANCE:.0%} of {rate}"
    for key, label in _ERROR_REASONS:
        if merged.errors[key]:
            return False, f"{label}: {merged.errors[key]}"
    return True, None


def cell_numbers(stages: list[dict]) -> dict:
    held = None
    peak = None
    for index, stage in enumerate(stages):
        if stage["pass"]:
            held = {"rate": stage["rate"], "stage": index}
        else:
            peak = stage["merged"]["successful_rps"]
            break
    unloaded = None
    if stages:
        latency = stages[0]["latency_us"]
        unloaded = {"p50": latency["p50"], "p99": latency["p99"]}
    exhausted = len(stages) == MAX_STAGES and all(stage["pass"] for stage in stages)
    return {"held": held, "peak": peak, "unloaded": unloaded, "ladder_exhausted": exhausted}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_ladder -v`
Expected: PASS, `Ran 3 tests` and `OK`

- [ ] **Step 5: Commit**

```bash
git add rig/ladder.py tests/test_ladder.py
git commit -s -S -m "feat: add the ladder stage rates and the pass rule"
```

### Task 3: RESULT parsing and merge

**Files:**
- Create: `rig/merge.py`
- Test: `tests/test_merge.py`

**Interfaces:**
- Consumes: the `RESULT` line format of the contract.
- Produces: `MissingLoader(ValueError)`, `LateLoader(ValueError)`, `LATE_LIMIT_MS = 1000`, `ERROR_KEYS`, `PERCENTILE_KEYS`; the frozen dataclasses `LoaderRecord` and `Merged` with the contract fields; `parse_result(text: str, loader: str) -> LoaderRecord | None`; `merge(records: dict[str, LoaderRecord | None], stage_s: int) -> Merged`. `parse_result` uses the last line that starts with `RESULT `, returns None when no line starts with it, and raises `ValueError` for invalid JSON or a missing key (every `ERROR_KEYS` counter, `mean` and every `PERCENTILE_KEYS` latency are required). `LoaderRecord.latency_us` and `Merged.latency_us` hold `mean` followed by the `PERCENTILE_KEYS`. `merge` sets the mean to 0.0 when no loader completed a request. The exception messages are `loader <name> returned no RESULT line` and `loader <name> started <late_ms> ms late`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_merge.py`:

```python
import unittest

from rig.merge import LateLoader, LoaderRecord, Merged, MissingLoader, merge, parse_result

# The RESULT line of the contract.
CONTRACT_LINE = (
    'RESULT {"tool": "wrk2", "late_ms": 0, "duration_us": 20000867, "requests": 39989, "bytes": 5038614,'
    ' "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},'
    ' "latency_us": {"mean": 689.5, "p50": 689, "p90": 1111, "p95": 1175, "p99": 1264, "p999": 1351, "max": 2822},'
    ' "requests_per_sec": 1999.363}'
)

WRK2_TEXT = (
    "Running 20s test @ http://10.0.1.10:8080/?name=you\n"
    "  4 threads and 64 connections\n"
    "  Thread calibration: mean lat.: 0.702ms, rate sampling interval: 10ms\n"
    "Requests/sec:   1999.36\n"
    "Transfer/sec:    246.01KB\n"
)

NO_ERRORS = {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0}


def record(loader: str, requests: int, bytes_: int, latency: dict, late_ms: int = 0, **errors: int) -> LoaderRecord:
    return LoaderRecord(
        loader=loader,
        tool="wrk2",
        late_ms=late_ms,
        duration_us=20000867,
        requests=requests,
        bytes=bytes_,
        errors=NO_ERRORS | errors,
        latency_us=latency,
        requests_per_sec=requests / 20,
    )


CONTRACT_RECORD = LoaderRecord(
    loader="loader-1",
    tool="wrk2",
    late_ms=0,
    duration_us=20000867,
    requests=39989,
    bytes=5038614,
    errors=NO_ERRORS,
    latency_us={"mean": 689.5, "p50": 689.0, "p90": 1111.0, "p95": 1175.0, "p99": 1264.0, "p999": 1351.0, "max": 2822.0},
    requests_per_sec=1999.363,
)

PARSE_CASES = [
    {"name": "wrk2 output then the RESULT line", "text": WRK2_TEXT + CONTRACT_LINE + "\n", "expected": CONTRACT_RECORD},
    {
        "name": "the last RESULT line wins",
        "text": CONTRACT_LINE.replace('"requests": 39989', '"requests": 1') + "\n" + WRK2_TEXT + CONTRACT_LINE + "\n",
        "expected": CONTRACT_RECORD,
    },
    {"name": "no RESULT line", "text": WRK2_TEXT, "expected": None},
    {"name": "RESULT inside a line is not a RESULT line", "text": "log: RESULT {}\n", "expected": None},
]

PARSE_ERROR_CASES = [
    {"name": "invalid JSON", "text": "RESULT {\"tool\": \"wrk2\",\n"},
    {"name": "no requests key", "text": CONTRACT_LINE.replace('"requests": 39989, ', "")},
    {"name": "no dropped counter", "text": CONTRACT_LINE.replace(', "dropped": 0', "")},
    {"name": "no p999 percentile", "text": CONTRACT_LINE.replace('"p999": 1351, ', "")},
]

LOADER_1 = CONTRACT_RECORD
LOADER_2 = record(
    "loader-2", 40011, 5041386,
    {"mean": 701.0, "p50": 695.0, "p90": 1120.0, "p95": 1190.0, "p99": 1301.0, "p999": 1402.0, "max": 3010.0},
    late_ms=12,
)
# late_ms at the limit of 1000 ms is valid.
LOADER_3 = record(
    "loader-3", 39800, 5014800,
    {"mean": 650.25, "p50": 670.0, "p90": 1100.0, "p95": 1180.0, "p99": 1250.0, "p999": 1500.0, "max": 2500.0},
    late_ms=1000, status=7, read=2,
)
LOADER_4 = record(
    "loader-4", 40200, 5065200,
    {"mean": 720.0, "p50": 700.0, "p90": 1090.0, "p95": 1170.0, "p99": 1280.0, "p999": 1340.0, "max": 4100.0},
    late_ms=3, connect=1,
)
ZERO = {"mean": 0.0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0, "p999": 0.0, "max": 0.0}

MERGE_CASES = [
    {
        # requests: 39989 + 40011 + 39800 + 40200 = 160000, and 160000 / 20 = 8000.
        # bytes: 5038614 + 5041386 + 5014800 + 5065200 = 20160000.
        # successful: 160000 - 7 status errors = 159993.
        # mean: (39989 * 689.5 + 40011 * 701 + 39800 * 650.25 + 40200 * 720) / 160000 = 110444076.5 / 160000.
        # Each percentile is the maximum of the four loaders.
        "name": "four loaders",
        "records": {"loader-1": LOADER_1, "loader-2": LOADER_2, "loader-3": LOADER_3, "loader-4": LOADER_4},
        "stage_s": 20,
        "expected": Merged(
            requests=160000,
            successful=159993,
            bytes=20160000,
            errors={"connect": 1, "read": 2, "write": 0, "status": 7, "timeout": 0, "dropped": 0},
            achieved_rps=8000.0,
            successful_rps=159993 / 20,
            latency_us={
                "mean": 110444076.5 / 160000,
                "p50": 700.0,
                "p90": 1120.0,
                "p95": 1190.0,
                "p99": 1301.0,
                "p999": 1500.0,
                "max": 4100.0,
            },
        ),
    },
    {
        "name": "no requests on any loader",
        "records": {"loader-1": record("loader-1", 0, 0, ZERO), "loader-2": record("loader-2", 0, 0, ZERO)},
        "stage_s": 20,
        "expected": Merged(
            requests=0,
            successful=0,
            bytes=0,
            errors=NO_ERRORS,
            achieved_rps=0.0,
            successful_rps=0.0,
            latency_us=ZERO,
        ),
    },
]

MERGE_ERROR_CASES = [
    {
        "name": "a loader without a RESULT line",
        "records": {"loader-1": LOADER_1, "loader-2": LOADER_2, "loader-3": None, "loader-4": LOADER_4},
        "error": MissingLoader,
        "message": "loader loader-3 returned no RESULT line",
    },
    {
        "name": "a loader 1001 ms late",
        "records": {
            "loader-1": LOADER_1,
            "loader-2": record("loader-2", 40011, 5041386, LOADER_2.latency_us, late_ms=1001),
            "loader-3": LOADER_3,
            "loader-4": LOADER_4,
        },
        "error": LateLoader,
        "message": "loader loader-2 started 1001 ms late",
    },
]


class ParseResultTest(unittest.TestCase):
    def test_parse(self):
        for case in PARSE_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(parse_result(case["text"], "loader-1"), case["expected"])

    def test_invalid_line(self):
        for case in PARSE_ERROR_CASES:
            with self.subTest(name=case["name"]):
                with self.assertRaises(ValueError):
                    parse_result(case["text"], "loader-1")


class MergeTest(unittest.TestCase):
    def test_merge(self):
        for case in MERGE_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(merge(case["records"], case["stage_s"]), case["expected"])

    def test_invalid_stage(self):
        for case in MERGE_ERROR_CASES:
            with self.subTest(name=case["name"]):
                with self.assertRaises(case["error"]) as ctx:
                    merge(case["records"], 20)
                self.assertEqual(str(ctx.exception), case["message"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_merge -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rig.merge'`

- [ ] **Step 3: Write the implementation**

Create `rig/merge.py`:

```python
"""Parse the RESULT line of a load process and merge the loader records of one stage."""

import json
from dataclasses import dataclass

LATE_LIMIT_MS = 1000
ERROR_KEYS = ("connect", "read", "write", "status", "timeout", "dropped")
PERCENTILE_KEYS = ("p50", "p90", "p95", "p99", "p999", "max")
_RESULT_PREFIX = "RESULT "


class MissingLoader(ValueError):
    pass


class LateLoader(ValueError):
    pass


@dataclass(frozen=True)
class LoaderRecord:
    loader: str
    tool: str
    late_ms: int
    duration_us: int
    requests: int
    bytes: int
    errors: dict[str, int]
    latency_us: dict[str, float]
    requests_per_sec: float


@dataclass(frozen=True)
class Merged:
    requests: int
    successful: int
    bytes: int
    errors: dict[str, int]
    achieved_rps: float
    successful_rps: float
    latency_us: dict[str, float]


def parse_result(text: str, loader: str) -> LoaderRecord | None:
    lines = [line for line in text.splitlines() if line.startswith(_RESULT_PREFIX)]
    if not lines:
        return None
    doc = json.loads(lines[-1][len(_RESULT_PREFIX):])
    try:
        return LoaderRecord(
            loader=loader,
            tool=doc["tool"],
            late_ms=int(doc["late_ms"]),
            duration_us=int(doc["duration_us"]),
            requests=int(doc["requests"]),
            bytes=int(doc["bytes"]),
            errors={key: int(doc["errors"][key]) for key in ERROR_KEYS},
            latency_us={key: float(doc["latency_us"][key]) for key in ("mean", *PERCENTILE_KEYS)},
            requests_per_sec=float(doc["requests_per_sec"]),
        )
    except KeyError as exc:
        raise ValueError(f"{loader}: RESULT line has no key {exc}") from None


def merge(records: dict[str, LoaderRecord | None], stage_s: int) -> Merged:
    present = []
    for loader, record in records.items():
        if record is None:
            raise MissingLoader(f"loader {loader} returned no RESULT line")
        if record.late_ms > LATE_LIMIT_MS:
            raise LateLoader(f"loader {loader} started {record.late_ms} ms late")
        present.append(record)
    requests = sum(r.requests for r in present)
    errors = {key: sum(r.errors[key] for r in present) for key in ERROR_KEYS}
    successful = requests - errors["status"]
    # A stage where no loader completed a request has no latency to weight.
    mean = sum(r.latency_us["mean"] * r.requests for r in present) / requests if requests else 0.0
    latency = {"mean": mean}
    for key in PERCENTILE_KEYS:
        latency[key] = max(r.latency_us[key] for r in present)
    return Merged(
        requests=requests,
        successful=successful,
        bytes=sum(r.bytes for r in present),
        errors=errors,
        achieved_rps=requests / stage_s,
        successful_rps=successful / stage_s,
        latency_us=latency,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS, `Ran 15 tests` and `OK` (8 registry, 3 ladder, 4 merge)

- [ ] **Step 5: Commit**

```bash
git add rig/merge.py tests/test_merge.py
git commit -s -S -m "feat: parse loader RESULT lines and merge a stage"
```

### Task 4: Snapshots and flags

**Files:**
- Create: `rig/flags.py`
- Test: `tests/test_flags.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `GENERATOR_BUSY = 85`, `SERVER_BUSY = 90`, `LOG_GROWTH_BYTES = 65536`, `Snapshot(cpu: tuple[int, int], ena: dict[str, int], conns: tuple[int, int] | None)`, `parse_snapshot(text: str) -> Snapshot`, `cpu_pct(before: Snapshot, after: Snapshot) -> int`, `ena_delta(before: Snapshot, after: Snapshot) -> dict[str, int]`, `stage_flags(server_busy: int, loader_busy: dict[str, int], passed: bool) -> dict`, `stage_void(loader_ena: dict[str, dict[str, int]]) -> str | None`, `keepalive_flag(before: Snapshot, after: Snapshot, connections: int) -> dict`, `cell_flags(pids_before: list[int], pids_after: list[int], log_before: int, log_after: int) -> dict`.
- Produces (rules later tasks rely on): `cpu_pct` uses integer division, so 84.9% is 84 and stays under `GENERATOR_BUSY`. `stage_void` reports the first loader in the insertion order of `loader_ena` and the first counter in the insertion order of its delta; Task 14 builds `loader_ena` in loader order. `keepalive_flag` takes server snapshots only, because only the server snapshot has a `conns` line.

- [ ] **Step 1: Write the failing test**

Create `tests/test_flags.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_flags -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rig.flags'`

- [ ] **Step 3: Write the implementation**

Create `rig/flags.py`:

```python
"""Snapshot parsing and the flag and void rules of a stage and a cell."""

from dataclasses import dataclass

GENERATOR_BUSY = 85
SERVER_BUSY = 90
LOG_GROWTH_BYTES = 65536


@dataclass(frozen=True)
class Snapshot:
    """One `box/snapshot.sh` output. `conns` is None on a loader."""

    cpu: tuple[int, int]
    ena: dict[str, int]
    conns: tuple[int, int] | None


def parse_snapshot(text: str) -> Snapshot:
    """Read the cpu, ena, and conns lines of a snapshot."""
    cpu = None
    ena = {}
    conns = None
    for line in text.splitlines():
        parts = line.split()
        if parts[0] == "cpu":
            cpu = (int(parts[1]), int(parts[2]))
        elif parts[0] == "ena":
            ena[parts[1]] = int(parts[2])
        elif parts[0] == "conns":
            conns = (int(parts[1]), int(parts[2]))
    return Snapshot(cpu=cpu, ena=ena, conns=conns)


def cpu_pct(before: Snapshot, after: Snapshot) -> int:
    """Busy percent over the window. Integer division drops the fraction."""
    busy = after.cpu[0] - before.cpu[0]
    total = after.cpu[1] - before.cpu[1]
    if total == 0:
        return 0
    return 100 * busy // total


def ena_delta(before: Snapshot, after: Snapshot) -> dict[str, int]:
    """The counters that changed during the window, with the change."""
    delta = {}
    for name, value in after.ena.items():
        change = value - before.ena.get(name, 0)
        if change:
            delta[name] = change
    return delta


def stage_flags(server_busy: int, loader_busy: dict[str, int], passed: bool) -> dict:
    """Review flags of one stage. A passing stage has no flags."""
    if passed or server_busy >= SERVER_BUSY:
        return {}
    if any(busy >= GENERATOR_BUSY for busy in loader_busy.values()):
        return {"generator_bound": True}
    return {"server_unsaturated": True}


def stage_void(loader_ena: dict[str, dict[str, int]]) -> str | None:
    """The void reason when the network shaped a loader during the stage."""
    for loader, delta in loader_ena.items():
        if delta:
            counter, change = next(iter(delta.items()))
            return f"loader {loader} throttled: {counter}={change}"
    return None


def keepalive_flag(before: Snapshot, after: Snapshot, connections: int) -> dict:
    """Flag a server TIME-WAIT growth above the total connection count."""
    delta = after.conns[1] - before.conns[1]
    if delta > connections:
        return {"keepalive_broken": {"time_wait_delta": delta, "connections": connections}}
    return {}


def cell_flags(pids_before: list[int], pids_after: list[int], log_before: int, log_after: int) -> dict:
    """Review flags of one cell from the probes before and after the load."""
    flags = {}
    if sorted(pids_before) != sorted(pids_after):
        flags["worker_churn"] = True
    growth = log_after - log_before
    if growth > LOG_GROWTH_BYTES:
        flags["log_growth"] = growth
    return flags
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_flags -v`
Expected: PASS: `Ran 7 tests` and `OK`

- [ ] **Step 5: Commit**

```bash
git add rig/flags.py tests/test_flags.py
git commit -s -S -m "feat: add snapshot parsing and the flag rules"
```

### Task 5: Run file

**Files:**
- Create: `rig/runfile.py`
- Test: `tests/test_runfile.py`

**Interfaces:**
- Consumes: `rig.VERSION = "1"` from `rig/__init__.py` (Task 1).
- Produces: `SCHEMA = "rapira-bench-run/1"`, `RunFile(*, run_id, suite, rig, rapira, servers, apps, loaders, ladder, processes, plan, smoke, started)`, `RunFile.doc` (the document dict), `RunFile.add_cell(cell: dict) -> None`, `RunFile.finish(finished: str) -> dict`, `RunFile.write(path: Path) -> None`, `run_status(plan: list[str], cells: list[dict]) -> tuple[str, list[str]]`.
- Produces (document shape): the top-level keys in this order: `schema`, `id`, `suite`, `smoke`, `started`, `finished`, `rig`, `rapira`, `servers`, `apps`, `loaders`, `ladder`, `processes`, `plan`, `cells`, `status`, `reasons`, `reporter`. `status` is the string `"complete"` or `"incomplete"`. `reasons` is the list of strings from `run_status`. `finished` is null until `finish`. `reporter` is `rig.VERSION`. Before `finish`, `status` is `"incomplete"`.
- Produces (reason strings): `"<key>: missing"`, `"<key>: void: <cell reason>"`, `"<key>: <cell status>"` for any other status that is not `ok`, and `"<key>: unplanned cell"`. Planned keys come first in plan order, unplanned cells last.
- Produces (write rule): `write` does not create the parent directory. Task 14 creates `runs/<id>/` before it writes raw evidence.

- [ ] **Step 1: Write the failing test**

Create `tests/test_runfile.py`:

```python
"""Tests of the run file writer and the run status rules."""

import json
import tempfile
import unittest
from pathlib import Path

from rig.runfile import SCHEMA, RunFile, run_status


def cell(key, status="ok", reason=None):
    """A cell with the fields that the status rules read."""
    out = {"key": key, "status": status}
    if reason is not None:
        out["reason"] = reason
    return out


PLAN = ["r1-hello-rapira-worker", "r1-hello-php-fpm"]

STATUS_CASES = [
    {
        "name": "every planned cell ok",
        "cells": [cell("r1-hello-rapira-worker"), cell("r1-hello-php-fpm")],
        "status": "complete",
        "reasons": [],
    },
    {
        "name": "planned cell missing",
        "cells": [cell("r1-hello-rapira-worker")],
        "status": "incomplete",
        "reasons": ["r1-hello-php-fpm: missing"],
    },
    {
        "name": "void cell",
        "cells": [cell("r1-hello-rapira-worker"), cell("r1-hello-php-fpm", "void", "probe mismatch on loader-2")],
        "status": "incomplete",
        "reasons": ["r1-hello-php-fpm: void: probe mismatch on loader-2"],
    },
    {
        "name": "incomplete cell",
        "cells": [cell("r1-hello-rapira-worker", "incomplete"), cell("r1-hello-php-fpm")],
        "status": "incomplete",
        "reasons": ["r1-hello-rapira-worker: incomplete"],
    },
    {
        "name": "unplanned cell",
        "cells": [cell("r1-hello-rapira-worker"), cell("r1-hello-php-fpm"), cell("r2-hello-php-fpm")],
        "status": "incomplete",
        "reasons": ["r2-hello-php-fpm: unplanned cell"],
    },
    {
        "name": "reasons follow the plan order, unplanned cells last",
        "cells": [cell("r9-static-rapira-hit"), cell("r1-hello-php-fpm", "void", "no RESULT line from loader-3")],
        "status": "incomplete",
        "reasons": [
            "r1-hello-rapira-worker: missing",
            "r1-hello-php-fpm: void: no RESULT line from loader-3",
            "r9-static-rapira-hit: unplanned cell",
        ],
    },
]

# The top-level keys in the order of spec section 6.1, with `reasons` after `status`.
TOP_KEYS = [
    "schema", "id", "suite", "smoke", "started", "finished", "rig", "rapira", "servers", "apps",
    "loaders", "ladder", "processes", "plan", "cells", "status", "reasons", "reporter",
]

STAGE = {
    "rate": 10000,
    "duration_s": 20,
    "pass": True,
    "merged": {
        "requests": 199990,
        "successful": 199990,
        "bytes": 25198740,
        "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
        "achieved_rps": 9999.5,
        "successful_rps": 9999.5,
    },
    "latency_us": {"mean": 689.5, "p50": 689, "p90": 1111, "p95": 1175, "p99": 1264, "p999": 1351, "max": 2822},
    "loaders": [],
    "server": {"busy_cpu": 12, "pss_kb": 912384, "established": 256, "time_wait": 0},
}


def new_run_file():
    return RunFile(
        run_id="20260925T120000Z-ci-0a1b2c3",
        suite={"name": "ci", "sha256": "ab" * 32},
        rig={"server_type": "c7a.8xlarge", "loader_type": "c7a.xlarge", "loader_count": 4, "az": "eu-central-1a"},
        rapira={"ref": "main", "sha": "0a1b2c3d", "version": "0.9.0", "build": "nightly"},
        servers={"frankenphp": "FrankenPHP v1.12.7"},
        apps={"apps/hello/worker.php": "cd" * 32},
        loaders=[{"name": "loader-1", "private_ip": "10.0.1.11", "wrk2": "44a94c1", "k6": "2.2.0"}],
        ladder={"stage_s": 20, "ratio": 2, "pass_tolerance": 0.95, "rates": {"hello": [10000, 20000]}},
        processes=32,
        plan=["r1-hello-rapira-worker"],
        smoke=False,
        started="2026-09-25T12:00:00Z",
    )


class TestRunStatus(unittest.TestCase):
    def test_run_status(self):
        for case in STATUS_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(run_status(PLAN, case["cells"]), (case["status"], case["reasons"]))


class TestRunFile(unittest.TestCase):
    def test_written_document_round_trips_with_numbers(self):
        run = new_run_file()
        run.add_cell({"key": "r1-hello-rapira-worker", "status": "ok", "stages": [STAGE]})
        doc = run.finish("2026-09-25T12:40:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            run.write(path)
            text = path.read_text()
        loaded = json.loads(text)
        self.assertEqual(loaded, doc)
        self.assertEqual(list(loaded), TOP_KEYS)
        self.assertEqual(loaded["schema"], SCHEMA)
        self.assertEqual((loaded["status"], loaded["reasons"]), ("complete", []))
        # indent=1 puts one space before each top-level key.
        self.assertEqual(text.splitlines()[1], ' "schema": "rapira-bench-run/1",')
        self.assertTrue(text.endswith("}\n"))
        merged = loaded["cells"][0]["stages"][0]["merged"]
        self.assertIsInstance(merged["requests"], int)
        self.assertIsInstance(merged["achieved_rps"], float)
        self.assertIsInstance(loaded["processes"], int)

    def test_finish_with_a_void_cell_is_incomplete(self):
        run = new_run_file()
        run.add_cell({"key": "r1-hello-rapira-worker", "status": "void", "reason": "loader loader-2 throttled: pps_allowance_exceeded=5"})
        doc = run.finish("2026-09-25T12:40:00Z")
        self.assertEqual(doc["finished"], "2026-09-25T12:40:00Z")
        self.assertEqual(doc["status"], "incomplete")
        self.assertEqual(doc["reasons"], ["r1-hello-rapira-worker: void: loader loader-2 throttled: pps_allowance_exceeded=5"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_runfile -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rig.runfile'`

- [ ] **Step 3: Write the implementation**

Create `rig/runfile.py`:

```python
"""Build and write the run file."""

import json
from pathlib import Path

from rig import VERSION

SCHEMA = "rapira-bench-run/1"


def run_status(plan: list[str], cells: list[dict]) -> tuple[str, list[str]]:
    """The run status and one reason per missing, void, incomplete, or unplanned cell."""
    by_key = {cell["key"]: cell for cell in cells}
    reasons = []
    for key in plan:
        cell = by_key.get(key)
        if cell is None:
            reasons.append(f"{key}: missing")
        elif cell["status"] == "void":
            reasons.append(f"{key}: void: {cell['reason']}")
        elif cell["status"] != "ok":
            reasons.append(f"{key}: {cell['status']}")
    for cell in cells:
        if cell["key"] not in plan:
            reasons.append(f"{cell['key']}: unplanned cell")
    if reasons:
        return "incomplete", reasons
    return "complete", []


class RunFile:
    """The `rapira-bench-run/1` document of one run."""

    def __init__(self, *, run_id: str, suite: dict, rig: dict, rapira: dict, servers: dict, apps: dict, loaders: list[dict], ladder: dict, processes: int, plan: list[str], smoke: bool, started: str):
        self.doc = {
            "schema": SCHEMA,
            "id": run_id,
            "suite": suite,
            "smoke": smoke,
            "started": started,
            "finished": None,
            "rig": rig,
            "rapira": rapira,
            "servers": servers,
            "apps": apps,
            "loaders": loaders,
            "ladder": ladder,
            "processes": processes,
            "plan": plan,
            "cells": [],
            "status": "incomplete",
            "reasons": [],
            "reporter": VERSION,
        }

    def add_cell(self, cell: dict) -> None:
        """Append one finished cell."""
        self.doc["cells"].append(cell)

    def finish(self, finished: str) -> dict:
        """Set the end time and the status, and return the document."""
        self.doc["finished"] = finished
        self.doc["status"], self.doc["reasons"] = run_status(self.doc["plan"], self.doc["cells"])
        return self.doc

    def write(self, path: Path) -> None:
        """Write the document as JSON with a trailing newline."""
        path.write_text(json.dumps(self.doc, indent=1, sort_keys=False) + "\n")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_runfile -v`
Expected: PASS: `Ran 3 tests` and `OK`

- [ ] **Step 5: Commit**

```bash
git add rig/runfile.py tests/test_runfile.py
git commit -s -S -m "feat: add the run file writer and the run status rules"
```

### Task 6: Report

**Files:**
- Create: `rig/report.py`
- Create: `rig/__main__.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: the run file shape of Task 5: `run["cells"]`, `run["status"]` (`"complete"` or `"incomplete"`), `run["reasons"]` (list of strings). From each cell: `key`, `target["name"]`, `target["app"]`, `status`, `reason` (void cells), `flags`, `held` (`{"rate", "stage"}` or null), `peak` (number or null), `unloaded` (`{"p50", "p99"}`), `stages[i]["latency_us"]["p99"]`, `stages[-1]["pass"]`, `stages[-1]["fail_reason"]`.
- Consumes (rule for Task 14): `cell["flags"]` holds every flag of the cell, the stage flags included (`generator_bound`, `server_unsaturated`, `ena_throttled`, `keepalive_broken`, `died`), as spec section 6.1 shows. The report reads no flags from the stages.
- Produces: `render(run: dict) -> tuple[str, int]`, exit status 1 when `run["status"] != "complete"`.
- Produces: `rows(run: dict) -> list[dict]`, one row per target with at least one `ok` cell, sorted by app name, then by peak from high to low. Row keys: `name`, `app`, `held` (median held rate or None), `peak` (median peak or None), `p99_held` (median p99 in microseconds at the held stage or None), `unloaded_p50` (median in microseconds), `n` (surviving cells), `spread` (`100 * (max - min) / median` of the peaks, or None), `flags` (sorted list of `name` or `name(value)` strings), `fails` (distinct fail reasons in cell order). Task 7 uses `rows`.
- Produces: `rig/__main__.py` with `main(argv: list[str] | None = None) -> int` and the subcommand `python3 -m rig report <run.json>`. Each subcommand is a `sub.add_parser(...)` with `set_defaults(func=cmd_<name>)` and a function `cmd_<name>(args) -> int`; Tasks 7, 13, and 14 add their subcommands in the same form.
- Produces (text format): columns `target`, `held req/s`, `peak req/s`, `p99 at held`, `unloaded p50`, `n`, `spread`, `flags`, `fail`. Latencies print in ms with two decimals, rates with no decimals, and `-` for a missing value. Void cells print under `VOIDED cells (excluded from every number above):`. An incomplete run ends with `INCOMPLETE RUN: <reasons joined by "; ">.` and `Do not publish these tables.`

- [ ] **Step 1: Write the failing test**

Create `tests/test_report.py`:

```python
"""Tests of the report tables on synthetic run files."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from rig.__main__ import main
from rig.report import render

FLOOR = 10000
VOIDED_TITLE = "VOIDED cells (excluded from every number above):"
FOOTER = "Do not publish these tables."


def stage(rate, passed, p50, p99, fail_reason=None):
    out = {"rate": rate, "duration_s": 20, "pass": passed, "latency_us": {"p50": p50, "p99": p99}}
    if not passed:
        out["fail_reason"] = fail_reason
    return out


def ok_cell(name, app, round_no, *, held, held_p99, peak, unloaded_p50, fail_reason, flags=None):
    """An ok cell. With `held` None the first stage fails at the floor."""
    if held is None:
        stages = [stage(FLOOR, False, unloaded_p50, 900000, fail_reason)]
        held_field = None
    else:
        stages = [
            stage(FLOOR, True, unloaded_p50, 1500),
            stage(held, True, 900, held_p99),
            stage(held * 2, False, 5000, 900000, fail_reason),
        ]
        held_field = {"rate": held, "stage": 1}
    return {
        "key": f"r{round_no}-{name}",
        "target": {"name": name, "app": app},
        "round": round_no,
        "status": "ok",
        "flags": flags or {},
        "held": held_field,
        "peak": peak,
        "unloaded": {"p50": unloaded_p50, "p99": stages[0]["latency_us"]["p99"]},
        "stages": stages,
    }


def void_cell(name, app, round_no, reason):
    return {
        "key": f"r{round_no}-{name}",
        "target": {"name": name, "app": app},
        "round": round_no,
        "status": "void",
        "reason": reason,
        "flags": {},
        "held": None,
        "peak": None,
        "unloaded": None,
        "stages": [],
    }


def run_doc(cells, status="complete", reasons=()):
    return {"id": "20260925T120000Z-ci-0a1b2c3", "cells": cells, "status": status, "reasons": list(reasons)}


HELLO_RAPIRA = ok_cell(
    "hello-rapira-worker", "hello", 1,
    held=640000, held_p99=1264, peak=1180234.0, unloaded_p50=689,
    fail_reason="achieved 1180234 req/s under 95% of 1280000",
)
# 1264 us is 1.26 ms, 689 us is 0.69 ms. One round has a spread of 0.
HELLO_RAPIRA_ROW = [
    "hello-rapira-worker", "640000", "1180234", "1.26ms", "0.69ms", "1", "0.0%", "-",
    "achieved 1180234 req/s under 95% of 1280000",
]

CASES = [
    {
        "name": "single round",
        "run": run_doc([HELLO_RAPIRA]),
        "rows": [HELLO_RAPIRA_ROW],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "rows sort by app then by peak from high to low",
        "run": run_doc([
            ok_cell("hello-php-fpm", "hello", 1, held=40000, held_p99=3000, peak=60000.0, unloaded_p50=800,
                    fail_reason="achieved 60000 req/s under 95% of 80000"),
            HELLO_RAPIRA,
            ok_cell("grpc-rapira", "grpc", 1, held=80000, held_p99=2500, peak=150000.0, unloaded_p50=400,
                    fail_reason="dropped iterations: 3100"),
        ]),
        "rows": [
            ["grpc-rapira", "80000", "150000", "2.50ms", "0.40ms", "1", "0.0%", "-", "dropped iterations: 3100"],
            HELLO_RAPIRA_ROW,
            ["hello-php-fpm", "40000", "60000", "3.00ms", "0.80ms", "1", "0.0%", "-", "achieved 60000 req/s under 95% of 80000"],
        ],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "three rounds with a void",
        "run": run_doc(
            [
                ok_cell("symfony-rapira-worker", "symfony", 1, held=40000, held_p99=5000, peak=52000.0, unloaded_p50=900,
                        fail_reason="achieved 52000 req/s under 95% of 80000", flags={"worker_churn": True}),
                void_cell("symfony-rapira-worker", "symfony", 2, "probe mismatch on loader-2"),
                ok_cell("symfony-rapira-worker", "symfony", 3, held=40000, held_p99=7000, peak=48000.0, unloaded_p50=1100,
                        fail_reason="achieved 48000 req/s under 95% of 80000"),
            ],
            "incomplete",
            ["r2-symfony-rapira-worker: void: probe mismatch on loader-2"],
        ),
        # Two rounds survive. Peak median (52000 + 48000) / 2 = 50000, spread 100 * 4000 / 50000 = 8.0%.
        # p99 at held median (5000 + 7000) / 2 = 6000 us, unloaded p50 median (900 + 1100) / 2 = 1000 us.
        "rows": [[
            "symfony-rapira-worker", "40000", "50000", "6.00ms", "1.00ms", "2", "8.0%", "worker_churn",
            "achieved 52000 req/s under 95% of 80000; achieved 48000 req/s under 95% of 80000",
        ]],
        "voided": ["r2-symfony-rapira-worker: probe mismatch on loader-2"],
        "footer": ["INCOMPLETE RUN: r2-symfony-rapira-worker: void: probe mismatch on loader-2.", FOOTER],
        "status": 1,
    },
    {
        "name": "generator_bound row with value flags",
        "run": run_doc([ok_cell(
            "hello-rapira-worker", "hello", 1,
            held=640000, held_p99=1264, peak=1180234.0, unloaded_p50=689,
            fail_reason="achieved 1180234 req/s under 95% of 1280000",
            flags={"generator_bound": True, "log_growth": 70000, "ena_throttled": {"pps_allowance_exceeded": 946}},
        )]),
        "rows": [HELLO_RAPIRA_ROW[:7] + [
            "ena_throttled(pps_allowance_exceeded=946),generator_bound,log_growth(70000)",
            "achieved 1180234 req/s under 95% of 1280000",
        ]],
        "voided": [],
        "footer": [],
        "status": 0,
    },
    {
        "name": "incomplete run with a missing cell",
        "run": run_doc([HELLO_RAPIRA], "incomplete", ["r1-hello-php-fpm: missing"]),
        "rows": [HELLO_RAPIRA_ROW],
        "voided": [],
        "footer": ["INCOMPLETE RUN: r1-hello-php-fpm: missing.", FOOTER],
        "status": 1,
    },
    {
        "name": "fail reason of a first stage with status errors",
        "run": run_doc([ok_cell("hello-php-fpm", "hello", 1, held=None, held_p99=None, peak=9988.0, unloaded_p50=2400,
                                fail_reason="status errors: 12")]),
        "rows": [["hello-php-fpm", "-", "9988", "-", "2.40ms", "1", "0.0%", "-", "status errors: 12"]],
        "voided": [],
        "footer": [],
        "status": 0,
    },
]


def parse(text):
    """The table rows split into 9 fields, the voided lines, and the footer lines."""
    lines = text.splitlines()
    end = lines.index("") if "" in lines else len(lines)
    table = [line.split(None, 8) for line in lines[2:end]]
    voided = []
    if VOIDED_TITLE in lines:
        start = lines.index(VOIDED_TITLE) + 1
        for line in lines[start:]:
            if not line:
                break
            voided.append(line.strip())
    footer = lines[-2:] if lines[-1] == FOOTER else []
    return table, voided, footer


class TestReport(unittest.TestCase):
    def test_render(self):
        for case in CASES:
            with self.subTest(name=case["name"]):
                text, status = render(case["run"])
                table, voided, footer = parse(text)
                self.assertEqual(table, case["rows"])
                self.assertEqual(voided, case["voided"])
                self.assertEqual(footer, case["footer"])
                self.assertEqual(status, case["status"])

    def test_cli_exit_status_of_an_incomplete_run(self):
        run = run_doc([HELLO_RAPIRA], "incomplete", ["r1-hello-php-fpm: missing"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            path.write_text(json.dumps(run))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                status = main(["report", str(path)])
        self.assertEqual(status, 1)
        self.assertTrue(out.getvalue().endswith(FOOTER + "\n"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_report -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rig.__main__'`

- [ ] **Step 3: Write the report module and the command line**

Create `rig/report.py`:

```python
"""Text tables of one run file."""

import statistics


def median_of(values):
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def flag_text(name, value):
    """`name` for a true flag, `name(value)` for a flag with a value."""
    if value is True:
        return name
    if isinstance(value, dict):
        return f"{name}({','.join(f'{k}={v}' for k, v in value.items())})"
    return f"{name}({value})"


def target_row(cells: list[dict]) -> dict:
    """The medians, the spread, the flags, and the fail reasons of the ok cells of one target."""
    held = [c for c in cells if c["held"] is not None]
    peaks = [c["peak"] for c in cells if c["peak"] is not None]
    peak = statistics.median(peaks) if peaks else None
    fails = []
    for c in cells:
        last = c["stages"][-1]
        if not last["pass"] and last["fail_reason"] not in fails:
            fails.append(last["fail_reason"])
    return {
        "name": cells[0]["target"]["name"],
        "app": cells[0]["target"]["app"],
        "held": median_of(c["held"]["rate"] for c in held),
        "peak": peak,
        "p99_held": median_of(c["stages"][c["held"]["stage"]]["latency_us"]["p99"] for c in held),
        "unloaded_p50": median_of(c["unloaded"]["p50"] for c in cells),
        "n": len(cells),
        "spread": 100.0 * (max(peaks) - min(peaks)) / peak if peak else None,
        "flags": sorted({flag_text(k, v) for c in cells for k, v in c["flags"].items()}),
        "fails": fails,
    }


def rows(run: dict) -> list[dict]:
    """One row per target with ok cells, sorted by app, then by peak from high to low."""
    groups = {}
    for cell in run["cells"]:
        if cell["status"] == "ok":
            groups.setdefault(cell["target"]["name"], []).append(cell)
    out = [target_row(cells) for cells in groups.values()]
    out.sort(key=lambda r: (r["app"], -(r["peak"] or 0)))
    return out


def num(value):
    return f"{value:.0f}" if value is not None else "-"


def ms(us):
    return f"{us / 1000:.2f}ms" if us is not None else "-"


def pct(value):
    return f"{value:.1f}%" if value is not None else "-"


def render(run: dict) -> tuple[str, int]:
    """The report text and the exit status: 1 when the run is incomplete."""
    table = rows(run)
    w = max([len("target")] + [len(r["name"]) for r in table]) + 2
    flags = {r["name"]: ",".join(r["flags"]) or "-" for r in table}
    fw = max([len("flags")] + [len(f) for f in flags.values()])
    hdr = f"{'target':<{w}} {'held req/s':>10} {'peak req/s':>10} {'p99 at held':>11} {'unloaded p50':>12} {'n':>3} {'spread':>7}  {'flags':<{fw}}  fail"
    lines = [hdr, "-" * len(hdr)]
    for r in table:
        fail = "; ".join(r["fails"]) or "-"
        lines.append(
            f"{r['name']:<{w}} {num(r['held']):>10} {num(r['peak']):>10} {ms(r['p99_held']):>11} "
            f"{ms(r['unloaded_p50']):>12} {r['n']:>3} {pct(r['spread']):>7}  {flags[r['name']]:<{fw}}  {fail}"
        )
    voided = [c for c in run["cells"] if c["status"] == "void"]
    if voided:
        lines += ["", "VOIDED cells (excluded from every number above):"]
        lines += [f"  {c['key']}: {c['reason']}" for c in voided]
    if run["status"] != "complete":
        lines += ["", f"INCOMPLETE RUN: {'; '.join(run['reasons'])}.", "Do not publish these tables."]
        return "\n".join(lines) + "\n", 1
    return "\n".join(lines) + "\n", 0
```

Create `rig/__main__.py`:

```python
"""Command line of the bench rig: `python3 -m rig <command>`."""

import argparse
import json
import sys
from pathlib import Path

from rig.report import render


def cmd_report(args) -> int:
    text, status = render(json.loads(Path(args.run).read_text()))
    sys.stdout.write(text)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m rig")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="print the tables of one run file")
    report.add_argument("run", help="path to run.json")
    report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_report -v`
Expected: PASS: `Ran 2 tests` and `OK`

- [ ] **Step 5: Commit**

```bash
git add rig/report.py rig/__main__.py tests/test_report.py
git commit -s -S -m "feat: add the run report and the rig command line"
```

### Task 7: Compare and publish

**Files:**
- Create: `rig/compare.py`
- Create: `rig/publish.py`
- Modify: `rig/__main__.py:8`, `rig/__main__.py:14-17`, `rig/__main__.py:23-24`
- Test: `tests/test_compare.py`
- Test: `tests/test_publish.py`

**Interfaces:**
- Consumes: `rows(run: dict) -> list[dict]` from `rig/report.py` (Task 6) with the keys `name`, `held`, `peak`, `spread`. `main(argv)` and the subparser form of `rig/__main__.py` (Task 6). The run file keys of Task 5: `id`, `started`, `suite["name"]`, `rapira["sha"]`, `rapira["version"]`, `status`, `smoke`, `processes`, `rig["server_type"]`, `rig["loader_type"]`, `rig["loader_count"]`, `ladder["stage_s"]`.
- Produces: `compare(a: dict, b: dict, *, force: bool = False) -> tuple[str, int]`, exit status 1 on a refusal, else 0. `IDENTITY` (the five fields that must match).
- Produces: `publish(run: dict, pages_dir: Path) -> Path`, `INDEX_SCHEMA = "rapira-bench-index/1"`, `index_entry(run: dict) -> dict`.
- Produces (paths for Tasks 16 and 18): publish writes `<pages_dir>/data/<id>.json` and the manifest `<pages_dir>/data/index.json`, so the board fetches `data/index.json`, then `data/<id>.json`. The manifest is `{"schema": "rapira-bench-index/1", "runs": [{"id", "started", "suite", "rapira_sha", "rapira_version", "status", "smoke"}]}`, sorted by `started` ascending, one entry per id.
- Produces (text format): a refusal prints `refused: <field> differs: <a> vs <b>` per field and `Use --force to compare anyway.` With `--force` the same lines start with `forced:`. The table starts with `a: <id>`, `b: <id>`, and an empty line. A target line is `<name>  held <a> -> <b>  peak <a> -> <b>  <delta>%  spread <a>% / <b>%`, with names padded to the longest name. A target in one run only prints `only in a` or `only in b`.
- Produces: `python3 -m rig compare <a.json> <b.json> [--force]` and `python3 -m rig publish --pages-dir <dir> <run.json>`, which prints the data path.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_compare.py`:

```python
"""Tests of the comparison of two run files."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from rig.__main__ import main
from rig.compare import compare


def ok_cell(name, app, round_no, held, peak):
    """An ok cell that holds `held` and fails at twice that rate."""
    return {
        "key": f"r{round_no}-{name}",
        "target": {"name": name, "app": app},
        "round": round_no,
        "status": "ok",
        "flags": {},
        "held": {"rate": held, "stage": 1},
        "peak": peak,
        "unloaded": {"p50": 700, "p99": 1500},
        "stages": [
            {"rate": 10000, "pass": True, "latency_us": {"p50": 700, "p99": 1500}},
            {"rate": held, "pass": True, "latency_us": {"p50": 900, "p99": 2000}},
            {"rate": held * 2, "pass": False, "fail_reason": f"achieved {peak:.0f} req/s under 95% of {held * 2}", "latency_us": {"p50": 5000, "p99": 900000}},
        ],
    }


def run_doc(run_id, cells):
    return {
        "id": run_id,
        "rig": {"server_type": "c7a.8xlarge", "loader_type": "c7a.xlarge", "loader_count": 4, "az": "eu-central-1a"},
        "processes": 32,
        "ladder": {"stage_s": 20},
        "cells": cells,
        "status": "complete",
        "reasons": [],
    }


A_ID = "20260924T120000Z-full-0a1b2c3"
B_ID = "20260925T120000Z-full-4d5e6f7"

# Peaks of three rounds. a: median 1180234.5, spread 100 * (1184200 - 1170000) / 1180234.5 = 1.2%.
# b: median 1120001.0, spread 100 * (1125100 - 1115000) / 1120001 = 0.9%.
# Delta 100 * (1120001.0 - 1180234.5) / 1180234.5 = -5.1%.
A_RUN = run_doc(A_ID, [
    ok_cell("hello-rapira-worker", "hello", 1, 640000, 1170000.0),
    ok_cell("hello-rapira-worker", "hello", 2, 640000, 1180234.5),
    ok_cell("hello-rapira-worker", "hello", 3, 640000, 1184200.0),
])
B_RUN = run_doc(B_ID, [
    ok_cell("hello-rapira-worker", "hello", 1, 640000, 1115000.0),
    ok_cell("hello-rapira-worker", "hello", 2, 640000, 1120001.0),
    ok_cell("hello-rapira-worker", "hello", 3, 640000, 1125100.0),
])
DELTA_LINE = "hello-rapira-worker  held 640000 -> 640000  peak 1180234.5 -> 1120001.0  -5.1%  spread 1.2% / 0.9%"

IDENTITY_CASES = [
    {"name": "server type differs", "section": "rig", "key": "server_type", "value": "c7a.4xlarge",
     "status": 1, "first": "refused: rig.server_type differs: c7a.8xlarge vs c7a.4xlarge"},
    {"name": "loader type differs", "section": "rig", "key": "loader_type", "value": "c7a.2xlarge",
     "status": 1, "first": "refused: rig.loader_type differs: c7a.xlarge vs c7a.2xlarge"},
    {"name": "loader count differs", "section": "rig", "key": "loader_count", "value": 3,
     "status": 1, "first": "refused: rig.loader_count differs: 4 vs 3"},
    {"name": "processes differ", "section": "", "key": "processes", "value": 16,
     "status": 1, "first": "refused: processes differs: 32 vs 16"},
    {"name": "stage duration differs", "section": "ladder", "key": "stage_s", "value": 30,
     "status": 1, "first": "refused: ladder.stage_s differs: 20 vs 30"},
    {"name": "availability zone is not identity", "section": "rig", "key": "az", "value": "eu-central-1b",
     "status": 0, "first": f"a: {A_ID}"},
]


def changed(run, section, key, value):
    out = copy.deepcopy(run)
    if section:
        out[section][key] = value
    else:
        out[key] = value
    return out


class TestCompare(unittest.TestCase):
    def test_identity(self):
        for case in IDENTITY_CASES:
            with self.subTest(name=case["name"]):
                b = changed(B_RUN, case["section"], case["key"], case["value"])
                text, status = compare(A_RUN, b)
                self.assertEqual(status, case["status"])
                self.assertEqual(text.splitlines()[0], case["first"])

    def test_refusal_names_the_force_option(self):
        text, _ = compare(A_RUN, changed(B_RUN, "", "processes", 16))
        self.assertEqual(text.splitlines(), ["refused: processes differs: 32 vs 16", "Use --force to compare anyway."])

    def test_force_prints_the_difference_and_the_deltas(self):
        text, status = compare(A_RUN, changed(B_RUN, "rig", "server_type", "c7a.4xlarge"), force=True)
        self.assertEqual(status, 0)
        self.assertEqual(text.splitlines(), [
            "forced: rig.server_type differs: c7a.8xlarge vs c7a.4xlarge",
            f"a: {A_ID}",
            f"b: {B_ID}",
            "",
            DELTA_LINE,
        ])

    def test_delta_line(self):
        text, status = compare(A_RUN, B_RUN)
        self.assertEqual(status, 0)
        self.assertEqual(text.splitlines()[3:], [DELTA_LINE])

    def test_target_in_one_run_only(self):
        a = run_doc(A_ID, [
            ok_cell("hello-rapira-worker", "hello", 1, 640000, 1180234.5),
            ok_cell("static-rapira-hit", "static", 1, 1280000, 1500000.0),
        ])
        b = run_doc(B_ID, [
            ok_cell("hello-rapira-worker", "hello", 1, 640000, 1120001.0),
            ok_cell("grpc-rapira", "grpc", 1, 80000, 150000.0),
        ])
        text, status = compare(a, b)
        self.assertEqual(status, 0)
        # Names pad to the longest name, hello-rapira-worker (19 characters).
        self.assertEqual(text.splitlines()[3:], [
            "hello-rapira-worker  held 640000 -> 640000  peak 1180234.5 -> 1120001.0  -5.1%  spread 0.0% / 0.0%",
            "static-rapira-hit    only in a",
            "grpc-rapira          only in b",
        ])

    def test_cli_force_flag(self):
        b = changed(B_RUN, "rig", "loader_count", 3)
        with tempfile.TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "a.json"
            path_b = Path(tmp) / "b.json"
            path_a.write_text(json.dumps(A_RUN))
            path_b.write_text(json.dumps(b))
            with contextlib.redirect_stdout(io.StringIO()):
                refused = main(["compare", str(path_a), str(path_b)])
                forced = main(["compare", str(path_a), str(path_b), "--force"])
        self.assertEqual((refused, forced), (1, 0))


if __name__ == "__main__":
    unittest.main()
```

Create `tests/test_publish.py`:

```python
"""Tests of publishing a run file into a gh-pages checkout."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from rig.__main__ import main
from rig.publish import publish


def run_doc(run_id, started, status="complete"):
    return {
        "schema": "rapira-bench-run/1",
        "id": run_id,
        "suite": {"name": "ci", "sha256": "ab" * 32},
        "smoke": False,
        "started": started,
        "rapira": {"ref": "main", "sha": "0a1b2c3d4e5f", "version": "0.9.0", "build": "nightly"},
        "cells": [],
        "status": status,
        "reasons": [],
    }


def entry(run_id, started, status="complete"):
    """The index entry of `run_doc(run_id, started, status)`."""
    return {
        "id": run_id,
        "started": started,
        "suite": "ci",
        "rapira_sha": "0a1b2c3d4e5f",
        "rapira_version": "0.9.0",
        "status": status,
        "smoke": False,
    }


RUN = run_doc("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z")

CASES = [
    {
        "name": "no index yet",
        "index": None,
        "runs": [entry("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z")],
    },
    {
        "name": "new run sorts by start time",
        "index": {"schema": "rapira-bench-index/1", "runs": [
            entry("20260924T120000Z-ci-1111111", "2026-09-24T12:00:00Z"),
            entry("20260926T120000Z-ci-2222222", "2026-09-26T12:00:00Z"),
        ]},
        "runs": [
            entry("20260924T120000Z-ci-1111111", "2026-09-24T12:00:00Z"),
            entry("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z"),
            entry("20260926T120000Z-ci-2222222", "2026-09-26T12:00:00Z"),
        ],
    },
    {
        "name": "same id replaces the entry",
        "index": {"schema": "rapira-bench-index/1", "runs": [
            entry("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z", "incomplete"),
        ]},
        "runs": [entry("20260925T120000Z-ci-0a1b2c3", "2026-09-25T12:00:00Z")],
    },
]


class TestPublish(unittest.TestCase):
    def test_publish(self):
        for case in CASES:
            with self.subTest(name=case["name"]):
                with tempfile.TemporaryDirectory() as tmp:
                    pages = Path(tmp)
                    if case["index"] is not None:
                        (pages / "data").mkdir()
                        (pages / "data" / "index.json").write_text(json.dumps(case["index"]))
                    path = publish(RUN, pages)
                    self.assertEqual(path, pages / "data" / "20260925T120000Z-ci-0a1b2c3.json")
                    self.assertEqual(json.loads(path.read_text()), RUN)
                    index = json.loads((pages / "data" / "index.json").read_text())
                self.assertEqual(index, {"schema": "rapira-bench-index/1", "runs": case["runs"]})

    def test_cli_prints_the_data_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_path = Path(tmp) / "run.json"
            run_path.write_text(json.dumps(RUN))
            pages = Path(tmp) / "pages"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                status = main(["publish", "--pages-dir", str(pages), str(run_path)])
            self.assertEqual(status, 0)
            self.assertEqual(out.getvalue(), f"{pages / 'data' / '20260925T120000Z-ci-0a1b2c3.json'}\n")
            self.assertTrue((pages / "data" / "index.json").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_compare tests.test_publish -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rig.compare'` and `ModuleNotFoundError: No module named 'rig.publish'`

- [ ] **Step 3: Write the compare and publish modules**

Create `rig/compare.py`:

```python
"""Deltas of held and peak between two run files."""

from rig.report import rows

# Run fields that must match for a fair comparison, as (section, key). An empty section is the top level.
IDENTITY = (
    ("rig", "server_type"),
    ("rig", "loader_type"),
    ("rig", "loader_count"),
    ("", "processes"),
    ("ladder", "stage_s"),
)


def identity_diffs(a: dict, b: dict) -> list[str]:
    diffs = []
    for section, key in IDENTITY:
        va = a[section][key] if section else a[key]
        vb = b[section][key] if section else b[key]
        if va != vb:
            name = f"{section}.{key}" if section else key
            diffs.append(f"{name} differs: {va} vs {vb}")
    return diffs


def num(value, digits):
    return f"{value:.{digits}f}" if value is not None else "-"


def spread(value):
    return f"{value:.1f}%" if value is not None else "-"


def compare(a: dict, b: dict, *, force: bool = False) -> tuple[str, int]:
    """The delta table and the exit status: 1 when the rig identity differs and `force` is false."""
    diffs = identity_diffs(a, b)
    if diffs and not force:
        lines = [f"refused: {d}" for d in diffs] + ["Use --force to compare anyway."]
        return "\n".join(lines) + "\n", 1
    lines = [f"forced: {d}" for d in diffs] + [f"a: {a['id']}", f"b: {b['id']}", ""]
    rows_a = {r["name"]: r for r in rows(a)}
    rows_b = {r["name"]: r for r in rows(b)}
    names = list(rows_a) + [n for n in rows_b if n not in rows_a]
    w = max(len(n) for n in names) if names else 0
    for name in names:
        ra = rows_a.get(name)
        rb = rows_b.get(name)
        if rb is None:
            lines.append(f"{name:<{w}}  only in a")
            continue
        if ra is None:
            lines.append(f"{name:<{w}}  only in b")
            continue
        delta = "-"
        if ra["peak"] and rb["peak"] is not None:
            delta = f"{100.0 * (rb['peak'] - ra['peak']) / ra['peak']:+.1f}%"
        lines.append(
            f"{name:<{w}}  held {num(ra['held'], 0)} -> {num(rb['held'], 0)}"
            f"  peak {num(ra['peak'], 1)} -> {num(rb['peak'], 1)}  {delta}"
            f"  spread {spread(ra['spread'])} / {spread(rb['spread'])}"
        )
    return "\n".join(lines) + "\n", 0
```

Create `rig/publish.py`:

```python
"""Write a run file into a gh-pages checkout and update the board manifest."""

import json
from pathlib import Path

INDEX_SCHEMA = "rapira-bench-index/1"


def index_entry(run: dict) -> dict:
    return {
        "id": run["id"],
        "started": run["started"],
        "suite": run["suite"]["name"],
        "rapira_sha": run["rapira"]["sha"],
        "rapira_version": run["rapira"]["version"],
        "status": run["status"],
        "smoke": run["smoke"],
    }


def publish(run: dict, pages_dir: Path) -> Path:
    """Write `data/<id>.json`, add or replace the run in `data/index.json`, and return the data path."""
    data = pages_dir / "data" / f"{run['id']}.json"
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text(json.dumps(run, indent=1) + "\n")
    index_path = data.parent / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text())
    else:
        index = {"schema": INDEX_SCHEMA, "runs": []}
    runs = [r for r in index["runs"] if r["id"] != run["id"]]
    runs.append(index_entry(run))
    runs.sort(key=lambda r: r["started"])
    index["runs"] = runs
    index_path.write_text(json.dumps(index, indent=1) + "\n")
    return data
```

- [ ] **Step 4: Run the tests to verify that only the command line tests fail**

Run: `python3 -m unittest tests.test_compare tests.test_publish -v`
Expected: FAIL: `Ran 8 tests` and `FAILED (errors=2)`. The errors are `test_cli_force_flag` and `test_cli_prints_the_data_path`, with `SystemExit: 2` after `argument command: invalid choice: 'compare'` and `invalid choice: 'publish'` on stderr. The other 6 tests pass.

- [ ] **Step 5: Add the compare and publish subcommands**

In `rig/__main__.py`, replace line 8:

```python
from rig.report import render
```

with:

```python
from rig.compare import compare
from rig.publish import publish
from rig.report import render
```

Replace the end of `cmd_report` and the start of `main` (lines 14 to 17):

```python
    return status


def main(argv: list[str] | None = None) -> int:
```

with:

```python
    return status


def cmd_compare(args) -> int:
    a = json.loads(Path(args.a).read_text())
    b = json.loads(Path(args.b).read_text())
    text, status = compare(a, b, force=args.force)
    sys.stdout.write(text)
    return status


def cmd_publish(args) -> int:
    print(publish(json.loads(Path(args.run).read_text()), Path(args.pages_dir)))
    return 0


def main(argv: list[str] | None = None) -> int:
```

Replace the report subparser end (lines 23 to 24):

```python
    report.set_defaults(func=cmd_report)

```

with:

```python
    report.set_defaults(func=cmd_report)

    comp = sub.add_parser("compare", help="print the held and peak deltas of two run files")
    comp.add_argument("a", help="path to the first run.json")
    comp.add_argument("b", help="path to the second run.json")
    comp.add_argument("--force", action="store_true", help="compare runs whose rig identity differs")
    comp.set_defaults(func=cmd_compare)

    pub = sub.add_parser("publish", help="write a run file into a gh-pages checkout")
    pub.add_argument("--pages-dir", required=True, help="path to the gh-pages checkout")
    pub.add_argument("run", help="path to run.json")
    pub.set_defaults(func=cmd_publish)

```

Check the result:

Run: `sed -n 1,12p rig/__main__.py && python3 -m rig --help`
Expected: the three imports `from rig.compare import compare`, `from rig.publish import publish`, `from rig.report import render`, and a usage line with `{report,compare,publish}`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_compare tests.test_publish tests.test_report -v`
Expected: PASS: `Ran 10 tests` and `OK`

- [ ] **Step 7: Commit**

```bash
git add rig/compare.py rig/publish.py rig/__main__.py tests/test_compare.py tests/test_publish.py
git commit -s -S -m "feat: add run comparison and publishing to gh-pages"
```

### Task 8: Apps and server configs

**Files:**
- Create: `apps/hello/expect.txt`
- Create: `apps/static/tiny.expect`
- Create: `servers/php.ini`
- Create: `servers/rapira/http.toml.tpl`
- Create: `tests/test_templates.py`
- Modify: `php/hello/classic.php`, `php/hello/dispatcher.php`, `php/hello/worker.php` moved to `apps/hello/` without a content change
- Modify: `fleet/franken/index.php` moved to `apps/hello/frankenphp.php`, then `apps/hello/frankenphp.php:2-3`
- Modify: `fleet/fpm/hello.php` moved to `apps/hello/fpm.php`, then `apps/hello/fpm.php:2-6`
- Modify: `fleet/static/app.css`, `fleet/static/tiny.css` moved to `apps/static/` without a content change
- Modify: `fleet/symfony/BenchController.php`, `fleet/symfony/index.php`, `fleet/symfony/bench/worker-franken.php`, `fleet/symfony/bench/worker-rapira.php` moved to `apps/symfony/`, then `apps/symfony/BenchController.php:9-13` and `apps/symfony/index.php:8`
- Modify: `fleet/laravel/BenchController.php`, `fleet/laravel/web.php`, `fleet/laravel/bench/worker-rapira.php` moved to `apps/laravel/`, then `apps/laravel/BenchController.php:8-11` and `apps/laravel/bench/worker-rapira.php:5`
- Modify: `grpc/bench.binpb`, `grpc/bench.proto`, `grpc/echo.bin`, `grpc/echo.grpc`, `grpc/echo.json`, `grpc/expect.bin`, `grpc/expect.grpc`, `grpc/expect.grpcweb`, `grpc/expect.json` moved to `apps/grpc/` without a content change
- Modify: `grpc/buf.gen.yaml` moved to `apps/grpc/buf.gen.yaml`, then `apps/grpc/buf.gen.yaml:5,7`
- Modify: `scripts/grpc-fixtures.py` moved to `apps/grpc/fixtures.py`, then `apps/grpc/fixtures.py:2,8,35`
- Modify: `php/grpc/gen/` moved to `apps/grpc/php/gen/` without a content change
- Modify: `php/grpc/autoload.php`, `php/grpc/dispatcher.php`, `php/grpc/rr-worker.php` moved to `apps/grpc/php/`, then rewritten
- Modify: `fleet/rapira-static.toml` moved to `servers/rapira/static.toml.tpl`, then rewritten
- Modify: `fleet/rapira-grpc.toml.tpl` moved to `servers/rapira/grpc.toml.tpl`, then rewritten
- Modify: `fleet/franken/Caddyfile.app-worker.tpl` moved to `servers/frankenphp/worker.Caddyfile.tpl`, then rewritten
- Modify: `fleet/franken/Caddyfile.app-classic.tpl` moved to `servers/frankenphp/classic.Caddyfile.tpl`, then rewritten
- Modify: `fleet/franken/Caddyfile.tpl` moved to `servers/frankenphp/stock.Caddyfile.tpl`, then rewritten
- Modify: `fleet/fpm/php-fpm.conf.tpl` moved to `servers/php-fpm/php-fpm.conf.tpl`, then rewritten
- Modify: `fleet/fpm/nginx.app.conf.tpl` moved to `servers/nginx/fpm.conf.tpl`, then rewritten
- Modify: `fleet/nginx/rapira.conf.tpl` moved to `servers/nginx/rapira.conf.tpl`, then rewritten
- Modify: `fleet/roadrunner/composer.json`, `fleet/roadrunner/composer.lock` moved to `servers/roadrunner/` without a content change
- Modify: `fleet/roadrunner/grpc.rr.yaml.tpl` moved to `servers/roadrunner/grpc.rr.yaml.tpl`, then rewritten
- Modify: `Makefile:121-123`
- Delete: `grpc/expect.http`
- Delete: `fleet/rapira-static-symfony.toml`
- Delete: `k6/hello.js`
- Test: `tests/test_templates.py`

**Interfaces:**
- Consumes: the paths that `suites/targets.toml` of Task 1 names in `expect`, `config`, and `start`: `apps/hello/expect.txt`, `apps/static/tiny.expect`, `apps/hello/{worker,classic,dispatcher,frankenphp,fpm}.php`, `apps/grpc/php/dispatcher.php`, `apps/grpc/echo.*`, `apps/grpc/expect.*`, and the `servers/` templates.
- Produces: the template files and their placeholders. Every placeholder is `@@NAME@@`. `servers/rapira/http.toml.tpl`: `@@LISTEN@@`, `@@ENTRY@@`, `@@MODE@@`, `@@PROCS@@`. `servers/rapira/static.toml.tpl`: the same four and `@@ROOT@@`. `servers/rapira/grpc.toml.tpl`: `@@LISTEN@@`, `@@RIG@@`, `@@ENTRY@@`, `@@PROCS@@`. `servers/frankenphp/worker.Caddyfile.tpl`: `@@LISTEN@@`, `@@THREADS@@`, `@@DOCROOT@@`, `@@ENTRY@@`, `@@PROCS@@`, `@@ENV@@`. `servers/frankenphp/stock.Caddyfile.tpl`: `@@LISTEN@@`, `@@THREADS@@`, `@@DOCROOT@@`, `@@INDEX@@`, `@@PROCS@@`, `@@ENV@@`. `servers/frankenphp/classic.Caddyfile.tpl`: `@@LISTEN@@`, `@@THREADS@@`, `@@DOCROOT@@`, `@@INDEX@@`. `servers/nginx/rapira.conf.tpl`: `@@PROCS@@`, `@@LISTEN@@`. `servers/nginx/fpm.conf.tpl`: `@@PROCS@@`, `@@LISTEN@@`, `@@DOCROOT@@`, `@@INDEX@@`. `servers/php-fpm/php-fpm.conf.tpl`: `@@PROCS@@`. `servers/roadrunner/grpc.rr.yaml.tpl`: `@@RIG@@`, `@@LISTEN@@`, `@@PROCS@@`. `@@MODE@@` is new: the rapira pool mode.
- Produces: `servers/php.ini`, which provisioning (Task 12) copies to `/opt/bench/php.ini`.
- Produces: `apps/grpc/php/autoload.php` reads the environment variable `GRPC_VENDOR` (the vendor directory of `servers/roadrunner/composer.lock`) and requires `$GRPC_VENDOR/autoload.php`. The rapira gRPC dispatcher and the RoadRunner worker both load it, so both run the pure-PHP protobuf runtime when ext-protobuf is absent (spec 3.6). Task 9 sets `GRPC_VENDOR=/opt/bench/apps/roadrunner-grpc/vendor` in `box/lib.sh`, and Task 12 installs the lock file there.
- Produces: FrankenPHP shapes. `worker`: `file_server off` and the worker with `match *`. `classic`: `file_server off` and `try_files {path} <index>`. `stock`: the global worker block and a plain `php_server` whose `index` and `try_files` name the worker file, so the default file server and the document root stat stay in place, a file in the document root is served, and every other request goes to the worker. The static FrankenPHP targets use `stock` with the document root `@RIG@/apps/static`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_templates.py`:

```python
"""Tests of the server config templates, the shared php.ini, and the expected body files."""

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
        "name": "rapira http behind nginx",
        "path": "servers/rapira/http.toml.tpl",
        "values": {"LISTEN": "127.0.0.1:8081", "ENTRY": "/opt/bench/apps/symfony/bench/worker-rapira.php", "MODE": "worker", "PROCS": "2"},
        "expected": {
            "http": {
                "listen": "127.0.0.1:8081",
                "pool": {"entrypoint": "/opt/bench/apps/symfony/bench/worker-rapira.php", "mode": "worker", "processes": 2},
            },
            "log": {"level": "warn"},
        },
    },
    {
        "name": "rapira static hit and miss",
        "path": "servers/rapira/static.toml.tpl",
        "values": {"LISTEN": ":8080", "ROOT": RIG + "/apps/static", "ENTRY": RIG + "/apps/hello/worker.php", "MODE": "worker", "PROCS": "32"},
        "expected": {
            "http": {
                "listen": ":8080",
                "middleware": ["static"],
                "static": {"root": RIG + "/apps/static"},
                "pool": {"entrypoint": RIG + "/apps/hello/worker.php", "mode": "worker", "processes": 32},
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

TEXT_CASES = [
    {
        "name": "frankenphp worker skips the document root",
        "path": "servers/frankenphp/worker.Caddyfile.tpl",
        # num_threads is the worker num plus one.
        "values": {"LISTEN": ":8080", "THREADS": "33", "PROCS": "32", "DOCROOT": RIG + "/apps/hello", "ENTRY": RIG + "/apps/hello/frankenphp.php", "ENV": ""},
        "present": ["grace_period 2s", "admin off", "auto_https off", "num_threads 33", "num 32", "file_server off", "match *", "file " + RIG + "/apps/hello/frankenphp.php", ":8080 {"],
        "absent": ["encode", "try_files"],
    },
    {
        "name": "frankenphp worker with Octane env lines",
        "path": "servers/frankenphp/worker.Caddyfile.tpl",
        "values": {"LISTEN": ":8080", "THREADS": "3", "PROCS": "2", "DOCROOT": "/opt/bench/apps/laravel/public", "ENTRY": "/opt/bench/apps/laravel/public/frankenphp-worker.php", "ENV": "\t\t\tenv LARAVEL_OCTANE 1\n\t\t\tenv APP_DEBUG false\n"},
        "present": ["\t\t\tenv LARAVEL_OCTANE 1\n\t\t\tenv APP_DEBUG false\n", "num_threads 3", "num 2", "match *"],
        "absent": ["encode"],
    },
    {
        "name": "frankenphp classic runs the index file",
        "path": "servers/frankenphp/classic.Caddyfile.tpl",
        # num_threads is the pool size in the classic shape.
        "values": {"LISTEN": ":8080", "THREADS": "32", "DOCROOT": "/opt/bench/apps/symfony/public", "INDEX": "index.php"},
        "present": ["grace_period 2s", "num_threads 32", "file_server off", "try_files {path} index.php", "root * /opt/bench/apps/symfony/public"],
        "absent": ["worker", "encode"],
    },
    {
        "name": "frankenphp stock keeps the file server",
        "path": "servers/frankenphp/stock.Caddyfile.tpl",
        "values": {"LISTEN": ":8080", "THREADS": "33", "PROCS": "32", "DOCROOT": RIG + "/apps/static", "INDEX": "index.php", "ENV": ""},
        "present": ["grace_period 2s", "num_threads 33", "num 32", "file " + RIG + "/apps/static/index.php", "index index.php", "try_files {path} {path}/index.php index.php", "root * " + RIG + "/apps/static"],
        "absent": ["file_server off", "match *", "encode"],
    },
    {
        "name": "nginx in front of rapira",
        "path": "servers/nginx/rapira.conf.tpl",
        "values": {"PROCS": "32", "LISTEN": "8080"},
        "present": ["worker_processes 32;", "listen 8080 backlog=65535;", "server 127.0.0.1:8081;", "keepalive_requests 1000000;"],
        "absent": [],
    },
    {
        "name": "nginx in front of php-fpm",
        "path": "servers/nginx/fpm.conf.tpl",
        "values": {"PROCS": "32", "LISTEN": "8080", "DOCROOT": "/opt/bench/apps/symfony/public", "INDEX": "index.php"},
        "present": ["worker_processes 32;", "listen 8080 backlog=65535;", "root /opt/bench/apps/symfony/public;", "fastcgi_pass 127.0.0.1:9000;", "SCRIPT_FILENAME  $document_root/index.php;", "SCRIPT_NAME      /index.php;"],
        "absent": ["include"],
    },
    {
        "name": "php-fpm static pool",
        "path": "servers/php-fpm/php-fpm.conf.tpl",
        "values": {"PROCS": "32"},
        "present": ["pm = static", "pm.max_children = 32", "listen = 127.0.0.1:9000"],
        "absent": [],
    },
    {
        "name": "roadrunner grpc",
        "path": "servers/roadrunner/grpc.rr.yaml.tpl",
        "values": {"RIG": RIG, "LISTEN": "0.0.0.0:8080", "PROCS": "32"},
        "present": ['listen: "tcp://0.0.0.0:8080"', "num_workers: 32", 'command: "php ' + RIG + '/apps/grpc/php/rr-worker.php"', '["' + RIG + '/apps/grpc/bench.proto"]'],
        "absent": ["-d opcache"],
    },
]

# The 128-byte asset of the static hit targets.
TINY_CSS = b"/* bench asset, micro tier: the response must be far smaller than the wire */\n.a { color: #1a2b3c; padding: 0 } /*-----------*/\n"

BODY_CASES = [
    {
        "name": "hello expected body",
        "path": "apps/hello/expect.txt",
        # 24 bytes: the body of GET /?name=you on every hello, symfony, and laravel target.
        "expected": b"Hello from worker, you!\n",
    },
    {
        "name": "static asset of 128 bytes",
        "path": "apps/static/tiny.css",
        "expected": TINY_CSS,
    },
    {
        "name": "static hit expected body is the asset",
        "path": "apps/static/tiny.expect",
        "expected": TINY_CSS,
    },
]

# Spec section 3.4: the values of the shared php.ini.
PHP_INI = {
    "opcache.enable": "1",
    "opcache.enable_cli": "1",
    "opcache.validate_timestamps": "0",
    "opcache.jit": "disable",
    "opcache.memory_consumption": "256",
    "memory_limit": "256M",
    "realpath_cache_size": "4096K",
    "realpath_cache_ttl": "600",
    "expose_php": "0",
    "error_log": "/dev/stderr",
}


def render(path, values):
    text = (REPO / path).read_text()
    for name, value in values.items():
        text = text.replace("@@" + name + "@@", value)
    return text


def settings(text):
    # The config lines without the comment lines.
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("#", ";")))


def shared_block(path):
    # The lines from worker_processes to the first keepalive_requests line.
    lines = (REPO / path).read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("worker_processes"))
    end = next(i for i, line in enumerate(lines) if line.strip() == "keepalive_requests 1000000;")
    return lines[start : end + 1]


class TemplateTests(unittest.TestCase):
    def test_toml_templates(self):
        for case in TOML_CASES:
            with self.subTest(name=case["name"]):
                text = render(case["path"], case["values"])
                self.assertNotIn("@@", text)
                self.assertEqual(case["expected"], tomllib.loads(text))

    def test_text_templates(self):
        for case in TEXT_CASES:
            with self.subTest(name=case["name"]):
                text = render(case["path"], case["values"])
                self.assertNotIn("@@", text)
                text = settings(text)
                for part in case["present"]:
                    self.assertIn(part, text)
                for part in case["absent"]:
                    self.assertNotIn(part, text)

    def test_nginx_templates_share_the_http_settings(self):
        self.assertEqual(shared_block("servers/nginx/rapira.conf.tpl"), shared_block("servers/nginx/fpm.conf.tpl"))

    def test_php_ini_values(self):
        values = {}
        for line in (REPO / "servers/php.ini").read_text().splitlines():
            if line and not line.startswith(";"):
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip()
        self.assertEqual(PHP_INI, values)

    def test_expected_bodies(self):
        for case in BODY_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(case["expected"], (REPO / case["path"]).read_bytes())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_templates -v`
Expected: FAIL with `Ran 5 tests` and `FAILED (errors=17)`. Each case reports `FileNotFoundError: [Errno 2] No such file or directory` for a path under `servers/` or `apps/`.

- [ ] **Step 3: Move the files**

Run:

```bash
mkdir -p apps/hello apps/static apps/symfony apps/laravel apps/grpc/php servers/rapira servers/frankenphp servers/php-fpm servers/nginx servers/roadrunner
git mv php/hello/classic.php php/hello/dispatcher.php php/hello/worker.php apps/hello/
git mv fleet/franken/index.php apps/hello/frankenphp.php
git mv fleet/fpm/hello.php apps/hello/fpm.php
git mv fleet/static/app.css fleet/static/tiny.css apps/static/
git mv fleet/symfony/BenchController.php fleet/symfony/index.php fleet/symfony/bench apps/symfony/
git mv fleet/laravel/BenchController.php fleet/laravel/web.php fleet/laravel/bench apps/laravel/
git mv grpc/bench.binpb grpc/bench.proto grpc/buf.gen.yaml grpc/echo.bin grpc/echo.grpc grpc/echo.json grpc/expect.bin grpc/expect.grpc grpc/expect.grpcweb grpc/expect.json apps/grpc/
git rm -q grpc/expect.http
git mv php/grpc/autoload.php php/grpc/dispatcher.php php/grpc/rr-worker.php php/grpc/gen apps/grpc/php/
git mv scripts/grpc-fixtures.py apps/grpc/fixtures.py
git mv fleet/rapira-static.toml servers/rapira/static.toml.tpl
git rm -q fleet/rapira-static-symfony.toml
git mv fleet/rapira-grpc.toml.tpl servers/rapira/grpc.toml.tpl
git mv fleet/franken/Caddyfile.app-worker.tpl servers/frankenphp/worker.Caddyfile.tpl
git mv fleet/franken/Caddyfile.app-classic.tpl servers/frankenphp/classic.Caddyfile.tpl
git mv fleet/franken/Caddyfile.tpl servers/frankenphp/stock.Caddyfile.tpl
git mv fleet/fpm/php-fpm.conf.tpl servers/php-fpm/php-fpm.conf.tpl
git mv fleet/fpm/nginx.app.conf.tpl servers/nginx/fpm.conf.tpl
git mv fleet/nginx/rapira.conf.tpl servers/nginx/rapira.conf.tpl
git mv fleet/roadrunner/composer.json fleet/roadrunner/composer.lock fleet/roadrunner/grpc.rr.yaml.tpl servers/roadrunner/
git rm -q k6/hello.js
git status --short | grep -c '^R'
```

Expected: the last command prints `43`. `grpc/expect.http` duplicates `apps/hello/expect.txt`, and one static template replaces `fleet/rapira-static-symfony.toml`, because no suite has a Symfony static target.

- [ ] **Step 4: Write the rapira templates and the shared php.ini**

Create `servers/php.ini`:

```ini
; The php.ini of every PHP runtime of the rig. rapira, FrankenPHP, and the RoadRunner workers
; load it through PHPRC. php-fpm loads it through -c. The run file records this text.
opcache.enable=1
opcache.enable_cli=1
opcache.validate_timestamps=0
opcache.jit=disable
opcache.memory_consumption=256
memory_limit=256M
realpath_cache_size=4096K
realpath_cache_ttl=600
expose_php=0
error_log=/dev/stderr
```

Create `servers/rapira/http.toml.tpl`:

```toml
# rapira.toml of the rapira HTTP targets. box/servers/rapira.sh renders the placeholders.
# The paths are absolute because rapira resolves a relative path against the directory of this file.
# The warn level is necessary: the rig voids a rapira cell on a WARN or ERROR line.
[http]
listen = "@@LISTEN@@"

[http.pool]
entrypoint = "@@ENTRY@@"
mode = "@@MODE@@"
processes = @@PROCS@@

[log]
level = "warn"
```

Replace the content of `servers/rapira/static.toml.tpl`:

```toml
# rapira.toml of the rapira static targets. box/servers/rapira.sh renders the placeholders.
# The static middleware serves a file from the root. A miss goes to the PHP pool.
# The warn level is necessary: the rig voids a rapira cell on a WARN or ERROR line.
[http]
listen = "@@LISTEN@@"
middleware = ["static"]

[http.static]
root = "@@ROOT@@"

[http.pool]
entrypoint = "@@ENTRY@@"
mode = "@@MODE@@"
processes = @@PROCS@@

[log]
level = "warn"
```

Replace the content of `servers/rapira/grpc.toml.tpl`:

```toml
# rapira.toml of the rapira gRPC targets. box/servers/rapira.sh renders the placeholders.
# The paths are absolute because rapira resolves a relative path against the directory of this file.
# The warn level is necessary: a lost call and a shed request log a WARN line, and the rig voids a
# rapira cell on a WARN or ERROR line.
[grpc]
listen = "@@LISTEN@@"
descriptor_set = "@@RIG@@/apps/grpc/bench.binpb"
services = ["bench.v1.EchoService"]

[grpc.pool]
entrypoint = "@@ENTRY@@"
mode = "dispatcher"
processes = @@PROCS@@

[log]
level = "warn"
```

Run: `python3 -m unittest tests.test_templates.TemplateTests.test_toml_templates tests.test_templates.TemplateTests.test_php_ini_values -v`
Expected: PASS, `Ran 2 tests` and `OK`.

- [ ] **Step 5: Write the FrankenPHP templates**

Replace the content of `servers/frankenphp/worker.Caddyfile.tpl`. The indentation is tabs:

```caddyfile
# FrankenPHP worker shape. box/servers/frankenphp.sh renders the placeholders.
# This is the production shape of the FrankenPHP performance guide. The file server is off and the
# worker matches every path, so no request stats the document root.
# https://frankenphp.dev/docs/performance/#try_files
# num_threads is the worker num plus one, because FrankenPHP needs more threads than workers.
# grace_period 2s limits the stop after TERM to 2 s. No encode directive: the responses stay
# uncompressed.
{
	auto_https off
	admin off
	grace_period 2s

	frankenphp {
		num_threads @@THREADS@@
	}
}

@@LISTEN@@ {
	root * @@DOCROOT@@

	php_server {
		file_server off

		worker {
			file @@ENTRY@@
			num @@PROCS@@
			match *
@@ENV@@
		}
	}
}
```

Replace the content of `servers/frankenphp/classic.Caddyfile.tpl`:

```caddyfile
# FrankenPHP classic shape. box/servers/frankenphp.sh renders the placeholders.
# php_server runs the index file on a thread of the pool for each request, as php-fpm does. The
# file server is off and try_files names only the index file, so a request stats one path.
# https://frankenphp.dev/docs/performance/#try_files
# num_threads is the pool size: the same value as the php-fpm children and the rapira processes.
# grace_period 2s limits the stop after TERM to 2 s. No encode directive: the responses stay
# uncompressed.
{
	auto_https off
	admin off
	grace_period 2s

	frankenphp {
		num_threads @@THREADS@@
	}
}

@@LISTEN@@ {
	root * @@DOCROOT@@

	php_server {
		file_server off
		try_files {path} @@INDEX@@
	}
}
```

Replace the content of `servers/frankenphp/stock.Caddyfile.tpl`:

```caddyfile
# FrankenPHP stock worker shape. box/servers/frankenphp.sh renders the placeholders.
# The global worker block and a plain php_server are the shape of the FrankenPHP docs. php_server
# keeps its default file server and try_files, so every request stats the document root before
# the worker answers. This shape shows the cost of the default next to the worker shape, and the
# static targets use it for the asset hit.
# The worker file is the index file of the document root. index and try_files repeat the
# php_server default with that name, so a worker file that is not index.php keeps the default routing.
# num_threads is the worker num plus one, because FrankenPHP needs more threads than workers.
# grace_period 2s limits the stop after TERM to 2 s. No encode directive: the responses stay
# uncompressed.
{
	auto_https off
	admin off
	grace_period 2s

	frankenphp {
		num_threads @@THREADS@@
		worker {
			file @@DOCROOT@@/@@INDEX@@
			num @@PROCS@@
@@ENV@@
		}
	}
}

@@LISTEN@@ {
	root * @@DOCROOT@@

	php_server {
		index @@INDEX@@
		try_files {path} {path}/@@INDEX@@ @@INDEX@@
	}
}
```

Run: `python3 -m unittest tests.test_templates.TemplateTests.test_text_templates -v`
Expected: FAIL with `FAILED (failures=2)`. The failing subtests are `nginx in front of php-fpm` and `roadrunner grpc`, because Step 6 rewrites these two templates. The FrankenPHP subtests pass.

- [ ] **Step 6: Write the nginx, php-fpm, and RoadRunner templates**

Replace the content of `servers/nginx/rapira.conf.tpl`:

```nginx
# nginx in front of rapira for the nginx-rapira targets. box/servers/nginx-rapira.sh renders the
# placeholders. The main, events, and http settings are the same as in fpm.conf.tpl.
worker_processes @@PROCS@@;
worker_rlimit_nofile 65536;
pid run/nginx.pid;
error_log stderr warn;

events {
    worker_connections 65536;
}

http {
    access_log off;

    client_body_temp_path tmp/client_body;
    fastcgi_temp_path tmp/fastcgi;
    proxy_temp_path tmp/proxy;
    uwsgi_temp_path tmp/uwsgi;
    scgi_temp_path tmp/scgi;

    # The default of 1000 closes the client connections during a stage and fills TIME-WAIT.
    keepalive_requests 1000000;

    upstream rapira {
        server 127.0.0.1:8081;
        keepalive 256;
        keepalive_requests 1000000;
    }

    server {
        listen @@LISTEN@@ backlog=65535;
        server_name _;

        location / {
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host $http_host;
            proxy_pass http://rapira;
        }
    }
}
```

Replace the content of `servers/nginx/fpm.conf.tpl`:

```nginx
# nginx in front of php-fpm for the php-fpm targets. box/servers/php-fpm.sh renders the
# placeholders. The main, events, and http settings are the same as in rapira.conf.tpl.
worker_processes @@PROCS@@;
worker_rlimit_nofile 65536;
pid run/nginx.pid;
error_log stderr warn;

events {
    worker_connections 65536;
}

http {
    access_log off;

    client_body_temp_path tmp/client_body;
    fastcgi_temp_path tmp/fastcgi;
    proxy_temp_path tmp/proxy;
    uwsgi_temp_path tmp/uwsgi;
    scgi_temp_path tmp/scgi;

    # The default of 1000 closes the client connections during a stage and fills TIME-WAIT.
    keepalive_requests 1000000;

    server {
        listen @@LISTEN@@ backlog=65535;
        server_name _;
        root @@DOCROOT@@;

        location / {
            fastcgi_pass 127.0.0.1:9000;
            # The fastcgi_params are inline, because an include resolves against the nginx prefix.
            fastcgi_param SCRIPT_FILENAME  $document_root/@@INDEX@@;
            fastcgi_param SCRIPT_NAME      /@@INDEX@@;
            fastcgi_param QUERY_STRING     $query_string;
            fastcgi_param REQUEST_METHOD   $request_method;
            fastcgi_param CONTENT_TYPE     $content_type;
            fastcgi_param CONTENT_LENGTH   $content_length;
            fastcgi_param REQUEST_URI      $request_uri;
            fastcgi_param DOCUMENT_URI     $document_uri;
            fastcgi_param DOCUMENT_ROOT    $document_root;
            fastcgi_param SERVER_PROTOCOL  $server_protocol;
            fastcgi_param REQUEST_SCHEME   $scheme;
            fastcgi_param GATEWAY_INTERFACE CGI/1.1;
            fastcgi_param SERVER_SOFTWARE  nginx/$nginx_version;
            fastcgi_param REMOTE_ADDR      $remote_addr;
            fastcgi_param REMOTE_PORT      $remote_port;
            fastcgi_param SERVER_ADDR      $server_addr;
            fastcgi_param SERVER_PORT      $server_port;
            fastcgi_param SERVER_NAME      $server_name;
        }
    }
}
```

Replace the content of `servers/php-fpm/php-fpm.conf.tpl`:

```ini
; php-fpm pool of the php-fpm targets. box/servers/php-fpm.sh renders the placeholders.
; php-fpm loads servers/php.ini through -c.

[global]
error_log = /proc/self/fd/2
daemonize = no

[bench]
; One FastCGI connection per request without keep_conn: the classic deployment pays this cost,
; and the target measures it.
listen = 127.0.0.1:9000
; The default of 511 drops connections at a high fan-out. The kernel limits the value to
; net.core.somaxconn, which provisioning raises.
listen.backlog = 65535
pm = static
pm.max_children = @@PROCS@@
catch_workers_output = yes
```

Replace the content of `servers/roadrunner/grpc.rr.yaml.tpl`:

```yaml
# RoadRunner config of the RoadRunner gRPC target. box/servers/roadrunner.sh renders the placeholders.
# rr needs version "3". max_concurrent_streams 200 is the hyper default of the rapira gRPC listener.
# The PHP workers get PHPRC and GRPC_VENDOR from the environment of rr.
version: "3"
server:
  command: "php @@RIG@@/apps/grpc/php/rr-worker.php"
  relay: pipes
grpc:
  listen: "tcp://@@LISTEN@@"
  proto: ["@@RIG@@/apps/grpc/bench.proto"]
  max_concurrent_streams: 200
  pool:
    num_workers: @@PROCS@@
    max_jobs: 0
logs:
  level: error
  # The grpc and server channels log one ERROR line for each call that the client cancels. The
  # load tool cancels the open calls at the end of a stage, and these lines only grow the log.
  # https://docs.roadrunner.dev/docs/logging-and-observability/logger#channels
  channels:
    grpc:
      level: panic
    server:
      level: panic
```

Run: `python3 -m unittest tests.test_templates.TemplateTests.test_text_templates tests.test_templates.TemplateTests.test_nginx_templates_share_the_http_settings -v`
Expected: PASS, `Ran 2 tests` and `OK`.

- [ ] **Step 7: Write the expected bodies**

Run:

```bash
printf 'Hello from worker, you!\n' > apps/hello/expect.txt
cp apps/static/tiny.css apps/static/tiny.expect
wc -c apps/hello/expect.txt apps/static/tiny.expect
```

Expected:

```
 24 apps/hello/expect.txt
128 apps/static/tiny.expect
152 total
```

- [ ] **Step 8: Update the gRPC PHP entries**

Replace the content of `apps/grpc/php/autoload.php`:

```php
<?php
// Loads the protobuf runtime and the generated classes.
// GRPC_VENDOR is the directory where provisioning installs servers/roadrunner/composer.lock. Its
// google/protobuf package is the pure-PHP runtime. PHP uses ext-protobuf when the extension is loaded.
// The rapira worker defines no STDERR constant, so the error goes to php://stderr.

$vendor = getenv('GRPC_VENDOR') . '/autoload.php';
if (!is_file($vendor)) {
    file_put_contents('php://stderr', "apps/grpc/php/autoload.php: $vendor is missing; provision the gRPC targets\n");
    exit(1);
}
require $vendor;

// PSR-4 loader for the generated classes: the Bench\ and GPBMetadata\ prefixes map to gen/.
spl_autoload_register(static function (string $class): void {
    $file = __DIR__ . '/gen/' . str_replace('\\', '/', $class) . '.php';
    if ((str_starts_with($class, 'Bench\\') || str_starts_with($class, 'GPBMetadata\\')) && is_file($file)) {
        require $file;
    }
});
```

Replace the content of `apps/grpc/php/dispatcher.php`:

```php
<?php
// gRPC echo workload, dispatcher mode: one call at a time on a blocking
// receive(). The reply is byte-identical to the other gRPC servers.

use Bench\V1\EchoRequest;
use Bench\V1\EchoResponse;
use Rapira\Exception\ClosedException;
use Rapira\Exception\RapiraThrowable;

require __DIR__ . '/autoload.php';

$d = \Rapira\get_dispatcher();

while (true) {
    try {
        $call = $d->receive();
        $req = new EchoRequest();
        $req->mergeFromString($call->getMessage());
        $out = new EchoResponse();
        $out->setText('Hello from worker, ' . $req->getText() . '!');
        $call->respond($out->serializeToString());
    } catch (ClosedException) {
        // Drained: no more work will arrive.
        break;
    } catch (RapiraThrowable) {
        // The host already closed the call.
    }
}
```

Replace the content of `apps/grpc/php/rr-worker.php`:

```php
<?php
// gRPC echo workload for the RoadRunner gRPC plugin. The reply is
// byte-identical to dispatcher.php.

use Bench\V1\EchoRequest;
use Bench\V1\EchoResponse;
use Bench\V1\EchoServiceInterface;
use Spiral\RoadRunner\GRPC\ContextInterface;
use Spiral\RoadRunner\GRPC\Server;
use Spiral\RoadRunner\Worker;

require __DIR__ . '/autoload.php';

final class EchoService implements EchoServiceInterface
{
    public function Echo(ContextInterface $ctx, EchoRequest $in): EchoResponse
    {
        $out = new EchoResponse();
        $out->setText('Hello from worker, ' . $in->getText() . '!');
        return $out;
    }
}

$server = new Server();
$server->registerService(EchoServiceInterface::class, new EchoService());
$server->serve(Worker::create());
```

Run: `for f in apps/grpc/php/autoload.php apps/grpc/php/dispatcher.php apps/grpc/php/rr-worker.php; do php -l "$f"; done`
Expected:

```
No syntax errors detected in apps/grpc/php/autoload.php
No syntax errors detected in apps/grpc/php/dispatcher.php
No syntax errors detected in apps/grpc/php/rr-worker.php
```

- [ ] **Step 9: Update the comments of the moved PHP files**

In `apps/hello/frankenphp.php` replace:

```php
// FrankenPHP hello worker: same handler body and wire output as the rapira
// workloads. Named index.php so the server routes / to it.
```

with:

```php
// FrankenPHP hello worker: same handler body and wire output as the rapira
// workloads.
```

In `apps/hello/fpm.php` replace:

```php
// fpm hello: wire output mirrors the other legs.
```

with:

```php
// php-fpm hello: the wire output is the same as on the other targets.
```

and replace:

```php
// Explicit Content-Length: PHP/nginx would otherwise chunk the response and
// the wire framing would differ from the other legs.
```

with:

```php
// Explicit Content-Length: PHP and nginx would otherwise chunk the response, and
// the wire framing would differ from the other targets.
```

In `apps/symfony/BenchController.php` replace:

```php
// Bench route: same body as the hello workload, so every leg and the k6
// checks share one contract. The skeleton's attribute scan of
```

with:

```php
// Bench route: same body as the hello workload, so every target shares one
// expected body. The skeleton's attribute scan of
```

and replace:

```php
// response, changing the wire framing against the other legs.
```

with:

```php
// response, changing the wire framing against the other targets.
```

In `apps/symfony/index.php` replace:

```php
// the classic legs execute one identical file. Same per-request shape as
```

with:

```php
// the classic targets execute one identical file. Same per-request shape as
```

In `apps/laravel/BenchController.php` replace:

```php
// Bench route: same body as the hello workload, so every leg and the k6
// checks share one contract. Content-Length is explicit because the
```

with:

```php
// Bench route: same body as the hello workload, so every target shares one
// expected body. Content-Length is explicit because the
```

and replace:

```php
// the wire framing against the other legs.
```

with:

```php
// the wire framing against the other targets.
```

In `apps/laravel/bench/worker-rapira.php` replace:

```php
// per-request state resets run here as on the FrankenPHP leg; only the loop
```

with:

```php
// per-request state resets run here as on the FrankenPHP target; only the loop
```

Run: `grep -rnw -i 'legs\?' apps || echo no leg word`
Expected: `no leg word`

- [ ] **Step 10: Update the gRPC fixture paths**

In `apps/grpc/fixtures.py` replace:

```python
"""Write the gRPC request bodies and the expected responses to grpc/."""
```

with:

```python
"""Write the gRPC request bodies and the expected responses to apps/grpc/."""
```

replace:

```python
GRPC_DIR = Path(__file__).resolve().parent.parent / "grpc"
```

with:

```python
GRPC_DIR = Path(__file__).resolve().parent
```

and delete this line:

```python
        "expect.http": (b"Hello from worker, you!\n", 24),
```

In `apps/grpc/buf.gen.yaml` replace both lines `    out: php/grpc/gen` with `    out: apps/grpc/php/gen`. The file is then:

```yaml
# `make grpc_fixtures` uses this template. The out paths resolve against the directory where buf runs: the repository root.
version: v2
plugins:
  - remote: buf.build/protocolbuffers/php:v36.2
    out: apps/grpc/php/gen
  - remote: buf.build/community/roadrunner-server-php-grpc:v5.3.0
    out: apps/grpc/php/gen
```

In `Makefile` replace the three recipe lines of `grpc_fixtures` (lines 121 to 123, each starts with a tab):

```makefile
	$(BUF) build grpc --as-file-descriptor-set -o grpc/bench.binpb
	$(BUF) generate grpc --template grpc/buf.gen.yaml
	python3 scripts/grpc-fixtures.py
```

with:

```makefile
	$(BUF) build apps/grpc --as-file-descriptor-set -o apps/grpc/bench.binpb
	$(BUF) generate apps/grpc --template apps/grpc/buf.gen.yaml
	python3 apps/grpc/fixtures.py
```

Run: `git add apps && python3 apps/grpc/fixtures.py && git diff --exit-code apps/grpc && echo fixtures unchanged`
Expected: `fixtures unchanged`. The script writes the same bytes to the moved fixture files.

- [ ] **Step 11: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_templates -v`
Expected: PASS, `Ran 5 tests` and `OK`.

- [ ] **Step 12: Verify the PHP syntax and the target word**

Run:

```bash
for f in apps/hello/*.php apps/grpc/php/*.php apps/symfony/*.php apps/symfony/bench/*.php apps/laravel/*.php apps/laravel/bench/*.php; do php -l "$f" >/dev/null || echo "syntax error: $f"; done
grep -rnw -i 'legs\?' apps servers || echo no leg word
```

Expected:

```
no leg word
```

- [ ] **Step 13: Commit**

```bash
git add apps servers tests/test_templates.py Makefile
git commit -s -S -m "feat: move the workloads to apps/ and the server configs to servers/"
```

### Task 9: Box core and the rapira server script

**Files:**
- Create: `box/lib.sh`
- Create: `box/target.sh`
- Create: `box/servers/rapira.sh`
- Create: `box/probe.sh`
- Create: `tests/box.Dockerfile`
- Delete: `scripts/box-lib.sh`
- Delete: `scripts/leg.sh`
- Delete: `scripts/test_nginx.py`
- Test: `tests/test_box.py`

**Interfaces:**
- Consumes: the templates, `servers/php.ini`, and the apps of Task 8: `servers/rapira/http.toml.tpl`, `servers/rapira/static.toml.tpl`, `servers/rapira/grpc.toml.tpl` with the placeholders `@@LISTEN@@`, `@@ENTRY@@`, `@@MODE@@`, `@@PROCS@@`, `@@ROOT@@`, `@@RIG@@`; `apps/hello/*.php`, `apps/hello/expect.txt`, `apps/grpc/php/dispatcher.php`, `apps/grpc/echo.json`.
- Consumes: the box protocol of the contract: `box/target.sh start TAG SERVER PROCS BINARY_DIR ARGS...`, `stop|probe|mem|log TAG SERVER`, and `box/probe.sh URL EXPECT_FILE PROTO [METHOD] [BODY_FILE] [HEADER...]`.
- Produces: `box/lib.sh` with `BENCH=/opt/bench`, `RIG=$HOME/bench-rig`, `PORT` (default 8080), `LISTEN_HOST` (default empty), the exported `PHPRC=/opt/bench/php.ini` and `GRPC_VENDOR=/opt/bench/apps/roadrunner-grpc/vendor`, and the functions `die`, `expand_path TEXT`, `rig_path PATH`, `render TEMPLATE OUTPUT NAME=VALUE...`, `port_busy`, `ensure_port_free`, `wait_port_free SECONDS`, `pids_of TAG`, `log_bytes TAG`, `pss_kb TAG`, `fail TAG MESSAGE`, `launch NAME TAG BIN ARGS...` (sets `pid`), `wait_listener TAG BIN`, `wait_answer TAG`, `wait_grpc_answer TAG`, `verify_children TAG PARENT COUNT WHAT [PATTERN]`, `stop_pid NAME TAG SIGNAL SECONDS`.
- Produces: the server script protocol that Task 10 follows: `box/servers/SERVER.sh start TAG PROCS BINARY_DIR ARGS...` prints `pid=<pid of the :8080 listener>` on stdout and nothing else; `box/servers/SERVER.sh stop TAG`. On the box a process of a target has `$BENCH/run/TAG.NAME.pid` and `$BENCH/log/TAG.NAME.log`, and a rendered config is `$BENCH/run/TAG.<ext>`.
- Produces: `box/target.sh start` prints the `pid=` line and then one `config=<path>` line per rendered config file (two lines for php-fpm and nginx-rapira). An error is one or more lines on stderr that start with `ERROR: `. `box/target.sh probe` prints the worker pids of the target (the children of each pid file), sorted and space-separated, then the total log bytes. FrankenPHP runs its threads in one process, so its worker pid line is empty.
- Produces: the rapira executable path `BINARY_DIR/bin/rapira`. The nightly tarball has the top directory `<stem>/` with `bin/rapira` and `lib/rapira/`, so Task 12 unpacks it with `tar --strip-components=1` into `/opt/bench/rapira/<sha7>`, and a server build installs the binary at `/opt/bench/rapira/<name>/bin/rapira`.
- Produces: `rapira.sh` renders `@@ROOT@@` as `$RIG/apps/static`. A relative `CONFIG_TPL` is a path under the staged rig, for example `servers/rapira/static.toml.tpl`.
- Produces: the container test image `tests/box.Dockerfile` and the run command `docker build -t rapira-bench-box -f tests/box.Dockerfile tests && docker run --rm --init -v "$PWD:/repo:ro" rapira-bench-box`.

- [ ] **Step 1: Write the failing test**

Create `tests/box.Dockerfile`:

```dockerfile
# Disposable image of tests/test_box.py. The test runs the box scripts as the user fedora, as on the
# rig boxes. It uses /opt/bench and the ports 8080, 8081, and 9000.
FROM fedora:44
RUN dnf -y install --setopt=install_weak_deps=False python3 curl iproute procps-ng diffutils \
    && dnf clean all
RUN useradd -m fedora && install -d -o fedora -g fedora /opt/bench
USER fedora
ENV HOME=/home/fedora BOX_TEST=1
WORKDIR /repo
CMD ["python3", "-m", "unittest", "tests.test_box", "-v"]
```

Create `tests/test_box.py`:

```python
"""Tests of the box scripts: start, probe, and stop each server kind, and the byte-exact probe.

The lifecycle tests use /opt/bench and the ports 8080, 8081, and 9000, so they run only in the
disposable image. From the repository root:

    docker build -t rapira-bench-box -f tests/box.Dockerfile tests
    docker run --rm --init -v "$PWD:/repo:ro" rapira-bench-box

Add -e RAPIRA_ASSET_URL=<URL of a nightly tarball> to run the rapira cases with the real binary.
The probe tests run on every host with bash, curl, and cmp.
"""

import os
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BENCH = Path("/opt/bench")
RIG = Path.home() / "bench-rig"
PROCS = 2
TAG = "t1"
ASSET_URL = os.environ.get("RAPIRA_ASSET_URL", "")
ASSET_DIR = Path("/tmp/rapira-asset")
STARTED = BENCH / "run/stub-started"
HELLO = {"path": "/?name=you", "expect": "apps/hello/expect.txt"}

# The stub of "rapira serve CONFIG" and "rr serve -c CONFIG". A copy of python3 named rapira or rr
# runs this file from its working directory, $BENCH/run, so /proc/<pid>/exe is the copy.
STUB = r'''
import os
import re
import signal
import sys
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

if "-c" in sys.argv:
    text = Path(sys.argv[sys.argv.index("-c") + 1]).read_text()
    listen = re.search(r'listen: "tcp://(.*)"', text).group(1)
    processes = int(re.search(r"num_workers: (\d+)", text).group(1))
else:
    config = tomllib.loads(Path(sys.argv[-1]).read_text())
    section = config["http"] if "http" in config else config["grpc"]
    listen = section["listen"]
    processes = section["pool"]["processes"]
if os.environ.get("BOX_STUB_SHORT") == "1":
    processes -= 1
host, port = listen.rsplit(":", 1)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        name = parse_qs(urlsplit(self.path).query).get("name", ["anonymous"])[0]
        body = f"Hello from worker, {name}!\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


server = HTTPServer((host or "0.0.0.0", int(port)), Handler)
Path("/opt/bench/run/stub-started").touch()
if ready := os.environ.get("BOX_STUB_READY"):
    with open(ready) as channel:
        channel.read(1)
children = []


def stop_child(_signum, _frame):
    raise SystemExit(0)


def stop_master(_signum, _frame):
    for child in children:
        try:
            os.kill(child, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for child in children:
        try:
            os.waitpid(child, 0)
        except ChildProcessError:
            pass
    raise SystemExit(0)


for _ in range(processes):
    pid = os.fork()
    if pid == 0:
        signal.signal(signal.SIGINT, stop_child)
        signal.signal(signal.SIGTERM, stop_child)
        server.serve_forever()
    children.append(pid)

signal.signal(signal.SIGINT, stop_master)
signal.signal(signal.SIGTERM, stop_master)
while True:
    signal.pause()
'''

LIFECYCLE_CASES = [
    {
        "name": "rapira worker",
        "server": "rapira",
        "binary": "pr",
        "args": ["worker", "@RIG@/apps/hello/worker.php"],
        "configs": {"toml": ['listen = ":8080"', f'entrypoint = "{RIG}/apps/hello/worker.php"', 'mode = "worker"', "processes = 2"]},
        "probe": HELLO,
        # The rapira master forks PROCS workers.
        "workers": PROCS,
        "stub_only": False,
    },
    {
        "name": "rapira classic",
        "server": "rapira",
        "binary": "pr",
        "args": ["classic", "@RIG@/apps/hello/classic.php"],
        "configs": {"toml": ['mode = "classic"', f'entrypoint = "{RIG}/apps/hello/classic.php"']},
        "probe": HELLO,
        "workers": PROCS,
        "stub_only": False,
    },
    {
        "name": "rapira dispatcher",
        "server": "rapira",
        "binary": "pr",
        "args": ["dispatcher", "@RIG@/apps/hello/dispatcher.php"],
        "configs": {"toml": ['mode = "dispatcher"', f'entrypoint = "{RIG}/apps/hello/dispatcher.php"']},
        "probe": HELLO,
        "workers": PROCS,
        "stub_only": False,
    },
    {
        "name": "rapira worker on the base binary",
        "server": "rapira",
        "binary": "base",
        "args": ["worker", "@RIG@/apps/hello/worker.php"],
        "configs": {"toml": ['mode = "worker"']},
        "probe": HELLO,
        "workers": PROCS,
        "stub_only": False,
    },
    {
        "name": "rapira static miss goes to the worker",
        "server": "rapira",
        "binary": "pr",
        "args": ["worker", "@RIG@/apps/hello/worker.php", "servers/rapira/static.toml.tpl"],
        "configs": {"toml": ['middleware = ["static"]', f'root = "{RIG}/apps/static"', 'mode = "worker"']},
        "probe": HELLO,
        "workers": PROCS,
        "stub_only": False,
    },
    {
        "name": "rapira grpc",
        "server": "rapira",
        "binary": "pr",
        "args": ["grpc", "@RIG@/apps/grpc/php/dispatcher.php"],
        "configs": {"toml": ["[grpc]", f'descriptor_set = "{RIG}/apps/grpc/bench.binpb"', f'entrypoint = "{RIG}/apps/grpc/php/dispatcher.php"']},
        "probe": None,
        "workers": PROCS,
        # The real gRPC dispatcher needs the protobuf runtime that provisioning installs.
        "stub_only": True,
    },
]

FAIL_CASES = [
    {
        "name": "rapira worker count differs",
        "server": "rapira",
        "binary": "pr",
        "args": ["worker", "@RIG@/apps/hello/worker.php"],
        "env": {"BOX_STUB_SHORT": "1"},
        "hold_before": None,
        "hold_after_start": None,
        "edit": None,
        # The stub forks PROCS - 1 workers.
        "error": "rapira has 1 workers, expected 2",
        "stub_started": True,
        "stub_only": True,
    },
    {
        "name": "rapira target port busy",
        "server": "rapira",
        "binary": "pr",
        "args": ["worker", "@RIG@/apps/hello/worker.php"],
        "env": {},
        "hold_before": 8080,
        "hold_after_start": None,
        "edit": None,
        "error": ":8080 is busy",
        "stub_started": False,
        "stub_only": False,
    },
    {
        "name": "rapira unknown mode",
        "server": "rapira",
        "binary": "pr",
        "args": ["fast", "@RIG@/apps/hello/worker.php"],
        "env": {},
        "hold_before": None,
        "hold_after_start": None,
        "edit": None,
        "error": "unknown rapira mode fast",
        "stub_started": False,
        "stub_only": False,
    },
]


def listening(port):
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def hold(port):
    busy = socket.socket()
    busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    busy.bind(("0.0.0.0", port))
    busy.listen()
    return busy


class BoxLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("BOX_TEST") != "1":
            raise unittest.SkipTest("run this test in the image of tests/box.Dockerfile")
        if os.geteuid() == 0:
            raise RuntimeError("run this test as a user other than root, as on the rig boxes")
        if ASSET_URL and not ASSET_DIR.exists():
            ASSET_DIR.mkdir(parents=True)
            archive, _ = urllib.request.urlretrieve(ASSET_URL)
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():
                    # Drop the top directory of the tarball, as provisioning does.
                    member.name = member.name.partition("/")[2]
                    if member.name:
                        tar.extract(member, ASSET_DIR, filter="tar")

    def setUp(self):
        self.addCleanup(self.force_cleanup)

    def reset(self):
        # Each case starts from an empty /opt/bench and a fresh staged rig, as on a provisioned box.
        self.force_cleanup()
        for path in BENCH.iterdir():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        shutil.rmtree(RIG, ignore_errors=True)
        for directory in ("box", "servers", "apps/hello", "apps/static", "apps/grpc"):
            shutil.copytree(REPO / directory, RIG / directory)
        for directory in ("run", "log", "bin", "rapira"):
            (BENCH / directory).mkdir()
        shutil.copy2(REPO / "servers/php.ini", BENCH / "php.ini")
        (BENCH / "run/serve").write_text(STUB)
        for binary in ("pr", "base"):
            if ASSET_URL:
                (BENCH / "rapira" / binary).symlink_to(ASSET_DIR)
            else:
                (BENCH / "rapira" / binary / "bin").mkdir(parents=True)
                shutil.copy2(os.path.realpath("/usr/bin/python3"), BENCH / "rapira" / binary / "bin/rapira")

    def force_cleanup(self):
        subprocess.run(["pkill", "-KILL", "-u", str(os.getuid()), "-f", "opt/bench|php-fpm|nginx"], check=False)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and self.leftovers():
            time.sleep(0.05)

    def leftovers(self):
        return subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", "opt/bench|php-fpm|nginx"], capture_output=True, text=True).stdout.split()

    def target(self, *arguments, env=None):
        return subprocess.run(
            [str(RIG / "box/target.sh"), *map(str, arguments)],
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            timeout=60,
        )

    def start_arguments(self, case):
        binary = BENCH / "rapira" / case["binary"] if case["binary"] else "-"
        return ["start", TAG, case["server"], PROCS, binary, *case["args"]]

    def assert_success(self, result):
        self.assertEqual(0, result.returncode, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")

    def assert_stopped(self, pids):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(Path(f"/proc/{pid}").exists() for pid in pids):
            time.sleep(0.05)
        self.assertEqual([], [pid for pid in pids if Path(f"/proc/{pid}").exists()])
        self.assertFalse(listening(8080))
        self.assertFalse(listening(8081))
        self.assertEqual([], sorted(path.name for path in (BENCH / "run").glob(f"{TAG}.*.pid")))

    def test_lifecycle(self):
        for case in LIFECYCLE_CASES:
            with self.subTest(name=case["name"]):
                if ASSET_URL and case["stub_only"]:
                    self.skipTest("the case needs the stub")
                self.reset()
                started = self.target(*self.start_arguments(case))
                self.assert_success(started)
                lines = started.stdout.splitlines()
                pid = int(next(line for line in lines if line.startswith("pid="))[4:])
                configs = sorted(line[7:] for line in lines if line.startswith("config="))
                self.assertEqual(sorted(str(BENCH / f"run/{TAG}.{suffix}") for suffix in case["configs"]), configs)
                for suffix, parts in case["configs"].items():
                    text = (BENCH / f"run/{TAG}.{suffix}").read_text()
                    for part in parts:
                        self.assertIn(part, text)
                ss = subprocess.run(["ss", "-Hltnp", "sport = :8080"], capture_output=True, text=True).stdout
                self.assertIn(f"pid={pid},", ss)
                if case["probe"]:
                    probe = subprocess.run(
                        [str(RIG / "box/probe.sh"), "http://127.0.0.1:8080" + case["probe"]["path"], case["probe"]["expect"], "http1"],
                        capture_output=True,
                        text=True,
                    )
                    self.assert_success(probe)
                masters = [int(path.read_text()) for path in (BENCH / "run").glob(f"{TAG}.*.pid")]
                self.assertIn(pid, masters)
                inspected = self.target("probe", TAG, case["server"])
                self.assert_success(inspected)
                workers = [int(value) for value in inspected.stdout.splitlines()[0].split()]
                self.assertEqual(case["workers"], len(workers))
                log_bytes = sum(path.stat().st_size for path in (BENCH / "log").glob(f"{TAG}.*.log"))
                self.assertEqual(log_bytes, int(inspected.stdout.splitlines()[1]))
                memory = self.target("mem", TAG, case["server"])
                self.assert_success(memory)
                self.assertGreater(int(memory.stdout), 0)
                self.assert_success(self.target("stop", TAG, case["server"]))
                self.assert_stopped(masters + workers)

    def test_failed_start_leaves_nothing(self):
        for case in FAIL_CASES:
            with self.subTest(name=case["name"]):
                if ASSET_URL and case["stub_only"]:
                    self.skipTest("the case needs the stub")
                self.reset()
                env = dict(case["env"])
                if case["edit"]:
                    (RIG / case["edit"][0]).write_text(case["edit"][1])
                if case["hold_before"]:
                    with hold(case["hold_before"]):
                        result = self.target(*self.start_arguments(case), env=env)
                elif case["hold_after_start"]:
                    ready = BENCH / "run/stub-ready"
                    os.mkfifo(ready)
                    env["BOX_STUB_READY"] = str(ready)
                    process = subprocess.Popen(
                        [str(RIG / "box/target.sh"), *map(str, self.start_arguments(case))],
                        env={**os.environ, **env},
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline and not STARTED.exists():
                        time.sleep(0.01)
                    with hold(case["hold_after_start"]):
                        # Opening the FIFO for writing blocks until the stub opens it for reading.
                        if STARTED.exists():
                            ready.write_text("1")
                        stdout, stderr = process.communicate(timeout=60)
                    result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
                else:
                    result = self.target(*self.start_arguments(case), env=env)
                self.assertNotEqual(0, result.returncode, result.stdout)
                self.assertIn(case["error"], result.stderr)
                self.assertEqual(case["stub_started"], STARTED.exists())
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and self.leftovers():
                    time.sleep(0.05)
                self.assertEqual([], self.leftovers())
                self.assert_stopped([])


class ReplyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def reply(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.seen = {"method": self.command, "body": self.rfile.read(length), "headers": {name.lower(): value for name, value in self.headers.items()}}
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.server.reply)))
        self.end_headers()
        self.wfile.write(self.server.reply)

    do_GET = reply
    do_POST = reply

    def log_message(self, _format, *_args):
        pass


PROBE_CASES = [
    {
        "name": "exact body",
        "reply": b"Hello from worker, you!\n",
        "args": [],
        "status": 0,
        "stderr": "",
        "method": "GET",
        "body": b"",
        "headers": {},
    },
    {
        "name": "one byte differs",
        "reply": b"Hello from worker, You!\n",
        "args": [],
        "status": 1,
        # The first 19 bytes are "Hello from worker, ", so byte 20 is the first difference.
        "stderr": " 20, line 1",
        "method": "GET",
        "body": b"",
        "headers": {},
    },
    {
        "name": "short body",
        "reply": b"Hello from worker, ",
        "args": [],
        "status": 1,
        "stderr": "after byte 19",
        "method": "GET",
        "body": b"",
        "headers": {},
    },
    {
        "name": "no answer",
        "reply": None,
        "args": [],
        "status": 1,
        "stderr": "curl failed",
        "method": None,
        "body": None,
        "headers": {},
    },
    {
        "name": "request shape of a Connect JSON target",
        "reply": b"Hello from worker, you!\n",
        "args": ["POST", "apps/grpc/echo.json", "content-type: application/json", "connect-protocol-version: 1"],
        "status": 0,
        "stderr": "",
        "method": "POST",
        # apps/grpc/fixtures.py writes this request body.
        "body": b'{"text":"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ01"}',
        "headers": {"content-type": "application/json", "connect-protocol-version": "1"},
    },
]


class ProbeTests(unittest.TestCase):
    def setUp(self):
        # probe.sh resolves a relative file under $HOME/bench-rig.
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home)
        os.symlink(REPO, Path(home) / "bench-rig")
        self.env = {**os.environ, "HOME": home}

    def probe(self, url, *arguments):
        return subprocess.run(
            [str(REPO / "box/probe.sh"), url, "apps/hello/expect.txt", "http1", *arguments],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_probe(self):
        for case in PROBE_CASES:
            with self.subTest(name=case["name"]):
                server = HTTPServer(("127.0.0.1", 0), ReplyHandler)
                # handle_request returns after 5 s without a request.
                server.timeout = 5
                server.reply = case["reply"]
                server.seen = None
                port = server.server_address[1]
                if case["reply"] is None:
                    server.server_close()
                else:
                    thread = threading.Thread(target=server.handle_request, daemon=True)
                    thread.start()
                result = self.probe(f"http://127.0.0.1:{port}/?name=you", *case["args"])
                if case["reply"] is not None:
                    thread.join(timeout=5)
                    server.server_close()
                self.assertEqual(case["status"], result.returncode, result.stderr)
                self.assertIn(case["stderr"], result.stderr)
                if case["method"]:
                    self.assertEqual(case["method"], server.seen["method"])
                    self.assertEqual(case["body"], server.seen["body"])
                    for name, value in case["headers"].items():
                        self.assertEqual(value, server.seen["headers"][name])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_box -v`
Expected: FAIL with `FAILED (errors=5, skipped=1)`. Each `test_probe` case reports `FileNotFoundError: [Errno 2] No such file or directory: '<repo>/box/probe.sh'`, and `BoxLifecycleTests` is skipped with `run this test in the image of tests/box.Dockerfile`.

- [ ] **Step 3: Write the shared helpers**

Create `box/lib.sh`:

```bash
#!/usr/bin/env bash
# Shared helpers of the box scripts. The scripts source this file.

BENCH=/opt/bench
RIG=$HOME/bench-rig
# The one place of the target port on the boxes. nginx-rapira.sh sets PORT for its rapira backend.
PORT=${PORT:-8080}
LISTEN_HOST=${LISTEN_HOST:-}
# Every PHP runtime loads the shared php.ini. The gRPC entries load the protobuf runtime from GRPC_VENDOR.
export PHPRC=$BENCH/php.ini
export GRPC_VENDOR=$BENCH/apps/roadrunner-grpc/vendor

die() {
  echo "ERROR: $*" >&2
  exit 1
}

# expand_path TEXT replaces the @RIG@ and @APPS@ tokens of the target registry.
expand_path() {
  local text=$1
  text=${text//@RIG@/$RIG}
  text=${text//@APPS@/$BENCH/apps}
  printf '%s\n' "$text"
}

# rig_path PATH expands the tokens of PATH and makes a relative PATH absolute under the staged rig.
rig_path() {
  local path
  path=$(expand_path "$1")
  case "$path" in
  /*) printf '%s\n' "$path" ;;
  *) printf '%s\n' "$RIG/$path" ;;
  esac
}

# render TEMPLATE OUTPUT NAME=VALUE... writes TEMPLATE to OUTPUT with each @@NAME@@ replaced by
# VALUE. It fails when a placeholder stays in the output.
render() {
  python3 - "$@" <<'PY'
import sys

template, output = sys.argv[1], sys.argv[2]
with open(template) as source:
    text = source.read()
for pair in sys.argv[3:]:
    name, value = pair.split("=", 1)
    text = text.replace("@@" + name + "@@", value)
if "@@" in text:
    sys.exit(f"ERROR: {template} has a placeholder without a value")
with open(output, "w") as target:
    target.write(text)
PY
}

port_busy() {
  ss -HltnO "sport = :$PORT" | grep -q .
}

ensure_port_free() {
  if port_busy; then
    ss -Hltnp "sport = :$PORT" >&2 || true
    die ":$PORT is busy"
  fi
}

# wait_port_free SECONDS waits until nothing listens on :$PORT.
wait_port_free() {
  for _ in $(seq 1 $(($1 * 2))); do
    port_busy || return 0
    sleep 0.5
  done
  return 1
}

# pids_of TAG prints each pid of the pid files of TAG and the children of that pid, sorted.
pids_of() {
  local file pid
  for file in "$BENCH/run/$1".*.pid; do
    [ -f "$file" ] || continue
    pid=$(cat "$file")
    echo "$pid"
    pgrep -P "$pid" || true
  done | sort -n
}

# log_bytes TAG prints the total size of the logs of TAG.
log_bytes() {
  { cat "$BENCH/log/$1".*.log 2>/dev/null || true; } | wc -c
}

# pss_kb TAG prints the sum of the proportional set size in KiB of the processes of TAG.
# https://docs.kernel.org/filesystems/proc.html#process-specific-subdirectories
pss_kb() {
  local pid
  for pid in $(pids_of "$1"); do
    cat "/proc/$pid/smaps_rollup" 2>/dev/null || true
  done | awk '/^Pss:/ { kb += $2 } END { print kb + 0 }'
}

# fail TAG MESSAGE prints the error and the last log lines of TAG, kills the processes of TAG,
# removes its pid files, and exits 1.
fail() {
  local tag=$1 file
  shift
  echo "ERROR: $tag: $*" >&2
  for file in "$BENCH/log/$tag".*.log; do
    [ -f "$file" ] && tail -5 "$file" >&2
  done
  # shellcheck disable=SC2046
  kill -KILL $(pids_of "$tag") 2>/dev/null || true
  rm -f "$BENCH/run/$tag".*.pid
  exit 1
}

# launch NAME TAG BIN ARGS... starts BIN with ARGS in the background in $BENCH/run. It writes the
# pid to $BENCH/run/TAG.NAME.pid and the output to $BENCH/log/TAG.NAME.log, and sets pid.
launch() {
  local name=$1 tag=$2 bin=$3
  shift 3
  (
    ulimit -n 65536 2>/dev/null || true
    cd "$BENCH/run" || exit 1
    exec nohup "$bin" "$@"
  ) </dev/null >"$BENCH/log/$tag.$name.log" 2>&1 &
  pid=$!
  echo "$pid" >"$BENCH/run/$tag.$name.pid"
}

# wait_listener TAG BIN waits up to 30 s until the process of the last launch listens on :$PORT.
# It checks that the process runs BIN.
wait_listener() {
  local tag=$1 bin=$2 exe
  for _ in $(seq 1 60); do
    ss -HltnpO "sport = :$PORT" 2>/dev/null | grep -q "pid=$pid," && break
    kill -0 "$pid" 2>/dev/null || fail "$tag" "pid $pid exited before it listened on :$PORT"
    sleep 0.5
  done
  ss -HltnpO "sport = :$PORT" 2>/dev/null | grep -q "pid=$pid," || fail "$tag" "pid $pid does not listen on :$PORT"
  exe=$(readlink "/proc/$pid/exe")
  [ "$exe" = "$(readlink -f "$bin")" ] || fail "$tag" "pid $pid runs $exe, expected $bin"
}

# wait_answer TAG waits up to 30 s until 127.0.0.1:$PORT returns an HTTP/1.1 response.
wait_answer() {
  local tag=$1
  for _ in $(seq 1 60); do
    curl -s -o /dev/null -m 1 "http://127.0.0.1:$PORT/" && return 0
    sleep 0.5
  done
  fail "$tag" "no answer on :$PORT"
}

# wait_grpc_answer TAG waits up to 30 s until the gRPC Echo call on 127.0.0.1:$PORT returns the
# expected bytes. A gRPC error is HTTP 200 with an empty body, so only the reply bytes prove a worker.
wait_grpc_answer() {
  local tag=$1
  for _ in $(seq 1 60); do
    "$RIG/box/probe.sh" "http://127.0.0.1:$PORT/bench.v1.EchoService/Echo" apps/grpc/expect.grpc grpc POST apps/grpc/echo.grpc >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  fail "$tag" "no gRPC answer on :$PORT"
}

# verify_children TAG PARENT COUNT WHAT [PATTERN] waits up to 30 s until PARENT has COUNT children
# that match PATTERN, and fails the start when the count differs.
verify_children() {
  local tag=$1 parent=$2 want=$3 what=$4 pattern=${5:-} have=0
  for _ in $(seq 1 60); do
    if [ -n "$pattern" ]; then
      have=$(pgrep -c -P "$parent" -f "$pattern" || true)
    else
      have=$(pgrep -c -P "$parent" || true)
    fi
    [ "$have" -ge "$want" ] && break
    sleep 0.5
  done
  [ "$have" -eq "$want" ] || fail "$tag" "$what has $have workers, expected $want"
}

# stop_pid NAME TAG SIGNAL SECONDS sends SIGNAL to the process of $BENCH/run/TAG.NAME.pid. After
# SECONDS it kills the process and its children with KILL. It removes the pid file.
stop_pid() {
  local name=$1 tag=$2 signal=$3 seconds=$4 file pid tree
  file=$BENCH/run/$tag.$name.pid
  [ -f "$file" ] || return 0
  pid=$(cat "$file")
  tree="$pid $(pgrep -P "$pid" | tr '\n' ' ' || true)"
  kill "-$signal" "$pid" 2>/dev/null || true
  for _ in $(seq 1 $((seconds * 2))); do
    # shellcheck disable=SC2086
    kill -0 $tree 2>/dev/null || break
    sleep 0.5
  done
  # shellcheck disable=SC2086
  kill -KILL $tree 2>/dev/null || true
  for _ in $(seq 1 20); do
    # shellcheck disable=SC2086
    kill -0 $tree 2>/dev/null || break
    sleep 0.5
  done
  # shellcheck disable=SC2086
  if kill -0 $tree 2>/dev/null; then
    die "$tag: a process of $name is alive after KILL"
  fi
  rm -f "$file"
}
```

Run: `bash -n box/lib.sh && echo ok`
Expected: `ok`

- [ ] **Step 4: Write the dispatcher**

Create `box/target.sh`:

```bash
#!/usr/bin/env bash
# target.sh start TAG SERVER PROCS BINARY_DIR ARGS...
# target.sh stop|probe|mem|log TAG SERVER
# Starts, stops, and inspects one target. box/servers/SERVER.sh starts and stops the server kind.
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/lib.sh"

cmd=${1:?start|stop|probe|mem|log}
tag=${2:?tag}
server=${3:?server}
script=$(dirname "$0")/servers/$server.sh
[ -x "$script" ] || die "unknown server $server"

start() {
  local procs=${1:?processes} bindir=${2:?binary dir} arg file
  local args=()
  shift 2
  for arg in "$@"; do
    args+=("$(expand_path "$arg")")
  done
  "$script" start "$tag" "$procs" "$bindir" "${args[@]}"
  for file in "$BENCH/run/$tag".*; do
    case "$file" in
    *.pid) ;;
    *) echo "config=$file" ;;
    esac
  done
}

case "$cmd" in
start)
  shift 3
  start "$@"
  ;;
stop)
  "$script" stop "$tag"
  ;;
probe)
  # The worker pids: the children of each process of the target.
  for file in "$BENCH/run/$tag".*.pid; do
    [ -f "$file" ] || continue
    pgrep -P "$(cat "$file")" || true
  done | sort -n | tr '\n' ' ' | sed 's/ $//'
  echo
  log_bytes "$tag"
  ;;
mem)
  pss_kb "$tag"
  ;;
log)
  { cat "$BENCH/log/$tag".*.log 2>/dev/null || true; } | { grep -E 'WARN|ERROR' || true; }
  ;;
*)
  die "unknown command $cmd"
  ;;
esac
```

Run: `bash -n box/target.sh && echo ok`
Expected: `ok`

- [ ] **Step 5: Write the rapira server script**

Create `box/servers/rapira.sh`:

```bash
#!/usr/bin/env bash
# rapira.sh start TAG PROCS BINARY_DIR MODE ENTRY [CONFIG_TPL]
# rapira.sh start TAG PROCS BINARY_DIR grpc ENTRY
# rapira.sh stop TAG
# MODE is worker, classic, or dispatcher. CONFIG_TPL is servers/rapira/http.toml.tpl by default.
# PORT and LISTEN_HOST select the listen address.
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/../lib.sh"

start() {
  local tag=$1 procs=$2 bindir=$3 mode=$4 entry=$5 tpl=${6:-servers/rapira/http.toml.tpl}
  local bin=$bindir/bin/rapira toml=$BENCH/run/$tag.toml
  case "$mode" in
  worker | classic | dispatcher) ;;
  grpc) tpl=servers/rapira/grpc.toml.tpl ;;
  *) die "unknown rapira mode $mode" ;;
  esac
  tpl=$(rig_path "$tpl")
  [ -x "$bin" ] || die "$bin is missing; provision the server"
  [ -f "$entry" ] || die "$entry is missing"
  [ -f "$tpl" ] || die "$tpl is missing"
  ensure_port_free
  render "$tpl" "$toml" "LISTEN=$LISTEN_HOST:$PORT" "ENTRY=$entry" "MODE=$mode" "PROCS=$procs" "ROOT=$RIG/apps/static" "RIG=$RIG"
  launch rapira "$tag" "$bin" serve "$toml"
  wait_listener "$tag" "$bin"
  if [ "$mode" = grpc ]; then
    wait_grpc_answer "$tag"
  else
    wait_answer "$tag"
  fi
  verify_children "$tag" "$pid" "$procs" rapira
  echo "pid=$pid"
}

stop() {
  local tag=$1
  # rapira drains on INT.
  stop_pid rapira "$tag" INT 45
  wait_port_free 20 || die "$tag: :$PORT is busy after the stop"
}

case "${1:?start|stop}" in
start)
  shift
  start "$@"
  ;;
stop)
  stop "${2:?tag}"
  ;;
*)
  die "unknown command $1"
  ;;
esac
```

Run: `bash -n box/servers/rapira.sh && echo ok`
Expected: `ok`

- [ ] **Step 6: Write the probe**

Create `box/probe.sh`:

```bash
#!/usr/bin/env bash
# probe.sh URL EXPECT_FILE PROTO [METHOD] [BODY_FILE] [HEADER...]
# Fetches URL once and compares the body with EXPECT_FILE byte for byte. PROTO is http1 or grpc.
# BODY_FILE - sends no body. A HEADER is "name: value". A relative file is under the staged rig.
# Exits 0 on a match. Exits 1 on a mismatch and prints the first differing byte on stderr.
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/lib.sh"

url=${1:?url}
expect=$(rig_path "${2:?expect file}")
proto=${3:?http1|grpc}
shift 3
method=GET
body=-
if [ $# -gt 0 ]; then
  method=$1
  shift
fi
if [ $# -gt 0 ]; then
  body=$1
  shift
fi

opts=(-s -m 5)
case "$proto" in
http1) opts+=(--http1.1 -X "$method") ;;
grpc) opts+=(--http2-prior-knowledge -X POST -H 'content-type: application/grpc' -H 'te: trailers' -H 'grpc-accept-encoding: identity') ;;
*) die "unknown proto $proto" ;;
esac
if [ "$body" != - ]; then
  opts+=(--data-binary "@$(rig_path "$body")")
fi
for header in "$@"; do
  opts+=(-H "$header")
done

got=$(mktemp)
trap 'rm -f "$got"' EXIT
curl "${opts[@]}" -o "$got" "$url" || die "curl failed for $url"
cmp "$expect" "$got" >&2 || exit 1
```

Run: `chmod +x box/target.sh box/probe.sh box/servers/rapira.sh && python3 -m unittest tests.test_box.ProbeTests -v`
Expected: PASS, `Ran 1 test` and `OK`. The five probe cases pass on the host.

- [ ] **Step 7: Remove the scripts that this task replaces**

Run: `git rm -q scripts/box-lib.sh scripts/leg.sh scripts/test_nginx.py`
Expected: no output. The v0.8.x CLI path of `scripts/leg.sh` and its stop by process pattern are gone. `stop_pid` stops a target by its pid files only.

- [ ] **Step 8: Check the scripts**

Run: `for f in box/lib.sh box/target.sh box/probe.sh box/servers/rapira.sh; do bash -n "$f"; done && if command -v shellcheck >/dev/null; then shellcheck -x box/lib.sh box/target.sh box/probe.sh box/servers/rapira.sh; else docker run --rm -v "$PWD:/mnt" koalaman/shellcheck:stable -x box/lib.sh box/target.sh box/probe.sh box/servers/rapira.sh; fi && echo clean`
Expected: `clean`

- [ ] **Step 9: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_box -v`
Expected: PASS, `Ran 1 test` and `OK (skipped=1)`. The probe tests run on the host, and the lifecycle class is skipped.

Run: `docker build -t rapira-bench-box -f tests/box.Dockerfile tests && docker run --rm --init -v "$PWD:/repo:ro" rapira-bench-box`
Expected: PASS, `Ran 3 tests` and `OK`. The lifecycle cases run in the image with the stub binary.

- [ ] **Step 10: Commit**

```bash
git add box/lib.sh box/target.sh box/probe.sh box/servers/rapira.sh tests/test_box.py tests/box.Dockerfile
git commit -s -S -m "feat: add the box target scripts and the rapira server script"
```

### Task 10: The other server scripts

**Files:**
- Create: `box/servers/frankenphp.sh`
- Create: `box/servers/php-fpm.sh`
- Create: `box/servers/nginx-rapira.sh`
- Create: `box/servers/roadrunner.sh`
- Modify: `tests/test_box.py` (the ends of `LIFECYCLE_CASES` and `FAIL_CASES`, and `BoxLifecycleTests.reset`)
- Modify: `tests/box.Dockerfile:3-5`
- Delete: `scripts/fleet-leg.sh`
- Test: `tests/test_box.py`

**Interfaces:**
- Consumes: from Task 9, `box/lib.sh` (`BENCH`, `RIG`, `PORT`, `LISTEN_HOST`, `PHPRC`, `GRPC_VENDOR`, `die`, `render`, `ensure_port_free`, `wait_port_free`, `fail`, `launch`, `wait_listener`, `wait_answer`, `wait_grpc_answer`, `verify_children`, `stop_pid`), `box/servers/rapira.sh start|stop`, the server script protocol (`start TAG PROCS BINARY_DIR ARGS...` prints `pid=<listener pid>`; `stop TAG`), and `tests/test_box.py` with `LIFECYCLE_CASES`, `FAIL_CASES`, and `BoxLifecycleTests.reset`.
- Consumes: from Task 8, `servers/frankenphp/{worker,classic,stock}.Caddyfile.tpl`, `servers/nginx/rapira.conf.tpl`, `servers/nginx/fpm.conf.tpl`, `servers/php-fpm/php-fpm.conf.tpl`, `servers/roadrunner/grpc.rr.yaml.tpl`, `apps/hello/frankenphp.php`, `apps/hello/fpm.php`, `apps/static/tiny.expect`.
- Produces: the box protocol for `frankenphp` (`SHAPE ENTRY DOCROOT [KEY=VALUE...]`, a classic `ENTRY` is the index file in `DOCROOT`), `php-fpm` (`DOCROOT INDEX`), `nginx-rapira` (`MODE ENTRY` with `BINARY_DIR`), and `roadrunner` (`grpc`).
- Produces: the paths that Task 12 installs: `/opt/bench/bin/frankenphp` (the `frankenphp-linux-x86_64-gnu` asset), `/opt/bench/bin/rr`, and `/opt/bench/apps/roadrunner-grpc/vendor/autoload.php` (`composer install` of `servers/roadrunner/composer.lock`). php-fpm and nginx come from the Fedora packages on `PATH`. nginx uses the prefix `/opt/bench/nginx` with `tmp/` and `run/`, which the start scripts create.
- Produces: config files `$BENCH/run/TAG.Caddyfile`, `TAG.fpm.conf` and `TAG.nginx.conf` (php-fpm), `TAG.nginx.conf` and `TAG.toml` (nginx-rapira), `TAG.rr.yaml`.

- [ ] **Step 1: Write the failing test**

Change `tests/box.Dockerfile`. Replace:

```dockerfile
FROM fedora:44
RUN dnf -y install --setopt=install_weak_deps=False python3 curl iproute procps-ng diffutils \
    && dnf clean all
```

with:

```dockerfile
FROM fedora:44
ARG FRANKENPHP_VERSION=1.12.7
RUN dnf -y install --setopt=install_weak_deps=False python3 curl iproute procps-ng diffutils nginx php-fpm \
    && dnf clean all
RUN curl -fsSL -o /usr/local/bin/frankenphp \
    "https://github.com/php/frankenphp/releases/download/v${FRANKENPHP_VERSION}/frankenphp-linux-x86_64-gnu" \
    && chmod 0755 /usr/local/bin/frankenphp
```

In `tests/test_box.py`, the end of `LIFECYCLE_CASES` is:

```python
        # The real gRPC dispatcher needs the protobuf runtime that provisioning installs.
        "stub_only": True,
    },
]
```

Insert these cases before the closing `]`:

```python
    {
        "name": "nginx-rapira worker",
        "server": "nginx-rapira",
        "binary": "pr",
        "args": ["worker", "@RIG@/apps/hello/worker.php"],
        "configs": {"nginx.conf": ["worker_processes 2;", "listen 8080 backlog=65535;"], "toml": ['listen = "127.0.0.1:8081"', 'mode = "worker"']},
        "probe": HELLO,
        # PROCS nginx workers and PROCS rapira workers.
        "workers": 2 * PROCS,
        "stub_only": False,
    },
    {
        "name": "frankenphp worker",
        "server": "frankenphp",
        "binary": None,
        "args": ["worker", "@RIG@/apps/hello/frankenphp.php", "@RIG@/apps/hello"],
        # num_threads is PROCS plus one in the worker shape.
        "configs": {"Caddyfile": ["num_threads 3", "num 2", "file_server off", "match *", ":8080 {"]},
        "probe": HELLO,
        # FrankenPHP runs its threads in one process, so it has no worker processes.
        "workers": 0,
        "stub_only": False,
    },
    {
        "name": "frankenphp worker with env lines",
        "server": "frankenphp",
        "binary": None,
        "args": ["worker", "@RIG@/apps/hello/frankenphp.php", "@RIG@/apps/hello", "APP_DEBUG=false", "MAX_REQUESTS=100000000"],
        "configs": {"Caddyfile": ["\t\t\tenv APP_DEBUG false\n", "\t\t\tenv MAX_REQUESTS 100000000\n"]},
        "probe": HELLO,
        "workers": 0,
        "stub_only": False,
    },
    {
        "name": "frankenphp classic",
        "server": "frankenphp",
        "binary": None,
        "args": ["classic", "@RIG@/apps/hello/classic.php", "@RIG@/apps/hello"],
        # num_threads is PROCS in the classic shape.
        "configs": {"Caddyfile": ["num_threads 2", "try_files {path} classic.php"]},
        "probe": HELLO,
        "workers": 0,
        "stub_only": False,
    },
    {
        "name": "frankenphp stock serves the static asset",
        "server": "frankenphp",
        "binary": None,
        "args": ["stock", "@RIG@/apps/hello/frankenphp.php", "@RIG@/apps/static"],
        # The entry is outside the static docroot, so the start script serves a run copy of it.
        "configs": {"Caddyfile": ["num_threads 3", "index index.php", f"root * {BENCH}/run/{TAG}.docroot"]},
        "probe": {"path": "/tiny.css", "expect": "apps/static/tiny.expect"},
        "workers": 0,
        "stub_only": False,
    },
    {
        "name": "php-fpm hello",
        "server": "php-fpm",
        "binary": None,
        "args": ["@RIG@/apps/hello", "fpm.php"],
        "configs": {"nginx.conf": ["worker_processes 2;", f"root {RIG}/apps/hello;", "$document_root/fpm.php;"], "fpm.conf": ["pm.max_children = 2"]},
        "probe": HELLO,
        # PROCS nginx workers and PROCS php-fpm pool workers.
        "workers": 2 * PROCS,
        "stub_only": False,
    },
    {
        "name": "roadrunner grpc",
        "server": "roadrunner",
        "binary": None,
        "args": ["grpc"],
        "configs": {"rr.yaml": ['listen: "tcp://0.0.0.0:8080"', "num_workers: 2", f'command: "php {RIG}/apps/grpc/php/rr-worker.php"']},
        "probe": None,
        # rr starts PROCS PHP workers.
        "workers": PROCS,
        "stub_only": False,
    },
```

The end of `FAIL_CASES` is:

```python
        "error": "unknown rapira mode fast",
        "stub_started": False,
        "stub_only": False,
    },
]
```

Insert these cases before the closing `]`:

```python
    {
        "name": "nginx-rapira backend port busy",
        "server": "nginx-rapira",
        "binary": "pr",
        "args": ["worker", "@RIG@/apps/hello/worker.php"],
        "env": {},
        "hold_before": 8081,
        "hold_after_start": None,
        "edit": None,
        "error": ":8081 is busy",
        "stub_started": False,
        "stub_only": False,
    },
    {
        "name": "nginx-rapira invalid nginx config",
        "server": "nginx-rapira",
        "binary": "pr",
        "args": ["worker", "@RIG@/apps/hello/worker.php"],
        "env": {},
        "hold_before": None,
        "hold_after_start": None,
        "edit": ("servers/nginx/rapira.conf.tpl", "events {}\nhttp { invalid_directive; }\n"),
        "error": "the nginx configuration is invalid",
        "stub_started": False,
        "stub_only": False,
    },
    {
        "name": "nginx-rapira nginx cannot listen",
        "server": "nginx-rapira",
        "binary": "pr",
        "args": ["worker", "@RIG@/apps/hello/worker.php"],
        "env": {},
        "hold_before": None,
        "hold_after_start": 8080,
        "edit": None,
        "error": "exited before it listened on :8080",
        "stub_started": True,
        "stub_only": True,
    },
    {
        "name": "frankenphp thread count differs",
        "server": "frankenphp",
        "binary": None,
        "args": ["worker", "@RIG@/apps/hello/frankenphp.php", "@RIG@/apps/hello"],
        "env": {},
        "hold_before": None,
        "hold_after_start": None,
        "edit": ("servers/frankenphp/worker.Caddyfile.tpl", (REPO / "servers/frankenphp/worker.Caddyfile.tpl").read_text().replace("num_threads @@THREADS@@", "num_threads 7")),
        "error": "the log does not confirm num_threads 3",
        "stub_started": False,
        "stub_only": False,
    },
    {
        "name": "frankenphp classic entry outside the document root",
        "server": "frankenphp",
        "binary": None,
        "args": ["classic", "@RIG@/apps/hello/classic.php", "@RIG@/apps/static"],
        "env": {},
        "hold_before": None,
        "hold_after_start": None,
        "edit": None,
        "error": f"the classic entry {RIG}/apps/hello/classic.php is not in {RIG}/apps/static",
        "stub_started": False,
        "stub_only": False,
    },
```

In `BoxLifecycleTests.reset`, after the line:

```python
                shutil.copy2(os.path.realpath("/usr/bin/python3"), BENCH / "rapira" / binary / "bin/rapira")
```

insert, at the indentation of the `for` loop:

```python
        shutil.copy2(os.path.realpath("/usr/bin/python3"), BENCH / "bin/rr")
        (BENCH / "bin/frankenphp").symlink_to("/usr/local/bin/frankenphp")
        (BENCH / "apps/roadrunner-grpc/vendor").mkdir(parents=True)
        (BENCH / "apps/roadrunner-grpc/vendor/autoload.php").write_text("<?php\n")
```

The stub serves as `rr` too: it reads `listen` and `num_workers` from `rr serve -c FILE`. The FrankenPHP cases run the real binary of the image, and the php-fpm and nginx cases run the Fedora packages.

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker build -t rapira-bench-box -f tests/box.Dockerfile tests && docker run --rm --init -v "$PWD:/repo:ro" rapira-bench-box`
Expected: FAIL. The new `test_lifecycle` cases fail in `assert_success` with `ERROR: unknown server nginx-rapira`, `ERROR: unknown server frankenphp`, `ERROR: unknown server php-fpm`, and `ERROR: unknown server roadrunner` in stderr. The new `test_failed_start_leaves_nothing` cases fail in `assertIn(case["error"], result.stderr)` for the same reason. The rapira cases pass. The summary is `FAILED (failures=12)`.

- [ ] **Step 3: Write the FrankenPHP server script**

Create `box/servers/frankenphp.sh`:

```bash
#!/usr/bin/env bash
# frankenphp.sh start TAG PROCS - SHAPE ENTRY DOCROOT [KEY=VALUE...]
# frankenphp.sh stop TAG
# SHAPE is worker, classic, or stock. Each KEY=VALUE becomes an env line of the worker.
# A classic ENTRY is the index file in DOCROOT. A stock ENTRY outside DOCROOT goes into a run copy
# of DOCROOT as index.php, so the assets and the worker file share one document root.
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/../lib.sh"

start() {
  local tag=$1 procs=$2 shape=$4 entry=$5 docroot=$6 threads pair line env_lines=""
  local bin=$BENCH/bin/frankenphp file=$BENCH/run/$tag.Caddyfile
  shift 6
  [ -x "$bin" ] || die "$bin is missing; provision the server"
  [ -f "$entry" ] || die "$entry is missing"
  [ -d "$docroot" ] || die "$docroot is missing"
  case "$shape" in
  worker) threads=$((procs + 1)) ;;
  stock)
    threads=$((procs + 1))
    if [ "$(dirname "$entry")" != "$docroot" ]; then
      rm -rf "$BENCH/run/$tag.docroot"
      mkdir -p "$BENCH/run/$tag.docroot"
      cp -r "$docroot"/. "$BENCH/run/$tag.docroot/"
      cp "$entry" "$BENCH/run/$tag.docroot/index.php"
      docroot=$BENCH/run/$tag.docroot
      entry=$docroot/index.php
    fi
    ;;
  classic)
    threads=$procs
    [ "$(dirname "$entry")" = "$docroot" ] || die "the classic entry $entry is not in $docroot"
    ;;
  *) die "unknown frankenphp shape $shape" ;;
  esac
  for pair in "$@"; do
    case "$pair" in
    *=*) ;;
    *) die "the worker env $pair is not KEY=VALUE" ;;
    esac
    printf -v line '\t\t\tenv %s %s\n' "${pair%%=*}" "${pair#*=}"
    env_lines=$env_lines$line
  done
  ensure_port_free
  render "$RIG/servers/frankenphp/$shape.Caddyfile.tpl" "$file" "LISTEN=:$PORT" "THREADS=$threads" "PROCS=$procs" \
    "ENTRY=$entry" "DOCROOT=$docroot" "INDEX=$(basename "$entry")" "ENV=$env_lines"
  launch frankenphp "$tag" "$bin" run --config "$file"
  wait_listener "$tag" "$bin"
  wait_answer "$tag"
  # FrankenPHP runs its threads in one process. The startup log line gives the thread count.
  grep -q "\"num_threads\":$threads," "$BENCH/log/$tag.frankenphp.log" ||
    fail "$tag" "the log does not confirm num_threads $threads"
  echo "pid=$pid"
}

stop() {
  local tag=$1
  # grace_period in the Caddyfile limits the stop after TERM to 2 s.
  stop_pid frankenphp "$tag" TERM 10
  wait_port_free 20 || die "$tag: :$PORT is busy after the stop"
}

case "${1:?start|stop}" in
start)
  shift
  start "$@"
  ;;
stop)
  stop "${2:?tag}"
  ;;
*)
  die "unknown command $1"
  ;;
esac
```

Run: `bash -n box/servers/frankenphp.sh && echo ok`
Expected: `ok`

- [ ] **Step 4: Write the php-fpm server script**

Create `box/servers/php-fpm.sh`:

```bash
#!/usr/bin/env bash
# php-fpm.sh start TAG PROCS - DOCROOT INDEX
# php-fpm.sh stop TAG
# nginx listens on :$PORT and sends each request to INDEX in DOCROOT on the php-fpm pool.
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/../lib.sh"

FPM_PORT=9000

start() {
  local tag=$1 procs=$2 docroot=$4 index=$5 nginx fpm
  local fpm_conf=$BENCH/run/$tag.fpm.conf nginx_conf=$BENCH/run/$tag.nginx.conf
  [ -f "$docroot/$index" ] || die "$docroot/$index is missing"
  nginx=$(readlink -f "$(command -v nginx)")
  fpm=$(readlink -f "$(command -v php-fpm)")
  ensure_port_free
  PORT=$FPM_PORT ensure_port_free
  install -d "$BENCH/nginx/tmp" "$BENCH/nginx/run"
  render "$RIG/servers/php-fpm/php-fpm.conf.tpl" "$fpm_conf" "PROCS=$procs"
  render "$RIG/servers/nginx/fpm.conf.tpl" "$nginx_conf" "PROCS=$procs" "LISTEN=$PORT" "DOCROOT=$docroot" "INDEX=$index"
  "$nginx" -t -q -p "$BENCH/nginx" -e stderr -c "$nginx_conf" || die "$tag: the nginx configuration is invalid"
  launch fpm "$tag" "$fpm" -F -y "$fpm_conf" -c "$PHPRC"
  launch nginx "$tag" "$nginx" -p "$BENCH/nginx" -e stderr -c "$nginx_conf" -g 'daemon off;'
  wait_listener "$tag" "$nginx"
  wait_answer "$tag"
  verify_children "$tag" "$(cat "$BENCH/run/$tag.fpm.pid")" "$procs" php-fpm 'php-fpm: pool bench'
  echo "pid=$pid"
}

stop() {
  local tag=$1
  # QUIT is the graceful stop of nginx and of php-fpm.
  stop_pid nginx "$tag" QUIT 45
  stop_pid fpm "$tag" QUIT 45
  wait_port_free 20 || die "$tag: :$PORT is busy after the stop"
  PORT=$FPM_PORT wait_port_free 20 || die "$tag: :$FPM_PORT is busy after the stop"
}

case "${1:?start|stop}" in
start)
  shift
  start "$@"
  ;;
stop)
  stop "${2:?tag}"
  ;;
*)
  die "unknown command $1"
  ;;
esac
```

Run: `bash -n box/servers/php-fpm.sh && echo ok`
Expected: `ok`

- [ ] **Step 5: Write the nginx-rapira server script**

Create `box/servers/nginx-rapira.sh`:

```bash
#!/usr/bin/env bash
# nginx-rapira.sh start TAG PROCS BINARY_DIR MODE ENTRY
# nginx-rapira.sh stop TAG
# nginx listens on :$PORT and proxies to rapira on 127.0.0.1:8081.
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/../lib.sh"

BACKEND_PORT=8081
RAPIRA=$(dirname "$0")/rapira.sh

start() {
  local tag=$1 procs=$2 bindir=$3 mode=$4 entry=$5 nginx
  local conf=$BENCH/run/$tag.nginx.conf
  nginx=$(readlink -f "$(command -v nginx)")
  ensure_port_free
  PORT=$BACKEND_PORT ensure_port_free
  install -d "$BENCH/nginx/tmp" "$BENCH/nginx/run"
  render "$RIG/servers/nginx/rapira.conf.tpl" "$conf" "PROCS=$procs" "LISTEN=$PORT"
  "$nginx" -t -q -p "$BENCH/nginx" -e stderr -c "$conf" || die "$tag: the nginx configuration is invalid"
  PORT=$BACKEND_PORT LISTEN_HOST=127.0.0.1 "$RAPIRA" start "$tag" "$procs" "$bindir" "$mode" "$entry" >/dev/null ||
    fail "$tag" "the rapira backend did not start"
  launch nginx "$tag" "$nginx" -p "$BENCH/nginx" -e stderr -c "$conf" -g 'daemon off;'
  wait_listener "$tag" "$nginx"
  wait_answer "$tag"
  verify_children "$tag" "$pid" "$procs" nginx 'nginx: worker process'
  echo "pid=$pid"
}

stop() {
  local tag=$1
  # QUIT is the graceful stop of nginx.
  stop_pid nginx "$tag" QUIT 45
  wait_port_free 20 || die "$tag: :$PORT is busy after the stop"
  PORT=$BACKEND_PORT "$RAPIRA" stop "$tag"
}

case "${1:?start|stop}" in
start)
  shift
  start "$@"
  ;;
stop)
  stop "${2:?tag}"
  ;;
*)
  die "unknown command $1"
  ;;
esac
```

Run: `bash -n box/servers/nginx-rapira.sh && echo ok`
Expected: `ok`

- [ ] **Step 6: Write the RoadRunner server script**

Create `box/servers/roadrunner.sh`:

```bash
#!/usr/bin/env bash
# roadrunner.sh start TAG PROCS - grpc
# roadrunner.sh stop TAG
set -euo pipefail
# shellcheck source=box/lib.sh
. "$(dirname "$0")/../lib.sh"

start() {
  local tag=$1 procs=$2 shape=$4
  local bin=$BENCH/bin/rr file=$BENCH/run/$tag.rr.yaml
  [ "$shape" = grpc ] || die "unknown roadrunner shape $shape"
  [ -x "$bin" ] || die "$bin is missing; provision the server"
  [ -f "$GRPC_VENDOR/autoload.php" ] || die "$GRPC_VENDOR is missing; provision the server"
  ensure_port_free
  render "$RIG/servers/roadrunner/grpc.rr.yaml.tpl" "$file" "LISTEN=0.0.0.0:$PORT" "PROCS=$procs" "RIG=$RIG"
  launch rr "$tag" "$bin" serve -c "$file"
  wait_listener "$tag" "$bin"
  wait_grpc_answer "$tag"
  verify_children "$tag" "$pid" "$procs" rr
  echo "pid=$pid"
}

stop() {
  local tag=$1
  # rr replaces a worker that exits, so stop_pid signals rr before it kills the workers.
  stop_pid rr "$tag" TERM 45
  wait_port_free 20 || die "$tag: :$PORT is busy after the stop"
}

case "${1:?start|stop}" in
start)
  shift
  start "$@"
  ;;
stop)
  stop "${2:?tag}"
  ;;
*)
  die "unknown command $1"
  ;;
esac
```

Run: `bash -n box/servers/roadrunner.sh && chmod +x box/servers/frankenphp.sh box/servers/php-fpm.sh box/servers/nginx-rapira.sh box/servers/roadrunner.sh && echo ok`
Expected: `ok`

- [ ] **Step 7: Remove the script that this task replaces**

Run: `git rm -q scripts/fleet-leg.sh`
Expected: no output. The stop by process pattern of the old script is gone: every stop reads the pid files of the target.

- [ ] **Step 8: Check the scripts**

Run: `for f in box/servers/*.sh; do bash -n "$f"; done && if command -v shellcheck >/dev/null; then shellcheck -x box/lib.sh box/target.sh box/probe.sh box/servers/*.sh; else docker run --rm -v "$PWD:/mnt" koalaman/shellcheck:stable -x box/lib.sh box/target.sh box/probe.sh box/servers/*.sh; fi && echo clean`
Expected: `clean`

- [ ] **Step 9: Run the test to verify it passes**

Run: `docker build -t rapira-bench-box -f tests/box.Dockerfile tests && docker run --rm --init -v "$PWD:/repo:ro" rapira-bench-box`
Expected: PASS, `Ran 3 tests` and `OK`.

Run: `python3 -m unittest tests.test_box -v`
Expected: PASS, `Ran 1 test` and `OK (skipped=1)`.

- [ ] **Step 10: Commit**

```bash
git add box/servers/frankenphp.sh box/servers/php-fpm.sh box/servers/nginx-rapira.sh box/servers/roadrunner.sh tests/test_box.py tests/box.Dockerfile
git commit -s -S -m "feat: add the FrankenPHP, php-fpm, nginx-rapira, and RoadRunner server scripts"
```

### Task 11: Load and snapshot scripts

**Files:**
- Create: `box/snapshot.sh`
- Create: `box/load.sh`
- Create: `loader/wrk2-report.lua`
- Create: `loader/k6-grpc.js`
- Delete: `k6/grpc.js`
- Test: `tests/test_snapshot_script.py`
- Test: `tests/test_load.py`

**Interfaces:**
- Consumes: `apps/grpc/bench.binpb` (moved by Task 8).
- Produces: `box/snapshot.sh [PORT]` printing `cpu <busy> <total>`, `ena <counter_name> <value>`, `conns <established> <time_wait>` exactly as the contract states.
- Produces: `box/load.sh wrk2 EPOCH RATE THREADS CONNS DURATION_S URL [METHOD] [BODY_FILE] [HEADER...]` and `box/load.sh k6 EPOCH RATE VUS DURATION_S URL` as the contract states, with the `RESULT` line shape of the contract, plus: the wrk2 binary name on the loader is `wrk2` on `PATH`; a relative `BODY_FILE` is a path in the staged rig directory (`$HOME/bench-rig/<path>` on a box), an absolute path stays as is, `-` means no body; the script exits 0 after the tool ran, also when the tool output has no `RESULT` line (the driver then gets `parse_result(...) is None`), and exits 2 with a `usage:` line on stderr for a wrong argument list; the tool output (stdout and stderr merged) is printed unchanged except the `RESULT` line, which becomes `RESULT {"tool": ..., "late_ms": ..., <reporter fields>}`.
- Produces: `loader/wrk2-report.lua` reading `WRK_METHOD`, `WRK_BODY_FILE`, `WRK_HEADERS` in `init` and printing `RESULT {...}` with `errors.dropped` fixed at 0.
- Produces: `loader/k6-grpc.js` reading the k6 env `TARGET`, `RATE`, `DURATION` (seconds), `VUS`; it counts thrown calls in the custom counter `call_failures`; `load.sh` runs it as `k6 run --quiet --no-color --summary-trend-stats "avg,med,p(90),p(95),p(99),p(99.9),max" -e TARGET=<url> -e RATE=<rate> -e DURATION=<s> -e VUS=<vus> $HOME/bench-rig/loader/k6-grpc.js`.

- [ ] **Step 1: Write the failing test for the snapshot script**

Create `tests/test_snapshot_script.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_snapshot_script -v`
Expected: FAIL with `FAILED (failures=5)`; every subtest reports `AssertionError: 127 != 0 : bash: .../box/snapshot.sh: No such file or directory`.

- [ ] **Step 3: Write the snapshot script**

Create `box/snapshot.sh`:

```bash
#!/usr/bin/env bash
# Prints the counters of one stage snapshot:
#
#   cpu <busy> <total>
#   ena <counter_name> <value>
#   conns <established> <time_wait>
#
# cpu comes from the first line of /proc/stat: busy is user+nice+system+irq+softirq+steal,
# total is busy+idle+iowait, in clock ticks.
# ena is one line per ethtool -S counter of the default-route device whose name contains allowance_exceeded.
# conns counts the TCP sockets on the local port PORT, and only when PORT is given.
#
#   snapshot.sh [PORT]
set -euo pipefail

port=${1:-}

awk '$1 == "cpu" { busy = $2 + $3 + $4 + $7 + $8 + $9; printf "cpu %d %d\n", busy, busy + $5 + $6; exit }' /proc/stat

dev=$(ip -o route show default | awk '{ for (i = 1; i < NF; i++) if ($i == "dev" && dev == "") dev = $(i + 1) } END { print dev }')
if [ -n "$dev" ]; then
  ethtool -S "$dev" | awk -F: '$1 ~ /allowance_exceeded/ { gsub(/[ \t]/, "", $1); gsub(/[ \t]/, "", $2); print "ena", $1, $2 }'
fi

if [ -n "$port" ]; then
  established=$(ss -Htn state established "( sport = :$port )" | wc -l)
  time_wait=$(ss -Htn state time-wait "( sport = :$port )" | wc -l)
  echo "conns $established $time_wait"
fi
```

Run: `chmod +x box/snapshot.sh`

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_snapshot_script -v`
Expected: PASS with `Ran 2 tests` and `OK`.

- [ ] **Step 5: Commit**

```bash
git add box/snapshot.sh tests/test_snapshot_script.py
git commit -s -S -m "feat: add the stage snapshot script"
```

- [ ] **Step 6: Write the failing test for the load script and the wrk2 reporter**

Create `tests/test_load.py`. The expected `RESULT` fields come from the contract sample line. The `Wrk2ReportTest` class runs the reporter under `luajit` with a harness that defines the `wrk` table the way `src/wrk.lua` of wrk2 does; the class is skipped when `luajit` is not installed.

```python
"""Tests for box/load.sh with a fake wrk2 and a fake k6, and for loader/wrk2-report.lua under luajit."""

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOAD = ROOT / "box" / "load.sh"
REPORT = ROOT / "loader" / "wrk2-report.lua"
K6_SCRIPT = ROOT / "loader" / "k6-grpc.js"

URL = "http://10.0.1.5:8080/?name=you"
GRPC_URL = "http://10.0.1.5:8080/bench.v1.EchoService/Echo"
TREND_STATS = "avg,med,p(90),p(95),p(99),p(99.9),max"

# The RESULT line of the rig contract without tool and late_ms, as a reporter prints it.
REPORTER_RESULT = {
    "duration_us": 20000867,
    "requests": 39989,
    "bytes": 5038614,
    "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
    "latency_us": {"mean": 689.5, "p50": 689, "p90": 1111, "p95": 1175, "p99": 1264, "p999": 1351, "max": 2822},
    "requests_per_sec": 1999.363,
}
HUMAN_LINES = ["Running 20s test @ http://10.0.1.5:8080/?name=you", "  4 threads and 64 connections"]
TOOL_OUTPUT = "\n".join(HUMAN_LINES + ["RESULT " + json.dumps(REPORTER_RESULT)]) + "\n"
FAILED_OUTPUT = "unable to connect to 10.0.1.5:8080 Connection refused\n"

# The fake tool writes its arguments and the request environment to FAKE_LOG,
# prints the file FAKE_OUTPUT, and exits with FAKE_EXIT.
FAKE_TOOL = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    names = ("WRK_METHOD", "WRK_BODY_FILE", "WRK_HEADERS")
    with open(os.environ["FAKE_LOG"], "w") as f:
        json.dump({"argv": sys.argv[1:], "env": {n: os.environ.get(n) for n in names}}, f)
    with open(os.environ["FAKE_OUTPUT"]) as f:
        sys.stdout.write(f.read())
    sys.exit(int(os.environ["FAKE_EXIT"]))
    """)

WRK2_ARGV = ["-t", "4", "-c", "64", "-d", "20s", "-R", "2500", "--latency", "-s", str(REPORT), URL]
NO_WRK_ENV = {"WRK_METHOD": None, "WRK_BODY_FILE": None, "WRK_HEADERS": None}

# EPOCH in args is replaced with time.time() + epoch_offset. late_ms is the
# inclusive range the script must report. A future epoch is 0.5 s ahead, which
# is more than the start-up time of bash and python3, so late_ms is 0.
LOAD_CASES = [
    {
        "name": "wrk2 get with a future epoch",
        "args": ["wrk2", "EPOCH", "2500", "4", "64", "20", URL],
        "epoch_offset": 0.5,
        "argv": WRK2_ARGV,
        "env": {"WRK_METHOD": "GET", "WRK_BODY_FILE": "-", "WRK_HEADERS": ""},
        "late_ms": (0, 0),
    },
    {
        "name": "wrk2 post with a relative body and two headers, 5 s late",
        "args": ["wrk2", "EPOCH", "2500", "4", "64", "20", URL, "POST", "apps/grpc/echo.grpc",
                 "content-type: application/grpc-web+proto", "x-grpc-web: 1"],
        # The epoch is 5 s in the past, so late_ms is 5000 plus the start-up time of the script.
        "epoch_offset": -5,
        "argv": WRK2_ARGV,
        "env": {
            "WRK_METHOD": "POST",
            "WRK_BODY_FILE": str(ROOT / "apps" / "grpc" / "echo.grpc"),
            "WRK_HEADERS": "content-type: application/grpc-web+proto\nx-grpc-web: 1",
        },
        "late_ms": (5000, 5999),
    },
    {
        "name": "wrk2 keeps an absolute body path",
        "args": ["wrk2", "EPOCH", "2500", "4", "64", "20", URL, "POST", "/srv/body.bin"],
        "epoch_offset": 0.5,
        "argv": WRK2_ARGV,
        "env": {"WRK_METHOD": "POST", "WRK_BODY_FILE": "/srv/body.bin", "WRK_HEADERS": ""},
        "late_ms": (0, 0),
    },
    {
        "name": "k6 grpc stage",
        "args": ["k6", "EPOCH", "4000", "64", "20", GRPC_URL],
        "epoch_offset": 0.5,
        "argv": ["run", "--quiet", "--no-color", "--summary-trend-stats", TREND_STATS,
                 "-e", f"TARGET={GRPC_URL}", "-e", "RATE=4000", "-e", "DURATION=20", "-e", "VUS=64", str(K6_SCRIPT)],
        "env": NO_WRK_ENV,
        "late_ms": (0, 0),
    },
]

# The tool fails and prints no RESULT line. load.sh keeps the output and exits 0.
MISSING_RESULT_CASES = [
    {"name": "wrk2 failure", "args": ["wrk2", "0", "2500", "4", "64", "20", URL]},
    {"name": "k6 failure", "args": ["k6", "0", "4000", "64", "20", GRPC_URL]},
]

USAGE_CASES = [
    {"name": "unknown tool", "args": ["curl", "0", "1", "1", "1", "1", URL]},
    {"name": "wrk2 without a url", "args": ["wrk2", "0", "2500", "4", "64", "20"]},
    {"name": "k6 with an extra argument", "args": ["k6", "0", "4000", "64", "20", GRPC_URL, "POST"]},
    {"name": "no arguments", "args": []},
]


class LoadScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        for name in ("wrk2", "k6"):
            path = bin_dir / name
            path.write_text(FAKE_TOOL)
            path.chmod(0o755)
        self.log = tmp / "fake.json"
        self.output = tmp / "output.txt"
        env = {k: v for k, v in os.environ.items() if not k.startswith("WRK_")}
        env.update(PATH=f"{bin_dir}:{env['PATH']}", FAKE_LOG=str(self.log), FAKE_OUTPUT=str(self.output))
        self.env = env

    def load(self, args, output, exit_code):
        self.output.write_text(output)
        env = dict(self.env, FAKE_EXIT=str(exit_code))
        return subprocess.run(["bash", str(LOAD)] + args, env=env, capture_output=True, text=True, timeout=30)

    def test_rewrites_result(self):
        for case in LOAD_CASES:
            with self.subTest(name=case["name"]):
                epoch = time.time() + case["epoch_offset"]
                args = [f"{epoch:.3f}" if a == "EPOCH" else a for a in case["args"]]
                proc = self.load(args, TOOL_OUTPUT, 0)
                done = time.time()
                self.assertEqual(proc.returncode, 0, proc.stderr)
                lines = proc.stdout.splitlines()
                self.assertEqual(lines[:-1], HUMAN_LINES)
                self.assertTrue(lines[-1].startswith("RESULT "))
                result = json.loads(lines[-1][len("RESULT "):])
                low, high = case["late_ms"]
                self.assertGreaterEqual(result["late_ms"], low)
                self.assertLessEqual(result["late_ms"], high)
                self.assertEqual(result, {"tool": case["args"][0], "late_ms": result["late_ms"], **REPORTER_RESULT})
                self.assertGreaterEqual(done, epoch)
                fake = json.loads(self.log.read_text())
                self.assertEqual(fake["argv"], case["argv"])
                self.assertEqual(fake["env"], case["env"])

    def test_missing_result_keeps_output(self):
        for case in MISSING_RESULT_CASES:
            with self.subTest(name=case["name"]):
                proc = self.load(case["args"], FAILED_OUTPUT, 1)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, FAILED_OUTPUT)

    def test_usage(self):
        for case in USAGE_CASES:
            with self.subTest(name=case["name"]):
                proc = self.load(case["args"], TOOL_OUTPUT, 0)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("usage: load.sh", proc.stderr)
                self.assertEqual(proc.stdout, "")


BODY = b"\x00\x00\x00\x00\x03\n\x01x"

# The harness defines the wrk table the way src/wrk.lua does, runs init(), and
# prints the request shape. Header lines are sorted by name.
INIT_HARNESS = textwrap.dedent("""\
    wrk = { method = "GET", headers = {}, body = nil }
    dofile(arg[1])
    init({})
    io.write("method ", wrk.method, "\\n")
    if wrk.body then
      io.write("body ", (wrk.body:gsub(".", function(c) return string.format("%02x", c:byte()) end)), "\\n")
    end
    local names = {}
    for name in pairs(wrk.headers) do names[#names + 1] = name end
    table.sort(names)
    for _, name in ipairs(names) do io.write("header ", name, "=", wrk.headers[name], "\\n") end
    """)

# BODY in env is replaced with the path of a file that holds the bytes of BODY.
INIT_CASES = [
    {"name": "no environment keeps the default request", "env": {}, "lines": ["method GET"]},
    {
        "name": "post with a binary body and headers",
        "env": {
            "WRK_METHOD": "POST",
            "WRK_BODY_FILE": "BODY",
            "WRK_HEADERS": "content-type: application/grpc-web+proto\nx-grpc-web: 1\nx-note: a:b",
        },
        "lines": [
            "method POST",
            "body " + BODY.hex(),
            "header content-type=application/grpc-web+proto",
            "header x-grpc-web=1",
            # Only the first colon splits the name from the value.
            "header x-note=a:b",
        ],
    },
    {
        "name": "dash body file and empty headers mean no body and no headers",
        "env": {"WRK_METHOD": "GET", "WRK_BODY_FILE": "-", "WRK_HEADERS": ""},
        "lines": ["method GET"],
    },
]

DONE_HARNESS = textwrap.dedent("""\
    dofile(arg[1])
    local pct = {{ [50] = {p50}, [90] = {p90}, [95] = {p95}, [99] = {p99}, [99.9] = {p999} }}
    local latency = {{ mean = {mean}, max = {max} }}
    function latency:percentile(p) return pct[p] end
    done({{ duration = {duration}, requests = {requests}, bytes = {bytes},
      errors = {{ connect = {connect}, read = {read}, write = {write}, status = {status}, timeout = {timeout} }} }},
      latency, nil)
    """)

DONE_CASES = [
    {
        # 39989 requests in 20.000867 s is 1999.3633 req/s, printed with 3 decimals.
        "name": "contract sample",
        "stats": {"duration": 20000867, "requests": 39989, "bytes": 5038614, "connect": 0, "read": 0, "write": 0,
                  "status": 0, "timeout": 0, "mean": 689.5, "p50": 689, "p90": 1111, "p95": 1175, "p99": 1264,
                  "p999": 1351, "max": 2822},
        "result": REPORTER_RESULT,
    },
    {
        # An empty histogram gives a NaN mean, and a zero duration gives no rate. Both print as 0.
        "name": "empty histogram and zero duration",
        "stats": {"duration": 0, "requests": 0, "bytes": 0, "connect": 0, "read": 0, "write": 0, "status": 0,
                  "timeout": 0, "mean": "0/0", "p50": 0, "p90": 0, "p95": 0, "p99": 0, "p999": 0, "max": 0},
        "result": {
            "duration_us": 0, "requests": 0, "bytes": 0,
            "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0, "dropped": 0},
            "latency_us": {"mean": 0, "p50": 0, "p90": 0, "p95": 0, "p99": 0, "p999": 0, "max": 0},
            "requests_per_sec": 0,
        },
    },
    {
        # 20000 requests in 10 s is 2000 req/s. The error counters pass through.
        "name": "error counters pass through",
        "stats": {"duration": 10000000, "requests": 20000, "bytes": 1000000, "connect": 1, "read": 2, "write": 3,
                  "status": 12, "timeout": 4, "mean": 1500.25, "p50": 1400, "p90": 2000, "p95": 2100, "p99": 2500,
                  "p999": 3000, "max": 4000},
        "result": {
            "duration_us": 10000000, "requests": 20000, "bytes": 1000000,
            "errors": {"connect": 1, "read": 2, "write": 3, "status": 12, "timeout": 4, "dropped": 0},
            "latency_us": {"mean": 1500.25, "p50": 1400, "p90": 2000, "p95": 2100, "p99": 2500, "p999": 3000,
                           "max": 4000},
            "requests_per_sec": 2000,
        },
    },
]


@unittest.skipUnless(shutil.which("luajit"), "luajit is not installed")
class Wrk2ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.body = self.dir / "body.bin"
        self.body.write_bytes(BODY)

    def luajit(self, source, env):
        harness = self.dir / "harness.lua"
        harness.write_text(source)
        clean = {k: v for k, v in os.environ.items() if not k.startswith("WRK_")}
        proc = subprocess.run(["luajit", str(harness), str(REPORT)], env=dict(clean, **env),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_init_sets_request(self):
        for case in INIT_CASES:
            with self.subTest(name=case["name"]):
                env = {k: str(self.body) if v == "BODY" else v for k, v in case["env"].items()}
                self.assertEqual(self.luajit(INIT_HARNESS, env).splitlines(), case["lines"])

    def test_done_prints_result(self):
        for case in DONE_CASES:
            with self.subTest(name=case["name"]):
                out = self.luajit(DONE_HARNESS.format(**case["stats"]), {})
                self.assertTrue(out.startswith("RESULT "))
                self.assertEqual(json.loads(out[len("RESULT "):]), case["result"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 7: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_load -v`
Expected: FAIL with `FAILED (failures=16)`: the `LoadScriptTest` subtests report `AssertionError: 127 != 0` or `AssertionError: 127 != 2` (no `box/load.sh`), and the `Wrk2ReportTest` subtests report `AssertionError: 1 != 0 : luajit: cannot open .../loader/wrk2-report.lua`. Without `luajit` the result is `FAILED (failures=10, skipped=2)`.

- [ ] **Step 8: Write the wrk2 reporter**

Create `loader/wrk2-report.lua`. The `done` hook prints the `RESULT` line without the `tool` and `late_ms` fields. The `init` hook sets the request shape. wrk2 runs `init` before it formats the request, so the values reach every request.

```lua
-- wrk2 reporter for one stage.
-- The init phase sets the request shape from the environment: WRK_METHOD,
-- WRK_BODY_FILE (a path, or "-" for no body), and WRK_HEADERS (one
-- "name: value" per line). box/load.sh sets these variables.
-- The done phase writes one machine-readable line: RESULT {json}.
-- It uses only the wrk Lua API documented in SCRIPTING.
-- Latency values come from the corrected histogram, in microseconds.
-- wrk2 has no dropped counter, so the dropped key is always 0.

init = function(args)
  local method = os.getenv("WRK_METHOD")
  if method and method ~= "" then
    wrk.method = method
  end

  local path = os.getenv("WRK_BODY_FILE")
  if path and path ~= "" and path ~= "-" then
    local f = assert(io.open(path, "rb"))
    wrk.body = f:read("*a")
    f:close()
  end

  local headers = os.getenv("WRK_HEADERS") or ""
  for line in headers:gmatch("[^\n]+") do
    local name, value = line:match("^([^:]+):%s*(.*)$")
    if name then
      wrk.headers[name] = value
    end
  end
end

-- hdr_mean divides by total_count without a zero guard, so an empty
-- histogram yields NaN. JSON has no NaN, so map it to 0.
local function finite(x)
  if x ~= x or x == math.huge or x == -math.huge then
    return 0
  end
  return x
end

local function int(x)
  return string.format("%.0f", finite(x))
end

done = function(summary, latency, requests)
  local duration_us = summary.duration
  local duration_s = duration_us / 1000000.0
  local rps = 0
  if duration_s > 0 then
    rps = summary.requests / duration_s
  end

  local out = string.format(
    'RESULT {"duration_us":%s,"requests":%s,"bytes":%s,' ..
    '"errors":{"connect":%s,"read":%s,"write":%s,"status":%s,"timeout":%s,"dropped":0},' ..
    '"latency_us":{"mean":%.3f,"p50":%s,"p90":%s,"p95":%s,"p99":%s,' ..
    '"p999":%s,"max":%s},"requests_per_sec":%.3f}',
    int(duration_us), int(summary.requests), int(summary.bytes),
    int(summary.errors.connect), int(summary.errors.read),
    int(summary.errors.write), int(summary.errors.status),
    int(summary.errors.timeout),
    finite(latency.mean),
    int(latency:percentile(50.0)), int(latency:percentile(90.0)),
    int(latency:percentile(95.0)), int(latency:percentile(99.0)),
    int(latency:percentile(99.9)), int(latency.max),
    finite(rps))

  io.write(out, "\n")
end
```

Run: `python3 -m unittest tests.test_load.Wrk2ReportTest -v`
Expected: PASS with `Ran 2 tests` and `OK` (with `luajit` missing: `OK (skipped=2)`).

- [ ] **Step 9: Write the load script**

Create `box/load.sh`:

```bash
#!/usr/bin/env bash
# Runs one load process at a shared start time for one stage.
# Prints the tool output with the RESULT line extended by the tool and late_ms fields.
#
#   load.sh wrk2 EPOCH RATE THREADS CONNS DURATION_S URL [METHOD] [BODY_FILE] [HEADER...]
#   load.sh k6 EPOCH RATE VUS DURATION_S URL
#
# EPOCH is a Unix time in seconds and can have a fraction. When EPOCH has passed,
# the tool starts at once and late_ms is the delay in milliseconds.
# A relative BODY_FILE is a path in the staged rig directory.
set -euo pipefail

RIG=$(cd "$(dirname "$0")/.." && pwd)
TREND_STATS="avg,med,p(90),p(95),p(99),p(99.9),max"

usage() {
  echo "usage: load.sh wrk2 EPOCH RATE THREADS CONNS DURATION_S URL [METHOD] [BODY_FILE] [HEADER...]" >&2
  echo "       load.sh k6 EPOCH RATE VUS DURATION_S URL" >&2
  exit 2
}

# wait_epoch EPOCH sleeps until EPOCH and prints the start delay in milliseconds.
wait_epoch() {
  python3 -c '
import sys, time
delay = float(sys.argv[1]) - time.time()
if delay > 0:
    time.sleep(delay)
    print(0)
else:
    print(round(-delay * 1000))
' "$1"
}

# rewrite TOOL LATE_MS FILE prints FILE and adds the tool and late_ms fields to its RESULT line.
rewrite() {
  python3 -c '
import json, sys
tool, late_ms, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
with open(path, errors="replace") as f:
    for line in f:
        if line.startswith("RESULT "):
            doc = json.loads(line[len("RESULT "):])
            line = "RESULT " + json.dumps({"tool": tool, "late_ms": late_ms, **doc}) + "\n"
        sys.stdout.write(line)
' "$1" "$2" "$3"
}

run_wrk2() {
  [ $# -ge 6 ] || usage
  local epoch=$1 rate=$2 threads=$3 conns=$4 duration=$5 url=$6
  shift 6
  local method=GET body=-
  if [ $# -gt 0 ]; then
    method=$1
    shift
  fi
  if [ $# -gt 0 ]; then
    body=$1
    shift
  fi
  case $body in
  - | /*) ;;
  *) body=$RIG/$body ;;
  esac
  local headers
  headers=$(printf '%s\n' "$@")
  late_ms=$(wait_epoch "$epoch")
  # The driver treats a missing RESULT line as an invalid stage, so a tool failure does not stop the script.
  WRK_METHOD=$method WRK_BODY_FILE=$body WRK_HEADERS=$headers \
    wrk2 -t "$threads" -c "$conns" -d "${duration}s" -R "$rate" --latency \
    -s "$RIG/loader/wrk2-report.lua" "$url" >"$out" 2>&1 || true
}

run_k6() {
  [ $# -eq 5 ] || usage
  local epoch=$1 rate=$2 vus=$3 duration=$4 url=$5
  late_ms=$(wait_epoch "$epoch")
  # The driver treats a missing RESULT line as an invalid stage, so a tool failure does not stop the script.
  k6 run --quiet --no-color --summary-trend-stats "$TREND_STATS" \
    -e TARGET="$url" -e RATE="$rate" -e DURATION="$duration" -e VUS="$vus" \
    "$RIG/loader/k6-grpc.js" >"$out" 2>&1 || true
}

[ $# -ge 1 ] || usage
tool=$1
shift
out=$(mktemp)
trap 'rm -f "$out"' EXIT
late_ms=0

case $tool in
wrk2) run_wrk2 "$@" ;;
k6) run_k6 "$@" ;;
*) usage ;;
esac
rewrite "$tool" "$late_ms" "$out"
```

Run: `chmod +x box/load.sh`

- [ ] **Step 10: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_load -v`
Expected: PASS with `Ran 5 tests` and `OK` (with `luajit` missing: `OK (skipped=2)`).

- [ ] **Step 11: Write the k6 stage script and remove the old one**

Create `loader/k6-grpc.js`. It keeps the gRPC part of `k6/grpc.js`: the protoset, the fixed text, and the check that the reply text is exactly the expected text. It ramps to the stage rate in one second, holds it for the rest of the stage, and prints the `RESULT` line from `handleSummary`. One check per call keeps `errors.status` at or under `requests`, so `successful = requests - errors.status` is never negative.

```javascript
// k6 gRPC stage: an open-loop run that ramps to the stage rate in one second and holds it, against
// bench.v1.EchoService/Echo. box/load.sh runs one process per stage and loader.
//
// - env: TARGET (full URL; connect() takes only its host:port), RATE (requests
//   per second), DURATION (seconds), VUS (pre-allocated and maximum VUs).
// - one check per call compares the reply with the fixed text. The failed
//   checks and the failed calls never exceed the iterations.
// - handleSummary prints one line RESULT {json}. box/load.sh adds the tool and
//   late_ms fields.

import grpc from "k6/net/grpc";
import { check } from "k6";
import { Counter } from "k6/metrics";

const TARGET = __ENV.TARGET || "http://127.0.0.1:8080/bench.v1.EchoService/Echo";
const VUS = Number(__ENV.VUS || 256);
const TEXT = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ01";
const REPLY = `Hello from worker, ${TEXT}!`;

// A connect or an invoke that throws adds 1 here, and the iteration completes.
const callFailures = new Counter("call_failures");

const RATE = Number(__ENV.RATE || 1000);
const DURATION = Number(__ENV.DURATION || 20);

// The rate climbs from 0 to RATE over the first second and then holds RATE. The first call of
// every VU opens its connection, and a constant rate from the start drops iterations in that
// window. A 20 s stage still achieves about 97.5% of RATE, inside the 95% pass rule.
export const options = {
	scenarios: {
		stage: {
			executor: "ramping-arrival-rate",
			startRate: 0,
			timeUnit: "1s",
			preAllocatedVUs: VUS,
			maxVUs: VUS,
			stages: [
				{ duration: "1s", target: RATE },
				{ duration: `${DURATION - 1}s`, target: RATE },
			],
		},
	},
};

const client = new grpc.Client();
client.loadProtoset("../apps/grpc/bench.binpb");
let connected = false;

export default function () {
	try {
		if (!connected) {
			client.connect(TARGET.split("/")[2], { plaintext: true });
			connected = true;
		}
		const res = client.invoke("bench.v1.EchoService/Echo", { text: TEXT });
		check(res, {
			"reply matches": (r) => r.status === grpc.StatusOK && r.message !== null && r.message.text === REPLY,
		});
	} catch (e) {
		callFailures.add(1);
	}
}

// A metric without samples is absent from the summary data, so every read has a default of 0.
function metric(data, name, key) {
	const m = data.metrics[name];
	return m ? m.values[key] : 0;
}

// grpc_req_duration is in milliseconds. The RESULT line is in microseconds.
function micros(data, stat) {
	return metric(data, "grpc_req_duration", stat) * 1000;
}

export function handleSummary(data) {
	const result = {
		duration_us: Math.round(data.state.testRunDurationMs * 1000),
		requests: metric(data, "iterations", "count"),
		bytes: metric(data, "data_received", "count"),
		errors: {
			connect: 0,
			read: 0,
			write: 0,
			status: metric(data, "checks", "fails") + metric(data, "call_failures", "count"),
			timeout: 0,
			dropped: metric(data, "dropped_iterations", "count"),
		},
		latency_us: {
			mean: Math.round(micros(data, "avg") * 1000) / 1000,
			p50: Math.round(micros(data, "med")),
			p90: Math.round(micros(data, "p(90)")),
			p95: Math.round(micros(data, "p(95)")),
			p99: Math.round(micros(data, "p(99)")),
			p999: Math.round(micros(data, "p(99.9)")),
			max: Math.round(micros(data, "max")),
		},
		requests_per_sec: metric(data, "iterations", "rate"),
	};
	return { stdout: `RESULT ${JSON.stringify(result)}\n` };
}
```

Run: `git rm k6/grpc.js`

- [ ] **Step 12: Verify the k6 script with the real k6 against a closed port**

A connect to port 1 fails on every call, so every iteration adds 1 to `call_failures`, `errors.status` equals `requests`, and no latency sample exists. Rate 10 for 2 s gives 20 or 21 iterations.

Run:

```bash
bash box/load.sh k6 0 10 2 2 http://127.0.0.1:1/bench.v1.EchoService/Echo | python3 -c '
import json, sys
lines = [line for line in sys.stdin if line.startswith("RESULT ")]
r = json.loads(lines[-1][len("RESULT "):])
assert r["tool"] == "k6" and r["requests"] >= 19 and r["errors"]["status"] == r["requests"], r
assert set(r["latency_us"].values()) == {0} and r["errors"]["dropped"] == 0, r
print("k6 RESULT ok: %d requests, %d status errors" % (r["requests"], r["errors"]["status"]))
'
```

Expected: `k6 RESULT ok: 21 requests, 21 status errors` (20 is also correct).

- [ ] **Step 13: Commit**

```bash
git add box/load.sh loader/wrk2-report.lua loader/k6-grpc.js tests/test_load.py
git commit -s -S -m "feat: run one wrk2 or k6 load process per stage"
```

### Task 12: Provisioning

**Files:**
- Create: `box/lock-apps.sh`
- Create: `apps/symfony/composer.json` (generated by `box/lock-apps.sh`)
- Create: `apps/symfony/composer.lock` (generated by `box/lock-apps.sh`)
- Create: `apps/laravel/composer.json` (generated by `box/lock-apps.sh`)
- Create: `apps/laravel/composer.lock` (generated by `box/lock-apps.sh`)
- Create: `box/provision-server.sh`
- Create: `box/provision-loader.sh`
- Modify: `Makefile:27` (the `.PHONY` line) and the end of the file (new `lock` target)

**Interfaces:**
- Consumes: `servers/php.ini`, `servers/roadrunner/composer.json`, `servers/roadrunner/composer.lock`, `apps/symfony/index.php`, `apps/symfony/BenchController.php`, `apps/symfony/bench/worker-rapira.php`, `apps/symfony/bench/worker-franken.php`, `apps/laravel/web.php`, `apps/laravel/BenchController.php`, `apps/laravel/bench/worker-rapira.php`, `apps/static/app.css`, `apps/static/tiny.css` (all from Task 8); the staged tree at `$HOME/bench-rig`; the NEEDS words from `python3 -m rig needs --suite <suite>` (Task 13): server kinds `rapira frankenphp php-fpm nginx-rapira roadrunner` and apps `hello symfony laravel static grpc`.
- Produces: `bash bench-rig/box/provision-server.sh` with the environment `NIGHTLY` (sha7; `REF` is then ignored), `REF`, `BASE_REF` (default `main`), `NEEDS`, `FRAME_POINTERS` (default 0, 1 for perf sessions). Exit 1 with `ERROR: ...` on failure.
- Produces: the server layout: the rapira under test at `/opt/bench/rapira/<sha7>/bin/rapira`, the base build at `/opt/bench/rapira/base/bin/rapira` (server build only), so `BINARY_DIR` of the box protocol is the directory that holds `bin/rapira`; `/opt/bench/bin/frankenphp`; `/opt/bench/bin/rr`; `/opt/bench/php.ini`; `/opt/bench/apps/symfony` (docroot `public/`, classic entry `public/index.php`, FrankenPHP worker `public/worker-franken.php`, rapira worker `bench/worker-rapira.php`, and `public/tiny.css` and `public/app.css`); `/opt/bench/apps/laravel` (docroot `public/`, classic entry `public/index.php`, Octane FrankenPHP worker `public/frankenphp-worker.php`, rapira worker `bench/worker-rapira.php`); `/opt/bench/apps/roadrunner-grpc/vendor`.
- Produces: `/opt/bench/meta.json`: `{"rapira": R, "base": R | null, "kernel": str}` where R is `{"ref", "sha" (40 hex), "version", "build" ("nightly" | "server"), "asset" (str | null), "binary_sha256", "rustflags" (str | null), "dir"}`. The driver fills the run file `rapira` section from R and finds `BINARY_DIR` in `dir`.
- Produces: `/opt/bench/versions.json`: an object of version lines with the keys `php` always, and `frankenphp`, `php-fpm`, `nginx`, `roadrunner`, `protobuf` when NEEDS asks for them.
- Produces: `bash bench-rig/box/provision-loader.sh` (no environment needed; `WRK2_COMMIT` and `K6_VERSION` override the pins), which installs `/usr/local/bin/wrk2` and k6 and writes `/opt/bench/loader.json`: `{"instance_id", "wrk2_commit", "wrk2_version", "k6_version"}`.
- Produces: `make lock` runs `box/lock-apps.sh`; `apps/laravel/composer.json` carries `extra.bench-skeleton` (for example `laravel/laravel:v12.12.2`), which `provision-server.sh` reads.

- [ ] **Step 1: Write the lock script**

Create `box/lock-apps.sh`. The Symfony skeleton lists its packages in `flex-require`, and Symfony Flex moves them into `require` during the first install, so the Symfony lock comes from a full `create-project`. The Laravel app files are in the skeleton package itself, so the lock records the exact skeleton version.

```bash
#!/usr/bin/env bash
# Creates the committed composer files of the framework apps:
# apps/symfony/composer.json, apps/symfony/composer.lock,
# apps/laravel/composer.json, and apps/laravel/composer.lock.
# Run it on the operator machine with PHP 8.5, the PHP of the server box, and composer.
# Commit the four files after a run.
set -euo pipefail

SYMFONY_SKELETON=${SYMFONY_SKELETON:-symfony/skeleton:^7.3}
LARAVEL_SKELETON=${LARAVEL_SKELETON:-laravel/laravel:v12.12.2}
OCTANE_VERSION=${OCTANE_VERSION:-^2.12}

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
export COMPOSER_NO_INTERACTION=1

# Symfony Flex moves the flex-require list of the skeleton into require during
# the first install, so the lock comes from a full create-project.
lock_symfony() {
  local dir=$WORK/symfony
  composer create-project --no-progress --no-scripts "$SYMFONY_SKELETON" "$dir"
  install -m 0644 "$dir/composer.json" "$dir/composer.lock" "$ROOT/apps/symfony/"
}

# The Laravel app files come from the skeleton package itself. The skeleton
# version goes into extra.bench-skeleton before the lock, so the lock hash
# covers it and box/provision-server.sh creates the same skeleton.
lock_laravel() {
  local dir=$WORK/laravel
  composer create-project --no-progress --no-scripts --no-install "$LARAVEL_SKELETON" "$dir"
  composer --working-dir="$dir" config extra.bench-skeleton "$LARAVEL_SKELETON"
  composer --working-dir="$dir" require --no-update "laravel/octane:$OCTANE_VERSION"
  composer --working-dir="$dir" update --no-progress --no-scripts --no-install
  install -m 0644 "$dir/composer.json" "$dir/composer.lock" "$ROOT/apps/laravel/"
}

lock_symfony
lock_laravel
composer validate --no-check-publish --no-check-all "$ROOT/apps/symfony/composer.json"
composer validate --no-check-publish --no-check-all "$ROOT/apps/laravel/composer.json"
```

Run: `chmod +x box/lock-apps.sh && bash -n box/lock-apps.sh && echo syntax ok`
Expected: `syntax ok`

- [ ] **Step 2: Add the `lock` target to the Makefile**

In `Makefile`, append ` lock` to the `.PHONY:` line (line 27), so the line ends with `preflight grpc_fixtures lock`. Then append this block at the end of the file:

```make
# Local only: needs PHP 8.5 and composer. Commit apps/symfony and apps/laravel composer.json and composer.lock after a run.
lock:
	box/lock-apps.sh
```

Run: `make -n lock`
Expected: `box/lock-apps.sh`

- [ ] **Step 3: Create the lock files**

Run: `make lock`
Expected: the output ends with the two lines `<repo>/apps/symfony/composer.json is valid` and `<repo>/apps/laravel/composer.json is valid`.

Run:

```bash
python3 -c '
import json
s = json.load(open("apps/symfony/composer.json"))
l = json.load(open("apps/laravel/composer.json"))
print(s["require"]["symfony/framework-bundle"], l["extra"]["bench-skeleton"], l["require"]["laravel/octane"])
'
```

Expected: `7.4.* laravel/laravel:v12.12.2 ^2.12`

- [ ] **Step 4: Commit**

```bash
git add box/lock-apps.sh Makefile apps/symfony/composer.json apps/symfony/composer.lock apps/laravel/composer.json apps/laravel/composer.lock
git commit -s -S -m "build: lock the Symfony and Laravel app dependencies"
```

- [ ] **Step 5: Write the server provisioning script**

Create `box/provision-server.sh`. It carries over `scripts/provision-server.sh`: the package list, the kernel knobs, the ref resolution, the build, ext-protobuf, RoadRunner, and the app setup. New: the nightly path, the NEEDS gates, the lock-based app install, the shared php.ini, the version records, and the clock check. Frame pointers are off by default.

```bash
#!/usr/bin/env bash
# Provisions the server box for the targets of one suite.
#
# Environment:
#   NIGHTLY         sha7 of a build on the nightly release of the core repository. REF is then ignored.
#   REF, BASE_REF   refs to build on the box when NIGHTLY is empty: a branch, a tag, a sha, or pr/N.
#   NEEDS           the server kinds and apps of the suite, space separated, from python3 -m rig needs.
#   FRAME_POINTERS  1 builds rapira with frame pointers for a perf session.
#
# Results:
#   /opt/bench/rapira/<sha7>/bin/rapira   the rapira under test
#   /opt/bench/rapira/base/bin/rapira     the base build, only for a server build
#   /opt/bench/meta.json                  the rapira identity for the run file
#   /opt/bench/versions.json              one version line per server and runtime
#   /opt/bench/php.ini                    the shared php.ini
#   /opt/bench/bin/frankenphp, /opt/bench/bin/rr
#   /opt/bench/apps/symfony, /opt/bench/apps/laravel, /opt/bench/apps/roadrunner-grpc
set -euo pipefail

NIGHTLY=${NIGHTLY:-}
REF=${REF:-}
BASE_REF=${BASE_REF:-main}
NEEDS=${NEEDS:-}
FRAME_POINTERS=${FRAME_POINTERS:-0}
CORE_SLUG=${CORE_SLUG:-rapira-rs/rapira}
CORE_REPO=${CORE_REPO:-https://github.com/$CORE_SLUG}
FRANKEN_VERSION=${FRANKEN_VERSION:-1.12.7}
PROTOBUF_VERSION=${PROTOBUF_VERSION:-5.36.2}
RR_VERSION=${RR_VERSION:-2025.1.15}

BENCH=/opt/bench
RIG=$HOME/bench-rig
CORE=$HOME/core
export COMPOSER_NO_INTERACTION=1

# needs WORD succeeds when WORD is in NEEDS.
needs() {
  case " $NEEDS " in
  *" $1 "*) return 0 ;;
  *) return 1 ;;
  esac
}

install_packages() {
  local pkgs="php-cli php-opcache ethtool curl tar diffutils python3 chrony"
  if [ -n "$NIGHTLY" ]; then
    # The runtime libraries of the nightly build: the rpm depends list in nfpm.yaml of the core repository.
    pkgs="$pkgs libpq openssl-libs libcurl libxml2 sqlite-libs oniguruma zlib"
  else
    pkgs="$pkgs php-devel php-embedded clang clang-devel gcc make cmake git perf"
  fi
  if needs php-fpm || needs nginx-rapira; then
    pkgs="$pkgs nginx"
  fi
  if needs php-fpm; then
    pkgs="$pkgs php-fpm"
  fi
  if needs symfony || needs laravel || needs roadrunner || needs grpc; then
    pkgs="$pkgs composer unzip git"
  fi
  if needs symfony || needs laravel; then
    pkgs="$pkgs php-mbstring php-xml php-pdo php-process php-sodium"
  fi
  # shellcheck disable=SC2086
  sudo dnf -y install $pkgs
}

system_knobs() {
  sudo tee /etc/sysctl.d/90-rapira-bench.conf >/dev/null <<'CONF'
kernel.perf_event_paranoid = -1
kernel.kptr_restrict = 0
net.core.somaxconn = 65535
CONF
  sudo sysctl -q -p /etc/sysctl.d/90-rapira-bench.conf
  sudo tee /etc/security/limits.d/90-rapira-bench.conf >/dev/null <<'CONF'
fedora soft nofile 1048576
fedora hard nofile 1048576
CONF
}

# The loaders start each stage at a shared wall-clock time, so every box needs a synchronized clock.
check_clock() {
  sudo systemctl enable --now chronyd
  if ! chronyc waitsync 60 0.01 0 1 >/dev/null; then
    echo "ERROR: chrony is not synchronized after 60 s"
    chronyc tracking
    exit 1
  fi
  if ! chronyc tracking | grep -q '^Leap status *: Normal$'; then
    echo "ERROR: the chrony leap status is not Normal"
    chronyc tracking
    exit 1
  fi
}

# record_version NAME TEXT stores one version line in /opt/bench/versions.json.
record_version() {
  python3 - "$BENCH/versions.json" "$1" "$2" <<'PY'
import json, os, sys

path, name, text = sys.argv[1:4]
doc = {}
if os.path.exists(path):
    with open(path) as f:
        doc = json.load(f)
doc[name] = text
with open(path, "w") as f:
    json.dump(doc, f, indent=1, sort_keys=True)
    f.write("\n")
PY
}

# write_meta DIR REF SHA VERSION BUILD ASSET RUSTFLAGS [BASE_DIR BASE_REF BASE_SHA BASE_VERSION]
# writes /opt/bench/meta.json with the rapira under test and the optional base build.
write_meta() {
  python3 - "$@" <<'PY'
import hashlib, json, platform, sys

def record(directory, ref, sha, version, build, asset, rustflags):
    with open(directory + "/bin/rapira", "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return {
        "ref": ref,
        "sha": sha,
        "version": version,
        "build": build,
        "asset": asset or None,
        "binary_sha256": digest,
        "rustflags": rustflags if build == "server" else None,
        "dir": directory,
    }

args = sys.argv[1:]
build, asset, rustflags = args[4], args[5], args[6]
meta = {
    "rapira": record(args[0], args[1], args[2], args[3], build, asset, rustflags),
    "base": None,
    "kernel": platform.release(),
}
if len(args) == 11:
    meta["base"] = record(args[7], args[8], args[9], args[10], build, "", rustflags)
with open("/opt/bench/meta.json", "w") as f:
    json.dump(meta, f, indent=1)
    f.write("\n")
PY
}

# resolve_nightly prints the full sha, the version, the tarball name, and the checksum file name
# of the NIGHTLY build on the nightly release.
resolve_nightly() {
  python3 - "$CORE_SLUG" "$NIGHTLY" <<'PY'
import json, re, sys, urllib.request

slug, sha7 = sys.argv[1], sys.argv[2]


def get(path):
    request = urllib.request.Request("https://api.github.com/repos/" + slug + path, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


# The core Nightly workflow moves the nightly tag to each new build and deletes the assets of older builds.
sha = get("/git/ref/tags/nightly")["object"]["sha"]
if not sha.startswith(sha7):
    sys.exit(f"ERROR: the nightly tag is at {sha[:7]}, not {sha7}; the release has no assets for {sha7}, rerun with NIGHTLY={sha[:7]}")
names = [asset["name"] for asset in get("/releases/tags/nightly")["assets"]]
tarball = re.compile(r"rapira-v(.+-nightly\." + re.escape(sha7) + r")-php8\.5-linux-x86_64\.tar\.gz")
found = [m for m in map(tarball.fullmatch, names) if m]
if len(found) != 1:
    sys.exit(f"ERROR: expected one php8.5 linux x86_64 tarball for {sha7} on the nightly release, found {len(found)}")
version = found[0].group(1)
sums = f"rapira-v{version}-SHA256SUMS.txt"
if sums not in names:
    sys.exit(f"ERROR: {sums} is missing on the nightly release")
print(sha, version, found[0].group(0), sums)
PY
}

install_nightly() {
  local resolved sha version asset sums
  local dir=$BENCH/rapira/$NIGHTLY dl=$HOME/nightly
  resolved=$(resolve_nightly)
  read -r sha version asset sums <<<"$resolved"
  rm -rf "$dl" "$dir"
  install -d "$dl" "$dir"
  curl -fsSL --retry 3 -o "$dl/$asset" "https://github.com/$CORE_SLUG/releases/download/nightly/$asset"
  curl -fsSL --retry 3 -o "$dl/$sums" "https://github.com/$CORE_SLUG/releases/download/nightly/$sums"
  (cd "$dl" && awk -v name="$asset" '$2 == name' "$sums" | sha256sum -c -)
  tar -xzf "$dl/$asset" -C "$dir" --strip-components=1
  if ldd "$dir/bin/rapira" 2>/dev/null | grep -F 'not found'; then
    echo "ERROR: $dir/bin/rapira has missing libraries"
    exit 1
  fi
  write_meta "$dir" nightly "$sha" "$version" nightly "$asset" ""
}

resolve_ref() {
  local r=$1
  case "$r" in
  pr/*)
    git -C "$CORE" fetch -q origin "refs/pull/${r#pr/}/head"
    git -C "$CORE" rev-parse FETCH_HEAD
    ;;
  *)
    git -C "$CORE" rev-parse --verify -q "origin/$r^{commit}" 2>/dev/null ||
      git -C "$CORE" rev-parse --verify "$r^{commit}"
    ;;
  esac
}

# build_one DIR SHA RUSTFLAGS builds rapira at SHA into DIR/bin/rapira and skips a build that is already there.
build_one() {
  local dir=$1 sha=$2 rustflags=$3
  local marker=$dir/build.marker
  if [ -f "$marker" ] && [ "$(cat "$marker")" = "$sha|$rustflags" ] && [ -x "$dir/bin/rapira" ]; then
    echo "==> $dir already built at $sha"
    return 0
  fi
  echo "==> build $dir at $sha"
  git -C "$CORE" checkout -q "$sha"
  (cd "$CORE" && env \
    RUSTFLAGS="$rustflags" \
    CARGO_PROFILE_RELEASE_DEBUG=line-tables-only \
    PHP_CONFIG=/usr/bin/php-config \
    CARGO_TARGET_DIR="$HOME/core-target" \
    cargo build --release)
  install -d "$dir/bin"
  install -m 0755 "$HOME/core-target/release/rapira" "$dir/bin/rapira"
  echo "$sha|$rustflags" >"$marker"
}

build_server() {
  local root_kb base_sha pr_sha pr7 pr_version base_version
  local rustflags=""
  root_kb=$(df -Pk / | awk 'NR==2 {print $2}')
  if [ "$root_kb" -lt $((30 * 1024 * 1024)) ]; then
    echo "ERROR: root filesystem is $((root_kb / 1024 / 1024)) GiB; expected >= 30 GiB"
    exit 1
  fi
  if [ ! -x "$HOME/.cargo/bin/cargo" ]; then
    curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal
  fi
  # shellcheck disable=SC1091
  . "$HOME/.cargo/env"
  if [ ! -d "$CORE/.git" ]; then
    git clone "$CORE_REPO" "$CORE"
  fi
  git -C "$CORE" fetch origin --tags --prune
  if [ "$FRAME_POINTERS" = 1 ]; then
    rustflags="-C force-frame-pointers=yes"
  fi
  base_sha=$(resolve_ref "$BASE_REF")
  pr_sha=$(resolve_ref "$REF")
  pr7=$(git -C "$CORE" rev-parse --short=7 "$pr_sha")
  build_one "$BENCH/rapira/base" "$base_sha" "$rustflags"
  build_one "$BENCH/rapira/$pr7" "$pr_sha" "$rustflags"
  pr_version=$(git -C "$CORE" describe --tags --always "$pr_sha")
  base_version=$(git -C "$CORE" describe --tags --always "$base_sha")
  write_meta "$BENCH/rapira/$pr7" "$REF" "$pr_sha" "$pr_version" server "" "$rustflags" \
    "$BENCH/rapira/base" "$BASE_REF" "$base_sha" "$base_version"
}

install_frankenphp() {
  local current
  current=$("$BENCH/bin/frankenphp" version 2>/dev/null || true)
  case $current in
  *"FrankenPHP v$FRANKEN_VERSION "*) ;;
  *)
    # The glibc build: https://frankenphp.dev/docs/performance/#avoid-musl-in-production-prefer-glibc-builds
    curl -fsSL --retry 3 -o "$BENCH/bin/frankenphp" \
      "https://github.com/php/frankenphp/releases/download/v$FRANKEN_VERSION/frankenphp-linux-x86_64-gnu"
    chmod 0755 "$BENCH/bin/frankenphp"
    ;;
  esac
  if ldd "$BENCH/bin/frankenphp" 2>/dev/null | grep -F 'not found'; then
    echo "ERROR: $BENCH/bin/frankenphp has missing libraries"
    exit 1
  fi
  record_version frankenphp "$("$BENCH/bin/frankenphp" version)"
}

# The nightly tarball ships no PHP headers, so only a server build gets ext-protobuf.
install_protobuf() {
  if php -r "exit(phpversion('protobuf') === '$PROTOBUF_VERSION' ? 0 : 1);"; then
    return 0
  fi
  local src=$HOME/protobuf-$PROTOBUF_VERSION
  rm -rf "$src"
  curl -fsSL "https://pecl.php.net/get/protobuf-$PROTOBUF_VERSION.tgz" | tar -xzf - -C "$HOME" "protobuf-$PROTOBUF_VERSION"
  (cd "$src" && phpize && ./configure && make -j"$(nproc)" && sudo make install)
  echo 'extension=protobuf.so' | sudo tee /etc/php.d/40-protobuf.ini >/dev/null
  if ! php -r "exit(phpversion('protobuf') === '$PROTOBUF_VERSION' ? 0 : 1);"; then
    echo "ERROR: ext-protobuf $PROTOBUF_VERSION is not loaded after the build"
    exit 1
  fi
}

install_roadrunner() {
  local current
  local rrg=$BENCH/apps/roadrunner-grpc
  current=$("$BENCH/bin/rr" --version 2>/dev/null || true)
  case $current in
  *"rr version $RR_VERSION "*) ;;
  *)
    curl -fsSL --retry 3 "https://github.com/roadrunner-server/roadrunner/releases/download/v$RR_VERSION/roadrunner-$RR_VERSION-linux-amd64.tar.gz" |
      tar -xzf - -C "$HOME" "roadrunner-$RR_VERSION-linux-amd64/rr"
    install -m 0755 "$HOME/roadrunner-$RR_VERSION-linux-amd64/rr" "$BENCH/bin/rr"
    ;;
  esac
  rm -rf "$rrg"
  install -d "$rrg"
  install -m 0644 "$RIG/servers/roadrunner/composer.json" "$RIG/servers/roadrunner/composer.lock" "$rrg/"
  composer --working-dir="$rrg" install --no-dev --no-progress
  record_version roadrunner "$("$BENCH/bin/rr" --version)"
}

install_symfony() {
  local dir=$BENCH/apps/symfony
  rm -rf "$dir"
  install -d "$dir"
  install -m 0644 "$RIG/apps/symfony/composer.json" "$RIG/apps/symfony/composer.lock" "$dir/"
  composer --working-dir="$dir" install --no-dev --no-progress --no-scripts
  # Symfony Flex runs its recipes only in create-project. recipes:install creates bin/, config/, public/, and src/.
  composer --working-dir="$dir" recipes:install --force --reset
  install -d "$dir/bench" "$dir/src/Controller"
  install -m 0644 "$RIG/apps/symfony/bench/worker-rapira.php" "$dir/bench/"
  install -m 0644 "$RIG/apps/symfony/bench/worker-franken.php" "$dir/public/"
  install -m 0644 "$RIG/apps/symfony/index.php" "$dir/public/index.php"
  install -m 0644 "$RIG/apps/symfony/BenchController.php" "$dir/src/Controller/"
  install -m 0644 "$RIG/apps/static/app.css" "$RIG/apps/static/tiny.css" "$dir/public/"
  printf 'APP_ENV=prod\nAPP_DEBUG=0\nAPP_SECRET=8f2f4c9a51e04d0bafd3a7f22c1e6b90\n' >"$dir/.env.local"
  (cd "$dir" &&
    composer dump-autoload --optimize --classmap-authoritative --no-dev --quiet &&
    composer dump-env prod --quiet &&
    rm -rf var/cache/prod &&
    php bin/console cache:warmup -q)
}

install_laravel() {
  local dir=$BENCH/apps/laravel skeleton
  skeleton=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["extra"]["bench-skeleton"])' "$RIG/apps/laravel/composer.json")
  rm -rf "$dir"
  # The Laravel app files come from the skeleton package. The vendor tree comes from the committed lock.
  composer create-project --no-progress --no-scripts --no-install "$skeleton" "$dir"
  install -m 0644 "$RIG/apps/laravel/composer.json" "$RIG/apps/laravel/composer.lock" "$dir/"
  install -m 0644 "$RIG/apps/laravel/web.php" "$dir/routes/web.php"
  install -m 0644 "$RIG/apps/laravel/BenchController.php" "$dir/app/Http/Controllers/"
  install -d "$dir/bench"
  install -m 0644 "$RIG/apps/laravel/bench/worker-rapira.php" "$dir/bench/"
  cat >"$dir/.env" <<'ENV'
APP_NAME=bench
APP_ENV=production
APP_DEBUG=false
APP_KEY=
APP_URL=http://localhost:8080
LOG_CHANNEL=stderr
LOG_LEVEL=error
LOG_DEPRECATIONS_CHANNEL=null
SESSION_DRIVER=array
SESSION_LIFETIME=120
CACHE_STORE=array
QUEUE_CONNECTION=sync
BROADCAST_CONNECTION=null
FILESYSTEM_DISK=local
MAIL_MAILER=log
DB_CONNECTION=sqlite
APP_MAINTENANCE_DRIVER=file
BCRYPT_ROUNDS=4
OCTANE_SERVER=frankenphp
ENV
  (cd "$dir" &&
    composer install --no-dev --no-progress --no-scripts &&
    php artisan key:generate --force -q &&
    composer dump-autoload --optimize --classmap-authoritative --no-dev --quiet &&
    php artisan optimize -q)
  install -m 0644 "$dir/vendor/laravel/octane/src/Commands/stubs/frankenphp-worker.php" "$dir/public/frankenphp-worker.php"
}

if [ -z "$NIGHTLY" ] && [ -z "$REF" ]; then
  echo "ERROR: set NIGHTLY=<sha7> or REF=<branch, tag, sha, or pr/N>"
  exit 1
fi

echo "==> packages"
install_packages
echo "==> system knobs"
system_knobs
echo "==> clock"
check_clock

sudo install -d -o fedora -g fedora "$BENCH" "$BENCH/bin" "$BENCH/run" "$BENCH/log" "$BENCH/apps" "$BENCH/rapira"
rm -f "$BENCH/versions.json"
install -m 0644 "$RIG/servers/php.ini" "$BENCH/php.ini"
if ! php -c "$BENCH/php.ini" -r 'exit(ini_get("opcache.enable_cli") ? 0 : 1);'; then
  echo "ERROR: opcache is off under $BENCH/php.ini"
  exit 1
fi
record_version php "$(php -v | sed -n 1p)"

if [ -n "$NIGHTLY" ]; then
  echo "==> rapira nightly $NIGHTLY"
  install_nightly
else
  echo "==> rapira server build of $REF and $BASE_REF"
  build_server
fi

if needs frankenphp; then
  echo "==> frankenphp $FRANKEN_VERSION"
  install_frankenphp
fi
if needs php-fpm; then
  record_version php-fpm "$(php-fpm -v | sed -n 1p)"
fi
if needs php-fpm || needs nginx-rapira; then
  record_version nginx "$(nginx -v 2>&1)"
fi
if needs roadrunner || needs grpc; then
  echo "==> grpc runtimes"
  if [ -z "$NIGHTLY" ]; then
    install_protobuf
  fi
  install_roadrunner
  record_version protobuf "$(php -r 'echo phpversion("protobuf") ?: "ext-protobuf not loaded";')"
fi
if needs symfony; then
  echo "==> symfony"
  install_symfony
fi
if needs laravel; then
  echo "==> laravel"
  install_laravel
fi

echo "==> server provisioned: needs=$NEEDS"
python3 -c 'import json; print(json.dumps(json.load(open("/opt/bench/meta.json"))["rapira"]))'
```

Run: `chmod +x box/provision-server.sh`

- [ ] **Step 6: Verify the server provisioning script**

Run: `bash -n box/provision-server.sh && echo syntax ok`
Expected: `syntax ok`

Run the Python resolver of `resolve_nightly` against the live release. The `sed` lines cut the heredoc body out of the script. The current nightly sha7 comes from the tag:

```bash
sha7=$(git ls-remote https://github.com/rapira-rs/rapira refs/tags/nightly | cut -c1-7)
sed -n '/^resolve_nightly() {/,/^PY$/p' box/provision-server.sh | sed '1,2d;$d' | python3 - rapira-rs/rapira "$sha7"
```

Expected: one line `<40 hex sha starting with sha7> <version>-nightly.<sha7> rapira-v<version>-nightly.<sha7>-php8.5-linux-x86_64.tar.gz rapira-v<version>-nightly.<sha7>-SHA256SUMS.txt`, for example `a36a356f8bbf9af5fb93c938636372de3c6f062e 0.8.1-nightly.a36a356 rapira-v0.8.1-nightly.a36a356-php8.5-linux-x86_64.tar.gz rapira-v0.8.1-nightly.a36a356-SHA256SUMS.txt`.

- [ ] **Step 7: Commit**

```bash
git add box/provision-server.sh
git commit -s -S -m "feat: provision the server from the nightly asset or a server build"
```

- [ ] **Step 8: Write the loader provisioning script**

Create `box/provision-loader.sh`. It replaces wrk and h2load with wrk2 at the pinned commit, keeps k6 2.2.0 and the kernel knobs, and adds the clock check and the loader record.

```bash
#!/usr/bin/env bash
# Provisions a loader box: wrk2 at a pinned commit, k6, the kernel knobs, and a clock check.
#
# Results:
#   /usr/local/bin/wrk2, /usr/bin/k6
#   /opt/bench/loader.json   the instance id, the wrk2 commit and version line, and the k6 version line
set -euo pipefail

WRK2_COMMIT=${WRK2_COMMIT:-44a94c17d8e6a0bac8559b53da76848e430cb7a7}
K6_VERSION=${K6_VERSION:-2.2.0}

BENCH=/opt/bench
BCSAVE=deps/luajit/src/jit/bcsave.lua

build_wrk2() {
  local src=$HOME/wrk2-src
  if [ -x /usr/local/bin/wrk2 ] && [ "$(cat "$BENCH/wrk2.commit" 2>/dev/null)" = "$WRK2_COMMIT" ]; then
    echo "==> wrk2 already built at $WRK2_COMMIT"
    return 0
  fi
  rm -rf "$src"
  git clone -q https://github.com/giltene/wrk2 "$src"
  git -C "$src" checkout -q "$WRK2_COMMIT"
  # The ELF string table of obj/bytecode.o holds a zero byte, the symbol name, and a zero byte.
  # The stock size #symname+1 truncates luaJIT_BC_wrk on current binutils, and every -s script run panics.
  sed -i 's/o\.sect\[3\]\.size = fofs(#symname+1)/o.sect[3].size = fofs(#symname+2)/' "$src/$BCSAVE"
  if ! grep -qF 'o.sect[3].size = fofs(#symname+2)' "$src/$BCSAVE"; then
    echo "ERROR: the $BCSAVE fix did not apply"
    exit 1
  fi
  make -s -C "$src" -j"$(nproc)"
  if ! nm "$src/obj/bytecode.o" | awk '$3 == "luaJIT_BC_wrk" { found = 1 } END { exit !found }'; then
    echo "ERROR: $src/obj/bytecode.o has no luaJIT_BC_wrk symbol"
    exit 1
  fi
  sudo install -m 0755 "$src/wrk" /usr/local/bin/wrk2
  echo "$WRK2_COMMIT" >"$BENCH/wrk2.commit"
}

install_k6() {
  local current
  current=$(k6 version 2>/dev/null || true)
  case $current in
  *"k6 v$K6_VERSION "*) ;;
  *) sudo dnf -y install "https://github.com/grafana/k6/releases/download/v$K6_VERSION/k6-v$K6_VERSION-linux-amd64.rpm" ;;
  esac
}

system_knobs() {
  sudo tee /etc/sysctl.d/90-rapira-bench.conf >/dev/null <<'CONF'
net.ipv4.ip_local_port_range = 1024 65000
CONF
  sudo sysctl -q -p /etc/sysctl.d/90-rapira-bench.conf
  sudo tee /etc/security/limits.d/90-rapira-bench.conf >/dev/null <<'CONF'
fedora soft nofile 1048576
fedora hard nofile 1048576
CONF
}

# The loaders start each stage at a shared wall-clock time, so every box needs a synchronized clock.
check_clock() {
  sudo systemctl enable --now chronyd
  if ! chronyc waitsync 60 0.01 0 1 >/dev/null; then
    echo "ERROR: chrony is not synchronized after 60 s"
    chronyc tracking
    exit 1
  fi
  if ! chronyc tracking | grep -q '^Leap status *: Normal$'; then
    echo "ERROR: the chrony leap status is not Normal"
    chronyc tracking
    exit 1
  fi
}

# instance_id prints the EC2 instance id from the IMDSv2 endpoint.
instance_id() {
  local token
  token=$(curl -fsS -X PUT -H "X-aws-ec2-metadata-token-ttl-seconds: 60" http://169.254.169.254/latest/api/token)
  curl -fsS -H "X-aws-ec2-metadata-token: $token" http://169.254.169.254/latest/meta-data/instance-id
}

write_record() {
  local id wrk2_line k6_line
  id=$(instance_id)
  # wrk2 --version prints the version line and exits 1.
  wrk2_line=$(wrk2 --version 2>/dev/null | sed -n 1p || true)
  k6_line=$(k6 version | sed -n 1p)
  python3 - "$BENCH/loader.json" "$id" "$WRK2_COMMIT" "$wrk2_line" "$k6_line" <<'PY'
import json, sys

path, instance_id, commit, wrk2, k6 = sys.argv[1:6]
with open(path, "w") as f:
    json.dump({"instance_id": instance_id, "wrk2_commit": commit, "wrk2_version": wrk2, "k6_version": k6}, f, indent=1)
    f.write("\n")
PY
}

echo "==> packages"
sudo dnf -y install gcc make git openssl-devel binutils ethtool curl tar diffutils python3 chrony
sudo install -d -o fedora -g fedora "$BENCH"
echo "==> system knobs"
system_knobs
echo "==> clock"
check_clock
echo "==> wrk2 $WRK2_COMMIT"
build_wrk2
echo "==> k6 $K6_VERSION"
install_k6
write_record
echo "==> loader provisioned: $(tr -d '\n' <"$BENCH/loader.json")"
```

Run: `chmod +x box/provision-loader.sh && bash -n box/provision-loader.sh && echo syntax ok`
Expected: `syntax ok`

- [ ] **Step 9: Verify the wrk2 fix and build on the operator machine**

This runs the clone, fix, build, and symbol check of `build_wrk2` in a temporary directory. It needs `gcc`, `make`, `git`, and the OpenSSL headers.

```bash
src=$(mktemp -d)
git clone -q https://github.com/giltene/wrk2 "$src"
git -C "$src" checkout -q 44a94c17d8e6a0bac8559b53da76848e430cb7a7
sed -i 's/o\.sect\[3\]\.size = fofs(#symname+1)/o.sect[3].size = fofs(#symname+2)/' "$src/deps/luajit/src/jit/bcsave.lua"
git -C "$src" diff --stat
make -s -C "$src" -j"$(nproc)" >/dev/null 2>&1
nm "$src/obj/bytecode.o"
rm -rf "$src"
```

Expected:

```
 deps/luajit/src/jit/bcsave.lua | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
0000000000000000 R luaJIT_BC_wrk
```

- [ ] **Step 10: Commit**

```bash
git add box/provision-loader.sh
git commit -s -S -m "feat: provision loaders with wrk2 and k6"
```

### Task 13: Terraform, Makefile, rig and ssh modules

**Files:**
- Create: `rig/ssh.py`
- Create: `rig/rig.py`
- Modify: `rig/registry.py` (append `suite_needs` at the end of the file)
- Modify: `rig/__main__.py` (full replacement; the report, compare, and publish commands of Tasks 6 and 7 keep their arguments)
- Modify: `terraform/main.tf:1-3`, `terraform/main.tf:89`, `terraform/main.tf:104-146`
- Modify: `terraform/variables.tf` (full replacement)
- Modify: `terraform/outputs.tf` (full replacement)
- Modify: `terraform/versions.tf:2`
- Create: `terraform/backend.tf.s3`
- Delete: `scripts/nuke.sh`
- Create: `terraform/nuke.sh` (`git mv` of `scripts/nuke.sh`, then edit lines 4 and 9 of the moved file)
- Create: `box/build-local.sh`
- Modify: `Makefile` (full replacement)
- Modify: `.gitignore` (append at the end)
- Test: `tests/test_rig.py`

**Interfaces:**
- Consumes: `Suite`, `Target`, `load_targets(path)`, `load_suite(path, targets, loader_count)` from `rig/registry.py` (Task 1); `render(run) -> (text, status)` from `rig/report.py` (Task 6); `compare(a, b, *, force)` from `rig/compare.py` and `publish(run, pages_dir)` from `rig/publish.py` (Task 7); `box/provision-server.sh` with the environment `NIGHTLY`, `REF`, `BASE_REF`, `NEEDS`, `box/provision-loader.sh`, and `box/lock-apps.sh` (Task 12); `apps/grpc/buf.gen.yaml` and `apps/grpc/fixtures.py` (Task 8); `/opt/bench/meta.json` (Task 12): `{"rapira": R, "base": R | null, "kernel": str}` where R has the keys `ref`, `sha`, `version`, `build`, `asset`, `binary_sha256`, `rustflags`, `dir`; the rapira executable at `<BINARY_DIR>/bin/rapira` (Tasks 9 and 12); the Rust toolchain at `$HOME/.cargo/env` on a server that provisioning built with `REF` (Task 12); the cloud-init TTL files `/etc/rapira-bench-deadline` and `/usr/local/sbin/rapira-bench-ttl-arm` (unchanged `terraform/cloud-init/bootstrap.sh`).
- Produces: `rig/ssh.py`: `Host`, `SshError`, `run(host, cmd, *, timeout=None, stdin: bytes | None = None) -> str`, `run_many(jobs, *, timeout=None)`, `stage_tree(hosts)`, `copy_from(host, remote, local)`, plus `USER = "fedora"`, `KNOWN_HOSTS`, `RIG_DIR = "bench-rig"`, `OPTIONS`, `set_key(path: Path) -> None`, `ssh_argv(host, cmd) -> list[str]`, `wait_ssh(host, *, tries=60, delay_s=5) -> None`, `tree_files(root: Path) -> list[str]`, `tree_tar(root: Path) -> bytes`, `stage_dir(root: Path, host: Host, dest: str) -> None`. `rig/rig.py`: `Rig` with the extra property `hosts -> list[Host]` (server first), `from_terraform(tf_dir)`, `remaining_ttl_s(host)`, `arm_ttl(hosts, minutes)`, `ensure_ttl(hosts, needed_s)`, plus `provision(rig: Rig, *, ttl_min: int, server_env: dict[str, str]) -> None`. `rig/registry.py`: `suite_needs(suite: Suite) -> list[str]` (sorted server kinds, then sorted apps). CLI: `python3 -m rig needs --suite NAME`, `python3 -m rig provision --ttl MIN --needs LIST [--nightly SHA7] [--ref REF] [--base-ref REF] [--frame-pointers 0|1]`, `python3 -m rig sync [--src DIR]`, `python3 -m rig ttl [--set MINUTES]`; `rig/__main__.py` constants `TF_DIR`, `SUITES_DIR`, `TARGETS_FILE` and `main(argv=None) -> int`. Makefile knobs of the contract plus `REGION`, `RUN`, `A`, `B`; with `TF_BACKEND=s3` the S3 settings come from `TF_CLI_ARGS_init`. `terraform/nuke.sh` (tag-scoped teardown, `REGION` from the environment). `box/build-local.sh` installs `/opt/bench/rapira/local/bin/rapira` and replaces the `rapira` record of `/opt/bench/meta.json` with `sha = "local"` and `dir = "/opt/bench/rapira/local"`, so the bench selects `/opt/bench/rapira/local`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_rig.py`:

```python
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rig import rig as rigmod
from rig import ssh
from rig.registry import Suite, Target, suite_needs
from rig.rig import Rig
from rig.ssh import Host, SshError

SERVER = Host("server", "3.0.0.1", "10.0.0.1")
LOADER_1 = Host("loader-1", "3.0.0.2", "10.0.0.2")
LOADER_2 = Host("loader-2", "3.0.0.3", "10.0.0.3")


def tf_outputs(loader_public, loader_private):
    values = {
        "server_public_ip": "3.0.0.1",
        "server_private_ip": "10.0.0.1",
        "loader_public_ips": loader_public,
        "loader_private_ips": loader_private,
        "ami_id": "ami-0123",
        "server_instance_type": "c7a.8xlarge",
        "loader_instance_type": "c7a.xlarge",
        "loader_count": len(loader_public),
        "key_file": "./rig-key.pem",
        "placement_group": "rapira-bench",
    }
    return json.dumps({name: {"sensitive": False, "type": "string", "value": value} for name, value in values.items()})


FROM_TERRAFORM_CASES = [
    {
        "name": "two loaders keep the output order",
        "stdout": tf_outputs(["3.0.0.2", "3.0.0.3"], ["10.0.0.2", "10.0.0.3"]),
        "returncode": 0,
        "key": True,
        "loaders": (LOADER_1, LOADER_2),
        "error": None,
    },
    {
        "name": "one loader",
        "stdout": tf_outputs(["3.0.0.2"], ["10.0.0.2"]),
        "returncode": 0,
        "key": True,
        "loaders": (LOADER_1,),
        "error": None,
    },
    {
        "name": "no outputs before make up",
        "stdout": "{}",
        "returncode": 0,
        "key": True,
        "loaders": None,
        "error": "run 'make up' first",
    },
    {
        "name": "terraform fails without state",
        "stdout": "",
        "returncode": 1,
        "key": True,
        "loaders": None,
        "error": "run 'make up' first",
    },
    {
        "name": "key file missing",
        "stdout": tf_outputs(["3.0.0.2"], ["10.0.0.2"]),
        "returncode": 0,
        "key": False,
        "loaders": None,
        "error": "rig-key.pem is missing",
    },
]

REMAINING_TTL_CASES = [
    {"name": "deadline in the future", "reply": "1600\n", "now": 1000.4, "expected": 600},
    {"name": "deadline passed", "reply": "900\n", "now": 1000.0, "expected": -100},
    {"name": "deadline unreadable over ssh", "reply": SshError("server: exit 1"), "now": 1000.0, "expected": 0},
    {"name": "deadline file is not a number", "reply": "\n", "now": 1000.0, "expected": 0},
]

ENSURE_TTL_CASES = [
    {
        "name": "extend only the short host",
        "left": {"server": 600, "loader-1": 100},
        "needed": 300,
        "auto_extend": "1",
        # 300 // 60 + 15 = 20 minutes.
        "armed": [(["loader-1"], 20)],
        "error": None,
    },
    {
        "name": "equal to the need is enough",
        "left": {"server": 300, "loader-1": 300},
        "needed": 300,
        "auto_extend": "1",
        "armed": [],
        "error": None,
    },
    {
        "name": "long run adds the margin to the need",
        "left": {"server": 0, "loader-1": 0},
        "needed": 3000,
        "auto_extend": "1",
        # 3000 // 60 + 15 = 65 minutes on each host.
        "armed": [(["server"], 65), (["loader-1"], 65)],
        "error": None,
    },
    {
        "name": "AUTO_EXTEND=0 makes a short TTL an error",
        "left": {"server": 600, "loader-1": 100},
        "needed": 300,
        "auto_extend": "0",
        "armed": [],
        "error": "TTL on loader-1 expires in 100 s",
    },
]

RUN_CASES = [
    {"name": "stdout on exit 0", "returncode": 0, "stdout": b"8\n", "stderr": b"", "timeout": False, "result": "8\n", "error": None},
    {"name": "exit 255 names the host and keeps stderr", "returncode": 255, "stdout": b"", "stderr": b"Connection refused\n", "timeout": False, "result": None, "error": "loader-1: exit 255: nproc\nConnection refused"},
    {"name": "stdout replaces an empty stderr", "returncode": 1, "stdout": b"ERROR: port 8080 busy\n", "stderr": b"", "timeout": False, "result": None, "error": "loader-1: exit 1: nproc\nERROR: port 8080 busy"},
    {"name": "timeout", "returncode": 0, "stdout": b"", "stderr": b"", "timeout": True, "result": None, "error": "loader-1: timeout after 5 s: nproc"},
]

NEEDS_CASES = [
    {
        "name": "servers first then apps, each sorted without repeats",
        "targets": [
            ("symfony", "php-fpm"),
            ("hello", "rapira"),
            ("hello", "frankenphp"),
            ("grpc", "roadrunner"),
            ("symfony", "rapira"),
        ],
        "expected": ["frankenphp", "php-fpm", "rapira", "roadrunner", "grpc", "hello", "symfony"],
    },
    {
        "name": "one rapira target",
        "targets": [("hello", "rapira")],
        "expected": ["rapira", "hello"],
    },
]

PROVISION_CASES = [
    {
        "name": "nightly with a quoted needs list",
        "env": {"NIGHTLY": "abc1234", "REF": "", "BASE_REF": "main", "NEEDS": "frankenphp rapira hello"},
        "results": ["ok", "ok", "ok"],
        "server_cmd": "NIGHTLY=abc1234 REF='' BASE_REF=main NEEDS='frankenphp rapira hello' bash bench-rig/box/provision-server.sh",
        "error": None,
    },
    {
        "name": "a failed loader fails the provisioning",
        "env": {"NIGHTLY": "", "REF": "pr/97", "BASE_REF": "main", "NEEDS": "rapira hello"},
        "results": ["ok", "ok", SshError("loader-2: exit 1: bash bench-rig/box/provision-loader.sh")],
        "server_cmd": "NIGHTLY='' REF=pr/97 BASE_REF=main NEEDS='rapira hello' bash bench-rig/box/provision-server.sh",
        "error": "loader-2: exit 1",
    },
]


def target(app, server):
    return Target(
        name=f"{app}-{server}-worker", server=server, app=app, mode="worker", proto="http1", binary=None,
        start=(), url="/", expect="apps/hello/expect.txt", config="",
    )


class FromTerraformTest(unittest.TestCase):
    def test_from_terraform(self):
        for case in FROM_TERRAFORM_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                tf_dir = Path(tmp)
                if case["key"]:
                    (tf_dir / "rig-key.pem").write_text("key")
                done = subprocess.CompletedProcess([], case["returncode"], stdout=case["stdout"], stderr="")
                with mock.patch("rig.rig.subprocess.run", return_value=done), mock.patch("rig.rig.ssh.set_key") as set_key:
                    if case["error"]:
                        with self.assertRaisesRegex(RuntimeError, case["error"]):
                            rigmod.from_terraform(tf_dir)
                        continue
                    rig = rigmod.from_terraform(tf_dir)
                self.assertEqual(rig.server, SERVER)
                self.assertEqual(rig.loaders, case["loaders"])
                self.assertEqual((rig.server_type, rig.loader_type, rig.ami_id), ("c7a.8xlarge", "c7a.xlarge", "ami-0123"))
                self.assertEqual(rig.key_file, tf_dir / "rig-key.pem")
                set_key.assert_called_once_with(tf_dir / "rig-key.pem")


class TtlTest(unittest.TestCase):
    def test_remaining_ttl_s(self):
        for case in REMAINING_TTL_CASES:
            with self.subTest(name=case["name"]):
                reply = case["reply"]
                effect = reply if isinstance(reply, Exception) else None
                with mock.patch("rig.rig.ssh.run", return_value=reply, side_effect=effect), mock.patch("rig.rig.time.time", return_value=case["now"]):
                    self.assertEqual(rigmod.remaining_ttl_s(SERVER), case["expected"])

    def test_ensure_ttl(self):
        for case in ENSURE_TTL_CASES:
            with self.subTest(name=case["name"]):
                armed = []
                with mock.patch.dict(os.environ, {"AUTO_EXTEND": case["auto_extend"]}), \
                        mock.patch("rig.rig.remaining_ttl_s", side_effect=lambda host: case["left"][host.name]), \
                        mock.patch("rig.rig.arm_ttl", side_effect=lambda hosts, minutes: armed.append(([h.name for h in hosts], minutes))):
                    if case["error"]:
                        with self.assertRaisesRegex(RuntimeError, case["error"]):
                            rigmod.ensure_ttl([SERVER, LOADER_1], case["needed"])
                    else:
                        rigmod.ensure_ttl([SERVER, LOADER_1], case["needed"])
                self.assertEqual(armed, case["armed"])


class SshTest(unittest.TestCase):
    def test_ssh_argv(self):
        ssh.set_key(Path("terraform/rig-key.pem"))
        self.assertEqual(ssh.ssh_argv(LOADER_1, "nproc"), [
            "ssh", "-i", "terraform/rig-key.pem", "-o", "User=fedora",
            "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=.ssh-known-hosts",
            "-o", "ControlMaster=auto", "-o", "ControlPath=.ssh-cm-%h", "-o", "ControlPersist=10m",
            "-o", "ConnectTimeout=5", "-o", "LogLevel=ERROR", "3.0.0.2", "nproc",
        ])

    def test_run(self):
        for case in RUN_CASES:
            with self.subTest(name=case["name"]):
                if case["timeout"]:
                    patch = mock.patch("rig.ssh.subprocess.run", side_effect=subprocess.TimeoutExpired("ssh", 5))
                else:
                    done = subprocess.CompletedProcess([], case["returncode"], stdout=case["stdout"], stderr=case["stderr"])
                    patch = mock.patch("rig.ssh.subprocess.run", return_value=done)
                with patch:
                    if case["error"]:
                        with self.assertRaises(SshError) as caught:
                            ssh.run(LOADER_1, "nproc", timeout=5)
                        self.assertEqual(str(caught.exception), case["error"])
                    else:
                        self.assertEqual(ssh.run(LOADER_1, "nproc", timeout=5), case["result"])

    def test_run_many_keeps_job_order_and_errors(self):
        failure = SshError("loader-1: exit 1: nproc")

        def fake_run(host, cmd, *, timeout=None):
            if host is LOADER_1:
                raise failure
            return host.name

        with mock.patch("rig.ssh.run", side_effect=fake_run):
            results = ssh.run_many([(SERVER, "nproc"), (LOADER_1, "nproc"), (LOADER_2, "nproc")])
        self.assertEqual(results, ["server", failure, "loader-2"])

    def test_tree_files_skip_ignored_and_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text("*.log\n")
            (root / "kept.txt").write_text("a")
            (root / "gone.txt").write_text("b")
            subprocess.run(["git", "-C", str(root), "add", ".gitignore", "kept.txt", "gone.txt"], check=True)
            (root / "gone.txt").unlink()
            (root / "new.txt").write_text("c")
            (root / "run.log").write_text("d")
            self.assertEqual(ssh.tree_files(root), [".gitignore", "kept.txt", "new.txt"])


class NeedsTest(unittest.TestCase):
    def test_suite_needs(self):
        for case in NEEDS_CASES:
            with self.subTest(name=case["name"]):
                suite = Suite(
                    name="t", rounds=1, stage_s=20, connections=256, smoke=False, floors={},
                    targets=tuple(target(app, server) for app, server in case["targets"]),
                )
                self.assertEqual(suite_needs(suite), case["expected"])


class ProvisionTest(unittest.TestCase):
    def test_provision(self):
        for case in PROVISION_CASES:
            with self.subTest(name=case["name"]):
                rig = Rig(SERVER, (LOADER_1, LOADER_2), "c7a.8xlarge", "c7a.xlarge", "ami-0123", Path("k"))
                jobs = []

                def fake_run_many(batch, *, timeout=None):
                    jobs.append([(host.name, cmd) for host, cmd in batch])
                    if batch[0][1].endswith("provision-server.sh"):
                        return case["results"]
                    return ["" for _ in batch]

                with mock.patch("rig.rig.ssh.wait_ssh"), mock.patch("rig.rig.ssh.stage_tree"), \
                        mock.patch("rig.rig.ssh.run_many", side_effect=fake_run_many), \
                        mock.patch("rig.rig.arm_ttl") as arm:
                    if case["error"]:
                        with self.assertRaisesRegex(SshError, case["error"]):
                            rigmod.provision(rig, ttl_min=60, server_env=case["env"])
                    else:
                        rigmod.provision(rig, ttl_min=60, server_env=case["env"])
                arm.assert_called_once_with([SERVER, LOADER_1, LOADER_2], 60)
                self.assertEqual(jobs[-1], [
                    ("server", case["server_cmd"]),
                    ("loader-1", "bash bench-rig/box/provision-loader.sh"),
                    ("loader-2", "bash bench-rig/box/provision-loader.sh"),
                ])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_rig -v`
Expected: FAIL with `ImportError: cannot import name 'rig' from 'rig'`

- [ ] **Step 3: Write `rig/ssh.py`**

```python
"""ssh and scp to the rig boxes."""

import io
import shlex
import subprocess
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

USER = "fedora"
KNOWN_HOSTS = ".ssh-known-hosts"
RIG_DIR = "bench-rig"
OPTIONS = (
    "StrictHostKeyChecking=accept-new",
    f"UserKnownHostsFile={KNOWN_HOSTS}",
    "ControlMaster=auto",
    "ControlPath=.ssh-cm-%h",
    "ControlPersist=10m",
    "ConnectTimeout=5",
    "LogLevel=ERROR",
)
TAIL_LINES = 20

_key = Path("terraform/rig-key.pem")


@dataclass(frozen=True)
class Host:
    name: str
    public_ip: str
    private_ip: str


class SshError(RuntimeError):
    """A remote command failed. The message names the host."""


def set_key(path: Path) -> None:
    """Use this private key for every later ssh and scp call."""
    global _key
    _key = path


def _options() -> list[str]:
    argv = ["-i", str(_key), "-o", f"User={USER}"]
    for option in OPTIONS:
        argv += ["-o", option]
    return argv


def ssh_argv(host: Host, cmd: str) -> list[str]:
    """The ssh command line that runs cmd on host."""
    return ["ssh", *_options(), host.public_ip, cmd]


def _tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-TAIL_LINES:])


def run(host: Host, cmd: str, *, timeout: float | None = None, stdin: bytes | None = None) -> str:
    """Run cmd on host and return its stdout. Raise SshError on a nonzero exit or a timeout."""
    # Without input the remote command reads /dev/null, so parallel calls do not read the terminal.
    feed = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    try:
        proc = subprocess.run(ssh_argv(host, cmd), capture_output=True, timeout=timeout, **feed)
    except subprocess.TimeoutExpired as exc:
        raise SshError(f"{host.name}: timeout after {timeout} s: {cmd}") from exc
    out = proc.stdout.decode(errors="replace")
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace")
        raise SshError(f"{host.name}: exit {proc.returncode}: {cmd}\n{_tail(err or out)}")
    return out


def run_many(jobs: list[tuple[Host, str]], *, timeout: float | None = None) -> list[str | SshError]:
    """Run the jobs in parallel. Each result is the stdout or the SshError, in job order."""

    def one(job: tuple[Host, str]) -> str | SshError:
        try:
            return run(job[0], job[1], timeout=timeout)
        except SshError as exc:
            return exc

    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        return list(pool.map(one, jobs))


def wait_ssh(host: Host, *, tries: int = 60, delay_s: float = 5) -> None:
    """Wait until host accepts ssh. Raise SshError after the last try."""
    for _ in range(tries - 1):
        try:
            run(host, "true", timeout=30)
            return
        except SshError:
            time.sleep(delay_s)
    run(host, "true", timeout=30)


def tree_files(root: Path) -> list[str]:
    """Tracked and untracked files of the git tree at root, without ignored and deleted files."""

    def git(*args: str) -> set[str]:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", *args], capture_output=True, check=True)
        return {name for name in out.stdout.decode().split("\0") if name}

    return sorted(git("-co", "--exclude-standard") - git("-d"))


def tree_tar(root: Path) -> bytes:
    """A gzip tar of the tree_files of root, with their mode bits."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in tree_files(root):
            tar.add(root / name, arcname=name, recursive=False)
    return buf.getvalue()


def _unpack(host: Host, data: bytes, dest: str) -> None:
    q = shlex.quote(dest)
    run(host, f"rm -rf {q} && mkdir -p {q} && tar -xzf - -C {q}", stdin=data)


def stage_dir(root: Path, host: Host, dest: str) -> None:
    """Replace ~/dest on host with the git tree at root."""
    _unpack(host, tree_tar(root), dest)


def stage_tree(hosts: list[Host]) -> None:
    """Copy this repository tree to ~/bench-rig on each host."""
    data = tree_tar(Path("."))
    for host in hosts:
        _unpack(host, data, RIG_DIR)


def copy_from(host: Host, remote: str, local: Path) -> None:
    """Copy one remote file to local with scp."""
    local.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["scp", "-q", *_options(), f"{host.public_ip}:{remote}", str(local)], capture_output=True)
    if proc.returncode != 0:
        raise SshError(f"{host.name}: scp {remote} failed\n{_tail(proc.stderr.decode(errors='replace'))}")
```

Run: `python3 -m py_compile rig/ssh.py && echo compiled`
Expected: `compiled`

- [ ] **Step 4: Write `rig/rig.py`**

```python
"""The rig from the terraform outputs, TTL handling, and provisioning."""

import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from rig import ssh
from rig.ssh import Host, SshError

DEADLINE_FILE = "/etc/rapira-bench-deadline"
TTL_ARM = "/usr/local/sbin/rapira-bench-ttl-arm"
TTL_MARGIN_MIN = 15


@dataclass(frozen=True)
class Rig:
    server: Host
    loaders: tuple[Host, ...]
    server_type: str
    loader_type: str
    ami_id: str
    key_file: Path

    @property
    def hosts(self) -> list[Host]:
        return [self.server, *self.loaders]


def from_terraform(tf_dir: Path) -> Rig:
    """Read the rig from `terraform output -json` and select its key for ssh."""
    proc = subprocess.run(["terraform", f"-chdir={tf_dir}", "output", "-json"], capture_output=True, text=True)
    outputs = json.loads(proc.stdout or "{}") if proc.returncode == 0 else {}
    if "server_public_ip" not in outputs:
        raise RuntimeError("no rig: terraform has no outputs; run 'make up' first")
    value = {name: item["value"] for name, item in outputs.items()}
    key_file = tf_dir / value["key_file"]
    if not key_file.is_file():
        raise RuntimeError(f"no rig: {key_file} is missing; run 'make up' first")
    loaders = tuple(
        Host(f"loader-{i}", public_ip, private_ip)
        for i, (public_ip, private_ip) in enumerate(zip(value["loader_public_ips"], value["loader_private_ips"]), start=1)
    )
    ssh.set_key(key_file)
    return Rig(
        server=Host("server", value["server_public_ip"], value["server_private_ip"]),
        loaders=loaders,
        server_type=value["server_instance_type"],
        loader_type=value["loader_instance_type"],
        ami_id=value["ami_id"],
        key_file=key_file,
    )


def remaining_ttl_s(host: Host) -> int:
    """Seconds until the TTL shutdown of host. 0 when the deadline is unreadable."""
    try:
        deadline = int(ssh.run(host, f"cat {DEADLINE_FILE}").strip())
    except (SshError, ValueError):
        return 0
    return deadline - int(time.time())


def arm_ttl(hosts: list[Host], minutes: int) -> None:
    """Set the TTL of every host to minutes from now."""
    for result in ssh.run_many([(host, f"sudo {TTL_ARM} {minutes}") for host in hosts]):
        if isinstance(result, SshError):
            raise result


def ensure_ttl(hosts: list[Host], needed_s: int) -> None:
    """Extend the TTL of each host that expires before needed_s. AUTO_EXTEND=0 makes a short TTL an error."""
    for host in hosts:
        left = remaining_ttl_s(host)
        if left >= needed_s:
            continue
        if os.environ.get("AUTO_EXTEND", "1") != "1":
            raise RuntimeError(f"TTL on {host.name} expires in {left} s, the run needs about {needed_s} s; run 'make extend TTL=<minutes>'")
        minutes = needed_s // 60 + TTL_MARGIN_MIN
        print(f"==> TTL on {host.name} has {left} s left, the run needs about {needed_s} s; extending to {minutes} min")
        arm_ttl([host], minutes)


def provision(rig: Rig, *, ttl_min: int, server_env: dict[str, str]) -> None:
    """Prepare every box, stage the tree, and run the provisioning scripts in parallel."""
    for host in rig.hosts:
        ssh.wait_ssh(host)
    for result in ssh.run_many([(host, "sudo cloud-init status --wait >/dev/null") for host in rig.hosts]):
        if isinstance(result, SshError):
            raise result
    arm_ttl(rig.hosts, ttl_min)
    ssh.stage_tree(rig.hosts)
    print(f"==> provisioning {len(rig.hosts)} boxes")
    env = " ".join(f"{name}={shlex.quote(value)}" for name, value in server_env.items())
    jobs = [(rig.server, f"{env} bash {ssh.RIG_DIR}/box/provision-server.sh")]
    jobs += [(loader, f"bash {ssh.RIG_DIR}/box/provision-loader.sh") for loader in rig.loaders]
    failed = [result for result in ssh.run_many(jobs) if isinstance(result, SshError)]
    if failed:
        raise SshError("provisioning failed; the rig still bills, fix and rerun 'make provision' or run 'make down'\n" + "\n".join(str(exc) for exc in failed))
```

Run: `python3 -m py_compile rig/rig.py && echo compiled`
Expected: `compiled`

- [ ] **Step 5: Append `suite_needs` to `rig/registry.py`**

Add at the end of `rig/registry.py`:

```python


def suite_needs(suite: Suite) -> list[str]:
    """Server kinds, then apps, of the suite targets. Provisioning installs only these."""
    return sorted({target.server for target in suite.targets}) + sorted({target.app for target in suite.targets})
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_rig -v`
Expected: PASS, `Ran 9 tests` and `OK`. The `==> TTL on ...` and `==> provisioning 3 boxes` lines on stdout come from the code under test.

- [ ] **Step 7: Replace `rig/__main__.py`**

Replace the whole file with this content. The `report`, `compare`, and `publish` commands keep the arguments of the contract CLI.

```python
"""Command line of the rig: python3 -m rig <command>."""

import argparse
import json
import sys
from pathlib import Path

from rig import ssh
from rig.compare import compare
from rig.publish import publish
from rig.registry import load_suite, load_targets, suite_needs
from rig.report import render
from rig.rig import arm_ttl, ensure_ttl, from_terraform, provision, remaining_ttl_s

TF_DIR = Path("terraform")
SUITES_DIR = Path("suites")
TARGETS_FILE = SUITES_DIR / "targets.toml"
SYNC_TTL_S = 1800


def load_json(path: str) -> dict:
    return json.loads(Path(path).read_text())


def cmd_report(args: argparse.Namespace) -> int:
    text, status = render(load_json(args.run))
    print(text, end="")
    return status


def cmd_compare(args: argparse.Namespace) -> int:
    text, status = compare(load_json(args.a), load_json(args.b), force=args.force)
    print(text, end="")
    return status


def cmd_publish(args: argparse.Namespace) -> int:
    print(publish(load_json(args.run), Path(args.pages_dir)))
    return 0


def cmd_needs(args: argparse.Namespace) -> int:
    # The loader count does not change the needs. The value 1 passes every loader check.
    suite = load_suite(SUITES_DIR / f"{args.suite}.toml", load_targets(TARGETS_FILE), 1)
    print(" ".join(suite_needs(suite)))
    return 0


def cmd_provision(args: argparse.Namespace) -> int:
    if not args.nightly and not args.ref:
        raise ValueError("set NIGHTLY=<sha7> or REF=<ref>, for example: make up REF=pr/97")
    env = {
        "NIGHTLY": args.nightly,
        "REF": args.ref,
        "BASE_REF": args.base_ref,
        "NEEDS": args.needs,
        "FRAME_POINTERS": args.frame_pointers,
    }
    provision(from_terraform(TF_DIR), ttl_min=args.ttl, server_env=env)
    print("==> rig ready; run: make bench")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    rig = from_terraform(TF_DIR)
    ensure_ttl([rig.server], SYNC_TTL_S)
    ssh.stage_tree([rig.server])
    ssh.stage_dir(Path(args.src), rig.server, "core-sync")
    print(ssh.run(rig.server, f"bash {ssh.RIG_DIR}/box/build-local.sh"), end="")
    return 0


def cmd_ttl(args: argparse.Namespace) -> int:
    rig = from_terraform(TF_DIR)
    if args.set:
        arm_ttl(rig.hosts, args.set)
        print(f"TTL set to {args.set} minutes on {len(rig.hosts)} boxes")
    for host in rig.hosts:
        print(f"{host.name} {host.public_ip}: TTL {remaining_ttl_s(host) // 60} min left")
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="python3 -m rig")
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("report", help="print the tables of one run file")
    p.add_argument("run")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("compare", help="print the deltas between two run files")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("publish", help="add one run file to a gh-pages checkout")
    p.add_argument("--pages-dir", required=True)
    p.add_argument("run")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("needs", help="print the server kinds and apps of a suite")
    p.add_argument("--suite", required=True)
    p.set_defaults(func=cmd_needs)

    p = sub.add_parser("provision", help="provision the server and the loaders")
    p.add_argument("--ttl", type=int, required=True)
    p.add_argument("--needs", required=True)
    p.add_argument("--nightly", default="")
    p.add_argument("--ref", default="")
    p.add_argument("--base-ref", default="main")
    p.add_argument("--frame-pointers", default="0", choices=("0", "1"))
    p.set_defaults(func=cmd_provision)

    p = sub.add_parser("sync", help="build a local rapira tree on the server")
    p.add_argument("--src", default="../core")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("ttl", help="print or set the TTL of every box")
    p.add_argument("--set", type=int, metavar="MINUTES")
    p.set_defaults(func=cmd_ttl)
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8: Verify the new commands**

Run: `python3 -m rig needs --suite ci`
Expected: `frankenphp php-fpm rapira roadrunner grpc hello laravel static symfony`

Run: `python3 -m rig --help`
Expected: the usage line lists `{report,compare,publish,needs,provision,sync,ttl}`.

Run: `python3 -m unittest discover -s tests -t .`
Expected: `OK`

- [ ] **Step 9: Commit the modules**

```bash
git add rig/ssh.py rig/rig.py rig/registry.py rig/__main__.py tests/test_rig.py
git commit -s -S -m "feat: add the ssh, rig, and provisioning commands"
```

- [ ] **Step 10: Edit `terraform/main.tf`**

A rig created by the old stack has the resource addresses `aws_instance.rig["server"]` and `aws_instance.rig["loader"]`. Destroy such a rig with `make down` before you apply this stack.

Replace lines 1-3:

```hcl
provider "aws" {
  profile = var.profile
  region  = var.region
```

with:

```hcl
provider "aws" {
  region = var.region
```

Replace line 89:

```hcl
    description = "bench traffic between the two boxes"
```

with:

```hcl
    description = "bench traffic between the rig boxes"
```

Replace lines 104-146 (the comment `# One definition for both boxes; only the instance type differs per role.` and the whole `resource "aws_instance" "rig"` block) with:

```hcl
resource "aws_instance" "server" {
  ami                                  = local.ami_id
  instance_type                        = var.server_instance_type
  subnet_id                            = data.aws_subnet.az.id
  vpc_security_group_ids               = [aws_security_group.rig.id]
  key_name                             = aws_key_pair.rig.key_name
  placement_group                      = aws_placement_group.rig.name
  associate_public_ip_address          = true
  user_data                            = local.user_data
  instance_initiated_shutdown_behavior = "terminate"

  root_block_device {
    # The Fedora cloud AMI root is 5 GiB; two release builds plus debuginfo need more.
    volume_size = 40
    volume_type = "gp3"
  }

  metadata_options {
    http_tokens = "required"
  }

  tags = {
    Name = "rapira-bench-server"
    Role = "server"
  }

  # Dependents are destroyed first, so this edge makes destroy terminate the
  # instances before it deletes the local key file: a partially failed
  # destroy must keep the ssh path to boxes that still bill.
  depends_on = [local_sensitive_file.key]

  lifecycle {
    # Fedora rebuilds the AMI daily and ami is ForceNew; a re-apply must not
    # replace a provisioned rig.
    ignore_changes = [ami]
  }
}

resource "aws_instance" "loader" {
  count = var.loader_count

  ami                                  = local.ami_id
  instance_type                        = var.loader_instance_type
  subnet_id                            = data.aws_subnet.az.id
  vpc_security_group_ids               = [aws_security_group.rig.id]
  key_name                             = aws_key_pair.rig.key_name
  placement_group                      = aws_placement_group.rig.name
  associate_public_ip_address          = true
  user_data                            = local.user_data
  instance_initiated_shutdown_behavior = "terminate"

  root_block_device {
    volume_size = 40
    volume_type = "gp3"
  }

  metadata_options {
    http_tokens = "required"
  }

  tags = {
    Name = "rapira-bench-loader-${count.index + 1}"
    Role = "loader"
  }

  depends_on = [local_sensitive_file.key]

  lifecycle {
    ignore_changes = [ami]
  }
}
```

Run: `terraform fmt -check terraform/main.tf && echo fmt-ok`
Expected: `fmt-ok`

- [ ] **Step 11: Replace the variables and outputs, raise the Terraform version, add the S3 backend file**

Replace `terraform/variables.tf` with:

```hcl
variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "az" {
  description = "Every instance shares this AZ. Cross-AZ traffic is billed; same-AZ private IPv4 is free."
  type        = string
  default     = "eu-central-1a"
}

variable "server_instance_type" {
  description = "The measured box. c7a has no SMT: one vCPU is one physical core; the 8xlarge network is a fixed 12.5 Gbps, no burst credits."
  type        = string
  default     = "c7a.8xlarge"
}

variable "loader_instance_type" {
  description = "One load generator. A c7a.xlarge moves at most about 1.1 Gbps of the hello workload, under its 1.562 Gbps baseline."
  type        = string
  default     = "c7a.xlarge"
}

variable "loader_count" {
  description = "The number of loaders. Every stage rate and the connection count are split evenly over them."
  type        = number
  default     = 4

  validation {
    condition     = var.loader_count >= 1
    error_message = "loader_count must be 1 or more."
  }
}

variable "ami_id" {
  description = "Pin an AMI for the life of a baseline set. Null selects the newest Fedora 44 cloud image, which Fedora rebuilds daily."
  type        = string
  default     = null
}

variable "ssh_cidr" {
  description = "CIDR allowed to ssh. The Makefile injects the operator IP; the default allows nothing real so destroy never prompts."
  type        = string
  default     = "127.0.0.1/32"
}
```

Replace `terraform/outputs.tf` with:

```hcl
output "server_public_ip" {
  value = aws_instance.server.public_ip
}

output "server_private_ip" {
  value = aws_instance.server.private_ip
}

output "loader_public_ips" {
  value = aws_instance.loader[*].public_ip
}

output "loader_private_ips" {
  value = aws_instance.loader[*].private_ip
}

# The AMI the boxes actually run, not the data source's newest image.
output "ami_id" {
  value = aws_instance.server.ami
}

output "server_instance_type" {
  value = var.server_instance_type
}

output "loader_instance_type" {
  value = var.loader_instance_type
}

output "loader_count" {
  value = var.loader_count
}

output "key_file" {
  value = local_sensitive_file.key.filename
}

output "placement_group" {
  value = aws_placement_group.rig.name
}
```

In `terraform/versions.tf`, replace line 2 `  required_version = ">= 1.5"` with `  required_version = ">= 1.10"`. The S3 backend needs 1.10 for `use_lockfile`.

Create `terraform/backend.tf.s3`:

```hcl
# `make up TF_BACKEND=s3` copies this file to backend.tf. The bucket, key,
# region, and use_lockfile settings come from TF_CLI_ARGS_init.
terraform {
  backend "s3" {}
}
```

- [ ] **Step 12: Validate the stack**

Run: `terraform fmt -check -recursive terraform && echo fmt-ok`
Expected: `fmt-ok`

Run: `terraform -chdir=terraform init -backend=false -input=false >/dev/null && terraform -chdir=terraform validate`
Expected: `Success! The configuration is valid.`

Run: `cp terraform/backend.tf.s3 terraform/backend.tf && terraform -chdir=terraform validate; rm terraform/backend.tf`
Expected: `Success! The configuration is valid.`

- [ ] **Step 13: Move the nuke script and drop the profile**

Run: `git mv scripts/nuke.sh terraform/nuke.sh`

In `terraform/nuke.sh`, delete line 4:

```bash
PROFILE=${PROFILE:-Rustatian}
```

and replace line 9:

```bash
  command aws --profile "$PROFILE" --region "$REGION" "$@"
```

with:

```bash
  command aws --region "$REGION" "$@"
```

Run: `bash -n terraform/nuke.sh && grep -c PROFILE terraform/nuke.sh`
Expected: `0`

- [ ] **Step 14: Write `box/build-local.sh`**

```bash
#!/usr/bin/env bash
# Build the tree that `make sync` staged in ~/core-sync and install it as the
# rapira binary of the next bench run.
set -euo pipefail

SRC=$HOME/core-sync
DEST=/opt/bench/rapira/local
META=/opt/bench/meta.json

if [ ! -d "$SRC" ]; then
  echo "ERROR: $SRC missing; run 'make sync'"
  exit 1
fi
if [ ! -f "$HOME/.cargo/env" ]; then
  echo "ERROR: the server has no Rust toolchain; provision the rig with REF=<ref> before 'make sync'"
  exit 1
fi
# shellcheck disable=SC1091
. "$HOME/.cargo/env"

(cd "$SRC" && env \
  RUSTFLAGS="" \
  CARGO_PROFILE_RELEASE_DEBUG=line-tables-only \
  PHP_CONFIG=/usr/bin/php-config \
  CARGO_TARGET_DIR="$HOME/core-target" \
  cargo build --release)
install -d "$DEST/bin"
install -m 0755 "$HOME/core-target/release/rapira" "$DEST/bin/rapira"

# The driver selects /opt/bench/rapira/<first 7 characters of sha>, so the sha "local" selects DEST.
python3 - "$META" "$DEST" <<'PY'
import hashlib
import json
import sys

meta_path, dest = sys.argv[1:3]
with open(meta_path) as f:
    meta = json.load(f)
with open(dest + "/bin/rapira", "rb") as f:
    digest = hashlib.sha256(f.read()).hexdigest()
meta["rapira"] = {
    "ref": "local",
    "sha": "local",
    "version": "local",
    "build": "server",
    "asset": None,
    "binary_sha256": digest,
    "rustflags": "",
    "dir": dest,
}
with open(meta_path, "w") as f:
    json.dump(meta, f, indent=1)
    f.write("\n")
PY

echo "==> rapira rebuilt from the synced tree into $DEST"
```

Run: `bash -n box/build-local.sh && echo syntax-ok`
Expected: `syntax-ok`

- [ ] **Step 15: Replace the Makefile and extend `.gitignore`**

Replace `Makefile` with:

```make
# AWS bench rig for rapira. See README.md for the flow and the knobs.

SHELL := /bin/bash
.NOTPARALLEL:

REGION ?= eu-central-1
SERVER_TYPE ?= c7a.8xlarge
LOADER_TYPE ?= c7a.xlarge
LOADER_COUNT ?= 4
AZ ?= eu-central-1a
TTL ?= 60
REF ?=
BASE_REF ?= main
NIGHTLY ?=
SUITE ?= ci
ROUNDS ?=
PROCESSES ?=
AMI ?=
TF_BACKEND ?= local
# 1 builds rapira with frame pointers for a perf session. Server builds only.
FRAME_POINTERS ?= 0
RUN ?=
A ?=
B ?=
BUF_VERSION ?= v1.73.0
BUF ?= go run github.com/bufbuild/buf/cmd/buf@$(BUF_VERSION)

TF := terraform -chdir=terraform
AWSC := aws --region $(REGION)

.PHONY: preflight up provision status bench report compare extend sync lock down nuke test grpc_fixtures

preflight:
	@$(AWSC) sts get-caller-identity >/dev/null 2>&1 || \
	  { echo "ERROR: AWS auth failed; run: aws sso login"; exit 1; }

# The chosen knobs persist in rig.auto.tfvars so every later terraform
# operation (provision, status, down) sees the applied values.
up: preflight
	@test -n "$(NIGHTLY)$(REF)" || { echo "ERROR: set NIGHTLY=<sha7> or REF=<ref>, for example: make up REF=pr/97"; exit 1; }
	@quota=$$($(AWSC) service-quotas get-service-quota --service-code ec2 --quota-code L-1216C47A --query Quota.Value --output text 2>/dev/null); \
	test -n "$$quota" || { echo "ERROR: could not read quota L-1216C47A; check the AWS permissions"; exit 1; }; \
	sv=$$($(AWSC) ec2 describe-instance-types --instance-types $(SERVER_TYPE) --query 'InstanceTypes[0].VCpuInfo.DefaultVCpus' --output text 2>/dev/null); \
	test -n "$$sv" || { echo "ERROR: unknown instance type $(SERVER_TYPE)"; exit 1; }; \
	lv=$$($(AWSC) ec2 describe-instance-types --instance-types $(LOADER_TYPE) --query 'InstanceTypes[0].VCpuInfo.DefaultVCpus' --output text 2>/dev/null); \
	test -n "$$lv" || { echo "ERROR: unknown instance type $(LOADER_TYPE)"; exit 1; }; \
	awk -v q=$$quota -v n=$$((sv + lv * $(LOADER_COUNT))) 'BEGIN { if (n > q) { printf "ERROR: the rig needs %d vCPUs, the account quota is %d (adjustable: quota L-1216C47A)\n", n, q; exit 1 } }'
	@ip=$$(curl -fs https://checkip.amazonaws.com) || \
	  { echo "ERROR: could not determine the operator IP"; exit 1; }; \
	{ echo "region = \"$(REGION)\""; \
	  echo "az = \"$(AZ)\""; \
	  echo "server_instance_type = \"$(SERVER_TYPE)\""; \
	  echo "loader_instance_type = \"$(LOADER_TYPE)\""; \
	  echo "loader_count = $(LOADER_COUNT)"; \
	  echo "ssh_cidr = \"$$ip/32\""; \
	  $(if $(AMI),echo "ami_id = \"$(AMI)\"";) } > terraform/rig.auto.tfvars
	@if [ "$(TF_BACKEND)" = s3 ]; then cp terraform/backend.tf.s3 terraform/backend.tf; else rm -f terraform/backend.tf; fi
	$(TF) init -input=false
	$(TF) apply -auto-approve -input=false || \
	  { echo "ERROR: apply failed; a partial rig may be billing, run 'make down'. On InsufficientInstanceCapacity retry with AZ=eu-central-1b or 1c."; exit 1; }
	@$(MAKE) --no-print-directory provision

provision:
	@needs=$$(python3 -m rig needs --suite $(SUITE)) && \
	python3 -m rig provision --ttl $(TTL) --needs "$$needs" --nightly "$(NIGHTLY)" --ref "$(REF)" --base-ref "$(BASE_REF)" --frame-pointers "$(FRAME_POINTERS)"

status: preflight
	@out=$$($(TF) output 2>/dev/null); \
	if [ -n "$$out" ]; then echo "$$out"; else echo "(no terraform outputs; rig not applied)"; fi
	@$(AWSC) ec2 describe-instances --filters Name=tag:Project,Values=rapira-bench \
	  Name=instance-state-name,Values=pending,running,shutting-down,stopping,stopped \
	  --query 'Reservations[].Instances[].{id:InstanceId,state:State.Name,type:InstanceType,role:Tags[?Key==`Role`]|[0].Value,ip:PublicIpAddress}' \
	  --output table
	@python3 -m rig ttl 2>/dev/null || true

bench:
	@python3 -m rig bench --suite $(SUITE) $(if $(ROUNDS),--rounds $(ROUNDS)) $(if $(PROCESSES),--processes $(PROCESSES))

# RUN selects a run directory; the default is the newest one under runs/.
report:
	@d="$(RUN)"; [ -n "$$d" ] || d=$$(ls -d runs/*/ 2>/dev/null | sort | tail -1); \
	test -n "$$d" || { echo "ERROR: no run in runs/"; exit 1; }; \
	python3 -m rig report "$${d%/}/run.json"

compare:
	@test -n "$(A)" && test -n "$(B)" || { echo "ERROR: set A=runs/<id> and B=runs/<id>"; exit 1; }
	@python3 -m rig compare "$(A)/run.json" "$(B)/run.json"

extend:
	@python3 -m rig ttl --set $(TTL)

# Build the local, possibly uncommitted ../core working tree on the server.
sync:
	@python3 -m rig sync --src ../core

lock:
	box/lock-apps.sh

down: preflight
	@$(TF) destroy -auto-approve -input=false || \
	  { echo "retrying: placement group deletion can lag instance termination"; sleep 30; $(TF) destroy -auto-approve -input=false; }
	@rm -f .ssh-known-hosts .ssh-cm-*

# Tag-scoped aws-cli teardown for lost tfstate or after the TTL fired.
# Recovery order after state loss: make nuke, then make up.
nuke: preflight
	@REGION=$(REGION) terraform/nuke.sh
	@rm -f .ssh-known-hosts .ssh-cm-*

test:
	python3 -m unittest discover -s tests -t .

# Local only: needs Go and network access to the buf remote plugins.
grpc_fixtures:
	$(BUF) build apps/grpc --as-file-descriptor-set -o apps/grpc/bench.binpb
	$(BUF) generate apps/grpc --template apps/grpc/buf.gen.yaml
	python3 apps/grpc/fixtures.py
```

Append to `.gitignore`:

```gitignore

# Local run output of `make bench`.
runs/
# Copied from backend.tf.s3 by `make up TF_BACKEND=s3`.
terraform/backend.tf
```

- [ ] **Step 16: Verify the Makefile**

Run: `make -n bench ROUNDS=3`
Expected: `python3 -m rig bench --suite ci --rounds 3`

Run: `make -n provision NIGHTLY=abc1234`
Expected:

```
needs=$(python3 -m rig needs --suite ci) && \
python3 -m rig provision --ttl 60 --needs "$needs" --nightly "abc1234" --ref "" --base-ref "main" --frame-pointers "0"
```

Run: `make -n compare A=runs/a B=runs/b | tail -1`
Expected: `python3 -m rig compare "runs/a/run.json" "runs/b/run.json"`

Run: `git check-ignore runs/x terraform/backend.tf`
Expected: `runs/x` and `terraform/backend.tf` on two lines.

- [ ] **Step 17: Commit the rig stack and the Makefile**

```bash
git add terraform/main.tf terraform/variables.tf terraform/outputs.tf terraform/versions.tf terraform/backend.tf.s3 terraform/nuke.sh box/build-local.sh Makefile .gitignore
git commit -s -S -m "feat!: replace the fixed rig pair with one server and N loaders"
```

### Task 14: Bench orchestration and CLI

**Files:**
- Create: `rig/bench.py`
- Modify: `rig/__main__.py` (three edits on the file of Task 13: the imports, the `cmd_bench` function, the `bench` subparser)
- Test: `tests/test_bench.py`

**Interfaces:**
- Consumes: `SuiteError`, `Target`, `Suite`, `plan_cells(suite)`, `cell_key(round_no, target)`, `load_suite`, `load_targets` (Task 1); `RATIO`, `MAX_STAGES`, `PASS_TOLERANCE`, `stage_rates(floor)`, `evaluate_stage(rate, merged)`, `cell_numbers(stages)` (Task 2); `ERROR_KEYS`, `parse_result(text, loader)`, `merge(records, stage_s)` with `MissingLoader` and `LateLoader` as `ValueError` subclasses whose messages name the loader (Task 3); `Snapshot`, `parse_snapshot`, `cpu_pct`, `ena_delta`, `stage_flags`, `stage_void`, `keepalive_flag`, `cell_flags` (Task 4); `RunFile(...)`, `add_cell`, `finish`, `write` (Task 5); `render(run)` (Task 6); `Host`, `SshError`, `RIG_DIR`, `run`, `run_many`, `copy_from`, `wait_ssh`, `stage_tree`, `tree_files` from `rig/ssh.py`, and `Rig`, `Rig.hosts`, `from_terraform`, `ensure_ttl` from `rig/rig.py` (Task 13); `rig/__main__.py` of Task 13 with `TF_DIR`, `SUITES_DIR`, `TARGETS_FILE`; the box protocol of the contract: `box/target.sh start|stop|probe|mem|log`, `box/probe.sh`, `box/load.sh wrk2|k6`, `box/snapshot.sh` (Tasks 9, 10, 11); `/opt/bench/meta.json` as `{"rapira": R, "base": R | null, "kernel": str}` (Task 12, or `box/build-local.sh` of Task 13), `/opt/bench/versions.json` (an object of version lines), `/opt/bench/php.ini`, and on each loader `/opt/bench/loader.json` with `wrk2_commit` and `k6_version` (Task 12); `box/probe.sh` and `box/load.sh` resolve a relative `EXPECT_FILE` or `BODY_FILE` under the staged rig (Tasks 9 and 11).
- Produces: `PORT = 8080`, `LEAD_S = 3`, `WARMUP_S = 10`, `Boxes`, `plan_run(rig, suite, *, processes, loader_threads) -> dict` (keys `cells`, `keys`, `rates`, `conns_per_loader`, `loader_threads`, `processes`), `run_suite(...) -> Path`, plus `BENCH_DIR = "/opt/bench"`, `K6_LATENCY_BUDGET_S = 0.005`, `SshBoxes`, `CellVoid`, `binary_dir(target, rapira) -> str` (`/opt/bench/rapira/<sha[:7]>` for `pr`, `/opt/bench/rapira/base` for `base`, `-` for other servers), `server_versions(boxes, server) -> dict` (the content of `/opt/bench/versions.json` plus `php_ini`), `app_hashes(root) -> dict[str, str]`, `load_cmd(target, url, epoch, rate, plan, duration_s) -> str`. The run file `rapira` section is the `rapira` record R of `meta.json` plus the key `base` with the base record or null. The URL that `probe.sh` and `load.sh` receive is `http://<server private ip>:8080<target.url>` for both protocols; `load.sh k6` gets `VUS = max(connections per loader, ceil(rate per loader * 0.005))`. Run file shapes: cell `key`, `target` (registry fields, `start` as a list, `headers` as an object), `round`, `status`, `reason` (void and incomplete only), `flags`, `held`, `peak`, `unloaded`, `stages`; stage `rate`, `duration_s`, `pass`, `fail_reason` (failing stage only), `merged` (`requests`, `successful`, `bytes`, `errors`, `achieved_rps`, `successful_rps`), `latency_us`, `loaders` (one record per loader with `loader`, `tool`, `late_ms`, `requests`, `bytes`, `errors`, `latency_us`, `requests_per_sec`, `busy_cpu`, `ena`), `server` (`busy_cpu`, `pss_kb`, `established`, `time_wait`), `flags`; top-level `rig` (`server_type`, `loader_type`, `loader_count`, `az`, `ami`, `kernel`, `placement_group`, `server_instance_id`), `suite` (`name`, `file_sha256`, `rounds`, `stage_s`, `connections`), `loaders` (`name`, `instance_id`, `private_ip`, `kernel`, `wrk2` (the commit), `k6` (the version line)), `ladder` (`stages` per app, `stage_s`, `ratio`, `max_stages`, `pass_tolerance`, `connections`, `conns_per_loader`, `loader_threads`, `lead_s`, `warmup_s`). Cell flags add `died` and `ladder_exhausted`. CLI: `python3 -m rig bench --suite NAME [--rounds N] [--processes N] [--smoke] [--out runs]`, exit status of `render`, 130 on Ctrl-C.

- [ ] **Step 1: Write the failing test**

Create `tests/test_bench.py`:

```python
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rig.bench import WARMUP_S, load_cmd, plan_run, run_suite
from rig.merge import ERROR_KEYS
from rig.registry import Suite, SuiteError, Target
from rig.rig import Rig
from rig.ssh import Host, SshError

SERVER = Host("server", "3.0.0.1", "10.0.0.1")
LOADERS = tuple(Host(f"loader-{i}", f"3.0.0.{i + 1}", f"10.0.0.{i + 1}") for i in range(1, 5))
RIG = Rig(SERVER, LOADERS, "c7a.8xlarge", "c7a.xlarge", "ami-0123", Path("key"))
RAPIRA = {"ref": "main", "sha": "abc1234def", "version": "0.9.0", "build": "nightly"}
THREADS = 4

WORKER = Target(
    name="hello-rapira-worker", server="rapira", app="hello", mode="worker", proto="http1", binary="pr",
    start=("worker", "@RIG@/apps/hello/worker.php"), url="/?name=you", expect="apps/hello/expect.txt",
    config="servers/rapira/http.toml.tpl",
)
FPM = Target(
    name="hello-php-fpm", server="php-fpm", app="hello", mode="classic", proto="http1", binary=None,
    start=("@RIG@/apps/hello", "fpm.php"), url="/?name=you", expect="apps/hello/expect.txt",
    config="servers/php-fpm/php-fpm.conf.tpl",
)

BOX = "bash bench-rig/box/"
START = BOX + "target.sh start"
PIDS = BOX + "target.sh probe"
STOP = BOX + "target.sh stop"
LOG = BOX + "target.sh log"
PROBE = BOX + "probe.sh"
LOAD = BOX + "load.sh"
SNAPSHOT = BOX + "snapshot.sh"
SAMPLE = "sleep "
FACTS = "echo kernel="

# One stage per loader: 20 s at the floor 10000 split over 4 loaders is 2500 req/s,
# so 50000 requests per loader. The second stage asks 5000 req/s per loader.
FULL = 50000
SHORT = 90000  # 4 x 90000 / 20 s = 18000 req/s, under 95% of 20000


def suite(targets, connections=256):
    return Suite(
        name="test", rounds=1, stage_s=20, connections=connections, smoke=False,
        floors={"hello": 10000}, targets=tuple(targets),
    )


def result_line(requests, late_ms=0):
    body = {
        "tool": "wrk2", "late_ms": late_ms, "duration_us": 20000000, "requests": requests, "bytes": requests * 126,
        "errors": {key: 0 for key in ERROR_KEYS},
        "latency_us": {"mean": 700.0, "p50": 690, "p90": 1100, "p95": 1170, "p99": 1260, "p999": 1350, "max": 2800},
        "requests_per_sec": requests / 20,
    }
    return f"Running 20s test\nRESULT {json.dumps(body)}\n"


def load_reply(late_ms=0):
    """A loader that holds 2500 req/s and reaches 90000 requests at 5000 req/s."""

    def reply(cmd):
        rate = int(shlex.split(cmd)[4])
        return result_line(FULL if rate <= 2500 else SHORT, late_ms)

    return reply


def counter(busy_step, total_step, extra=""):
    """Growing /proc/stat counters: every window between two calls has the same busy percent."""
    state = {"n": 0}

    def reply(cmd):
        state["n"] += 1
        return f"cpu {busy_step * state['n']} {total_step * state['n']}\n{extra}"

    return reply


def replies(overrides):
    table = {
        ("*", FACTS): "kernel=6.17.1\ninstance_id=i-0abc\naz=eu-central-1a\nplacement_group=rapira-bench\nwrk2=44a94c17d8e6a0bac8559b53da76848e430cb7a7\nk6=k6 v2.2.0\n",
        ("server", START): "config=/opt/bench/run/r1-hello-rapira-worker.toml\npid=100\n",
        ("server", PIDS): "101 102\n4096\n",
        ("server", STOP): "",
        ("server", LOG): "",
        ("server", SAMPLE): "cpu 1 2\nconns 256 3\n204800\n",
        # Server 50% busy in every stage.
        ("server", SNAPSHOT): counter(50, 100, "conns 256 0\n"),
        # Loaders 90% busy in every stage.
        ("*", SNAPSHOT): counter(90, 100),
        ("*", PROBE): "",
        ("*", LOAD): load_reply(),
    }
    for host in LOADERS:
        table[(host.name, SNAPSHOT)] = counter(90, 100)
    table.update(overrides)
    return table


class FakeBoxes:
    """Replies by (host name, command prefix); "*" matches any host. A list reply is used in order."""

    def __init__(self, table):
        self.table = table
        self.calls = []
        self.copies = []

    def reply(self, host, cmd):
        self.calls.append((host.name, cmd))
        for name in (host.name, "*"):
            for (key_host, prefix), value in self.table.items():
                if key_host == name and cmd.startswith(prefix):
                    if isinstance(value, list):
                        value = value.pop(0) if len(value) > 1 else value[0]
                    if isinstance(value, BaseException):
                        raise value
                    return value(cmd) if callable(value) else value
        raise AssertionError(f"no reply for {host.name}: {cmd}")

    def run(self, host, cmd, timeout=None):
        return self.reply(host, cmd)

    def run_many(self, jobs, timeout=None):
        results = []
        for host, cmd in jobs:
            try:
                results.append(self.reply(host, cmd))
            except SshError as exc:
                results.append(exc)
        return results

    def copy_from(self, host, remote, local):
        self.copies.append((host.name, remote))
        local.write_text(f"copy of {remote}\n")


def label(cmd):
    if cmd.startswith(LOAD):
        return "warmup" if shlex.split(cmd)[7] == str(WARMUP_S) else "load"
    for prefix, name in ((START, "start"), (PIDS, "pids"), (STOP, "stop"), (LOG, "log"), (PROBE, "probe"),
                         (SNAPSHOT, "snapshot"), (SAMPLE, "sample"), (FACTS, "facts")):
        if cmd.startswith(prefix):
            return name
    return cmd


STAGE = ["snapshot"] * 5 + ["load"] * 4 + ["sample"] + ["snapshot"] * 5

CELL_CASES = [
    {
        "name": "pass then fail",
        "overrides": {},
        "status": "ok",
        "reason": None,
        "held": {"rate": 10000, "stage": 0},
        "peak": 18000.0,
        "stages": 2,
        # The failing stage has every loader at 90% and the server at 50%.
        "flags": {"generator_bound": True},
        "loads": 8,
    },
    {
        "name": "probe mismatch on loader-2 voids before load",
        "overrides": {("loader-2", PROBE): SshError("loader-2: exit 1: probe")},
        "status": "void",
        "reason": "probe mismatch on loader-2",
        "held": None,
        "peak": None,
        "stages": 0,
        "flags": {},
        "loads": 0,
    },
    {
        "name": "missing RESULT from loader-3 voids with the loader name",
        "overrides": {("loader-3", LOAD): "wrk2: panic: bytecode\n"},
        "status": "void",
        "reason": "loader-3",
        "held": None,
        "peak": None,
        "stages": 0,
        "flags": {},
        "loads": 4,
    },
    {
        "name": "loader-1 late by 1500 ms voids",
        "overrides": {("loader-1", LOAD): load_reply(late_ms=1500)},
        "status": "void",
        "reason": "loader-1",
        "held": None,
        "peak": None,
        "stages": 0,
        "flags": {},
        "loads": 4,
    },
    {
        "name": "failed probe after the failing stage sets died",
        "overrides": {("loader-1", PROBE): ["", SshError("loader-1: exit 1: probe")]},
        "status": "ok",
        "reason": None,
        "held": {"rate": 10000, "stage": 0},
        "peak": 18000.0,
        "stages": 2,
        "flags": {"generator_bound": True, "died": True},
        "loads": 8,
    },
    {
        "name": "a WARN line in the rapira log voids",
        "overrides": {("server", LOG): "2026-09-25T10:00:00Z WARN worker restarted\n"},
        "status": "void",
        "reason": "server log: 1 warn or error lines",
        "held": None,
        "peak": None,
        "stages": 2,
        "flags": {"generator_bound": True},
        "loads": 8,
    },
]


def bench(boxes, out, targets, connections=256):
    with mock.patch("rig.bench.ensure_ttl") as ttl:
        path = run_suite(
            RIG, suite(targets, connections), boxes, out, processes=32, run_id="run1", rapira=RAPIRA,
            servers={}, apps={}, loader_threads=THREADS,
        )
    return path, ttl


class RunSuiteTest(unittest.TestCase):
    def test_cells(self):
        for case in CELL_CASES:
            with self.subTest(name=case["name"]), tempfile.TemporaryDirectory() as tmp:
                boxes = FakeBoxes(replies(case["overrides"]))
                path, _ = bench(boxes, Path(tmp), [WORKER])
                run = json.loads(path.read_text())
                cell = run["cells"][0]
                self.assertEqual(cell["status"], case["status"])
                if case["reason"]:
                    self.assertIn(case["reason"], cell["reason"])
                self.assertEqual(cell["held"], case["held"])
                self.assertEqual(cell["peak"], case["peak"])
                self.assertEqual(len(cell["stages"]), case["stages"])
                self.assertEqual(cell["flags"], case["flags"])
                self.assertEqual(sum(1 for _, cmd in boxes.calls if label(cmd) == "load"), case["loads"])
                self.assertEqual([label(cmd) for _, cmd in boxes.calls][-2:], ["stop", "log"])
                self.assertEqual(run["status"], "complete" if case["status"] == "ok" else "incomplete")

    def test_command_order_and_raw_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({}))
            path, ttl = bench(boxes, Path(tmp), [WORKER])
            expected = (
                ["facts"] * 5 + ["start", "pids"] + ["probe"] * 4 + ["warmup"] * 4 + STAGE + STAGE
                + ["probe", "pids", "stop", "log"]
            )
            self.assertEqual([label(cmd) for _, cmd in boxes.calls], expected)
            self.assertEqual(boxes.calls[5], ("server", "bash bench-rig/box/target.sh start r1-hello-rapira-worker rapira 32 /opt/bench/rapira/abc1234 worker @RIG@/apps/hello/worker.php"))
            # Stage 1 asks 20000 req/s: 5000 per loader, 4 threads, 256 / 4 = 64 connections.
            self.assertEqual(
                shlex.split(boxes.calls[37][1])[4:],
                ["5000", "4", "64", "20", "http://10.0.0.1:8080/?name=you", "GET", "-"],
            )
            # probe.sh resolves a relative expect file under the staged rig.
            self.assertEqual(
                shlex.split(boxes.calls[7][1]),
                ["bash", "bench-rig/box/probe.sh", "http://10.0.0.1:8080/?name=you", "apps/hello/expect.txt", "http1", "GET", "-"],
            )
            raw = path.parent / "raw" / "r1-hello-rapira-worker"
            self.assertEqual(sorted(p.name for p in raw.iterdir()), sorted(
                [f"{stage}-loader-{i}.txt" for stage in (0, 1) for i in range(1, 5)]
                + ["config.toml", "server.log", "snapshots.txt"]
            ))
            self.assertEqual(boxes.copies, [("server", "/opt/bench/run/r1-hello-rapira-worker.toml")])
            # One cell: 1 * (8 * 20 + 60) + 300 seconds.
            ttl.assert_called_once_with([SERVER, *LOADERS], 520)

    def test_interrupt_stops_the_target_and_writes_an_incomplete_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({("loader-2", LOAD): [load_reply(), KeyboardInterrupt()]}))
            with self.assertRaises(KeyboardInterrupt):
                bench(boxes, Path(tmp), [WORKER, FPM])
            run = json.loads((Path(tmp) / "run1" / "run.json").read_text())
            self.assertEqual(run["status"], "incomplete")
            self.assertEqual(run["plan"], ["r1-hello-rapira-worker", "r1-hello-php-fpm"])
            self.assertEqual([(c["key"], c["status"]) for c in run["cells"]], [("r1-hello-rapira-worker", "incomplete")])
            self.assertIn(("server", "bash bench-rig/box/target.sh stop r1-hello-rapira-worker rapira"), boxes.calls)


GRPCWEB = Target(
    name="grpc-rapira-grpcweb", server="rapira", app="grpc", mode="dispatcher", proto="http1", binary="pr",
    start=("grpc", "@RIG@/apps/grpc/php/dispatcher.php"), url="/bench.v1.EchoService/Echo",
    expect="apps/grpc/expect.grpcweb", config="servers/rapira/grpc.toml.tpl", method="POST",
    headers=(("content-type", "application/grpc-web+proto"), ("x-grpc-web", "1")), body="apps/grpc/echo.grpc",
)
GRPC = Target(
    name="grpc-rapira", server="rapira", app="grpc", mode="dispatcher", proto="grpc", binary="pr",
    start=("grpc", "@RIG@/apps/grpc/php/dispatcher.php"), url="/bench.v1.EchoService/Echo",
    expect="apps/grpc/expect.grpc", config="servers/rapira/grpc.toml.tpl", method="POST", body="apps/grpc/echo.grpc",
)
GRPC_URL = "http://10.0.0.1:8080/bench.v1.EchoService/Echo"

LOAD_CMD_CASES = [
    {
        "name": "wrk2 gets the request shape and a body path under the staged rig",
        "target": GRPCWEB,
        "rate": 2500,
        "expected": [
            "bash", "bench-rig/box/load.sh", "wrk2", "100.000", "2500", "4", "64", "20", GRPC_URL,
            "POST", "apps/grpc/echo.grpc", "content-type: application/grpc-web+proto", "x-grpc-web: 1",
        ],
    },
    {
        "name": "k6 VUS at a low rate are the connections of one loader",
        "target": GRPC,
        "rate": 2500,
        # 2500 x 0.005 s = 12.5, rounded up to 13 VUs, under the 64 connections of one loader.
        "expected": ["bash", "bench-rig/box/load.sh", "k6", "100.000", "2500", "64", "20", GRPC_URL],
    },
    {
        "name": "k6 VUS at a high rate follow the latency budget",
        "target": GRPC,
        "rate": 40100,
        # 40100 x 0.005 s = 200.5, rounded up to 201 VUs.
        "expected": ["bash", "bench-rig/box/load.sh", "k6", "100.000", "40100", "201", "20", GRPC_URL],
    },
]


class LoadCmdTest(unittest.TestCase):
    def test_load_cmd(self):
        plan = {"conns_per_loader": 64, "loader_threads": THREADS}
        for case in LOAD_CMD_CASES:
            with self.subTest(name=case["name"]):
                cmd = load_cmd(case["target"], GRPC_URL, 100.0, case["rate"], plan, 20)
                self.assertEqual(shlex.split(cmd), case["expected"])


PLAN_CASES = [
    {"name": "256 over 4 loaders x 4 threads", "connections": 256, "error": None, "per_loader": 64},
    {"name": "128 over 4 loaders x 4 threads", "connections": 128, "error": None, "per_loader": 32},
    {"name": "250 is not a multiple of 16", "connections": 250, "error": "connections 250", "per_loader": None},
    {"name": "8 is under one connection per thread", "connections": 8, "error": "connections 8", "per_loader": None},
]


class PlanRunTest(unittest.TestCase):
    def test_plan_run(self):
        for case in PLAN_CASES:
            with self.subTest(name=case["name"]):
                s = suite([WORKER, FPM], case["connections"])
                if case["error"]:
                    with self.assertRaisesRegex(SuiteError, case["error"]):
                        plan_run(RIG, s, processes=32, loader_threads=THREADS)
                    continue
                plan = plan_run(RIG, s, processes=32, loader_threads=THREADS)
                self.assertEqual(plan["conns_per_loader"], case["per_loader"])
                self.assertEqual(plan["keys"], ["r1-hello-rapira-worker", "r1-hello-php-fpm"])

    def test_refused_suite_creates_no_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            boxes = FakeBoxes(replies({}))
            with self.assertRaises(SuiteError):
                bench(boxes, Path(tmp), [WORKER], connections=250)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertEqual(boxes.calls, [])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_bench -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rig.bench'`

- [ ] **Step 3: Write `rig/bench.py`**

```python
"""The cell sequence and the rate ladder of one suite run."""

import hashlib
import json
import math
import shlex
import time
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from rig import ssh
from rig.flags import Snapshot, cell_flags, cpu_pct, ena_delta, keepalive_flag, parse_snapshot, stage_flags, stage_void
from rig.ladder import MAX_STAGES, PASS_TOLERANCE, RATIO, cell_numbers, evaluate_stage, stage_rates
from rig.merge import merge, parse_result
from rig.registry import Suite, SuiteError, Target, cell_key, plan_cells
from rig.rig import Rig, ensure_ttl
from rig.runfile import RunFile
from rig.ssh import Host, SshError

PORT = 8080
LEAD_S = 3
WARMUP_S = 10
BOX = f"bash {ssh.RIG_DIR}/box"
BENCH_DIR = "/opt/bench"
RAPIRA_SERVERS = ("rapira", "nginx-rapira")
# k6 preallocates the VUs for this latency at the stage rate of one loader.
K6_LATENCY_BUDGET_S = 0.005
# ssh slack over the lead time and the stage duration of one load call.
LOAD_SLACK_S = 60
SNAPSHOT_TIMEOUT_S = 30
SUITES_DIR = Path("suites")
FACTS_CMD = (
    "echo kernel=$(uname -r); "
    "echo instance_id=$(cat /sys/devices/virtual/dmi/id/board_asset_tag); "
    "t=$(curl -s -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' http://169.254.169.254/latest/api/token); "
    "m=http://169.254.169.254/latest/meta-data/placement; "
    "echo az=$(curl -s -H \"X-aws-ec2-metadata-token: $t\" $m/availability-zone); "
    "echo placement_group=$(curl -s -H \"X-aws-ec2-metadata-token: $t\" $m/group-name)"
)
# Provisioning writes the wrk2 commit and the k6 version to loader.json.
TOOLS_CMD = (
    "python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); "
    "print(\"wrk2=\" + d[\"wrk2_commit\"]); print(\"k6=\" + d[\"k6_version\"])' "
    f"{BENCH_DIR}/loader.json"
)


class Boxes(Protocol):
    def run(self, host: Host, cmd: str, timeout: float | None = None) -> str: ...
    def run_many(self, jobs: list[tuple[Host, str]], timeout: float | None = None) -> list[str | SshError]: ...
    def copy_from(self, host: Host, remote: str, local: Path) -> None: ...


class SshBoxes:
    """The Boxes of a real rig."""

    def run(self, host: Host, cmd: str, timeout: float | None = None) -> str:
        return ssh.run(host, cmd, timeout=timeout)

    def run_many(self, jobs: list[tuple[Host, str]], timeout: float | None = None) -> list[str | SshError]:
        return ssh.run_many(jobs, timeout=timeout)

    def copy_from(self, host: Host, remote: str, local: Path) -> None:
        ssh.copy_from(host, remote, local)


class CellVoid(Exception):
    """The cell is excluded from every number. The message is the reason."""


def plan_run(rig: Rig, suite: Suite, *, processes: int, loader_threads: int) -> dict:
    """Check the suite against the rig and return the cells, the keys, and the load shape."""
    loaders = len(rig.loaders)
    if suite.connections % (loaders * loader_threads) != 0:
        raise SuiteError(
            f"connections {suite.connections} is not a multiple of {loaders} loaders x {loader_threads} threads"
        )
    cells = plan_cells(suite)
    return {
        "cells": cells,
        "keys": [cell_key(round_no, target) for round_no, target in cells],
        "rates": {app: stage_rates(floor) for app, floor in sorted(suite.floors.items())},
        "conns_per_loader": suite.connections // loaders,
        "loader_threads": loader_threads,
        "processes": processes,
    }


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def binary_dir(target: Target, rapira: dict) -> str:
    """The rapira install directory of a target, or "-" for other servers."""
    if target.server not in RAPIRA_SERVERS:
        return "-"
    if target.binary == "base":
        return f"{BENCH_DIR}/rapira/base"
    return f"{BENCH_DIR}/rapira/{rapira['sha'][:7]}"


def request_args(target: Target) -> list[str]:
    """METHOD, BODY_FILE, and HEADER arguments of probe.sh and load.sh. A relative file is under the staged rig."""
    body = target.body or "-"
    return [target.method, body, *(f"{name}: {value}" for name, value in target.headers)]


def box_cmd(script: str, *args: object) -> str:
    return " ".join([f"{BOX}/{script}", *(shlex.quote(str(arg)) for arg in args)])


def probe_cmd(target: Target, url: str) -> str:
    return box_cmd("probe.sh", url, target.expect, target.proto, *request_args(target))


def load_cmd(target: Target, url: str, epoch: float, rate: int, plan: dict, duration_s: int) -> str:
    """One load.sh call of one loader."""
    if target.proto == "grpc":
        vus = max(plan["conns_per_loader"], math.ceil(rate * K6_LATENCY_BUDGET_S))
        return box_cmd("load.sh", "k6", f"{epoch:.3f}", rate, vus, duration_s, url)
    return box_cmd(
        "load.sh", "wrk2", f"{epoch:.3f}", rate, plan["loader_threads"], plan["conns_per_loader"], duration_s, url,
        *request_args(target),
    )


def parse_facts(text: str) -> dict:
    facts = {}
    for line in text.splitlines():
        name, sep, value = line.partition("=")
        if sep:
            facts[name] = value
    return facts


def host_facts(boxes: Boxes, rig: Rig) -> dict[str, dict]:
    """Kernel, instance id, AZ, placement group, and on loaders the tool versions."""
    jobs = [(rig.server, FACTS_CMD)] + [(loader, f"{FACTS_CMD}; {TOOLS_CMD}") for loader in rig.loaders]
    facts = {}
    for (host, _), out in zip(jobs, boxes.run_many(jobs, timeout=SNAPSHOT_TIMEOUT_S)):
        if isinstance(out, SshError):
            raise out
        facts[host.name] = parse_facts(out)
    return facts


def server_versions(boxes: Boxes, server: Host) -> dict:
    """The version lines that provisioning recorded and the shared php.ini text."""
    versions = json.loads(boxes.run(server, f"cat {BENCH_DIR}/versions.json"))
    versions["php_ini"] = boxes.run(server, f"cat {BENCH_DIR}/php.ini")
    return versions


def app_hashes(root: Path) -> dict[str, str]:
    """SHA-256 of every staged file under apps/, composer.lock files included."""
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ssh.tree_files(root)
        if name.startswith("apps/")
    }


def suite_record(suite: Suite) -> dict:
    path = SUITES_DIR / f"{suite.name}.toml"
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return {"name": suite.name, "file_sha256": digest, "rounds": suite.rounds, "stage_s": suite.stage_s, "connections": suite.connections}


def target_record(target: Target) -> dict:
    return {**asdict(target), "start": list(target.start), "headers": dict(target.headers)}


def snapshots(boxes: Boxes, rig: Rig, cell_dir: Path, label: str) -> dict[str, Snapshot]:
    """Snapshots of every box. The server snapshot counts the connections on PORT."""
    jobs = [(rig.server, box_cmd("snapshot.sh", PORT))] + [(loader, box_cmd("snapshot.sh")) for loader in rig.loaders]
    taken = {}
    with (cell_dir / "snapshots.txt").open("a") as log:
        for (host, _), out in zip(jobs, boxes.run_many(jobs, timeout=SNAPSHOT_TIMEOUT_S)):
            if isinstance(out, SshError):
                raise out
            log.write(f"# {label} {host.name}\n{out}")
            taken[host.name] = parse_snapshot(out)
    return taken


def run_stage(boxes: Boxes, rig: Rig, suite: Suite, plan: dict, target: Target, cell: dict, cell_dir: Path, index: int, rate: int) -> dict:
    """Run one stage from every loader and return its stage record."""
    server = rig.server
    url = f"http://{server.private_ip}:{PORT}{target.url}"
    per_loader = rate // len(rig.loaders)
    before = snapshots(boxes, rig, cell_dir, f"stage {index} before")
    epoch = time.time() + LEAD_S
    jobs = [(loader, load_cmd(target, url, epoch, per_loader, plan, suite.stage_s)) for loader in rig.loaders]
    # One server sample in the middle of the stage: the connection states and the memory of the target.
    jobs.append((server, f"sleep {LEAD_S + suite.stage_s // 2}; {box_cmd('snapshot.sh', PORT)}; {box_cmd('target.sh', 'mem', cell['key'], target.server)}"))
    results = boxes.run_many(jobs, timeout=LEAD_S + suite.stage_s + LOAD_SLACK_S)
    after = snapshots(boxes, rig, cell_dir, f"stage {index} after")

    records = {}
    for loader, out in zip(rig.loaders, results):
        text = str(out) if isinstance(out, SshError) else out
        (cell_dir / f"{index}-{loader.name}.txt").write_text(text)
        try:
            records[loader.name] = None if isinstance(out, SshError) else parse_result(out, loader.name)
        except ValueError as exc:
            raise CellVoid(f"stage {index}: {loader.name}: {exc}") from exc
    try:
        merged = merge(records, suite.stage_s)
    except ValueError as exc:
        raise CellVoid(f"stage {index}: {exc}") from exc
    loader_ena = {loader.name: ena_delta(before[loader.name], after[loader.name]) for loader in rig.loaders}
    throttled = stage_void(loader_ena)
    if throttled:
        raise CellVoid(f"stage {index}: {throttled}")
    sample = results[-1]
    if isinstance(sample, SshError):
        raise sample
    sample_lines = sample.strip().splitlines()
    middle = parse_snapshot("\n".join(sample_lines[:-1]))

    passed, reason = evaluate_stage(rate, merged)
    server_busy = cpu_pct(before[server.name], after[server.name])
    loader_busy = {loader.name: cpu_pct(before[loader.name], after[loader.name]) for loader in rig.loaders}
    flags = stage_flags(server_busy, loader_busy, passed)
    server_ena = ena_delta(before[server.name], after[server.name])
    if server_ena:
        flags["ena_throttled"] = server_ena
    flags.update(keepalive_flag(before[server.name], after[server.name], suite.connections))
    stage = {
        "rate": rate,
        "duration_s": suite.stage_s,
        "pass": passed,
        "merged": {
            "requests": merged.requests,
            "successful": merged.successful,
            "bytes": merged.bytes,
            "errors": dict(merged.errors),
            "achieved_rps": merged.achieved_rps,
            "successful_rps": merged.successful_rps,
        },
        "latency_us": dict(merged.latency_us),
        "loaders": [
            {
                "loader": record.loader,
                "tool": record.tool,
                "late_ms": record.late_ms,
                "requests": record.requests,
                "bytes": record.bytes,
                "errors": dict(record.errors),
                "latency_us": dict(record.latency_us),
                "requests_per_sec": record.requests_per_sec,
                "busy_cpu": loader_busy[name],
                "ena": loader_ena[name],
            }
            for name, record in records.items()
        ],
        "server": {
            "busy_cpu": server_busy,
            "pss_kb": int(sample_lines[-1]),
            "established": middle.conns[0] if middle.conns else 0,
            "time_wait": middle.conns[1] if middle.conns else 0,
        },
        "flags": flags,
    }
    if not passed:
        stage["fail_reason"] = reason
    return stage


def add_stage_flags(into: dict, flags: dict) -> None:
    """Carry the flags of one stage to the cell. ENA deltas add up over the stages."""
    for name, value in flags.items():
        if name == "ena_throttled":
            total = into.setdefault("ena_throttled", {})
            for counter, delta in value.items():
                total[counter] = total.get(counter, 0) + delta
        else:
            into[name] = value


def worker_probe(boxes: Boxes, rig: Rig, cell: dict, target: Target) -> tuple[list[int], int]:
    """The sorted worker pids and the log bytes of the target."""
    lines = boxes.run(rig.server, box_cmd("target.sh", "probe", cell["key"], target.server)).splitlines()
    return [int(pid) for pid in lines[0].split()], int(lines[1])


def start_target(boxes: Boxes, rig: Rig, target: Target, cell: dict, cell_dir: Path, processes: int, rapira: dict) -> None:
    """Start the target and copy its rendered configs."""
    cmd = box_cmd("target.sh", "start", cell["key"], target.server, processes, binary_dir(target, rapira), *target.start)
    try:
        out = boxes.run(rig.server, cmd)
    except SshError as exc:
        raise CellVoid(f"start failed: {str(exc).splitlines()[-1]}") from exc
    for line in out.splitlines():
        if line.startswith("config="):
            remote = line.removeprefix("config=")
            name = Path(remote).name
            suffix = name.removeprefix(cell["key"] + ".")
            boxes.copy_from(rig.server, remote, cell_dir / f"config.{suffix}")


def measure(boxes: Boxes, rig: Rig, suite: Suite, plan: dict, target: Target, cell: dict, cell_dir: Path, rapira: dict) -> None:
    """Run the cell sequence of spec 5.3 from the start of the target to the probe after the failing stage."""
    start_target(boxes, rig, target, cell, cell_dir, plan["processes"], rapira)
    pids_before, log_before = worker_probe(boxes, rig, cell, target)
    url = f"http://{rig.server.private_ip}:{PORT}{target.url}"
    results = boxes.run_many([(loader, probe_cmd(target, url)) for loader in rig.loaders], timeout=SNAPSHOT_TIMEOUT_S)
    for loader, out in zip(rig.loaders, results):
        if isinstance(out, SshError):
            raise CellVoid(f"probe mismatch on {loader.name}")
    rates = plan["rates"][target.app]
    epoch = time.time() + LEAD_S
    warmup = [(loader, load_cmd(target, url, epoch, rates[0] // len(rig.loaders), plan, WARMUP_S)) for loader in rig.loaders]
    boxes.run_many(warmup, timeout=LEAD_S + WARMUP_S + LOAD_SLACK_S)

    failed = False
    for index, rate in enumerate(rates):
        stage = run_stage(boxes, rig, suite, plan, target, cell, cell_dir, index, rate)
        cell["stages"].append(stage)
        add_stage_flags(cell["flags"], stage["flags"])
        if not stage["pass"]:
            failed = True
            break
    if failed:
        try:
            boxes.run(rig.loaders[0], probe_cmd(target, url), timeout=SNAPSHOT_TIMEOUT_S)
        except SshError:
            cell["flags"]["died"] = True
    pids_after, log_after = worker_probe(boxes, rig, cell, target)
    cell["flags"].update(cell_flags(pids_before, pids_after, log_before, log_after))


def void(cell: dict, reason: str) -> None:
    if cell["status"] == "ok":
        cell["status"] = "void"
        cell["reason"] = reason


def stop_target(boxes: Boxes, rig: Rig, target: Target, cell: dict, cell_dir: Path) -> None:
    """Stop the target, keep its WARN and ERROR lines, and apply the rapira log rule."""
    try:
        boxes.run(rig.server, box_cmd("target.sh", "stop", cell["key"], target.server))
        lines = boxes.run(rig.server, box_cmd("target.sh", "log", cell["key"], target.server))
    except SshError as exc:
        void(cell, f"stop failed: {str(exc).splitlines()[-1]}")
        return
    (cell_dir / "server.log").write_text(lines)
    count = len(lines.splitlines())
    if count and target.server in RAPIRA_SERVERS:
        void(cell, f"server log: {count} warn or error lines")


def run_cell(boxes: Boxes, rig: Rig, suite: Suite, plan: dict, target: Target, cell: dict, cell_dir: Path, rapira: dict) -> None:
    cell_dir.mkdir(parents=True)
    try:
        measure(boxes, rig, suite, plan, target, cell, cell_dir, rapira)
    except CellVoid as exc:
        void(cell, str(exc))
    except SshError as exc:
        void(cell, f"ssh: {str(exc).splitlines()[0]}")
    except BaseException:
        cell["status"] = "incomplete"
        cell["reason"] = "interrupted"
        raise
    finally:
        stop_target(boxes, rig, target, cell, cell_dir)
    if cell["status"] == "ok":
        numbers = cell_numbers(cell["stages"])
        cell["held"] = numbers["held"]
        cell["peak"] = numbers["peak"]
        cell["unloaded"] = numbers["unloaded"]
        if numbers["ladder_exhausted"]:
            cell["flags"]["ladder_exhausted"] = True


def run_suite(rig: Rig, suite: Suite, boxes: Boxes, out_dir: Path, *, processes: int, run_id: str, rapira: dict,
              servers: dict, apps: dict, loader_threads: int) -> Path:
    """Run every planned cell and write run.json. Returns the run.json path."""
    plan = plan_run(rig, suite, processes=processes, loader_threads=loader_threads)
    ensure_ttl(rig.hosts, len(plan["cells"]) * (8 * suite.stage_s + 60) + 300)
    run_dir = out_dir / run_id
    (run_dir / "raw").mkdir(parents=True)
    facts = host_facts(boxes, rig)
    server_facts = facts[rig.server.name]
    run_file = RunFile(
        run_id=run_id,
        suite=suite_record(suite),
        rig={
            "server_type": rig.server_type,
            "loader_type": rig.loader_type,
            "loader_count": len(rig.loaders),
            "az": server_facts.get("az"),
            "ami": rig.ami_id,
            "kernel": server_facts.get("kernel"),
            "placement_group": server_facts.get("placement_group"),
            "server_instance_id": server_facts.get("instance_id"),
        },
        rapira=rapira,
        servers=servers,
        apps=apps,
        loaders=[
            {
                "name": loader.name,
                "instance_id": facts[loader.name].get("instance_id"),
                "private_ip": loader.private_ip,
                "kernel": facts[loader.name].get("kernel"),
                "wrk2": facts[loader.name].get("wrk2"),
                "k6": facts[loader.name].get("k6"),
            }
            for loader in rig.loaders
        ],
        ladder={
            "stages": plan["rates"],
            "stage_s": suite.stage_s,
            "ratio": RATIO,
            "max_stages": MAX_STAGES,
            "pass_tolerance": PASS_TOLERANCE,
            "connections": suite.connections,
            "conns_per_loader": plan["conns_per_loader"],
            "loader_threads": loader_threads,
            "lead_s": LEAD_S,
            "warmup_s": WARMUP_S,
        },
        processes=processes,
        plan=plan["keys"],
        smoke=suite.smoke,
        started=utc_now(),
    )
    path = run_dir / "run.json"
    try:
        for (round_no, target), key in zip(plan["cells"], plan["keys"]):
            print(f"==> {key}")
            cell = {
                "key": key, "target": target_record(target), "round": round_no, "status": "ok", "flags": {},
                "held": None, "peak": None, "unloaded": None, "stages": [],
            }
            try:
                run_cell(boxes, rig, suite, plan, target, cell, run_dir / "raw" / key, rapira)
            finally:
                run_file.add_cell(cell)
    finally:
        run_file.finish(utc_now())
        run_file.write(path)
    return path
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_bench -v`
Expected: PASS, `Ran 6 tests` and `OK`. The `==> r1-...` lines on stdout come from `run_suite`.

- [ ] **Step 5: Add the `bench` command to `rig/__main__.py`**

Replace:

```python
import argparse
import json
import sys
from pathlib import Path

from rig import ssh
from rig.compare import compare
```

with:

```python
import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from rig import ssh
from rig.bench import BENCH_DIR, SshBoxes, app_hashes, run_suite, server_versions
from rig.compare import compare
```

Insert this function directly above `def cmd_report(args: argparse.Namespace) -> int:`:

```python
def cmd_bench(args: argparse.Namespace) -> int:
    rig = from_terraform(TF_DIR)
    suite = load_suite(SUITES_DIR / f"{args.suite}.toml", load_targets(TARGETS_FILE), len(rig.loaders))
    if args.rounds:
        suite = replace(suite, rounds=args.rounds)
    if args.smoke:
        suite = replace(suite, smoke=True)
    for host in rig.hosts:
        ssh.wait_ssh(host)
    ssh.stage_tree(rig.hosts)
    boxes = SshBoxes()
    processes = args.processes or int(boxes.run(rig.server, "nproc"))
    loader_threads = int(boxes.run(rig.loaders[0], "nproc"))
    meta = json.loads(boxes.run(rig.server, f"cat {BENCH_DIR}/meta.json"))
    # The base build is part of the identity of a run with base targets.
    rapira = {**meta["rapira"], "base": meta["base"]}
    run_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{suite.name}-{rapira['sha'][:7]}"
    path = Path(args.out) / run_id / "run.json"
    try:
        run_suite(
            rig, suite, boxes, Path(args.out), processes=processes, run_id=run_id, rapira=rapira,
            servers=server_versions(boxes, rig.server), apps=app_hashes(Path(".")), loader_threads=loader_threads,
        )
    except KeyboardInterrupt:
        print(f"ERROR: interrupted; the run file is {path}", file=sys.stderr)
        return 130
    text, status = render(json.loads(path.read_text()))
    print(text, end="")
    print(f"==> {path}")
    return status
```

Replace:

```python
    top = argparse.ArgumentParser(prog="python3 -m rig")
    sub = top.add_subparsers(dest="command", required=True)

```

with:

```python
    top = argparse.ArgumentParser(prog="python3 -m rig")
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("bench", help="run a suite on the rig")
    p.add_argument("--suite", required=True)
    p.add_argument("--rounds", type=int)
    p.add_argument("--processes", type=int)
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--out", default="runs")
    p.set_defaults(func=cmd_bench)

```

- [ ] **Step 6: Verify the command and the whole suite**

Run: `python3 -m rig bench --help`
Expected: `usage: python3 -m rig bench [-h] --suite SUITE [--rounds ROUNDS]` followed by `[--processes PROCESSES] [--smoke] [--out OUT]`.

Run: `python3 -m unittest discover -s tests -t .`
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add rig/bench.py rig/__main__.py tests/test_bench.py
git commit -s -S -m "feat: run a suite as a rate ladder over the loaders"
```

### Task 15: Removals and docs

**Files:**
- Delete: `scripts/` (every file that is left after Tasks 8 to 13), `fleet/`, `php/`, `k6/`, `grpc/` (every file that is left after Task 8)
- Create: `METHOD.md`
- Modify: `README.md` (full replacement)
- Modify: `NOTES.md:3`, and insert a section after `NOTES.md:4`
- Modify: `.gitignore:13`

**Interfaces:**
- Consumes: the Makefile targets and knobs of Task 13 (`up`, `provision`, `bench`, `report`, `compare`, `status`, `extend`, `sync`, `lock`, `down`, `nuke`, `test`, `grpc_fixtures`; `SUITE`, `NIGHTLY`, `REF`, `BASE_REF`, `ROUNDS`, `PROCESSES`, `SERVER_TYPE`, `LOADER_TYPE`, `LOADER_COUNT`, `AZ`, `REGION`, `TTL`, `AMI`, `TF_BACKEND`, `RUN`, `A`, `B`, `AUTO_EXTEND`, `TF_CLI_ARGS_init`); the CLI of Tasks 13 and 14; the ladder rules (`RATIO = 2`, `MAX_STAGES = 20`, `PASS_TOLERANCE = 0.95`) of Task 2; `LATE_LIMIT_MS = 1000` of Task 3; `GENERATOR_BUSY = 85`, `SERVER_BUSY = 90`, `LOG_GROWTH_BYTES = 65536` of Task 4; `LEAD_S = 3`, `WARMUP_S = 10`, `K6_LATENCY_BUDGET_S = 0.005`, the void reasons, and the `died` and `ladder_exhausted` flags of Task 14; the server facts of Tasks 9 and 10; the board views of Task 16.
- Produces: `METHOD.md`, the rewritten `README.md` (Tasks 17 and 18 append the sections "CI bootstrap" and "CI"), and the `NOTES.md` section "Method change, 2026-09-25".

- [ ] **Step 1: Remove the old drivers and the moved directories**

Run: `git rm -r -q --ignore-unmatch scripts fleet php k6 grpc`

- [ ] **Step 2: Verify that nothing refers to a removed path**

Run: `git ls-files scripts fleet php k6 grpc | wc -l`
Expected: `0`

Run: `git grep -nE '(^|[^a-zA-Z0-9_./@-])(scripts|fleet|php|k6|grpc)/' -- . ':!NOTES.md' ':!docs/' ':!README.md' | grep -v '"k6/'`
Expected: no output. Step 5 replaces `README.md`, and a k6 module name such as `"k6/net/grpc"` is not a path. Any other match is a reference to a removed path. Change it to the new path of Tasks 8 to 13 before the commit.

Run: `python3 -m unittest discover -s tests -t .`
Expected: `OK`

- [ ] **Step 3: Commit the removal**

```bash
git commit -s -S -m "chore: remove the old drivers and the moved directories"
```

- [ ] **Step 4: Write `METHOD.md`**

````markdown
# Benchmark method

This document defines how the rig measures a target, which numbers it reports, when it voids a cell, and how to review a run before publication. [README.md](README.md) gives the operations. [NOTES.md](NOTES.md) keeps dated records.

## Terms

- A target is one server with one app and one mode, for example `hello-rapira-worker`. `suites/targets.toml` defines every target.
- A cell is one target in one round. Its key is `r<round>-<target>`.
- A stage is one constant request rate that the loaders hold for `stage_s` seconds.
- The ladder is the list of stages of a cell.

## Rig rules

- Run benchmarks only on the EC2 rig of this repository: one server and `LOADER_COUNT` loaders in one cluster placement group.
- Run one target at a time on the server.
- Send all load to the private address of the server.
- Keep the server type, the loader type, the loader count, the Availability Zone, the AMI, the suite, the worker count, and the stage duration equal for one comparison. `make compare` refuses a pair with a different rig shape, worker count, or stage duration.
- Every target runs `PROCESSES` workers. The default is the server CPU count. The start script verifies the worker count after the start, and a different count fails the start.
- Every PHP runtime uses the shared `servers/php.ini`. The run file records its text.
- The driver runs the cells in rotated order: round r starts at target r of the suite and wraps. No target is always first.
- Pin `AMI` when a result set takes more than one day.

Do not combine results from different rig shapes in one direct comparison.

## Load tools

wrk2 loads the HTTP/1.1 targets. k6 loads the gRPC targets. Each loader runs one load process per stage.

These wrk2 facts shape the method:

- `-R` is the total request rate of one process. wrk2 divides it over its threads and connections.
- wrk2 divides `-c` by `-t` with integer division and drops the remainder. The driver therefore requires a connection count that is a multiple of the thread count.
- The first 10 seconds of a run are a calibration window. wrk2 resets the latency histogram after that window but keeps the request count. A 20 second stage therefore reports the throughput over 20 seconds and the latency over the last 10 seconds.
- The reported latency is corrected for coordinated omission.
- The `status` error counter counts responses with a status above 399. The `timeout` counter is a tally per connection that wrk2 takes every 2 seconds. It is not a request count.
- An overloaded target gives no wrk2 errors. The achieved rate falls below the requested rate, and the corrected latency grows to seconds.
- wrk2 sends HTTP/1.1 only.

These k6 facts shape the method:

- k6 uses the `ramping-arrival-rate` executor with a one second ramp to the stage rate, because the first call of every VU opens its connection. A dropped iteration means that no VU was free for a scheduled call, so the target or the loader did not keep the rate.
- Each k6 process preallocates enough VUs for a latency of 5 ms at its rate, and at least the connection count of one loader.
- Each call checks the response bytes. A failed check or a failed call counts as a status error.

## The ladder

The stage rates of an app start at the floor of the suite file and double at each stage: floor, 2 times floor, 4 times floor, and so on. The driver stops a cell at the first failing stage and at 20 stages at most. The floors of the `ci` suite are 10000 req/s for hello, Symfony, static, and gRPC, and 5000 req/s for Laravel.

Each loader sends the stage rate divided by the loader count. Each loader runs one thread per vCPU and the connection count divided by the loader count. With the default rig and the default suite, that is 64 connections per loader and 256 connections in total.

The driver runs this sequence for each cell:

1. It starts the target on the server and verifies the listener process, the executable, and the worker count.
2. Each loader sends one probe and compares the response body with the expected file byte for byte.
3. The loaders send a warm-up of 10 seconds at the floor rate. The driver discards its output.
4. For each stage, the driver takes a snapshot of every box, sets a start time 3 seconds ahead, starts one load process on each loader, collects the results, and takes a second snapshot of every box. It samples the connection states and the memory of the server in the middle of the stage.
5. After a failing stage, one loader sends one more probe.
6. The driver stops the target, verifies that its processes are gone, and reads the WARN and ERROR lines of its log.

A load process that starts more than 1000 ms after the start time makes the stage invalid. Provisioning verifies that chrony is synchronized on every box, so the shared start time is valid to much less than one second.

## Merge over loaders

For each stage, the driver adds the requests, the bytes, and each error counter of all loaders. It then calculates:

- `achieved_rps`: the requests divided by `stage_s`.
- `successful_rps`: the requests minus the status errors, divided by `stage_s`.
- Each latency percentile: the maximum over the loaders. This value is an upper bound, not a pooled percentile. The stage keeps the record of each loader, so a reader can see the spread.

## Pass rule

A stage passes when `achieved_rps` is at least 95% of the stage rate and every error counter is 0. The first failing stage ends the cell. The stage records the first reason that applies: the achieved rate, status errors, connect errors, read errors, write errors, timeouts, or dropped iterations.

## Reported numbers

- `held`: the highest passing rate and its stage index. It is null when the first stage fails.
- `peak`: the successful req/s of the failing stage. Use it to track regressions, because it changes continuously with the target. It is null when every stage passes.
- `unloaded`: the p50 and p99 latency of the first stage, at the floor rate.

With more than one round, the report shows the median of the surviving cells and the spread: 100 times (maximum minus minimum) divided by the median.

## Flags

Flags carry values. They are review items. They do not make a cell fail.

- `generator_bound`: in the failing stage, the busy CPU of a loader is 85% or more and the server is below 90%. The peak is then a floor. State it as "at least" the value.
- `server_unsaturated`: in the failing stage, the server is below 90% and every loader is below 85%. The target failed for a reason other than CPU, for example a queue in its worker pool.
- `ena_throttled`: the ENA allowance counters of the server changed during a stage. The flag gives the deltas. Do not use that cell for a throughput claim.
- `keepalive_broken`: the TIME-WAIT count of the server grew by more than the total connection count during a stage. The target does not keep connections open.
- `worker_churn`: the worker process list changed during the cell.
- `log_growth`: the server log grew by more than 65536 bytes during the cell. The flag gives the byte count.
- `died`: the probe after the failing stage failed. The target stopped answering.
- `ladder_exhausted`: every one of the 20 stages passed. The cell has no peak.

## Voids

A void excludes the cell from every number and makes the run incomplete. The driver voids a cell when:

- The target does not start, or its worker count differs from `PROCESSES`.
- The probe before load does not match the expected response on a loader.
- A loader gives no `RESULT` line for a stage, or a `RESULT` line that the driver cannot read. The reason names the loader.
- A loader starts a stage more than 1000 ms late.
- The ENA allowance counters of a loader change during a stage. The network shaped that loader, so the stage does not measure the server.
- A rapira target logs a WARN or ERROR line during the cell. Rapira runs at log level `warn`. The raw directory keeps the lines. Other servers get only the `log_growth` flag.
- An ssh command to a box fails during the cell.
- The driver cannot stop the target or read its log.

An interrupted run stops the current target and writes the run file with the status `incomplete`. The interrupted cell has the status `incomplete`, and the cells that did not run are listed as missing.

## Review before publication

Publish a result only when all these conditions are true:

- `make bench` and `make report` return status 0.
- The report has no `Do not publish these tables.` line.
- Each reported row has the planned number of rounds.
- The spread is small enough for the stated conclusion.
- No flag changes the stated conclusion. Read the flag rules above for each flag in the row.
- `run.json` contains the expected rapira build, binary SHA-256, AMI, instance types, loader count, worker count, and app hashes.
- The raw files support the values in the report.
- `run.json` records the wrk2 commit and the k6 version of each loader.

If the result depends on a response header or on a server configuration, capture that evidence before the measured run and keep it with the run. The raw directory already keeps the rendered configuration of every target.

## Reading the board

The board on the `gh-pages` branch shows every published run.

- The history view shows the held rate and the peak of each target across runs, one line per target, grouped by app.
- A point with `generator_bound` is a floor. The board draws it with a triangle marker.
- A voided cell is a gap in its line.
- The run view shows the latency percentiles against the rate for each target, and the stage table.
- The board hides smoke runs. Select the checkbox to show them.

Compare points only when the rig shape is the same. Numbers from before 2026-09-25 come from another method and are not on the board.

## Server facts

- A target listens on port 8080.
- An nginx-rapira target runs nginx on port 8080 and the rapira worker on `127.0.0.1:8081`. nginx sends the requests through an HTTP/1.1 keepalive upstream. nginx and rapira both use `PROCESSES` workers on the same server. Compare this row with the direct rapira worker row of the same run.
- php-fpm listens on `127.0.0.1:9000` behind nginx. php-fpm uses `PROCESSES` workers.
- FrankenPHP uses one more thread than its worker count in the worker and stock rows. The start script reads the thread count from the FrankenPHP log and fails the start on a mismatch. Every FrankenPHP config sets `grace_period 2s`.
- The FrankenPHP worker row uses `php_server` with `file_server off` and the worker with `match *`. The stock row uses the default `php_server` shape. No row uses compression, HTTP/2, HTTP/3, or TLS.
- Laravel worker rows use Octane on rapira and on FrankenPHP.
- RoadRunner runs at log level `error` with its `grpc` and `server` channels at `panic`. A failed RoadRunner call therefore shows only through the k6 checks or `worker_churn`.
- In CI both gRPC targets use the pure PHP protobuf runtime, because the nightly asset has no PHP headers for the PECL extension. A server build loads the PECL extension for both targets.

## Connection distribution tests

These rules apply to a special test of how a server spreads connections over its workers:

- Pin the reproducer commit and its dependency lock file.
- Restart the server and nginx before each comparison cell. nginx can keep upstream connections after the load stops.
- Record the request count, the CPU use, and the accepted sockets of each worker. The total CPU use does not show the distribution.
- Take the last request counter sample before the load process stops. The stop of a client can cancel the requests in progress.
- For exact connection counts, stop the other clients and match each accepted socket by the client address and source port.
````

- [ ] **Step 5: Replace `README.md`**

````markdown
# Rapira AWS benchmark rig

This repository runs Rapira benchmarks on Amazon EC2. Terraform creates one server instance and several loader instances in one cluster placement group. The loaders send a staged rate ladder to the private address of the server: wrk2 for HTTP/1.1 targets and k6 for gRPC targets. The operator machine controls the run through SSH.

[METHOD.md](METHOD.md) defines the measurement method and the review before publication. [NOTES.md](NOTES.md) keeps dated records.

The default server is one `c7a.8xlarge`. The default loaders are four `c7a.xlarge`. The default region is `eu-central-1`, and the default Availability Zone is `eu-central-1a`.

## Requirements on the operator machine

- Bash, GNU Make, Git, `curl`, `tar`, and an OpenSSH client
- Python 3.11 or later. The rig uses only the Python standard library.
- Terraform 1.10 or later
- AWS CLI v2 with an active session for the default profile

Start the AWS session before you create the rig:

```bash
aws sso login
```

## Standard flow

Bench the nightly build of a commit on the rapira main branch:

```bash
make up NIGHTLY=<sha7>
make bench
make down
```

Bench a Git ref that the server builds from source:

```bash
make up REF=pr/97 SUITE=full
make bench SUITE=full
make down
```

`make up` checks the vCPU quota, writes `terraform/rig.auto.tfvars`, applies the Terraform stack, and runs `make provision`. `make bench` runs the suite, writes the run file, and prints the report. `make down` destroys the rig.

`NIGHTLY` is the first 7 characters of the commit SHA of a nightly release asset of `rapira-rs/rapira`. The core Nightly workflow deletes the assets of older builds, so use the SHA of the current `nightly` release. `REF` accepts a branch, a tag, a commit, or `pr/N`. When you set `NIGHTLY`, provisioning ignores `REF`.

Provisioning installs only the servers and apps that the suite uses. Set the same `SUITE` on `make up` and on `make bench`. To bench another suite on the same rig, run `make provision SUITE=<suite>` with the same `NIGHTLY` or `REF` first.

## Software on the EC2 instances

| Instance | Condition | Software |
| --- | --- | --- |
| Server | All runs | PHP with opcache, the shared `servers/php.ini`, and the rapira binary: the nightly release asset, or a build of `REF` and `BASE_REF` |
| Server | FrankenPHP targets | FrankenPHP 1.12.7, the glibc release asset |
| Server | php-fpm and nginx-rapira targets | php-fpm and nginx |
| Server | Symfony and Laravel targets | The Composer dependencies from the committed `composer.lock` files |
| Server | gRPC targets | RoadRunner 2025.1.15 and its PHP worker packages. A server build also gets PECL protobuf 5.36.2. |
| Loader | All runs | wrk2 at commit `44a94c1`, k6 2.2.0, and a chrony synchronization check |

The Fedora 44 EC2 image supplies Bash, `dnf`, `sudo`, the OpenSSH server, cloud-init, systemd, core utilities, and the procps and iproute tools. Provisioning uses these tools but does not install them.

## Suites

`suites/targets.toml` defines every target. A target name is `<app>-<server>-<mode>`. A target that runs the base rapira binary has the suffix `-base`. A request variant has its own suffix, for example `grpc-rapira-connect`. A suite file lists its targets, the number of rounds, the stage duration, the total connection count, and the ladder floor of each app.

- `ci` is the per-merge suite: 16 targets over hello, Symfony, Laravel, static files, and gRPC, one round.
- `full` adds the nginx-rapira worker rows, the FrankenPHP stock rows, the static miss and plain rows, the 27 KiB asset, and the gRPC-Web and Connect variants. It runs three rounds.
- `ab` runs hello and Symfony on the rapira worker, classic, and dispatcher modes for the `pr` and the `base` binary. It runs three rounds. Provision it with `REF` and `BASE_REF`.

The driver refuses a suite before it creates a run directory when a floor is not a multiple of the loader count, when `stage_s` is less than 12, or when the connection count is not a multiple of the loader count times the loader vCPU count.

## Settings

- `SUITE` selects `suites/<name>.toml`. It defaults to `ci`.
- `NIGHTLY` selects the nightly release asset by its SHA prefix.
- `REF` selects the Git ref that the server builds. `BASE_REF` selects the base ref and defaults to `main`.
- `ROUNDS` replaces the round count of the suite.
- `PROCESSES` sets the worker count of every target. It defaults to the server CPU count.
- `SERVER_TYPE` defaults to `c7a.8xlarge`. `LOADER_TYPE` defaults to `c7a.xlarge`. `LOADER_COUNT` defaults to 4.
- `AZ` defaults to `eu-central-1a`. `REGION` defaults to `eu-central-1`.
- `AMI` pins an AMI. Without it, Terraform selects the newest Fedora 44 image, which Fedora rebuilds every day. Pin it when a result set takes more than one day.
- `TTL` sets the instance lifetime in minutes. It defaults to 60.
- `TF_BACKEND=s3` keeps the Terraform state in S3. The default `local` keeps the state in `terraform/`.
- `FRAME_POINTERS=1` builds rapira with frame pointers for a perf session. It applies to server builds only.
- `RUN` selects the run directory of `make report`. `A` and `B` select the two run directories of `make compare`.
- `AUTO_EXTEND=0` stops a run when the remaining instance lifetime is too short.

## Other targets

- `make provision` provisions the rig again, for example after a change of `SUITE` or `REF`.
- `make status` shows the Terraform outputs, the instances, and the remaining lifetime of each box.
- `make extend TTL=<minutes>` sets a new lifetime on every box.
- `make sync` builds the local `../core` working tree on the server. The next `make bench` uses that binary. The rig must come from `make up REF=<ref>`, because a nightly rig has no Rust toolchain.
- `make report` prints the tables of the newest run. `make report RUN=runs/<id>` prints another run.
- `make compare A=runs/<a> B=runs/<b>` prints the deltas between two runs.
- `make lock` creates the Symfony and Laravel `composer.lock` files.
- `make test` runs the unit tests.
- `make nuke` removes the tagged AWS resources when the Terraform state is not usable.
- `make grpc_fixtures` builds the gRPC descriptor, the PHP classes, and the request and response fixtures. It needs Go and access to the buf remote plugins.

## Results

Each run writes `runs/<id>/`. The run id is `<UTC timestamp>-<suite>-<rapira sha7>`. The directory holds:

- `run.json`: the run file with the schema `rapira-bench-run/1`. It records the rig, the rapira build, the server versions, the php.ini text, the app hashes, the loaders, the ladder, every cell, and every stage.
- `raw/<cell>/`: the wrk2 or k6 output of each stage and loader, the rendered server configs, the WARN and ERROR lines of the server log, and the snapshots.

`make bench` and `make report` return status 1 when the run is incomplete. An incomplete run has a missing, voided, or interrupted cell. The report then ends with `Do not publish these tables.`

`make compare` refuses two runs with a different server type, loader type, loader count, worker count, or stage duration. Give `--force` to `python3 -m rig compare` to compare them anyway.

`python3 -m rig publish --pages-dir <dir> runs/<id>/run.json` adds a run to a checkout of the `gh-pages` branch: it writes `data/<id>.json` and updates `index.json`.

Use only runs from the same rig shape for a direct comparison. Read [METHOD.md](METHOD.md) before you publish a number.

## Remote state

`make up TF_BACKEND=s3` copies `terraform/backend.tf.s3` to `terraform/backend.tf`. The S3 settings come from the `TF_CLI_ARGS_init` environment variable:

```bash
export TF_CLI_ARGS_init='-backend-config=bucket=<bucket> -backend-config=key=rig/terraform.tfstate -backend-config=region=eu-central-1 -backend-config=use_lockfile=true'
make up TF_BACKEND=s3 NIGHTLY=<sha7>
```

Keep `TF_BACKEND=s3` and the variable set for every later target that uses Terraform, for example `make down`.

## Cost and teardown

The instances use on-demand billing per second. The `ci` suite takes about 45 minutes and costs about $3 with the default rig. The server type is most of the cost.

Every box has a lifetime. The bootstrap sets 180 minutes. Provisioning replaces it with `TTL`. `make bench` extends it from the run estimate: the number of cells times (8 times `stage_s` plus 60) plus 300 seconds. When the lifetime ends, the box shuts down, and a shutdown terminates the instance.

- Run `make down` after every session. Check `make status` when you are not sure.
- When `make down` fails, run it again. Placement group deletion can lag instance termination.
- When the Terraform state is lost, run `make nuke`, then `make up`. `make nuke` deletes only the resources with the tag `Project=rapira-bench`.

## Tests

`make test` runs the unit tests with `python3 -m unittest discover -s tests -t .`. `tests/test_box.py` runs the box scripts in a container. The header of that file gives the command.
````

- [ ] **Step 6: Add the method change to `NOTES.md`**

Replace line 3:

```markdown
Dated records and the decisions the code cannot show. Methodology lives in INSTRUCTIONS.md, operations in README.md. Numbers from different rigs or instance sizes never mix into one table.
```

with:

```markdown
Dated records and the decisions the code cannot show. Methodology lives in METHOD.md, operations in README.md. Numbers from different rigs or instance sizes never mix into one table.
```

Insert after line 4 (the empty line after the introduction), above `## gRPC baseline (rapira PHP dispatcher vs RoadRunner), 2026-09-24`, this section and one empty line:

```markdown
## Method change, 2026-09-25

- From this date the rig measures with the staged rate ladder of METHOD.md. wrk2 loads the HTTP/1.1 targets and k6 loads the gRPC targets, from four c7a.xlarge loaders against one c7a.8xlarge server.
- The numbers before this date come from closed-loop wrk, h2load, and k6 passes from one c7a.4xlarge loader at a fixed connection count. The numbers after this date are the held rate, the peak successful rate, and the unloaded latency of the ladder. Do not compare numbers from before and after this date, and do not put them in one table.
- Each run now writes `runs/<id>/run.json` with the schema `rapira-bench-run/1`. The directories under `results/` stay as the record of the old method.
- FrankenPHP now runs the glibc release asset 1.12.7 with the production worker shape, and every PHP runtime uses the shared `servers/php.ini`. The FrankenPHP and php-fpm numbers before this date used other settings.
- The fixed pair of the decisions below no longer applies. The loader count and the instance types are knobs, and the Makefile quota check adds the vCPUs of the server and all loaders.
```

- [ ] **Step 7: Update the ssh comment in `.gitignore`**

Replace line 13 `# ssh session files the drivers create.` with `# ssh session files that rig/ssh.py creates.`

Run: `sed -n 13p .gitignore`
Expected: `# ssh session files that rig/ssh.py creates.`

- [ ] **Step 8: Verify the docs**

Run this check. It reports en and em dashes, a dash between spaces, the word that the global constraints ban, and a paragraph that continues on the next line:

```bash
python3 - README.md METHOD.md <<'EOF'
import re
import sys

bad = 0
for path in sys.argv[1:]:
    fence = False
    prev = ""
    for number, line in enumerate(open(path).read().splitlines(), start=1):
        if line.startswith("```"):
            fence = not fence
            prev = ""
            continue
        if fence:
            continue
        if chr(0x2013) in line or chr(0x2014) in line or " - " in line or re.search(r"\blegs?\b", line, re.I):
            print(f"{path}:{number}: dash or banned word")
            bad += 1
        prose = bool(line) and not re.match(r"(#|- |\||\d+\. |> )", line)
        if prose and prev:
            print(f"{path}:{number}: hard-wrapped paragraph")
            bad += 1
        prev = line if prose or re.match(r"(- |\d+\. )", line) else ""
print("ok" if bad == 0 else f"{bad} problems")
EOF
```

Expected: `ok`

Run: `sed -n 5,12p NOTES.md | python3 -c "import sys; t = sys.stdin.read(); print('ok' if chr(0x2013) not in t and chr(0x2014) not in t and ' - ' not in t else 'dash')"`
Expected: `ok`

Run: `grep -c '^## Method change, 2026-09-25$' NOTES.md && grep -c 'INSTRUCTIONS' README.md NOTES.md`
Expected: `1`, then `README.md:0` and `NOTES.md:0`

- [ ] **Step 9: Commit the docs**

```bash
git add METHOD.md README.md NOTES.md .gitignore
git commit -s -S -m "docs: describe the rate ladder method and the new rig flow"
```

### Task 16: Board

**Files:**
- Create: `board/index.html`
- Create: `board/app.js`
- Create: `board/style.css`
- Create: `board/chart.umd.js`
- Create: `board/VENDOR.md`
- Create: `tests/fixtures/board_runs.json`
- Modify: `Makefile` (append after the last line)
- Test: `tests/test_board_data.py`

**Interfaces:**
- Consumes: the run file shape of Task 5 (spec 6.1). The board reads `id`, `started`, `rapira.sha`, `rig.server_type`, `rig.loader_type`, `rig.loader_count`, `processes`, and `cells`. Per cell it reads `key`, `target.name`, `target.app`, `status`, `reason`, `flags`, `held.rate`, `peak`, and `stages`. Per stage it reads `rate`, `pass`, `fail_reason`, `flags`, `merged.achieved_rps`, `merged.successful_rps`, and `latency_us.p50`, `p90`, `p99`, `p999`.
- Consumes: `publish(run, pages_dir)` of Task 7, which writes `<pages_dir>/data/<id>.json` and the manifest `<pages_dir>/data/index.json` with `{"schema": "rapira-bench-index/1", "runs": [{"id", "started", "suite", "rapira_sha", "rapira_version", "status", "smoke"}]}`. The board is served from the pages root. It fetches `data/index.json` and then `data/<id>.json` for each run.
- Produces: `board/app.js` exports, under node only, `visibleRuns(entries, showSmoke) -> entries` (smoke entries dropped unless `showSmoke`, sorted by `started` ascending) and `historySeries(runs) -> {"labels": [str], "apps": {app: {target_name: {"peak": [number | null], "held": [number | null], "floor": [bool], "flags": [[str]]}}}}`.
- Produces: the board file list `board/index.html board/app.js board/style.css board/chart.umd.js`, which the publish job of Task 18 copies to the root of `gh-pages`.
- Produces: the Makefile knob `PAGES ?= runs/pages` and the target `board`.

- [ ] **Step 1: Write the failing test**

Create `tests/fixtures/board_runs.json`. It holds three run files reduced to the fields the history view reads:

```json
[
 {
  "id": "20260926T010000Z-ci-aaaaaaa",
  "started": "2026-09-26T01:00:00Z",
  "smoke": false,
  "rapira": {"sha": "aaaaaaa0000000000000000000000000000000a1"},
  "cells": [
   {"key": "r1-hello-rapira-worker", "target": {"name": "hello-rapira-worker", "app": "hello"}, "round": 1, "status": "ok", "flags": {}, "held": {"rate": 640000, "stage": 6}, "peak": 1180234.5, "stages": []},
   {"key": "r1-symfony-rapira-worker", "target": {"name": "symfony-rapira-worker", "app": "symfony"}, "round": 1, "status": "ok", "flags": {}, "held": {"rate": 80000, "stage": 3}, "peak": 120500.0, "stages": []},
   {"key": "r1-grpc-rapira-grpc", "target": {"name": "grpc-rapira-grpc", "app": "grpc"}, "round": 1, "status": "ok", "flags": {"generator_bound": true}, "held": {"rate": 80000, "stage": 3}, "peak": 150000.0, "stages": []}
  ]
 },
 {
  "id": "20260927T010000Z-ci-bbbbbbb",
  "started": "2026-09-27T01:00:00Z",
  "smoke": false,
  "rapira": {"sha": "bbbbbbb0000000000000000000000000000000b2"},
  "cells": [
   {"key": "r1-hello-rapira-worker", "target": {"name": "hello-rapira-worker", "app": "hello"}, "round": 1, "status": "ok", "flags": {"generator_bound": true}, "held": {"rate": 1280000, "stage": 7}, "peak": 1300000.0, "stages": []},
   {"key": "r1-symfony-rapira-worker", "target": {"name": "symfony-rapira-worker", "app": "symfony"}, "round": 1, "status": "void", "reason": "probe mismatch on loader-2", "flags": {}, "held": null, "peak": null, "stages": []},
   {"key": "r1-grpc-rapira-grpc", "target": {"name": "grpc-rapira-grpc", "app": "grpc"}, "round": 1, "status": "ok", "flags": {"generator_bound": true}, "held": {"rate": 80000, "stage": 3}, "peak": 140000.0, "stages": []}
  ]
 },
 {
  "id": "20260928T010000Z-full-ccccccc",
  "started": "2026-09-28T01:00:00Z",
  "smoke": false,
  "rapira": {"sha": "ccccccc0000000000000000000000000000000c3"},
  "cells": [
   {"key": "r1-hello-rapira-worker", "target": {"name": "hello-rapira-worker", "app": "hello"}, "round": 1, "status": "ok", "flags": {}, "held": {"rate": 640000, "stage": 6}, "peak": 1100000.0, "stages": []},
   {"key": "r2-hello-rapira-worker", "target": {"name": "hello-rapira-worker", "app": "hello"}, "round": 2, "status": "ok", "flags": {"log_growth": 70000}, "held": {"rate": 640000, "stage": 6}, "peak": 1150000.0, "stages": []},
   {"key": "r3-hello-rapira-worker", "target": {"name": "hello-rapira-worker", "app": "hello"}, "round": 3, "status": "void", "reason": "loader loader-3 returned no RESULT line", "flags": {}, "held": null, "peak": null, "stages": []},
   {"key": "r1-symfony-rapira-worker", "target": {"name": "symfony-rapira-worker", "app": "symfony"}, "round": 1, "status": "ok", "flags": {}, "held": {"rate": 80000, "stage": 3}, "peak": 110000.0, "stages": []},
   {"key": "r2-symfony-rapira-worker", "target": {"name": "symfony-rapira-worker", "app": "symfony"}, "round": 2, "status": "ok", "flags": {}, "held": {"rate": 80000, "stage": 3}, "peak": 130000.0, "stages": []},
   {"key": "r3-symfony-rapira-worker", "target": {"name": "symfony-rapira-worker", "app": "symfony"}, "round": 3, "status": "ok", "flags": {}, "held": {"rate": 40000, "stage": 2}, "peak": 90000.0, "stages": []},
   {"key": "r1-static-rapira-hit", "target": {"name": "static-rapira-hit", "app": "static"}, "round": 1, "status": "ok", "flags": {}, "held": null, "peak": 9000.0, "stages": []},
   {"key": "r1-grpc-rapira-grpc", "target": {"name": "grpc-rapira-grpc", "app": "grpc"}, "round": 1, "status": "incomplete", "flags": {}, "held": null, "peak": null, "stages": []}
  ]
 }
]
```

Create `tests/test_board_data.py`. The test runs the transforms of `board/app.js` under node, so the browser code and the test use one implementation:

```python
"""Data transforms of board/app.js, run under node.

The fixture holds three run files reduced to the fields the board reads.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "board" / "app.js"
FIXTURE = ROOT / "tests" / "fixtures" / "board_runs.json"
NODE = shutil.which("node")

# Loads app.js as a CommonJS module, calls one export with the JSON
# arguments from stdin, and prints the JSON result.
CALL = (
    "const board = require(process.argv[1]);"
    "const args = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
    "process.stdout.write(JSON.stringify(board[process.argv[2]](...args)));"
)

HISTORY_CASES = [
    {
        # Run a: one ok cell. Run b: generator_bound makes the point a floor.
        # Run c: r3 is void, so the median covers r1 and r2 only:
        # (1100000 + 1150000) / 2 = 1125000; held 640000 in both rounds.
        "name": "single rounds, a floor, and a median over surviving rounds",
        "app": "hello",
        "target": "hello-rapira-worker",
        "expected": {
            "peak": [1180234.5, 1300000.0, 1125000.0],
            "held": [640000, 1280000, 640000],
            "floor": [False, True, False],
            "flags": [[], ["generator_bound"], ["log_growth"]],
        },
    },
    {
        # Run b is void: both numbers are gaps.
        # Run c: peaks 110000, 130000, 90000 give the median 110000;
        # held 80000, 80000, 40000 give the median 80000.
        "name": "void cell is a gap and three rounds give the median",
        "app": "symfony",
        "target": "symfony-rapira-worker",
        "expected": {
            "peak": [120500.0, None, 110000.0],
            "held": [80000, None, 80000],
            "floor": [False, False, False],
            "flags": [[], [], []],
        },
    },
    {
        # The target is only in run c, and its first stage failed:
        # held is null and peak is set.
        "name": "target absent from earlier runs and first stage failed",
        "app": "static",
        "target": "static-rapira-hit",
        "expected": {
            "peak": [None, None, 9000.0],
            "held": [None, None, None],
            "floor": [False, False, False],
            "flags": [[], [], []],
        },
    },
    {
        # An incomplete cell is excluded like a void cell.
        "name": "incomplete cell is a gap",
        "app": "grpc",
        "target": "grpc-rapira-grpc",
        "expected": {
            "peak": [150000.0, 140000.0, None],
            "held": [80000, 80000, None],
            "floor": [True, True, False],
            "flags": [["generator_bound"], ["generator_bound"], []],
        },
    },
]

INDEX_ENTRIES = [
    {"id": "b", "started": "2026-09-27T01:00:00Z", "smoke": False},
    {"id": "s", "started": "2026-09-26T12:00:00Z", "smoke": True},
    {"id": "a", "started": "2026-09-26T01:00:00Z", "smoke": False},
]

VISIBLE_CASES = [
    {"name": "smoke runs hidden, oldest first", "show_smoke": False, "expected": ["a", "b"]},
    {"name": "smoke runs shown, oldest first", "show_smoke": True, "expected": ["a", "s", "b"]},
]


def call_js(function, *args):
    out = subprocess.run(
        [NODE, "-e", CALL, str(APP_JS), function],
        input=json.dumps(args),
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, "node is not installed")
class HistorySeriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history = call_js("historySeries", json.loads(FIXTURE.read_text()))

    def test_series(self):
        for case in HISTORY_CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(self.history["apps"][case["app"]][case["target"]], case["expected"])

    def test_labels_name_the_commit_and_the_date(self):
        self.assertEqual(
            self.history["labels"],
            ["aaaaaaa 2026-09-26", "bbbbbbb 2026-09-27", "ccccccc 2026-09-28"],
        )

    def test_targets_are_grouped_by_app(self):
        grouped = {app: sorted(targets) for app, targets in self.history["apps"].items()}
        self.assertEqual(
            grouped,
            {
                "hello": ["hello-rapira-worker"],
                "symfony": ["symfony-rapira-worker"],
                "static": ["static-rapira-hit"],
                "grpc": ["grpc-rapira-grpc"],
            },
        )


@unittest.skipUnless(NODE, "node is not installed")
class VisibleRunsTest(unittest.TestCase):
    def test_visible_runs(self):
        for case in VISIBLE_CASES:
            with self.subTest(name=case["name"]):
                runs = call_js("visibleRuns", INDEX_ENTRIES, case["show_smoke"])
                self.assertEqual([run["id"] for run in runs], case["expected"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_board_data -v`
Expected: FAIL. Each `AssertionError` holds `Error: Cannot find module '<repo>/board/app.js'`. The summary lines are `Ran 1 test` and `FAILED (failures=2, errors=1)`: the error is `setUpClass` of `HistorySeriesTest`, and the two failures are the two subtests of `test_visible_runs`.

- [ ] **Step 3: Vendor Chart.js**

Run:

```bash
mkdir -p board
curl -fsSL -o board/chart.umd.js https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.js
sha256sum board/chart.umd.js
head -2 board/chart.umd.js
```

Expected:

```
ecc3cd1eeb8c34d2178e3f59fd63ec5a3d84358c11730af0b9958dc886d7652a  board/chart.umd.js
/*!
 * Chart.js v4.5.1
```

Stop when the hash differs. Do not commit a file with another hash.

- [ ] **Step 4: Record the vendored file**

Create `board/VENDOR.md`:

```markdown
# Vendored files

## chart.umd.js

- Library: Chart.js 4.5.1, MIT license, release notes at https://github.com/chartjs/Chart.js/releases/tag/v4.5.1
- Source: https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.js
- SHA-256: `ecc3cd1eeb8c34d2178e3f59fd63ec5a3d84358c11730af0b9958dc886d7652a`
- Size: 208518 bytes

To update the file:

- Download the new version from the same URL with the new version number.
- Run `sha256sum board/chart.umd.js` and write the new hash in this file.
- Write the new version, URL, and size in this file.
```

Run: `grep -c "$(sha256sum board/chart.umd.js | cut -d' ' -f1)" board/VENDOR.md && stat -c %s board/chart.umd.js`
Expected: `1` and `208518`.

- [ ] **Step 5: Write the implementation**

Create `board/app.js`:

```javascript
"use strict";

// The board loads at most this many runs, the newest ones.
const HISTORY_RUNS = 60;
const PALETTE = ["#2f6fdf", "#d9480f", "#2b8a3e", "#ae3ec9", "#e67700", "#0c8599", "#c2255c", "#5c7cfa"];
const PERCENTILES = ["p50", "p90", "p99", "p999"];

function median(values) {
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

// Index entries to show, oldest first. Smoke runs show only on request.
function visibleRuns(entries, showSmoke) {
  return entries
    .filter((entry) => showSmoke || !entry.smoke)
    .sort((a, b) => (a.started < b.started ? -1 : a.started > b.started ? 1 : 0));
}

// One point of one target in one run: the median over the ok cells.
// A target without an ok cell in the run is a gap.
function historyPoint(cells) {
  const ok = cells.filter((cell) => cell.status === "ok");
  const peaks = ok.filter((cell) => cell.peak !== null).map((cell) => cell.peak);
  const helds = ok.filter((cell) => cell.held !== null).map((cell) => cell.held.rate);
  const flags = new Set();
  ok.forEach((cell) => Object.keys(cell.flags).forEach((name) => flags.add(name)));
  return {
    peak: peaks.length ? median(peaks) : null,
    held: helds.length ? median(helds) : null,
    floor: flags.has("generator_bound"),
    flags: Array.from(flags).sort(),
  };
}

// Series per app and target over the runs, which come in started order.
function historySeries(runs) {
  const apps = {};
  runs.forEach((run) => {
    run.cells.forEach((cell) => {
      apps[cell.target.app] = apps[cell.target.app] || {};
      apps[cell.target.app][cell.target.name] = { peak: [], held: [], floor: [], flags: [] };
    });
  });
  runs.forEach((run) => {
    Object.keys(apps).forEach((app) => {
      Object.keys(apps[app]).forEach((name) => {
        const point = historyPoint(run.cells.filter((cell) => cell.target.name === name));
        const series = apps[app][name];
        series.peak.push(point.peak);
        series.held.push(point.held);
        series.floor.push(point.floor);
        series.flags.push(point.flags);
      });
    });
  });
  const labels = runs.map((run) => run.rapira.sha.slice(0, 7) + " " + run.started.slice(0, 10));
  return { labels, apps };
}

function flagText(flags) {
  return Object.keys(flags || {})
    .sort()
    .map((name) => (flags[name] === true ? name : name + "(" + JSON.stringify(flags[name]) + ")"))
    .join(", ");
}

function el(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function number(value) {
  return value === null || value === undefined ? "-" : Math.round(value).toLocaleString("en-US");
}

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) {
    throw new Error(path + ": HTTP " + response.status);
  }
  return response.json();
}

const state = { entries: [], docs: new Map(), charts: [] };

async function loadRun(id) {
  if (!state.docs.has(id)) {
    state.docs.set(id, await fetchJson("data/" + id + ".json"));
  }
  return state.docs.get(id);
}

function addChart(parent, config) {
  const box = el("div");
  box.className = "chart";
  const canvas = el("canvas");
  box.appendChild(canvas);
  parent.appendChild(box);
  state.charts.push(new Chart(canvas, config));
}

function drawHistory(history) {
  const root = document.getElementById("history-charts");
  root.replaceChildren();
  Object.keys(history.apps)
    .sort()
    .forEach((app) => {
      root.appendChild(el("h3", app));
      const datasets = [];
      Object.keys(history.apps[app])
        .sort()
        .forEach((name, i) => {
          const series = history.apps[app][name];
          const color = PALETTE[i % PALETTE.length];
          datasets.push({
            label: name + " peak",
            data: series.peak,
            borderColor: color,
            backgroundColor: color,
            pointStyle: series.floor.map((floor) => (floor ? "triangle" : "circle")),
            pointRadius: series.floor.map((floor) => (floor ? 7 : 3)),
            flags: series.flags,
          });
          datasets.push({
            label: name + " held",
            data: series.held,
            borderColor: color,
            backgroundColor: color,
            borderDash: [6, 4],
            pointRadius: 2,
            flags: series.flags,
          });
        });
      addChart(root, {
        type: "line",
        data: { labels: history.labels, datasets },
        options: {
          spanGaps: false,
          scales: { y: { title: { display: true, text: "req/s" }, beginAtZero: true } },
          plugins: {
            tooltip: {
              callbacks: {
                afterLabel: (item) => {
                  const flags = item.dataset.flags[item.dataIndex];
                  return flags.length ? "flags: " + flags.join(", ") : "";
                },
              },
            },
          },
        },
      });
    });
}

function stageTable(cell) {
  const table = el("table");
  const head = el("tr");
  ["stage", "rate", "pass", "achieved req/s", "successful req/s", "p50 us", "p99 us", "flags"].forEach((title) =>
    head.appendChild(el("th", title))
  );
  table.appendChild(head);
  cell.stages.forEach((stage, i) => {
    const row = el("tr");
    row.appendChild(el("td", String(i)));
    row.appendChild(el("td", number(stage.rate)));
    row.appendChild(el("td", stage.pass ? "pass" : "fail: " + stage.fail_reason));
    row.appendChild(el("td", number(stage.merged.achieved_rps)));
    row.appendChild(el("td", number(stage.merged.successful_rps)));
    row.appendChild(el("td", number(stage.latency_us.p50)));
    row.appendChild(el("td", number(stage.latency_us.p99)));
    row.appendChild(el("td", flagText(stage.flags)));
    if (!stage.pass) {
      row.className = "fail";
    }
    table.appendChild(row);
  });
  return table;
}

function drawRun(entry, run) {
  const meta = document.getElementById("run-meta");
  meta.textContent =
    "rapira " + entry.rapira_version + " (" + entry.rapira_sha.slice(0, 7) + "), suite " + entry.suite +
    ", " + run.rig.server_type + " server, " + run.rig.loader_count + " x " + run.rig.loader_type +
    " loaders, " + run.processes + " workers, status " + entry.status;
  const root = document.getElementById("run-cells");
  root.replaceChildren();
  run.cells.forEach((cell) => {
    const section = el("section");
    section.className = "cell";
    section.appendChild(el("h3", cell.key));
    const summary =
      cell.status === "ok"
        ? "held " + number(cell.held && cell.held.rate) + " req/s, peak " + number(cell.peak) + " req/s"
        : cell.status + (cell.reason ? ": " + cell.reason : "");
    section.appendChild(el("p", summary));
    const flags = flagText(cell.flags);
    if (flags) {
      section.appendChild(el("p", "flags: " + flags));
    }
    if (cell.stages.length) {
      addChart(section, {
        type: "line",
        data: {
          datasets: PERCENTILES.map((key, i) => ({
            label: key,
            data: cell.stages.map((stage) => ({ x: stage.rate, y: stage.latency_us[key] })),
            borderColor: PALETTE[i],
            backgroundColor: PALETTE[i],
          })),
        },
        options: {
          scales: {
            x: { type: "logarithmic", title: { display: true, text: "rate req/s" } },
            y: { type: "logarithmic", title: { display: true, text: "latency us" } },
          },
        },
      });
      section.appendChild(stageTable(cell));
    }
    root.appendChild(section);
  });
}

async function render() {
  state.charts.forEach((chart) => chart.destroy());
  state.charts = [];
  const showSmoke = document.getElementById("smoke").checked;
  const entries = visibleRuns(state.entries, showSmoke).slice(-HISTORY_RUNS);
  const runs = await Promise.all(entries.map((entry) => loadRun(entry.id)));
  drawHistory(historySeries(runs));
  const select = document.getElementById("run-select");
  const current = select.value;
  select.replaceChildren();
  entries
    .slice()
    .reverse()
    .forEach((entry) => {
      const option = el("option", entry.started + " " + entry.suite + " " + entry.rapira_sha.slice(0, 7));
      option.value = entry.id;
      select.appendChild(option);
    });
  if (entries.some((entry) => entry.id === current)) {
    select.value = current;
  }
  const entry = entries.find((item) => item.id === select.value);
  if (entry) {
    drawRun(entry, await loadRun(entry.id));
  }
}

function showError(error) {
  const node = document.getElementById("error");
  node.textContent = String(error);
  node.hidden = false;
}

async function main() {
  const style = getComputedStyle(document.documentElement);
  Chart.defaults.color = style.getPropertyValue("--fg").trim();
  Chart.defaults.borderColor = style.getPropertyValue("--grid").trim();
  Chart.defaults.maintainAspectRatio = false;
  state.entries = (await fetchJson("data/index.json")).runs;
  document.getElementById("smoke").addEventListener("change", () => render().catch(showError));
  document.getElementById("run-select").addEventListener("change", () => render().catch(showError));
  await render();
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { visibleRuns, historySeries };
} else {
  document.addEventListener("DOMContentLoaded", () => main().catch(showError));
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `node --check board/app.js && python3 -m unittest tests.test_board_data -v`
Expected: PASS, `Ran 4 tests` and `OK`.

- [ ] **Step 7: Write the page and the style sheet**

Create `board/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rapira benchmarks</title>
<link rel="stylesheet" href="style.css">
<script src="chart.umd.js" defer></script>
<script src="app.js" defer></script>
</head>
<body>
<header>
<h1>Rapira benchmarks</h1>
<p>Rate ladder results for each merge to the rapira main branch. <a href="https://github.com/rapira-rs/benchmarks/blob/main/METHOD.md">How to read these numbers</a>.</p>
<label><input type="checkbox" id="smoke"> Show smoke runs</label>
</header>
<p id="error" hidden></p>
<main>
<section id="history">
<h2>History</h2>
<p class="note">Solid line: peak req/s, the successful rate in the first failing stage. Dashed line: held req/s, the highest passing rate. A triangle marks a generator-bound peak: the real peak is higher. A gap marks a voided or missing cell.</p>
<div id="history-charts"></div>
</section>
<section id="run">
<h2>Run</h2>
<label>Run <select id="run-select"></select></label>
<p id="run-meta"></p>
<div id="run-cells"></div>
</section>
</main>
</body>
</html>
```

Create `board/style.css`:

```css
:root {
  --bg: #ffffff;
  --fg: #1f2328;
  --muted: #59636e;
  --grid: #d0d7de;
  --fail: #fbe9e7;
  --error: #b42318;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117;
    --fg: #e6edf3;
    --muted: #9198a1;
    --grid: #30363d;
    --fail: #3d1d1a;
    --error: #ff7b72;
  }
}

body {
  margin: 0 auto;
  max-width: 72rem;
  padding: 1rem;
  background: var(--bg);
  color: var(--fg);
  font: 15px/1.5 system-ui, sans-serif;
}

a {
  color: inherit;
}

.note,
#run-meta {
  color: var(--muted);
}

#error {
  color: var(--error);
  font-weight: bold;
}

.chart {
  position: relative;
  height: 22rem;
  margin-bottom: 1.5rem;
}

.cell {
  border-top: 1px solid var(--grid);
  padding-top: 0.5rem;
}

table {
  border-collapse: collapse;
  width: 100%;
  font-variant-numeric: tabular-nums;
  margin-bottom: 1.5rem;
}

th,
td {
  border-bottom: 1px solid var(--grid);
  padding: 0.25rem 0.5rem;
  text-align: right;
}

th:nth-child(3),
td:nth-child(3),
th:last-child,
td:last-child {
  text-align: left;
}

tr.fail {
  background: var(--fail);
}

@media (max-width: 40rem) {
  .chart {
    height: 16rem;
  }

  table {
    display: block;
    overflow-x: auto;
  }
}
```

Run: `grep -c 'src="chart.umd.js"\|src="app.js"\|href="style.css"' board/index.html`
Expected: `3`

- [ ] **Step 8: Add the board target to the Makefile**

Append these lines to the end of `Makefile`. Each recipe line starts with one tab character:

```make
# Serve the board and a local pages dir for a manual check. Fill the dir first:
# python3 -m rig publish --pages-dir runs/pages runs/<id>/run.json
PAGES ?= runs/pages
.PHONY: board
board:
	@test -f $(PAGES)/data/index.json || { echo "ERROR: no $(PAGES)/data/index.json; run: python3 -m rig publish --pages-dir $(PAGES) runs/<id>/run.json"; exit 1; }
	cp board/index.html board/app.js board/style.css board/chart.umd.js $(PAGES)/
	python3 -m http.server --bind 127.0.0.1 --directory $(PAGES) 8000
```

Run: `make -n board && make board PAGES=/nonexistent; echo "exit=$?"`
Expected:

```
test -f runs/pages/data/index.json || { echo "ERROR: no runs/pages/data/index.json; run: python3 -m rig publish --pages-dir runs/pages runs/<id>/run.json"; exit 1; }
cp board/index.html board/app.js board/style.css board/chart.umd.js runs/pages/
python3 -m http.server --bind 127.0.0.1 --directory runs/pages 8000
ERROR: no /nonexistent/data/index.json; run: python3 -m rig publish --pages-dir /nonexistent runs/<id>/run.json
make: *** [Makefile:<line>: board] Error 1
exit=2
```

A `missing separator` error means the recipe lines lost their tab characters.

- [ ] **Step 9: Commit**

```bash
git add board/index.html board/app.js board/style.css board/chart.umd.js board/VENDOR.md tests/fixtures/board_runs.json tests/test_board_data.py Makefile
git commit -s -S -m "feat: add the static benchmark board"
```

### Task 17: CI bootstrap stack

**Files:**
- Create: `terraform/ci/versions.tf`
- Create: `terraform/ci/variables.tf`
- Create: `terraform/ci/main.tf`
- Create: `terraform/ci/outputs.tf`
- Create: `terraform/ci/.terraform.lock.hcl` (written by `terraform init`)
- Create: `terraform/s3.tfbackend`
- Modify: `.gitignore` (append after the last line)
- Modify: `README.md` (append after the last line)

**Interfaces:**
- Consumes: the tag `Project=rapira-bench` and the region `eu-central-1` of the global constraints. The rig stack of Task 13: `make up TF_BACKEND=s3` copies `terraform/backend.tf.s3` (a partial `backend "s3" {}` block) to `terraform/backend.tf` and runs `terraform -chdir=terraform init`. Terraform resolves a `-backend-config` file path against the `-chdir` directory, so `-backend-config=s3.tfbackend` reads `terraform/s3.tfbackend`.
- Consumes: the AWS calls of the rig stack, the Makefile `up` quota check, and `terraform/nuke.sh` (Task 13): EC2 describe calls, `RunInstances`, `TerminateInstances`, security groups, the placement group, the key pair, tags, and `servicequotas:GetServiceQuota`.
- Produces: the Terraform outputs `role_arn` and `bucket` of `terraform/ci`, the IAM role name `rapira-bench-ci`, and the bucket name `rapira-bench-tfstate-<account id>`.
- Produces: `terraform/s3.tfbackend` with `key = "rig/terraform.tfstate"`, `region = "eu-central-1"`, and `use_lockfile = true`. The bucket comes from `-backend-config=bucket=<name>`. Task 18 passes both through `TF_CLI_ARGS_init`.
- Produces: the Terraform variable `github_sub_prefix` (default `repo:rapira-rs@293146261/benchmarks@1314214977`) and the repository variable names `AWS_ROLE_ARN` and `TF_STATE_BUCKET`.

The repository was created on 2026-07-27. GitHub gives repositories created after 2026-07-15 the immutable OIDC subject format, which holds the owner id and the repository id. `gh api repos/rapira-rs/benchmarks/actions/oidc/customization/sub` returns `"use_immutable_subject":true` with the prefix `repo:rapira-rs@293146261/benchmarks@1314214977`. The trust policy therefore matches `repo:rapira-rs@293146261/benchmarks@1314214977:ref:refs/heads/main`. A policy on `repo:rapira-rs/benchmarks:ref:refs/heads/main` never matches a token of this repository.

- [ ] **Step 1: Write the provider constraints and the variables**

Create `terraform/ci/versions.tf`:

```hcl
terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
```

Create `terraform/ci/variables.tf`:

```hcl
variable "region" {
  description = "The region of the rig stack and the state bucket."
  type        = string
  default     = "eu-central-1"
}

# The repository was created after 2026-07-15 and uses the immutable subject
# format with the owner and repository ids.
# https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims
# Read it with: gh api repos/rapira-rs/benchmarks/actions/oidc/customization/sub --jq .sub_claim_prefix
variable "github_sub_prefix" {
  description = "The OIDC subject prefix of the benchmarks repository."
  type        = string
  default     = "repo:rapira-rs@293146261/benchmarks@1314214977"
}
```

Run: `terraform -chdir=terraform/ci fmt -check && echo fmt-ok`
Expected: `fmt-ok`

- [ ] **Step 2: Write the provider, the role, and the bucket**

Create `terraform/ci/main.tf`:

```hcl
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project = "rapira-bench"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  bucket = "rapira-bench-tfstate-${data.aws_caller_identity.current.account_id}"
  # The subject claim of a job on the main branch without an environment.
  # https://docs.github.com/en/actions/reference/security/oidc#filtering-for-a-specific-branch
  subject = "${var.github_sub_prefix}:ref:refs/heads/main"
}

# IAM checks the provider TLS certificate against its trusted root CAs, so no
# thumbprint is set.
# https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc_verify-thumbprint.html
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.subject]
    }
  }
}

resource "aws_iam_role" "ci" {
  name               = "rapira-bench-ci"
  assume_role_policy = data.aws_iam_policy_document.trust.json
  # The bench job runs up to 90 minutes and destroys the rig at the end.
  max_session_duration = 7200
}

data "aws_iam_policy_document" "ci" {
  statement {
    sid = "Describe"
    actions = [
      "ec2:Describe*",
      "servicequotas:GetServiceQuota",
    ]
    resources = ["*"]
  }

  statement {
    sid = "Rig"
    actions = [
      "ec2:RunInstances",
      "ec2:TerminateInstances",
      "ec2:ModifyInstanceAttribute",
      "ec2:CreateTags",
      "ec2:DeleteTags",
      "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:CreatePlacementGroup",
      "ec2:DeletePlacementGroup",
      "ec2:ImportKeyPair",
      "ec2:DeleteKeyPair",
      "ec2:DeleteVolume",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region]
    }
  }

  statement {
    sid       = "StateList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }

  # The state object and its lockfile.
  statement {
    sid       = "StateObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/rig/*"]
  }
}

resource "aws_iam_role_policy" "ci" {
  name   = "rapira-bench-ci"
  role   = aws_iam_role.ci.id
  policy = data.aws_iam_policy_document.ci.json
}

resource "aws_s3_bucket" "state" {
  bucket = local.bucket
}

# Versions keep earlier states when a write goes wrong.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
```

Run: `terraform -chdir=terraform/ci fmt -check && echo fmt-ok`
Expected: `fmt-ok`

- [ ] **Step 3: Write the outputs and the backend settings of the rig stack**

Create `terraform/ci/outputs.tf`:

```hcl
# Set as the repository variable AWS_ROLE_ARN.
output "role_arn" {
  value = aws_iam_role.ci.arn
}

# Set as the repository variable TF_STATE_BUCKET.
output "bucket" {
  value = aws_s3_bucket.state.bucket
}
```

Create `terraform/s3.tfbackend`:

```hcl
# S3 backend settings of the rig stack for TF_BACKEND=s3. The bucket name
# holds the account id and comes from -backend-config=bucket=<name>.
key          = "rig/terraform.tfstate"
region       = "eu-central-1"
use_lockfile = true
```

Run: `terraform -chdir=terraform/ci fmt -check && echo fmt-ok && grep -c '^key\|^region\|^use_lockfile' terraform/s3.tfbackend`
Expected: `fmt-ok` and `3`.

- [ ] **Step 4: Ignore the local state of the bootstrap stack**

The existing patterns `terraform/.terraform/` and `terraform/*.tfstate*` do not match files under `terraform/ci/`. Run:

```bash
cat >> .gitignore <<'EOF'

# The bootstrap stack keeps its state on the owner machine.
terraform/ci/.terraform/
terraform/ci/*.tfstate*
EOF
```

Run: `git check-ignore terraform/ci/terraform.tfstate terraform/ci/terraform.tfstate.backup`
Expected:

```
terraform/ci/terraform.tfstate
terraform/ci/terraform.tfstate.backup
```

- [ ] **Step 5: Validate the stack**

Run:

```bash
terraform -chdir=terraform/ci fmt -check && echo fmt-ok
terraform -chdir=terraform/ci init -backend=false -input=false -no-color | grep -F "Terraform has been successfully initialized!"
terraform -chdir=terraform/ci validate -no-color
git check-ignore terraform/ci/.terraform
git check-ignore terraform/ci/.terraform.lock.hcl; echo "lock ignored=$?"
```

Expected:

```
fmt-ok
Terraform has been successfully initialized!
Success! The configuration is valid.

terraform/ci/.terraform
lock ignored=1
```

- [ ] **Step 6: Document the bootstrap**

Append the section to `README.md`:

````bash
cat >> README.md <<'EOF'

## CI bootstrap

The stack in `terraform/ci/` creates the AWS resources of the bench workflow: the GitHub OIDC identity provider, the IAM role `rapira-bench-ci`, and the S3 bucket `rapira-bench-tfstate-<account id>` for the rig state. The owner applies it once with the default AWS CLI profile. Its state stays in `terraform/ci/` on the owner machine and git ignores it. Keep that state file: a later apply needs it to find the resources.

```bash
aws sso login
terraform -chdir=terraform/ci init
terraform -chdir=terraform/ci apply
terraform -chdir=terraform/ci output
```

The output `role_arn` is the value of the repository variable `AWS_ROLE_ARN`. The output `bucket` is the value of the repository variable `TF_STATE_BUCKET`.

The role trusts only jobs on the `main` branch of this repository. This repository uses the immutable OIDC subject format, which contains the owner id and the repository id. The variable `github_sub_prefix` holds that prefix. This command prints the current value:

```bash
gh api repos/rapira-rs/benchmarks/actions/oidc/customization/sub --jq .sub_claim_prefix
```

An AWS account has at most one OIDC provider for `token.actions.githubusercontent.com`. If the apply stops with `EntityAlreadyExists`, import the provider and apply again:

```bash
terraform -chdir=terraform/ci import aws_iam_openid_connect_provider.github arn:aws:iam::<account id>:oidc-provider/token.actions.githubusercontent.com
```

The role policy allows the EC2 actions of the rig stack in `eu-central-1`, all EC2 describe calls, the service quota read, and read and write access to the `rig/` objects of the state bucket.

With `TF_BACKEND=s3`, the rig stack keeps its state in the bucket. `terraform/s3.tfbackend` holds the key `rig/terraform.tfstate`, the region, and `use_lockfile = true`. The bucket name holds the account id, so it is not in the repository: the CI workflow gives it to `terraform init` through `TF_CLI_ARGS_init`. If a CI run leaves a rig that bills, run `make nuke` on the operator machine. It finds the resources by their tag and needs no state.
EOF
````

Run: `grep -c '^## CI bootstrap$' README.md && grep -nP '[\x{2013}\x{2014}]' README.md; echo "dashes=$?"`
Expected: `1` and `dashes=1`.

- [ ] **Step 7: Commit**

```bash
git add terraform/ci/versions.tf terraform/ci/variables.tf terraform/ci/main.tf terraform/ci/outputs.tf terraform/ci/.terraform.lock.hcl terraform/s3.tfbackend .gitignore README.md
git commit -s -S -m "feat: add the CI bootstrap stack with the OIDC role and the state bucket"
```

### Task 18: Workflows and owner steps

**Files:**
- Create: `.github/workflows/bench.yml`
- Create: `docs/core-dispatch.yml`
- Modify: `README.md` (append after the last line)

**Interfaces:**
- Consumes: the Makefile of Task 13: `make up NIGHTLY=<sha7> AZ=<az>` (applies the rig stack and then runs `make provision`; `TF_BACKEND=s3` from the environment copies `terraform/backend.tf.s3` to `terraform/backend.tf` before `terraform -chdir=terraform init`), `make down`, `make nuke`. On an apply error `make up` prints its own hint that names `InsufficientInstanceCapacity`, so the retry matches the AWS error text `api error InsufficientInstanceCapacity`.
- Consumes: the CLI of Tasks 6, 7, and 14: `python3 -m rig bench --suite ci --out runs` (writes `runs/<id>/run.json` and `runs/<id>/raw/`), `python3 -m rig report <run.json>` (exit 1 when the run is incomplete), `python3 -m rig publish --pages-dir <dir> <run.json>`.
- Consumes: the manifest of Task 7 at `data/index.json` in `gh-pages` (`runs[].rapira_sha`, `runs[].smoke`), and the board file list of Task 16.
- Consumes: `terraform/s3.tfbackend` and the repository variables `AWS_ROLE_ARN` and `TF_STATE_BUCKET` of Task 17.
- Produces: the workflow `bench.yml` with the optional `workflow_dispatch` inputs `sha` and `version`, the jobs `bench` and `publish`, the concurrency group `bench`, and the artifacts `run` and `raw`. `actions/upload-artifact` keeps the path part after the first wildcard, so the `run` artifact holds `<id>/run.json`.
- Produces: the core repository secret name `BENCH_DISPATCH_TOKEN`.

The action pins are the latest releases on 2026-09-25, resolved with `gh api repos/<owner>/<repo>/releases/latest` and `gh api repos/<owner>/<repo>/git/ref/tags/<tag>`: `actions/checkout` v7.0.1 `3d3c42e5aac5ba805825da76410c181273ba90b1`, `aws-actions/configure-aws-credentials` v6.3.0 `e1253824e5c10ff9df46874f81ed3ec929e19cfd`, `hashicorp/setup-terraform` v4.0.1 `dfe3c3f87815947d99a8997f908cb6525fc44e9e`, `actions/upload-artifact` v7.0.1 `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a`, `actions/download-artifact` v8.0.1 `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c`. Terraform 1.16.4 is the latest Terraform release on that date.

- [ ] **Step 1: Write the bench workflow**

Create `.github/workflows/bench.yml`:

```yaml
name: Bench

on:
  workflow_dispatch:
    inputs:
      sha:
        description: The rapira commit of the Nightly run that sent the dispatch. The job benches the current nightly release and logs this value.
        required: false
        default: ""
      version:
        description: The nightly version of that commit. The job logs this value.
        required: false
        default: ""

# One bench at a time. A new dispatch replaces a waiting one and never cancels a running one.
concurrency:
  group: bench
  cancel-in-progress: false

permissions:
  contents: read

defaults:
  run:
    shell: bash

jobs:
  bench:
    runs-on: ubuntu-latest
    timeout-minutes: 90
    permissions:
      contents: read
      id-token: write
    env:
      TF_BACKEND: s3
      # Every terraform init of the Makefile gets the S3 backend settings.
      TF_CLI_ARGS_init: -backend-config=s3.tfbackend -backend-config=bucket=${{ vars.TF_STATE_BUCKET }}
    outputs:
      skip: ${{ steps.resolve.outputs.skip }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false

      - name: Resolve the nightly commit
        id: resolve
        env:
          GH_TOKEN: ${{ github.token }}
          INPUT_SHA: ${{ inputs.sha }}
          INPUT_VERSION: ${{ inputs.version }}
        run: |
          set -euo pipefail
          # The core Nightly workflow deletes older assets when it moves the tag,
          # so the job benches the commit the tag points to now.
          sha=$(gh api repos/rapira-rs/rapira/commits/nightly --jq .sha)
          sha7=${sha:0:7}
          asset=$(gh api repos/rapira-rs/rapira/releases/tags/nightly --jq '.assets[].name' | grep -E -- "-nightly\.${sha7}-php8\.5-linux-x86_64\.tar\.gz$" || true)
          if [ -z "$asset" ]; then
            echo "::error::the nightly release has no php8.5 linux x86_64 asset for $sha7"
            exit 1
          fi
          echo "nightly $sha7, asset $asset, dispatched for sha '$INPUT_SHA' version '$INPUT_VERSION'"
          # The branch has no data/index.json before the first publish.
          if ! gh api -H "Accept: application/vnd.github.raw+json" "repos/$GITHUB_REPOSITORY/contents/data/index.json?ref=gh-pages" > index.json; then
            echo '{"runs": []}' > index.json
          fi
          skip=$(python3 -c 'import json, sys; runs = json.load(open("index.json"))["runs"]; print(str(any(r["rapira_sha"].startswith(sys.argv[1]) and not r["smoke"] for r in runs)).lower())' "$sha7")
          if [ "$skip" = true ]; then
            echo "$sha7 is already on the board"
          fi
          echo "skip=$skip" >> "$GITHUB_OUTPUT"
          echo "sha7=$sha7" >> "$GITHUB_OUTPUT"

      - name: Assume the CI role
        if: steps.resolve.outputs.skip == 'false'
        uses: aws-actions/configure-aws-credentials@e1253824e5c10ff9df46874f81ed3ec929e19cfd # v6.3.0
        with:
          role-to-assume: ${{ vars.AWS_ROLE_ARN }}
          aws-region: eu-central-1
          # The credentials must last for the whole 90 minute job, the destroy included.
          role-duration-seconds: 7200

      - name: Install terraform
        if: steps.resolve.outputs.skip == 'false'
        uses: hashicorp/setup-terraform@dfe3c3f87815947d99a8997f908cb6525fc44e9e # v4.0.1
        with:
          terraform_version: 1.16.4
          # The wrapper adds text to stdout and breaks terraform output -json.
          terraform_wrapper: false

      - name: Create and provision the rig
        if: steps.resolve.outputs.skip == 'false'
        timeout-minutes: 30
        env:
          SHA7: ${{ steps.resolve.outputs.sha7 }}
        run: |
          set -euo pipefail
          if make up NIGHTLY="$SHA7" AZ=eu-central-1a 2>&1 | tee up.log; then
            exit 0
          fi
          # A capacity error retries once in the next AZ. Other errors stop the job.
          # The AWS error text is "api error InsufficientInstanceCapacity: ...".
          if ! grep -q 'api error InsufficientInstanceCapacity' up.log; then
            exit 1
          fi
          make down
          make up NIGHTLY="$SHA7" AZ=eu-central-1b

      - name: Run the ci suite
        if: steps.resolve.outputs.skip == 'false'
        timeout-minutes: 45
        run: python3 -m rig bench --suite ci --out runs

      - name: Upload the run file
        if: always() && steps.resolve.outputs.skip == 'false'
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: run
          path: runs/*/run.json
          if-no-files-found: ignore
          retention-days: 90

      - name: Upload the raw evidence
        if: always() && steps.resolve.outputs.skip == 'false'
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: raw
          path: runs/*/raw/
          if-no-files-found: ignore
          retention-days: 90

      # The report exits 1 for an incomplete run, which stops the publish job.
      - name: Report the run
        if: steps.resolve.outputs.skip == 'false'
        run: python3 -m rig report runs/*/run.json

      - name: Destroy the rig
        if: always() && steps.resolve.outputs.skip == 'false'
        run: make down

      - name: Remove tagged leftovers
        if: always() && steps.resolve.outputs.skip == 'false'
        run: make nuke

  publish:
    needs: bench
    if: needs.bench.outputs.skip == 'false'
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false

      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: gh-pages
          path: pages

      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          name: run
          path: run

      - name: Publish the run to gh-pages
        run: |
          set -euo pipefail
          # The artifact holds <id>/run.json.
          run_file=$(echo run/*/run.json)
          python3 -m rig publish --pages-dir pages "$run_file"
          cp board/index.html board/app.js board/style.css board/chart.umd.js pages/
          # GitHub Pages serves the files as they are, without a Jekyll build.
          touch pages/.nojekyll
          id=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["id"])' "$run_file")
          cd pages
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add -A
          git commit -s -m "chore: publish run $id"
          git push
```

Run: `python3 -c 'import yaml; print(sorted(yaml.safe_load(open(".github/workflows/bench.yml"))["jobs"]))'`
Expected: `['bench', 'publish']`

The checks in this task need PyYAML on the executor machine. `python3 -c 'import yaml'` must exit 0.

- [ ] **Step 2: Write the dispatch workflow for the core repository**

Create `docs/core-dispatch.yml`. It follows the four-space indentation of the core workflows:

```yaml
# Copy this file to .github/workflows/bench-dispatch.yml in rapira-rs/rapira.
# It starts the bench workflow of rapira-rs/benchmarks after a successful Nightly run.
# BENCH_DISPATCH_TOKEN is a fine-grained token with "Actions: read and write" on rapira-rs/benchmarks only.
name: Bench dispatch
on:
    workflow_run:
        workflows: [Nightly]
        types: [completed]
        branches: [main]

permissions:
    contents: read

jobs:
    dispatch:
        if: github.event.workflow_run.conclusion == 'success'
        runs-on: ubuntu-latest
        timeout-minutes: 5
        steps:
            - shell: bash
              env:
                  GH_TOKEN: ${{ secrets.BENCH_DISPATCH_TOKEN }}
                  REPO_TOKEN: ${{ github.token }}
                  SHA: ${{ github.event.workflow_run.head_sha }}
              run: |
                  set -euo pipefail
                  # The same version string as the guard job of nightly.yml.
                  CARGO_VER=$(GH_TOKEN="$REPO_TOKEN" gh api -H "Accept: application/vnd.github.raw+json" "repos/${GITHUB_REPOSITORY}/contents/.release-please-manifest.json?ref=${SHA}" | jq -er '.["."]')
                  gh workflow run bench.yml -R rapira-rs/benchmarks -f sha="$SHA" -f version="${CARGO_VER}-nightly.${SHA:0:7}"
```

- [ ] **Step 3: Check both workflows**

Run:

```bash
python3 - <<'EOF'
import re
import yaml

for path in (".github/workflows/bench.yml", "docs/core-dispatch.yml"):
    wf = yaml.safe_load(open(path))
    steps = [step for job in wf["jobs"].values() for step in job["steps"]]
    print(path)
    print("  jobs:", sorted(wf["jobs"]))
    print("  unpinned:", [s["uses"] for s in steps if "uses" in s and not re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", s["uses"])])
    print("  expressions in run:", [s.get("name", "") for s in steps if "${{" in s.get("run", "")])
EOF
```

Expected:

```
.github/workflows/bench.yml
  jobs: ['bench', 'publish']
  unpinned: []
  expressions in run: []
docs/core-dispatch.yml
  jobs: ['dispatch']
  unpinned: []
  expressions in run: []
```

An empty `expressions in run` list means that every workflow input and variable reaches the shell through `env`.

- [ ] **Step 4: Document the CI flow and the owner steps**

Append the section to `README.md`:

````bash
cat >> README.md <<'EOF'

## CI

After each successful Nightly run on the rapira main branch, the core repository starts `.github/workflows/bench.yml` in this repository. The bench job finds the commit of the current `nightly` release and stops when the board already has that commit. Then it creates the rig with the S3 backend, provisions it with the nightly asset, runs `suites/ci.toml`, and uploads `run.json` and `raw/` as artifacts for 90 days. `make down` and `make nuke` run at the end of each bench job that passes the commit check, also after a failure. A capacity error retries once in `eu-central-1b`. The publish job runs only when the run is complete. It adds the run to the `gh-pages` branch and copies `board/` there.

Only one bench run runs at a time. A new dispatch replaces a waiting one and never stops a running one. The bench job stops after 90 minutes.

Do these owner steps once, in this order:

1. Examine the tracked files for account ids, IP addresses, and keys. Then make this repository public.
2. Create the `gh-pages` branch with the commands below.
3. In the repository settings, open Pages, select "Deploy from a branch", and select the branch `gh-pages` with the folder `/`.
4. Apply `terraform/ci` as the "CI bootstrap" section shows.
5. Set the repository variables `AWS_ROLE_ARN` and `TF_STATE_BUCKET` with the commands below.
6. Create a fine-grained token for the resource owner `rapira-rs` with access to the repository `rapira-rs/benchmarks` only and the permission "Actions: Read and write". If the organization approves tokens, approve the request.
7. Store the token as the secret `BENCH_DISPATCH_TOKEN` in `rapira-rs/rapira`.
8. Copy `docs/core-dispatch.yml` to `.github/workflows/bench-dispatch.yml` in `rapira-rs/rapira` through a pull request.
9. Start the first run by hand with `gh workflow run bench.yml -R rapira-rs/benchmarks` and examine the result on the board.

Commands for step 2:

```bash
git switch --orphan gh-pages
git commit --allow-empty -s -S -m "chore: start the board branch"
git push origin gh-pages
git switch main
```

Commands for step 5 and step 7:

```bash
gh variable set AWS_ROLE_ARN -R rapira-rs/benchmarks --body "$(terraform -chdir=terraform/ci output -raw role_arn)"
gh variable set TF_STATE_BUCKET -R rapira-rs/benchmarks --body "$(terraform -chdir=terraform/ci output -raw bucket)"
gh secret set BENCH_DISPATCH_TOKEN -R rapira-rs/rapira
```
EOF
````

Run: `grep -c '^## CI$' README.md && grep -nP '[\x{2013}\x{2014}]' README.md; echo "dashes=$?"`
Expected: `1` and `dashes=1`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/bench.yml docs/core-dispatch.yml README.md
git commit -s -S -m "ci: bench each rapira nightly build and publish it to the board"
```
