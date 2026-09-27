# Operations

This document is the reference for the operator: the requirements, every `make` target and knob, the run files, the remote state, the CI bootstrap, and the owner steps. [README.md](../README.md) gives the overview, and [METHOD.md](../METHOD.md) gives the measurement method.

## Requirements on the operator machine

- Bash, GNU Make, Git, `curl`, `tar`, and an OpenSSH client
- Python 3.11 or later. The rig uses only the Python standard library.
- Terraform 1.10 or later
- AWS CLI v2 with an active session for the default profile

Start the AWS session before you create the rig:

```bash
aws sso login
```

## Standard flow

Bench the nightly build of a commit on the rapira main branch against a base build:

```bash
make up NIGHTLY=<sha7> BASE=<sha7>
make bench
make down
```

Bench a Git ref that the server builds from source against a base build:

```bash
make up REF=pr/97 BASE=<sha7>
make bench
make down
```

`make up` checks the vCPU quota, writes `terraform/rig.auto.tfvars`, applies the Terraform stack, and runs `make provision`. `make bench` runs the suite, writes the run file, and prints the report. `make down` destroys the rig.

`NIGHTLY` is the first 7 characters of the commit SHA of a nightly release asset of `rapira-rs/rapira`. The core Nightly workflow deletes the assets of older builds, so use the SHA of the current `nightly` release. `REF` accepts a branch, a tag, a commit, or `pr/N`. When you set `NIGHTLY`, provisioning ignores `REF`. The new build of the run is the build of `NIGHTLY` or `REF`.

`BASE` is the first 7 characters of the commit SHA of the base build. It is required. The server installs the base build from the release cache, so the tarball of `BASE` must be in the `binaries` release of this repository. Set `BASE` equal to `NIGHTLY` for an A/A run, which measures the noise of the rig. This command lists the cached tarballs:

```bash
gh release view binaries -R rapira-rs/benchmarks --json assets --jq '.assets[].name'
```

`python3 -m rig base --index <file> --new <sha7>` prints the base build that CI selects: the sha7 of the newest non-smoke run in the index file whose sha is not `<sha7>`. It fails when the index has no such run. `--base <sha7>` prints the given sha7. This command gets the index of the board:

```bash
gh api -H "Accept: application/vnd.github.raw+json" "repos/rapira-rs/benchmarks/contents/data/index.json?ref=gh-pages" > index.json
```

Provisioning installs only the apps that the suite uses. Set the same `SUITE` on `make up` and on `make bench`.

## Software on the EC2 instances

| Instance | Condition | Software |
| --- | --- | --- |
| Server | All runs | PHP with opcache, the shared `servers/php.ini`, and two rapira binaries: the new build under `/opt/bench/rapira/<sha7>`, from the nightly release asset or a build of `REF`, and the base build under `/opt/bench/rapira/base-<sha7>`, from the `binaries` release |
| Server | Yii3 target | The PHP modules `mbstring`, `xml`, `pdo`, and `pgsql`, Composer, the app-api at the commit of `apps/yii3/source.toml` with the dependencies of the committed `composer.lock`, and the Valkey service for its route cache |
| Server | gRPC target | Composer and the pure PHP protobuf runtime of `apps/grpc/composer.lock` |
| Loader | All runs | wrk2 at commit `44a94c1`, h2load from the Fedora `nghttp2` package, and a chrony synchronization check |

The Fedora 44 EC2 image supplies Bash, `dnf`, `sudo`, the OpenSSH server, cloud-init, systemd, core utilities, and the procps and iproute tools. Provisioning uses these tools but does not install them.

## Suites

`suites/targets.toml` defines every target. A target name is `<app>-rapira-<mode>`, with the suffix `-static` for the static middleware; the gRPC target is `grpc-rapira`. A suite file has these keys:

- `name`, `rounds`, `smoke`, and `targets`, the list of target names.
- `[connections]`: the connection count of each proto, `http1` and `grpc`.
- `[grpc]`: `streams`, the stream count of each gRPC connection.
- `[stages.rate]`: `warmup_s` and `duration_s` of the rate stage. The rate stage runs one rapira process per server vCPU.
- `[stages.cap]`: `warmup_s`, `duration_s`, and `processes`, the rapira process count of the capacity stage.
- `[stages.rate.rates]` and `[stages.cap.rates]`: the rate of each target of the suite in that stage kind, as the total req/s over all loaders. The keys are target names.

`ci` is the per-merge suite: the six targets, 3 rounds, 1000 connections for the HTTP targets, and 100 connections with 100 streams each for the gRPC target. The rate stage has an 11 s warm-up and 15 s measured. The capacity stage has a 5 s warm-up, 15 s measured, and 2 processes. [METHOD.md](../METHOD.md) gives the rules for its rates.

The driver refuses a suite before it creates a run directory when one of these conditions is true:

- `rounds` is less than 1.
- `stages` does not have exactly the keys `rate` and `cap`.
- A `duration_s` is less than 15, or a `warmup_s` is negative.
- `stages.rate` sets `processes`, or `stages.cap.processes` is missing or less than 1.
- A target of the suite has no rate in a stage kind, or a rates table names a target that is not in the suite.
- A target name is not in `suites/targets.toml` or is in the list two times, or a proto of a target has no connection count.
- A rate or a connection count is not a multiple of the loader count.

`make bench` also refuses an HTTP connection count that is not a multiple of the loader count times the loader vCPU count. It does this check after the rig is up.

## Settings

- `SUITE` selects `suites/<name>.toml`. It defaults to `ci`.
- `NIGHTLY` selects the nightly release asset by its SHA prefix.
- `REF` selects the Git ref that the server builds.
- `BASE` selects the base build by its SHA prefix. It is required.
- `ROUNDS` replaces the round count of the suite.
- `SERVER_TYPE` defaults to `c7a.2xlarge`. `LOADER_TYPE` defaults to `c7a.xlarge`. `LOADER_COUNT` defaults to 2. The default rig needs 16 vCPUs of the quota.
- `LOADER_COUNT` must divide every rate and connection count of the suite, and the HTTP connection count must be a multiple of the loader count times the loader vCPU count. `make bench` refuses another count after the rig is up.
- `AZ` defaults to `eu-central-1a`. `REGION` defaults to `eu-central-1`.
- `AMI` pins an AMI. Without it, Terraform selects the newest Fedora 44 image, which Fedora rebuilds every day. Pin it when a result set takes more than one day.
- `TTL` sets the instance lifetime in minutes. It defaults to 60.
- `TF_BACKEND=s3` keeps the Terraform state in S3. The default `local` keeps the state in `terraform/`.
- `FRAME_POINTERS=1` builds rapira with frame pointers for a perf session. It applies to server builds only.
- `RUN` selects the run directory of `make report`.
- `PAGES` selects the pages directory of `make board`. It defaults to `runs/pages`.
- `AUTO_EXTEND=0` stops a run when the remaining instance lifetime is too short.

## Other targets

- `make provision` provisions the rig again, for example after a change of `SUITE` or `REF`. It needs `BASE` and `NIGHTLY` or `REF`, as `make up` does.
- `make status` shows the Terraform outputs, the instances, and the remaining lifetime of each box.
- `make extend TTL=<minutes>` sets a new lifetime on every box.
- `make sync` builds the local `../core` working tree on the server and installs it under `/opt/bench/rapira/local`. The next `make bench` uses that binary as the new build. The base build does not change. The rig must come from `make up REF=<ref> BASE=<sha7>`, because a nightly rig has no Rust toolchain.
- `make report` prints the summary table of the newest run. `make report RUN=runs/<id>` prints another run.
- `make board` copies the board files into `PAGES` and serves the directory on 127.0.0.1:8000. Fill the directory first with `python3 -m rig publish --pages-dir runs/pages runs/<id>/run.json`.
- `make lock` creates `apps/yii3/composer.lock`, `apps/yii3/expect.json`, and `apps/grpc/composer.lock` from the pinned sources. It needs PHP 8.5, Composer, and a Valkey or Redis server on 127.0.0.1:6379 on the operator machine, for example `docker run --rm -d -p 127.0.0.1:6379:6379 valkey/valkey:9.1.2-alpine`.
- `make test` runs the unit tests.
- `make nuke` removes the tagged AWS resources when the Terraform state is not usable.
- `make grpc_fixtures` builds the gRPC descriptor, the PHP classes, and the request and response fixtures. It needs Go and access to the buf remote plugins.

