"""Archive receipt and compare-before-delete boundary for local MySQL history."""
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import os

from digital_twin.platform.domain.storage_preservation import ARCHIVE_BEFORE_DELETE
from .history_archive import LocalHistoryArchive, canonical_bytes
from .mysql_schema_tuning import quote_identifier


def _execute(connection, sql, params=()):
    if hasattr(connection, "execute"):
        return connection.execute(sql, params)
    cursor = connection.cursor()
    cursor.execute(sql, params)
    return cursor


def archive_for_connection(connection):
    from .settings import data_dir
    row = _execute(connection, "SELECT DATABASE() AS archive_database").fetchone()
    database = row.get("archive_database") if isinstance(row, dict) else row[0]
    if not database:
        raise ValueError("History archive requires a database namespace")
    namespace = hashlib.sha256(str(database).encode()).hexdigest()[:24]
    configured = _execute(connection, "SELECT value FROM runtime_settings WHERE `key`=%s",
                          ("operationalCriticalFreeSpaceMb",)).fetchone()
    reserve_mb = (configured.get("value") if isinstance(configured, dict) else configured[0]) if configured else None
    reserve_mb = reserve_mb or os.environ.get("OPERATIONAL_CRITICAL_FREE_SPACE_MB") or "6144"
    return LocalHistoryArchive(data_dir() / "history-archive" / namespace,
                               minimum_free_bytes=int(reserve_mb) * 1024 * 1024)


def record_key(table, key_values):
    keys = ARCHIVE_BEFORE_DELETE.get(table)
    if not keys or set(key_values) != set(keys):
        raise ValueError("History archive requires the complete allowed primary key")
    return hashlib.sha256(canonical_bytes(key_values)).hexdigest()


@contextmanager
def eviction_transaction(connection):
    raw = getattr(connection, "connection", connection)
    # Runtime maintenance normally uses autocommit. Do not let a crash land
    # between deleting the hot row and promoting its archive receipt.
    owned = bool(raw.get_autocommit()) and not (int(raw.server_status) & 1)
    if owned:
        _execute(connection, "START TRANSACTION")
    try:
        yield
        if owned:
            raw.commit()
    except BaseException:
        if owned:
            raw.rollback()
        raise


def archive_delete_row(connection, table, key_values, guard_sql, guard_params=(), *, archive=None, may_evict=None):
    """Use the caller's transaction; a concurrent correction prevents deletion.

    File persistence/readback precedes both the receipt and the DELETE. The
    receipt is committed in the same MySQL transaction as eviction. No runtime
    DDL, table scan, row lock held while writing files, or external I/O occurs.
    """
    key_hash = record_key(table, key_values)
    archive = archive or archive_for_connection(connection)
    if not archive.can_stage():
        return 0
    keys = ARCHIVE_BEFORE_DELETE[table]
    quoted = quote_identifier(table)
    where = " AND ".join(quote_identifier(key) + " = %s" for key in keys)
    values = tuple(key_values[key] for key in keys)
    columns = _execute(connection, "SHOW COLUMNS FROM " + quoted).fetchall()
    names = [column["Field"] for column in columns]
    if not names:
        raise ValueError("History archive column inventory unavailable")
    row_hash_sql = "SHA2(CAST(JSON_ARRAY(" + ",".join(quote_identifier(name) for name in names) + ") AS CHAR),256)"
    row = _execute(connection, "SELECT *, " + row_hash_sql + " AS archive_row_hash FROM " + quoted
                   + " WHERE " + where + " AND (" + guard_sql + ") LIMIT 1", values + tuple(guard_params)).fetchone()
    if not row:
        return 0
    row = dict(row)
    sql_hash = row.pop("archive_row_hash")
    receipt = archive.put(row)
    if not receipt.get("restoreVerified"):
        raise ValueError("Unverified history archive")
    if may_evict is not None and not may_evict():
        return 0
    with eviction_transaction(connection):
        _execute(connection,
            "INSERT INTO storage_archive_records "
            "(table_name,key_hash,row_hash,manifest_hash,original_bytes,stored_bytes,account_id,source_at,archived_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE manifest_hash=VALUES(manifest_hash), original_bytes=VALUES(original_bytes), "
            "stored_bytes=stored_bytes+VALUES(stored_bytes), account_id=VALUES(account_id), "
            "source_at=VALUES(source_at), archived_at=VALUES(archived_at)",
            (table, key_hash, sql_hash, receipt["manifestSha256"], receipt["originalBytes"],
             receipt["writtenBytes"], row.get("account_id") or "", row.get("generated_at") or row.get("as_of") or "",
             datetime.now(timezone.utc).isoformat()))
        cursor = _execute(connection, "DELETE FROM " + quoted + " WHERE " + where
            + " AND (" + guard_sql + ") AND " + row_hash_sql + " = %s",
            values + tuple(guard_params) + (sql_hash,))
        deleted = max(0, int(cursor.rowcount))
        if deleted:
            _execute(connection, "UPDATE storage_archive_records SET evicted=1 WHERE table_name=%s "
                     "AND key_hash=%s AND row_hash=%s", (table, key_hash, sql_hash))
    # A correction/claim racing with archival leaves an unpromoted receipt.
    # It cannot be mistaken for the authoritative, evicted version.
    return deleted


