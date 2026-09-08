"""Post-AI delivery policy for graph-backed investment notifications."""

from __future__ import annotations

from typing import Dict, Mapping

from .context_observation_notifications import (
    context_observation_delivery_decision,
    review_observation_delivery_decision,
    typedb_context_observation_contract,
    typedb_review_observation_contract,
)
from .hypothesis_lifecycle import has_material_delta
from .investment_decision_actionability import investment_decision_actionability
from .investment_reasoning.decision_delta import DecisionDelta
from .investment_reasoning.disposition import reasoning_disposition_delivery
from .notification.delivery_policy import (
    DeliveryPolicyContext,
    FINAL_AI_DELIVERY_POLICY_VERSION,
    evaluate_final_decision_delivery,
)
from .ontology_decision_state import REVIEW_LEVEL_RANK


PRE_AI_DEFERRED_DELIVERY_POLICY_VERSION = "pre-ai-deferred-delivery-v2"

EXPLICIT_DELIVERY_AUTHORIZATIONS = {
    "typedb-profit-loss-change": (
        "profit-loss-threshold-transition",
        "손익 관리 조건 변화가 확인되어 현재 판단을 다시 알립니다.",
    ),
}

VERIFIED_MARKET_TRANSITION_TRIGGER_IDS = frozenset({
    "insight_profit_loss_worsened",
    "insight_profit_loss_improved",
    "insight_ma60_crossed_below",
    "insight_ma60_crossed_above",
})


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _items(value: object):
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if _text(item)]
    return [_text(value)] if _text(value) else []


def explicit_delivery_authorization(context: Mapping[str, object]) -> Dict[str, str]:
    """Return a delivery authorization already granted by admission policy.

    Cooldown and material-change admission run before TypeDB/AI delivery gates.
    A later unchanged-state gate must not revoke an explicit TypeDB-backed
    threshold authorization. Generic ``meaningful-change`` is intentionally
    provisional because internal relation lifecycle churn is not user value.
    """

    decision = _text(_mapping(context).get("cooldownDecision")).lower()
    authorization = EXPLICIT_DELIVERY_AUTHORIZATIONS.get(decision)
    if not authorization:
        return {}
    value_class, reason = authorization
    return {
        "decision": decision,
        "pushValueClass": value_class,
        "reason": reason,
    }


