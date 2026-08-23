"""Disabled TypeDB-rule candidates that consume validated model signals."""

from dataclasses import replace
import hashlib
import json
from typing import Dict, Iterable, List

from ..hypothesis_scoping import condition_scope_profile
from ..ontology_change_impact import rule_condition_dependency_profile
from ..ontology_rulebox_contracts import (
    GraphInferenceRule,
    GraphRuleCondition,
    HypothesisLifecyclePolicy,
)
from .rule_contracts import (
    CROSS_ASSET_SIGNALS,
    EVENT_SIGNALS,
    FLOW_SIGNALS,
    PRICE_TREND_SIGNALS,
    VALUATION_SIGNALS,
    model_signal_type_for_rule,
    rule_statistical_signal_contract,
)
from .registry import model_release, signal_hypothesis_family


STATISTICAL_RULE_CANDIDATE_RELEASE_VERSION = "statistical-rule-candidate-release-v2"
STATISTICAL_RULE_PRODUCTION_RELEASE_VERSION = "statistical-rule-model-signal-production-v4"
MODEL_INPUT_ROUTING_CONTRACT_VERSION = "predictive-model-input-routing-v1"
EMPIRICAL_PRICE_THEORIES = {
    "behavioral-momentum-and-trend",
    "behavioral-mean-reversion",
}
MODEL_SIGNAL_LABELS = {
    "price-trend-continuation-support": "가격 경로 추세 지속 신호",
    "price-trend-break-risk": "가격 경로 추세 훼손 신호",
    "price-downside-acceleration-risk": "가격 경로 하락 가속 신호",
    "price-recovery-support": "가격 경로 회복 신호",
    "flow-accumulation-support": "수급 축적 신호",
    "flow-distribution-risk": "수급 분산 신호",
    "flow-price-divergence-risk": "가격·수급 괴리 신호",
    "cross-asset-residual-support": "교차자산 상대 강도 신호",
    "cross-asset-residual-risk": "교차자산 상대 약세 신호",
    "regime-transition-risk": "시장 국면 전환 위험 신호",
    "valuation-relative-opportunity": "상대가치 기회 신호",
    "valuation-relative-stretch-risk": "상대가치 부담 신호",
    "event-abnormal-return-support": "사건 반응 지지 신호",
    "event-abnormal-return-risk": "사건 반응 위험 신호",
    "event-response-persistence": "사건 반응 지속 신호",
}


def _candidate_signal_type(rule: GraphInferenceRule) -> str:
    return model_signal_type_for_rule(
        rule.rule_id,
        rule.resolved_knowledge_basis.theory_family,
    )


def _retained_context_conditions(rule: GraphInferenceRule) -> List[GraphRuleCondition]:
    """Keep account policy and structural instrument facts outside the scorer."""
    rows = []
    for index, condition in enumerate(rule.conditions or []):
        profile = condition_scope_profile(condition.to_dict(), index)
        if str(profile.get("scope") or "") == "account":
            rows.append(condition)
    return rows


def _model_input_routing_contract(rule: GraphInferenceRule) -> Dict[str, object]:
    profiles = [
        rule_condition_dependency_profile(condition.to_dict())
        for index, condition in enumerate(rule.conditions or [])
        if str(condition_scope_profile(condition.to_dict(), index).get("scope") or "") == "market"
    ]
    return {
        "version": MODEL_INPUT_ROUTING_CONTRACT_VERSION,
        "hypothesisContractId": rule.rule_id,
        "sourceRuleVersion": rule.version,
        "scopeFamilies": sorted({
            family
            for profile in profiles
            for family in profile.get("scopeFamilies") or []
            if str(family or "").strip()
        }),
        "dependencyKeys": sorted({
            key
            for profile in profiles
            for key in profile.get("dependencyKeys") or []
            if str(key or "").strip()
        }),
        "conditionProfiles": profiles,
        "conservative": any(bool(profile.get("conservative")) for profile in profiles),
    }


def compile_price_signal_rule_candidate(rule: GraphInferenceRule) -> GraphInferenceRule:
    theory = str(rule.resolved_knowledge_basis.theory_family or "")
    if theory not in EMPIRICAL_PRICE_THEORIES:
        raise ValueError("Rule is not part of the price-signal migration cohort: " + rule.rule_id)
    return compile_model_signal_rule_candidate(rule)


