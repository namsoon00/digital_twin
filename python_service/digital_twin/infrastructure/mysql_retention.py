from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Dict, List, Mapping, Optional, Sequence

from .mysql_schema_tuning import quote_identifier


FALSE_VALUES = {"0", "false", "no", "off", "disabled", "disable", "none"}
TRUE_VALUES = {"1", "true", "yes", "on", "enabled", "enable"}
DEFAULT_RETENTION_HOURS = 24
# Retention is deliberately low-priority. A large delete can hold page locks,
# saturate the redo log, and turn routine history cleanup into an outage.
DEFAULT_BATCH_SIZE = 50
DEFAULT_CHECK_INTERVAL_SECONDS = 300
DEFAULT_SNAPSHOT_HISTORY_KEEP_COUNT = 6
DEFAULT_SUPPRESSED_NOTIFICATION_RETENTION_MINUTES = 120
DEFAULT_LARGE_DOMAIN_EVENT_KEEP_COUNT = 20
DEFAULT_DELIVERED_NOTIFICATION_KEEP_COUNT = 30
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
DEFAULT_PROJECTION_RUN_KEEP_COUNT = 48
DEFAULT_WORLD_PROJECTION_OUTBOX_RETENTION_HOURS = 24
DEFAULT_INFERENCE_DETAIL_OUTBOX_RETENTION_HOURS = 168
DEFAULT_HYPOTHESIS_LIFECYCLE_EVENT_RETENTION_DAYS = 180
DEFAULT_MARKET_TIME_SERIES_RETENTION_DAYS = {
    "3m": 7,
    "15m": 120,
    "1h": 730,
    "1d": 3650,
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
    "notification_jobs",
    "model_review_jobs",
    "monitor_sent",
    "market_quote_cache",
    "ontology_ai_opinion_samples",
    "ontology_projection_runs",
    "ontology_world_projection_outbox",
    "ontology_inference_detail_outbox",
    "investment_decision_episodes",
    "investment_decision_outcomes",
    "investment_hypothesis_lifecycle_states",
    "investment_hypothesis_lifecycle_events",
    "investment_research_runs",
    "research_evidence",
    "market_time_series_observations",
    "ontology_reasoning_mailbox_events",
    "symbol_universe",
    "symbol_universe_sources",
})


@dataclass(frozen=True)
class MySQLRetentionTarget:
    table: str
    time_column: str


MYSQL_OPERATIONAL_HISTORY_RETENTION_TARGETS = (
    MySQLRetentionTarget("domain_events", "occurred_at"),
    MySQLRetentionTarget("monitor_snapshot_history", "generated_at"),
    MySQLRetentionTarget("notification_jobs", "created_at"),
    MySQLRetentionTarget("model_review_jobs", "created_at"),
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
    return _int_setting(settings or {}, "operationalHistoryRetentionBatchSize", DEFAULT_BATCH_SIZE, 1, 50)


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
        "15m": _int_setting(configured, "marketTimeSeries15mRetentionDays", 120, 1, 36500),
        "1h": _int_setting(configured, "marketTimeSeries1hRetentionDays", 730, 1, 36500),
        "1d": _int_setting(configured, "marketTimeSeriesDailyRetentionDays", 3650, 1, 36500),
    }


