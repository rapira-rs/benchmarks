"""Parse the RESULT line of a load process and merge the loader records of one stage into the numbers of a cell."""

import json
from dataclasses import dataclass

LATE_LIMIT_MS = 1000
ERROR_KEYS = ("connect", "read", "write", "status", "timeout", "dropped")
PERCENTILE_KEYS = ("p50", "p90", "p95", "p99", "p999", "max")
# The percentiles of the cell record. The loader record keeps all of PERCENTILE_KEYS and the mean as evidence.
MERGED_KEYS = ("p50", "p90", "p99", "p999", "max")
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


def merge(records: dict[str, LoaderRecord | None], window_s: int) -> Merged:
    """Merge the loader records into the error sums, the rates, and the maximum of each percentile of MERGED_KEYS.

    window_s is the seconds that the requests counts of the records cover.
    """
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
    return Merged(
        errors=errors,
        achieved_rps=requests / window_s,
        successful_rps=successful / window_s,
        latency_us={key: max(r.latency_us[key] for r in present) for key in MERGED_KEYS},
    )
