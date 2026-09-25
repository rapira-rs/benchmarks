# Benchmark method

This document defines how the rig measures a target, which numbers it reports, when it voids a cell, and how to review a run before publication. [README.md](README.md) gives the overview and [docs/operations.md](docs/operations.md) the operations. [NOTES.md](NOTES.md) keeps dated records.

## Terms

- A target is rapira in one mode with one app, for example `hello-rapira-worker`. `suites/targets.toml` defines every target.
- A cell is one target in one round. Its key is `r<round>-<target>`.
- A stage is the one load run of a cell: a warm-up of `warmup_s` seconds, then `duration_s` seconds measured, at the rate of the app of the target.

## Rig rules

- Run benchmarks only on the EC2 rig of this repository: one server and `LOADER_COUNT` loaders in one cluster placement group.
- Run one target at a time on the server.
- Send all load to the private address of the server.
- Keep the server type, the loader type, the loader count, the Availability Zone, the AMI, the suite, the worker count, the rates, and the duration equal for one comparison. `make compare` refuses a pair with a different rig shape, worker count, rate, or duration.
- Every target runs `PROCESSES` workers. The default is the server CPU count. The start script verifies the worker count after the start, and a different count fails the start.
- Every PHP process uses the shared `servers/php.ini`. The run file records its text.
- The driver runs the cells in rotated order: round r starts at target r of the suite and wraps. No target is always first.
- Pin `AMI` when a result set takes more than one day.

## Load tools

wrk2 loads the HTTP/1.1 targets. h2load loads the gRPC target over h2c. Each loader runs one load process per cell.

These wrk2 facts shape the method:

- `-R` is the total request rate of one process. wrk2 divides it over its threads and connections.
- wrk2 divides `-c` by `-t` with integer division and drops the remainder. The driver therefore requires a connection count that is a multiple of the thread count.
- The first 10 seconds of a run are a calibration window. wrk2 resets the latency histogram after that window but keeps the request count. The warm-up of the suite is 10 seconds, so the reported latency covers the measured window and the request count covers the whole run.
- The reported latency is corrected for coordinated omission.
- The `status` error counter counts responses with a status above 399. The `timeout` counter is a tally per connection that wrk2 takes every 2 seconds. It is not a request count.
- An overloaded target gives no wrk2 errors. The achieved rate falls below the requested rate, and the corrected latency grows to seconds.
- wrk2 sends HTTP/1.1 only.

These h2load facts shape the method:

- `--rps` is the rate per connection. The driver divides the rate of a loader by its connection count. `-m` limits the streams in flight per connection.
- `--warm-up-time` and `-D` set the warm-up and the measured window. h2load counts only the requests that end in the measured window, and writes one line per such request to `--log-file`: the start time, the HTTP status or -1 for a failed stream, and the response time. `loader/h2load-report.py` turns that log into the `RESULT` line.
- h2load does not read the `grpc-status` trailer. A gRPC error inside a 200 response is invisible to the counters. The probes before and after the stage are the correctness check.

## The cell sequence

The driver runs this sequence for each cell:

1. It starts the target on the server and verifies the listener process, the executable, and the worker count.
2. Each loader sends one probe and compares the response body with the expected file byte for byte.
3. The driver sets a start time 3 seconds ahead and starts, in one parallel batch, one load process on each loader and two timed samples on every box: at the start of the measured window and half a second after its end. The busy CPU, the ENA deltas, and the TIME-WAIT growth come from these samples.
4. The driver reads the RSS of the target: the sum of `VmRSS` over the listener and its workers.
5. One loader sends one more probe.
6. The driver stops the target, verifies that its processes are gone, and reads the WARN and ERROR lines of its log.

A load process that starts more than 1000 ms after the start time voids the cell. Provisioning verifies that chrony is synchronized on every box, so the shared start time is valid to much less than one second.

## Rates and connections

The `ci` suite sends 250000 req/s to every HTTP target over 5000 connections and 100000 req/s to the gRPC target over 100 connections with up to 100 streams each. Each loader sends the rate divided by the loader count over the connections divided by the loader count, with one wrk2 thread per vCPU.

## Merge over loaders

For each cell, the driver adds the requests, the bytes, and each error counter of all loaders. It then calculates:

- `achieved_rps`: the requests divided by the seconds the tool counted: `warmup_s` plus `duration_s` for wrk2, `duration_s` for h2load.
- `successful_rps`: the requests minus the status errors, divided by the same seconds.
- Each latency percentile: the maximum over the loaders. This value is an upper bound, not a pooled percentile. The cell keeps the record of each loader, so a reader can see the spread.

## Reported numbers

