"""Use cases for versioned time-series storage and deterministic feature packets."""

import hashlib
import json
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Mapping

from ..domain.time_series_storage import (
    TEMPORAL_FEATURE_SET_VERSION,
    TemporalFeatureSnapshot,
    TimeSeriesWatermark,
    compare_feature_snapshots,
    payload_fingerprint,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def truthy(value: object) -> bool:
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "off", "disabled"}


def parsed_utc(value: object):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def healthy_backend_status(health: Mapping[str, object]) -> bool:
    return str(dict(health or {}).get("status") or "").strip().lower() in {"ready", "healthy"}


def merge_backend_health(previous: Mapping[str, object], current: Mapping[str, object]) -> Dict[str, object]:
    """Keep a durable failure streak so process restarts cannot hide an outage."""

    prior = dict(previous or {})
    observed = dict(current or {})
    stamp = str(observed.get("checkedAt") or utc_now_iso())
    if healthy_backend_status(observed):
        observed.update({
            "consecutiveFailureCount": 0,
            "firstFailureAt": "",
            "lastHealthyAt": stamp,
        })
    else:
        observed.update({
            "consecutiveFailureCount": int(prior.get("consecutiveFailureCount") or 0) + 1,
            "firstFailureAt": str(prior.get("firstFailureAt") or stamp),
            "lastHealthyAt": str(prior.get("lastHealthyAt") or ""),
        })
    observed["checkedAt"] = stamp
    return observed


def backend_runtime_resolution(
    control: Mapping[str, object],
    deployments: Iterable[Mapping[str, object]],
    health: Mapping[str, Mapping[str, object]],
) -> Dict[str, object]:
    """Describe the backend that callers can actually use, not only the requested label."""

    current_control = dict(control or {})
    requested = str(current_control.get("activeBackendId") or "mysql-primary")
    registered = {
        str(dict(item or {}).get("backendId") or "")
        for item in deployments or []
        if str(dict(item or {}).get("backendId") or "")
    }
    selected_health = dict((health or {}).get(requested) or {})
    fallback_health = dict((health or {}).get("mysql-primary") or {})
    effective = requested
    reason = "selected-backend-ready"
    if requested not in registered:
        effective = "mysql-primary"
        reason = "selected-backend-not-registered"
    elif requested != "mysql-primary" and not healthy_backend_status(selected_health):
        effective = "mysql-primary"
        reason = "selected-backend-" + str(selected_health.get("status") or "unavailable")
    return {
        "requestedBackendId": requested,
        "effectiveBackendId": effective,
        "failedOver": effective != requested,
        "reason": reason,
        "requestedBackendHealth": selected_health,
        "fallbackBackendHealth": fallback_health if effective != requested else {},
        "checkedAt": str(selected_health.get("checkedAt") or fallback_health.get("checkedAt") or utc_now_iso()),
    }


