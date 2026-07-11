import os
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

from ..domain.accounts import AccountConfig
from ..domain.repositories import MonitorAccountJob
from .settings import utc_now


class MySQLDependencyError(RuntimeError):
    pass


_MYSQL_DATABASE_READY = set()


def mysql_settings(settings: Dict[str, str] = None) -> Dict[str, object]:
    configured = settings or {}
    url = str(configured.get("mysqlUrl") or os.environ.get("MYSQL_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if url:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        return {
            "host": parsed.hostname or "127.0.0.1",
            "port": int(parsed.port or 3306),
            "user": urllib.parse.unquote(parsed.username or ""),
            "password": urllib.parse.unquote(parsed.password or ""),
            "database": (parsed.path or "/").lstrip("/"),
            "unix_socket": query.get("unix_socket", [""])[0],
        }
    return {
        "host": configured.get("mysqlHost") or os.environ.get("MYSQL_HOST") or "127.0.0.1",
        "port": int(configured.get("mysqlPort") or os.environ.get("MYSQL_PORT") or 3306),
        "user": configured.get("mysqlUser") or os.environ.get("MYSQL_USER") or "root",
        "password": configured.get("mysqlPassword") or os.environ.get("MYSQL_PASSWORD") or "",
        "database": configured.get("mysqlDatabase") or os.environ.get("MYSQL_DATABASE") or "orbit_alpha",
        "unix_socket": configured.get("mysqlUnixSocket") or os.environ.get("MYSQL_UNIX_SOCKET") or "",
    }


def ensure_mysql_database_exists(settings: Dict[str, object]) -> None:
    database = str((settings or {}).get("database") or "").strip()
    if not database:
        raise MySQLDependencyError("MySQL database name is required. Set MYSQL_DATABASE.")
    cache_key = (
        str((settings or {}).get("host") or "127.0.0.1"),
        str((settings or {}).get("port") or 3306),
        database,
        str((settings or {}).get("unix_socket") or ""),
        str((settings or {}).get("user") or ""),
    )
    if cache_key in _MYSQL_DATABASE_READY:
        return
    try:
        import pymysql
    except ImportError as error:
        raise MySQLDependencyError("MySQL backend requires pymysql. Install with: python3 -m pip install pymysql") from error
    kwargs = {
        "host": settings.get("host") or "127.0.0.1",
        "port": int(settings.get("port") or 3306),
        "user": settings.get("user") or "",
        "password": settings.get("password") or "",
        "charset": "utf8mb4",
        "autocommit": True,
    }
    if settings.get("unix_socket"):
        kwargs["unix_socket"] = settings["unix_socket"]
    connection = pymysql.connect(**kwargs)
    try:
        with connection.cursor() as cursor:
            escaped = database.replace("`", "``")
            cursor.execute("CREATE DATABASE IF NOT EXISTS `" + escaped + "` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        _MYSQL_DATABASE_READY.add(cache_key)
    finally:
        connection.close()


def forget_mysql_database(settings: Dict[str, object]) -> None:
    database = str((settings or {}).get("database") or "").strip()
    cache_key = (
        str((settings or {}).get("host") or "127.0.0.1"),
        str((settings or {}).get("port") or 3306),
        database,
        str((settings or {}).get("unix_socket") or ""),
        str((settings or {}).get("user") or ""),
    )
    _MYSQL_DATABASE_READY.discard(cache_key)


def mysql_backend_enabled(settings: Dict[str, str] = None) -> bool:
    configured = settings or {}
    backend = str(configured.get("operationalDbBackend") or os.environ.get("OPERATIONAL_DB_BACKEND") or "").strip().lower()
    if backend in {"mysql", "mariadb"}:
        return True
    return bool(str(configured.get("mysqlUrl") or os.environ.get("MYSQL_URL") or "").strip())


def monitor_account_job_from_row(row) -> MonitorAccountJob:
    return MonitorAccountJob(
        account_id=str(row.get("account_id") or ""),
        status=str(row.get("status") or "pending"),
        priority=int(row.get("priority") or 100),
        next_run_at=str(row.get("next_run_at") or ""),
        locked_by=str(row.get("locked_by") or ""),
        locked_until=str(row.get("locked_until") or ""),
        attempts=int(row.get("attempts") or 0),
        last_started_at=str(row.get("last_started_at") or ""),
        last_finished_at=str(row.get("last_finished_at") or ""),
        last_error=str(row.get("last_error") or ""),
        updated_at=str(row.get("updated_at") or ""),
    )


class MySQLMonitorAccountJobStore:
    def __init__(self, settings: Dict[str, str] = None):
        self.settings = mysql_settings(settings)
        ensure_mysql_database_exists(self.settings)
        self.ensure_schema()

    def connect(self):
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as error:
            raise MySQLDependencyError("MySQL backend requires pymysql. Install with: python3 -m pip install pymysql") from error
        kwargs = {
            "host": self.settings["host"],
            "port": int(self.settings["port"] or 3306),
            "user": self.settings["user"],
            "password": self.settings["password"],
            "database": self.settings["database"],
            "charset": "utf8mb4",
            "cursorclass": DictCursor,
            "autocommit": False,
        }
        if self.settings.get("unix_socket"):
            kwargs["unix_socket"] = self.settings["unix_socket"]
        return pymysql.connect(**kwargs)

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ensure_schema(self) -> None:
        with self.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS monitor_account_jobs (
                        account_id VARCHAR(191) PRIMARY KEY,
                        status VARCHAR(32) NOT NULL DEFAULT 'pending',
                        priority INT NOT NULL DEFAULT 100,
                        next_run_at VARCHAR(40) NOT NULL DEFAULT '',
                        locked_by VARCHAR(191) NOT NULL DEFAULT '',
                        locked_until VARCHAR(40) NOT NULL DEFAULT '',
                        attempts INT NOT NULL DEFAULT 0,
                        last_started_at VARCHAR(40) NOT NULL DEFAULT '',
                        last_finished_at VARCHAR(40) NOT NULL DEFAULT '',
                        last_error VARCHAR(500) NOT NULL DEFAULT '',
                        updated_at VARCHAR(40) NOT NULL,
                        KEY idx_monitor_account_jobs_due (status, next_run_at, priority, account_id),
                        KEY idx_monitor_account_jobs_lock (status, locked_until, account_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)

    def sync_accounts(self, accounts: Iterable[AccountConfig], default_interval_seconds: int) -> None:
        enabled_accounts = [account for account in (accounts or []) if getattr(account, "enabled", True)]
        stamp = utc_now()
        with self.transaction() as connection:
            with connection.cursor() as cursor:
                for account in enabled_accounts:
                    cursor.execute(
                        """
                        INSERT INTO monitor_account_jobs (
                            account_id, status, priority, next_run_at, locked_by, locked_until,
                            attempts, last_started_at, last_finished_at, last_error, updated_at
                        )
                        VALUES (%s, 'pending', 100, %s, '', '', 0, '', '', '', %s)
                        ON DUPLICATE KEY UPDATE
                            next_run_at = CASE
                                WHEN next_run_at = '' THEN VALUES(next_run_at)
                                ELSE next_run_at
                            END,
                            updated_at = VALUES(updated_at)
                        """,
                        (account.account_id, stamp, stamp),
                    )
                if enabled_accounts:
                    placeholders = ",".join(["%s"] * len(enabled_accounts))
                    cursor.execute(
                        "DELETE FROM monitor_account_jobs WHERE account_id NOT IN (" + placeholders + ")",
                        [account.account_id for account in enabled_accounts],
                    )
                else:
                    cursor.execute("DELETE FROM monitor_account_jobs")

    def claim_due(
        self,
        limit: int,
        worker_id: str,
        lock_seconds: int,
        default_interval_seconds: int,
    ) -> List[MonitorAccountJob]:
        stamp = utc_now()
        locked_until = (datetime.now(timezone.utc) + timedelta(seconds=max(60, int(lock_seconds or 600)))).isoformat().replace("+00:00", "Z")
        claimed: List[MonitorAccountJob] = []
        with self.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT account_id, status, priority, next_run_at, locked_by, locked_until,
                        attempts, last_started_at, last_finished_at, last_error, updated_at
                    FROM monitor_account_jobs
                    WHERE (
                        status IN ('pending', 'done', 'failed')
                        AND (next_run_at = '' OR next_run_at <= %s)
                    ) OR (
                        status = 'processing'
                        AND COALESCE(NULLIF(locked_until, ''), next_run_at, updated_at) <= %s
                    )
                    ORDER BY priority ASC, next_run_at ASC, account_id ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (stamp, stamp, max(1, int(limit or 1))),
                )
                rows = cursor.fetchall()
                for row in rows:
                    job = monitor_account_job_from_row(row)
                    cursor.execute(
                        """
                        UPDATE monitor_account_jobs
                        SET status = 'processing',
                            locked_by = %s,
                            locked_until = %s,
                            attempts = attempts + 1,
                            last_started_at = %s,
                            last_error = '',
                            updated_at = %s
                        WHERE account_id = %s
                        """,
                        (worker_id, locked_until, stamp, stamp, job.account_id),
                    )
                    if cursor.rowcount:
                        job.status = "processing"
                        job.locked_by = worker_id
                        job.locked_until = locked_until
                        job.attempts += 1
                        job.last_started_at = stamp
                        job.last_error = ""
                        job.updated_at = stamp
                        claimed.append(job)
        return claimed

    def mark_done(self, account_id: str, next_run_at: str) -> None:
        stamp = utc_now()
        with self.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE monitor_account_jobs
                    SET status = 'done',
                        next_run_at = %s,
                        locked_by = '',
                        locked_until = '',
                        attempts = 0,
                        last_finished_at = %s,
                        last_error = '',
                        updated_at = %s
                    WHERE account_id = %s
                    """,
                    (str(next_run_at or stamp), stamp, stamp, str(account_id or "")),
                )

    def mark_failed(self, account_id: str, error: str, next_run_at: str) -> None:
        stamp = utc_now()
        with self.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE monitor_account_jobs
                    SET status = 'failed',
                        next_run_at = %s,
                        locked_by = '',
                        locked_until = '',
                        last_finished_at = %s,
                        last_error = %s,
                        updated_at = %s
                    WHERE account_id = %s
                    """,
                    (str(next_run_at or stamp), stamp, str(error or "")[:500], stamp, str(account_id or "")),
                )

    def summary(self) -> Dict[str, object]:
        stamp = utc_now()
        with self.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT status, COUNT(*) AS count FROM monitor_account_jobs GROUP BY status")
                rows = cursor.fetchall()
                cursor.execute(
                    """
                    SELECT COUNT(*) AS count FROM monitor_account_jobs
                    WHERE (
                        status IN ('pending', 'done', 'failed')
                        AND (next_run_at = '' OR next_run_at <= %s)
                    ) OR (
                        status = 'processing'
                        AND COALESCE(NULLIF(locked_until, ''), next_run_at, updated_at) <= %s
                    )
                    """,
                    (stamp, stamp),
                )
                due = cursor.fetchone()
                cursor.execute("SELECT COUNT(*) AS count FROM monitor_account_jobs")
                total = cursor.fetchone()
        return {
            "backend": "mysql",
            "total": int((total or {}).get("count") or 0),
            "due": int((due or {}).get("count") or 0),
            "statuses": {row["status"]: int(row["count"] or 0) for row in rows},
        }
