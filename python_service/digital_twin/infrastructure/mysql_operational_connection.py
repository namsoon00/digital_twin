from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict
import warnings

from .mysql_monitoring import MySQLDependencyError, ensure_mysql_database_exists, mysql_settings
from .mysql_retention import (
    apply_mysql_operational_history_retention,
    operational_history_retention_check_interval_seconds,
    operational_history_retention_enabled,
)
from .mysql_schema_tuning import ensure_mysql_operational_schema_tuning, mysql_partitioning_mode


def mysql_operation_timeout_seconds(settings: Dict[str, str]) -> int:
    try:
        parsed = int(float(str((settings or {}).get("mysqlOperationTimeoutSeconds") or "").strip()))
    except ValueError:
        parsed = 10
    return max(1, min(120, parsed))


class MySQLConnectionProxy:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql: str, params=None):
        cursor = self.connection.cursor()
        cursor.execute(sql, params or ())
        return cursor

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
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
        ensure_mysql_database_exists(self.mysql_config)
        self.ensure_schema()
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

    def connect(self):
        return MySQLConnectionProxy(self.raw_connection(autocommit=True))

    @contextmanager
    def transaction(self):
        proxy = MySQLConnectionProxy(self.raw_connection(autocommit=False))
        try:
            yield proxy
            proxy.commit()
        except Exception:
            proxy.rollback()
            raise
        finally:
            proxy.close()

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
        investment_strategy_profile VARCHAR(64) NOT NULL DEFAULT 'balanced',
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        KEY idx_service_accounts_enabled (enabled)
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
    CREATE TABLE IF NOT EXISTS monitor_snapshot_history (
        account_id VARCHAR(191) NOT NULL,
        generated_at VARCHAR(40) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
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
        threshold INT NOT NULL DEFAULT 45,
        base_score INT NOT NULL DEFAULT 25,
        low_score_action VARCHAR(64) NOT NULL DEFAULT 'suppress',
        conditions_json LONGTEXT NOT NULL,
        similarity_enabled TINYINT NOT NULL DEFAULT 1,
        similarity_window_minutes INT NOT NULL DEFAULT 60,
        similarity_penalty INT NOT NULL DEFAULT 25,
        similarity_bypass_score_delta INT NOT NULL DEFAULT 15,
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
        KEY idx_market_time_series_account_time (account_id, granularity, bucket_at)
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
        impact_score DOUBLE NOT NULL DEFAULT 0,
        confidence DOUBLE NOT NULL DEFAULT 0,
        dedupe_key VARCHAR(191) NOT NULL DEFAULT '',
        payload_json LONGTEXT NOT NULL,
        KEY idx_research_evidence_symbol_last_seen (symbol, last_seen_at, evidence_id),
        KEY idx_research_evidence_kind_time (kind, last_seen_at)
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
        confidence DOUBLE NOT NULL DEFAULT 0,
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
        overall_score DOUBLE NOT NULL DEFAULT 0,
        data_coverage_score DOUBLE NOT NULL DEFAULT 0,
        context_coverage_score DOUBLE NOT NULL DEFAULT 0,
        reasoning_readiness_score DOUBLE NOT NULL DEFAULT 0,
        relation_density_score DOUBLE NOT NULL DEFAULT 0,
        entity_count INT NOT NULL DEFAULT 0,
        relation_count INT NOT NULL DEFAULT 0,
        evidence_count INT NOT NULL DEFAULT 0,
        belief_count INT NOT NULL DEFAULT 0,
        opinion_count INT NOT NULL DEFAULT 0,
        reasoning_card_count INT NOT NULL DEFAULT 0,
        data_gap_count INT NOT NULL DEFAULT 0,
        bounded_context_count INT NOT NULL DEFAULT 0,
        high_pressure_count INT NOT NULL DEFAULT 0,
        payload_json LONGTEXT NOT NULL,
        KEY idx_ontology_quality_portfolio_time (portfolio_id, created_at)
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
        confidence DOUBLE NOT NULL DEFAULT 0,
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
]
