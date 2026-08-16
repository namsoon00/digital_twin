from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import random
import time
from typing import Callable, Dict, Tuple
import warnings

from .mysql_monitoring import MySQLDependencyError, ensure_mysql_database_exists, mysql_settings
from .mysql_retention import (
    apply_mysql_operational_history_retention,
    operational_history_retention_check_interval_seconds,
    operational_history_retention_enabled,
)
from .mysql_schema_tuning import ensure_mysql_operational_schema_tuning, mysql_partitioning_mode
from .mysql_connection_pool import pooled_mysql_connection


_FALSEY_VALUES = {"", "0", "false", "no", "off", "disabled"}
MYSQL_DEADLOCK_ERROR_CODE = 1213
MYSQL_CONNECTION_LOST_ERROR_CODES = frozenset({2006, 2013, 2055})


@dataclass(frozen=True)
class MySQLDeadlockRetryReceipt:
    operation: str
    attempts: int
    retry_count: int
    recovered: bool
    delays_ms: Tuple[int, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "operation": self.operation,
            "attempts": self.attempts,
            "retryCount": self.retry_count,
            "recovered": self.recovered,
            "delaysMs": list(self.delays_ms),
        }


class MySQLDeadlockRetryExhausted(RuntimeError):
    def __init__(self, operation: str, receipt: MySQLDeadlockRetryReceipt, error: Exception):
        self.operation = str(operation or "mysql-transaction")
        self.receipt = receipt
        self.error = error
        super().__init__(
            "MySQL deadlock retries exhausted"
            + " operation=" + self.operation
            + " attempts=" + str(receipt.attempts)
            + " error=" + str(error)
        )


def mysql_error_code(error: Exception) -> int:
    for value in list(getattr(error, "args", ()) or []):
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def mysql_is_deadlock(error: Exception) -> bool:
    return mysql_error_code(error) == MYSQL_DEADLOCK_ERROR_CODE


def mysql_is_connection_lost(error: Exception) -> bool:
    """Whether retrying a complete idempotent maintenance pass is safe."""
    return mysql_error_code(error) in MYSQL_CONNECTION_LOST_ERROR_CODES


def mysql_deadlock_retry_count(settings: Dict[str, object] = None) -> int:
    try:
        parsed = int(float(str((settings or {}).get("mysqlDeadlockRetryCount") or "3").strip()))
    except (TypeError, ValueError):
        parsed = 3
    return max(0, min(8, parsed))


def mysql_deadlock_retry_base_milliseconds(settings: Dict[str, object] = None) -> int:
    try:
        parsed = int(float(str((settings or {}).get("mysqlDeadlockRetryBaseMilliseconds") or "30").strip()))
    except (TypeError, ValueError):
        parsed = 30
    return max(1, min(1000, parsed))


def mysql_deadlock_retry_max_milliseconds(settings: Dict[str, object] = None) -> int:
    try:
        parsed = int(float(str((settings or {}).get("mysqlDeadlockRetryMaxMilliseconds") or "250").strip()))
    except (TypeError, ValueError):
        parsed = 250
    return max(mysql_deadlock_retry_base_milliseconds(settings), min(5000, parsed))


def mysql_deadlock_retry_delay_milliseconds(
    settings: Dict[str, object],
    retry_number: int,
    random_fn: Callable[[], float] = None,
) -> int:
    base = mysql_deadlock_retry_base_milliseconds(settings)
    maximum = mysql_deadlock_retry_max_milliseconds(settings)
    exponential = min(maximum, base * (2 ** max(0, int(retry_number or 1) - 1)))
    try:
        random_value = float((random_fn or random.random)())
    except (TypeError, ValueError):
        random_value = 0.5
    jitter = 0.75 + min(1.0, max(0.0, random_value)) * 0.5
    return max(1, int(round(exponential * jitter)))


def run_mysql_deadlock_retry(
    settings: Dict[str, object],
    operation: str,
    callback: Callable[[], object],
    sleep_fn: Callable[[float], None] = None,
    random_fn: Callable[[], float] = None,
):
    """Retry a complete, idempotent transaction after MySQL error 1213 only."""
    retry_count = mysql_deadlock_retry_count(settings)
    attempts = 0
    delays = []
    while True:
        attempts += 1
        try:
            value = callback()
            return value, MySQLDeadlockRetryReceipt(
                operation=str(operation or "mysql-transaction"),
                attempts=attempts,
                retry_count=max(0, attempts - 1),
                recovered=bool(delays),
                delays_ms=tuple(delays),
            )
        except Exception as error:
            if not mysql_is_deadlock(error):
                raise
            if attempts > retry_count:
                receipt = MySQLDeadlockRetryReceipt(
                    operation=str(operation or "mysql-transaction"),
                    attempts=attempts,
                    retry_count=max(0, attempts - 1),
                    recovered=False,
                    delays_ms=tuple(delays),
                )
                raise MySQLDeadlockRetryExhausted(operation, receipt, error) from error
            delay_ms = mysql_deadlock_retry_delay_milliseconds(settings, attempts, random_fn=random_fn)
            delays.append(delay_ms)
            (sleep_fn or time.sleep)(delay_ms / 1000.0)


def mysql_operational_schema_bootstrap_enabled(settings: Dict[str, str] = None) -> bool:
    """Whether this connection may run the full schema bootstrap.

    Short-lived, isolated workers already run behind the service manager's
    schema bootstrap. Repeating DDL and index checks in every child adds
    avoidable startup work to the realtime reasoning path.
    """
    value = str((settings or {}).get("_skipOperationalSchemaBootstrap") or "").strip().lower()
    return value in _FALSEY_VALUES


def mysql_operational_constructor_retention_enabled(settings: Dict[str, str] = None) -> bool:
    """Keep destructive-ish history cleanup out of ordinary store creation.

    Retention is now owned by the dedicated low-priority maintenance worker.
    The opt-in flag remains for a one-off tool that intentionally needs the
    old constructor behaviour.
    """
    value = str((settings or {}).get("_runOperationalHistoryRetentionOnInit") or "").strip().lower()
    return value not in _FALSEY_VALUES


def mysql_operation_timeout_seconds(settings: Dict[str, str]) -> int:
    try:
        parsed = int(float(str((settings or {}).get("mysqlOperationTimeoutSeconds") or "").strip()))
    except ValueError:
        parsed = 10
    return max(1, min(120, parsed))


class MySQLConnectionProxy:
    def __init__(self, connection, release=None):
        self.connection = connection
        self.release = release
        self.closed = False

    def execute(self, sql: str, params=None):
        cursor = self.connection.cursor()
        cursor.execute(sql, params or ())
        return cursor

    def executemany(self, sql: str, rows):
        cursor = self.connection.cursor()
        cursor.executemany(sql, list(rows or []))
        return cursor

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.release:
            self.release(self.connection)
        else:
            self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

class MySQLOperationalConnection:
    _schema_ready = set()
    _retention_last_run = {}
    _retention_last_warning = {}

    def __init__(self, settings: Dict[str, str] = None):
        self.runtime_settings = dict(settings or {})
        self.mysql_config = mysql_settings(settings)
        self.last_transaction_retry: Dict[str, object] = {}
        ensure_mysql_database_exists(self.mysql_config)
        if mysql_operational_schema_bootstrap_enabled(self.runtime_settings):
            self.ensure_schema()
        if mysql_operational_constructor_retention_enabled(self.runtime_settings):
            self.ensure_history_retention()

    def raw_connection(self, autocommit: bool = True):
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as error:
            raise MySQLDependencyError("MySQL backend requires pymysql. Install with: python3 -m pip install pymysql") from error
        timeout_seconds = mysql_operation_timeout_seconds(self.runtime_settings)
        kwargs = {
            "host": self.mysql_config["host"],
            "port": int(self.mysql_config["port"] or 3306),
            "user": self.mysql_config["user"],
            "password": self.mysql_config["password"],
            "database": self.mysql_config["database"],
            "charset": "utf8mb4",
            "cursorclass": DictCursor,
            "autocommit": autocommit,
            "connect_timeout": timeout_seconds,
            "read_timeout": timeout_seconds,
            "write_timeout": timeout_seconds,
        }
        if self.mysql_config.get("unix_socket"):
            kwargs["unix_socket"] = self.mysql_config["unix_socket"]
        return pymysql.connect(**kwargs)

    def pooled_connection(self, autocommit: bool = True):
        timeout_seconds = mysql_operation_timeout_seconds(self.runtime_settings)
        key = (
            "orbit-alpha-operational",
            str(self.mysql_config.get("host") or ""),
            int(self.mysql_config.get("port") or 3306),
            str(self.mysql_config.get("user") or ""),
            str(self.mysql_config.get("database") or ""),
            str(self.mysql_config.get("unix_socket") or ""),
            timeout_seconds,
        )
        return pooled_mysql_connection(
            key,
            self.raw_connection,
            autocommit=autocommit,
            settings=self.runtime_settings,
        )

    def connect(self):
        connection, release = self.pooled_connection(autocommit=True)
        return MySQLConnectionProxy(connection, release=release)

    @contextmanager
    def transaction(self):
        connection, release = self.pooled_connection(autocommit=False)
        proxy = MySQLConnectionProxy(connection, release=release)
        try:
            yield proxy
            proxy.commit()
        except Exception:
            try:
                proxy.rollback()
            except Exception:
                # A read/write timeout can already have closed the socket.
                # Preserve the original database exception for retry and
                # diagnostics instead of replacing it with rollback failure.
                pass
            raise
        finally:
            proxy.close()

    def transaction_with_deadlock_retry(self, operation: str, callback: Callable[[MySQLConnectionProxy], object]):
        """Run one database mutation in a fresh transaction on every retry.

        A deadlock rolls back the complete InnoDB transaction. Retrying an
        individual statement would leave the mutation and its outbox event out
        of sync, so the callback always owns the whole atomic unit.
        """
        def invoke():
            with self.transaction() as connection:
                return callback(connection)

        try:
            result, receipt = run_mysql_deadlock_retry(
                self.runtime_settings,
                operation,
                invoke,
            )
        except MySQLDeadlockRetryExhausted as error:
            self.last_transaction_retry = error.receipt.to_dict()
            raise
        self.last_transaction_retry = receipt.to_dict()
        return result

    def schema_key(self):
        return (
            str(self.mysql_config.get("host") or ""),
            str(self.mysql_config.get("port") or ""),
            str(self.mysql_config.get("database") or ""),
            str(self.mysql_config.get("unix_socket") or ""),
            mysql_partitioning_mode(self.runtime_settings),
        )

    def ensure_schema(self) -> None:
        schema_key = self.schema_key()
        if schema_key in MySQLOperationalConnection._schema_ready:
            return
        with self.transaction() as connection:
            for statement in MYSQL_SCHEMA:
                connection.execute(statement)
            ensure_mysql_operational_schema_tuning(connection, self.runtime_settings)
        MySQLOperationalConnection._schema_ready.add(schema_key)

    def ensure_history_retention(self) -> None:
        if self.runtime_settings.get("_skipOperationalHistoryRetention"):
            return
        if not operational_history_retention_enabled(self.runtime_settings):
            return
        schema_key = self.schema_key()
        now = datetime.now(timezone.utc)
        last_run = MySQLOperationalConnection._retention_last_run.get(schema_key)
        min_interval = operational_history_retention_check_interval_seconds(self.runtime_settings)
        if last_run and (now - last_run).total_seconds() < min_interval:
            return
        MySQLOperationalConnection._retention_last_run[schema_key] = now
        try:
            with self.connect() as connection:
                apply_mysql_operational_history_retention(connection, self.runtime_settings, now=now)
        except Exception as error:
            if self.should_warn_retention_failure(schema_key, now):
                warnings.warn(
                    "MySQL operational history retention skipped: " + str(error),
                    RuntimeWarning,
                    stacklevel=2,
                )
            return

    def should_warn_retention_failure(self, schema_key, now: datetime) -> bool:
        try:
            interval = int(float(str(self.runtime_settings.get("operationalHistoryRetentionWarningIntervalSeconds") or "3600").strip()))
        except ValueError:
            interval = 3600
        interval = max(60, min(24 * 3600, interval))
        last_warning = MySQLOperationalConnection._retention_last_warning.get(schema_key)
        if last_warning and (now - last_warning).total_seconds() < interval:
            return False
        MySQLOperationalConnection._retention_last_warning[schema_key] = now
        return True

