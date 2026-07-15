import hashlib
import os
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
    digest = hashlib.sha1(_seed_value(seed).encode("utf-8")).hexdigest()[:20]
    return "orbit_alpha_test_" + digest


def mysql_test_settings(seed=None):
    return {
        "operationalDbBackend": "mysql",
        "mysqlHost": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "mysqlPort": os.environ.get("MYSQL_PORT", "3306"),
        "mysqlDatabase": os.environ.get("MYSQL_TEST_DATABASE") or test_database_name(seed),
        "mysqlUser": os.environ.get("MYSQL_USER", "root"),
        "mysqlPassword": os.environ.get("MYSQL_PASSWORD", ""),
        "mysqlUnixSocket": os.environ.get("MYSQL_UNIX_SOCKET", ""),
        "operationalHistoryRetentionEnabled": "0",
    }


def reset_mysql_test_database(seed=None):
    settings = mysql_test_settings(seed)
    os.environ["MYSQL_DATABASE"] = settings["mysqlDatabase"]
    os.environ["OPERATIONAL_DB_BACKEND"] = "mysql"
    config = {
        "host": settings["mysqlHost"],
        "port": int(settings["mysqlPort"] or 3306),
        "user": settings["mysqlUser"],
        "password": settings["mysqlPassword"],
        "database": settings["mysqlDatabase"],
        "unix_socket": settings["mysqlUnixSocket"],
    }
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
    MySQLOperationalConnection._schema_ready.discard((
        str(config.get("host") or ""),
        str(config.get("port") or ""),
        str(config.get("database") or ""),
        str(config.get("unix_socket") or ""),
        mysql_partitioning_mode(settings),
    ))
    MySQLOperationalConnection._retention_last_run.pop((
        str(config.get("host") or ""),
        str(config.get("port") or ""),
        str(config.get("database") or ""),
        str(config.get("unix_socket") or ""),
        mysql_partitioning_mode(settings),
    ), None)
    ensure_mysql_database_exists(config)
    return settings


def mysql_test_connection(seed=None):
    settings = mysql_test_settings(seed)
    config = {
        "host": settings["mysqlHost"],
        "port": int(settings["mysqlPort"] or 3306),
        "user": settings["mysqlUser"],
        "password": settings["mysqlPassword"],
        "database": settings["mysqlDatabase"],
        "unix_socket": settings["mysqlUnixSocket"],
    }
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


def test_store_seed(temp_name: str) -> Path:
    return Path(temp_name) / "mysql-test-store"
