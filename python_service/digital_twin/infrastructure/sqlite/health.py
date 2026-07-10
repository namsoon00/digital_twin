import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List

from ..settings import data_dir, service_db_path, utc_now
from .connection import connect_sqlite, sqlite_transaction, with_sqlite_retry


LOG_FILES = [
    "python-monitor.log",
    "python-market-data.log",
    "python-news.log",
    "python-model-review.log",
    "python-ontology-reasoning.log",
    "python-notifications.log",
]


DEFAULT_RETENTION_DAYS = int(os.environ.get("SQLITE_RETENTION_DAYS") or "7")
DEFAULT_CLEANUP_BATCH_SIZE = int(os.environ.get("SQLITE_CLEANUP_BATCH_SIZE") or "500")
DEFAULT_MAX_DELETE_PER_TABLE = int(os.environ.get("SQLITE_CLEANUP_MAX_DELETE_PER_TABLE") or "5000")
DEFAULT_APP_STORE_MESSAGE_LIMIT = int(os.environ.get("SQLITE_APP_STORE_MESSAGE_LIMIT") or "120")
DEFAULT_APP_STORE_ITEM_LIMIT = int(os.environ.get("SQLITE_APP_STORE_ITEM_LIMIT") or "500")
DEFAULT_APP_STORE_MEMORY_LIMIT = int(os.environ.get("SQLITE_APP_STORE_MEMORY_LIMIT") or "500")

CLEANUP_POLICIES = [
    {
        "key": "notificationJobs",
        "table": "notification_jobs",
        "timeColumn": "created_at",
        "protectWhere": "status IN ('pending', 'processing')",
        "maxRows": 2000,
    },
    {
        "key": "modelReviewJobs",
        "table": "model_review_jobs",
        "timeColumn": "created_at",
        "protectWhere": "status IN ('pending', 'processing')",
        "maxRows": 500,
    },
    {
        "key": "domainEvents",
        "table": "domain_events",
        "timeColumn": "occurred_at",
        "protectWhere": "",
        "maxRows": 5000,
    },
    {
        "key": "researchEvidence",
        "table": "research_evidence",
        "timeColumn": "last_seen_at",
        "protectWhere": "",
        "maxRows": 1000,
    },
    {
        "key": "marketQuoteCache",
        "table": "market_quote_cache",
        "timeColumn": "updated_at",
        "protectWhere": "",
        "maxRows": 2000,
    },
    {
        "key": "monitorSent",
        "table": "monitor_sent",
        "timeColumn": "sent_at",
        "protectWhere": "",
        "maxRows": 1000,
    },
    {
        "key": "ontologyQualitySamples",
        "table": "ontology_ai_opinion_samples",
        "timeColumn": "created_at",
        "protectWhere": "",
        "maxRows": 500,
    },
]

