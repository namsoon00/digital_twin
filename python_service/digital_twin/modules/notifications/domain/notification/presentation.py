"""Customer notification kinds, independent of legacy queue policy keys."""

from dataclasses import dataclass
from typing import Mapping


PRESENTATION_VERSION = "notification-presentation-v3"


@dataclass(frozen=True)
class NotificationKind:
    key: str
    label: str
    icon: str


NOTIFICATION_KINDS = {
    item.key: item for item in (
        NotificationKind("price-change", "시세 변화", "📊"),
        NotificationKind("relation-change", "관계 변화", "🔗"),
        NotificationKind("ai-interpretation", "AI 해석", "🧠"),
        NotificationKind("investment-decision", "투자 판단", "🧭"),
        NotificationKind("news", "뉴스·공시", "📰"),
        NotificationKind("account-change", "계좌 변화", "💼"),
        NotificationKind("holdings", "보유 현황", "📋"),
        NotificationKind("calendar", "투자 일정", "🗓️"),
        NotificationKind("operations", "운영 상태", "⚙️"),
        NotificationKind("report", "개발·검토 보고", "📦"),
        NotificationKind("notice", "알림", "🔔"),
    )
}

LEGACY_KINDS = {
    "marketObservation": "price-change",
    "newsDigest": "news",
    "externalDartDisclosure": "news",
    "portfolioActivityObservation": "account-change",
    "portfolioHoldingsSnapshot": "holdings",
    "investmentCalendarReminder": "calendar",
    "ontologyInferenceMissing": "operations",
    "ontologyReasoningQueue": "operations",
    "investmentAlertCoverage": "operations",
    "operationalStorageCapacity": "operations",
    "monitorHeartbeat": "operations",
    "monitorConnection": "operations",
    "externalDataConnection": "operations",
    "modelReview": "report",
    "workHandoff": "report",
    "operatorReasoningReport": "report",
    "ontologyLabExperiment": "report",
}

DEFAULT_POLICY_TYPES = {
    "price-change": "marketObservation", "relation-change": "investmentInsight",
    "ai-interpretation": "investmentInsight", "investment-decision": "investmentInsight",
    "news": "newsDigest", "account-change": "portfolioActivityObservation",
    "holdings": "portfolioHoldingsSnapshot", "calendar": "investmentCalendarReminder",
    "operations": "notification", "report": "workHandoff", "notice": "notification",
}


def mapping(value: object) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def context_value(context: Mapping, key: str):
    value = context.get(key)
    return value if value not in (None, "", {}, []) else mapping(context.get("metadata")).get(key)


def notification_kind(message_type: str, context: Mapping = None) -> NotificationKind:
    """Read upstream authority; neither choose an action nor admit delivery."""

    values = mapping(context)
    content = mapping(values.get("notificationContent"))
    requested = str(content.get("kind") or "").strip()
    writer = mapping(context_value(values, "notificationWriterProvenance"))
    ai_authored = writer.get("aiAuthored") is True
    response = (
        mapping(context_value(values, "notificationAiValidatedResponse"))
        or mapping(context_value(values, "validatedDecisionResponse"))
        or mapping(context_value(values, "notificationInferenceResponse"))
    )
    publication = mapping(context_value(values, "decisionPublication"))
    mode = str(context_value(values, "notificationDecisionMode") or "")
    action = str(response.get("action") or "").upper()
    narrative_only = (
        requested == "ai-interpretation"
        or mode in {"typedb-context-observation", "typedb-review-observation", "context-narrative"}
        or str(writer.get("writerRole") or "") in {"narrative-only", "context-narrative"}
        or str(publication.get("outcomeKind") or "").upper() in {"OBSERVATION", "REVIEW_ONLY", "ABSTAIN"}
        or action == "NO_ACTION"
    )
    if message_type == "investmentInsight" or requested in {"investment-decision", "ai-interpretation"}:
        if not narrative_only and action in {"BUY", "ADD", "HOLD", "WATCH", "TRIM", "SELL", "AVOID"}:
            return NOTIFICATION_KINDS["investment-decision"]
        if ai_authored:
            return NOTIFICATION_KINDS["ai-interpretation"]
        trigger = mapping(context_value(values, "reasoningDeliveryTrigger"))
        facts = mapping(trigger.get("facts"))
        transitions = [mapping(row) for row in facts.get("confirmedSignalTransitions") or []]
        rules = mapping(context_value(values, "ontologyRelationContext"))
        decision = mapping(rules.get("decision"))
        rule_id = str(decision.get("selectedRuleId") or "").lower()
        raw_delta = "raw_delta" in rule_id or "raw-delta" in rule_id or rule_id == "graph.materiality.alert_candidate.v1"
        if facts.get("cryptoTransitions") or (raw_delta and transitions and all(
            str(row.get("signalId") or "") in {"price", "price-change", "pnl"} for row in transitions
        )):
            return NOTIFICATION_KINDS["price-change"]
        if not rules and not mode and requested != "relation-change":
            return NOTIFICATION_KINDS["notice"]
        return NOTIFICATION_KINDS["relation-change"]
    if requested:
        return NOTIFICATION_KINDS.get(requested, NOTIFICATION_KINDS["notice"])
    return NOTIFICATION_KINDS[LEGACY_KINDS.get(message_type, "notice")]


def presentation_metadata(message_type: str, context: Mapping = None) -> dict:
    values = mapping(context)
    content = mapping(values.get("notificationContent"))
    kind = notification_kind(message_type, values)
    subject = content.get("subject") or context_value(values, "notificationSubject")
    if isinstance(subject, Mapping):
        subject = subject.get("name") or subject.get("symbol") or ""
    subject = str(
        subject or context_value(values, "symbolDisplayName")
        or context_value(values, "displayTarget") or context_value(values, "target")
        or context_value(values, "rawSymbol") or context_value(values, "symbol") or ""
    ).strip()
    if "/" in subject:
        subject = subject.split("/", 1)[0].strip()
    return {
        "version": PRESENTATION_VERSION,
        "kind": kind.key,
        "label": kind.label,
        "icon": kind.icon,
        "subject": subject,
        "policyMessageType": str(message_type or "notification"),
    }
