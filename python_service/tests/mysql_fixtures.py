import atexit
import fcntl
import hashlib
import os
import tempfile
from pathlib import Path

from digital_twin.infrastructure.mysql_monitoring import (
    MySQLMonitorAccountJobStore,
    ensure_mysql_database_exists,
    forget_mysql_database,
)
from digital_twin.infrastructure.mysql_operational import (
    MySQLAccountRegistry,
    MySQLAppStore,
    MySQLEventLog,
    MySQLExternalSignalCache,
    MySQLMarketQuoteCache,
    MySQLModelReviewJobStore,
    MySQLMonitorStore,
    MySQLMonitoringCycleRecorder,
    MySQLNotificationJobStore,
    MySQLNotificationRuleStore,
    MySQLNotificationTemplateStore,
    MySQLOperationalConnection,
    MySQLOntologyQualitySampleStore,
    MySQLOntologyReasoningCursorStore,
    MySQLResearchEvidenceStore,
    MySQLRuntimeSettingsStore,
    MySQLSymbolUniverseStore,
)
from digital_twin.infrastructure.mysql_schema_tuning import mysql_partitioning_mode


_CREATED_TEST_DATABASES = {}
_HELD_TEST_DATABASE_LOCKS = {}
DEFAULT_TEST_DATABASE = "orbit_alpha_test"


def is_managed_test_database(database: object) -> bool:
    """Return whether a schema is owned by the local test fixture.

    The exact default name is deliberately accepted in addition to the
    historical worker/override prefix.  It lets a terminated test run leave
    at most one reusable schema instead of one schema per temporary directory.
    """
    name = str(database or "")
    return name == DEFAULT_TEST_DATABASE or name.startswith(DEFAULT_TEST_DATABASE + "_")


def mysql_test_database_config(settings):
    settings = settings if isinstance(settings, dict) else {}
    return {
        "host": settings.get("mysqlHost") or "127.0.0.1",
        "port": int(settings.get("mysqlPort") or 3306),
        "user": settings.get("mysqlUser") or "root",
        "password": settings.get("mysqlPassword") or "",
        "database": settings.get("mysqlDatabase") or "",
        "unix_socket": settings.get("mysqlUnixSocket") or "",
    }


def register_mysql_test_database(config) -> None:
    database = str((config or {}).get("database") or "")
    if is_managed_test_database(database):
        _CREATED_TEST_DATABASES[database] = dict(config)


def acquire_mysql_test_database_lock(config) -> None:
    """Serialize processes that reuse the bounded default test schema.

    Multiple Codex or CI sessions can run the suite at the same time. Without
    a process-wide lock they repeatedly drop the same schema underneath each
    other, causing both false failures and sustained MySQL DDL load. Explicit
    parallel workers use isolated bounded names and do not need this lock.
    """

    config = config if isinstance(config, dict) else {}
    if str(config.get("database") or "") != DEFAULT_TEST_DATABASE:
        return
    identity = "|".join([
        str(config.get("host") or "127.0.0.1"),
        str(config.get("port") or 3306),
        str(config.get("unix_socket") or ""),
        DEFAULT_TEST_DATABASE,
    ])
    if identity in _HELD_TEST_DATABASE_LOCKS:
        return
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    lock_path = Path(tempfile.gettempdir()) / ("orbit-alpha-mysql-test-" + digest + ".lock")
    handle = lock_path.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    _HELD_TEST_DATABASE_LOCKS[identity] = handle


def _test_database_cleanup() -> None:
    """Drop temporary test schemas left by a completed Python test process."""
    try:
        import pymysql
    except ImportError:
        return
    grouped = {}
    for database, config in list(_CREATED_TEST_DATABASES.items()):
        if not is_managed_test_database(database):
            continue
        key = (
            str(config.get("host") or ""),
            int(config.get("port") or 3306),
            str(config.get("user") or ""),
            str(config.get("password") or ""),
            str(config.get("unix_socket") or ""),
        )
        grouped.setdefault(key, {"config": config, "databases": []})["databases"].append(database)
    for group in grouped.values():
        config = group["config"]
        kwargs = {
            "host": config["host"],
            "port": int(config["port"] or 3306),
            "user": config["user"],
            "password": config["password"],
            "charset": "utf8mb4",
            "autocommit": True,
        }
        if config.get("unix_socket"):
            kwargs["unix_socket"] = config["unix_socket"]
        try:
            connection = pymysql.connect(**kwargs)
            try:
                with connection.cursor() as cursor:
                    for database in group["databases"]:
                        cursor.execute("DROP DATABASE IF EXISTS `" + database.replace("`", "``") + "`")
            finally:
                connection.close()
        except Exception:
            # A test failure should retain its original exit status. The next
            # maintenance run can remove any leftover isolated test schema.
            continue


