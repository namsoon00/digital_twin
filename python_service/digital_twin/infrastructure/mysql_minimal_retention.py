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

from digital_twin.platform.domain.mysql_minimal_retention import MySQLMinimalRetentionPolicy, policy_cutoffs
from digital_twin.platform.domain.storage_preservation import ARCHIVE_BEFORE_DELETE, PROTECTED_HISTORY_TABLES
from .mysql_history_archive import archive_delete_row
from .mysql_schema_tuning import quote_identifier


MYSQL_MINIMAL_RETENTION_LOCK_NAME = "orbit_alpha_minimal_mysql_retention"


def _history_unreferenced_sql():
    # Retain exact historical input while either delivery or shadow still owns
    # it. Recheck at DELETE time; latest-two retention alone is insufficient.
    return ("NOT EXISTS (SELECT 1 FROM verified_reasoning_source_snapshots source "
        "JOIN reasoning_engine_jobs job ON job.source_snapshot_id = source.snapshot_id "
        "WHERE source.account_id = monitor_snapshot_history.account_id "
        "AND source.generated_at = monitor_snapshot_history.generated_at "
        "AND job.job_status NOT IN ('completed','superseded','cancelled')) "
        "AND NOT EXISTS (SELECT 1 FROM verified_reasoning_source_snapshots source "
        "JOIN ontology_reasoning_mailbox_events mailbox ON mailbox.source_snapshot_id = source.snapshot_id "
        "WHERE source.account_id = monitor_snapshot_history.account_id "
        "AND source.generated_at = monitor_snapshot_history.generated_at "
        "AND mailbox.state IN ('pending','direct-pending'))")


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


def _research_audit_expired_sql() -> str:
    # Historical rows without a research audit keep the operational policy.
    # New question receipts survive their research horizon before that same
    # terminal-retention window starts. Recheck this guard at DELETE time.
    return "COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.auditRetainUntil')), '') < " + _cutoff_sql()


