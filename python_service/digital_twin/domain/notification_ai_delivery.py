"""Post-AI delivery policy for graph-backed investment notifications."""

from __future__ import annotations

from typing import Dict, Mapping

from .context_observation_notifications import (
    context_observation_delivery_decision,
    typedb_context_observation_contract,
)
from .ontology_decision_state import REVIEW_LEVEL_RANK


FINAL_AI_DELIVERY_POLICY_VERSION = "final-ai-delivery-v9"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _items(value: object):
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if _text(item)]
    return [_text(value)] if _text(value) else []


def holding_review_baseline_is_deliverable(context: Mapping[str, object]) -> bool:
    """Allow one useful first graph opinion without opening baseline floods.

    A watchlist HOLD is only a quiet observation baseline. A real position at
    ``check`` or a stronger review level is different: suppressing that first
    TypeDB opinion means every following identical inference is classified as
    unchanged even though the user has never received the judgement.
    """

    context = _mapping(context)
    relation = _mapping(context.get("ontologyRelationContext"))
    envelope = _mapping(relation.get("actionEnvelope"))
    relation_state = _mapping(relation.get("decisionState"))
    synthesis = _mapping(context.get("v2DecisionSynthesis"))
    target_role = _text(
        envelope.get("targetRole")
        or relation.get("targetRole")
        or synthesis.get("target_role")
        or context.get("targetRole")
    ).lower()
    review_level = _text(
        synthesis.get("review_level")
        or relation_state.get("reviewLevel")
        or envelope.get("reviewLevel")
        or context.get("deliveryReviewLevel")
    ).lower()
    return (
        target_role == "holding"
        and review_level != "blocked"
        and REVIEW_LEVEL_RANK.get(review_level, -1)
        >= REVIEW_LEVEL_RANK["check"]
    )


def first_holding_review_candidate_is_admissible(context: Mapping[str, object]) -> bool:
    """Allow one holding baseline to reach AI, without authorizing a push."""

    context = _mapping(context)
    try:
        recent_sent_count = int(context.get("cooldownRecentSentCount") or 0)
    except (TypeError, ValueError):
        recent_sent_count = 0
    return (
        holding_review_baseline_is_deliverable(context)
        and _text(context.get("cooldownDecision")).lower() == "new-condition"
        and recent_sent_count <= 0
    )


def first_holding_review_delivery_is_authorized(context: Mapping[str, object]) -> bool:
    """Authorize only the first completed, AI-authored holding decision."""

    context = _mapping(context)
    if not first_holding_review_candidate_is_admissible(context):
        return False
    validated = _mapping(context.get("notificationAiValidatedResponse"))
    transition = _mapping(context.get("aiDecisionTransition"))
    publication = _mapping(context.get("decisionPublication"))
    execution = _mapping(context.get("notificationAiExecutionAudit"))
    writer = _mapping(context.get("notificationWriterProvenance"))
    outcome_kind = _text(publication.get("outcomeKind")).upper()
    execution_status = _text(execution.get("status")).lower()
    return bool(
        validated.get("action")
        and transition.get("historyAvailable") is False
        and (not outcome_kind or outcome_kind == "FINAL_DECISION")
        and (not execution_status or execution_status == "completed")
        and execution_status != "typedb-fallback"
        and (not writer or bool(writer.get("aiAuthored")))
    )


def _verified_follow_up_transitions(context: Mapping[str, object]):
    packet = _mapping(_mapping(context).get("decisionContinuityPacket"))
    return [
        dict(item)
        for item in packet.get("followUpConditions") or []
        if isinstance(item, Mapping)
        and bool(item.get("transitionVerified"))
        and _text(item.get("transitionAt"))
        and _text(item.get("status")).lower() in {"satisfied", "invalidated", "expired"}
        and (
            _text(item.get("status")).lower() == "expired"
            or (item.get("previousMatched") is False and item.get("currentMatched") is True)
        )
    ]


def _customer_action_contract_gaps(validated: Mapping[str, object]):
    response = _mapping(validated)
    gaps = []
    if not _text(response.get("action")):
        gaps.append("final-action")
    if not _text(
        response.get("executionDecision")
        or response.get("currentActionPlan")
        or response.get("investmentViewAction")
    ):
        gaps.append("current-action-plan")
    if not (
        _text(response.get("changeAnalysis") or response.get("investmentView") or response.get("summary"))
        or _items(response.get("evidence"))
    ):
        gaps.append("why-now")
    if not (
        _text(response.get("nextActionPlan") or response.get("invalidationCondition"))
        or _items(response.get("nextChecks"))
        or _items(response.get("followUpConditions"))
    ):
        gaps.append("next-condition")
    return gaps


