from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Dict, List, Mapping, Optional, Sequence

from .mysql_schema_tuning import quote_identifier


FALSE_VALUES = {"0", "false", "no", "off", "disabled", "disable", "none"}
TRUE_VALUES = {"1", "true", "yes", "on", "enabled", "enable"}
DEFAULT_RETENTION_HOURS = 12
# Retention is deliberately low-priority. A large delete can hold page locks,
# saturate the redo log, and turn routine history cleanup into an outage.
DEFAULT_BATCH_SIZE = 50
DEFAULT_CHECK_INTERVAL_SECONDS = 120
DEFAULT_SNAPSHOT_HISTORY_KEEP_COUNT = 2
DEFAULT_SUPPRESSED_NOTIFICATION_RETENTION_MINUTES = 120
DEFAULT_LARGE_DOMAIN_EVENT_KEEP_COUNT = 20
DEFAULT_DELIVERED_NOTIFICATION_KEEP_COUNT = 30
DEFAULT_SENT_ARTICLE_DELIVERY_LEDGER_RETENTION_DAYS = 365
# The event log is a transport/audit trail, not the canonical store for the
# same snapshot, evidence claim, or delivered notification. These payloads
# can be large, so retain a bounded operator window per high-volume event.
DEFAULT_LARGE_DOMAIN_EVENT_NAMES = (
    "monitoring.alerts_detected",
    "monitoring.snapshot_collected",
    "market_data.collected",
    "research_evidence.collected",
    "ontology.reasoning_requested",
)
DEFAULT_PROJECTION_RUN_KEEP_COUNT = 2
DEFAULT_ONTOLOGY_EXECUTION_TRACE_RETENTION_DAYS = 90
DEFAULT_WORLD_PROJECTION_OUTBOX_RETENTION_HOURS = 6
DEFAULT_INFERENCE_DETAIL_OUTBOX_RETENTION_HOURS = 24 * 7
DEFAULT_HYPOTHESIS_LIFECYCLE_EVENT_RETENTION_DAYS = 90
DEFAULT_MARKET_TIME_SERIES_RETENTION_DAYS = {
    "3m": 7,
    "15m": 30,
    "1h": 365,
    "1d": 1825,
}
RETENTION_LOCK_NAME = "orbit_alpha_operational_history_retention"
EPHEMERAL_MYSQL_DATABASE_PATTERN = re.compile(
    r"^orbit_alpha_(?:debug_smoke|smoke_[A-Za-z0-9_]+|test(?:_[A-Za-z0-9_]+)?)$"
)

# Only these application-owned tables may be rebuilt by an explicit operator
# maintenance command. Runtime retention never runs OPTIMIZE TABLE itself.
MYSQL_OPERATIONAL_COMPACTION_TABLES = frozenset({
    "app_store",
    "domain_events",
    "monitor_snapshots",
    "monitor_snapshot_history",
    "verified_reasoning_source_snapshots",
    "notification_jobs",
    "notification_article_delivery_ledger",
    "news_notification_admissions",
    "ai_inference_subject_heads",
    "ai_inference_requests",
    "ai_inference_results",
    "news_article_enrichment_revisions",
    "news_analysis_work_items",
    "model_review_jobs",
    "monitor_sent",
    "market_quote_cache",
    "ontology_ai_opinion_samples",
    "ontology_projection_runs",
    "ontology_reasoning_run_stages",
    "ontology_reasoning_rule_runs",
    "ontology_world_projection_outbox",
    "ontology_inference_detail_outbox",
    "investment_decision_episodes",
    "investment_subject_decision_cases",
    "decision_candidate_snapshots",
    "decision_publications",
    "investment_decision_follow_ups",
    "investment_decision_outcomes",
    "investment_decision_outcome_targets",
    "investment_hypothesis_proposal_requests",
    "mysql_retention_runs",
    "investment_hypothesis_lifecycle_states",
    "investment_hypothesis_lifecycle_events",
    "investment_hypothesis_transition_history",
    "investment_research_runs",
    "research_evidence",
    "market_time_series_observations",
    "ontology_reasoning_mailbox_events",
    "portfolio_rebalance_review_windows",
    "symbol_universe",
    "symbol_universe_sources",
    "shared_instrument_inference_snapshots",
    "portfolio_inference_overlays",
})


@dataclass(frozen=True)
class MySQLRetentionTarget:
    table: str
    time_column: str


MYSQL_OPERATIONAL_HISTORY_RETENTION_TARGETS = (
    MySQLRetentionTarget("domain_events", "occurred_at"),
    MySQLRetentionTarget("monitor_snapshot_history", "generated_at"),
    MySQLRetentionTarget("monitor_sent", "sent_at"),
    MySQLRetentionTarget("ontology_ai_opinion_samples", "created_at"),
)


def _execute(connection, sql: str, params=()):
    if hasattr(connection, "execute"):
        return connection.execute(sql, params)
    cursor = connection.cursor()
    cursor.execute(sql, params or ())
    return cursor


def _setting(settings: Mapping[str, object], key: str, fallback: object) -> object:
    if settings and key in settings:
        return settings.get(key)
    return fallback


def _int_setting(settings: Mapping[str, object], key: str, fallback: int, minimum: int, maximum: int) -> int:
    raw = _setting(settings, key, fallback)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, value))


def _csv_setting(settings: Mapping[str, object], key: str, fallback: Sequence[str]) -> List[str]:
    raw = _setting(settings or {}, key, ",".join(fallback))
    if isinstance(raw, (list, tuple)):
        values = [str(item or "").strip() for item in raw]
    else:
        values = [item.strip() for item in str(raw or "").split(",")]
    return [item for item in values if item]


def operational_history_retention_enabled(settings: Mapping[str, object] = None) -> bool:
    raw = str(_setting(settings or {}, "operationalHistoryRetentionEnabled", "1")).strip().lower()
    if raw in FALSE_VALUES:
        return False
    if raw in TRUE_VALUES:
        return True
    return True


