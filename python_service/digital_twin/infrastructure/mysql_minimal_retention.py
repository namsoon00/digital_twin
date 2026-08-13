"""MySQL adapter for the bounded minimal-data retention use case.

The adapter works from primary-key candidate lists.  It never scans or emits
stored payload text, and it does not touch current snapshots, credentials,
pending queues, or active evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import time
import uuid
from typing import Dict, List, Mapping, Sequence

from ..domain.mysql_minimal_retention import MySQLMinimalRetentionPolicy, policy_cutoffs
from .mysql_schema_tuning import quote_identifier


MYSQL_MINIMAL_RETENTION_LOCK_NAME = "orbit_alpha_minimal_mysql_retention"
TERMINAL_NOTIFICATION_STATUSES = ("done", "suppressed", "superseded", "sent")
TERMINAL_WORLD_PROJECTION_STATUSES = ("completed", "superseded")
# Research status values evolve with the collection workflow.  Completed-at is
# the durable terminal marker; these three states are the only ones that may
# still receive work and must never be compacted.
ACTIVE_RESEARCH_RUN_STATUSES = ("queued", "processing", "reasoning-queued")


def _execute(connection, sql: str, params: Sequence[object] = ()):  # pragma: no cover - exercised through callers
    if hasattr(connection, "execute"):
        return connection.execute(sql, tuple(params or ()))
    cursor = connection.cursor()
    cursor.execute(sql, tuple(params or ()))
    return cursor


def _row_value(row, key: str, index: int = 0, fallback=None):
    if isinstance(row, Mapping):
        return row.get(key, fallback)
    try:
        return row[index]
    except (IndexError, TypeError):
        return fallback


def _fetchone(cursor):
    reader = getattr(cursor, "fetchone", None)
    if not callable(reader):
        return None
    return reader()


def _fetchall(cursor) -> List[object]:
    reader = getattr(cursor, "fetchall", None)
    return list(reader() or []) if callable(reader) else []


def _integer(value: object, fallback: int = 0) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return fallback


def _status_placeholders(statuses: Sequence[str]) -> str:
    return ", ".join(["%s"] * len(statuses))


def _cutoff_sql() -> str:
    return "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"


class MySQLMinimalRetentionRepository:
    """Low-priority, idempotent MySQL compaction adapter."""

    def __init__(self, connection):
        self.connection = connection

    def preview(self, policy: MySQLMinimalRetentionPolicy, now: datetime = None) -> Dict[str, object]:
        cutoffs = policy_cutoffs(policy, now=now)
        notification_statuses = _status_placeholders(TERMINAL_NOTIFICATION_STATUSES)
        summaries = {
            "completedWorldProjection": self._summary(
                """
                SELECT COUNT(*) AS candidate_count,
                       COALESCE(SUM(OCTET_LENGTH(payload_json) + OCTET_LENGTH(result_json)), 0) AS candidate_bytes
                FROM `ontology_world_projection_outbox`
                WHERE status IN (""" + _status_placeholders(TERMINAL_WORLD_PROJECTION_STATUSES) + ")"
                " AND completed_at <> '' AND completed_at < " + _cutoff_sql(),
                tuple(TERMINAL_WORLD_PROJECTION_STATUSES) + (cutoffs["completedWorldProjection"],),
            ),
            "completedInferenceDetail": self._summary(
                """
                SELECT COUNT(*) AS candidate_count,
                       COALESCE(SUM(OCTET_LENGTH(result_json)), 0) AS candidate_bytes
                FROM `ontology_inference_detail_outbox`
                WHERE status IN ('completed', 'superseded') AND completed_at <> ''
                  AND completed_at < """ + _cutoff_sql(),
                (cutoffs["completedInferenceDetail"],),
            ),
            "failedWorldProjectionPayload": self._summary(
                """
                SELECT COUNT(*) AS candidate_count,
                       COALESCE(SUM(OCTET_LENGTH(payload_json) + OCTET_LENGTH(result_json)), 0) AS candidate_bytes
                FROM `ontology_world_projection_outbox`
                WHERE status = 'failed' AND updated_at < """ + _cutoff_sql()
                + " AND (payload_json <> '{}' OR result_json <> '{}')",
                (cutoffs["failedWorldProjectionPayload"],),
            ),
            "failedWorldProjection": self._summary(
                """
                SELECT COUNT(*) AS candidate_count,
                       COALESCE(SUM(OCTET_LENGTH(payload_json) + OCTET_LENGTH(result_json)), 0) AS candidate_bytes
                FROM `ontology_world_projection_outbox`
                WHERE status = 'failed' AND updated_at < """ + _cutoff_sql(),
                (cutoffs["failedWorldProjection"],),
            ),
            "terminalNotifications": self._summary(
                """
                SELECT COUNT(*) AS candidate_count, COALESCE(SUM(payload_bytes), 0) AS candidate_bytes
                FROM (
                    SELECT OCTET_LENGTH(`text`) + OCTET_LENGTH(payload_json) AS payload_bytes,
                           ROW_NUMBER() OVER (
                               PARTITION BY account_id
                               ORDER BY updated_at DESC, job_id DESC
                           ) AS history_rank,
                           updated_at
                    FROM `notification_jobs`
                    WHERE status IN (""" + notification_statuses + """
                    )
                ) ranked WHERE history_rank > %s AND updated_at < """ + _cutoff_sql(),
                tuple(TERMINAL_NOTIFICATION_STATUSES) + (policy.delivered_notification_keep_count, cutoffs["terminalNotifications"]),
            ),
            "snapshotHistory": self._summary(
                """
                SELECT COUNT(*) AS candidate_count, COALESCE(SUM(payload_bytes), 0) AS candidate_bytes
                FROM (
                    SELECT COALESCE(OCTET_LENGTH(payload_json), 0) + COALESCE(OCTET_LENGTH(projection_payload_json), 0) AS payload_bytes,
                           ROW_NUMBER() OVER (
                               PARTITION BY account_id
                               ORDER BY generated_at DESC
                           ) AS history_rank
                    FROM `monitor_snapshot_history`
                ) ranked WHERE history_rank > %s
                """,
                (policy.snapshot_history_keep_count,),
            ),
            "projectionPayload": self._summary(
                """
                SELECT COUNT(*) AS candidate_count,
                       COALESCE(SUM(
                           OCTET_LENGTH(source_symbols_json) + OCTET_LENGTH(context_payload_json) + OCTET_LENGTH(result_payload_json)
                       ), 0) AS candidate_bytes
                FROM `ontology_projection_runs`
                WHERE status <> 'projecting' AND updated_at < """ + _cutoff_sql(),
                (cutoffs["projectionPayload"],),
            ),
            "lifecycleEvents": self._summary(
                "SELECT COUNT(*) AS candidate_count, COALESCE(SUM(OCTET_LENGTH(payload_json)), 0) AS candidate_bytes "
                "FROM `investment_hypothesis_lifecycle_events` WHERE occurred_at < " + _cutoff_sql(),
                (cutoffs["lifecycleEvents"],),
            ),
            "lifecycleStateBaselines": self._summary(
                """
                SELECT COUNT(*) AS candidate_count, 0 AS candidate_bytes
                FROM `investment_hypothesis_lifecycle_states` state
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM `investment_hypothesis_transition_history` history
                    WHERE history.transition_id = CONCAT('baseline:', SHA2(state.lifecycle_key, 256))
                )
                """,
                (),
            ),
            "inactiveEvidence": self._summary(
                """
                SELECT COUNT(*) AS candidate_count, COALESCE(SUM(OCTET_LENGTH(payload_json)), 0) AS candidate_bytes
                FROM `research_evidence`
                WHERE lifecycle_state IN ('expired', 'retracted')
                  AND lifecycle_changed_at < """ + _cutoff_sql(),
                (cutoffs["inactiveEvidence"],),
            ),
            "terminalResearchRuns": self._summary(
                "SELECT COUNT(*) AS candidate_count, COALESCE(SUM(OCTET_LENGTH(payload_json)), 0) AS candidate_bytes "
                "FROM `investment_research_runs` WHERE completed_at <> '' AND status NOT IN ("
                + _status_placeholders(ACTIVE_RESEARCH_RUN_STATUSES) + ") AND updated_at < " + _cutoff_sql(),
                tuple(ACTIVE_RESEARCH_RUN_STATUSES) + (cutoffs["researchTerminal"],),
            ),
            "auditRuns": self._summary(
                """
                SELECT COUNT(*) AS candidate_count, COALESCE(SUM(OCTET_LENGTH(report_json)), 0) AS candidate_bytes
                FROM (
                    SELECT report_json,
                           ROW_NUMBER() OVER (ORDER BY created_at DESC, run_id DESC) AS history_rank
                    FROM `mysql_retention_runs`
                ) ranked WHERE history_rank > %s
                """,
                (policy.audit_keep_count,),
            ),
        }
        for granularity in policy.market_time_series_retention_days:
            summaries["marketTimeSeries:" + granularity] = self._summary(
                "SELECT COUNT(*) AS candidate_count, 0 AS candidate_bytes "
                "FROM `market_time_series_observations` WHERE granularity = %s AND bucket_at < " + _cutoff_sql(),
                (granularity, cutoffs["marketTimeSeries:" + granularity]),
            )
        eligible_rows = sum(_integer(item.get("candidateRows")) for item in summaries.values())
        eligible_bytes = sum(_integer(item.get("candidateBytes")) for item in summaries.values())
        return {
            "eligibleRows": eligible_rows,
            "eligibleBytes": eligible_bytes,
            "policies": summaries,
        }

    def apply(self, policy: MySQLMinimalRetentionPolicy, now: datetime = None) -> Dict[str, object]:
        if not self._acquire_lock():
            return {"status": "skipped", "skipped": "locked", "deleted": 0, "compacted": 0, "tables": {}, "policies": {}}

        current = now or datetime.now(timezone.utc)
        cutoffs = policy_cutoffs(policy, now=current)
        budget = {
            "started": time.monotonic(),
            "maxSeconds": policy.max_run_seconds,
            "remainingBytes": policy.max_delete_bytes,
            "deletedBytes": 0,
        }
        tables: Dict[str, int] = {}
        policies: Dict[str, int] = {}
        deleted_total = 0
        compacted = 0
        archived_total = 0
        estimated_bytes = 0
        try:
            actions = [
                ("worldProjection:completed", self._delete_world_projection_rows, (TERMINAL_WORLD_PROJECTION_STATUSES, cutoffs["completedWorldProjection"], "completed_at")),
                ("inferenceDetail:completed", self._delete_inference_detail_rows, (cutoffs["completedInferenceDetail"],)),
                ("worldProjection:failedPayload", self._compact_failed_world_projection_payloads, (cutoffs["failedWorldProjectionPayload"],)),
                ("worldProjection:failed", self._delete_failed_world_projection_rows, (cutoffs["failedWorldProjection"],)),
                ("notifications:terminal", self._delete_terminal_notifications, (policy.delivered_notification_keep_count, cutoffs["terminalNotifications"])),
                ("snapshots:history", self._delete_snapshot_history, (policy.snapshot_history_keep_count,)),
                ("projectionRuns:payload", self._compact_projection_payloads, (cutoffs["projectionPayload"],)),
                ("projectionRuns:history", self._delete_projection_runs, (cutoffs["projectionPayload"],)),
                (
                    "lifecycle:stateBaselines",
                    self._archive_lifecycle_state_baselines,
                    (self._iso(current),),
                ),
                (
                    "lifecycle:events",
                    self._archive_and_delete_lifecycle_events,
                    (cutoffs["lifecycleEvents"], self._iso(current)),
                ),
                ("research:inactiveEvidence", self._delete_inactive_evidence, (cutoffs["inactiveEvidence"],)),
                ("research:terminalRuns", self._delete_terminal_research_runs, (cutoffs["researchTerminal"],)),
                ("audit:runs", self._delete_audit_runs, (policy.audit_keep_count,)),
            ]
            for name, action, arguments in actions:
                if not self._has_budget(budget):
                    policies[name] = 0
                    continue
                result = action(policy, budget, *arguments)
                changed = _integer(result.get("deleted"))
                deleted_total += changed
                compacted += _integer(result.get("compacted"))
                archived_total += _integer(result.get("archived"))
                estimated_bytes += _integer(result.get("estimatedBytes"))
                policies[name] = (
                    changed
                    + _integer(result.get("compacted"))
                    + _integer(result.get("archived"))
                )
                for table, count in dict(result.get("tables") or {}).items():
                    tables[table] = tables.get(table, 0) + _integer(count)

            for granularity in policy.market_time_series_retention_days:
                name = "marketTimeSeries:" + granularity
                if not self._has_budget(budget):
                    policies[name] = 0
                    continue
                result = self._delete_market_time_series(
                    policy,
                    budget,
                    granularity,
                    cutoffs["marketTimeSeries:" + granularity],
                )
                policies[name] = _integer(result.get("deleted"))
                deleted_total += _integer(result.get("deleted"))
                estimated_bytes += _integer(result.get("estimatedBytes"))
                for table, count in dict(result.get("tables") or {}).items():
                    tables[table] = tables.get(table, 0) + _integer(count)
        finally:
            self._release_lock()

        return {
            "status": "ok",
            "deleted": deleted_total,
            "compacted": compacted,
            "archived": archived_total,
            "estimatedBytes": estimated_bytes,
            "tables": tables,
            "policies": policies,
        }

    def record_run(self, result: Mapping[str, object], now: datetime = None) -> None:
        """Persist a compact, payload-free audit row. Audit failure never blocks retention."""

        stamp = self._iso(now)
        report = {
            "status": str((result or {}).get("status") or ""),
            "mode": str((result or {}).get("mode") or ""),
            "profile": str((result or {}).get("profile") or ""),
            "deleted": _integer((result or {}).get("deleted")),
            "compacted": _integer((result or {}).get("compacted")),
            "archived": _integer((result or {}).get("archived")),
            "estimatedBytes": _integer((result or {}).get("estimatedBytes")),
            "tables": dict((result or {}).get("tables") or {}),
            "policies": dict((result or {}).get("policies") or {}),
            "preview": self._compact_preview((result or {}).get("preview")),
        }
        try:
            _execute(
                self.connection,
                """
                INSERT INTO `mysql_retention_runs` (
                    run_id, profile, mode, status, deleted_count, compacted_count,
                    estimated_bytes, report_json, started_at, completed_at, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    "mysql-retention:" + uuid.uuid4().hex,
                    report["profile"],
                    report["mode"],
                    report["status"],
                    report["deleted"],
                    report["compacted"],
                    report["estimatedBytes"],
                    json.dumps(report, ensure_ascii=False, separators=(",", ":")),
                    stamp,
                    stamp,
                    stamp,
                ),
            )
        except Exception:
            return

    def _summary(self, sql: str, params: Sequence[object]) -> Dict[str, int]:
        try:
            row = _fetchone(_execute(self.connection, sql, params)) or {}
        except Exception:
            return {"candidateRows": 0, "candidateBytes": 0, "status": "unavailable"}
        return {
            "candidateRows": _integer(_row_value(row, "candidate_count")),
            "candidateBytes": _integer(_row_value(row, "candidate_bytes")),
        }

    def _delete_world_projection_rows(self, policy, budget, statuses, cutoff_iso, time_column) -> Dict[str, object]:
        candidates = self._byte_bounded_candidates(
            """
            SELECT job_id,
                   OCTET_LENGTH(payload_json) + OCTET_LENGTH(result_json) AS payload_bytes
            FROM `ontology_world_projection_outbox`
            WHERE status IN (""" + _status_placeholders(statuses) + ")"
            " AND " + quote_identifier(time_column) + " <> ''"
            " AND " + quote_identifier(time_column) + " < " + _cutoff_sql()
            + " ORDER BY " + quote_identifier(time_column) + ", job_id LIMIT %s",
            tuple(statuses) + (cutoff_iso, policy.batch_size),
            "job_id",
            policy,
            budget,
        )
        deleted, bytes_deleted = self._delete_candidates(
            "ontology_world_projection_outbox",
            "job_id",
            candidates,
            "status IN (" + _status_placeholders(statuses) + ") AND " + quote_identifier(time_column) + " < " + _cutoff_sql(),
            tuple(statuses) + (cutoff_iso,),
            budget,
        )
        return self._result("ontology_world_projection_outbox", deleted, bytes_deleted)

    def _delete_inference_detail_rows(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        statuses = ("completed", "superseded")
        candidates = self._byte_bounded_candidates(
            """
            SELECT job_id, OCTET_LENGTH(result_json) AS payload_bytes
            FROM `ontology_inference_detail_outbox`
            WHERE status IN (""" + _status_placeholders(statuses) + ")"
            " AND completed_at <> '' AND completed_at < " + _cutoff_sql()
            + " ORDER BY completed_at, job_id LIMIT %s",
            tuple(statuses) + (cutoff_iso, policy.batch_size),
            "job_id",
            policy,
            budget,
        )
        deleted, bytes_deleted = self._delete_candidates(
            "ontology_inference_detail_outbox",
            "job_id",
            candidates,
            "status IN ('completed', 'superseded') AND completed_at < " + _cutoff_sql(),
            (cutoff_iso,),
            budget,
        )
        return self._result("ontology_inference_detail_outbox", deleted, bytes_deleted)

    def _compact_failed_world_projection_payloads(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        candidates = self._byte_bounded_candidates(
            """
            SELECT job_id,
                   OCTET_LENGTH(payload_json) + OCTET_LENGTH(result_json) AS payload_bytes
            FROM `ontology_world_projection_outbox`
            WHERE status = 'failed' AND updated_at < """ + _cutoff_sql()
            + " AND (payload_json <> '{}' OR result_json <> '{}')"
            + " ORDER BY updated_at, job_id LIMIT %s",
            (cutoff_iso, policy.batch_size),
            "job_id",
            policy,
            budget,
        )
        compacted = 0
        bytes_compacted = 0
        for row in candidates:
            if not self._has_budget(budget):
                break
            job_id = str(_row_value(row, "job_id", fallback="") or "").strip()
            if not job_id:
                continue
            cursor = _execute(
                self.connection,
                """
                UPDATE `ontology_world_projection_outbox`
                SET payload_json = '{}', result_json = '{}'
                WHERE job_id = %s AND status = 'failed' AND updated_at < """ + _cutoff_sql(),
                (job_id, cutoff_iso),
            )
            if _integer(getattr(cursor, "rowcount", 0)):
                compacted += 1
                row_bytes = _integer(_row_value(row, "payload_bytes"))
                bytes_compacted += row_bytes
                budget["remainingBytes"] = max(0, _integer(budget.get("remainingBytes")) - row_bytes)
                budget["deletedBytes"] = _integer(budget.get("deletedBytes")) + row_bytes
        return {
            "deleted": 0,
            "compacted": compacted,
            "estimatedBytes": bytes_compacted,
            "tables": {"ontology_world_projection_outbox": compacted} if compacted else {},
        }

    def _delete_failed_world_projection_rows(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        candidates = self._byte_bounded_candidates(
            """
            SELECT job_id,
                   OCTET_LENGTH(payload_json) + OCTET_LENGTH(result_json) AS payload_bytes
            FROM `ontology_world_projection_outbox`
            WHERE status = 'failed' AND updated_at < """ + _cutoff_sql()
            + " ORDER BY updated_at, job_id LIMIT %s",
            (cutoff_iso, policy.batch_size),
            "job_id",
            policy,
            budget,
        )
        deleted, bytes_deleted = self._delete_candidates(
            "ontology_world_projection_outbox",
            "job_id",
            candidates,
            "status = 'failed' AND updated_at < " + _cutoff_sql(),
            (cutoff_iso,),
            budget,
        )
        return self._result("ontology_world_projection_outbox", deleted, bytes_deleted)

    def _delete_terminal_notifications(self, policy, budget, keep_count, cutoff_iso) -> Dict[str, object]:
        statuses = _status_placeholders(TERMINAL_NOTIFICATION_STATUSES)
        account_rows = _fetchall(_execute(
            self.connection,
            "SELECT DISTINCT account_id FROM `notification_jobs` WHERE status IN ("
            + statuses
            + ") ORDER BY account_id",
            TERMINAL_NOTIFICATION_STATUSES,
        ))
        candidate_rows = []
        for account_row in account_rows:
            if len(candidate_rows) >= policy.batch_size:
                break
            account_id = str(_row_value(account_row, "account_id", fallback="") or "")
            boundary = _fetchone(_execute(
                self.connection,
                "SELECT updated_at, job_id FROM `notification_jobs` WHERE account_id = %s AND status IN ("
                + statuses
                + ") ORDER BY updated_at DESC, job_id DESC LIMIT 1 OFFSET %s",
                (account_id, *TERMINAL_NOTIFICATION_STATUSES, max(0, keep_count - 1)),
            ))
            if not boundary:
                continue
            boundary_at = str(_row_value(boundary, "updated_at", fallback="") or "")
            boundary_id = str(_row_value(boundary, "job_id", fallback="") or "")
            rows = _fetchall(_execute(
                self.connection,
                "SELECT job_id, OCTET_LENGTH(`text`) + OCTET_LENGTH(payload_json) AS payload_bytes "
                "FROM `notification_jobs` WHERE account_id = %s AND status IN ("
                + statuses
                + ") AND updated_at < "
                + _cutoff_sql()
                + " AND (updated_at < %s OR (updated_at = %s AND job_id < %s)) "
                "ORDER BY updated_at, job_id LIMIT %s",
                (
                    account_id,
                    *TERMINAL_NOTIFICATION_STATUSES,
                    cutoff_iso,
                    boundary_at,
                    boundary_at,
                    boundary_id,
                    policy.batch_size - len(candidate_rows),
                ),
            ))
            candidate_rows.extend(rows)
        candidates = self._bounded_candidate_rows(candidate_rows, "job_id", policy, budget)
        deleted, bytes_deleted = self._delete_candidates(
            "notification_jobs",
            "job_id",
            candidates,
            "status IN (" + _status_placeholders(TERMINAL_NOTIFICATION_STATUSES) + ") AND updated_at < " + _cutoff_sql(),
            tuple(TERMINAL_NOTIFICATION_STATUSES) + (cutoff_iso,),
            budget,
        )
        return self._result("notification_jobs", deleted, bytes_deleted)

    def _delete_snapshot_history(self, policy, budget, keep_count) -> Dict[str, object]:
        account_rows = _fetchall(_execute(
            self.connection,
            "SELECT DISTINCT account_id FROM `monitor_snapshot_history` ORDER BY account_id",
        ))
        candidate_rows = []
        for account_row in account_rows:
            if len(candidate_rows) >= policy.batch_size:
                break
            account_id = str(_row_value(account_row, "account_id", fallback="") or "")
            boundary = _fetchone(_execute(
                self.connection,
                "SELECT generated_at FROM `monitor_snapshot_history` WHERE account_id = %s "
                "ORDER BY generated_at DESC LIMIT 1 OFFSET %s",
                (account_id, max(0, keep_count - 1)),
            ))
            boundary_at = str(_row_value(boundary, "generated_at", fallback="") or "") if boundary else ""
            if not boundary_at:
                continue
            rows = _fetchall(_execute(
                self.connection,
                "SELECT account_id, generated_at, "
                "OCTET_LENGTH(payload_json) + COALESCE(OCTET_LENGTH(projection_payload_json), 0) AS payload_bytes "
                "FROM `monitor_snapshot_history` WHERE account_id = %s AND generated_at < %s "
                "ORDER BY generated_at LIMIT %s",
                (account_id, boundary_at, policy.batch_size - len(candidate_rows)),
            ))
            candidate_rows.extend(rows)
        candidates = self._bounded_candidate_rows(
            candidate_rows,
            "generated_at",
            policy,
            budget,
            compound_key=("account_id", "generated_at"),
        )
        deleted = 0
        bytes_deleted = 0
        for row in candidates:
            if not self._has_budget(budget):
                break
            account_id = str(_row_value(row, "account_id", fallback="") or "")
            generated_at = str(_row_value(row, "generated_at", fallback="") or "")
            if not account_id or not generated_at:
                continue
            cursor = _execute(
                self.connection,
                "DELETE FROM `monitor_snapshot_history` WHERE account_id = %s AND generated_at = %s",
                (account_id, generated_at),
            )
            if _integer(getattr(cursor, "rowcount", 0)):
                deleted += 1
                row_bytes = _integer(_row_value(row, "payload_bytes"))
                bytes_deleted += row_bytes
                budget["remainingBytes"] = max(0, _integer(budget.get("remainingBytes")) - row_bytes)
                budget["deletedBytes"] = _integer(budget.get("deletedBytes")) + row_bytes
        return self._result("monitor_snapshot_history", deleted, bytes_deleted)

    def _compact_projection_payloads(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        candidates = self._byte_bounded_candidates(
            """
            SELECT run_id,
                   OCTET_LENGTH(source_symbols_json) + OCTET_LENGTH(context_payload_json) + OCTET_LENGTH(result_payload_json) AS payload_bytes
            FROM `ontology_projection_runs`
            WHERE status <> 'projecting' AND updated_at < """ + _cutoff_sql()
            + " AND (context_payload_json <> '{}' OR result_payload_json <> '{}' OR source_symbols_json <> '[]')"
            + " ORDER BY updated_at, run_id LIMIT %s",
            (cutoff_iso, policy.batch_size),
            "run_id",
            policy,
            budget,
        )
        compacted = 0
        bytes_compacted = 0
        for row in candidates:
            if not self._has_budget(budget):
                break
            run_id = str(_row_value(row, "run_id", fallback="") or "")
            if not run_id:
                continue
            cursor = _execute(
                self.connection,
                """
                UPDATE `ontology_projection_runs`
                SET source_symbols_json = '[]', context_payload_json = '{}', result_payload_json = '{}'
                WHERE run_id = %s AND status <> 'projecting' AND updated_at < """ + _cutoff_sql(),
                (run_id, cutoff_iso),
            )
            if _integer(getattr(cursor, "rowcount", 0)):
                compacted += 1
                row_bytes = _integer(_row_value(row, "payload_bytes"))
                bytes_compacted += row_bytes
                budget["remainingBytes"] = max(0, _integer(budget.get("remainingBytes")) - row_bytes)
                budget["deletedBytes"] = _integer(budget.get("deletedBytes")) + row_bytes
        return {
            "deleted": 0,
            "compacted": compacted,
            "estimatedBytes": bytes_compacted,
            "tables": {"ontology_projection_runs": compacted} if compacted else {},
        }

    def _delete_projection_runs(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        world_rows = _fetchall(_execute(
            self.connection,
            "SELECT DISTINCT world_id FROM `ontology_projection_runs` WHERE status <> 'projecting' ORDER BY world_id",
        ))
        candidate_rows = []
        for world_row in world_rows:
            if len(candidate_rows) >= policy.batch_size:
                break
            world_id = str(_row_value(world_row, "world_id", fallback="") or "")
            boundary = _fetchone(_execute(
                self.connection,
                "SELECT updated_at, run_id FROM `ontology_projection_runs` "
                "WHERE status <> 'projecting' AND world_id = %s "
                "ORDER BY updated_at DESC, run_id DESC LIMIT 1 OFFSET 1",
                (world_id,),
            ))
            if not boundary:
                continue
            boundary_at = str(_row_value(boundary, "updated_at", fallback="") or "")
            boundary_id = str(_row_value(boundary, "run_id", fallback="") or "")
            rows = _fetchall(_execute(
                self.connection,
                "SELECT run_id, OCTET_LENGTH(source_symbols_json) + OCTET_LENGTH(context_payload_json) "
                "+ OCTET_LENGTH(result_payload_json) AS payload_bytes "
                "FROM `ontology_projection_runs` WHERE status <> 'projecting' AND world_id = %s "
                "AND updated_at < "
                + _cutoff_sql()
                + " AND (updated_at < %s OR (updated_at = %s AND run_id < %s)) "
                "ORDER BY updated_at, run_id LIMIT %s",
                (
                    world_id,
                    cutoff_iso,
                    boundary_at,
                    boundary_at,
                    boundary_id,
                    policy.batch_size - len(candidate_rows),
                ),
            ))
            candidate_rows.extend(rows)
        candidates = self._bounded_candidate_rows(candidate_rows, "run_id", policy, budget)
        deleted, bytes_deleted = self._delete_candidates(
            "ontology_projection_runs",
            "run_id",
            candidates,
            "status <> 'projecting' AND updated_at < " + _cutoff_sql(),
            (cutoff_iso,),
            budget,
        )
        return self._result("ontology_projection_runs", deleted, bytes_deleted)

    def _archive_and_delete_lifecycle_events(
        self,
        policy,
        budget,
        cutoff_iso,
        archived_at,
    ) -> Dict[str, object]:
        candidates = self._byte_bounded_candidates(
            """
            SELECT transition_id, lifecycle_key, lifecycle_id, scope,
                   account_id, market_id, symbol, previous_state,
                   current_state, inference_generation_id,
                   previous_generation_id, occurred_at, material_change,
                   OCTET_LENGTH(payload_json) AS payload_bytes
            FROM `investment_hypothesis_lifecycle_events`
            WHERE occurred_at < """ + _cutoff_sql() + " ORDER BY occurred_at, transition_id LIMIT %s",
            (cutoff_iso, policy.batch_size),
            "transition_id",
            policy,
            budget,
        )
        archived = 0
        deleted = 0
        bytes_deleted = 0
        history_fields = (
            "transition_id",
            "lifecycle_key",
            "lifecycle_id",
            "scope",
            "account_id",
            "market_id",
            "symbol",
            "previous_state",
            "current_state",
            "inference_generation_id",
            "previous_generation_id",
            "occurred_at",
        )
        for row in candidates:
            if not self._has_budget(budget):
                break
            values = tuple(str(_row_value(row, field, fallback="") or "") for field in history_fields)
            transition_id = values[0].strip()
            if not transition_id:
                continue
            archive_cursor = _execute(
                self.connection,
                """
                INSERT IGNORE INTO `investment_hypothesis_transition_history` (
                    transition_id, lifecycle_key, lifecycle_id, scope,
                    account_id, market_id, symbol, previous_state,
                    current_state, inference_generation_id,
                    previous_generation_id, occurred_at, material_change,
                    archived_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                """,
                values + (
                    1 if _integer(_row_value(row, "material_change")) else 0,
                    archived_at,
                ),
            )
            archived += _integer(getattr(archive_cursor, "rowcount", 0))
            delete_cursor = _execute(
                self.connection,
                """
                DELETE FROM `investment_hypothesis_lifecycle_events`
                WHERE transition_id = %s
                  AND occurred_at < """ + _cutoff_sql() + """
                  AND EXISTS (
                      SELECT 1
                      FROM `investment_hypothesis_transition_history` history
                      WHERE history.transition_id = %s
                  )
                """,
                (transition_id, cutoff_iso, transition_id),
            )
            if _integer(getattr(delete_cursor, "rowcount", 0)):
                deleted += 1
                row_bytes = max(0, _integer(_row_value(row, "payload_bytes")))
                bytes_deleted += row_bytes
                budget["remainingBytes"] = max(
                    0,
                    _integer(budget.get("remainingBytes")) - row_bytes,
                )
                budget["deletedBytes"] = _integer(budget.get("deletedBytes")) + row_bytes
        return {
            **self._result(
                "investment_hypothesis_lifecycle_events",
                deleted,
                bytes_deleted,
            ),
            "archived": archived,
        }

    def _archive_lifecycle_state_baselines(
        self,
        policy,
        budget,
        archived_at,
    ) -> Dict[str, object]:
        if not self._has_budget(budget):
            return {"deleted": 0, "compacted": 0, "archived": 0, "estimatedBytes": 0, "tables": {}}
        cursor = _execute(
            self.connection,
            """
            INSERT IGNORE INTO `investment_hypothesis_transition_history` (
                transition_id, lifecycle_key, lifecycle_id, scope,
                account_id, market_id, symbol, previous_state,
                current_state, inference_generation_id,
                previous_generation_id, occurred_at, material_change,
                archived_at
            )
            SELECT CONCAT('baseline:', SHA2(state.lifecycle_key, 256)),
                   state.lifecycle_key, state.lifecycle_id, state.scope,
                   state.account_id, state.market_id, state.symbol, '',
                   state.state, state.inference_generation_id,
                   state.previous_generation_id,
                   COALESCE(
                       NULLIF(state.last_transition_at, ''),
                       NULLIF(state.last_observed_at, ''),
                       state.updated_at
                   ),
                   state.material_change,
                   %s
            FROM `investment_hypothesis_lifecycle_states` state
            WHERE NOT EXISTS (
                SELECT 1
                FROM `investment_hypothesis_transition_history` history
                WHERE history.transition_id = CONCAT('baseline:', SHA2(state.lifecycle_key, 256))
            )
            ORDER BY state.updated_at, state.lifecycle_key
            LIMIT %s
            """,
            (archived_at, policy.batch_size),
        )
        archived = _integer(getattr(cursor, "rowcount", 0))
        return {
            "deleted": 0,
            "compacted": 0,
            "archived": archived,
            "estimatedBytes": 0,
            "tables": {},
        }

    def _delete_inactive_evidence(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        candidates = self._byte_bounded_candidates(
            """
            SELECT evidence_id, OCTET_LENGTH(payload_json) AS payload_bytes
            FROM `research_evidence`
            WHERE lifecycle_state IN ('expired', 'retracted')
              AND lifecycle_changed_at < """ + _cutoff_sql() + " ORDER BY lifecycle_changed_at, evidence_id LIMIT %s",
            (cutoff_iso, policy.batch_size),
            "evidence_id",
            policy,
            budget,
        )
        deleted, bytes_deleted = self._delete_candidates(
            "research_evidence",
            "evidence_id",
            candidates,
            "lifecycle_state IN ('expired', 'retracted') AND lifecycle_changed_at < " + _cutoff_sql(),
            (cutoff_iso,),
            budget,
        )
        return self._result("research_evidence", deleted, bytes_deleted)

    def _delete_terminal_research_runs(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        candidates = self._byte_bounded_candidates(
            """
            SELECT run_id, OCTET_LENGTH(payload_json) AS payload_bytes
            FROM `investment_research_runs`
            WHERE completed_at <> '' AND status NOT IN (""" + _status_placeholders(ACTIVE_RESEARCH_RUN_STATUSES) + ")"
            " AND updated_at < " + _cutoff_sql() + " ORDER BY updated_at, run_id LIMIT %s",
            tuple(ACTIVE_RESEARCH_RUN_STATUSES) + (cutoff_iso, policy.batch_size),
            "run_id",
            policy,
            budget,
        )
        deleted, bytes_deleted = self._delete_candidates(
            "investment_research_runs",
            "run_id",
            candidates,
            "completed_at <> '' AND status NOT IN (" + _status_placeholders(ACTIVE_RESEARCH_RUN_STATUSES) + ") AND updated_at < " + _cutoff_sql(),
            tuple(ACTIVE_RESEARCH_RUN_STATUSES) + (cutoff_iso,),
            budget,
        )
        return self._result("investment_research_runs", deleted, bytes_deleted)

    def _delete_market_time_series(self, policy, budget, granularity, cutoff_iso) -> Dict[str, object]:
        candidates = self._byte_bounded_candidates(
            """
            SELECT account_id, symbol, granularity, bucket_at, 0 AS payload_bytes
            FROM `market_time_series_observations`
            WHERE granularity = %s AND bucket_at < """ + _cutoff_sql()
            + " ORDER BY bucket_at, account_id, symbol LIMIT %s",
            (granularity, cutoff_iso, policy.batch_size),
            "bucket_at",
            policy,
            budget,
            compound_key=("account_id", "symbol", "granularity", "bucket_at"),
        )
        deleted = 0
        for row in candidates:
            if not self._has_budget(budget):
                break
            values = tuple(str(_row_value(row, key, fallback="") or "") for key in ("account_id", "symbol", "granularity", "bucket_at"))
            if not all(values):
                continue
            cursor = _execute(
                self.connection,
                """
                DELETE FROM `market_time_series_observations`
                WHERE account_id = %s AND symbol = %s AND granularity = %s AND bucket_at = %s
                """,
                values,
            )
            deleted += _integer(getattr(cursor, "rowcount", 0))
        return self._result("market_time_series_observations", deleted, 0)

    def _delete_audit_runs(self, policy, budget, keep_count) -> Dict[str, object]:
        boundary = _fetchone(_execute(
            self.connection,
            "SELECT created_at, run_id FROM `mysql_retention_runs` "
            "ORDER BY created_at DESC, run_id DESC LIMIT 1 OFFSET %s",
            (max(0, keep_count - 1),),
        ))
        if boundary:
            boundary_at = str(_row_value(boundary, "created_at", fallback="") or "")
            boundary_id = str(_row_value(boundary, "run_id", fallback="") or "")
            candidates = self._byte_bounded_candidates(
                "SELECT run_id, OCTET_LENGTH(report_json) AS payload_bytes FROM `mysql_retention_runs` "
                "WHERE created_at < %s OR (created_at = %s AND run_id < %s) "
                "ORDER BY created_at, run_id LIMIT %s",
                (boundary_at, boundary_at, boundary_id, policy.batch_size),
                "run_id",
                policy,
                budget,
            )
        else:
            candidates = []
        deleted, bytes_deleted = self._delete_candidates(
            "mysql_retention_runs",
            "run_id",
            candidates,
            "1 = 1",
            (),
            budget,
        )
        return self._result("mysql_retention_runs", deleted, bytes_deleted)

    def _byte_bounded_candidates(
        self,
        sql: str,
        params: Sequence[object],
        identifier: str,
        policy: MySQLMinimalRetentionPolicy,
        budget: Dict[str, object],
        compound_key: Sequence[str] = (),
    ) -> List[object]:
        if not self._has_budget(budget):
            return []
        rows = _fetchall(_execute(self.connection, sql, params))
        return self._bounded_candidate_rows(rows, identifier, policy, budget, compound_key=compound_key)

    def _bounded_candidate_rows(
        self,
        rows: Sequence[object],
        identifier: str,
        policy: MySQLMinimalRetentionPolicy,
        budget: Dict[str, object],
        compound_key: Sequence[str] = (),
    ) -> List[object]:
        result = []
        remaining = _integer(budget.get("remainingBytes"))
        for row in rows:
            valid = bool(str(_row_value(row, identifier, fallback="") or "").strip())
            if compound_key:
                valid = all(str(_row_value(row, key, fallback="") or "").strip() for key in compound_key)
            if not valid:
                continue
            row_bytes = max(0, _integer(_row_value(row, "payload_bytes")))
            if row_bytes > remaining:
                if result or _integer(budget.get("deletedBytes")) > 0:
                    continue
                # A single retained packet can be larger than the preferred
                # turn budget. Allow it only at the start of a pass so the
                # worker can make progress without exceeding the budget after
                # it has already deleted other records.
                result.append(row)
                break
            result.append(row)
            remaining -= row_bytes
            if len(result) >= policy.batch_size or remaining <= 0:
                break
        return result

    def _delete_candidates(
        self,
        table: str,
        primary_key: str,
        candidates: Sequence[object],
        guard_sql: str,
        guard_params: Sequence[object],
        budget: Dict[str, object],
    ) -> tuple:
        deleted = 0
        bytes_deleted = 0
        quoted_table = quote_identifier(table)
        quoted_key = quote_identifier(primary_key)
        for row in candidates or []:
            if not self._has_budget(budget):
                break
            key = str(_row_value(row, primary_key, fallback="") or "").strip()
            if not key:
                continue
            cursor = _execute(
                self.connection,
                "DELETE FROM " + quoted_table + " WHERE " + quoted_key + " = %s AND " + guard_sql,
                (key,) + tuple(guard_params or ()),
            )
            if _integer(getattr(cursor, "rowcount", 0)):
                deleted += 1
                row_bytes = max(0, _integer(_row_value(row, "payload_bytes")))
                bytes_deleted += row_bytes
                budget["remainingBytes"] = max(0, _integer(budget.get("remainingBytes")) - row_bytes)
                budget["deletedBytes"] = _integer(budget.get("deletedBytes")) + row_bytes
        return deleted, bytes_deleted

    def _result(self, table: str, deleted: int, estimated_bytes: int) -> Dict[str, object]:
        return {
            "deleted": deleted,
            "compacted": 0,
            "estimatedBytes": estimated_bytes,
            "tables": {table: deleted} if deleted else {},
        }

    def _has_budget(self, budget: Mapping[str, object]) -> bool:
        return (
            _integer(budget.get("remainingBytes")) > 0
            and (time.monotonic() - float(budget.get("started") or 0)) < _integer(budget.get("maxSeconds"), 1)
        )

    def _acquire_lock(self) -> bool:
        try:
            row = _fetchone(_execute(self.connection, "SELECT GET_LOCK(%s, 0) AS acquired", (MYSQL_MINIMAL_RETENTION_LOCK_NAME,)))
            return _integer(_row_value(row, "acquired")) == 1
        except Exception:
            return False

    def _release_lock(self) -> None:
        try:
            _execute(self.connection, "SELECT RELEASE_LOCK(%s)", (MYSQL_MINIMAL_RETENTION_LOCK_NAME,))
        except Exception:
            return

    @staticmethod
    def _iso(now: datetime = None) -> str:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _compact_preview(value: object) -> Dict[str, object]:
        preview = dict(value or {}) if isinstance(value, Mapping) else {}
        policies = dict(preview.get("policies") or {}) if isinstance(preview.get("policies"), Mapping) else {}
        return {
            "eligibleRows": _integer(preview.get("eligibleRows")),
            "eligibleBytes": _integer(preview.get("eligibleBytes")),
            "policies": {
                str(name): {
                    "candidateRows": _integer(dict(item or {}).get("candidateRows")),
                    "candidateBytes": _integer(dict(item or {}).get("candidateBytes")),
                    "status": str(dict(item or {}).get("status") or "ok"),
                }
                for name, item in policies.items()
            },
        }