CLEANUP_INDEXES = [
    {
        "name": "idx_monitor_sent_sent_at",
        "table": "monitor_sent",
        "sql": "CREATE INDEX IF NOT EXISTS idx_monitor_sent_sent_at ON monitor_sent(sent_at)",
    },
    {
        "name": "idx_domain_events_occurred_at",
        "table": "domain_events",
        "sql": "CREATE INDEX IF NOT EXISTS idx_domain_events_occurred_at ON domain_events(occurred_at, event_id)",
    },
    {
        "name": "idx_domain_events_name_time",
        "table": "domain_events",
        "sql": "CREATE INDEX IF NOT EXISTS idx_domain_events_name_time ON domain_events(name, occurred_at, event_id)",
    },
    {
        "name": "idx_model_review_jobs_created",
        "table": "model_review_jobs",
        "sql": "CREATE INDEX IF NOT EXISTS idx_model_review_jobs_created ON model_review_jobs(created_at, job_id)",
    },
    {
        "name": "idx_model_review_jobs_pending_order",
        "table": "model_review_jobs",
        "sql": "CREATE INDEX IF NOT EXISTS idx_model_review_jobs_pending_order ON model_review_jobs(created_at, job_id) WHERE status IN ('pending', 'failed')",
    },
    {
        "name": "idx_ontology_quality_created_at",
        "table": "ontology_ai_opinion_samples",
        "sql": "CREATE INDEX IF NOT EXISTS idx_ontology_quality_created_at ON ontology_ai_opinion_samples(created_at, sample_id)",
    },
    {
        "name": "idx_notification_jobs_created",
        "table": "notification_jobs",
        "sql": "CREATE INDEX IF NOT EXISTS idx_notification_jobs_created ON notification_jobs(created_at, job_id)",
    },
    {
        "name": "idx_notification_jobs_status_created",
        "table": "notification_jobs",
        "sql": "CREATE INDEX IF NOT EXISTS idx_notification_jobs_status_created ON notification_jobs(status, created_at, job_id)",
    },
    {
        "name": "idx_notification_jobs_pending_order",
        "table": "notification_jobs",
        "sql": "CREATE INDEX IF NOT EXISTS idx_notification_jobs_pending_order ON notification_jobs(created_at, job_id) WHERE status = 'pending'",
    },
    {
        "name": "idx_notification_jobs_failed_attempts",
        "table": "notification_jobs",
        "sql": "CREATE INDEX IF NOT EXISTS idx_notification_jobs_failed_attempts ON notification_jobs(attempts, created_at, job_id) WHERE status = 'failed'",
    },
    {
        "name": "idx_notification_jobs_processing_started",
        "table": "notification_jobs",
        "sql": "CREATE INDEX IF NOT EXISTS idx_notification_jobs_processing_started ON notification_jobs(processing_started_at, updated_at, created_at, job_id) WHERE status = 'processing'",
    },
    {
        "name": "idx_notification_jobs_message_time_status",
        "table": "notification_jobs",
        "sql": "CREATE INDEX IF NOT EXISTS idx_notification_jobs_message_time_status ON notification_jobs(message_type, created_at, status)",
    },
    {
        "name": "idx_research_evidence_last_seen",
        "table": "research_evidence",
        "sql": "CREATE INDEX IF NOT EXISTS idx_research_evidence_last_seen ON research_evidence(last_seen_at, evidence_id)",
    },
    {
        "name": "idx_research_evidence_symbol_last_seen",
        "table": "research_evidence",
        "sql": "CREATE INDEX IF NOT EXISTS idx_research_evidence_symbol_last_seen ON research_evidence(symbol, last_seen_at, evidence_id)",
    },
    {
        "name": "idx_market_quote_cache_updated",
        "table": "market_quote_cache",
        "sql": "CREATE INDEX IF NOT EXISTS idx_market_quote_cache_updated ON market_quote_cache(updated_at, provider, account_id, symbol)",
    },
    {
        "name": "idx_market_quote_cache_account_updated",
        "table": "market_quote_cache",
        "sql": "CREATE INDEX IF NOT EXISTS idx_market_quote_cache_account_updated ON market_quote_cache(provider, account_id, updated_at, symbol)",
    },
    {
        "name": "idx_symbol_universe_active_market_symbol",
        "table": "symbol_universe",
        "sql": "CREATE INDEX IF NOT EXISTS idx_symbol_universe_active_market_symbol ON symbol_universe(active, market, symbol)",
    },
]


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _table_count(connection, table: str, where: str = "", params: Iterable[object] = ()):
    try:
        row = connection.execute("SELECT COUNT(*) AS count FROM " + table + (" WHERE " + where if where else ""), tuple(params)).fetchone()
        return int(row["count"] if row else 0)
    except sqlite3.Error:
        return 0


def _group_counts(connection, table: str, column: str) -> Dict[str, int]:
    try:
        rows = connection.execute("SELECT " + column + " AS key, COUNT(*) AS count FROM " + table + " GROUP BY " + column).fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["key"] or ""): int(row["count"] or 0) for row in rows}


