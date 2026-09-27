# Rapira benchmarks

The benchmark rig of [rapira](https://github.com/rapira-rs/rapira). It runs on Amazon EC2: one c7a.2xlarge server and two c7a.xlarge loaders. Each run measures the new nightly build and the base build, which is the build of the previous board run, one after the other on the same instances.

## What is tested

Rapira in its classic, worker, and dispatcher modes on a hello app, the dispatcher behind the static middleware, the dispatcher with a Yii3 API app, and a gRPC echo service: six targets, listed in `suites/targets.toml`. The `ci` suite runs them after each nightly build of rapira main.

Each target runs two stage kinds for each build. The rate stage runs one rapira process per server vCPU at a constant rate that keeps the server at about 50% CPU, and reports the p99 latency and the RSS of the rapira process tree. The capacity stage runs 2 rapira processes at a rate above their capacity, and reports the achieved req/s. The base cell and the new cell of one round, target, and stage kind are a pair. Each run reports the median change from the base build to the new build over its pairs. [METHOD.md](METHOD.md) defines the stages, the pairs, the flags, the voids, and the summary.

## Where the results are

- The board: https://rapira.rs/benchmarks/ (one row per target with the change of the capacity, the p99, and the RSS against the base build in percent, the spread of the pairs as a band, and the merged pull requests on the x axis).
- The run files: `run.json` and the raw evidence of every CI run, as workflow artifacts and in the `gh-pages` branch under `data/`.
- [NOTES.md](NOTES.md): dated records, including the numbers of the earlier methods.

[docs/operations.md](docs/operations.md) has the commands, the knobs, the cost, and the CI setup.
