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

## Load tools

wrk2 loads the HTTP/1.1 targets. k6 loads the gRPC targets. The gRPC-Web and Connect variants use HTTP/1.1, so wrk2 loads them. Each loader runs one load process per stage.

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
- Each call checks the status and the text of the response. A failed check or a failed call counts as a status error.

## The ladder

The stage rates of an app start at the floor of the suite file and double at each stage: floor, 2 times floor, 4 times floor, and so on. The driver stops a cell at the first failing stage and at 20 stages at most. The floors of the `ci` suite are 10000 req/s for hello, Symfony, static, and gRPC, and 5000 req/s for Laravel.

Each loader sends the stage rate divided by the loader count. For wrk2, each loader runs one thread per vCPU and the connection count divided by the loader count. With the default rig and the default suite, that is 64 connections per loader and 256 connections in total. k6 opens one connection per VU, so a gRPC stage above 51200 req/s uses more than 64 connections per loader.

The driver runs this sequence for each cell:

1. It starts the target on the server and verifies the listener process, the executable, and the worker count.
2. Each loader sends one probe and compares the response body with the expected file byte for byte.
3. The loaders send a warm-up of 10 seconds at the floor rate. The driver discards its output.
4. For each stage, the driver sets a start time 3 seconds ahead and starts, in one parallel batch, one load process on each loader and three timed samples on every box: at the start time, in the middle of the stage, and half a second after the end of the stage. The busy CPU, the ENA deltas, and the TIME-WAIT growth come from the start and end samples. The middle sample of the server also reads the connection states and the memory.
5. After a failing stage, one loader sends one more probe.
6. The driver stops the target, verifies that its processes are gone, and reads the WARN and ERROR lines of its log.

A load process that starts more than 1000 ms after the start time voids the cell. Provisioning verifies that chrony is synchronized on every box, so the shared start time is valid to much less than one second.

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

With more than one round, the report shows the median of the surviving cells and the spread of the peak: 100 times (maximum minus minimum) divided by the median.

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

The board on the `gh-pages` branch shows the newest 60 runs. One run is one merged commit on the rapira main branch, benched from its nightly build.

- Each target has one chart. The x axis lists the runs in order, labelled with the first 7 characters of the rapira sha.
- Each line is one ladder rate. The y axis is the p99 latency at that rate in milliseconds, on a logarithmic scale.
- A point exists only where the stage passed. The highest line with a point is the held rate of that run.
- A voided cell, a failing stage, and a run without the target give no point.
- The tooltip shows the flags of the cell. Read the flag rules above before you draw a conclusion from a point.
- The peak is not on the chart. `make report` and `make compare` carry it.
- Smoke runs are not shown.

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