MYSQL_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS service_accounts (
        id VARCHAR(191) PRIMARY KEY,
        label VARCHAR(255) NOT NULL,
        provider VARCHAR(64) NOT NULL DEFAULT 'toss',
        enabled TINYINT NOT NULL DEFAULT 1,
        watchlist_symbols TEXT NOT NULL,
        quiet_hours_enabled TINYINT NOT NULL DEFAULT 1,
        quiet_hours_start VARCHAR(16) NOT NULL DEFAULT '22:00',
        quiet_hours_end VARCHAR(16) NOT NULL DEFAULT '05:00',
        quiet_hours_timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Seoul',
        message_delivery_level VARCHAR(64) NOT NULL DEFAULT 'absoluteBeginner',
        notification_detail_level VARCHAR(32) NOT NULL DEFAULT 'concise',
        investment_strategy_profile VARCHAR(64) NOT NULL DEFAULT 'balanced',
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_service_accounts_enabled (enabled)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS account_watchlist_symbols (
        account_id VARCHAR(191) NOT NULL,
        symbol VARCHAR(64) NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (account_id, symbol),
        KEY idx_account_watchlist_symbols_updated (account_id, updated_at, symbol)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS toss_credentials (
        account_id VARCHAR(191) PRIMARY KEY,
        base_url TEXT NOT NULL,
        client_id TEXT NOT NULL,
        client_secret TEXT NOT NULL,
        account_seq TEXT NOT NULL,
        updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_configs (
        account_id VARCHAR(191) PRIMARY KEY,
        notify_provider VARCHAR(64) NOT NULL DEFAULT '',
        bot_token TEXT NOT NULL,
        chat_id TEXT NOT NULL,
        link_url TEXT NOT NULL,
        updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_settings (
        `key` VARCHAR(191) PRIMARY KEY,
        value LONGTEXT NOT NULL,
        updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS app_store (
        store_id VARCHAR(191) PRIMARY KEY,
        payload_json LONGTEXT NOT NULL,
        updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS external_dataset_state (
        dataset_id VARCHAR(191) NOT NULL,
        partition_key VARCHAR(191) NOT NULL,
        provider_id VARCHAR(96) NOT NULL,
        subject_json TEXT NOT NULL,
        watermark_json TEXT NOT NULL,
        priority INT NOT NULL DEFAULT 50,
        active TINYINT NOT NULL DEFAULT 1,
        job_status VARCHAR(32) NOT NULL DEFAULT 'pending',
        next_due_at VARCHAR(40) NOT NULL,
        last_attempt_at VARCHAR(40) NOT NULL DEFAULT '',
        last_success_at VARCHAR(40) NOT NULL DEFAULT '',
        source_as_of VARCHAR(80) NOT NULL DEFAULT '',
        lease_owner VARCHAR(191) NOT NULL DEFAULT '',
        lease_until VARCHAR(40) NOT NULL DEFAULT '',
        attempt_count INT NOT NULL DEFAULT 0,
        consecutive_failures INT NOT NULL DEFAULT 0,
        last_error VARCHAR(500) NOT NULL DEFAULT '',
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (dataset_id, partition_key),
        KEY idx_external_dataset_due (active, job_status, next_due_at, priority),
        KEY idx_external_dataset_provider (provider_id, job_status, next_due_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS external_fact_current (
        dataset_id VARCHAR(191) NOT NULL,
        subject_key VARCHAR(191) NOT NULL,
        provider_id VARCHAR(96) NOT NULL,
        source_revision VARCHAR(191) NOT NULL,
        payload_hash CHAR(64) NOT NULL,
        source_as_of VARCHAR(80) NOT NULL DEFAULT '',
        fetched_at VARCHAR(40) NOT NULL,
        expires_at VARCHAR(40) NOT NULL,
        payload_json LONGTEXT NOT NULL,
        quality_json TEXT NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (dataset_id, subject_key),
        KEY idx_external_fact_subject (subject_key, dataset_id),
        KEY idx_external_fact_freshness (expires_at, dataset_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS external_fact_revision (
        revision_id VARCHAR(191) PRIMARY KEY,
        dataset_id VARCHAR(191) NOT NULL,
        subject_key VARCHAR(191) NOT NULL,
        provider_id VARCHAR(96) NOT NULL,
        source_revision VARCHAR(191) NOT NULL,
        payload_hash CHAR(64) NOT NULL,
        source_as_of VARCHAR(80) NOT NULL DEFAULT '',
        fetched_at VARCHAR(40) NOT NULL,
        payload_json LONGTEXT NOT NULL,
        quality_json TEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_external_fact_source_revision (dataset_id, subject_key, source_revision),
        KEY idx_external_fact_revision_subject (subject_key, dataset_id, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS external_provider_state (
        provider_id VARCHAR(96) NOT NULL,
        bucket_id VARCHAR(191) NOT NULL,
        window_date VARCHAR(16) NOT NULL DEFAULT '',
        request_count INT NOT NULL DEFAULT 0,
        next_allowed_at VARCHAR(40) NOT NULL DEFAULT '',
        circuit_open_until VARCHAR(40) NOT NULL DEFAULT '',
        consecutive_failures INT NOT NULL DEFAULT 0,
        health_state VARCHAR(32) NOT NULL DEFAULT 'unknown',
        last_attempt_at VARCHAR(40) NOT NULL DEFAULT '',
        last_success_at VARCHAR(40) NOT NULL DEFAULT '',
        last_error VARCHAR(500) NOT NULL DEFAULT '',
        updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (provider_id, bucket_id),
        KEY idx_external_provider_health (health_state, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS external_collection_runs (
        run_id VARCHAR(191) PRIMARY KEY,
        dataset_id VARCHAR(191) NOT NULL,
        partition_key VARCHAR(191) NOT NULL,
        provider_id VARCHAR(96) NOT NULL,
        worker_id VARCHAR(191) NOT NULL DEFAULT '',
        run_status VARCHAR(32) NOT NULL,
        started_at VARCHAR(40) NOT NULL,
        completed_at VARCHAR(40) NOT NULL,
        duration_ms INT NOT NULL DEFAULT 0,
        response_bytes BIGINT NOT NULL DEFAULT 0,
        source_as_of VARCHAR(80) NOT NULL DEFAULT '',
        source_revision VARCHAR(191) NOT NULL DEFAULT '',
        material_change TINYINT NOT NULL DEFAULT 0,
        error_message VARCHAR(500) NOT NULL DEFAULT '',
        created_at VARCHAR(40) NOT NULL,
        KEY idx_external_collection_dataset_time (dataset_id, completed_at),
        KEY idx_external_collection_provider_time (provider_id, completed_at),
        KEY idx_external_collection_status_time (run_status, completed_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS domain_events (
        event_id VARCHAR(191) PRIMARY KEY,
        name VARCHAR(191) NOT NULL,
        aggregate_id VARCHAR(191) NOT NULL DEFAULT '',
        occurred_at VARCHAR(40) NOT NULL,
        correlation_id VARCHAR(191) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        event_json LONGTEXT NOT NULL,
        KEY idx_domain_events_name_time (name, occurred_at),
        KEY idx_domain_events_aggregate_time (aggregate_id, occurred_at, event_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_mandates (
        mandate_id VARCHAR(191) PRIMARY KEY,
        portfolio_id VARCHAR(191) NOT NULL,
        account_id VARCHAR(191) NOT NULL,
        policy_version VARCHAR(191) NOT NULL,
        profile VARCHAR(64) NOT NULL DEFAULT 'balanced',
        status VARCHAR(32) NOT NULL DEFAULT 'active',
        effective_at VARCHAR(40) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_investment_mandates_account_status (account_id, status, updated_at),
        KEY idx_investment_mandates_portfolio_version (portfolio_id, policy_version)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_mandate_versions (
        mandate_version_id VARCHAR(191) PRIMARY KEY,
        mandate_id VARCHAR(191) NOT NULL,
        portfolio_id VARCHAR(191) NOT NULL,
        account_id VARCHAR(191) NOT NULL,
        policy_version VARCHAR(191) NOT NULL,
        profile VARCHAR(64) NOT NULL DEFAULT 'balanced',
        effective_at VARCHAR(40) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_investment_mandate_version (portfolio_id, policy_version),
        KEY idx_investment_mandate_history (portfolio_id, created_at, mandate_version_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_ledger_entries (
        entry_id VARCHAR(191) PRIMARY KEY,
        idempotency_key VARCHAR(255) NOT NULL,
        portfolio_id VARCHAR(191) NOT NULL,
        account_id VARCHAR(191) NOT NULL,
        entry_type VARCHAR(64) NOT NULL,
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        currency VARCHAR(16) NOT NULL DEFAULT 'KRW',
        quantity DECIMAL(36,12) NOT NULL DEFAULT 0,
        unit_price DECIMAL(36,12) NOT NULL DEFAULT 0,
        amount DECIMAL(36,12) NOT NULL DEFAULT 0,
        fee DECIMAL(36,12) NOT NULL DEFAULT 0,
        occurred_at VARCHAR(40) NOT NULL,
        source_reference VARCHAR(255) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_portfolio_ledger_idempotency (portfolio_id, idempotency_key),
        KEY idx_portfolio_ledger_replay (portfolio_id, occurred_at, entry_id),
        KEY idx_portfolio_ledger_account_symbol (account_id, symbol, occurred_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_snapshot_checkpoints (
        portfolio_id VARCHAR(191) PRIMARY KEY,
        account_id VARCHAR(191) NOT NULL,
        account_fingerprint VARCHAR(191) NOT NULL,
        observed_at VARCHAR(40) NOT NULL,
        balance_fingerprint VARCHAR(64) NOT NULL,
        checkpoint_version BIGINT NOT NULL DEFAULT 0,
        position_count INT NOT NULL DEFAULT 0,
        status VARCHAR(32) NOT NULL DEFAULT 'accepted',
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_portfolio_checkpoint_account_time (account_id, observed_at),
        KEY idx_portfolio_checkpoint_fingerprint (balance_fingerprint)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_activity_episodes (
        episode_id VARCHAR(191) PRIMARY KEY,
        portfolio_id VARCHAR(191) NOT NULL,
        account_id VARCHAR(191) NOT NULL,
        classification VARCHAR(64) NOT NULL,
        confidence VARCHAR(32) NOT NULL DEFAULT 'low',
        observed_at VARCHAR(40) NOT NULL,
        observation_fingerprint VARCHAR(64) NOT NULL,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_portfolio_activity_observation (portfolio_id, observation_fingerprint, observed_at),
        KEY idx_portfolio_activity_latest (portfolio_id, observed_at, episode_id),
        KEY idx_portfolio_activity_account_class (account_id, classification, observed_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_snapshot_quarantines (
        quarantine_id VARCHAR(191) PRIMARY KEY,
        portfolio_id VARCHAR(191) NOT NULL,
        account_id VARCHAR(191) NOT NULL,
        reason VARCHAR(96) NOT NULL,
        observed_at VARCHAR(40) NOT NULL,
        balance_fingerprint VARCHAR(64) NOT NULL,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_portfolio_snapshot_quarantine (portfolio_id, balance_fingerprint, observed_at, reason),
        KEY idx_portfolio_snapshot_quarantine_latest (portfolio_id, observed_at, quarantine_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_state_snapshots (
        state_id VARCHAR(191) PRIMARY KEY,
        portfolio_id VARCHAR(191) NOT NULL,
        account_id VARCHAR(191) NOT NULL,
        observed_at VARCHAR(40) NOT NULL,
        source_checkpoint_version BIGINT NOT NULL DEFAULT 0,
        position_count INT NOT NULL DEFAULT 0,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_portfolio_state_checkpoint (portfolio_id, source_checkpoint_version),
        KEY idx_portfolio_state_latest (portfolio_id, observed_at, state_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_decision_action_observations (
        observation_id VARCHAR(191) PRIMARY KEY,
        portfolio_id VARCHAR(191) NOT NULL,
        account_id VARCHAR(191) NOT NULL,
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        activity_episode_id VARCHAR(191) NOT NULL,
        prior_decision_episode_id VARCHAR(191) NOT NULL DEFAULT '',
        correspondence VARCHAR(32) NOT NULL DEFAULT 'unclassified',
        observed_at VARCHAR(40) NOT NULL,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_decision_activity_pair (activity_episode_id, prior_decision_episode_id, symbol),
        KEY idx_decision_action_account_symbol (account_id, symbol, observed_at),
        KEY idx_decision_action_prior_subject (prior_decision_episode_id, account_id, symbol, observed_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_reconciliations (
        reconciliation_id VARCHAR(191) PRIMARY KEY,
        portfolio_id VARCHAR(191) NOT NULL,
        account_id VARCHAR(191) NOT NULL,
        balance_fingerprint VARCHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'matched',
        difference_count INT NOT NULL DEFAULT 0,
        source_snapshot_at VARCHAR(40) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_portfolio_reconciliation_balance (portfolio_id, balance_fingerprint),
        KEY idx_portfolio_reconciliation_status (portfolio_id, status, source_snapshot_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_exposure_snapshots (
        exposure_snapshot_id VARCHAR(191) PRIMARY KEY,
        portfolio_id VARCHAR(191) NOT NULL,
        observed_at VARCHAR(40) NOT NULL DEFAULT '',
        over_policy_count INT NOT NULL DEFAULT 0,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        KEY idx_portfolio_exposure_latest (portfolio_id, observed_at, exposure_snapshot_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_risk_snapshots (
        risk_snapshot_id VARCHAR(191) PRIMARY KEY,
        portfolio_id VARCHAR(191) NOT NULL,
        observed_at VARCHAR(40) NOT NULL DEFAULT '',
        data_state VARCHAR(32) NOT NULL DEFAULT 'partial',
        sample_count INT NOT NULL DEFAULT 0,
        annualized_volatility_pct DECIMAL(16,6) NOT NULL DEFAULT 0,
        maximum_drawdown_pct DECIMAL(16,6) NOT NULL DEFAULT 0,
        maximum_pairwise_correlation DECIMAL(16,6) NOT NULL DEFAULT 0,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_portfolio_risk_latest (portfolio_id, observed_at, risk_snapshot_id),
        KEY idx_portfolio_risk_state (portfolio_id, data_state, observed_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_decision_cycles (
        cycle_id VARCHAR(191) PRIMARY KEY,
        portfolio_id VARCHAR(191) NOT NULL,
        account_id VARCHAR(191) NOT NULL,
        policy_version VARCHAR(191) NOT NULL,
        source_snapshot_id VARCHAR(191) NOT NULL,
        candidate_fingerprint VARCHAR(64) NOT NULL,
        data_state VARCHAR(32) NOT NULL DEFAULT 'partial',
        candidate_count INT NOT NULL DEFAULT 0,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_portfolio_decision_cycle_source (portfolio_id, source_snapshot_id, policy_version),
        KEY idx_portfolio_decision_cycle_latest (portfolio_id, created_at, cycle_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_rebalance_proposals (
        proposal_id VARCHAR(191) PRIMARY KEY,
        portfolio_id VARCHAR(191) NOT NULL,
        mandate_version VARCHAR(191) NOT NULL DEFAULT '',
        exposure_snapshot_id VARCHAR(191) NOT NULL DEFAULT '',
        status VARCHAR(32) NOT NULL DEFAULT 'review-required',
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_rebalance_proposals_portfolio_status (portfolio_id, status, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_rebalance_states (
        portfolio_id VARCHAR(191) PRIMARY KEY,
        policy_version VARCHAR(191) NOT NULL DEFAULT '',
        status VARCHAR(32) NOT NULL DEFAULT 'WITHIN_POLICY',
        semantic_fingerprint VARCHAR(191) NOT NULL DEFAULT '',
        revision VARCHAR(191) NOT NULL DEFAULT '',
        transition_type VARCHAR(32) NOT NULL DEFAULT '',
        observed_at VARCHAR(40) NOT NULL DEFAULT '',
        current_payload_json LONGTEXT NOT NULL,
        event_payload_json LONGTEXT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_rebalance_states_status (status, observed_at),
        KEY idx_rebalance_states_revision (revision)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_rebalance_review_windows (
        portfolio_id VARCHAR(191) NOT NULL,
        review_window VARCHAR(80) NOT NULL,
        observed_at VARCHAR(40) NOT NULL DEFAULT '',
        source_event_id VARCHAR(191) NOT NULL DEFAULT '',
        reasoning_event_id VARCHAR(191) NOT NULL DEFAULT '',
        created_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (portfolio_id, review_window),
        KEY idx_rebalance_review_created (created_at, portfolio_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_action_plans (
        plan_id VARCHAR(191) PRIMARY KEY,
        portfolio_id VARCHAR(191) NOT NULL,
        decision_episode_id VARCHAR(191) NOT NULL,
        policy_version VARCHAR(191) NOT NULL DEFAULT '',
        inference_generation_id VARCHAR(191) NOT NULL DEFAULT '',
        action VARCHAR(32) NOT NULL DEFAULT 'HOLD',
        status VARCHAR(32) NOT NULL DEFAULT 'review-required',
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_action_plans_decision (decision_episode_id, created_at),
        KEY idx_action_plans_portfolio_status (portfolio_id, status, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_action_plan_reviews (
        review_id VARCHAR(191) PRIMARY KEY,
        plan_id VARCHAR(191) NOT NULL,
        decision VARCHAR(32) NOT NULL,
        reviewer VARCHAR(191) NOT NULL DEFAULT 'local-user',
        policy_version VARCHAR(191) NOT NULL DEFAULT '',
        reason VARCHAR(1000) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        reviewed_at VARCHAR(40) NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        KEY idx_action_plan_reviews_plan_time (plan_id, reviewed_at, review_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS trade_execution_episodes (
        execution_episode_id VARCHAR(191) PRIMARY KEY,
        action_plan_id VARCHAR(191) NOT NULL,
        portfolio_id VARCHAR(191) NOT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        payload_json LONGTEXT NOT NULL,
        started_at VARCHAR(40) NOT NULL DEFAULT '',
        completed_at VARCHAR(40) NOT NULL DEFAULT '',
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_execution_episodes_plan (action_plan_id, created_at),
        KEY idx_execution_episodes_portfolio_status (portfolio_id, status, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS trade_execution_fills (
        fill_id VARCHAR(191) PRIMARY KEY,
        provider_execution_id VARCHAR(191) NOT NULL,
        execution_episode_id VARCHAR(191) NOT NULL,
        order_intent_id VARCHAR(191) NOT NULL DEFAULT '',
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        side VARCHAR(8) NOT NULL,
        quantity DECIMAL(36,12) NOT NULL DEFAULT 0,
        price DECIMAL(36,12) NOT NULL DEFAULT 0,
        fee DECIMAL(36,12) NOT NULL DEFAULT 0,
        currency VARCHAR(16) NOT NULL DEFAULT 'KRW',
        executed_at VARCHAR(40) NOT NULL,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_trade_fill_provider_execution (provider_execution_id),
        KEY idx_trade_fills_episode_time (execution_episode_id, executed_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_decision_reviews (
        review_id VARCHAR(191) PRIMARY KEY,
        decision_episode_id VARCHAR(191) NOT NULL,
        selected_hypothesis_status VARCHAR(64) NOT NULL DEFAULT 'pending',
        policy_compliant TINYINT(1) NOT NULL DEFAULT 0,
        execution_compliant TINYINT(1) NOT NULL DEFAULT 0,
        evidence_still_valid TINYINT(1) NOT NULL DEFAULT 0,
        payload_json LONGTEXT NOT NULL,
        reviewed_at VARCHAR(40) NOT NULL DEFAULT '',
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_decision_reviews_episode_time (decision_episode_id, reviewed_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_performance_attributions (
        attribution_id VARCHAR(191) PRIMARY KEY,
        decision_episode_id VARCHAR(191) NOT NULL,
        action_plan_id VARCHAR(191) NOT NULL DEFAULT '',
        execution_episode_id VARCHAR(191) NOT NULL DEFAULT '',
        market_return_pct DECIMAL(18,8) NOT NULL DEFAULT 0,
        instrument_return_pct DECIMAL(18,8) NOT NULL DEFAULT 0,
        active_return_pct DECIMAL(18,8) NOT NULL DEFAULT 0,
        execution_cost DECIMAL(36,12) NOT NULL DEFAULT 0,
        realized_profit_loss DECIMAL(36,12) NOT NULL DEFAULT 0,
        currency_effect_pct DECIMAL(18,8) NOT NULL DEFAULT 0,
        payload_json LONGTEXT NOT NULL,
        observed_at VARCHAR(40) NOT NULL DEFAULT '',
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_performance_attribution_decision (decision_episode_id, observed_at),
        KEY idx_performance_attribution_execution (execution_episode_id, observed_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS monitor_snapshots (
        account_id VARCHAR(191) PRIMARY KEY,
        account_label VARCHAR(255) NOT NULL DEFAULT '',
        provider VARCHAR(64) NOT NULL DEFAULT '',
        mode VARCHAR(64) NOT NULL DEFAULT '',
        status VARCHAR(255) NOT NULL DEFAULT '',
        generated_at VARCHAR(40) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS monitor_snapshot_reasoning_inputs (
        account_id VARCHAR(191) NOT NULL,
        generated_at VARCHAR(40) NOT NULL DEFAULT '',
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (account_id, symbol),
        KEY idx_monitor_reasoning_inputs_account_generation (account_id, generated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS market_observation_reasoning_anchors (
        account_id VARCHAR(191) NOT NULL,
        symbol VARCHAR(64) NOT NULL,
        completed_price DECIMAL(24,8) NOT NULL DEFAULT 0,
        completed_at VARCHAR(40) NOT NULL DEFAULT '',
        pending_price DECIMAL(24,8) NOT NULL DEFAULT 0,
        pending_event_id VARCHAR(191) NOT NULL DEFAULT '',
        pending_at VARCHAR(40) NOT NULL DEFAULT '',
        updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (account_id, symbol),
        KEY idx_market_observation_anchor_pending (pending_event_id, account_id, symbol)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS monitor_snapshot_history (
        account_id VARCHAR(191) NOT NULL,
        generated_at VARCHAR(40) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        projection_payload_json LONGTEXT NULL,
        created_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (account_id, generated_at),
        KEY idx_monitor_snapshot_history_account_time (account_id, generated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS monitor_sent (
        sent_key_hash CHAR(64) PRIMARY KEY,
        sent_key TEXT NOT NULL,
        sent_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_templates (
        message_type VARCHAR(191) PRIMARY KEY,
        template LONGTEXT NOT NULL,
        description TEXT NOT NULL,
        enabled TINYINT NOT NULL DEFAULT 1,
        updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_rules (
        message_type VARCHAR(191) PRIMARY KEY,
        enabled TINYINT NOT NULL DEFAULT 1,
        conditions_json LONGTEXT NOT NULL,
        similarity_enabled TINYINT NOT NULL DEFAULT 1,
        similarity_window_minutes INT NOT NULL DEFAULT 60,
        similarity_bypass_conditions_json LONGTEXT NOT NULL,
        similarity_fields_json LONGTEXT NOT NULL,
        state_cooldown_enabled TINYINT NOT NULL DEFAULT 0,
        state_cooldown_minutes INT NOT NULL DEFAULT 0,
        market_hours_enabled TINYINT NOT NULL DEFAULT 0,
        market_hours_markets_json LONGTEXT NOT NULL,
        updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_jobs (
        job_id VARCHAR(191) PRIMARY KEY,
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        account_label VARCHAR(255) NOT NULL DEFAULT '',
        message_type VARCHAR(191) NOT NULL DEFAULT 'notification',
        source_event_id VARCHAR(191) NOT NULL DEFAULT '',
        source_event_name VARCHAR(191) NOT NULL DEFAULT '',
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        decision_episode_id VARCHAR(191) NOT NULL DEFAULT '',
        decision_key VARCHAR(191) NOT NULL DEFAULT '',
        api_source VARCHAR(191) NOT NULL DEFAULT 'notification_jobs',
        data_quality VARCHAR(32) NOT NULL DEFAULT 'actual',
        is_mock TINYINT NOT NULL DEFAULT 0,
        dedupe_key VARCHAR(191) DEFAULT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        attempts INT NOT NULL DEFAULT 0,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL DEFAULT '',
        last_error TEXT NOT NULL,
        text LONGTEXT NOT NULL,
        processing_started_at VARCHAR(40) NOT NULL DEFAULT '',
        retry_at VARCHAR(40) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        UNIQUE KEY idx_notification_jobs_dedupe (dedupe_key),
        KEY idx_notification_jobs_status_created (status, created_at, job_id),
        KEY idx_notification_jobs_message_time_status (message_type, created_at, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_lifecycle_events (
        event_id VARCHAR(191) PRIMARY KEY,
        job_id VARCHAR(191) NOT NULL,
        stage VARCHAR(64) NOT NULL,
        outcome VARCHAR(64) NOT NULL DEFAULT '',
        reason TEXT NOT NULL,
        metadata_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        KEY idx_notification_lifecycle_job_time (job_id, created_at, event_id),
        KEY idx_notification_lifecycle_stage_time (stage, created_at, event_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_delivery_attempts (
        attempt_id VARCHAR(191) PRIMARY KEY,
        job_id VARCHAR(191) NOT NULL,
        channel VARCHAR(64) NOT NULL DEFAULT '',
        audience VARCHAR(64) NOT NULL DEFAULT '',
        provider VARCHAR(191) NOT NULL DEFAULT '',
        status VARCHAR(32) NOT NULL DEFAULT 'started',
        reason TEXT NOT NULL,
        metadata_json LONGTEXT NOT NULL,
        started_at VARCHAR(40) NOT NULL,
        completed_at VARCHAR(40) NOT NULL DEFAULT '',
        KEY idx_notification_delivery_job_time (job_id, started_at, attempt_id),
        KEY idx_notification_delivery_status_time (status, started_at, attempt_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_inbox_receipts (
        recipient_id VARCHAR(191) NOT NULL,
        job_id VARCHAR(191) NOT NULL,
        read_at VARCHAR(40) NOT NULL DEFAULT '',
        acknowledged_at VARCHAR(40) NOT NULL DEFAULT '',
        important TINYINT NOT NULL DEFAULT 0,
        updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (recipient_id, job_id),
        KEY idx_notification_inbox_recipient_updated (recipient_id, updated_at, job_id),
        KEY idx_notification_inbox_recipient_read (recipient_id, read_at, job_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_inference_subject_heads (
        subject_key VARCHAR(255) PRIMARY KEY,
        latest_request_id VARCHAR(191) NOT NULL DEFAULT '',
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_ai_inference_subject_heads_updated (updated_at, subject_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_inference_requests (
        request_id VARCHAR(191) PRIMARY KEY,
        notification_job_id VARCHAR(191) NOT NULL,
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        account_label VARCHAR(255) NOT NULL DEFAULT '',
        message_type VARCHAR(191) NOT NULL DEFAULT '',
        subject_key VARCHAR(255) NOT NULL,
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        inference_generation_id VARCHAR(191) NOT NULL DEFAULT '',
        context_hash CHAR(64) NOT NULL,
        prompt_version VARCHAR(120) NOT NULL DEFAULT '',
        model VARCHAR(120) NOT NULL DEFAULT '',
        reasoning_effort VARCHAR(32) NOT NULL DEFAULT 'max',
        priority SMALLINT NOT NULL DEFAULT 20,
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        attempts INT NOT NULL DEFAULT 0,
        available_at VARCHAR(40) NOT NULL DEFAULT '',
        lease_owner VARCHAR(191) NOT NULL DEFAULT '',
        lease_expires_at VARCHAR(40) NOT NULL DEFAULT '',
        heartbeat_at VARCHAR(40) NOT NULL DEFAULT '',
        superseded_by VARCHAR(191) NOT NULL DEFAULT '',
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        started_at VARCHAR(40) NOT NULL DEFAULT '',
        completed_at VARCHAR(40) NOT NULL DEFAULT '',
        last_error TEXT NOT NULL,
        context_json LONGTEXT NOT NULL,
        UNIQUE KEY idx_ai_inference_requests_notification (notification_job_id),
        KEY idx_ai_inference_requests_ready (status, available_at, priority, created_at, request_id),
        KEY idx_ai_inference_requests_subject (subject_key, status, updated_at, request_id),
        KEY idx_ai_inference_requests_lease (status, lease_expires_at, request_id),
        KEY idx_ai_inference_requests_completed (status, completed_at, request_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_inference_results (
        result_id VARCHAR(191) PRIMARY KEY,
        request_id VARCHAR(191) NOT NULL,
        notification_job_id VARCHAR(191) NOT NULL,
        model VARCHAR(120) NOT NULL DEFAULT '',
        reasoning_effort VARCHAR(32) NOT NULL DEFAULT 'max',
        source VARCHAR(255) NOT NULL DEFAULT '',
        validation_state VARCHAR(32) NOT NULL DEFAULT 'conditional',
        latency_ms BIGINT NOT NULL DEFAULT 0,
        prompt_bytes BIGINT NOT NULL DEFAULT 0,
        response_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        UNIQUE KEY idx_ai_inference_results_request (request_id),
        KEY idx_ai_inference_results_notification (notification_job_id, created_at),
        KEY idx_ai_inference_results_created (created_at, result_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_article_delivery_ledger (
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        identity_key VARCHAR(191) NOT NULL,
        delivered_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        source_job_id VARCHAR(191) NOT NULL DEFAULT '',
        message_type VARCHAR(191) NOT NULL DEFAULT '',
        PRIMARY KEY (account_id, identity_key),
        KEY idx_notification_article_delivery_ledger_account_time (account_id, delivered_at, identity_key),
        KEY idx_notification_article_delivery_ledger_time (delivered_at, account_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS model_review_jobs (
        job_id VARCHAR(191) PRIMARY KEY,
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        account_label VARCHAR(255) NOT NULL DEFAULT '',
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        title VARCHAR(255) NOT NULL DEFAULT '',
        alert_key VARCHAR(191) NOT NULL DEFAULT '',
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        attempts INT NOT NULL DEFAULT 0,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL DEFAULT '',
        result LONGTEXT NOT NULL,
        last_error TEXT NOT NULL,
        alert_lines_json LONGTEXT NOT NULL,
        processing_started_at VARCHAR(40) NOT NULL DEFAULT '',
        retry_at VARCHAR(40) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        KEY idx_model_review_jobs_status (status, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS market_quote_cache (
        provider VARCHAR(64) NOT NULL,
        account_id VARCHAR(191) NOT NULL,
        symbol VARCHAR(64) NOT NULL,
        payload_json LONGTEXT NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (provider, account_id, symbol),
        KEY idx_market_quote_cache_account_updated (provider, account_id, updated_at, symbol)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS market_time_series_observations (
        account_id VARCHAR(191) NOT NULL,
        symbol VARCHAR(64) NOT NULL,
        granularity VARCHAR(16) NOT NULL,
        bucket_at VARCHAR(40) NOT NULL,
        observed_at VARCHAR(40) NOT NULL,
        source_as_of VARCHAR(40) NOT NULL DEFAULT '',
        provider VARCHAR(191) NOT NULL DEFAULT '',
        source_role VARCHAR(64) NOT NULL DEFAULT '',
        name VARCHAR(255) NOT NULL DEFAULT '',
        market VARCHAR(64) NOT NULL DEFAULT '',
        currency VARCHAR(16) NOT NULL DEFAULT '',
        sample_count INT NOT NULL DEFAULT 1,
        open_price DOUBLE NOT NULL DEFAULT 0,
        high_price DOUBLE NOT NULL DEFAULT 0,
        low_price DOUBLE NOT NULL DEFAULT 0,
        current_price DOUBLE NOT NULL DEFAULT 0,
        change_rate DOUBLE NOT NULL DEFAULT 0,
        quantity DOUBLE NOT NULL DEFAULT 0,
        average_price DOUBLE NOT NULL DEFAULT 0,
        profit_loss_rate DOUBLE NOT NULL DEFAULT 0,
        volume DOUBLE NOT NULL DEFAULT 0,
        trading_value DOUBLE NOT NULL DEFAULT 0,
        volume_ratio DOUBLE NOT NULL DEFAULT 0,
        trade_strength DOUBLE NOT NULL DEFAULT 0,
        bid_ask_imbalance DOUBLE NOT NULL DEFAULT 0,
        foreign_net_volume DOUBLE NOT NULL DEFAULT 0,
        institution_net_volume DOUBLE NOT NULL DEFAULT 0,
        individual_net_volume DOUBLE NOT NULL DEFAULT 0,
        investor_coverage_json LONGTEXT NULL,
        ma5 DOUBLE NOT NULL DEFAULT 0,
        ma20 DOUBLE NOT NULL DEFAULT 0,
        ma60 DOUBLE NOT NULL DEFAULT 0,
        ma20_slope DOUBLE NOT NULL DEFAULT 0,
        ma60_slope DOUBLE NOT NULL DEFAULT 0,
        ma20_distance DOUBLE NOT NULL DEFAULT 0,
        ma60_distance DOUBLE NOT NULL DEFAULT 0,
        data_quality VARCHAR(64) NOT NULL DEFAULT '',
        PRIMARY KEY (account_id, symbol, granularity, bucket_at),
        KEY idx_market_time_series_interval_time (granularity, bucket_at),
        KEY idx_market_time_series_symbol_time (symbol, granularity, bucket_at),
        KEY idx_market_time_series_account_time (account_id, granularity, bucket_at),
        KEY idx_market_time_series_snapshot_cutoff (account_id, symbol, granularity, observed_at, bucket_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS time_series_backend_deployments (
        backend_id VARCHAR(191) PRIMARY KEY,
        adapter_name VARCHAR(64) NOT NULL,
        adapter_version VARCHAR(64) NOT NULL,
        deployment_status VARCHAR(32) NOT NULL DEFAULT 'registered',
        contract_version VARCHAR(64) NOT NULL,
        capabilities_json LONGTEXT NOT NULL,
        settings_json LONGTEXT NOT NULL,
        last_health_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_time_series_backend_status (deployment_status, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS time_series_backend_control (
        control_id VARCHAR(64) PRIMARY KEY,
        active_backend_id VARCHAR(191) NOT NULL DEFAULT '',
        shadow_backend_id VARCHAR(191) NOT NULL DEFAULT '',
        candidate_backend_id VARCHAR(191) NOT NULL DEFAULT '',
        version BIGINT NOT NULL DEFAULT 0,
        updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    INSERT IGNORE INTO time_series_backend_control (
        control_id, updated_at
    ) VALUES ('global', '')
    """,
    """
    CREATE TABLE IF NOT EXISTS time_series_projection_outbox (
        job_id VARCHAR(191) PRIMARY KEY,
        backend_id VARCHAR(191) NOT NULL,
        dedupe_key VARCHAR(191) NOT NULL,
        operation_name VARCHAR(64) NOT NULL,
        source_event_id VARCHAR(191) NOT NULL DEFAULT '',
        source_observed_at VARCHAR(40) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        job_status VARCHAR(32) NOT NULL DEFAULT 'queued',
        lease_owner VARCHAR(191) NOT NULL DEFAULT '',
        lease_until VARCHAR(40) NOT NULL DEFAULT '',
        available_at VARCHAR(40) NOT NULL DEFAULT '',
        attempt_count INT NOT NULL DEFAULT 0,
        last_error VARCHAR(255) NOT NULL DEFAULT '',
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_time_series_projection_dedupe (backend_id, dedupe_key),
        KEY idx_time_series_projection_ready (backend_id, job_status, available_at, lease_until, created_at),
        KEY idx_time_series_projection_source (source_event_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS temporal_feature_snapshots (
        snapshot_id VARCHAR(191) PRIMARY KEY,
        feature_set_version VARCHAR(64) NOT NULL,
        backend_id VARCHAR(191) NOT NULL,
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        as_of VARCHAR(40) NOT NULL DEFAULT '',
        watermark_json LONGTEXT NOT NULL,
        symbols_json LONGTEXT NOT NULL,
        windows_json LONGTEXT NOT NULL,
        payload_hash VARCHAR(64) NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        KEY idx_temporal_feature_account_time (account_id, as_of, created_at),
        KEY idx_temporal_feature_backend_time (backend_id, as_of, created_at),
        KEY idx_temporal_feature_hash (payload_hash)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS reasoning_engine_deployments (
        deployment_id VARCHAR(191) PRIMARY KEY,
        engine_family VARCHAR(64) NOT NULL,
        engine_version VARCHAR(64) NOT NULL,
        deployment_status VARCHAR(32) NOT NULL DEFAULT 'registered',
        graph_store_binding VARCHAR(191) NOT NULL DEFAULT '',
        time_series_backend_id VARCHAR(191) NOT NULL DEFAULT '',
        release_bundle_json LONGTEXT NOT NULL,
        capabilities_json LONGTEXT NOT NULL,
        last_health_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_reasoning_engine_release (engine_family, engine_version, deployment_id),
        KEY idx_reasoning_engine_status (deployment_status, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS reasoning_engine_control (
        control_id VARCHAR(64) PRIMARY KEY,
        active_deployment_id VARCHAR(191) NOT NULL DEFAULT '',
        delivery_deployment_id VARCHAR(191) NOT NULL DEFAULT '',
        candidate_deployment_id VARCHAR(191) NOT NULL DEFAULT '',
        version BIGINT NOT NULL DEFAULT 0,
        updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    INSERT IGNORE INTO reasoning_engine_control (
        control_id, updated_at
    ) VALUES ('global', '')
    """,
    """
    CREATE TABLE IF NOT EXISTS reasoning_engine_comparisons (
        comparison_id VARCHAR(191) PRIMARY KEY,
        baseline_deployment_id VARCHAR(191) NOT NULL,
        candidate_deployment_id VARCHAR(191) NOT NULL,
        baseline_release_id VARCHAR(191) NOT NULL DEFAULT '',
        candidate_release_id VARCHAR(191) NOT NULL DEFAULT '',
        candidate_release_fingerprint VARCHAR(64) NOT NULL DEFAULT '',
        validation_cohort_id VARCHAR(96) NOT NULL DEFAULT '',
        candidate_runtime_revision VARCHAR(64) NOT NULL DEFAULT '',
        source_event_id VARCHAR(191) NOT NULL DEFAULT '',
        comparison_status VARCHAR(32) NOT NULL DEFAULT 'pending',
        fact_parity_pct DECIMAL(6,3) NOT NULL DEFAULT 0,
        rule_slot_coverage_pct DECIMAL(6,3) NOT NULL DEFAULT 0,
        unexplained_decision_difference_count INT NOT NULL DEFAULT 0,
        shadow_delivery_count INT NOT NULL DEFAULT 0,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_reasoning_comparison_candidate_time (candidate_deployment_id, created_at),
        KEY idx_reasoning_comparison_release_time (candidate_deployment_id, candidate_release_fingerprint, created_at),
        KEY idx_reasoning_comparison_status_time (comparison_status, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS reasoning_engine_shadow_jobs (
        job_id VARCHAR(191) PRIMARY KEY,
        dedupe_key VARCHAR(191) NOT NULL,
        scope_key VARCHAR(191) NOT NULL,
        baseline_deployment_id VARCHAR(191) NOT NULL,
        candidate_deployment_id VARCHAR(191) NOT NULL,
        candidate_release_id VARCHAR(191) NOT NULL DEFAULT '',
        candidate_runtime_revision VARCHAR(64) NOT NULL DEFAULT '',
        source_event_id VARCHAR(191) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        job_status VARCHAR(32) NOT NULL DEFAULT 'queued',
        attempts INT NOT NULL DEFAULT 0,
        available_at VARCHAR(40) NOT NULL,
        lease_owner VARCHAR(191) NOT NULL DEFAULT '',
        lease_expires_at VARCHAR(40) NOT NULL DEFAULT '',
        last_error TEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        completed_at VARCHAR(40) NOT NULL DEFAULT '',
        UNIQUE KEY uq_reasoning_shadow_dedupe (candidate_deployment_id, dedupe_key),
        KEY idx_reasoning_shadow_status_available (job_status, available_at),
        KEY idx_reasoning_shadow_scope_status (candidate_deployment_id, scope_key, job_status),
        KEY idx_reasoning_shadow_candidate_time (candidate_deployment_id, created_at),
        KEY idx_reasoning_shadow_release_ready (candidate_deployment_id, candidate_release_id, candidate_runtime_revision, job_status, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS reasoning_engine_jobs (
        job_id VARCHAR(191) PRIMARY KEY,
        deployment_id VARCHAR(191) NOT NULL,
        source_event_id VARCHAR(191) NOT NULL,
        scope_key VARCHAR(191) NOT NULL,
        input_fingerprint VARCHAR(64) NOT NULL,
        request_json LONGTEXT NOT NULL,
        result_json LONGTEXT NOT NULL,
        job_status VARCHAR(32) NOT NULL DEFAULT 'queued',
        priority INT NOT NULL DEFAULT 60,
        supersedable TINYINT NOT NULL DEFAULT 0,
        attempts INT NOT NULL DEFAULT 0,
        available_at VARCHAR(40) NOT NULL DEFAULT '',
        lease_owner VARCHAR(191) NOT NULL DEFAULT '',
        lease_expires_at VARCHAR(40) NOT NULL DEFAULT '',
        claimed_at VARCHAR(40) NOT NULL DEFAULT '',
        queue_wait_ms BIGINT NOT NULL DEFAULT 0,
        duration_ms BIGINT NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        completed_at VARCHAR(40) NOT NULL DEFAULT '',
        UNIQUE KEY uq_reasoning_engine_job_event (deployment_id, source_event_id),
        KEY idx_reasoning_engine_job_ready (deployment_id, job_status, available_at, priority, created_at),
        KEY idx_reasoning_engine_job_scope (deployment_id, scope_key, job_status, created_at),
        KEY idx_reasoning_engine_job_completed (deployment_id, completed_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS symbol_universe (
        market VARCHAR(64) NOT NULL,
        symbol VARCHAR(64) NOT NULL,
        name VARCHAR(255) NOT NULL,
        exchange VARCHAR(64) NOT NULL DEFAULT '',
        currency VARCHAR(16) NOT NULL DEFAULT '',
        sector VARCHAR(255) NOT NULL DEFAULT '',
        asset_type VARCHAR(64) NOT NULL DEFAULT 'STOCK',
        source VARCHAR(255) NOT NULL DEFAULT '',
        source_url TEXT NOT NULL,
        active TINYINT NOT NULL DEFAULT 1,
        fetched_at VARCHAR(40) NOT NULL DEFAULT '',
        first_seen_at VARCHAR(40) NOT NULL DEFAULT '',
        last_seen_at VARCHAR(40) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (market, symbol),
        KEY idx_symbol_universe_symbol (symbol),
        KEY idx_symbol_universe_active_market_symbol (active, market, symbol),
        KEY idx_symbol_universe_active_name_market (active, name, market)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS symbol_universe_sources (
        market VARCHAR(64) PRIMARY KEY,
        source VARCHAR(255) NOT NULL DEFAULT '',
        source_url TEXT NOT NULL,
        status VARCHAR(64) NOT NULL DEFAULT '',
        record_count INT NOT NULL DEFAULT 0,
        last_attempt_at VARCHAR(40) NOT NULL DEFAULT '',
        last_success_at VARCHAR(40) NOT NULL DEFAULT '',
        last_error TEXT NOT NULL,
        updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS research_evidence (
        evidence_id VARCHAR(191) PRIMARY KEY,
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        kind VARCHAR(64) NOT NULL DEFAULT '',
        source VARCHAR(255) NOT NULL DEFAULT '',
        title VARCHAR(500) NOT NULL DEFAULT '',
        summary LONGTEXT NOT NULL,
        url TEXT NOT NULL,
        published_at VARCHAR(40) NOT NULL DEFAULT '',
        observed_at VARCHAR(40) NOT NULL DEFAULT '',
        first_seen_at VARCHAR(40) NOT NULL DEFAULT '',
        last_seen_at VARCHAR(40) NOT NULL DEFAULT '',
        polarity VARCHAR(64) NOT NULL DEFAULT 'context',
        source_trust_state VARCHAR(32) NOT NULL DEFAULT 'unknown',
        materiality_state VARCHAR(32) NOT NULL DEFAULT 'context',
        data_state VARCHAR(32) NOT NULL DEFAULT 'partial',
        validation_state VARCHAR(32) NOT NULL DEFAULT 'conditional',
        lifecycle_state VARCHAR(32) NOT NULL DEFAULT 'active',
        lifecycle_changed_at VARCHAR(40) NOT NULL DEFAULT '',
        dedupe_key VARCHAR(191) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        KEY idx_research_evidence_symbol_last_seen (symbol, last_seen_at, evidence_id),
        KEY idx_research_evidence_kind_time (kind, last_seen_at),
        KEY idx_research_evidence_lifecycle_kind_time (lifecycle_state, kind, published_at, evidence_id),
        KEY idx_research_evidence_lifecycle_kind_seen (lifecycle_state, kind, last_seen_at, evidence_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS news_analysis_work_items (
        evidence_id VARCHAR(191) PRIMARY KEY,
        subject_revision VARCHAR(191) NOT NULL DEFAULT '',
        work_class VARCHAR(32) NOT NULL DEFAULT 'model',
        work_state VARCHAR(32) NOT NULL DEFAULT 'pending',
        priority INT NOT NULL DEFAULT 0,
        lease_owner VARCHAR(191) NOT NULL DEFAULT '',
        lease_until VARCHAR(40) NOT NULL DEFAULT '',
        not_before_at VARCHAR(40) NOT NULL DEFAULT '',
        attempt_count INT NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        completed_at VARCHAR(40) NOT NULL DEFAULT '',
        KEY idx_news_analysis_ready (work_class, work_state, not_before_at, lease_until, priority, updated_at),
        KEY idx_news_analysis_terminal (work_state, completed_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_calendar_events (
        event_id VARCHAR(191) PRIMARY KEY,
        title VARCHAR(255) NOT NULL DEFAULT '',
        event_type VARCHAR(64) NOT NULL DEFAULT 'custom',
        starts_at VARCHAR(40) NOT NULL DEFAULT '',
        ends_at VARCHAR(40) NOT NULL DEFAULT '',
        timezone_name VARCHAR(80) NOT NULL DEFAULT 'Asia/Seoul',
        all_day TINYINT NOT NULL DEFAULT 0,
        status VARCHAR(32) NOT NULL DEFAULT 'active',
        importance INT NOT NULL DEFAULT 60,
        symbols_json LONGTEXT NOT NULL,
        markets_json LONGTEXT NOT NULL,
        account_ids_json LONGTEXT NOT NULL,
        source VARCHAR(120) NOT NULL DEFAULT 'manual',
        source_url TEXT NOT NULL,
        notes LONGTEXT NOT NULL,
        reminder_offsets_json LONGTEXT NOT NULL,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_investment_calendar_time_status (status, starts_at, event_id),
        KEY idx_investment_calendar_type_time (event_type, starts_at, event_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_calendar_candidates (
        candidate_id VARCHAR(191) PRIMARY KEY,
        proposed_event_id VARCHAR(191) NOT NULL DEFAULT '',
        title VARCHAR(255) NOT NULL DEFAULT '',
        event_type VARCHAR(64) NOT NULL DEFAULT 'custom',
        starts_at VARCHAR(40) NOT NULL DEFAULT '',
        timezone_name VARCHAR(80) NOT NULL DEFAULT 'Asia/Seoul',
        all_day TINYINT NOT NULL DEFAULT 1,
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        review_reason VARCHAR(80) NOT NULL DEFAULT 'needsReview',
        importance INT NOT NULL DEFAULT 60,
        readiness_state VARCHAR(32) NOT NULL DEFAULT 'needs-review',
        symbols_json LONGTEXT NOT NULL,
        markets_json LONGTEXT NOT NULL,
        account_ids_json LONGTEXT NOT NULL,
        source VARCHAR(120) NOT NULL DEFAULT 'research-evidence',
        source_url TEXT NOT NULL,
        notes LONGTEXT NOT NULL,
        reminder_offsets_json LONGTEXT NOT NULL,
        source_evidence_id VARCHAR(191) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        reviewed_at VARCHAR(40) NOT NULL DEFAULT '',
        review_note TEXT NOT NULL,
        KEY idx_investment_calendar_candidates_status (status, created_at, candidate_id),
        KEY idx_investment_calendar_candidates_type_status (event_type, status, candidate_id),
        KEY idx_investment_calendar_candidates_evidence (source_evidence_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_strategy_proposals (
        proposal_id VARCHAR(191) PRIMARY KEY,
        status VARCHAR(32) NOT NULL DEFAULT 'proposed',
        title VARCHAR(255) NOT NULL DEFAULT '',
        source_trigger VARCHAR(120) NOT NULL DEFAULT '',
        source_experiment_id VARCHAR(191) NOT NULL DEFAULT '',
        symbols_json LONGTEXT NOT NULL,
        rule_ids_json LONGTEXT NOT NULL,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        approved_at VARCHAR(40) NOT NULL DEFAULT '',
        deployed_at VARCHAR(40) NOT NULL DEFAULT '',
        KEY idx_investment_strategy_proposals_status (status, updated_at, proposal_id),
        KEY idx_investment_strategy_proposals_experiment (source_experiment_id),
        KEY idx_investment_strategy_proposals_trigger (source_trigger, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ontology_ai_opinion_samples (
        sample_id VARCHAR(191) PRIMARY KEY,
        portfolio_id VARCHAR(191) NOT NULL DEFAULT '',
        created_at VARCHAR(40) NOT NULL,
        overall_state VARCHAR(32) NOT NULL DEFAULT 'blocked',
        data_state VARCHAR(32) NOT NULL DEFAULT 'unavailable',
        context_state VARCHAR(32) NOT NULL DEFAULT 'insufficient',
        reasoning_state VARCHAR(32) NOT NULL DEFAULT 'blocked',
        relation_state VARCHAR(32) NOT NULL DEFAULT 'empty',
        validation_state VARCHAR(32) NOT NULL DEFAULT 'blocked',
        entity_count INT NOT NULL DEFAULT 0,
        relation_count INT NOT NULL DEFAULT 0,
        evidence_count INT NOT NULL DEFAULT 0,
        belief_count INT NOT NULL DEFAULT 0,
        opinion_count INT NOT NULL DEFAULT 0,
        reasoning_card_count INT NOT NULL DEFAULT 0,
        data_gap_count INT NOT NULL DEFAULT 0,
        bounded_context_count INT NOT NULL DEFAULT 0,
        action_required_count INT NOT NULL DEFAULT 0,
        payload_json LONGTEXT NOT NULL,
        KEY idx_ontology_quality_portfolio_time (portfolio_id, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ontology_projection_runs (
        run_id VARCHAR(191) PRIMARY KEY,
        portfolio_id VARCHAR(191) NOT NULL DEFAULT '',
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        tenant_id VARCHAR(191) NOT NULL DEFAULT '',
        world_id VARCHAR(191) NOT NULL DEFAULT '',
        world_type VARCHAR(64) NOT NULL DEFAULT '',
        market_world_id VARCHAR(191) NOT NULL DEFAULT '',
        source_snapshot_at VARCHAR(40) NOT NULL DEFAULT '',
        source_snapshot_fingerprint VARCHAR(64) NOT NULL DEFAULT '',
        first_observed_at VARCHAR(40) NOT NULL DEFAULT '',
        last_observed_at VARCHAR(40) NOT NULL DEFAULT '',
        started_at VARCHAR(40) NOT NULL DEFAULT '',
        completed_at VARCHAR(40) NOT NULL DEFAULT '',
        activated_at VARCHAR(40) NOT NULL DEFAULT '',
        status VARCHAR(64) NOT NULL DEFAULT 'projecting',
        graph_store VARCHAR(64) NOT NULL DEFAULT '',
        projection_mode VARCHAR(128) NOT NULL DEFAULT '',
        material_fingerprint VARCHAR(64) NOT NULL DEFAULT '',
        abox_snapshot_id VARCHAR(191) NOT NULL DEFAULT '',
        active_abox_snapshot_id VARCHAR(191) NOT NULL DEFAULT '',
        tbox_version VARCHAR(191) NOT NULL DEFAULT '',
        tbox_fingerprint VARCHAR(64) NOT NULL DEFAULT '',
        rulebox_rules_hash VARCHAR(64) NOT NULL DEFAULT '',
        entity_count INT NOT NULL DEFAULT 0,
        relation_count INT NOT NULL DEFAULT 0,
        inference_generation_id VARCHAR(191) NOT NULL DEFAULT '',
        inference_status VARCHAR(64) NOT NULL DEFAULT '',
        source_symbols_json LONGTEXT NOT NULL,
        context_payload_json LONGTEXT NOT NULL,
        result_payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_ontology_projection_runs_account_updated (account_id, updated_at, run_id),
        KEY idx_ontology_projection_runs_world_updated (world_id, updated_at, run_id),
        KEY idx_ontology_projection_runs_abox (abox_snapshot_id),
        KEY idx_ontology_projection_runs_material (account_id, material_fingerprint)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ontology_reasoning_run_stages (
        run_id VARCHAR(191) NOT NULL,
        stage_key VARCHAR(191) NOT NULL,
        trace_version VARCHAR(64) NOT NULL DEFAULT '',
        world_id VARCHAR(191) NOT NULL DEFAULT '',
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        inference_generation_id VARCHAR(191) NOT NULL DEFAULT '',
        lane VARCHAR(48) NOT NULL DEFAULT 'CORE_REASONING',
        stage_order INT NOT NULL DEFAULT 0,
        status VARCHAR(64) NOT NULL DEFAULT '',
        started_at VARCHAR(40) NOT NULL DEFAULT '',
        completed_at VARCHAR(40) NOT NULL DEFAULT '',
        duration_ms BIGINT NOT NULL DEFAULT 0,
        input_count INT NOT NULL DEFAULT 0,
        output_count INT NOT NULL DEFAULT 0,
        detail_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (run_id, stage_key),
        KEY idx_reasoning_run_stages_world_time (world_id, updated_at, run_id),
        KEY idx_reasoning_run_stages_generation (inference_generation_id, account_id, updated_at),
        KEY idx_reasoning_run_stages_lane_time (lane, updated_at, run_id),
        KEY idx_reasoning_run_stages_stage_time (stage_key, updated_at, run_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ontology_reasoning_rule_runs (
        run_id VARCHAR(191) NOT NULL,
        rule_run_key VARCHAR(64) NOT NULL,
        trace_version VARCHAR(64) NOT NULL DEFAULT '',
        world_id VARCHAR(191) NOT NULL DEFAULT '',
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        inference_generation_id VARCHAR(191) NOT NULL DEFAULT '',
        lane VARCHAR(48) NOT NULL DEFAULT 'CORE_REASONING',
        stage_key VARCHAR(96) NOT NULL DEFAULT 'native-rule-evaluation',
        rule_id VARCHAR(191) NOT NULL DEFAULT '',
        rule_version VARCHAR(64) NOT NULL DEFAULT '',
        status VARCHAR(64) NOT NULL DEFAULT '',
        selected_reason VARCHAR(96) NOT NULL DEFAULT '',
        query_mode VARCHAR(96) NOT NULL DEFAULT '',
        query_count INT NOT NULL DEFAULT 0,
        duration_ms BIGINT NOT NULL DEFAULT 0,
        query_duration_ms BIGINT NOT NULL DEFAULT 0,
        target_symbols_json TEXT NOT NULL,
        matched TINYINT(1) NOT NULL DEFAULT 0,
        reused TINYINT(1) NOT NULL DEFAULT 0,
        cost_class VARCHAR(32) NOT NULL DEFAULT 'fast',
        failure_reason TEXT NOT NULL,
        detail_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (run_id, rule_run_key),
        KEY idx_reasoning_rule_runs_world_time (world_id, updated_at, run_id),
        KEY idx_reasoning_rule_runs_generation (inference_generation_id, account_id, updated_at),
        KEY idx_reasoning_rule_runs_rule_time (rule_id, updated_at, run_id),
        KEY idx_reasoning_rule_runs_status_time (status, updated_at, run_id),
        KEY idx_reasoning_rule_runs_cost_time (cost_class, updated_at, run_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ontology_reasoning_rule_result_slots (
        world_id VARCHAR(191) NOT NULL,
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        symbol VARCHAR(64) NOT NULL,
        rule_id VARCHAR(191) NOT NULL,
        rule_version VARCHAR(64) NOT NULL DEFAULT '',
        rulebox_rules_hash VARCHAR(64) NOT NULL DEFAULT '',
        tbox_fingerprint VARCHAR(64) NOT NULL DEFAULT '',
        scope_plan_fingerprint VARCHAR(64) NOT NULL DEFAULT '',
        result_state VARCHAR(32) NOT NULL DEFAULT 'not-matched',
        matched TINYINT(1) NOT NULL DEFAULT 0,
        catalog_rule_count INT NOT NULL DEFAULT 0,
        inference_generation_id VARCHAR(191) NOT NULL DEFAULT '',
        source_abox_snapshot_id VARCHAR(191) NOT NULL DEFAULT '',
        source_run_id VARCHAR(191) NOT NULL DEFAULT '',
        input_fingerprint VARCHAR(64) NOT NULL DEFAULT '',
        revision_vector_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (world_id, symbol, rule_id),
        KEY idx_reasoning_rule_slots_catalog (
            world_id, account_id, rulebox_rules_hash, tbox_fingerprint, symbol
        ),
        KEY idx_reasoning_rule_slots_generation (inference_generation_id, account_id, updated_at),
        KEY idx_reasoning_rule_slots_rule (rule_id, matched, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ontology_graph_assembly_cache (
        cache_key VARCHAR(64) PRIMARY KEY,
        payload_json LONGTEXT NOT NULL,
        payload_bytes INT NOT NULL DEFAULT 0,
        created_at VARCHAR(40) NOT NULL,
        expires_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_ontology_graph_assembly_cache_expiry (expires_at, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ontology_world_projection_outbox (
        job_id VARCHAR(191) PRIMARY KEY,
        dedupe_key VARCHAR(64) NOT NULL,
        projection_kind VARCHAR(32) NOT NULL DEFAULT 'market',
        world_id VARCHAR(191) NOT NULL DEFAULT '',
        world_type VARCHAR(64) NOT NULL DEFAULT '',
        tenant_id VARCHAR(191) NOT NULL DEFAULT '',
        market_id VARCHAR(96) NOT NULL DEFAULT '',
        source_world_id VARCHAR(191) NOT NULL DEFAULT '',
        source_account_id VARCHAR(191) NOT NULL DEFAULT '',
        source_observed_at VARCHAR(40) NOT NULL DEFAULT '',
        material_fingerprint VARCHAR(64) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        attempts INT NOT NULL DEFAULT 0,
        available_at VARCHAR(40) NOT NULL DEFAULT '',
        lease_owner VARCHAR(191) NOT NULL DEFAULT '',
        lease_expires_at VARCHAR(40) NOT NULL DEFAULT '',
        last_error TEXT NOT NULL,
        result_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        completed_at VARCHAR(40) NOT NULL DEFAULT '',
        KEY idx_world_projection_outbox_ready (status, available_at, created_at, job_id),
        KEY idx_world_projection_outbox_dedupe (dedupe_key, status, updated_at),
        KEY idx_world_projection_outbox_world (world_id, status, updated_at),
        KEY idx_world_projection_outbox_source (source_world_id, status, updated_at),
        KEY idx_world_projection_outbox_completed (status, completed_at, job_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ontology_inference_detail_outbox (
        job_id VARCHAR(191) PRIMARY KEY,
        dedupe_key VARCHAR(64) NOT NULL,
        world_id VARCHAR(191) NOT NULL DEFAULT '',
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        inference_generation_id VARCHAR(191) NOT NULL DEFAULT '',
        source_abox_snapshot_id VARCHAR(191) NOT NULL DEFAULT '',
        target_symbols_json LONGTEXT NOT NULL,
        projection_run_id VARCHAR(191) NOT NULL DEFAULT '',
        detail_limit INT NOT NULL DEFAULT 80,
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        attempts INT NOT NULL DEFAULT 0,
        available_at VARCHAR(40) NOT NULL DEFAULT '',
        lease_owner VARCHAR(191) NOT NULL DEFAULT '',
        lease_expires_at VARCHAR(40) NOT NULL DEFAULT '',
        last_error TEXT NOT NULL,
        result_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        completed_at VARCHAR(40) NOT NULL DEFAULT '',
        KEY idx_inference_detail_outbox_ready (status, available_at, created_at, job_id),
        KEY idx_inference_detail_outbox_dedupe (dedupe_key, status, updated_at),
        KEY idx_inference_detail_outbox_generation (world_id, inference_generation_id, status, updated_at),
        KEY idx_inference_detail_outbox_projection_run (projection_run_id, status, updated_at),
        KEY idx_inference_detail_outbox_completed (status, completed_at, job_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_decision_episodes (
        episode_id VARCHAR(191) PRIMARY KEY,
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        subject_name VARCHAR(255) NOT NULL DEFAULT '',
        question_id VARCHAR(191) NOT NULL DEFAULT '',
        hypothesis_set_id VARCHAR(191) NOT NULL DEFAULT '',
        selected_hypothesis_id VARCHAR(191) NOT NULL DEFAULT '',
        action VARCHAR(32) NOT NULL DEFAULT 'HOLD',
        review_level VARCHAR(32) NOT NULL DEFAULT 'check',
        data_state VARCHAR(32) NOT NULL DEFAULT 'partial',
        validation_state VARCHAR(32) NOT NULL DEFAULT 'conditional',
        inference_generation_id VARCHAR(191) NOT NULL DEFAULT '',
        status VARCHAR(32) NOT NULL DEFAULT 'active',
        decided_at VARCHAR(40) NOT NULL,
        source VARCHAR(120) NOT NULL DEFAULT 'notification-ai',
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_decision_episodes_account_symbol_time (account_id, symbol, decided_at),
        KEY idx_decision_episodes_hypothesis_status (selected_hypothesis_id, status, decided_at),
        KEY idx_decision_episodes_inference (inference_generation_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_decision_follow_ups (
        condition_id VARCHAR(191) PRIMARY KEY,
        episode_id VARCHAR(191) NOT NULL,
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        field_name VARCHAR(120) NOT NULL DEFAULT '',
        comparison_operator VARCHAR(8) NOT NULL DEFAULT '',
        threshold_value DOUBLE NOT NULL DEFAULT 0,
        purpose VARCHAR(32) NOT NULL DEFAULT 'switch',
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        observable TINYINT(1) NOT NULL DEFAULT 1,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        transitioned_at VARCHAR(40) NOT NULL DEFAULT '',
        KEY idx_decision_follow_ups_subject_status (account_id, symbol, status, updated_at),
        KEY idx_decision_follow_ups_episode (episode_id, status, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_hypothesis_lifecycle_states (
        lifecycle_key VARCHAR(255) PRIMARY KEY,
        lifecycle_id VARCHAR(191) NOT NULL DEFAULT '',
        scope VARCHAR(32) NOT NULL DEFAULT '',
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        portfolio_world_id VARCHAR(191) NOT NULL DEFAULT '',
        market_world_id VARCHAR(191) NOT NULL DEFAULT '',
        market_id VARCHAR(64) NOT NULL DEFAULT '',
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        family_id VARCHAR(191) NOT NULL DEFAULT '',
        state VARCHAR(32) NOT NULL DEFAULT 'observed',
        first_observed_at VARCHAR(40) NOT NULL DEFAULT '',
        last_observed_at VARCHAR(40) NOT NULL DEFAULT '',
        last_transition_at VARCHAR(40) NOT NULL DEFAULT '',
        inference_generation_id VARCHAR(191) NOT NULL DEFAULT '',
        inference_generation_at VARCHAR(40) NOT NULL DEFAULT '',
        previous_generation_id VARCHAR(191) NOT NULL DEFAULT '',
        semantic_fingerprint VARCHAR(64) NOT NULL DEFAULT '',
        transition_reason TEXT NOT NULL,
        material_change TINYINT(1) NOT NULL DEFAULT 0,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_hypothesis_lifecycle_events (
        transition_id VARCHAR(191) PRIMARY KEY,
        lifecycle_key VARCHAR(255) NOT NULL,
        lifecycle_id VARCHAR(191) NOT NULL DEFAULT '',
        scope VARCHAR(32) NOT NULL DEFAULT '',
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        market_id VARCHAR(64) NOT NULL DEFAULT '',
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        previous_state VARCHAR(32) NOT NULL DEFAULT '',
        current_state VARCHAR(32) NOT NULL DEFAULT '',
        inference_generation_id VARCHAR(191) NOT NULL DEFAULT '',
        previous_generation_id VARCHAR(191) NOT NULL DEFAULT '',
        occurred_at VARCHAR(40) NOT NULL,
        material_change TINYINT(1) NOT NULL DEFAULT 0,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        KEY idx_hypothesis_lifecycle_events_occurred (occurred_at, transition_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_hypothesis_transition_history (
        transition_id VARCHAR(191) PRIMARY KEY,
        lifecycle_key VARCHAR(255) NOT NULL,
        lifecycle_id VARCHAR(191) NOT NULL DEFAULT '',
        scope VARCHAR(32) NOT NULL DEFAULT '',
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        market_id VARCHAR(64) NOT NULL DEFAULT '',
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        previous_state VARCHAR(32) NOT NULL DEFAULT '',
        current_state VARCHAR(32) NOT NULL DEFAULT '',
        inference_generation_id VARCHAR(191) NOT NULL DEFAULT '',
        previous_generation_id VARCHAR(191) NOT NULL DEFAULT '',
        occurred_at VARCHAR(40) NOT NULL,
        material_change TINYINT(1) NOT NULL DEFAULT 0,
        archived_at VARCHAR(40) NOT NULL,
        KEY idx_hypothesis_transition_history_subject_time (account_id, symbol, occurred_at),
        KEY idx_hypothesis_transition_history_lifecycle_time (lifecycle_key, occurred_at),
        KEY idx_hypothesis_transition_history_state_time (current_state, occurred_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_decision_outcomes (
        outcome_id VARCHAR(191) PRIMARY KEY,
        episode_id VARCHAR(191) NOT NULL,
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        observed_at VARCHAR(40) NOT NULL,
        selected_hypothesis_status VARCHAR(64) NOT NULL DEFAULT 'pending',
        price DOUBLE NOT NULL DEFAULT 0,
        profit_loss_rate DOUBLE NOT NULL DEFAULT 0,
        price_change_from_decision_pct DOUBLE NOT NULL DEFAULT 0,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        KEY idx_decision_outcomes_episode_time (episode_id, observed_at),
        KEY idx_decision_outcomes_symbol_time (account_id, symbol, observed_at),
        KEY idx_decision_outcomes_hypothesis_status (selected_hypothesis_status, observed_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS mysql_retention_runs (
        run_id VARCHAR(191) PRIMARY KEY,
        profile VARCHAR(96) NOT NULL DEFAULT '',
        mode VARCHAR(32) NOT NULL DEFAULT 'preview',
        status VARCHAR(64) NOT NULL DEFAULT 'ok',
        deleted_count INT NOT NULL DEFAULT 0,
        compacted_count INT NOT NULL DEFAULT 0,
        estimated_bytes BIGINT NOT NULL DEFAULT 0,
        report_json LONGTEXT NOT NULL,
        started_at VARCHAR(40) NOT NULL,
        completed_at VARCHAR(40) NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        KEY idx_mysql_retention_runs_created (created_at, run_id),
        KEY idx_mysql_retention_runs_status (status, created_at, run_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_learning_proposals (
        proposal_id VARCHAR(191) PRIMARY KEY,
        status VARCHAR(32) NOT NULL DEFAULT 'review-required',
        title VARCHAR(255) NOT NULL DEFAULT '',
        reason LONGTEXT NOT NULL,
        affected_rule_ids_json LONGTEXT NOT NULL,
        source_episode_ids_json LONGTEXT NOT NULL,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        reviewed_at VARCHAR(40) NOT NULL DEFAULT '',
        review_note TEXT NOT NULL,
        KEY idx_learning_proposals_status_time (status, updated_at, proposal_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_research_runs (
        run_id VARCHAR(191) PRIMARY KEY,
        question_id VARCHAR(191) NOT NULL DEFAULT '',
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        status VARCHAR(40) NOT NULL DEFAULT 'ready',
        started_at VARCHAR(40) NOT NULL,
        completed_at VARCHAR(40) NOT NULL DEFAULT '',
        changed_evidence_count INT NOT NULL DEFAULT 0,
        reasoning_refreshed TINYINT(1) NOT NULL DEFAULT 0,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_investment_research_runs_symbol_time (account_id, symbol, started_at),
        KEY idx_investment_research_runs_status_time (status, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS investment_hypothesis_proposals (
        proposal_id VARCHAR(191) PRIMARY KEY,
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        status VARCHAR(40) NOT NULL DEFAULT 'review-required',
        title VARCHAR(255) NOT NULL DEFAULT '',
        source_question_id VARCHAR(191) NOT NULL DEFAULT '',
        source VARCHAR(120) NOT NULL DEFAULT 'ai-research-planner',
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        reviewed_at VARCHAR(40) NOT NULL DEFAULT '',
        review_note TEXT NOT NULL,
        KEY idx_hypothesis_proposals_symbol_status (symbol, status, updated_at),
        KEY idx_hypothesis_proposals_status_time (status, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS hypothesis_development_cases (
        case_id VARCHAR(191) PRIMARY KEY,
        fingerprint VARCHAR(64) NOT NULL,
        account_id VARCHAR(191) NOT NULL DEFAULT '',
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        status VARCHAR(40) NOT NULL DEFAULT 'proposed',
        stage VARCHAR(40) NOT NULL DEFAULT 'proposal',
        title VARCHAR(255) NOT NULL DEFAULT '',
        latest_proposal_id VARCHAR(191) NOT NULL DEFAULT '',
        candidate_rule_id VARCHAR(191) NOT NULL DEFAULT '',
        experiment_id VARCHAR(191) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        UNIQUE KEY uq_hypothesis_development_fingerprint (fingerprint),
        KEY idx_hypothesis_development_status_time (status, updated_at, case_id),
        KEY idx_hypothesis_development_symbol_status (symbol, status, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS hypothesis_development_events (
        event_id VARCHAR(191) PRIMARY KEY,
        case_id VARCHAR(191) NOT NULL,
        event_type VARCHAR(80) NOT NULL,
        status VARCHAR(40) NOT NULL DEFAULT '',
        stage VARCHAR(40) NOT NULL DEFAULT '',
        reason TEXT NOT NULL,
        payload_json LONGTEXT NOT NULL,
        occurred_at VARCHAR(40) NOT NULL,
        KEY idx_hypothesis_development_events_case_time (case_id, occurred_at, event_id),
        KEY idx_hypothesis_development_events_type_time (event_type, occurred_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ontology_experiments (
        experiment_id VARCHAR(191) PRIMARY KEY,
        status VARCHAR(40) NOT NULL DEFAULT 'draft',
        title VARCHAR(255) NOT NULL DEFAULT '',
        source_case_id VARCHAR(191) NOT NULL DEFAULT '',
        source_proposal_id VARCHAR(191) NOT NULL DEFAULT '',
        symbols_json TEXT NOT NULL,
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_ontology_experiments_status_time (status, updated_at, experiment_id),
        KEY idx_ontology_experiments_case (source_case_id, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ontology_experiment_runs (
        run_id VARCHAR(191) PRIMARY KEY,
        experiment_id VARCHAR(191) NOT NULL,
        status VARCHAR(40) NOT NULL DEFAULT '',
        completed_at VARCHAR(40) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        KEY idx_ontology_experiment_runs_experiment_time (experiment_id, completed_at, run_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ontology_reasoning_mailbox_events (
        event_id VARCHAR(191) PRIMARY KEY,
        occurred_at VARCHAR(40) NOT NULL DEFAULT '',
        state VARCHAR(32) NOT NULL DEFAULT 'pending',
        unresolved_entry_count INT NOT NULL DEFAULT 0,
        terminal_reason VARCHAR(255) NOT NULL DEFAULT '',
        event_json LONGTEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_reasoning_mailbox_events_state_time (state, updated_at, event_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ontology_reasoning_mailbox (
        mailbox_key VARCHAR(191) PRIMARY KEY,
        source_event_id VARCHAR(191) NOT NULL,
        account_scope VARCHAR(255) NOT NULL DEFAULT 'market',
        symbol VARCHAR(64) NOT NULL DEFAULT '',
        fact_family VARCHAR(255) NOT NULL DEFAULT '',
        work_class VARCHAR(32) NOT NULL DEFAULT 'MARKET',
        impact_scope VARCHAR(32) NOT NULL DEFAULT 'SUBJECT',
        reasoning_lane VARCHAR(40) NOT NULL DEFAULT 'REALTIME_REASONING',
        market_scope VARCHAR(96) NOT NULL DEFAULT 'market',
        rule_families_json TEXT NOT NULL,
        revision_vector_json TEXT NOT NULL,
        trigger_name VARCHAR(96) NOT NULL DEFAULT '',
        review_level VARCHAR(32) NOT NULL DEFAULT 'normal',
        priority_hint INT NOT NULL DEFAULT 0,
        occurred_at VARCHAR(40) NOT NULL DEFAULT '',
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_reasoning_mailbox_pending (priority_hint, occurred_at, mailbox_key),
        KEY idx_reasoning_mailbox_lane_pending (reasoning_lane, priority_hint, occurred_at, mailbox_key),
        KEY idx_reasoning_mailbox_source_event (source_event_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ontology_reasoning_work_items (
        mailbox_key VARCHAR(191) PRIMARY KEY,
        source_event_id VARCHAR(191) NOT NULL,
        work_state VARCHAR(32) NOT NULL DEFAULT 'queued',
        lease_owner VARCHAR(191) NOT NULL DEFAULT '',
        lease_until VARCHAR(40) NOT NULL DEFAULT '',
        not_before_at VARCHAR(40) NOT NULL DEFAULT '',
        attempt_count INT NOT NULL DEFAULT 0,
        last_stage VARCHAR(64) NOT NULL DEFAULT 'queued',
        stage_started_at VARCHAR(40) NOT NULL DEFAULT '',
        heartbeat_at VARCHAR(40) NOT NULL DEFAULT '',
        checkpoint_json LONGTEXT NOT NULL,
        last_error VARCHAR(255) NOT NULL DEFAULT '',
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_reasoning_work_ready (work_state, not_before_at, lease_until, updated_at),
        KEY idx_reasoning_work_source (source_event_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ontology_reasoning_queue_state (
        state_id VARCHAR(64) PRIMARY KEY,
        pending_entry_count INT NOT NULL DEFAULT 0,
        running_entry_count INT NOT NULL DEFAULT 0,
        retrying_entry_count INT NOT NULL DEFAULT 0,
        pending_symbol_count INT NOT NULL DEFAULT 0,
        oldest_pending_at VARCHAR(40) NOT NULL DEFAULT '',
        pending_symbols_json LONGTEXT NOT NULL,
        active_worker_id VARCHAR(191) NOT NULL DEFAULT '',
        active_lease_until VARCHAR(40) NOT NULL DEFAULT '',
        last_stage VARCHAR(64) NOT NULL DEFAULT '',
        last_stage_at VARCHAR(40) NOT NULL DEFAULT '',
        last_completed_at VARCHAR(40) NOT NULL DEFAULT '',
        last_timeout_at VARCHAR(40) NOT NULL DEFAULT '',
        version BIGINT NOT NULL DEFAULT 0,
        updated_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    INSERT IGNORE INTO ontology_reasoning_queue_state (
        state_id, pending_symbols_json, updated_at
    ) VALUES ('global', '[]', '')
    """,
]
