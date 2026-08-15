"""MySQL control-plane stores for versioned engines and temporal backends."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Mapping, Optional

from ..domain.reasoning_engine_versions import (
    EngineControlState,
    EngineReleaseBundle,
    ReasoningEngineDescriptor,
    engine_status,
    engine_transition_allowed,
)
from ..domain.time_series_storage import (
    TemporalFeatureSnapshot,
    TimeSeriesBackendDescriptor,
    TimeSeriesCapabilities,
    backend_transition_allowed,
    canonical_json,
    clean_status,
    payload_fingerprint,
)
from .mysql_operational_connection import MySQLOperationalConnection


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: Optional[datetime] = None) -> str:
    return (value or utc_now()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def json_value(value: object, fallback):
    if isinstance(value, type(fallback)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


class MySQLTimeSeriesBackendRegistryStore(MySQLOperationalConnection):
    def upsert(self, descriptor: TimeSeriesBackendDescriptor) -> None:
        stamp = iso_utc()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO time_series_backend_deployments (
                    backend_id, adapter_name, adapter_version, deployment_status,
                    contract_version, capabilities_json, settings_json,
                    last_health_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, '{}', %s, %s)
                ON DUPLICATE KEY UPDATE
                    adapter_name = VALUES(adapter_name),
                    adapter_version = VALUES(adapter_version),
                    contract_version = VALUES(contract_version),
                    capabilities_json = VALUES(capabilities_json),
                    settings_json = VALUES(settings_json),
                    updated_at = VALUES(updated_at)
                """,
                (
                    descriptor.backend_id,
                    descriptor.adapter_name,
                    descriptor.adapter_version,
                    clean_status(descriptor.status),
                    descriptor.contract_version,
                    canonical_json(descriptor.capabilities.to_dict()),
                    canonical_json(descriptor.settings),
                    stamp,
                    stamp,
                ),
            )

    def list(self) -> List[Dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM time_series_backend_deployments ORDER BY created_at, backend_id"
            ).fetchall()
        return [self.row_payload(row) for row in rows or []]

    def get(self, backend_id: str) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM time_series_backend_deployments WHERE backend_id = %s",
                (str(backend_id or ""),),
            ).fetchone()
        return self.row_payload(row) if row else {}

    def transition(self, backend_id: str, target_status: str) -> Dict[str, object]:
        target = clean_status(target_status)
        stamp = iso_utc()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT deployment_status FROM time_series_backend_deployments WHERE backend_id = %s FOR UPDATE",
                (str(backend_id or ""),),
            ).fetchone()
            if not row:
                raise ValueError("Unknown time-series backend: " + str(backend_id or ""))
            current = clean_status(row.get("deployment_status"))
            if not backend_transition_allowed(current, target):
                raise ValueError("Invalid time-series backend transition: " + current + " -> " + target)
            connection.execute(
                "UPDATE time_series_backend_deployments SET deployment_status = %s, updated_at = %s WHERE backend_id = %s",
                (target, stamp, str(backend_id or "")),
            )
        return self.get(backend_id)

    def update_health(self, backend_id: str, health: Mapping[str, object]) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE time_series_backend_deployments SET last_health_json = %s, updated_at = %s WHERE backend_id = %s",
                (canonical_json(dict(health or {})), iso_utc(), str(backend_id or "")),
            )

    def control(self) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM time_series_backend_control WHERE control_id = 'global'"
            ).fetchone()
        row = dict(row or {})
        return {
            "activeBackendId": str(row.get("active_backend_id") or ""),
            "shadowBackendId": str(row.get("shadow_backend_id") or ""),
            "candidateBackendId": str(row.get("candidate_backend_id") or ""),
            "version": int(row.get("version") or 0),
            "updatedAt": str(row.get("updated_at") or ""),
        }

    def set_control(
        self,
        active_backend_id: str,
        shadow_backend_id: str = "",
        candidate_backend_id: str = "",
        expected_version: Optional[int] = None,
    ) -> Dict[str, object]:
        stamp = iso_utc()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT version FROM time_series_backend_control WHERE control_id = 'global' FOR UPDATE"
            ).fetchone() or {"version": 0}
            version = int(row.get("version") or 0)
            if expected_version is not None and version != int(expected_version):
                raise RuntimeError("Time-series backend control changed concurrently")
            known = {
                str(item.get("backend_id") or "")
                for item in connection.execute(
                    "SELECT backend_id FROM time_series_backend_deployments"
                ).fetchall()
            }
            requested = [active_backend_id, shadow_backend_id, candidate_backend_id]
            unknown = [value for value in requested if value and value not in known]
            if unknown:
                raise ValueError("Unknown time-series backend: " + ", ".join(unknown))
            connection.execute(
                """
                UPDATE time_series_backend_control
                SET active_backend_id = %s, shadow_backend_id = %s,
                    candidate_backend_id = %s, version = %s, updated_at = %s
                WHERE control_id = 'global'
                """,
                (active_backend_id, shadow_backend_id, candidate_backend_id, version + 1, stamp),
            )
        return self.control()

    @staticmethod
    def row_payload(row: Mapping[str, object]) -> Dict[str, object]:
        values = dict(row or {})
        return {
            "backendId": str(values.get("backend_id") or ""),
            "adapterName": str(values.get("adapter_name") or ""),
            "adapterVersion": str(values.get("adapter_version") or ""),
            "status": clean_status(values.get("deployment_status")),
            "contractVersion": str(values.get("contract_version") or ""),
            "capabilities": json_value(values.get("capabilities_json"), {}),
            "settings": json_value(values.get("settings_json"), {}),
            "health": json_value(values.get("last_health_json"), {}),
            "createdAt": str(values.get("created_at") or ""),
            "updatedAt": str(values.get("updated_at") or ""),
        }