class MySQLMinimalRetentionRepository:
    """Low-priority, idempotent MySQL compaction adapter."""

    def __init__(self, connection, *, candidate_query_timeout_ms: int = 0):
        self.connection = connection
        self.candidate_query_timeout_ms = max(0, int(candidate_query_timeout_ms))

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
                + _status_placeholders(ACTIVE_RESEARCH_RUN_STATUSES) + ") AND updated_at < " + _cutoff_sql()
                + " AND " + _research_audit_expired_sql(),
                tuple(ACTIVE_RESEARCH_RUN_STATUSES) + (cutoffs["researchTerminal"], cutoffs["researchTerminal"]),
            ),
            "completedTimeSeriesProjection": self._summary(
                "SELECT COUNT(*) AS candidate_count, COALESCE(SUM(OCTET_LENGTH(payload_json)), 0) AS candidate_bytes "
                "FROM `time_series_projection_outbox` WHERE job_status = 'completed' AND updated_at < " + _cutoff_sql(),
                (cutoffs["completedTimeSeriesProjection"],),
            ),
            "temporalFeatureSnapshots": self._summary(
                "SELECT COUNT(*) AS candidate_count, COALESCE(SUM(OCTET_LENGTH(windows_json)), 0) AS candidate_bytes "
                "FROM `temporal_feature_snapshots` WHERE created_at < " + _cutoff_sql(),
                (cutoffs["temporalFeatureSnapshots"],),
            ),
            "statisticalModelSignalSnapshots": self._summary(
                "SELECT COUNT(*) AS candidate_count, COALESCE(SUM(OCTET_LENGTH(payload_json)), 0) AS candidate_bytes "
                "FROM `statistical_model_signal_snapshots` snapshot WHERE created_at < " + _cutoff_sql()
                + " AND NOT EXISTS (SELECT 1 FROM `statistical_model_signal_heads` head "
                "WHERE head.snapshot_id = snapshot.snapshot_id)"
                + " AND NOT EXISTS (SELECT 1 FROM `statistical_model_assessment_heads` assessment "
                "WHERE assessment.snapshot_id = snapshot.snapshot_id)",
                (cutoffs["statisticalModelSignalSnapshots"],),
            ),
            "reasoningShadowJobs": self._summary(
                "SELECT COUNT(*) AS candidate_count, COALESCE(SUM(OCTET_LENGTH(payload_json)), 0) AS candidate_bytes "
                "FROM `reasoning_engine_shadow_jobs` WHERE job_status IN ('completed', 'failed', 'superseded') "
                "AND updated_at < " + _cutoff_sql(),
                (cutoffs["reasoningShadowJobs"],),
            ),
            "reasoningEngineJobs": self._summary(
                "SELECT COUNT(*) AS candidate_count, COALESCE(SUM(OCTET_LENGTH(request_json) + OCTET_LENGTH(result_json)), 0) AS candidate_bytes "
                "FROM `reasoning_engine_jobs` WHERE job_status IN ('completed', 'excluded', 'failed', 'superseded') "
                "AND updated_at < " + _cutoff_sql(),
                (cutoffs["reasoningEngineJobs"],),
            ),
            "reasoningSourceSnapshots": self._summary(
                "SELECT COUNT(*) AS candidate_count, COALESCE(SUM(OCTET_LENGTH(payload_json)), 0) AS candidate_bytes "
                "FROM `verified_reasoning_source_snapshots` source WHERE source.created_at < "
                + _cutoff_sql()
                + " AND NOT EXISTS (SELECT 1 FROM `reasoning_engine_jobs` job "
                "WHERE job.source_snapshot_id = source.snapshot_id "
                "AND job.job_status IN ('queued', 'retry', 'processing', 'awaiting_world_projection')) "
                "AND NOT EXISTS (SELECT 1 FROM `ontology_reasoning_mailbox_events` mailbox "
                "WHERE mailbox.source_snapshot_id = source.snapshot_id "
                "AND mailbox.state IN ('pending', 'direct-pending'))",
                (cutoffs["reasoningEngineJobs"],),
            ),
            "investmentReasoningCases": {"candidateRows": 0, "candidateBytes": 0, "protected": True},
            "subjectDecisionCases": {"candidateRows": 0, "candidateBytes": 0, "protected": True},
            "decisionCandidateSnapshots": {"candidateRows": 0, "candidateBytes": 0, "protected": True},
            "decisionPublications": {"candidateRows": 0, "candidateBytes": 0, "protected": True},
            "reasoningComparisons": self._summary(
                "SELECT COUNT(*) AS candidate_count, COALESCE(SUM(OCTET_LENGTH(payload_json)), 0) AS candidate_bytes "
                "FROM `reasoning_engine_comparisons` WHERE created_at < " + _cutoff_sql(),
                (cutoffs["reasoningComparisons"],),
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
        attempted = []
        next_policy = ""
        try:
            actions = [
                ("worldProjection:obsoletePayload", self._compact_obsolete_world_projection_payloads, ()),
                ("worldProjection:completed", self._delete_world_projection_rows, (TERMINAL_WORLD_PROJECTION_STATUSES, cutoffs["completedWorldProjection"], "completed_at")),
                ("inferenceDetail:completed", self._delete_inference_detail_rows, (cutoffs["completedInferenceDetail"],)),
                ("worldProjection:failedPayload", self._compact_failed_world_projection_payloads, (cutoffs["failedWorldProjectionPayload"],)),
                ("worldProjection:failed", self._delete_failed_world_projection_rows, (cutoffs["failedWorldProjection"],)),
                ("notifications:terminal", self._delete_terminal_notifications, (policy.delivered_notification_keep_count, cutoffs["terminalNotifications"])),
                ("notifications:decisionReceipts", self._delete_decision_notification_receipts, (cutoffs["decisionNotificationReceipts"],)),
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
                (
                    "timeSeriesProjection:deliveredPayload",
                    self._compact_completed_time_series_projection_payloads,
                    (),
                ),
                (
                    "timeSeriesProjection:completed",
                    self._delete_completed_time_series_projection,
                    (cutoffs["completedTimeSeriesProjection"],),
                ),
                (
                    "temporalFeatureSnapshots:history",
                    self._delete_temporal_feature_snapshots,
                    (cutoffs["temporalFeatureSnapshots"],),
                ),
                (
                    "statisticalModelSignalSnapshots:history",
                    self._delete_statistical_model_signal_snapshots,
                    (cutoffs["statisticalModelSignalSnapshots"],),
                ),
                (
                    "reasoningShadowJobs:terminal",
                    self._delete_reasoning_shadow_jobs,
                    (cutoffs["reasoningShadowJobs"],),
                ),
                (
                    "reasoningEngineJobs:terminal",
                    self._delete_reasoning_engine_jobs,
                    (cutoffs["reasoningEngineJobs"],),
                ),
                ("reasoningJobSources:orphaned", self._delete_reasoning_job_sources, ()),
                (
                    "reasoningSourceSnapshots:terminal",
                    self._delete_reasoning_source_snapshots,
                    (cutoffs["reasoningEngineJobs"],),
                ),
                (
                    "decisionPublications:terminal",
                    self._delete_decision_publications,
                    (cutoffs["investmentReasoningCases"],),
                ),
                (
                    "decisionCandidateSnapshots:terminal",
                    self._delete_decision_candidate_snapshots,
                    (cutoffs["investmentReasoningCases"],),
                ),
                (
                    "subjectDecisionCases:terminal",
                    self._delete_subject_decision_cases,
                    (cutoffs["investmentReasoningCases"],),
                ),
                (
                    "investmentReasoningCases:terminal",
                    self._delete_investment_reasoning_cases,
                    (cutoffs["investmentReasoningCases"],),
                ),
                (
                    "reasoningComparisons:history",
                    self._delete_reasoning_comparisons,
                    (cutoffs["reasoningComparisons"],),
                ),
                ("audit:runs", self._delete_audit_runs, (policy.audit_keep_count,)),
            ]
            actions.extend(("marketTimeSeries:" + granularity, self._delete_market_time_series,
                            (granularity, cutoffs["marketTimeSeries:" + granularity]))
                           for granularity in policy.market_time_series_retention_days)
            row = _fetchone(_execute(self.connection,
                "SELECT next_policy FROM mysql_retention_progress WHERE profile = %s", (policy.profile,)))
            resume = str(_row_value(row, "next_policy", fallback="") or "")
            offset = next((index for index, action in enumerate(actions) if action[0] == resume), 0)
            ordered = actions[offset:] + actions[:offset]
            next_policy = ordered[0][0]
            for index, (name, action, arguments) in enumerate(ordered):
                if not self._has_budget(budget):
                    break
                # Advance before expensive SQL so a timeout or worker restart
                # cannot strand every later table behind the same policy.
                next_policy = ordered[(index + 1) % len(ordered)][0]
                _execute(self.connection,
                    "INSERT INTO mysql_retention_progress (profile,next_policy,updated_at) VALUES (%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE next_policy=VALUES(next_policy),updated_at=VALUES(updated_at)",
                    (policy.profile, next_policy, self._iso()))
                attempted.append(name)
                reads_before = _integer(budget.get("candidateReads"))
                try:
                    result = action(policy, budget, *arguments)
                except Exception as error:
                    error.orbit_retention_policy = name
                    raise
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
                if budget.get("candidateReadDeferred"):
                    # Give an unstarted policy a fresh turn. A policy that
                    # already read candidates keeps its scan cursor but yields
                    # to the next policy, preserving round-robin fairness.
                    if _integer(budget.get("candidateReads")) == reads_before:
                        next_policy = name
                        _execute(self.connection,
                            "INSERT INTO mysql_retention_progress (profile,next_policy,updated_at) VALUES (%s,%s,%s) "
                            "ON DUPLICATE KEY UPDATE next_policy=VALUES(next_policy),updated_at=VALUES(updated_at)",
                            (policy.profile, next_policy, self._iso()))
                    break

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
            "progress": {"attemptedPolicies": attempted, "nextPolicy": next_policy,
                         "remainingPolicies": len(actions) - len(attempted)
                            + int(bool(budget.get("candidateReadDeferred")) and next_policy == attempted[-1]),
                         "yieldReason": "candidate-query-budget" if budget.get("candidateReadDeferred") else ""},
        }

    def compact_delivered_payloads(
        self,
        policy: MySQLMinimalRetentionPolicy,
        now: datetime = None,
    ) -> Dict[str, object]:
        """Clear delivered outbox bodies without scanning unrelated history."""

        if not self._acquire_lock():
            return {
                "status": "skipped",
                "skipped": "locked",
                "deleted": 0,
                "compacted": 0,
                "tables": {},
                "policies": {},
            }

        budget = {
            "started": time.monotonic(),
            "maxSeconds": policy.max_run_seconds,
            "remainingBytes": policy.max_delete_bytes,
            "deletedBytes": 0,
        }
        tables: Dict[str, int] = {}
        policies: Dict[str, int] = {}
        compacted = 0
        estimated_bytes = 0
        try:
            actions = [
                (
                    "worldProjection:obsoletePayload",
                    self._compact_obsolete_world_projection_payloads,
                ),
                (
                    "timeSeriesProjection:deliveredPayload",
                    self._compact_completed_time_series_projection_payloads,
                ),
            ]
            for name, action in actions:
                if not self._has_budget(budget):
                    policies[name] = 0
                    continue
                result = action(policy, budget)
                changed = _integer(result.get("compacted"))
                compacted += changed
                estimated_bytes += _integer(result.get("estimatedBytes"))
                policies[name] = changed
                for table, count in dict(result.get("tables") or {}).items():
                    tables[table] = tables.get(table, 0) + _integer(count)
        finally:
            self._release_lock()

        return {
            "status": "ok",
            "deleted": 0,
            "compacted": compacted,
            "archived": 0,
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
            "progress": dict((result or {}).get("progress") or {}),
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

    def _compact_obsolete_world_projection_payloads(self, policy, budget) -> Dict[str, object]:
        scan_policy = "worldProjection:obsoletePayload"
        cursors = self._candidate_rows(
            "SELECT after_time, after_id FROM mysql_retention_scan_cursors "
            "WHERE profile = %s AND policy_name = %s", (policy.profile, scan_policy), budget,
        )
        cursor = cursors[0] if cursors else {}
        after_time = str(_row_value(cursor, "after_time", fallback="") or "")
        after_id = str(_row_value(cursor, "after_id", 1, "") or "")
        limit = min(8, policy.batch_size)
        ranges, range_params = [], []
        for status in TERMINAL_WORLD_PROJECTION_STATUSES:
            # Separate status ranges use the covering completion index. A
            # window function or JSON predicate before LIMIT scans all history.
            ranges.append(
                "(SELECT job_id, completed_at FROM `ontology_world_projection_outbox` "
                "FORCE INDEX (idx_world_projection_outbox_completed) "
                "WHERE status = %s AND (completed_at > %s OR (completed_at = %s AND job_id > %s)) "
                "ORDER BY completed_at, job_id LIMIT %s)"
            )
            range_params.extend((status, after_time, after_time, after_id, limit))
        # Merge at most sixteen headers using the database's collation, so the
        # persisted keyset has exactly the same ordering as each status range.
        window = self._candidate_rows(
            "SELECT job_id, completed_at FROM (" + " UNION ALL ".join(ranges)
            + ") candidate ORDER BY completed_at, job_id LIMIT %s", tuple(range_params) + (limit,), budget,
        )
        if not window:
            if (after_time or after_id) and self._has_budget(budget):
                self._save_scan_cursor(policy.profile, scan_policy, "", "")
            return self._result("ontology_world_projection_outbox", 0, 0)

        compacted = bytes_compacted = 0
        for item in window:
            key = str(_row_value(item, "job_id") or "")
            metadata = self._candidate_rows(
                "SELECT world_id, projection_kind, completed_at FROM `ontology_world_projection_outbox` "
                "WHERE job_id = %s AND status IN ('completed', 'superseded')", (key,), budget,
            )
            if metadata:
                row = metadata[0]
                world = str(_row_value(row, "world_id") or "")
                kind = str(_row_value(row, "projection_kind", 1) or "")
                completed_at = str(_row_value(row, "completed_at", 2) or "")
                newer = self._candidate_rows(
                    "SELECT job_id FROM `ontology_world_projection_outbox` "
                    "WHERE status = 'completed' AND world_id = %s AND projection_kind = %s AND completed_at <> '' "
                    "AND (completed_at > %s OR (completed_at = %s AND job_id > %s)) "
                    "ORDER BY completed_at DESC, job_id DESC LIMIT 1",
                    (world, kind, completed_at, completed_at, key), budget,
                )
                if newer:
                    # Read a cold payload only after its indexed identity and
                    # replacement are known, one primary key per deadline.
                    rows = self._candidate_rows(
                        "SELECT job_id, OCTET_LENGTH(payload_json) + OCTET_LENGTH(result_json) AS payload_bytes "
                        "FROM `ontology_world_projection_outbox` WHERE job_id = %s "
                        "AND status IN ('completed', 'superseded') "
                        "AND (payload_json <> '{}' OR result_json <> '{}')", (key,), budget,
                    )
                    candidates = self._bounded_candidate_rows(rows, "job_id", policy, budget)
                    if len(candidates) != len(rows) or not self._has_budget(budget):
                        break
                    if candidates:
                        updated = _execute(
                            self.connection,
                            "UPDATE `ontology_world_projection_outbox` older "
                            "JOIN `ontology_world_projection_outbox` newer ON newer.job_id = %s "
                            "AND newer.status = 'completed' AND newer.completed_at <> '' "
                            "AND newer.world_id = older.world_id AND newer.projection_kind = older.projection_kind "
                            "AND (newer.completed_at > older.completed_at "
                            "OR (newer.completed_at = older.completed_at AND newer.job_id > older.job_id)) "
                            "SET older.payload_json = '{}', older.result_json = '{}' "
                            "WHERE older.job_id = %s AND older.status IN ('completed', 'superseded') "
                            "AND older.world_id = %s AND older.projection_kind = %s AND older.completed_at = %s "
                            "AND (older.payload_json <> '{}' OR older.result_json <> '{}')",
                            (str(_row_value(newer[0], "job_id") or ""), key, world, kind, completed_at),
                        )
                        count = max(0, _integer(getattr(updated, "rowcount", 0)))
                        size = _integer(_row_value(candidates[0], "payload_bytes")) if count else 0
                        compacted += count
                        bytes_compacted += size
                        budget["remainingBytes"] = max(0, _integer(budget.get("remainingBytes")) - size)
                        budget["deletedBytes"] = _integer(budget.get("deletedBytes")) + size
            if not self._has_budget(budget):
                break
            # Skip protected/empty prefixes, but revisit them after a sweep.
            # Failed reads/writes and exhausted budgets leave the cursor before
            # unprocessed work, including after replacement of the worker.
            self._save_scan_cursor(policy.profile, scan_policy,
                                   str(_row_value(item, "completed_at", 1) or ""), key)
        return {"deleted": 0, "compacted": compacted, "estimatedBytes": bytes_compacted,
                "tables": {"ontology_world_projection_outbox": compacted} if compacted else {}}

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

    def _delete_completed_time_series_projection(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        candidates = self._byte_bounded_candidates(
            "SELECT job_id, OCTET_LENGTH(payload_json) AS payload_bytes "
            "FROM `time_series_projection_outbox` WHERE job_status = 'completed' AND updated_at < "
            + _cutoff_sql() + " ORDER BY updated_at, job_id LIMIT %s",
            (cutoff_iso, policy.batch_size),
            "job_id",
            policy,
            budget,
        )
        deleted, bytes_deleted = self._delete_candidates(
            "time_series_projection_outbox",
            "job_id",
            candidates,
            "job_status = 'completed' AND updated_at < " + _cutoff_sql(),
            (cutoff_iso,),
            budget,
        )
        return self._result("time_series_projection_outbox", deleted, bytes_deleted)

    def _compact_completed_time_series_projection_payloads(self, policy, budget) -> Dict[str, object]:
        candidates = self._byte_bounded_candidates(
            "SELECT job_id, OCTET_LENGTH(payload_json) AS payload_bytes "
            "FROM `time_series_projection_outbox` "
            "WHERE job_status = 'completed' AND payload_json <> '{}' "
            "ORDER BY updated_at, job_id LIMIT %s",
            (policy.batch_size,),
            "job_id",
            policy,
            budget,
        )
        return self._compact_payload_candidates(
            "time_series_projection_outbox",
            candidates,
            "job_status = 'completed'",
            budget,
        )

    def _compact_payload_candidates(
        self,
        table: str,
        candidates: Sequence[object],
        guard_sql: str,
        budget: Dict[str, object],
        clear_result: bool = False,
    ) -> Dict[str, object]:
        selected = [
            row for row in candidates
            if str(_row_value(row, "job_id", fallback="") or "").strip()
        ]
        if not selected:
            return {
                "deleted": 0,
                "compacted": 0,
                "estimatedBytes": 0,
                "tables": {},
            }

        job_ids = [
            str(_row_value(row, "job_id", fallback="") or "").strip()
            for row in selected
        ]
        cursor = _execute(
            self.connection,
            "UPDATE " + quote_identifier(table)
            + (
                " SET payload_json = '{}', result_json = '{}'"
                if clear_result
                else " SET payload_json = '{}'"
            )
            + " WHERE job_id IN ("
            + ", ".join(["%s"] * len(job_ids))
            + ") AND " + guard_sql
            + (
                " AND (payload_json <> '{}' OR result_json <> '{}')"
                if clear_result
                else " AND payload_json <> '{}'"
            ),
            tuple(job_ids),
        )
        compacted = max(0, _integer(getattr(cursor, "rowcount", 0)))
        selected_bytes = sum(
            max(0, _integer(_row_value(row, "payload_bytes")))
            for row in selected
        )
        bytes_compacted = (
            selected_bytes
            if compacted >= len(selected)
            else int(selected_bytes * compacted / max(1, len(selected)))
        )
        budget["remainingBytes"] = max(
            0,
            _integer(budget.get("remainingBytes")) - bytes_compacted,
        )
        budget["deletedBytes"] = (
            _integer(budget.get("deletedBytes")) + bytes_compacted
        )
        return {
            "deleted": 0,
            "compacted": compacted,
            "estimatedBytes": bytes_compacted,
            "tables": {table: compacted} if compacted else {},
        }

    def _delete_temporal_feature_snapshots(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        scan_policy = "temporalFeatureSnapshots:history"
        cursors = self._candidate_rows(
            "SELECT after_time, after_id FROM mysql_retention_scan_cursors "
            "WHERE profile = %s AND policy_name = %s", (policy.profile, scan_policy), budget,
        )
        cursor = cursors[0] if cursors else {}
        after_time = str(_row_value(cursor, "after_time", fallback="") or "")
        after_id = str(_row_value(cursor, "after_id", 1, "") or "")
        # Bound the covering-index scan before checking references or loading
        # windows. Protected prefixes must not make LIMIT scan all history.
        window = self._candidate_rows(
            "SELECT snapshot_id, created_at FROM `temporal_feature_snapshots` "
            "FORCE INDEX (idx_temporal_feature_created) WHERE created_at < %s "
            "AND (created_at > %s OR (created_at = %s AND snapshot_id > %s)) "
            "ORDER BY created_at, snapshot_id LIMIT %s",
            (cutoff_iso, after_time, after_time, after_id, min(8, policy.batch_size)), budget,
        )
        if not window:
            if (after_time or after_id) and self._has_budget(budget):
                self._save_scan_cursor(policy.profile, scan_policy, "", "")
            return self._result("temporal_feature_snapshots", 0, 0)
        guard = ("created_at < " + _cutoff_sql()
                 + " AND NOT EXISTS (SELECT 1 FROM statistical_model_signal_snapshots signal_snapshot "
                 "WHERE signal_snapshot.source_feature_snapshot_id = temporal_feature_snapshots.snapshot_id)")
        deleted = bytes_deleted = 0
        for item in window:
            key = str(_row_value(item, "snapshot_id") or "")
            rows = self._candidate_rows(
                "SELECT snapshot_id, OCTET_LENGTH(windows_json) AS payload_bytes "
                "FROM `temporal_feature_snapshots` WHERE snapshot_id = %s AND " + guard,
                (key, cutoff_iso), budget,
            )
            candidates = self._bounded_candidate_rows(rows, "snapshot_id", policy, budget)
            # The existing archive boundary verifies exact bytes and rechecks
            # references and the retention clock before evicting the hot row.
            count, size = self._delete_candidates(
                "temporal_feature_snapshots", "snapshot_id", candidates, guard, (cutoff_iso,), budget,
            )
            deleted += count
            bytes_deleted += size
            if (len(candidates) != len(rows) or not self._has_budget(budget)
                    or (candidates and not count)):
                break
            self._save_scan_cursor(policy.profile, scan_policy,
                                   str(_row_value(item, "created_at", 1) or ""), key)
        return self._result("temporal_feature_snapshots", deleted, bytes_deleted)

    def _delete_statistical_model_signal_snapshots(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        current_guard = (
            "created_at < " + _cutoff_sql()
            + " AND NOT EXISTS (SELECT 1 FROM `statistical_model_signal_heads` head "
            + "WHERE head.snapshot_id = statistical_model_signal_snapshots.snapshot_id)"
            + " AND NOT EXISTS (SELECT 1 FROM `statistical_model_assessment_heads` assessment "
            + "WHERE assessment.snapshot_id = statistical_model_signal_snapshots.snapshot_id)"
        )
        candidates = self._byte_bounded_candidates(
            "SELECT snapshot_id, OCTET_LENGTH(payload_json) AS payload_bytes "
            "FROM `statistical_model_signal_snapshots` WHERE " + current_guard
            + " ORDER BY created_at, snapshot_id LIMIT %s",
            (cutoff_iso, min(8, policy.batch_size)),
            "snapshot_id",
            policy,
            budget,
        )
        deleted, bytes_deleted = self._delete_candidates(
            "statistical_model_signal_snapshots",
            "snapshot_id",
            candidates,
            current_guard,
            (cutoff_iso,),
            budget,
        )
        return self._result("statistical_model_signal_snapshots", deleted, bytes_deleted)

    def _delete_reasoning_shadow_jobs(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        statuses = ("completed", "failed", "superseded")
        candidates = self._byte_bounded_candidates(
            "SELECT job_id, OCTET_LENGTH(payload_json) AS payload_bytes "
            "FROM `reasoning_engine_shadow_jobs` WHERE job_status IN ("
            + _status_placeholders(statuses) + ") AND updated_at < " + _cutoff_sql()
            + " ORDER BY updated_at, job_id LIMIT %s",
            statuses + (cutoff_iso, policy.batch_size),
            "job_id",
            policy,
            budget,
        )
        deleted, bytes_deleted = self._delete_candidates(
            "reasoning_engine_shadow_jobs",
            "job_id",
            candidates,
            "job_status IN (" + _status_placeholders(statuses) + ") AND updated_at < " + _cutoff_sql(),
            statuses + (cutoff_iso,),
            budget,
        )
        return self._result("reasoning_engine_shadow_jobs", deleted, bytes_deleted)

    def _delete_reasoning_engine_jobs(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        statuses = ("completed", "excluded", "failed", "superseded")
        candidates = self._byte_bounded_candidates(
            "SELECT job_id, OCTET_LENGTH(request_json) + OCTET_LENGTH(result_json) AS payload_bytes "
            "FROM `reasoning_engine_jobs` WHERE job_status IN ("
            + _status_placeholders(statuses) + ") AND updated_at < " + _cutoff_sql()
            + " AND NOT EXISTS (SELECT 1 FROM `market_observation_reasoning_anchors` anchor "
            "WHERE anchor.pending_event_id <> '' "
            "AND (anchor.pending_event_id = `reasoning_engine_jobs`.source_event_id OR EXISTS ("
            "SELECT 1 FROM `reasoning_engine_job_sources` source "
            "WHERE source.survivor_job_id = `reasoning_engine_jobs`.job_id "
            "AND source.source_event_id = anchor.pending_event_id)))"
            + " ORDER BY updated_at, job_id LIMIT %s",
            statuses + (cutoff_iso, policy.batch_size),
            "job_id",
            policy,
            budget,
        )
        deleted, bytes_deleted = self._delete_candidates(
            "reasoning_engine_jobs",
            "job_id",
            candidates,
            "job_status IN (" + _status_placeholders(statuses) + ") AND updated_at < " + _cutoff_sql()
            + " AND NOT EXISTS (SELECT 1 FROM `market_observation_reasoning_anchors` anchor "
            "WHERE anchor.pending_event_id <> '' "
            "AND (anchor.pending_event_id = `reasoning_engine_jobs`.source_event_id OR EXISTS ("
            "SELECT 1 FROM `reasoning_engine_job_sources` source "
            "WHERE source.survivor_job_id = `reasoning_engine_jobs`.job_id "
            "AND source.source_event_id = anchor.pending_event_id)))",
            statuses + (cutoff_iso,),
            budget,
        )
        return self._result("reasoning_engine_jobs", deleted, bytes_deleted)

    def _delete_reasoning_job_sources(self, policy, budget) -> Dict[str, object]:
        # A table-wide DELETE JOIN also locks live ingress lineage while looking
        # for orphans. Select a bounded, nonlocking key list, then delete by the
        # full primary key. Run independently of parent deletion so an interrupted
        # pass can collect its remaining orphans on a later scheduled turn.
        absent_parent = (
            "NOT EXISTS (SELECT 1 FROM `reasoning_engine_jobs` job "
            "WHERE job.job_id = `reasoning_engine_job_sources`.survivor_job_id)"
        )
        keys = ("survivor_job_id", "source_event_id", "account_id", "symbol")
        candidates = self._byte_bounded_candidates(
            "SELECT survivor_job_id, source_event_id, account_id, symbol, "
            "OCTET_LENGTH(deployment_id) + OCTET_LENGTH(survivor_job_id) + "
            "OCTET_LENGTH(source_event_id) + OCTET_LENGTH(account_id) + OCTET_LENGTH(symbol) + "
            "OCTET_LENGTH(source_snapshot_id) + OCTET_LENGTH(source_snapshot_at) + "
            "OCTET_LENGTH(representation_mode) + OCTET_LENGTH(created_at) AS payload_bytes "
            "FROM `reasoning_engine_job_sources` WHERE " + absent_parent
            + " ORDER BY survivor_job_id, source_event_id, account_id, symbol LIMIT %s",
            (policy.batch_size,), "survivor_job_id", policy, budget,
        )
        deleted = bytes_deleted = 0
        for row in candidates:
            if not self._has_budget(budget):
                break
            # Account and symbol may legitimately be empty in ingress lineage.
            values = tuple(_row_value(row, key, index=index, fallback="") for index, key in enumerate(keys))
            count = archive_delete_row(self.connection, "reasoning_engine_job_sources",
                                       dict(zip(keys, values)), absent_parent,
                                       may_evict=lambda: self._has_budget(budget))
            if count:
                deleted += 1
                row_bytes = max(0, _integer(_row_value(row, "payload_bytes")))
                bytes_deleted += row_bytes
                budget["remainingBytes"] = max(0, _integer(budget.get("remainingBytes")) - row_bytes)
                budget["deletedBytes"] = _integer(budget.get("deletedBytes")) + row_bytes
        return self._result("reasoning_engine_job_sources", deleted, bytes_deleted)

    def _delete_reasoning_source_snapshots(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        scan_policy = "reasoningSourceSnapshots:terminal"
        cursors = self._candidate_rows(
            "SELECT after_time, after_id FROM mysql_retention_scan_cursors "
            "WHERE profile = %s AND policy_name = %s", (policy.profile, scan_policy), budget,
        )
        cursor = cursors[0] if cursors else {}
        after_time = str(_row_value(cursor, "after_time", fallback="") or "")
        after_id = str(_row_value(cursor, "after_id", 1, "") or "")
        # Covering metadata only. Applying anti-joins before LIMIT lets MySQL
        # materialize whole job/mailbox tables, even with source indexes.
        window = self._candidate_rows(
            "SELECT snapshot_id, created_at FROM `verified_reasoning_source_snapshots` "
            "WHERE created_at < %s AND (created_at > %s OR (created_at = %s AND snapshot_id > %s)) "
            "ORDER BY created_at, snapshot_id LIMIT %s",
            (cutoff_iso, after_time, after_time, after_id, min(8, policy.batch_size)), budget,
        )
        if not window:
            if (after_time or after_id) and self._has_budget(budget):
                self._save_scan_cursor(policy.profile, scan_policy, "", "")
            return self._result("verified_reasoning_source_snapshots", 0, 0)
        guard = ("created_at < " + _cutoff_sql()
            + " AND NOT EXISTS (SELECT 1 FROM `reasoning_engine_jobs` job "
            "WHERE job.source_snapshot_id = `verified_reasoning_source_snapshots`.snapshot_id "
            "AND job.job_status IN ('queued', 'retry', 'processing', 'awaiting_world_projection')) "
            "AND NOT EXISTS (SELECT 1 FROM `ontology_reasoning_mailbox_events` mailbox "
            "WHERE mailbox.source_snapshot_id = `verified_reasoning_source_snapshots`.snapshot_id "
            "AND mailbox.state IN ('pending', 'direct-pending'))")
        deleted = bytes_deleted = 0
        for item in window:
            # A single source packet can be many megabytes. Do not fetch eight
            # cold/compressed payloads under one SELECT deadline.
            key = str(_row_value(item, "snapshot_id") or "")
            rows = self._candidate_rows(
                "SELECT snapshot_id, OCTET_LENGTH(payload_json) AS payload_bytes "
                "FROM `verified_reasoning_source_snapshots` WHERE snapshot_id = %s AND " + guard,
                (key, cutoff_iso), budget,
            )
            candidates = self._bounded_candidate_rows(rows, "snapshot_id", policy, budget)
            count, size = self._delete_candidates(
                "verified_reasoning_source_snapshots", "snapshot_id", candidates, guard, (cutoff_iso,), budget,
            )
            deleted += count
            bytes_deleted += size
            if len(candidates) != len(rows) or not self._has_budget(budget):
                break
            # Protected prefixes must not starve later rows. Rewind at the end
            # of each sweep to revisit sources whose consumers finish later.
            # Failed reads/deletes and exhausted budgets cannot advance here.
            self._save_scan_cursor(policy.profile, scan_policy,
                                   str(_row_value(item, "created_at", 1) or ""), key)
        return self._result("verified_reasoning_source_snapshots", deleted, bytes_deleted)

    def _delete_reasoning_comparisons(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        # Materialize only indexed IDs before touching potentially large JSON.
        # LIMIT inside the derived table prevents merging it into a payload scan.
        candidates = self._byte_bounded_candidates(
            "SELECT comparison.comparison_id, OCTET_LENGTH(comparison.payload_json) AS payload_bytes "
            "FROM (SELECT comparison_id, created_at FROM `reasoning_engine_comparisons` "
            "WHERE created_at < " + _cutoff_sql()
            + " ORDER BY created_at, comparison_id LIMIT %s) candidate "
            "JOIN `reasoning_engine_comparisons` comparison ON comparison.comparison_id = candidate.comparison_id "
            "ORDER BY candidate.created_at, candidate.comparison_id",
            (cutoff_iso, min(8, policy.batch_size)),
            "comparison_id",
            policy,
            budget,
        )
        deleted, bytes_deleted = self._delete_candidates(
            "reasoning_engine_comparisons",
            "comparison_id",
            candidates,
            "created_at < " + _cutoff_sql(),
            (cutoff_iso,),
            budget,
        )
        return self._result("reasoning_engine_comparisons", deleted, bytes_deleted)

    def _delete_investment_reasoning_cases(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        """Preserve decision identity and replay roots independently of age."""
        return self._result("investment_reasoning_cases", 0, 0)

    def _delete_decision_publications(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        return self._delete_subject_artifacts(
            "decision_publications", "publication_id", policy, budget, cutoff_iso,
        )

    def _delete_decision_candidate_snapshots(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        return self._delete_subject_artifacts(
            "decision_candidate_snapshots", "candidate_set_id", policy, budget, cutoff_iso,
        )

    def _delete_subject_artifacts(self, table, identifier, policy, budget, cutoff_iso) -> Dict[str, object]:
        if table in PROTECTED_HISTORY_TABLES:
            return self._result(table, 0, 0)
        quoted_table, quoted_id = quote_identifier(table), quote_identifier(identifier)
        # Join only IDs while finding the oldest eligible subjects. The LIMIT
        # materializes that small set before any LONGTEXT payload is accessed.
        # The subject's clock + PK order matches its retention index; ordering
        # by a child ID instead forces a sort across the joined history.
        candidates = self._byte_bounded_candidates(
            "SELECT artifact." + quoted_id + ", OCTET_LENGTH(artifact.payload_json) AS payload_bytes "
            "FROM (SELECT child." + quoted_id + ", subject.completed_at, subject.subject_case_id "
            "FROM `investment_subject_decision_cases` subject "
            "JOIN " + quoted_table + " child ON child.subject_case_id = subject.subject_case_id "
            "WHERE subject.completed_at <> '' AND subject.completed_at < " + _cutoff_sql()
            + " ORDER BY subject.completed_at, subject.subject_case_id LIMIT %s) candidates "
            "JOIN " + quoted_table + " artifact ON artifact." + quoted_id + " = candidates." + quoted_id
            + " ORDER BY candidates.completed_at, candidates.subject_case_id",
            (cutoff_iso, policy.batch_size),
            identifier,
            policy,
            budget,
        )
        deleted, bytes_deleted = self._delete_candidates(
            table,
            identifier,
            candidates,
            "EXISTS (SELECT 1 FROM `investment_subject_decision_cases` subject "
            "WHERE subject.subject_case_id = " + quoted_table + ".subject_case_id "
            "AND subject.completed_at <> '' AND subject.completed_at < " + _cutoff_sql() + ")",
            (cutoff_iso,),
            budget,
        )
        return self._result(table, deleted, bytes_deleted)

    def _delete_subject_decision_cases(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        """Preserve decision identity and replay roots independently of age."""
        return self._result("investment_subject_decision_cases", 0, 0)

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
        from .mysql_retention import _notification_cooldown_history_cutoff
        protected_since = _notification_cooldown_history_cutoff(self.connection)
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
            "status IN (" + _status_placeholders(TERMINAL_NOTIFICATION_STATUSES) + ") AND updated_at < " + _cutoff_sql()
            + " AND NOT EXISTS (SELECT 1 FROM notification_delivery_attempts receipt "
            "WHERE receipt.job_id=notification_jobs.job_id AND receipt.status='delivered' AND receipt.completed_at >= %s)",
            tuple(TERMINAL_NOTIFICATION_STATUSES) + (cutoff_iso, protected_since),
            budget,
        )
        return self._result("notification_jobs", deleted, bytes_deleted)

    def _delete_decision_notification_receipts(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        """Preserve decision identity and replay roots independently of age."""
        return self._result("decision_notification_receipts", 0, 0)

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
                "AND " + _history_unreferenced_sql() + " "
                "ORDER BY generated_at LIMIT %s",
                (account_id, boundary_at, min(8, policy.batch_size - len(candidate_rows))),
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
                "DELETE FROM `monitor_snapshot_history` WHERE account_id = %s AND generated_at = %s AND "
                + _history_unreferenced_sql(),
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
            + " AND NOT EXISTS (SELECT 1 FROM ontology_projection_trace_outbox d WHERE d.run_id = ontology_projection_runs.run_id)"
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
                WHERE run_id = %s AND status <> 'projecting' AND updated_at < """ + _cutoff_sql()
                + " AND NOT EXISTS (SELECT 1 FROM ontology_projection_trace_outbox d WHERE d.run_id = ontology_projection_runs.run_id)",
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
                "AND NOT EXISTS (SELECT 1 FROM ontology_projection_trace_outbox d WHERE d.run_id = ontology_projection_runs.run_id) "
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
            "status <> 'projecting' AND updated_at < " + _cutoff_sql()
            + " AND NOT EXISTS (SELECT 1 FROM ontology_projection_trace_outbox d WHERE d.run_id = ontology_projection_runs.run_id)",
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
        scan_policy = "research:inactiveEvidence"
        cursors = self._candidate_rows(
            "SELECT after_time, after_id FROM mysql_retention_scan_cursors "
            "WHERE profile = %s AND policy_name = %s", (policy.profile, scan_policy), budget,
        )
        cursor = cursors[0] if cursors else {}
        after_id = str(_row_value(cursor, "after_id", 1, "") or "")
        # lifecycle_changed_at has no ordering index. Sorting inactive rows
        # before LIMIT reads unbounded cold records. Walk at most eight primary
        # keys instead, including active rows, and persist progress past them.
        window = self._candidate_rows(
            "SELECT evidence_id FROM `research_evidence` FORCE INDEX (PRIMARY) "
            "WHERE evidence_id > %s ORDER BY evidence_id LIMIT %s",
            (after_id, min(8, policy.batch_size)), budget,
        )
        if not window:
            if after_id and self._has_budget(budget):
                self._save_scan_cursor(policy.profile, scan_policy, "", "")
            return self._result("research_evidence", 0, 0)
        guard = "lifecycle_state IN ('expired', 'retracted') AND lifecycle_changed_at < " + _cutoff_sql()
        deleted = bytes_deleted = 0
        for item in window:
            key = str(_row_value(item, "evidence_id") or "")
            rows = self._candidate_rows(
                "SELECT evidence_id, OCTET_LENGTH(payload_json) AS payload_bytes "
                "FROM `research_evidence` WHERE evidence_id = %s AND " + guard,
                (key, cutoff_iso), budget,
            )
            candidates = self._bounded_candidate_rows(rows, "evidence_id", policy, budget)
            count, size = self._delete_candidates(
                "research_evidence", "evidence_id", candidates, guard, (cutoff_iso,), budget,
            )
            deleted += count
            bytes_deleted += size
            if (len(candidates) != len(rows) or not self._has_budget(budget)
                    or (candidates and not count)):
                break
            # Exceptions/budget exhaustion cannot skip an unfinished key.
            # Wrapping the sweep revisits evidence that becomes inactive later.
            self._save_scan_cursor(policy.profile, scan_policy, "", key)
        return self._result("research_evidence", deleted, bytes_deleted)

    def _delete_terminal_research_runs(self, policy, budget, cutoff_iso) -> Dict[str, object]:
        # LIMIT after a JSON predicate still reads every protected audit body.
        # Walk a covering metadata index first, then inspect at most eight
        # primary keys. Persist the position so protected prefixes cannot
        # starve later expired rows when a worker is replaced between turns.
        cursors = self._candidate_rows(
            "SELECT after_time, after_id FROM mysql_retention_scan_cursors "
            "WHERE profile = %s AND policy_name = %s",
            (policy.profile, "research:terminalRuns"), budget,
        )
        cursor = cursors[0] if cursors else {}
        after_time = str(_row_value(cursor, "after_time", fallback="") or "")
        after_id = str(_row_value(cursor, "after_id", 1, "") or "")
        window = self._candidate_rows(
            "SELECT run_id, updated_at FROM `investment_research_runs` "
            "WHERE updated_at < %s AND (updated_at > %s OR (updated_at = %s AND run_id > %s)) "
            "ORDER BY updated_at, run_id LIMIT %s",
            (cutoff_iso, after_time, after_time, after_id, min(8, policy.batch_size)), budget,
        )
        if not window:
            if (after_time or after_id) and self._has_budget(budget):
                # Revisit audits on the next sweep as their horizons expire.
                self._save_research_scan_cursor(policy.profile, "", "")
            return self._result("investment_research_runs", 0, 0)
        identifiers = tuple(str(_row_value(row, "run_id") or "") for row in window)
        guard = ("completed_at <> '' AND status NOT IN ("
                 + _status_placeholders(ACTIVE_RESEARCH_RUN_STATUSES)
                 + ") AND updated_at < " + _cutoff_sql() + " AND " + _research_audit_expired_sql())
        guard_params = tuple(ACTIVE_RESEARCH_RUN_STATUSES) + (cutoff_iso, cutoff_iso)
        rows = self._candidate_rows(
            "SELECT run_id, OCTET_LENGTH(payload_json) AS payload_bytes "
            "FROM `investment_research_runs` WHERE run_id IN ("
            + _status_placeholders(identifiers) + ") AND " + guard
            + " ORDER BY updated_at, run_id",
            identifiers + guard_params, budget,
        )
        candidates = self._bounded_candidate_rows(rows, "run_id", policy, budget)
        deleted, bytes_deleted = self._delete_candidates(
            "investment_research_runs", "run_id", candidates, guard, guard_params, budget,
        )
        # Do not advance past candidates excluded by byte/time budgets, or a
        # failed DELETE. Already committed deletions are harmless to revisit.
        if len(candidates) == len(rows) and self._has_budget(budget):
            self._save_research_scan_cursor(
                policy.profile, str(_row_value(window[-1], "updated_at", 1) or ""), identifiers[-1],
            )
        return self._result("investment_research_runs", deleted, bytes_deleted)

    def _save_research_scan_cursor(self, profile, after_time, after_id):
        self._save_scan_cursor(profile, "research:terminalRuns", after_time, after_id)

    def _save_scan_cursor(self, profile, policy_name, after_time, after_id):
        _execute(self.connection,
            "INSERT INTO mysql_retention_scan_cursors (profile,policy_name,after_time,after_id,updated_at) "
            "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
            "after_time=VALUES(after_time),after_id=VALUES(after_id),updated_at=VALUES(updated_at)",
            (profile, policy_name, after_time, after_id, self._iso()))

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
        rows = self._candidate_rows(sql, params, budget)
        return self._bounded_candidate_rows(rows, identifier, policy, budget, compound_key=compound_key)

    def _candidate_rows(self, sql: str, params: Sequence[object], budget: Dict[str, object]) -> List[object]:
        if not self._has_budget(budget):
            return []
        if self.candidate_query_timeout_ms:
            remaining_ms = int(1000 * (float(budget["maxSeconds"])
                                      - (time.monotonic() - float(budget["started"]))))
            # Reserve a useful read window instead of starting a large packet
            # read with only e.g. 250 ms left and reporting normal turn expiry
            # as a database outage. Short configured turns must still progress.
            admission_ms = min(self.candidate_query_timeout_ms,
                               max(1, int(500 * float(budget["maxSeconds"]))))
            if remaining_ms < admission_ms:
                budget["candidateReadDeferred"] = True
                return []
            timeout_ms = min(self.candidate_query_timeout_ms, remaining_ms)
            if timeout_ms <= 0:
                return []
            # A socket timeout alone can leave the SELECT running on MySQL.
            # Scope this server deadline to the read, without changing pooled
            # session settings or applying a SELECT-only hint to mutations.
            sql = sql.replace("SELECT ", "SELECT /*+ MAX_EXECUTION_TIME(" + str(timeout_ms) + ") */ ", 1)
        budget["candidateReads"] = _integer(budget.get("candidateReads")) + 1
        return _fetchall(_execute(self.connection, sql, params))

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
        if table in PROTECTED_HISTORY_TABLES:
            return 0, 0
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
            if table in ARCHIVE_BEFORE_DELETE:
                count = archive_delete_row(self.connection, table, {primary_key: key}, guard_sql, guard_params,
                                           may_evict=lambda: self._has_budget(budget))
            else:
                cursor = _execute(
                    self.connection,
                    "DELETE FROM " + quoted_table + " WHERE " + quoted_key + " = %s AND " + guard_sql,
                    (key,) + tuple(guard_params or ()),
                )
                count = _integer(getattr(cursor, "rowcount", 0))
            if count:
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
            not budget.get("candidateReadDeferred")
            and _integer(budget.get("remainingBytes")) > 0
            and (time.monotonic() - float(budget.get("started") or 0)) < _integer(budget.get("maxSeconds"), 1)
        )

    def _acquire_lock(self) -> bool:
        row = _fetchone(_execute(self.connection, "SELECT GET_LOCK(%s, 0) AS acquired", (MYSQL_MINIMAL_RETENTION_LOCK_NAME,)))
        acquired = _row_value(row, "acquired")
        if acquired is None:
            raise RuntimeError("MySQL retention lock acquisition returned NULL")
        return _integer(acquired) == 1

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
