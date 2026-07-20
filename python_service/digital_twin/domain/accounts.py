from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo


DEFAULT_QUIET_HOURS_ENABLED = True
DEFAULT_QUIET_HOURS_START = "22:00"
DEFAULT_QUIET_HOURS_END = "05:00"
DEFAULT_QUIET_HOURS_TIMEZONE = "Asia/Seoul"
QUIET_HOURS_BYPASS_MESSAGE_TYPES = {"workHandoff", "operatorReasoningReport"}
DEFAULT_MESSAGE_DELIVERY_LEVEL = "absoluteBeginner"
DEFAULT_INVESTMENT_STRATEGY_PROFILE = "balanced"
MESSAGE_DELIVERY_LEVELS = {
    "absoluteBeginner": {
        "label": "왕초보",
        "description": "같은 알림 내용을 유지하되 전문 용어를 쉬운 말로 풀어서 보여줍니다.",
        "detailLevel": "full_plain",
        "terminology": "plain",
        "decisionStateVisibility": "summary",
        "ruleVisibility": "explained_summary",
        "promptInstruction": "왕초보 투자자가 오해하지 않도록 알림 항목은 줄이지 말고 같은 내용을 쉬운 단어와 짧은 문장으로 풀어 설명한다.",
    },
    "beginner": {
        "label": "초보",
        "description": "같은 알림 내용을 유지하되 핵심 수치와 쉬운 이유를 함께 설명합니다.",
        "detailLevel": "full_guided",
        "terminology": "plain_with_basic_terms",
        "decisionStateVisibility": "guided",
        "ruleVisibility": "explained_summary",
        "promptInstruction": "초보 투자자가 따라올 수 있도록 알림 항목은 유지하고 현재가, 평균매입가, 수익률, 다음 확인 조건을 쉬운 말과 기본 용어를 함께 써서 설명한다.",
    },
    "intermediate": {
        "label": "중수",
        "description": "같은 알림 내용을 유지하되 가격, 수급, 추세, 부족 데이터를 표준 용어로 설명합니다.",
        "detailLevel": "full_balanced",
        "terminology": "standard",
        "decisionStateVisibility": "detailed",
        "ruleVisibility": "matched_rules",
        "promptInstruction": "중수 사용자가 판단 근거를 비교할 수 있도록 수급, 추세, 반대 신호, 부족 데이터를 분리해 설명한다.",
    },
    "advanced": {
        "label": "고수",
        "description": "같은 알림 내용을 유지하되 관계 규칙, 검증 메모, 발송 기준을 원래 용어에 가깝게 설명합니다.",
        "detailLevel": "diagnostic",
        "terminology": "technical_allowed",
        "decisionStateVisibility": "diagnostic",
        "ruleVisibility": "diagnostic",
        "promptInstruction": "고급 사용자가 검증할 수 있도록 관계 규칙, 신뢰도, 부족 데이터, 기준시각, 발송 기준을 최대한 구체적으로 유지한다.",
    },
}
INVESTMENT_STRATEGY_PROFILES = {
    "capitalPreservation": {
        "label": "안정형",
        "description": "손실 제한과 현금 여력을 우선하고, 새 진입은 강한 확인 뒤에만 검토합니다.",
        "riskTolerance": "low",
        "timeHorizon": "mid",
        "lossTolerancePct": -5,
        "profitProtectionPct": 7,
        "maxPositionWeightPct": 15,
        "maxSectorWeightPct": 30,
        "fxExposureReviewPct": 8,
        "addBuyPolicy": "blocked_until_recovery",
        "addBuyWatchSignalMin": 4,
        "addBuyReviewSignalMin": 6,
        "allowLossAddBuyReview": False,
        "defaultHoldingRole": "core",
        "watchlistActionPolicy": "entry_after_confirmation",
        "holdingActionPolicy": "protect_capital_first",
        "promptInstruction": "손실 제한과 비중 축소 기준을 우선 검토하고, 추가매수는 주요 평균 가격 회복과 거래 증가가 같이 확인될 때만 제안한다.",
    },
    "balanced": {
        "label": "균형형",
        "description": "손실 관리와 수익 유지의 균형을 잡고, 보유와 관심종목의 행동 범위를 명확히 나눕니다.",
        "riskTolerance": "medium",
        "timeHorizon": "mid",
        "lossTolerancePct": -8,
        "profitProtectionPct": 12,
        "maxPositionWeightPct": 25,
        "maxSectorWeightPct": 45,
        "fxExposureReviewPct": 12,
        "addBuyPolicy": "watch_after_flow_defense",
        "addBuyWatchSignalMin": 3,
        "addBuyReviewSignalMin": 5,
        "allowLossAddBuyReview": True,
        "defaultHoldingRole": "coreSatellite",
        "watchlistActionPolicy": "small_entry_after_confirmation",
        "holdingActionPolicy": "risk_adjusted_hold_trim",
        "promptInstruction": "손익률, 5/20/60일 평균 가격, 수급, 뉴스·공시, 금리·환율을 함께 보고 보유·분할축소·소액 진입을 균형 있게 제안한다.",
    },
    "growth": {
        "label": "성장형",
        "description": "추세와 성장 근거가 유지되면 변동성을 더 허용하되, 손실 확대 구간은 분할 대응합니다.",
        "riskTolerance": "high",
        "timeHorizon": "long",
        "lossTolerancePct": -12,
        "profitProtectionPct": 18,
        "maxPositionWeightPct": 35,
        "maxSectorWeightPct": 55,
        "fxExposureReviewPct": 18,
        "addBuyPolicy": "review_after_recovery",
        "addBuyWatchSignalMin": 2,
        "addBuyReviewSignalMin": 4,
        "allowLossAddBuyReview": True,
        "defaultHoldingRole": "growthCore",
        "watchlistActionPolicy": "staged_entry",
        "holdingActionPolicy": "let_winners_run_with_trim_guard",
        "promptInstruction": "성장 근거와 추세 유지 여부를 더 크게 보되, 평균 가격 이탈·뉴스 악화·수급 약화가 겹치면 분할축소 기준을 제안한다. 손실 구간의 외국인·기관 동반 순매수는 회복 확인 후 조건부 분할 추가매수로만 해석한다.",
    },
    "aggressive": {
        "label": "공격형",
        "description": "기회 포착을 더 중시하지만, 집중도와 급락 리스크는 별도 경고로 강하게 표시합니다.",
        "riskTolerance": "very_high",
        "timeHorizon": "mixed",
        "lossTolerancePct": -15,
        "profitProtectionPct": 25,
        "maxPositionWeightPct": 45,
        "maxSectorWeightPct": 65,
        "fxExposureReviewPct": 25,
        "addBuyPolicy": "review_with_guardrails",
        "addBuyWatchSignalMin": 1,
        "addBuyReviewSignalMin": 3,
        "allowLossAddBuyReview": True,
        "defaultHoldingRole": "highConviction",
        "watchlistActionPolicy": "staged_entry_allowed",
        "holdingActionPolicy": "momentum_follow_with_risk_stop",
        "promptInstruction": "강한 추세·수급·뉴스가 동시에 맞으면 진입 후보를 적극 제안하되, 집중도 과다와 급락 신호는 즉시 축소 조건으로 제시한다. 손실 구간 추가매수도 수급, 가격 회복, 비중 한도를 통과한 소액 분할 검토로 제한한다.",
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


def normalize_investment_strategy_profile(value: object) -> str:
    text = str(value or "").strip()
    aliases = {
        "안정형": "capitalPreservation",
        "안정": "capitalPreservation",
        "capital_preservation": "capitalPreservation",
        "capital-preservation": "capitalPreservation",
        "conservative": "capitalPreservation",
        "균형형": "balanced",
        "균형": "balanced",
        "balance": "balanced",
        "성장형": "growth",
        "성장": "growth",
        "공격형": "aggressive",
        "공격": "aggressive",
    }
    normalized = aliases.get(text, text)
    return normalized if normalized in INVESTMENT_STRATEGY_PROFILES else DEFAULT_INVESTMENT_STRATEGY_PROFILE


def investment_strategy_profile(value: object = None) -> Dict[str, object]:
    normalized = normalize_investment_strategy_profile(value)
    profile = dict(INVESTMENT_STRATEGY_PROFILES[normalized])
    profile["profile"] = normalized
    profile["ontologyBox"] = "ABox"
    profile["tboxClass"] = "InvestmentStrategyProfile"
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
    investment_strategy_profile: str = DEFAULT_INVESTMENT_STRATEGY_PROFILE

    @classmethod
    def from_dict(cls, payload: Dict[str, object], settings: Dict[str, str]) -> "AccountConfig":
        watchlist_raw = payload.get("watchlistSymbols") if "watchlistSymbols" in payload else settings.get("watchlistSymbols")
        quiet_enabled_value = payload.get("quietHoursEnabled") if "quietHoursEnabled" in payload else payload.get("quiet_hours_enabled")
        quiet_start_value = payload.get("quietHoursStart") if "quietHoursStart" in payload else payload.get("quiet_hours_start")
        quiet_end_value = payload.get("quietHoursEnd") if "quietHoursEnd" in payload else payload.get("quiet_hours_end")
        quiet_timezone_value = payload.get("quietHoursTimezone") if "quietHoursTimezone" in payload else payload.get("quiet_hours_timezone")
        delivery_level_value = payload.get("messageDeliveryLevel") if "messageDeliveryLevel" in payload else payload.get("message_delivery_level")
        strategy_profile_value = payload.get("investmentStrategyProfile") if "investmentStrategyProfile" in payload else payload.get("investment_strategy_profile")
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
            investment_strategy_profile=normalize_investment_strategy_profile(strategy_profile_value or settings.get("investmentStrategyProfile")),
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
            "investmentStrategyProfile": normalize_investment_strategy_profile(self.investment_strategy_profile),
        }

    def masked(self) -> Dict[str, object]:
        profile = self.message_delivery_profile()
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
            "investmentStrategyProfile": strategy_profile["profile"],
            "investmentStrategyProfileLabel": strategy_profile["label"],
        }

    def message_delivery_profile(self) -> Dict[str, object]:
        profile = message_delivery_profile(self.message_delivery_level)
        profile["accountId"] = self.account_id
        profile["accountLabel"] = self.label
        return profile

    def message_delivery_context(self) -> Dict[str, object]:
        profile = self.message_delivery_profile()
        context = {
            "messageDeliveryLevel": profile["level"],
            "messageDeliveryLevelLabel": profile["label"],
            "messageDeliveryProfile": profile,
        }
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
        context.update({
            "accountId": self.account_id,
            "accountLabel": self.label,
            "provider": self.provider,
        })
        return context

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
