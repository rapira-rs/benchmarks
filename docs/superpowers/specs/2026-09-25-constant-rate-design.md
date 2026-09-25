# Constant-rate benchmark design

Date: 2026-09-25. Status: approved by the owner in conversation; this document is the authority for the implementation plan. It replaces sections 4 to 8 and 11 to 12 of `2026-09-25-benchmarks-rework-design.md`; the rig, provisioning, and CI mechanics of that document stay where this one does not change them.

## 1. Goals

- Bench rapira only: its classic, worker, and dispatcher modes on a hello app, the dispatcher with the static middleware on, the Yii3 app-api on the dispatcher, and gRPC echo.
- Measure at one constant request rate per app, from one loader, and report two numbers per target: the p99 latency and the RSS of the rapira process tree, next to the achieved rate.
- Show the history on two charts, p99 and RSS, with the merged pull request as the x label and a click that opens it.
- Keep everything else from the rework: the EC2 rig, the box protocol, the run file discipline, the CI path, the flags and voids.

## 2. Decisions

- The competitor servers, the frameworks, the static-file app, the gRPC-Web and Connect variants, k6, the rate ladder, and the `full` and `ab` suites are removed.
- HTTP rows run at 250000 req/s with 5000 connections. The gRPC row runs at 100000 req/s. A target that cannot hold its rate is not a failure: the row shows the achieved rate next to the p99, and `held` is false.
- The stage is 60 s of measured load after a 10 s warm-up at the same rate.
- RSS is the sum of `VmRSS` over the rapira process tree, read once at the end of the stage.
- One loader box, `c7a.2xlarge`, generates every load; `LOADER_COUNT` stays a knob and defaults to 1.
- h2load generates the gRPC load. k6 cannot hold 100000 req/s from a few boxes, ghz has one maintainer, and h2load is the nghttp2 tool that the same Fedora image already served for the closed-loop gRPC numbers of 2026-09-24.
- The suite is `ci` only.

## 3. Rig and provisioning

- Terraform: `loader_count` defaults to 1 and `loader_instance_type` to `c7a.2xlarge`. The server stays `c7a.8xlarge`. Nothing else changes in `terraform/`.
- Loader provisioning installs wrk2 (unchanged, pinned commit) and the `nghttp2` package for h2load. k6 goes. `/opt/bench/loader.json` records `wrk2_commit` and `h2load_version` (the first line of `h2load --version`).
- Server provisioning installs PHP with opcache and the shared `servers/php.ini`, the rapira binary (the nightly asset or a build of `REF`), and for the apps of the suite: Composer, the PHP modules of the Yii3 app (`php-mbstring`, `php-xml`, `php-pdo`, `php-pgsql`), Valkey, the Yii3 app, and the pure PHP protobuf runtime of the gRPC app from `apps/grpc/composer.json` and its committed `composer.lock` into `/opt/bench/apps/grpc/vendor`. FrankenPHP, php-fpm, nginx, RoadRunner, Symfony, Laravel, the PECL protobuf extension, and the base build of `BASE_REF` are no longer installed. `/opt/bench/versions.json` records `php`, and with the Yii3 app `valkey` and `yii3` (the commit), and with the gRPC app `protobuf` (the runtime version).
- The Yii3 app: `apps/yii3/source.toml` pins `repo = "https://github.com/Yii3-Benchmarks/app-api"` and `commit` (the master head on 2026-09-25, `0fa2e2a`). Provisioning clones that commit to `/opt/bench/apps/yii3`, copies the committed `apps/yii3/composer.lock` over it, and runs `composer install --no-dev --classmap-authoritative --no-scripts`. The app keeps its compiled routes in its PSR-16 cache, which is `yiisoft/cache-redis` on `127.0.0.1:6379`, and reads that cache when a process builds its container, so the server runs the Fedora `valkey` service. The app defaults to the `prod` environment without debug when `APP_ENV` and `APP_DEBUG` are unset, so nothing sets them. Its `worker-rapira.php` entry point detects the rapira mode at startup and supports all three modes. The `/` endpoint returns the 65 byte JSON body `{"status":"success","data":{"name":"My Project","version":"1.0"}}` without a database; `apps/yii3/expect.json` holds that body byte for byte. `make lock` creates `apps/yii3/composer.lock` with `composer install` from the pristine `composer.json` of the pinned commit (`composer update` would also bump the constraints of that file) and captures `expect.json` with `php -S` and `public/index.php`. It runs on the operator machine with PHP 8.5, Composer, and a Valkey or Redis server on `127.0.0.1:6379`, for example `docker run --rm -d -p 127.0.0.1:6379:6379 valkey/valkey:9.1.2-alpine`. `make lock` also writes `apps/grpc/composer.lock`.
- The gRPC app keeps in `apps/grpc` the descriptor, the request and response classes, the dispatcher, `echo.grpc` (the request frame), and `expect.grpc` (the response frame), and gains `composer.json` with `google/protobuf` and its lock. The RoadRunner worker, the RoadRunner service interface and its buf plugin, and the gRPC-Web and Connect fixtures go.

