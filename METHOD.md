# Benchmark method

This document defines how the rig measures a target, which numbers it reports, when it voids a cell, and how to review a run before publication. [README.md](README.md) gives the overview and [docs/operations.md](docs/operations.md) the operations. [NOTES.md](NOTES.md) keeps dated records.

## Terms

- A target is rapira in one mode with one app, for example `hello-rapira-worker`. `suites/targets.toml` defines every target.
- A build is one rapira binary. The `new` build is the nightly build under test. The `base` build is the build of the previous board run. Each run measures both builds.
- A stage is one load run on the server: a warm-up of `warmup_s` seconds, then `duration_s` seconds measured. A stage has one of two kinds: `rate` or `cap`. The kind sets the process count, the rate, the warm-up, and the measured window.
- A cell is one stage of one round, one build, one stage kind, and one target. Its key is `r<round>-<build>-<stage>-<target>`, for example `r2-new-cap-hello-rapira-worker`. The key also names the raw directory of the cell.
- A pair is the base cell and the new cell of one round, one target, and one stage kind.

## Rig rules

- Run benchmarks only on the EC2 rig of this repository: one server and `LOADER_COUNT` loaders in one cluster placement group.
- Run one target at a time on the server.
- Send all load to the private address of the server.
- Measure both builds of a comparison in one run, on the same instances. The server installs the new build under `/opt/bench/rapira/<sha7>` and the base build under `/opt/bench/rapira/base-<sha7>`, so the two builds have separate binaries also when they come from the same commit.
- Two runs use different instances. Do not compare the absolute numbers of two runs.
- Each cell starts its own server with the process count of its stage kind. The start script verifies the process count after the start, and a different count fails the start.
- Every PHP process uses the shared `servers/php.ini`. The run file records its text.
- Pin `AMI` when a result set takes more than one day.

## Order

The rounds run in sequence. Round r starts at target r of the suite and wraps, so no target is always first. For each target, the rate pair runs before the capacity pair. The two cells of a pair run one after the other, so a drift of the instances affects both cells of the pair.

The build order in each pair is base then new in odd rounds, and new then base in even rounds. For example, round 1 of target T runs `r1-base-rate-T`, `r1-new-rate-T`, `r1-base-cap-T`, and `r1-new-cap-T`.

With 3 rounds the build order is not balanced: the base build runs first in rounds 1 and 3, and the new build runs first in round 2. An effect of the position in the pair can move the median delta. That effect shows as a band that crosses zero.

## Stage kinds

- Rate stage (`rate`): the rapira pool has one process per server vCPU (`nproc` of the server). The loaders send a constant rate per target that keeps the server at about 50% CPU. The stage gives the p99 latency and the RSS of the target.
- Capacity stage (`cap`): the rapira pool has `stages.cap.processes` processes. The loaders send a rate per target above the capacity of those processes. The stage gives the achieved req/s. A capacity cell is not expected to hold its rate.

A capacity stage does not make rapira shed requests. rapira sheds a request only after its intake is full for 30 s. The connections limit the requests in flight: 1000 for HTTP, and 100 connections with 100 streams each for gRPC. A request therefore waits much less than 1 s.

## Load tools

wrk2 loads the HTTP/1.1 targets. h2load loads the gRPC target over h2c. Each loader runs one load process per cell.

These wrk2 facts shape the method:

- `-R` is the total request rate of one process. wrk2 divides it over its threads and connections.
- wrk2 divides `-c` by `-t` with integer division and drops the remainder. The driver therefore requires an HTTP connection count that is a multiple of the loader count times the thread count.
- The calibration window of wrk2 is 10 s plus 5 ms per connection of a thread: 10.6 s with 1000 connections over 2 loaders of 4 threads, which is 125 connections per thread. wrk2 resets the latency histogram after that window but keeps the request count. The rate stage has a warm-up of 11 s, so the reported latency covers the measured window, and the request count covers the whole run.
- Each wrk2 thread prints the mean latency of its calibration in a `Thread calibration: mean lat.: <x>ms` line. The loader record of the cell keeps these values as `calibration_ms`.
- The reported latency is corrected for coordinated omission.
- The `status` error counter counts responses with a status above 399. The `timeout` counter is a tally per connection that wrk2 takes every 2 seconds. It is not a request count.
- An overloaded target gives no wrk2 errors. The achieved rate falls below the requested rate, and the corrected latency grows to seconds.
- wrk2 sends HTTP/1.1 only.

