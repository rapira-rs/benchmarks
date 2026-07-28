# Benchmark harness — runs each server against bench.js, exports one k6 summary
# JSON per server into results/, then renders a comparison table.
# See INSTRUCTIONS.md for the manual flow and the gotchas this automates.
#
#   make bench                       # franken → fpm → roadrunner → swoole → table
#   make bench-all                   # rapira → rapira-classic → franken → fpm → roadrunner → swoole → table
#   make bench-rapira                # a single server (any of the five)
#   make bench-rapira-classic        # rapira --classic on the php-fpm scripts (apples-to-apples vs fpm)
#   make bench BENCH=scenario        # scenario workload (scenario.js + app/ mini-app workers)
#   make bench-wrk-all               # all five servers under wrk (high-concurrency ceilings)
#   make bench-wrk-fpm               # a single server under wrk
#   make report                      # re-print the table(s) from existing results/
#   make bench VUS=1000 DURATION=15s CHECKS=0    # override bench.js defaults
#   make bench-wrk-all WRK_THREADS=12 WRK_CONNS=5000 WRK_DURATION=15s   # override wrk defaults
#   make clean                       # drop results/
#
# One server at a time — all five bind :8080, so targets are strictly serial.

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
PHP_NTS := $(HOME)/.local/php-nts

# php-fpm stack: the pacman php-fpm (split from the SAME pacman build as the CLI `php`
# that swoole and roadrunner run) + nginx front; ~/.local/nginx is the no-sudo fallback.
# PHP-build parity caveat: only fpm/swoole/roadrunner share the distro 8.5.8. rapira loads
# the local $(PHP_NTS) embed build (8.5.8-dev) — required since the NTS rewrite — and
# FrankenPHP ships its own statically linked PHP.
PHP_FPM_BIN ?= $(shell command -v php-fpm || echo /usr/bin/php-fpm)
NGINX_BIN ?= $(shell command -v nginx || echo $(HOME)/.local/nginx/sbin/nginx)

# BENCH selects the workload: hello (bench.js, the original ceiling probe) or
# scenario (scenario.js + the app/app.php mini-app workers). In hello mode every
# derived value matches the historical behavior, so reference numbers stay valid.
BENCH ?= hello
ifeq ($(BENCH),scenario)
  K6_SCRIPT      := scenario.js
  SUF            := .scenario
  RAPIRA_SCRIPT  := rapira/scenario-worker.php
  RAPIRA_CLASSIC_SCRIPT := fpm/scenario.php
  FRANKEN_CONFIG := Caddyfile.scenario
  FPM_NGINX_CONF := nginx.scenario.conf
  RR_CONFIG      := .rr.scenario.yaml
  SWOOLE_SCRIPT  := scenario-server.php
else
  K6_SCRIPT      := bench.js
  SUF            :=
  RAPIRA_SCRIPT  := rapira/worker.php
  RAPIRA_CLASSIC_SCRIPT := fpm/hello.php
  FRANKEN_CONFIG := Caddyfile
  FPM_NGINX_CONF := nginx.conf
  RR_CONFIG      := .rr.yaml
  SWOOLE_SCRIPT  := server.php
endif

# rapira runs as a local binary in rapira/, like the other SAPIs' artifacts;
# it is refreshed from the cargo build output whenever that exists.
RAPIRA_SRC_BIN := ../core/target/release/rapira
RAPIRA_BIN := rapira/rapira

.PHONY: bench bench-all bench-rapira bench-rapira-classic bench-franken bench-fpm bench-roadrunner bench-swoole \
        bench-wrk bench-wrk-all bench-wrk-rapira bench-wrk-rapira-classic bench-wrk-franken bench-wrk-fpm \
        bench-wrk-roadrunner bench-wrk-swoole report clean

bench: bench-franken bench-fpm bench-roadrunner bench-swoole report

bench-all: bench-rapira bench-rapira-classic bench-franken bench-fpm bench-roadrunner bench-swoole report

bench-wrk: bench-wrk-franken bench-wrk-fpm bench-wrk-roadrunner bench-wrk-swoole report

bench-wrk-all: bench-wrk-rapira bench-wrk-rapira-classic bench-wrk-franken bench-wrk-fpm bench-wrk-roadrunner bench-wrk-swoole report

$(RESULTS):
	@mkdir -p $(RESULTS)

# Shared steps, $(call ...)-ed from each server target.
# port_guard: refuse to start on a busy :8080 (a stale server here once produced a ghost benchmark).
define port_guard
	if ss -ltn | grep -q ':8080 '; then echo "ERROR: :8080 already in use — stop that server first"; exit 1; fi