## 4. Targets and the suite

`suites/targets.toml` defines six targets. The name form stays `<app>-rapira-<mode>` with a `-static` suffix for the static middleware; the gRPC target is `grpc-rapira`. Every target has `server = "rapira"`. The `binary` field, the `-base` twins, `BASE_REF`, and the base build are gone with the `ab` suite.

| target | app | mode | config template | request |
| --- | --- | --- | --- | --- |
| hello-rapira-classic | hello | classic | servers/rapira/http.toml.tpl | GET /?name=you |
| hello-rapira-worker | hello | worker | servers/rapira/http.toml.tpl | GET /?name=you |
| hello-rapira-dispatcher | hello | dispatcher | servers/rapira/http.toml.tpl | GET /?name=you |
| hello-rapira-dispatcher-static | hello | dispatcher | servers/rapira/static.toml.tpl | GET /?name=you |
| yii3-rapira-dispatcher | yii3 | dispatcher | servers/rapira/http.toml.tpl | GET / |
| grpc-rapira | grpc | dispatcher | servers/rapira/grpc.toml.tpl | POST /bench.v1.EchoService/Echo |

The static row enables rapira's static middleware with the static root of the hello app; the request misses the root and reaches PHP, so the row shows the cost of the middleware on the PHP path.

The suite file `suites/ci.toml`:

```toml
name = "ci"
rounds = 1
warmup_s = 10
duration_s = 60
smoke = false
targets = ["hello-rapira-classic", "hello-rapira-worker", "hello-rapira-dispatcher", "hello-rapira-dispatcher-static", "yii3-rapira-dispatcher", "grpc-rapira"]

[rates]
hello = 250000
yii3 = 250000
grpc = 100000

[connections]
http1 = 5000
grpc = 100

[grpc]
streams = 100
```

`Target` loses `binary`; its `proto` field selects wrk2 for `http1` and h2load for `grpc`. `Suite` loses `stage_s`, `connections`, and `floors` and gains `warmup_s`, `duration_s`, `rates` (one per app), `connections` (per proto), and `grpc_streams`. `load_suite` refuses a suite when an app of a target has no rate, when a proto of a target has no connection count, when `duration_s` is under 30, or when a rate or a connection count is not a multiple of the loader count. The driver refuses the run before it creates the run directory when `connections.http1` is not a multiple of the loader count times the loader vCPU count, as today. `plan_cells` and the round rotation stay.

## 5. Load and measurement

### 5.1 Cell sequence

1. Start the target with `box/target.sh start` and verify the listener, the executable, and the worker count.
2. One byte-exact probe from the loader with `box/probe.sh`. A mismatch voids the cell.
3. One load stage: the driver computes the start time as now plus 3 s and sends, in one parallel ssh batch, the load process and two timed snapshot jobs per box (server and loader) at the start of the measured window and 0.5 s after its end. For an HTTP target the load is wrk2; for the gRPC target it is h2load. The warm-up is part of the same process, so the target is warm when the measured window starts.
4. At the end of the stage the driver reads the RSS of the target with `box/target.sh mem`.
5. One byte-exact probe after the stage. A failed probe sets the `died` flag.
6. Stop the target, verify the process tree is gone, read the WARN and ERROR lines of its log.

### 5.2 wrk2 (HTTP rows)

`box/load.sh wrk2 EPOCH RATE THREADS CONNS WARMUP_S DURATION_S URL [METHOD] [BODY_FILE] [HEADER...]` runs one wrk2 process for `WARMUP_S + DURATION_S` seconds at `RATE` req/s with `CONNS` connections and `THREADS` threads. wrk2 resets its latency histogram after its own 10 s calibration window, so the warm-up is the calibration window and the reported latency covers the measured 60 s. `requests` in the `RESULT` line counts the whole run; the driver divides the requests by `WARMUP_S + DURATION_S` for the achieved rate. `loader/wrk2-report.lua` stays. The defaults are 5000 connections and one thread per loader vCPU.

### 5.3 h2load (gRPC row)

