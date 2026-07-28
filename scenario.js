// k6 multi-scenario load benchmark: mixed GET/POST/PUT/DELETE traffic against the
// shared mini-app (app/app.php) served by rapira, FrankenPHP, php-fpm, RoadRunner,
// or Swoole on :8080.
//
//   Run (a scenario-mode server must be listening, see INSTRUCTIONS.md):
//     k6 run benchmarks/scenario.js
//
//   Override defaults via env:
//     k6 run -e BASE=http://127.0.0.1:8080 -e VUS=1000 -e DURATION=30s benchmarks/scenario.js
//     k6 run -e CHECKS=0 benchmarks/scenario.js   # ceiling probe (no JS checks, discard bodies)

import http from 'k6/http';
import { check } from 'k6';

const BASE = __ENV.BASE || 'http://127.0.0.1:8080';
const VUS = Number(__ENV.VUS || 100);
const DURATION = __ENV.DURATION || '30s';
// Same escape hatch as bench.js: per-request JS checks cost ~30% generator
// throughput — k6 becomes the bottleneck before the server does.
const CHECKS = __ENV.CHECKS !== '0';

// Traffic mix: share of the VU budget per scenario. Weights sum to 1.0.
const SPLIT = [
	['browse', 0.40],
	['echoJson', 0.25],
	['form', 0.20],
	['misc', 0.15],
];

// Round each share, floor at 1 VU, push rounding drift onto the largest bucket
// so the allocation sums back to VUS exactly. With VUS < 4 the floor-at-1 rule
// wins and the effective total is 4.
function allocateVUs(total, split) {
	const alloc = {};
	let assigned = 0;
	for (const [name, frac] of split) {
		const v = Math.max(1, Math.round(total * frac));
		alloc[name] = v;
		assigned += v;
	}
	alloc[split[0][0]] = Math.max(1, alloc[split[0][0]] + (total - assigned));
	return alloc;
}
const VU = allocateVUs(VUS, SPLIT);

// Payload pools: plain module-scope consts, built once per VU at init — NOT
// SharedArray, which re-deserializes on every access and would tax the hot loop.
const NAMES = ['ada', 'linus', 'grace', 'dennis', 'ken', 'margaret', 'you', 'anon'];
const BROWSE_PATHS = ['/', '/hello', '/greet', '/index.html', '/api/hello'];
const UNKNOWN_PATHS = ['/nope', '/admin', '/v2/missing', '/static/none.js'];

const JSON_HDR = { 'Content-Type': 'application/json' };
const FORM_HDR = { 'Content-Type': 'application/x-www-form-urlencoded' };

// ~0.8 KB JSON bodies, pre-serialized once.
const JSON_PAYLOADS = (function() {
	const out = [];
	for (let i = 0; i < 16; i++) {
		out.push(JSON.stringify({
			id: i,
			name: NAMES[i % NAMES.length],
			email: `user${i}@example.test`,
			tags: ['alpha', 'beta', 'gamma'],
			note: 'x'.repeat(700),
		}));
	}
	return out;
})();

const FORM_PAYLOADS = NAMES.map((n, i) =>
	`name=${n}&email=user${i}%40example.test&message=${'hello+'.repeat(30)}`);

const RESOURCE_BODY = JSON.stringify({ op: 'update', value: 42 });

// Intentional 404/405s count as expected responses, not failures.
const NOTFOUND = http.expectedStatuses(404, 405);

// Cheap per-VU rotation counters — no Math.random on the hot path. The 404 pool
// gets its own counter: inside misc() ctr strides by 4 between visits to the same
// branch, which would phase-lock ctr-based indexing to a single UNKNOWN_PATHS entry.
let ctr = 0;
let miss = 0;

const scenario = (exec, vus) => ({
	executor: 'constant-vus',
	exec,
	vus,
	duration: DURATION,
	gracefulStop: '5s',
});

