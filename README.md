# Rapira benchmarks

This repository benchmarks [rapira](https://github.com/rapira-rs/rapira) against FrankenPHP, php-fpm behind nginx, nginx in front of rapira, and RoadRunner, on Amazon EC2. The CI benches every nightly build of the rapira main branch and publishes the results to a board. Everything here is reproducible by hand with one server and four loaders.

## What it measures

Each target is one server, one app, and one mode, for example `hello-rapira-worker`. The apps are a hello page, Symfony, Laravel, static files, and a gRPC echo service.

The loaders send a rate ladder: the request rate starts at a floor and doubles each stage until the target fails a stage. wrk2 generates the HTTP/1.1 load with coordinated-omission correction, and k6 generates the gRPC load. A stage passes when the target holds at least 95% of the rate with zero errors.

Each cell reports three numbers:

- `held`: the highest rate the target held.
- `peak`: the successful req/s in the first failing stage. It changes continuously with the target, so it tracks regressions.
- `unloaded`: the p50 and p99 latency at the floor rate.

Flags mark what the operator must review before a number is trusted, for example a loader at its CPU limit or a target that stopped answering. [METHOD.md](METHOD.md) defines the ladder, the pass rule, the flags, the voids, and the review before publication.

## Results

The board on the `gh-pages` branch shows one chart per target: the merged commits on the x axis, one line per ladder rate, and the p99 latency at that rate on a logarithmic scale. GitHub Pages serves it at https://rapira-rs.github.io/benchmarks/ after the first published run.

Each run also produces `runs/<id>/run.json` (the `rapira-bench-run/1` format) and the raw evidence per cell. [NOTES.md](NOTES.md) keeps dated records and the numbers of the earlier method, which do not compare with the ladder numbers.

## How CI runs it

After each successful Nightly run in `rapira-rs/rapira`, a dispatch workflow starts `.github/workflows/bench.yml` here. The job assumes an AWS role through OpenID Connect, creates the rig with Terraform, provisions the nightly asset, runs the `ci` suite (16 targets, one round, about 45 minutes, about $3), destroys the rig, and publishes the run to `gh-pages`. A run that is incomplete is not published.

## Run it yourself

You need Bash, GNU Make, Git, Python 3.11 or later, Terraform 1.10 or later, and AWS CLI v2 with an active session (`aws sso login`).

```bash
make up NIGHTLY=<sha7>
make bench
make down
```

`NIGHTLY` is the first 7 characters of the commit of the current rapira `nightly` release. `make up REF=<branch, tag, sha, or pr/N>` builds rapira on the server instead. `make bench` prints the report and returns a nonzero status when the run is incomplete.

Three suites exist: `ci` (the per-merge suite), `full` (every target, three rounds), and `ab` (rapira `pr` against `base`, three rounds, needs `REF` and `BASE_REF`). The default rig is one `c7a.8xlarge` server and four `c7a.xlarge` loaders in `eu-central-1a`. The instances bill per second, so run `make down` after every session; every box also has a lifetime of 60 minutes that the driver extends only for the run it makes.

Compare only runs from the same rig shape. Read [METHOD.md](METHOD.md) before you publish a number.

[docs/operations.md](docs/operations.md) lists every `make` target and knob, the run file contents, the remote state, the CI bootstrap, and the owner steps.

## Layout

| Path | Contents |
| --- | --- |
| `rig/` | The operator package (Python standard library): the ladder driver, the run file, report, compare, publish, provisioning |
| `box/` | The scripts that run on the boxes: provisioning, target start and stop, probe, snapshot, load |
| `servers/` | The config templates per server and the shared `php.ini` |
| `apps/` | The workloads: hello, Symfony, Laravel, static files, gRPC |
| `suites/` | The target registry and the suite files |
| `terraform/` | The rig stack, and `terraform/ci/` for the CI role and the state bucket |
| `board/` | The static board that the publish job copies to `gh-pages` |
| `tests/` | Unit tests (`make test`) and the container test of the box scripts |