`box/load.sh h2load EPOCH RATE THREADS CONNS STREAMS WARMUP_S DURATION_S URL BODY_FILE` runs one h2load process over h2c: `-t THREADS`, `-c CONNS`, `-m STREAMS`, `--rps RATE/CONNS` (the rate per client), `--warm-up-time WARMUP_S`, `-D DURATION_S`, `-d BODY_FILE`, the gRPC headers (`content-type: application/grpc`, `te: trailers`), and `--log-file` to a temporary file. h2load writes one log line per request that ended in the measured window (the `--log-file` write of `src/h2load.cc` in nghttp2 is inside the main duration phase, so the warm-up requests are not in the log): the start time in microseconds since the epoch, the HTTP status or -1 for a failed stream, and the response time in microseconds, separated by tabs. A new script `loader/h2load-report.py LOG_FILE DURATION_S` (Python 3 standard library) reads that log and prints the same `RESULT` line as wrk2: `requests`, `duration_us` (`DURATION_S` in microseconds), `requests_per_sec`, `bytes` (0, h2load does not report it per request), `errors` with `status` (the lines whose status is not 200, the -1 lines included), `connect`, `read`, `write`, `timeout`, and `dropped` at 0, and `latency_us` with `mean`, the nearest-rank `p50`, `p90`, `p95`, `p99`, `p999`, and `max`. A failed h2load process leaves no `RESULT` line, as a failed wrk2 does. h2load does not read the `grpc-status` trailer, so a gRPC error inside a 200 response is invisible to the counters; the probes before and after the stage are the correctness check, and a target that answers errors fails the probe after the stage.

### 5.4 Merge and numbers

One loader gives one record; with `LOADER_COUNT` above 1 the merge of the rework applies (sums of requests and errors, the maximum of each percentile). The cell record carries:

- `rate`: the requested rate.
- `achieved_rps`: the merged requests divided by the window the tool counted: `warmup_s` plus `duration_s` for wrk2, which counts the whole run, and `duration_s` for h2load, which counts the measured window only.
- `successful_rps`: the achieved rate without status errors.
- `errors`: the merged error counters.
- `latency_us`: `p50`, `p90`, `p99`, `p999`, `max`.
- `rss_kb`: the RSS of the rapira process tree at the end of the stage.
- `held`: true when `achieved_rps` is at least 95% of `rate` and every error counter is 0.
- `cpu`: `server_busy` and `loader_busy` (the maximum over loaders) in percent over the measured window.
- `flags`: `generator_bound` and `server_unsaturated` when `held` is false, `ena_throttled`, `keepalive_broken`, `worker_churn`, `log_growth`, `died`, with the values of the rework.

The voids of the rework stay: a start failure, a probe mismatch, a loader without a `RESULT` line, a late loader, a loader ENA delta, a rapira WARN or ERROR line, an ssh failure, a failed stop.

### 5.5 RSS

`box/target.sh mem TAG SERVER` prints `rss_kb`: the sum of `VmRSS` from `/proc/<pid>/status` over the listener and every descendant. The `pss_kb` function of `box/lib.sh` becomes `rss_kb`.

### 5.6 Time budget

A cell is about 10 s start and probe, 70 s load, and 15 s stop and drain. Six cells are about 10 minutes. The TTL estimate is the cell count times (`warmup_s` plus `duration_s` plus 60) plus 300 s.

## 6. Result format

### 6.1 The run file

`runs/<id>/run.json` has the schema `rapira-bench-run/2`. The top level keeps `id`, `suite` (`name`, `file_sha256`, `rounds`, `warmup_s`, `duration_s`, `rates`, `connections`), `smoke`, `started`, `finished`, `rig`, `rapira` (without `base`), `servers` (the version lines of `/opt/bench/versions.json` and the php.ini text), `apps` (the hashes of the staged `apps/` files), `loaders` (one record per loader with `wrk2`, the wrk2 commit, and `h2load`, the h2load version line), `processes`, `plan`, `cells`, `status`, `reasons`, and `reporter`. `ladder` goes. `rapira` gains `pr`: `{"number", "url", "title"}` or null.

A cell is `key`, `target` (the registry record of the target), `round`, `status` (`ok`, `void`, `incomplete`), `reason`, `flags`, `rate`, `achieved_rps`, `successful_rps`, `errors`, `latency_us`, `rss_kb`, `held`, `cpu`, `loaders` (the record per loader), and `unloaded` goes. A cell that is not `ok` has null numbers.

### 6.2 Raw evidence

`runs/<id>/raw/<cell>/` keeps the loader output of the stage (the wrk2 text or the h2load summary and the `RESULT` line, not the per-request log), the rendered config, the WARN and ERROR lines of the server log, and `snapshots.txt`.

### 6.3 The manifest

`data/index.json` on `gh-pages` keeps the schema `rapira-bench-index/1`; an entry gains `pr` (the number or null).