atexit.register(_test_database_cleanup)


def _seed_value(seed=None) -> str:
    if seed:
        data_dir = os.environ.get("DIGITAL_TWIN_DATA_DIR")
        if data_dir:
            try:
                seed_path = Path(seed).resolve()
                data_path = Path(data_dir).resolve()
                if seed_path == data_path or seed_path.parent == data_path:
                    return str(data_path)
            except OSError:
                pass
        try:
            return str(Path(seed).resolve())
        except OSError:
            return str(seed)
    data_dir = os.environ.get("DIGITAL_TWIN_DATA_DIR")
    if data_dir:
        return str(Path(data_dir).resolve())
    settings_path = os.environ.get("SETTINGS_PATH")
    if settings_path:
        return str(Path(settings_path).resolve())
    return str(Path(os.getcwd()).resolve())


def test_database_name(seed=None) -> str:
    """Return a bounded reusable schema name for the current test worker.

    Earlier versions hashed every ``TemporaryDirectory`` seed, which created
    a new ``orbit_alpha_test_<hash>`` database for almost every test case.
    A normal suite is sequential, so it now reuses one schema and resets it
    before fixtures need isolation. Parallel runners may opt into a stable
    worker namespace without reintroducing a per-test schema leak.
    """
    worker = (
        os.environ.get("DIGITAL_TWIN_TEST_WORKER")
        or os.environ.get("PYTEST_XDIST_WORKER")
        or ""
    ).strip()
    if not worker:
        return DEFAULT_TEST_DATABASE
    digest = hashlib.sha1(worker.encode("utf-8")).hexdigest()[:12]
    return DEFAULT_TEST_DATABASE + "_worker_" + digest


def mysql_test_settings(seed=None):
    settings = {
        "mysqlHost": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "mysqlPort": os.environ.get("MYSQL_PORT", "3306"),
        "mysqlDatabase": os.environ.get("MYSQL_TEST_DATABASE") or test_database_name(seed),
        "mysqlUser": os.environ.get("MYSQL_USER", "root"),
        "mysqlPassword": os.environ.get("MYSQL_PASSWORD", ""),
        "mysqlUnixSocket": os.environ.get("MYSQL_UNIX_SOCKET", ""),
        "operationalHistoryRetentionEnabled": "0",
    }
    config = mysql_test_database_config(settings)
    acquire_mysql_test_database_lock(config)
    register_mysql_test_database(config)
    return settings


def reset_mysql_test_database(seed=None):
    settings = mysql_test_settings(seed)
    os.environ["MYSQL_DATABASE"] = settings["mysqlDatabase"]
    config = mysql_test_database_config(settings)
    import pymysql

    forget_mysql_database(config)
    kwargs = {
        "host": config["host"],
        "port": int(config["port"] or 3306),
        "user": config["user"],
        "password": config["password"],
        "charset": "utf8mb4",
        "autocommit": True,
    }
    if config.get("unix_socket"):
        kwargs["unix_socket"] = config["unix_socket"]
    connection = pymysql.connect(**kwargs)
    try:
        with connection.cursor() as cursor:
            database = config["database"].replace("`", "``")
            cursor.execute("DROP DATABASE IF EXISTS `" + database + "`")
    finally:
        connection.close()
    forget_mysql_database(config)
    schema_key = (
        str(config.get("host") or ""),
        str(config.get("port") or ""),
        str(config.get("database") or ""),
        str(config.get("unix_socket") or ""),
        mysql_partitioning_mode(settings),
    )
    MySQLOperationalConnection._schema_ready.discard(schema_key)
    MySQLOperationalConnection._retention_last_run.pop(schema_key, None)
    MySQLOperationalConnection._retention_last_warning.pop(schema_key, None)
    ensure_mysql_database_exists(config)
    register_mysql_test_database(config)
    return settings


