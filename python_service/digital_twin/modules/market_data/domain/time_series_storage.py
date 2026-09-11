"""Vendor-neutral contracts for temporal market data and feature snapshots."""

import hashlib
import json
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Mapping, Protocol, Tuple


TIME_SERIES_CONTRACT_VERSION = "time-series-storage-contract-v1"
TEMPORAL_FEATURE_SET_VERSION = "temporal-features-v2"
TEMPORAL_WINDOW_REFERENCE_VERSION = "temporal-window-reference-v1"

BACKEND_STATUSES = {
    "registered",
    "provisioning",
    "shadow",
    "candidate",
    "active",
    "retired",
    "blocked",
}

BACKEND_TRANSITIONS = {
    "registered": {"provisioning", "blocked", "retired"},
    "provisioning": {"shadow", "blocked", "retired"},
    "shadow": {"candidate", "blocked", "retired"},
    "candidate": {"active", "shadow", "blocked", "retired"},
    "active": {"candidate", "retired", "blocked"},
    "blocked": {"provisioning", "shadow", "retired"},
    "retired": {"provisioning"},
}


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def payload_fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def temporal_window_reference(windows: Mapping[str, object]) -> Dict[str, object]:
    """Describe immutable windows without copying raw time-series rows.

    Raw observations already live in the selected time-series backend. The
    MySQL feature record only needs the point-in-time boundary and hashes that
    prove which rows were scored. This keeps audit identity while avoiding a
    full history copy on every reasoning cycle.
    """

    timestamp_keys = (
        "bucketAt",
        "observedAt",
        "sourceAsOf",
        "capitalFlowObservedAt",
        "capitalFlowSourceAsOf",
        "date",
    )
    symbols: Dict[str, Dict[str, object]] = {}
    total_rows = 0
    for raw_symbol, raw_windows in sorted(dict(windows or {}).items()):
        symbol = str(raw_symbol or "").upper().strip()
        if not symbol:
            continue
        descriptors: Dict[str, object] = {}
        fingerprints: Dict[str, str] = {}
        for raw_name, raw_rows in sorted(dict(raw_windows or {}).items()):
            name = str(raw_name or "").upper().strip()
            rows = [row if isinstance(row, dict) else {} for row in raw_rows or []]
            if not name:
                continue
            def observed_at(row):
                return next(
                    (
                        normalized_timestamp(row.get(key))
                        for key in timestamp_keys
                        if row.get(key) not in (None, "")
                    ),
                    "",
                )

            first_observed_at = next((value for value in (observed_at(row) for row in rows) if value), "")
            last_observed_at = next((value for value in (observed_at(row) for row in reversed(rows)) if value), "")
            sample_indexes = sorted({
                0,
                max(0, len(rows) // 4),
                max(0, len(rows) // 2),
                max(0, (len(rows) * 3) // 4),
                max(0, len(rows) - 1),
            }) if rows else []
            sampled_rows = [rows[index] for index in sample_indexes]
            content_hash = payload_fingerprint({
                "rowCount": len(rows),
                "firstObservedAt": first_observed_at,
                "lastObservedAt": last_observed_at,
                "samples": semantic_feature_value(sampled_rows),
            })
            duplicate_of = next(
                (window for window, fingerprint in fingerprints.items() if fingerprint == content_hash),
                "",
            )
            descriptor = {
                "rowCount": len(rows),
                "firstObservedAt": first_observed_at,
                "lastObservedAt": last_observed_at,
                "contentHash": content_hash,
                "contentHashMode": "boundary-sample-v1",
            }
            if duplicate_of:
                descriptor["duplicateOf"] = duplicate_of
            descriptors[name] = descriptor
            fingerprints[name] = content_hash
            total_rows += len(rows)
        if descriptors:
            symbols[symbol] = descriptors
    return {
        "storageMode": TEMPORAL_WINDOW_REFERENCE_VERSION,
        "source": "time-series-backend-point-in-time-read",
        "symbolCount": len(symbols),
        "rowReferenceCount": total_rows,
        "symbols": symbols,
    }


def is_temporal_window_reference(value: object) -> bool:
    return bool(
        isinstance(value, dict)
        and str(value.get("storageMode") or "") == TEMPORAL_WINDOW_REFERENCE_VERSION
    )


def normalized_timestamp(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def compacted_legacy_window_reference(
    symbols: Iterable[object],
    payload_hash: object,
    as_of: object,
    inline_bytes: int,
) -> Dict[str, object]:
    normalized_symbols = sorted({
        str(symbol or "").upper().strip()
        for symbol in symbols or []
        if str(symbol or "").strip()
    })
    return {
        "storageMode": TEMPORAL_WINDOW_REFERENCE_VERSION,
        "source": "legacy-inline-window-compaction",
        "symbolCount": len(normalized_symbols),
        "rowReferenceCount": 0,
        "rowCountState": "not-retained",
        "symbols": {
            symbol: {"storageState": "compacted-reference-only"}
            for symbol in normalized_symbols
        },
        "snapshotPayloadHash": str(payload_hash or ""),
        "snapshotAsOf": normalized_timestamp(as_of),
        "legacyInlineBytes": max(0, int(inline_bytes or 0)),
    }


def semantic_feature_value(value: object, key: str = ""):
    """Remove backend presentation metadata before cross-store comparison."""

    if isinstance(value, dict):
        return {
            str(child_key): semantic_feature_value(child_value, str(child_key))
            for child_key, child_value in sorted(value.items())
            if str(child_key) != "observationSource"
        }
    if isinstance(value, (list, tuple)):
        return [semantic_feature_value(item, key) for item in value]
    if isinstance(value, float):
        # SQL drivers may round the same IEEE-754 value at the final few
        # decimal places. Ten significant digits is strict enough for every
        # investment feature while avoiding false backend mismatches.
        return float(format(value, ".10g"))
    if key in {
        "generatedAt",
        "updatedAt",
        "sourceAsOf",
        "bucketAt",
        "capitalFlowSourceAsOf",
        "capitalFlowObservedAt",
    }:
        return normalized_timestamp(value)
    return value


def clean_status(value: object, fallback: str = "registered") -> str:
    status = str(value or fallback).strip().lower()
    return status if status in BACKEND_STATUSES else fallback


def backend_transition_allowed(current: object, target: object) -> bool:
    current_status = clean_status(current)
    target_status = clean_status(target)
    return current_status == target_status or target_status in BACKEND_TRANSITIONS.get(current_status, set())


@dataclass(frozen=True)
class TimeSeriesCapabilities:
    out_of_order_write: bool = False
    idempotent_upsert: bool = False
    time_partitioning: bool = False
    automatic_retention: bool = False
    incremental_aggregation: bool = False
    as_of_join: bool = False
    window_functions: bool = False
    batch_ingestion: bool = False
    point_in_time_read: bool = False

    def to_dict(self) -> Dict[str, bool]:
        return {
            "outOfOrderWrite": self.out_of_order_write,
            "idempotentUpsert": self.idempotent_upsert,
            "timePartitioning": self.time_partitioning,
            "automaticRetention": self.automatic_retention,
            "incrementalAggregation": self.incremental_aggregation,
            "asOfJoin": self.as_of_join,
            "windowFunctions": self.window_functions,
            "batchIngestion": self.batch_ingestion,
            "pointInTimeRead": self.point_in_time_read,
        }

    def missing(self, required: "TimeSeriesCapabilities") -> List[str]:
        available = self.to_dict()
        return sorted(
            key
            for key, required_value in required.to_dict().items()
            if required_value and not available.get(key)
        )


@dataclass(frozen=True)
class TimeSeriesBackendDescriptor:
    backend_id: str
    adapter_name: str
    adapter_version: str
    status: str = "registered"
    contract_version: str = TIME_SERIES_CONTRACT_VERSION
    capabilities: TimeSeriesCapabilities = field(default_factory=TimeSeriesCapabilities)
    settings: Dict[str, str] = field(default_factory=dict)

    def to_dict(self, include_settings: bool = False) -> Dict[str, object]:
        payload = {
            "backendId": self.backend_id,
            "adapterName": self.adapter_name,
            "adapterVersion": self.adapter_version,
            "status": clean_status(self.status),
            "contractVersion": self.contract_version,
            "capabilities": self.capabilities.to_dict(),
        }
        if include_settings:
            payload["settings"] = dict(self.settings)
        return payload


@dataclass(frozen=True)
class TimeSeriesWatermark:
    backend_id: str
    observed_through: str
    source_event_id: str = ""
    sequence: int = 0
    status: str = "ready"
    requested_backend_id: str = ""
    effective_backend_id: str = ""
    failover_reason: str = ""
    health_checked_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class TemporalFeatureSnapshot:
    snapshot_id: str
    feature_set_version: str
    backend_id: str
    account_id: str
    as_of: str
    symbols: Tuple[str, ...]
    watermark: TimeSeriesWatermark
    windows: Dict[str, Dict[str, List[Dict[str, object]]]]
    payload_hash: str

    @classmethod
    def create(
        cls,
        backend_id: object,
        account_id: object,
        as_of: object,
        windows: Mapping[str, object],
        watermark: TimeSeriesWatermark,
        feature_set_version: str = TEMPORAL_FEATURE_SET_VERSION,
    ) -> "TemporalFeatureSnapshot":
        normalized_windows = {
            str(symbol or "").upper().strip(): {
                str(window or "").upper().strip(): [dict(row or {}) for row in rows or []]
                for window, rows in dict(window_rows or {}).items()
            }
            for symbol, window_rows in dict(windows or {}).items()
            if str(symbol or "").strip()
        }
        core = {
            "featureSetVersion": str(feature_set_version or TEMPORAL_FEATURE_SET_VERSION),
            "backendId": str(backend_id or ""),
            "accountId": str(account_id or ""),
            "asOf": str(as_of or ""),
            "watermark": watermark.to_dict(),
            "windows": normalized_windows,
        }
        digest = payload_fingerprint(core)
        return cls(
            snapshot_id="temporal-feature:" + digest[:24],
            feature_set_version=core["featureSetVersion"],
            backend_id=core["backendId"],
            account_id=core["accountId"],
            as_of=core["asOf"],
            symbols=tuple(sorted(normalized_windows)),
            watermark=watermark,
            windows=normalized_windows,
            payload_hash=digest,
        )

    def to_dict(self, include_windows: bool = True) -> Dict[str, object]:
        payload = {
            "snapshotId": self.snapshot_id,
            "featureSetVersion": self.feature_set_version,
            "backendId": self.backend_id,
            "accountId": self.account_id,
            "asOf": self.as_of,
            "symbols": list(self.symbols),
            "watermark": self.watermark.to_dict(),
            "payloadHash": self.payload_hash,
        }
        if include_windows:
            payload["windows"] = self.windows
        return payload


class TimeSeriesIngestPort(Protocol):
    def write_observations(self, observations: Iterable[Mapping[str, object]]) -> Dict[str, object]:
        ...


class TimeSeriesQueryPort(Protocol):
    def load_temporal_windows(
        self,
        account_id: str,
        symbols: Iterable[str],
        definitions: Iterable[object],
        as_of: str = "",
    ) -> Dict[str, Dict[str, List[Dict[str, object]]]]:
        ...

    def watermark(self) -> TimeSeriesWatermark:
        ...


class TimeSeriesLifecyclePort(Protocol):
    def descriptor(self) -> TimeSeriesBackendDescriptor:
        ...

    def health(self) -> Dict[str, object]:
        ...


def compare_feature_snapshots(
    active: TemporalFeatureSnapshot,
    candidate: TemporalFeatureSnapshot,
) -> Dict[str, object]:
    active_windows_hash = payload_fingerprint(semantic_feature_value(active.windows))
    candidate_windows_hash = payload_fingerprint(semantic_feature_value(candidate.windows))
    return {
        "status": "equivalent" if active_windows_hash == candidate_windows_hash else "different",
        "activeSnapshotId": active.snapshot_id,
        "candidateSnapshotId": candidate.snapshot_id,
        "activeBackendId": active.backend_id,
        "candidateBackendId": candidate.backend_id,
        "activeWindowsHash": active_windows_hash,
        "candidateWindowsHash": candidate_windows_hash,
        "symbolCount": len(set(active.symbols) | set(candidate.symbols)),
    }
