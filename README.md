# Rapira benchmarks

The benchmark rig of [rapira](https://github.com/rapira-rs/rapira). It runs on Amazon EC2: one server, one loader, and a constant request rate per target.

## What is tested

Rapira in its classic, worker, and dispatcher modes on a hello app, the dispatcher behind the static middleware, the dispatcher with a Yii3 API app, and a gRPC echo service: six targets, listed in `suites/targets.toml`. The `ci` suite runs them after each nightly build of rapira main.

Each target reports two numbers at its rate: the p99 latency and the RSS of the rapira process tree, next to the rate it achieved. [METHOD.md](METHOD.md) defines the stage, the `held` rule, the flags, and the voids.

## Where the results are

- The board: https://rapira.rs/benchmarks/ (two charts, p99 and RSS, one line per target, the merged pull requests on the x axis).
- The run files: `run.json` and the raw evidence of every CI run, as workflow artifacts and in the `gh-pages` branch under `data/`.
- [NOTES.md](NOTES.md): dated records, including the numbers of the earlier methods.

[docs/operations.md](docs/operations.md) has the commands, the knobs, the cost, and the CI setup.
