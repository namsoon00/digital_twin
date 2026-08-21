import re
from typing import Dict

from .message_types import (
    DEFAULT_MESSAGE,
    GENERIC_NOTIFICATION,
    MODEL_REVIEW,
    OPERATOR_REASONING_REPORT,
    WORK_HANDOFF,
    is_operations_delivery_message_type,
)


SYSTEM_ERROR = "systemError"
BADGED_SYSTEM_MESSAGE_TYPES = {
    DEFAULT_MESSAGE,
    GENERIC_NOTIFICATION,
    MODEL_REVIEW,
    OPERATOR_REASONING_REPORT,
    WORK_HANDOFF,
    SYSTEM_ERROR,
}


def _context_path_value(context: Dict[str, object], path: str):
    current = context or {}
    for part in [item for item in str(path or "").split(".") if item]:
        if isinstance(current, dict) and part in current:
            current = current.get(part)
            continue
        return None
    return current


def _clean_badge_label(value: object) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or "")).strip()
    if not text:
        return ""
    text = text.splitlines()[0].strip()
    for separator in [" / ", " | ", " - "]:
        if separator in text:
            text = text.split(separator, 1)[0].strip()
    if ":" in text and len(text.split(":", 1)[0]) <= 18:
        text = text.split(":", 1)[0].strip()
    text = re.sub(r"^\[[^\]]+\]\s*", "", text).strip()
    if text.lower() in {"portfolio", "macro", "main", "notification"}:
        return ""
    return text[:24]


def notification_start_badge_label(context: Dict[str, object] = None) -> str:
    context = context or {}
    message_type = str(
        context.get("messageType")
        or context.get("message_type")
        or context.get("rule")
        or ""
    ).strip()
    if message_type == "workHandoff":
        return "작업완료"
    for path in [
        "symbolDisplayName",
        "displaySymbolName",
        "companyName",
        "displayName",
        "ontologyInsight.subjectName",
        "activeInvestmentOpinion.subjectName",
        "displayTarget",
        "target",
        "rawTarget",
        "symbol",
        "rawSymbol",
        "ontologyInsight.subject",
    ]:
        label = _clean_badge_label(_context_path_value(context, path))
        if label:
            return label
    return ""


def labeled_message_start_badge(base_badge: str, context: Dict[str, object] = None) -> str:
    label = notification_start_badge_label(context)
    return str(base_badge or "").strip() + (" · " + label if label else "")


def notification_start_badge_required(context: Dict[str, object] = None) -> bool:
    """Keep a classification row only for operational or system messages."""

    values = dict(context or {})
    message_type = str(
        values.get("messageType")
        or values.get("message_type")
        or values.get("rule")
        or ""
    ).strip()
    if not message_type:
        return True
    if str(values.get("deliveryAudience") or "").strip().lower() == "operations":
        return True
    if str(values.get("operationalBadge") or "").strip():
        return True
    return message_type in BADGED_SYSTEM_MESSAGE_TYPES or is_operations_delivery_message_type(message_type)


def strip_message_start_badge(rendered: object, base_badge: object) -> str:
    """Remove an existing generic classification row from a customer alert."""

    text = str(rendered or "").strip()
    badge = str(base_badge or "").strip()
    if not text or not badge:
        return text
    first, rest = (text.split("\n", 1) + [""])[:2]
    plain_first = re.sub(r"<[^>]+>", "", first).strip()
    if plain_first != badge and not plain_first.startswith(badge + " · "):
        return text
    return rest.lstrip()