class TimeSeriesBackendPlatformService:
    """Guard candidate selection, promotion, and rollback independently of vendors."""

    def __init__(self, adapters, registry, outbox, snapshot_service, settings=None):
        self.adapters = dict(adapters or {})
        self.registry = registry
        self.outbox = outbox
        self.snapshot_service = snapshot_service
        self.settings = dict(settings or {})

    def status(self) -> Dict[str, object]:
        health = {}
        for backend_id, adapter in self.adapters.items():
            previous = {}
            getter = getattr(self.registry, "get", None)
            if callable(getter):
                previous = dict((getter(backend_id) or {}).get("health") or {})
            health[backend_id] = merge_backend_health(previous, adapter.health())
            self.registry.update_health(backend_id, health[backend_id])
        control = self.registry.control()
        deployments = self.registry.list()
        return {
            "control": control,
            "deployments": deployments,
            "health": health,
            "queue": self.outbox.summary(),
            "runtimeResolution": backend_runtime_resolution(control, deployments, health),
        }

    def queue_blockers(self, backend_id: str) -> List[str]:
        summary = self.outbox.summary()
        row = next(
            (item for item in summary.get("backends") or [] if str(item.get("backendId") or "") == backend_id),
            {},
        )
        counts = dict(row.get("counts") or {})
        blockers = []
        for status in ["queued", "processing", "retry", "failed"]:
            count = int(counts.get(status) or 0)
            if count:
                blockers.append("projection-" + status + ":" + str(count))
        return blockers

    def mark_candidate(self, backend_id: str) -> Dict[str, object]:
        backend_id = str(backend_id or "")
        if backend_id not in self.adapters:
            raise ValueError("Unknown time-series backend: " + backend_id)
        control = self.registry.control()
        if backend_id == str(control.get("activeBackendId") or ""):
            raise ValueError("The active backend cannot also be the candidate")
        health = self.adapters[backend_id].health()
        if str(health.get("status") or "").lower() not in {"ready", "healthy"}:
            return {"status": "blocked", "backendId": backend_id, "blockers": ["backend-unhealthy"], "health": health}
        self.registry.transition(backend_id, "candidate")
        next_control = self.registry.set_control(
            str(control.get("activeBackendId") or "mysql-primary"),
            str(control.get("shadowBackendId") or backend_id),
            backend_id,
            expected_version=int(control.get("version") or 0),
        )
        return {"status": "candidate", "backendId": backend_id, "control": next_control}

    def compare(self, backend_id, account_id, symbols, definitions, as_of="") -> Dict[str, object]:
        control = self.registry.control()
        active = str(control.get("activeBackendId") or "mysql-primary")
        if backend_id == active:
            raise ValueError("Candidate backend must differ from the active backend")
        return self.snapshot_service.compare(active, backend_id, account_id, symbols, definitions, as_of)

    def promotion_blockers(self, backend_id: str, comparison: Mapping[str, object]) -> List[str]:
        row = self.registry.get(backend_id)
        blockers = []
        if str(row.get("status") or "") != "candidate":
            blockers.append("backend-not-candidate")
        health = self.adapters[backend_id].health() if backend_id in self.adapters else {"status": "unavailable"}
        if str(health.get("status") or "").lower() not in {"ready", "healthy"}:
            blockers.append("backend-unhealthy")
        blockers.extend(self.queue_blockers(backend_id))
        if str(comparison.get("status") or "") != "equivalent":
            blockers.append("temporal-feature-parity-failed")
        control = self.registry.control()
        active_id = str(control.get("activeBackendId") or "")
        active_watermark = self.adapters[active_id].watermark() if active_id in self.adapters else None
        candidate_watermark = self.adapters[backend_id].watermark() if backend_id in self.adapters else None
        active_at = parsed_utc(getattr(active_watermark, "observed_through", ""))
        candidate_at = parsed_utc(getattr(candidate_watermark, "observed_through", ""))
        max_lag = max(0, int(float(self.settings.get("timeSeriesPromotionMaxWatermarkLagSeconds") or 180)))
        if not active_at or not candidate_at:
            blockers.append("watermark-unavailable")
        elif (active_at - candidate_at).total_seconds() > max_lag:
            blockers.append("watermark-lag-exceeded")
        return sorted(set(blockers))

    def promote(self, backend_id: str, comparison: Mapping[str, object]) -> Dict[str, object]:
        backend_id = str(backend_id or "")
        blockers = self.promotion_blockers(backend_id, comparison)
        if blockers:
            return {"status": "blocked", "backendId": backend_id, "blockers": blockers, "comparison": dict(comparison)}
        control = self.registry.control()
        previous = str(control.get("activeBackendId") or "")
        self.registry.transition(previous, "candidate")
        self.registry.transition(backend_id, "active")
        next_control = self.registry.set_control(
            backend_id,
            previous,
            previous,
            expected_version=int(control.get("version") or 0),
        )
        return {"status": "promoted", "previousBackendId": previous, "control": next_control, "comparison": dict(comparison)}

    def rollback(self) -> Dict[str, object]:
        control = self.registry.control()
        active = str(control.get("activeBackendId") or "")
        fallback = str(control.get("candidateBackendId") or control.get("shadowBackendId") or "")
        if not fallback or fallback not in self.adapters:
            raise ValueError("No rollback time-series backend is registered")
        health = self.adapters[fallback].health()
        if str(health.get("status") or "").lower() not in {"ready", "healthy"}:
            return {"status": "blocked", "blockers": ["rollback-backend-unhealthy"], "health": health}
        self.registry.transition(active, "candidate")
        self.registry.transition(fallback, "active")
        next_control = self.registry.set_control(
            fallback,
            active,
            active,
            expected_version=int(control.get("version") or 0),
        )
        return {"status": "rolled-back", "previousBackendId": active, "control": next_control}


