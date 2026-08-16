# Benchmark harness — runs each server against bench.js, exports one k6 summary
# JSON per server into results/, then renders a comparison table.
# See INSTRUCTIONS.md for the manual flow and the gotchas this automates.
#
#   make bench                       # franken → fpm → roadrunner → swoole → table
#   make bench-all                   # rapira → rapira-worker → rapira-classic → franken → fpm → roadrunner → swoole → table
#   make bench-rapira                # a single server (rapira = dispatcher mode, blocking receive loop)
#   make bench-rapira-async          # dispatcher mode, fiber per request (scaffolding cost — see the script)
#   make bench-rapira-worker         # rapira worker mode (resident handler closure)
#   make bench-rapira-classic        # rapira classic mode on the php-fpm scripts (apples-to-apples vs fpm)
#   make bench BENCH=scenario        # scenario workload (scenario.js + app/ mini-app workers)
#   make bench-wrk-all               # all five servers under wrk (high-concurrency ceilings)
#   make bench-wrk-fpm               # a single server under wrk
#   make report                      # re-print the table(s) from existing results/
#   make bench VUS=1000 DURATION=15s CHECKS=0    # override bench.js defaults
#   make bench-wrk-all WRK_THREADS=12 WRK_CONNS=5000 WRK_DURATION=15s   # override wrk defaults
#   make clean                       # drop results/
#
# Remote bench — the load generator runs on a SECOND box, which removes the
# generator-vs-server CPU contention that caps the fast servers on a single box
# (see INSTRUCTIONS.md "The wrk bench"). Two steps, two machines:
#
#   make start_all                   # on the BENCH box: all eight legs at once, one per port
#                                    #   8081 rapira (sync)     8085 roadrunner
#                                    #   8082 rapira-worker     8086 swoole
#                                    #   8083 rapira-classic    8087 rapira-async
#                                    #   8084 franken           8088 fpm (nginx front)
#   make stop_all                    # on the BENCH box: stop the fleet
#   make remote_bench_all_k6         # on the LOAD box: k6 walks the fleet → "(remote)" tables
#   make remote_bench_all_wrk        # on the LOAD box: same under wrk
#   make start_all BENCH=scenario && make remote_bench_all_k6 BENCH=scenario
#   make remote_bench_all_wrk REMOTE_HOST=10.1.0.25 WRK_CONNS=5000
#
# One server at a time — the bench-* targets all bind :8080, so they are strictly serial.
# start_all is the deliberate exception: each server gets its own port so they coexist,
# and the remote targets still bench them ONE AT A TIME (concurrent legs would fight for
# CPU and every number would be garbage). Remote results are written to a separate
# `.remote` file set, so they never overwrite the local reference rows.

.NOTPARALLEL:
SHELL := /bin/bash

# Empty by default so bench.js's own defaults apply; set on the command line to override.
VUS ?=
DURATION ?=
CHECKS ?=
K6_ENV := $(if $(VUS),-e VUS=$(VUS)) $(if $(DURATION),-e DURATION=$(DURATION)) $(if $(CHECKS),-e CHECKS=$(CHECKS))
K6_STATS := avg,min,med,max,p(90),p(95),p(99)

# wrk knobs (bench-wrk-*): a C generator that sustains high connection counts k6
# can't (see INSTRUCTIONS.md "The wrk bench"). 12 of the box's 32 hardware threads,
# so the generator and the server don't fight over every core. Kept at 12 across the
# worker bump to 32 so wrk numbers stay comparable to earlier runs.
WRK_THREADS ?= 12
WRK_CONNS ?= 500
WRK_DURATION ?= 15s
WRK_URL ?= http://127.0.0.1:8080/?name=you

RESULTS := results

# Fleet ports (start_all / remote_bench_all_*): PORT_BASE+1 … PORT_BASE+8, one per
# server, so all eight coexist and a remote generator can walk them without a start/stop
# cycle per leg. PORT_BASE itself is left free; at the default it is :8080, the port
# every serial bench-* target binds.
#
# Distinct ports are NOT enough isolation, and the guards enforce that: the two flows
# share the results/<name>.pid and .server.log namespace, and the reap_* patterns match
# on cmdline, not port — reap_rapira's `pkill -f '[r]apira/rapira serve'` kills every
# rapira on the box. So a serial run finishing while a fleet is up would clobber the
# fleet's pidfiles and SIGKILL its servers mid-benchmark. port_guard therefore refuses
# while a fleet is up, and fleet_port_guard refuses while PORT_BASE is busy.
PORT_BASE ?= 8080
PORT_RAPIRA         := $(shell expr $(PORT_BASE) + 1)
PORT_RAPIRA_WORKER  := $(shell expr $(PORT_BASE) + 2)
PORT_RAPIRA_CLASSIC := $(shell expr $(PORT_BASE) + 3)
PORT_FRANKEN        := $(shell expr $(PORT_BASE) + 4)
PORT_ROADRUNNER     := $(shell expr $(PORT_BASE) + 5)
PORT_SWOOLE         := $(shell expr $(PORT_BASE) + 6)
PORT_RAPIRA_ASYNC   := $(shell expr $(PORT_BASE) + 7)
PORT_FPM            := $(shell expr $(PORT_BASE) + 8)

