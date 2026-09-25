# Bench notes

Dated records and the decisions the code cannot show. Methodology lives in METHOD.md, operations in README.md. Numbers from different rigs or instance sizes never mix into one table.

## Method change, 2026-09-25

- From this date the rig measures with the staged rate ladder of METHOD.md. wrk2 loads the HTTP/1.1 targets and k6 loads the gRPC targets, from four c7a.xlarge loaders against one c7a.8xlarge server.
- The numbers before this date come from closed-loop wrk and h2load passes and fixed-rate k6 passes from one c7a.4xlarge loader at a fixed connection count. The numbers after this date are the held rate, the peak successful rate, and the unloaded latency of the ladder. Do not compare numbers from before and after this date, and do not put them in one table.
- Each run now writes `runs/<id>/run.json` with the schema `rapira-bench-run/1`. The directories under `results/` stay as the record of the old method.
- FrankenPHP now runs the glibc release asset 1.12.7 with the production worker shape, and every PHP runtime uses the shared `servers/php.ini`. The FrankenPHP and php-fpm numbers before this date used other settings.
- The fixed pair of the decisions below no longer applies. The loader count and the instance types are knobs, and the Makefile quota check adds the vCPUs of the server and all loaders.

## gRPC baseline (rapira PHP dispatcher vs RoadRunner), 2026-09-24

Rig: c7a.8xlarge server + c7a.4xlarge loader, eu-central-1a, AMI ami-066f414c6023b2bf9, plain release build of rapira at feature/grpc-connectrpc `e421bbc` (pr_sha `924399d` is that commit plus a since-removed example that the run did not use), PHP 8.5.10 NTS with opcache and PECL protobuf 5.36.2, RoadRunner 2025.1.15 with php opcache.enable_cli=1 and 32 workers, h2load nghttp2/1.70.0, k6 2.2.0, wrk 4.2.0, 32 workers on every leg, 512 connections and 15 s on every saturated pass, lowc at 32 connections for 10 s, k6 open loop at 20000 req/s with 256 VUs (1 s ramp then 15 s), medians of 3 interleaved rounds. Run dir: `results/20260924T191959Z-c7a.8xlarge-grpc`.

Closed loop (h2load, c=512, medians; the hello row also under wrk):

| leg | request | req/s (median) | spread | Pss MiB |
| --- | --- | --- | --- | --- |
| rapira-http-h1 (wrk) | GET /?name=you | 939,155 | 1.0% | 230.6 |
| rapira-http-h1 | GET /?name=you (h2load --h1) | 919,936 | 1.3% | 230.6 |
| rapira-connect-h1 | Connect proto over HTTP/1.1 | 815,299 | 0.3% | 205.7 |
| rapira-connectjson-h1 | Connect JSON over HTTP/1.1 | 766,225 | 4.9% | 223.6 |
| rapira-grpcweb-h1 | gRPC-Web over HTTP/1.1 | 827,481 | 1.0% | 209.3 |
| rapira-connect-h2c | Connect proto over h2c | 815,498 | 1.9% | 249.6 |
| rapira-grpc | gRPC over h2c | 810,216 | 0.6% | 248.0 |
| rr-grpc | gRPC over h2c | 137,787 | 0.9% | 542.2 |

Low concurrency (h2load, c=32, medians of p50 / p99 / avg):

| leg | p50 | p99 | avg |
| --- | --- | --- | --- |
| rapira-http-h1 | 0.16ms | 0.25ms | 0.16ms |
| rapira-connect-h1 | 0.19ms | 0.29ms | 0.19ms |
| rapira-connectjson-h1 | 0.18ms | 0.29ms | 0.18ms |
| rapira-grpcweb-h1 | 0.18ms | 0.28ms | 0.18ms |
| rapira-connect-h2c | 0.15ms | 0.25ms | 0.16ms |
| rapira-grpc | 0.16ms | 0.26ms | 0.16ms |
| rr-grpc | 0.30ms | 1.22ms | 0.34ms |

Open loop (k6, target 20000 req/s, achieved req/s is the average over the 16 s run including the 1 s ramp; `rapira-connect-h2c` carries no k6 pass):

