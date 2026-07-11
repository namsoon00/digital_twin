import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from ..domain.accounts import AccountConfig, split_symbols
from ..domain.data_freshness import evaluate_notification_data_freshness
from ..domain.events import (
    DomainEvent,
    alerts_detected_event,
    monitoring_cycle_completed_event,
    snapshot_collected_event,
)
from ..domain.fact_changes import fact_signature, research_evidence_fact_payload
from ..domain.investment_research import ResearchEvidence
from ..domain.model_review import ModelReviewJob
from ..domain.notification_rules import (
    DEFAULT_NOTIFICATION_RULES,
    NotificationRuleConfig,
    apply_market_hours_rule,
    apply_similarity_rule,
    apply_state_cooldown_rule,
    default_notification_rule,
    evaluate_notification_rule,
    notification_fingerprint,
)
from ..domain.notification_templates import DEFAULT_NOTIFICATION_TEMPLATES, NotificationTemplate, alert_context, render_notification
from ..domain.notifications import NotificationJob, notification_debug_number
from ..domain.ontology_quality import OntologyQualitySample, build_ontology_quality_sample
from ..domain.portfolio import AccountSnapshot, AlertEvent
from ..domain.repositories import MonitoringCycleRecordResult
from ..domain.symbol_universe import ListedSymbol, normalize_market, normalize_symbol, utc_now_iso as symbol_utc_now_iso
from .model_review_queue import model_review_payloads_from_event
from .mysql_monitoring import MySQLDependencyError, MySQLMonitorAccountJobStore, mysql_settings
from .operational_common import (
    MAX_NOTIFICATION_DELIVERY_ATTEMPTS,
    NOTIFICATION_HISTORY_LOOKBACK_LIMIT,
    age_minutes_since,
    json_dumps,
    notification_history_is_recent_in_flight,
    research_evidence_from_row,
    rule_from_row,
    template_from_row,
)
from .settings import read_json, settings_path, utc_now


def _json_loads(value, fallback):
    try:
        payload = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return payload if isinstance(payload, type(fallback)) else fallback


def research_evidence_change_payload(
    symbol: str,
    kind: str,
    source: str,
    title: str,
    summary: str,
    url: str,
    published_at: str,
    polarity: str,
    impact_score: float,
    confidence: float,
    payload: Dict[str, object],
) -> Dict[str, object]:
    return research_evidence_fact_payload({
        "symbol": symbol,
        "kind": kind,
        "source": source,
        "title": title,
        "summary": summary,
        "url": url,
        "publishedAt": published_at,
        "polarity": polarity,
        "impactScore": round(float(impact_score or 0), 6),
        "confidence": round(float(confidence or 0), 6),
        "payload": payload if isinstance(payload, dict) else {},
    })


def _sent_key_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _is_duplicate_key_error(error: Exception) -> bool:
    return bool(getattr(error, "args", None)) and str(error.args[0]) == "1062"


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
    _schema_ready = False

    def __init__(self, settings: Dict[str, str] = None):
        self.runtime_settings = dict(settings or {})
        self.mysql_config = mysql_settings(settings)
        self.ensure_schema()

    def raw_connection(self, autocommit: bool = True):
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as error:
            raise MySQLDependencyError("MySQL backend requires pymysql. Install with: python3 -m pip install pymysql") from error
        kwargs = {
            "host": self.mysql_config["host"],
            "port": int(self.mysql_config["port"] or 3306),
            "user": self.mysql_config["user"],
            "password": self.mysql_config["password"],
            "database": self.mysql_config["database"],
            "charset": "utf8mb4",
            "cursorclass": DictCursor,
            "autocommit": autocommit,
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

    def ensure_schema(self) -> None:
        if MySQLOperationalConnection._schema_ready:
            return
        with self.transaction() as connection:
            for statement in MYSQL_SCHEMA:
                connection.execute(statement)
        MySQLOperationalConnection._schema_ready = True


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
        KEY idx_symbol_universe_active_market_symbol (active, market, symbol)
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
]


def insert_domain_event_with_connection(connection, event: DomainEvent) -> None:
    connection.execute(
        """
        INSERT IGNORE INTO domain_events (
            event_id, name, aggregate_id, occurred_at, correlation_id, payload_json, event_json
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event.event_id,
            event.name,
            str(event.aggregate_id or "")[:191],
            event.occurred_at,
            event.correlation_id,
            json_dumps(event.payload),
            json_dumps(event.to_dict()),
        ),
    )


class MySQLRuntimeSettingsStore(MySQLOperationalConnection):
    def __init__(self, settings: Dict[str, str] = None, legacy_path: Optional[object] = None):
        self.legacy_path = legacy_path or settings_path()
        super().__init__(settings)

    def load(self) -> Dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT `key`, value FROM runtime_settings ORDER BY `key`").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def replace(self, settings: Dict[str, object]) -> None:
        stamp = utc_now()
        with self.transaction() as connection:
            connection.execute("DELETE FROM runtime_settings")
            for key, value in (settings or {}).items():
                connection.execute(
                    "INSERT INTO runtime_settings (`key`, value, updated_at) VALUES (%s, %s, %s)",
                    (str(key), str(value or ""), stamp),
                )

    def save(self, settings: Dict[str, object]) -> None:
        stamp = utc_now()
        with self.transaction() as connection:
            for key, value in (settings or {}).items():
                connection.execute(
                    """
                    INSERT INTO runtime_settings (`key`, value, updated_at)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = VALUES(updated_at)
                    """,
                    (str(key), str(value or ""), stamp),
                )


class MySQLAccountRegistry(MySQLOperationalConnection):
    def __init__(self, settings: Dict[str, str] = None, legacy_path: Optional[object] = None):
        self.legacy_path = legacy_path
        super().__init__(settings)
        self.settings_map = settings or {}

    @property
    def settings(self):
        return self.settings_map

    def account_from_row(self, row) -> AccountConfig:
        watchlist = row["watchlist_symbols"] if row["watchlist_symbols"] else self.settings.get("watchlistSymbols", "")
        return AccountConfig(
            account_id=row["id"],
            label=row["label"],
            provider=row["provider"],
            base_url=row["base_url"] or self.settings.get("tossApiBaseUrl", "https://openapi.tossinvest.com"),
            client_id=row["client_id"] or self.settings.get("tossClientId", ""),
            client_secret=row["client_secret"] or self.settings.get("tossClientSecret", ""),
            account_seq=row["account_seq"] or self.settings.get("tossAccountSeq", ""),
            watchlist_symbols=split_symbols(watchlist),
            notify_provider=row["notify_provider"] or self.settings.get("notifyProvider", ""),
            telegram_bot_token=row["bot_token"] or self.settings.get("telegramBotToken", ""),
            telegram_chat_id=row["chat_id"] or self.settings.get("telegramChatId", ""),
            notify_link_url=row["link_url"] or self.settings.get("notifyLinkUrl", ""),
            enabled=bool(row["enabled"]),
            quiet_hours_enabled=bool(row["quiet_hours_enabled"]),
            quiet_hours_start=row["quiet_hours_start"] or "22:00",
            quiet_hours_end=row["quiet_hours_end"] or "05:00",
            quiet_hours_timezone=row["quiet_hours_timezone"] or "Asia/Seoul",
            message_delivery_level=row["message_delivery_level"] or "absoluteBeginner",
        )

    def select_accounts(self, enabled_only: bool) -> List[AccountConfig]:
        sql = """
            SELECT a.*, COALESCE(t.base_url, '') AS base_url, COALESCE(t.client_id, '') AS client_id,
                   COALESCE(t.client_secret, '') AS client_secret, COALESCE(t.account_seq, '') AS account_seq,
                   COALESCE(g.notify_provider, '') AS notify_provider, COALESCE(g.bot_token, '') AS bot_token,
                   COALESCE(g.chat_id, '') AS chat_id, COALESCE(g.link_url, '') AS link_url
            FROM service_accounts a
            LEFT JOIN toss_credentials t ON t.account_id = a.id
            LEFT JOIN telegram_configs g ON g.account_id = a.id
        """
        params = []
        if enabled_only:
            sql += " WHERE a.enabled = %s"
            params.append(1)
        sql += " ORDER BY a.created_at, a.id"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self.account_from_row(row) for row in rows]

    def load(self) -> List[AccountConfig]:
        accounts = self.select_accounts(True)
        return accounts or [self.default_account()]

    def load_all(self) -> List[AccountConfig]:
        accounts = self.select_accounts(False)
        return accounts or [self.default_account()]

    def load_saved(self) -> List[AccountConfig]:
        return self.select_accounts(False)

    def default_account(self) -> AccountConfig:
        return AccountConfig(
            account_id="default",
            label="기본 계정",
            provider="toss",
            base_url=self.settings.get("tossApiBaseUrl", "https://openapi.tossinvest.com"),
            client_id=self.settings.get("tossClientId", ""),
            client_secret=self.settings.get("tossClientSecret", ""),
            account_seq=self.settings.get("tossAccountSeq", ""),
            watchlist_symbols=split_symbols(self.settings.get("watchlistSymbols", "TSLA,AAPL,NVDA,000660")),
            notify_provider=self.settings.get("notifyProvider", ""),
            telegram_bot_token=self.settings.get("telegramBotToken", ""),
            telegram_chat_id=self.settings.get("telegramChatId", ""),
            notify_link_url=self.settings.get("notifyLinkUrl", ""),
            enabled=True,
            message_delivery_level=self.settings.get("messageDeliveryLevel", "absoluteBeginner"),
        )

    def upsert_with_connection(self, connection, account: AccountConfig) -> None:
        stamp = utc_now()
        existing = connection.execute("SELECT created_at FROM service_accounts WHERE id = %s", (account.account_id,)).fetchone()
        created_at = existing["created_at"] if existing else stamp
        connection.execute(
            """
            INSERT INTO service_accounts (
                id, label, provider, enabled, watchlist_symbols, quiet_hours_enabled,
                quiet_hours_start, quiet_hours_end, quiet_hours_timezone, message_delivery_level,
                created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                label = VALUES(label), provider = VALUES(provider), enabled = VALUES(enabled),
                watchlist_symbols = VALUES(watchlist_symbols),
                quiet_hours_enabled = VALUES(quiet_hours_enabled),
                quiet_hours_start = VALUES(quiet_hours_start),
                quiet_hours_end = VALUES(quiet_hours_end),
                quiet_hours_timezone = VALUES(quiet_hours_timezone),
                message_delivery_level = VALUES(message_delivery_level),
                updated_at = VALUES(updated_at)
            """,
            (
                account.account_id,
                account.label,
                account.provider,
                1 if account.enabled else 0,
                ",".join(account.watchlist_symbols),
                1 if account.quiet_hours_enabled else 0,
                account.quiet_hours_start,
                account.quiet_hours_end,
                account.quiet_hours_timezone,
                account.message_delivery_level,
                created_at,
                stamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO toss_credentials (account_id, base_url, client_id, client_secret, account_seq, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE base_url = VALUES(base_url), client_id = VALUES(client_id),
                client_secret = VALUES(client_secret), account_seq = VALUES(account_seq), updated_at = VALUES(updated_at)
            """,
            (account.account_id, account.base_url, account.client_id, account.client_secret, account.account_seq, stamp),
        )
        connection.execute(
            """
            INSERT INTO telegram_configs (account_id, notify_provider, bot_token, chat_id, link_url, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE notify_provider = VALUES(notify_provider), bot_token = VALUES(bot_token),
                chat_id = VALUES(chat_id), link_url = VALUES(link_url), updated_at = VALUES(updated_at)
            """,
            (account.account_id, account.notify_provider, account.telegram_bot_token, account.telegram_chat_id, account.notify_link_url, stamp),
        )

    def upsert(self, account: AccountConfig) -> None:
        with self.transaction() as connection:
            self.upsert_with_connection(connection, account)

    def upsert_with_event(self, account: AccountConfig, event: DomainEvent) -> None:
        with self.transaction() as connection:
            self.upsert_with_connection(connection, account)
            insert_domain_event_with_connection(connection, event)

    def remove(self, account_id: str) -> bool:
        with self.transaction() as connection:
            connection.execute("DELETE FROM toss_credentials WHERE account_id = %s", (account_id,))
            connection.execute("DELETE FROM telegram_configs WHERE account_id = %s", (account_id,))
            cursor = connection.execute("DELETE FROM service_accounts WHERE id = %s", (account_id,))
        return int(cursor.rowcount or 0) > 0

    def remove_with_event(self, account_id: str, event: DomainEvent) -> bool:
        with self.transaction() as connection:
            connection.execute("DELETE FROM toss_credentials WHERE account_id = %s", (account_id,))
            connection.execute("DELETE FROM telegram_configs WHERE account_id = %s", (account_id,))
            cursor = connection.execute("DELETE FROM service_accounts WHERE id = %s", (account_id,))
            removed = int(cursor.rowcount or 0) > 0
            if removed:
                insert_domain_event_with_connection(connection, event)
        return removed


class MySQLAppStore(MySQLOperationalConnection):
    store_id = "default"

    def load(self) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute("SELECT payload_json FROM app_store WHERE store_id = %s", (self.store_id,)).fetchone()
        return _json_loads(row["payload_json"], {}) if row else {}

    def replace(self, payload: Dict[str, object]) -> None:
        stamp = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO app_store (store_id, payload_json, updated_at)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
                (self.store_id, json_dumps(payload if isinstance(payload, dict) else {}), stamp),
            )


