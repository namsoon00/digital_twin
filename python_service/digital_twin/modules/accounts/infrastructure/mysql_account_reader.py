"""Read-only account adapter. Cross-owner mutations live in account_transactions."""

from typing import Dict, List, Optional

from digital_twin.domain.accounts import AccountConfig, split_symbols
from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.mysql_operational_core_stores import MySQLRuntimeSettingsStore


class MySQLAccountReader(MySQLOperationalConnection):
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
        watchlist = row["watchlist_symbols"] if row["watchlist_symbols"] is not None else self.settings.get("watchlistSymbols", "")
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
            notification_detail_level=row["notification_detail_level"] or "concise",
            investment_strategy_profile=row["investment_strategy_profile"] or self.settings.get("investmentStrategyProfile", "balanced"),
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
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
        sql += " ORDER BY a.updated_at DESC, a.id"
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
            notification_detail_level=self.settings.get("notificationDetailLevel", "concise"),
            investment_strategy_profile=self.settings.get("investmentStrategyProfile", "balanced"),
        )
