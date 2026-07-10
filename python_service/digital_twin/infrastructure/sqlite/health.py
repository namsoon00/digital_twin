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
    ("monitor_sent", "idx_monitor_sent_sent_at", "sent_at"),
    ("domain_events", "idx_domain_events_occurred_at", "occurred_at"),
    ("model_review_jobs", "idx_model_review_jobs_created", "created_at"),
    ("ontology_ai_opinion_samples", "idx_ontology_quality_created_at", "created_at"),
    ("notification_jobs", "idx_notification_jobs_created", "created_at"),
    ("research_evidence", "idx_research_evidence_last_seen", "last_seen_at"),
    ("market_quote_cache", "idx_market_quote_cache_updated", "updated_at"),
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

    age_where = protected + " AND " + time_column + " != '' AND " + time_column + " < ?"
    result["deleted"] += delete_where(age_where, (cutoff_text,), max_delete)
    remaining_allowance = max(0, max_delete - int(result["deleted"]))
    if remaining_allowance and max_rows:
        with connect_sqlite(db_path) as connection:
            try:
                eligible = int(connection.execute(
                    "SELECT COUNT(*) AS count FROM " + table + " WHERE " + protected,
                ).fetchone()["count"])
            except sqlite3.Error:
                eligible = 0
        overflow = max(0, eligible - max_rows)
        if overflow:
            trim_where = protected
            result["deleted"] += delete_where(trim_where, (), min(overflow, remaining_allowance))
    return result


def _ensure_cleanup_indexes(db_path: Path) -> None:
    with sqlite_transaction(db_path) as connection:
        for table, index_name, column in CLEANUP_INDEXES:
            try:
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS " + index_name + " ON " + table + "(" + column + ")"
                )
            except sqlite3.Error:
                continue


def cleanup_old_sqlite_data(
    path: Path = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    archive_old_data: bool = False,
    batch_size: int = DEFAULT_CLEANUP_BATCH_SIZE,
    max_delete_per_table: int = DEFAULT_MAX_DELETE_PER_TABLE,
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
    return result


def run_sqlite_maintenance(
    path: Path = None,
    checkpoint: bool = True,
    optimize: bool = True,
    recover_processing: bool = True,
    cleanup_old_data: bool = False,
    archive_old_data: bool = False,
    retention_days: int = DEFAULT_RETENTION_DAYS,
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
