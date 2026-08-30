# Benchmarks

This repo is the AWS bench rig for rapira (rapira-rs/rapira#97): Terraform plus ssh drivers that bench a PR against main on a two-box EC2 pair. What to know:

- Read `README.md` first (targets, knobs, cost, teardown); `INSTRUCTIONS.md` (gitignored, local) holds the methodology and per-server facts. The old maindev harness is in git history.
- Auth: `aws sso login --profile Rustatian`, account 860334207583, eu-central-1. Every AWS resource carries the tag `Project=rapira-bench`.
- Money: the rig bills per second while up; check `make status` for the remaining TTL and never leave a rig running past its use.
