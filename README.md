# Rapira AWS benchmark rig

This repository runs Rapira benchmarks on Amazon EC2. Terraform creates one server instance and several loader instances in one cluster placement group. The loaders send a staged rate ladder to the private address of the server: wrk2 for HTTP/1.1 targets and k6 for gRPC targets. The gRPC-Web and Connect variants use HTTP/1.1, so wrk2 loads them. The operator machine controls the run through SSH.

[METHOD.md](METHOD.md) defines the measurement method and the review before publication. [NOTES.md](NOTES.md) keeps dated records.

The default server is one `c7a.8xlarge`. The default loaders are four `c7a.xlarge`. The default region is `eu-central-1`, and the default Availability Zone is `eu-central-1a`.

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

Bench the nightly build of a commit on the rapira main branch:

```bash
make up NIGHTLY=<sha7>
make bench
make down
```

Bench a Git ref that the server builds from source:

```bash
make up REF=pr/97 SUITE=full
make bench SUITE=full
make down
```

`make up` checks the vCPU quota, writes `terraform/rig.auto.tfvars`, applies the Terraform stack, and runs `make provision`. `make bench` runs the suite, writes the run file, and prints the report. `make down` destroys the rig.

`NIGHTLY` is the first 7 characters of the commit SHA of a nightly release asset of `rapira-rs/rapira`. The core Nightly workflow deletes the assets of older builds, so use the SHA of the current `nightly` release. `REF` accepts a branch, a tag, a commit, or `pr/N`. When you set `NIGHTLY`, provisioning ignores `REF`.

Provisioning installs only the servers and apps that the suite uses. Set the same `SUITE` on `make up` and on `make bench`. To bench another suite on the same rig, run `make provision SUITE=<suite>` with the same `NIGHTLY` or `REF` first.

## Software on the EC2 instances

| Instance | Condition | Software |
| --- | --- | --- |
| Server | All runs | PHP with opcache, the shared `servers/php.ini`, and the rapira binary: the nightly release asset, or a build of `REF` and `BASE_REF` |
| Server | FrankenPHP targets | FrankenPHP 1.12.7, the glibc release asset |
| Server | php-fpm targets | php-fpm and nginx |
| Server | nginx-rapira targets | nginx |
| Server | Symfony and Laravel targets | The Composer dependencies from the committed `composer.lock` files |
| Server | gRPC targets | RoadRunner 2025.1.15 and its PHP worker packages. A server build also gets PECL protobuf 5.36.2. |
| Loader | All runs | wrk2 at commit `44a94c1`, k6 2.2.0, and a chrony synchronization check |

The Fedora 44 EC2 image supplies Bash, `dnf`, `sudo`, the OpenSSH server, cloud-init, systemd, core utilities, and the procps and iproute tools. Provisioning uses these tools but does not install them.

## Suites

`suites/targets.toml` defines every target. A target name is `<app>-<server>-<mode>`. A target that runs the base rapira binary has the suffix `-base`. A request variant has its own suffix, for example `grpc-rapira-connect`. A suite file lists its targets, the number of rounds, the stage duration, the total connection count, and the ladder floor of each app.

- `ci` is the per-merge suite: 16 targets over hello, Symfony, Laravel, static files, and gRPC, one round.
- `full` adds the nginx-rapira worker rows, the FrankenPHP stock rows, the static miss and plain rows, the 27 KiB asset, and the gRPC-Web and Connect variants. It runs three rounds.
- `ab` runs hello on the rapira worker, classic, and dispatcher modes and Symfony on the worker and classic modes, for the `pr` and the `base` binary. It runs three rounds. Provision it with `REF` and `BASE_REF`.

The driver refuses a suite before it creates a run directory when one of these conditions is true:

- A floor is not a multiple of the loader count.
- `stage_s` is less than 12.
- The connection count is not a multiple of the loader count times the loader vCPU count.

## Settings

- `SUITE` selects `suites/<name>.toml`. It defaults to `ci`.
- `NIGHTLY` selects the nightly release asset by its SHA prefix.
- `REF` selects the Git ref that the server builds. `BASE_REF` selects the base ref and defaults to `main`.
- `ROUNDS` replaces the round count of the suite.
- `PROCESSES` sets the worker count of every target. It defaults to the server CPU count.
- `SERVER_TYPE` defaults to `c7a.8xlarge`. `LOADER_TYPE` defaults to `c7a.xlarge`. `LOADER_COUNT` defaults to 4.
- `AZ` defaults to `eu-central-1a`. `REGION` defaults to `eu-central-1`.
- `AMI` pins an AMI. Without it, Terraform selects the newest Fedora 44 image, which Fedora rebuilds every day. Pin it when a result set takes more than one day.
- `TTL` sets the instance lifetime in minutes. It defaults to 60.
- `TF_BACKEND=s3` keeps the Terraform state in S3. The default `local` keeps the state in `terraform/`.
- `FRAME_POINTERS=1` builds rapira with frame pointers for a perf session. It applies to server builds only.
- `RUN` selects the run directory of `make report`. `A` and `B` select the two run directories of `make compare`.
- `AUTO_EXTEND=0` stops a run when the remaining instance lifetime is too short.

