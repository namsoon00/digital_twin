import hashlib
import json
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
from .mysql_monitoring import MySQLDependencyError, MySQLMonitorAccountJobStore, ensure_mysql_database_exists, mysql_settings
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
from .mysql_notification_jobs import MySQLNotificationJobStore
from .mysql_operational_connection import MYSQL_SCHEMA, MySQLConnectionProxy, MySQLOperationalConnection
from .mysql_operational_events import insert_domain_event_with_connection
from .mysql_operational_helpers import (
    _is_duplicate_key_error,
    _json_loads,
    _sent_key_hash,
    research_evidence_change_payload,
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
        stored_settings = MySQLRuntimeSettingsStore(settings).load()
        self.settings_map = dict(stored_settings)
        self.settings_map.update(settings or {})

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
