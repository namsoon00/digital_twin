import re
from typing import Dict


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
