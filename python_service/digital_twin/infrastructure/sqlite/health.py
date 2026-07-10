import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable

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


def run_sqlite_maintenance(path: Path = None, checkpoint: bool = True, optimize: bool = True, recover_processing: bool = True) -> Dict[str, object]:
    db_path = Path(path or service_db_path()).resolve()
    result = {"ranAt": utc_now(), "checkpoint": None, "optimized": False, "recoveredProcessing": {}}
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
    if checkpoint:
        with connect_sqlite(db_path) as connection:
            result["checkpoint"] = [int(item) for item in connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()]
    if optimize:
        with connect_sqlite(db_path) as connection:
            with_sqlite_retry(lambda: connection.execute("PRAGMA optimize"))
            result["optimized"] = True
    result["health"] = sqlite_health_snapshot(db_path)
    return result