class VersionedMarketTimeSeriesStore:
    """Compatibility facade with an active read path and asynchronous replicas."""

    def __init__(self, baseline_store, adapters, registry, outbox, settings=None, snapshot_store=None):
        self.baseline = baseline_store
        self.adapters = dict(adapters or {})
        self.registry = registry
        self.outbox = outbox
        self.settings = dict(settings or {})
        self.snapshot_store = snapshot_store
        self.last_feature_snapshot = None
        self._active_resolution_lock = threading.Lock()
        self._active_resolution_checked_at = 0.0
        self._active_resolution = {}

    def control(self) -> Dict[str, object]:
        return dict(self.registry.control() or {})

    def requested_active_backend_id(self) -> str:
        control = self.control()
        requested = str(control.get("activeBackendId") or self.settings.get("timeSeriesActiveBackendId") or "mysql-primary")
        return requested

    @staticmethod
    def healthy_status(health: Mapping[str, object]) -> bool:
        return healthy_backend_status(health)

    @staticmethod
    def adapter_health(adapter) -> Dict[str, object]:
        health_reader = getattr(adapter, "health", None)
        if not callable(health_reader):
            return {"status": "unavailable", "error": "backend-health-contract-missing"}
        try:
            return dict(health_reader() or {})
        except Exception as error:  # noqa: BLE001 - backend selection must preserve the canonical fallback.
            return {"status": "unavailable", "error": str(error)[:240]}

    def active_backend_resolution(self, refresh: bool = False) -> Dict[str, object]:
        requested = self.requested_active_backend_id()
        now = time.monotonic()
        try:
            cache_seconds = max(
                1,
                min(60, int(float(self.settings.get("timeSeriesRuntimeHealthCacheSeconds") or 15))),
            )
        except (TypeError, ValueError):
            cache_seconds = 15
        with self._active_resolution_lock:
            if (
                not refresh
                and self._active_resolution
                and self._active_resolution.get("requestedBackendId") == requested
                and now - self._active_resolution_checked_at < cache_seconds
            ):
                return dict(self._active_resolution)

            resolution = {
                "requestedBackendId": requested,
                "effectiveBackendId": requested,
                "failedOver": False,
                "reason": "selected-backend-ready",
                "checkedAt": utc_now_iso(),
            }
            requested_adapter = self.adapters.get(requested)
            if requested_adapter is None:
                resolution.update({
                    "effectiveBackendId": "mysql-primary",
                    "failedOver": requested != "mysql-primary",
                    "reason": "selected-backend-not-registered",
                })
            elif requested != "mysql-primary" and truthy(
                self.settings.get("timeSeriesRuntimeFailoverEnabled", "1")
            ):
                selected_health = self.adapter_health(requested_adapter)
                resolution["requestedBackendHealth"] = selected_health
                if not self.healthy_status(selected_health):
                    resolution.update({
                        "effectiveBackendId": "mysql-primary",
                        "failedOver": True,
                        "reason": "selected-backend-" + str(selected_health.get("status") or "unavailable"),
                        "fallbackBackendHealth": self.adapter_health(self.baseline),
                    })

            self._active_resolution = resolution
            self._active_resolution_checked_at = now
            return dict(resolution)

    def force_baseline_resolution(self, requested: str, reason: str, error: object = "") -> Dict[str, object]:
        resolution = {
            "requestedBackendId": str(requested or self.requested_active_backend_id()),
            "effectiveBackendId": "mysql-primary",
            "failedOver": True,
            "reason": str(reason or "selected-backend-read-failed"),
            "error": str(error or "")[:240],
            "fallbackBackendHealth": self.adapter_health(self.baseline),
            "checkedAt": utc_now_iso(),
        }
        with self._active_resolution_lock:
            self._active_resolution = resolution
            self._active_resolution_checked_at = time.monotonic()
        return dict(resolution)

    def active_backend_id(self) -> str:
        return str(self.active_backend_resolution().get("effectiveBackendId") or "mysql-primary")

    def active_adapter(self):
        return self.adapters.get(self.active_backend_id()) or self.baseline

    def projection_backend_ids(self) -> List[str]:
        if not truthy(self.settings.get("timeSeriesShadowWritesEnabled", "1")):
            return []
        result = []
        for backend_id, adapter in self.adapters.items():
            if backend_id == "mysql-primary" or adapter is self.baseline:
                continue
            if backend_id == "questdb-shadow" and not truthy(self.settings.get("timeSeriesQuestDbEnabled")):
                continue
            if truthy(self.settings.get("timeSeriesProjectionCircuitEnabled", "1")):
                getter = getattr(self.registry, "get", None)
                deployment = dict(getter(backend_id) or {}) if callable(getter) else {}
                persisted_health = dict(deployment.get("health") or {})
                if persisted_health and not self.healthy_status(persisted_health):
                    continue
            result.append(backend_id)
        return sorted(result)

    @staticmethod
    def snapshot_scope(snapshots: Iterable[object]):
        account_ids = set()
        symbols = set()
        observed_ats = set()
        materialized = list(snapshots or [])
        for snapshot in materialized:
            account_id = str(getattr(snapshot, "account_id", "") or "").strip()
            observed_at = str(getattr(snapshot, "generated_at", "") or "").strip()
            if account_id:
                account_ids.add(account_id)
            if observed_at:
                observed_ats.add(observed_at)
            for position in list(getattr(snapshot, "positions", []) or []) + list(getattr(snapshot, "watchlist", []) or []):
                symbol = str(getattr(position, "symbol", "") or "").upper().strip()
                if symbol:
                    symbols.add(symbol)
        return materialized, account_ids, symbols, observed_ats

    def enqueue_rows_with_connection(self, connection, rows: Iterable[Mapping[str, object]]) -> int:
        observations = [dict(row or {}) for row in rows or []]
        if not observations:
            return 0
        chunk_size = max(1, min(100, int(float(self.settings.get("timeSeriesProjectionPayloadRows") or 50))))
        queued = 0
        for offset in range(0, len(observations), chunk_size):
            chunk = observations[offset:offset + chunk_size]
            source_as_of = max(str(row.get("observed_at") or "") for row in chunk)
            payload = {"contractVersion": "time-series-storage-contract-v1", "observations": chunk}
            dedupe_basis = {"sourceAsOf": source_as_of, "rows": chunk}
            for backend_id in self.projection_backend_ids():
                queued += int(self.outbox.enqueue_with_connection(
                    connection,
                    backend_id=backend_id,
                    operation_name="write-observations",
                    payload=payload,
                    source_observed_at=source_as_of,
                    dedupe_key=backend_id + ":" + payload_fingerprint(dedupe_basis),
                ))
        return queued

    def enqueue_capital_flow_rows_with_connection(self, connection, rows: Iterable[Mapping[str, object]]) -> int:
        observations = [dict(row or {}) for row in rows or []]
        if not observations:
            return 0
        chunk_size = max(1, min(100, int(float(self.settings.get("timeSeriesProjectionPayloadRows") or 50))))
        queued = 0
        for offset in range(0, len(observations), chunk_size):
            chunk = observations[offset:offset + chunk_size]
            source_as_of = max(str(row.get("observed_at") or row.get("source_as_of") or "") for row in chunk)
            payload = {"contractVersion": "capital-flow-observation-v1", "observations": chunk}
            for backend_id in self.projection_backend_ids():
                queued += int(self.outbox.enqueue_with_connection(
                    connection,
                    backend_id=backend_id,
                    operation_name="write-capital-flow-observations",
                    payload=payload,
                    source_observed_at=source_as_of,
                    dedupe_key=backend_id + ":capital-flow:" + payload_fingerprint(chunk),
                ))
        return queued

    def enqueue_rows(self, rows: Iterable[Mapping[str, object]]) -> int:
        with self.outbox.transaction() as connection:
            return self.enqueue_rows_with_connection(connection, rows)

    def enqueue_capital_flow_rows(self, rows: Iterable[Mapping[str, object]]) -> int:
        with self.outbox.transaction() as connection:
            return self.enqueue_capital_flow_rows_with_connection(connection, rows)

    def record_snapshots_with_connection(self, connection, snapshots: Iterable[object]) -> Dict[str, object]:
        materialized, account_ids, symbols, observed_ats = self.snapshot_scope(snapshots)
        result = dict(self.baseline.record_snapshots_with_connection(connection, materialized) or {})
        capital_flow_rows = [dict(row or {}) for row in result.pop("_capitalFlowRows", []) or []]
        result["capitalFlowProjectionQueuedCount"] = self.enqueue_capital_flow_rows_with_connection(
            connection,
            capital_flow_rows,
        )
        if (
            not materialized
            or not account_ids
            or not symbols
            or not observed_ats
            or int(result.get("savedCount") or 0) + int(result.get("aggregateCount") or 0) <= 0
        ):
            result["projectionQueuedCount"] = 0
            result["activeBackendId"] = self.active_backend_id()
            return result
        rows = self.baseline.projectable_rows_with_connection(
            connection,
            account_ids=account_ids,
            symbols=symbols,
            observed_ats=observed_ats,
        )
        result["projectionQueuedCount"] = self.enqueue_rows_with_connection(connection, rows)
        result["activeBackendId"] = self.active_backend_id()
        return result

    def record_snapshots(self, snapshots: Iterable[object]) -> Dict[str, object]:
        with self.baseline.transaction() as connection:
            return self.record_snapshots_with_connection(connection, snapshots)

    def record_positions(self, account_id, positions, observed_at, provider="", replace=True) -> Dict[str, object]:
        materialized = list(positions or [])
        result = dict(self.baseline.record_positions(account_id, materialized, observed_at, provider, replace) or {})
        capital_flow_rows = [dict(row or {}) for row in result.pop("_capitalFlowRows", []) or []]
        result["capitalFlowProjectionQueuedCount"] = self.enqueue_capital_flow_rows(capital_flow_rows)
        symbols = [str(getattr(position, "symbol", "") or "").upper().strip() for position in materialized]
        if (
            not str(account_id or "").strip()
            or not str(observed_at or "").strip()
            or not any(symbols)
            or int(result.get("savedCount") or 0) + int(result.get("aggregateCount") or 0) <= 0
        ):
            result["projectionQueuedCount"] = 0
            result["activeBackendId"] = self.active_backend_id()
            return result
        rows = self.baseline.projectable_rows(
            account_ids=[str(account_id or "")],
            symbols=symbols,
            observed_ats=[str(observed_at or "")],
        )
        result["projectionQueuedCount"] = self.enqueue_rows(rows)
        result["activeBackendId"] = self.active_backend_id()
        return result

    def load_capital_flow_observations(self, **kwargs):
        resolution = self.active_backend_resolution()
        adapter = self.adapters.get(str(resolution.get("effectiveBackendId") or "")) or self.baseline
        reader = getattr(adapter, "load_capital_flow_observations", None)
        if not callable(reader):
            reader = getattr(self.baseline, "load_capital_flow_observations")
        try:
            return reader(**kwargs)
        except Exception as error:
            if adapter is self.baseline:
                raise
            self.force_baseline_resolution(
                str(resolution.get("requestedBackendId") or ""),
                "selected-backend-capital-flow-read-failed",
                error,
            )
            return self.baseline.load_capital_flow_observations(**kwargs)

    def capital_flow_quality(self, legacy_days: int = 30) -> Dict[str, object]:
        baseline_quality = dict(self.baseline.capital_flow_quality(legacy_days) or {})
        baseline_quality["activeBackendId"] = self.active_backend_id()
        adapter = self.active_adapter()
        if adapter is not self.baseline and callable(getattr(adapter, "capital_flow_quality", None)):
            try:
                baseline_quality["activeBackend"] = adapter.capital_flow_quality(legacy_days)
            except Exception as error:  # noqa: BLE001 - baseline quality remains available.
                baseline_quality["activeBackend"] = {"status": "unavailable", "error": str(error)[:240]}
        return baseline_quality

    def rebuild_capital_flow_from_legacy(self, limit: int = 50000) -> Dict[str, object]:
        result = dict(self.baseline.rebuild_capital_flow_from_legacy(limit) or {})
        observations = [dict(row or {}) for row in result.pop("observations", []) or []]
        result["projectionQueuedCount"] = self.enqueue_capital_flow_rows(observations)
        result["activeBackendId"] = self.active_backend_id()
        return result

    def record_daily_candles(self, candles_by_symbol, metadata_by_symbol=None, provider="toss-candles") -> Dict[str, object]:
        result = dict(self.baseline.record_daily_candles(candles_by_symbol, metadata_by_symbol, provider) or {})
        rows = [dict(row or {}) for row in result.pop("_projectedRows", []) or []]
        result["projectionQueuedCount"] = self.enqueue_rows(rows)
        result["activeBackendId"] = self.active_backend_id()
        return result

    def load_temporal_windows(self, account_id, symbols, definitions, as_of=""):
        resolution = self.active_backend_resolution()
        backend_id = str(resolution.get("effectiveBackendId") or "mysql-primary")
        adapter = self.adapters.get(backend_id) or self.baseline
        try:
            windows = adapter.load_temporal_windows(account_id, symbols, definitions, as_of)
        except Exception as error:
            if adapter is self.baseline:
                raise
            resolution = self.force_baseline_resolution(
                str(resolution.get("requestedBackendId") or ""),
                "selected-backend-temporal-read-failed",
                error,
            )
            backend_id = "mysql-primary"
            adapter = self.baseline
            windows = adapter.load_temporal_windows(account_id, symbols, definitions, as_of)
        if self.snapshot_store is not None:
            source_watermark = adapter.watermark() if callable(getattr(adapter, "watermark", None)) else TimeSeriesWatermark(
                backend_id, str(as_of or ""), status="compatibility"
            )
            watermark = TimeSeriesWatermark(
                backend_id=source_watermark.backend_id,
                observed_through=source_watermark.observed_through,
                source_event_id=source_watermark.source_event_id,
                sequence=source_watermark.sequence,
                status=source_watermark.status,
                requested_backend_id=str(resolution.get("requestedBackendId") or backend_id),
                effective_backend_id=backend_id,
                failover_reason=str(resolution.get("reason") or ""),
                health_checked_at=str(resolution.get("checkedAt") or ""),
            )
            snapshot = TemporalFeatureSnapshot.create(
                backend_id=backend_id,
                account_id=account_id,
                as_of=str(as_of or watermark.observed_through or utc_now_iso()),
                windows=windows,
                watermark=watermark,
                feature_set_version=TEMPORAL_FEATURE_SET_VERSION,
            )
            self.snapshot_store.upsert(snapshot)
            self.last_feature_snapshot = snapshot.to_dict(include_windows=False)
        return windows

    def summary(self, account_id="") -> Dict[str, object]:
        resolution = self.active_backend_resolution()
        backend_id = str(resolution.get("effectiveBackendId") or "mysql-primary")
        adapter = self.adapters.get(backend_id) or self.baseline
        active = dict(adapter.summary(account_id) or {})
        active["requestedActiveBackendId"] = str(resolution.get("requestedBackendId") or "")
        active["activeBackendId"] = backend_id
        active["runtimeFailover"] = resolution
        active["backendControl"] = self.control()
        return active

    def __getattr__(self, name):
        # Methods not in the vendor-neutral contract remain on the MySQL
        # compatibility adapter until their own ports are introduced.
        return getattr(self.baseline, name)