export const options = {
	discardResponseBodies: !CHECKS,
	scenarios: {
		browse: scenario('browse', VU.browse),
		echoJson: scenario('echoJson', VU.echoJson),
		form: scenario('form', VU.form),
		misc: scenario('misc', VU.misc),
	},
	thresholds: {
		http_req_failed: ['rate<0.01'],   // fewer than 1% failed requests
		http_req_duration: ['p(95)<500'], // 95th percentile under 500ms

		// Per-scenario submetrics: --summary-export only emits a tagged submetric
		// when a threshold references it — these feed the report's scenario table.
		'http_reqs{scenario:browse}': ['count>=0'],
		'http_reqs{scenario:echoJson}': ['count>=0'],
		'http_reqs{scenario:form}': ['count>=0'],
		'http_reqs{scenario:misc}': ['count>=0'],
		'http_req_duration{scenario:browse}': ['p(95)<500'],
		'http_req_duration{scenario:echoJson}': ['p(95)<500'],
		'http_req_duration{scenario:form}': ['p(95)<500'],
		'http_req_duration{scenario:misc}': ['p(95)<500'],
		'http_req_failed{scenario:browse}': ['rate<0.01'],
		'http_req_failed{scenario:echoJson}': ['rate<0.01'],
		'http_req_failed{scenario:form}': ['rate<0.01'],
		'http_req_failed{scenario:misc}': ['rate<0.01'],
	},
};

export function browse() {
	const path = BROWSE_PATHS[ctr % BROWSE_PATHS.length];
	const name = NAMES[ctr % NAMES.length];
	ctr++;
	const res = http.get(`${BASE}${path}?name=${name}`, { tags: { name: 'browse' } });
	if (CHECKS) {
		check(res, {
			'browse is 200': (r) => r.status === 200,
			'browse greets': (r) => r.body && r.body.includes(name),
		});
	}
}

export function echoJson() {
	const body = JSON_PAYLOADS[ctr++ % JSON_PAYLOADS.length];
	const res = http.post(`${BASE}/echo`, body, { headers: JSON_HDR, tags: { name: 'echo' } });
	if (CHECKS) {
		check(res, {
			'echo is 200': (r) => r.status === 200,
			'echo echoed': (r) => r.body && r.body.includes('example.test'),
		});
	}
}

export function form() {
	const body = FORM_PAYLOADS[ctr++ % FORM_PAYLOADS.length];
	const res = http.post(`${BASE}/form`, body, { headers: FORM_HDR, tags: { name: 'form' } });
	if (CHECKS) {
		check(res, {
			'form is 200': (r) => r.status === 200,
			'form echoed': (r) => r.body && r.body.includes('example.test'),
		});
	}
}

export function misc() {
	const pick = ctr++ % 4;
	if (pick === 0) {
		const res = http.put(`${BASE}/resource/${ctr % 100}`, RESOURCE_BODY,
			{ headers: JSON_HDR, tags: { name: 'resource' } });
		if (CHECKS) check(res, { 'put is 200': (r) => r.status === 200 });
	} else if (pick === 1) {
		const res = http.del(`${BASE}/resource/${ctr % 100}`, null,
			{ tags: { name: 'resource' } });
		if (CHECKS) check(res, { 'del is 200': (r) => r.status === 200 });
	} else if (pick === 2) {
		// Unknown route -> 404, marked expected via responseCallback.
		const res = http.post(`${BASE}${UNKNOWN_PATHS[miss++ % UNKNOWN_PATHS.length]}`, null,
			{ tags: { name: 'notfound' }, responseCallback: NOTFOUND });
		if (CHECKS) check(res, { 'is 404': (r) => r.status === 404 });
	} else {
		// Wrong method on a known route -> 405, also expected.
		const res = http.patch(`${BASE}/resource/${ctr % 100}`, null,
			{ tags: { name: 'notfound' }, responseCallback: NOTFOUND });
		if (CHECKS) check(res, { 'is 405': (r) => r.status === 405 });
	}
}
