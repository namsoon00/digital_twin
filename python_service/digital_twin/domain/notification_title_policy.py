"""Structured, deterministic first-line titles for investment notifications."""

import re
from typing import Dict

from .notification_icon_policy import (
    INVESTMENT_CONTEXTUAL_MESSAGE_TYPES,
    investment_notification_icon,
    investment_notification_state,
)


WATCHLIST_ACTION_TITLES = {
    "BUY": "소액 진입 검토",
    "ADD": "소액 진입 검토",
    "HOLD": "관심 유지",
    "TRIM": "신규 진입 회피",
    "SELL": "신규 진입 회피",
    "AVOID": "신규 진입 회피",
}
HOLDING_ACTION_TITLES = {
    "BUY": "매수 검토",
    "ADD": "추가매수 검토",
    "HOLD": "보유 유지",
    "TRIM": "분할축소 검토",
    "SELL": "매도 검토",
    "AVOID": "신규 진입 회피",
}


def _clean_target(value: object) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or "")).strip()
    if not text:
        return ""
    text = text.splitlines()[0].strip()
    text = re.sub(r"^\[[^\]]+\]\s*", "", text).strip()
    for separator in [" / ", " | ", "/", "|"]:
        if separator in text:
            text = text.split(separator, 1)[0].strip()
            break
    return text[:24].rstrip()


def investment_notification_target(context: Dict[str, object], fallback: object = "") -> str:
    values = dict(context or {}) if isinstance(context, dict) else {}
    metadata = values.get("metadata") if isinstance(values.get("metadata"), dict) else {}
    insight = values.get("ontologyInsight") if isinstance(values.get("ontologyInsight"), dict) else {}
    opinion = values.get("activeInvestmentOpinion") if isinstance(values.get("activeInvestmentOpinion"), dict) else {}
    raw_symbol = str(values.get("rawSymbol") or values.get("symbol") or "").strip().upper()
    candidates = [
        values.get("companyName"),
        values.get("displayName"),
        metadata.get("companyName"),
        metadata.get("displayName"),
        values.get("symbolDisplayName"),
        values.get("displaySymbolName"),
        insight.get("subjectName"),
        opinion.get("subjectName"),
        values.get("displayTarget"),
        values.get("target"),
        values.get("title"),
        values.get("symbol"),
        values.get("rawSymbol"),
    ]
    for value in candidates:
        target = _clean_target(value)
        if target and target.upper() != raw_symbol:
            return target
    for key in ["displayTarget", "target", "title", "symbol", "rawSymbol"]:
        target = _clean_target(values.get(key))
        if target:
            return target
    return _clean_target(fallback)


def investment_action_title(action: object, watchlist: bool = False) -> str:
    key = str(action or "").strip().upper()
    return (WATCHLIST_ACTION_TITLES if watchlist else HOLDING_ACTION_TITLES).get(key, "")


def _status_title(status: object, watchlist: bool) -> str:
    key = str(status or "").strip().upper()
    if watchlist:
        return {
            "ENTRY_ELIGIBLE": "소액 진입 검토",
            "ENTRY_DEFERRED": "진입 조건 재확인",
            "ENTRY_OBSERVING": "관심 유지",
            "ENTRY_BLOCKED": "신규 진입 회피",
        }.get(key, "")
    return "보유 점검" if key == "HOLDING_REVIEW" else ""


def is_contextual_investment_notification(message_type: object, context: Dict[str, object] = None) -> bool:
    key = str(message_type or (context or {}).get("messageType") or (context or {}).get("rule") or "").strip()
    if key in INVESTMENT_CONTEXTUAL_MESSAGE_TYPES:
        return True
    values = context or {}
    return bool(
        isinstance(values.get("notificationAiValidatedResponse"), dict)
        and isinstance(values.get("ontologyRelationContext"), dict)
    )


def investment_notification_title(
    message_type: object,
    context: Dict[str, object] = None,
    fallback_target: object = "",
) -> str:
    """Render one icon, one target, and one current action or action change."""

    values = dict(context or {}) if isinstance(context, dict) else {}
    if not is_contextual_investment_notification(message_type, values):
        return ""
    state = investment_notification_state(values)
    current = "판단 보류" if state.get("dataUnavailable") else investment_action_title(
        state.get("action"),
        bool(state.get("watchlist")),
    )
    current = current or _status_title(state.get("status"), bool(state.get("watchlist")))
    if not current:
        return ""
    previous = ""
    if state.get("previousAction") and not state.get("dataUnavailable"):
        previous = investment_action_title(state.get("previousAction"), bool(state.get("watchlist")))
        if previous == current:
            previous = ""
    action_text = (previous + " → " + current) if previous else current
    target = investment_notification_target(values, fallback_target)
    icon = investment_notification_icon(message_type, values) or "🧭"
    body = (target + " · " + action_text) if target else action_text
    return (icon + " " + body).strip()
