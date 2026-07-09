from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo


DEFAULT_QUIET_HOURS_ENABLED = True
DEFAULT_QUIET_HOURS_START = "22:00"
DEFAULT_QUIET_HOURS_END = "05:00"
DEFAULT_QUIET_HOURS_TIMEZONE = "Asia/Seoul"
QUIET_HOURS_BYPASS_MESSAGE_TYPES = {"workHandoff"}
DEFAULT_MESSAGE_DELIVERY_LEVEL = "absoluteBeginner"
MESSAGE_DELIVERY_LEVELS = {
    "absoluteBeginner": {
        "label": "왕초보",
        "description": "전문 용어를 풀어서 쓰고, 지금 확인할 행동만 짧게 보여줍니다.",
        "detailLevel": "minimal",
        "terminology": "plain",
        "scoreVisibility": "hidden",
        "ruleVisibility": "summary",
        "promptInstruction": "왕초보 투자자가 오해하지 않도록 전문 용어를 피하고, 한 번에 확인할 행동을 1~2개로 줄여 설명한다.",
    },
    "beginner": {
        "label": "초보",
        "description": "핵심 수치와 쉬운 이유를 함께 보여주고, 모델 세부식은 숨깁니다.",
        "detailLevel": "guided",
        "terminology": "plain_with_basic_terms",
        "scoreVisibility": "label",
        "ruleVisibility": "summary",
        "promptInstruction": "초보 투자자가 따라올 수 있도록 현재가, 평균매입가, 수익률, 다음 확인 조건을 쉬운 말로 설명한다.",
    },
    "intermediate": {
        "label": "중수",
        "description": "가격, 수급, 추세, 부족 데이터를 균형 있게 보여줍니다.",
        "detailLevel": "balanced",
        "terminology": "standard",
        "scoreVisibility": "score",
        "ruleVisibility": "matched_rules",
        "promptInstruction": "중수 사용자가 판단 근거를 비교할 수 있도록 수급, 추세, 반대 신호, 부족 데이터를 분리해 설명한다.",
    },
    "advanced": {
        "label": "고수",
        "description": "관계 규칙, 검증 메모, 발송 기준까지 더 자세히 보여줍니다.",
        "detailLevel": "diagnostic",
        "terminology": "technical_allowed",
        "scoreVisibility": "full",
        "ruleVisibility": "diagnostic",
        "promptInstruction": "고급 사용자가 검증할 수 있도록 관계 규칙, 신뢰도, 부족 데이터, 기준시각, 발송 기준을 최대한 구체적으로 유지한다.",
    },
}


def normalize_message_delivery_level(value: object) -> str:
    text = str(value or "").strip()
    aliases = {
        "왕초보": "absoluteBeginner",
        "absolute_beginner": "absoluteBeginner",
        "absolute-beginner": "absoluteBeginner",
        "veryBeginner": "absoluteBeginner",
        "beginner0": "absoluteBeginner",
        "초보": "beginner",
        "중수": "intermediate",
        "고수": "advanced",
    }
    normalized = aliases.get(text, text)
    return normalized if normalized in MESSAGE_DELIVERY_LEVELS else DEFAULT_MESSAGE_DELIVERY_LEVEL


def message_delivery_profile(level: object = None) -> Dict[str, object]:
    normalized = normalize_message_delivery_level(level)
    profile = dict(MESSAGE_DELIVERY_LEVELS[normalized])
    profile["level"] = normalized
    profile["ontologyBox"] = "ABox"
    profile["tboxClass"] = "MessageDeliveryProfile"
    return profile


def configured(value: Optional[str]) -> str:
    return str(value or "").strip()


def split_symbols(raw: str) -> List[str]:
    return [item.strip().upper() for item in str(raw or "").split(",") if item.strip()]


def bool_value(value, fallback: bool = True) -> bool:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    normalized = str(value).strip().lower()
    if normalized in {"0", "false", "no", "off"}:
        return False
    if normalized in {"1", "true", "yes", "on"}:
        return True
    return fallback


def normalize_time_text(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) < 2:
        return fallback
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return fallback
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return fallback
    return str(hour).zfill(2) + ":" + str(minute).zfill(2)


def quiet_minutes(value: str) -> int:
    normalized = normalize_time_text(value, "00:00")
    hour, minute = normalized.split(":", 1)
    return int(hour) * 60 + int(minute)


def quiet_timezone(value: object) -> str:
    text = str(value or "").strip() or DEFAULT_QUIET_HOURS_TIMEZONE
    try:
        ZoneInfo(text)
        return text
    except Exception:  # noqa: BLE001 - invalid local configuration should fall back safely.
        return DEFAULT_QUIET_HOURS_TIMEZONE