def holding_review_baseline_is_deliverable(context: Mapping[str, object]) -> bool:
    """Allow one useful first graph opinion without opening baseline floods.

    A watchlist HOLD is only a quiet observation baseline. A real position at
    ``check`` or a stronger review level is different: suppressing that first
    TypeDB opinion means every following identical inference is classified as
    unchanged even though the user has never received the judgement.
    """

    context = _mapping(context)
    # A reference-only relation can explain the current context, but it does
    # not own an investment action.  Treating it as the first holding opinion
    # lets threshold observations bypass the initial-baseline guard and
    # produces a push that says only "자료 변화 관찰".  Keep those rows in web
    # history until an action-eligible TypeDB hypothesis exists.
    if typedb_context_observation_contract(context) or typedb_review_observation_contract(context):
        return False
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
    publication = _mapping(context.get("decisionPublication"))
    execution = _mapping(context.get("notificationAiExecutionAudit"))
    writer = _mapping(context.get("notificationWriterProvenance"))
    outcome_kind = _text(publication.get("outcomeKind")).upper()
    execution_status = _text(execution.get("status")).lower()
    return bool(
        validated.get("action")
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


def verified_follow_up_transitions(context: Mapping[str, object]):
    """Return only persisted follow-up conditions with a verified edge transition."""

    return _verified_follow_up_transitions(context)


def material_source_event_keys(context: Mapping[str, object]):
    values = _mapping(context)
    insight = _mapping(values.get("ontologyInsight"))
    semantic = _mapping(insight.get("semanticComponents"))
    return _items(
        semantic.get("materialSourceEventKeys")
        or insight.get("materialSourceEventKeys")
        or values.get("materialSourceEventKeys")
        or []
    )


def verified_market_transition_triggers(context: Mapping[str, object]):
    """Return exact market-threshold edges recorded by admission policy."""

    rows = []
    for item in _mapping(context).get("deliveryTriggerLedger") or []:
        if not isinstance(item, Mapping):
            continue
        trigger_id = _text(item.get("conditionId"))
        if not trigger_id:
            raw_trigger_id = _text(item.get("triggerId"))
            trigger_id = raw_trigger_id.rsplit(":", 1)[-1]
        if (
            trigger_id not in VERIFIED_MARKET_TRANSITION_TRIGGER_IDS
            or _text(item.get("status")).lower() not in {"matched", "released"}
        ):
            continue
        rows.append({**dict(item), "conditionId": trigger_id})
    return rows


def _relation_lifecycle_evidence_delta(context: Mapping[str, object]) -> Dict[str, object]:
    values = _mapping(context)
    transition = _mapping(values.get("decisionTransition")) or _mapping(
        _mapping(values.get("ontologyRelationDiff")).get("decisionTransition")
    )
    lifecycle = _mapping(transition.get("relationLifecycleTransition"))
    return _mapping(lifecycle.get("evidenceDelta"))


def has_user_observable_relation_delta(context: Mapping[str, object]) -> bool:
    return has_material_delta(_relation_lifecycle_evidence_delta(context))


def pre_ai_deferred_delivery_decision(context: Mapping[str, object]) -> Dict[str, object]:
    """Resolve an unchanged graph candidate after decision continuity is loaded.

    Admission cannot evaluate previous-decision follow-up conditions because the
    continuity packet is attached later. This policy closes that ordering gap:
    a verified condition edge or a new material source may still reach AI, while
    a genuinely unchanged candidate becomes a terminal web-only record.
    """

    values = _mapping(context)
    gate = _mapping(values.get("preDecisionDeliveryGate"))
    reason_code = _text(gate.get("reasonCode"))
    base = {
        "version": PRE_AI_DEFERRED_DELIVERY_POLICY_VERSION,
        "decision": "proceed",
        "reasonCode": reason_code,
        "verifiedFollowUpTransitionCount": 0,
        "materialSourceEventCount": 0,
    }
    if reason_code != "unchanged_graph_inference":
        base["reason"] = "AI 전 종결 대상이 아닙니다."
        return base

    follow_ups = verified_follow_up_transitions(values)
    market_transitions = verified_market_transition_triggers(values)
    material_sources = material_source_event_keys(values)
    graph_transition = _mapping(values.get("decisionTransition")) or _mapping(
        _mapping(values.get("ontologyRelationDiff")).get("decisionTransition")
    )
    graph_transition_kind = _text(graph_transition.get("kind")).lower()
    observable_relation_delta = has_user_observable_relation_delta(values)
    material_graph_transition = bool(
        graph_transition.get("material")
        and (
            graph_transition_kind not in {"relation-strengthened", "relation-weakened"}
            or observable_relation_delta
        )
    )
    base.update({
        "verifiedFollowUpTransitionCount": len(follow_ups),
        "verifiedMarketTransitionCount": len(market_transitions),
        "materialSourceEventCount": len(material_sources),
        "materialGraphTransition": material_graph_transition,
        "observableRelationEvidenceChanged": observable_relation_delta,
    })
    if follow_ups:
        base.update({
            "reason": "직전 판단의 확인 조건이 거짓에서 참으로 전환되어 AI 재판단을 진행합니다.",
            "pushValueClass": "verified-threshold-transition",
        })
        return base
    if market_transitions:
        base.update({
            "reason": _text(market_transitions[0].get("reason"))
            or "검증된 시장 임계값 전환이 확인되어 AI 재판단을 진행합니다.",
            "pushValueClass": "verified-market-threshold-transition",
        })
        return base
    if material_sources or material_graph_transition:
        base.update({
            "reason": "새 중요 원문 또는 실질 그래프 전이가 있어 AI 재판단을 진행합니다.",
            "pushValueClass": "material-decision-evidence",
        })
        return base

    base.update({
        "decision": "suppress",
        "suppressionReason": "unchanged_graph_without_decision_value",
        "reason": "직전 판단의 조건 전이와 새 중요 근거가 없어 웹 이력에만 저장합니다.",
        "pushValueClass": "web-only-unchanged-graph",
    })
    return base


def customer_action_contract_gaps(
    validated: Mapping[str, object],
    context: Mapping[str, object] = None,
):
    return list(
        investment_decision_actionability(context or {}, validated).get("gaps") or []
    )


def _legacy_final_ai_delivery_decision(context: Mapping[str, object]) -> Dict[str, object]:
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
    material_sources = material_source_event_keys(context)
    target_role = _text(envelope.get("targetRole") or relation.get("targetRole")).lower()
    readiness = _mapping(envelope.get("dataReadiness"))
    selected_rule_id = _text(envelope.get("selectedRuleId"))
    eligible_rule_ids = {_text(item) for item in _items(readiness.get("eligibleRuleIds"))}
    selected_core_eligible = bool(selected_rule_id and selected_rule_id in eligible_rule_ids)
    publication_outcome = _text(publication.get("outcomeKind")).upper()
    execution_status = _text(execution_audit.get("status")).lower()
    adoption_state = _text(execution_audit.get("adoptionState")).lower()
    verified_follow_ups = _verified_follow_up_transitions(context)
    verified_market_transitions = verified_market_transition_triggers(context)
    observable_relation_delta = has_user_observable_relation_delta(context)
    canonical_subject = bool(
        publication
        or context.get("investmentSubjectDecisionCaseId")
        or context.get("investmentSubjectDecisionCase")
    )
    action_contract_gaps = customer_action_contract_gaps(validated, context)
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
        "verifiedMarketTransitionCount": len(verified_market_transitions),
        "observableRelationEvidenceChanged": observable_relation_delta,
        "pushValueClass": "undetermined",
        "customerActionContractGaps": action_contract_gaps,
    }
    if typedb_context_observation_contract(context) and publication_outcome == "OBSERVATION":
        observation_decision = context_observation_delivery_decision(context)
        base.update({
            key: value
            for key, value in observation_decision.items()
            if key not in {"version", "publicationOutcome"}
        })
        base["contextObservationDeliveryVersion"] = observation_decision.get("version")
        base["contextObservationSelectedRuleId"] = observation_decision.get("selectedRuleId")
        return base
    if typedb_review_observation_contract(context) and publication_outcome == "REVIEW_ONLY":
        if base["typedbFallback"]:
            base.update({
                "decision": "suppress",
                "suppressionReason": "ai_failure_web_history",
                "reason": "AI 판단 실패와 TypeDB 대체 검토는 운영·웹 이력에만 저장하고 투자 푸시로 보내지 않습니다.",
                "pushValueClass": "web-only-ai-failure",
            })
            return base
        review_decision = review_observation_delivery_decision(context)
        base.update({
            key: value
            for key, value in review_decision.items()
            if key not in {"version", "publicationOutcome"}
        })
        base["reviewObservationDeliveryVersion"] = review_decision.get("version")
        base["reviewObservationSelectedRuleId"] = review_decision.get("selectedRuleId")
        return base
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
    explicit_authorization = explicit_delivery_authorization(context)
    if explicit_authorization:
        base.update({
            "reason": explicit_authorization["reason"],
            "pushValueClass": explicit_authorization["pushValueClass"],
            "deliveryAuthorization": explicit_authorization["decision"],
        })
        return base
    if (
        verified_market_transitions
        and _text(ai_transition.get("kind")).lower() != "action-changed"
    ):
        trigger = verified_market_transitions[0]
        base["reason"] = _text(trigger.get("reason")) or "검증된 시장 임계값 전환이 확인됐습니다."
        base["pushValueClass"] = "verified-market-threshold-transition"
        base["deliveryAuthorization"] = "market-transition:" + _text(trigger.get("conditionId"))
        return base
    if (
        _text(context.get("cooldownDecision")).lower() == "scheduled-summary"
        and _text(ai_transition.get("kind")).lower() != "action-changed"
        and not bool(user_transition.get("material"))
        and not verified_follow_ups
        and not material_sources
    ):
        base.update({
            "decision": "suppress",
            "suppressionReason": "scheduled_summary_web_history",
            "reason": "같은 판단의 정기 재확인은 웹 이력에만 저장합니다.",
            "pushValueClass": "web-only-scheduled-summary",
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
        _text(ai_transition.get("kind")).lower() == "unchanged"
        and bool(graph_transition.get("material"))
        and _text(graph_transition.get("kind")).lower()
        in {"relation-strengthened", "relation-weakened"}
        and not observable_relation_delta
    ):
        base.update({
            "decision": "suppress",
            "suppressionReason": "internal_relation_lifecycle_churn",
            "reason": (
                "최종 행동과 검증된 가격·수급 조건은 유지됐고, 그래프 세대 교체에 따른 "
                "내부 관계 이력만 달라져 웹 판단 이력에만 기록합니다."
            ),
            "pushValueClass": "web-only-internal-relation-change",
        })
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


def decision_delta_from_context(context: Mapping[str, object]) -> DecisionDelta:
    """Translate the compatibility context into one canonical decision delta."""

    values = _mapping(context)
    validated = _mapping(values.get("notificationAiValidatedResponse"))
    execution_audit = _mapping(values.get("notificationAiExecutionAudit"))
    publication = _mapping(values.get("decisionPublication"))
    writer = _mapping(values.get("notificationWriterProvenance"))
    ai_transition = _mapping(values.get("aiDecisionTransition"))
    user_transition = _mapping(values.get("investmentNotificationTransition"))
    relation = _mapping(values.get("ontologyRelationContext"))
    envelope = _mapping(relation.get("actionEnvelope"))
    graph_transition = _mapping(values.get("decisionTransition")) or _mapping(
        _mapping(values.get("ontologyRelationDiff")).get("decisionTransition")
    )
    readiness = _mapping(envelope.get("dataReadiness"))
    selected_rule_id = _text(envelope.get("selectedRuleId"))
    eligible_rule_ids = {_text(item) for item in _items(readiness.get("eligibleRuleIds"))}
    verified_follow_ups = _verified_follow_up_transitions(values)
    verified_market_transitions = verified_market_transition_triggers(values)
    market_transition = verified_market_transitions[0] if verified_market_transitions else {}
    material_sources = material_source_event_keys(values)
    execution_status = _text(execution_audit.get("status")).lower()
    return DecisionDelta(
        target_role=_text(envelope.get("targetRole") or relation.get("targetRole")).lower(),
        final_action=_text(validated.get("action")).upper(),
        previous_final_action=_text(ai_transition.get("previousAction")).upper(),
        ai_transition_kind=_text(ai_transition.get("kind")).lower(),
        history_available=bool(ai_transition.get("historyAvailable")),
        graph_transition_present=bool(graph_transition),
        graph_transition_kind=_text(graph_transition.get("kind")).lower(),
        graph_transition_material=bool(graph_transition.get("material")),
        user_state_transition_kind=_text(user_transition.get("kind")).lower(),
        user_state_changed=bool(user_transition.get("changed")),
        user_state_material=bool(user_transition.get("material")),
        selected_core_inference_eligible=bool(
            selected_rule_id and selected_rule_id in eligible_rule_ids
        ),
        typedb_fallback=execution_status == "typedb-fallback",
        publication_outcome=_text(publication.get("outcomeKind")).upper(),
        execution_status=execution_status,
        ai_adoption_state=_text(execution_audit.get("adoptionState")).lower(),
        ai_authored=bool(writer.get("aiAuthored")),
        canonical_subject=bool(
            publication
            or values.get("investmentSubjectDecisionCaseId")
            or values.get("investmentSubjectDecisionCase")
        ),
        validated_response_present=bool(validated),
        customer_action_contract_gaps=tuple(customer_action_contract_gaps(validated, values)),
        verified_follow_up_transition_count=len(verified_follow_ups),
        verified_market_transition_count=len(verified_market_transitions),
        verified_market_transition_id=_text(market_transition.get("conditionId")),
        verified_market_transition_reason=_text(market_transition.get("reason")),
        material_source_event_count=len(material_sources),
        observable_relation_evidence_changed=has_user_observable_relation_delta(values),
    )


def delivery_policy_context_from_context(
    context: Mapping[str, object],
) -> DeliveryPolicyContext:
    """Translate only notification-owned state for the delivery policy."""

    values = _mapping(context)
    authorization = explicit_delivery_authorization(values)
    return DeliveryPolicyContext(
        cooldown_decision=_text(values.get("cooldownDecision")).lower(),
        state_transition_notifications_enabled=(
            values.get("investmentStateTransitionNotificationsEnabled") is not False
        ),
        first_holding_review_authorized=first_holding_review_delivery_is_authorized(values),
        explicit_delivery_authorization=_text(authorization.get("decision")).lower(),
        explicit_delivery_value_class=_text(authorization.get("pushValueClass")),
        explicit_delivery_reason=_text(authorization.get("reason")),
    )


def final_ai_delivery_decision(context: Mapping[str, object]) -> Dict[str, object]:
    """Apply the typed delivery policy and retain legacy output only as audit."""

    values = _mapping(context)
    delta = decision_delta_from_context(values)
    policy_context = delivery_policy_context_from_context(values)
    publication_outcome = delta.publication_outcome
    is_specialized_observation = bool(
        (
            typedb_context_observation_contract(values)
            and publication_outcome == "OBSERVATION"
        )
        or (
            typedb_review_observation_contract(values)
            and publication_outcome == "REVIEW_ONLY"
        )
    )
    disposition = reasoning_disposition_delivery(values)
    if (
        disposition.get("decision") == "suppress"
        and not delta.typedb_fallback
        and not is_specialized_observation
    ):
        return {
            **disposition,
            "finalAction": delta.final_action,
            "authorizationSources": [],
            "decisionDelta": delta.to_dict(),
            "deliveryPolicyContext": policy_context.to_dict(),
            "effectiveDeliveryPolicy": "reasoning-disposition-v1",
            "deliveryPolicyParity": {
                "version": "decision-delta-parity-v1",
                "status": "not-applicable-internal-disposition",
            },
        }
    legacy = _legacy_final_ai_delivery_decision(values)
    if is_specialized_observation:
        return {
            **legacy,
            "decisionDelta": delta.to_dict(),
            "deliveryPolicyContext": policy_context.to_dict(),
            "effectiveDeliveryPolicy": "specialized-observation-policy",
            "deliveryPolicyParity": {
                "version": "decision-delta-parity-v1",
                "status": "not-applicable-specialized-observation",
            },
        }

    typed = evaluate_final_decision_delivery(delta, policy_context).to_dict(
        delta,
        policy_context,
    )
    parity_keys = (
        "decision",
        "suppressionReason",
        "pushValueClass",
        "deliveryAuthorization",
    )
    differences = {
        key: {
            "legacy": legacy.get(key),
            "typed": typed.get(key),
        }
        for key in parity_keys
        if legacy.get(key) != typed.get(key)
    }
    parity = {
        "version": "decision-delta-parity-v1",
        "status": "match" if not differences else "mismatch",
        "differences": differences,
    }
    if differences:
        # A compatibility implementation must never override the canonical
        # fail-closed contract. Keep the mismatch visible for migration work,
        # but let the typed policy own push authorization.
        return {
            **typed,
            "effectiveDeliveryPolicy": "decision-delta-v1",
            "deliveryPolicyParity": parity,
        }
    return {
        **typed,
        # Keep existing customer-facing wording while the classification and
        # audit state come from the typed policy.
        "reason": legacy.get("reason") or typed.get("reason"),
        "effectiveDeliveryPolicy": "decision-delta-v1",
        "deliveryPolicyParity": parity,
    }