| leg | req/s (achieved) | p50 | p99 | p99.9 | failed checks |
| --- | --- | --- | --- | --- | --- |
| rapira-http-h1 | 19,371 | 0.13ms | 0.23ms | 0.92ms | 0 |
| rapira-connect-h1 | 19,369 | 0.13ms | 0.43ms | 1.98ms | 0 |
| rapira-connectjson-h1 | 19,370 | 0.13ms | 0.26ms | 0.96ms | 0 |
| rapira-grpcweb-h1 | 19,371 | 0.13ms | 0.33ms | 1.10ms | 0 |
| rapira-grpc | 19,370 | 0.16ms | 0.49ms | 1.12ms | 0 |
| rr-grpc | 19,370 | 0.24ms | 1.16ms | 2.90ms | 0 |

- Flags: every rapira row carries `server_unsaturated` (server CPU 75 to 83%, loader 62 to 74%) and `lowc_doubled` (5 to 10 of 32 workers held two connections during the lowc warm-up); rr-grpc carries `server_unsaturated` (server 69%, loader 27%). No `generator_bound`, `ena_throttled`, `worker_churn`, or `log_growth` on any cell; no voided or invalid cell.
- The connection sweep (5 s cells, rapira-grpc and rapira-http-h1): rapira-grpc 575k at c=256, 812k at 512, 1,047k at 1024 with `generator_bound`; hello h2load --h1 712k, 920k, 1,257k with `generator_bound` at 1024. One c7a.4xlarge loader binds before the server reaches 90% CPU on these rows, so the c=512 rows are not a server maximum, and the c=1024 values are floors. Run dirs: `results/20260924T191554Z-c7a.8xlarge-grpc` (256), `results/20260924T191335Z-c7a.8xlarge-grpc` (512), and `results/20260924T191704Z-c7a.8xlarge-grpc` (1024).
- The thread check on rapira-grpc at c=512, 5 s cells: h2load -t8 gives 684k req/s (`results/20260924T191813Z-c7a.8xlarge-grpc`) and -t16 gives 812k (`results/20260924T191335Z-c7a.8xlarge-grpc`), so the loader thread count also limits the c=512 rows.
- The forced-error check: a dispatcher that answers `fail()` on every 500th call was voided by the byte rule alone (`short responses: data=151664695 succeeded=1669983 expect_len=91`, `results/20260924T191846Z-c7a.8xlarge-grpc`); the probe and the log rule did not fire.
- RoadRunner runs with `logs.level: error` and its `grpc` and `server` channels at `panic`, because both log one ERROR line per queued call that h2load cancels at the end of a pass (560 lines at c=512 in the container check).
- The open-loop rate ramps from 0 to 20000 req/s over 1 s because k6 starts its schedule before its VUs are active; the first smoke run dropped 77 to 147 iterations at the start with a constant rate.
- What the tables do not show: the Rust transport ceiling of the plan was removed on 2026-09-24 as not a fair row; the suite benches the rapira binary only.

## AWS baseline, 2026-08-30

Rig: c7a.8xlarge server + c7a.4xlarge loader, eu-central-1a, plain release builds (no frame pointers), 32 workers, wrk c=8000 d=15s, medians of 3 interleaved rounds. Run dirs: `results/20260830T140609Z-c7a.8xlarge-ab` and `results/20260830T142132Z-c7a.8xlarge-fleet`, provenance in each run-meta.json.

A/B, main `5d188a0` (pr) vs release v0.7.0 `0612b16` (base), hello workload:

| mode | v0.7.0 | main | delta |
| --- | --- | --- | --- |
| dispatcher | 1,087,148 | 1,268,192 | +16.7% |
| worker | 1,014,621 | 1,211,527 | +19.4% |
| classic | 501,769 | 491,877 | -2.0% (inside the ~7% round spread) |

- Every v0.7.0 cell carried wrk read errors (connection resets): ~700 per 15s cell in classic, 7 to 18 in the resident modes. Every main cell had zero. The strict report voided the v0.7.0 cells; the table above is from the raw wrk output with that caveat. The reset fix and the throughput gain both trace to the per-connection PHPHandler work.
- main lowc p50: 0.14 to 0.16 ms across modes at c=32.

Fleet (main, plain build), hello workload:

| leg | req/s (median) | note |
| --- | --- | --- |
| swoole | 1,285,881 | generator_bound: a pair ceiling, not a swoole ceiling |
| rapira-dispatcher | 1,267,571 | at the pair ceiling; one round PPS-throttled to 728k, median unaffected |
| rapira-worker | 1,149,696 | |
| rapira-static-miss | 574,679 | |
| rapira-classic | 480,928 | |
| roadrunner | 143,996 | ext-protobuf loaded |
| fpm | 124,502 | |
| franken | 104,467 | server_unsaturated at the num 32 / num_threads 33 parity pool |
| franken-static-miss | 103,620 | equals franken hello: php_server stats the docroot every request |

- The pair ceiling is ~1.27M req/s hello: the loader sits at ~96% CPU and the Nitro PPS allowance starts shaving packets in the same zone. A server faster than that needs a bigger loader or two loaders.
- Static hit (27 KiB css): rapira and franken both peg ~55.7k req/s = 11.5 Gbps, the wire itself. Throughput does not separate them at this file size; the tail does: franken timed out 61 to 68 requests per cell, rapira 11 to 19. All hit cells voided on those timeouts by design.
- Static miss on rapira costs ~50% of worker throughput at 32 cores (575k vs 1,150k). The maindev rig measured ~35% at smaller scale. The probe path is worth a look in core.
- k6 probe (16-core loader, VUS=256): rapira modes 0.82 to 0.89 ms avg, fpm and roadrunner ~1.9 ms, franken 2.7 ms, zero failed checks on every leg.

## Full bench (frameworks, main vs v0.7.0), 2026-08-30

Rig: the fixed c7a pair, plain release builds, eu-central-1a, 32 workers/threads/children per leg, wrk c=2048 d=15s, lowc c=32, k6 VUS=256, medians of 3 interleaved rounds. Run dir: `results/20260830T160204Z-c7a.8xlarge-frameworks`. Servers: rapira main `5d188a0` (pr), rapira v0.7.0 `0612b16` (base, classic only), FrankenPHP 1.12.4 (static, its own PHP 8.5.8), php-fpm + nginx (Fedora PHP 8.5.9 NTS, same build rapira embeds). Apps: Symfony 7.4.17 (prod, warmed cache, dump-env) and Laravel 12.68.0 + Octane 2.19.1 (config and route cached, array session and cache). The Laravel worker legs run Octane machinery on both servers (stock octane FrankenPHP entry vs an Octane-Worker rapira bridge); the Symfony worker legs run one byte-identical kernel-loop entry on both.

Symfony (hello route through the full kernel):

| server | mode | req/s (median) | lowc p50 | lowc p99 | spread |
| --- | --- | --- | --- | --- | --- |
| rapira-main | worker | 186,010 | 0.27ms | 0.60ms | 3.0% |
| franken | worker | 92,338 | 0.36ms | 1.26ms | 1.2% |
| rapira-main | classic | 51,054 | 0.90ms | 2.29ms | 0.6% |
| rapira-0.7.0 | classic | 50,002* | 0.89ms | 1.87ms | 0.2% |
| franken | classic | 42,362 | 0.88ms | 2.31ms | 7.4% |
| fpm | classic | 38,955 | 0.86ms | 2.12ms | 1.3% |

Laravel (Octane on the worker legs):

| server | mode | req/s (median) | lowc p50 | lowc p99 | spread |
| --- | --- | --- | --- | --- | --- |
| rapira-main | worker | 33,268 | 1.57ms | 3.44ms | 1.0% |
| franken | worker | 30,422 | 1.19ms | 3.04ms | 0.5% |
| rapira-main | classic | 14,604 | 3.94ms | 7.98ms | 0.6% |
| rapira-0.7.0 | classic | 14,474* | 3.92ms | 7.99ms | 0.7% |
| fpm | classic | 13,412 | 2.27ms | 4.49ms | 1.3% |
| franken | classic | 12,587 | 2.68ms | 5.22ms | 2.0% |

\* Every v0.7.0 cell carried a handful of wrk read errors (10 to 33 resets per 15s cell, the same class the hello A/B documented) and was voided by the strict report; these two rows are medians from the raw wrk output with that caveat. All other cells are clean; zero failed k6 checks anywhere.