These h2load facts shape the method:

- `--rps` is the rate per connection. The driver divides the rate of a loader by its connection count. `-m` limits the streams in flight per connection.
- h2load refuses fewer clients than threads. The driver gives h2load one thread per vCPU of the loader, and at most one thread per connection.
- `--warm-up-time` and `-D` set the warm-up and the measured window. h2load counts only the requests that end in the measured window, and writes one line per such request to `--log-file`: the start time, the HTTP status or -1 for a failed stream, and the response time. `loader/h2load-report.py` turns that log into the `RESULT` line.
- h2load does not read the `grpc-status` trailer. A gRPC error inside a 200 response is invisible to the counters. The probes before and after the stage are the correctness check.
- h2load times a request from the moment it sends it, so its latency is not corrected for coordinated omission: a request that waits for a free stream does not count that wait. Compare the gRPC p99 with the wrk2 rows with this in mind.
- h2load prints no calibration lines. Its `calibration_ms` is an empty list.

## The cell sequence

The driver runs this sequence for each cell:

1. It starts the target on the server from the binary directory of the build of the cell, with the process count of the stage kind, and verifies the listener process, the executable, and the process count.
2. Each loader sends one probe and compares the response body with the expected file byte for byte.
3. The driver sets a start time 3 seconds ahead and starts, in one parallel batch, one load process on each loader and two timed samples on every box: at the start of the measured window and half a second after its end. The busy CPU, the ENA deltas, and the TIME-WAIT growth come from these samples.
4. The driver reads the RSS of the target: the sum of `VmRSS` over the listener and its workers.
5. One loader sends one more probe.
6. The driver stops the target, verifies that its processes are gone, and reads the WARN and ERROR lines of its log.

A load process that starts more than 1000 ms after the start time voids the cell. Provisioning verifies that chrony is synchronized on every box, so the shared start time is valid to much less than one second.

## Rates and connections

The suite sets one rate per target for each stage kind. A rate is the total req/s over all loaders. The `ci` suite uses 1000 connections for each HTTP target and 100 connections with up to 100 streams each for the gRPC target. Each loader sends the rate divided by the loader count over the connections divided by the loader count, with one wrk2 thread per vCPU.

The rates of the `ci` suite come from an A/A calibration run. [NOTES.md](NOTES.md) records it. These rules set the rates:

- Rate stage: the calibration rate times 50, divided by the measured server busy percent, rounded down to a multiple of 1000. When the result differs from the calibration rate by more than 30%, a second pass of 1 round checks it.
- Capacity stage: 2 times the achieved capacity, rounded up to a multiple of 10000.
- When a loader goes above 70% busy CPU in the capacity stage of a target, `stages.cap.processes` is 1 for all targets.

## Merge over loaders

For each cell, the driver adds the requests, the bytes, and each error counter of all loaders. It then calculates:

- `achieved_rps`: the requests divided by the seconds the tool counted: `warmup_s` plus `duration_s` for wrk2, `duration_s` for h2load. The two cells of a pair use the same stage, so the pair compares equal windows.
- `successful_rps`: the requests minus the status errors, divided by the same seconds.
- Each latency percentile: the maximum over the loaders. This value is an upper bound, not a pooled percentile. The cell keeps the record of each loader, so a reader can see the spread.

## Cell numbers

- `achieved_rps`: the rate the target answered.
- `held`: true when `achieved_rps` is at least 95% of the rate and every error counter is 0. A cell that did not hold is not a failure: it shows the rate it achieved.
- `latency_us`: p50, p90, p99, p99.9, and max over the measured window.
- `rss_kb`: the RSS of the rapira process tree at the end of the stage. A page that the workers share counts once per process, so the sum is an upper bound of the footprint.