## Results

Each run writes `runs/<id>/`. The run id is `<UTC timestamp>-<suite>-<rapira sha7>`. The directory holds:

- `run.json`: the run file with the schema `rapira-bench-run/3`. It records the rig, the new build in `rapira` with its pull request, the base build in `base`, the version lines of the server, the php.ini text, the app hashes, the loaders, the suite, every cell, the run status, and the summary. A cell records its build, its stage kind, its process count, its rate, its achieved rate, its latency, its RSS, its `held` state, its flags, and the record of each loader with the wrk2 calibration values.
- `raw/<cell>/`: the wrk2 or h2load output of each loader with its `RESULT` line, the rendered rapira config, the WARN and ERROR lines of the server log, and the snapshots.

The run status is `complete` when every planned cell ran. A complete run can hold void cells. The status is `incomplete` when a cell is missing or interrupted, and `broken` when every new cell of one target is void. `make bench` and `make report` return a nonzero status when the run is not complete.

`python3 -m rig publish --pages-dir <dir> runs/<id>/run.json` adds a complete run to a checkout of the `gh-pages` branch: it writes `data/<id>.json` and updates `data/index.json`. The index entry records the run schema and the sha of the base build. `rig publish` refuses an incomplete or a broken run.

`python3 -m rig bench --suite <name> --smoke` marks the run as a smoke run. The board does not show it, and the CI commit check ignores it.

Each run compares its two builds on the same instances. Do not compare the absolute numbers of two runs. Read [METHOD.md](../METHOD.md) before you publish a number.

## Release cache

The release with the tag `binaries` in this repository keeps the php8.5 linux x86_64 nightly tarballs of rapira and the `rapira-v<version>-SHA256SUMS.txt` files of the core nightly release, under their core names. The base build of a run comes from this release, because the core Nightly workflow deletes the assets of older builds.

- Provisioning finds the tarball of `BASE` in this release, verifies it against the cached SHA256SUMS file, and checks its libraries with `ldd`.
- The publish job of CI uploads the tarball and the SHA256SUMS file of each published new build, and then deletes all tarballs except the newest 10 with their SHA256SUMS files.
- A `BASE` that is not in the release fails the provisioning. In CI it fails the bench job before `make up`, with an error that names the sha.

## Remote state

`make up TF_BACKEND=s3` copies `terraform/backend.tf.s3` to `terraform/backend.tf`. `terraform/s3.tfbackend` holds the key, the region, and `use_lockfile`. Only the bucket comes from the `TF_CLI_ARGS_init` environment variable:

```bash
export TF_CLI_ARGS_init='-backend-config=s3.tfbackend -backend-config=bucket=<bucket>'
make up TF_BACKEND=s3 NIGHTLY=<sha7> BASE=<sha7>
```

Keep `TF_BACKEND=s3` and the variable set for every later target that uses Terraform, for example `make down`.

## Cost and teardown

The instances use on-demand billing per second. In eu-central-1 the default rig costs 0.93704 USD per hour: 0.46852 USD for the c7a.2xlarge server and 0.23426 USD for each c7a.xlarge loader.