def operational_history_retention_hours(settings: Mapping[str, object] = None) -> int:
    return _int_setting(settings or {}, "operationalHistoryRetentionHours", DEFAULT_RETENTION_HOURS, 1, 24 * 30)


def operational_history_retention_batch_size(settings: Mapping[str, object] = None) -> int:
    # Clamp legacy 1,000-row settings too. The next maintenance turn will
    # continue any backlog, so keeping a transaction short is more important
    # than draining it in one pass.
    configured = settings or {}
    if "_effectiveOperationalHistoryRetentionBatchSize" in configured:
        return _int_setting(
            configured,
            "_effectiveOperationalHistoryRetentionBatchSize",
            DEFAULT_BATCH_SIZE,
            1,
            500,
        )
    return _int_setting(configured, "operationalHistoryRetentionBatchSize", DEFAULT_BATCH_SIZE, 1, 50)


def operational_history_retention_check_interval_seconds(settings: Mapping[str, object] = None) -> int:
    return _int_setting(
        settings or {},
        "operationalHistoryRetentionCheckIntervalSeconds",
        DEFAULT_CHECK_INTERVAL_SECONDS,
        60,
        24 * 3600,
    )


def operational_snapshot_history_keep_count(settings: Mapping[str, object] = None) -> int:
    return _int_setting(
        settings or {},
        "operationalSnapshotHistoryKeepCount",
        DEFAULT_SNAPSHOT_HISTORY_KEEP_COUNT,
        1,
        500,
    )


def operational_suppressed_notification_retention_minutes(settings: Mapping[str, object] = None) -> int:
    return _int_setting(
        settings or {},
        "operationalSuppressedNotificationRetentionMinutes",
        DEFAULT_SUPPRESSED_NOTIFICATION_RETENTION_MINUTES,
        1,
        24 * 60,
    )


def operational_delivered_notification_keep_count(settings: Mapping[str, object] = None) -> int:
    """Keep a compact per-account delivery history, not duplicated alert payloads."""
    return _int_setting(
        settings or {},
        "operationalDeliveredNotificationKeepCount",
        DEFAULT_DELIVERED_NOTIFICATION_KEEP_COUNT,
        5,
        500,
    )


def sent_article_delivery_ledger_retention_days(settings: Mapping[str, object] = None) -> int:
    """Keep compact article identities after full notification payloads expire."""
    return _int_setting(
        settings or {},
        "sentArticleDeliveryLedgerRetentionDays",
        DEFAULT_SENT_ARTICLE_DELIVERY_LEDGER_RETENTION_DAYS,
        1,
        365,
    )


def operational_projection_run_keep_count(settings: Mapping[str, object] = None) -> int:
    """Keep a compact, per-world projection audit window.

    MySQL remains the source record for portfolio snapshots and decision
    episodes. Projection runs contain duplicated graph-sized payloads, so
    retaining every successful replay grows storage without improving the
    active ABox or InferenceBox read path.
    """
    return _int_setting(
        settings or {},
        "operationalProjectionRunKeepCount",
        DEFAULT_PROJECTION_RUN_KEEP_COUNT,
        2,
        500,
    )


def ontology_execution_trace_retention_days(settings: Mapping[str, object] = None) -> int:
    return _int_setting(
        settings or {},
        "ontologyExecutionTraceRetentionDays",
        DEFAULT_ONTOLOGY_EXECUTION_TRACE_RETENTION_DAYS,
        1,
        365,
    )


def operational_world_projection_outbox_retention_hours(settings: Mapping[str, object] = None) -> int:
    # Projection packets can each carry a multi-megabyte ABox result.  Cap
    # legacy seven-day settings as well as new values so retained queue audit
    # data cannot exhaust the local MySQL volume again.
    return _int_setting(
        settings or {},
        "ontologyWorldProjectionCompletedRetentionHours",
        DEFAULT_WORLD_PROJECTION_OUTBOX_RETENTION_HOURS,
        1,
        DEFAULT_WORLD_PROJECTION_OUTBOX_RETENTION_HOURS,
    )


def operational_inference_detail_outbox_retention_hours(settings: Mapping[str, object] = None) -> int:
    return _int_setting(
        settings or {},
        "ontologyInferenceDetailCompletedRetentionHours",
        DEFAULT_INFERENCE_DETAIL_OUTBOX_RETENTION_HOURS,
        24,
        24 * 365,
    )


def operational_ai_inference_queue_retention_hours(settings: Mapping[str, object] = None) -> int:
    return _int_setting(settings or {}, "notificationAiQueueRetentionHours", 24, 1, 24 * 30)


def operational_large_domain_event_keep_count(settings: Mapping[str, object] = None) -> int:
    return _int_setting(
        settings or {},
        "operationalLargeDomainEventKeepCount",
        DEFAULT_LARGE_DOMAIN_EVENT_KEEP_COUNT,
        1,
        10000,
    )


def operational_large_domain_event_names(settings: Mapping[str, object] = None) -> List[str]:
    configured = _csv_setting(
        settings or {},
        "operationalLargeDomainEventNames",
        DEFAULT_LARGE_DOMAIN_EVENT_NAMES,
    )
    # Critical high-volume events must remain bounded even when an existing
    # local settings row predates a new default.  Their canonical facts live
    # in dedicated stores; this table is a transport/audit window.
    return list(dict.fromkeys(list(DEFAULT_LARGE_DOMAIN_EVENT_NAMES) + configured))


def market_time_series_retention_days(settings: Mapping[str, object] = None) -> Dict[str, int]:
    configured = settings or {}
    return {
        "3m": _int_setting(configured, "marketTimeSeriesRawRetentionDays", 7, 1, 3650),
        "15m": _int_setting(configured, "marketTimeSeries15mRetentionDays", 30, 1, 36500),
        "1h": _int_setting(configured, "marketTimeSeries1hRetentionDays", 365, 1, 36500),
        "1d": _int_setting(configured, "marketTimeSeriesDailyRetentionDays", 1825, 1, 36500),
    }


def hypothesis_lifecycle_event_retention_days(settings: Mapping[str, object] = None) -> int:
    return _int_setting(
        settings or {},
        "hypothesisLifecycleEventRetentionDays",
        DEFAULT_HYPOTHESIS_LIFECYCLE_EVENT_RETENTION_DAYS,
        1,
        3650,
    )