# name:port pairs — the fleet/remote targets walk this list in order.
FLEET := rapira:$(PORT_RAPIRA) rapira-worker:$(PORT_RAPIRA_WORKER) \
         rapira-classic:$(PORT_RAPIRA_CLASSIC) franken:$(PORT_FRANKEN) \
         roadrunner:$(PORT_ROADRUNNER) swoole:$(PORT_SWOOLE) \
         rapira-async:$(PORT_RAPIRA_ASYNC) fpm:$(PORT_FPM)

# The bench box, as seen from the load box. Results from a remote run are suffixed
# so they never clobber the local reference rows.
REMOTE_HOST ?= 10.1.0.25
RSUF := .remote

# php-fpm stack: the distro php-fpm (split from the SAME package build as the CLI `php`
# that swoole and roadrunner run) + nginx front; ~/.local/nginx is the no-sudo fallback.
# PHP-build parity: fpm/swoole/roadrunner AND rapira all run the distro PHP — rapira embeds
# its libphp (php-embedded) through the embed SAPI, so nothing is pinned here: ld.so resolves
# libphp from the default search path. Only FrankenPHP differs (statically linked PHP).
PHP_FPM_BIN ?= $(shell command -v php-fpm || echo /usr/bin/php-fpm)
NGINX_BIN ?= $(shell command -v nginx || echo $(HOME)/.local/nginx/sbin/nginx)

# BENCH selects the workload: hello (bench.js, the original ceiling probe) or
# scenario (scenario.js + the app/app.php mini-app workers). In hello mode every
# derived value matches the historical behavior, so reference numbers stay valid.
BENCH ?= hello
ifeq ($(BENCH),scenario)
  K6_SCRIPT      := scenario.js
  SUF            := .scenario
  RAPIRA_SCRIPT  := rapira/scenario-dispatcher.php
  RAPIRA_ASYNC_SCRIPT := rapira/scenario-async-dispatcher.php
  RAPIRA_WORKER_SCRIPT := rapira/scenario-worker.php
  RAPIRA_CLASSIC_SCRIPT := fpm/scenario.php
  FRANKEN_CONFIG := Caddyfile.scenario
  FPM_NGINX_CONF := nginx.scenario.conf
  RR_CONFIG      := .rr.scenario.yaml
  SWOOLE_SCRIPT  := scenario-server.php
  # scenario.js takes BASE (origin only); $$p is the fleet loop's port variable, so the
  # quoting breaks around it (the URL carries glob characters — keep it quoted).
  REMOTE_K6_URL   = -e 'BASE=http://$(REMOTE_HOST):'$$p
else
  K6_SCRIPT      := bench.js
  SUF            :=
  RAPIRA_SCRIPT  := rapira/dispatcher.php
  RAPIRA_ASYNC_SCRIPT := rapira/async-dispatcher.php
  RAPIRA_WORKER_SCRIPT := rapira/worker.php
  RAPIRA_CLASSIC_SCRIPT := fpm/hello.php
  FRANKEN_CONFIG := Caddyfile
  FPM_NGINX_CONF := nginx.conf
  RR_CONFIG      := .rr.yaml
  SWOOLE_SCRIPT  := server.php
  # bench.js takes TARGET (a full URL, path and query included).
  REMOTE_K6_URL   = -e 'TARGET=http://$(REMOTE_HOST):'$$p'/?name=you'
endif

# rapira runs as a local binary in rapira/, like the other SAPIs' artifacts;
# it is refreshed from the cargo build output whenever that exists.
RAPIRA_SRC_BIN := ../core/target/release/rapira
RAPIRA_BIN := rapira/rapira

.PHONY: bench bench-all bench-rapira bench-rapira-async bench-rapira-worker bench-rapira-classic bench-franken bench-fpm bench-roadrunner bench-swoole \
        bench-wrk bench-wrk-all bench-wrk-rapira bench-wrk-rapira-async bench-wrk-rapira-worker bench-wrk-rapira-classic bench-wrk-franken bench-wrk-fpm \
        bench-wrk-roadrunner bench-wrk-swoole report clean \
        start_all stop_all remote_bench_all_k6 remote_bench_all_wrk

bench: bench-franken bench-fpm bench-roadrunner bench-swoole report

bench-all: bench-rapira bench-rapira-async bench-rapira-worker bench-rapira-classic bench-franken bench-fpm bench-roadrunner bench-swoole report

bench-wrk: bench-wrk-franken bench-wrk-fpm bench-wrk-roadrunner bench-wrk-swoole report

bench-wrk-all: bench-wrk-rapira bench-wrk-rapira-async bench-wrk-rapira-worker bench-wrk-rapira-classic bench-wrk-franken bench-wrk-fpm bench-wrk-roadrunner bench-wrk-swoole report

$(RESULTS):
	@mkdir -p $(RESULTS)

