# Rapira benchmarks

The benchmark rig of [rapira](https://github.com/rapira-rs/rapira). It runs on Amazon EC2: one server, four loaders, and a rate ladder that doubles the request rate until the target fails.

## What is tested

Rapira in its worker, classic, and dispatcher modes against other PHP application servers, on a set of PHP apps from a hello page and static files to a gRPC echo service. `suites/targets.toml` lists every target, and the `ci` suite runs 16 of them after each nightly build of rapira main.

Each target reports the highest rate it held, its peak throughput in the first failing stage, and its unloaded latency. [METHOD.md](METHOD.md) defines the ladder, the pass rule, the flags, and the voids.

## Where the results are

- The board: https://rapira-rs.github.io/benchmarks/ (one chart per target, the merged commits on the x axis, one line per rate, p99 latency).
- The run files: `run.json` and the raw evidence of every CI run, as workflow artifacts and in the `gh-pages` branch under `data/`.
- [NOTES.md](NOTES.md): dated records, including the numbers of the earlier method.

[docs/operations.md](docs/operations.md) has the commands, the knobs, the cost, and the CI setup.
