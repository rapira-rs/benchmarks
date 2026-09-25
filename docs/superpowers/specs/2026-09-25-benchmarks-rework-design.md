# Benchmarks rework: design

Date: 2026-09-25. Status: approved in conversation, written for review.

This document defines the rework of the rapira benchmark rig. It replaces the fixed-duration closed-loop method with a staged rate ladder, replaces the single loader with several small loaders, defines one result format, adds a FrankenPHP HTTP row set, and adds a per-merge CI run with a public board.

## 1. Goals

- Run one benchmark suite from CI on AWS after every merge to the rapira main branch, in about 45 minutes and for about $3.
- Publish every run to a live board with history per target.
- Measure with a staged rate ladder: raise the request rate in stages until the target fails, and report the last stage it held and the throughput it reached in the failing stage.
- Save every run in one machine-readable format that a script can compare across runs.
- Add a fair FrankenPHP HTTP row set.
- Keep manual runs for a rapira PR against main and for the full row set.
- Remove duplicated drivers, ad-hoc formats, and hard-coded rig assumptions.

## 2. Decisions

These answers were given by the repository owner during the design and are fixed for the plan.

| Question | Decision |
| --- | --- |
| Per-merge budget | About 45 minutes and about $3. The 32-core server stays. Three or four small loaders. One round. The full ladder. |
| Method | The ladder replaces the saturated wrk pass, the low-concurrency wrk pass, and the k6 probe. |
| Load tool | wrk2 for HTTP/1.1 targets, k6 for gRPC targets. |
| Loaders | Four `c7a.xlarge`. |
| Board and history | The `gh-pages` branch of this repository. This repository becomes public. |
| Trigger | The core repository dispatches a workflow in this repository after its Nightly workflow publishes the build. |
| Rapira binary in CI | The nightly release asset for the merged commit. Manual runs can still build a ref on the server. |
| Driver language | Python 3, standard library only, on the operator side. Bash on the boxes. |
| Naming | The thing under test is a target. A ladder step is a rate. |
| AWS auth | Local runs use the default AWS CLI profile. No `PROFILE` knob, no Terraform `profile` variable, no profile name in the repository. CI uses the OIDC role. |

## 3. Rig and provisioning

### 3.1 Terraform

The rig stack in `terraform/` creates one server instance and `var.loader_count` loader instances. Defaults: server `c7a.8xlarge`, loaders `c7a.xlarge`, four loaders. All instances share the cluster placement group, the security group, the key pair, and the AMI. Outputs are `server_public_ip`, `server_private_ip`, `loader_public_ips` (list), `loader_private_ips` (list), `ami_id`, `server_instance_type`, `loader_instance_type`, `loader_count`, and `key_file`.

The quota check in the Makefile sums the vCPUs of the server and all loaders. The instance types and the loader count are `?=` knobs.

The loader size arithmetic: a wrk-class tool delivers about 80k req/s per loader core on this rig. Four `c7a.xlarge` give 16 cores, about 1.3M req/s, which is the ceiling the current single `c7a.4xlarge` loader reaches. Each loader moves at most about 1.1 Gbps for the hello workload, under the 1.562 Gbps baseline of a `c7a.xlarge`, so no loader depends on network burst credits. Four `c7a.xlarge` cost the same as one `c7a.4xlarge`.

### 3.2 CI bootstrap stack

A second stack in `terraform/ci/` is applied once by the owner with the default AWS CLI profile. It creates the GitHub OIDC identity provider, an IAM role that trusts `repo:rapira-rs/benchmarks` on the main branch, and an S3 bucket for the rig tfstate with the native state lockfile. The role policy allows the EC2 actions the rig stack needs in eu-central-1, the service quota read, and read and write on the state bucket. The rig stack uses the S3 backend when `TF_BACKEND=s3` is set, which the CI workflow sets. Local runs keep the local state.

### 3.3 Rapira binary

In CI the server downloads `rapira-v<version>-nightly.<sha7>-php8.5-linux-x86_64.tar.gz` from the `nightly` release of the core repository, verifies it against the published `SHA256SUMS` file, and unpacks it under `/opt/bench/rapira/<sha7>/`. The core Nightly workflow deletes the assets of older builds when it moves the `nightly` tag, so the bench resolves the SHA of the current `nightly` release at the start of the run and benches that commit. If that SHA is already on the board, the run stops before it creates the rig.

Manual runs keep `REF=<ref>` and `BASE_REF=<ref>` builds on the server, and `make sync` for a local working tree. A manual run records `build = "server"` and its rustflags; a CI run records `build = "nightly"` and the asset name.

