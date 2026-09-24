# Rapira AWS benchmark rig

This repository runs Rapira benchmarks on Amazon AWS EC2. Terraform creates one server instance and one loader instance in the same Availability Zone. The loader runs `wrk`, `h2load`, and `k6` against the private address of the server. The operator machine only controls the run through SSH.

The server is `c7a.8xlarge`. The loader is `c7a.4xlarge`. The default region is `eu-central-1`.

## Requirements on the operator machine

- Bash, GNU Make, Git, Python 3, `curl`, `tar`, and an OpenSSH client
- Terraform 1.5 or later
- AWS CLI v2
- An active session for the `Rustatian` AWS profile

Start the AWS session before you create the rig:

```bash
aws sso login --profile Rustatian
```

## Software on the EC2 instances

Provisioning installs these tools and packages:

| Instance | Condition | Installed software |
| --- | --- | --- |
| Server | All runs | `php-cli`, `php-devel`, `php-embedded`, `php-opcache`, `clang`, `clang-devel`, `gcc`, `make`, `cmake`, `git`, `perf`, `ethtool`, `curl`, `tar`, `diffutils`, and `python3` |
| Server | Rust is absent | Minimal Rust toolchain from `rustup` |
| Server | The `REF` tree has `crates/plugins/grpc/examples/echo_ceiling.rs` | `rapira-ceiling` binary built from that example |
| Server | `LEGS=all`, `LEGS=frameworks`, or `LEGS=grpc` | `nginx`, `php-fpm`, `composer`, `unzip`, and the FrankenPHP binary |
| Server | `LEGS=all` or `LEGS=frameworks` | `php-mbstring`, `php-xml`, `php-pdo`, `php-process`, `php-sodium`, and Composer application dependencies |
| Server | `LEGS=all` or `LEGS=grpc` | PECL `protobuf` 5.36.2 built from source, RoadRunner 2025.1.15 in `/opt/bench/fleet/rr`, and the RoadRunner PHP worker packages under `/opt/bench/fleet/roadrunner-grpc` |
| Loader | All runs | `gcc`, `gcc-c++`, `make`, `git`, `openssl-devel`, `zlib-devel`, `libev-devel`, `c-ares-devel`, `ethtool`, `curl`, `tar`, `diffutils`, `wrk`, `k6`, and `h2load` built from nghttp2 1.70.0 |
| Server | `make perf` and tool is absent | `inferno` from Cargo; the script also tries to install `php-embedded-debuginfo` |

With `LEGS=all`, provisioning loads the PECL `protobuf` extension into every PHP SAPI except FrankenPHP, so the Rapira and php-fpm rows of the fleet and framework tables from that rig run with it, in the base build and in the pr build.

The Fedora 44 EC2 image must supply Bash, `dnf`, `sudo`, OpenSSH server, cloud-init, systemd, RPM tools, core utilities, `awk`, `sed`, `grep`, procps tools, and iproute tools. Provisioning uses these base operating system tools but does not install them.

The loader installer selects wrk 4.2.0 and k6 2.2.0 when the tools are absent. It reuses installed wrk and k6 binaries. It builds h2load 1.70.0 when h2load is absent or has a different version. Record the output of `wrk --version`, `k6 version`, and `h2load --version` with each comparison.

## Standard flow

```bash
make up REF=pr/97
make bench
make down
```

The instances use on-demand billing. A shutdown terminates an instance. The bootstrap sets a maximum lifetime of 180 minutes. Provisioning replaces this value with `TTL`, which defaults to 60 minutes.

## Benchmark suites

