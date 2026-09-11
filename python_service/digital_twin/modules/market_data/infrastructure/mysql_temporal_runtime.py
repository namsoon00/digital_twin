import uuid
from datetime import timedelta
from typing import Dict, Iterable, List, Mapping, Optional
from digital_twin.domain.time_series_storage import TemporalFeatureSnapshot, TimeSeriesBackendDescriptor, backend_transition_allowed, canonical_json, clean_status, compacted_legacy_window_reference, is_temporal_window_reference, payload_fingerprint, temporal_window_reference
from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection

from digital_twin.infrastructure.storage_values import utc_now
from digital_twin.infrastructure.storage_values import iso_utc
from digital_twin.infrastructure.storage_values import json_value

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

    def failover_active(self, expected_active_backend_id: str, fallback_backend_id: str) -> Dict[str, object]:
        """Atomically demote an unavailable active backend and activate its fallback."""

        expected = str(expected_active_backend_id or "").strip()
        fallback = str(fallback_backend_id or "").strip()
        stamp = iso_utc()
        with self.transaction() as connection:
            control = connection.execute(
                "SELECT * FROM time_series_backend_control WHERE control_id = 'global' FOR UPDATE"
            ).fetchone() or {}
            active = str(control.get("active_backend_id") or "")
            if active != expected:
                raise RuntimeError("Time-series active backend changed concurrently")
            rows = connection.execute(
                "SELECT backend_id FROM time_series_backend_deployments "
                "WHERE backend_id IN (%s, %s) FOR UPDATE",
                (expected, fallback),
            ).fetchall()
            known = {str(row.get("backend_id") or "") for row in rows or []}
            if expected not in known or fallback not in known:
                raise ValueError("Time-series failover backend is not registered")
            connection.execute(
                "UPDATE time_series_backend_deployments SET deployment_status = 'candidate', updated_at = %s "
                "WHERE backend_id = %s",
                (stamp, expected),
            )
            connection.execute(
                "UPDATE time_series_backend_deployments SET deployment_status = 'active', updated_at = %s "
                "WHERE backend_id = %s",
                (stamp, fallback),
            )
            connection.execute(
                "UPDATE time_series_backend_control SET active_backend_id = %s, shadow_backend_id = %s, "
                "candidate_backend_id = %s, version = version + 1, updated_at = %s WHERE control_id = 'global'",
                (fallback, expected, expected, stamp),
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
                "AND ((job_status IN ('queued', 'retry') "
                "AND (available_at = '' OR available_at <= %s)) "
                # Projection writes are idempotent. Reclaiming an expired
                # processing lease closes the crash window between write and receipt.
                "OR (job_status = 'processing' AND (lease_until = '' OR lease_until < %s))) "
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
                    payload_json = '{}', last_error = '', updated_at = %s
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

    def cancel_backend_pending(self, backend_id: str, reason: str) -> int:
        """Discard replayable replica work after the canonical read path fails over."""

        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE time_series_projection_outbox SET job_status = 'cancelled', payload_json = '{}', "
                "lease_owner = '', lease_until = '', available_at = '', last_error = %s, updated_at = %s "
                "WHERE backend_id = %s AND job_status IN ('queued', 'retry', 'processing')",
                (str(reason or "projection-cancelled")[:255], iso_utc(), str(backend_id or "")),
            )
        return int(getattr(cursor, "rowcount", 0) or 0)

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
                    available_at = %s, last_error = %s,
                    payload_json = CASE WHEN %s = 1 THEN '{}' ELSE payload_json END,
                    updated_at = %s
                WHERE job_id = %s
                """,
                (
                    "failed" if terminal else "retry",
                    attempts,
                    "" if terminal else iso_utc(utc_now() + timedelta(seconds=delay)),
                    str(error or "")[:255],
                    1 if terminal else 0,
                    iso_utc(),
                    str(job_id or ""),
                ),
            )
        return {"jobId": str(job_id or ""), "attemptCount": attempts, "terminal": terminal}

    def compact_terminal_payloads(self, limit: int = 5000) -> int:
        """Drop replay payloads from terminal replica jobs; MySQL remains canonical."""

        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE time_series_projection_outbox SET payload_json = '{}', updated_at = %s "
                "WHERE job_status IN ('failed', 'cancelled') AND payload_json <> '{}' "
                "ORDER BY updated_at, job_id LIMIT %s",
                (iso_utc(), max(1, min(20000, int(limit or 5000)))),
            )
        return int(getattr(cursor, "rowcount", 0) or 0)

    def blocking_counts(self, backend_id: str) -> Dict[str, int]:
        """Return only work that can still affect candidate parity or delivery.

        Terminal failures whose replay payload was compacted remain as audit
        history, but a successful full backfill and parity comparison supersede
        them. They must not permanently prevent a repaired backend promotion.
        """

        with self.connect() as connection:
            rows = connection.execute(
                "SELECT job_status, COUNT(*) AS count FROM time_series_projection_outbox "
                "WHERE backend_id = %s AND ("
                "job_status IN ('queued', 'processing', 'retry') OR "
                "(job_status = 'failed' AND payload_json <> '{}')) "
                "GROUP BY job_status",
                (str(backend_id or ""),),
            ).fetchall()
        return {
            str(row.get("job_status") or ""): int(row.get("count") or 0)
            for row in rows or []
            if str(row.get("job_status") or "")
        }

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
        window_reference = temporal_window_reference(snapshot.windows)
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
                    canonical_json(window_reference),
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
        stored_windows = json_value(row.get("windows_json"), {})
        referenced = is_temporal_window_reference(stored_windows)
        return {
            "snapshotId": str(row.get("snapshot_id") or ""),
            "featureSetVersion": str(row.get("feature_set_version") or ""),
            "backendId": str(row.get("backend_id") or ""),
            "accountId": str(row.get("account_id") or ""),
            "asOf": str(row.get("as_of") or ""),
            "watermark": json_value(row.get("watermark_json"), {}),
            "symbols": json_value(row.get("symbols_json"), []),
            "windows": {} if referenced else stored_windows,
            "windowReference": stored_windows if referenced else {},
            "storageMode": str(stored_windows.get("storageMode") or "legacy-inline-windows"),
            "payloadHash": str(row.get("payload_hash") or ""),
            "createdAt": str(row.get("created_at") or ""),
        }

    def compact_legacy_windows(
        self,
        limit: int = 100,
        apply: bool = False,
        after_snapshot_id: str = "",
    ) -> Dict[str, object]:
        """Replace legacy inline windows with point-in-time references.

        This is an explicit maintenance operation. It changes only replay
        storage representation; snapshot ids and full-payload hashes remain
        unchanged and continue to bind statistical evidence.
        """

        bounded_limit = max(1, min(1000, int(limit or 100)))
        cursor_id = str(after_snapshot_id or "")
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT snapshot_id, symbols_json, payload_hash, as_of, "
                "OCTET_LENGTH(windows_json) AS windows_bytes "
                "FROM temporal_feature_snapshots "
                "WHERE snapshot_id > %s "
                "AND OCTET_LENGTH(windows_json) > 4096 "
                "AND windows_json NOT LIKE %s "
                "ORDER BY snapshot_id LIMIT %s",
                (cursor_id, '%\"storageMode\":\"temporal-window-reference-v1\"%', bounded_limit),
            ).fetchall()
            candidates = []
            parse_failures = []
            for row in rows or []:
                snapshot_id = str(row.get("snapshot_id") or "")
                windows_bytes = int(row.get("windows_bytes") or 0)
                if not snapshot_id or windows_bytes <= 0:
                    continue
                try:
                    compact = canonical_json(compacted_legacy_window_reference(
                        json_value(row.get("symbols_json"), []),
                        row.get("payload_hash"),
                        row.get("as_of"),
                        windows_bytes,
                    ))
                except Exception as error:  # noqa: BLE001 - report a damaged legacy row without stopping the batch.
                    parse_failures.append({"snapshotId": snapshot_id, "reason": str(error)[:180]})
                    continue
                candidates.append((snapshot_id, windows_bytes, str(row.get("payload_hash") or ""), compact))
            updated = 0
            if apply:
                for snapshot_id, windows_bytes, payload_hash, compact in candidates:
                    cursor = connection.execute(
                        "UPDATE temporal_feature_snapshots SET windows_json = %s "
                        "WHERE snapshot_id = %s AND payload_hash = %s "
                        "AND OCTET_LENGTH(windows_json) = %s "
                        "AND windows_json NOT LIKE %s",
                        (
                            compact,
                            snapshot_id,
                            payload_hash,
                            windows_bytes,
                            '%\"storageMode\":\"temporal-window-reference-v1\"%',
                        ),
                    )
                    updated += int(getattr(cursor, "rowcount", 0) or 0)
        bytes_before = sum(windows_bytes for _, windows_bytes, _, _ in candidates)
        bytes_after = sum(len(compact.encode("utf-8")) for _, _, _, compact in candidates)
        return {
            "status": "compacted" if apply else "preview",
            "apply": bool(apply),
            "candidateCount": len(candidates),
            "updatedCount": updated,
            "bytesBefore": bytes_before,
            "bytesAfter": bytes_after,
            "reclaimableBytes": max(0, bytes_before - bytes_after),
            "parseFailures": parse_failures,
            "hasMore": len(rows or []) >= bounded_limit,
            "nextCursor": str((rows or [{}])[-1].get("snapshot_id") or cursor_id),
        }