class MySQLExternalSignalCache(MySQLAppStore):
    store_id = "external_signals"


class MySQLOntologyReasoningCursorStore(MySQLAppStore):
    store_id = "ontology_reasoning_cursor"

    def load(self) -> Dict[str, object]:
        payload = super().load()
        payload.setdefault("processedEventIds", [])
        return payload

    def processed_event_ids(self) -> List[str]:
        return [str(item or "").strip() for item in self.load().get("processedEventIds", []) if str(item or "").strip()]

    def save(self, payload: Dict[str, object]) -> None:
        next_payload = dict(payload or {})
        next_payload.setdefault("processedEventIds", self.processed_event_ids())
        next_payload["updatedAt"] = utc_now()
        self.replace(next_payload)

    def mark_processed(self, event_ids: Iterable[str]) -> None:
        existing = self.processed_event_ids()
        seen = set(existing)
        merged = list(existing)
        for event_id in event_ids or []:
            clean = str(event_id or "").strip()
            if clean and clean not in seen:
                seen.add(clean)
                merged.append(clean)
        payload = self.load()
        payload["processedEventIds"] = merged[-1000:]
        self.save(payload)


class MySQLNotificationTemplateStore(MySQLOperationalConnection):
    def __init__(self, settings: Dict[str, str] = None):
        super().__init__(settings)
        self.seed_defaults()

    def seed_defaults(self) -> None:
        stamp = utc_now()
        with self.transaction() as connection:
            for message_type, payload in DEFAULT_NOTIFICATION_TEMPLATES.items():
                connection.execute(
                    """
                    INSERT IGNORE INTO notification_templates (message_type, template, description, enabled, updated_at)
                    VALUES (%s, %s, %s, 1, %s)
                    """,
                    (message_type, str(payload.get("template") or ""), str(payload.get("description") or ""), stamp),
                )

    def list(self) -> List[NotificationTemplate]:
        with self.connect() as connection:
            rows = connection.execute("SELECT message_type, template, description, enabled, updated_at FROM notification_templates ORDER BY message_type").fetchall()
        return [template_from_row(row) for row in rows]

    def get(self, message_type: str) -> NotificationTemplate:
        key = str(message_type or "notification").strip() or "notification"
        with self.connect() as connection:
            row = connection.execute(
                "SELECT message_type, template, description, enabled, updated_at FROM notification_templates WHERE message_type = %s",
                (key,),
            ).fetchone()
            if not row:
                row = connection.execute(
                    "SELECT message_type, template, description, enabled, updated_at FROM notification_templates WHERE message_type = 'default'"
                ).fetchone()
        return template_from_row(row) if row else NotificationTemplate.default("default")

    def upsert(self, message_type: str, template: str, description: str = "", enabled: bool = True) -> NotificationTemplate:
        key = str(message_type or "").strip()
        if not key:
            raise ValueError("message_type is required")
        stamp = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO notification_templates (message_type, template, description, enabled, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE template = VALUES(template), description = VALUES(description),
                    enabled = VALUES(enabled), updated_at = VALUES(updated_at)
                """,
                (key, str(template or ""), str(description or ""), 1 if enabled else 0, stamp),
            )
        return self.get(key)

    def reset(self, message_type: str) -> NotificationTemplate:
        key = str(message_type or "").strip() or "default"
        configured = DEFAULT_NOTIFICATION_TEMPLATES.get(key) or DEFAULT_NOTIFICATION_TEMPLATES["default"]
        return self.upsert(key, configured["template"], configured.get("description", ""), True)

    def render(self, message_type: str, context: Dict[str, object]) -> str:
        return render_notification(self.get(message_type), context)

    def render_job(self, job: NotificationJob) -> str:
        context = dict(job.context or {})
        context.setdefault("body", job.text)
        context.setdefault("messageType", job.message_type)
        context.setdefault("accountId", job.account_id)
        context.setdefault("accountLabel", job.account_label)
        context.setdefault("jobId", job.job_id)
        context.setdefault("notificationNumber", notification_debug_number(job.job_id))
        return self.render(job.message_type, context)


class MySQLNotificationRuleStore(MySQLOperationalConnection):
    def __init__(self, settings: Dict[str, str] = None, seed_defaults: bool = True):
        super().__init__(settings)
        if seed_defaults:
            self.seed_defaults()

    def seed_defaults(self) -> None:
        stamp = utc_now()
        with self.transaction() as connection:
            for message_type, rule in DEFAULT_NOTIFICATION_RULES.items():
                connection.execute(
                    """
                    INSERT IGNORE INTO notification_rules (
                        message_type, enabled, threshold, base_score, low_score_action, conditions_json,
                        similarity_enabled, similarity_window_minutes, similarity_penalty, similarity_bypass_score_delta,
                        similarity_bypass_conditions_json, similarity_fields_json, state_cooldown_enabled,
                        state_cooldown_minutes, market_hours_enabled, market_hours_markets_json, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        message_type,
                        1 if rule.enabled else 0,
                        int(rule.threshold),
                        int(rule.base_score),
                        rule.low_score_action,
                        json_dumps([condition.to_dict() for condition in rule.conditions]),
                        1 if rule.similarity_enabled else 0,
                        int(rule.similarity_window_minutes),
                        int(rule.similarity_penalty),
                        int(rule.similarity_bypass_score_delta),
                        json_dumps([condition.to_dict() for condition in rule.similarity_bypass_conditions]),
                        json_dumps(rule.similarity_fields),
                        1 if rule.state_cooldown_enabled else 0,
                        int(rule.state_cooldown_minutes),
                        1 if rule.market_hours_enabled else 0,
                        json_dumps(rule.market_hours_markets),
                        stamp,
                    ),
                )

    def list(self) -> List[NotificationRuleConfig]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM notification_rules ORDER BY message_type").fetchall()
        return [rule_from_row(row) for row in rows]

    def get(self, message_type: str) -> NotificationRuleConfig:
        key = str(message_type or "notification").strip() or "notification"
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM notification_rules WHERE message_type = %s", (key,)).fetchone()
        return rule_from_row(row) if row else default_notification_rule(key)

    def upsert(self, rule: NotificationRuleConfig) -> NotificationRuleConfig:
        normalized = NotificationRuleConfig.from_dict(rule.to_dict() if isinstance(rule, NotificationRuleConfig) else dict(rule or {}))
        normalized.message_type = str(normalized.message_type or "").strip()
        if not normalized.message_type:
            raise ValueError("message_type is required")
        normalized.updated_at = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO notification_rules (
                    message_type, enabled, threshold, base_score, low_score_action, conditions_json,
                    similarity_enabled, similarity_window_minutes, similarity_penalty, similarity_bypass_score_delta,
                    similarity_bypass_conditions_json, similarity_fields_json, state_cooldown_enabled,
                    state_cooldown_minutes, market_hours_enabled, market_hours_markets_json, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE enabled = VALUES(enabled), threshold = VALUES(threshold),
                    base_score = VALUES(base_score), low_score_action = VALUES(low_score_action),
                    conditions_json = VALUES(conditions_json), similarity_enabled = VALUES(similarity_enabled),
                    similarity_window_minutes = VALUES(similarity_window_minutes),
                    similarity_penalty = VALUES(similarity_penalty),
                    similarity_bypass_score_delta = VALUES(similarity_bypass_score_delta),
                    similarity_bypass_conditions_json = VALUES(similarity_bypass_conditions_json),
                    similarity_fields_json = VALUES(similarity_fields_json),
                    state_cooldown_enabled = VALUES(state_cooldown_enabled),
                    state_cooldown_minutes = VALUES(state_cooldown_minutes),
                    market_hours_enabled = VALUES(market_hours_enabled),
                    market_hours_markets_json = VALUES(market_hours_markets_json),
                    updated_at = VALUES(updated_at)
                """,
                (
                    normalized.message_type,
                    1 if normalized.enabled else 0,
                    int(normalized.threshold),
                    int(normalized.base_score),
                    normalized.low_score_action,
                    json_dumps([condition.to_dict() for condition in normalized.conditions]),
                    1 if normalized.similarity_enabled else 0,
                    int(normalized.similarity_window_minutes),
                    int(normalized.similarity_penalty),
                    int(normalized.similarity_bypass_score_delta),
                    json_dumps([condition.to_dict() for condition in normalized.similarity_bypass_conditions]),
                    json_dumps(normalized.similarity_fields),
                    1 if normalized.state_cooldown_enabled else 0,
                    int(normalized.state_cooldown_minutes),
                    1 if normalized.market_hours_enabled else 0,
                    json_dumps(normalized.market_hours_markets),
                    normalized.updated_at,
                ),
            )
        return self.get(normalized.message_type)

    def reset(self, message_type: str) -> NotificationRuleConfig:
        return self.upsert(default_notification_rule(str(message_type or "notification").strip() or "notification"))

    def similar_history(self, job: NotificationJob, rule: NotificationRuleConfig, fingerprint: str):
        return MySQLNotificationJobStore(self.runtime_settings).similar_history_for_rule(job, rule, fingerprint)

    def evaluate_job(self, job: NotificationJob):
        rule = self.get(job.message_type)
        decision = evaluate_notification_rule(job, rule)
        recent_count, previous_score, previous_context, last_sent_at = self.similar_history(job, rule, decision.fingerprint)
        decision = apply_state_cooldown_rule(decision, rule, recent_count, previous_score, previous_context, last_sent_at, age_minutes_since(last_sent_at), job)
        decision = apply_similarity_rule(decision, rule, recent_count, previous_score, previous_context, job)
        return apply_market_hours_rule(decision, rule, job)


class MySQLMonitorStore(MySQLOperationalConnection):
    def __init__(self, settings: Dict[str, str] = None):
        super().__init__(settings)
        self.payload = {"previous": self.load_previous(), "sent": self.load_sent()}

    def load_previous(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute("SELECT account_id, payload_json FROM monitor_snapshots").fetchall()
        previous = {}
        for row in rows:
            previous[row["account_id"]] = _json_loads(row["payload_json"], {})
        return previous

    def load_history(self, account_id: str, limit: int = 6) -> List[Dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM monitor_snapshot_history
                WHERE account_id = %s
                ORDER BY generated_at DESC
                LIMIT %s
                """,
                (str(account_id or ""), max(1, int(limit or 6))),
            ).fetchall()
        history = [_json_loads(row["payload_json"], {}) for row in reversed(rows)]
        return [item for item in history if item]

    def load_sent(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute("SELECT sent_key, sent_at FROM monitor_sent").fetchall()
        return {row["sent_key"]: row["sent_at"] for row in rows}

    @property
    def previous(self) -> Dict[str, object]:
        return self.payload["previous"]

    @property
    def sent(self) -> Dict[str, object]:
        return self.payload["sent"]

    def upsert_snapshot_state_with_connection(self, connection, account_id: str, state: Dict[str, object], stamp: str = "") -> None:
        updated_at = stamp or utc_now()
        generated_at = str(state.get("generatedAt") or updated_at)
        connection.execute(
            """
            INSERT INTO monitor_snapshots (
                account_id, account_label, provider, mode, status, generated_at, payload_json, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE account_label = VALUES(account_label), provider = VALUES(provider),
                mode = VALUES(mode), status = VALUES(status), generated_at = VALUES(generated_at),
                payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
            """,
            (
                account_id,
                str(state.get("accountLabel") or ""),
                str(state.get("provider") or ""),
                str(state.get("mode") or ""),
                str(state.get("status") or ""),
                generated_at,
                json_dumps(state),
                updated_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO monitor_snapshot_history (account_id, generated_at, payload_json, created_at)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE payload_json = VALUES(payload_json), created_at = VALUES(created_at)
            """,
            (account_id, generated_at, json_dumps(state), updated_at),
        )

    def upsert_snapshot_state(self, account_id: str, state: Dict[str, object]) -> None:
        with self.transaction() as connection:
            self.upsert_snapshot_state_with_connection(connection, account_id, state)

    def save_snapshot(self, snapshot: AccountSnapshot) -> None:
        state = snapshot.to_monitor_state()
        self.upsert_snapshot_state(snapshot.account_id, state)
        self.previous[snapshot.account_id] = state

    def sent_entries(self, events: Iterable[AlertEvent], stamp: str) -> Dict[str, str]:
        entries: Dict[str, str] = {}
        for event in events:
            entries[event.key] = stamp
            entries[event.cadence_key()] = stamp
        return entries

    def mark_sent_with_connection(self, connection, events: Iterable[AlertEvent], stamp: str) -> Dict[str, str]:
        entries = self.sent_entries(events, stamp)
        for key, sent_at in entries.items():
            connection.execute(
                """
                INSERT INTO monitor_sent (sent_key_hash, sent_key, sent_at)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE sent_at = VALUES(sent_at)
                """,
                (_sent_key_hash(key), key, sent_at),
            )
        return entries

    def mark_sent(self, events: Iterable[AlertEvent]) -> None:
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self.transaction() as connection:
            entries = self.mark_sent_with_connection(connection, events, stamp)
        self.sent.update(entries)

    def record_cycle(self, account_ids: List[str], snapshots: List[AccountSnapshot], alert_events: List[AlertEvent], dry_run: bool = False):
        return MySQLMonitoringCycleRecorder(self.runtime_settings, monitor_store=self).record_cycle(account_ids, snapshots, alert_events, dry_run=dry_run)

    def write(self) -> None:
        pass


class MySQLMonitoringCycleRecorder(MySQLOperationalConnection):
    def __init__(self, settings: Dict[str, str] = None, monitor_store: MySQLMonitorStore = None):
        self.monitor_store = monitor_store
        super().__init__(settings)
        if self.monitor_store is None:
            self.monitor_store = MySQLMonitorStore(settings)

    def record_cycle(self, account_ids: List[str], snapshots: List[AccountSnapshot], alert_events: List[AlertEvent], dry_run: bool = False):
        if dry_run:
            return MonitoringCycleRecordResult(False, 0, "dry-run")
        snapshot_states = {snapshot.account_id: snapshot.to_monitor_state() for snapshot in snapshots}
        delivered = bool(alert_events)
        alert_source_event = alerts_detected_event(alert_events) if alert_events else None
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        queued = 0
        sent_entries: Dict[str, str] = {}
        notification_store = MySQLNotificationJobStore(self.runtime_settings)
        model_review_store = MySQLModelReviewJobStore(self.runtime_settings)
        with self.transaction() as connection:
            for snapshot in snapshots:
                insert_domain_event_with_connection(connection, snapshot_collected_event(snapshot))
            if alert_source_event:
                insert_domain_event_with_connection(connection, alert_source_event)
                for event in alert_events:
                    context = alert_context(event)
                    message = MySQLNotificationTemplateStore(self.runtime_settings).render(event.rule, context)
                    job = NotificationJob.create(
                        message,
                        account_id=event.account_id,
                        account_label=event.account_label,
                        message_type=event.rule or "alert",
                        source_event_id=alert_source_event.event_id,
                        source_event_name=alert_source_event.name,
                        dedupe_key=":".join(["outbox", alert_source_event.event_id, event.key]),
                        context=context,
                    )
                    if notification_store.enqueue_with_connection(connection, job):
                        queued += 1
                model_review_store.enqueue_from_event_with_connection(connection, alert_source_event)
                sent_entries = self.monitor_store.mark_sent_with_connection(connection, alert_events, stamp)
            insert_domain_event_with_connection(
                connection,
                monitoring_cycle_completed_event(list(account_ids or []), len(snapshots), len(alert_events), False, delivered),
            )
            for account_id, state in snapshot_states.items():
                self.monitor_store.upsert_snapshot_state_with_connection(connection, account_id, state, stamp)
        self.monitor_store.previous.update(snapshot_states)
        self.monitor_store.sent.update(sent_entries)
        return MonitoringCycleRecordResult(delivered, queued, "queued=" + str(queued))


class MySQLEventLog(MySQLOperationalConnection):
    def handle(self, event: DomainEvent) -> None:
        with self.transaction() as connection:
            insert_domain_event_with_connection(connection, event)

    def insert_event_dict(self, event: Dict[str, object]) -> None:
        self.handle(DomainEvent.from_dict(event))

    def events(self, name: str = "", aggregate_id: str = "", limit: int = 0) -> List[DomainEvent]:
        clauses = []
        params = []
        if name:
            clauses.append("name = %s")
            params.append(name)
        if aggregate_id:
            clauses.append("aggregate_id = %s")
            params.append(aggregate_id)
        sql = "SELECT event_json FROM domain_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_at, event_id"
        if limit:
            sql += " LIMIT %s"
            params.append(int(limit))
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [DomainEvent.from_dict(_json_loads(row["event_json"], {})) for row in rows]

    def latest_events(self, limit: int = 12) -> List[DomainEvent]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT event_json FROM domain_events ORDER BY occurred_at DESC, event_id DESC LIMIT %s",
                (max(1, min(200, int(limit or 12))),),
            ).fetchall()
        return [DomainEvent.from_dict(_json_loads(row["event_json"], {})) for row in reversed(rows)]

    def latest_events_by_name(self, names: Iterable[str]) -> Dict[str, DomainEvent]:
        result = {}
        with self.connect() as connection:
            for name in [str(item or "").strip() for item in names or [] if str(item or "").strip()]:
                row = connection.execute(
                    "SELECT event_json FROM domain_events WHERE name = %s ORDER BY occurred_at DESC, event_id DESC LIMIT 1",
                    (name,),
                ).fetchone()
                if row:
                    result[name] = DomainEvent.from_dict(_json_loads(row["event_json"], {}))
        return result

    def event_counts(self) -> Dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT name, COUNT(*) AS count FROM domain_events GROUP BY name").fetchall()
        return {row["name"]: int(row["count"] or 0) for row in rows}


class MySQLModelReviewJobStore(MySQLOperationalConnection):
    def jobs(self) -> List[ModelReviewJob]:
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM model_review_jobs ORDER BY created_at, job_id").fetchall()
        return [ModelReviewJob.from_dict(_json_loads(row["payload_json"], {})) for row in rows]

    def upsert_job_with_connection(self, connection, job: ModelReviewJob) -> None:
        payload = job.to_dict()
        connection.execute(
            """
            INSERT INTO model_review_jobs (
                job_id, account_id, account_label, symbol, title, alert_key, status, attempts,
                created_at, updated_at, result, last_error, alert_lines_json, payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE account_id = VALUES(account_id), account_label = VALUES(account_label),
                symbol = VALUES(symbol), title = VALUES(title), alert_key = VALUES(alert_key),
                status = VALUES(status), attempts = VALUES(attempts), updated_at = VALUES(updated_at),
                result = VALUES(result), last_error = VALUES(last_error),
                alert_lines_json = VALUES(alert_lines_json), payload_json = VALUES(payload_json)
            """,
            (
                job.job_id,
                job.account_id,
                job.account_label,
                job.symbol,
                job.title,
                str(job.alert_key or "")[:191],
                job.status,
                job.attempts,
                job.created_at,
                job.updated_at,
                job.result,
                job.last_error,
                json_dumps(job.alert_lines),
                json_dumps(payload),
            ),
        )

    def upsert_job(self, job: ModelReviewJob) -> None:
        with self.transaction() as connection:
            self.upsert_job_with_connection(connection, job)

    def enqueue_with_connection(self, connection, job: ModelReviewJob) -> bool:
        existing = connection.execute("SELECT job_id FROM model_review_jobs WHERE job_id = %s", (job.job_id,)).fetchone()
        if existing:
            return False
        self.upsert_job_with_connection(connection, job)
        return True

    def enqueue(self, job: ModelReviewJob) -> bool:
        with self.transaction() as connection:
            return self.enqueue_with_connection(connection, job)

    def enqueue_from_event_with_connection(self, connection, event: DomainEvent) -> int:
        count = 0
        for item in model_review_payloads_from_event(event):
            if self.enqueue_with_connection(connection, ModelReviewJob.create(item)):
                count += 1
        return count

    def enqueue_from_event(self, event: DomainEvent) -> int:
        with self.transaction() as connection:
            return self.enqueue_from_event_with_connection(connection, event)

    def claim_pending(self, limit: int = 1, stale_after_minutes: int = 30) -> List[ModelReviewJob]:
        stamp = utc_now()
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT job_id, payload_json FROM model_review_jobs
                WHERE status IN ('pending', 'failed')
                ORDER BY created_at, job_id
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (max(1, int(limit or 1)),),
            ).fetchall()
            jobs = []
            for row in rows:
                job = ModelReviewJob.from_dict(_json_loads(row["payload_json"], {}))
                job.status = "processing"
                job.attempts += 1
                job.updated_at = stamp
                job.last_error = ""
                payload = job.to_dict()
                connection.execute(
                    """
                    UPDATE model_review_jobs
                    SET status = %s, attempts = %s, updated_at = %s, last_error = %s,
                        processing_started_at = %s, payload_json = %s
                    WHERE job_id = %s
                    """,
                    (job.status, job.attempts, job.updated_at, job.last_error, stamp, json_dumps(payload), job.job_id),
                )
                jobs.append(job)
        return jobs

    def pending(self, limit: int = 1) -> List[ModelReviewJob]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM model_review_jobs WHERE status IN ('pending', 'failed') ORDER BY created_at, job_id LIMIT %s",
                (int(limit or 1),),
            ).fetchall()
        return [ModelReviewJob.from_dict(_json_loads(row["payload_json"], {})) for row in rows]

    def mark_done(self, job: ModelReviewJob, result: str) -> None:
        job.status = "done"
        job.result = result
        job.last_error = ""
        job.updated_at = utc_now()
        self.upsert_job(job)

    def mark_failed(self, job: ModelReviewJob, error: str) -> None:
        job.status = "failed"
        job.last_error = error
        job.updated_at = utc_now()
        self.upsert_job(job)

    def mark_processing(self, job: ModelReviewJob) -> ModelReviewJob:
        job.status = "processing"
        job.attempts += 1
        job.updated_at = utc_now()
        self.upsert_job(job)
        return job

    def update(self, updated: ModelReviewJob) -> None:
        self.upsert_job(updated)

    def summary(self) -> Dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM model_review_jobs GROUP BY status").fetchall()
        return {row["status"]: int(row["count"] or 0) for row in rows}


class MySQLNotificationJobStore(MySQLOperationalConnection):
    def __init__(self, settings: Dict[str, str] = None):
        super().__init__(settings)
        if not self.notification_rule_defaults_exist():
            MySQLNotificationRuleStore(self.runtime_settings)

    def notification_rule_defaults_exist(self) -> bool:
        message_types = list(DEFAULT_NOTIFICATION_RULES.keys())
        if not message_types:
            return True
        placeholders = ",".join(["%s"] * len(message_types))
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM notification_rules WHERE message_type IN (" + placeholders + ")",
                message_types,
            ).fetchone()
        return int(row["count"] if row else 0) >= len(message_types)

    def jobs(self) -> List[NotificationJob]:
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM notification_jobs ORDER BY created_at, job_id").fetchall()
        return [NotificationJob.from_dict(_json_loads(row["payload_json"], {})) for row in rows]

    def recent(self, limit: int = 40, message_type: str = "", status: str = "") -> List[NotificationJob]:
        clauses = []
        params = []
        if str(message_type or "").strip():
            clauses.append("message_type = %s")
            params.append(str(message_type or "").strip())
        if str(status or "").strip():
            clauses.append("status = %s")
            params.append(str(status or "").strip())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(200, int(limit or 40))))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM notification_jobs" + where + " ORDER BY created_at DESC, job_id DESC LIMIT %s",
                params,
            ).fetchall()
        return [NotificationJob.from_dict(_json_loads(row["payload_json"], {})) for row in rows]

    def upsert_job_with_connection(self, connection, job: NotificationJob) -> None:
        payload = job.to_dict()
        dedupe_value = str(job.dedupe_key or "").strip()[:191] or None
        cursor = connection.execute(
            """
            UPDATE notification_jobs
            SET account_id = %s,
                account_label = %s,
                message_type = %s,
                source_event_id = %s,
                source_event_name = %s,
                dedupe_key = %s,
                status = %s,
                attempts = %s,
                created_at = %s,
                updated_at = %s,
                last_error = %s,
                text = %s,
                payload_json = %s
            WHERE job_id = %s
            """,
            (
                job.account_id,
                job.account_label,
                job.message_type,
                job.source_event_id,
                job.source_event_name,
                dedupe_value,
                job.status,
                job.attempts,
                job.created_at,
                job.updated_at,
                job.last_error,
                job.text,
                json_dumps(payload),
                job.job_id,
            ),
        )
        if cursor.rowcount:
            return
        connection.execute(
            """
            INSERT INTO notification_jobs (
                job_id, account_id, account_label, message_type, source_event_id, source_event_name,
                dedupe_key, status, attempts, created_at, updated_at, last_error, text, payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                job.job_id,
                job.account_id,
                job.account_label,
                job.message_type,
                job.source_event_id,
                job.source_event_name,
                dedupe_value,
                job.status,
                job.attempts,
                job.created_at,
                job.updated_at,
                job.last_error,
                job.text,
                json_dumps(payload),
            ),
        )

    def upsert_job(self, job: NotificationJob) -> None:
        with self.transaction() as connection:
            self.upsert_job_with_connection(connection, job)

    def rule_for_connection(self, connection, message_type: str) -> NotificationRuleConfig:
        key = str(message_type or "notification").strip() or "notification"
        row = connection.execute("SELECT * FROM notification_rules WHERE message_type = %s", (key,)).fetchone()
        return rule_from_row(row) if row else default_notification_rule(key)

    def similar_history_for_rule(self, job: NotificationJob, rule: NotificationRuleConfig, fingerprint: str):
        with self.connect() as connection:
            return self.similar_history_with_connection(connection, job, rule, fingerprint)

    def similar_history_with_connection(
        self,
        connection,
        job: NotificationJob,
        rule: NotificationRuleConfig,
        fingerprint: str,
    ):
        if not rule.similarity_enabled or not int(rule.similarity_window_minutes or 0) or not fingerprint:
            similarity_minutes = 0
        else:
            similarity_minutes = int(rule.similarity_window_minutes or 0)
        state_minutes = int(rule.state_cooldown_minutes or 0) + 60 if rule.state_cooldown_enabled and int(rule.state_cooldown_minutes or 0) else 0
        history_minutes = max(similarity_minutes, state_minutes)
        if not history_minutes or not fingerprint:
            return 0, 0, {}, ""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=history_minutes)
        cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
        rows = connection.execute(
            """
            SELECT payload_json, created_at, status FROM notification_jobs
            WHERE message_type = %s AND created_at >= %s AND status IN ('pending', 'processing', 'done')
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (job.message_type, cutoff_text, NOTIFICATION_HISTORY_LOOKBACK_LIMIT),
        ).fetchall()
        count = 0
        previous_score = 0
        most_recent_context: Dict[str, object] = {}
        most_recent_at = ""
        for row in rows:
            previous = NotificationJob.from_dict(_json_loads(row["payload_json"], {}))
            if previous.job_id == job.job_id:
                continue
            previous_context = previous.context or {}
            previous_fingerprint = str(previous_context.get("honeyFingerprint") or notification_fingerprint(previous, rule))
            if previous_fingerprint != fingerprint:
                continue
            status = str(row["status"] or "").strip()
            if status != "done" and not notification_history_is_recent_in_flight(row):
                continue
            count += 1
            if not most_recent_context:
                most_recent_context = dict(previous_context)
            if status == "done" and not most_recent_at:
                most_recent_at = row["created_at"] or previous.created_at
            previous_score = max(previous_score, int(previous_context.get("honeyScore") or 0))
        return count, previous_score, most_recent_context, most_recent_at

    def evaluate_job_with_connection(self, connection, job: NotificationJob):
        rule = self.rule_for_connection(connection, job.message_type)
        decision = evaluate_notification_rule(job, rule)
        recent_count, previous_score, previous_context, last_sent_at = self.similar_history_with_connection(connection, job, rule, decision.fingerprint)
        decision = apply_state_cooldown_rule(
            decision,
            rule,
            recent_count,
            previous_score,
            previous_context,
            last_sent_at,
            age_minutes_since(last_sent_at),
            job,
        )
        decision = apply_similarity_rule(decision, rule, recent_count, previous_score, previous_context, job)
        return apply_market_hours_rule(decision, rule, job)

    def enqueue_with_connection(self, connection, job: NotificationJob) -> bool:
        if not job.text.strip():
            return False
        existing = connection.execute("SELECT job_id FROM notification_jobs WHERE job_id = %s", (job.job_id,)).fetchone()
        if existing:
            return False
        dedupe_value = str(job.dedupe_key or "").strip()[:191]
        if dedupe_value:
            existing = connection.execute(
                "SELECT job_id FROM notification_jobs WHERE dedupe_key = %s",
                (dedupe_value,),
            ).fetchone()
            if existing:
                return False

        decision = self.evaluate_job_with_connection(connection, job)
        context = dict(job.context or {})
        context.update(decision.to_context())
        freshness_decision = evaluate_notification_data_freshness(context, self.runtime_settings)
        context.update(freshness_decision.to_context())
        job.context = context
        if decision.should_send and not freshness_decision.should_send:
            job.status = "suppressed"
            job.updated_at = utc_now()
            job.last_error = "데이터 신선도 기준 미통과로 발송하지 않았습니다. " + str(freshness_decision.reason or "")
            job.context["honeySuppressionReason"] = "stale_data"
            try:
                self.upsert_job_with_connection(connection, job)
            except Exception as error:
                if _is_duplicate_key_error(error):
                    return False
                raise
            return False
        if not decision.should_send:
            job.status = "suppressed"
            job.updated_at = utc_now()
            if decision.suppression_reason == "market_closed":
                job.last_error = "장 시간 외라 발송하지 않았습니다. " + str(decision.market_hours_reason or "")
            elif decision.suppression_reason == "state_cooldown":
                job.last_error = decision.state_reason or "같은 임계값 상태가 지속되어 발송하지 않았습니다."
            else:
                job.last_error = "발송 우선도 " + str(decision.score) + "이 기준 " + str(decision.threshold) + "보다 낮아 발송하지 않았습니다."
            try:
                self.upsert_job_with_connection(connection, job)
            except Exception as error:
                if _is_duplicate_key_error(error):
                    return False
                raise
            return False

        try:
            self.upsert_job_with_connection(connection, job)
        except Exception as error:
            if _is_duplicate_key_error(error):
                return False
            raise
        return True

    def enqueue(self, job: NotificationJob) -> bool:
        with self.transaction() as connection:
            return self.enqueue_with_connection(connection, job)

    def pending(self, limit: int = 10) -> List[NotificationJob]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM notification_jobs
                WHERE status IN ('pending', 'failed')
                ORDER BY created_at, job_id
                LIMIT %s
                """,
                (int(limit or 10),),
            ).fetchall()
        return [NotificationJob.from_dict(_json_loads(row["payload_json"], {})) for row in rows]

    def claim_pending(self, limit: int = 10, stale_after_minutes: int = 30) -> List[NotificationJob]:
        stamp = utc_now()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max(1, int(stale_after_minutes or 30)))).isoformat().replace("+00:00", "Z")
        requested = max(1, int(limit or 10))
        claimed: List[NotificationJob] = []
        with self.transaction() as connection:
            query_specs = [
                (
                    """
                    SELECT job_id, payload_json FROM notification_jobs
                    WHERE status = 'pending'
                    ORDER BY created_at, job_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (),
                ),
                (
                    """
                    SELECT job_id, payload_json FROM notification_jobs
                    WHERE status = 'processing'
                      AND COALESCE(NULLIF(processing_started_at, ''), NULLIF(updated_at, ''), created_at) <= %s
                    ORDER BY created_at, job_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (cutoff,),
                ),
                (
                    """
                    SELECT job_id, payload_json FROM notification_jobs
                    WHERE status = 'failed' AND attempts < %s
                    ORDER BY attempts, created_at, job_id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (MAX_NOTIFICATION_DELIVERY_ATTEMPTS,),
                ),
            ]
            for sql, params in query_specs:
                remaining = requested - len(claimed)
                if remaining <= 0:
                    break
                rows = connection.execute(sql, tuple(params) + (remaining,)).fetchall()
                for row in rows:
                    job = NotificationJob.from_dict(_json_loads(row["payload_json"], {}))
                    if not job.job_id:
                        continue
                    job.status = "processing"
                    job.attempts += 1
                    job.updated_at = stamp
                    job.last_error = ""
                    payload = job.to_dict()
                    cursor = connection.execute(
                        """
                        UPDATE notification_jobs
                        SET status = %s, attempts = %s, updated_at = %s, last_error = %s,
                            processing_started_at = %s, payload_json = %s
                        WHERE job_id = %s
                          AND (
                            status = 'pending'
                            OR (status = 'failed' AND attempts < %s)
                            OR (
                              status = 'processing'
                              AND COALESCE(NULLIF(processing_started_at, ''), NULLIF(updated_at, ''), created_at) <= %s
                            )
                          )
                        """,
                        (
                            job.status,
                            job.attempts,
                            job.updated_at,
                            job.last_error,
                            stamp,
                            json_dumps(payload),
                            job.job_id,
                            MAX_NOTIFICATION_DELIVERY_ATTEMPTS,
                            cutoff,
                        ),
                    )
                    if cursor.rowcount:
                        claimed.append(job)
        return claimed

    def update(self, updated: NotificationJob) -> None:
        self.upsert_job(updated)

    def mark_processing(self, job: NotificationJob) -> NotificationJob:
        job.status = "processing"
        job.attempts += 1
        job.updated_at = utc_now()
        with self.transaction() as connection:
            self.upsert_job_with_connection(connection, job)
            connection.execute(
                "UPDATE notification_jobs SET processing_started_at = %s WHERE job_id = %s",
                (job.updated_at, job.job_id),
            )
        return job

    def mark_done(self, job: NotificationJob) -> None:
        job.status = "done"
        job.last_error = ""
        job.updated_at = utc_now()
        self.update(job)

    def mark_failed(self, job: NotificationJob, error: str) -> None:
        job.status = "failed"
        job.last_error = error
        job.updated_at = utc_now()
        self.update(job)

    def mark_suppressed(self, job: NotificationJob, reason: str) -> None:
        job.status = "suppressed"
        job.last_error = str(reason or "알림 정책으로 발송하지 않았습니다.")
        job.updated_at = utc_now()
        self.update(job)

    def summary(self) -> Dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM notification_jobs GROUP BY status").fetchall()
        return {row["status"]: int(row["count"] or 0) for row in rows}


class MySQLMarketQuoteCache(MySQLOperationalConnection):
    def save(self, provider: str, account_id: str, symbol: str, payload: Dict[str, object]) -> None:
        clean_symbol = str(symbol or "").upper().strip()
        if not clean_symbol or not isinstance(payload, dict):
            return
        stamp = utc_now()
        cached = dict(payload)
        cached.setdefault("updatedAt", stamp)
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO market_quote_cache (provider, account_id, symbol, payload_json, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
                (
                    str(provider or "").strip().lower() or "unknown",
                    str(account_id or "").strip(),
                    clean_symbol,
                    json_dumps(cached),
                    stamp,
                ),
            )

    def load(self, provider: str, account_id: str, symbol: str) -> Dict[str, object]:
        clean_symbol = str(symbol or "").upper().strip()
        if not clean_symbol:
            return {}
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json, updated_at
                FROM market_quote_cache
                WHERE provider = %s AND account_id = %s AND symbol = %s
                """,
                (
                    str(provider or "").strip().lower() or "unknown",
                    str(account_id or "").strip(),
                    clean_symbol,
                ),
            ).fetchone()
        if not row:
            return {}
        payload = _json_loads(row["payload_json"], {})
        payload.setdefault("updatedAt", row["updated_at"])
        payload.setdefault("symbol", clean_symbol)
        return payload

    def load_many(self, provider: str, account_id: str, symbols: Iterable[str]) -> Dict[str, Dict[str, object]]:
        clean_symbols = []
        seen = set()
        for symbol in symbols or []:
            clean_symbol = str(symbol or "").upper().strip()
            if not clean_symbol or clean_symbol in seen:
                continue
            seen.add(clean_symbol)
            clean_symbols.append(clean_symbol)
        if not clean_symbols:
            return {}
        placeholders = ",".join(["%s"] * len(clean_symbols))
        params = [
            str(provider or "").strip().lower() or "unknown",
            str(account_id or "").strip(),
            *clean_symbols,
        ]
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT symbol, payload_json, updated_at
                FROM market_quote_cache
                WHERE provider = %s AND account_id = %s AND symbol IN (""" + placeholders + """)
                """,
                params,
            ).fetchall()
        result: Dict[str, Dict[str, object]] = {}
        for row in rows:
            clean_symbol = str(row["symbol"] or "").upper().strip()
            payload = _json_loads(row["payload_json"], {})
            if not payload:
                continue
            payload.setdefault("updatedAt", row["updated_at"])
            payload.setdefault("symbol", clean_symbol)
            result[clean_symbol] = payload
        return result

    def stale_universe_symbols(
        self,
        provider: str,
        account_id: str,
        markets: Iterable[str] = None,
        limit: int = 200,
        max_age_minutes: int = 240,
    ) -> List[Dict[str, object]]:
        clean_markets = [normalize_market(market) for market in (markets or []) if normalize_market(market)]
        limit_value = max(1, min(1000, int(limit or 200)))
        age_minutes = max(0, int(max_age_minutes or 0))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
        cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
        clauses = ["su.active = 1"]
        params: List[object] = [
            str(provider or "").strip().lower() or "unknown",
            str(account_id or "").strip(),
        ]
        if clean_markets:
            clauses.append("su.market IN (" + ",".join(["%s"] * len(clean_markets)) + ")")
            params.extend(clean_markets)
        clauses.append("(mq.updated_at IS NULL OR mq.updated_at <= %s)")
        params.append(cutoff_text)
        sql = """
            SELECT su.symbol, su.name, su.market, su.exchange, su.currency, su.sector,
                   su.asset_type, mq.updated_at AS quote_updated_at
            FROM symbol_universe su
            LEFT JOIN market_quote_cache mq
              ON mq.provider = %s AND mq.account_id = %s AND mq.symbol = su.symbol
            WHERE """ + " AND ".join(clauses) + """
            ORDER BY
                CASE WHEN mq.updated_at IS NULL THEN 0 ELSE 1 END,
                COALESCE(mq.updated_at, ''),
                CASE su.market WHEN 'KOSPI' THEN 1 WHEN 'KOSDAQ' THEN 2 WHEN 'NASDAQ' THEN 3 ELSE 9 END,
                su.symbol
            LIMIT %s
        """
        with self.connect() as connection:
            rows = connection.execute(sql, params + [limit_value]).fetchall()
        return [
            {
                "symbol": row["symbol"],
                "name": row["name"],
                "market": row["market"],
                "exchange": row["exchange"],
                "currency": row["currency"],
                "sector": row["sector"],
                "assetType": row["asset_type"],
                "quoteUpdatedAt": row["quote_updated_at"] or "",
            }
            for row in rows
        ]

    def summary(self, provider: str, account_id: str) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT symbol, payload_json, updated_at
                FROM market_quote_cache
                WHERE provider = %s AND account_id = %s
                ORDER BY updated_at DESC
                """,
                (
                    str(provider or "").strip().lower() or "unknown",
                    str(account_id or "").strip(),
                ),
            ).fetchall()
        markets: Dict[str, int] = {}
        qualities: Dict[str, int] = {}
        latest = ""
        for row in rows:
            latest = latest or row["updated_at"]
            payload = _json_loads(row["payload_json"], {})
            market = str(payload.get("market") or "UNKNOWN")
            quality = str(payload.get("dataQuality") or "unknown")
            markets[market] = markets.get(market, 0) + 1
            qualities[quality] = qualities.get(quality, 0) + 1
        return {
            "provider": str(provider or "").strip().lower() or "unknown",
            "accountId": str(account_id or "").strip(),
            "count": len(rows),
            "latestUpdatedAt": latest,
            "markets": markets,
            "dataQuality": qualities,
        }