- `make bench` compares the Rapira build from `BASE_REF` with the build from `REF`. It runs the configured dispatcher, worker, and classic modes.
- `make bench_fleet` compares the hello workload across Rapira, Rapira behind nginx, FrankenPHP, php-fpm behind nginx, and the static file legs. Provision with `LEGS=all`.
- `make bench_frameworks` compares Symfony and Laravel across the configured servers. Laravel worker rows use Octane. The direct and nginx Rapira rows use the same application worker script. Provision with `LEGS=frameworks` or `LEGS=all`.
- `make bench_static` measures static hits, static misses that continue to PHP, and direct Rapira worker requests. A hit returns `ASSET` without PHP execution. A miss checks the static path and then runs PHP. A plain row runs the same Rapira worker without the static middleware. FrankenPHP has no plain row. Provision with `LEGS=all`.
- `make bench_grpc` measures unary gRPC, gRPC-Web, and Connect calls on Rapira. It also measures the same call on the RoadRunner gRPC plugin and on the Rust ceiling, and the hello request on the Rapira HTTP dispatcher. h2load runs the closed-loop passes. k6 runs an open loop at a fixed request rate. Provision with `LEGS=grpc` or `LEGS=all`, and with `PLAIN=1`. `REF` must contain `crates/plugins/grpc/examples/echo_ceiling.rs`. Until that example merges, use `REF=chore/grpc-echo-ceiling`.
- `make perf` records a Rapira profile while `wrk` supplies load. Profiling is optional and is not part of a result table.

Use this command for a full framework bench with comparison to the latest release:

```bash
make up REF=main BASE_REF=<latest-release-tag> LEGS=frameworks PLAIN=1
make bench_frameworks
```

Use `PLAIN=1` when a table compares Rapira with FrankenPHP or php-fpm. This setting removes the Rapira profiling build options from that comparison.

## Rapira behind nginx

The fleet row is `rapira-nginx-worker`. Its matching direct row is `rapira-worker`.

The framework row is `rapira-pr-nginx-worker`. Its matching direct row is `rapira-pr-worker`.

For an nginx worker row, the loader connects to nginx on server port 8080. nginx sends the request through an HTTP/1.1 keepalive upstream to the Rapira worker at `127.0.0.1:8081`. Rapira uses `PROCESSES` worker processes. nginx uses `PROCESSES` worker processes. Both tiers use the CPUs of the same EC2 server. The row measures the complete nginx and Rapira path.

Run only the matching fleet rows:

```bash
make bench_fleet LEG_LIST='rapira-worker rapira-nginx-worker'
```

Run only the matching framework rows:

```bash
make bench_frameworks SERVERS='rapira-pr-worker rapira-pr-nginx-worker'
```

## gRPC suite

The gRPC suite has these rows:

- `rapira-http-h1` sends the hello request to the Rapira HTTP dispatcher. It is the reference row.
- `rapira-grpc`, `rapira-grpcweb-h1`, `rapira-connect-h1`, `rapira-connect-h2c`, and `rapira-connectjson-h1` send the unary Echo call to the Rapira gRPC plugin.
- `rr-grpc` sends the gRPC Echo call to the RoadRunner gRPC plugin.
- `ceiling-grpc` and `ceiling-connect-h1` send the Echo call to `rapira-ceiling`. This binary runs the Rapira gRPC server with a Rust handler and no PHP. These rows are the Rust ceiling: they show the transport throughput without PHP.

`h1` in a row name means HTTP/1.1. `h2c` means HTTP/2 without TLS. The gRPC rows use h2c.

Each cell sends one probe request before load and compares the response with the expected bytes. Then h2load runs a saturated pass with `GRPC_CONNS` connections and a low-concurrency pass with `PROCESSES` connections. Each connection has one request in progress at a time. Then k6 sends requests at `OPEN_RATE` requests per second. `rapira-http-h1` also runs a saturated `wrk` pass with `GRPC_CONNS` connections. `rapira-connect-h2c` has no k6 pass, because k6 has no h2c client.

```bash
make up REF=chore/grpc-echo-ceiling LEGS=grpc PLAIN=1
make bench_grpc
```

Read `rapira-http-h1 (wrk)` and `rapira-http-h1` first. These rows send the same request under `wrk` and under `h2load --h1`. Then read `rapira-connect-h1`, `rapira-connectjson-h1`, `rapira-grpcweb-h1`, `rapira-connect-h2c`, and `rapira-grpc` for the cost of each protocol step on Rapira. `ceiling-connect-h1` and `ceiling-grpc` show the transport without PHP. `rr-grpc` is the RoadRunner comparison for `rapira-grpc`. Do not state one row as a percentage of another row.

