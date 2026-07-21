"""Categorical investment decision state shared by reasoning, AI and delivery.

Raw market measurements keep their numeric values.  This module deliberately
does not turn them into an aggregate investment score or probability.
"""

from typing import Dict, Iterable, List, Tuple


REVIEW_LEVELS = ("normal", "observe", "check", "act", "immediate", "blocked")
DATA_STATES = ("sufficient", "partial", "insufficient", "unavailable")
CHANGE_STATES = (
    "unchanged",
    "new-condition",
    "improving",
    "worsening",
    "direction-changed",
    "new-evidence",
)
EVIDENCE_ROLES = ("risk", "support", "counter", "context", "blocking")
CONFLICT_STATES = ("risk-only", "support-only", "mixed", "context-only")
VALIDATION_STATES = ("ready", "conditional", "blocked")

RETIRED_AGGREGATE_FIELDS = {
    "confidenceImpact",
    "dataQualityScore",
    "dynamicRiskScore",
    "exitPressure",
    "externalSignalCoverageScore",
    "externalSignalQualityScore",
    "externalSignalSourceHealthScore",
    "holdingDecisionScore",
    "investorFlowScore",
    "liquidityRiskScore",
    "notificationScore",
    "opinionImpact",
    "previousRelationScore",
    "relationScore",
    "relationStrength",
    "riskImpact",
    "risk_impact",
    "selectionScore",
    "stagePriority",
    "stage_priority",
    "supportImpact",
    "support_impact",
    "temporalRiskScore",
    "temporalSupportScore",
    "trendDynamicRiskScore",
    "trendScore",
    "valuationInputCoveragePct",
    "valuationReliabilityScore",
}


REVIEW_LEVEL_LABELS = {
    "normal": "평소 관찰",
    "observe": "변화 관찰",
    "check": "조건 확인",
    "act": "대응 준비",
    "immediate": "즉시 재확인",
    "blocked": "판단 보류",
}

DATA_STATE_LABELS = {
    "sufficient": "판단에 필요한 자료 있음",
    "partial": "일부 자료만 있음",
    "insufficient": "핵심 자료 부족",
    "unavailable": "자료 사용 불가",
}

CHANGE_STATE_LABELS = {
    "unchanged": "이전과 같은 상태",
    "new-condition": "새 조건 성립",
    "improving": "이전보다 개선",
    "worsening": "이전보다 악화",
    "direction-changed": "판단 방향 변경",
    "new-evidence": "새 뉴스·공시·근거",
}

EVIDENCE_ROLE_LABELS = {
    "risk": "위험 근거",
    "support": "버티거나 좋아질 근거",
    "counter": "반대 근거",
    "context": "참고 근거",
    "blocking": "판단을 막는 자료 문제",
}

CONFLICT_STATE_LABELS = {
    "risk-only": "위험 근거만 확인",
    "support-only": "버티거나 좋아질 근거만 확인",
    "mixed": "위험과 반대 근거가 함께 있음",
    "context-only": "방향을 정하기 어려운 참고 근거",
}

VALIDATION_STATE_LABELS = {
    "ready": "검증 완료",
    "conditional": "조건부 사용",
    "blocked": "판단 보류",
}

# ``blocked`` describes unavailable judgement, not a stronger investment
# action.  Keep it outside the escalation order so it cannot suppress a
# cooldown or win a merge merely because of its array position.
ACTIONABLE_REVIEW_LEVELS = ("normal", "observe", "check", "act", "immediate")
REVIEW_LEVEL_RANK = {
    **{level: index for index, level in enumerate(ACTIONABLE_REVIEW_LEVELS)},
    "blocked": -1,
}


ACTION_LEVEL_ORDER = ("reference", "watch", "review", "action", "urgent")
ACTION_LEVEL_RANK = {level: index for index, level in enumerate(ACTION_LEVEL_ORDER)}

