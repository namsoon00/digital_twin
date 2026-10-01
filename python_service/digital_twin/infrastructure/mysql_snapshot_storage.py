"""Lossless storage policy for large, immutable operational JSON snapshots.

Compression is physical: JSON queries, source fingerprints and replay contracts
remain unchanged. Existing tables are rebuilt only by explicit offline work.
"""
import hashlib
import re

from digital_twin.infrastructure.mysql_schema_tuning import quote_identifier


COMPRESSED_SNAPSHOT_TABLES = frozenset({
    "monitor_snapshot_history", "verified_reasoning_source_snapshots",
    "statistical_model_signal_snapshots", "ontology_reasoning_run_stages",
})


def compression_supported(connection):
    row = connection.execute("SELECT @@innodb_page_size AS pageSize, "
                             "@@innodb_file_per_table AS filePerTable").fetchone() or {}
    return int(row.get("pageSize") or 0) in {8192, 16384} and bool(row.get("filePerTable"))


def snapshot_schema_statement(statement, supported=True):
    match = re.search(r"CREATE TABLE IF NOT EXISTS\s+`?(\w+)`?", statement, re.I)
    if not supported or not match or match[1] not in COMPRESSED_SNAPSHOT_TABLES:
        return statement
    return statement.replace("ENGINE=InnoDB", "ENGINE=InnoDB ROW_FORMAT=COMPRESSED KEY_BLOCK_SIZE=8", 1)


def table_fingerprint(connection, table):
    """Hash all stored columns without transferring private payloads to Python."""
    if table not in COMPRESSED_SNAPSHOT_TABLES:
        raise ValueError("Table is outside snapshot compression policy")
    quoted = quote_identifier(table)
    columns = connection.execute("SHOW COLUMNS FROM " + quoted).fetchall()
    fields = ",".join(quote_identifier(row["Field"]) for row in columns)
    keys = connection.execute("SHOW KEYS FROM " + quoted + " WHERE Key_name='PRIMARY'").fetchall()
    order = ",".join(quote_identifier(row["Column_name"]) for row in sorted(keys, key=lambda r: r["Seq_in_index"]))
    if not fields or not order:
        raise ValueError("Snapshot compression requires a primary key")
    rows = connection.execute("SELECT SHA2(CAST(JSON_ARRAY(" + fields + ") AS CHAR),256) AS rowHash FROM "
                              + quoted + " ORDER BY " + order).fetchall()
    digest = hashlib.sha256()
    for row in rows:
        value = str(row["rowHash"] or "")
        if not re.fullmatch(r"[a-f0-9]{64}", value):
            raise RuntimeError("Snapshot row fingerprint is unavailable")
        digest.update(value.encode("ascii"))
    return {"rows": len(rows), "sha256": digest.hexdigest()}


def compress_snapshot_table(connection, table, *, writers_paused=False):
    """Explicit offline migration; verify every row before accepting the result."""
    if not writers_paused:
        raise ValueError("Pause managed writers before snapshot compression")
    if table not in COMPRESSED_SNAPSHOT_TABLES:
        raise ValueError("Table is outside snapshot compression policy")
    if not compression_supported(connection):
        raise ValueError("Snapshot compression needs file-per-table and 8/16 KiB InnoDB pages")
    row = connection.execute("SELECT row_format AS rowFormat FROM information_schema.tables "
        "WHERE table_schema=DATABASE() AND table_name=%s", (table,)).fetchone()
    if not row:
        raise ValueError("Snapshot table does not exist")
    if str(row["rowFormat"]).lower() == "compressed":
        return {"table": table, "status": "already-compressed"}
    before = table_fingerprint(connection, table)
    connection.execute("ALTER TABLE " + quote_identifier(table) + " ROW_FORMAT=COMPRESSED KEY_BLOCK_SIZE=8")
    after = table_fingerprint(connection, table)
    if before != after:
        raise RuntimeError("Snapshot contents changed during compression")
    connection.execute("ANALYZE TABLE " + quote_identifier(table))
    return {"table": table, "status": "compressed", "before": before, "after": after}


def run_snapshot_compression(settings, *, apply=False, tables=()):
    """Preview locally allocated bytes; migrate only with managed writers idle."""
    from pathlib import Path
    import shutil
    import time
    import pymysql
    from .mysql_operational_connection import MySQLOperationalConnection, MySQLConnectionProxy
    from digital_twin import service_manager

    requested = sorted(set(tables or COMPRESSED_SNAPSHOT_TABLES))
    if set(requested) - COMPRESSED_SNAPSHOT_TABLES:
        raise ValueError("Table is outside snapshot compression policy")
    configured = {**settings, "_skipOperationalSchemaBootstrap": True,
                  "_skipOperationalHistoryRetention": True}
    store = MySQLOperationalConnection(configured)
    if store.mysql_config["host"] not in {"127.0.0.1", "localhost"}:
        raise ValueError("Snapshot compression requires the managed local MySQL server")
    if apply:
        running = [name for name, spec in service_manager.worker_specs().items()
                   if spec.get("role") not in {"mysql", "typedb", "questdb", "cloudflare-share"}
                   and service_manager.is_running(service_manager.read_pid(spec.get("pid")), spec)]
        if running:
            return {"status": "deferred", "reason": "pause-managed-writers", "workers": running}
    reserve = 12 * 1024 ** 3
    results = []
    # Rebuilds can exceed the realtime pool's 120-second ceiling. Keep this
    # explicit offline connection isolated; never lengthen worker timeouts.
    config = store.mysql_config
    raw = pymysql.connect(**{key: config[key] for key in ('host', 'port', 'user', 'password', 'database')},
        unix_socket=config.get('unix_socket') or None, charset='utf8mb4', autocommit=True,
        cursorclass=pymysql.cursors.DictCursor, connect_timeout=10, read_timeout=900, write_timeout=30)
    with MySQLConnectionProxy(raw) as connection:
        connection.execute("SET SESSION lock_wait_timeout=30")
        if not compression_supported(connection):
            return {"status": "unsupported", "tables": []}
        data_root = Path(connection.execute("SELECT @@datadir AS directory").fetchone()["directory"])
        root = data_root / store.mysql_config["database"]
        def allocated(table):
            files = list(root.glob(table + ".ibd")) + list(root.glob(table + "#p#*.ibd"))
            if not files:
                raise ValueError("Local table allocation could not be measured")
            return sum(path.stat().st_blocks * 512 for path in files)
        for table in requested:
            row = connection.execute("SELECT row_format AS rowFormat FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name=%s", (table,)).fetchone()
            if not row:
                continue
            before_bytes = allocated(table)
            item = {"table": table, "rowFormat": row["rowFormat"], "beforeBytes": before_bytes}
            results.append(item)
            if not apply or str(row["rowFormat"]).lower() == "compressed":
                continue
            # Do not infer free space from DATA_LENGTH: off-page JSON can make
            # that estimate far smaller than the actual file allocation.
            if shutil.disk_usage(root).free < reserve + before_bytes + 64 * 1024 ** 2:
                return {"status": "deferred", "reason": "insufficient-headroom", "tables": results}
            started = time.monotonic()
            item.update(compress_snapshot_table(connection, table, writers_paused=True))
            item.update(afterBytes=allocated(table), durationSeconds=round(time.monotonic() - started, 3))
    return {"status": "compressed" if apply else "preview", "tables": results}
