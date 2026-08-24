"""One governed claim contract for every executable RuleBox rule.

Not every rule is a market prediction. Policy, execution, data-quality and
context rules still need an explicit proposition that explains what the rule
is allowed to assert and how that assertion is validated. Predictive claims
add a pre-registered outcome contract and an automatic, reproducible
qualification policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping

from .hypothesis_catalog import hypothesis_family_definition
from .hypothesis_outcome_contract import (
    HypothesisOutcomeContract,
    HypothesisOutcomeCriterion,
)
from .ontology_rule_knowledge import RuleKnowledgeBasis, resolved_rule_knowledge_basis


RULE_CLAIM_CONTRACT_VERSION = "rule-claim-contract-v1"
HYPOTHESIS_QUALIFICATION_POLICY_VERSION = "hypothesis-auto-qualification-v1"

CLAIM_TYPES = frozenset({
    "market-hypothesis",
    "risk-invariant",
    "execution-feasibility",
    "data-reliability",
    "causal-context",
})

RULE_KIND_TO_CLAIM_TYPE = {
    "predictive-hypothesis": "market-hypothesis",
    "policy-constraint": "risk-invariant",
    "execution-gate": "execution-feasibility",
    "data-quality-gate": "data-reliability",
    "context-observation": "causal-context",
}

CLAIM_VALIDATION_MODES = {
    "market-hypothesis": "forward-outcome-observation",
    "risk-invariant": "policy-invariant-evaluation",
    "execution-feasibility": "execution-observation-evaluation",
    "data-reliability": "provenance-freshness-evaluation",
    "causal-context": "current-generation-consistency",
}

CLAIM_DECISION_AUTHORITIES = {
    "market-hypothesis": "conditional-investment-evidence",
    "risk-invariant": "guardrail-only",
    "execution-feasibility": "guardrail-only",
    "data-reliability": "guardrail-only",
    "causal-context": "reference-only",
}


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _values(value: object) -> List[object]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [] if value in (None, "") else [value]


def _unique(values: Iterable[object], limit: int = 32) -> List[str]:
    rows: List[str] = []
    for value in values or []:
        item = _text(value)
        if item and item not in rows:
            rows.append(item)
        if len(rows) >= limit:
            break
    return rows


def _number(value: object, fallback: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else fallback)
    except (TypeError, ValueError):
        return fallback


def _integer(value: object, fallback: int = 0) -> int:
    try:
        return int(float(value if value not in (None, "") else fallback))
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True)
class HypothesisQualificationPolicy:
    """Pre-registered thresholds for deriving a hypothesis qualification."""

    observation_floor: int = 3
    limited_active_floor: int = 5
    active_floor: int = 12
    limited_min_hit_rate: float = 0.55
    active_min_hit_rate: float = 0.60
    active_min_lower_confidence: float = 0.40
    quarantine_floor: int = 5
    quarantine_max_hit_rate: float = 0.35
    quarantine_max_upper_confidence: float = 0.50
    require_non_negative_action_return: bool = True
    automatic_promotion: bool = True
    automatic_quarantine: bool = True
    version: str = HYPOTHESIS_QUALIFICATION_POLICY_VERSION

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": self.version or HYPOTHESIS_QUALIFICATION_POLICY_VERSION,
            "observationFloor": int(self.observation_floor),
            "limitedActiveFloor": int(self.limited_active_floor),
            "activeFloor": int(self.active_floor),
            "limitedMinHitRate": float(self.limited_min_hit_rate),
            "activeMinHitRate": float(self.active_min_hit_rate),
            "activeMinLowerConfidence": float(self.active_min_lower_confidence),
            "quarantineFloor": int(self.quarantine_floor),
            "quarantineMaxHitRate": float(self.quarantine_max_hit_rate),
            "quarantineMaxUpperConfidence": float(self.quarantine_max_upper_confidence),
            "requireNonNegativeActionReturn": bool(self.require_non_negative_action_return),
            "automaticPromotion": bool(self.automatic_promotion),
            "automaticQuarantine": bool(self.automatic_quarantine),
        }

    @staticmethod
    def from_dict(payload: Mapping[str, object] = None):
        item = dict(payload or {}) if isinstance(payload, Mapping) else {}
        return HypothesisQualificationPolicy(
            observation_floor=max(1, _integer(item.get("observationFloor") or item.get("observation_floor"), 3)),
            limited_active_floor=max(1, _integer(item.get("limitedActiveFloor") or item.get("limited_active_floor"), 5)),
            active_floor=max(1, _integer(item.get("activeFloor") or item.get("active_floor"), 12)),
            limited_min_hit_rate=max(0.0, min(1.0, _number(item.get("limitedMinHitRate") or item.get("limited_min_hit_rate"), 0.55))),
            active_min_hit_rate=max(0.0, min(1.0, _number(item.get("activeMinHitRate") or item.get("active_min_hit_rate"), 0.60))),
            active_min_lower_confidence=max(0.0, min(1.0, _number(item.get("activeMinLowerConfidence") or item.get("active_min_lower_confidence"), 0.40))),
            quarantine_floor=max(1, _integer(item.get("quarantineFloor") or item.get("quarantine_floor"), 5)),
            quarantine_max_hit_rate=max(0.0, min(1.0, _number(item.get("quarantineMaxHitRate") or item.get("quarantine_max_hit_rate"), 0.35))),
            quarantine_max_upper_confidence=max(0.0, min(1.0, _number(item.get("quarantineMaxUpperConfidence") or item.get("quarantine_max_upper_confidence"), 0.50))),
            require_non_negative_action_return=bool(item.get("requireNonNegativeActionReturn", item.get("require_non_negative_action_return", True))),
            automatic_promotion=bool(item.get("automaticPromotion", item.get("automatic_promotion", True))),
            automatic_quarantine=bool(item.get("automaticQuarantine", item.get("automatic_quarantine", True))),
            version=_text(item.get("version")) or HYPOTHESIS_QUALIFICATION_POLICY_VERSION,
        )


@dataclass(frozen=True)
class RuleClaimContract:
    claim_contract_id: str = ""
    rule_id: str = ""
    claim_type: str = ""
    statement: str = ""
    theory_family: str = ""
    thesis_family: str = ""
    prediction_target: str = ""
    expected_direction: str = ""
    expected_outcome: str = ""
    default_horizon: str = ""
    outcome_metric: str = ""
    falsification_contract: str = ""
    required_evidence_domains: List[str] = field(default_factory=list)
    evidence_independence_key: str = ""
    validation_mode: str = ""
    decision_authority: str = ""
    catalog_status: str = "approved"
    qualification_policy: HypothesisQualificationPolicy = field(default_factory=HypothesisQualificationPolicy)
    outcome_contract: HypothesisOutcomeContract = field(default_factory=HypothesisOutcomeContract)
    source: str = "rulebox-governed-contract"
    version: str = RULE_CLAIM_CONTRACT_VERSION

    @property
    def is_predictive(self) -> bool:
        return self.claim_type == "market-hypothesis"

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": self.version or RULE_CLAIM_CONTRACT_VERSION,
            "claimContractId": self.claim_contract_id,
            "ruleId": self.rule_id,
            "claimType": self.claim_type,
            "statement": self.statement,
            "theoryFamily": self.theory_family,
            "thesisFamily": self.thesis_family,
            "predictionTarget": self.prediction_target,
            "expectedDirection": self.expected_direction,
            "expectedOutcome": self.expected_outcome,
            "defaultHorizon": self.default_horizon,
            "outcomeMetric": self.outcome_metric,
            "falsificationContract": self.falsification_contract,
            "requiredEvidenceDomains": list(self.required_evidence_domains or []),
            "evidenceIndependenceKey": self.evidence_independence_key,
            "validationMode": self.validation_mode,
            "decisionAuthority": self.decision_authority,
            "catalogStatus": self.catalog_status,
            "qualificationPolicy": self.qualification_policy.to_dict() if self.is_predictive else {},
            "outcomeContract": self.outcome_contract.to_dict(),
            "source": self.source,
        }

    @staticmethod
    def from_dict(payload: Mapping[str, object] = None):
        item = dict(payload or {}) if isinstance(payload, Mapping) else {}
        return RuleClaimContract(
            claim_contract_id=_text(item.get("claimContractId") or item.get("claim_contract_id")),
            rule_id=_text(item.get("ruleId") or item.get("rule_id")),
            claim_type=_text(item.get("claimType") or item.get("claim_type")),
            statement=_text(item.get("statement")),
            theory_family=_text(item.get("theoryFamily") or item.get("theory_family")),
            thesis_family=_text(item.get("thesisFamily") or item.get("thesis_family")),
            prediction_target=_text(item.get("predictionTarget") or item.get("prediction_target")),
            expected_direction=_text(item.get("expectedDirection") or item.get("expected_direction")),
            expected_outcome=_text(item.get("expectedOutcome") or item.get("expected_outcome")),
            default_horizon=_text(item.get("defaultHorizon") or item.get("default_horizon")),
            outcome_metric=_text(item.get("outcomeMetric") or item.get("outcome_metric")),
            falsification_contract=_text(item.get("falsificationContract") or item.get("falsification_contract")),
            required_evidence_domains=_unique(_values(item.get("requiredEvidenceDomains") or item.get("required_evidence_domains"))),
            evidence_independence_key=_text(item.get("evidenceIndependenceKey") or item.get("evidence_independence_key")),
            validation_mode=_text(item.get("validationMode") or item.get("validation_mode")),
            decision_authority=_text(item.get("decisionAuthority") or item.get("decision_authority")),
            catalog_status=_text(item.get("catalogStatus") or item.get("catalog_status")) or "approved",
            qualification_policy=HypothesisQualificationPolicy.from_dict(item.get("qualificationPolicy") or item.get("qualification_policy")),
            outcome_contract=HypothesisOutcomeContract.from_dict(item.get("outcomeContract") or item.get("outcome_contract")),
            source=_text(item.get("source")) or "rulebox-governed-contract",
            version=_text(item.get("version")) or RULE_CLAIM_CONTRACT_VERSION,
        )


FAMILY_OUTCOME_PROFILES = {
    "trend-continuation": ([1440, 10080], 0.75, ("quote", "trend")),
    "trend-break": ([1440, 10080], 0.75, ("quote", "trend")),
    "mean-reversion": ([60, 1440], 0.50, ("quote", "trend")),
    "failed-recovery": ([60, 1440], 0.50, ("quote", "trend")),
    "flow-accumulation": ([60, 1440], 0.50, ("quote", "flow")),
    "flow-distribution": ([60, 1440], 0.50, ("quote", "flow")),
    "fundamental-rerating": ([10080, 43200], 1.50, ("quote", "research")),
    "fundamental-deterioration": ([10080, 43200], 1.50, ("quote", "research")),
    "event-support": ([60, 1440], 0.75, ("quote", "research")),
    "event-risk": ([60, 1440], 0.75, ("quote", "research")),
    "cross-asset-support": ([1440, 10080], 0.75, ("quote", "trend")),
    "cross-asset-risk": ([1440, 10080], 0.75, ("quote", "trend")),
    "thesis-support": ([1440, 10080, 43200], 1.00, ("quote", "research")),
    "thesis-risk": ([1440, 10080, 43200], 1.00, ("quote", "research")),
}


def predictive_outcome_contract(thesis_family: str, direction: str) -> HypothesisOutcomeContract:
    horizons, material_move, domains = FAMILY_OUTCOME_PROFILES.get(
        thesis_family,
        ([1440, 10080], 0.75, ("quote", "trend")),
    )
    risk = _text(direction).lower() == "risk"
    primary_horizon = int(horizons[0])
    criteria = [
        HypothesisOutcomeCriterion(
            criterion_id=thesis_family + ":expected-direction",
            label="사전 정의한 방향의 유의한 가격 반응",
            role="result",
            metric="instrumentReturnPct",
            operator="<=" if risk else ">=",
            threshold=-abs(material_move) if risk else abs(material_move),
            horizon_minutes=primary_horizon,
            required=True,
            required_observation_domains=["quote"],
            source_policy=["point-in-time-market-observation"],
            failure_outcome="inconclusive",
        ),
        HypothesisOutcomeCriterion(
            criterion_id=thesis_family + ":opposite-direction",
            label="가설과 반대인 유의한 가격 반응",
            role="invalidation",
            metric="instrumentReturnPct",
            operator=">=" if risk else "<=",
            threshold=abs(material_move) if risk else -abs(material_move),
            horizon_minutes=primary_horizon,
            required=True,
            required_observation_domains=["quote"],
            source_policy=["point-in-time-market-observation"],
        ),
    ]
    return HypothesisOutcomeContract(
        outcome_horizon_minutes=list(horizons),
        # The causal evidence domains are frozen at decision time. A later
        # result only requires a point-in-time quote so outcome collection is
        # not blocked when flow or research providers do not publish again at
        # the exact review horizon.
        required_observation_domains=["quote"],
        minimum_independent_episodes=5,
        maximum_observation_delay_minutes=180,
        verification_focus=[
            "independent-decision-episodes",
            "point-in-time-source-alignment",
            "corroboration-versus-contradiction",
            "decision-evidence-domains:" + ",".join(domains),
        ],
        evaluation_scope="market-and-account-separated",
        criteria=criteria,
    )


def authored_outcome_contract_complete(contract: HypothesisOutcomeContract) -> bool:
    """Validate the RuleBox template before episode-specific IDs are frozen."""

    return bool(
        contract.outcome_horizon_minutes
        and contract.required_observation_domains
        and int(contract.minimum_independent_episodes or 0) > 0
        and int(contract.maximum_observation_delay_minutes or 0) > 0
        and any(
            criterion.required and criterion.role in {"result", "invalidation"}
            for criterion in contract.criteria or []
        )
    )


def _rule_id(rule: object) -> str:
    if isinstance(rule, Mapping):
        return _text(rule.get("rule_id") or rule.get("ruleId") or rule.get("id"))
    return _text(getattr(rule, "rule_id", ""))


def _rule_label(rule: object) -> str:
    if isinstance(rule, Mapping):
        return _text(rule.get("label") or _rule_id(rule))
    return _text(getattr(rule, "label", "") or _rule_id(rule))


def resolved_rule_claim_contract(
    rule: object,
    knowledge_basis: RuleKnowledgeBasis = None,
) -> RuleClaimContract:
    basis = knowledge_basis or resolved_rule_knowledge_basis(rule)
    raw = rule.get("claim_contract") or rule.get("claimContract") if isinstance(rule, Mapping) else getattr(rule, "claim_contract", None)
    explicit = raw if isinstance(raw, RuleClaimContract) else RuleClaimContract.from_dict(raw)
    rule_id = _rule_id(rule)
    expected_type = RULE_KIND_TO_CLAIM_TYPE.get(basis.rule_kind, "")
    if (
        explicit.claim_contract_id
        and explicit.rule_id == rule_id
        and explicit.claim_type == expected_type
        and explicit.version == RULE_CLAIM_CONTRACT_VERSION
    ):
        return explicit

    family = hypothesis_family_definition(basis.thesis_family)
    direction = family.expected_direction if family else "context"
    predictive = expected_type == "market-hypothesis"
    outcome_contract = (
        predictive_outcome_contract(basis.thesis_family, direction)
        if predictive
        else HypothesisOutcomeContract()
    )
    statement = basis.plain_language_basis or _rule_label(rule)
    if predictive and family:
        statement = _rule_label(rule) + ": " + family.expected_outcome
    return RuleClaimContract(
        claim_contract_id="rule-claim:" + rule_id,
        rule_id=rule_id,
        claim_type=expected_type,
        statement=statement,
        theory_family=basis.theory_family,
        thesis_family=basis.thesis_family,
        prediction_target=family.prediction_target if family else expected_type,
        expected_direction=direction,
        expected_outcome=family.expected_outcome if family else statement,
        default_horizon=family.default_horizon if family else "current-generation",
        outcome_metric=family.outcome_metric if family else CLAIM_VALIDATION_MODES.get(expected_type, "contract-evaluation"),
        falsification_contract=family.falsification_contract if family else "정의된 계약 조건이 성립하지 않으면 주장을 사용하지 않습니다.",
        required_evidence_domains=list(family.required_evidence_domains) if family else list(basis.applicability or []),
        evidence_independence_key=basis.evidence_independence_key or basis.thesis_family,
        validation_mode=CLAIM_VALIDATION_MODES.get(expected_type, "contract-evaluation"),
        decision_authority=CLAIM_DECISION_AUTHORITIES.get(expected_type, "reference-only"),
        catalog_status="approved",
        qualification_policy=HypothesisQualificationPolicy(),
        outcome_contract=outcome_contract,
        source="rulebox-governed-contract",
    )


def rule_claim_contract_violations(contract: RuleClaimContract, rule_id: str = "") -> List[str]:
    prefix = (_text(rule_id) or contract.rule_id or "<unknown-rule>") + ": "
    issues: List[str] = []
    if contract.claim_type not in CLAIM_TYPES:
        issues.append(prefix + "claim contract has invalid claim_type")
    if not contract.claim_contract_id or contract.rule_id != _text(rule_id or contract.rule_id):
        issues.append(prefix + "claim contract identity is incomplete")
    if not contract.statement:
        issues.append(prefix + "claim contract requires statement")
    if not contract.validation_mode or not contract.decision_authority:
        issues.append(prefix + "claim contract requires validation and authority boundaries")
    if not contract.theory_family or not contract.thesis_family:
        issues.append(prefix + "claim contract requires theory and thesis families")
    if contract.is_predictive:
        if not authored_outcome_contract_complete(contract.outcome_contract):
            issues.append(prefix + "predictive claim requires a complete outcome contract")
        if not contract.prediction_target or not contract.expected_outcome or not contract.falsification_contract:
            issues.append(prefix + "predictive claim requires target, expected outcome and falsification")
    elif contract.outcome_contract.criteria:
        issues.append(prefix + "non-predictive claim must not masquerade as a market outcome hypothesis")
    return issues


def rule_claim_coverage(rules: Iterable[object]) -> Dict[str, object]:
    rows = list(rules or [])
    claim_ids: List[str] = []
    issues: List[str] = []
    counts: Dict[str, int] = {}
    predictive_with_outcomes = 0
    for rule in rows:
        rule_id = _rule_id(rule)
        basis = resolved_rule_knowledge_basis(rule)
        contract = resolved_rule_claim_contract(rule, basis)
        claim_ids.append(contract.claim_contract_id)
        counts[contract.claim_type] = int(counts.get(contract.claim_type, 0)) + 1
        issues.extend(rule_claim_contract_violations(contract, rule_id))
        if contract.is_predictive and authored_outcome_contract_complete(contract.outcome_contract):
            predictive_with_outcomes += 1
    duplicates = sorted({claim_id for claim_id in claim_ids if claim_ids.count(claim_id) > 1})
    if duplicates:
        issues.extend("duplicate claim contract: " + claim_id for claim_id in duplicates)
    return {
        "version": RULE_CLAIM_CONTRACT_VERSION,
        "ruleCount": len(rows),
        "claimCount": len(claim_ids),
        "claimTypeCounts": dict(sorted(counts.items())),
        "predictiveClaimCount": int(counts.get("market-hypothesis", 0)),
        "structuredOutcomeContractCount": predictive_with_outcomes,
        "orphanRuleCount": max(0, len(rows) - len(claim_ids)),
        "duplicateClaimCount": len(duplicates),
        "violationCount": len(set(issues)),
        "violations": sorted(set(issues)),
        "complete": bool(rows) and len(rows) == len(claim_ids) and not issues and not duplicates,
    }


def hypothesis_qualification(
    contract: RuleClaimContract,
    performance: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Derive qualification from durable outcomes without mutating RuleBox."""

    if not contract.is_predictive:
        return {
            "status": "active-guardrail" if contract.decision_authority == "guardrail-only" else "active-reference",
            "automatic": True,
            "decisionAuthority": contract.decision_authority,
            "reason": "시장 수익률 예측이 아닌 계약 유형의 검증 경계를 사용합니다.",
            "policy": contract.qualification_policy.to_dict(),
        }
    metrics = dict(performance or {}) if isinstance(performance, Mapping) else {}
    policy = contract.qualification_policy
    decisive = max(0, _integer(metrics.get("decisiveOutcomeCount")))
    rate = max(0.0, min(1.0, _number(metrics.get("directionalHitRate"))))
    confidence = metrics.get("directionalHitRateConfidence95") if isinstance(metrics.get("directionalHitRateConfidence95"), Mapping) else {}
    lower = max(0.0, min(1.0, _number(confidence.get("lower"))))
    upper = max(0.0, min(1.0, _number(confidence.get("upper"))))
    adjusted_return = _number(metrics.get("averageActionAdjustedReturnPct"))
    return_state = _text(metrics.get("actionReturnState")).lower()
    return_available = (
        return_state in {"non-negative", "negative", "flat"}
        or (not return_state and "averageActionAdjustedReturnPct" in metrics)
    )
    non_negative = (
        (return_available and adjusted_return >= 0)
        or not policy.require_non_negative_action_return
    )
    if (
        policy.automatic_quarantine
        and decisive >= policy.quarantine_floor
        and rate <= policy.quarantine_max_hit_rate
        and upper <= policy.quarantine_max_upper_confidence
        and return_available
        and adjusted_return < 0
    ):
        status = "quarantined"
        reason = "독립 결과에서 반증과 음의 행동 조정 수익이 반복돼 자동 격리했습니다."
    elif (
        policy.automatic_promotion
        and decisive >= policy.active_floor
        and rate >= policy.active_min_hit_rate
        and lower >= policy.active_min_lower_confidence
        and non_negative
    ):
        status = "active"
        reason = "독립 결과 수, 적중률 신뢰구간과 행동 조정 수익이 운영 자격 기준을 통과했습니다."
    elif (
        policy.automatic_promotion
        and decisive >= policy.limited_active_floor
        and rate >= policy.limited_min_hit_rate
        and non_negative
    ):
        status = "limited-active"
        reason = "초기 성과 기준을 통과했지만 완전 운영 자격을 위한 표본과 신뢰구간이 더 필요합니다."
    elif decisive >= policy.observation_floor:
        status = "observed"
        reason = "사후 결과가 쌓였지만 승격 또는 격리 기준에는 아직 도달하지 않았습니다."
    else:
        status = "shadow"
        reason = "독립된 사후 결과가 부족해 실험적 가설로 관찰합니다."
    return {
        "status": status,
        "automatic": True,
        "decisionAuthority": (
            "quarantined-reference-only" if status == "quarantined"
            else "qualified-investment-evidence" if status == "active"
            else "conditional-investment-evidence"
        ),
        "reason": reason,
        "decisiveOutcomeCount": decisive,
        "directionalHitRate": rate,
        "directionalHitRateConfidence95": {"lower": lower, "upper": upper},
        "averageActionAdjustedReturnPct": adjusted_return,
        "actionReturnAvailable": return_available,
        "policy": policy.to_dict(),
    }