endef
# wait_ready(1=name): poll until the server answers, else dump its log and kill it.
define wait_ready
	ok=0; for i in $$(seq 1 60); do \
	  curl -sf -o /dev/null -m1 'http://127.0.0.1:8080/?name=you' && { ok=1; break; }; sleep 0.5; \
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
# stop_server(1=name, 2=signal): signal the pidfile, wait for :8080 to free, force-kill if held.
define stop_server
	kill -$(2) $$(cat $(RESULTS)/$(1)$(SUF).pid) 2>/dev/null || true; \
	for i in $$(seq 1 30); do ss -ltn | grep -q ':8080 ' || break; sleep 0.5; done; \
	if ss -ltn | grep -q ':8080 '; then \
	  echo "WARN: $(1) still holds :8080 — force-killing"; \
	  kill -KILL $$(cat $(RESULTS)/$(1)$(SUF).pid) 2>/dev/null; sleep 1; \
	fi; \
	rm -f $(RESULTS)/$(1)$(SUF).pid
endef

# start_<name>: artifact preflight + launch + pidfile. Shared by the k6 (bench-*)
# and wrk (bench-wrk-*) target families so the launch logic lives in one place.
define start_rapira
	if [ -x $(RAPIRA_SRC_BIN) ]; then cp -f $(RAPIRA_SRC_BIN) $(RAPIRA_BIN); fi; \
	test -x $(RAPIRA_BIN) || { echo "ERROR: $(RAPIRA_BIN) missing — build it first:"; \
	  echo "  cd ../core && PHP_CONFIG=$(PHP_NTS)/bin/php-config LD_LIBRARY_PATH=$(PHP_NTS)/lib cargo build --release"; exit 1; }; \
	echo "==> rapira: starting (32 worker processes)"; \
	LD_LIBRARY_PATH=$(PHP_NTS)/lib ./$(RAPIRA_BIN) serve --processes 32 --listen :8080 \
	  $(RAPIRA_SCRIPT) > $(RESULTS)/rapira$(SUF).server.log 2>&1 & \
	echo $$! > $(RESULTS)/rapira$(SUF).pid
endef
# rapira in classic mode: per-request script execution (no resident worker) on
# the SAME fpm/ scripts php-fpm serves — apples-to-apples with the fpm stack,
# differing only in the front (pingora vs nginx+fastcgi).
define start_rapira_classic
	if [ -x $(RAPIRA_SRC_BIN) ]; then cp -f $(RAPIRA_SRC_BIN) $(RAPIRA_BIN); fi; \
	test -x $(RAPIRA_BIN) || { echo "ERROR: $(RAPIRA_BIN) missing — build it first:"; \
	  echo "  cd ../core && PHP_CONFIG=$(PHP_NTS)/bin/php-config LD_LIBRARY_PATH=$(PHP_NTS)/lib cargo build --release"; exit 1; }; \
	echo "==> rapira-classic: starting (32 worker processes, per-request script)"; \
	LD_LIBRARY_PATH=$(PHP_NTS)/lib ./$(RAPIRA_BIN) serve --classic --processes 32 --listen :8080 \
	  $(RAPIRA_CLASSIC_SCRIPT) > $(RESULTS)/rapira-classic$(SUF).server.log 2>&1 & \
	echo $$! > $(RESULTS)/rapira-classic$(SUF).pid
endef
define start_franken
	test -x franken/frankenphp -a -f franken/$(FRANKEN_CONFIG) || { echo "ERROR: franken/frankenphp or franken/$(FRANKEN_CONFIG) missing — fetch/check it (see INSTRUCTIONS.md §2)"; exit 1; }; \
	echo "==> frankenphp: starting (32 workers)"; \
	cd franken && { ./frankenphp run --config $(FRANKEN_CONFIG) > ../$(RESULTS)/franken$(SUF).server.log 2>&1 & \
	  echo $$! > ../$(RESULTS)/franken$(SUF).pid; }
endef
# fpm is the one two-process stack here: an nginx master (+ workers) fronting a
# php-fpm master (+ 32 children). nginx's pid goes to the standard pidfile (it
# holds :8080, which the wait/stop logic keys on); fpm's master gets its own.
define start_fpm
	test -x "$(PHP_FPM_BIN)" || { echo "ERROR: php-fpm missing — install it (see INSTRUCTIONS.md §3)"; exit 1; }; \
	test -x "$(NGINX_BIN)" || { echo "ERROR: nginx missing — install it (see INSTRUCTIONS.md §3)"; exit 1; }; \
	echo "==> php-fpm: starting (32 static workers, nginx front)"; \
	mkdir -p fpm/run fpm/tmp; \
	cd fpm && { $(PHP_FPM_BIN) -F -p $$PWD -y php-fpm.conf > ../$(RESULTS)/fpm$(SUF).fpm.log 2>&1 & \
	  echo $$! > ../$(RESULTS)/fpm$(SUF).fpm.pid; } && \
	{ $(NGINX_BIN) -p $$PWD -e stderr -c $(FPM_NGINX_CONF) -g 'daemon off;' > ../$(RESULTS)/fpm$(SUF).server.log 2>&1 & \
	  echo $$! > ../$(RESULTS)/fpm$(SUF).pid; }