def _known(value: object, allowed: Iterable[str], fallback: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in set(allowed) else fallback


def without_aggregate_decision_fields(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): without_aggregate_decision_fields(item)
            for key, item in value.items()
            if str(key) not in RETIRED_AGGREGATE_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [without_aggregate_decision_fields(item) for item in value]
    return value


def review_level_for(action_level: object, data_state: object = "sufficient") -> str:
    data = _known(data_state, DATA_STATES, "partial")
    if data in {"unavailable", "insufficient"}:
        return "blocked"
    return {
        "urgent": "immediate",
        "action": "act",
        "review": "check",
        "watch": "observe",
        "reference": "normal",
    }.get(str(action_level or "").strip().lower(), "observe")


def review_level_at_least(value: object, minimum: object) -> bool:
    current = _known(value, REVIEW_LEVELS, "normal")
    required = _known(minimum, REVIEW_LEVELS, "observe")
    if current == "blocked":
        return required == "blocked"
    if required == "blocked":
        return False
    return REVIEW_LEVEL_RANK[current] >= REVIEW_LEVEL_RANK[required]


def data_state_is_usable(value: object) -> bool:
    return _known(value, DATA_STATES, "unavailable") in {"sufficient", "partial"}


def data_state_from_evidence(
    *,
    usable: object = True,
    freshness_status: object = "",
    missing: Iterable[object] = (),
    has_evidence: bool = True,
) -> str:
    freshness = str(freshness_status or "").strip().lower()
    if usable is False or freshness in {"unavailable", "missing", "error"}:
        return "unavailable"
    if not has_evidence:
        return "insufficient"
    missing_rows = [item for item in missing or [] if item not in (None, "", [], {})]
    if freshness == "stale" or missing_rows:
        return "partial"
    return "sufficient"


def evidence_role_from_relation(relation: Dict[str, object]) -> str:
    relation = relation or {}
    if relation.get("evidenceUsableForJudgement") is False:
        return "blocking"
    polarity = str(relation.get("polarity") or "").strip().lower()
    if polarity in {"risk", "support", "counter", "context", "blocking"}:
        return polarity
    group = str(relation.get("actionGroup") or "").strip()
    if group in {
        "lossControl", "entryRisk", "eventRisk", "distributionRisk",
        "factorRisk", "executionRisk", "dataQuality", "temporalRisk",
    }:
        return "risk"
    if group in {"recovery", "eventConfirmation", "flowTrend", "entry", "addBuy"}:
        return "support"
    return "context"


def conflict_state_from_roles(roles: Iterable[object]) -> str:
    values = {str(item or "").strip().lower() for item in roles or []}
    has_risk = bool(values & {"risk", "blocking"})
    has_support = bool(values & {"support", "counter"})
    if has_risk and has_support:
        return "mixed"
    if has_risk:
        return "risk-only"
    if has_support:
        return "support-only"
    return "context-only"


def change_state_from_facts(facts: Dict[str, object], *, has_new_evidence: bool = False) -> str:
    facts = facts or {}
    if has_new_evidence:
        return "new-evidence"
    previous_action = str(facts.get("previousAction") or "").strip().upper()
    current_action = str(facts.get("currentAction") or facts.get("action") or "").strip().upper()
    if previous_action and current_action and previous_action != current_action:
        return "direction-changed"
    try:
        pnl_delta = float(facts.get("profitLossRateDeltaPct") or 0)
    except (TypeError, ValueError):
        pnl_delta = 0.0
    if pnl_delta >= 1.0:
        return "improving"
    if pnl_delta <= -1.0:
        return "worsening"
    if facts.get("isNewCondition") or facts.get("newThresholdState"):
        return "new-condition"
    return "unchanged"


def validation_state_for(
    *,
    graph_backed: bool,
    evidence_count: int,
    has_counter_evidence: bool,
    has_invalidation_condition: bool,
    data_state: object,
) -> str:
    data = _known(data_state, DATA_STATES, "partial")
    if not graph_backed or data in {"unavailable", "insufficient"} or evidence_count <= 0:
        return "blocked"
    if evidence_count < 2 or not has_counter_evidence or not has_invalidation_condition or data == "partial":
        return "conditional"
    return "ready"


def semantic_relation_sort_key(relation: Dict[str, object]) -> Tuple[int, int, str, str]:
    """Sort TypeDB relations without a Python stage-priority policy.

    The relation's TypeDB-owned action level and evidence role provide the
    stable tie-breaker.  ``decisionStage`` is deliberately opaque here: stage
    ordering belongs to the authored TypeDB rule data, not to a hidden Python
    list that can silently override newly added stages.
    """
    relation = relation or {}
    stage = str(relation.get("decisionStage") or "").strip()
    level = str(relation.get("actionLevel") or "reference").strip().lower()
    role = evidence_role_from_relation(relation)
    role_index = {"blocking": 0, "risk": 1, "counter": 2, "support": 3, "context": 4}.get(role, 5)
    return (-ACTION_LEVEL_RANK.get(level, -1), role_index, stage, str(relation.get("ruleId") or ""))


def state_payload(review_level: str, data_state: str, change_state: str, conflict_state: str) -> Dict[str, object]:
    return {
        "reviewLevel": _known(review_level, REVIEW_LEVELS, "observe"),
        "reviewLevelLabel": REVIEW_LEVEL_LABELS.get(review_level, REVIEW_LEVEL_LABELS["observe"]),
        "dataState": _known(data_state, DATA_STATES, "partial"),
        "dataStateLabel": DATA_STATE_LABELS.get(data_state, DATA_STATE_LABELS["partial"]),
        "changeState": _known(change_state, CHANGE_STATES, "unchanged"),
        "changeStateLabel": CHANGE_STATE_LABELS.get(change_state, CHANGE_STATE_LABELS["unchanged"]),
        "conflictState": _known(conflict_state, CONFLICT_STATES, "context-only"),
        "conflictStateLabel": CONFLICT_STATE_LABELS.get(conflict_state, CONFLICT_STATE_LABELS["context-only"]),
    }