## Other targets

- `make provision` provisions the rig again, for example after a change of `SUITE` or `REF`.
- `make status` shows the Terraform outputs, the instances, and the remaining lifetime of each box.
- `make extend TTL=<minutes>` sets a new lifetime on every box.
- `make sync` builds the local `../core` working tree on the server. The next `make bench` uses that binary. The rig must come from `make up REF=<ref>`, because a nightly rig has no Rust toolchain.
- `make report` prints the tables of the newest run. `make report RUN=runs/<id>` prints another run.
- `make compare A=runs/<a> B=runs/<b>` prints the deltas between two runs.
- `make lock` creates the Symfony and Laravel `composer.lock` files. It needs PHP 8.5 and Composer on the operator machine.
- `make test` runs the unit tests.
- `make nuke` removes the tagged AWS resources when the Terraform state is not usable.
- `make grpc_fixtures` builds the gRPC descriptor, the PHP classes, and the request and response fixtures. It needs Go and access to the buf remote plugins.

## Results

Each run writes `runs/<id>/`. The run id is `<UTC timestamp>-<suite>-<rapira sha7>`. The directory holds:

- `run.json`: the run file with the schema `rapira-bench-run/1`. It records the rig, the rapira build, the server versions, the php.ini text, the app hashes, the loaders, the ladder, every cell, and every stage.
- `raw/<cell>/`: the wrk2 or k6 output of each stage and loader, the rendered server configs, the WARN and ERROR lines of the server log, and the snapshots.

`make bench` and `make report` return a nonzero status when the run is incomplete. An incomplete run has a missing, voided, or interrupted cell. The report then ends with `Do not publish these tables.`

`make compare` refuses two runs with a different server type, loader type, loader count, worker count, or stage duration. Give `--force` to `python3 -m rig compare` to compare them anyway.

`python3 -m rig publish --pages-dir <dir> runs/<id>/run.json` adds a run to a checkout of the `gh-pages` branch: it writes `data/<id>.json` and updates `data/index.json`.

Use only runs from the same rig shape for a direct comparison. Read [METHOD.md](METHOD.md) before you publish a number.

## Remote state

`make up TF_BACKEND=s3` copies `terraform/backend.tf.s3` to `terraform/backend.tf`. The S3 settings come from the `TF_CLI_ARGS_init` environment variable:

```bash
export TF_CLI_ARGS_init='-backend-config=bucket=<bucket> -backend-config=key=rig/terraform.tfstate -backend-config=region=eu-central-1 -backend-config=use_lockfile=true'
make up TF_BACKEND=s3 NIGHTLY=<sha7>
```

Keep `TF_BACKEND=s3` and the variable set for every later target that uses Terraform, for example `make down`.

## Cost and teardown

The instances use on-demand billing per second. The `ci` suite takes about 45 minutes and costs about $3 with the default rig. The server type is most of the cost.

Every box has a lifetime. The bootstrap sets 180 minutes. Provisioning replaces it with `TTL`. `make bench` extends it from the run estimate: the number of cells times (8 times `stage_s` plus 60) plus 300 seconds. When the lifetime ends, the box shuts down, and a shutdown terminates the instance.

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

The output `role_arn` is the value of the repository variable `AWS_ROLE_ARN`. The output `bucket` is the value of the repository variable `TF_STATE_BUCKET`.

The role trusts only jobs on the `main` branch of this repository. This repository uses the immutable OIDC subject format, which contains the owner id and the repository id. The variable `github_sub_prefix` holds that prefix. This command prints the current value:

```bash
gh api repos/rapira-rs/benchmarks/actions/oidc/customization/sub --jq .sub_claim_prefix
```

An AWS account has at most one OIDC provider for `token.actions.githubusercontent.com`. If the apply stops with `EntityAlreadyExists`, import the provider and apply again:

```bash
terraform -chdir=terraform/ci import aws_iam_openid_connect_provider.github arn:aws:iam::<account id>:oidc-provider/token.actions.githubusercontent.com
```

The role policy allows the EC2 actions of the rig stack in `eu-central-1`, all EC2 describe calls, the service quota read, and read and write access to the `rig/` objects of the state bucket.

With `TF_BACKEND=s3`, the rig stack keeps its state in the bucket. `terraform/s3.tfbackend` holds the key `rig/terraform.tfstate`, the region, and `use_lockfile = true`. The bucket name holds the account id, so it is not in the repository: the CI workflow gives it to `terraform init` through `TF_CLI_ARGS_init`. If a CI run leaves a rig that bills, run `make nuke` on the operator machine. It finds the resources by their tag and needs no state.