- `achieved_rps`: the rate the target answered.
- `held`: true when `achieved_rps` is at least 95% of the rate and every error counter is 0. A target that did not hold is not a failure: the row shows the rate it achieved.
- `latency_us`: p50, p90, p99, p99.9, and max over the measured window. The board shows the p99.
- `rss_kb`: the RSS of the rapira process tree at the end of the stage. The board shows it in MiB. A page that the workers share counts once per process, so the sum is an upper bound of the footprint.

With more than one round, the report shows the median of the ok cells, and `held` only when every ok cell held.

## Flags

Flags carry values. They are review items. They do not make a cell fail.

- `generator_bound`: the cell did not hold, the busy CPU of a loader is 85% or more, and the server is below 90%. The achieved rate is then a floor. State it as "at least" the value.
- `server_unsaturated`: the cell did not hold, the server is below 90%, and every loader is below 85%. The target failed for a reason other than CPU, for example a queue in its worker pool.
- `ena_throttled`: the ENA allowance counters of the server changed during the stage. The flag gives the deltas. Do not use that cell for a throughput claim.
- `keepalive_broken`: the TIME-WAIT count of the server grew by more than the connection count during the stage. The target does not keep connections open.
- `worker_churn`: the worker process list changed during the cell.
- `log_growth`: the server log grew by more than 65536 bytes during the cell. The flag gives the byte count.
- `died`: the probe after the stage failed. The target stopped answering.

## Voids

A void excludes the cell from every number and makes the run incomplete. The numbers of a voided cell are null; its raw directory keeps the evidence. The driver voids a cell when:

- The target does not start, or its worker count differs from `PROCESSES`.
- The probe before the stage does not match the expected response on a loader.
- A loader gives no `RESULT` line, or a `RESULT` line that the driver cannot read. The reason names the loader.
- A loader starts the stage more than 1000 ms late.
- The ENA allowance counters of a loader change during the stage. The network shaped that loader, so the stage does not measure the server.
- rapira logs a WARN or ERROR line during the cell. rapira runs at log level `warn`. The raw directory keeps the lines.
- An ssh command to a box fails during the cell. The probe after the stage is the exception: a failed probe sets the `died` flag.
- The driver cannot stop the target or read its log.

An interrupted run stops the current target and writes the run file with the status `incomplete`. The interrupted cell has the status `incomplete`, and the cells that did not run are listed as missing.

## Review before publication

Publish a result only when all these conditions are true:

- `make bench` and `make report` return status 0.
- The report has no `Do not publish these tables.` line.
- Each reported row has the planned number of rounds.
- No flag changes the stated conclusion. Read the flag rules above for each flag in the row.
- `run.json` contains the expected rapira build, binary SHA-256, AMI, instance types, loader count, worker count, and app hashes.
- The raw files support the values in the report.
- `run.json` records the wrk2 commit and the h2load version of each loader.

If the result depends on a response header or on a server configuration, capture that evidence before the measured run and keep it with the run. The raw directory already keeps the rendered configuration of every target.

## Reading the board

The board on the `gh-pages` branch shows the newest 60 runs. One run is one merged pull request on the rapira main branch, benched from its nightly build.

- The p99 chart shows the p99 latency of every target in milliseconds on a logarithmic scale. The RSS chart shows the RSS of the rapira process tree in MiB.
- The x axis lists the runs in order, labelled with the pull request number. A run without a pull request, for example a manual run, shows the first 7 characters of the rapira sha. A click on a point opens the pull request, or the commit.
- The tooltip shows the achieved rate against the requested rate, whether the target held the rate, and the flags of the cell. Read the flag rules above before you draw a conclusion from a point.
- A voided cell and a run without the target give no point.
- Smoke runs are not shown.

Compare points only when the rig shape is the same. Numbers from before 2026-09-25 come from other methods and are not on the board.

## Server facts

- A target listens on port 8080.
- The static target runs the hello dispatcher behind the static middleware with `apps/hello` as its root. The request misses the root, so the row shows the cost of the middleware on the PHP path. Compare it with the plain dispatcher row of the same run.
- The Yii3 target runs the app-api of `apps/yii3/source.toml` on the dispatcher in its `prod` environment without debug. Its `/` route answers from the application parameters. The route cache lives in the Valkey service of the server. No request touches a database.
- The gRPC target uses the pure PHP protobuf runtime.

## Connection distribution tests

These rules apply to a special test of how a server spreads connections over its workers:

- Pin the reproducer commit and its dependency lock file.
- Restart the server before each comparison cell.
- Record the request count, the CPU use, and the accepted sockets of each worker. The total CPU use does not show the distribution.
- Take the last request counter sample before the load process stops. The stop of a client can cancel the requests in progress.
- For exact connection counts, stop the other clients and match each accepted socket by the client address and source port.