def hypothesis_lifecycle_event_retention_days(settings: Mapping[str, object] = None) -> int:
    return _int_setting(
        settings or {},
        "hypothesisLifecycleEventRetentionDays",
        DEFAULT_HYPOTHESIS_LIFECYCLE_EVENT_RETENTION_DAYS,
        7,
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


def _delete_suppressed_notification_rows(connection, cutoff_iso: str, batch_size: int) -> int:
    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    sql = (
        "DELETE FROM `notification_jobs`"
        " WHERE `status` = 'suppressed'"
        " AND `created_at` < "
        + cutoff_sql
        + " ORDER BY `created_at`, `job_id` LIMIT %s"
    )
    return _delete_one_batch(connection, sql, (cutoff_iso, batch_size))


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
    sql = """
        DELETE jobs
        FROM `notification_jobs` jobs
        JOIN (
            SELECT job_id
            FROM (
                SELECT job_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY account_id
                           ORDER BY updated_at DESC, job_id DESC
                       ) AS row_number_value
                FROM `notification_jobs`
                WHERE `status` = 'done'
            ) ranked
            WHERE ranked.row_number_value > %s
            LIMIT %s
        ) stale
          ON stale.job_id = jobs.job_id
    """
    return _delete_one_batch(connection, sql, (keep_count, batch_size))


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
    sql = """
        DELETE history
        FROM `monitor_snapshot_history` history
        JOIN (
            SELECT account_id, generated_at
            FROM (
                SELECT account_id,
                       generated_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY account_id
                           ORDER BY generated_at DESC
                       ) AS row_number_value
                FROM `monitor_snapshot_history`
            ) ranked
            WHERE ranked.row_number_value > %s
            LIMIT %s
        ) stale
          ON stale.account_id = history.account_id
         AND stale.generated_at = history.generated_at
    """
    return _delete_one_batch(connection, sql, (keep_count, batch_size))


def _delete_large_domain_events_over_keep_count(
    connection,
    names: Sequence[str],
    keep_count: int,
    batch_size: int,
) -> int:
    event_names = [str(item or "").strip() for item in names or [] if str(item or "").strip()]
    if not event_names:
        return 0
    placeholders = ", ".join(["%s"] * len(event_names))
    sql = (
        """
        DELETE events
        FROM `domain_events` events
        JOIN (
            SELECT event_id
            FROM (
                SELECT event_id,
                       name,
                       ROW_NUMBER() OVER (
                           PARTITION BY name
                           ORDER BY occurred_at DESC, event_id DESC
                       ) AS row_number_value
                FROM `domain_events`
                WHERE name IN (
        """
        + placeholders
        + """
                )
            ) ranked
            WHERE ranked.row_number_value > %s
            LIMIT %s
        ) stale
          ON stale.event_id = events.event_id
        """
    )
    params = tuple(event_names) + (keep_count, batch_size)
    return _delete_one_batch(connection, sql, params)


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
    sql = """
        DELETE runs
        FROM `ontology_projection_runs` runs
        JOIN (
            SELECT run_id
            FROM (
                SELECT run_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY COALESCE(NULLIF(world_id, ''), '__legacy__')
                           ORDER BY updated_at DESC, run_id DESC
                       ) AS row_number_value
                FROM `ontology_projection_runs`
                WHERE status <> 'projecting'
            ) ranked
            WHERE row_number_value > %s
            LIMIT %s
        ) stale
          ON stale.run_id = runs.run_id
    """
    return _delete_one_batch(connection, sql, (keep_count, batch_size))


def _delete_completed_world_projection_outbox_rows(connection, cutoff_iso: str, batch_size: int) -> int:
    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    sql = (
        "DELETE FROM `ontology_world_projection_outbox`"
        " WHERE `status` IN ('completed', 'superseded')"
        " AND `completed_at` <> '' AND `completed_at` < " + cutoff_sql
        + " ORDER BY `completed_at`, `job_id` LIMIT %s"
    )
    return _delete_one_batch(connection, sql, (cutoff_iso, batch_size))


def _delete_completed_inference_detail_outbox_rows(connection, cutoff_iso: str, batch_size: int) -> int:
    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    sql = (
        "DELETE FROM `ontology_inference_detail_outbox`"
        " WHERE `status` IN ('completed', 'superseded')"
        " AND `completed_at` <> '' AND `completed_at` < " + cutoff_sql
        + " ORDER BY `completed_at`, `job_id` LIMIT %s"
    )
    return _delete_one_batch(connection, sql, (cutoff_iso, batch_size))


def mysql_operational_compaction_tables(retention_result: Mapping[str, object] = None) -> List[str]:
    """Return tables that actually changed and are safe to rebuild offline."""
    tables = dict((retention_result or {}).get("tables") or {})
    return sorted(
        table
        for table, deleted in tables.items()
        if table in MYSQL_OPERATIONAL_COMPACTION_TABLES and int(deleted or 0) > 0
    )


def optimize_mysql_operational_tables(connection, tables: Sequence[str]) -> Dict[str, object]:
    """Physically reclaim free pages after an explicit, paused maintenance run.

    The caller owns scheduling: `OPTIMIZE TABLE` may rebuild a large table and
    should never run on a regular realtime ingestion path.
    """
    requested = sorted({str(item or "").strip() for item in tables or [] if str(item or "").strip()})
    allowed = [table for table in requested if table in MYSQL_OPERATIONAL_COMPACTION_TABLES]
    rejected = [table for table in requested if table not in MYSQL_OPERATIONAL_COMPACTION_TABLES]
    optimized = []
    failures = []
    for table in allowed:
        try:
            _execute(connection, "OPTIMIZE TABLE " + quote_identifier(table))
            optimized.append(table)
        except Exception as error:  # noqa: BLE001 - retain the rest of the maintenance result.
            failures.append({"table": table, "reason": str(error)[:220]})
    return {
        "status": "ok" if not failures else "partial",
        "requestedTables": requested,
        "optimizedTables": optimized,
        "rejectedTables": rejected,
        "failures": failures,
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
    # World-projection payloads are multi-megabyte ABox packets. Two rows are
    # enough to make steady progress while keeping one delete transaction
    # below the normal read timeout and redo-log stall threshold.
    outbox_batch_size = min(batch_size, 2)
    projection_run_keep_count = operational_projection_run_keep_count(configured)
    world_projection_outbox_retention_hours = operational_world_projection_outbox_retention_hours(configured)
    inference_detail_outbox_retention_hours = operational_inference_detail_outbox_retention_hours(configured)
    delivered_notification_keep_count = operational_delivered_notification_keep_count(configured)
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
        "largeDomainEventKeepCount": operational_large_domain_event_keep_count(configured),
        "largeDomainEventNames": operational_large_domain_event_names(configured),
        "projectionRunKeepCount": projection_run_keep_count,
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
