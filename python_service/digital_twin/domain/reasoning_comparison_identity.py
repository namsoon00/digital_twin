"""Immutable input and independent-execution identity for engine comparisons."""

import hashlib
import json
from typing import Mapping


COMPARISON_INPUT_CONTRACT_VERSION = "reasoning-comparison-input-v2"


def _mapping(value):
    return dict(value) if isinstance(value, Mapping) else {}


def _texts(values):
    if not isinstance(values, (list, tuple, set)):
        values = [values] if values else []
    return sorted({str(value).strip() for value in values if str(value or "").strip()})


def _hash(value):
    body = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def job_comparison_identity(job: Mapping[str, object]):
    values = _mapping(job)
    payload = _mapping(_mapping(values.get("sourceEvent")).get("payload"))
    boundaries = [_mapping(item) for item in values.get("sourceBoundaries") or []]
    result = _mapping(values.get("result"))
    inputs = {
        "scopeKey": str(values.get("scopeKey") or ""),
        "sourceSnapshotId": str(values.get("sourceSnapshotId") or ""),
        "sourceSnapshotAt": str(values.get("sourceSnapshotAt") or ""),
        "sourcePayloadHash": str(values.get("sourcePayloadHash") or ""),
        "accountIds": _texts(payload.get("accountIds") or payload.get("accountId") or [item.get("accountId") for item in boundaries]),
        "symbols": _texts(payload.get("affectedSymbols") or payload.get("symbols") or payload.get("symbol") or [symbol for item in boundaries for symbol in item.get("symbols") or []]),
    }
    return {
        **inputs,
        "inputFingerprint": _hash(inputs),
        "executionKey": _hash(_texts(result.get("batch_job_ids") or result.get("batchJobIds") or [values.get("jobId")])),
    }


def comparison_input_contract(baseline: Mapping[str, object], candidate: Mapping[str, object]):
    left, right = job_comparison_identity(baseline), job_comparison_identity(candidate)
    checks = {key: bool(left[key]) and left[key] == right[key] for key in (
        "scopeKey", "sourceSnapshotId", "sourceSnapshotAt", "sourcePayloadHash", "accountIds", "symbols",
    )}
    return {
        "contractVersion": COMPARISON_INPUT_CONTRACT_VERSION,
        "eligible": all(checks.values()),
        "status": "equivalent" if all(checks.values()) else "incomparable-inputs",
        "checks": checks,
        "baseline": left,
        "candidate": right,
        "independentExecutionKey": _hash([left["executionKey"], right["executionKey"]]),
        "baselineJobId": str(baseline.get("jobId") or ""),
        "candidateJobId": str(candidate.get("jobId") or ""),
    }