def compile_model_signal_rule_candidate(rule: GraphInferenceRule) -> GraphInferenceRule:
    contract = rule_statistical_signal_contract(rule)
    if not bool(contract.get("required")):
        raise ValueError("Rule does not own a statistical hypothesis: " + rule.rule_id)
    signal_types = list(contract.get("signalTypes") or []) or [_candidate_signal_type(rule)]
    supported = set(
        PRICE_TREND_SIGNALS
        + FLOW_SIGNALS
        + CROSS_ASSET_SIGNALS
        + VALUATION_SIGNALS
        + EVENT_SIGNALS
    )
    unsupported = [signal_type for signal_type in signal_types if signal_type not in supported]
    if unsupported:
        raise ValueError("Unsupported statistical signal type: " + unsupported[0])
    release_ids_by_type = dict(contract.get("signalReleaseIdsByType") or {})
    signal_conditions = []
    releases = []
    for index, signal_type in enumerate(signal_types):
        release_id = str(release_ids_by_type.get(signal_type) or "")
        release = model_release(release_id)
        releases.append(release)
        eligibility_status = (
            "conditional" if release.decision_eligibility == "conditional" else "eligible"
        )
        signal_conditions.append(GraphRuleCondition(
            condition_id=(
                "validated-model-signal:" + rule.rule_id
                if index == 0
                else "validated-model-signal-" + str(index + 1) + ":" + rule.rule_id
            ),
            kind="relation",
            description="역사적 재생과 시점 고정 검증을 통과한 통계 신호가 존재합니다.",
            relation_type="HAS_MODEL_SIGNAL",
            direction="out",
            target_kind="statistical-model-hypothesis-evidence",
            target_property_filters={
                "signalType": signal_type,
                "hypothesisContractId": rule.rule_id,
                "hypothesisFamilyId": signal_hypothesis_family(signal_type),
                "releaseId": release_id,
                "strengthBand": "strong",
                "validationStatus": release.validation_status,
                "decisionEligibility": release.decision_eligibility,
                "eligibilityStatus": eligibility_status,
            },
            role="required",
            hypothesis_scope="market",
            evidence_group_key="validated-model-signal:" + signal_type,
            change_trigger=True,
            invalidation_trigger=True,
        ))
    release = releases[0]
    context_conditions = _retained_context_conditions(rule)
    conditions = [*context_conditions, *signal_conditions]
    formation_ids = [item.condition_id for item in conditions]
    lifecycle = rule.resolved_hypothesis_lifecycle()
    required_domains = ["model-signal"]
    next_data_requirements = []
    model_families = {item.model_family for item in releases}
    if "price-path-statistics" in model_families:
        required_domains.append("price-path")
        next_data_requirements.append("다음 시점 고정 가격 경로와 모델 신호 변화")
    if "investor-flow-statistics" in model_families:
        required_domains.append("investor-flow")
        next_data_requirements.append("다음 투자자 수급과 가격 반응의 동조 여부")
    candidate_lifecycle = HypothesisLifecyclePolicy(
        formation_condition_ids=formation_ids,
        invalidation_condition_ids=[item.condition_id for item in signal_conditions],
        validity_minutes=lifecycle.validity_minutes,
        required_freshness_domains=sorted(set(required_domains)),
        next_data_requirements=next_data_requirements,
        invalidation_mode="typedb-rule-not-materialized",
        outcome_contract=lifecycle.outcome_contract,
    )
    basis = replace(
        rule.resolved_knowledge_basis,
        threshold_origin="calibrated-model-release-policy",
        validation_status="candidate-replay-required",
        decision_eligibility="reference-only",
        decision_authority="typedb-model-signal-rule",
        migration_disposition="candidate-awaiting-promotion",
        outcome_validation_required=True,
        plain_language_basis=(
            rule.resolved_knowledge_basis.plain_language_basis
            + " 숫자 경로 조건은 검증된 통계 모델 신호로 교체한 비활성 후보입니다."
        ).strip(),
    )
    return replace(
        rule,
        rule_id=rule.rule_id + ".model-signal-candidate.v1",
        label=rule.label + " · 통계 신호 후보",
        version=STATISTICAL_RULE_CANDIDATE_RELEASE_VERSION,
        conditions=conditions,
        knowledge_basis=basis,
        hypothesis_lifecycle=candidate_lifecycle,
        any_condition_min_count=1,
        execution_stage="candidate-model-signal",
        failure_policy="block",
        cost_hint="compact-model-signal",
        model_input_contract=_model_input_routing_contract(rule),
        enabled=False,
    )


def price_signal_rule_candidates(rules: Iterable[GraphInferenceRule]) -> List[GraphInferenceRule]:
    return [
        compile_price_signal_rule_candidate(rule)
        for rule in rules or []
        if str(rule.resolved_knowledge_basis.theory_family or "") in EMPIRICAL_PRICE_THEORIES
        and str(rule.resolved_knowledge_basis.rule_kind or "") == "predictive-hypothesis"
    ]


