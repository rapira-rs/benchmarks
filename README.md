# Rapira AWS benchmark rig

This repository runs Rapira benchmarks on Amazon AWS EC2. Terraform creates one server instance and one loader instance in the same Availability Zone. The loader runs `wrk` and `k6` against the private address of the server. The operator machine only controls the run through SSH.

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
| Server | All runs | `php-cli`, `php-devel`, `php-embedded`, `php-opcache`, `clang`, `clang-devel`, `gcc`, `make`, `cmake`, `git`, `perf`, `ethtool`, `curl`, `tar`, and `python3` |
| Server | Rust is absent | Minimal Rust toolchain from `rustup` |
| Server | `LEGS=all` or `LEGS=frameworks` | `nginx`, `php-fpm`, `composer`, `unzip`, `php-mbstring`, `php-xml`, `php-pdo`, `php-process`, and `php-sodium` |
| Server | `LEGS=all` or `LEGS=frameworks` | FrankenPHP binary and Composer application dependencies |
| Loader | All runs | `gcc`, `make`, `git`, `openssl-devel`, `zlib-devel`, `ethtool`, `curl`, `tar`, `wrk`, and `k6` |
| Server | `make perf` and tool is absent | `inferno` from Cargo; the script also tries to install `php-embedded-debuginfo` |

The Fedora 44 EC2 image must supply Bash, `dnf`, `sudo`, OpenSSH server, cloud-init, systemd, RPM tools, core utilities, `awk`, `sed`, `grep`, procps tools, and iproute tools. Provisioning uses these base operating system tools but does not install them.

The loader installer selects wrk 4.2.0 and k6 2.2.0 when the tools are absent. It reuses installed binaries. Record the output of `wrk --version` and `k6 version` with each comparison.

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

## Settings and other targets

- `BASE_REF` selects the base Git ref. It defaults to `main`.
- `REF` selects the other Git ref. It accepts a branch, tag, commit, or `pr/N`.
- `ROUNDS` defaults to 3.
- `PROCESSES` defaults to the server CPU count.
- `WRK_DURATION` defaults to 15 seconds.
- `WRK_THREADS` defaults to the loader CPU count.
- `WRK_CONNS` defaults to 250 times `PROCESSES`, with a minimum of 1000. Framework runs use 64 times `PROCESSES` by default.
- `LOWC` defaults to 32 connections.
- `K6_VUS` defaults to 256.
- `WORKLOAD` selects `k6/<name>.js` and the handlers in `php/<name>/`.
- `MODES` selects the Rapira modes for `make bench`.
- `FRAMEWORKS` and `SERVERS` select framework rows.
- `LEG_LIST` selects fleet rows.
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

The report uses the median across rounds and shows the spread and surviving cell count. It excludes voided cells and invalid artifacts from the affected values. The benchmark command returns a nonzero status when a planned cell is missing, a cell is voided, generator output is missing or invalid, either `wrk` pass reports request errors, a k6 HTTP request fails, or a k6 check fails. An nginx worker report also requires nonempty configuration and build evidence files.

Cell flags show possible measurement limits. They do not always make the command fail. Review `generator_bound`, `server_unsaturated`, `ena_throttled`, `keepalive_broken`, `worker_churn`, and `log_growth` before you publish a result.

Use only results from the same AWS rig and run for a direct comparison. Pin `AMI` when one result set takes more than one day. See `INSTRUCTIONS.md` for the review checklist and the limits of the automated checks.
