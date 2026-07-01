from dataclasses import dataclass
from typing import Dict, List, Optional


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

    @classmethod
    def from_dict(cls, payload: Dict[str, object], settings: Dict[str, str]) -> "AccountConfig":
        return cls(
            account_id=configured(payload.get("id") or payload.get("accountId") or "default"),
            label=configured(payload.get("label") or payload.get("name") or payload.get("id") or "기본 계정"),
            provider=configured(payload.get("provider") or "toss"),
            base_url=configured(payload.get("baseUrl") or settings.get("tossApiBaseUrl") or "https://openapi.tossinvest.com"),
            client_id=configured(payload.get("clientId") or payload.get("client_id") or ""),
            client_secret=configured(payload.get("clientSecret") or payload.get("client_secret") or ""),
            account_seq=configured(payload.get("accountSeq") or payload.get("account_seq") or ""),
            watchlist_symbols=split_symbols(configured(payload.get("watchlistSymbols") or settings.get("watchlistSymbols"))),
            notify_provider=configured(payload.get("notifyProvider") or payload.get("notify_provider") or settings.get("notifyProvider")),
            telegram_bot_token=configured(payload.get("telegramBotToken") or payload.get("telegram_bot_token") or settings.get("telegramBotToken")),
            telegram_chat_id=configured(payload.get("telegramChatId") or payload.get("telegram_chat_id") or settings.get("telegramChatId")),
            notify_link_url=configured(payload.get("notifyLinkUrl") or payload.get("notify_link_url") or settings.get("notifyLinkUrl")),
            enabled=bool(payload.get("enabled", True)),
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
        }

    def masked(self) -> Dict[str, object]:
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
        }

