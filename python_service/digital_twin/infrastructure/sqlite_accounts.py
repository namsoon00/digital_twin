import json
import os
import sqlite3
from pathlib import Path
from typing import List, Optional

from ..domain.accounts import AccountConfig, split_symbols
from ..domain.events import DomainEvent
from .settings import data_dir, read_json, runtime_settings, service_db_path, utc_now


def json_dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class AccountRegistry:
    def __init__(self, path: Optional[Path] = None, legacy_path: Optional[Path] = None):
        self.path = path or service_db_path()
        self.legacy_path = legacy_path or data_dir() / "accounts.json"
        self.settings = runtime_settings()
        self.ensure_schema()
        self.import_legacy_accounts_if_needed()

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return connection

    def ensure_schema(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS service_accounts (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'toss',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    watchlist_symbols TEXT NOT NULL DEFAULT '',
                    quiet_hours_enabled INTEGER NOT NULL DEFAULT 1,
                    quiet_hours_start TEXT NOT NULL DEFAULT '22:00',
                    quiet_hours_end TEXT NOT NULL DEFAULT '05:00',
                    quiet_hours_timezone TEXT NOT NULL DEFAULT 'Asia/Seoul',
                    message_delivery_level TEXT NOT NULL DEFAULT 'absoluteBeginner',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            self.ensure_columns(
                connection,
                "service_accounts",
                {
                    "quiet_hours_enabled": "INTEGER NOT NULL DEFAULT 1",
                    "quiet_hours_start": "TEXT NOT NULL DEFAULT '22:00'",
                    "quiet_hours_end": "TEXT NOT NULL DEFAULT '05:00'",
                    "quiet_hours_timezone": "TEXT NOT NULL DEFAULT 'Asia/Seoul'",
                    "message_delivery_level": "TEXT NOT NULL DEFAULT 'absoluteBeginner'",
                },
            )
            connection.execute("""
                UPDATE service_accounts
                SET message_delivery_level = 'absoluteBeginner'
                WHERE message_delivery_level IS NULL OR message_delivery_level = ''
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS toss_credentials (
                    account_id TEXT PRIMARY KEY,
                    base_url TEXT NOT NULL DEFAULT '',
                    client_id TEXT NOT NULL DEFAULT '',
                    client_secret TEXT NOT NULL DEFAULT '',
                    account_seq TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES service_accounts(id) ON DELETE CASCADE
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS telegram_configs (
                    account_id TEXT PRIMARY KEY,
                    notify_provider TEXT NOT NULL DEFAULT '',
                    bot_token TEXT NOT NULL DEFAULT '',
                    chat_id TEXT NOT NULL DEFAULT '',
                    link_url TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES service_accounts(id) ON DELETE CASCADE
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_service_accounts_enabled ON service_accounts(enabled)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS domain_events (
                    event_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL DEFAULT '',
                    occurred_at TEXT NOT NULL,
                    correlation_id TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    event_json TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_domain_events_name_time ON domain_events(name, occurred_at)")

    def ensure_columns(self, connection, table: str, columns) -> None:
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(" + table + ")").fetchall()
        }
        for name, definition in columns.items():
            if name not in existing:
                try:
                    connection.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " " + definition)
                except sqlite3.OperationalError as error:
                    if "duplicate column name" not in str(error).lower():
                        raise

    def rows_count(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM service_accounts").fetchone()
            return int(row["count"] if row else 0)

    def import_legacy_accounts_if_needed(self) -> None:
        if self.rows_count() > 0 or not self.legacy_path.exists():
            return
        payload = read_json(self.legacy_path, {})
        accounts = payload.get("accounts") if isinstance(payload, dict) else None
        if not isinstance(accounts, list) or not accounts:
            return
        for item in accounts:
            if isinstance(item, dict):
                self.upsert(AccountConfig.from_dict(item, self.settings))

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
            SELECT
                a.id,
                a.label,
                a.provider,
                a.enabled,
                a.watchlist_symbols,
                a.quiet_hours_enabled,
                a.quiet_hours_start,
                a.quiet_hours_end,
                a.quiet_hours_timezone,
                a.message_delivery_level,
                COALESCE(t.base_url, '') AS base_url,
                COALESCE(t.client_id, '') AS client_id,
                COALESCE(t.client_secret, '') AS client_secret,
                COALESCE(t.account_seq, '') AS account_seq,
                COALESCE(g.notify_provider, '') AS notify_provider,
                COALESCE(g.bot_token, '') AS bot_token,
                COALESCE(g.chat_id, '') AS chat_id,
                COALESCE(g.link_url, '') AS link_url
            FROM service_accounts a
            LEFT JOIN toss_credentials t ON t.account_id = a.id
            LEFT JOIN telegram_configs g ON g.account_id = a.id
        """
        params = []
        if enabled_only:
            sql += " WHERE a.enabled = ?"
            params.append(1)
        sql += " ORDER BY a.created_at, a.id"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self.account_from_row(row) for row in rows]

    def load(self) -> List[AccountConfig]:
        accounts = self.select_accounts(enabled_only=True)
        if accounts:
            return accounts
        return [self.default_account()]

    def load_all(self) -> List[AccountConfig]:
        accounts = self.select_accounts(enabled_only=False)
        if accounts:
            return accounts
        return [self.default_account()]

    def load_saved(self) -> List[AccountConfig]:
        return self.select_accounts(enabled_only=False)

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

    def save_all(self, accounts: List[AccountConfig]) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("DELETE FROM telegram_configs")
            connection.execute("DELETE FROM toss_credentials")
            connection.execute("DELETE FROM service_accounts")
            for account in accounts:
                self.upsert_with_connection(connection, account)

    def upsert_with_connection(self, connection, account: AccountConfig) -> None:
        stamp = utc_now()
        existing = connection.execute("SELECT created_at FROM service_accounts WHERE id = ?", (account.account_id,)).fetchone()
        connection.execute(
            """
            INSERT INTO service_accounts (
                id, label, provider, enabled, watchlist_symbols,
                quiet_hours_enabled, quiet_hours_start, quiet_hours_end, quiet_hours_timezone,
                message_delivery_level,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                label = excluded.label,
                provider = excluded.provider,
                enabled = excluded.enabled,
                watchlist_symbols = excluded.watchlist_symbols,
                quiet_hours_enabled = excluded.quiet_hours_enabled,
                quiet_hours_start = excluded.quiet_hours_start,
                quiet_hours_end = excluded.quiet_hours_end,
                quiet_hours_timezone = excluded.quiet_hours_timezone,
                message_delivery_level = excluded.message_delivery_level,
                updated_at = excluded.updated_at
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
                existing["created_at"] if existing else stamp,
                stamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO toss_credentials (account_id, base_url, client_id, client_secret, account_seq, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                base_url = excluded.base_url,
                client_id = excluded.client_id,
                client_secret = excluded.client_secret,
                account_seq = excluded.account_seq,
                updated_at = excluded.updated_at
            """,
            (account.account_id, account.base_url, account.client_id, account.client_secret, account.account_seq, stamp),
        )
        connection.execute(
            """
            INSERT INTO telegram_configs (account_id, notify_provider, bot_token, chat_id, link_url, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                notify_provider = excluded.notify_provider,
                bot_token = excluded.bot_token,
                chat_id = excluded.chat_id,
                link_url = excluded.link_url,
                updated_at = excluded.updated_at
            """,
            (
                account.account_id,
                account.notify_provider,
                account.telegram_bot_token,
                account.telegram_chat_id,
                account.notify_link_url,
                stamp,
            ),
        )

    def upsert(self, account: AccountConfig) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            self.upsert_with_connection(connection, account)

    def insert_event_with_connection(self, connection, event: DomainEvent) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO domain_events (
                event_id, name, aggregate_id, occurred_at, correlation_id, payload_json, event_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.name,
                event.aggregate_id,
                event.occurred_at,
                event.correlation_id,
                json_dumps(event.payload),
                json_dumps(event.to_dict()),
            ),
        )

    def upsert_with_event(self, account: AccountConfig, event: DomainEvent) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            self.upsert_with_connection(connection, account)
            self.insert_event_with_connection(connection, event)

    def remove(self, account_id: str) -> bool:
        with self.connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            cursor = connection.execute("DELETE FROM service_accounts WHERE id = ?", (account_id,))
            removed = cursor.rowcount > 0
        if not removed:
            return False
        return True

    def remove_with_event(self, account_id: str, event: DomainEvent) -> bool:
        with self.connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            cursor = connection.execute("DELETE FROM service_accounts WHERE id = ?", (account_id,))
            removed = cursor.rowcount > 0
            if removed:
                self.insert_event_with_connection(connection, event)
        return removed
