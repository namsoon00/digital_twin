"""Resolve the icon shown for a notification type and its current context."""

import re
from typing import Dict

from .message_types import INVESTMENT_INSIGHT, MESSAGE_TYPE_EMOJIS, WATCHLIST_ONTOLOGY_SIGNAL
from .operational_notification_presentation import operational_notification_presentation


INVESTMENT_CONTEXTUAL_MESSAGE_TYPES = {INVESTMENT_INSIGHT, WATCHLIST_ONTOLOGY_SIGNAL}


def _context_layers(context: Dict[str, object]):
    values = dict(context or {}) if isinstance(context, dict) else {}
    yield values
    metadata = values.get("metadata") if isinstance(values.get("metadata"), dict) else {}
    if metadata:
        yield metadata


def _context_value(context: Dict[str, object], key: str) -> object:
    for layer in _context_layers(context):
        value = layer.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _relation_context(context: Dict[str, object]) -> Dict[str, object]:
    for key in ["ontologyRelationContext", "relationContext"]:
        value = _context_value(context, key)
        if isinstance(value, dict):
            return dict(value)
    return {}


def _normalized(value: object) -> str:
    return str(value or "").strip().replace("-", "_").replace(" ", "_").upper()


def _investment_action_context(context: Dict[str, object]) -> Dict[str, object]:
    relation = _relation_context(context)
    decision = relation.get("decision") if isinstance(relation.get("decision"), dict) else {}
    envelope = relation.get("actionEnvelope") if isinstance(relation.get("actionEnvelope"), dict) else {}
    if not envelope:
        envelope = decision.get("actionEnvelope") if isinstance(decision.get("actionEnvelope"), dict) else {}
    transition = _context_value(context, "decisionTransition")
    if not isinstance(transition, dict):
        diff = _context_value(context, "ontologyRelationDiff")
        transition = diff.get("decisionTransition") if isinstance(diff, dict) and isinstance(diff.get("decisionTransition"), dict) else {}
    response = _context_value(context, "notificationAiValidatedResponse")
    response = response if isinstance(response, dict) else {}
    facts = relation.get("facts") if isinstance(relation.get("facts"), dict) else {}
    readiness = envelope.get("dataReadiness") if isinstance(envelope.get("dataReadiness"), dict) else {}
    action = _normalized(
        response.get("action")
        or transition.get("currentAction")
        or envelope.get("preferredAction")
        or decision.get("candidateAction")
    )
    status = _normalized(transition.get("currentStatus") or envelope.get("status"))
    previous_action = _normalized(transition.get("previousAction"))
    watchlist = (
        facts.get("isWatchlist") is True
        or str(facts.get("source") or "").strip().lower() == "watchlist"
        or any(
            str(container.get("targetRole") or "").strip().lower() == "watchlist"
            or str(container.get("actionPolicy") or "").strip() == "ENTRY_ONLY"
            for container in [relation, decision, envelope]
            if isinstance(container, dict)
        )
    )
    data_unavailable = (
        _normalized(response.get("dataState")) in {"UNAVAILABLE", "MISSING", "BLOCKED"}
        or _normalized(response.get("validationState")) == "BLOCKED"
        or _normalized(readiness.get("state")) == "BLOCKED"
        or readiness.get("usable") is False
    )
    return {
        "action": action,
        "previousAction": previous_action,
        "status": status,
        "watchlist": watchlist,
        "dataUnavailable": data_unavailable,
    }


def investment_notification_icon(message_type: object, context: Dict[str, object] = None) -> str:
    """Return a single action/status icon for graph-backed investment alerts.

    This is presentation only. It reads the saved final action, action-envelope
    state, and transition; it never changes a decision or delivery policy.
    """

    key = str(message_type or _context_value(context or {}, "messageType") or "").strip()
    if key not in INVESTMENT_CONTEXTUAL_MESSAGE_TYPES:
        return ""
    state = _investment_action_context(context or {})
    action = state["action"]
    previous = state["previousAction"]
    status = state["status"]
    if state["dataUnavailable"] or status == "JUDGEMENT_BLOCKED":
        return "⚠️"
    if state["watchlist"]:
        if previous in {"BUY", "ADD"} and action == "HOLD":
            return "⏸️"
        if previous in {"HOLD", "AVOID", "SELL", "TRIM"} and action in {"BUY", "ADD"}:
            return "🟢"
        if action in {"AVOID", "SELL", "TRIM"}:
            return "🛡️"
        if status == "ENTRY_ELIGIBLE" and action in {"BUY", "ADD"}:
            return "🟢"
        if status == "ENTRY_DEFERRED":
            return "⏳"
        if status == "ENTRY_OBSERVING":
            return "👀"
        if status == "ENTRY_BLOCKED":
            return "🛡️"
        return ""
    if action in {"SELL", "TRIM"}:
        return "↘️"
    if previous in {"SELL", "TRIM"} and action == "HOLD":
        return "⚖️"
    if action in {"BUY", "ADD"}:
        return "🟢"
    if action == "AVOID":
        return "🛡️"
    if status == "HOLDING_REVIEW":
        return "⚖️"
    return ""


def news_digest_notification_icon(message_type: object, context: Dict[str, object] = None) -> str:
    """Choose a presentation icon for event-style news alerts saved before a title icon."""

    if str(message_type or _context_value(context or {}, "messageType") or "").strip() != "newsDigest":
        return ""
    digest = _context_value(context or {}, "newsDigest")
    digest = digest if isinstance(digest, dict) else {}
    icon = str(digest.get("eventIcon") or "").strip()
    if icon:
        return icon
    if str(digest.get("deliveryMode") or "").strip() == "story-update":
        return "↻"
    if str(digest.get("eventKind") or "").strip() == "disclosure":
        return "📄"
    if str(digest.get("urgency") or "").strip() == "breaking":
        return "⚡"
    return ""


def notification_title_with_context_icon(
    message_type: object,
    title: object,
    context: Dict[str, object] = None,
) -> str:
    """Refresh a persisted title's icon without rewriting its customer text."""

    text = str(title or "").strip()
    if not text:
        return text
    icon = investment_notification_icon(message_type, context) or news_digest_notification_icon(message_type, context)
    if not icon:
        return text
    previous = str(_context_value(context or {}, "titleIcon") or "").strip()
    if previous and previous in text:
        return text.replace(previous, icon, 1)
    matched = re.match(r"^(\[[^\]]+\]\s+)(\S+)(\s+.*)$", text)
    if matched and not re.search(r"[A-Za-z0-9가-힣]", matched.group(2)):
        return matched.group(1) + icon + matched.group(3)
    return text if text.startswith(icon) else icon + " " + text


def notification_message_icon(message_type: object, context: Dict[str, object] = None) -> str:
    """Return the contextual icon without changing message meaning or routing."""
    values = dict(context or {}) if isinstance(context, dict) else {}
    if not values:
        return MESSAGE_TYPE_EMOJIS.get(str(message_type or "").strip(), "🔔")
    operational = operational_notification_presentation(message_type, values)
    if operational:
        return operational.icon
    icon = str(values.get("notificationIcon") or "").strip()
    if icon:
        return icon
    news_icon = news_digest_notification_icon(message_type, values)
    if news_icon:
        return news_icon
    investment_icon = investment_notification_icon(message_type, values)
    if investment_icon:
        return investment_icon
    icon = str(values.get("titleIcon") or "").strip()
    if icon:
        return icon
    return MESSAGE_TYPE_EMOJIS.get(str(message_type or "").strip(), "🔔")