class TimeSeriesProjectionRunner:
    def __init__(self, adapters, outbox, registry, settings=None, worker_id=""):
        self.adapters = dict(adapters or {})
        self.outbox = outbox
        self.registry = registry
        self.settings = dict(settings or {})
        self.worker_id = worker_id or ("ts-projection-" + socket.gethostname() + "-" + str(id(self)))
        self._last_health_probe_at = {}
        self._terminal_payloads_compacted = False

    def _persisted_health(self, backend_id: str) -> Dict[str, object]:
        getter = getattr(self.registry, "get", None)
        if not callable(getter):
            return {}
        try:
            return dict((getter(backend_id) or {}).get("health") or {})
        except Exception:  # noqa: BLE001 - a health read must not stop the worker.
            return {}

    def _backend_health(self, backend_id: str, adapter) -> Dict[str, object]:
        previous = self._persisted_health(backend_id)
        probe_interval = max(
            5,
            min(300, int(float(self.settings.get("timeSeriesProjectionHealthProbeSeconds") or 30))),
        )
        now = time.monotonic()
        if (
            previous
            and backend_id in self._last_health_probe_at
            and now - float(self._last_health_probe_at[backend_id]) < probe_interval
        ):
            return previous
        health = merge_backend_health(previous, VersionedMarketTimeSeriesStore.adapter_health(adapter))
        self._last_health_probe_at[backend_id] = now
        try:
            self.registry.update_health(backend_id, health)
        except Exception:  # noqa: BLE001 - projection can still report the probe result.
            pass
        return health

    def _auto_failover(self, health_by_backend: Mapping[str, Mapping[str, object]]) -> Dict[str, object]:
        if not truthy(self.settings.get("timeSeriesAutoFailoverEnabled", "1")):
            return {"status": "disabled"}
        control = dict(self.registry.control() or {})
        active = str(control.get("activeBackendId") or "mysql-primary")
        if active == "mysql-primary":
            return {"status": "not-required", "activeBackendId": active}
        health = dict(health_by_backend.get(active) or {})
        threshold = max(1, min(10, int(float(self.settings.get("timeSeriesAutoFailoverFailureCount") or 2))))
        if healthy_backend_status(health) or int(health.get("consecutiveFailureCount") or 0) < threshold:
            return {
                "status": "waiting" if not healthy_backend_status(health) else "not-required",
                "activeBackendId": active,
                "failureCount": int(health.get("consecutiveFailureCount") or 0),
                "failureThreshold": threshold,
            }
        baseline = self.adapters.get("mysql-primary")
        baseline_health = (
            VersionedMarketTimeSeriesStore.adapter_health(baseline)
            if baseline is not None else {"status": "unavailable"}
        )
        if not healthy_backend_status(baseline_health):
            return {"status": "blocked", "reason": "fallback-backend-unhealthy", "health": baseline_health}
        failover = getattr(self.registry, "failover_active", None)
        if not callable(failover):
            return {"status": "unsupported", "reason": "atomic-registry-failover-unavailable"}
        next_control = failover(active, "mysql-primary")
        cancel = getattr(self.outbox, "cancel_backend_pending", None)
        cancelled = int(cancel(active, "projection-circuit-open:auto-failover") or 0) if callable(cancel) else 0
        health.update({
            "autoFailoverAt": utc_now_iso(),
            "autoFailoverBackendId": "mysql-primary",
            "projectionCircuitOpen": True,
        })
        try:
            self.registry.update_health(active, health)
        except Exception:  # noqa: BLE001 - control has already moved atomically.
            pass
        return {
            "status": "failed-over",
            "previousBackendId": active,
            "activeBackendId": "mysql-primary",
            "cancelledProjectionCount": cancelled,
            "control": next_control,
        }

    def run_once(self) -> Dict[str, object]:
        compacted_terminal_payloads = 0
        compact = getattr(self.outbox, "compact_terminal_payloads", None)
        if not self._terminal_payloads_compacted and callable(compact):
            compacted_terminal_payloads = int(compact() or 0)
            self._terminal_payloads_compacted = True
        candidate_ids = [key for key in self.adapters if key != "mysql-primary"]
        health_by_backend = {
            backend_id: self._backend_health(backend_id, self.adapters[backend_id])
            for backend_id in candidate_ids
        }
        failover = self._auto_failover(health_by_backend)
        circuit_enabled = truthy(self.settings.get("timeSeriesProjectionCircuitEnabled", "1"))
        backend_ids = [
            backend_id for backend_id in candidate_ids
            if not circuit_enabled or healthy_backend_status(health_by_backend.get(backend_id) or {})
        ]
        batch_size = max(1, min(200, int(float(self.settings.get("timeSeriesProjectionBatchSize") or 20))))
        lease_seconds = max(30, min(1800, int(float(self.settings.get("timeSeriesProjectionLeaseSeconds") or 120))))
        max_attempts = max(1, min(20, int(float(self.settings.get("timeSeriesProjectionMaxAttempts") or 8))))
        jobs = self.outbox.claim(backend_ids, self.worker_id, batch_size, lease_seconds)
        completed = 0
        failed = 0
        for job in jobs:
            backend_id = str(job.get("backendId") or "")
            adapter = self.adapters.get(backend_id)
            if not adapter:
                self.outbox.retry(job.get("jobId"), "adapter-not-registered", max_attempts)
                failed += 1
                continue
            try:
                operation = str(job.get("operation") or "")
                if operation == "write-capital-flow-observations":
                    adapter.write_capital_flow_observations(
                        dict(job.get("payload") or {}).get("observations") or []
                    )
                elif operation == "write-observations":
                    adapter.write_observations(dict(job.get("payload") or {}).get("observations") or [])
                else:
                    raise ValueError("Unsupported time-series projection operation: " + operation)
                self.outbox.complete(job.get("jobId"))
                completed += 1
            except Exception as error:  # noqa: BLE001 - retries isolate candidate backend failures.
                self.outbox.retry(job.get("jobId"), str(error), max_attempts)
                failed += 1
            try:
                self.registry.update_health(backend_id, adapter.health())
            except Exception:  # noqa: BLE001 - health persistence must not lose the projection receipt.
                pass
        return {
            "workerId": self.worker_id,
            "claimedCount": len(jobs),
            "completedCount": completed,
            "failedCount": failed,
            "circuitOpenBackends": sorted(set(candidate_ids) - set(backend_ids)),
            "backendHealth": health_by_backend,
            "autoFailover": failover,
            "compactedTerminalPayloadCount": compacted_terminal_payloads,
            "queue": self.outbox.summary(),
            "checkedAt": utc_now_iso(),
        }

    def enqueue_backfill(
        self,
        source_store,
        backend_id: str,
        max_rows: int = 0,
        batch_size: int = 500,
        observed_after: str = "",
    ) -> Dict[str, object]:
        if backend_id not in self.adapters or backend_id == "mysql-primary":
            raise ValueError("Backfill target must be a registered non-MySQL backend")
        configured_limit = max(10, min(100, int(float(self.settings.get("timeSeriesProjectionPayloadRows") or 50))))
        bounded_batch = max(10, min(configured_limit, int(batch_size or configured_limit)))
        bounded_max = max(0, int(max_rows or 0))
        after_key = {}
        queued = 0
        source_rows = 0
        while bounded_max == 0 or source_rows < bounded_max:
            request_limit = min(bounded_batch, bounded_max - source_rows) if bounded_max else bounded_batch
            rows = source_store.projectable_rows(
                limit=request_limit,
                after_key=after_key,
                observed_after=str(observed_after or ""),
            )
            if not rows:
                break
            source_rows += len(rows)
            last = rows[-1]
            after_key = {
                key: str(last.get(key) or "")
                for key in ["account_id", "symbol", "granularity", "bucket_at"]
            }
            payload = {"contractVersion": "time-series-storage-contract-v1", "observations": rows}
            queued += int(self.outbox.enqueue(
                backend_id=backend_id,
                operation_name="write-observations",
                payload=payload,
                source_observed_at=max(str(row.get("observed_at") or "") for row in rows),
                dedupe_key=backend_id + ":backfill:" + payload_fingerprint(rows),
            ))
            if len(rows) < request_limit:
                break
        return {
            "status": "queued",
            "backendId": backend_id,
            "sourceRowCount": source_rows,
            "queuedBatchCount": queued,
            "batchSize": bounded_batch,
            "observedAfter": str(observed_after or ""),
        }

    def watch(self) -> None:
        interval = max(1, min(60, int(float(self.settings.get("timeSeriesProjectionIntervalSeconds") or 2))))
        while True:
            result = self.run_once()
            if int(result.get("claimedCount") or 0) == 0:
                time.sleep(interval)


class TemporalFeatureSnapshotService:
    def __init__(self, adapters, snapshot_store):
        self.adapters = dict(adapters or {})
        self.snapshot_store = snapshot_store

    def create(self, backend_id, account_id, symbols, definitions, as_of="") -> TemporalFeatureSnapshot:
        adapter = self.adapters[str(backend_id)]
        effective_as_of = str(as_of or utc_now_iso())
        windows = adapter.load_temporal_windows(account_id, symbols, definitions, effective_as_of)
        watermark = adapter.watermark()
        snapshot = TemporalFeatureSnapshot.create(
            backend_id=backend_id,
            account_id=account_id,
            as_of=effective_as_of,
            windows=windows,
            watermark=watermark,
            feature_set_version=TEMPORAL_FEATURE_SET_VERSION,
        )
        self.snapshot_store.upsert(snapshot)
        return snapshot

    def compare(self, active_backend_id, candidate_backend_id, account_id, symbols, definitions, as_of=""):
        effective_as_of = str(as_of or utc_now_iso())
        active = self.create(active_backend_id, account_id, symbols, definitions, effective_as_of)
        candidate = self.create(candidate_backend_id, account_id, symbols, definitions, effective_as_of)
        return compare_feature_snapshots(active, candidate)
