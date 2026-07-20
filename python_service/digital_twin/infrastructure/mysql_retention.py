from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Mapping, Optional, Sequence

from .mysql_schema_tuning import quote_identifier


FALSE_VALUES = {"0", "false", "no", "off", "disabled", "disable", "none"}
TRUE_VALUES = {"1", "true", "yes", "on", "enabled", "enable"}
DEFAULT_RETENTION_HOURS = 24
DEFAULT_BATCH_SIZE = 1000
DEFAULT_CHECK_INTERVAL_SECONDS = 300
DEFAULT_SNAPSHOT_HISTORY_KEEP_COUNT = 6
DEFAULT_SUPPRESSED_NOTIFICATION_RETENTION_MINUTES = 120
DEFAULT_LARGE_DOMAIN_EVENT_KEEP_COUNT = 100
DEFAULT_LARGE_DOMAIN_EVENT_NAMES = ("monitoring.alerts_detected",)
DEFAULT_MARKET_TIME_SERIES_RETENTION_DAYS = {
    "3m": 7,
    "15m": 120,
    "1h": 730,
    "1d": 3650,
}
RETENTION_LOCK_NAME = "orbit_alpha_operational_history_retention"


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
    return _int_setting(settings or {}, "operationalHistoryRetentionBatchSize", DEFAULT_BATCH_SIZE, 1, 10000)


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


def operational_large_domain_event_keep_count(settings: Mapping[str, object] = None) -> int:
    return _int_setting(
        settings or {},
        "operationalLargeDomainEventKeepCount",
        DEFAULT_LARGE_DOMAIN_EVENT_KEEP_COUNT,
        1,
        10000,
    )


def operational_large_domain_event_names(settings: Mapping[str, object] = None) -> List[str]:
    return _csv_setting(
        settings or {},
        "operationalLargeDomainEventNames",
        DEFAULT_LARGE_DOMAIN_EVENT_NAMES,
    )


def market_time_series_retention_days(settings: Mapping[str, object] = None) -> Dict[str, int]:
    configured = settings or {}
    return {
        "3m": _int_setting(configured, "marketTimeSeriesRawRetentionDays", 7, 1, 3650),
        "15m": _int_setting(configured, "marketTimeSeries15mRetentionDays", 120, 1, 36500),
        "1h": _int_setting(configured, "marketTimeSeries1hRetentionDays", 730, 1, 36500),
        "1d": _int_setting(configured, "marketTimeSeriesDailyRetentionDays", 3650, 1, 36500),
    }


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


def _delete_stale_rows(connection, target: MySQLRetentionTarget, cutoff_iso: str, batch_size: int) -> int:
    table = quote_identifier(target.table)
    time_column = quote_identifier(target.time_column)
    total = 0
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
    while True:
        cursor = _execute(connection, sql, (cutoff_iso, batch_size))
        affected = int(getattr(cursor, "rowcount", 0) or 0)
        total += affected
        if affected < batch_size:
            break
    return total


def _delete_suppressed_notification_rows(connection, cutoff_iso: str, batch_size: int) -> int:
    total = 0
    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    sql = (
        "DELETE FROM `notification_jobs`"
        " WHERE `status` = 'suppressed'"
        " AND `created_at` < "
        + cutoff_sql
        + " ORDER BY `created_at`, `job_id` LIMIT %s"
    )
    while True:
        cursor = _execute(connection, sql, (cutoff_iso, batch_size))
        affected = int(getattr(cursor, "rowcount", 0) or 0)
        total += affected
        if affected < batch_size:
            break
    return total


def _delete_market_time_series_rows(
    connection,
    granularity: str,
    cutoff_iso: str,
    batch_size: int,
) -> int:
    total = 0
    cutoff_sql = "CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci"
    sql = (
        "DELETE FROM `market_time_series_observations`"
        " WHERE `granularity` = %s"
        " AND `bucket_at` < "
        + cutoff_sql
        + " ORDER BY `bucket_at`, `account_id`, `symbol` LIMIT %s"
    )
    while True:
        cursor = _execute(connection, sql, (granularity, cutoff_iso, batch_size))
        affected = int(getattr(cursor, "rowcount", 0) or 0)
        total += affected
        if affected < batch_size:
            break
    return total


def _delete_snapshot_history_over_keep_count(connection, keep_count: int, batch_size: int) -> int:
    total = 0
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
    while True:
        cursor = _execute(connection, sql, (keep_count, batch_size))
        affected = int(getattr(cursor, "rowcount", 0) or 0)
        total += affected
        if affected < batch_size:
            break
    return total


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
    total = 0
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
    while True:
        cursor = _execute(connection, sql, params)
        affected = int(getattr(cursor, "rowcount", 0) or 0)
        total += affected
        if affected < batch_size:
            break
    return total


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
    batch_size = operational_history_retention_batch_size(configured)
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

        domain_event_deleted = _delete_large_domain_events_over_keep_count(
            connection,
            operational_large_domain_event_names(configured),
            operational_large_domain_event_keep_count(configured),
            batch_size,
        )
        deleted_by_table["domain_events"] = deleted_by_table.get("domain_events", 0) + domain_event_deleted
        deleted_by_policy["count:domain_events"] = domain_event_deleted

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
    finally:
        if locked:
            try:
                _release_lock(connection)
            except Exception:
                pass

    return {
        "enabled": True,
        "retentionHours": operational_history_retention_hours(configured),
        "cutoffIso": cutoff_iso,
        "snapshotHistoryKeepCount": operational_snapshot_history_keep_count(configured),
        "suppressedNotificationCutoffIso": suppressed_cutoff_iso,
        "suppressedNotificationRetentionMinutes": operational_suppressed_notification_retention_minutes(configured),
        "largeDomainEventKeepCount": operational_large_domain_event_keep_count(configured),
        "largeDomainEventNames": operational_large_domain_event_names(configured),
        "marketTimeSeriesRetentionDays": market_time_series_retention_days(configured),
        "marketTimeSeriesCutoffs": time_series_cutoffs,
        "deleted": sum(deleted_by_table.values()),
        "tables": deleted_by_table,
        "policies": deleted_by_policy,
    }
