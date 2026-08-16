// k6 load benchmark for the rapira HTTP front.
//
//   Run (server must be listening on :8080):
//     k6 run benchmarks/bench.js
//
//   Override defaults via env:
//     k6 run -e TARGET=http://127.0.0.1:8080/?name=you -e VUS=1000 -e DURATION=30s benchmarks/bench.js

import http from "k6/http";
import { check } from "k6";

const TARGET = __ENV.TARGET || "http://127.0.0.1:8080/?name=you";
// Per-request JS checks cost ~30% generator throughput (measured: 122k -> 162k rps
// without them) — k6 becomes the bottleneck before the server does. Disable them
// with -e CHECKS=0 when probing the server's ceiling.
const CHECKS = __ENV.CHECKS !== "0";

export const options = {
	vus: Number(__ENV.VUS || 500),
	duration: __ENV.DURATION || "30s",
	discardResponseBodies: !CHECKS,
	thresholds: {
		http_req_failed: ["rate<0.01"], // fewer than 1% failed requests
		http_req_duration: ["p(95)<500"], // 95th percentile under 500ms
	},
};

export default function () {
	const res = http.get(TARGET);
	if (CHECKS) {
		check(res, {
			"status is 200": (r) => r.status === 200,
			"greets you": (r) => r.body.includes("you"),
		});
	}
}
