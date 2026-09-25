// k6 gRPC workload: an open-loop run of one protocol against
// bench.v1.EchoService/Echo, or against the hello worker for http-h1. The
// request rate is fixed, so a server that cannot serve the fixed rate shows as
// dropped iterations.
//
// - env: TARGET (full URL; grpc uses only its host:port, because connect()
//   takes an address), PROTO (grpc, grpcweb-h1, connect-h1, connectjson-h1 or
//   http-h1), RATE (requests per second), DURATION, VUS (pre-allocated and
//   maximum VUs).
// - the checks compare each reply with the fixed reply: the message text for
//   grpc, the body with its file in grpc/ for the other protocols. The checks
//   and dropped_iterations thresholds fail the run on one bad reply or one
//   dropped iteration.
// - the report uses iterations, dropped_iterations, checks and
//   grpc_req_duration or http_req_duration from the --summary-export JSON.

import grpc from "k6/net/grpc";
import http from "k6/http";
import { check } from "k6";

const TARGET = __ENV.TARGET || "http://127.0.0.1:8080/bench.v1.EchoService/Echo";
const PROTO = __ENV.PROTO || "grpc";
const VUS = Number(__ENV.VUS || 256);
const TEXT = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ01";
const REPLY = `Hello from worker, ${TEXT}!`;

export const options = {
	scenarios: {
		open: {
			// The rate increases from 0 to RATE in one second and then stays at
			// RATE for DURATION. k6 starts the schedule before its VUs are
			// active, and a constant rate from the start drops the iterations of
			// that start-up window.
			executor: "ramping-arrival-rate",
			startRate: 0,
			timeUnit: "1s",
			stages: [
				{ duration: "1s", target: Number(__ENV.RATE || 1000) },
				{ duration: __ENV.DURATION || "15s", target: Number(__ENV.RATE || 1000) },
			],
			preAllocatedVUs: VUS,
			maxVUs: VUS,
		},
	},
	thresholds: {
		checks: ["rate==1"],
		// A threshold makes k6 export the metric also when its count is 0.
		dropped_iterations: ["count==0"],
	},
};

const client = new grpc.Client();
client.loadProtoset("../grpc/bench.binpb");
let connected = false;

function unary() {
	if (!connected) {
		client.connect(TARGET.split("/")[2], { plaintext: true });
		connected = true;
	}
	const res = client.invoke("bench.v1.EchoService/Echo", { text: TEXT });
	check(res, {
		"status is OK": (r) => r.status === grpc.StatusOK,
		"echoes the text": (r) => r.message !== null && r.message.text === REPLY,
	});
}

function sameBytes(body, expected) {
	if (body === null || body.byteLength !== expected.byteLength) {
		return false;
	}
	const a = new Uint8Array(body);
	const b = new Uint8Array(expected);
	return a.every((v, i) => v === b[i]);
}

// The POST protocols: request headers, request body and expected response body.
// A binary expectation is compared byte for byte, a text expectation as a string.
const POSTS = {
	"grpcweb-h1": {
		headers: { "content-type": "application/grpc-web+proto", "x-grpc-web": "1" },
		body: open("../grpc/echo.grpc", "b"),
		expect: open("../grpc/expect.grpcweb", "b"),
	},
	"connect-h1": {
		headers: { "content-type": "application/proto", "connect-protocol-version": "1", "accept-encoding": "identity" },
		body: open("../grpc/echo.bin", "b"),
		expect: open("../grpc/expect.bin", "b"),
	},
	"connectjson-h1": {
		headers: { "content-type": "application/json", "connect-protocol-version": "1", "accept-encoding": "identity" },
		body: open("../grpc/echo.json"),
		expect: open("../grpc/expect.json"),
	},
};

function post() {
	const p = POSTS[PROTO];
	const binary = typeof p.expect !== "string";
	const res = http.post(TARGET, p.body, {
		headers: p.headers,
		responseType: binary ? "binary" : "text",
	});
	check(res, {
		"status is 200": (r) => r.status === 200,
		"body matches": (r) => (binary ? sameBytes(r.body, p.expect) : r.body === p.expect),
	});
}

const HELLO = open("../grpc/expect.http");

function hello() {
	const res = http.get(TARGET);
	check(res, {
		"status is 200": (r) => r.status === 200,
		"body matches": (r) => r.body === HELLO,
	});
}

const RUN = {
	grpc: unary,
	"grpcweb-h1": post,
	"connect-h1": post,
	"connectjson-h1": post,
	"http-h1": hello,
};
if (!(PROTO in RUN)) {
	throw new Error(`unknown PROTO ${PROTO}`);
}

export default function () {
	RUN[PROTO]();
}
