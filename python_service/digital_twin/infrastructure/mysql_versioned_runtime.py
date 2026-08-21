"""MySQL control-plane stores for versioned engines and temporal backends."""

import json
import os
import socket
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
from ..domain.independent_reasoning import (
    independent_reasoning_request,
    merge_reasoning_events,
    reasoning_event_scope,
    reasoning_queue_slot_key,
    shard_reasoning_event,
)
from ..domain.events import DomainEvent
from ..domain.investment_reasoning import FactDelta
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


def reasoning_worker_process_owner(worker_id: object):
    """Return the host and PID encoded in a managed reasoning worker ID."""

    parts = str(worker_id or "").split(":", 2)
    if len(parts) < 3 or not parts[0].strip():
        return "", 0
    try:
        process_id = int(parts[1])
    except (TypeError, ValueError):
        return "", 0
    return parts[0].strip(), process_id if process_id > 0 else 0


def local_process_is_alive(process_id: int) -> bool:
    """Check a local PID without sending a signal that changes the process."""

    try:
        os.kill(int(process_id), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        # An ambiguous OS response must not steal a possibly live lease.
        return True
    return True


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

    def retire_unselected(self, engine_version: str, keep_deployment_ids: Iterable[str]) -> Dict[str, object]:
        """Retire obsolete logical deployments and terminalize their work."""

        keep = sorted({str(value or "").strip() for value in keep_deployment_ids or [] if str(value or "").strip()})
        version = str(engine_version or "").strip().lower()
        if not version:
            return {"retiredDeploymentIds": [], "supersededJobCount": 0, "supersededShadowJobCount": 0}
        stamp = iso_utc()
        with self.transaction() as connection:
            params = [version]
            keep_clause = ""
            if keep:
                keep_clause = " AND deployment_id NOT IN (" + ",".join(["%s"] * len(keep)) + ")"
                params.extend(keep)
            rows = connection.execute(
                "SELECT deployment_id FROM reasoning_engine_deployments "
                "WHERE engine_version = %s AND deployment_status <> 'retired'" + keep_clause + " FOR UPDATE",
                tuple(params),
            ).fetchall()
            retired = [str(row.get("deployment_id") or "") for row in rows or [] if str(row.get("deployment_id") or "")]
            if not retired:
                return {"retiredDeploymentIds": [], "supersededJobCount": 0, "supersededShadowJobCount": 0}
            placeholders = ",".join(["%s"] * len(retired))
            connection.execute(
                "UPDATE reasoning_engine_deployments SET deployment_status = 'retired', updated_at = %s "
                "WHERE deployment_id IN (" + placeholders + ")",
                (stamp, *retired),
            )
            jobs = connection.execute(
                "UPDATE reasoning_engine_jobs SET job_status = 'superseded', completed_at = %s, "
                "lease_owner = '', lease_expires_at = '', heartbeat_at = '', "
                "last_error = %s, updated_at = %s WHERE deployment_id IN (" + placeholders + ") "
                "AND job_status IN ('queued', 'retry', 'processing', 'awaiting_source')",
                (stamp, "The reasoning deployment was retired after control-plane promotion.", stamp, *retired),
            )
            shadow = connection.execute(
                "UPDATE reasoning_engine_shadow_jobs SET job_status = 'failed', completed_at = %s, "
                "lease_owner = '', lease_expires_at = '', last_error = %s, updated_at = %s "
                "WHERE candidate_deployment_id IN (" + placeholders + ") "
                "AND job_status IN ('queued', 'retry', 'processing')",
                (stamp, "The shadow deployment was retired after control-plane promotion.", stamp, *retired),
            )
        return {
            "retiredDeploymentIds": retired,
            "supersededJobCount": int(getattr(jobs, "rowcount", 0) or 0),
            "supersededShadowJobCount": int(getattr(shadow, "rowcount", 0) or 0),
        }

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
    def required_source_boundary_at(event) -> str:
        payload = dict(getattr(event, "payload", {}) or {})
        source_at = str(
            payload.get("sourceObservedAt")
            or payload.get("sourceAsOf")
            or ""
        ).strip()
        occurred_at = str(getattr(event, "occurred_at", "") or "").strip()
        work_class = str(payload.get("workClass") or "").strip().upper()
        # Monitor-owned market/portfolio events are emitted immediately after
        # their verified snapshot. Other facts need the first monitor packet
        # created after collection so the new fact cannot be paired with an
        # older world state.
        if work_class in {"MARKET", "PORTFOLIO"} and source_at:
            return source_at
        return max(source_at, occurred_at) if source_at and occurred_at else source_at or occurred_at or iso_utc()

    @staticmethod
    def native_target_symbol_limit_with_connection(connection) -> int:
        try:
            row = connection.execute(
                "SELECT value FROM runtime_settings WHERE `key` = %s",
                ("typedbNativeRuleTargetSymbolLimit",),
            ).fetchone() or {}
            value = int(float(str(row.get("value") or "4")))
        except (AttributeError, TypeError, ValueError):
            value = 4
        return max(1, min(200, value))

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
        configured_row = connection.execute(
            "SELECT value FROM runtime_settings WHERE `key` = %s",
            ("reasoningEngineV2DeploymentId",),
        ).fetchone() or {}
        configured = str(configured_row.get("value") or "").strip()
        if configured and configured in deployments:
            return [configured]
        # A cold-start race is repaired from the append-only domain event log.
        # Never invent an obsolete fallback deployment at ingress time.
        return sorted(set(deployments))

    @staticmethod
    def source_boundaries_with_connection(connection, event) -> List[Dict[str, object]]:
        payload = dict(getattr(event, "payload", {}) or {})
        existing = []
        singular = payload.get("verifiedSourceSnapshot")
        if isinstance(singular, Mapping) and singular:
            existing.append(dict(singular))
        for value in payload.get("verifiedSourceSnapshots") or []:
            if isinstance(value, Mapping) and value:
                existing.append(dict(value))
        if existing:
            return list({
                str(value.get("accountId") or "") + "|" + str(value.get("snapshotId") or value.get("generatedAt") or ""): value
                for value in existing
                if str(value.get("snapshotId") or value.get("generatedAt") or "").strip()
            }.values())
        scope = reasoning_event_scope(event)
        account_ids = [str(value or "").strip() for value in scope.get("accountIds") or [] if str(value or "").strip()]
        required_at = MySQLReasoningEngineJobStore.required_source_boundary_at(event)
        account_filter = ""
        params: List[object] = [required_at]
        if account_ids:
            account_filter = " AND source.account_id IN (" + ",".join(["%s"] * len(account_ids)) + ")"
            params.extend(account_ids)
        rows = connection.execute(
            """
            SELECT source.snapshot_id, source.account_id, source.generated_at,
                   source.contract_version, source.fingerprint
            FROM verified_reasoning_source_snapshots source
            INNER JOIN (
                SELECT account_id, MIN(generated_at) AS generated_at
                FROM verified_reasoning_source_snapshots
                WHERE generated_at >= %s
                GROUP BY account_id
            ) latest
              ON latest.account_id = source.account_id
             AND latest.generated_at = source.generated_at
            WHERE 1 = 1
            """ + account_filter + " ORDER BY source.account_id",
            tuple(params),
        ).fetchall()
        return [{
            "snapshotId": str(row.get("snapshot_id") or ""),
            "accountId": str(row.get("account_id") or ""),
            "generatedAt": str(row.get("generated_at") or ""),
            "contractVersion": str(row.get("contract_version") or ""),
            "fingerprint": str(row.get("fingerprint") or ""),
        } for row in rows or [] if str(row.get("snapshot_id") or "")]

    @classmethod
    def bind_source_boundaries_with_connection(cls, connection, event):
        boundaries = cls.source_boundaries_with_connection(connection, event)
        payload = dict(getattr(event, "payload", {}) or {})
        if boundaries:
            payload["verifiedSourceSnapshots"] = boundaries
            if len(boundaries) == 1:
                payload["verifiedSourceSnapshot"] = boundaries[0]
        return DomainEvent(
            name=event.name,
            aggregate_id=event.aggregate_id,
            schema_version=event.schema_version,
            payload=payload,
            occurred_at=event.occurred_at,
            event_id=event.event_id,
            correlation_id=event.correlation_id,
        )

    @classmethod
    def backfill_source_boundaries_with_connection(
        cls,
        connection,
        deployment_id: str,
        limit: int = 200,
    ) -> Dict[str, object]:
        """Upgrade queued pre-cutover jobs to immutable point-in-time inputs."""

        rows = connection.execute(
            "SELECT job_id, request_json, source_boundary_json FROM reasoning_engine_jobs "
            "WHERE deployment_id = %s AND job_status IN ('queued', 'retry', 'awaiting_source') "
            "ORDER BY created_at, job_id LIMIT %s FOR UPDATE SKIP LOCKED",
            (str(deployment_id or ""), max(1, min(1000, int(limit or 200)))),
        ).fetchall()
        updated = 0
        waiting = 0
        stamp = iso_utc()
        for row in rows or []:
            job_id = str(row.get("job_id") or "")
            stored = json_value(row.get("request_json"), {})
            source_payload = stored.get("sourceEvent") if isinstance(stored, Mapping) else {}
            if not isinstance(source_payload, Mapping) or not source_payload:
                boundaries = []
                bounded_event = None
            else:
                source_event = DomainEvent.from_dict(dict(source_payload))
                required_at = cls.required_source_boundary_at(source_event)
                current_boundaries = json_value(row.get("source_boundary_json"), [])
                current_boundaries = current_boundaries if isinstance(current_boundaries, list) else []
                if current_boundaries and all(
                    str(value.get("generatedAt") or "") >= required_at
                    for value in current_boundaries
                    if isinstance(value, Mapping)
                ):
                    continue
                clean_payload = dict(source_event.payload or {})
                clean_payload.pop("verifiedSourceSnapshot", None)
                clean_payload.pop("verifiedSourceSnapshots", None)
                source_event = DomainEvent(
                    name=source_event.name,
                    aggregate_id=source_event.aggregate_id,
                    schema_version=source_event.schema_version,
                    payload=clean_payload,
                    occurred_at=source_event.occurred_at,
                    event_id=source_event.event_id,
                    correlation_id=source_event.correlation_id,
                )
                bounded_event = cls.bind_source_boundaries_with_connection(
                    connection,
                    source_event,
                )
                boundaries = cls.source_boundaries_with_connection(connection, bounded_event)
            if not boundaries or bounded_event is None:
                connection.execute(
                    "UPDATE reasoning_engine_jobs SET job_status = 'awaiting_source', source_boundary_json = '[]', "
                    "source_snapshot_id = '', source_snapshot_at = '', source_payload_hash = '', "
                    "last_error = %s, updated_at = %s WHERE job_id = %s",
                    (
                        "Waiting for the first verified source snapshot that covers this fact revision.",
                        stamp,
                        job_id,
                    ),
                )
                waiting += 1
                continue
            request = independent_reasoning_request(str(deployment_id or ""), [bounded_event])
            primary = boundaries[0]
            connection.execute(
                "UPDATE reasoning_engine_jobs SET job_status = 'queued', source_snapshot_id = %s, source_snapshot_at = %s, "
                "source_boundary_json = %s, source_payload_hash = %s, scope_key = %s, "
                "input_fingerprint = %s, request_json = %s, reasoning_lane = %s, available_at = %s, "
                "last_error = '', updated_at = %s "
                "WHERE job_id = %s",
                (
                    str(primary.get("snapshotId") or "")[:191],
                    str(primary.get("generatedAt") or "")[:40],
                    canonical_json(boundaries),
                    payload_fingerprint(bounded_event.to_dict()),
                    request.scope_id[:191],
                    request.input_fingerprint[:64],
                    canonical_json({
                        "request": request.to_dict(),
                        "sourceEvent": bounded_event.to_dict(),
                    }),
                    FactDelta.from_request(request).lane,
                    stamp,
                    stamp,
                    job_id,
                ),
            )
            updated += 1
        return {
            "scannedCount": len(rows or []),
            "updatedCount": updated,
            "waitingCount": waiting,
        }

    @staticmethod
    def event_priority(event) -> int:
        payload = dict(getattr(event, "payload", {}) or {})
        level = str(payload.get("reviewLevel") or payload.get("materialityReviewLevel") or "").lower()
        return {"critical": 100, "urgent": 90, "caution": 80, "check": 70, "observe": 50}.get(level, 60)

    @staticmethod
    def stored_source_event(row: Mapping[str, object]):
        stored = json_value(dict(row or {}).get("request_json"), {})
        source = stored.get("sourceEvent") if isinstance(stored, Mapping) else {}
        return DomainEvent.from_dict(dict(source)) if isinstance(source, Mapping) and source else None

    @staticmethod
    def source_boundaries(event) -> List[Dict[str, object]]:
        payload = dict(getattr(event, "payload", {}) or {})
        singular = payload.get("verifiedSourceSnapshot")
        boundaries = [
            dict(value)
            for value in payload.get("verifiedSourceSnapshots") or []
            if isinstance(value, Mapping) and value
        ]
        if not boundaries and isinstance(singular, Mapping) and singular:
            boundaries = [dict(singular)]
        return list({
            str(value.get("accountId") or "") + "|" + str(value.get("snapshotId") or value.get("generatedAt") or ""): value
            for value in boundaries
            if str(value.get("snapshotId") or value.get("generatedAt") or "").strip()
        }.values())

    @classmethod
    def queued_job_material(cls, deployment_id: str, event):
        request = independent_reasoning_request(str(deployment_id or ""), [event])
        lane = FactDelta.from_request(request).lane
        boundaries = cls.source_boundaries(event)
        primary = max(
            boundaries,
            key=lambda value: str(value.get("generatedAt") or ""),
            default={},
        )
        scope_key = (
            reasoning_queue_slot_key(event, lane)
            if request.supersedable
            else request.scope_id
        )
        return {
            "request": request,
            "lane": lane,
            "boundaries": boundaries,
            "primary": primary,
            "scopeKey": scope_key,
            "requestJson": canonical_json({
                "request": request.to_dict(),
                "sourceEvent": event.to_dict(),
            }),
        }

    @classmethod
    def pending_slot_events_with_connection(
        cls,
        connection,
        deployment_id: str,
        scope_key: str,
    ) -> List[object]:
        rows = connection.execute(
            "SELECT request_json FROM reasoning_engine_jobs "
            "WHERE deployment_id = %s AND scope_key = %s AND supersedable = 1 "
            "AND job_status IN ('queued', 'retry', 'awaiting_source', 'awaiting_world_projection') "
            "ORDER BY source_snapshot_at, created_at, job_id FOR UPDATE",
            (str(deployment_id or ""), str(scope_key or "")),
        ).fetchall()
        return [
            event for event in (cls.stored_source_event(row) for row in rows or []) if event is not None
        ]

    @classmethod
    def ingress_event_with_connection(cls, connection, event) -> Dict[str, object]:
        deployments = cls.target_deployments_with_connection(connection)
        if not deployments:
            return {
                "saved": False,
                "savedJobIds": [],
                "deploymentIds": [],
                "status": "no-v2-target",
            }
        symbol_limit = cls.native_target_symbol_limit_with_connection(connection)
        event = cls.bind_source_boundaries_with_connection(connection, event)
        # Persist one stable scope per symbol. The runner can still combine
        # compatible symbol jobs up to the native TypeDB execution limit.
        source_events = shard_reasoning_event(event, 1)
        stamp = iso_utc()
        saved_jobs = []
        superseded = 0
        for deployment_id in deployments:
            for source_event in source_events:
                material = cls.queued_job_material(deployment_id, source_event)
                request = material["request"]
                if request.supersedable:
                    pending = cls.pending_slot_events_with_connection(
                        connection,
                        deployment_id,
                        material["scopeKey"],
                    )
                    if pending:
                        source_event = merge_reasoning_events([*pending, source_event])
                        material = cls.queued_job_material(deployment_id, source_event)
                        request = material["request"]
                source_boundary = material["primary"]
                source_boundaries = material["boundaries"]
                job_id = "reasoning-engine-job:" + uuid.uuid4().hex
                cursor = connection.execute(
                    """
                    INSERT IGNORE INTO reasoning_engine_jobs (
                        job_id, deployment_id, source_event_id, source_snapshot_id,
                        source_snapshot_at, source_boundary_json, source_payload_hash, scope_key,
                        input_fingerprint, request_json, result_json, job_status,
                        priority, supersedable, reasoning_lane, available_at, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}', %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        job_id,
                        str(deployment_id or "")[:191],
                        str(source_event.event_id or "")[:191],
                        str(source_boundary.get("snapshotId") or "")[:191],
                        str(source_boundary.get("generatedAt") or "")[:40],
                        canonical_json(source_boundaries),
                        payload_fingerprint(source_event.to_dict()),
                        str(material["scopeKey"] or "")[:191],
                        request.input_fingerprint[:64],
                        material["requestJson"],
                        "queued" if source_boundaries else "awaiting_source",
                        cls.event_priority(source_event),
                        1 if request.supersedable else 0,
                        material["lane"],
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
                          AND job_status IN ('queued', 'retry', 'awaiting_source', 'awaiting_world_projection')
                        """,
                        (stamp, stamp, deployment_id, material["scopeKey"], job_id),
                    )
                    superseded += int(getattr(updated, "rowcount", 0) or 0)
        return {
            "saved": bool(saved_jobs),
            "savedJobIds": saved_jobs,
            "deploymentIds": deployments,
            "supersededCount": superseded,
            "sourceShardCount": len(source_events),
            "sourceShardSymbolLimit": 1,
            "nativeTargetSymbolLimit": symbol_limit,
        }

    def ingress_event(self, event) -> Dict[str, object]:
        """Idempotently materialize one durable source event for V2."""

        with self.transaction() as connection:
            return self.ingress_event_with_connection(connection, event)

    def reshard_claimed_job(
        self,
        job_id: str,
        event,
        max_symbols: int,
        worker_id: str = "",
    ) -> Dict[str, object]:
        """Atomically replace one legacy oversized job with bounded shards."""

        source_events = shard_reasoning_event(event, max_symbols)
        if len(source_events) <= 1:
            return {"status": "unchanged", "shardCount": len(source_events)}
        stamp = iso_utc()
        inserted_job_ids = []
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM reasoning_engine_jobs WHERE job_id = %s FOR UPDATE",
                (str(job_id or ""),),
            ).fetchone() or {}
            if not row:
                return {"status": "missing", "shardCount": 0}
            if str(row.get("job_status") or "") != "processing":
                return {"status": "lease-lost", "shardCount": 0}
            if str(worker_id or "") and str(row.get("lease_owner") or "") != str(worker_id or ""):
                return {"status": "lease-lost", "shardCount": 0}
            deployment_id = str(row.get("deployment_id") or "")
            priority = int(row.get("priority") or self.event_priority(event))
            first_event = source_events[0]
            first_request = independent_reasoning_request(deployment_id, [first_event])
            first_payload = dict(first_event.payload or {})
            first_boundary = first_payload.get("verifiedSourceSnapshot")
            first_boundary = dict(first_boundary or {}) if isinstance(first_boundary, Mapping) else {}
            first_boundaries = [
                dict(value)
                for value in first_payload.get("verifiedSourceSnapshots") or []
                if isinstance(value, Mapping) and value
            ] or ([first_boundary] if first_boundary else [])
            connection.execute(
                """
                UPDATE reasoning_engine_jobs
                SET source_event_id = %s, source_snapshot_id = %s,
                    source_snapshot_at = %s, source_boundary_json = %s,
                    source_payload_hash = %s, scope_key = %s,
                    input_fingerprint = %s, request_json = %s, result_json = '{}',
                    job_status = 'queued', supersedable = %s, reasoning_lane = %s,
                    available_at = %s, lease_owner = '', lease_expires_at = '',
                    heartbeat_at = '', claimed_at = '', current_stage = '',
                    stage_started_at = '', stage_updated_at = '', stage_details_json = NULL,
                    duration_ms = 0,
                    last_error = 'Oversized source event was split before TypeDB execution.',
                    completed_at = '', updated_at = %s
                WHERE job_id = %s
                """,
                (
                    str(first_event.event_id or "")[:191],
                    str(first_boundary.get("snapshotId") or "")[:191],
                    str(first_boundary.get("generatedAt") or "")[:40],
                    canonical_json(first_boundaries),
                    payload_fingerprint(first_event.to_dict()),
                    first_request.scope_id[:191],
                    first_request.input_fingerprint[:64],
                    canonical_json({
                        "request": first_request.to_dict(),
                        "sourceEvent": first_event.to_dict(),
                    }),
                    1 if first_request.supersedable else 0,
                    FactDelta.from_request(first_request).lane,
                    stamp,
                    stamp,
                    str(job_id or ""),
                ),
            )
            inserted_job_ids.append(str(job_id or ""))
            for source_event in source_events[1:]:
                request = independent_reasoning_request(deployment_id, [source_event])
                event_payload = dict(source_event.payload or {})
                source_boundary = event_payload.get("verifiedSourceSnapshot")
                source_boundary = dict(source_boundary or {}) if isinstance(source_boundary, Mapping) else {}
                source_boundaries = [
                    dict(value)
                    for value in event_payload.get("verifiedSourceSnapshots") or []
                    if isinstance(value, Mapping) and value
                ] or ([source_boundary] if source_boundary else [])
                child_job_id = "reasoning-engine-job:" + uuid.uuid4().hex
                cursor = connection.execute(
                    """
                    INSERT IGNORE INTO reasoning_engine_jobs (
                        job_id, deployment_id, source_event_id, source_snapshot_id,
                        source_snapshot_at, source_boundary_json, source_payload_hash, scope_key,
                        input_fingerprint, request_json, result_json, job_status,
                        priority, supersedable, reasoning_lane, available_at, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}', 'queued', %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        child_job_id,
                        deployment_id[:191],
                        str(source_event.event_id or "")[:191],
                        str(source_boundary.get("snapshotId") or "")[:191],
                        str(source_boundary.get("generatedAt") or "")[:40],
                        canonical_json(source_boundaries),
                        payload_fingerprint(source_event.to_dict()),
                        request.scope_id[:191],
                        request.input_fingerprint[:64],
                        canonical_json({
                            "request": request.to_dict(),
                            "sourceEvent": source_event.to_dict(),
                        }),
                        priority,
                        1 if request.supersedable else 0,
                        FactDelta.from_request(request).lane,
                        stamp,
                        stamp,
                        stamp,
                    ),
                )
                if int(getattr(cursor, "rowcount", 0) or 0):
                    inserted_job_ids.append(child_job_id)
        return {
            "status": "resharded",
            "shardCount": len(source_events),
            "jobIds": inserted_job_ids,
        }

    def compact_supersedable_backlog(
        self,
        deployment_id: str,
        limit: int = 2000,
    ) -> Dict[str, object]:
        """Collapse historical pending deltas into one latest-state job per slot.

        This is both a one-time migration for pre-slot V2 jobs and a bounded
        recovery path after an interrupted ingress transaction. Processing and
        non-fungible jobs are deliberately excluded.
        """

        stamp = iso_utc()
        scanned = 0
        compacted = 0
        migrated = 0
        age_resets = 0
        groups = {}
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM reasoning_engine_jobs WHERE deployment_id = %s "
                "AND supersedable = 1 AND job_status IN ('queued', 'retry', 'awaiting_source', 'awaiting_world_projection') "
                "ORDER BY source_snapshot_at, created_at, job_id LIMIT %s FOR UPDATE",
                (str(deployment_id or ""), max(1, min(10000, int(limit or 2000)))),
            ).fetchall()
            for row in rows or []:
                event = self.stored_source_event(row)
                if event is None:
                    continue
                lane = str(row.get("reasoning_lane") or "") or FactDelta.from_request(
                    independent_reasoning_request(deployment_id, [event])
                ).lane
                slot = reasoning_queue_slot_key(event, lane)
                groups.setdefault(slot, []).append((dict(row), event))
                scanned += 1

            for slot, items in groups.items():
                survivor_row, survivor_event = max(
                    items,
                    key=lambda value: (
                        str(value[0].get("source_snapshot_at") or ""),
                        str((value[1].payload or {}).get("sourceObservedAt") or ""),
                        str(value[0].get("created_at") or ""),
                        str(value[0].get("job_id") or ""),
                    ),
                )
                merged_event = merge_reasoning_events([event for _, event in items])
                if merged_event.event_id != survivor_event.event_id:
                    merged_event = DomainEvent(
                        name=merged_event.name,
                        aggregate_id=merged_event.aggregate_id,
                        schema_version=merged_event.schema_version,
                        payload=dict(merged_event.payload or {}),
                        occurred_at=max(merged_event.occurred_at, survivor_event.occurred_at),
                        event_id=survivor_event.event_id,
                        correlation_id=survivor_event.correlation_id or merged_event.correlation_id,
                    )
                material = self.queued_job_material(deployment_id, merged_event)
                survivor_id = str(survivor_row.get("job_id") or "")
                replaced_ids = [
                    str(row.get("job_id") or "")
                    for row, _ in items
                    if str(row.get("job_id") or "") != survivor_id
                ]
                if replaced_ids:
                    placeholders = ",".join(["%s"] * len(replaced_ids))
                    connection.execute(
                        "UPDATE reasoning_engine_jobs SET job_status = 'superseded', completed_at = %s, "
                        "available_at = '', lease_owner = '', lease_expires_at = '', heartbeat_at = '', "
                        "current_stage = '', stage_started_at = '', stage_updated_at = '', stage_details_json = NULL, "
                        "last_error = 'Merged into the newest durable reasoning slot.', updated_at = %s "
                        "WHERE job_id IN (" + placeholders + ")",
                        (stamp, stamp, *replaced_ids),
                    )
                    compacted += len(replaced_ids)
                scope_migrated = str(survivor_row.get("scope_key") or "") != slot
                if scope_migrated:
                    migrated += 1
                compacted_age_reset = bool(
                    str(survivor_row.get("last_error") or "").startswith("Coalesced ")
                    and str(survivor_row.get("created_at") or "")
                    < str(survivor_row.get("updated_at") or "")
                )
                if not replaced_ids and not scope_migrated and not compacted_age_reset:
                    continue
                if compacted_age_reset:
                    age_resets += 1
                primary = material["primary"]
                connection.execute(
                    "UPDATE reasoning_engine_jobs SET source_snapshot_id = %s, source_snapshot_at = %s, "
                    "source_boundary_json = %s, source_payload_hash = %s, scope_key = %s, "
                    "input_fingerprint = %s, request_json = %s, job_status = %s, priority = %s, "
                    "reasoning_lane = %s, available_at = %s, lease_owner = '', lease_expires_at = '', "
                    "heartbeat_at = '', current_stage = '', stage_started_at = '', stage_updated_at = '', "
                    "stage_details_json = NULL, last_error = %s, completed_at = '', created_at = %s, updated_at = %s "
                    "WHERE job_id = %s",
                    (
                        str(primary.get("snapshotId") or "")[:191],
                        str(primary.get("generatedAt") or "")[:40],
                        canonical_json(material["boundaries"]),
                        payload_fingerprint(merged_event.to_dict()),
                        slot[:191],
                        material["request"].input_fingerprint[:64],
                        material["requestJson"],
                        "queued" if material["boundaries"] else "awaiting_source",
                        max(int(row.get("priority") or 0) for row, _ in items),
                        material["lane"],
                        stamp,
                        (
                            "Coalesced " + str(len(items)) + " pending fact changes into the latest source snapshot."
                            if len(items) > 1 else ""
                        ),
                        stamp,
                        stamp,
                        survivor_id,
                    ),
                )
        return {
            "status": "compacted" if compacted or migrated or age_resets else "unchanged",
            "scannedCount": scanned,
            "slotCount": len(groups),
            "compactedCount": compacted,
            "migratedCount": migrated,
            "queueAgeResetCount": age_resets,
        }

    def recover_dead_local_leases(
        self,
        deployment_id: str,
        current_worker_id: str = "",
        host_name: str = "",
        process_alive=None,
    ) -> Dict[str, object]:
        """Immediately return jobs owned by a dead worker on this machine.

        The durable expiry remains authoritative for remote or ambiguous
        owners. A supervised local restart has stronger evidence: the worker
        ID contains its host and PID, so the replacement can recover only a
        process that the local OS confirms no longer exists.
        """

        local_host = str(host_name or socket.gethostname()).strip()
        alive = process_alive if callable(process_alive) else local_process_is_alive
        stamp = iso_utc()
        recovered_job_ids = []
        superseded_job_ids = []
        recovered_owners = []
        with self.transaction() as connection:
            protected_deployments = {str(deployment_id or "")}
            try:
                control = connection.execute(
                    "SELECT active_deployment_id, delivery_deployment_id, candidate_deployment_id "
                    "FROM reasoning_engine_control WHERE control_id = 'global'"
                ).fetchone() or {}
                protected_deployments.update(
                    str(control.get(key) or "").strip()
                    for key in (
                        "active_deployment_id",
                        "delivery_deployment_id",
                        "candidate_deployment_id",
                    )
                    if str(control.get(key) or "").strip()
                )
            except (AttributeError, TypeError):
                pass
            rows = connection.execute(
                "SELECT job_id, deployment_id, lease_owner FROM reasoning_engine_jobs "
                "WHERE job_status = 'processing' AND lease_owner <> '' FOR UPDATE",
            ).fetchall()
            for row in rows or []:
                owner = str(row.get("lease_owner") or "")
                if not owner or owner == str(current_worker_id or ""):
                    continue
                owner_host, process_id = reasoning_worker_process_owner(owner)
                if owner_host != local_host or process_id <= 0:
                    continue
                try:
                    owner_alive = bool(alive(process_id))
                except Exception:  # noqa: BLE001 - uncertain ownership keeps the lease.
                    owner_alive = True
                if owner_alive:
                    continue
                job_id = str(row.get("job_id") or "")
                job_deployment = str(row.get("deployment_id") or deployment_id or "")
                if job_deployment in protected_deployments:
                    recovered_job_ids.append(job_id)
                else:
                    superseded_job_ids.append(job_id)
                recovered_owners.append(owner)
            recovered_job_ids = [value for value in recovered_job_ids if value]
            superseded_job_ids = [value for value in superseded_job_ids if value]
            if recovered_job_ids:
                placeholders = ",".join(["%s"] * len(recovered_job_ids))
                connection.execute(
                    "UPDATE reasoning_engine_jobs SET job_status = 'retry', "
                    "lease_owner = '', lease_expires_at = '', heartbeat_at = '', "
                    "claimed_at = '', current_stage = '', stage_started_at = '', "
                    "stage_updated_at = '', stage_details_json = NULL, available_at = %s, "
                    "last_error = 'The previous local V2 worker stopped; retrying immediately.', "
                    "updated_at = %s WHERE job_status = 'processing' "
                    "AND job_id IN (" + placeholders + ")",
                    (stamp, stamp, *recovered_job_ids),
                )
            if superseded_job_ids:
                placeholders = ",".join(["%s"] * len(superseded_job_ids))
                connection.execute(
                    "UPDATE reasoning_engine_jobs SET job_status = 'superseded', "
                    "lease_owner = '', lease_expires_at = '', heartbeat_at = '', claimed_at = '', "
                    "current_stage = '', stage_started_at = '', stage_updated_at = '', "
                    "stage_details_json = NULL, available_at = '', completed_at = %s, "
                    "last_error = 'Obsolete V2 deployment worker stopped; claim superseded.', "
                    "updated_at = %s WHERE job_status = 'processing' "
                    "AND job_id IN (" + placeholders + ")",
                    (stamp, stamp, *superseded_job_ids),
                )
        return {
            "status": "recovered" if recovered_job_ids or superseded_job_ids else "unchanged",
            "recoveredCount": len(recovered_job_ids),
            "recoveredJobIds": recovered_job_ids,
            "supersededCount": len(superseded_job_ids),
            "supersededJobIds": superseded_job_ids,
            "recoveredOwnerCount": len(set(recovered_owners)),
        }

    def release_worker_leases(
        self,
        deployment_id: str,
        worker_id: str,
        reason: str = "The V2 worker stopped; retrying immediately.",
    ) -> Dict[str, object]:
        """Return this worker's in-flight jobs before a managed restart.

        Lease expiry remains the crash fallback. A supervised SIGTERM has an
        exact owner identity, so retaining those claims for the full lease
        interval only creates avoidable queue latency.
        """

        owner = str(worker_id or "").strip()
        if not owner:
            return {"status": "unchanged", "releasedCount": 0, "workerId": ""}
        stamp = iso_utc()
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE reasoning_engine_jobs SET job_status = 'retry', "
                "lease_owner = '', lease_expires_at = '', heartbeat_at = '', "
                "claimed_at = '', current_stage = '', stage_started_at = '', "
                "stage_updated_at = '', stage_details_json = NULL, "
                "available_at = %s, last_error = %s, updated_at = %s "
                "WHERE deployment_id = %s AND job_status = 'processing' AND lease_owner = %s",
                (
                    stamp,
                    str(reason or "The V2 worker stopped; retrying immediately.")[:500],
                    stamp,
                    str(deployment_id or ""),
                    owner,
                ),
            )
        released = max(0, int(getattr(cursor, "rowcount", 0) or 0))
        return {
            "status": "released" if released else "unchanged",
            "releasedCount": released,
            "workerId": owner,
            "deploymentId": str(deployment_id or ""),
            "availableAt": stamp if released else "",
        }

    def next_lane(self, deployment_id: str) -> str:
        stamp = iso_utc()
        try:
            maximum_wait = int(float(str(self.runtime_settings.get("reasoningEngineV2LaneMaxWaitSeconds") or "120")))
        except (TypeError, ValueError):
            maximum_wait = 120
        aged_cutoff = iso_utc(utc_now() - timedelta(seconds=max(30, min(1800, maximum_wait))))
        boundary_filter = (
            " AND source_boundary_json IS NOT NULL AND source_boundary_json NOT IN ('', '[]') "
            if str(self.runtime_settings.get("reasoningEngineV2RequireSourceBoundary") or "1").strip().lower()
            not in {"0", "false", "no", "off", "disabled"}
            else ""
        )
        with self.connect() as connection:
            row = connection.execute(
                "SELECT reasoning_lane FROM reasoning_engine_jobs WHERE deployment_id = %s "
                "AND job_status IN ('queued', 'retry', 'awaiting_world_projection') AND (available_at = '' OR available_at <= %s) "
                + boundary_filter +
                "ORDER BY CASE WHEN created_at <= %s THEN 0 ELSE 1 END, "
                "CASE WHEN created_at <= %s THEN created_at ELSE '' END, "
                "priority DESC, CASE reasoning_lane WHEN 'REALTIME' THEN 0 "
                "WHEN 'CONTEXT' THEN 1 ELSE 2 END, created_at, job_id LIMIT 1",
                (str(deployment_id or ""), stamp, aged_cutoff, aged_cutoff),
            ).fetchone()
        return str((row or {}).get("reasoning_lane") or "")

    def claim(
        self,
        deployment_id: str,
        worker_id: str,
        limit: int = 1,
        lease_seconds: int = 600,
        reasoning_lane: str = "",
    ) -> List[Dict[str, object]]:
        stamp = iso_utc()
        lease_until = iso_utc(utc_now() + timedelta(seconds=max(60, int(lease_seconds or 600))))
        bounded = max(1, min(20, int(limit or 1)))
        boundary_filter = (
            " AND source_boundary_json IS NOT NULL AND source_boundary_json NOT IN ('', '[]')"
            if str(self.runtime_settings.get("reasoningEngineV2RequireSourceBoundary") or "1").strip().lower()
            not in {"0", "false", "no", "off", "disabled"}
            else ""
        )
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE reasoning_engine_jobs
                SET job_status = 'retry', lease_owner = '', lease_expires_at = '',
                    heartbeat_at = '', current_stage = '', stage_started_at = '',
                    stage_updated_at = '', stage_details_json = NULL,
                    available_at = %s,
                    last_error = 'The prior V2 worker lease expired; retrying safely.',
                    updated_at = %s
                WHERE deployment_id = %s AND job_status = 'processing'
                  AND lease_expires_at <> '' AND lease_expires_at < %s
                """,
                (stamp, stamp, str(deployment_id or ""), stamp),
            )
            lane_filter = " AND reasoning_lane = %s" if str(reasoning_lane or "") else ""
            lane_params = (str(reasoning_lane or ""),) if lane_filter else ()
            rows = connection.execute(
                """
                SELECT * FROM reasoning_engine_jobs
                WHERE deployment_id = %s
                  AND job_status IN ('queued', 'retry', 'awaiting_world_projection')
                  AND (available_at = '' OR available_at <= %s)
                  AND (lease_expires_at = '' OR lease_expires_at < %s)
                """ + boundary_filter + lane_filter + """
                ORDER BY priority DESC, CASE reasoning_lane WHEN 'REALTIME' THEN 0
                    WHEN 'CONTEXT' THEN 1 ELSE 2 END, created_at, job_id
                LIMIT %s FOR UPDATE SKIP LOCKED
                """,
                (str(deployment_id or ""), stamp, stamp, *lane_params, bounded),
            ).fetchall()
            job_ids = [str(row.get("job_id") or "") for row in rows or []]
            if not job_ids:
                return []
            placeholders = ",".join(["%s"] * len(job_ids))
            connection.execute(
                "UPDATE reasoning_engine_jobs SET job_status = 'processing', lease_owner = %s, "
                "lease_expires_at = %s, heartbeat_at = %s, claimed_at = %s, "
                "current_stage = 'claimed', stage_started_at = %s, stage_updated_at = %s, "
                "stage_details_json = NULL, "
                "queue_wait_ms = TIMESTAMPDIFF(MICROSECOND, STR_TO_DATE(REPLACE(REPLACE(created_at, 'T', ' '), 'Z', ''), '%%Y-%%m-%%d %%H:%%i:%%s.%%f'), UTC_TIMESTAMP(6)) DIV 1000, "
                "updated_at = %s WHERE job_id IN (" + placeholders + ")",
                (
                    str(worker_id or "reasoning-v2"), lease_until, stamp, stamp,
                    stamp, stamp, stamp, *job_ids,
                ),
            )
        return [self.row_payload(row) for row in rows or []]

    def bind_release(
        self,
        job_ids: Iterable[str],
        release_identity: Mapping[str, object],
        reasoning_lane: str,
    ) -> None:
        selected = [str(job_id or "") for job_id in job_ids or [] if str(job_id or "")]
        if not selected:
            return
        release = dict(release_identity or {})
        placeholders = ",".join(["%s"] * len(selected))
        with self.connect() as connection:
            connection.execute(
                "UPDATE reasoning_engine_jobs SET release_fingerprint = %s, "
                "validation_cohort_id = %s, runtime_revision = %s, reasoning_lane = %s "
                "WHERE job_id IN (" + placeholders + ")",
                (
                    str(release.get("releaseFingerprint") or "")[:64],
                    str(release.get("validationCohortId") or "")[:96],
                    str(release.get("runtimeRevision") or "")[:64],
                    str(reasoning_lane or "CONTEXT")[:32],
                    *selected,
                ),
            )

    def heartbeat(
        self,
        job_ids: Iterable[str],
        worker_id: str,
        lease_seconds: int,
        progress: Mapping[str, object] = None,
    ) -> bool:
        selected = [str(job_id or "") for job_id in job_ids or [] if str(job_id or "")]
        if not selected:
            return False
        stamp = iso_utc()
        lease_until = iso_utc(utc_now() + timedelta(seconds=max(60, int(lease_seconds or 600))))
        progress_values = dict(progress or {})
        stage = str(progress_values.get("stage") or "")[:96]
        details = dict(progress_values.get("details") or {})
        placeholders = ",".join(["%s"] * len(selected))
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE reasoning_engine_jobs SET heartbeat_at = %s, lease_expires_at = %s, "
                "stage_started_at = CASE WHEN current_stage <> %s THEN %s ELSE stage_started_at END, "
                "current_stage = %s, stage_updated_at = %s, stage_details_json = %s, "
                "updated_at = %s WHERE job_status = 'processing' AND lease_owner = %s "
                "AND job_id IN (" + placeholders + ")",
                (
                    stamp, lease_until, stage, stamp, stage, stamp,
                    canonical_json(details), stamp, str(worker_id or ""), *selected,
                ),
            )
        return int(getattr(cursor, "rowcount", 0) or 0) == len(selected)

    def complete(self, job_id: str, result: Mapping[str, object], worker_id: str = "") -> None:
        stamp = iso_utc()
        values = dict(result or {})
        with self.connect() as connection:
            where = "job_id = %s"
            params = [str(job_id or "")]
            if str(worker_id or ""):
                where += " AND job_status = 'processing' AND lease_owner = %s"
                params.append(str(worker_id or ""))
            cursor = connection.execute(
                """
                UPDATE reasoning_engine_jobs
                SET job_status = 'completed', result_json = %s,
                    duration_ms = %s, lease_owner = '', lease_expires_at = '',
                    heartbeat_at = '', current_stage = '', stage_started_at = '',
                    stage_updated_at = '', stage_details_json = NULL,
                    last_error = '', completed_at = %s, updated_at = %s
                WHERE """ + where,
                (
                    canonical_json(values),
                    max(0, int(values.get("duration_ms") or values.get("durationMs") or 0)),
                    stamp,
                    stamp,
                    *params,
                ),
            )
            if str(worker_id or "") and int(getattr(cursor, "rowcount", 0) or 0) != 1:
                raise RuntimeError("The V2 reasoning job lease was lost before completion publication.")

    def defer(self, job_id: str, reason: str, retry_after_seconds: int = 15) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE reasoning_engine_jobs
                SET job_status = 'queued', lease_owner = '', lease_expires_at = '',
                    heartbeat_at = '', current_stage = '', stage_started_at = '',
                    stage_updated_at = '', stage_details_json = NULL,
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

    def await_world_projection(
        self,
        job_id: str,
        result: Mapping[str, object],
        reason: str,
        retry_after_seconds: int = 30,
        max_attempts: int = 5,
    ) -> Dict[str, object]:
        """Park a tracked subject whose SharedPremiseWorld is not ready.

        This is an expected dependency state, not a generic execution error.
        The bounded counter prevents a deterministic TypeDB projection defect
        from becoming an immortal queue item.
        """

        with self.transaction() as connection:
            row = connection.execute(
                "SELECT attempts FROM reasoning_engine_jobs WHERE job_id = %s FOR UPDATE",
                (str(job_id or ""),),
            ).fetchone() or {"attempts": 0}
            attempts = int(row.get("attempts") or 0) + 1
            terminal = attempts >= max(1, int(max_attempts or 5))
            base_delay = max(10, int(retry_after_seconds or 30))
            delay = min(900, base_delay * (2 ** min(5, attempts - 1)))
            stamp = iso_utc()
            values = dict(result or {})
            values.update({
                "status": "failed" if terminal else "awaiting_world_projection",
                "retryable": not terminal,
                "retry_after_seconds": 0 if terminal else delay,
                "reason": str(reason or values.get("reason") or "SharedPremiseWorld projection is pending.")[:500],
                "reason_code": str(
                    values.get("reason_code")
                    or values.get("reasonCode")
                    or "shared-premise-world-projection-pending"
                )[:96],
                "world_projection_attempt": attempts,
                "world_projection_max_attempts": max(1, int(max_attempts or 5)),
            })
            connection.execute(
                """
                UPDATE reasoning_engine_jobs
                SET job_status = %s, attempts = %s, result_json = %s,
                    lease_owner = '', lease_expires_at = '', heartbeat_at = '',
                    current_stage = '', stage_started_at = '', stage_updated_at = '',
                    stage_details_json = NULL,
                    available_at = %s, last_error = %s, completed_at = %s,
                    updated_at = %s
                WHERE job_id = %s
                """,
                (
                    "failed" if terminal else "awaiting_world_projection",
                    attempts,
                    canonical_json(values),
                    "" if terminal else iso_utc(utc_now() + timedelta(seconds=delay)),
                    str(reason or "")[:500] if terminal else "",
                    stamp if terminal else "",
                    stamp,
                    str(job_id or ""),
                ),
            )
        return {
            "jobId": str(job_id or ""),
            "attemptCount": attempts,
            "terminal": terminal,
            "retryAfterSeconds": 0 if terminal else delay,
        }

    def exclude(
        self,
        job_id: str,
        result: Mapping[str, object],
        reason: str,
        reason_code: str = "reasoning-scope-not-applicable",
    ) -> None:
        """Finish a durable request that has no applicable investment scope."""

        stamp = iso_utc()
        values = dict(result or {})
        values.update({
            "status": "excluded",
            "retryable": False,
            "retry_after_seconds": 0,
            "reason": str(reason or values.get("reason") or "Reasoning scope is not applicable.")[:500],
            "reason_code": str(
                reason_code
                or values.get("reason_code")
                or values.get("reasonCode")
                or "reasoning-scope-not-applicable"
            )[:96],
        })
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE reasoning_engine_jobs
                SET job_status = 'excluded', result_json = %s,
                    lease_owner = '', lease_expires_at = '', heartbeat_at = '',
                    current_stage = '', stage_started_at = '', stage_updated_at = '',
                    stage_details_json = NULL,
                    available_at = '', last_error = '', completed_at = %s,
                    duration_ms = %s, updated_at = %s
                WHERE job_id = %s
                """,
                (
                    canonical_json(values),
                    stamp,
                    max(0, int(values.get("duration_ms") or values.get("durationMs") or 0)),
                    stamp,
                    str(job_id or ""),
                ),
            )

    def fail(
        self,
        job_id: str,
        result: Mapping[str, object],
        reason: str,
        reason_code: str = "reasoning-execution-failed",
    ) -> None:
        """Finish a durable request after a non-transient execution failure."""

        stamp = iso_utc()
        values = dict(result or {})
        values.update({
            "status": "failed",
            "retryable": False,
            "retry_after_seconds": 0,
            "reason": str(reason or values.get("reason") or "Reasoning execution failed.")[:500],
            "reason_code": str(
                reason_code
                or values.get("reason_code")
                or values.get("reasonCode")
                or "reasoning-execution-failed"
            )[:96],
        })
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE reasoning_engine_jobs
                SET job_status = 'failed', result_json = %s,
                    lease_owner = '', lease_expires_at = '', heartbeat_at = '',
                    current_stage = '', stage_started_at = '', stage_updated_at = '',
                    stage_details_json = NULL,
                    available_at = '', last_error = %s, completed_at = %s,
                    duration_ms = %s, updated_at = %s
                WHERE job_id = %s
                """,
                (
                    canonical_json(values),
                    str(reason or "")[:500],
                    stamp,
                    max(0, int(values.get("duration_ms") or values.get("durationMs") or 0)),
                    stamp,
                    str(job_id or ""),
                ),
            )

    def supersede(self, job_id: str, reason: str) -> None:
        stamp = iso_utc()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE reasoning_engine_jobs
                SET job_status = 'superseded', lease_owner = '', lease_expires_at = '',
                    heartbeat_at = '', current_stage = '', stage_started_at = '',
                    stage_updated_at = '', stage_details_json = NULL,
                    available_at = '', last_error = %s,
                    completed_at = %s, updated_at = %s
                WHERE job_id = %s
                """,
                (str(reason or "")[:500], stamp, stamp, str(job_id or "")),
            )

    def supersede_pending_deployment(
        self,
        deployment_id: str,
        reason: str,
    ) -> Dict[str, object]:
        """Terminalize work for a deployment that no worker can consume."""

        stamp = iso_utc()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE reasoning_engine_jobs
                SET job_status = 'superseded', lease_owner = '', lease_expires_at = '',
                    heartbeat_at = '', current_stage = '', stage_started_at = '',
                    stage_updated_at = '', stage_details_json = NULL,
                    available_at = '', last_error = %s,
                    completed_at = %s, updated_at = %s
                WHERE deployment_id = %s
                  AND job_status IN ('queued', 'retry', 'awaiting_source', 'awaiting_world_projection')
                """,
                (
                    str(reason or "")[:500],
                    stamp,
                    stamp,
                    str(deployment_id or ""),
                ),
            )
        return {
            "status": "superseded",
            "deploymentId": str(deployment_id or ""),
            "supersededCount": int(getattr(cursor, "rowcount", 0) or 0),
        }

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
                    lease_expires_at = '', heartbeat_at = '', current_stage = '',
                    stage_started_at = '', stage_updated_at = '', stage_details_json = NULL,
                    available_at = %s, last_error = %s,
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

    def live_queue_state(self, deployment_id: str) -> Dict[str, object]:
        """Return the small TypeDB-writer queue view used by background jobs."""

        clean_deployment_id = str(deployment_id or "").strip()
        if not clean_deployment_id:
            return {
                "status": "not-configured",
                "deploymentId": "",
                "effectivePendingCount": 0,
            }
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT job_status, COUNT(*) AS row_count, MIN(created_at) AS oldest "
                "FROM reasoning_engine_jobs WHERE deployment_id = %s "
                "AND job_status IN ('queued', 'retry', 'processing', 'awaiting_world_projection') "
                "GROUP BY job_status",
                (clean_deployment_id,),
            ).fetchall()
        counts = {
            str(row.get("job_status") or ""): int(row.get("row_count") or 0)
            for row in rows or []
        }
        oldest_values = [
            str(row.get("oldest") or "")
            for row in rows or []
            if str(row.get("job_status") or "") in {"queued", "retry", "processing"}
            and str(row.get("oldest") or "")
        ]
        waiting_oldest_values = [
            str(row.get("oldest") or "")
            for row in rows or []
            if str(row.get("job_status") or "") == "awaiting_world_projection"
            and str(row.get("oldest") or "")
        ]
        pending = sum(counts.get(status, 0) for status in ["queued", "retry", "processing"])
        return {
            "status": "active" if pending else "waiting" if counts.get("awaiting_world_projection", 0) else "idle",
            "probeMode": "versioned-reasoning-live-queue-v1",
            "deploymentId": clean_deployment_id,
            "effectivePendingCount": pending,
            "pendingCount": pending,
            "queuedCount": counts.get("queued", 0),
            "retryingCount": counts.get("retry", 0),
            "processingCount": counts.get("processing", 0),
            "awaitingWorldProjectionCount": counts.get("awaiting_world_projection", 0),
            "oldestRequestAt": min(oldest_values) if oldest_values else "",
            "oldestAwaitingWorldProjectionAt": (
                min(waiting_oldest_values) if waiting_oldest_values else ""
            ),
        }

    @staticmethod
    def percentile(values: Iterable[int], percentile: float = 0.95) -> int:
        ordered = sorted(max(0, int(value or 0)) for value in values or [])
        if not ordered:
            return 0
        index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.999999)))
        return ordered[index]

    def summary(
        self,
        deployment_id: str = "",
        lookback: int = 200,
        release_fingerprint: str = "",
        validation_cohort_id: str = "",
    ) -> Dict[str, object]:
        params = []
        where = ""
        if str(deployment_id or ""):
            where = " WHERE deployment_id = %s"
            params.append(str(deployment_id or ""))
        cohort_conditions = []
        cohort_params = list(params)
        if str(release_fingerprint or ""):
            cohort_conditions.append("release_fingerprint = %s")
            cohort_params.append(str(release_fingerprint or ""))
        if str(validation_cohort_id or ""):
            cohort_conditions.append("validation_cohort_id = %s")
            cohort_params.append(str(validation_cohort_id or ""))
        cohort_where = where
        for condition in cohort_conditions:
            cohort_where += (" AND " if cohort_where else " WHERE ") + condition
        with self.connect() as connection:
            counts = connection.execute(
                "SELECT job_status, COUNT(*) AS row_count, MIN(created_at) AS oldest, MAX(updated_at) AS latest "
                "FROM reasoning_engine_jobs" + where + " GROUP BY job_status",
                tuple(params),
            ).fetchall()
            cohort_counts = connection.execute(
                "SELECT job_status, COUNT(*) AS row_count FROM reasoning_engine_jobs"
                + cohort_where
                + " GROUP BY job_status",
                tuple(cohort_params),
            ).fetchall()
            completed_where = cohort_where + (" AND" if cohort_where else " WHERE") + " job_status = 'completed'"
            rows = connection.execute(
                "SELECT * FROM reasoning_engine_jobs" + completed_where + " ORDER BY completed_at DESC LIMIT %s",
                (*cohort_params, max(1, min(2000, int(lookback or 200)))),
            ).fetchall()
            pending_where = where + (" AND" if where else " WHERE") + " job_status IN ('queued', 'retry', 'processing')"
            pending_lanes = connection.execute(
                "SELECT job_status, reasoning_lane, current_stage, "
                "COUNT(*) AS row_count, MIN(created_at) AS oldest, "
                "MIN(stage_started_at) AS stage_started_at, MAX(stage_updated_at) AS stage_updated_at "
                "FROM reasoning_engine_jobs" + pending_where
                + " GROUP BY job_status, reasoning_lane, current_stage",
                tuple(params),
            ).fetchall()
        count_map = {str(row.get("job_status") or ""): int(row.get("row_count") or 0) for row in counts or []}
        cohort_count_map = {
            str(row.get("job_status") or ""): int(row.get("row_count") or 0)
            for row in cohort_counts or []
        }
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
        stage_values: Dict[str, list] = {}
        for result in results:
            stages = result.get("stage_durations_ms") or result.get("stageDurationsMs") or {}
            if not isinstance(stages, Mapping):
                continue
            for stage, value in stages.items():
                try:
                    stage_values.setdefault(str(stage or "unknown"), []).append(int(value or 0))
                except (TypeError, ValueError):
                    continue
        pending_by_lane = {}
        oldest_pending_by_lane = {}
        processing_stages = {}
        now = utc_now()
        for row in pending_lanes or []:
            lane = str(row.get("reasoning_lane") or "unclassified")
            pending_by_lane[lane] = (
                int(pending_by_lane.get(lane) or 0)
                + int(row.get("row_count") or 0)
            )
            oldest_at = str(row.get("oldest") or "")
            age = 0
            try:
                parsed = datetime.fromisoformat(oldest_at.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                age = max(0, int((now - parsed.astimezone(timezone.utc)).total_seconds()))
            except ValueError:
                pass
            previous = dict(oldest_pending_by_lane.get(lane) or {})
            if not previous or oldest_at < str(previous.get("createdAt") or oldest_at):
                oldest_pending_by_lane[lane] = {"createdAt": oldest_at, "ageSeconds": age}
            if str(row.get("job_status") or "") == "processing":
                stage = str(row.get("current_stage") or "unknown")
                previous_stage = dict(processing_stages.get(stage) or {})
                processing_stages[stage] = {
                    "count": int(previous_stage.get("count") or 0) + int(row.get("row_count") or 0),
                    "startedAt": min(
                        value for value in [
                            str(previous_stage.get("startedAt") or ""),
                            str(row.get("stage_started_at") or ""),
                        ] if value
                    ) if previous_stage.get("startedAt") or row.get("stage_started_at") else "",
                    "updatedAt": str(row.get("stage_updated_at") or ""),
                }
        return {
            "deploymentId": str(deployment_id or ""),
            "releaseFingerprint": str(release_fingerprint or ""),
            "validationCohortId": str(validation_cohort_id or ""),
            "counts": count_map,
            "cohortCounts": cohort_count_map,
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
            "stageDurationP95Ms": {
                stage: self.percentile(values)
                for stage, values in sorted(stage_values.items())
            },
            "failureCount": int(cohort_count_map.get("failed") or 0),
            "pendingCount": sum(int(count_map.get(status) or 0) for status in ["queued", "retry", "processing"]),
            "awaitingSourceCount": int(count_map.get("awaiting_source") or 0),
            "awaitingWorldProjectionCount": int(count_map.get("awaiting_world_projection") or 0),
            "excludedCount": int(count_map.get("excluded") or 0),
            "oldestPendingAgeSeconds": oldest_pending_age,
            "pendingByLane": pending_by_lane,
            "oldestPendingByLane": oldest_pending_by_lane,
            "processingStages": processing_stages,
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
            "sourceSnapshotId": str(values.get("source_snapshot_id") or ""),
            "sourceSnapshotAt": str(values.get("source_snapshot_at") or ""),
            "sourceBoundaries": json_value(values.get("source_boundary_json"), []),
            "sourcePayloadHash": str(values.get("source_payload_hash") or ""),
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
            "releaseFingerprint": str(values.get("release_fingerprint") or ""),
            "validationCohortId": str(values.get("validation_cohort_id") or ""),
            "runtimeRevision": str(values.get("runtime_revision") or ""),
            "reasoningLane": str(values.get("reasoning_lane") or ""),
            "heartbeatAt": str(values.get("heartbeat_at") or ""),
            "currentStage": str(values.get("current_stage") or ""),
            "stageStartedAt": str(values.get("stage_started_at") or ""),
            "stageUpdatedAt": str(values.get("stage_updated_at") or ""),
            "stageDetails": json_value(values.get("stage_details_json"), {}),
            "availableAt": str(values.get("available_at") or ""),
            "lastError": str(values.get("last_error") or ""),
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