# Shared steps, $(call ...)-ed from each server target.
# port_guard: refuse to start on a busy :8080 (a stale server here once produced a ghost benchmark).
define port_guard
	if ss -ltn | grep -q ':$(or $(1),8080) '; then echo "ERROR: :$(or $(1),8080) already in use — stop that server first"; exit 1; fi; \
	up=; for e in $(FLEET); do p=$${e##*:}; if ss -ltn | grep -q ":$$p "; then up="$$up $$p"; fi; done; \
	if [ -n "$$up" ]; then \
	  echo "ERROR: a start_all fleet is up on$$up — run 'make stop_all' before any serial bench-* run"; \
	  exit 1; \
	fi
endef
# fleet_port_guard: the start_all counterpart — refuse if a serial run holds PORT_BASE, then
# report EVERY busy fleet port in one pass rather than failing on the first, so a half-up
# fleet is diagnosable at a glance.
define fleet_port_guard
	if ss -ltn | grep -q ':$(PORT_BASE) '; then \
	  echo "ERROR: :$(PORT_BASE) is in use — a serial bench-* run is active; let it finish first"; exit 1; \
	fi; \
	busy=; \
	for e in $(FLEET); do \
	  p=$${e##*:}; \
	  if ss -ltn | grep -q ":$$p "; then busy="$$busy $$p"; fi; \
	done; \
	if [ -n "$$busy" ]; then echo "ERROR: port(s)$$busy already in use — run 'make stop_all' first"; exit 1; fi
endef
# wait_ready(1=name, 2=port [8080]): poll until the server answers, else dump its log and kill it.
define wait_ready
	ok=0; for i in $$(seq 1 60); do \
	  curl -sf -o /dev/null -m1 'http://127.0.0.1:$(or $(2),8080)/?name=you' && { ok=1; break; }; sleep 0.5; \
	done; \
	if [ $$ok -ne 1 ]; then \
	  echo "ERROR: $(1) never became ready; last log lines:"; tail -8 $(RESULTS)/$(1)$(SUF).server.log; \
	  kill -KILL $$(cat $(RESULTS)/$(1)$(SUF).pid) 2>/dev/null; rm -f $(RESULTS)/$(1)$(SUF).pid; exit 1; \
	fi
endef
# run_k6(1=name): raise fd limit (10k VUs need >10k sockets), export the summary.
# k6 exits non-zero when a threshold fails — the summary is still written, so keep going.
define run_k6
	ulimit -n 65536 2>/dev/null; \
	k6 run $(K6_ENV) --summary-trend-stats "$(K6_STATS)" \
	  --summary-export $(RESULTS)/$(1)$(SUF).summary.json $(K6_SCRIPT) 2>&1 | tee $(RESULTS)/$(1)$(SUF).k6.log \
	  || echo "(k6 exited non-zero — a threshold likely failed; summary still exported)"
endef
# run_wrk(1=name): the high-concurrency generator; hits the GET route only (any
# GET greets in both workloads). Keep going on failure so stop_server still runs.
define run_wrk
	ulimit -n 65536 2>/dev/null; \
	wrk -t$(WRK_THREADS) -c$(WRK_CONNS) -d$(WRK_DURATION) --latency '$(WRK_URL)' \
	  | tee $(RESULTS)/$(1)$(SUF).wrk.txt \
	  || echo "(wrk exited non-zero — see $(RESULTS)/$(1)$(SUF).wrk.txt)"
endef
# stop_server(1=name, 2=signal, 3=port [8080]): signal the pidfile, wait for the port to
# free, force-kill if held.
define stop_server
	kill -$(2) $$(cat $(RESULTS)/$(1)$(SUF).pid) 2>/dev/null || true; \
	for i in $$(seq 1 30); do ss -ltn | grep -q ':$(or $(3),8080) ' || break; sleep 0.5; done; \
	if ss -ltn | grep -q ':$(or $(3),8080) '; then \
	  echo "WARN: $(1) still holds :$(or $(3),8080) — force-killing"; \
	  kill -KILL $$(cat $(RESULTS)/$(1)$(SUF).pid) 2>/dev/null; sleep 1; \
	fi; \
	rm -f $(RESULTS)/$(1)$(SUF).pid
endef

# start_<name>(1=port [8080]): artifact preflight + launch + pidfile. Shared by the k6
# (bench-*), wrk (bench-wrk-*) and fleet (start_all) target families so the launch logic
# lives in one place. The port argument is what lets start_all run them side by side; the
# serial targets pass nothing and keep binding :8080, which is also each config's own
# built-in default (franken/Caddyfile, swoole/server.php), so their command lines are
# unchanged from before the fleet existed.
define start_rapira
	if [ -x $(RAPIRA_SRC_BIN) ]; then cp -f $(RAPIRA_SRC_BIN) $(RAPIRA_BIN); fi; \
	test -x $(RAPIRA_BIN) || { echo "ERROR: $(RAPIRA_BIN) missing — build it first:"; \
	  echo "  cd ../core && cargo build --release"; exit 1; }; \
	echo "==> rapira: starting on :$(or $(1),8080) (32 worker processes, dispatcher + fibers)"; \
	./$(RAPIRA_BIN) serve --mode dispatcher --processes 32 --listen :$(or $(1),8080) \
	  $(RAPIRA_SCRIPT) > $(RESULTS)/rapira$(SUF).server.log 2>&1 & \
	echo $$! > $(RESULTS)/rapira$(SUF).pid
endef
# rapira in worker mode: the resident handler-closure loop (\Rapira\handle_request)
# over per-request superglobals.
define start_rapira_worker
	if [ -x $(RAPIRA_SRC_BIN) ]; then cp -f $(RAPIRA_SRC_BIN) $(RAPIRA_BIN); fi; \
	test -x $(RAPIRA_BIN) || { echo "ERROR: $(RAPIRA_BIN) missing — build it first:"; \
	  echo "  cd ../core && cargo build --release"; exit 1; }; \
	echo "==> rapira-worker: starting on :$(or $(1),8080) (32 worker processes, handler closure)"; \
	./$(RAPIRA_BIN) serve --mode worker --processes 32 --listen :$(or $(1),8080) \
	  $(RAPIRA_WORKER_SCRIPT) > $(RESULTS)/rapira-worker$(SUF).server.log 2>&1 & \
	echo $$! > $(RESULTS)/rapira-worker$(SUF).pid
endef
# rapira in dispatcher mode, async flavour: core's examples/dispatcher-async.php shape —
# a fiber per request, tryReceive() between resumes, blocking receive() when idle. Note the
# bench handlers never suspend, so this measures the fiber-per-request scaffolding cost, not
# request overlap — see the header comment in rapira/async-dispatcher.php.
define start_rapira_async
	if [ -x $(RAPIRA_SRC_BIN) ]; then cp -f $(RAPIRA_SRC_BIN) $(RAPIRA_BIN); fi; \
	test -x $(RAPIRA_BIN) || { echo "ERROR: $(RAPIRA_BIN) missing — build it first:"; \
	  echo "  cd ../core && cargo build --release"; exit 1; }; \
	echo "==> rapira-async: starting on :$(or $(1),8080) (32 worker processes, fiber per request)"; \
	./$(RAPIRA_BIN) serve --mode dispatcher --processes 32 --listen :$(or $(1),8080) \
	  $(RAPIRA_ASYNC_SCRIPT) > $(RESULTS)/rapira-async$(SUF).server.log 2>&1 & \
	echo $$! > $(RESULTS)/rapira-async$(SUF).pid
endef
# rapira in classic mode: per-request script execution (no resident worker) on
# the SAME fpm/ scripts php-fpm serves — apples-to-apples with the fpm stack,
# differing only in the front (pingora vs nginx+fastcgi).
define start_rapira_classic
	if [ -x $(RAPIRA_SRC_BIN) ]; then cp -f $(RAPIRA_SRC_BIN) $(RAPIRA_BIN); fi; \
	test -x $(RAPIRA_BIN) || { echo "ERROR: $(RAPIRA_BIN) missing — build it first:"; \
	  echo "  cd ../core && cargo build --release"; exit 1; }; \
	echo "==> rapira-classic: starting on :$(or $(1),8080) (32 worker processes, per-request script)"; \
	./$(RAPIRA_BIN) serve --mode classic --processes 32 --listen :$(or $(1),8080) \
	  $(RAPIRA_CLASSIC_SCRIPT) > $(RESULTS)/rapira-classic$(SUF).server.log 2>&1 & \
	echo $$! > $(RESULTS)/rapira-classic$(SUF).pid
endef
define start_franken
	test -x franken/frankenphp -a -f franken/$(FRANKEN_CONFIG) || { echo "ERROR: franken/frankenphp or franken/$(FRANKEN_CONFIG) missing — fetch/check it (see INSTRUCTIONS.md §2)"; exit 1; }; \
	echo "==> frankenphp: starting on :$(or $(1),8080) (32 workers)"; \
	cd franken && { FRANKEN_PORT=$(or $(1),8080) ./frankenphp run --config $(FRANKEN_CONFIG) > ../$(RESULTS)/franken$(SUF).server.log 2>&1 & \
	  echo $$! > ../$(RESULTS)/franken$(SUF).pid; }
endef
# fpm is the one two-process stack here: an nginx master (+ workers) fronting a
# php-fpm master (+ 32 children). nginx's pid goes to the standard pidfile (it
# holds :8080, which the wait/stop logic keys on); fpm's master gets its own.
# fpm is the one leg whose port cannot be parameterized: nginx has no env-var substitution
# and `listen` takes no variables, unlike Caddy ({$FRANKEN_PORT:8080}), Swoole (getenv),
# rapira (--listen) and rr (-o http.address=). So a non-default port is RENDERED into a
# generated sibling config; the checked-in nginx.conf is never touched and the serial path
# keeps using it verbatim. php-fpm itself always listens on 127.0.0.1:9000 (one pool, no
# per-port copy), so only the nginx front moves.
define start_fpm
	test -x "$(PHP_FPM_BIN)" || { echo "ERROR: php-fpm missing — install it (see INSTRUCTIONS.md §3)"; exit 1; }; \
	test -x "$(NGINX_BIN)" || { echo "ERROR: nginx missing — install it (see INSTRUCTIONS.md §3)"; exit 1; }; \
	conf=$(FPM_NGINX_CONF); \
	if [ -n "$(1)" ] && [ "$(1)" != 8080 ]; then \
	  conf=nginx.$(1).generated.conf; \
	  sed 's/listen 8080/listen $(1)/' fpm/$(FPM_NGINX_CONF) > fpm/$$conf; \
	  grep -q "listen $(1)" fpm/$$conf || { echo "ERROR: could not render listen :$(1) into fpm/$$conf"; exit 1; }; \
	fi; \
	echo "==> php-fpm: starting on :$(or $(1),8080) (32 static workers, nginx front)"; \
	mkdir -p fpm/run fpm/tmp; \
	cd fpm && { $(PHP_FPM_BIN) -F -p $$PWD -y php-fpm.conf > ../$(RESULTS)/fpm$(SUF).fpm.log 2>&1 & \
	  echo $$! > ../$(RESULTS)/fpm$(SUF).fpm.pid; } && \
	{ $(NGINX_BIN) -p $$PWD -e stderr -c $$conf -g 'daemon off;' > ../$(RESULTS)/fpm$(SUF).server.log 2>&1 & \
	  echo $$! > ../$(RESULTS)/fpm$(SUF).pid; }
endef
define start_roadrunner
	test -x roadrunner/rr -a -d roadrunner/vendor || { echo "ERROR: roadrunner/rr or roadrunner/vendor missing — fetch them (see INSTRUCTIONS.md §4)"; exit 1; }; \
	echo "==> roadrunner: starting on :$(or $(1),8080) (32 workers)"; \
	cd roadrunner && { ./rr serve -c $(RR_CONFIG) -o http.address=0.0.0.0:$(or $(1),8080) > ../$(RESULTS)/roadrunner$(SUF).server.log 2>&1 & \
	  echo $$! > ../$(RESULTS)/roadrunner$(SUF).pid; }
endef
define start_swoole
	test -f swoole/swoole.so || { echo "ERROR: swoole/swoole.so missing — build it (see INSTRUCTIONS.md §5)"; exit 1; }; \
	echo "==> swoole: starting on :$(or $(1),8080) (32 workers)"; \
	cd swoole && { SWOOLE_PORT=$(or $(1),8080) php -d extension=$$PWD/swoole.so $(SWOOLE_SCRIPT) > ../$(RESULTS)/swoole$(SUF).server.log 2>&1 & \
	  echo $$! > ../$(RESULTS)/swoole$(SUF).pid; }
endef
# reap_<name>: the rapira/fpm/roadrunner/swoole masters fork/spawn workers; reap
# any stragglers after stop (bracketed chars avoid the pattern matching itself;
# each pattern is anchored to something unique to OUR launch cmdline so it can
# never collateral-kill an unrelated process). rapira workers share the master's
# cmdline ('./rapira/rapira serve …' — PR_SET_NAME renames comm to rapira-worker
# but not /proc/pid/cmdline), so the one -f pattern reaps master and workers alike.
define reap_rapira
	pkill -KILL -f '[r]apira/rapira serve' 2>/dev/null || true
endef
# stop_fpm: QUIT (graceful) to both masters. Force path: kill nginx workers via
# -P (parent pid) BEFORE the master — orphaned nginx workers keep serving :8080
# and their cmdline is the unanchorable 'nginx: worker process'.
define stop_fpm
	kill -QUIT $$(cat $(RESULTS)/fpm$(SUF).pid 2>/dev/null) 2>/dev/null || true; \
	kill -QUIT $$(cat $(RESULTS)/fpm$(SUF).fpm.pid 2>/dev/null) 2>/dev/null || true; \
	for i in $$(seq 1 30); do ss -ltn | grep -q ':$(or $(1),8080) ' || break; sleep 0.5; done; \
	if ss -ltn | grep -q ':$(or $(1),8080) '; then \
	  echo "WARN: fpm still holds :$(or $(1),8080) — force-killing"; \
	  pkill -KILL -P $$(cat $(RESULTS)/fpm$(SUF).pid 2>/dev/null) 2>/dev/null; \
	  kill -KILL $$(cat $(RESULTS)/fpm$(SUF).pid 2>/dev/null) 2>/dev/null; sleep 1; \
	fi; \
	rm -f $(RESULTS)/fpm$(SUF).pid $(RESULTS)/fpm$(SUF).fpm.pid
endef
define reap_fpm
	pkill -KILL -f '[f]pm/php-fpm.conf' 2>/dev/null || true; \
	pkill -KILL -f '[p]hp-fpm: pool bench' 2>/dev/null || true; \
	pkill -KILL -f '[n]ginx: master process.*benchmarks/fpm' 2>/dev/null || true
endef
define reap_roadrunner
	pkill -KILL -f 'php ([s]cenario-)?[w]orker.php' 2>/dev/null || true
endef
define reap_swoole
	pkill -KILL -f '[s]woole\.so ([s]cenario-)?server\.php' 2>/dev/null || true
endef
# remote_guard: refuse to bench a fleet that isn't up. Without it every leg still
# "runs" and writes a summary full of zeros, and the only symptom is a k6 threshold
# error — a ghost benchmark of exactly the kind the port guard exists to prevent.
# Checks the whole fleet in one pass so one message names every server that is down.
define remote_guard
	fail=; \
	for e in $(FLEET); do \
	  s=$${e%%:*}; p=$${e##*:}; \
	  curl -sf -o /dev/null -m2 'http://$(REMOTE_HOST):'$$p'/?name=you' || fail="$$fail $$s(:$$p)"; \
	done; \
	if [ -n "$$fail" ]; then \
	  echo "ERROR: no answer from $(REMOTE_HOST) —$$fail"; \
	  echo "  start the fleet on the bench box first:  make start_all$(if $(filter scenario,$(BENCH)), BENCH=scenario)"; \
	  exit 1; \
	fi
endef

bench-rapira: | $(RESULTS)
	@$(call port_guard)
	@$(call start_rapira)
	@$(call wait_ready,rapira)
	@$(call run_k6,rapira)
	@$(call stop_server,rapira,INT)
	@$(call reap_rapira)
	@echo "==> rapira: done"

bench-rapira-async: | $(RESULTS)
	@$(call port_guard)
	@$(call start_rapira_async)
	@$(call wait_ready,rapira-async)
	@$(call run_k6,rapira-async)
	@$(call stop_server,rapira-async,INT)
	@$(call reap_rapira)
	@echo "==> rapira-async: done"

bench-rapira-worker: | $(RESULTS)
	@$(call port_guard)
	@$(call start_rapira_worker)
	@$(call wait_ready,rapira-worker)
	@$(call run_k6,rapira-worker)
	@$(call stop_server,rapira-worker,INT)
	@$(call reap_rapira)
	@echo "==> rapira-worker: done"

bench-rapira-classic: | $(RESULTS)
	@$(call port_guard)
	@$(call start_rapira_classic)
	@$(call wait_ready,rapira-classic)
	@$(call run_k6,rapira-classic)
	@$(call stop_server,rapira-classic,INT)
	@$(call reap_rapira)
	@echo "==> rapira-classic: done"

bench-franken: | $(RESULTS)
	@$(call port_guard)
	@$(call start_franken)
	@$(call wait_ready,franken)
	@$(call run_k6,franken)
	@$(call stop_server,franken,TERM)
	@echo "==> frankenphp: done"

bench-fpm: | $(RESULTS)
	@$(call port_guard)
	@$(call start_fpm)
	@$(call wait_ready,fpm)
	@$(call run_k6,fpm)
	@$(call stop_fpm)
	@$(call reap_fpm)
	@echo "==> php-fpm: done"

bench-roadrunner: | $(RESULTS)
	@$(call port_guard)
	@$(call start_roadrunner)
	@$(call wait_ready,roadrunner)
	@$(call run_k6,roadrunner)
	@$(call stop_server,roadrunner,TERM)
	@$(call reap_roadrunner)
	@echo "==> roadrunner: done"

bench-swoole: | $(RESULTS)
	@$(call port_guard)
	@$(call start_swoole)
	@$(call wait_ready,swoole)
	@$(call run_k6,swoole)
	@$(call stop_server,swoole,TERM)
	@$(call reap_swoole)
	@echo "==> swoole: done"

bench-wrk-rapira: | $(RESULTS)
	@$(call port_guard)
	@$(call start_rapira)
	@$(call wait_ready,rapira)
	@$(call run_wrk,rapira)
	@$(call stop_server,rapira,INT)
	@$(call reap_rapira)
	@echo "==> rapira (wrk): done"

bench-wrk-rapira-async: | $(RESULTS)
	@$(call port_guard)
	@$(call start_rapira_async)
	@$(call wait_ready,rapira-async)
	@$(call run_wrk,rapira-async)
	@$(call stop_server,rapira-async,INT)
	@$(call reap_rapira)
	@echo "==> rapira-async (wrk): done"

bench-wrk-rapira-worker: | $(RESULTS)
	@$(call port_guard)
	@$(call start_rapira_worker)
	@$(call wait_ready,rapira-worker)
	@$(call run_wrk,rapira-worker)
	@$(call stop_server,rapira-worker,INT)
	@$(call reap_rapira)
	@echo "==> rapira-worker (wrk): done"

bench-wrk-rapira-classic: | $(RESULTS)
	@$(call port_guard)
	@$(call start_rapira_classic)
	@$(call wait_ready,rapira-classic)
	@$(call run_wrk,rapira-classic)
	@$(call stop_server,rapira-classic,INT)
	@$(call reap_rapira)
	@echo "==> rapira-classic (wrk): done"

bench-wrk-franken: | $(RESULTS)
	@$(call port_guard)
	@$(call start_franken)
	@$(call wait_ready,franken)
	@$(call run_wrk,franken)
	@$(call stop_server,franken,TERM)
	@echo "==> frankenphp (wrk): done"

bench-wrk-fpm: | $(RESULTS)
	@$(call port_guard)
	@$(call start_fpm)
	@$(call wait_ready,fpm)
	@$(call run_wrk,fpm)
	@$(call stop_fpm)
	@$(call reap_fpm)
	@echo "==> php-fpm (wrk): done"

bench-wrk-roadrunner: | $(RESULTS)
	@$(call port_guard)
	@$(call start_roadrunner)
	@$(call wait_ready,roadrunner)
	@$(call run_wrk,roadrunner)
	@$(call stop_server,roadrunner,TERM)
	@$(call reap_roadrunner)
	@echo "==> roadrunner (wrk): done"

bench-wrk-swoole: | $(RESULTS)
	@$(call port_guard)
	@$(call start_swoole)
	@$(call wait_ready,swoole)
	@$(call run_wrk,swoole)
	@$(call stop_server,swoole,TERM)
	@$(call reap_swoole)
	@echo "==> swoole (wrk): done"

# ---- Remote bench: fleet on this box, generator on another ----------------------
#
# start_all: every server at once, one per port, so a remote generator can walk them
# without a start/stop cycle per leg. Starts are serial with a readiness wait each, so
# a failure names the server that broke — but it leaves the already-started ones up:
# run `make stop_all` to clear a partial fleet before retrying.
start_all: | $(RESULTS)
	@$(call fleet_port_guard)
	@$(call start_rapira,$(PORT_RAPIRA))
	@$(call wait_ready,rapira,$(PORT_RAPIRA))
	@$(call start_rapira_worker,$(PORT_RAPIRA_WORKER))
	@$(call wait_ready,rapira-worker,$(PORT_RAPIRA_WORKER))
	@$(call start_rapira_classic,$(PORT_RAPIRA_CLASSIC))
	@$(call wait_ready,rapira-classic,$(PORT_RAPIRA_CLASSIC))
	@$(call start_franken,$(PORT_FRANKEN))
	@$(call wait_ready,franken,$(PORT_FRANKEN))
	@$(call start_roadrunner,$(PORT_ROADRUNNER))
	@$(call wait_ready,roadrunner,$(PORT_ROADRUNNER))
	@$(call start_swoole,$(PORT_SWOOLE))
	@$(call wait_ready,swoole,$(PORT_SWOOLE))
	@$(call start_rapira_async,$(PORT_RAPIRA_ASYNC))
	@$(call wait_ready,rapira-async,$(PORT_RAPIRA_ASYNC))
	@$(call start_fpm,$(PORT_FPM))
	@$(call wait_ready,fpm,$(PORT_FPM))
	@echo; echo "==> fleet up ($(BENCH) workload):"
	@for e in $(FLEET); do printf '      %-16s 0.0.0.0:%s\n' "$${e%%:*}" "$${e##*:}"; done
	@echo; echo "    from the load box:  make remote_bench_all_k6 REMOTE_HOST=<this box>$(if $(filter scenario,$(BENCH)), BENCH=scenario)"

# stop_all: stop everything start_all launched; safe against a partial fleet.
# Order matters — reap_rapira's pkill pattern matches ALL rapira instances, so it runs
# only after all three rapira legs have been signalled, never between them.
stop_all:
	@$(call stop_server,rapira,INT,$(PORT_RAPIRA))
	@$(call stop_server,rapira-async,INT,$(PORT_RAPIRA_ASYNC))
	@$(call stop_server,rapira-worker,INT,$(PORT_RAPIRA_WORKER))
	@$(call stop_server,rapira-classic,INT,$(PORT_RAPIRA_CLASSIC))
	@$(call reap_rapira)
	@$(call stop_server,franken,TERM,$(PORT_FRANKEN))
	@$(call stop_server,roadrunner,TERM,$(PORT_ROADRUNNER))
	@$(call reap_roadrunner)
	@$(call stop_server,swoole,TERM,$(PORT_SWOOLE))
	@$(call reap_swoole)
	@$(call stop_fpm,$(PORT_FPM))
	@$(call reap_fpm)
	@echo "==> fleet down"

# remote_bench_all_k6 / _wrk: run the generator HERE against the fleet on REMOTE_HOST,
# ONE LEG AT A TIME — the servers coexist, but benching them concurrently would have
# them fight for the bench box's cores and every number would be meaningless.
# Results go to the $(RSUF) file set, which `make report` renders as its own tables.
remote_bench_all_k6: | $(RESULTS)
	@$(call remote_guard)
	@for e in $(FLEET); do \
	  s=$${e%%:*}; p=$${e##*:}; \
	  echo; echo "==> $$s: k6 -> http://$(REMOTE_HOST):$$p"; \
	  ulimit -n 65536 2>/dev/null; \
	  k6 run $(K6_ENV) $(REMOTE_K6_URL) --summary-trend-stats "$(K6_STATS)" \
	    --summary-export $(RESULTS)/$$s$(SUF)$(RSUF).summary.json $(K6_SCRIPT) 2>&1 \
	    | tee $(RESULTS)/$$s$(SUF)$(RSUF).k6.log \
	    || echo "(k6 exited non-zero — a threshold likely failed; summary still exported)"; \
	done
	@$(MAKE) --no-print-directory report

remote_bench_all_wrk: | $(RESULTS)
	@$(call remote_guard)
	@for e in $(FLEET); do \
	  s=$${e%%:*}; p=$${e##*:}; \
	  echo; echo "==> $$s: wrk -> http://$(REMOTE_HOST):$$p"; \
	  ulimit -n 65536 2>/dev/null; \
	  wrk -t$(WRK_THREADS) -c$(WRK_CONNS) -d$(WRK_DURATION) --latency 'http://$(REMOTE_HOST):'$$p'/?name=you' \
	    | tee $(RESULTS)/$$s$(SUF)$(RSUF).wrk.txt \
	    || echo "(wrk exited non-zero — see $(RESULTS)/$$s$(SUF)$(RSUF).wrk.txt)"; \
	done
	@$(MAKE) --no-print-directory report

# Renders the tables from whatever summaries exist in results/ (rapira first if
# present): the hello table from <name>.summary.json, and a per-scenario table from
# <name>.scenario.summary.json when scenario runs exist. Each table is rendered twice,
# once per file set — the local one, then a "REMOTE" group from the <name>.remote.*
# files that remote_bench_all_* writes. A group is skipped entirely when it has no files,
# so on a box that has only ever run locally the output is unchanged.
# Format facts (verified against k6 v2.0.0 --summary-export):
#   http_reqs = {count, rate}; http_req_duration includes avg (a default trend stat);
#   http_req_failed is a Rate metric where `passes` counts FAILED requests, `value` is the rate;
#   tagged submetrics are keyed 'metric{scenario:name}' and exported only because
#   scenario.js declares thresholds on them.
define REPORT_PY
import json, os, re, time
SERVERS = ('rapira', 'rapira-async', 'rapira-worker', 'rapira-classic', 'franken', 'fpm', 'roadrunner', 'swoole')
SCEN = ('browse', 'echoJson', 'form', 'misc')
# '' = locally generated runs; '.remote' = runs driven from another box
# (make remote_bench_all_*). Rendered as two separate table groups on purpose: a
# loopback number and an over-the-wire number in one table would invite exactly the
# comparison that isn't valid. Column formats are identical, so a group is still
# readable against the other — just never row-by-row in the same block.
VARIANTS = (('', ''), ('.remote', 'REMOTE (load generator off-box)'))
def when(path):
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(path)))
def wrk_ms(tok):
    m = re.match(r'([0-9.]+)(us|ms|s|m)$$', tok or '')
    if not m:
        return None
    return float(m.group(1)) * {'us': 0.001, 'ms': 1.0, 's': 1000.0, 'm': 60000.0}[m.group(2)]
def collect(rsuf):
    rows, stamps = [], []
    for name in SERVERS:
        path = os.path.join('results', name + rsuf + '.summary.json')
        if not os.path.exists(path):
            continue
        with open(path) as f:
            m = json.load(f)['metrics']
        reqs, dur, failed = m.get('http_reqs', {}), m.get('http_req_duration', {}), m.get('http_req_failed', {})
        rows.append((name, int(reqs.get('count', 0)), reqs.get('rate', 0.0),
                     dur.get('avg'), int(failed.get('passes', 0)), failed.get('value', 0.0) * 100))
        stamps.append(name + ': ' + when(path))
    srows, sstamps = [], []
    for name in SERVERS:
        path = os.path.join('results', name + '.scenario' + rsuf + '.summary.json')
        if not os.path.exists(path):
            continue
        with open(path) as f:
            m = json.load(f)['metrics']
        def cell(rk, dk, fk, m=m):
            r, d, x = m.get(rk, {}), m.get(dk, {}), m.get(fk, {})
            return (int(r.get('count', 0)), r.get('rate', 0.0), d.get('avg'), x.get('value', 0.0) * 100)
        srows.append((name, '(total)') + cell('http_reqs', 'http_req_duration', 'http_req_failed'))
        for s in SCEN:
            srows.append(('', s) + cell('http_reqs{scenario:%s}' % s,
                                        'http_req_duration{scenario:%s}' % s,
                                        'http_req_failed{scenario:%s}' % s))
        sstamps.append(name + ': ' + when(path))
    wrows, wstamps = [], []
    for name in SERVERS:
        for suf, label in (('', name), ('.scenario', name + ' (scn)')):
            path = os.path.join('results', name + suf + rsuf + '.wrk.txt')
            if not os.path.exists(path):
                continue
            with open(path) as f:
                txt = f.read()
            rate = re.search(r'Requests/sec:\s+([0-9.]+)', txt)
            total = re.search(r'([0-9]+) requests in', txt)
            lat = re.search(r'Latency\s+(\S+)\s+\S+\s+\S+', txt)   # Thread Stats avg
            p50 = re.search(r'\s50%\s+(\S+)', txt)
            p99 = re.search(r'\s99%\s+(\S+)', txt)
            errs = 0
            se = re.search(r'Socket errors: connect ([0-9]+), read ([0-9]+), write ([0-9]+), timeout ([0-9]+)', txt)
            if se:
                errs += sum(int(g) for g in se.groups())
            nx = re.search(r'Non-2xx or 3xx responses: ([0-9]+)', txt)
            if nx:
                errs += int(nx.group(1))
            wrows.append((label, int(total.group(1)) if total else 0, float(rate.group(1)) if rate else 0.0,
                          wrk_ms(lat.group(1) if lat else None),
                          wrk_ms(p50.group(1) if p50 else None),
                          wrk_ms(p99.group(1) if p99 else None), errs))
            wstamps.append(label + ': ' + when(path))
    return rows, stamps, srows, sstamps, wrows, wstamps
def render(rsuf, group):
    rows, stamps, srows, sstamps, wrows, wstamps = collect(rsuf)
    if not rows and not srows and not wrows:
        return False
    if group:
        print(); print(group); print('=' * len(group))
    if rows:
        hdr = '%-14s %12s %12s %12s %10s %9s' % ('server', 'requests', 'req/s', 'avg', 'failed', 'failed%')
        print(); print(hdr); print('-' * len(hdr))
        for name, total, rate, avg, nfail, frate in rows:
            avgs = ('%.2fms' % avg) if avg is not None else 'n/a'
            print('%-14s %12d %12.1f %12s %10d %8.2f%%' % (name, total, rate, avgs, nfail, frate))
        print(); print('(' + '; '.join(stamps) + ')')
    if srows:
        hdr = '%-14s %-10s %12s %12s %12s %9s' % ('server', 'scenario', 'requests', 'req/s', 'avg', 'failed%')
        print(); print(hdr); print('-' * len(hdr))
        for name, scen, total, rate, avg, frate in srows:
            avgs = ('%.2fms' % avg) if avg is not None else 'n/a'
            print('%-14s %-10s %12d %12.1f %12s %8.2f%%' % (name, scen, total, rate, avgs, frate))
        print(); print('(' + '; '.join(sstamps) + ')')
    if wrows:
        def ms(v):
            return ('%.2fms' % v) if v is not None else 'n/a'
        hdr = '%-20s %12s %12s %10s %10s %10s %8s' % ('server (wrk)', 'requests', 'req/s', 'avg', 'p50', 'p99', 'errors')
        print(); print(hdr); print('-' * len(hdr))
        for label, total, rate, avg, p50, p99, errs in wrows:
            print('%-20s %12d %12.1f %10s %10s %10s %8d' % (label, total, rate, ms(avg), ms(p50), ms(p99), errs))
        print(); print('(' + '; '.join(wstamps) + ')')
    return True
printed = False
for rsuf, group in VARIANTS:
    printed = render(rsuf, group) or printed
if not printed:
    raise SystemExit('no summaries in results/ — run `make bench` first')
endef
export REPORT_PY

report:
	@python3 -c "$$REPORT_PY"

clean:
	@rm -rf $(RESULTS)
	@echo "results/ removed"
