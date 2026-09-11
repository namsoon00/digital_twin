"""Delivery and research ownership for one deterministic reasoning disposition."""

from __future__ import annotations

from typing import Dict, Mapping


ACTIONABLE_DECISION = "ACTIONABLE_DECISION"
HYPOTHESIS_COMPARISON_REQUIRED = "HYPOTHESIS_COMPARISON_REQUIRED"
HYPOTHESIS_QUALIFICATION_PENDING = "HYPOTHESIS_QUALIFICATION_PENDING"
HYPOTHESIS_RESEARCH_ONLY = "HYPOTHESIS_RESEARCH_ONLY"
RULE_COVERAGE_GAP_CANDIDATE = "RULE_COVERAGE_GAP_CANDIDATE"
NO_MATERIAL_PREDICTIVE_RULE_MATCH = "NO_MATERIAL_PREDICTIVE_RULE_MATCH"
WAITING_FOR_SCHEDULED_SOURCE = "WAITING_FOR_SCHEDULED_SOURCE"
DATA_SOURCE_FAILURE = "DATA_SOURCE_FAILURE"
JUDGEMENT_BLOCKED = "JUDGEMENT_BLOCKED"
CONTEXT_OBSERVATION = "CONTEXT_OBSERVATION"

AI_JUDGEMENT_DISPOSITIONS = frozenset({
    ACTIONABLE_DECISION,
    HYPOTHESIS_COMPARISON_REQUIRED,
})

INTERNAL_ONLY_DISPOSITIONS = frozenset({
    HYPOTHESIS_QUALIFICATION_PENDING,
    HYPOTHESIS_RESEARCH_ONLY,
    RULE_COVERAGE_GAP_CANDIDATE,
    NO_MATERIAL_PREDICTIVE_RULE_MATCH,
    WAITING_FOR_SCHEDULED_SOURCE,
    DATA_SOURCE_FAILURE,
    JUDGEMENT_BLOCKED,
})

DISPOSITION_SUPPRESSION = {
    HYPOTHESIS_QUALIFICATION_PENDING: (
        "hypothesis_qualification_pending",
        "가설은 성립했지만 독립적인 사후 관측이 부족합니다. 고객 알림 대신 shadow 성과 관측을 계속합니다.",
        "web-only-hypothesis-qualification",
    ),
    HYPOTHESIS_RESEARCH_ONLY: (
        "hypothesis_research_only",
        "현재 가설은 연구용 상태라 투자 행동 알림에 사용할 수 없습니다.",
        "web-only-research-hypothesis",
    ),
    RULE_COVERAGE_GAP_CANDIDATE: (
        "rule_hypothesis_coverage_gap",
        "규칙은 성립했지만 검증 가능한 가설이 생성되지 않았습니다. 내부 가설 제안·검증 큐에서 보완합니다.",
        "internal-rule-coverage-gap",
    ),
    NO_MATERIAL_PREDICTIVE_RULE_MATCH: (
        "no_material_predictive_rule_match",
        "현재 사실에서 사용자 행동을 바꿀 예측 규칙이 성립하지 않아 알림을 만들지 않습니다.",
        "web-only-no-material-match",
    ),
    WAITING_FOR_SCHEDULED_SOURCE: (
        "waiting_for_scheduled_source",
        "필수 자료의 예정 발표 시각 전입니다. 반복 재판단 없이 다음 수집 시각을 기다립니다.",
        "web-only-scheduled-data-wait",
    ),
    DATA_SOURCE_FAILURE: (
        "reasoning_data_source_failure",
        "필수 원천 데이터 조회가 실패했습니다. 투자 판단 알림과 분리해 공급자 상태에서 처리합니다.",
        "internal-data-source-failure",
    ),
    JUDGEMENT_BLOCKED: (
        "reasoning_judgement_blocked",
        "판단 계약의 필수 조건이 충족되지 않아 투자 판단 알림을 보내지 않습니다.",
        "web-only-judgement-blocked",
    ),
}


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def decision_synthesis_payload(context: Mapping[str, object]) -> Dict[str, object]:
    """Read the immutable synthesis from either the canonical or legacy envelope."""

    values = _mapping(context)
    synthesis = _mapping(values.get("v2DecisionSynthesis"))
    if synthesis:
        return synthesis
    return _mapping(_mapping(values.get("metadata")).get("v2DecisionSynthesis"))


def disposition_code_from_context(context: Mapping[str, object]) -> str:
    """Return an explicit code, with a narrow legacy inference for old records."""

    synthesis = decision_synthesis_payload(context)
    if not synthesis:
        return ""
    explicit = str(
        synthesis.get("disposition_code")
        or synthesis.get("dispositionCode")
        or ""
    ).upper().strip()
    if explicit:
        return explicit

    execution_ids = synthesis.get("execution_eligible_hypothesis_ids") or synthesis.get(
        "executionEligibleHypothesisIds"
    ) or []
    eligible_ids = synthesis.get("eligible_hypothesis_ids") or synthesis.get(
        "eligibleHypothesisIds"
    ) or []
    action_state = str(
        synthesis.get("action_state") or synthesis.get("actionState") or ""
    ).upper().strip()
    hypothesis_state = str(
        synthesis.get("hypothesis_state") or synthesis.get("hypothesisState") or ""
    ).upper().strip()
    if action_state == "COMPARISON_REQUIRED":
        return HYPOTHESIS_COMPARISON_REQUIRED
    if execution_ids:
        return ACTIONABLE_DECISION
    if eligible_ids:
        return HYPOTHESIS_QUALIFICATION_PENDING
    if hypothesis_state == "NO_ELIGIBLE_THESIS":
        return NO_MATERIAL_PREDICTIVE_RULE_MATCH
    return ""


def reasoning_disposition_delivery(context: Mapping[str, object]) -> Dict[str, object]:
    """Resolve whether this result belongs at the customer-facing AI boundary."""

    code = disposition_code_from_context(context)
    base = {
        "version": "reasoning-disposition-delivery-v1",
        "dispositionCode": code,
        "decision": "proceed",
    }
    if not code:
        return {**base, "reason": "구조화된 처분 코드가 없는 이전 기록입니다."}
    if code in AI_JUDGEMENT_DISPOSITIONS:
        return {
            **base,
            "reason": "행동 후보 또는 경쟁 가설 비교가 준비되어 AI 판단 경계로 전달합니다.",
            "pushValueClass": "reasoning-action-candidate",
        }
    if code == CONTEXT_OBSERVATION:
        return {
            **base,
            "reason": "행동 판단과 분리된 관찰 계약은 전용 발송 정책에서 평가합니다.",
            "pushValueClass": "context-observation-candidate",
        }
    suppression_reason, reason, value_class = DISPOSITION_SUPPRESSION.get(
        code,
        (
            "unknown_reasoning_disposition",
            "알 수 없는 추론 처분은 고객 알림으로 보내지 않습니다.",
            "web-only-unknown-disposition",
        ),
    )
    return {
        **base,
        "decision": "suppress",
        "suppressionReason": suppression_reason,
        "reason": reason,
        "pushValueClass": value_class,
    }


def reasoning_disposition_requires_ai(context: Mapping[str, object]) -> bool:
    return disposition_code_from_context(context) in AI_JUDGEMENT_DISPOSITIONS
