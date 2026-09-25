"""Parse the RESULT line of a load process and merge the loader records of one stage."""

import json
from dataclasses import dataclass

LATE_LIMIT_MS = 1000
ERROR_KEYS = ("connect", "read", "write", "status", "timeout", "dropped")
PERCENTILE_KEYS = ("p50", "p90", "p95", "p99", "p999", "max")
_RESULT_PREFIX = "RESULT "


class MissingLoader(ValueError):
    pass


class LateLoader(ValueError):
    pass


@dataclass(frozen=True)
class LoaderRecord:
    loader: str
    tool: str
    late_ms: int
    duration_us: int
    requests: int
    bytes: int
    errors: dict[str, int]
    latency_us: dict[str, float]
    requests_per_sec: float


@dataclass(frozen=True)
class Merged:
    requests: int
    successful: int
    bytes: int
    errors: dict[str, int]
    achieved_rps: float
    successful_rps: float
    latency_us: dict[str, float]


def parse_result(text: str, loader: str) -> LoaderRecord | None:
    lines = [line for line in text.splitlines() if line.startswith(_RESULT_PREFIX)]
    if not lines:
        return None
    doc = json.loads(lines[-1][len(_RESULT_PREFIX):])
    try:
        return LoaderRecord(
            loader=loader,
            tool=doc["tool"],
            late_ms=int(doc["late_ms"]),
            duration_us=int(doc["duration_us"]),
            requests=int(doc["requests"]),
            bytes=int(doc["bytes"]),
            errors={key: int(doc["errors"][key]) for key in ERROR_KEYS},
            latency_us={key: float(doc["latency_us"][key]) for key in ("mean", *PERCENTILE_KEYS)},
            requests_per_sec=float(doc["requests_per_sec"]),
        )
    except KeyError as exc:
        raise ValueError(f"{loader}: RESULT line has no key {exc}") from None


def merge(records: dict[str, LoaderRecord | None], stage_s: int) -> Merged:
    present = []
    for loader, record in records.items():
        if record is None:
            raise MissingLoader(f"loader {loader} returned no RESULT line")
        if record.late_ms > LATE_LIMIT_MS:
            raise LateLoader(f"loader {loader} started {record.late_ms} ms late")
        present.append(record)
    requests = sum(r.requests for r in present)
    errors = {key: sum(r.errors[key] for r in present) for key in ERROR_KEYS}
    successful = requests - errors["status"]
    # A stage where no loader completed a request has no latency to weight.
    mean = sum(r.latency_us["mean"] * r.requests for r in present) / requests if requests else 0.0
    latency = {"mean": mean}
    for key in PERCENTILE_KEYS:
        latency[key] = max(r.latency_us[key] for r in present)
    return Merged(
        requests=requests,
        successful=successful,
        bytes=sum(r.bytes for r in present),
        errors=errors,
        achieved_rps=requests / stage_s,
        successful_rps=successful / stage_s,
        latency_us=latency,
    )
