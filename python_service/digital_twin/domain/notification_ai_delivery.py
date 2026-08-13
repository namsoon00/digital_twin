"""Post-AI delivery policy for graph-backed investment notifications."""

from __future__ import annotations

from typing import Dict, Mapping


FINAL_AI_DELIVERY_POLICY_VERSION = "final-ai-delivery-v1"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _items(value: object):
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if _text(item)]
    return [_text(value)] if _text(value) else []


def final_ai_delivery_decision(context: Mapping[str, object]) -> Dict[str, object]:
    """Suppress watchlist candidate churn when the final AI action did not move.

    TypeDB owns the candidate and action envelope. The AI owns the final user
    action. Candidate movement remains auditable, but it must not bypass a push
    cooldown as an execution change when the previous and current final actions
    are identical and no explicitly decision-changing source event was added.
    """

    context = _mapping(context)
    validated = _mapping(context.get("notificationAiValidatedResponse"))
    ai_transition = _mapping(context.get("aiDecisionTransition"))
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
    base = {
        "version": FINAL_AI_DELIVERY_POLICY_VERSION,
        "decision": "send",
        "reason": "최종 AI 판단 발송 조건을 통과했습니다.",
        "targetRole": target_role,
        "finalAction": _text(validated.get("action")).upper(),
        "previousFinalAction": _text(ai_transition.get("previousAction")).upper(),
        "graphTransitionKind": _text(graph_transition.get("kind")).lower(),
        "materialSourceEventCount": len(material_sources),
    }
    if not validated or not ai_transition.get("historyAvailable"):
        return base
    if _text(ai_transition.get("kind")).lower() == "action-changed":
        return base
    if material_sources:
        base["reason"] = "최종 행동은 유지됐지만 판단 변경 원문이 새로 확인됐습니다."
        return base
    if (
        target_role == "watchlist"
        and _text(ai_transition.get("kind")).lower() == "unchanged"
        and bool(graph_transition.get("material"))
        and _text(graph_transition.get("kind")).lower() in {
            "action-changed", "envelope-changed", "readiness-changed",
        }
    ):
        base.update({
            "decision": "suppress",
            "reason": (
                "관심종목의 TypeDB 계산 후보만 바뀌고 최종 AI 행동은 "
                + (base["finalAction"] or "동일")
                + "로 유지됐으며 새 판단 변경 원문이 없어 푸시하지 않습니다."
            ),
        })
    return base
