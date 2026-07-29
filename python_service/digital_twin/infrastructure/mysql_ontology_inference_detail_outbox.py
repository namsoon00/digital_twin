"""Durable deferred readback queue for TypeDB InferenceBox detail.

The realtime projection path proves the active generation with small TypeDB
markers and can deliver its in-memory native result immediately.  This outbox
retains only immutable generation identifiers so the more expensive complete
InferenceBox expansion can run when live reasoning work is idle.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Dict, List, Mapping

from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps
from .settings import utc_now


PENDING = "pending"
PROCESSING = "processing"
COMPLETED = "completed"
FAILED = "failed"
SUPERSEDED = "superseded"


def _clean(value: object) -> str:
    return str(value or "").strip()


def _clean_symbols(values: object) -> List[str]:
    items = values if isinstance(values, (list, tuple, set)) else [values]
    return sorted({
        _clean(value).upper()
        for value in items
        if _clean(value)
    })[:200]


def _sha(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _timestamp_after(seconds: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=max(0, int(seconds or 0)))
    ).isoformat().replace("+00:00", "Z")


class MySQLOntologyInferenceDetailOutboxStore(MySQLOperationalConnection):
    """Store latest detailed-readback work per PortfolioWorld.

    A pending job is coalesced to the newest active generation for its world.
    A claimed older job may still finish, but the worker recognizes its
    generation mismatch and records it as superseded instead of reading or
    persisting stale detail.
    """

    def max_result_bytes(self) -> int:
        try:
            value = int(float(str(
                self.runtime_settings.get("ontologyInferenceDetailMaxResultBytes")
                or 5 * 1024 * 1024
            )))
        except (TypeError, ValueError):
            value = 5 * 1024 * 1024
        return max(128 * 1024, min(32 * 1024 * 1024, value))

    def enqueue(
        self,
        world_id: str,
        account_id: str,
        inference_generation_id: str,
        source_abox_snapshot_id: str,
        target_symbols: List[str] = None,
        projection_run_id: str = "",
        detail_limit: int = 80,
    ) -> Dict[str, object]:
        world = _clean(world_id)
        generation = _clean(inference_generation_id)
        source_abox = _clean(source_abox_snapshot_id)
        targets = _clean_symbols(target_symbols)
        if not (world and generation and source_abox):
            return {
                "status": "deferred-incomplete-inference-detail-identity",
                "saved": False,
                "eventuallyConsistent": False,
                "worldId": world,
                "inferenceGenerationId": generation,
                "sourceAboxSnapshotId": source_abox,
                "reason": "world, inference generation, and source ABox identities are required.",
            }
        bounded_limit = max(1, min(500, int(detail_limit or 80)))
        dedupe_key = _sha(world)[:64]
        fingerprint = _sha("|".join([
            dedupe_key,
            generation,
            source_abox,
            ",".join(targets),
            str(bounded_limit),
        ]))
        job_id = "inference-detail:" + fingerprint[:48]
        stamp = utc_now()
        targets_json = json_dumps(targets)
        with self.transaction() as connection:
            completed = connection.execute(
                """
                SELECT job_id, completed_at FROM ontology_inference_detail_outbox
                WHERE dedupe_key = %s AND inference_generation_id = %s
                    AND source_abox_snapshot_id = %s AND status = %s
                ORDER BY updated_at DESC LIMIT 1
                """,
                (dedupe_key, generation, source_abox, COMPLETED),
            ).fetchone()
            if completed:
                return {
                    "status": "already-captured-inference-detail",
                    "saved": False,
                    "eventuallyConsistent": True,
                    "worldId": world,
                    "inferenceGenerationId": generation,
                    "sourceAboxSnapshotId": source_abox,
                    "jobId": _clean(completed.get("job_id")),
                    "completedAt": _clean(completed.get("completed_at")),
                }
            pending = connection.execute(
                """
                SELECT job_id FROM ontology_inference_detail_outbox
                WHERE dedupe_key = %s AND status = %s
                ORDER BY updated_at DESC LIMIT 1 FOR UPDATE
                """,
                (dedupe_key, PENDING),
            ).fetchone()
            if pending:
                active_job_id = _clean(pending.get("job_id"))
                connection.execute(
                    """
                    UPDATE ontology_inference_detail_outbox
                    SET account_id = %s, inference_generation_id = %s,
                        source_abox_snapshot_id = %s, target_symbols_json = %s,
                        projection_run_id = %s, detail_limit = %s,
                        available_at = %s, last_error = '', updated_at = %s
                    WHERE job_id = %s AND status = %s
                    """,
                    (
                        _clean(account_id), generation, source_abox, targets_json,
                        _clean(projection_run_id), bounded_limit, stamp, stamp,
                        active_job_id, PENDING,
                    ),
                )
                return {
                    "status": "queued-coalesced-inference-detail",
                    "saved": True,
                    "eventuallyConsistent": True,
                    "worldId": world,
                    "inferenceGenerationId": generation,
                    "sourceAboxSnapshotId": source_abox,
                    "jobId": active_job_id,
                    "coalescedPendingUpdate": True,
                }
            connection.execute(
                """
                INSERT INTO ontology_inference_detail_outbox (
                    job_id, dedupe_key, world_id, account_id,
                    inference_generation_id, source_abox_snapshot_id,
                    target_symbols_json, projection_run_id, detail_limit,
                    status, attempts, available_at, lease_owner,
                    lease_expires_at, last_error, result_json,
                    created_at, updated_at, completed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, 0, %s, '', '', '', '{}', %s, %s, '')
                """,
                (
                    job_id, dedupe_key, world, _clean(account_id), generation,
                    source_abox, targets_json, _clean(projection_run_id),
                    bounded_limit, PENDING, stamp, stamp, stamp,
                ),
            )
        return {
            "status": "queued-inference-detail",
            "saved": True,
            "eventuallyConsistent": True,
            "worldId": world,
            "inferenceGenerationId": generation,
            "sourceAboxSnapshotId": source_abox,
            "jobId": job_id,
            "coalescedPendingUpdate": False,
        }

    def claim(self, worker_id: str, limit: int = 1, lease_seconds: int = 300) -> List[Dict[str, object]]:
        worker = _clean(worker_id) or "ontology-inference-detail"
        bounded = max(1, min(20, int(limit or 1)))
        stamp = utc_now()
        lease_expires = _timestamp_after(max(30, min(3600, int(lease_seconds or 300))))
        claimed: List[Dict[str, object]] = []
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE ontology_inference_detail_outbox
                SET status = %s, lease_owner = '', lease_expires_at = '', updated_at = %s
                WHERE status = %s AND lease_expires_at != '' AND lease_expires_at < %s
                """,
                (PENDING, stamp, PROCESSING, stamp),
            )
            rows = connection.execute(
                """
                SELECT * FROM ontology_inference_detail_outbox
                WHERE status = %s AND available_at <= %s
                ORDER BY created_at ASC, job_id ASC LIMIT %s FOR UPDATE
                """,
                (PENDING, stamp, bounded),
            ).fetchall()
            for row in rows or []:
                job_id = _clean(row.get("job_id"))
                if not job_id:
                    continue
                cursor = connection.execute(
                    """
                    UPDATE ontology_inference_detail_outbox
                    SET status = %s, attempts = attempts + 1, lease_owner = %s,
                        lease_expires_at = %s, updated_at = %s
                    WHERE job_id = %s AND status = %s
                    """,
                    (PROCESSING, worker, lease_expires, stamp, job_id, PENDING),
                )
                if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                    continue
                claimed.append(self.row_payload({
                    **dict(row),
                    "status": PROCESSING,
                    "attempts": int(row.get("attempts") or 0) + 1,
                    "lease_owner": worker,
                    "lease_expires_at": lease_expires,
                }))
        return claimed

    def stored_result_json(self, result: Mapping[str, object] = None) -> str:
        values = dict(result or {})
        encoded = json_dumps(values)
        result_bytes = len(encoded.encode("utf-8"))
        if result_bytes <= self.max_result_bytes():
            return encoded
        inference = values.get("inferenceBox") if isinstance(values.get("inferenceBox"), dict) else {}
        compact = {
            "status": str(values.get("status") or ""),
            "detailTruncated": True,
            "resultBytes": result_bytes,
            "maxResultBytes": self.max_result_bytes(),
            "inferenceGenerationId": str(inference.get("inferenceGenerationId") or values.get("inferenceGenerationId") or ""),
            "sourceAboxSnapshotId": str(inference.get("sourceAboxSnapshotId") or values.get("sourceAboxSnapshotId") or ""),
            "targetSymbols": _clean_symbols(inference.get("targetSymbols") or values.get("targetSymbols") or []),
            "relationCount": int(inference.get("relationCount") or 0),
            "traceCount": int(inference.get("traceCount") or len(inference.get("traces") or [])),
            "reason": "Detailed InferenceBox payload exceeded the durable outbox size ceiling.",
        }
        return json_dumps(compact)

    def complete(
        self,
        job_id: str,
        worker_id: str,
        result: Mapping[str, object] = None,
        terminal_status: str = COMPLETED,
    ) -> bool:
        status = _clean(terminal_status).lower()
        if status not in {COMPLETED, SUPERSEDED}:
            status = COMPLETED
        stamp = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE ontology_inference_detail_outbox
                SET status = %s, lease_owner = '', lease_expires_at = '',
                    last_error = '', result_json = %s, updated_at = %s, completed_at = %s
                WHERE job_id = %s AND status = %s AND lease_owner = %s
                """,
                (
                    status, self.stored_result_json(result), stamp, stamp,
                    _clean(job_id), PROCESSING, _clean(worker_id),
                ),
            )
        return int(getattr(cursor, "rowcount", 0) or 0) == 1

    def retry(
        self,
        job_id: str,
        worker_id: str,
        reason: object,
        max_attempts: int = 8,
    ) -> Dict[str, object]:
        clean_job_id = _clean(job_id)
        clean_worker = _clean(worker_id)
        bounded_attempts = max(1, min(32, int(max_attempts or 8)))
        stamp = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT attempts FROM ontology_inference_detail_outbox
                WHERE job_id = %s AND status = %s AND lease_owner = %s FOR UPDATE
                """,
                (clean_job_id, PROCESSING, clean_worker),
            ).fetchone()
            if not row:
                return {"status": "lease-lost", "jobId": clean_job_id}
            attempts = int(row.get("attempts") or 0)
            terminal = attempts >= bounded_attempts
            delay_seconds = min(300, max(5, 2 ** min(8, attempts)))
            status = FAILED if terminal else PENDING
            available_at = "" if terminal else _timestamp_after(delay_seconds)
            connection.execute(
                """
                UPDATE ontology_inference_detail_outbox
                SET status = %s, available_at = %s, lease_owner = '', lease_expires_at = '',
                    last_error = %s, updated_at = %s
                WHERE job_id = %s
                """,
                (status, available_at, _clean(reason)[:1000], stamp, clean_job_id),
            )
        return {
            "status": status,
            "jobId": clean_job_id,
            "attempts": attempts,
            "retryAfterSeconds": 0 if terminal else delay_seconds,
        }

    def summary(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count, MIN(created_at) AS oldest_at
                FROM ontology_inference_detail_outbox GROUP BY status
                """
            ).fetchall()
        states = {
            _clean(row.get("status")): {
                "count": int(row.get("count") or 0),
                "oldestAt": _clean(row.get("oldest_at")),
            }
            for row in rows or []
        }
        return {
            "enabled": True,
            "states": states,
            "pendingCount": int((states.get(PENDING) or {}).get("count") or 0),
            "processingCount": int((states.get(PROCESSING) or {}).get("count") or 0),
            "failedCount": int((states.get(FAILED) or {}).get("count") or 0),
            "supersededCount": int((states.get(SUPERSEDED) or {}).get("count") or 0),
        }

    def requeue_failed(self, limit: int = 100) -> int:
        bounded = max(1, min(5000, int(limit or 100)))
        stamp = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE ontology_inference_detail_outbox
                SET status = %s, available_at = %s, lease_owner = '', lease_expires_at = '', updated_at = %s
                WHERE status = %s ORDER BY updated_at ASC LIMIT %s
                """,
                (PENDING, stamp, stamp, FAILED, bounded),
            )
        return int(getattr(cursor, "rowcount", 0) or 0)

    def prune_completed(self, retention_hours: int = 168, limit: int = 5000) -> int:
        bounded_hours = max(24, min(24 * 365, int(retention_hours or 168)))
        bounded_limit = max(1, min(50000, int(limit or 5000)))
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=bounded_hours)
        ).isoformat().replace("+00:00", "Z")
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM ontology_inference_detail_outbox
                WHERE status IN (%s, %s) AND completed_at != '' AND completed_at < %s
                ORDER BY completed_at ASC LIMIT %s
                """,
                (COMPLETED, SUPERSEDED, cutoff, bounded_limit),
            )
        return int(getattr(cursor, "rowcount", 0) or 0)

    @staticmethod
    def row_payload(row: Mapping[str, object]) -> Dict[str, object]:
        values = dict(row or {})
        return {
            "jobId": _clean(values.get("job_id")),
            "dedupeKey": _clean(values.get("dedupe_key")),
            "worldId": _clean(values.get("world_id")),
            "accountId": _clean(values.get("account_id")),
            "inferenceGenerationId": _clean(values.get("inference_generation_id")),
            "sourceAboxSnapshotId": _clean(values.get("source_abox_snapshot_id")),
            "targetSymbols": _clean_symbols(_json_loads(values.get("target_symbols_json"), [])),
            "projectionRunId": _clean(values.get("projection_run_id")),
            "detailLimit": int(values.get("detail_limit") or 80),
            "status": _clean(values.get("status")),
            "attempts": int(values.get("attempts") or 0),
            "availableAt": _clean(values.get("available_at")),
            "leaseOwner": _clean(values.get("lease_owner")),
            "leaseExpiresAt": _clean(values.get("lease_expires_at")),
            "lastError": _clean(values.get("last_error")),
            "result": _json_loads(values.get("result_json"), {}),
            "createdAt": _clean(values.get("created_at")),
            "updatedAt": _clean(values.get("updated_at")),
            "completedAt": _clean(values.get("completed_at")),
        }