def read_archived_row(connection, table, key_values, *, archive=None):
    key_hash = record_key(table, key_values)
    row = _execute(connection, "SELECT manifest_hash FROM storage_archive_records "
        "WHERE table_name=%s AND key_hash=%s AND evicted=1 ORDER BY archived_at DESC, row_hash DESC LIMIT 1",
        (table, key_hash)).fetchone()
    if not row:
        return None
    restored = (archive or archive_for_connection(connection)).get(row["manifest_hash"])
    if any(restored.get(key) != value for key, value in key_values.items()):
        raise ValueError("History archive identity mismatch")
    return restored


def history_archive_status(connection):
    # This is an explicit operator query on compact receipts, never payloads.
    return _execute(connection, "SELECT table_name, COUNT(*) AS records, "
        "SUM(original_bytes) AS original_bytes, SUM(stored_bytes) AS stored_bytes, "
        "MIN(archived_at) AS first_archived_at, MAX(archived_at) AS last_archived_at "
        "FROM storage_archive_records WHERE evicted=1 GROUP BY table_name").fetchall()


def read_archived_source_window(connection, account_id, start, end, *, max_bytes=64 * 1024 * 1024):
    headers = _execute(connection, "SELECT a.manifest_hash, a.original_bytes, a.key_hash FROM storage_archive_records a "
        "WHERE a.table_name='verified_reasoning_source_snapshots' AND a.account_id=%s "
        "AND a.source_at >= %s AND a.source_at <= %s AND a.evicted=1 "
        "AND NOT EXISTS (SELECT 1 FROM storage_archive_records newer WHERE newer.table_name=a.table_name "
        "AND newer.key_hash=a.key_hash AND newer.evicted=1 AND (newer.archived_at > a.archived_at "
        "OR (newer.archived_at=a.archived_at AND newer.row_hash > a.row_hash))) "
        "ORDER BY a.source_at LIMIT 1001",
        (account_id, start, end)).fetchall()
    if len(headers) > 1000 or sum(int(row["original_bytes"]) for row in headers) > max_bytes:
        raise ValueError("Historical source window exceeds bounded archive read limit")
    if not headers:
        return []
    archive = archive_for_connection(connection)
    rows = [archive.get(row["manifest_hash"]) for row in headers]
    if any(row.get("account_id") != account_id or not start <= row.get("generated_at", "") <= end
           or record_key("verified_reasoning_source_snapshots", {"snapshot_id": row.get("snapshot_id")}) != header["key_hash"]
           for row, header in zip(rows, headers)):
        raise ValueError("Historical source archive boundary mismatch")
    return rows


def restore_archived_row(connection, table, key_values):
    """Explicit maintenance only: insert an exact historical row, never overwrite."""
    restored = read_archived_row(connection, table, key_values)
    if not restored:
        return {"status": "missing", "table": table}
    fields = tuple(restored)
    where = " AND ".join(quote_identifier(key) + "=%s" for key in key_values)
    existing = _execute(connection, "SELECT * FROM " + quote_identifier(table) + " WHERE " + where,
                        tuple(key_values.values())).fetchone()
    if existing:
        if dict(existing) != restored:
            raise ValueError("Restore refuses to replace a newer or different row")
        return {"status": "already-restored", "table": table}
    with eviction_transaction(connection):
        _execute(connection, "INSERT INTO " + quote_identifier(table) + " ("
            + ",".join(quote_identifier(field) for field in fields) + ") VALUES ("
            + ",".join(["%s"] * len(fields)) + ")", tuple(restored[field] for field in fields))
        readback = _execute(connection, "SELECT * FROM " + quote_identifier(table) + " WHERE " + where,
                            tuple(key_values.values())).fetchone()
        if not readback or dict(readback) != restored:
            raise ValueError("Restored row failed exact readback verification")
    return {"status": "restored", "table": table, "verified": True}


def verify_history_archive(connection, limit=32):
    headers = _execute(connection, "SELECT table_name, key_hash, manifest_hash FROM storage_archive_records "
        "WHERE evicted=1 ORDER BY table_name,key_hash,row_hash LIMIT %s", (max(1, min(128, int(limit))),)).fetchall()
    archive = archive_for_connection(connection)
    for header in headers:
        restored = archive.get(header["manifest_hash"])
        keys = {name: restored[name] for name in ARCHIVE_BEFORE_DELETE[header["table_name"]]}
        if record_key(header["table_name"], keys) != header["key_hash"]:
            raise ValueError("Archived row identity verification failed")
    return {"status": "verified", "checked": len(headers), "scope": "bounded-sample"}