A `generator_bound` flag on a ceiling row means that the loader was the limit. The ceiling can be faster than the row value. State that value as "at least" the row value.

A `lowc_doubled=<n>` flag means that `<n>` workers held two or more connections during the low-concurrency warm-up.

## Settings and other targets

- `BASE_REF` selects the base Git ref. It defaults to `main`.
- `REF` selects the other Git ref. It accepts a branch, tag, commit, or `pr/N`.
- `LEGS` selects the server software that provisioning installs. It accepts `rapira`, `frameworks`, `grpc`, or `all`. It defaults to `rapira`.
- `ROUNDS` defaults to 3.
- `PROCESSES` defaults to the server CPU count.
- `WRK_DURATION` defaults to 15 seconds.
- `WRK_THREADS` defaults to the loader CPU count.
- `WRK_CONNS` defaults to 250 times `PROCESSES`, with a minimum of 1000. Framework runs use 64 times `PROCESSES` by default.
- `LOWC` defaults to 32 connections.
- `K6_VUS` defaults to 256.
- `GRPC_CONNS` sets the connection count of the saturated `make bench_grpc` passes. It defaults to 16 times `PROCESSES`. `make bench_grpc` does not use `WRK_CONNS`.
- `OPEN_RATE` sets the k6 open-loop rate of `make bench_grpc` in requests per second. It defaults to 20000.
- `WORKLOAD` selects `k6/<name>.js` and the handlers in `php/<name>/`.
- `MODES` selects the Rapira modes for `make bench`.
- `FRAMEWORKS` and `SERVERS` select framework rows.
- `LEG_LIST` selects fleet rows and gRPC rows.
- `APPS`, `SERVERS`, `KINDS`, and `ASSET` select static rows. `ASSET` defaults to `tiny.css`.
- `ALLOW_SAME=1` permits a deliberate comparison of identical Rapira builds.
- `AUTO_EXTEND=0` stops a run when the remaining instance lifetime is too short.
- `make provision REF=<ref>` provisions new refs on an active rig.
- `make sync` builds the local `../core` working tree as the `pr` leg.
- `make status` shows the instance state and the remaining lifetime.
- `make extend TTL=<minutes>` sets a new lifetime on both instances.
- `make report` renders the latest benchmark result again.
- `make nuke` removes tagged AWS resources when the Terraform state is not usable.

## Results

Each run creates `results/<timestamp>-<server-type>-<suite>/`. The directory contains raw `wrk` output, raw `k6` output, cell metadata, the expected cell list, server metadata, `run-meta.json`, and `report.txt`. Each nginx worker cell also contains `<cell>.nginx.conf` with the rendered configuration and `<cell>.nginx.txt` with the nginx build data and executable SHA-256 value.

Each gRPC cell contains `<cell>.h2load.txt` with the saturated h2load output, `<cell>.lowc.h2load.txt` with the low-concurrency h2load output, and `<cell>.config` with the rendered server configuration. Each gRPC cell except `rapira-connect-h2c` also contains `<cell>.k6.summary.json` with the k6 open-loop summary. A gRPC cell also contains `<cell>.server-log.txt` when the server log had WARN or ERROR lines. A gRPC run directory also contains `loader-tools.txt` with the first line of `h2load --version`, `k6 version`, and `wrk --version`.

The report uses the median across rounds and shows the spread and surviving cell count. It excludes voided cells and invalid artifacts from the affected values. The benchmark command returns a nonzero status when a planned cell is missing, a cell is voided, generator output is missing or invalid, either `wrk` pass reports request errors, a k6 HTTP request fails, or a k6 check fails. An nginx worker report also requires nonempty configuration and build evidence files.

Cell flags show possible measurement limits. They do not always make the command fail. Review `generator_bound`, `server_unsaturated`, `ena_throttled`, `keepalive_broken`, `worker_churn`, and `log_growth` before you publish a result.

Use only results from the same AWS rig and run for a direct comparison. Pin `AMI` when one result set takes more than one day. See `INSTRUCTIONS.md` for the review checklist and the limits of the automated checks.