def cleanup_policy_summary() -> List[Dict[str, object]]:
    return [
        {
            "key": policy["key"],
            "table": policy["table"],
            "timeColumn": policy["timeColumn"],
            "maxRows": policy["maxRows"],
            "protectWhere": policy["protectWhere"],
        }
        for policy in CLEANUP_POLICIES
    ]


def cleanup_index_summary() -> List[Dict[str, object]]:
    return [
        {
            "name": str(index["name"]),
            "table": str(index["table"]),
        }
        for index in CLEANUP_INDEXES
    ]


def _recent_lock_count() -> int:
    count = 0
    for name in LOG_FILES:
        path = data_dir() / name
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[-20000:]
        except OSError:
            continue
        count += text.lower().count("database is locked")
    return count


def sqlite_health_snapshot(path: Path = None) -> Dict[str, object]:
    db_path = Path(path or service_db_path()).resolve()
    wal_path = Path(str(db_path) + "-wal")
    shm_path = Path(str(db_path) + "-shm")
    payload: Dict[str, object] = {
        "generatedAt": utc_now(),
        "path": str(db_path),
        "exists": db_path.exists(),
        "sizeBytes": _file_size(db_path),
        "walSizeBytes": _file_size(wal_path),
        "shmSizeBytes": _file_size(shm_path),
        "recentLockLogCount": _recent_lock_count(),
        "journalMode": "",
        "busyTimeoutMs": 0,
        "tables": {},
        "outbox": {},
        "migrations": [],
        "pageCount": 0,
        "freelistCount": 0,
        "freelistBytes": 0,
        "cleanupPolicy": cleanup_policy_summary(),
        "cleanupIndexes": cleanup_index_summary(),
    }
    if not db_path.exists():
        return payload
    with connect_sqlite(db_path) as connection:
        try:
            payload["journalMode"] = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        except sqlite3.Error:
            payload["journalMode"] = ""
        try:
            payload["busyTimeoutMs"] = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
        except sqlite3.Error:
            payload["busyTimeoutMs"] = 0
        try:
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
            freelist_count = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
            payload["pageCount"] = page_count
            payload["freelistCount"] = freelist_count
            payload["freelistBytes"] = page_size * freelist_count
        except sqlite3.Error:
            pass
        payload["tables"] = {
            "runtimeSettings": _table_count(connection, "runtime_settings"),
            "domainEvents": _table_count(connection, "domain_events"),
            "monitorSnapshots": _table_count(connection, "monitor_snapshots"),
            "researchEvidence": _table_count(connection, "research_evidence"),
            "symbolUniverse": _table_count(connection, "symbol_universe"),
            "marketQuoteCache": _table_count(connection, "market_quote_cache"),
        }
        payload["outbox"] = {
            "notificationJobs": _group_counts(connection, "notification_jobs", "status"),
            "modelReviewJobs": _group_counts(connection, "model_review_jobs", "status"),
        }
        try:
            rows = connection.execute("SELECT version, applied_at FROM schema_migrations ORDER BY applied_at DESC, version DESC LIMIT 12").fetchall()
            payload["migrations"] = [{"version": row["version"], "appliedAt": row["applied_at"]} for row in rows]
        except sqlite3.Error:
            payload["migrations"] = []
    return payload


def _archive_path(table: str, archive_id: str) -> Path:
    archive_dir = data_dir() / "archive" / "sqlite"
    archive_dir.mkdir(parents=True, exist_ok=True)
    return archive_dir / (table + "-" + archive_id + ".jsonl")


def _write_archive_rows(table: str, rows: List[sqlite3.Row], archive_id: str) -> str:
    if not rows:
        return ""
    path = _archive_path(table, archive_id)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return str(path)


