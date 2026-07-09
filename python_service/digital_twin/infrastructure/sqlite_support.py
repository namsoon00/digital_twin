import os
import sqlite3
from pathlib import Path


SQLITE_BUSY_TIMEOUT_MS = 30000


def connect_sqlite(path: Path):
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(resolved), timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = " + str(SQLITE_BUSY_TIMEOUT_MS))
    try:
        connection.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        # Another local worker may be migrating or writing. Keep the connection
        # usable with the busy timeout; WAL will be applied by a later opener.
        pass
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        os.chmod(resolved, 0o600)
    except OSError:
        pass
    return connection
