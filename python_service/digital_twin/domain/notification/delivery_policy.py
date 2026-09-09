"""Pure notification delivery policy for a canonical investment decision delta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from ..investment_reasoning.decision_delta import DecisionDelta


FINAL_AI_DELIVERY_POLICY_VERSION = "final-ai-delivery-v18"


@dataclass(frozen=True)
class DeliveryPolicyContext:
    """Delivery-only state kept outside the investment decision contract."""

    cooldown_decision: str = ""
    state_transition_notifications_enabled: bool = True
    first_holding_review_authorized: bool = False
    explicit_delivery_authorization: str = ""
    explicit_delivery_value_class: str = ""
    explicit_delivery_reason: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "cooldownDecision": self.cooldown_decision,
            "stateTransitionNotificationsEnabled": self.state_transition_notifications_enabled,
            "firstHoldingReviewAuthorized": self.first_holding_review_authorized,
            "explicitDeliveryAuthorization": self.explicit_delivery_authorization,
        }


@dataclass(frozen=True)
class DeliveryDecision:
    decision: str
    reason: str
    push_value_class: str
    suppression_reason: str = ""
    delivery_authorization: str = ""
    version: str = FINAL_AI_DELIVERY_POLICY_VERSION

    @property
    def should_send(self) -> bool:
        return self.decision == "send"

    def to_dict(
        self,
        delta: DecisionDelta,
        policy_context: DeliveryPolicyContext = None,
    ) -> Dict[str, object]:
        policy_context = policy_context or DeliveryPolicyContext()
        values = {
            "version": self.version,
            "decision": self.decision,
            "reason": self.reason,
            "pushValueClass": self.push_value_class,
            **delta.delivery_diagnostics(),
            "deliveryPolicyContext": policy_context.to_dict(),
        }
        if self.suppression_reason:
            values["suppressionReason"] = self.suppression_reason
        if self.delivery_authorization:
            values["deliveryAuthorization"] = self.delivery_authorization
        return values


def _send(reason: str, value_class: str, authorization: str = "") -> DeliveryDecision:
    return DeliveryDecision(
        decision="send",
        reason=reason,
        push_value_class=value_class,
        delivery_authorization=authorization,
    )


def _suppress(reason: str, reason_code: str, value_class: str) -> DeliveryDecision:
    return DeliveryDecision(
        decision="suppress",
        reason=reason,
        push_value_class=value_class,
        suppression_reason=reason_code,
    )


def evaluate_final_decision_delivery(
    delta: DecisionDelta,
    policy_context: DeliveryPolicyContext = None,
) -> DeliveryDecision:
    """Choose push versus web history without inspecting a loose context."""

    policy = policy_context or DeliveryPolicyContext()

    if delta.publication_outcome in {"REVIEW_ONLY", "ABSTAIN", "ABSTAINED"}:
        return _suppress(
            "최종 투자 판단이 아닌 검토 결과는 웹 이력에만 저장합니다.",
            "review_only_web_history",
            "web-only-review",
        )
    if delta.typedb_fallback:
        return _suppress(
            "AI 판단 실패와 TypeDB 대체 결과는 운영·웹 이력에만 저장하고 투자 푸시로 보내지 않습니다.",
            "ai_failure_web_history",
            "web-only-ai-failure",
        )
    if delta.canonical_subject and delta.publication_outcome != "FINAL_DECISION":
        return _suppress(
            "정식 최종 판단 발행물이 없어 웹 검토 이력에만 저장합니다.",
            "missing_final_decision_publication",
            "web-only-incomplete-publication",
        )
    if delta.canonical_subject and (
        delta.execution_status != "completed"
        or delta.ai_adoption_state != "decision-and-narrative-adopted"
        or not delta.ai_authored
    ):
        return _suppress(
            "완료되고 검증된 AI 판단이 최종 발행물에 채택되지 않아 푸시하지 않습니다.",
            "ai_decision_not_adopted",
            "web-only-unadopted-ai",
        )
    if delta.canonical_subject and delta.customer_action_contract_gaps:
        return _suppress(
            "현재 행동·변경 이유·다음 조건 중 일부가 없어 불완전한 투자 알림을 보내지 않습니다.",
            "incomplete_customer_action_contract",
            "web-only-incomplete-message",
        )
    if (
        not delta.validated_response_present
        or not delta.final_action
        or delta.final_action == "NO_ACTION"
    ):
        return _suppress(
            "사용자가 실행·유지할 최종 행동이 검증되지 않아 푸시하지 않습니다.",
            "missing_validated_final_action",
            "web-only-missing-action",
        )
    if policy.first_holding_review_authorized:
        return _send(
            "보유 종목의 첫 최종 AI 판단이 완료되어 현재 행동과 다음 확인 조건을 알립니다.",
            "first-final-holding-decision",
        )
    if policy.explicit_delivery_authorization:
        return _send(
            policy.explicit_delivery_reason or "명시적으로 승인된 판단 변화가 확인됐습니다.",
            policy.explicit_delivery_value_class or "material-user-state-transition",
            policy.explicit_delivery_authorization,
        )
    if delta.has_verified_market_transition and not delta.final_action_changed:
        return _send(
            delta.verified_market_transition_reason
            or "검증된 시장 임계값 전환이 확인됐습니다.",
            "verified-market-threshold-transition",
            "market-transition:" + delta.verified_market_transition_id,
        )
    if (
        policy.cooldown_decision == "scheduled-summary"
        and not delta.final_action_changed
        and not delta.user_state_material
        and not delta.investment_insight_material
        and not delta.has_verified_follow_up
        and not delta.has_material_source_event
    ):
        return _suppress(
            "같은 판단의 정기 재확인은 웹 이력에만 저장합니다.",
            "scheduled_summary_web_history",
            "web-only-scheduled-summary",
        )
    if not delta.history_available:
        if delta.investment_insight_publishable and delta.investment_insight_material:
            return _send(
                "이 종목의 첫 근거 기반 투자 인사이트가 완성됐습니다.",
                "initial-grounded-investment-insight",
            )
        if delta.final_action == "HOLD":
            return _suppress(
                "최초 보유·관찰 상태지만 첫 판단 발송 조건을 충족하지 않아 기준선으로만 저장합니다.",
                "initial_graph_baseline",
                "web-only-initial-baseline",
            )
        return _send("첫 실행 가능한 AI 판단이 확인됐습니다.", "initial-action-decision")
    if (
        delta.final_action_changed
        and delta.graph_transition_present
        and delta.graph_transition_kind == "initial"
        and not delta.graph_transition_material
        and not delta.has_material_source_event
    ):
        return _suppress(
            "최종 행동 후보는 바뀌었지만 그래프의 실질 변화나 새 판단 원문이 없어 기준선 이력에만 기록합니다.",
            "non_material_action_rebaseline",
            "undetermined",
        )
    if policy.state_transition_notifications_enabled and delta.user_state_material:
        return _send(
            "사용자에게 표시되는 최종 판단 상태가 변경됐습니다.",
            "material-user-state-transition",
        )
    if (
        delta.graph_transition_kind == "initial"
        and not delta.graph_transition_material
        and delta.final_action == "HOLD"
        and not delta.user_state_changed
    ):
        return _suppress(
            "이전 최종 판단과 같은 비실행 상태라 다시 알리지 않습니다.",
            "final_ai_state_unchanged",
            "undetermined",
        )
    if (
        delta.user_state_changed
        and not policy.state_transition_notifications_enabled
        and not delta.final_action_changed
        and not delta.has_material_source_event
    ):
        return _suppress(
            "추론 상태 변경 알림 설정이 꺼져 있어 상태 이력만 저장합니다.",
            "inference_state_notification_disabled",
            "undetermined",
        )
    if delta.final_action_changed:
        return _send("최종 AI 행동이 변경됐습니다.", "final-action-change")
    if delta.has_verified_follow_up:
        return _send(
            "직전 판단의 관찰 조건이 거짓에서 참으로 실제 전환됐습니다.",
            "verified-threshold-transition",
        )
    if delta.has_material_source_event:
        return _send(
            "최종 행동은 유지됐지만 판단 변경 원문이 새로 확인됐습니다.",
            "material-source-evidence",
        )
    if delta.investment_insight_publishable and delta.investment_insight_material:
        return _send(
            "투자 방향, 관측 기간, 근거 강도 또는 지배 가설이 달라졌습니다.",
            "material-investment-insight-change",
        )
    if (
        delta.ai_transition_kind == "unchanged"
        and delta.graph_transition_material
        and delta.graph_transition_kind in {"relation-strengthened", "relation-weakened"}
        and not delta.observable_relation_evidence_changed
    ):
        return _suppress(
            "최종 행동과 검증된 가격·수급 조건은 유지됐고, 그래프 세대 교체에 따른 내부 관계 이력만 달라져 웹 판단 이력에만 기록합니다.",
            "internal_relation_lifecycle_churn",
            "web-only-internal-relation-change",
        )
    if (
        delta.user_state_changed
        and not delta.user_state_material
        and not delta.final_action_changed
    ):
        return _suppress(
            "최종 행동은 유지됐고 판단 차단·복구가 아닌 자료 또는 AI 응답 검증 상태만 바뀌어 웹 판단 이력에만 기록합니다.",
            "non_actionable_readiness_change",
            "undetermined",
        )
    if (
        delta.ai_transition_kind == "unchanged"
        and delta.graph_transition_material
        and delta.graph_transition_kind in {
            "action-changed",
            "envelope-changed",
            "readiness-changed",
        }
    ):
        return _suppress(
            "TypeDB 계산 후보만 바뀌고 최종 AI 행동은 "
            + (delta.final_action or "동일")
            + "로 유지됐으며 새 판단 변경 원문이 없어 푸시하지 않습니다.",
            "graph_candidate_only_change",
            "undetermined",
        )
    return _suppress(
        "최종 행동과 검증된 임계값·판단 원문이 모두 유지되어 웹 판단 이력에만 저장합니다.",
        "no_user_action_or_material_evidence_change",
        "web-only-context-change",
    )
