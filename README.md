# rapira AWS bench rig

Two on-demand EC2 boxes in eu-central-1, managed by Terraform: a server box that builds and runs rapira, and a loader box that drives wrk and k6 over the free same-AZ private network. The default bench is an A/B of rapira at `BASE_REF` (main) against a PR ref, both built on the same box, so host drift cancels out. Tracked as rapira-rs/rapira#97.

## Prerequisites

- `terraform`, `aws` (v2), `python3`, `git` on this machine.
- `aws sso login --profile Rustatian` with a fresh session.

## Flow

```bash
make up REF=pr/97          # apply + provision + build base and pr (~25 min first time)
make bench                 # interleaved A/B, medians, report
make perf MODE=worker      # flamegraph of one leg under load
make down                  # destroy everything
```

Rebench another ref on a running rig with `make provision REF=<ref>` (no terraform change). Bench the local uncommitted `../core` tree with `make sync` (it becomes the pr leg; tracked files only).

## Targets

- `up` - quota preflight, terraform apply, then provision. Knobs: `TTL` minutes (default 60), `AMI` (pin for a baseline set), `REF`, `BASE_REF`, `LEGS=all`, `PLAIN=1`. The pair is fixed: a c7a.8xlarge server (32 cores, no SMT, fixed 12.5 Gbps network) and a c7a.4xlarge loader, sized to saturate it on both CPU (~0.62 loader cores per server core, measured) and sustained bandwidth.
- `provision` - rerun the ssh provisioning with new refs on a running rig.
- `bench` - the A/B. Each cell runs three passes against one server start: wrk at saturating load (`WRK_CONNS` defaults to max(1000, 250x processes)), wrk at low concurrency for per-request latency (`LOWC`, 32), and k6 for checked latency (`K6_VUS`, 256). Knobs: `ROUNDS` (3), `MODES` ("dispatcher worker classic"), `WORKLOAD` (hello), `WRK_DURATION` (15s), `PROCESSES` (server cores), `AUTO_EXTEND=0` to fail on a short TTL, `ALLOW_SAME=1` for a null-run calibration.
- `bench_fleet` - rapira (pr) plus franken, fpm, roadrunner, swoole, and the static file legs; needs `LEGS=all` at provision time. Round-interleaved with rotated leg order. The static legs serve `fleet/static/app.css` (a 27 KiB stylesheet): hit fetches the file (rapira static middleware, franken php_server file path), miss requests the hello URL through the same server, so miss minus plain worker is the probe tax. Static hit throughput is wire-bound at 12.5 Gbps, so compare hit legs against each other, never against hello rows.
- `perf` - `LEG` (base or pr), `MODE`, `DUR` seconds; fetches flame.svg and perf.data. The server has perf, `kernel.perf_event_paranoid=-1`, `kernel.kptr_restrict=0`, php debuginfo, and every rapira build carries frame pointers plus line tables.
- `sync` - upload the local core working tree, rebuild the pr binary from it.
- `status` - instance states and remaining TTL. `extend TTL=120` re-arms the TTL.
- `report` - re-render the latest run in `results/`.
- `down` - terraform destroy. `nuke` - tag-scoped aws-cli teardown when tfstate is lost or the TTL already fired; after state loss the order is `make nuke`, then `make up`.

## Workloads

A workload is one k6 script plus one PHP handler per rapira mode: `k6/<name>.js` and `php/<name>/<mode>.php`. `WORKLOAD=<name>` selects it for the A/B, and the handler files define the mode set. The k6 script contract lives in the header of `k6/hello.js`; a script that declares thresholds on `http_reqs{scenario:x}` submetrics gets one report row per scenario automatically. The wrk passes always probe the plain GET route, so every workload must answer a bare GET. The fleet configs serve the hello workload only.

## Money

The pair costs about $2.83 per hour all-in (c7a.8xlarge $1.87 + c7a.4xlarge $0.94 + gp3 roots and public IPv4). A full session from apply to destroy is roughly 30 to 45 minutes, so $1.50 to $2.10. The instances self-terminate: the bootstrap arms a 180 minute outer bound at boot, provisioning narrows it to TTL, a systemd unit re-arms it after a reboot. The pair needs 48 on-demand vCPUs; the account quota (L-1216C47A, currently 64) is checked by the preflight.

## Reading results

- Each run writes `results/<stamp>-<type>-<kind>/` with raw wrk and k6 output per cell, per-cell metadata, `run-meta.json` (shas, binary and workload hashes, AMI, kernel, php build, knobs), and `report.txt`.
- The report shows medians with the cross-round spread and cell counts. Voided cells (non-2xx, socket errors, missing generator output) are listed, never averaged; a run with missing cells or failed k6 checks is stamped INCOMPLETE or BROKEN and must not be published.
- Flags (`generator_bound`, `server_unsaturated`, `ena_throttled`, `keepalive_broken`, `worker_churn`, `log_growth`) mark cells whose number is not a clean server ceiling. `INSTRUCTIONS.md` explains each flag and how to read the tables (Little's law, the lowc pass, the k6 probe, network credits).
- The server network is a fixed 12.5 Gbps; the loader's 6.25 Gbps sustained baseline covers hello traffic to about 2.3M req/s before burst credits come into play. The per-cell ENA counter diffs stay the truth on throttling either way.
- AWS numbers form their own baseline set. Never mix numbers from another rig or instance size into one table, and pin `AMI` when a set spans days.
- Fleet caveat: rapira carries frame pointers, the prebuilt competitors do not. Provision with `PLAIN=1` before a publishable fleet table.