### 3.4 Shared php.ini

One `servers/php.ini` applies to every PHP runtime: `opcache.enable=1`, `opcache.enable_cli=1`, `opcache.validate_timestamps=0`, `opcache.jit=disable`, `opcache.memory_consumption=256`, `memory_limit=256M`, `realpath_cache_size=4096K`, `realpath_cache_ttl=600`, `expose_php=0`, `display_errors=0`, `log_errors=1`, `error_reporting=E_ALL & ~E_DEPRECATED`, `error_log` to stderr. The last three matter because `PHPRC` replaces the distribution ini, and the built-in defaults print notices into response bodies. It reaches rapira and FrankenPHP through the `PHPRC` environment variable and php-fpm through `-c`. The run file records the ini text.

### 3.5 FrankenPHP

FrankenPHP is the glibc release asset `frankenphp-linux-x86_64-gnu` at a pinned version, 1.12.7 at the time of writing. The FrankenPHP performance guide states that the musl build is slower for thread-safe PHP and recommends glibc. Provisioning records the output of `frankenphp version`, which names the FrankenPHP, PHP, and Caddy versions. Every FrankenPHP Caddyfile sets `grace_period 2s` so a stop is a clean TERM.

### 3.6 Applications

Symfony and Laravel get committed `composer.lock` files under `apps/symfony/` and `apps/laravel/`, so a row is reproducible across dates. Provisioning runs `composer install` from the lock file. RoadRunner and the PECL protobuf extension install only when the suite has a gRPC target.

The nightly tarball ships no PHP headers, so the PECL protobuf extension cannot be built for the rapira gRPC target in CI. In CI both gRPC targets run the pure-PHP protobuf runtime so they stay a fair pair. A manual run with a server build loads the extension for both. A follow-up in the core repository can bundle ext-protobuf into the nightly build the way igbinary and redis are bundled.

### 3.7 Loaders

Each loader gets wrk2 built from a pinned upstream commit, the pinned k6 rpm, the sysctl port range, and the nofile limit. Provisioning checks that chrony reports a synchronized source, because the lockstep start uses wall-clock time. Each loader records `wrk2 --version` output (commit) and `k6 version`.

### 3.8 TTL and teardown

The cloud-init TTL mechanism is unchanged and applies to every box. The driver extends the TTL from its run estimate as today. In CI, `terraform destroy` runs in an `always()` step and `nuke` runs after it as a second guard.

## 4. Targets, suites, identity

### 4.1 Target registry

`suites/targets.toml` defines every target. A target has:

- `name`: the key, for example `hello-rapira-worker`.
- `server`: `rapira`, `frankenphp`, `php-fpm`, `nginx-rapira`, or `roadrunner`.
- `app`: `hello`, `symfony`, `laravel`, `static`, or `grpc`.
- `mode`: `worker`, `classic`, or `dispatcher`.
- `proto`: `http1` or `grpc`.
- `binary`: for targets that run a rapira binary, `pr` or `base`; absent for other servers.
- `start`: the arguments to the on-box start script.
- `url`: the request path and query.
- `expect`: the file with the exact expected response body.
- `config`: the config template the start script renders, for the record.

The driver reads the file with `tomllib`. The on-box scripts take the `start` arguments and know nothing about suites.

### 4.2 Suites

A suite file lists targets, rounds, and the ladder floor per app. `suites/ci.toml` is the per-merge suite. `suites/full.toml` is the manual three-round suite. `suites/ab.toml` runs the rapira `base` and `pr` binaries on hello and Symfony. `make bench SUITE=ci` selects a suite. One `bench` target replaces the five suite targets; `up`, `provision`, `status`, `extend`, `sync`, `report`, `down`, and `nuke` stay.

The CI suite starts with these 16 targets:

| app | targets |
| --- | --- |
| hello | rapira worker, rapira dispatcher, rapira classic, FrankenPHP worker, php-fpm |
| symfony | rapira worker, rapira classic, FrankenPHP worker, FrankenPHP classic, php-fpm |
| laravel | rapira worker (Octane bridge), FrankenPHP worker (Octane) |
| static | rapira hit, FrankenPHP hit, both on the 128 B asset |
| grpc | rapira, RoadRunner |

The full suite adds the nginx-rapira worker rows, the FrankenPHP stock `php_server` worker row, the static miss and plain rows, the 27 KiB asset, and the gRPC-Web and Connect variants.