def final_ai_delivery_decision(context: Mapping[str, object]) -> Dict[str, object]:
    """Suppress candidate churn when the final user action did not move.

    TypeDB owns the candidate and action envelope. The AI owns the final user
    action. Candidate movement remains auditable, but it must not bypass a push
    cooldown as an execution change when the previous and current final actions
    are identical and no explicitly decision-changing source event was added.
    """

    context = _mapping(context)
    validated = _mapping(context.get("notificationAiValidatedResponse"))
    execution_audit = _mapping(context.get("notificationAiExecutionAudit"))
    publication = _mapping(context.get("decisionPublication"))
    writer = _mapping(context.get("notificationWriterProvenance"))
    ai_transition = _mapping(context.get("aiDecisionTransition"))
    user_transition = _mapping(context.get("investmentNotificationTransition"))
    relation = _mapping(context.get("ontologyRelationContext"))
    envelope = _mapping(relation.get("actionEnvelope"))
    graph_transition = _mapping(context.get("decisionTransition")) or _mapping(
        _mapping(context.get("ontologyRelationDiff")).get("decisionTransition")
    )
    semantic = _mapping(_mapping(context.get("ontologyInsight")).get("semanticComponents"))
    material_sources = _items(
        semantic.get("materialSourceEventKeys")
        or _mapping(context.get("ontologyInsight")).get("materialSourceEventKeys")
        or context.get("materialSourceEventKeys")
        or []
    )
    target_role = _text(envelope.get("targetRole") or relation.get("targetRole")).lower()
    readiness = _mapping(envelope.get("dataReadiness"))
    selected_rule_id = _text(envelope.get("selectedRuleId"))
    eligible_rule_ids = {_text(item) for item in _items(readiness.get("eligibleRuleIds"))}
    selected_core_eligible = bool(selected_rule_id and selected_rule_id in eligible_rule_ids)
    publication_outcome = _text(publication.get("outcomeKind")).upper()
    execution_status = _text(execution_audit.get("status")).lower()
    adoption_state = _text(execution_audit.get("adoptionState")).lower()
    verified_follow_ups = _verified_follow_up_transitions(context)
    canonical_subject = bool(
        publication
        or context.get("investmentSubjectDecisionCaseId")
        or context.get("investmentSubjectDecisionCase")
    )
    action_contract_gaps = _customer_action_contract_gaps(validated)
    base = {
        "version": FINAL_AI_DELIVERY_POLICY_VERSION,
        "decision": "send",
        "reason": "최종 AI 판단 발송 조건을 통과했습니다.",
        "targetRole": target_role,
        "finalAction": _text(validated.get("action")).upper(),
        "previousFinalAction": _text(ai_transition.get("previousAction")).upper(),
        "graphTransitionKind": _text(graph_transition.get("kind")).lower(),
        "materialSourceEventCount": len(material_sources),
        "userStateTransitionKind": _text(user_transition.get("kind")).lower(),
        "userStateChanged": bool(user_transition.get("changed")),
        "selectedCoreInferenceEligible": selected_core_eligible,
        "typedbFallback": execution_status == "typedb-fallback",
        "publicationOutcome": publication_outcome,
        "aiAdoptionState": adoption_state,
        "verifiedFollowUpTransitionCount": len(verified_follow_ups),
        "pushValueClass": "undetermined",
        "customerActionContractGaps": action_contract_gaps,
    }
    if publication_outcome in {"REVIEW_ONLY", "ABSTAIN", "ABSTAINED"}:
        base.update({
            "decision": "suppress",
            "suppressionReason": "review_only_web_history",
            "reason": "최종 투자 판단이 아닌 검토 결과는 웹 이력에만 저장합니다.",
            "pushValueClass": "web-only-review",
        })
        return base
    if base["typedbFallback"]:
        base.update({
            "decision": "suppress",
            "suppressionReason": "ai_failure_web_history",
            "reason": "AI 판단 실패와 TypeDB 대체 결과는 운영·웹 이력에만 저장하고 투자 푸시로 보내지 않습니다.",
            "pushValueClass": "web-only-ai-failure",
        })
        return base
    if typedb_context_observation_contract(context):
        observation_decision = context_observation_delivery_decision(context)
        base.update({
            key: value
            for key, value in observation_decision.items()
            if key not in {"version", "publicationOutcome"}
        })
        base["contextObservationDeliveryVersion"] = observation_decision.get("version")
        base["contextObservationSelectedRuleId"] = observation_decision.get("selectedRuleId")
        return base
    if canonical_subject and publication_outcome != "FINAL_DECISION":
        base.update({
            "decision": "suppress",
            "suppressionReason": "missing_final_decision_publication",
            "reason": "정식 최종 판단 발행물이 없어 웹 검토 이력에만 저장합니다.",
            "pushValueClass": "web-only-incomplete-publication",
        })
        return base
    if canonical_subject and (
        execution_status != "completed"
        or adoption_state != "decision-and-narrative-adopted"
        or not bool(writer.get("aiAuthored"))
    ):
        base.update({
            "decision": "suppress",
            "suppressionReason": "ai_decision_not_adopted",
            "reason": "완료되고 검증된 AI 판단이 최종 발행물에 채택되지 않아 푸시하지 않습니다.",
            "pushValueClass": "web-only-unadopted-ai",
        })
        return base
    if canonical_subject and action_contract_gaps:
        base.update({
            "decision": "suppress",
            "suppressionReason": "incomplete_customer_action_contract",
            "reason": "현재 행동·변경 이유·다음 조건 중 일부가 없어 불완전한 투자 알림을 보내지 않습니다.",
            "pushValueClass": "web-only-incomplete-message",
        })
        return base
    if not validated or not base["finalAction"] or base["finalAction"] == "NO_ACTION":
        base.update({
            "decision": "suppress",
            "suppressionReason": "missing_validated_final_action",
            "reason": "사용자가 실행·유지할 최종 행동이 검증되지 않아 푸시하지 않습니다.",
            "pushValueClass": "web-only-missing-action",
        })
        return base
    if first_holding_review_delivery_is_authorized(context):
        base.update({
            "reason": "보유 종목의 첫 최종 AI 판단이 완료되어 현재 행동과 다음 확인 조건을 알립니다.",
            "pushValueClass": "first-final-holding-decision",
        })
        return base
    transition_enabled = context.get("investmentStateTransitionNotificationsEnabled") is not False
    if not ai_transition.get("historyAvailable"):
        if base["finalAction"] == "HOLD":
            base.update({
                "decision": "suppress",
                "suppressionReason": "initial_graph_baseline",
                "reason": "최초 보유·관찰 상태지만 첫 판단 발송 조건을 충족하지 않아 기준선으로만 저장합니다.",
                "pushValueClass": "web-only-initial-baseline",
            })
            return base
        base["pushValueClass"] = "initial-action-decision"
        return base
    if (
        _text(ai_transition.get("kind")).lower() == "action-changed"
        and bool(graph_transition)
        and _text(graph_transition.get("kind")).lower() == "initial"
        and not bool(graph_transition.get("material"))
        and not material_sources
    ):
        base.update({
            "decision": "suppress",
            "suppressionReason": "non_material_action_rebaseline",
            "reason": (
                "최종 행동 후보는 바뀌었지만 그래프의 실질 변화나 새 판단 원문이 없어 "
                "기준선 이력에만 기록합니다."
            ),
        })
        return base
    if transition_enabled and user_transition.get("material"):
        base["reason"] = "사용자에게 표시되는 최종 판단 상태가 변경됐습니다."
        base["pushValueClass"] = "material-user-state-transition"
        return base
    if (
        _text(graph_transition.get("kind")).lower() == "initial"
        and not bool(graph_transition.get("material"))
        and base["finalAction"] == "HOLD"
        and not user_transition.get("changed")
    ):
        base.update({
            "decision": "suppress",
            "suppressionReason": "final_ai_state_unchanged",
            "reason": "이전 최종 판단과 같은 비실행 상태라 다시 알리지 않습니다.",
        })
        return base
    if (
        user_transition.get("changed")
        and not transition_enabled
        and _text(ai_transition.get("kind")).lower() != "action-changed"
        and not material_sources
    ):
        base.update({
            "decision": "suppress",
            "suppressionReason": "inference_state_notification_disabled",
            "reason": "추론 상태 변경 알림 설정이 꺼져 있어 상태 이력만 저장합니다.",
        })
        return base
    if _text(ai_transition.get("kind")).lower() == "action-changed":
        base["pushValueClass"] = "final-action-change"
        return base
    if verified_follow_ups:
        base["reason"] = "직전 판단의 관찰 조건이 거짓에서 참으로 실제 전환됐습니다."
        base["pushValueClass"] = "verified-threshold-transition"
        return base
    if material_sources:
        base["reason"] = "최종 행동은 유지됐지만 판단 변경 원문이 새로 확인됐습니다."
        base["pushValueClass"] = "material-source-evidence"
        return base
    if (
        user_transition.get("changed")
        and not user_transition.get("material")
        and _text(ai_transition.get("kind")).lower() != "action-changed"
    ):
        base.update({
            "decision": "suppress",
            "suppressionReason": "non_actionable_readiness_change",
            "reason": (
                "최종 행동은 유지됐고 판단 차단·복구가 아닌 자료 또는 AI 응답 검증 상태만 "
                "바뀌어 웹 판단 이력에만 기록합니다."
            ),
        })
        return base
    if (
        _text(ai_transition.get("kind")).lower() == "unchanged"
        and bool(graph_transition.get("material"))
        and _text(graph_transition.get("kind")).lower() in {
            "action-changed", "envelope-changed", "readiness-changed",
        }
    ):
        base.update({
            "decision": "suppress",
            "suppressionReason": "graph_candidate_only_change",
            "reason": (
                "TypeDB 계산 후보만 바뀌고 최종 AI 행동은 "
                + (base["finalAction"] or "동일")
                + "로 유지됐으며 새 판단 변경 원문이 없어 푸시하지 않습니다."
            ),
        })
        return base
    base.update({
        "decision": "suppress",
        "suppressionReason": "no_user_action_or_material_evidence_change",
        "reason": "최종 행동과 검증된 임계값·판단 원문이 모두 유지되어 웹 판단 이력에만 저장합니다.",
        "pushValueClass": "web-only-context-change",
    })
    return base
