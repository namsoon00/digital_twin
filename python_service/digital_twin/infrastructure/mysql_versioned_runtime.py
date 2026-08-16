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
from ..domain.reasoning_shadow import reasoning_comparison_summary
from ..domain.independent_reasoning import independent_reasoning_request
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


class MySQLReasoningShadowJobStore(MySQLOperationalConnection):
    """Durable latest-state handoff from the delivery engine to V2."""

    def enqueue(
        self,
        baseline_deployment_id: str,
        candidate_deployment_id: str,
        source_event_id: str,
        scope_key: str,
        dedupe_key: str,
        payload: Mapping[str, object],
    ) -> Dict[str, object]:
        stamp = iso_utc()
        job_id = "reasoning-shadow:" + uuid.uuid4().hex
        clean_scope = str(scope_key or "global")[:191]
        clean_candidate = str(candidate_deployment_id or "")[:191]
        clean_release_id = str(dict(payload or {}).get("candidateReleaseId") or "")[:191]
        clean_runtime_revision = str(
            dict(payload or {}).get("candidateRuntimeRevision") or ""
        )[:64]
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT IGNORE INTO reasoning_engine_shadow_jobs (
                    job_id, dedupe_key, scope_key, baseline_deployment_id,
                    candidate_deployment_id, candidate_release_id,
                    candidate_runtime_revision, source_event_id, payload_json,
                    job_status, available_at, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued', %s, %s, %s)
                """,
                (
                    job_id,
                    str(dedupe_key or "")[:191],
                    clean_scope,
                    str(baseline_deployment_id or "")[:191],
                    clean_candidate,
                    clean_release_id,
                    clean_runtime_revision,
                    str(source_event_id or "")[:191],
                    canonical_json(dict(payload or {})),
                    stamp,
                    stamp,
                    stamp,
                ),
            )
            saved = bool(int(getattr(cursor, "rowcount", 0) or 0))
            if saved:
                connection.execute(
                    """
                    UPDATE reasoning_engine_shadow_jobs
                    SET job_status = 'superseded', completed_at = %s,
                        lease_owner = '', lease_expires_at = '', updated_at = %s,
                        last_error = 'A newer immutable shadow input owns this scope.'
                    WHERE candidate_deployment_id = %s AND scope_key = %s
                      AND job_id <> %s AND job_status IN ('queued', 'retry')
                    """,
                    (stamp, stamp, clean_candidate, clean_scope, job_id),
                )
        return {"saved": saved, "jobId": job_id if saved else "", "status": "queued" if saved else "duplicate"}

    def claim(
        self,
        candidate_deployment_id: str,
        worker_id: str,
        lease_seconds: int = 3600,
        candidate_release_id: str = "",
        candidate_runtime_revision: str = "",
    ) -> Dict[str, object]:
        stamp = iso_utc()
        lease_until = iso_utc(utc_now() + timedelta(seconds=max(60, int(lease_seconds or 3600))))
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE reasoning_engine_shadow_jobs
                SET job_status = 'retry', lease_owner = '', lease_expires_at = '',
                    available_at = %s,
                    last_error = 'The previous shadow worker lease expired; retrying safely.',
                    updated_at = %s
                WHERE candidate_deployment_id = %s AND job_status = 'processing'
                  AND lease_expires_at <> '' AND lease_expires_at < %s
                """,
                (stamp, stamp, str(candidate_deployment_id or ""), stamp),
            )
            filters = ""
            params = [str(candidate_deployment_id or "")]
            if str(candidate_release_id or ""):
                filters += " AND candidate_release_id = %s"
                params.append(str(candidate_release_id or ""))
            if str(candidate_runtime_revision or ""):
                filters += " AND candidate_runtime_revision = %s"
                params.append(str(candidate_runtime_revision or ""))
            row = connection.execute(
                """
                SELECT * FROM reasoning_engine_shadow_jobs
                WHERE candidate_deployment_id = %s
                """ + filters + """
                  AND job_status IN ('queued', 'retry')
                  AND (available_at = '' OR available_at <= %s)
                  AND (lease_expires_at = '' OR lease_expires_at < %s)
                ORDER BY created_at, job_id
                LIMIT 1 FOR UPDATE SKIP LOCKED
                """,
                (*params, stamp, stamp),
            ).fetchone()
            if not row:
                return {}
            job_id = str(row.get("job_id") or "")
            connection.execute(
                """
                UPDATE reasoning_engine_shadow_jobs
                SET job_status = 'processing', lease_owner = %s,
                    lease_expires_at = %s, updated_at = %s
                WHERE job_id = %s
                """,
                (str(worker_id or "reasoning-shadow"), lease_until, stamp, job_id),
            )
        return self.row_payload(row)

    def complete(self, job_id: str) -> None:
        stamp = iso_utc()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE reasoning_engine_shadow_jobs
                SET job_status = 'completed', lease_owner = '', lease_expires_at = '',
                    last_error = '', completed_at = %s, updated_at = %s
                WHERE job_id = %s
                """,
                (stamp, stamp, str(job_id or "")),
            )

    def defer(self, job_id: str, reason: str, retry_after_seconds: int = 30) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE reasoning_engine_shadow_jobs
                SET job_status = 'queued', lease_owner = '', lease_expires_at = '',
                    available_at = %s, last_error = %s, updated_at = %s
                WHERE job_id = %s
                """,
                (
                    iso_utc(utc_now() + timedelta(seconds=max(1, int(retry_after_seconds or 30)))),
                    str(reason or "")[:500],
                    iso_utc(),
                    str(job_id or ""),
                ),
            )

    def discard(self, job_id: str, reason: str) -> None:
        stamp = iso_utc()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE reasoning_engine_shadow_jobs
                SET job_status = 'failed', lease_owner = '', lease_expires_at = '',
                    available_at = '', last_error = %s, completed_at = %s,
                    updated_at = %s
                WHERE job_id = %s
                """,
                (str(reason or "")[:500], stamp, stamp, str(job_id or "")),
            )

    def retry(self, job_id: str, error: str, max_attempts: int = 5) -> Dict[str, object]:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT attempts FROM reasoning_engine_shadow_jobs WHERE job_id = %s FOR UPDATE",
                (str(job_id or ""),),
            ).fetchone() or {"attempts": 0}
            attempts = int(row.get("attempts") or 0) + 1
            terminal = attempts >= max(1, int(max_attempts or 5))
            delay = min(1800, 15 * (2 ** min(6, attempts - 1)))
            connection.execute(
                """
                UPDATE reasoning_engine_shadow_jobs
                SET job_status = %s, attempts = %s, lease_owner = '',
                    lease_expires_at = '', available_at = %s, last_error = %s,
                    completed_at = %s, updated_at = %s
                WHERE job_id = %s
                """,
                (
                    "failed" if terminal else "retry",
                    attempts,
                    "" if terminal else iso_utc(utc_now() + timedelta(seconds=delay)),
                    str(error or "")[:500],
                    iso_utc() if terminal else "",
                    iso_utc(),
                    str(job_id or ""),
                ),
            )
        return {"jobId": str(job_id or ""), "attemptCount": attempts, "terminal": terminal}

    def summary(
        self,
        candidate_deployment_id: str = "",
        candidate_release_id: str = "",
        candidate_runtime_revision: str = "",
    ) -> Dict[str, object]:
        filters = []
        params = []
        if str(candidate_deployment_id or ""):
            filters.append("candidate_deployment_id = %s")
            params.append(str(candidate_deployment_id or ""))
        if str(candidate_release_id or ""):
            filters.append("candidate_release_id = %s")
            params.append(str(candidate_release_id or ""))
        if str(candidate_runtime_revision or ""):
            filters.append("candidate_runtime_revision = %s")
            params.append(str(candidate_runtime_revision or ""))
        where_sql = " WHERE " + " AND ".join(filters) if filters else ""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT candidate_deployment_id, candidate_release_id,
                       candidate_runtime_revision, job_status, COUNT(*) AS row_count,
                       MIN(created_at) AS oldest, MAX(updated_at) AS latest
                FROM reasoning_engine_shadow_jobs
                """ + where_sql + """
                GROUP BY candidate_deployment_id, candidate_release_id,
                         candidate_runtime_revision, job_status
                """,
                tuple(params),
            ).fetchall()
        deployments: Dict[str, Dict[str, object]] = {}
        for row in rows or []:
            deployment_id = str(row.get("candidate_deployment_id") or "")
            release_id = str(row.get("candidate_release_id") or "")
            runtime_revision = str(row.get("candidate_runtime_revision") or "")
            deployment_key = "|".join([deployment_id, release_id, runtime_revision])
            status = str(row.get("job_status") or "")
            item = deployments.setdefault(deployment_key, {
                "candidateDeploymentId": deployment_id,
                "candidateReleaseId": release_id,
                "candidateRuntimeRevision": runtime_revision,
                "counts": {},
                "oldest": {},
                "latest": {},
            })
            item["counts"][status] = int(row.get("row_count") or 0)
            item["oldest"][status] = str(row.get("oldest") or "")
            item["latest"][status] = str(row.get("latest") or "")
        return {"deployments": list(deployments.values())}

    @staticmethod
    def row_payload(row: Mapping[str, object]) -> Dict[str, object]:
        values = dict(row or {})
        return {
            "jobId": str(values.get("job_id") or ""),
            "dedupeKey": str(values.get("dedupe_key") or ""),
            "scopeKey": str(values.get("scope_key") or ""),
            "baselineDeploymentId": str(values.get("baseline_deployment_id") or ""),
            "candidateDeploymentId": str(values.get("candidate_deployment_id") or ""),
            "candidateReleaseId": str(values.get("candidate_release_id") or ""),
            "candidateRuntimeRevision": str(values.get("candidate_runtime_revision") or ""),
            "sourceEventId": str(values.get("source_event_id") or ""),
            "payload": json_value(values.get("payload_json"), {}),
            "status": str(values.get("job_status") or ""),
            "attemptCount": int(values.get("attempts") or 0),
            "createdAt": str(values.get("created_at") or ""),
            "updatedAt": str(values.get("updated_at") or ""),
        }