- Symfony worker: rapira 2.0x franken. Laravel worker: rapira +9% over stock Octane-on-franken; Octane's per-request machinery, not the server, dominates that leg.
- Worker over classic: Symfony 3.6x on rapira, 2.2x on franken; Laravel 2.3x on rapira, 2.4x on franken.
- Classic: rapira main leads both frameworks (+31% over fpm on Symfony, +9% on Laravel), and main vs v0.7.0 classic is flat (+2.1% / +0.9%), consistent with the hello A/B.
- Laravel classic lowc oddity: rapira p50 3.94ms vs fpm 2.27ms per request despite higher rapira throughput at saturation; Symfony classic shows no such gap (0.90 vs 0.86). A per-request cost on big codebases in classic mode is worth a look in core; the r2 cell measured 2.46ms, so round variance is high on this metric.
- v0.7.0 flush bug found while building the legs: PHP `flush()` after output makes v0.7.0 seal the response lengthless and the front closes the connection (~every request under symfony/runtime's `Response::send(true)` tail; 170k resets per 5s cell). Fixed on main. The symfony classic entry now uses send(false) on all servers; the residual v0.7.0 resets above are the pre-existing hello-class ones.
- Flags: symfony-franken worker and classic plus symfony-fpm ran server_unsaturated at c=2048 (their pool shape, matching the hello fleet); the rapira and laravel legs saturated.

## Static cache bench (feature/cache-for-static vs main vs FrankenPHP), 2026-08-31

Rig: the fixed c7a pair, plain release builds, eu-central-1a, AMI ami-040c604473c52f25f, 32 workers/threads per leg, wrk c=8000 d=15s, lowc c=32, k6 VUS=256, medians of 3 interleaved rounds, new `make bench_static` driver. Run dir: `results/20260831T190826Z-c7a.8xlarge-static`. Servers: rapira feature/cache-for-static `649b8c7` (pr, per-worker in-memory file cache: 16 MiB/process, 256 KiB/file cap, 1 s freshness), rapira main `5d188a0` (base, uncached ServeDir), FrankenPHP 1.12.4. Apps behind the fallthrough: the hello worker and the Symfony 7.4.18 kernel-loop worker. hit fetches tiny.css (128 B) with PHP never running; miss requests the hello URL through the same server shape so the static probe runs and falls through to PHP; plain is the same rapira worker with no static middleware (franken has no such shape).

Asset size is the whole game: 27 KiB pegs the 12.5 Gbps wire at ~56k req/s on every leg (2026-08-30 fleet run), and even 1 KiB put the cache leg at ~11 Gbps with shaved bw allowances and the loader over its 6.25 Gbps sustained baseline (first launch of this run, aborted after one cell). At 128 B every hit leg is CPU-bound except the cache legs, which run into the ~1.27M req/s pair ceiling instead (loader ~95% CPU): the cache hit rows are floors, not ceilings, with busy_server only ~72%.

Hello app:

| leg | req/s (median) | lowc p50 | lowc p99 | spread |
| --- | --- | --- | --- | --- |
| rapira-cache hit | >= 1,266,878 (pair ceiling) | 0.12ms | 0.18ms | 0.6% |
| rapira-cache miss | 973,109 | 0.14ms | 0.25ms | 2.0% |
| rapira-cache plain | 1,161,508 | 0.14ms | 0.24ms | 0.9% |
| rapira-main hit | 261,909 | 0.24ms | 0.43ms | 1.0% |
| rapira-main miss | 578,811 | 0.17ms | 0.31ms | 0.6% |
| rapira-main plain | 1,174,781 | 0.14ms | 0.23ms | 1.4% |
| franken hit | 139,749 | 0.21ms | 1.22ms | 0.4% |
| franken miss | 104,213 | 0.28ms | 1.77ms | 15.0% |

Symfony (kernel-loop worker):

| leg | req/s (median) | lowc p50 | lowc p99 | spread |
| --- | --- | --- | --- | --- |
| rapira-cache hit | >= 1,263,239 (pair ceiling) | 0.12ms | 0.19ms | 1.0% |
| rapira-cache miss | 173,863 | 0.27ms | 0.62ms | 4.7% |
| rapira-cache plain | 173,755 | 0.25ms | 0.54ms | 1.1% |
| rapira-main hit | 259,373 | 0.25ms | 0.43ms | 1.6% |
| rapira-main miss | 137,706 | 0.32ms | 0.83ms | 3.5% |
| rapira-main plain | 176,592 | 0.25ms | 0.45ms | 0.7% |
| franken hit | 145,077 | 0.20ms | 1.21ms | 0.8% |
| franken miss | 89,695 | 0.34ms | 1.79ms | 0.6% |

- Hit: the cache is worth at least 4.8x over main's uncached ServeDir path (1,267k floor vs 262k clean server ceiling at 96% busy) and at least 8.7x over franken's php_server file path; the true cache ceiling is unmeasurable on this pair. lowc per-request p50 halves (0.12ms vs 0.24-0.25ms).
- Main's uncached hit (262k) is slower than its own miss fallthrough to resident hello PHP (579k): stat + open + read through spawn_blocking per request costs more than a full PHP hello round trip. The maindev-era note on ServeDir vs Caddy pointed the same way.
- Miss (the branch's regression control) improved, not regressed: the probe tax vs plain is -50.7% on main hello and -16.2% on the branch (973k vs 579k, +68%); on symfony the branch's tax vanishes entirely (173.9k miss vs 173.8k plain, main -22.0%). The 2026-08-30 fleet's "static miss costs ~50% of worker" finding is specific to main.
- Plain (middleware off): cache vs main is -1.1% hello / -1.6% symfony, inside or bordering the round spread; no baseline regression from the branch.
- Flags: every cache hit cell is generator_bound at the pair ceiling, and four cells across the run grazed the PPS allowance by 20 to 946 packets (~0.004% of a cell's packets; ena_throttled per the strict rule, magnitudes noise). franken legs ran server_unsaturated at its usual pool shape. Zero voided cells, zero failed k6 checks, 48/48 cells.
- Rig note: `WRK_CONNS` defaulted to the hello 250x (c=8000) for all legs including symfony; the symfony worker at ~174k req/s holds a ~46ms queue at that depth, well under the 5s timeout, and no cell voided. A future symfony-heavy static run could pin 64x for parity with bench_frameworks.

## Decisions and their reasons

- Fixed pair, no size knobs. Measured on the null runs: wrk needs ~0.62 loader cores per saturated server core, and hello at a 32-core ceiling moves ~4.4 Gbps sustained. A c7a.2xlarge loader fails both (8 cores, 3.125 Gbps baseline); c7a.4xlarge clears both. The 8xlarge server has a fixed 12.5 Gbps link, no burst credits.
- Plain builds for published tables: the prebuilt competitors do not carry frame pointers, so a frame-pointer rapira would understate its own gap. Perf sessions rebuild with frame pointers on demand.
- Strict voiding: a cell with resets, timeouts, or missing generator output is listed and excluded, never averaged. The v0.7.0 run shows why: averaging reset-y cells would have hidden the finding.
- vCPU quota L-1216C47A raised 32 to 64 on 2026-08-30; the pair needs 48.
- Null-run calibrations on the c7a.xlarge pair (2026-08-30, before the sizes were fixed): dispatcher ~240k, worker ~210k, classic ~80k req/s at c=1000, null deltas within noise at one round.

## Maindev era, retired rig

The pre-AWS harness ran servers on maindev (32 cores) with the load from a Mac over LAN or on-box. Its numbers are not comparable to the AWS baseline and are recorded here only as history. The harness itself is in git history before the 2026-08-30 wipe.

- Framework bench 2026-08-29 (on-box wrk 12t/500c/15s, medians of 3, 32 workers, rapira at feature/static-middleware): hello rapira 1,068,750 vs franken 86,349. Laravel classic 6,807 vs 5,736; Laravel worker 38,262 (bridge) vs 23,089 (Octane). Symfony classic 26,911 vs 21,968; Symfony worker 148,033 vs 72,839. Worker over classic: 5.6x Laravel, 5.5x Symfony. Laravel is ~4x heavier than Symfony per matching cell.
- Static legs 2026-08-29 (182 KiB css, on-box): rapira hit 75,191 at 5.4 ms, rapira miss 692,822 at 0.69 ms (miss tax ~-35% vs the 1,068k hello ceiling), franken hit 191,234 at 6.0 ms (plain Caddy file_server). Caddy beat the ServeDir hit path ~2.5x on-box: sendfile from page cache vs a probe plus spawn_blocking per 64 KiB chunk through hyper. Over a 12.5 Gbps wire this difference disappears; on faster links or loopback it returns.
- Framework rig operational lessons that shaped this rig: FrankenPHP ignores TERM while draining (stop is TERM, wait, KILL); an unpinned franken pool auto-sizes and breaks worker parity; backgrounded servers must close their stdio or the ssh pipe hangs.
- The Mac-as-loader ceiling (~140k rps over LAN) is what moved benching to EC2; see rapira-rs/rapira#97.