### 4.3 FrankenPHP rows

The FrankenPHP worker row uses the production shape from the FrankenPHP docs: `php_server` with `file_server off`, the worker file with `match *`, so no request stats the docroot. The classic row uses `php_server` with `file_server off` and `try_files {path} index.php`. The worker row pins `num_threads` to the worker count plus one, because FrankenPHP needs more threads than workers. The classic row pins `num_threads` to `PROCESSES`, the pool size of php-fpm. Both set `admin off`, `auto_https off`, no `encode`, no access log. The stock `php_server` worker row exists in the full suite so the cost of the default shape is visible next to the tuned row. Autoscaling, compression, HTTP/2, HTTP/3, and TLS rows are out: they change the wire and would be unfair to servers measured over HTTP/1.1.

### 4.4 Cell identity

A cell is one target in one round. Its identity in the run file is the target fields plus `round`, the binary identity of the server (rapira SHA and binary SHA-256, or the competitor version line), and the app hash. Nothing is parsed out of a name.

### 4.5 Pool sizes

Every target runs `PROCESSES` workers, which defaults to the server CPU count. The start script verifies the worker count after start and fails the start when the count differs. FrankenPHP is verified from its startup log line and a mismatch fails the start.

## 5. Load ladder and measurement

### 5.1 Tools and their verified limits

wrk2 is built from the pinned upstream commit `44a94c1` (2019-09-23). On current binutils the stock build exits 0 but produces a broken bytecode object: the symbol `luaJIT_BC_wrk` is truncated and every `-s script.lua` run panics. Provisioning applies the one-line fix in `deps/luajit/src/jit/bcsave.lua` (the string table size `#symname+1` becomes `#symname+2`) and then asserts that `nm obj/bytecode.o` prints `luaJIT_BC_wrk`. Build dependencies are `gcc`, `make`, `openssl-devel`, and `git`.

Verified wrk2 facts that shape the ladder:

- `-R` is the total request rate of the process, split evenly over threads and connections.
- `-c` is divided by `-t` with integer division and the remainder is dropped silently, so `-c` must be a multiple of `-t`.
- The first 10 s of a run are a calibration window. The latency histogram is reset after it; the request count is not. A 20 s stage therefore reports throughput over 20 s and latency over the last 10 s. A stage under 10 s reports unfiltered latency and a NaN rate row.
- The `status` error counts responses with a status above 399. The `timeout` counter is a per-connection tally taken every 2 s, not a request count. Under overload wrk2 reports no errors: the achieved rate falls under the requested rate and the corrected latency grows into seconds.
- The `done()` hook receives the coordinated-omission-corrected histogram. The reporter script prints one line `RESULT {json}` with duration, requests, bytes, the five error counters, mean, p50, p90, p95, p99, p99.9, max in microseconds, and requests per second. An empty histogram yields NaN from the mean, which the script maps to 0.
- wrk2 speaks HTTP/1.1 only. gRPC targets use k6.
- At 200k req/s over loopback one wrk2 process used about 44% of one core. The sizing basis stays the network measurement on this rig, about 80k req/s per loader core, which gives a `c7a.xlarge` loader headroom for its quarter of every stage up to the hello ceiling.

k6 runs the gRPC targets with the `ramping-arrival-rate` executor: one k6 process per stage, a one second ramp from 0 to the stage rate and then the rate for the rest of the stage, because the first call of every VU opens its connection and a constant rate from the start drops iterations in that window. `preAllocatedVUs` is sized to the stage rate times the expected latency with margin, and `handleSummary` writes one JSON per stage. A 20 s stage achieves about 97.5% of the rate, inside the pass rule. The k6 script keeps the byte-exact response checks of today.

### 5.2 Stages

A stage is one constant request rate held for `stage_s` seconds, default 20. The stage list per app is geometric with ratio 2 from the floor in the suite file: hello 10k, symfony 10k, laravel 5k, static 10k, grpc 10k. The list has no rate bound; the run stops at the first failing stage. The driver caps a cell at 20 stages as a guard against a loop.

Every loader runs one process per stage with rate `stage_rate / loader_count`, threads equal to its vCPU count, and 64 connections, which is 256 connections in total with four loaders, the Yii3 setting. Connections are a multiple of threads by construction.

