"""Notifications-owned configuration contracts; persisted field meanings are unchanged."""

from datetime import datetime
from typing import Dict
from zoneinfo import ZoneInfo


DEFAULT_QUIET_HOURS_ENABLED = True


DEFAULT_QUIET_HOURS_START = "22:00"


DEFAULT_QUIET_HOURS_END = "05:00"


DEFAULT_QUIET_HOURS_TIMEZONE = "Asia/Seoul"


QUIET_HOURS_BYPASS_MESSAGE_TYPES = {"workHandoff", "operatorReasoningReport"}


DEFAULT_MESSAGE_DELIVERY_LEVEL = "absoluteBeginner"


MESSAGE_DELIVERY_LEVELS = {
    "absoluteBeginner": {
        "label": "왕초보",
        "description": "전문 용어를 쉬운 말로 풀어서 보여줍니다.",
        "detailLevel": "full_plain",
        "terminology": "plain",
        "decisionStateVisibility": "summary",
        "ruleVisibility": "explained_summary",
        "promptInstruction": "왕초보 투자자가 오해하지 않도록 선택된 알림 항목을 쉬운 단어와 짧은 문장으로 풀어 설명한다.",
    },
    "beginner": {
        "label": "초보",
        "description": "핵심 수치와 쉬운 이유를 함께 설명합니다.",
        "detailLevel": "full_guided",
        "terminology": "plain_with_basic_terms",
        "decisionStateVisibility": "guided",
        "ruleVisibility": "explained_summary",
        "promptInstruction": "초보 투자자가 따라올 수 있도록 선택된 현재가, 평균매입가, 수익률, 다음 확인 조건을 쉬운 말과 기본 용어를 함께 써서 설명한다.",
    },
    "intermediate": {
        "label": "중수",
        "description": "가격, 수급, 추세, 부족 데이터를 표준 용어로 설명합니다.",
        "detailLevel": "full_balanced",
        "terminology": "standard",
        "decisionStateVisibility": "detailed",
        "ruleVisibility": "matched_rules",
        "promptInstruction": "중수 사용자가 판단 근거를 비교할 수 있도록 수급, 추세, 반대 신호, 부족 데이터를 분리해 설명한다.",
    },
    "advanced": {
        "label": "고수",
        "description": "관계 규칙, 검증 메모, 발송 기준을 원래 용어에 가깝게 설명합니다.",
        "detailLevel": "diagnostic",
        "terminology": "technical_allowed",
        "decisionStateVisibility": "diagnostic",
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