def _write_archive_payloads(table: str, payloads: List[Dict[str, object]], archive_id: str) -> str:
    if not payloads:
        return ""
    path = _archive_path(table, archive_id)
    with path.open("a", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return str(path)


def _trim_ordered_list(items: object, limit: int, keep_tail: bool) -> Dict[str, object]:
    if not isinstance(items, list):
        return {"items": [], "removed": []}
    normalized_limit = max(0, int(limit or 0))
    if not normalized_limit or len(items) <= normalized_limit:
        return {"items": list(items), "removed": []}
    if keep_tail:
        return {
            "items": list(items[-normalized_limit:]),
            "removed": list(items[:-normalized_limit]),
        }
    return {
        "items": list(items[:normalized_limit]),
        "removed": list(items[normalized_limit:]),
    }


def compact_app_store_payload(
    path: Path = None,
    archive_old_data: bool = False,
    archive_id: str = "",
    message_limit: int = DEFAULT_APP_STORE_MESSAGE_LIMIT,
    item_limit: int = DEFAULT_APP_STORE_ITEM_LIMIT,
    memory_limit: int = DEFAULT_APP_STORE_MEMORY_LIMIT,
) -> Dict[str, object]:
    db_path = Path(path or service_db_path()).resolve()
    result = {
        "table": "app_store",
        "deleted": 0,
        "archivePath": "",
        "limits": {
            "messages": max(1, int(message_limit or DEFAULT_APP_STORE_MESSAGE_LIMIT)),
            "items": max(1, int(item_limit or DEFAULT_APP_STORE_ITEM_LIMIT)),
            "memories": max(1, int(memory_limit or DEFAULT_APP_STORE_MEMORY_LIMIT)),
        },
    }
    with sqlite_transaction(db_path) as connection:
        row = connection.execute(
            "SELECT payload_json FROM app_store WHERE store_id = 'default'"
        ).fetchone()
        if not row:
            return result
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            return result
        if not isinstance(payload, dict):
            return result
        messages = _trim_ordered_list(payload.get("messages"), result["limits"]["messages"], keep_tail=True)
        items = _trim_ordered_list(payload.get("items"), result["limits"]["items"], keep_tail=False)
        memories = _trim_ordered_list(payload.get("memories"), result["limits"]["memories"], keep_tail=False)
        removed_payloads = []
        for key, trimmed in [("messages", messages), ("items", items), ("memories", memories)]:
            removed = list(trimmed["removed"] or [])
            if not removed:
                continue
            payload[key] = list(trimmed["items"] or [])
            result["deleted"] += len(removed)
            removed_payloads.append({
                "storeId": "default",
                "field": key,
                "removed": removed,
            })
        if not result["deleted"]:
            return result
        if archive_old_data:
            result["archivePath"] = _write_archive_payloads("app_store", removed_payloads, archive_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        connection.execute(
            """
            UPDATE app_store
            SET payload_json = ?, updated_at = ?
            WHERE store_id = 'default'
            """,
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), utc_now()),
        )
    return result


def _protected_clause(protect_where: str) -> str:
    return "NOT (" + protect_where + ")" if str(protect_where or "").strip() else "1 = 1"


def _candidate_rowids(
    connection,
    table: str,
    time_column: str,
    where_sql: str,
    params: Iterable[object],
    limit: int,
):
    return connection.execute(
        "SELECT rowid FROM " + table + " WHERE " + where_sql + " ORDER BY " + time_column + ", rowid LIMIT ?",
        tuple(params) + (int(limit),),
    ).fetchall()


def _candidate_overflow_rowids(
    connection,
    table: str,
    time_column: str,
    where_sql: str,
    params: Iterable[object],
    keep_rows: int,
    limit: int,
):
    normalized_params = tuple(params)
    return connection.execute(
        """
        SELECT rowid FROM """ + table + """
        WHERE """ + where_sql + """
          AND rowid NOT IN (
            SELECT rowid FROM """ + table + """
            WHERE """ + where_sql + """
            ORDER BY """ + time_column + """ DESC, rowid DESC
            LIMIT ?
          )
        ORDER BY """ + time_column + """, rowid
        LIMIT ?
        """,
        normalized_params + normalized_params + (max(0, int(keep_rows or 0)), int(limit)),
    ).fetchall()


