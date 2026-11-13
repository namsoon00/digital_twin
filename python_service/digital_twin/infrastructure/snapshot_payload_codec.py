"""Lossless, self-contained storage for large snapshot evidence.

Keep account, quote and symbol paths queryable in SQL. Only large evidence
values are encoded; readers restore them before domain code or fingerprints.
No external blob reference can outlive its source row or archival manifest.
"""
import base64
import hashlib
import json
import zlib

from .operational_common import json_dumps


MARKER = "__orbit_snapshot_zlib_v1__"
MIN_BYTES = 32 * 1024
MAX_BYTES = 64 * 1024 * 1024
EVIDENCE_GROUPS = frozenset({
    "companyKnowledge", "researchEvidence", "companyOverviews", "corporateActions",
    "driverDcfReadiness", "driverDcfInputs", "yfinanceData", "secFilings", "dartDisclosures",
})


def _pack(value):
    raw = json_dumps(value).encode("utf-8")
    if not MIN_BYTES <= len(raw) <= MAX_BYTES:
        return value
    compressed = zlib.compress(raw, 1)
    encoded = base64.b64encode(compressed).decode("ascii")
    if len(encoded) + 160 >= len(raw):
        return value
    return {MARKER: encoded, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _prepare(state):
    if not isinstance(state, dict):
        return state
    result = dict(state)
    signals = state.get("externalSignals")
    if isinstance(signals, dict):
        result["externalSignals"] = {
            group: {symbol: _pack(value) for symbol, value in values.items()}
            if group in EVIDENCE_GROUPS and isinstance(values, dict) else values
            for group, values in signals.items()
        }
    metadata = state.get("metadata")
    if isinstance(metadata, dict):
        result["metadata"] = dict(metadata)
        if isinstance(metadata.get("previousMonitorState"), dict):
            result["metadata"]["previousMonitorState"] = _prepare(metadata["previousMonitorState"])
        if isinstance(metadata.get("monitorStateHistory"), list):
            result["metadata"]["monitorStateHistory"] = [_prepare(item) for item in metadata["monitorStateHistory"]]
    return result


def snapshot_json_dumps(state):
    return json_dumps(_prepare(state))


def _restore(value):
    if MARKER not in value:
        return value
    # Corruption is a failed read, never absent evidence or a fresh fallback.
    try:
        size = value["bytes"]
        if (set(value) != {MARKER, "bytes", "sha256"} or type(size) is not int
                or not 0 < size <= MAX_BYTES):
            raise ValueError("invalid envelope")
        packed = base64.b64decode(value[MARKER], validate=True)
        inflater = zlib.decompressobj()
        raw = inflater.decompress(packed, size + 1)
        if (len(raw) != size or not inflater.eof or inflater.unused_data
                or inflater.unconsumed_tail or hashlib.sha256(raw).hexdigest() != value["sha256"]):
            raise ValueError("invalid payload")
        return json.loads(raw)
    except (ValueError, TypeError, KeyError, zlib.error) as error:
        raise ValueError("Snapshot evidence integrity check failed") from error


def snapshot_json_loads(payload, default=None):
    """Read old plain rows and new encoded rows, including SQL-selected leaves."""
    if payload is None or payload == "":
        return {} if default is None else default
    return json.loads(payload, object_hook=_restore)


def expanded_snapshot_json(payload):
    """Preserve the serialized source port contract and its fingerprint input."""
    if isinstance(payload, str) and MARKER in payload:
        return json_dumps(snapshot_json_loads(payload))
    return payload
