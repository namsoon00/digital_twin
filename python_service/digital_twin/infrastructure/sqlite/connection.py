import os
import random
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, TypeVar


SQLITE_BUSY_TIMEOUT_MS = int(os.environ.get("SQLITE_BUSY_TIMEOUT_MS") or "30000")
SQLITE_RETRY_ATTEMPTS = int(os.environ.get("SQLITE_RETRY_ATTEMPTS") or "5")
SQLITE_RETRY_BASE_SECONDS = float(os.environ.get("SQLITE_RETRY_BASE_SECONDS") or "0.05")

T = TypeVar("T")


class ManagedSQLiteConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def is_locked_error(error: BaseException) -> bool:
    text = str(error or "").lower()
    return isinstance(error, sqlite3.OperationalError) and (
        "database is locked" in text
        or "database table is locked" in text
        or "database schema is locked" in text
        or "locked database" in text
    )


def with_sqlite_retry(operation: Callable[[], T], attempts: int = SQLITE_RETRY_ATTEMPTS) -> T:
    last_error = None
    for index in range(max(1, int(attempts or 1))):
        try:
            return operation()
        except sqlite3.OperationalError as error:
            if not is_locked_error(error) or index >= attempts - 1:
                raise
            last_error = error
            delay = SQLITE_RETRY_BASE_SECONDS * (2 ** index)
            delay += random.uniform(0, SQLITE_RETRY_BASE_SECONDS)
            time.sleep(delay)
    if last_error:
        raise last_error
    return operation()


def connect_sqlite(path: Path):
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(resolved), timeout=SQLITE_BUSY_TIMEOUT_MS / 1000, factory=ManagedSQLiteConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = " + str(SQLITE_BUSY_TIMEOUT_MS))
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        os.chmod(resolved, 0o600)
    except OSError:
        pass
    return connection


@contextmanager
def sqlite_transaction(path: Path, mode: str = "DEFERRED"):
    connection = connect_sqlite(path)
    opened = False
    try:
        statement = "BEGIN " + str(mode or "DEFERRED").upper()
        with_sqlite_retry(lambda: connection.execute(statement))
        opened = True
        yield connection
        connection.commit()
    except Exception:
        if opened:
            connection.rollback()
        raise
    finally:
        connection.close()