def _delete_rowids(
    connection,
    table: str,
    rowids: List[int],
    archive_old_data: bool,
    archive_id: str,
) -> Dict[str, object]:
    if not rowids:
        return {"deleted": 0, "archivePath": ""}
    placeholders = ",".join(["?"] * len(rowids))
    archive_path = ""
    if archive_old_data:
        rows = connection.execute(
            "SELECT * FROM " + table + " WHERE rowid IN (" + placeholders + ")",
            rowids,
        ).fetchall()
        archive_path = _write_archive_rows(table, rows, archive_id)
    cursor = connection.execute(
        "DELETE FROM " + table + " WHERE rowid IN (" + placeholders + ")",
        rowids,
    )
    return {"deleted": int(cursor.rowcount or 0), "archivePath": archive_path}


def _cleanup_policy(
    db_path: Path,
    policy: Dict[str, object],
    cutoff_text: str,
    archive_old_data: bool,
    archive_id: str,
    batch_size: int,
    max_delete: int,
) -> Dict[str, object]:
    table = str(policy["table"])
    time_column = str(policy["timeColumn"])
    protected = _protected_clause(str(policy.get("protectWhere") or ""))
    max_rows = int(policy.get("maxRows") or 0)
    result = {"table": table, "deleted": 0, "archivePath": "", "cutoff": cutoff_text, "maxRows": max_rows}

    def delete_where(where_sql: str, params: Iterable[object], allowance: int) -> int:
        total = 0
        while allowance > 0:
            limit = max(1, min(batch_size, allowance))
            with sqlite_transaction(db_path) as connection:
                rows = _candidate_rowids(connection, table, time_column, where_sql, params, limit)
                rowids = [int(row["rowid"]) for row in rows]
                deleted = _delete_rowids(connection, table, rowids, archive_old_data, archive_id)
            count = int(deleted["deleted"] or 0)
            if deleted.get("archivePath"):
                result["archivePath"] = deleted["archivePath"]
            total += count
            allowance -= count
            if count <= 0 or count < limit:
                break
        return total

    def delete_overflow(where_sql: str, params: Iterable[object], keep_rows: int, allowance: int) -> int:
        total = 0
        while allowance > 0:
            limit = max(1, min(batch_size, allowance))
            with sqlite_transaction(db_path) as connection:
                rows = _candidate_overflow_rowids(connection, table, time_column, where_sql, params, keep_rows, limit)
                rowids = [int(row["rowid"]) for row in rows]
                deleted = _delete_rowids(connection, table, rowids, archive_old_data, archive_id)
            count = int(deleted["deleted"] or 0)
            if deleted.get("archivePath"):
                result["archivePath"] = deleted["archivePath"]
            total += count
            allowance -= count
            if count <= 0 or count < limit:
                break
        return total

    age_where = protected + " AND " + time_column + " != '' AND " + time_column + " < ?"
    result["deleted"] += delete_where(age_where, (cutoff_text,), max_delete)
    remaining_allowance = max(0, max_delete - int(result["deleted"]))
    if remaining_allowance and max_rows:
        result["deleted"] += delete_overflow(protected, (), max_rows, remaining_allowance)
    return result


def _ensure_cleanup_indexes(db_path: Path) -> None:
    with sqlite_transaction(db_path) as connection:
        for index in CLEANUP_INDEXES:
            try:
                connection.execute(str(index["sql"]))
            except sqlite3.Error:
                continue


