"""Fail-closed contract for customer-facing investment decisions.

TypeDB may surface a hypothesis for comparison before its observed outcomes
qualify it to originate an executable action.  The AI may also return fluent
prose without a usable action plan.  This module keeps those two failure modes
out of final publications and push notifications.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Mapping

from digital_twin.modules.decisions.domain.decision_evidence_contract import hypothesis_decision_eligibility


INVESTMENT_DECISION_ACTIONABILITY_VERSION = "investment-decision-actionability-v2"
PERSISTED_DECISION_AUTHORIZATION_VERSION = "persisted-decision-authorization-v1"
EXECUTABLE_ACTIONS = {"BUY", "ADD", "TRIM", "SELL"}
KNOWN_ACTIONS = {*EXECUTABLE_ACTIONS, "HOLD", "AVOID", "WATCH"}

_INTERNAL_TEXT = re.compile(
    r"(?:graph\.|HAS_[A-Z_]+|MATCHES_[A-Z_]+|BLOCKS_[A-Z_]+|"
    r"typedb|rulebox|inferencebox|온톨로지|가설|추론\s*세대|관계\s*수명|"
    r"watchlistontologysignal|"
    r"(?:hypothesis|inference|evidence|trace)[-_:][a-z0-9_.:-]+)",
    re.IGNORECASE,
)
_OBSERVABLE_TERMS = (
    "가격", "현재가", "종가", "거래량", "체결강도", "외국인", "기관", "수급",
    "매출", "영업이익", "순이익", "현금흐름", "부채", "실적", "공시", "발표",
    "금리", "환율", "물가", "고용", "정규장", "장 시작", "장 마감", "다음 분기",
    "가이던스", "전망", "주식 수", "배당", "자사주", "유상증자", "인도량",
    "판매량", "점유율", "수주", "재고", "마진",
)
_CONDITION_BOUNDARY_TERMS = (
    "이상", "이하", "초과", "미만", "상회", "하회", "돌파", "이탈", "도달",
    "순매수", "순매도", "매수 우위", "매도 우위", "상향", "하향", "인상", "인하",
    "동결", "흑자", "적자", "전환", "승인", "부결", "중단", "재개", "확정", "철회",
    "위로", "아래로", "위를", "아래를", "위에", "아래에",
)


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _value(response: object, camel: str, snake: str = "") -> object:
    if isinstance(response, Mapping):
        return response.get(camel) if camel in response else response.get(snake or camel)
    return getattr(response, snake or camel, None)


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _items(value: object) -> List[object]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value] if value not in (None, "") else []


def _unique(values: Iterable[object]) -> List[str]:
    rows: List[str] = []
    for value in values or []:
        text = _text(value)
        if text and text not in rows:
            rows.append(text)
    return rows


def _hypothesis_set(context: Mapping[str, object]) -> Dict[str, object]:
    values = _mapping(context)
    prepared = _mapping(values.get("_notificationAiPreparedDecisionCore"))
    prepared_set = _mapping(prepared.get("hypothesisSet"))
    if prepared_set:
        return prepared_set
    direct = _mapping(values.get("hypothesisSet") or values.get("hypothesis_set"))
    if direct:
        return direct
    relation = _mapping(values.get("ontologyRelationContext"))
    brain = _mapping(relation.get("investmentBrain"))
    return _mapping(relation.get("hypothesisSet")) or _mapping(brain.get("hypothesisSet"))


def _selected_hypothesis(context: Mapping[str, object], response: object) -> Dict[str, object]:
    selected_id = _text(_value(response, "selectedHypothesisId", "selected_hypothesis_id"))
    if not selected_id:
        return {}
    selected = next((
        _mapping(item)
        for item in _hypothesis_set(context).get("hypotheses") or []
        if isinstance(item, Mapping)
        and _text(item.get("hypothesisId") or item.get("hypothesis_id")) == selected_id
    ), {})
    if selected:
        return selected
    return next((
        _mapping(item)
        for item in _items(_value(response, "hypotheses"))
        if isinstance(item, Mapping)
        and _text(item.get("hypothesisId") or item.get("hypothesis_id")) == selected_id
    ), {})


def _verified_claim_sections(response: object) -> set:
    validation = _mapping(_value(response, "claimValidation", "claim_validation"))
    verified_ids = {
        _text(item.get("claimId"))
        for item in validation.get("validations") or []
        if isinstance(item, Mapping) and _text(item.get("status")).lower() == "verified"
    }
    sections = set()
    for item in _items(_value(response, "narrativeClaims", "narrative_claims")):
        if not isinstance(item, Mapping):
            continue
        claim_id = _text(item.get("claimId"))
        if verified_ids and claim_id not in verified_ids:
            continue
        if _items(item.get("evidenceIds")) and _text(item.get("text")):
            sections.add(_text(item.get("section")).lower())
    return sections


def _supported_causal_path(response: object, selected: Mapping[str, object]) -> bool:
    known_ids = {
        _text(value)
        for value in _items(
            selected.get("supportingEvidenceIds")
            or selected.get("supporting_evidence_ids")
        )
        if _text(value)
    }
    for item in _items(_value(response, "causalChain", "causal_chain")):
        if not isinstance(item, Mapping) or _text(item.get("status")).lower() != "supported":
            continue
        evidence_ids = {_text(value) for value in _items(item.get("evidenceIds")) if _text(value)}
        if evidence_ids and (not known_ids or evidence_ids.intersection(known_ids)):
            return True
    return False


def _usable_text(value: object, minimum: int = 8) -> bool:
    text = _text(value)
    return bool(len(text) >= minimum and not _INTERNAL_TEXT.search(text))


def _action_plan_matches(action: str, value: object) -> bool:
    text = _text(value)
    if not _usable_text(text):
        return False
    positive_terms = {
        "BUY": ("매수", "진입"),
        "ADD": ("추가매수", "추가 매수", "비중을 늘"),
        "HOLD": ("보유", "유지", "주문하지", "매수하지", "매도하지"),
        "TRIM": ("축소", "줄", "일부 매도"),
        "SELL": ("매도", "정리", "청산"),
        "AVOID": ("피", "보류", "대기", "진입하지", "매수하지"),
        "WATCH": ("관찰", "관심", "확인"),
    }
    return any(term in text for term in positive_terms.get(action, ()))


def is_concrete_observable_condition(value: object) -> bool:
    """Return whether prose defines a reproducible decision boundary.

    Mentioning a metric or saying that a relation should be observed is not a
    condition.  Free text needs both a customer-observable metric and an
    explicit threshold or state transition.  Structured follow-up conditions
    remain the preferred representation.
    """

    text = _text(value)
    if not _usable_text(text):
        return False
    if not any(term in text for term in _OBSERVABLE_TERMS):
        return False
    return any(term in text for term in _CONDITION_BOUNDARY_TERMS)


def _observable_next_condition(response: object) -> bool:
    for item in _items(_value(response, "followUpConditions", "follow_up_conditions")):
        if not isinstance(item, Mapping):
            continue
        if (
            _text(item.get("field"))
            and _text(item.get("operator"))
            and item.get("threshold") not in (None, "")
        ):
            return True
    candidates = [
        _value(response, "nextActionPlan", "next_action_plan"),
        _value(response, "invalidationCondition", "invalidation_condition"),
        *_items(_value(response, "nextChecks", "next_checks")),
    ]
    for value in candidates:
        if is_concrete_observable_condition(value):
            return True
    return False


def investment_decision_actionability(
    context: Mapping[str, object],
    response: object,
) -> Dict[str, object]:
    """Evaluate whether one AI result is complete enough to publish or push."""

    action = _text(_value(response, "action")).upper()
    executable = action in EXECUTABLE_ACTIONS
    selected = _selected_hypothesis(context, response)
    hypothesis_assessment = hypothesis_decision_eligibility(selected) if selected else {}
    if selected and "executionEligible" in selected:
        hypothesis_assessment["executionEligible"] = bool(selected.get("executionEligible"))
        hypothesis_assessment["decisionUse"] = _text(selected.get("decisionUse")) or (
            "execution" if selected.get("executionEligible") else "research-only"
        )
        hypothesis_assessment["outcomeQualificationStatus"] = _text(
            selected.get("outcomeQualificationStatus")
        ) or _text(_mapping(selected.get("qualification")).get("status")) or "not-recorded"
    gaps: List[str] = []
    current_plan = _value(response, "currentActionPlan", "current_action_plan") or _value(
        response, "executionDecision", "execution_decision"
    )
    why_now_values = [
        _value(response, "changeAnalysis", "change_analysis"),
        _value(response, "investmentView", "investment_view"),
        _value(response, "summary"),
        *_items(_value(response, "evidence")),
    ]
    if action not in KNOWN_ACTIONS:
        gaps.append("final-action")
    if not _usable_text(current_plan):
        gaps.append("current-action-plan")
    elif action in KNOWN_ACTIONS and not _action_plan_matches(action, current_plan):
        gaps.append("action-plan-consistency")
    if not any(_usable_text(item) for item in why_now_values):
        gaps.append("why-now")
    if not _observable_next_condition(response):
        gaps.append("observable-next-condition")

    verified_sections = _verified_claim_sections(response)
    if executable:
        readiness = _text(_value(response, "decisionReadiness", "decision_readiness")).lower()
        assurance = _mapping(_value(response, "decisionAssurance", "decision_assurance"))
        if readiness != "ready":
            gaps.append("decision-readiness")
        if _text(assurance.get("executionEligibility")).lower() != "eligible":
            gaps.append("execution-eligibility")
        if not selected:
            gaps.append("selected-hypothesis")
        elif not bool(hypothesis_assessment.get("executionEligible")):
            gaps.append("hypothesis-execution-qualification")
        if not _supported_causal_path(response, selected):
            gaps.append("supported-causal-path")
        if verified_sections and "support" not in verified_sections:
            gaps.append("verified-support-explanation")
        if not is_concrete_observable_condition(
            _value(response, "invalidationCondition", "invalidation_condition")
        ):
            gaps.append("invalidation-condition")

    gaps = _unique(gaps)
    stages = {
        "evidence": {
            "status": (
                "passed"
                if not any(item in gaps for item in {"why-now", "verified-support-explanation"})
                else "failed"
            ),
            "gaps": [item for item in gaps if item in {"why-now", "verified-support-explanation"}],
        },
        "hypothesis": {
            "status": (
                "passed"
                if not executable
                or not any(item in gaps for item in {"selected-hypothesis", "hypothesis-execution-qualification"})
                else "failed"
            ),
            "gaps": [
                item for item in gaps
                if item in {"selected-hypothesis", "hypothesis-execution-qualification"}
            ],
        },
        "causal": {
            "status": "passed" if not executable or "supported-causal-path" not in gaps else "failed",
            "gaps": [item for item in gaps if item == "supported-causal-path"],
        },
        "action": {
            "status": (
                "passed"
                if not any(item in gaps for item in {
                    "final-action", "current-action-plan", "action-plan-consistency",
                    "decision-readiness", "execution-eligibility",
                })
                else "failed"
            ),
            "gaps": [
                item for item in gaps
                if item in {
                    "final-action", "current-action-plan", "action-plan-consistency",
                    "decision-readiness", "execution-eligibility",
                }
            ],
        },
        "followUp": {
            "status": (
                "passed"
                if not any(item in gaps for item in {"observable-next-condition", "invalidation-condition"})
                else "failed"
            ),
            "gaps": [
                item for item in gaps
                if item in {"observable-next-condition", "invalidation-condition"}
            ],
        },
    }
    return {
        "version": INVESTMENT_DECISION_ACTIONABILITY_VERSION,
        "status": (
            "actionable"
            if executable and not gaps
            else "decision-complete"
            if not executable and not gaps
            else "review-only"
        ),
        "publishable": not gaps,
        "executionEligible": executable and not gaps,
        "action": action,
        "gaps": gaps,
        "selectedHypothesisId": _text(
            _value(response, "selectedHypothesisId", "selected_hypothesis_id")
        ),
        "hypothesisDecisionUse": hypothesis_assessment.get("decisionUse") or "not-evaluated",
        "hypothesisQualification": hypothesis_assessment.get("outcomeQualificationStatus") or "not-evaluated",
        "verifiedClaimSections": sorted(verified_sections),
        "supportedCausalPath": _supported_causal_path(response, selected),
        "observableNextCondition": _observable_next_condition(response),
        "stages": stages,
    }


def persisted_decision_authorization(value: object) -> Dict[str, object]:
    """Revalidate a stored executable opinion before exposing it as current.

    The immutable episode remains available for audit.  A stale or legacy
    executable opinion is projected as ``NO_ACTION`` when its full evidence,
    causal, action, and follow-up contract cannot be reproduced.
    """

    if hasattr(value, "to_dict") and callable(value.to_dict):
        payload = _mapping(value.to_dict())
    else:
        payload = _mapping(value)
    action = _text(payload.get("action")).upper()
    if action not in EXECUTABLE_ACTIONS:
        return {
            "version": PERSISTED_DECISION_AUTHORIZATION_VERSION,
            "status": "not-required",
            "state": "pass",
            "authorized": True,
            "recordedAction": action,
            "effectiveAction": action or "NO_ACTION",
            "gaps": [],
            "detail": "저장된 비실행 판단입니다.",
        }

    recomputed = investment_decision_actionability(payload, payload)
    stored = _mapping(
        payload.get("decisionActionability") or payload.get("decision_actionability")
    )
    assurance = _mapping(
        payload.get("decisionAssurance") or payload.get("decision_assurance")
    )
    gaps = list(recomputed.get("gaps") or [])
    if _text(stored.get("status")).lower() != "actionable":
        gaps.append("persisted-actionability-status")
    if stored.get("publishable") is not True:
        gaps.append("persisted-publication-authorization")
    if stored.get("executionEligible") is not True:
        gaps.append("persisted-execution-authorization")
    if _text(assurance.get("executionEligibility")).lower() != "eligible":
        gaps.append("persisted-assurance-authorization")
    if _text(recomputed.get("hypothesisDecisionUse")).lower() != "execution":
        gaps.append("persisted-hypothesis-qualification")
    gaps = _unique(gaps)
    authorized = not gaps
    label = {
        "BUY": "매수",
        "ADD": "추가매수",
        "TRIM": "분할축소",
        "SELL": "매도",
    }[action]
    qualification_gap = any(
        item in {
            "hypothesis-execution-qualification",
            "execution-eligibility",
            "persisted-hypothesis-qualification",
            "persisted-execution-authorization",
            "persisted-assurance-authorization",
        }
        for item in gaps
    )
    detail = (
        "선택한 설명의 독립된 사후 검증이 충분하지 않아 저장된 "
        + label
        + " 의견을 현재 실행 판단으로 사용할 수 없습니다."
        if qualification_gap
        else "행동 근거와 취소 조건을 다시 검증할 수 없어 저장된 "
        + label
        + " 의견을 현재 실행 판단으로 사용할 수 없습니다."
    )
    if authorized:
        detail = "근거, 실행 자격, 현재 행동과 취소 조건을 다시 확인했습니다."
    return {
        "version": PERSISTED_DECISION_AUTHORIZATION_VERSION,
        "status": "authorized" if authorized else "blocked",
        "state": "pass" if authorized else "blocked",
        "authorized": authorized,
        "recordedAction": action,
        "effectiveAction": action if authorized else "NO_ACTION",
        "gaps": gaps,
        "detail": detail,
        "recomputed": recomputed,
    }
