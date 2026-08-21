import re
from typing import Dict, Optional

from ..domain.investment_ubiquitous_language import user_facing_investment_language
from ..domain.investment_decision_history import context_with_ai_decision_transition
from ..domain.investment_notification_state import (
    context_with_investment_notification_state,
    investment_notification_transition_line,
)
from ..domain.notification_ai import active_investment_opinion_value, notification_ai_prompt_context, relation_context_value
from ..domain.notification_ai_gate_contracts import (
    AI_DECISION_MODE,
    AI_DECISION_SOURCE_LABEL,
    NOTIFICATION_AI_GATE_VERSION,
    NotificationAIValidatedResponse,
)
from .notification_ai_gate_message import (
    compact_invalidation_line,
    compact_next_action_line,
    execution_headline,
    execution_telegram_message,
    prepend_execution_start_badge,
    strategy_guide_quality,
)
from ..domain.notification_ai_gate_sources import source_labels_from_context
from ..domain.notification_ai_gate_text import _text, reference_date
from ..domain.notification_ai_gate_validation import (
    ai_decision_input_packet,
    delivery_profile_from_context,
    reconcile_change_analysis_with_decision_history,
)
from ..domain.notification_icon_policy import investment_notification_icon
from ..domain.notification_narrative import (
    apply_narrative_brief_to_response,
    build_investment_narrative_brief,
    narrative_fingerprint,
    response_writer_provenance,
)


def _dedupe_sentences(value: object) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    result = []
    seen = set()
    for sentence in sentences:
        clean = sentence.strip()
        key = re.sub(r"[^0-9a-z가-힣]+", "", clean.casefold())
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return " ".join(result)


def _dedupe_response_rows(values: object, limit: int) -> list:
    rows = []
    keys = []
    for value in values or []:
        clean = _dedupe_sentences(value)
        key = re.sub(r"[^0-9a-z가-힣]+", "", clean.casefold())
        if not clean or not key or any(key in known or known in key for known in keys):
            continue
        rows.append(clean)
        keys.append(key)
        if len(rows) >= limit:
            break
    return rows


