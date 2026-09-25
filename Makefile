# AWS bench rig for rapira. This file holds the local tools. The rig targets come with the ladder driver.

SHELL := /bin/bash
BUF_VERSION ?= v1.73.0
BUF ?= go run github.com/bufbuild/buf/cmd/buf@$(BUF_VERSION)

.PHONY: test lock grpc_fixtures

test:
	python3 -m unittest discover -s tests -t .

# Local only: needs PHP 8.5 and composer. Commit apps/symfony and apps/laravel composer.json and composer.lock after a run.
lock:
	box/lock-apps.sh

# Local only: needs Go and network access to the buf remote plugins.
grpc_fixtures:
	$(BUF) build apps/grpc --as-file-descriptor-set -o apps/grpc/bench.binpb
	$(BUF) generate apps/grpc --template apps/grpc/buf.gen.yaml
	python3 apps/grpc/fixtures.py
