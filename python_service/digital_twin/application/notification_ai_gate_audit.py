import re
from typing import Dict, Optional

from ..domain.investment_ubiquitous_language import user_facing_investment_language
from ..domain.notification_ai import active_investment_opinion_value, notification_ai_prompt_context, relation_context_value
from ..domain.notification_ai_gate_contracts import (
    AI_DECISION_MODE,
    AI_DECISION_SOURCE_LABEL,
    NOTIFICATION_AI_GATE_VERSION,
    NotificationAIValidatedResponse,
)
from .notification_ai_gate_message import execution_telegram_message, prepend_execution_start_badge, strategy_guide_quality
from ..domain.notification_ai_gate_sources import source_labels_from_context
from ..domain.notification_ai_gate_text import _text, reference_date
from ..domain.notification_ai_gate_validation import ai_decision_input_packet, delivery_profile_from_context


def _ontology_id(kind: str, value: object) -> str:
    normalized = re.sub(r"[^A-Za-z0-9가-힣_.:-]+", "-", str(value or "").strip())
    return kind + ":" + (normalized or "notification")

def notification_ai_validation_assertions(
    context: Dict[str, object],
    response: NotificationAIValidatedResponse,
    payload: Dict[str, object],
) -> Dict[str, object]:
    message_type = str(context.get("messageType") or context.get("rule") or "notification")
    target = str(context.get("displayTarget") or context.get("target") or context.get("title") or message_type)
    reference = response.reference_date or reference_date(context)
    assertion_key = message_type + ":" + target + ":" + reference
    validation_id = _ontology_id("ai-validation", assertion_key)
    opinion_id = _ontology_id("validated-opinion", assertion_key + ":" + response.action)
    audit_id = _ontology_id("ai-judgment-audit", assertion_key + ":" + response.action)
    dispatch_id = _ontology_id("notification-dispatch", assertion_key)
    delivery_profile = delivery_profile_from_context(context)
    delivery_id = _ontology_id("message-delivery-profile", delivery_profile.get("level") or "absoluteBeginner")
    relation_context = relation_context_value(context)
    active_opinion = active_investment_opinion_value(context)
    execution_plan = relation_context.get("executionPlan") if isinstance(relation_context.get("executionPlan"), dict) else {}
    if not execution_plan and isinstance(active_opinion.get("executionPlan"), dict):
        execution_plan = active_opinion.get("executionPlan")
    entities = [
        {
            "id": validation_id,
            "ontologyBox": "ABox",
            "tboxClass": "AIValidation",
            "engineVersion": NOTIFICATION_AI_GATE_VERSION,
            "decisionMode": AI_DECISION_MODE,
            "messageType": message_type,
            "target": target,
            "referenceDate": reference,
            "validationWarnings": list(response.validation_warnings or []),
        },
        {
            "id": opinion_id,
            "ontologyBox": "ABox",
            "tboxClass": "ValidatedOpinion",
            "action": response.action,
            "actionLabel": response.action_label,
            "validationState": response.validation_state,
            "validationLabel": response.validation_label,
            "dataState": response.data_state,
            "dataStateLabel": response.data_state_label,
            "reviewLevel": response.review_level,
            "reviewLabel": response.review_label,
            "decisionMode": AI_DECISION_MODE,
            "validatedOpinion": dict(payload or {}),
        },
        {
            "id": dispatch_id,
            "ontologyBox": "ABox",
            "tboxClass": "NotificationDispatch",
            "messageType": message_type,
            "producesValidatedMessage": True,
        },
        {
            "id": audit_id,
            "ontologyBox": "ABox",
            "tboxClass": "AIJudgmentAudit",
            "decisionMode": AI_DECISION_MODE,
            "precomputedAction": response.precomputed_action,
            "aiAction": response.action,
            "disagreementReason": response.disagreement_reason,
            "validationState": response.validation_state,
            "dataState": response.data_state,
            "reviewLevel": response.review_level,
            "validationReasons": list(response.validation_reasons or []),
        },
        {
            "id": delivery_id,
            "ontologyBox": "ABox",
            "tboxClass": "MessageDeliveryProfile",
            "level": delivery_profile.get("level"),
            "label": delivery_profile.get("label"),
            "detailLevel": delivery_profile.get("detailLevel"),
            "terminology": delivery_profile.get("terminology"),
            "ruleVisibility": delivery_profile.get("ruleVisibility"),
        },
    ]
    relations = [
        {"source": validation_id, "target": opinion_id, "relationType": "VALIDATES_OPINION"},
        {"source": validation_id, "target": opinion_id, "relationType": "PRODUCES_AI_DECISION"},
        {"source": validation_id, "target": audit_id, "relationType": "HAS_DECISION_AUDIT"},
        {"source": validation_id, "target": dispatch_id, "relationType": "PRODUCES_VALIDATED_MESSAGE"},
        {"source": dispatch_id, "target": delivery_id, "relationType": "USES_MESSAGE_DELIVERY_PROFILE"},
    ]
    if active_opinion:
        active_id = _ontology_id("active-opinion", target)
        entities.append({
            "id": active_id,
            "ontologyBox": "ABox",
            "tboxClass": "ActiveInvestmentOpinion",
            "action": active_opinion.get("action"),
            "source": "notification-context",
        })
        relations.append({"source": validation_id, "target": active_id, "relationType": "VALIDATES_OPINION"})
    if execution_plan:
        plan_id = _ontology_id("execution-plan", target)
        entities.append({
            "id": plan_id,
            "ontologyBox": "ABox",
            "tboxClass": "ExecutionPlan",
            "primaryAction": execution_plan.get("primaryAction"),
            "primaryActionLabel": execution_plan.get("primaryActionLabel"),
            "executionPlan": dict(execution_plan),
        })
        relations.append({"source": active_id if active_opinion else opinion_id, "target": plan_id, "relationType": "HAS_EXECUTION_PLAN"})
        relations.append({"source": validation_id, "target": plan_id, "relationType": "VALIDATES_DATA"})
    return {
        "box": "ABox",
        "engineVersion": NOTIFICATION_AI_GATE_VERSION,
        "entities": entities,
        "relations": relations,
    }