class MySQLSymbolUniverseStore(MySQLOperationalConnection):
    def upsert_many_with_connection(self, connection, symbols: Iterable[ListedSymbol], stamp: str = "") -> int:
        items = [item for item in symbols if item.symbol and item.market]
        if not items:
            return 0
        stamp = stamp or utc_now()
        for item in items:
            existing = connection.execute(
                "SELECT first_seen_at FROM symbol_universe WHERE market = %s AND symbol = %s",
                (item.market, item.symbol),
            ).fetchone()
            if existing and existing["first_seen_at"]:
                item.first_seen_at = existing["first_seen_at"]
            payload = item.to_dict(max_age_hours=24)
            connection.execute(
                """
                INSERT INTO symbol_universe (
                    market, symbol, name, exchange, currency, sector, asset_type,
                    source, source_url, active, fetched_at, first_seen_at, last_seen_at,
                    payload_json, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    name = VALUES(name),
                    exchange = VALUES(exchange),
                    currency = VALUES(currency),
                    sector = VALUES(sector),
                    asset_type = VALUES(asset_type),
                    source = VALUES(source),
                    source_url = VALUES(source_url),
                    active = VALUES(active),
                    fetched_at = VALUES(fetched_at),
                    last_seen_at = VALUES(last_seen_at),
                    payload_json = VALUES(payload_json),
                    updated_at = VALUES(updated_at)
                """,
                (
                    item.market,
                    item.symbol,
                    item.name,
                    item.exchange,
                    item.currency,
                    item.sector,
                    item.asset_type,
                    item.source,
                    item.source_url,
                    1 if item.active else 0,
                    item.fetched_at,
                    item.first_seen_at,
                    item.last_seen_at,
                    json_dumps(payload),
                    stamp,
                ),
            )
        return len(items)

    def upsert_many(self, symbols: Iterable[ListedSymbol]) -> int:
        with self.transaction() as connection:
            return self.upsert_many_with_connection(connection, symbols)

    def row_to_symbol(self, row) -> ListedSymbol:
        payload = _json_loads(row["payload_json"], {})
        payload.update({
            "symbol": row["symbol"],
            "name": row["name"],
            "market": row["market"],
            "exchange": row["exchange"],
            "currency": row["currency"],
            "sector": row["sector"],
            "assetType": row["asset_type"],
            "source": row["source"],
            "sourceUrl": row["source_url"],
            "active": bool(row["active"]),
            "fetchedAt": row["fetched_at"],
            "firstSeenAt": row["first_seen_at"],
            "lastSeenAt": row["last_seen_at"],
        })
        return ListedSymbol.from_dict(payload)

    def symbol_search_clauses(self, query: str = "", market: str = ""):
        query_value = str(query or "").strip()
        market_value = normalize_market(market)
        clauses = ["active = 1"]
        params: List[object] = []
        if market_value:
            clauses.append("market = %s")
            params.append(market_value)
        if query_value:
            clauses.append("(symbol LIKE %s OR name LIKE %s)")
            like = "%" + query_value.upper() + "%"
            params.extend([like, "%" + query_value + "%"])
        return query_value, clauses, params

    def search(self, query: str = "", market: str = "", limit: int = 80, offset: int = 0) -> List[ListedSymbol]:
        query_value, clauses, params = self.symbol_search_clauses(query, market)
        limit_value = max(1, min(500, int(limit or 80)))
        offset_value = max(0, int(offset or 0))
        exact_symbol = normalize_symbol(query_value)
        sql = """
            SELECT * FROM symbol_universe
            WHERE """ + " AND ".join(clauses) + """
            ORDER BY
                CASE WHEN %s != '' AND symbol = %s THEN 0 WHEN %s != '' AND symbol LIKE %s THEN 1 ELSE 2 END,
                CASE market WHEN 'KOSPI' THEN 1 WHEN 'KOSDAQ' THEN 2 WHEN 'NASDAQ' THEN 3 ELSE 9 END,
                symbol
            LIMIT %s OFFSET %s
        """
        with self.connect() as connection:
            rows = connection.execute(
                sql,
                params + [exact_symbol, exact_symbol, exact_symbol, exact_symbol + "%", limit_value, offset_value],
            ).fetchall()
        return [self.row_to_symbol(row) for row in rows]

    def search_count(self, query: str = "", market: str = "") -> int:
        _, clauses, params = self.symbol_search_clauses(query, market)
        sql = "SELECT COUNT(*) AS count FROM symbol_universe WHERE " + " AND ".join(clauses)
        with self.connect() as connection:
            row = connection.execute(sql, params).fetchone()
        return int(row["count"] if row else 0)

    def get(self, symbol: str, market: str = "") -> Optional[ListedSymbol]:
        clean_symbol = normalize_symbol(symbol)
        clean_market = normalize_market(market)
        if not clean_symbol:
            return None
        with self.connect() as connection:
            if clean_market:
                row = connection.execute(
                    "SELECT * FROM symbol_universe WHERE market = %s AND symbol = %s",
                    (clean_market, clean_symbol),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM symbol_universe
                    WHERE symbol = %s
                    ORDER BY CASE market WHEN 'KOSPI' THEN 1 WHEN 'KOSDAQ' THEN 2 WHEN 'NASDAQ' THEN 3 ELSE 9 END
                    LIMIT 1
                    """,
                    (clean_symbol,),
                ).fetchone()
        return self.row_to_symbol(row) if row else None

    def counts_by_market(self) -> Dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT market, COUNT(*) AS count FROM symbol_universe WHERE active = 1 GROUP BY market"
            ).fetchall()
        return {row["market"]: int(row["count"]) for row in rows}

    def latest_seen_by_market(self) -> Dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT market, MAX(last_seen_at) AS last_seen_at FROM symbol_universe WHERE active = 1 GROUP BY market"
            ).fetchall()
        return {row["market"]: row["last_seen_at"] or "" for row in rows}

    def mark_source_with_connection(
        self,
        connection,
        market: str,
        source: str,
        source_url: str,
        status: str,
        count: int = 0,
        error: str = "",
        stamp: str = "",
    ) -> None:
        stamp = stamp or symbol_utc_now_iso()
        success_at = stamp if status == "ok" else ""
        existing = connection.execute(
            "SELECT last_success_at FROM symbol_universe_sources WHERE market = %s",
            (normalize_market(market),),
        ).fetchone()
        last_success_at = success_at or (existing["last_success_at"] if existing else "")
        connection.execute(
            """
            INSERT INTO symbol_universe_sources (
                market, source, source_url, status, record_count, last_attempt_at,
                last_success_at, last_error, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                source = VALUES(source),
                source_url = VALUES(source_url),
                status = VALUES(status),
                record_count = VALUES(record_count),
                last_attempt_at = VALUES(last_attempt_at),
                last_success_at = VALUES(last_success_at),
                last_error = VALUES(last_error),
                updated_at = VALUES(updated_at)
            """,
            (
                normalize_market(market),
                str(source or ""),
                str(source_url or ""),
                str(status or ""),
                int(count or 0),
                stamp,
                last_success_at,
                str(error or ""),
                stamp,
            ),
        )

    def mark_source(self, market: str, source: str, source_url: str, status: str, count: int = 0, error: str = "") -> None:
        with self.transaction() as connection:
            self.mark_source_with_connection(connection, market, source, source_url, status, count, error)

    def refresh_market(self, market: str, source: str, source_url: str, symbols: Iterable[ListedSymbol]) -> int:
        stamp = symbol_utc_now_iso()
        with self.transaction() as connection:
            count = self.upsert_many_with_connection(connection, symbols, stamp)
            self.mark_source_with_connection(connection, market, source, source_url, "ok", count=count, stamp=stamp)
        return count

    def source_states(self) -> List[Dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT market, source, source_url, status, record_count, last_attempt_at,
                       last_success_at, last_error, updated_at
                FROM symbol_universe_sources
                ORDER BY CASE market WHEN 'KOSPI' THEN 1 WHEN 'KOSDAQ' THEN 2 WHEN 'NASDAQ' THEN 3 ELSE 9 END
                """
            ).fetchall()
        return [
            {
                "market": row["market"],
                "source": row["source"],
                "sourceUrl": row["source_url"],
                "status": row["status"],
                "recordCount": int(row["record_count"]),
                "lastAttemptAt": row["last_attempt_at"],
                "lastSuccessAt": row["last_success_at"],
                "lastError": row["last_error"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]


class MySQLResearchEvidenceStore(MySQLOperationalConnection):
    def upsert_many(self, items: Iterable[ResearchEvidence]) -> int:
        stamp = utc_now()
        written = 0
        changed_symbols: List[str] = []
        changed_items: List[ResearchEvidence] = []
        with self.transaction() as connection:
            for item in items or []:
                evidence_id = str(item.evidence_id or "").strip()
                if not evidence_id:
                    continue
                symbol = str(item.symbol or "").upper().strip()
                kind = str(item.kind or "").strip()
                source = str(item.source or "").strip()
                title = str(item.title or "").strip()
                observed_at = str(item.observed_at or item.published_at or stamp).strip()
                published_at = str(item.published_at or item.observed_at or "").strip()
                dedupe_key = "|".join([symbol, kind, source, title, str(item.url or "").strip()])[:191]
                payload = dict(item.raw_payload or {})
                current_signature = fact_signature(research_evidence_change_payload(
                    symbol,
                    kind,
                    source,
                    title,
                    str(item.summary or ""),
                    str(item.url or ""),
                    published_at,
                    str(item.polarity or "context"),
                    float(item.impact_score or 0),
                    float(item.confidence or 0),
                    payload,
                ))
                previous_row = connection.execute(
                    """
                    SELECT symbol, kind, source, title, summary, url, published_at,
                           polarity, impact_score, confidence, payload_json
                    FROM research_evidence
                    WHERE evidence_id = %s
                    """,
                    (evidence_id,),
                ).fetchone()
                previous_signature = ""
                if previous_row:
                    previous_payload = _json_loads(previous_row["payload_json"], {})
                    previous_signature = fact_signature(research_evidence_change_payload(
                        str(previous_row["symbol"] or ""),
                        str(previous_row["kind"] or ""),
                        str(previous_row["source"] or ""),
                        str(previous_row["title"] or ""),
                        str(previous_row["summary"] or ""),
                        str(previous_row["url"] or ""),
                        str(previous_row["published_at"] or ""),
                        str(previous_row["polarity"] or "context"),
                        float(previous_row["impact_score"] or 0),
                        float(previous_row["confidence"] or 0),
                        previous_payload,
                    ))
                connection.execute(
                    """
                    INSERT INTO research_evidence (
                        evidence_id, symbol, kind, source, title, summary, url, published_at,
                        observed_at, first_seen_at, last_seen_at, polarity, impact_score,
                        confidence, dedupe_key, payload_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        symbol = VALUES(symbol),
                        kind = VALUES(kind),
                        source = VALUES(source),
                        title = VALUES(title),
                        summary = VALUES(summary),
                        url = VALUES(url),
                        published_at = VALUES(published_at),
                        observed_at = VALUES(observed_at),
                        last_seen_at = VALUES(last_seen_at),
                        polarity = VALUES(polarity),
                        impact_score = VALUES(impact_score),
                        confidence = VALUES(confidence),
                        dedupe_key = VALUES(dedupe_key),
                        payload_json = VALUES(payload_json)
                    """,
                    (
                        evidence_id,
                        symbol,
                        kind,
                        source,
                        title,
                        str(item.summary or ""),
                        str(item.url or ""),
                        published_at,
                        observed_at,
                        stamp,
                        stamp,
                        str(item.polarity or "context"),
                        float(item.impact_score or 0),
                        float(item.confidence or 0),
                        dedupe_key,
                        json_dumps(payload),
                    ),
                )
                if not previous_row or current_signature != previous_signature:
                    written += 1
                    if symbol and symbol not in changed_symbols:
                        changed_symbols.append(symbol)
                    changed_items.append(item)
        self.last_changed_symbols = changed_symbols
        self.last_changed_items = changed_items
        return written

    def latest(self, symbol: str = "", kind: str = "", limit: int = 50) -> List[ResearchEvidence]:
        conditions = []
        params: List[object] = []
        normalized_symbol = str(symbol or "").upper().strip()
        normalized_kind = str(kind or "").strip()
        if normalized_symbol:
            conditions.append("symbol = %s")
            params.append(normalized_symbol)
        if normalized_kind:
            conditions.append("kind = %s")
            params.append(normalized_kind)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(max(1, min(500, int(limit or 50))))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_evidence" + where + " ORDER BY last_seen_at DESC, published_at DESC, evidence_id DESC LIMIT %s",
                params,
            ).fetchall()
        return [research_evidence_from_row(row) for row in rows]

    def delete(self, evidence_id: str) -> bool:
        normalized_id = str(evidence_id or "").strip()
        if not normalized_id:
            return False
        with self.transaction() as connection:
            cursor = connection.execute("DELETE FROM research_evidence WHERE evidence_id = %s", (normalized_id,))
        return int(cursor.rowcount or 0) > 0

    def summary_counts(self, column: str, limit: int = 20) -> List[Dict[str, object]]:
        if column not in {"symbol", "kind", "source", "polarity"}:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT """ + column + """ AS name, COUNT(*) AS count, MAX(last_seen_at) AS latest_seen_at
                FROM research_evidence
                WHERE """ + column + """ != ''
                GROUP BY """ + column + """
                ORDER BY count DESC, latest_seen_at DESC
                LIMIT %s
                """,
                (max(1, min(100, int(limit or 20))),),
            ).fetchall()
        return [
            {
                "name": row["name"],
                "count": int(row["count"] or 0),
                "latestSeenAt": row["latest_seen_at"],
            }
            for row in rows
        ]

    def summary(self) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count, MAX(last_seen_at) AS latest_seen_at FROM research_evidence"
            ).fetchone()
        return {
            "total": int(row["count"] or 0) if row else 0,
            "latestSeenAt": row["latest_seen_at"] if row else "",
            "bySymbol": self.summary_counts("symbol"),
            "byKind": self.summary_counts("kind"),
            "bySource": self.summary_counts("source"),
            "byPolarity": self.summary_counts("polarity"),
        }


class MySQLOntologyQualitySampleStore(MySQLOperationalConnection):
    def record(self, sample: OntologyQualitySample) -> None:
        stamp = sample.created_at or utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO ontology_ai_opinion_samples (
                    sample_id, portfolio_id, created_at, overall_score, data_coverage_score,
                    context_coverage_score, reasoning_readiness_score, relation_density_score,
                    entity_count, relation_count, evidence_count, belief_count, opinion_count,
                    reasoning_card_count, data_gap_count, bounded_context_count, high_pressure_count,
                    payload_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    portfolio_id = VALUES(portfolio_id),
                    created_at = VALUES(created_at),
                    overall_score = VALUES(overall_score),
                    data_coverage_score = VALUES(data_coverage_score),
                    context_coverage_score = VALUES(context_coverage_score),
                    reasoning_readiness_score = VALUES(reasoning_readiness_score),
                    relation_density_score = VALUES(relation_density_score),
                    entity_count = VALUES(entity_count),
                    relation_count = VALUES(relation_count),
                    evidence_count = VALUES(evidence_count),
                    belief_count = VALUES(belief_count),
                    opinion_count = VALUES(opinion_count),
                    reasoning_card_count = VALUES(reasoning_card_count),
                    data_gap_count = VALUES(data_gap_count),
                    bounded_context_count = VALUES(bounded_context_count),
                    high_pressure_count = VALUES(high_pressure_count),
                    payload_json = VALUES(payload_json)
                """,
                (
                    sample.sample_id,
                    sample.portfolio_id,
                    stamp,
                    float(sample.overall_score or 0),
                    float(sample.data_coverage_score or 0),
                    float(sample.context_coverage_score or 0),
                    float(sample.reasoning_readiness_score or 0),
                    float(sample.relation_density_score or 0),
                    int(sample.entity_count or 0),
                    int(sample.relation_count or 0),
                    int(sample.evidence_count or 0),
                    int(sample.belief_count or 0),
                    int(sample.opinion_count or 0),
                    int(sample.reasoning_card_count or 0),
                    int(sample.data_gap_count or 0),
                    int(sample.bounded_context_count or 0),
                    int(sample.high_pressure_count or 0),
                    json_dumps(sample.payload),
                ),
            )

    def record_graph(self, graph, source: str = "monitoring", created_at: str = "") -> OntologyQualitySample:
        sample = build_ontology_quality_sample(graph, source=source, created_at=created_at)
        self.record(sample)
        return sample

    def latest(self, portfolio_id: str = "", limit: int = 20) -> List[Dict[str, object]]:
        clauses = []
        params: List[object] = []
        if portfolio_id:
            clauses.append("portfolio_id = %s")
            params.append(str(portfolio_id))
        sql = """
            SELECT sample_id, portfolio_id, created_at, overall_score, data_coverage_score,
                   context_coverage_score, reasoning_readiness_score, relation_density_score,
                   entity_count, relation_count, evidence_count, belief_count, opinion_count,
                   reasoning_card_count, data_gap_count, bounded_context_count, high_pressure_count,
                   payload_json
            FROM ontology_ai_opinion_samples
        """
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(max(1, min(200, int(limit or 20))))
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        result = []
        for row in rows:
            result.append({
                "sampleId": row["sample_id"],
                "portfolioId": row["portfolio_id"],
                "createdAt": row["created_at"],
                "overallScore": row["overall_score"],
                "dataCoverageScore": row["data_coverage_score"],
                "contextCoverageScore": row["context_coverage_score"],
                "reasoningReadinessScore": row["reasoning_readiness_score"],
                "relationDensityScore": row["relation_density_score"],
                "entityCount": row["entity_count"],
                "relationCount": row["relation_count"],
                "evidenceCount": row["evidence_count"],
                "beliefCount": row["belief_count"],
                "opinionCount": row["opinion_count"],
                "reasoningCardCount": row["reasoning_card_count"],
                "dataGapCount": row["data_gap_count"],
                "boundedContextCount": row["bounded_context_count"],
                "highPressureCount": row["high_pressure_count"],
                "payload": _json_loads(row["payload_json"], {}),
            })
        return result
