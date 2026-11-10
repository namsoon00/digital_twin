"""Passive slow-operation receipts; no diagnostic queries or payload logging."""

import json
import os
import sys
import time
from datetime import datetime, timezone


def slow_mysql_operation(connection, statement, started_at, error=None, *, previous_statement=""):
    try:
        elapsed = max(0, int((time.monotonic() - started_at) * 1000))
        try:
            threshold = max(0, int(os.environ.get("ORBIT_MYSQL_SLOW_OPERATION_MS", "1000")))
        except ValueError:
            threshold = 1000
        if not threshold or elapsed < threshold:
            return
        # Import lazily: the connection facade owns the safe verb/table parser.
        from .mysql_operational_connection import mysql_statement_label, mysql_error_code
        label = statement if statement == "POOL ACQUIRE" else mysql_statement_label(statement)
        try:
            connection_id = int(connection.thread_id()) if connection is not None else 0
        except Exception:
            connection_id = 0
        record = {
            "event": "mysql.slow-operation",
            "observedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "pid": os.getpid(), "connectionId": connection_id,
            "operation": label, "elapsedMs": elapsed,
            "status": "error" if error is not None else "ok",
        }
        if error is not None:
            record["errorCode"] = mysql_error_code(error)
        if statement == "COMMIT" and previous_statement:
            record["previousOperation"] = mysql_statement_label(previous_statement)
        print(json.dumps(record, separators=(",", ":")), file=sys.stderr, flush=True)
    except Exception:
        # Observability cannot change transaction, pool or retry semantics.
        return