def notification_ai_decision_audit(
    context: Dict[str, object],
    response: NotificationAIValidatedResponse,
    payload: Dict[str, object],
) -> Dict[str, object]:
    prompt_context = notification_ai_prompt_context(str((context or {}).get("messageType") or (context or {}).get("rule") or "notification"), context or {})
    delivery_profile = delivery_profile_from_context(context or {})
    decision_input = ai_decision_input_packet(context or {}, prompt_context, delivery_profile)
    source_urls = list(response.source_urls or [])
    source_labels = source_labels_from_context(context or {}, payload)
    guide_quality = strategy_guide_quality(context or {}, response)
    return {
        "engineVersion": NOTIFICATION_AI_GATE_VERSION,
        "decisionMode": AI_DECISION_MODE,
        "finalDecisionOwner": "aiResponse",
        "source": response.source,
        "fallbackUsed": "fallback" in str(response.source or "").lower(),
        "precomputedAction": response.precomputed_action,
        "aiAction": response.action,
        "disagreement": bool(response.disagreement_reason),
        "disagreementReason": response.disagreement_reason,
        "validationState": response.validation_state,
        "validationLabel": response.validation_label,
        "dataState": response.data_state,
        "dataStateLabel": response.data_state_label,
        "reviewLevel": response.review_level,
        "reviewLabel": response.review_label,
        "validationReasons": list(response.validation_reasons or []),
        "validationWarnings": list(response.validation_warnings or []),
        "sourceUrls": source_urls,
        "sourceLabels": source_labels,
        "strategyGuideQuality": guide_quality,
        "inputSummary": {
            "rawLineCount": len(decision_input.get("rawAlert", {}).get("rawLines") or []),
            "activeRuleCount": len(decision_input.get("relationshipDatabaseInference", {}).get("activeRules") or []),
            "researchEvidenceCount": len(decision_input.get("researchEvidence") or []),
            "newsHeadlineCount": len(decision_input.get("newsHeadlines") or []),
            "sourceAlertEventCount": len(decision_input.get("sourceAlertEvents") or []),
            "hasDisclosure": bool(decision_input.get("disclosure")),
        },
        "inputPacket": decision_input,
        "rawResponseSnippet": _text(response.raw_response, 1200),
        "parsedResponse": dict(payload or {}),
    }