class MySQLReasoningEngineJobStore(MySQLOperationalConnection):
    """Direct source-event queue for independently executable engines."""

    @staticmethod
    def target_deployments_with_connection(connection) -> List[str]:
        control = connection.execute(
            "SELECT active_deployment_id, delivery_deployment_id, candidate_deployment_id "
            "FROM reasoning_engine_control WHERE control_id = 'global'"
        ).fetchone() or {}
        requested = list(dict.fromkeys(
            str(control.get(key) or "").strip()
            for key in ["active_deployment_id", "delivery_deployment_id", "candidate_deployment_id"]
            if str(control.get(key) or "").strip()
        ))
        deployments = []
        if requested:
            placeholders = ",".join(["%s"] * len(requested))
            rows = connection.execute(
                "SELECT deployment_id FROM reasoning_engine_deployments "
                "WHERE engine_version = 'v2' AND deployment_id IN (" + placeholders + ")",
                tuple(requested),
            ).fetchall()
            deployments = [
                str(row.get("deployment_id") or "").strip()
                for row in rows or []
                if str(row.get("deployment_id") or "").strip()
            ]
        # Engine registration and source ingestion can race during a cold
        # service start. The stable V2 slot preserves the source event until
        # the independent worker initializes its deployment descriptor.
        return sorted(set(deployments or ["ontology-v2-shadow"]))

    @staticmethod
    def event_priority(event) -> int:
        payload = dict(getattr(event, "payload", {}) or {})
        level = str(payload.get("reviewLevel") or payload.get("materialityReviewLevel") or "").lower()
        return {"critical": 100, "urgent": 90, "caution": 80, "check": 70, "observe": 50}.get(level, 60)

    @classmethod
    def ingress_event_with_connection(cls, connection, event) -> Dict[str, object]:
        deployments = cls.target_deployments_with_connection(connection)
        stamp = iso_utc()
        saved_jobs = []
        superseded = 0
        for deployment_id in deployments:
            request = independent_reasoning_request(deployment_id, [event])
            job_id = "reasoning-engine-job:" + uuid.uuid4().hex
            cursor = connection.execute(
                """
                INSERT IGNORE INTO reasoning_engine_jobs (
                    job_id, deployment_id, source_event_id, scope_key,
                    input_fingerprint, request_json, result_json, job_status,
                    priority, supersedable, available_at, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, '{}', 'queued', %s, %s, %s, %s, %s)
                """,
                (
                    job_id,
                    str(deployment_id or "")[:191],
                    str(getattr(event, "event_id", "") or "")[:191],
                    request.scope_id[:191],
                    request.input_fingerprint[:64],
                    canonical_json({
                        "request": request.to_dict(),
                        "sourceEvent": event.to_dict(),
                    }),
                    cls.event_priority(event),
                    1 if request.supersedable else 0,
                    stamp,
                    stamp,
                    stamp,
                ),
            )
            saved = bool(int(getattr(cursor, "rowcount", 0) or 0))
            if not saved:
                continue
            saved_jobs.append(job_id)
            if request.supersedable:
                updated = connection.execute(
                    """
                    UPDATE reasoning_engine_jobs
                    SET job_status = 'superseded', completed_at = %s,
                        lease_owner = '', lease_expires_at = '', updated_at = %s,
                        last_error = 'A newer direct source revision owns this scope.'
                    WHERE deployment_id = %s AND scope_key = %s
                      AND job_id <> %s AND supersedable = 1
                      AND job_status IN ('queued', 'retry')
                    """,
                    (stamp, stamp, deployment_id, request.scope_id, job_id),
                )
                superseded += int(getattr(updated, "rowcount", 0) or 0)
        return {
            "saved": bool(saved_jobs),
            "savedJobIds": saved_jobs,
            "deploymentIds": deployments,
            "supersededCount": superseded,
        }

    def ingress_event(self, event) -> Dict[str, object]:
        """Idempotently materialize one durable source event for V2."""

        with self.transaction() as connection:
            return self.ingress_event_with_connection(connection, event)

    def claim(self, deployment_id: str, worker_id: str, limit: int = 1, lease_seconds: int = 600) -> List[Dict[str, object]]:
        stamp = iso_utc()
        lease_until = iso_utc(utc_now() + timedelta(seconds=max(60, int(lease_seconds or 600))))
        bounded = max(1, min(20, int(limit or 1)))
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE reasoning_engine_jobs
                SET job_status = 'retry', lease_owner = '', lease_expires_at = '',
                    available_at = %s,
                    last_error = 'The prior V2 worker lease expired; retrying safely.',
                    updated_at = %s
                WHERE deployment_id = %s AND job_status = 'processing'
                  AND lease_expires_at <> '' AND lease_expires_at < %s
                """,
                (stamp, stamp, str(deployment_id or ""), stamp),
            )
            rows = connection.execute(
                """
                SELECT * FROM reasoning_engine_jobs
                WHERE deployment_id = %s
                  AND job_status IN ('queued', 'retry')
                  AND (available_at = '' OR available_at <= %s)
                  AND (lease_expires_at = '' OR lease_expires_at < %s)
                ORDER BY priority DESC, created_at, job_id
                LIMIT %s FOR UPDATE SKIP LOCKED
                """,
                (str(deployment_id or ""), stamp, stamp, bounded),
            ).fetchall()
            job_ids = [str(row.get("job_id") or "") for row in rows or []]
            if not job_ids:
                return []
            placeholders = ",".join(["%s"] * len(job_ids))
            connection.execute(
                "UPDATE reasoning_engine_jobs SET job_status = 'processing', lease_owner = %s, "
                "lease_expires_at = %s, claimed_at = %s, "
                "queue_wait_ms = TIMESTAMPDIFF(MICROSECOND, STR_TO_DATE(REPLACE(REPLACE(created_at, 'T', ' '), 'Z', ''), '%%Y-%%m-%%d %%H:%%i:%%s.%%f'), UTC_TIMESTAMP(6)) DIV 1000, "
                "updated_at = %s WHERE job_id IN (" + placeholders + ")",
                (str(worker_id or "reasoning-v2"), lease_until, stamp, stamp, *job_ids),
            )
        return [self.row_payload(row) for row in rows or []]

    def complete(self, job_id: str, result: Mapping[str, object]) -> None:
        stamp = iso_utc()
        values = dict(result or {})
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE reasoning_engine_jobs
                SET job_status = 'completed', result_json = %s,
                    duration_ms = %s, lease_owner = '', lease_expires_at = '',
                    last_error = '', completed_at = %s, updated_at = %s
                WHERE job_id = %s
                """,
                (
                    canonical_json(values),
                    max(0, int(values.get("duration_ms") or values.get("durationMs") or 0)),
                    stamp,
                    stamp,
                    str(job_id or ""),
                ),
            )

    def defer(self, job_id: str, reason: str, retry_after_seconds: int = 15) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE reasoning_engine_jobs
                SET job_status = 'queued', lease_owner = '', lease_expires_at = '',
                    available_at = %s, last_error = %s, updated_at = %s
                WHERE job_id = %s
                """,
                (
                    iso_utc(utc_now() + timedelta(seconds=max(1, int(retry_after_seconds or 15)))),
                    str(reason or "")[:500],
                    iso_utc(),
                    str(job_id or ""),
                ),
            )

    def retry(self, job_id: str, error: str, max_attempts: int = 3) -> Dict[str, object]:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT attempts FROM reasoning_engine_jobs WHERE job_id = %s FOR UPDATE",
                (str(job_id or ""),),
            ).fetchone() or {"attempts": 0}
            attempts = int(row.get("attempts") or 0) + 1
            terminal = attempts >= max(1, int(max_attempts or 3))
            delay = min(900, 10 * (2 ** min(6, attempts - 1)))
            stamp = iso_utc()
            connection.execute(
                """
                UPDATE reasoning_engine_jobs
                SET job_status = %s, attempts = %s, lease_owner = '',
                    lease_expires_at = '', available_at = %s, last_error = %s,
                    completed_at = %s, updated_at = %s
                WHERE job_id = %s
                """,
                (
                    "failed" if terminal else "retry",
                    attempts,
                    "" if terminal else iso_utc(utc_now() + timedelta(seconds=delay)),
                    str(error or "")[:500],
                    stamp if terminal else "",
                    stamp,
                    str(job_id or ""),
                ),
            )
        return {"jobId": str(job_id or ""), "attemptCount": attempts, "terminal": terminal}

    def get(self, job_id: str) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM reasoning_engine_jobs WHERE job_id = %s",
                (str(job_id or ""),),
            ).fetchone()
        return self.row_payload(row) if row else {}

    @staticmethod
    def percentile(values: Iterable[int], percentile: float = 0.95) -> int:
        ordered = sorted(max(0, int(value or 0)) for value in values or [])
        if not ordered:
            return 0
        index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.999999)))
        return ordered[index]

    def summary(self, deployment_id: str = "", lookback: int = 200) -> Dict[str, object]:
        params = []
        where = ""
        if str(deployment_id or ""):
            where = " WHERE deployment_id = %s"
            params.append(str(deployment_id or ""))
        with self.connect() as connection:
            counts = connection.execute(
                "SELECT job_status, COUNT(*) AS row_count, MIN(created_at) AS oldest, MAX(updated_at) AS latest "
                "FROM reasoning_engine_jobs" + where + " GROUP BY job_status",
                tuple(params),
            ).fetchall()
            completed_where = where + (" AND" if where else " WHERE") + " job_status = 'completed'"
            rows = connection.execute(
                "SELECT * FROM reasoning_engine_jobs" + completed_where + " ORDER BY completed_at DESC LIMIT %s",
                (*params, max(1, min(2000, int(lookback or 200)))),
            ).fetchall()
        count_map = {str(row.get("job_status") or ""): int(row.get("row_count") or 0) for row in counts or []}
        oldest = {str(row.get("job_status") or ""): str(row.get("oldest") or "") for row in counts or []}
        latest = {str(row.get("job_status") or ""): str(row.get("latest") or "") for row in counts or []}
        unique_runs = {}
        for row in rows or []:
            result = json_value(row.get("result_json"), {})
            run_id = str(
                result.get("request_id")
                or result.get("requestId")
                or row.get("job_id")
                or ""
            )
            unique_runs.setdefault(run_id, (row, result))
        run_rows = [item[0] for item in unique_runs.values()]
        results = [item[1] for item in unique_runs.values()]
        symbols = sorted({
            str(symbol or "").upper().strip()
            for result in results
            for symbol in result.get("symbols") or []
            if str(symbol or "").strip()
        })
        successful = [result for result in results if str(result.get("status") or "") in {"ok", "partial"}]
        trace_complete = [result for result in results if result.get("trace_complete") or result.get("traceComplete")]
        candidate_runs = [result for result in results if result.get("candidate_events") or result.get("candidateEvents")]
        shadow_delivery_authorized = [
            result for result in results
            if result.get("delivery_authorized") or result.get("deliveryAuthorized")
        ]
        pending_times = [
            _timestamp
            for status, _timestamp in oldest.items()
            if status in {"queued", "retry", "processing"} and _timestamp
        ]
        oldest_pending_age = 0
        if pending_times:
            try:
                pending_at = min(
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                    for value in pending_times
                )
                if pending_at.tzinfo is None:
                    pending_at = pending_at.replace(tzinfo=timezone.utc)
                oldest_pending_age = max(0, int((utc_now() - pending_at.astimezone(timezone.utc)).total_seconds()))
            except ValueError:
                oldest_pending_age = 0
        return {
            "deploymentId": str(deployment_id or ""),
            "counts": count_map,
            "oldest": oldest,
            "latest": latest,
            "sampleCount": len(results),
            "successfulRunCount": len(successful),
            "traceCompleteRunCount": len(trace_complete),
            "candidateEventRunCount": len(candidate_runs),
            "shadowDeliveryAuthorizedRunCount": len(shadow_delivery_authorized),
            "distinctSymbolCount": len(symbols),
            "symbols": symbols[:200],
            "durationP95Ms": self.percentile([int(row.get("duration_ms") or 0) for row in run_rows]),
            "queueWaitP95Ms": self.percentile([int(row.get("queue_wait_ms") or 0) for row in run_rows]),
            "failureCount": int(count_map.get("failed") or 0),
            "pendingCount": sum(int(count_map.get(status) or 0) for status in ["queued", "retry", "processing"]),
            "oldestPendingAgeSeconds": oldest_pending_age,
            "latestCompletedAt": str(rows[0].get("completed_at") or "") if rows else "",
        }

    @staticmethod
    def row_payload(row: Mapping[str, object]) -> Dict[str, object]:
        values = dict(row or {})
        request = json_value(values.get("request_json"), {})
        return {
            "jobId": str(values.get("job_id") or ""),
            "deploymentId": str(values.get("deployment_id") or ""),
            "sourceEventId": str(values.get("source_event_id") or ""),
            "scopeKey": str(values.get("scope_key") or ""),
            "inputFingerprint": str(values.get("input_fingerprint") or ""),
            "request": dict(request.get("request") or {}),
            "sourceEvent": dict(request.get("sourceEvent") or {}),
            "result": json_value(values.get("result_json"), {}),
            "status": str(values.get("job_status") or ""),
            "attemptCount": int(values.get("attempts") or 0),
            "priority": int(values.get("priority") or 0),
            "queueWaitMs": int(values.get("queue_wait_ms") or 0),
            "durationMs": int(values.get("duration_ms") or 0),
            "createdAt": str(values.get("created_at") or ""),
            "updatedAt": str(values.get("updated_at") or ""),
            "completedAt": str(values.get("completed_at") or ""),
        }


class MySQLReasoningEngineComparisonStore(MySQLOperationalConnection):
    def record(
        self,
        baseline_deployment_id: str,
        candidate_deployment_id: str,
        source_event_id: str,
        comparison: Mapping[str, object],
    ) -> Dict[str, object]:
        values = dict(comparison or {})
        comparison_id = "reasoning-comparison:" + uuid.uuid4().hex
        stamp = iso_utc()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO reasoning_engine_comparisons (
                    comparison_id, baseline_deployment_id, candidate_deployment_id,
                    baseline_release_id, candidate_release_id,
                    candidate_release_fingerprint, validation_cohort_id,
                    candidate_runtime_revision, source_event_id,
                    comparison_status, fact_parity_pct,
                    rule_slot_coverage_pct, unexplained_decision_difference_count,
                    shadow_delivery_count, payload_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    comparison_id,
                    str(baseline_deployment_id or "")[:191],
                    str(candidate_deployment_id or "")[:191],
                    str(values.get("baselineReleaseId") or "")[:191],
                    str(values.get("candidateReleaseId") or "")[:191],
                    str(values.get("candidateReleaseFingerprint") or "")[:64],
                    str(values.get("validationCohortId") or "")[:96],
                    str(values.get("candidateRuntimeRevision") or "")[:64],
                    str(source_event_id or "")[:191],
                    str(values.get("status") or "unknown")[:32],
                    float(values.get("factParityPct") or 0.0),
                    float(values.get("ruleSlotCoveragePct") or 0.0),
                    int(values.get("unexplainedDecisionDifferenceCount") or 0),
                    int(values.get("shadowDeliveryCount") or 0),
                    canonical_json(values),
                    stamp,
                    stamp,
                ),
            )
        return {"comparisonId": comparison_id, "createdAt": stamp, **values}

    def latest(
        self,
        candidate_deployment_id: str,
        limit: int = 100,
        candidate_release_fingerprint: str = "",
        validation_cohort_id: str = "",
    ) -> List[Dict[str, object]]:
        filters = ["candidate_deployment_id = %s"]
        params = [str(candidate_deployment_id or "")]
        if str(candidate_release_fingerprint or ""):
            filters.append("candidate_release_fingerprint = %s")
            params.append(str(candidate_release_fingerprint or ""))
        if str(validation_cohort_id or ""):
            filters.append("validation_cohort_id = %s")
            params.append(str(validation_cohort_id or ""))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reasoning_engine_comparisons
                WHERE """ + " AND ".join(filters) + """
                ORDER BY created_at DESC, comparison_id DESC LIMIT %s
                """,
                (*params, max(1, min(1000, int(limit or 100)))),
            ).fetchall()
        return [self.row_payload(row) for row in rows or []]

    def summary(
        self,
        candidate_deployment_id: str,
        limit: int = 200,
        candidate_release_fingerprint: str = "",
        validation_cohort_id: str = "",
    ) -> Dict[str, object]:
        rows = self.latest(
            candidate_deployment_id,
            limit=limit,
            candidate_release_fingerprint=candidate_release_fingerprint,
            validation_cohort_id=validation_cohort_id,
        )
        return reasoning_comparison_summary(
            rows,
            candidate_deployment_id=candidate_deployment_id,
            candidate_release_fingerprint=candidate_release_fingerprint,
            validation_cohort_id=validation_cohort_id,
        )

    @staticmethod
    def row_payload(row: Mapping[str, object]) -> Dict[str, object]:
        values = dict(row or {})
        payload = json_value(values.get("payload_json"), {})
        return {
            "comparisonId": str(values.get("comparison_id") or ""),
            "baselineDeploymentId": str(values.get("baseline_deployment_id") or ""),
            "candidateDeploymentId": str(values.get("candidate_deployment_id") or ""),
            "baselineReleaseId": str(values.get("baseline_release_id") or ""),
            "candidateReleaseId": str(values.get("candidate_release_id") or ""),
            "candidateReleaseFingerprint": str(values.get("candidate_release_fingerprint") or ""),
            "validationCohortId": str(values.get("validation_cohort_id") or ""),
            "candidateRuntimeRevision": str(values.get("candidate_runtime_revision") or ""),
            "sourceEventId": str(values.get("source_event_id") or ""),
            "status": str(values.get("comparison_status") or ""),
            "factParityPct": float(values.get("fact_parity_pct") or 0.0),
            "ruleSlotCoveragePct": float(values.get("rule_slot_coverage_pct") or 0.0),
            "unexplainedDecisionDifferenceCount": int(values.get("unexplained_decision_difference_count") or 0),
            "shadowDeliveryCount": int(values.get("shadow_delivery_count") or 0),
            "payload": payload,
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
