"""Use cases for versioned time-series storage and deterministic feature packets."""

import hashlib
import json
import socket
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
            health[backend_id] = adapter.health()
            self.registry.update_health(backend_id, health[backend_id])
        return {
            "control": self.registry.control(),
            "deployments": self.registry.list(),
            "health": health,
            "queue": self.outbox.summary(),
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

    def control(self) -> Dict[str, object]:
        return dict(self.registry.control() or {})

    def active_backend_id(self) -> str:
        control = self.control()
        requested = str(control.get("activeBackendId") or self.settings.get("timeSeriesActiveBackendId") or "mysql-primary")
        return requested if requested in self.adapters else "mysql-primary"

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

    def enqueue_rows(self, rows: Iterable[Mapping[str, object]]) -> int:
        with self.outbox.transaction() as connection:
            return self.enqueue_rows_with_connection(connection, rows)

    def record_snapshots_with_connection(self, connection, snapshots: Iterable[object]) -> Dict[str, object]:
        materialized, account_ids, symbols, observed_ats = self.snapshot_scope(snapshots)
        result = dict(self.baseline.record_snapshots_with_connection(connection, materialized) or {})
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

    def record_daily_candles(self, candles_by_symbol, metadata_by_symbol=None, provider="toss-candles") -> Dict[str, object]:
        result = dict(self.baseline.record_daily_candles(candles_by_symbol, metadata_by_symbol, provider) or {})
        rows = [dict(row or {}) for row in result.pop("_projectedRows", []) or []]
        result["projectionQueuedCount"] = self.enqueue_rows(rows)
        result["activeBackendId"] = self.active_backend_id()
        return result

    def load_temporal_windows(self, account_id, symbols, definitions, as_of=""):
        adapter = self.active_adapter()
        windows = adapter.load_temporal_windows(account_id, symbols, definitions, as_of)
        if self.snapshot_store is not None:
            watermark = adapter.watermark() if callable(getattr(adapter, "watermark", None)) else TimeSeriesWatermark(
                self.active_backend_id(), str(as_of or ""), status="compatibility"
            )
            snapshot = TemporalFeatureSnapshot.create(
                backend_id=self.active_backend_id(),
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
        active = dict(self.active_adapter().summary(account_id) or {})
        active["activeBackendId"] = self.active_backend_id()
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

    def run_once(self) -> Dict[str, object]:
        backend_ids = [key for key in self.adapters if key != "mysql-primary"]
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
                if operation != "write-observations":
                    raise ValueError("Unsupported time-series projection operation: " + operation)
                adapter.write_observations(dict(job.get("payload") or {}).get("observations") or [])
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