def model_signal_rule_candidates(rules: Iterable[GraphInferenceRule]) -> List[GraphInferenceRule]:
    return [
        compile_model_signal_rule_candidate(rule)
        for rule in rules or []
        if bool(rule_statistical_signal_contract(rule).get("required"))
    ]


def promote_model_signal_rule(rule: GraphInferenceRule) -> GraphInferenceRule:
    """Replace one raw predictive rule with its governed model-signal form."""

    contract = rule_statistical_signal_contract(rule)
    if not bool(contract.get("productionEligible")):
        raise ValueError("Rule has no production model signal: " + rule.rule_id)
    candidate = compile_model_signal_rule_candidate(rule)
    release_id = str((contract.get("releaseIds") or [""])[0])
    release = model_release(release_id)
    signal_labels = [
        MODEL_SIGNAL_LABELS.get(signal_type, signal_type)
        for signal_type in contract.get("signalTypes") or []
    ]
    signal_summary = " + ".join(signal_labels) or "검증된 모델 신호"
    basis = replace(
        candidate.resolved_knowledge_basis,
        threshold_origin="governed-model-score-contract",
        validation_status=release.validation_status,
        decision_eligibility=release.decision_eligibility,
        decision_authority="typedb-model-signal-rule",
        migration_disposition="model-signal-production",
        plain_language_basis=(
            rule.resolved_knowledge_basis.plain_language_basis
            + " 원시 숫자 조건 대신 시점 고정 모델 신호를 TypeDB가 해석합니다."
        ).strip(),
    )
    return replace(
        candidate,
        rule_id=rule.rule_id,
        label=rule.label + " · 모델 신호",
        version=STATISTICAL_RULE_PRODUCTION_RELEASE_VERSION,
        knowledge_basis=basis,
        derivations=[
            replace(
                derivation,
                belief_label=(
                    signal_summary
                    + "가 강하고 규칙의 계정·구조 조건을 충족해 '"
                    + str(derivation.target_label or derivation.target_key or "관계 후보")
                    + "' 관계를 지지합니다."
                ),
                ai_influence_label=(
                    signal_summary
                    + " · "
                    + str(
                        derivation.candidate_action_label
                        or derivation.decision_label
                        or derivation.ai_influence_label
                        or derivation.target_label
                    )
                ),
            )
            for derivation in candidate.derivations
        ],
        prompt_hint=(
            signal_summary
            + "와 규칙에 남은 계정·구조 조건만 설명하고, 제거된 원시 임계치가 "
            + "직접 확인된 것처럼 재서술하지 않습니다."
        ),
        execution_stage="production-model-signal",
        failure_policy="block",
        # Conversion changes the decision boundary, not catalog governance.
        # Duplicate or operator-disabled hypotheses must remain disabled.
        enabled=rule.enabled,
    )


def production_model_signal_rulebox(rules: Iterable[GraphInferenceRule]) -> List[GraphInferenceRule]:
    """Fail closed for every predictive rule that lacks a governed scorer."""

    result = []
    for rule in rules or []:
        contract = rule_statistical_signal_contract(rule)
        if not bool(contract.get("required")):
            result.append(rule)
        elif bool(contract.get("productionEligible")):
            result.append(promote_model_signal_rule(rule))
        else:
            basis = replace(
                rule.resolved_knowledge_basis,
                decision_eligibility="reference-only",
                decision_authority="disabled-awaiting-model-signal",
                migration_disposition="awaiting-governed-model-scorer",
            )
            result.append(replace(
                rule,
                knowledge_basis=basis,
                execution_stage="awaiting-model-signal",
                enabled=False,
            ))
    return result


def statistical_rule_candidate_release(rules: Iterable[GraphInferenceRule]) -> Dict[str, object]:
    source_rules = list(rules or [])
    candidates = model_signal_rule_candidates(source_rules)
    payload = {
        "version": STATISTICAL_RULE_CANDIDATE_RELEASE_VERSION,
        "status": "disabled-candidate",
        "productionEligible": False,
        "candidateCount": len(candidates),
        "sourceRuleIds": [
            rule.rule_id
            for rule in source_rules
            if bool(rule_statistical_signal_contract(rule).get("required"))
        ],
        "candidateRuleIds": [rule.rule_id for rule in candidates],
        "rules": [rule.to_dict() for rule in candidates],
        "promotionGates": [
            "point-in-time-replay-complete",
            "minimum-outcome-sample-count-met",
            "probability-calibration-approved",
            "economic-utility-not-worse",
            "no-action-envelope-regression",
            "latency-slo-not-worse",
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    payload["fingerprint"] = hashlib.sha256(encoded).hexdigest()
    return payload