def normalize_validated_ai_explanation(response: NotificationAIValidatedResponse) -> None:
    """Keep the stored AI result concise without weakening its decision."""

    for field in (
        "summary", "opinion", "current_action_plan", "change_analysis",
        "next_action_plan", "invalidation_condition", "epistemic_summary",
    ):
        setattr(response, field, _dedupe_sentences(getattr(response, field, "")))
    response.evidence = _dedupe_response_rows(response.evidence, 3)
    response.counter_evidence = _dedupe_response_rows(response.counter_evidence, 2)
    response.next_checks = _dedupe_response_rows(response.next_checks, 3)
    response.missing_data_impact = _dedupe_response_rows(response.missing_data_impact, 3)


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
    writer = response_writer_provenance(response, context)
    ai_authored = bool(writer.get("aiAuthored"))
    ai_decision_authored = bool(ai_authored and writer.get("decisionOwner") == "ai")
    validation_id = _ontology_id("ai-validation" if ai_decision_authored else "presentation-validation", assertion_key)
    opinion_id = _ontology_id("validated-opinion" if ai_decision_authored else "inference-opinion", assertion_key + ":" + response.action)
    audit_id = _ontology_id("ai-judgment-audit" if ai_decision_authored else "inference-presentation-audit", assertion_key + ":" + response.action)
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
            "tboxClass": "AIValidation" if ai_decision_authored else "ValidationAssessment",
            "engineVersion": NOTIFICATION_AI_GATE_VERSION,
            "decisionMode": AI_DECISION_MODE,
            "messageType": message_type,
            "target": target,
            "referenceDate": reference,
            "validationWarnings": list(response.validation_warnings or []),
            "writerProvenance": writer,
        },
        {
            "id": opinion_id,
            "ontologyBox": "ABox",
            "tboxClass": "ValidatedOpinion" if ai_decision_authored else "InvestmentOpinion",
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
            "writerProvenance": writer,
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
            "tboxClass": "AIJudgmentAudit" if ai_decision_authored else "ValidationAssessment",
            "decisionMode": AI_DECISION_MODE,
            "precomputedAction": response.precomputed_action,
            "aiAction": response.action,
            "disagreementReason": response.disagreement_reason,
            "validationState": response.validation_state,
            "dataState": response.data_state,
            "reviewLevel": response.review_level,
            "validationReasons": list(response.validation_reasons or []),
            "writerProvenance": writer,
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
        {"source": validation_id, "target": audit_id, "relationType": "HAS_DECISION_AUDIT"},
        {"source": validation_id, "target": dispatch_id, "relationType": "PRODUCES_VALIDATED_MESSAGE"},
        {"source": dispatch_id, "target": delivery_id, "relationType": "USES_MESSAGE_DELIVERY_PROFILE"},
    ]
    if ai_decision_authored:
        relations.append({"source": validation_id, "target": opinion_id, "relationType": "PRODUCES_AI_DECISION"})
    else:
        relations.append({"source": validation_id, "target": opinion_id, "relationType": "VALIDATES_DATA"})
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
    writer = response_writer_provenance(response, context)
    return {
        "engineVersion": NOTIFICATION_AI_GATE_VERSION,
        "decisionMode": AI_DECISION_MODE,
        "finalDecisionOwner": "aiResponse" if writer.get("decisionOwner") == "ai" else "typedbDecisionSynthesis",
        "source": response.source,
        "fallbackUsed": bool(writer.get("fallbackUsed")),
        "writerProvenance": writer,
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
        "decisionHistory": dict((context or {}).get("investmentDecisionHistory") or {}),
        "previousFinalDecision": dict((context or {}).get("previousInvestmentDecisionEpisode") or {}),
        "decisionContinuity": dict((context or {}).get("decisionContinuityPacket") or {}),
        "aiDecisionTransition": dict((context or {}).get("aiDecisionTransition") or {}),
        "inputSummary": {
            "rawLineCount": len(decision_input.get("rawAlert", {}).get("rawLines") or []),
            "activeRuleCount": len(decision_input.get("relationshipDatabaseInference", {}).get("activeRules") or []),
            "researchEvidenceCount": len(decision_input.get("researchEvidence") or []),
            "newsHeadlineCount": len(decision_input.get("newsHeadlines") or []),
            "sourceAlertEventCount": len(decision_input.get("sourceAlertEvents") or []),
            "hasDisclosure": bool(decision_input.get("disclosure")),
            "hasDecisionContinuity": bool((context or {}).get("decisionContinuityPacket")),
            "decisionContinuitySummary": dict(
                ((context or {}).get("decisionContinuityPacket") or {}).get("summary") or {}
            ) if isinstance((context or {}).get("decisionContinuityPacket"), dict) else {},
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
    normalize_validated_ai_explanation(response)
    enriched = context_with_ai_decision_transition(context or {}, response.action)
    reconciled_change, false_initial_history = reconcile_change_analysis_with_decision_history(
        enriched,
        response.action,
        response.change_analysis,
    )
    response.change_analysis = reconciled_change
    response.change_analysis = _dedupe_sentences(response.change_analysis)
    if false_initial_history:
        warning = "저장된 이전 AI 판단과 맞지 않는 첫 판단 표현을 결정 이력 기준으로 보정했습니다."
        if warning not in response.validation_warnings:
            response.validation_warnings.append(warning)
    narrative_brief = build_investment_narrative_brief(enriched, response)
    apply_narrative_brief_to_response(narrative_brief, response)
    narrative_payload = narrative_brief.to_dict()
    narrative_payload["fingerprint"] = narrative_fingerprint(narrative_payload)
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
    enriched["validatedDecisionResponse"] = payload
    enriched["notificationNarrativeBrief"] = narrative_payload
    enriched["notificationWriterProvenance"] = dict(narrative_brief.writer_provenance)
    enriched["notificationClaimValidation"] = dict(response.claim_validation or {})
    enriched = context_with_investment_notification_state(enriched)
    icon = investment_notification_icon(enriched.get("messageType") or enriched.get("rule") or "", enriched)
    if icon:
        enriched["headline"] = execution_headline(enriched, response)
        enriched["titleIcon"] = icon
    enriched["notificationAiDecisionAudit"] = audit
    writer = dict(narrative_brief.writer_provenance)
    ai_authored = bool(writer.get("aiAuthored"))
    ai_decision_authored = bool(ai_authored and writer.get("decisionOwner") == "ai")
    enriched["notificationAiGate"] = {
        "enabled": True,
        "engineVersion": NOTIFICATION_AI_GATE_VERSION,
        "decisionMode": AI_DECISION_MODE if ai_decision_authored else "typedb-evidence-presentation",
        "source": response.source,
        "validationWarnings": list(response.validation_warnings or []),
        "validationState": response.validation_state,
        "dataState": response.data_state,
        "reviewLevel": response.review_level,
        "messageDeliveryProfile": delivery_profile_from_context(enriched),
        "auditIds": audit_entity_ids,
        "strategyGuideQuality": guide_quality,
        "writerProvenance": writer,
        "claimValidation": dict(response.claim_validation or {}),
    }
    validation_payload = {
        "ontologyBox": "ABox",
        "tboxClass": "AIValidation" if ai_decision_authored else "ValidationAssessment",
        "engineVersion": NOTIFICATION_AI_GATE_VERSION,
        "decisionMode": AI_DECISION_MODE if ai_decision_authored else "typedb-evidence-presentation",
        "validates": ["activeInvestmentOpinion", "executionPlan", "missingData"],
        "finalDecisionOwner": "aiResponse" if ai_decision_authored else "typedbDecisionSynthesis",
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
        "writerProvenance": writer,
        "claimValidation": dict(response.claim_validation or {}),
    }
    if ai_decision_authored:
        enriched["ontologyAiValidation"] = validation_payload
        enriched.pop("ontologyPresentationValidation", None)
    else:
        enriched["ontologyPresentationValidation"] = validation_payload
        enriched.pop("ontologyAiValidation", None)
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
    relation_context = relation_context_value(enriched)
    action_envelope = relation_context.get("actionEnvelope") if isinstance(relation_context.get("actionEnvelope"), dict) else {}
    execution_plan = relation_context.get("executionPlan") if isinstance(relation_context.get("executionPlan"), dict) else {}
    has_invalidation = bool(
        response.invalidation_condition
        or action_envelope.get("invalidationConditions")
        or execution_plan.get("weakenConditions")
    )
    has_next_check = bool(
        response.next_checks
        or action_envelope.get("nextChecks")
        or execution_plan.get("nextChecks")
    )
    invalidation = compact_invalidation_line(enriched, response) if has_invalidation else ""
    if invalidation:
        lines.append("다음 판단 조건: " + invalidation)
    next_action = compact_next_action_line(enriched, response) if has_next_check else ""
    if next_action:
        lines.append("다음 확인: " + next_action)
    if response.missing_data_impact:
        lines.append("부족 데이터: " + " / ".join(response.missing_data_impact[:3]))
    source_label = AI_DECISION_SOURCE_LABEL if ai_authored else str(writer.get("label") or "TypeDB 관계 해석")
    lines.append("분석출처: " + source_label + " / " + response.source)
    opinion_payload = {
        "engineVersion": NOTIFICATION_AI_GATE_VERSION,
        "source": source_label,
        "messageType": enriched.get("messageType") or enriched.get("rule") or "",
        "lines": lines,
        "validatedResponse": payload,
        "writerProvenance": writer,
    }
    if ai_authored:
        enriched["notificationAiOpinion"] = opinion_payload
        enriched.pop("notificationInferenceOpinion", None)
    else:
        enriched["notificationInferenceOpinion"] = opinion_payload
        enriched.pop("notificationAiOpinion", None)
    telegram_message = prepend_execution_start_badge(execution_telegram_message(enriched, response), enriched)
    transition_line = investment_notification_transition_line(enriched)
    if transition_line:
        telegram_message = transition_line + "\n\n" + telegram_message
    delivery_level = str(delivery_profile_from_context(enriched).get("level") or "beginner")
    enriched["telegramMessage"] = user_facing_investment_language(
        telegram_message,
        settings,
        delivery_level,
    )
    enriched["readableMessage"] = re.sub(r"</?(?:b|code)>", "", enriched["telegramMessage"])
    return enriched