def cleanup_old_sqlite_data(
    path: Path = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    archive_old_data: bool = False,
    batch_size: int = DEFAULT_CLEANUP_BATCH_SIZE,
    max_delete_per_table: int = DEFAULT_MAX_DELETE_PER_TABLE,
    compact_app_store: bool = True,
) -> Dict[str, object]:
    db_path = Path(path or service_db_path()).resolve()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days or DEFAULT_RETENTION_DAYS)))).isoformat().replace("+00:00", "Z")
    archive_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result = {
        "retentionDays": max(1, int(retention_days or DEFAULT_RETENTION_DAYS)),
        "cutoff": cutoff,
        "archive": bool(archive_old_data),
        "tables": {},
        "deletedTotal": 0,
    }
    _ensure_cleanup_indexes(db_path)
    for policy in CLEANUP_POLICIES:
        try:
            item = _cleanup_policy(
                db_path,
                policy,
                cutoff,
                bool(archive_old_data),
                archive_id,
                max(1, int(batch_size or DEFAULT_CLEANUP_BATCH_SIZE)),
                max(0, int(max_delete_per_table or DEFAULT_MAX_DELETE_PER_TABLE)),
            )
        except sqlite3.Error as error:
            item = {"table": policy["table"], "deleted": 0, "error": str(error)}
        result["tables"][policy["key"]] = item
        result["deletedTotal"] += int(item.get("deleted") or 0)
    if compact_app_store:
        try:
            item = compact_app_store_payload(
                db_path,
                archive_old_data=bool(archive_old_data),
                archive_id=archive_id,
            )
        except sqlite3.Error as error:
            item = {"table": "app_store", "deleted": 0, "error": str(error)}
        result["tables"]["appStore"] = item
        result["deletedTotal"] += int(item.get("deleted") or 0)
    return result


def run_sqlite_maintenance(
    path: Path = None,
    checkpoint: bool = True,
    optimize: bool = True,
    recover_processing: bool = True,
    cleanup_old_data: bool = False,
    archive_old_data: bool = False,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    compact_app_store: bool = True,
    vacuum: bool = False,
) -> Dict[str, object]:
    db_path = Path(path or service_db_path()).resolve()
    result = {"ranAt": utc_now(), "checkpoint": None, "optimized": False, "vacuumed": False, "recoveredProcessing": {}, "cleanup": None}
    if not db_path.exists():
        result["missing"] = True
        return result
    with sqlite_transaction(db_path) as connection:
        if recover_processing:
            stamp = utc_now()
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
            for table in ["notification_jobs", "model_review_jobs"]:
                try:
                    rows = connection.execute(
                        """
                        SELECT job_id, payload_json FROM """ + table + """
                        WHERE status = 'processing'
                          AND COALESCE(NULLIF(processing_started_at, ''), NULLIF(updated_at, ''), created_at) <= ?
                        """,
                        (cutoff,),
                    ).fetchall()
                    recovered = 0
                    for row in rows:
                        try:
                            payload = json.loads(row["payload_json"] or "{}")
                        except json.JSONDecodeError:
                            payload = {}
                        if isinstance(payload, dict):
                            payload["status"] = "failed"
                            payload["updatedAt"] = stamp
                            payload["lastError"] = "stuck processing job recovered by SQLite maintenance"
                        cursor = connection.execute(
                            """
                            UPDATE """ + table + """
                            SET status = 'failed', updated_at = ?, last_error = 'stuck processing job recovered by SQLite maintenance',
                                payload_json = ?
                            WHERE job_id = ? AND status = 'processing'
                            """,
                            (stamp, json.dumps(payload, ensure_ascii=False, sort_keys=True), row["job_id"]),
                        )
                        recovered += int(cursor.rowcount or 0)
                    result["recoveredProcessing"][table] = recovered
                except sqlite3.Error:
                    result["recoveredProcessing"][table] = 0
    if cleanup_old_data:
        result["cleanup"] = cleanup_old_sqlite_data(
            db_path,
            retention_days=retention_days,
            archive_old_data=archive_old_data,
            compact_app_store=compact_app_store,
        )
    if checkpoint:
        with connect_sqlite(db_path) as connection:
            result["checkpoint"] = [int(item) for item in connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()]
    if optimize:
        with connect_sqlite(db_path) as connection:
            with_sqlite_retry(lambda: connection.execute("PRAGMA optimize"))
            result["optimized"] = True
    if vacuum:
        with connect_sqlite(db_path) as connection:
            with_sqlite_retry(lambda: connection.execute("VACUUM"))
            result["vacuumed"] = True
    result["health"] = sqlite_health_snapshot(db_path)
    return result