## Flags

Flags carry values. They are review items. They do not make a cell fail.

- `generator_bound`: a rate cell did not hold, the busy CPU of a loader is 85% or more, and the server is below 90%. The achieved rate is then a lower bound. State it as "at least" the value.
- `server_unsaturated`: a rate cell did not hold, the server is below 90%, and every loader is below 85%. The target failed for a reason other than CPU, for example a queue in its worker pool.
- `loader_busy`: a loader of the cell is above 70% busy CPU over the measured window. The flag gives the highest loader busy percent. It applies to both stage kinds. On a capacity cell, the achieved req/s is then a lower bound of the capacity.
- `loader_skew`: a rate cell of a wrk2 target, where the calibration mean of one wrk2 thread is more than 15% above the median of the other threads of the same loader. The flag gives the loader, the value of the thread, and the median in ms. The p99 of such a cell comes from one slow thread.
- `not_saturated`: a capacity cell held its rate. Its achieved req/s is not a capacity.
- `ena_throttled`: the ENA allowance counters of the server changed during the stage. The flag gives the deltas. Do not use that cell for a throughput claim.
- `keepalive_broken`: the TIME-WAIT count of the server grew by more than the connection count during the stage. The target does not keep connections open.
- `worker_churn`: the worker process list changed during the cell.
- `log_growth`: the server log grew by more than 65536 bytes during the cell. The flag gives the byte count.
- `died`: the probe after the stage failed. The target stopped answering.

## Voids

A void excludes the cell from every number, and the summary drops its pair. The numbers of a voided cell are null; its raw directory keeps the evidence. The driver voids a cell when:

- The target does not start, or its process count differs from the process count of the stage kind.
- The probe before the stage does not match the expected response on a loader.
- A loader gives no `RESULT` line, or a `RESULT` line that the driver cannot read. The reason names the loader.
- A loader starts the stage more than 1000 ms late.
- The ENA allowance counters of a loader change during the stage. The network shaped that loader, so the stage does not measure the server.
- rapira logs a WARN or ERROR line during the cell. rapira runs at log level `warn`. The raw directory keeps the lines.
- An ssh command to a box fails during the cell. The probe after the stage is the exception: a failed probe sets the `died` flag.
- The driver cannot stop the target or read its log.

## Run status

A run is `complete` when every planned cell ran. A complete run can hold void cells. Two cases stop the publication:

- A missing cell, for example after a crash of the driver or of the rig, makes the run `incomplete`. An interrupted run stops the current target and writes the run file with the status `incomplete`. The interrupted cell has the status `incomplete`, and the cells that did not run are listed as missing.
- When every new cell of one target is void, the run is `broken`. `rig bench` exits with an error, and `rig publish` refuses the run. A new build that cannot run therefore fails the CI job, and it does not become the base build of the next run.

A core change can break the config template for the base build, for example with a key that only the new build accepts. Every base cell of that run is then void. The run is complete and publishes, and its points on the board are gaps.

## Summary

After the last cell, the driver writes the `summary` of the run file. An ok cell has the status `ok`, which means that it is not void. Whether it held does not matter. A counted pair has two ok cells. The summary has 3 measures for each target:

- `capacity`: the achieved req/s of the capacity cells.
- `p99`: the p99 latency of the rate cells. A pair where one of the two cells has `loader_skew` does not count for this measure.
- `rss`: the RSS of the rate cells. A pair with `loader_skew` counts for this measure.

Each measure holds:

- `pairs`: the number of counted pairs.
- `delta_pct`: the median of the paired deltas. One paired delta is `(new - base) / base x 100`.
- `min_pct` and `max_pct`: the smallest and the largest paired delta.
- `base` and `new`: the median of the base values and the median of the new values over the counted pairs. They are separate medians, so `new / base` can differ from `delta_pct`.
- `flags`: the sorted names of the flags of the cells in the counted pairs.