The load side must be able to break every target. The four loaders are sized for about 1.3M req/s on hello, far above the 200k req/s top of the Yii3 ladder, and a ladder stops only at a failing stage or a void. A cell whose failing stage carries `generator_bound` is a floor, and with this sizing that happens only near the hello ceiling on rapira. The gRPC targets are the exception: k6 on 16 loader cores reaches an estimated 100k to 160k req/s, below the rapira gRPC ceiling of about 810k. The first CI run measures that k6 limit; if the RoadRunner gRPC target is also generator-bound, the follow-up in section 13 applies.

### 5.3 Cell sequence

1. Start the target on the server, verify the listener pid, the executable, and the worker count.
2. From every loader, one byte-exact probe against the expected response file. A mismatch on any loader voids the cell before load.
3. Warm-up: 10 s at the floor rate from every loader, output discarded.
4. For each stage: compute the start time as now plus 3 s, then start in one parallel ssh batch one load process per loader with a sleep until that time and three timed snapshot jobs per box: at the start time, at the middle of the stage, and 0.5 s after the end of the stage. The busy CPU, the ENA deltas, and the TIME-WAIT growth come from the start and end samples, so the 3 s lead and the ssh round trips stay outside the busy window. The server sample in the middle of the stage also reads the connection states and the memory. Collect the `RESULT` line from each loader, merge, evaluate the pass rule, and stop when the stage fails.
5. After a failing stage, one byte-exact probe from one loader. A failed probe sets the `died` flag: the target stopped answering. A passing probe means the target saturated but survived.
6. Stop the target, verify the pid tree is gone, read the server log.

Provisioning verifies that chrony is synchronized on every box, which makes the shared start time valid to well under one second.

### 5.4 Merge across loaders

Per stage the driver sums `requests`, `bytes`, and each error counter over loaders and computes `achieved_rps = sum(requests) / stage_s` and `successful_rps = (sum(requests) - sum(status errors)) / stage_s`. Each latency percentile is the maximum over loaders, which is a bound, not a pooled percentile; the per-loader records stay in the stage so a reader can see the spread. A loader whose `RESULT` line is missing makes the stage invalid, and an invalid stage voids the cell with the reason.

### 5.5 Pass rule and stop rule

A stage passes when `achieved_rps >= 0.95 * rate` and every error counter on every loader is 0. The run stops after the first failing stage.

The cell reports:

- `held`: the highest passing rate with its stage index. Its latency percentiles are the ones of that stage record. Null when the first stage fails.
- `peak`: `successful_rps` of the failing stage, the continuous number for regression tracking. Null when no stage failed, which happens only at the 20-stage cap; the cell then carries `ladder_exhausted`.
- `unloaded`: the p50 and p99 of the first stage, the replacement for the old low-concurrency pass.

With rounds above one the reported held and peak are the medians over surviving cells and the spread is `100 * (max - min) / median`, as today.

### 5.6 Flags and voids

Flags carry values and are review items, not failures:

- `generator_bound`: in the failing stage any loader busy CPU is at or above 85% while the server is below 90%. The peak is then a floor and the board draws it as one.
- `server_unsaturated`: the failing stage has the server below 90% and every loader below 85%. The target failed for a reason other than CPU, for example pool queueing.
- `ena_throttled`: the server ENA allowance counters changed during a stage, with the deltas.
- `worker_churn`: the worker pid list changed during the cell.
- `log_growth`: the server log grew by more than 64 KiB during the cell, with the byte count.
- `keepalive_broken`: the server TIME-WAIT count grew by more than the total connection count during a stage.
- `died`: the probe after the failing stage failed.

Voids exclude the cell from every number and make the run incomplete:

- The target did not start or its worker count differed from `PROCESSES`.
- A pre-load probe mismatched on any loader.
- A loader produced no `RESULT` line for a stage.
- A loader ENA allowance counter changed during a stage: the loader was shaped, so the stage is not a server measurement.
- A rapira target logged a WARN or ERROR line during the cell. Rapira runs at log level `warn` and the lines are kept in the raw directory. Competitor targets keep only `log_growth`.

### 5.7 Time budget

One stage costs `stage_s` plus about 5 s of ssh and snapshot overhead. A hello target on rapira climbs from 10k to about 1.28M in eight stages, about 3.5 min with start, warm-up, and stop. Framework targets fail after three to six stages, 1.5 to 2.5 min. The 16 CI targets sum to about 36 min of cells, and the first CI runs calibrate the floors.

## 6. Result format

### 6.1 The run file

Each run writes `runs/<id>/run.json` with `schema = "rapira-bench-run/1"`. The run id is `<UTC timestamp>-<suite>-<rapira sha7>`.

Top level:

- `schema`, `id`, `suite` (name, file SHA-256), `smoke` (bool), `started`, `finished`.
- `rig`: server and loader instance types, loader count, AZ, AMI, kernel, placement group.
- `rapira`: `ref`, `sha`, `version`, `build` (`nightly` or `server`), `asset`, `binary_sha256`, `rustflags`, `dir` (the install directory on the server), and `base` (the same record for the base build, or null).
- `servers`: one version line per competitor server (FrankenPHP, php-fpm and its PHP, nginx, RoadRunner) and the shared php.ini text.
- `apps`: SHA-256 of every app file and the composer.lock files.
- `loaders`: per loader the instance id, private IP, wrk2 commit, k6 version.
- `ladder`: the stage list actually used per app, the stage duration, the pass rule parameters.
- `processes`: the worker count.
- `plan`: the ordered list of cell keys.
- `cells`: the list of cells.
- `status`: `complete` or `incomplete`.
- `reasons`: the list of reasons when the status is incomplete.
- `reporter`: the version of the report tool that last rendered the run.

A cell:

- `key`, `target` (all registry fields), `round`.
- `status`: `ok`, `void` (with `reason`), or `incomplete`.
- `flags`: an object with values, for example `generator_bound: true`, `ena_throttled: {"pps_allowance_exceeded": 946}`, `worker_churn: true`, `log_growth: 70000`.
- `held`: the highest passing rate, with its stage index; null when no stage passed.
- `peak`: the successful req/s in the first failing stage; null when every stage passed.
- `unloaded`: the p50 and p99 of the first stage.
- `stages`: the list of stages.

A stage:

- `rate`: the planned total req/s.
- `duration_s`.
- `pass`: bool, with `fail_reason` when false.
- `merged`: `requests`, `successful`, `bytes`, `errors` (`connect`, `read`, `write`, `status`, `timeout`, `dropped`), `achieved_rps`, `successful_rps`.
- `latency_us`: `p50`, `p90`, `p99`, `p999`, `max`, each the maximum over loaders.
- `loaders`: one record per loader with its own counts, its percentiles, `busy_cpu`, and ENA deltas.
- `server`: `busy_cpu`, `pss_kb`, `established`, `time_wait`.

All numbers are JSON numbers.

### 6.2 Raw evidence

`runs/<id>/raw/` keeps the wrk2 and k6 output per stage per loader, the rendered server configs, the server logs, and the snapshots. In CI the directory is uploaded as a workflow artifact. It is never committed.

### 6.3 Tools

`rig/report.py <run.json>` renders text tables: one row per target with held, peak, the p99 at the held stage, the number of surviving rounds, and the flags. It exits nonzero when the run is incomplete and prints the publication warning. `rig/compare.py <a.json> <b.json>` prints per-target deltas of held and peak with both spreads, and refuses pairs whose rig identity differs unless `--force` is given.

## 7. Board

The `gh-pages` branch holds `data/<run-id>.json`, `data/index.json` (a manifest with id, date, suite, rapira sha and version, status, smoke), and the board files. The board is static HTML and JavaScript under `board/` in the main branch, copied to `gh-pages` by the publish job, with Chart.js vendored as one pinned file.

The board is one page with one chart per target, grouped under one heading per app, sorted by app and then by target name. There is no run selector and no second view. The layout follows perf.rust-lang.org: a run is one merged commit on the rapira main branch, because the nightly build that the CI benches exists only after a merge.

One chart:

- The x axis lists the newest 60 runs that are not smoke runs, in `started` order, each labelled with the first 7 characters of the rapira sha.
- One line per ladder rate that the target held in at least one of those runs, in ascending rate order. A rate that only failed has no line.
- The y axis is the p99 latency in milliseconds at that rate, on a logarithmic scale.
- A point exists only where the stage passed. The value is the median over the ok cells of the target in that run. A failing stage, a voided cell, and a run without the target give no point, so the highest line with a point is the held rate of that run.
- The tooltip of a point shows the sha, the rapira version, the rate, the p99, and the flags of the cell.

The peak is not on the chart. `rig report` and `rig compare` carry it. Smoke runs are in the manifest with `smoke = true` and are not shown.

## 8. CI

### 8.1 Trigger

A workflow in the core repository runs on the completion of its Nightly workflow for a push to main and calls `workflow_dispatch` on `rapira-rs/benchmarks` with the version and SHA. It uses a fine-grained token stored in the core repository, scoped to actions on this repository.

### 8.2 Bench workflow

`.github/workflows/bench.yml` in this repository:

