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