The `ci` suite has 72 cells. The measured overhead is about 9 s per cell, so a rate cell takes about 35 s and a capacity cell about 29 s. The cells take about 38 minutes. The rig creation, the provisioning, and the destroy take about 2.1 minutes. A CI run therefore holds the rig for about 41 minutes, which costs about 0.65 USD. The EBS volumes and the public IPv4 addresses add about 0.03 USD. The upper bound of a normal run is about 48 minutes, which costs about 0.75 USD. The job timeout of 75 minutes stops a run that hangs.

Every box has a lifetime. The bootstrap sets 75 minutes. Provisioning replaces it with `TTL`. `make bench` extends it from the run estimate: for each cell, `warmup_s` plus `duration_s` of its stage kind plus 20 seconds, and 300 seconds for the run. For the `ci` suite that is 36 x (11 + 15 + 20) + 36 x (5 + 15 + 20) + 300 = 3396 seconds. When the lifetime ends, the box shuts down, and a shutdown terminates the instance. A CI runner that stops before the provisioning therefore bills at most about 1.2 USD.

- Run `make down` after every session. Check `make status` when you are not sure.
- When `make down` fails, run it again. Placement group deletion can lag instance termination.
- When the Terraform state is lost, run `make nuke`, then `make up`. `make nuke` deletes only the resources with the tag `Project=rapira-bench`.

## Tests

`make test` runs the unit tests with `python3 -m unittest discover -s tests -t .`. `tests/test_box.py` runs the box scripts in a container. The header of that file gives the command.

## CI bootstrap

The stack in `terraform/ci/` creates the AWS resources of the bench workflow: the GitHub OIDC identity provider, the IAM role `rapira-bench-ci`, and the S3 bucket `rapira-bench-tfstate-<account id>` for the rig state. The owner applies it once with the default AWS CLI profile. Its state stays in `terraform/ci/` on the owner machine and git ignores it. Keep that state file: a later apply needs it to find the resources.

```bash
aws sso login
terraform -chdir=terraform/ci init
terraform -chdir=terraform/ci apply
terraform -chdir=terraform/ci output
```

The output `role_arn` is the value of the repository secret `AWS_ROLE_ARN`. The output `bucket` is the value of the repository secret `TF_STATE_BUCKET`. Secrets are masked in the workflow logs. Repository variables are not.

The role trusts only jobs on the `main` branch of this repository. This repository uses the immutable OIDC subject format, which contains the owner id and the repository id. The variable `github_sub_prefix` holds that prefix. This command prints the current value:

```bash
gh api repos/rapira-rs/benchmarks/actions/oidc/customization/sub --jq .sub_claim_prefix
```

An AWS account has at most one OIDC provider for `token.actions.githubusercontent.com`. If the apply stops with `EntityAlreadyExists`, import the provider. Then apply again.

```bash
terraform -chdir=terraform/ci import aws_iam_openid_connect_provider.github arn:aws:iam::<account id>:oidc-provider/token.actions.githubusercontent.com
```

After the import, this stack owns the provider. A destroy of this stack removes the provider for every role that uses it.

The role policy allows the EC2 actions of the rig stack in `eu-central-1`, all EC2 describe calls, the service quota read, and read and write access to the `rig/` objects of the state bucket.

With `TF_BACKEND=s3`, the rig stack keeps its state in the bucket. `terraform/s3.tfbackend` holds the key `rig/terraform.tfstate`, the region, and `use_lockfile = true`. The bucket name holds the account id, so it is not in the repository: the CI workflow gives it to `terraform init` through `TF_CLI_ARGS_init`. If a CI run leaves a rig that bills, run `make nuke` on the operator machine. It finds the resources by their tag and needs no state.

## CI

After each successful Nightly run on the rapira main branch, the core repository starts `.github/workflows/bench.yml` in this repository. The bench job runs these steps:

- The resolve step finds the commit of the current `nightly` release, looks up the merged pull request of that commit for the board label, and stops when the board already has that commit. It selects the base build with the `base` input of the dispatch, or else with `python3 -m rig base` on the index of the board. It fails when the index has no other non-smoke run and the `base` input is empty. It checks that the tarball of the base build is in the `binaries` release. It downloads the new tarball and its SHA256SUMS file into `$RUNNER_TEMP` and keeps them as an artifact for 1 day.
- The rig step creates the rig with the S3 backend and provisions it with `NIGHTLY` and `BASE`. A capacity error retries once in `eu-central-1b`.
- The suite step runs `suites/ci.toml`. It fails when the run is incomplete or broken.
- The job uploads `run.json` and `raw/` as artifacts for 90 days. `make down` runs at the end of each bench job that passes the commit check, also after a failure. `make nuke` runs only when `make down` failed.

The publish job runs only when the run is complete. It uploads the new tarball and its SHA256SUMS file to the `binaries` release, deletes all tarballs except the newest 10 with their SHA256SUMS files, adds the run to the `gh-pages` branch, copies `board/` there, and pushes the branch. The commit step skips when nothing changed, so a rerun of the publish job works.

To choose the base build of a manual run, give the `base` input:

```bash
gh workflow run bench.yml -R rapira-rs/benchmarks -f base=<sha7>
```

Only one bench run runs at a time. A new dispatch replaces a waiting one and never stops a running one. The bench job stops after 75 minutes. The rig step stops after 30 minutes and the suite step after 55 minutes. A local rig and the CI bench cannot run at the same time, because they use the same resource names and the same tag. Check the Bench runs of this repository before `make up` and before `make nuke`.

Do these owner steps once, in this order:

1. Examine the tracked files and the git history for account ids, IP addresses, and keys. Then make this repository public.
2. Create the `gh-pages` branch with the commands below.
3. In the repository settings, open Pages. Select "Deploy from a branch". Select the branch `gh-pages` with the folder `/`.
4. Apply `terraform/ci` as the "CI bootstrap" section above shows.
5. Set the repository secrets `AWS_ROLE_ARN` and `TF_STATE_BUCKET` with the commands below.
6. Create a fine-grained token for the resource owner `rapira-rs` with access to the repository `rapira-rs/benchmarks` only and the permission "Actions: Read and write". If the organization approves tokens, approve the request. The token has an expiry date. Create a new token before that date and repeat step 7.
7. Store the token as the secret `BENCH_DISPATCH_TOKEN` in `rapira-rs/rapira`.
8. Copy `docs/core-dispatch.yml` to `.github/workflows/bench-dispatch.yml` in `rapira-rs/rapira` through a pull request.
9. Create the `binaries` release with the commands below. Upload the tarball and the SHA256SUMS file of the current nightly build from the `nightly` release of `rapira-rs/rapira`. This build is the base build of the first run.
10. Start the first run by hand with `gh workflow run bench.yml -R rapira-rs/benchmarks -f base=<sha7>`, where `<sha7>` is the build of step 9. Examine the result on the board.

Commands for step 2:

```bash
git switch --orphan gh-pages
git commit --allow-empty -s -S -m "chore: start the board branch"
git push origin gh-pages
git switch main
```

Commands for step 5 and step 7:

```bash
gh secret set AWS_ROLE_ARN -R rapira-rs/benchmarks --body "$(terraform -chdir=terraform/ci output -raw role_arn)"
gh secret set TF_STATE_BUCKET -R rapira-rs/benchmarks --body "$(terraform -chdir=terraform/ci output -raw bucket)"
gh secret set BENCH_DISPATCH_TOKEN -R rapira-rs/rapira
```

Commands for step 9:

```bash
gh release download nightly -R rapira-rs/rapira -p 'rapira-v*-nightly.<sha7>-php8.5-linux-x86_64.tar.gz' -p 'rapira-v*-nightly.<sha7>-SHA256SUMS.txt'
gh release create binaries -R rapira-rs/benchmarks --title binaries --notes "The rapira builds of the board runs."
gh release upload binaries -R rapira-rs/benchmarks rapira-v*-nightly.<sha7>-*
```
