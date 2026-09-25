# AWS bench rig for rapira. See README.md for the flow and the knobs.

SHELL := /bin/bash
.NOTPARALLEL:

REGION ?= eu-central-1
SERVER_TYPE ?= c7a.8xlarge
LOADER_TYPE ?= c7a.xlarge
LOADER_COUNT ?= 4
AZ ?= eu-central-1a
TTL ?= 60
REF ?=
BASE_REF ?= main
NIGHTLY ?=
SUITE ?= ci
ROUNDS ?=
PROCESSES ?=
AMI ?=
TF_BACKEND ?= local
# 1 builds rapira with frame pointers for a perf session. Server builds only.
FRAME_POINTERS ?= 0
RUN ?=
A ?=
B ?=
BUF_VERSION ?= v1.73.0
BUF ?= go run github.com/bufbuild/buf/cmd/buf@$(BUF_VERSION)

TF := terraform -chdir=terraform
AWSC := aws --region $(REGION)

.PHONY: preflight up provision status bench report compare extend sync lock down nuke test grpc_fixtures

preflight:
	@$(AWSC) sts get-caller-identity >/dev/null 2>&1 || \
	  { echo "ERROR: AWS auth failed; run: aws sso login"; exit 1; }

# The chosen knobs persist in rig.auto.tfvars so every later terraform
# operation (provision, status, down) sees the applied values.
up: preflight
	@test -n "$(NIGHTLY)$(REF)" || { echo "ERROR: set NIGHTLY=<sha7> or REF=<ref>, for example: make up REF=pr/97"; exit 1; }
	@quota=$$($(AWSC) service-quotas get-service-quota --service-code ec2 --quota-code L-1216C47A --query Quota.Value --output text 2>/dev/null); \
	test -n "$$quota" || { echo "ERROR: could not read quota L-1216C47A; check the AWS permissions"; exit 1; }; \
	sv=$$($(AWSC) ec2 describe-instance-types --instance-types $(SERVER_TYPE) --query 'InstanceTypes[0].VCpuInfo.DefaultVCpus' --output text 2>/dev/null); \
	test -n "$$sv" || { echo "ERROR: unknown instance type $(SERVER_TYPE)"; exit 1; }; \
	lv=$$($(AWSC) ec2 describe-instance-types --instance-types $(LOADER_TYPE) --query 'InstanceTypes[0].VCpuInfo.DefaultVCpus' --output text 2>/dev/null); \
	test -n "$$lv" || { echo "ERROR: unknown instance type $(LOADER_TYPE)"; exit 1; }; \
	awk -v q=$$quota -v n=$$((sv + lv * $(LOADER_COUNT))) 'BEGIN { if (n > q) { printf "ERROR: the rig needs %d vCPUs, the account quota is %d (adjustable: quota L-1216C47A)\n", n, q; exit 1 } }'
	@ip=$$(curl -fs https://checkip.amazonaws.com) || \
	  { echo "ERROR: could not determine the operator IP"; exit 1; }; \
	{ echo "region = \"$(REGION)\""; \
	  echo "az = \"$(AZ)\""; \
	  echo "server_instance_type = \"$(SERVER_TYPE)\""; \
	  echo "loader_instance_type = \"$(LOADER_TYPE)\""; \
	  echo "loader_count = $(LOADER_COUNT)"; \
	  echo "ssh_cidr = \"$$ip/32\""; \
	  $(if $(AMI),echo "ami_id = \"$(AMI)\"";) } > terraform/rig.auto.tfvars
	@if [ "$(TF_BACKEND)" = s3 ]; then cp terraform/backend.tf.s3 terraform/backend.tf; else rm -f terraform/backend.tf; fi
	$(TF) init -input=false
	$(TF) apply -auto-approve -input=false || \
	  { echo "ERROR: apply failed; a partial rig may be billing, run 'make down'. On InsufficientInstanceCapacity retry with AZ=eu-central-1b or 1c."; exit 1; }
	@$(MAKE) --no-print-directory provision

provision:
	@needs=$$(python3 -m rig needs --suite $(SUITE)) && \
	python3 -m rig provision --ttl $(TTL) --needs "$$needs" --nightly "$(NIGHTLY)" --ref "$(REF)" --base-ref "$(BASE_REF)" --frame-pointers "$(FRAME_POINTERS)"

status: preflight
	@out=$$($(TF) output 2>/dev/null); \
	if [ -n "$$out" ]; then echo "$$out"; else echo "(no terraform outputs; rig not applied)"; fi
	@$(AWSC) ec2 describe-instances --filters Name=tag:Project,Values=rapira-bench \
	  Name=instance-state-name,Values=pending,running,shutting-down,stopping,stopped \
	  --query 'Reservations[].Instances[].{id:InstanceId,state:State.Name,type:InstanceType,role:Tags[?Key==`Role`]|[0].Value,ip:PublicIpAddress}' \
	  --output table
	@python3 -m rig ttl 2>/dev/null || true

bench:
	@python3 -m rig bench --suite $(SUITE) $(if $(ROUNDS),--rounds $(ROUNDS)) $(if $(PROCESSES),--processes $(PROCESSES))

# RUN selects a run directory; the default is the newest one under runs/.
report:
	@d="$(RUN)"; [ -n "$$d" ] || d=$$(ls -d runs/*/ 2>/dev/null | sort | tail -1); \
	test -n "$$d" || { echo "ERROR: no run in runs/"; exit 1; }; \
	python3 -m rig report "$${d%/}/run.json"

compare:
	@test -n "$(A)" && test -n "$(B)" || { echo "ERROR: set A=runs/<id> and B=runs/<id>"; exit 1; }
	@python3 -m rig compare "$(A)/run.json" "$(B)/run.json"

extend:
	@python3 -m rig ttl --set $(TTL)

# Build the local, possibly uncommitted ../core working tree on the server.
sync:
	@python3 -m rig sync --src ../core

lock:
	box/lock-apps.sh

down: preflight
	@$(TF) destroy -auto-approve -input=false || \
	  { echo "retrying: placement group deletion can lag instance termination"; sleep 30; $(TF) destroy -auto-approve -input=false; }
	@rm -f .ssh-known-hosts .ssh-cm-*

# Tag-scoped aws-cli teardown for lost tfstate or after the TTL fired.
# Recovery order after state loss: make nuke, then make up.
nuke: preflight
	@REGION=$(REGION) terraform/nuke.sh
	@rm -f .ssh-known-hosts .ssh-cm-*

test:
	python3 -m unittest discover -s tests -t .

# Local only: needs Go and network access to the buf remote plugins.
grpc_fixtures:
	$(BUF) build apps/grpc --as-file-descriptor-set -o apps/grpc/bench.binpb
	$(BUF) generate apps/grpc --template apps/grpc/buf.gen.yaml
	python3 apps/grpc/fixtures.py
