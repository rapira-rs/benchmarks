// k6 hello workload. On a small loader k6 is generator-bound far below
// rapira's ceiling; read its numbers as a latency probe, not throughput.
//
// Workload contract (every k6/<workload>.js follows it, paired with the PHP
// handlers in php/<workload>/):
// - env: TARGET (full URL), VUS, DURATION, CHECKS (0 disables per-request
//   checks and discards bodies).
// - keep a threshold on http_req_failed so failures are visible.
// - report.py reads the --summary-export JSON: http_reqs, http_req_duration,
//   http_req_failed, and any `metric{scenario:name}` submetrics; declare a
//   threshold on a submetric to make k6 export it as its own report row.

import http from "k6/http";
import { check } from "k6";

const TARGET = __ENV.TARGET || "http://127.0.0.1:8080/?name=you";
const CHECKS = __ENV.CHECKS !== "0";

export const options = {
	vus: Number(__ENV.VUS || 256),
	duration: __ENV.DURATION || "15s",
	discardResponseBodies: !CHECKS,
	thresholds: {
		http_req_failed: ["rate<0.01"],
	},
};

export default function () {
	const res = http.get(TARGET);
	if (CHECKS) {
		check(res, {
			"status is 200": (r) => r.status === 200,
			"greets you": (r) => r.body !== null && r.body.includes("you"),
		});
	}
}
