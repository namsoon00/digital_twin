"""Accounts-owned configuration contracts; persisted field meanings are unchanged."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from digital_twin.domain.notification_explanation import (
    DEFAULT_NOTIFICATION_DETAIL_LEVEL,
    normalize_notification_detail_level,
    notification_detail_profile,
)
from digital_twin.modules.notifications.contracts import (
    DEFAULT_QUIET_HOURS_ENABLED,
    DEFAULT_QUIET_HOURS_START,
    DEFAULT_QUIET_HOURS_END,
    DEFAULT_QUIET_HOURS_TIMEZONE,
    QUIET_HOURS_BYPASS_MESSAGE_TYPES,
    DEFAULT_MESSAGE_DELIVERY_LEVEL,
    MESSAGE_DELIVERY_LEVELS,
    normalize_message_delivery_level,
    message_delivery_profile,
    bool_value,
    normalize_time_text,
    quiet_minutes,
    quiet_timezone,
    is_quiet_time,
)
from digital_twin.modules.portfolio.contracts import (
    DEFAULT_INVESTMENT_STRATEGY_PROFILE,
    INVESTMENT_STRATEGY_PROFILES,
    normalize_investment_strategy_profile,
    investment_strategy_profile,
)


def configured(value: Optional[str]) -> str:
    return str(value or "").strip()


def split_symbols(raw: str) -> List[str]:
    return [item.strip().upper() for item in str(raw or "").split(",") if item.strip()]


@dataclass
class AccountConfig:
    account_id: str
    label: str
    provider: str
    base_url: str
    client_id: str
    client_secret: str
    account_seq: str
    watchlist_symbols: List[str]
    notify_provider: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    notify_link_url: str = ""
    enabled: bool = True
    quiet_hours_enabled: bool = DEFAULT_QUIET_HOURS_ENABLED
    quiet_hours_start: str = DEFAULT_QUIET_HOURS_START
    quiet_hours_end: str = DEFAULT_QUIET_HOURS_END
    quiet_hours_timezone: str = DEFAULT_QUIET_HOURS_TIMEZONE
    message_delivery_level: str = DEFAULT_MESSAGE_DELIVERY_LEVEL
    notification_detail_level: str = DEFAULT_NOTIFICATION_DETAIL_LEVEL
    investment_strategy_profile: str = DEFAULT_INVESTMENT_STRATEGY_PROFILE
    created_at: str = ""
    updated_at: str = ""

    def domain_profile(self):
        """Expose separated account concepts without breaking legacy callers."""
        from digital_twin.modules.accounts.contracts import AccountDomainProfile

        return AccountDomainProfile.from_legacy(self)

    def investment_mandate(self, effective_at: str = ""):
        from digital_twin.domain.investment_mandate import InvestmentMandate

        account = self.domain_profile().brokerage_account
        return InvestmentMandate.from_profile(
            account_id=account.account_id,
            portfolio_id=account.portfolio_id,
            profile=self.investment_strategy_profile_payload(),
            effective_at=effective_at,
        )

    @classmethod
    def from_dict(cls, payload: Dict[str, object], settings: Dict[str, str]) -> "AccountConfig":
        watchlist_raw = (
            payload.get("watchlistSymbols")
            if "watchlistSymbols" in payload
            else settings.get("watchlistSymbols")
        )
        quiet_enabled_value = (
            payload.get("quietHoursEnabled")
            if "quietHoursEnabled" in payload
            else payload.get("quiet_hours_enabled")
        )
        quiet_start_value = (
            payload.get("quietHoursStart")
            if "quietHoursStart" in payload
            else payload.get("quiet_hours_start")
        )
        quiet_end_value = (
            payload.get("quietHoursEnd")
            if "quietHoursEnd" in payload
            else payload.get("quiet_hours_end")
        )
        quiet_timezone_value = (
            payload.get("quietHoursTimezone")
            if "quietHoursTimezone" in payload
            else payload.get("quiet_hours_timezone")
        )
        delivery_level_value = (
            payload.get("messageDeliveryLevel")
            if "messageDeliveryLevel" in payload
            else payload.get("message_delivery_level")
        )
        notification_detail_value = (
            payload.get("notificationDetailLevel")
            if "notificationDetailLevel" in payload
            else payload.get("notification_detail_level")
        )
        strategy_profile_value = (
            payload.get("investmentStrategyProfile")
            if "investmentStrategyProfile" in payload
            else payload.get("investment_strategy_profile")
        )
        return cls(
            account_id=configured(payload.get("id") or payload.get("accountId") or "default"),
            label=configured(
                payload.get("label") or payload.get("name") or payload.get("id") or "기본 계정"
            ),
            provider=configured(payload.get("provider") or "toss"),
            base_url=configured(
                payload.get("baseUrl")
                or settings.get("tossApiBaseUrl")
                or "https://openapi.tossinvest.com"
            ),
            client_id=configured(payload.get("clientId") or payload.get("client_id") or ""),
            client_secret=configured(
                payload.get("clientSecret") or payload.get("client_secret") or ""
            ),
            account_seq=configured(payload.get("accountSeq") or payload.get("account_seq") or ""),
            watchlist_symbols=split_symbols(configured(watchlist_raw)),
            notify_provider=configured(
                payload.get("notifyProvider")
                or payload.get("notify_provider")
                or settings.get("notifyProvider")
            ),
            telegram_bot_token=configured(
                payload.get("telegramBotToken")
                or payload.get("telegram_bot_token")
                or settings.get("telegramBotToken")
            ),
            telegram_chat_id=configured(
                payload.get("telegramChatId")
                or payload.get("telegram_chat_id")
                or settings.get("telegramChatId")
            ),
            notify_link_url=configured(
                payload.get("notifyLinkUrl")
                or payload.get("notify_link_url")
                or settings.get("notifyLinkUrl")
            ),
            enabled=bool(payload.get("enabled", True)),
            quiet_hours_enabled=bool_value(quiet_enabled_value, DEFAULT_QUIET_HOURS_ENABLED),
            quiet_hours_start=normalize_time_text(quiet_start_value, DEFAULT_QUIET_HOURS_START),
            quiet_hours_end=normalize_time_text(quiet_end_value, DEFAULT_QUIET_HOURS_END),
            quiet_hours_timezone=quiet_timezone(quiet_timezone_value),
            message_delivery_level=normalize_message_delivery_level(delivery_level_value),
            notification_detail_level=normalize_notification_detail_level(
                notification_detail_value
            ),
            investment_strategy_profile=normalize_investment_strategy_profile(
                strategy_profile_value or settings.get("investmentStrategyProfile")
            ),
            created_at=configured(payload.get("createdAt") or payload.get("created_at") or ""),
            updated_at=configured(payload.get("updatedAt") or payload.get("updated_at") or ""),
        )

    def to_private_dict(self) -> Dict[str, object]:
        return {
            "id": self.account_id,
            "label": self.label,
            "provider": self.provider,
            "baseUrl": self.base_url,
            "clientId": self.client_id,
            "clientSecret": self.client_secret,
            "accountSeq": self.account_seq,
            "watchlistSymbols": ",".join(self.watchlist_symbols),
            "notifyProvider": self.notify_provider,
            "telegramBotToken": self.telegram_bot_token,
            "telegramChatId": self.telegram_chat_id,
            "notifyLinkUrl": self.notify_link_url,
            "enabled": self.enabled,
            "quietHoursEnabled": self.quiet_hours_enabled,
            "quietHoursStart": self.quiet_hours_start,
            "quietHoursEnd": self.quiet_hours_end,
            "quietHoursTimezone": self.quiet_hours_timezone,
            "messageDeliveryLevel": normalize_message_delivery_level(self.message_delivery_level),
            "notificationDetailLevel": normalize_notification_detail_level(
                self.notification_detail_level
            ),
            "investmentStrategyProfile": normalize_investment_strategy_profile(
                self.investment_strategy_profile
            ),
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    def masked(self) -> Dict[str, object]:
        profile = self.message_delivery_profile()
        detail_profile = self.notification_detail_profile()
        strategy_profile = self.investment_strategy_profile_payload()
        return {
            "id": self.account_id,
            "label": self.label,
            "provider": self.provider,
            "baseUrl": self.base_url,
            "clientId": bool(self.client_id),
            "clientSecret": bool(self.client_secret),
            "accountSeq": self.account_seq,
            "watchlistSymbols": self.watchlist_symbols,
            "notifyProvider": self.notify_provider,
            "telegramBotToken": bool(self.telegram_bot_token),
            "telegramChatId": bool(self.telegram_chat_id),
            "notifyLinkUrl": self.notify_link_url,
            "enabled": self.enabled,
            "quietHoursEnabled": self.quiet_hours_enabled,
            "quietHoursStart": self.quiet_hours_start,
            "quietHoursEnd": self.quiet_hours_end,
            "quietHoursTimezone": self.quiet_hours_timezone,
            "messageDeliveryLevel": profile["level"],
            "messageDeliveryLevelLabel": profile["label"],
            "notificationDetailLevel": detail_profile["level"],
            "notificationDetailLevelLabel": detail_profile["label"],
            "investmentStrategyProfile": strategy_profile["profile"],
            "investmentStrategyProfileLabel": strategy_profile["label"],
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    def message_delivery_profile(self) -> Dict[str, object]:
        profile = message_delivery_profile(self.message_delivery_level)
        profile["accountId"] = self.account_id
        profile["accountLabel"] = self.label
        return profile

    def notification_detail_profile(self) -> Dict[str, object]:
        profile = notification_detail_profile(self.notification_detail_level)
        profile["accountId"] = self.account_id
        profile["accountLabel"] = self.label
        return profile

    def message_delivery_context(self) -> Dict[str, object]:
        profile = self.message_delivery_profile()
        context = {
            "messageDeliveryLevel": profile["level"],
            "messageDeliveryLevelLabel": profile["label"],
            "messageDeliveryProfile": profile,
            "notifyLinkUrl": self.notify_link_url,
        }
        detail_profile = self.notification_detail_profile()
        context.update(
            {
                "notificationDetailLevel": detail_profile["level"],
                "notificationDetailLevelLabel": detail_profile["label"],
                "notificationDetailProfile": detail_profile,
            }
        )
        context.update(self.investment_strategy_context())
        return context

    def investment_strategy_profile_payload(self) -> Dict[str, object]:
        profile = investment_strategy_profile(self.investment_strategy_profile)
        profile["accountId"] = self.account_id
        profile["accountLabel"] = self.label
        return profile

    def investment_strategy_context(self) -> Dict[str, object]:
        profile = self.investment_strategy_profile_payload()
        return {
            "investmentStrategyProfile": profile["profile"],
            "investmentStrategyProfileLabel": profile["label"],
            "investmentStrategy": profile,
        }

    def ontology_account_context(self) -> Dict[str, object]:
        context = self.message_delivery_context()
        context.update(self.investment_strategy_context())
        context.update(
            {
                "accountId": self.account_id,
                "accountLabel": self.label,
                "provider": self.provider,
            }
        )
        return context

    def quiet_hours_active(self, now: datetime = None, message_type: str = "") -> bool:
        if message_type in QUIET_HOURS_BYPASS_MESSAGE_TYPES:
            return False
        if not self.quiet_hours_enabled:
            return False
        return is_quiet_time(
            now or datetime.now(ZoneInfo("UTC")),
            self.quiet_hours_start,
            self.quiet_hours_end,
            self.quiet_hours_timezone,
        )

    def quiet_hours_reason(self) -> str:
        return (
            "계정 알림 금지 시간 "
            + self.quiet_hours_start
            + "-"
            + self.quiet_hours_end
            + " "
            + self.quiet_hours_timezone
        )