def context_with_validated_ai_response(
    context: Dict[str, object],
    response: NotificationAIValidatedResponse,
    settings: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    enriched = dict(context or {})
    payload = response.to_dict()
    guide_quality = strategy_guide_quality(enriched, response)
    payload["strategyGuideQuality"] = guide_quality
    audit = notification_ai_decision_audit(enriched, response, payload)
    assertions = notification_ai_validation_assertions(enriched, response, payload)
    audit_entity_ids = [
        item.get("id")
        for item in assertions.get("entities", [])
        if isinstance(item, dict) and item.get("tboxClass") == "AIJudgmentAudit"
    ]
    enriched["notificationAiValidatedResponse"] = payload
    enriched["notificationAiDecisionAudit"] = audit
    enriched["notificationAiGate"] = {
        "enabled": True,
        "engineVersion": NOTIFICATION_AI_GATE_VERSION,
        "decisionMode": AI_DECISION_MODE,
        "source": response.source,
        "validationWarnings": list(response.validation_warnings or []),
        "validationState": response.validation_state,
        "dataState": response.data_state,
        "reviewLevel": response.review_level,
        "messageDeliveryProfile": delivery_profile_from_context(enriched),
        "auditIds": audit_entity_ids,
        "strategyGuideQuality": guide_quality,
    }
    enriched["ontologyAiValidation"] = {
        "ontologyBox": "ABox",
        "tboxClass": "AIValidation",
        "engineVersion": NOTIFICATION_AI_GATE_VERSION,
        "decisionMode": AI_DECISION_MODE,
        "validates": ["activeInvestmentOpinion", "executionPlan", "missingData"],
        "finalDecisionOwner": "aiResponse",
        "validatedOpinion": payload,
        "decisionAudit": {
            "precomputedAction": response.precomputed_action,
            "aiAction": response.action,
            "disagreementReason": response.disagreement_reason,
            "validationState": response.validation_state,
            "dataState": response.data_state,
            "reviewLevel": response.review_level,
            "validationReasons": list(response.validation_reasons or []),
        },
        "validationWarnings": list(response.validation_warnings or []),
        "strategyGuideQuality": guide_quality,
        "producesValidatedMessage": True,
        "assertionIds": [item.get("id") for item in assertions.get("entities", [])],
    }
    enriched["ontologyAssertions"] = assertions
    lines = [
        "판단: " + response.action_label + " · " + response.review_label,
        "자료 상태: " + response.data_state_label + " · AI 검증: " + response.validation_label,
        "해석: " + response.summary,
    ]
    if response.evidence:
        lines.append("근거: " + " / ".join(response.evidence[:3]))
    if response.counter_evidence:
        lines.append("반대 근거: " + " / ".join(response.counter_evidence[:3]))
    if response.invalidation_condition:
        lines.append("의견이 약해지는 조건: " + response.invalidation_condition)
    if response.next_checks:
        lines.append("다음 확인: " + " / ".join(response.next_checks[:3]))
    if response.missing_data_impact:
        lines.append("부족 데이터: " + " / ".join(response.missing_data_impact[:3]))
    lines.append("분석출처: " + AI_DECISION_SOURCE_LABEL + " / " + response.source)
    enriched["notificationAiOpinion"] = {
        "engineVersion": NOTIFICATION_AI_GATE_VERSION,
        "source": AI_DECISION_SOURCE_LABEL,
        "messageType": enriched.get("messageType") or enriched.get("rule") or "",
        "lines": lines,
        "validatedResponse": payload,
    }
    telegram_message = prepend_execution_start_badge(execution_telegram_message(enriched, response), enriched)
    delivery_level = str(delivery_profile_from_context(enriched).get("level") or "beginner")
    enriched["telegramMessage"] = user_facing_investment_language(
        telegram_message,
        settings,
        delivery_level,
    )
    enriched["readableMessage"] = re.sub(r"</?(?:b|code)>", "", enriched["telegramMessage"])
    return enriched
