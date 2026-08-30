# Bench notes

Dated records and the decisions the code cannot show. Methodology lives in INSTRUCTIONS.md, operations in README.md. Numbers from different rigs or instance sizes never mix into one table.

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