1. Resolve the SHA of the current `nightly` release. Stop when that SHA is already in `data/index.json` on `gh-pages`.
2. Assume the OIDC role.
3. `terraform apply` with the S3 backend.
4. Provision, run `suites/ci.toml`, collect `run.json` and `raw/`.
5. Upload `raw/` as an artifact with 90 days retention, and `run.json` as an artifact.
6. `terraform destroy` in an `always()` step, then `nuke` as a second guard.

A concurrency group serializes bench runs and does not cancel a running one. A dispatch that arrives while one waits replaces the waiting one. A capacity error retries once in the next AZ. The bench job has a timeout of 110 minutes (70 minutes for the suite), and the TTL on the boxes is armed at 60 minutes and extended by the driver as today.

### 8.3 Publish job

A second job in `bench.yml`, after the bench job, checks out `gh-pages`, writes `data/<run-id>.json`, appends to `data/index.json`, copies `board/`, commits with the run id, and pushes. The board is served by GitHub Pages from `gh-pages`.

### 8.4 Repository visibility

The repository becomes public before the first CI run. The tracked files are audited for account ids, IP addresses, and keys before the switch. The secrets already stay out of git: tfstate, the key, the tfvars, and INSTRUCTIONS.md.

## 9. Repository layout

```
Makefile
README.md              operations
METHOD.md              methodology and review checklist
NOTES.md               dated records
terraform/             rig stack
terraform/ci/          bootstrap stack: OIDC, role, state bucket
rig/                   operator-side Python driver, report.py, compare.py
box/                   on-box bash: provision-server.sh, provision-loader.sh, target start and stop, probe, snapshot
servers/               config templates per server and php.ini
apps/                  hello, symfony, laravel, static, grpc
loader/                wrk2 reporter script, k6 scripts
suites/                targets.toml, ci.toml, full.toml, ab.toml
board/                 static board
tests/                 Python unit tests and the container test
.github/workflows/     bench.yml with the bench and publish jobs
runs/                  local run output, ignored
```

## 10. Docs

`README.md` keeps the operator flow, the knobs, the cost, and the teardown. `METHOD.md` is new and tracked: the ladder, the pass rule, the flags and their thresholds, the voiding rules, the review checklist before publication, and the reading guide for the board. It takes the content of the local INSTRUCTIONS.md and rewrites it for the ladder. `NOTES.md` keeps its records and gains an entry that marks the change of method, so old and new numbers are never mixed. The `docs` entry leaves `.gitignore`. All docs follow the STE rules of the repository.

## 11. Tests

- `tests/test_ladder.py`: the stage list generation, the pass rule, and held and peak selection, as flat case tables with names. The stop rule (a passing stage then a failing stage ends the cell) is a driver rule and is tested in `tests/test_bench.py`.
- `tests/test_merge.py`: merging loader records into a stage, including a loader with missing output, a loader with errors, and the maximum-over-loaders percentiles.
- `tests/test_flags.py`: every flag rule with values at, below, and above its threshold.
- `tests/test_runfile.py`: the writer produces the schema, typed numbers, and the status rules.
- `tests/test_report.py` and `tests/test_compare.py`: table tests on synthetic run files.
- `tests/test_box.py`: the container test for the start and stop scripts, kept from `test_nginx.py`.

Expected values are derived from the rules in `METHOD.md` before the implementation is read.

## 12. Removals

- The five drivers, `remote-lib.sh`, `leg.sh`, `fleet-leg.sh`, and the wrk and lowc passes.
- The old `report.py` tables and the text-only result format.
- The v0.8.x CLI launch path.
- The `franken-static-miss` duplicate row and the swoole remnants in the docs.
- The fixed pair: `SERVER_TYPE :=`, `LOADER_TYPE :=`, the `sv+lv` quota check, and the "both boxes" wording.
- The hard-coded ports on the operator side; the port is one value in the driver.
- The `PROFILE` knob, the Terraform `profile` variable, and every profile name in the Makefile, README, nuke script, and Terraform defaults.

Not removed: the ad-hoc directories under `results/` stay in place and untouched. New runs go to `runs/`.

## 13. Follow-ups outside this rework

- Core repository: bundle ext-protobuf into the nightly build so the CI gRPC targets can use the extension.
- A prebuilt AMI with the packages and the apps, to cut provisioning time, when the budget needs it.
- A faster gRPC generator than k6 for the gRPC targets, so those rows break the servers instead of reading as floors. Candidates are evaluated against the dependency rule before adoption.