def mysql_test_connection(seed=None):
    settings = mysql_test_settings(seed)
    config = mysql_test_database_config(settings)
    register_mysql_test_database(config)
    ensure_mysql_database_exists(config)
    import pymysql

    kwargs = {
        "host": config["host"],
        "port": int(config["port"] or 3306),
        "user": config["user"],
        "password": config["password"],
        "database": config["database"],
        "charset": "utf8mb4",
        "autocommit": True,
    }
    if config.get("unix_socket"):
        kwargs["unix_socket"] = config["unix_socket"]
    return pymysql.connect(**kwargs)


def _mysql_sql(sql: str) -> str:
    return sql.replace("?", "%s")


def mysql_execute(seed, sql: str, params=()):
    connection = mysql_test_connection(seed)
    try:
        with connection.cursor() as cursor:
            cursor.execute(_mysql_sql(sql), params)
            return cursor.rowcount
    finally:
        connection.close()


def mysql_fetchall(seed, sql: str, params=()):
    connection = mysql_test_connection(seed)
    try:
        with connection.cursor() as cursor:
            cursor.execute(_mysql_sql(sql), params)
            return cursor.fetchall()
    finally:
        connection.close()


def mysql_fetchone(seed, sql: str, params=()):
    rows = mysql_fetchall(seed, sql, params)
    return rows[0] if rows else None


def _settings(seed=None):
    return mysql_test_settings(seed)


class TestRuntimeSettingsStore(MySQLRuntimeSettingsStore):
    def __init__(self, seed=None, legacy_path=None):
        super().__init__(_settings(seed))


class TestAccountRegistry(MySQLAccountRegistry):
    def __init__(self, seed=None, legacy_path=None):
        super().__init__(_settings(seed))


class TestAppStore(MySQLAppStore):
    def __init__(self, seed=None, legacy_path=None):
        super().__init__(_settings(seed))


class TestExternalSignalCache(MySQLExternalSignalCache):
    def __init__(self, seed=None, legacy_path=None):
        super().__init__(_settings(seed))


class TestOntologyReasoningCursorStore(MySQLOntologyReasoningCursorStore):
    def __init__(self, seed=None, legacy_path=None):
        super().__init__(_settings(seed))


class TestMonitorStore(MySQLMonitorStore):
    def __init__(self, seed=None, legacy_path=None):
        super().__init__(_settings(seed))


class TestMonitoringCycleRecorder(MySQLMonitoringCycleRecorder):
    def __init__(self, seed=None, monitor_store=None, legacy_path=None):
        super().__init__(_settings(seed), monitor_store=monitor_store)


class TestEventLog(MySQLEventLog):
    def __init__(self, seed=None, legacy_path=None):
        super().__init__(_settings(seed))


class TestModelReviewJobStore(MySQLModelReviewJobStore):
    def __init__(self, seed=None, legacy_path=None):
        super().__init__(_settings(seed))


class TestNotificationJobStore(MySQLNotificationJobStore):
    def __init__(self, seed=None, legacy_path=None):
        super().__init__(_settings(seed))


class TestNotificationTemplateStore(MySQLNotificationTemplateStore):
    def __init__(self, seed=None, legacy_path=None):
        super().__init__(_settings(seed))


class TestNotificationRuleStore(MySQLNotificationRuleStore):
    def __init__(self, seed=None, legacy_path=None, seed_defaults=True):
        super().__init__(_settings(seed), seed_defaults=seed_defaults)


class TestMarketQuoteCache(MySQLMarketQuoteCache):
    def __init__(self, seed=None, legacy_path=None):
        super().__init__(_settings(seed))


class TestSymbolUniverseStore(MySQLSymbolUniverseStore):
    def __init__(self, seed=None, legacy_path=None):
        super().__init__(_settings(seed))


class TestResearchEvidenceStore(MySQLResearchEvidenceStore):
    def __init__(self, seed=None, legacy_path=None):
        super().__init__(_settings(seed))


class TestOntologyQualitySampleStore(MySQLOntologyQualitySampleStore):
    def __init__(self, seed=None, legacy_path=None):
        super().__init__(_settings(seed))


class TestMonitorAccountJobStore(MySQLMonitorAccountJobStore):
    def __init__(self, seed=None, legacy_path=None):
        super().__init__(_settings(seed))


def test_mysql_seed(temp_name: str) -> Path:
    return Path(temp_name) / "mysql-test-store"


test_store_seed = test_mysql_seed
