# AWS bench rig for rapira. See README.md for the flow and the knobs.
# Bench knobs (ROUNDS, MODES, WORKLOAD, WRK_*, ...) are given on the command
# line and reach the scripts through make's automatic export.

SHELL := /bin/bash
.NOTPARALLEL:

PROFILE ?= Rustatian
REGION ?= eu-central-1
# Fixed pair: the loader needs ~0.62 cores and ~4.4 Gbps sustained per
# saturated 32-core server, which rules the smaller sizes out.
SERVER_TYPE := c7a.8xlarge
LOADER_TYPE := c7a.4xlarge
AZ ?= eu-central-1a
TTL ?= 60
BASE_REF ?= main
REF ?=
LEGS ?= rapira
PLAIN ?= 0
AMI ?=

TF := terraform -chdir=terraform
AWSC := aws --profile $(PROFILE) --region $(REGION)

.PHONY: up provision status bench bench_fleet bench_frameworks perf sync extend report down nuke preflight

preflight:
	@$(AWSC) sts get-caller-identity >/dev/null 2>&1 || \
	  { echo "ERROR: AWS auth failed; run: aws sso login --profile $(PROFILE)"; exit 1; }

# The chosen knobs persist in rig.auto.tfvars so every later terraform
# operation (provision, status, down) sees the applied values; re-running
# `make up` rewrites them on purpose.
up: preflight
	@test -n "$(REF)" || { echo "ERROR: set REF (branch, tag, sha, or pr/N), e.g. make up REF=pr/97"; exit 1; }
	@quota=$$($(AWSC) service-quotas get-service-quota --service-code ec2 --quota-code L-1216C47A --query Quota.Value --output text 2>/dev/null); \
	test -n "$$quota" || { echo "ERROR: could not read quota L-1216C47A; check the profile permissions"; exit 1; }; \
	sv=$$($(AWSC) ec2 describe-instance-types --instance-types $(SERVER_TYPE) --query 'InstanceTypes[0].VCpuInfo.DefaultVCpus' --output text 2>/dev/null); \
	test -n "$$sv" || { echo "ERROR: unknown instance type $(SERVER_TYPE)"; exit 1; }; \
	lv=$$($(AWSC) ec2 describe-instance-types --instance-types $(LOADER_TYPE) --query 'InstanceTypes[0].VCpuInfo.DefaultVCpus' --output text 2>/dev/null); \
	test -n "$$lv" || { echo "ERROR: unknown instance type $(LOADER_TYPE)"; exit 1; }; \
	awk -v q=$$quota -v n=$$((sv + lv)) 'BEGIN { if (n > q) { printf "ERROR: the pair needs %d vCPUs, the account quota is %d (adjustable: quota L-1216C47A)\n", n, q; exit 1 } }'
	@ip=$$(curl -fs https://checkip.amazonaws.com) || \
	  { echo "ERROR: could not determine the operator IP"; exit 1; }; \
	{ echo "profile = \"$(PROFILE)\""; \
	  echo "region = \"$(REGION)\""; \
	  echo "az = \"$(AZ)\""; \
	  echo "server_instance_type = \"$(SERVER_TYPE)\""; \
	  echo "loader_instance_type = \"$(LOADER_TYPE)\""; \
	  echo "ssh_cidr = \"$$ip/32\""; \
	  $(if $(AMI),echo "ami_id = \"$(AMI)\"";) } > terraform/rig.auto.tfvars
	$(TF) init -input=false
	$(TF) apply -auto-approve -input=false || \
	  { echo "ERROR: apply failed; a partial rig may be billing, run 'make down'. On InsufficientInstanceCapacity retry with AZ=eu-central-1b or 1c."; exit 1; }
	@$(MAKE) --no-print-directory provision

provision:
	@BASE_REF=$(BASE_REF) REF=$(REF) LEGS=$(LEGS) TTL=$(TTL) PLAIN=$(PLAIN) scripts/provision.sh

status: preflight
	@out=$$($(TF) output 2>/dev/null); \
	if [ -n "$$out" ]; then echo "$$out"; else echo "(no terraform outputs; rig not applied)"; fi
	@$(AWSC) ec2 describe-instances --filters Name=tag:Project,Values=rapira-bench \
	  Name=instance-state-name,Values=pending,running,shutting-down,stopping,stopped \
	  --query 'Reservations[].Instances[].{id:InstanceId,state:State.Name,type:InstanceType,role:Tags[?Key==`Role`]|[0].Value,ip:PublicIpAddress}' \
	  --output table
	@( . scripts/remote-lib.sh; rig_init >/dev/null 2>&1 && \
	  for h in $$SERVER_PUB $$LOADER_PUB; do echo "$$h: TTL $$(( $$(remaining_ttl_s $$h) / 60 )) min left"; done ) || true

bench:
	@scripts/bench-ab.sh

bench_fleet:
	@scripts/bench-fleet.sh

bench_frameworks:
	@scripts/bench-frameworks.sh

# LEG selects the binary (base or pr); REF stays the git-ref knob of up/provision.
perf:
	@scripts/perf.sh

# Bench the local, possibly uncommitted ../core working tree as the pr leg.
sync:
	@set -e; . scripts/remote-lib.sh; rig_init; \
	ttl_ensure 1800 $$SERVER_PUB; \
	stage_tree ../core $$SERVER_PUB core-sync; \
	rssh $$SERVER_PUB "PLAIN=$(PLAIN) bash bench-rig/scripts/build-local.sh"

extend:
	@set -e; . scripts/remote-lib.sh; rig_init; \
	arm_ttl_all $(TTL) $$SERVER_PUB $$LOADER_PUB; \
	echo "TTL set to $(TTL) minutes on both boxes"

report:
	@d=$$(ls -d results/*-ab results/*-fleet 2>/dev/null | sort | tail -1); \
	test -n "$$d" || { echo "ERROR: no A/B or fleet run in results/"; exit 1; }; \
	python3 scripts/report.py "$$d"

down: preflight
	@$(TF) destroy -auto-approve -input=false || \
	  { echo "retrying: placement group deletion can lag instance termination"; sleep 30; $(TF) destroy -auto-approve -input=false; }
	@rm -f .ssh-known-hosts .ssh-cm-*

# Tag-scoped aws-cli teardown for lost tfstate or after the TTL fired.
# Recovery order after state loss: make nuke, then make up.
nuke: preflight
	@PROFILE=$(PROFILE) REGION=$(REGION) scripts/nuke.sh
	@rm -f .ssh-known-hosts .ssh-cm-*
