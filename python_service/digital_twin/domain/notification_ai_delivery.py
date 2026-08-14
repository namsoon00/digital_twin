"""Post-AI delivery policy for graph-backed investment notifications."""

from __future__ import annotations

from typing import Dict, Mapping


FINAL_AI_DELIVERY_POLICY_VERSION = "final-ai-delivery-v2"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _items(value: object):
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if _text(item)]
    return [_text(value)] if _text(value) else []


def final_ai_delivery_decision(context: Mapping[str, object]) -> Dict[str, object]:
    """Suppress candidate churn when the final user action did not move.

    TypeDB owns the candidate and action envelope. The AI owns the final user
    action. Candidate movement remains auditable, but it must not bypass a push
    cooldown as an execution change when the previous and current final actions
    are identical and no explicitly decision-changing source event was added.
    """

    context = _mapping(context)
    validated = _mapping(context.get("notificationAiValidatedResponse"))
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
    }
    transition_enabled = context.get("investmentStateTransitionNotificationsEnabled") is not False
    if transition_enabled and user_transition.get("changed"):
        base["reason"] = "사용자에게 표시되는 최종 판단 상태가 변경됐습니다."
        return base
    if not validated:
        return base
    if not ai_transition.get("historyAvailable"):
        if (
            _text(graph_transition.get("kind")).lower() == "initial"
            and not bool(graph_transition.get("material"))
            and base["finalAction"] == "HOLD"
        ):
            base.update({
                "decision": "suppress",
                "suppressionReason": "initial_graph_baseline",
                "reason": "첫 비실행 최종 판단 상태를 알림 없이 기준선으로 저장합니다.",
            })
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
        return base
    if material_sources:
        base["reason"] = "최종 행동은 유지됐지만 판단 변경 원문이 새로 확인됐습니다."
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
