from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Mapping, Optional

from .mysql_schema_tuning import quote_identifier


FALSE_VALUES = {"0", "false", "no", "off", "disabled", "disable", "none"}
TRUE_VALUES = {"1", "true", "yes", "on", "enabled", "enable"}
DEFAULT_RETENTION_HOURS = 24
DEFAULT_BATCH_SIZE = 1000
DEFAULT_CHECK_INTERVAL_SECONDS = 300
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
    batch_size = operational_history_retention_batch_size(configured)
    locked = False
    if use_lock:
        locked = _acquire_lock(connection)
        if not locked:
            return {"enabled": True, "deleted": 0, "tables": {}, "skipped": "locked", "cutoffIso": cutoff_iso}

    deleted_by_table: Dict[str, int] = {}
    try:
        for target in MYSQL_OPERATIONAL_HISTORY_RETENTION_TARGETS:
            deleted_by_table[target.table] = _delete_stale_rows(connection, target, cutoff_iso, batch_size)
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
        "deleted": sum(deleted_by_table.values()),
        "tables": deleted_by_table,
    }