## 7. Report and compare

`rig report` prints one row per target with ok cells, in the order of the cells: `req/s`, `held`, `p99`, `p50`, `RSS MiB`, `n`, and `flags`. The voided cells and their reasons follow the table, as today. With more than one round the numbers are medians over the ok cells, and `held` is true when every ok cell held. The publication warning and the exit status rules stay. `rig compare` prints the deltas of `achieved req/s`, `p99`, and `RSS` between two runs and refuses runs with a different server type, loader type, loader count, worker count, rate, or duration unless `--force` is given.

## 8. Board

One page, two charts, no selector:

- The p99 chart: the y axis is the p99 in milliseconds on a logarithmic scale; the x axis lists the newest 60 non-smoke runs in `started` order; one line per target.
- The RSS chart: the y axis is the RSS in MiB on a linear scale; the same x axis and lines.
- An x label is `#<number>` when the run has `rapira.pr`, and the first 7 characters of the sha otherwise. A click on a point opens the pull request of that run in a new tab; a run without a pull request opens the commit on GitHub.
- The tooltip shows the label, the pull request title, the target, the achieved rate against the requested rate, `held`, the p99 or the RSS, and the flags.
- A voided cell is a gap. A run without the target has no point.
- The page draws only run files with the schema `rapira-bench-run/2`. The run of the ladder method stays in the manifest and is not drawn.

`board/app.js` exports `visibleRuns(entries)`, `runLabel(run)`, `runLink(run)`, and `targetSeries(runs)` for the node test. `targetSeries` returns `labels`, `links`, `titles`, and `targets` with `p99_ms`, `rss_mib`, `achieved`, `rate`, `held`, and `flags` per run.

## 9. CI

- The resolve step of `.github/workflows/bench.yml` also asks `gh api repos/rapira-rs/rapira/commits/<sha>/pulls` for the merged pull request of the nightly commit and passes `--pr-number`, `--pr-url`, and `--pr-title` to `rig bench` when one exists. `docs/core-dispatch.yml` does not change.
- The bench step timeout becomes 30 minutes and the job timeout 60 minutes.
- The provisioning of the loader adds `nghttp2`; the CI role needs no change.

## 10. Docs

- `METHOD.md` is rewritten for the constant-rate method: terms, rig rules, the two tools, the cell sequence, the numbers, `held`, the flags, the voids, the review before publication, and the board.
- `README.md` keeps its three paragraphs; "what is tested" names the six rows and the two numbers.
- `docs/operations.md` follows the knobs and targets that remain.
- `NOTES.md` gets a dated entry "Method change 2, 2026-09-25": the constant rate replaces the ladder, the targets that were removed, and that the ladder numbers of the same day do not compare.

## 11. Tests

Flat case tables with a `name` field, standard library only. New or changed: the suite loader (rates, connections, refusals), the wrk2 and h2load command builders, the `RESULT` parser for both tools, `loader/h2load-report.py` against a fixture log, the cell numbers and `held`, the run status, the report and compare tables, `publish` with the `pr` entry, the board transforms under node, the `rss_kb` box function in the container test.

## 12. Removals

`apps/symfony`, `apps/laravel`, `apps/static`, `apps/hello/fpm.php`, `apps/hello/frankenphp.php`, `apps/grpc/php/rr-worker.php`, `apps/grpc/php/gen/Bench/V1/EchoServiceInterface.php`, the gRPC-Web and Connect fixtures of `apps/grpc` (`echo.bin`, `echo.json`, `expect.bin`, `expect.grpcweb`, `expect.json`) and their lines in `apps/grpc/fixtures.py`, the RoadRunner plugin of `apps/grpc/buf.gen.yaml`, `servers/frankenphp`, `servers/php-fpm`, `servers/nginx`, `servers/roadrunner`, `box/servers/frankenphp.sh`, `box/servers/php-fpm.sh`, `box/servers/nginx-rapira.sh`, `box/servers/roadrunner.sh`, `loader/k6-grpc.js`, `suites/full.toml`, `suites/ab.toml`, `rig/ladder.py`, `tests/test_ladder.py`, the `binary` field of the targets and the base build (`BASE_REF`, `--base-ref`, `binary_dir`), the k6 and RoadRunner and PECL provisioning, the Symfony and Laravel lock flow of `box/lock-apps.sh`, the FrankenPHP, nginx, and php-fpm packages of `tests/box.Dockerfile`, and every registry target that is not in section 4.

## 13. Follow-ups outside this change

- A gRPC generator that reads the `grpc-status` trailer.
- More than one round in CI when the run time allows it.
