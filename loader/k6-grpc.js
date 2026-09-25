// k6 gRPC stage: an open-loop run that ramps to the stage rate in one second and holds it, against
// bench.v1.EchoService/Echo. box/load.sh runs one process per stage and loader.
//
// - env: TARGET (full URL; connect() takes only its host:port), RATE (requests
//   per second), DURATION (seconds), VUS (pre-allocated and maximum VUs).
// - one check per call compares the reply with the fixed text. The failed
//   checks and the failed calls never exceed the iterations.
// - handleSummary prints one line RESULT {json}. box/load.sh adds the tool and
//   late_ms fields.

import grpc from "k6/net/grpc";
import { check } from "k6";
import { Counter } from "k6/metrics";

const TARGET = __ENV.TARGET || "http://127.0.0.1:8080/bench.v1.EchoService/Echo";
const VUS = Number(__ENV.VUS || 256);
const TEXT = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ01";
const REPLY = `Hello from worker, ${TEXT}!`;

// A connect or an invoke that throws adds 1 here, and the iteration completes.
const callFailures = new Counter("call_failures");

const RATE = Number(__ENV.RATE || 1000);
const DURATION = Number(__ENV.DURATION || 20);

// The rate climbs from 0 to RATE over the first second and then holds RATE. The first call of
// every VU opens its connection, and a constant rate from the start drops iterations in that
// window. A 20 s stage still achieves about 97.5% of RATE, inside the 95% pass rule.
export const options = {
	scenarios: {
		stage: {
			executor: "ramping-arrival-rate",
			startRate: 0,
			timeUnit: "1s",
			preAllocatedVUs: VUS,
			maxVUs: VUS,
			stages: [
				{ duration: "1s", target: RATE },
				{ duration: `${DURATION - 1}s`, target: RATE },
			],
		},
	},
};

const client = new grpc.Client();
client.loadProtoset("../apps/grpc/bench.binpb");
let connected = false;

export default function () {
	try {
		if (!connected) {
			client.connect(TARGET.split("/")[2], { plaintext: true });
			connected = true;
		}
		const res = client.invoke("bench.v1.EchoService/Echo", { text: TEXT });
		check(res, {
			"reply matches": (r) => r.status === grpc.StatusOK && r.message !== null && r.message.text === REPLY,
		});
	} catch (e) {
		callFailures.add(1);
	}
}

// A metric without samples is absent from the summary data, so every read has a default of 0.
function metric(data, name, key) {
	const m = data.metrics[name];
	return m ? m.values[key] : 0;
}

// grpc_req_duration is in milliseconds. The RESULT line is in microseconds.
function micros(data, stat) {
	return metric(data, "grpc_req_duration", stat) * 1000;
}

export function handleSummary(data) {
	const result = {
		duration_us: Math.round(data.state.testRunDurationMs * 1000),
		requests: metric(data, "iterations", "count"),
		bytes: metric(data, "data_received", "count"),
		errors: {
			connect: 0,
			read: 0,
			write: 0,
			status: metric(data, "checks", "fails") + metric(data, "call_failures", "count"),
			timeout: 0,
			dropped: metric(data, "dropped_iterations", "count"),
		},
		latency_us: {
			mean: Math.round(micros(data, "avg") * 1000) / 1000,
			p50: Math.round(micros(data, "med")),
			p90: Math.round(micros(data, "p(90)")),
			p95: Math.round(micros(data, "p(95)")),
			p99: Math.round(micros(data, "p(99)")),
			p999: Math.round(micros(data, "p(99.9)")),
			max: Math.round(micros(data, "max")),
		},
		requests_per_sec: metric(data, "iterations", "rate"),
	};
	return { stdout: `RESULT ${JSON.stringify(result)}\n` };
}