def operational_history_retention_cutoff(
    settings: Mapping[str, object] = None,
    now: Optional[datetime] = None,
) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    cutoff = current - timedelta(hours=operational_history_retention_hours(settings))
    return cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def operational_suppressed_notification_cutoff(
    settings: Mapping[str, object] = None,
    now: Optional[datetime] = None,
) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    cutoff = current - timedelta(minutes=operational_suppressed_notification_retention_minutes(settings))
    return cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sent_article_delivery_ledger_cutoff(
    settings: Mapping[str, object] = None,
    now: Optional[datetime] = None,
) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    cutoff = current - timedelta(days=sent_article_delivery_ledger_retention_days(settings))
    return cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def market_time_series_retention_cutoffs(
    settings: Mapping[str, object] = None,
    now: Optional[datetime] = None,
) -> Dict[str, str]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    return {
        granularity: (current - timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        for granularity, days in market_time_series_retention_days(settings).items()
    }


def hypothesis_lifecycle_event_retention_cutoff(
    settings: Mapping[str, object] = None,
    now: Optional[datetime] = None,
) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    cutoff = current - timedelta(days=hypothesis_lifecycle_event_retention_days(settings))
    return cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fetch_scalar(cursor):
    row = cursor.fetchone()
    if not row:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def _acquire_lock(connection) -> bool:
    cursor = _execute(connection, "SELECT GET_LOCK(%s, 0) AS acquired", (RETENTION_LOCK_NAME,))
    return _fetch_scalar(cursor) == 1


def _release_lock(connection) -> None:
    _execute(connection, "SELECT RELEASE_LOCK(%s)", (RETENTION_LOCK_NAME,))


def _delete_one_batch(connection, sql: str, params=()) -> int:
    """Delete one bounded batch; later maintenance turns drain the rest."""
    cursor = _execute(connection, sql, params)
    return int(getattr(cursor, "rowcount", 0) or 0)


def _row_identifiers(rows, key: str) -> List[str]:
    identifiers = []
    for row in rows or []:
        value = row.get(key) if isinstance(row, dict) else row[0]
        identifier = str(value or "").strip()
        if identifier:
            identifiers.append(identifier)
    return identifiers


def _delete_stale_rows(connection, target: MySQLRetentionTarget, cutoff_iso: str, batch_size: int) -> int:
    table = quote_identifier(target.table)
    time_column = quote_identifier(target.time_column)
    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    sql = (
        "DELETE FROM "
        + table
        + " WHERE "
        + time_column
        + " < "
        + cutoff_sql
        + " ORDER BY "
        + time_column
        + " LIMIT %s"
    )
    return _delete_one_batch(connection, sql, (cutoff_iso, batch_size))


def _delete_stale_reasoning_source_snapshots(connection, cutoff_iso: str, batch_size: int) -> int:
    """Delete replay packets only after every referencing job is terminal."""

    sql = (
        "DELETE FROM `verified_reasoning_source_snapshots` WHERE snapshot_id IN ("
        "SELECT snapshot_id FROM (SELECT source.snapshot_id "
        "FROM `verified_reasoning_source_snapshots` source "
        "WHERE source.created_at < %s AND NOT EXISTS ("
        "SELECT 1 FROM `reasoning_engine_jobs` job "
        "WHERE job.source_snapshot_id = source.snapshot_id "
        "AND job.job_status IN ('queued', 'retry', 'processing')"
        ") AND NOT EXISTS ("
        "SELECT 1 FROM `ontology_reasoning_mailbox_events` mailbox "
        "WHERE mailbox.source_snapshot_id = source.snapshot_id "
        "AND mailbox.state IN ('pending', 'direct-pending')"
        ") ORDER BY source.created_at, source.snapshot_id LIMIT %s) stale_sources)"
    )
    return _delete_one_batch(connection, sql, (cutoff_iso, batch_size))


def _delete_stale_shared_inference_rows(connection, cutoff_iso: str, batch_size: int) -> Dict[str, int]:
    """Retain active shared heads while bounding immutable dual-run history."""

    overlays = _delete_one_batch(
        connection,
        "DELETE FROM `portfolio_inference_overlays` WHERE `created_at` < %s "
        "ORDER BY `created_at`, `overlay_id` LIMIT %s",
        (cutoff_iso, batch_size),
    )
    snapshots = _delete_one_batch(
        connection,
        "DELETE FROM `shared_instrument_inference_snapshots` WHERE snapshot_id IN ("
        "SELECT snapshot_id FROM (SELECT s.snapshot_id "
        "FROM `shared_instrument_inference_snapshots` s "
        "LEFT JOIN `shared_instrument_inference_heads` h ON h.snapshot_id = s.snapshot_id "
        "WHERE s.created_at < %s AND h.snapshot_id IS NULL "
        "ORDER BY s.created_at, s.snapshot_id LIMIT %s) expired_shared_inference)",
        (cutoff_iso, batch_size),
    )
    return {"snapshots": snapshots, "overlays": overlays}


def _delete_suppressed_notification_rows(connection, cutoff_iso: str, batch_size: int) -> int:
    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    sql = (
        "DELETE FROM `notification_jobs`"
        " WHERE `status` IN ('suppressed', 'superseded')"
        " AND `created_at` < "
        + cutoff_sql
        + " ORDER BY `created_at`, `job_id` LIMIT %s"
    )
    return _delete_one_batch(connection, sql, (cutoff_iso, batch_size))


def _delete_terminal_notification_rows(connection, cutoff_iso: str, batch_size: int) -> int:
    """Delete only delivered legacy/current notifications, never a retryable job."""

    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    sql = (
        "DELETE FROM `notification_jobs`"
        " WHERE `status` IN ('done', 'sent')"
        " AND `created_at` < "
        + cutoff_sql
        + " ORDER BY `created_at`, `job_id` LIMIT %s"
    )
    return _delete_one_batch(connection, sql, (cutoff_iso, batch_size))


def _delete_completed_model_review_rows(connection, cutoff_iso: str, batch_size: int) -> int:
    """Keep failed reviews retryable; only completed review output is history."""

    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    sql = (
        "DELETE FROM `model_review_jobs`"
        " WHERE `status` = 'done'"
        " AND `updated_at` < "
        + cutoff_sql
        + " ORDER BY `updated_at`, `job_id` LIMIT %s"
    )
    return _delete_one_batch(connection, sql, (cutoff_iso, batch_size))


def _delete_completed_news_analysis_work_rows(connection, cutoff_iso: str, batch_size: int) -> int:
    """Retain retryable and leased analysis work; prune only completed leases."""
    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    sql = (
        "DELETE FROM `news_analysis_work_items`"
        " WHERE `work_state` = 'completed'"
        " AND `completed_at` != '' AND `completed_at` < "
        + cutoff_sql
        + " ORDER BY `completed_at`, `evidence_id` LIMIT %s"
    )
    return _delete_one_batch(connection, sql, (cutoff_iso, batch_size))


def _delete_terminal_ai_inference_rows(connection, cutoff_iso: str, batch_size: int) -> Dict[str, int]:
    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    results = _delete_one_batch(
        connection,
        "DELETE FROM `ai_inference_results` WHERE `created_at` < "
        + cutoff_sql
        + " ORDER BY `created_at`, `result_id` LIMIT %s",
        (cutoff_iso, batch_size),
    )
    requests = _delete_one_batch(
        connection,
        "DELETE FROM `ai_inference_requests` WHERE `status` IN ('completed', 'failed', 'superseded')"
        " AND `completed_at` != '' AND `completed_at` < "
        + cutoff_sql
        + " ORDER BY `completed_at`, `request_id` LIMIT %s",
        (cutoff_iso, batch_size),
    )
    return {"requests": requests, "results": results}


def _delete_delivered_notification_rows_over_keep_count(
    connection,
    keep_count: int,
    batch_size: int,
) -> int:
    """Retain a bounded, account-local notification history after delivery.

    ``notification_jobs`` keeps the user-visible alert text and a full payload,
    while the canonical decision and evidence records live in their own tables.
    Delivery history therefore must not grow with every realtime cycle.
    """
    account_rows = _execute(
        connection,
        "SELECT DISTINCT account_id FROM `notification_jobs` WHERE `status` = 'done' ORDER BY account_id",
    ).fetchall()
    job_ids = []
    for row in account_rows or []:
        if len(job_ids) >= batch_size:
            break
        account_id = str((row.get("account_id") if isinstance(row, dict) else row[0]) or "")
        rows = _execute(
            connection,
            "SELECT job_id FROM `notification_jobs` WHERE account_id = %s AND `status` = 'done' "
            "ORDER BY updated_at DESC, job_id DESC LIMIT %s OFFSET %s",
            (account_id, batch_size - len(job_ids), keep_count),
        ).fetchall()
        job_ids.extend(_row_identifiers(rows, "job_id"))
    job_ids = list(dict.fromkeys(job_ids))[:batch_size]
    if not job_ids:
        return 0
    return _delete_one_batch(
        connection,
        "DELETE FROM `notification_jobs` WHERE job_id IN ("
        + ", ".join(["%s"] * len(job_ids))
        + ") AND `status` = 'done'",
        tuple(job_ids),
    )


def _delete_expired_article_delivery_ledger_rows(connection, cutoff_iso: str, batch_size: int) -> int:
    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    sql = (
        "DELETE FROM `notification_article_delivery_ledger`"
        " WHERE `delivered_at` < "
        + cutoff_sql
        + " ORDER BY `delivered_at`, `account_id`, `identity_key` LIMIT %s"
    )
    return _delete_one_batch(connection, sql, (cutoff_iso, batch_size))


def _delete_market_time_series_rows(
    connection,
    granularity: str,
    cutoff_iso: str,
    batch_size: int,
) -> int:
    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    sql = (
        "DELETE FROM `market_time_series_observations`"
        " WHERE `granularity` = %s"
        " AND `bucket_at` < "
        + cutoff_sql
        + " ORDER BY `bucket_at`, `account_id`, `symbol` LIMIT %s"
    )
    return _delete_one_batch(connection, sql, (granularity, cutoff_iso, batch_size))


def _delete_snapshot_history_over_keep_count(connection, keep_count: int, batch_size: int) -> int:
    account_rows = _execute(
        connection,
        "SELECT DISTINCT account_id FROM `monitor_snapshot_history` ORDER BY account_id",
    ).fetchall()
    deleted = 0
    for row in account_rows or []:
        if deleted >= batch_size:
            break
        account_id = str((row.get("account_id") if isinstance(row, dict) else row[0]) or "")
        candidates = _execute(
            connection,
            "SELECT generated_at FROM `monitor_snapshot_history` WHERE account_id = %s "
            "ORDER BY generated_at DESC LIMIT %s OFFSET %s",
            (account_id, batch_size - deleted, keep_count),
        ).fetchall()
        generated_values = _row_identifiers(candidates, "generated_at")
        if not generated_values:
            continue
        deleted += _delete_one_batch(
            connection,
            "DELETE FROM `monitor_snapshot_history` WHERE account_id = %s AND generated_at IN ("
            + ", ".join(["%s"] * len(generated_values))
            + ")",
            (account_id, *generated_values),
        )
    return deleted


def _delete_large_domain_events_over_keep_count(
    connection,
    names: Sequence[str],
    keep_count: int,
    batch_size: int,
) -> int:
    event_names = [str(item or "").strip() for item in names or [] if str(item or "").strip()]
    if not event_names:
        return 0
    event_ids = []
    for event_name in event_names:
        if len(event_ids) >= batch_size:
            break
        rows = _execute(
            connection,
            "SELECT event_id FROM `domain_events` WHERE name = %s "
            "ORDER BY occurred_at DESC, event_id DESC LIMIT %s OFFSET %s",
            (event_name, batch_size - len(event_ids), keep_count),
        ).fetchall()
        event_ids.extend(_row_identifiers(rows, "event_id"))
    event_ids = list(dict.fromkeys(event_ids))[:batch_size]
    if not event_ids:
        return 0
    delete_sql = (
        "DELETE FROM `domain_events` WHERE `event_id` IN ("
        + ", ".join(["%s"] * len(event_ids))
        + ")"
    )
    return _delete_one_batch(connection, delete_sql, tuple(event_ids))


def _delete_stale_projection_runs(connection, cutoff_iso: str, batch_size: int) -> int:
    """Delete completed projection audits past the operational time window.

    A currently projecting row is an active recovery contract and must remain
    available until the projection-run store marks it stale or completes it.
    """
    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    sql = (
        "DELETE FROM `ontology_projection_runs`"
        " WHERE `status` <> 'projecting'"
        " AND `updated_at` < " + cutoff_sql
        + " ORDER BY `updated_at`, `run_id` LIMIT %s"
    )
    return _delete_one_batch(connection, sql, (cutoff_iso, batch_size))


def _delete_projection_runs_over_keep_count(connection, keep_count: int, batch_size: int) -> int:
    """Retain only the newest completed audit rows for each ontology world."""
    world_rows = _execute(
        connection,
        "SELECT DISTINCT world_id FROM `ontology_projection_runs` WHERE status <> 'projecting' ORDER BY world_id",
    ).fetchall()
    run_ids = []
    for row in world_rows or []:
        if len(run_ids) >= batch_size:
            break
        world_id = str((row.get("world_id") if isinstance(row, dict) else row[0]) or "")
        rows = _execute(
            connection,
            "SELECT run_id FROM `ontology_projection_runs` WHERE status <> 'projecting' AND world_id = %s "
            "ORDER BY updated_at DESC, run_id DESC LIMIT %s OFFSET %s",
            (world_id, batch_size - len(run_ids), keep_count),
        ).fetchall()
        run_ids.extend(_row_identifiers(rows, "run_id"))
    run_ids = list(dict.fromkeys(run_ids))[:batch_size]
    if not run_ids:
        return 0
    return _delete_one_batch(
        connection,
        "DELETE FROM `ontology_projection_runs` WHERE run_id IN ("
        + ", ".join(["%s"] * len(run_ids))
        + ") AND status <> 'projecting'",
        tuple(run_ids),
    )


def _delete_completed_world_projection_outbox_rows(connection, cutoff_iso: str, batch_size: int) -> int:
    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    candidate_sql = (
        "SELECT `job_id` FROM `ontology_world_projection_outbox`"
        " WHERE `status` IN ('completed', 'superseded')"
        " AND `completed_at` <> '' AND `completed_at` < " + cutoff_sql
        + " ORDER BY `completed_at`, `job_id` LIMIT %s"
    )
    rows = _execute(connection, candidate_sql, (cutoff_iso, batch_size)).fetchall()
    job_ids = _row_identifiers(rows, "job_id")
    if not job_ids:
        return 0
    sql = (
        "DELETE FROM `ontology_world_projection_outbox` WHERE `job_id` IN ("
        + ", ".join(["%s"] * len(job_ids))
        + ") AND `status` IN ('completed', 'superseded')"
        + " AND `completed_at` <> '' AND `completed_at` < " + cutoff_sql
    )
    return _delete_one_batch(connection, sql, tuple(job_ids) + (cutoff_iso,))


def _delete_completed_inference_detail_outbox_rows(connection, cutoff_iso: str, batch_size: int) -> int:
    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    candidate_sql = (
        "SELECT `job_id` FROM `ontology_inference_detail_outbox`"
        " WHERE `status` IN ('completed', 'superseded')"
        " AND `completed_at` <> '' AND `completed_at` < " + cutoff_sql
        + " ORDER BY `completed_at`, `job_id` LIMIT %s"
    )
    rows = _execute(connection, candidate_sql, (cutoff_iso, batch_size)).fetchall()
    job_ids = _row_identifiers(rows, "job_id")
    if not job_ids:
        return 0
    sql = (
        "DELETE FROM `ontology_inference_detail_outbox` WHERE `job_id` IN ("
        + ", ".join(["%s"] * len(job_ids))
        + ") AND `status` IN ('completed', 'superseded')"
        + " AND `completed_at` <> '' AND `completed_at` < " + cutoff_sql
    )
    return _delete_one_batch(connection, sql, tuple(job_ids) + (cutoff_iso,))


def mysql_operational_compaction_tables(retention_result: Mapping[str, object] = None) -> List[str]:
    """Return tables that actually changed and are safe to rebuild offline."""
    tables = dict((retention_result or {}).get("tables") or {})
    return sorted(
        table
        for table, deleted in tables.items()
        if table in MYSQL_OPERATIONAL_COMPACTION_TABLES and int(deleted or 0) > 0
    )


def mysql_operational_space_reclaim_candidates(
    connection,
    minimum_reclaim_mb: int = 256,
    maximum_tables: int = 3,
) -> List[Dict[str, object]]:
    """Plan explicit compaction from table metadata without reading payloads."""

    minimum_bytes = max(16, int(minimum_reclaim_mb or 256)) * 1024 * 1024
    limit = max(1, min(10, int(maximum_tables or 3)))
    metadata_source = "innodb-tablespaces"
    try:
        rows = _execute(
            connection,
            """
        SELECT tables_meta.table_name AS tableName,
               COALESCE(tables_meta.data_length, 0) AS dataBytes,
               COALESCE(tables_meta.index_length, 0) AS indexBytes,
               COALESCE(tables_meta.data_free, 0) AS statisticalFreeBytes,
               COALESCE(SUM(spaces.allocated_size), 0) AS allocatedBytes
        FROM information_schema.tables tables_meta
        LEFT JOIN information_schema.innodb_tablespaces spaces
          ON spaces.name = CONCAT(tables_meta.table_schema, '/', tables_meta.table_name)
          OR LEFT(
               spaces.name,
               CHAR_LENGTH(CONCAT(tables_meta.table_schema, '/', tables_meta.table_name, '#p#'))
             ) = CONCAT(tables_meta.table_schema, '/', tables_meta.table_name, '#p#')
        WHERE tables_meta.table_schema = DATABASE()
        GROUP BY tables_meta.table_name, tables_meta.data_length, tables_meta.index_length, tables_meta.data_free
        ORDER BY allocatedBytes DESC, tables_meta.table_name
        """,
        ).fetchall()
    except Exception:
        # ``information_schema.innodb_tablespaces`` requires PROCESS on some
        # MySQL builds. The application account deliberately does not have
        # that server-wide privilege, so use the per-table allocator estimate
        # exposed by ``information_schema.tables`` instead.
        metadata_source = "information-schema-tables"
        rows = _execute(
            connection,
            """
            SELECT table_name AS tableName,
                   COALESCE(data_length, 0) AS dataBytes,
                   COALESCE(index_length, 0) AS indexBytes,
                   COALESCE(data_free, 0) AS statisticalFreeBytes,
                   COALESCE(data_length, 0) + COALESCE(index_length, 0)
                     + COALESCE(data_free, 0) AS allocatedBytes
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            ORDER BY allocatedBytes DESC, table_name
            """,
        ).fetchall()
    candidates = []
    for row in rows or []:
        table = str((row.get("tableName") if isinstance(row, dict) else row[0]) or "").strip()
        data_bytes = int((row.get("dataBytes") if isinstance(row, dict) else row[1]) or 0)
        index_bytes = int((row.get("indexBytes") if isinstance(row, dict) else row[2]) or 0)
        statistical_free_bytes = int((row.get("statisticalFreeBytes") if isinstance(row, dict) else row[3]) or 0)
        allocated_bytes = int((row.get("allocatedBytes") if isinstance(row, dict) else row[4]) or 0)
        reclaimable_bytes = max(0, allocated_bytes - data_bytes - index_bytes)
        allocated_bytes = max(1, allocated_bytes)
        if table not in MYSQL_OPERATIONAL_COMPACTION_TABLES or reclaimable_bytes < minimum_bytes:
            continue
        if reclaimable_bytes / allocated_bytes < 0.20:
            continue
        candidates.append({
            "table": table,
            "dataBytes": data_bytes,
            "indexBytes": index_bytes,
            "allocatedBytes": allocated_bytes,
            "reclaimableBytes": reclaimable_bytes,
            "statisticalFreeBytes": statistical_free_bytes,
            "temporaryHeadroomBytes": data_bytes + index_bytes + 64 * 1024 * 1024,
            "metadataSource": metadata_source,
        })
        if len(candidates) >= limit:
            break
    return candidates


def safe_mysql_operational_compaction_tables(
    candidates: Sequence[Mapping[str, object]],
    free_bytes: int,
    reserve_bytes: int,
) -> Dict[str, object]:
    """Select sequential table rebuilds while retaining the shared reserve."""

    available_for_work = max(0, int(free_bytes or 0) - max(0, int(reserve_bytes or 0)))
    selected = []
    skipped = []
    for item in candidates or []:
        table = str((item or {}).get("table") or "")
        required = max(0, int((item or {}).get("temporaryHeadroomBytes") or 0))
        if required <= available_for_work:
            selected.append(table)
            # Be conservative: do not assume the previous rebuild's free pages
            # become visible before the next table starts.
            available_for_work -= required
        else:
            skipped.append({"table": table, "requiredBytes": required, "reason": "insufficient-headroom"})
    return {
        "selectedTables": selected,
        "skippedTables": skipped,
        "freeBytes": max(0, int(free_bytes or 0)),
        "reserveBytes": max(0, int(reserve_bytes or 0)),
    }


def optimize_mysql_operational_tables(connection, tables: Sequence[str]) -> Dict[str, object]:
    """Physically reclaim free pages after an explicit, paused maintenance run.

    The caller owns scheduling: `OPTIMIZE TABLE` may rebuild a large table and
    should never run on a regular realtime ingestion path.
    """
    requested = sorted({str(item or "").strip() for item in tables or [] if str(item or "").strip()})
    allowed = [table for table in requested if table in MYSQL_OPERATIONAL_COMPACTION_TABLES]
    rejected = [table for table in requested if table not in MYSQL_OPERATIONAL_COMPACTION_TABLES]
    optimized = []
    analyzed = []
    failures = []
    metadata_failures = []
    for table in allowed:
        try:
            _execute(connection, "OPTIMIZE TABLE " + quote_identifier(table))
            optimized.append(table)
        except Exception as error:  # noqa: BLE001 - retain the rest of the maintenance result.
            failures.append({"table": table, "reason": str(error)[:220]})
            continue
        try:
            # MySQL can keep pre-rebuild allocator statistics until an explicit
            # analyze. Refresh them so the capacity dashboard does not report
            # already reclaimed pages as another compaction candidate.
            _execute(connection, "ANALYZE TABLE " + quote_identifier(table))
            analyzed.append(table)
        except Exception as error:  # noqa: BLE001 - physical compaction still succeeded.
            metadata_failures.append({"table": table, "reason": str(error)[:220]})
    return {
        "status": "ok" if not failures and not metadata_failures else "partial",
        "requestedTables": requested,
        "optimizedTables": optimized,
        "analyzedTables": analyzed,
        "rejectedTables": rejected,
        "failures": failures,
        "metadataFailures": metadata_failures,
    }


def ephemeral_mysql_database_names(
    names: Sequence[str],
    protected_databases: Sequence[str] = None,
) -> List[str]:
    """Identify disposable smoke/test schemas without touching app databases."""
    protected = {str(item or "").strip() for item in protected_databases or [] if str(item or "").strip()}
    return sorted({
        name
        for name in (str(item or "").strip() for item in names or [])
        if name and name not in protected and EPHEMERAL_MYSQL_DATABASE_PATTERN.fullmatch(name)
    })


def drop_ephemeral_mysql_databases(
    connection,
    protected_databases: Sequence[str] = None,
) -> Dict[str, object]:
    """Drop only known test/smoke schemas during an explicit maintenance run."""
    rows = _execute(
        connection,
        "SELECT schema_name AS schemaName FROM information_schema.schemata",
    ).fetchall()
    candidates = ephemeral_mysql_database_names(
        [row.get("schemaName") if isinstance(row, dict) else row[0] for row in rows or []],
        protected_databases,
    )
    dropped = []
    failures = []
    for database in candidates:
        try:
            _execute(connection, "DROP DATABASE " + quote_identifier(database))
            dropped.append(database)
        except Exception as error:  # noqa: BLE001 - report a protected or locked schema clearly.
            failures.append({"database": database, "reason": str(error)[:220]})
    return {
        "status": "ok" if not failures else "partial",
        "candidateDatabases": candidates,
        "droppedDatabases": dropped,
        "failures": failures,
    }


def apply_mysql_operational_history_retention(
    connection,
    settings: Mapping[str, object] = None,
    now: Optional[datetime] = None,
    use_lock: bool = True,
) -> Dict[str, object]:
    configured = settings or {}
    if not operational_history_retention_enabled(configured):
        return {"enabled": False, "deleted": 0, "tables": {}, "skipped": "disabled"}

    cutoff_iso = operational_history_retention_cutoff(configured, now=now)
    suppressed_cutoff_iso = operational_suppressed_notification_cutoff(configured, now=now)
    time_series_cutoffs = market_time_series_retention_cutoffs(configured, now=now)
    lifecycle_event_cutoff_iso = hypothesis_lifecycle_event_retention_cutoff(configured, now=now)
    batch_size = operational_history_retention_batch_size(configured)
    # World-projection payloads are multi-megabyte ABox packets. Delete one at
    # a time so the low-priority transaction does not monopolize the redo log.
    outbox_batch_size = min(batch_size, 1)
    projection_run_keep_count = operational_projection_run_keep_count(configured)
    execution_trace_retention_days = ontology_execution_trace_retention_days(configured)
    world_projection_outbox_retention_hours = operational_world_projection_outbox_retention_hours(configured)
    inference_detail_outbox_retention_hours = operational_inference_detail_outbox_retention_hours(configured)
    ai_inference_queue_retention_hours = operational_ai_inference_queue_retention_hours(configured)
    delivered_notification_keep_count = operational_delivered_notification_keep_count(configured)
    article_delivery_ledger_cutoff = sent_article_delivery_ledger_cutoff(configured, now=now)
    locked = False
    if use_lock:
        locked = _acquire_lock(connection)
        if not locked:
            return {"enabled": True, "deleted": 0, "tables": {}, "skipped": "locked", "cutoffIso": cutoff_iso}

    deleted_by_table: Dict[str, int] = {}
    deleted_by_policy: Dict[str, int] = {}
    try:
        for target in MYSQL_OPERATIONAL_HISTORY_RETENTION_TARGETS:
            deleted = _delete_stale_rows(connection, target, cutoff_iso, batch_size)
            deleted_by_table[target.table] = deleted
            deleted_by_policy["time:" + target.table] = deleted

        source_snapshot_deleted = _delete_stale_reasoning_source_snapshots(
            connection,
            cutoff_iso,
            batch_size,
        )
        deleted_by_table["verified_reasoning_source_snapshots"] = source_snapshot_deleted
        deleted_by_policy["terminal:verified_reasoning_source_snapshots"] = source_snapshot_deleted

        shared_inference_deleted = _delete_stale_shared_inference_rows(
            connection,
            cutoff_iso,
            batch_size,
        )
        deleted_by_table["shared_instrument_inference_snapshots"] = shared_inference_deleted["snapshots"]
        deleted_by_table["portfolio_inference_overlays"] = shared_inference_deleted["overlays"]
        deleted_by_policy["time:shared_instrument_inference_snapshots"] = shared_inference_deleted["snapshots"]
        deleted_by_policy["time:portfolio_inference_overlays"] = shared_inference_deleted["overlays"]

        terminal_notification_deleted = _delete_terminal_notification_rows(connection, cutoff_iso, batch_size)
        deleted_by_table["notification_jobs"] = terminal_notification_deleted
        deleted_by_policy["terminal:notification_jobs"] = terminal_notification_deleted

        completed_model_review_deleted = _delete_completed_model_review_rows(connection, cutoff_iso, batch_size)
        deleted_by_table["model_review_jobs"] = completed_model_review_deleted
        deleted_by_policy["completed:model_review_jobs"] = completed_model_review_deleted

        ai_inference_cutoff = (
            (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            - timedelta(hours=ai_inference_queue_retention_hours)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        ai_inference_deleted = _delete_terminal_ai_inference_rows(connection, ai_inference_cutoff, batch_size)
        deleted_by_table["ai_inference_requests"] = ai_inference_deleted["requests"]
        deleted_by_table["ai_inference_results"] = ai_inference_deleted["results"]
        deleted_by_policy["terminal:ai_inference_requests"] = ai_inference_deleted["requests"]
        deleted_by_policy["time:ai_inference_results"] = ai_inference_deleted["results"]
        news_analysis_deleted = _delete_completed_news_analysis_work_rows(
            connection,
            ai_inference_cutoff,
            batch_size,
        )
        deleted_by_table["news_analysis_work_items"] = news_analysis_deleted
        deleted_by_policy["terminal:news_analysis_work_items"] = news_analysis_deleted

        snapshot_deleted = _delete_snapshot_history_over_keep_count(
            connection,
            operational_snapshot_history_keep_count(configured),
            batch_size,
        )
        deleted_by_table["monitor_snapshot_history"] = deleted_by_table.get("monitor_snapshot_history", 0) + snapshot_deleted
        deleted_by_policy["count:monitor_snapshot_history"] = snapshot_deleted

        suppressed_deleted = _delete_suppressed_notification_rows(connection, suppressed_cutoff_iso, batch_size)
        deleted_by_table["notification_jobs"] = deleted_by_table.get("notification_jobs", 0) + suppressed_deleted
        deleted_by_policy["suppressed:notification_jobs"] = suppressed_deleted

        delivered_notification_deleted = _delete_delivered_notification_rows_over_keep_count(
            connection,
            delivered_notification_keep_count,
            batch_size,
        )
        deleted_by_table["notification_jobs"] = deleted_by_table.get("notification_jobs", 0) + delivered_notification_deleted
        deleted_by_policy["count:delivered_notification_jobs"] = delivered_notification_deleted

        article_delivery_ledger_deleted = _delete_expired_article_delivery_ledger_rows(
            connection,
            article_delivery_ledger_cutoff,
            batch_size,
        )
        deleted_by_table["notification_article_delivery_ledger"] = article_delivery_ledger_deleted
        deleted_by_policy["time:notification_article_delivery_ledger"] = article_delivery_ledger_deleted

        domain_event_deleted = _delete_large_domain_events_over_keep_count(
            connection,
            operational_large_domain_event_names(configured),
            operational_large_domain_event_keep_count(configured),
            batch_size,
        )
        deleted_by_table["domain_events"] = deleted_by_table.get("domain_events", 0) + domain_event_deleted
        deleted_by_policy["count:domain_events"] = domain_event_deleted

        projection_time_deleted = _delete_stale_projection_runs(connection, cutoff_iso, batch_size)
        projection_count_deleted = _delete_projection_runs_over_keep_count(
            connection,
            projection_run_keep_count,
            batch_size,
        )
        deleted_by_table["ontology_projection_runs"] = projection_time_deleted + projection_count_deleted
        deleted_by_policy["time:ontology_projection_runs"] = projection_time_deleted
        deleted_by_policy["count:ontology_projection_runs"] = projection_count_deleted

        execution_trace_cutoff = (
            (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            - timedelta(days=execution_trace_retention_days)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        stage_trace_deleted = _delete_stale_rows(
            connection,
            MySQLRetentionTarget("ontology_reasoning_run_stages", "updated_at"),
            execution_trace_cutoff,
            batch_size,
        )
        rule_trace_deleted = _delete_stale_rows(
            connection,
            MySQLRetentionTarget("ontology_reasoning_rule_runs", "updated_at"),
            execution_trace_cutoff,
            batch_size,
        )
        deleted_by_table["ontology_reasoning_run_stages"] = stage_trace_deleted
        deleted_by_table["ontology_reasoning_rule_runs"] = rule_trace_deleted
        deleted_by_policy["time:ontology_reasoning_run_stages"] = stage_trace_deleted
        deleted_by_policy["time:ontology_reasoning_rule_runs"] = rule_trace_deleted

        world_projection_cutoff = (
            (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            - timedelta(hours=world_projection_outbox_retention_hours)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        world_projection_deleted = _delete_completed_world_projection_outbox_rows(
            connection,
            world_projection_cutoff,
            outbox_batch_size,
        )
        deleted_by_table["ontology_world_projection_outbox"] = world_projection_deleted
        deleted_by_policy["time:ontology_world_projection_outbox"] = world_projection_deleted

        inference_detail_cutoff = (
            (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            - timedelta(hours=inference_detail_outbox_retention_hours)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        inference_detail_deleted = _delete_completed_inference_detail_outbox_rows(
            connection,
            inference_detail_cutoff,
            outbox_batch_size,
        )
        deleted_by_table["ontology_inference_detail_outbox"] = inference_detail_deleted
        deleted_by_policy["time:ontology_inference_detail_outbox"] = inference_detail_deleted

        time_series_deleted = 0
        for granularity, series_cutoff in time_series_cutoffs.items():
            deleted = _delete_market_time_series_rows(
                connection,
                granularity,
                series_cutoff,
                batch_size,
            )
            time_series_deleted += deleted
            deleted_by_policy["tier:market_time_series_observations:" + granularity] = deleted
        deleted_by_table["market_time_series_observations"] = time_series_deleted

        lifecycle_event_deleted = _delete_stale_rows(
            connection,
            MySQLRetentionTarget("investment_hypothesis_lifecycle_events", "occurred_at"),
            lifecycle_event_cutoff_iso,
            batch_size,
        )
        deleted_by_table["investment_hypothesis_lifecycle_events"] = lifecycle_event_deleted
        deleted_by_policy["time:investment_hypothesis_lifecycle_events"] = lifecycle_event_deleted
    finally:
        if locked:
            try:
                _release_lock(connection)
            except Exception:
                pass

    return {
        "enabled": True,
        "retentionHours": operational_history_retention_hours(configured),
        "batchSize": batch_size,
        "outboxBatchSize": outbox_batch_size,
        "mode": "bounded-single-batch-per-policy",
        "cutoffIso": cutoff_iso,
        "snapshotHistoryKeepCount": operational_snapshot_history_keep_count(configured),
        "suppressedNotificationCutoffIso": suppressed_cutoff_iso,
        "suppressedNotificationRetentionMinutes": operational_suppressed_notification_retention_minutes(configured),
        "deliveredNotificationKeepCount": delivered_notification_keep_count,
        "sentArticleDeliveryLedgerRetentionDays": sent_article_delivery_ledger_retention_days(configured),
        "sentArticleDeliveryLedgerCutoffIso": article_delivery_ledger_cutoff,
        "largeDomainEventKeepCount": operational_large_domain_event_keep_count(configured),
        "largeDomainEventNames": operational_large_domain_event_names(configured),
        "projectionRunKeepCount": projection_run_keep_count,
        "ontologyExecutionTraceRetentionDays": execution_trace_retention_days,
        "worldProjectionOutboxRetentionHours": world_projection_outbox_retention_hours,
        "inferenceDetailOutboxRetentionHours": inference_detail_outbox_retention_hours,
        "marketTimeSeriesRetentionDays": market_time_series_retention_days(configured),
        "marketTimeSeriesCutoffs": time_series_cutoffs,
        "hypothesisLifecycleEventRetentionDays": hypothesis_lifecycle_event_retention_days(configured),
        "hypothesisLifecycleEventCutoffIso": lifecycle_event_cutoff_iso,
        "deleted": sum(deleted_by_table.values()),
        "tables": deleted_by_table,
        "policies": deleted_by_policy,
    }