class MySQLReasoningEngineRegistryStore(MySQLOperationalConnection):
    def upsert(self, descriptor: ReasoningEngineDescriptor) -> None:
        stamp = iso_utc()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO reasoning_engine_deployments (
                    deployment_id, engine_family, engine_version,
                    deployment_status, graph_store_binding,
                    time_series_backend_id, release_bundle_json,
                    capabilities_json, last_health_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '{}', %s, %s)
                ON DUPLICATE KEY UPDATE
                    graph_store_binding = VALUES(graph_store_binding),
                    time_series_backend_id = VALUES(time_series_backend_id),
                    release_bundle_json = VALUES(release_bundle_json),
                    capabilities_json = VALUES(capabilities_json),
                    updated_at = VALUES(updated_at)
                """,
                (
                    descriptor.deployment_id,
                    descriptor.engine_family,
                    descriptor.engine_version,
                    engine_status(descriptor.status),
                    descriptor.graph_store_binding,
                    descriptor.time_series_backend_id,
                    canonical_json(descriptor.release_bundle.to_dict()),
                    canonical_json(descriptor.capabilities),
                    stamp,
                    stamp,
                ),
            )

    def list(self) -> List[Dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM reasoning_engine_deployments ORDER BY created_at, deployment_id"
            ).fetchall()
        return [self.row_payload(row) for row in rows or []]

    def get(self, deployment_id: str) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM reasoning_engine_deployments WHERE deployment_id = %s",
                (str(deployment_id or ""),),
            ).fetchone()
        return self.row_payload(row) if row else {}

    def transition(self, deployment_id: str, target_status: str) -> Dict[str, object]:
        target = engine_status(target_status)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT deployment_status FROM reasoning_engine_deployments WHERE deployment_id = %s FOR UPDATE",
                (str(deployment_id or ""),),
            ).fetchone()
            if not row:
                raise ValueError("Unknown reasoning engine deployment: " + str(deployment_id or ""))
            current = engine_status(row.get("deployment_status"))
            if not engine_transition_allowed(current, target):
                raise ValueError("Invalid reasoning engine transition: " + current + " -> " + target)
            connection.execute(
                "UPDATE reasoning_engine_deployments SET deployment_status = %s, updated_at = %s WHERE deployment_id = %s",
                (target, iso_utc(), str(deployment_id or "")),
            )
        return self.get(deployment_id)

    def control(self) -> EngineControlState:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM reasoning_engine_control WHERE control_id = 'global'"
            ).fetchone() or {}
        return EngineControlState(
            active_deployment_id=str(row.get("active_deployment_id") or ""),
            delivery_deployment_id=str(row.get("delivery_deployment_id") or ""),
            candidate_deployment_id=str(row.get("candidate_deployment_id") or ""),
            version=int(row.get("version") or 0),
        )

    def set_control(
        self,
        active_deployment_id: str,
        delivery_deployment_id: str,
        candidate_deployment_id: str = "",
        expected_version: Optional[int] = None,
    ) -> EngineControlState:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT version FROM reasoning_engine_control WHERE control_id = 'global' FOR UPDATE"
            ).fetchone() or {"version": 0}
            version = int(row.get("version") or 0)
            if expected_version is not None and version != int(expected_version):
                raise RuntimeError("Reasoning engine control changed concurrently")
            known = {
                str(item.get("deployment_id") or "")
                for item in connection.execute(
                    "SELECT deployment_id FROM reasoning_engine_deployments"
                ).fetchall()
            }
            requested = [active_deployment_id, delivery_deployment_id, candidate_deployment_id]
            unknown = [value for value in requested if value and value not in known]
            if unknown:
                raise ValueError("Unknown reasoning engine deployment: " + ", ".join(unknown))
            connection.execute(
                """
                UPDATE reasoning_engine_control
                SET active_deployment_id = %s, delivery_deployment_id = %s,
                    candidate_deployment_id = %s, version = %s, updated_at = %s
                WHERE control_id = 'global'
                """,
                (
                    active_deployment_id,
                    delivery_deployment_id,
                    candidate_deployment_id,
                    version + 1,
                    iso_utc(),
                ),
            )
        return self.control()

    def update_health(self, deployment_id: str, health: Mapping[str, object]) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE reasoning_engine_deployments SET last_health_json = %s, updated_at = %s WHERE deployment_id = %s",
                (canonical_json(dict(health or {})), iso_utc(), str(deployment_id or "")),
            )

    @staticmethod
    def row_payload(row: Mapping[str, object]) -> Dict[str, object]:
        values = dict(row or {})
        return {
            "deploymentId": str(values.get("deployment_id") or ""),
            "engineFamily": str(values.get("engine_family") or ""),
            "engineVersion": str(values.get("engine_version") or ""),
            "status": engine_status(values.get("deployment_status")),
            "graphStoreBinding": str(values.get("graph_store_binding") or ""),
            "timeSeriesBackendId": str(values.get("time_series_backend_id") or ""),
            "releaseBundle": json_value(values.get("release_bundle_json"), {}),
            "capabilities": json_value(values.get("capabilities_json"), {}),
            "health": json_value(values.get("last_health_json"), {}),
            "createdAt": str(values.get("created_at") or ""),
            "updatedAt": str(values.get("updated_at") or ""),
        }


class MySQLTimeSeriesProjectionOutboxStore(MySQLOperationalConnection):
    def enqueue_with_connection(
        self,
        connection,
        backend_id: str,
        operation_name: str,
        payload: Mapping[str, object],
        source_event_id: str = "",
        source_observed_at: str = "",
        dedupe_key: str = "",
    ) -> bool:
        clean_backend = str(backend_id or "").strip()
        if not clean_backend:
            return False
        clean_payload = dict(payload or {})
        dedupe = str(dedupe_key or "").strip() or payload_fingerprint({
            "backendId": clean_backend,
            "operation": str(operation_name or "write-observations"),
            "payload": clean_payload,
        })
        job_id = "ts-job:" + uuid.uuid4().hex
        stamp = iso_utc()
        cursor = connection.execute(
            """
            INSERT IGNORE INTO time_series_projection_outbox (
                job_id, backend_id, dedupe_key, operation_name,
                source_event_id, source_observed_at, payload_json,
                job_status, available_at, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued', %s, %s, %s)
            """,
            (
                job_id,
                clean_backend,
                dedupe[:191],
                str(operation_name or "write-observations")[:64],
                str(source_event_id or "")[:191],
                str(source_observed_at or "")[:40],
                canonical_json(clean_payload),
                stamp,
                stamp,
                stamp,
            ),
        )
        return bool(int(getattr(cursor, "rowcount", 0) or 0))

    def enqueue(self, **kwargs) -> bool:
        with self.transaction() as connection:
            return self.enqueue_with_connection(connection, **kwargs)

    def claim(
        self,
        backend_ids: Iterable[str],
        worker_id: str,
        limit: int = 20,
        lease_seconds: int = 120,
    ) -> List[Dict[str, object]]:
        backends = sorted({str(value or "").strip() for value in backend_ids or [] if str(value or "").strip()})
        if not backends:
            return []
        stamp = iso_utc()
        lease_until = iso_utc(utc_now() + timedelta(seconds=max(30, int(lease_seconds or 120))))
        placeholders = ",".join(["%s"] * len(backends))
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM time_series_projection_outbox "
                "WHERE backend_id IN (" + placeholders + ") "
                "AND job_status IN ('queued', 'retry') "
                "AND (available_at = '' OR available_at <= %s) "
                "AND (lease_until = '' OR lease_until < %s) "
                "ORDER BY created_at, job_id LIMIT %s FOR UPDATE SKIP LOCKED",
                (*backends, stamp, stamp, max(1, min(200, int(limit or 20)))),
            ).fetchall()
            job_ids = [str(row.get("job_id") or "") for row in rows or [] if str(row.get("job_id") or "")]
            if not job_ids:
                return []
            id_placeholders = ",".join(["%s"] * len(job_ids))
            connection.execute(
                "UPDATE time_series_projection_outbox "
                "SET job_status = 'processing', lease_owner = %s, lease_until = %s, updated_at = %s "
                "WHERE job_id IN (" + id_placeholders + ")",
                (str(worker_id or "time-series-projection"), lease_until, stamp, *job_ids),
            )
        return [self.row_payload(row) for row in rows or []]

    def complete(self, job_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE time_series_projection_outbox
                SET job_status = 'completed', lease_owner = '', lease_until = '',
                    last_error = '', updated_at = %s
                WHERE job_id = %s
                """,
                (iso_utc(), str(job_id or "")),
            )

    def defer(self, job_id: str, reason: str, retry_after_seconds: int = 30) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE time_series_projection_outbox
                SET job_status = 'queued', lease_owner = '', lease_until = '',
                    available_at = %s, last_error = %s, updated_at = %s
                WHERE job_id = %s
                """,
                (
                    iso_utc(utc_now() + timedelta(seconds=max(1, int(retry_after_seconds or 30)))),
                    str(reason or "")[:255],
                    iso_utc(),
                    str(job_id or ""),
                ),
            )

    def retry(self, job_id: str, error: str, max_attempts: int = 8) -> Dict[str, object]:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT attempt_count FROM time_series_projection_outbox WHERE job_id = %s FOR UPDATE",
                (str(job_id or ""),),
            ).fetchone() or {"attempt_count": 0}
            attempts = int(row.get("attempt_count") or 0) + 1
            terminal = attempts >= max(1, int(max_attempts or 8))
            delay = min(900, 5 * (2 ** min(7, attempts - 1)))
            connection.execute(
                """
                UPDATE time_series_projection_outbox
                SET job_status = %s, attempt_count = %s, lease_owner = '', lease_until = '',
                    available_at = %s, last_error = %s, updated_at = %s
                WHERE job_id = %s
                """,
                (
                    "failed" if terminal else "retry",
                    attempts,
                    "" if terminal else iso_utc(utc_now() + timedelta(seconds=delay)),
                    str(error or "")[:255],
                    iso_utc(),
                    str(job_id or ""),
                ),
            )
        return {"jobId": str(job_id or ""), "attemptCount": attempts, "terminal": terminal}

    def summary(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT backend_id, job_status, COUNT(*) AS row_count, MIN(created_at) AS oldest "
                "FROM time_series_projection_outbox GROUP BY backend_id, job_status"
            ).fetchall()
        by_backend: Dict[str, Dict[str, object]] = {}
        for row in rows or []:
            backend = str(row.get("backend_id") or "")
            status = str(row.get("job_status") or "")
            entry = by_backend.setdefault(backend, {"backendId": backend, "counts": {}, "oldest": {}})
            entry["counts"][status] = int(row.get("row_count") or 0)
            entry["oldest"][status] = str(row.get("oldest") or "")
        return {"backends": list(by_backend.values())}

    @staticmethod
    def row_payload(row: Mapping[str, object]) -> Dict[str, object]:
        values = dict(row or {})
        return {
            "jobId": str(values.get("job_id") or ""),
            "backendId": str(values.get("backend_id") or ""),
            "dedupeKey": str(values.get("dedupe_key") or ""),
            "operation": str(values.get("operation_name") or ""),
            "sourceEventId": str(values.get("source_event_id") or ""),
            "sourceObservedAt": str(values.get("source_observed_at") or ""),
            "payload": json_value(values.get("payload_json"), {}),
            "status": str(values.get("job_status") or ""),
            "attemptCount": int(values.get("attempt_count") or 0),
            "createdAt": str(values.get("created_at") or ""),
            "updatedAt": str(values.get("updated_at") or ""),
        }


class MySQLTemporalFeatureSnapshotStore(MySQLOperationalConnection):
    def upsert(self, snapshot: TemporalFeatureSnapshot) -> bool:
        stamp = iso_utc()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT IGNORE INTO temporal_feature_snapshots (
                    snapshot_id, feature_set_version, backend_id, account_id,
                    as_of, watermark_json, symbols_json, windows_json,
                    payload_hash, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.feature_set_version,
                    snapshot.backend_id,
                    snapshot.account_id,
                    snapshot.as_of,
                    canonical_json(snapshot.watermark.to_dict()),
                    canonical_json(list(snapshot.symbols)),
                    canonical_json(snapshot.windows),
                    snapshot.payload_hash,
                    stamp,
                ),
            )
        return bool(int(getattr(cursor, "rowcount", 0) or 0))

    def latest(self, account_id: str, backend_id: str = "") -> Dict[str, object]:
        clauses = ["account_id = %s"]
        params: List[object] = [str(account_id or "")]
        if backend_id:
            clauses.append("backend_id = %s")
            params.append(str(backend_id))
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM temporal_feature_snapshots WHERE " + " AND ".join(clauses)
                + " ORDER BY as_of DESC, created_at DESC LIMIT 1",
                params,
            ).fetchone()
        if not row:
            return {}
        return {
            "snapshotId": str(row.get("snapshot_id") or ""),
            "featureSetVersion": str(row.get("feature_set_version") or ""),
            "backendId": str(row.get("backend_id") or ""),
            "accountId": str(row.get("account_id") or ""),
            "asOf": str(row.get("as_of") or ""),
            "watermark": json_value(row.get("watermark_json"), {}),
            "symbols": json_value(row.get("symbols_json"), []),
            "windows": json_value(row.get("windows_json"), {}),
            "payloadHash": str(row.get("payload_hash") or ""),
            "createdAt": str(row.get("created_at") or ""),
        }