endef
define start_roadrunner
	test -x roadrunner/rr -a -d roadrunner/vendor || { echo "ERROR: roadrunner/rr or roadrunner/vendor missing — fetch them (see INSTRUCTIONS.md §4)"; exit 1; }; \
	echo "==> roadrunner: starting (32 workers)"; \
	cd roadrunner && { ./rr serve -c $(RR_CONFIG) > ../$(RESULTS)/roadrunner$(SUF).server.log 2>&1 & \
	  echo $$! > ../$(RESULTS)/roadrunner$(SUF).pid; }
endef
define start_swoole
	test -f swoole/swoole.so || { echo "ERROR: swoole/swoole.so missing — build it (see INSTRUCTIONS.md §5)"; exit 1; }; \
	echo "==> swoole: starting (32 workers)"; \
	cd swoole && { php -d extension=$$PWD/swoole.so $(SWOOLE_SCRIPT) > ../$(RESULTS)/swoole$(SUF).server.log 2>&1 & \
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
	kill -QUIT $$(cat $(RESULTS)/fpm$(SUF).pid) 2>/dev/null || true; \
	kill -QUIT $$(cat $(RESULTS)/fpm$(SUF).fpm.pid) 2>/dev/null || true; \
	for i in $$(seq 1 30); do ss -ltn | grep -q ':8080 ' || break; sleep 0.5; done; \
	if ss -ltn | grep -q ':8080 '; then \
	  echo "WARN: fpm still holds :8080 — force-killing"; \
	  pkill -KILL -P $$(cat $(RESULTS)/fpm$(SUF).pid) 2>/dev/null; \
	  kill -KILL $$(cat $(RESULTS)/fpm$(SUF).pid) 2>/dev/null; sleep 1; \
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

bench-rapira: | $(RESULTS)
	@$(call port_guard)
	@$(call start_rapira)
	@$(call wait_ready,rapira)
	@$(call run_k6,rapira)
	@$(call stop_server,rapira,INT)
	@$(call reap_rapira)
	@echo "==> rapira: done"

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

# Renders the tables from whatever summaries exist in results/ (rapira first if
# present): the hello table from <name>.summary.json, and a per-scenario table from
# <name>.scenario.summary.json when scenario runs exist.
# Format facts (verified against k6 v2.0.0 --summary-export):
#   http_reqs = {count, rate}; http_req_duration includes avg (a default trend stat);
#   http_req_failed is a Rate metric where `passes` counts FAILED requests, `value` is the rate;
#   tagged submetrics are keyed 'metric{scenario:name}' and exported only because
#   scenario.js declares thresholds on them.
define REPORT_PY
import json, os, re, time
rows, stamps = [], []
for name in ('rapira', 'rapira-classic', 'franken', 'fpm', 'roadrunner', 'swoole'):
    path = os.path.join('results', name + '.summary.json')
    if not os.path.exists(path):
        continue
    with open(path) as f:
        m = json.load(f)['metrics']
    reqs, dur, failed = m.get('http_reqs', {}), m.get('http_req_duration', {}), m.get('http_req_failed', {})
    rows.append((name, int(reqs.get('count', 0)), reqs.get('rate', 0.0),
                 dur.get('avg'), int(failed.get('passes', 0)), failed.get('value', 0.0) * 100))
    stamps.append(name + ': ' + time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(path))))
scen_names = ('browse', 'echoJson', 'form', 'misc')
srows, sstamps = [], []
for name in ('rapira', 'rapira-classic', 'franken', 'fpm', 'roadrunner', 'swoole'):
    path = os.path.join('results', name + '.scenario.summary.json')
    if not os.path.exists(path):
        continue
    with open(path) as f:
        m = json.load(f)['metrics']
    def cell(rk, dk, fk, m=m):
        r, d, x = m.get(rk, {}), m.get(dk, {}), m.get(fk, {})
        return (int(r.get('count', 0)), r.get('rate', 0.0), d.get('avg'), x.get('value', 0.0) * 100)
    srows.append((name, '(total)') + cell('http_reqs', 'http_req_duration', 'http_req_failed'))
    for s in scen_names:
        srows.append(('', s) + cell('http_reqs{scenario:%s}' % s,
                                    'http_req_duration{scenario:%s}' % s,
                                    'http_req_failed{scenario:%s}' % s))
    sstamps.append(name + ': ' + time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(path))))
def wrk_ms(tok):
    m = re.match(r'([0-9.]+)(us|ms|s|m)$$', tok or '')
    if not m:
        return None
    return float(m.group(1)) * {'us': 0.001, 'ms': 1.0, 's': 1000.0, 'm': 60000.0}[m.group(2)]
wrows, wstamps = [], []
for name in ('rapira', 'rapira-classic', 'franken', 'fpm', 'roadrunner', 'swoole'):
    for suf, label in (('', name), ('.scenario', name + ' (scn)')):
        path = os.path.join('results', name + suf + '.wrk.txt')
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
        wstamps.append(label + ': ' + time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(path))))
if not rows and not srows and not wrows:
    raise SystemExit('no summaries in results/ — run `make bench` first')
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
endef
export REPORT_PY

report:
	@python3 -c "$$REPORT_PY"

clean:
	@rm -rf $(RESULTS)
	@echo "results/ removed"