def is_quiet_time(now: datetime, start: str, end: str, timezone_name: str) -> bool:
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("UTC"))
    local_now = now.astimezone(ZoneInfo(quiet_timezone(timezone_name)))
    current = local_now.hour * 60 + local_now.minute
    start_minutes = quiet_minutes(start)
    end_minutes = quiet_minutes(end)
    if start_minutes == end_minutes:
        return False
    if start_minutes < end_minutes:
        return start_minutes <= current < end_minutes
    return current >= start_minutes or current < end_minutes


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

    @classmethod
    def from_dict(cls, payload: Dict[str, object], settings: Dict[str, str]) -> "AccountConfig":
        watchlist_raw = payload.get("watchlistSymbols") if "watchlistSymbols" in payload else settings.get("watchlistSymbols")
        quiet_enabled_value = payload.get("quietHoursEnabled") if "quietHoursEnabled" in payload else payload.get("quiet_hours_enabled")
        quiet_start_value = payload.get("quietHoursStart") if "quietHoursStart" in payload else payload.get("quiet_hours_start")
        quiet_end_value = payload.get("quietHoursEnd") if "quietHoursEnd" in payload else payload.get("quiet_hours_end")
        quiet_timezone_value = payload.get("quietHoursTimezone") if "quietHoursTimezone" in payload else payload.get("quiet_hours_timezone")
        delivery_level_value = payload.get("messageDeliveryLevel") if "messageDeliveryLevel" in payload else payload.get("message_delivery_level")
        return cls(
            account_id=configured(payload.get("id") or payload.get("accountId") or "default"),
            label=configured(payload.get("label") or payload.get("name") or payload.get("id") or "기본 계정"),
            provider=configured(payload.get("provider") or "toss"),
            base_url=configured(payload.get("baseUrl") or settings.get("tossApiBaseUrl") or "https://openapi.tossinvest.com"),
            client_id=configured(payload.get("clientId") or payload.get("client_id") or ""),
            client_secret=configured(payload.get("clientSecret") or payload.get("client_secret") or ""),
            account_seq=configured(payload.get("accountSeq") or payload.get("account_seq") or ""),
            watchlist_symbols=split_symbols(configured(watchlist_raw)),
            notify_provider=configured(payload.get("notifyProvider") or payload.get("notify_provider") or settings.get("notifyProvider")),
            telegram_bot_token=configured(payload.get("telegramBotToken") or payload.get("telegram_bot_token") or settings.get("telegramBotToken")),
            telegram_chat_id=configured(payload.get("telegramChatId") or payload.get("telegram_chat_id") or settings.get("telegramChatId")),
            notify_link_url=configured(payload.get("notifyLinkUrl") or payload.get("notify_link_url") or settings.get("notifyLinkUrl")),
            enabled=bool(payload.get("enabled", True)),
            quiet_hours_enabled=bool_value(quiet_enabled_value, DEFAULT_QUIET_HOURS_ENABLED),
            quiet_hours_start=normalize_time_text(quiet_start_value, DEFAULT_QUIET_HOURS_START),
            quiet_hours_end=normalize_time_text(quiet_end_value, DEFAULT_QUIET_HOURS_END),
            quiet_hours_timezone=quiet_timezone(quiet_timezone_value),
            message_delivery_level=normalize_message_delivery_level(delivery_level_value),
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
        }

    def masked(self) -> Dict[str, object]:
        profile = self.message_delivery_profile()
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
        }

    def message_delivery_profile(self) -> Dict[str, object]:
        profile = message_delivery_profile(self.message_delivery_level)
        profile["accountId"] = self.account_id
        profile["accountLabel"] = self.label
        return profile

    def message_delivery_context(self) -> Dict[str, object]:
        profile = self.message_delivery_profile()
        return {
            "messageDeliveryLevel": profile["level"],
            "messageDeliveryLevelLabel": profile["label"],
            "messageDeliveryProfile": profile,
        }

    def quiet_hours_active(self, now: datetime = None, message_type: str = "") -> bool:
        if message_type in QUIET_HOURS_BYPASS_MESSAGE_TYPES:
            return False
        if not self.quiet_hours_enabled:
            return False
        return is_quiet_time(now or datetime.now(ZoneInfo("UTC")), self.quiet_hours_start, self.quiet_hours_end, self.quiet_hours_timezone)

    def quiet_hours_reason(self) -> str:
        return (
            "계정 알림 금지 시간 "
            + self.quiet_hours_start
            + "-"
            + self.quiet_hours_end
            + " "
            + self.quiet_hours_timezone
        )