With 0 counted pairs, the numbers are null and the flag list is empty. `make report` prints the summary table: the target, the measure, the pairs, the delta with its min and max, the base and new medians with their unit, and the flags. Below the table, it lists the void cells with their reasons. When the run is not complete, the report ends with the run status, the reasons, and the line `Do not publish these tables.`

## Review before publication

Publish a result only when all these conditions are true:

- `make bench` and `make report` return status 0.
- Each measure of each target has as many pairs as the suite has rounds, or the voids and the `loader_skew` flags of the run explain the missing pairs.
- No flag changes the stated conclusion. Read the flag rules above for each flag in the summary.
- `run.json` contains the expected new build and base build with their binary SHA-256, the AMI, the instance types, the loader count, the process counts, and the app hashes.
- The raw files support the values in the report.
- `run.json` records the wrk2 commit and the h2load version of each loader.

If the result depends on a response header or on a server configuration, capture that evidence before the measured run and keep it with the run. The raw directory already keeps the rendered configuration of every target.

## Reading the board

The board on the `gh-pages` branch shows the newest 60 runs. One run is one nightly build of the rapira main branch, labelled with the merged pull request of its commit. Merges that land between two nightly builds share one run. Each point compares the new build of a run with the base build of the same run, which is the build of the previous board run.

- Each target has one row with three charts: the capacity delta, the p99 delta, and the RSS delta, in percent.
- A point is the `delta_pct` of one run. A band from `min_pct` to `max_pct` shows the spread of the paired deltas. A line marks zero. The y axis has no fixed minimum.
- A run without the target, or a measure with 0 counted pairs, gives a gap.
- The x axis lists the runs in order, labelled with the pull request number. A run without a pull request, for example a manual run, shows the first 7 characters of the rapira sha. A click in a chart opens the pull request, or the commit, of the run at the pointer. On a touch screen, a tap shows the tooltip, and a tap on a point opens the run.
- The tooltip shows the start time and the label of the run, the pull request title, `new <x> vs base <y> <unit>`, `delta <d>% (min <a>%, max <b>%, <n> pairs)`, and the flag names. Read the flag rules above before you draw a conclusion from a point.
- `new` and `base` in the tooltip are separate medians. Use `delta_pct` for the change.
- Tone: a higher capacity is better, and a lower p99 and a lower RSS are better. A point is green for a better value or red for a worse value only when all these conditions are true: the point has as many pairs as the suite has rounds, its whole band is strictly on one side of zero, and `|delta_pct|` is at least the noise floor of the measure. Otherwise the point is gray.
- The noise floor of a measure is the largest `|paired delta|` of that measure over all targets of an A/A run, rounded up to a multiple of 0.5%. In an A/A run, the base build and the new build are the same commit. The floors are in [NOTES.md](NOTES.md) and in `NOISE_FLOOR_PCT` of `board/app.js`.
- Smoke runs are not shown.

The board draws only runs with the schema `rapira-bench-run/3`. The numbers of the earlier methods do not compare with these deltas, and the board does not show them.

## Server facts

- A target listens on port 8080.
- The static target runs the hello dispatcher behind the static middleware with `apps/hello` as its root. The request misses the root, so the target shows the cost of the middleware on the PHP path. Compare its numbers with the plain dispatcher numbers of the same run.
- The Yii3 target runs the app-api of `apps/yii3/source.toml` on the dispatcher in its `prod` environment without debug. Its `/` route answers from the application parameters. The route cache lives in the Valkey service of the server. No request touches a database.
- The gRPC target uses the pure PHP protobuf runtime.

## Connection distribution tests

These rules apply to a special test of how a server spreads connections over its workers:

- Pin the reproducer commit and its dependency lock file.
- Restart the server before each comparison cell.
- Record the request count, the CPU use, and the accepted sockets of each worker. The total CPU use does not show the distribution.
- Take the last request counter sample before the load process stops. The stop of a client can cancel the requests in progress.
- For exact connection counts, stop the other clients and match each accepted socket by the client address and source port.
