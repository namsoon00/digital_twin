"""Knowledge provenance and hypothesis boundaries for investment rules.

The TypeDB rule remains the authority for whether investment conditions match.
This module describes why an authored rule exists, what kind of judgement it
may influence, and whether a matched rule represents an investment hypothesis
or a non-competing guardrail.  It never evaluates market values or chooses an
investment action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping

from .ontology_rule_ownership import (
    RULE_OWNERS,
    RULE_OWNERSHIP_CONTRACT_VERSION,
    rule_ownership_contract,
)


RULE_KNOWLEDGE_BASIS_VERSION = "ontology-rule-knowledge-basis-v2"

RULE_KINDS = frozenset({
    "predictive-hypothesis",
    "policy-constraint",
    "execution-gate",
    "data-quality-gate",
    "context-observation",
})

DECISION_ELIGIBILITY_STATES = frozenset({
    "eligible",
    "conditional",
    "guardrail-only",
    "reference-only",
})

CANDIDATE_OWNER_CONTRACTS = {
    "predictive-hypothesis": (
        "statistical-model", "candidate-feature-contract", "candidate-model-signal",
        "candidate-validation-only", "review-before-catalog-admission",
    ),
    "policy-constraint": (
        "portfolio-policy", "candidate-policy-input", "candidate-policy-guardrail",
        "candidate-validation-only", "review-before-catalog-admission",
    ),
    "execution-gate": (
        "trade-execution", "candidate-execution-input", "candidate-execution-guardrail",
        "candidate-validation-only", "review-before-catalog-admission",
    ),
    "data-quality-gate": (
        "data-quality", "candidate-quality-input", "candidate-quality-guardrail",
        "candidate-validation-only", "review-before-catalog-admission",
    ),
    "context-observation": (
        "ontology-semantic", "candidate-observation-input", "candidate-semantic-context",
        "candidate-validation-only", "review-before-catalog-admission",
    ),
}


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _value(subject: object, *names: str) -> object:
    if isinstance(subject, Mapping):
        for name in names:
            if name in subject:
                return subject.get(name)
        return None
    for name in names:
        if hasattr(subject, name):
            return getattr(subject, name)
    return None


def _items(value: object) -> List[object]:
    return list(value) if isinstance(value, (list, tuple, set)) else []


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _unique(values: Iterable[object], limit: int = 32) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        item = _text(value)
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
        if len(result) >= max(1, int(limit or 1)):
            break
    return result


@dataclass(frozen=True)
class RuleKnowledgeReference:
    reference_id: str
    title: str
    source_type: str
    claim: str = ""
    url: str = ""
    applicability: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "referenceId": self.reference_id,
            "title": self.title,
            "sourceType": self.source_type,
            "claim": self.claim,
            "url": self.url,
            "applicability": self.applicability,
        }

    @staticmethod
    def from_dict(payload: Mapping[str, object]):
        item = dict(payload or {})
        return RuleKnowledgeReference(
            reference_id=_text(item.get("referenceId") or item.get("reference_id")),
            title=_text(item.get("title")),
            source_type=_text(item.get("sourceType") or item.get("source_type") or "internal"),
            claim=_text(item.get("claim")),
            url=_text(item.get("url")),
            applicability=_text(item.get("applicability")),
        )


@dataclass(frozen=True)
class RuleKnowledgeBasis:
    rule_kind: str = ""
    theory_family: str = ""
    thesis_family: str = ""
    basis_origin: str = ""
    threshold_origin: str = ""
    validation_status: str = ""
    decision_eligibility: str = ""
    requires_hypothesis: bool = False
    outcome_validation_required: bool = False
    evidence_independence_key: str = ""
    owner: str = ""
    input_contract: str = ""
    output_contract: str = ""
    decision_authority: str = ""
    migration_disposition: str = ""
    ownership_contract_version: str = ""
    plain_language_basis: str = ""
    applicability: List[str] = field(default_factory=list)
    references: List[RuleKnowledgeReference] = field(default_factory=list)
    version: str = RULE_KNOWLEDGE_BASIS_VERSION

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": self.version or RULE_KNOWLEDGE_BASIS_VERSION,
            "ruleKind": self.rule_kind,
            "theoryFamily": self.theory_family,
            "thesisFamily": self.thesis_family,
            "basisOrigin": self.basis_origin,
            "thresholdOrigin": self.threshold_origin,
            "validationStatus": self.validation_status,
            "decisionEligibility": self.decision_eligibility,
            "requiresHypothesis": bool(self.requires_hypothesis),
            "outcomeValidationRequired": bool(self.outcome_validation_required),
            "evidenceIndependenceKey": self.evidence_independence_key,
            "owner": self.owner,
            "inputContract": self.input_contract,
            "outputContract": self.output_contract,
            "decisionAuthority": self.decision_authority,
            "migrationDisposition": self.migration_disposition,
            "ownershipContractVersion": self.ownership_contract_version,
            "plainLanguageBasis": self.plain_language_basis,
            "applicability": list(self.applicability or []),
            "references": [item.to_dict() for item in self.references or []],
        }

    @staticmethod
    def from_dict(payload: Mapping[str, object]):
        item = dict(payload or {}) if isinstance(payload, Mapping) else {}
        references = [
            RuleKnowledgeReference.from_dict(value)
            for value in item.get("references") or []
            if isinstance(value, Mapping)
        ]
        return RuleKnowledgeBasis(
            rule_kind=_text(item.get("ruleKind") or item.get("rule_kind")),
            theory_family=_text(item.get("theoryFamily") or item.get("theory_family")),
            thesis_family=_text(item.get("thesisFamily") or item.get("thesis_family")),
            basis_origin=_text(item.get("basisOrigin") or item.get("basis_origin")),
            threshold_origin=_text(item.get("thresholdOrigin") or item.get("threshold_origin")),
            validation_status=_text(item.get("validationStatus") or item.get("validation_status")),
            decision_eligibility=_text(item.get("decisionEligibility") or item.get("decision_eligibility")),
            requires_hypothesis=_bool(item.get("requiresHypothesis") if "requiresHypothesis" in item else item.get("requires_hypothesis")),
            outcome_validation_required=_bool(
                item.get("outcomeValidationRequired")
                if "outcomeValidationRequired" in item
                else item.get("outcome_validation_required")
            ),
            evidence_independence_key=_text(
                item.get("evidenceIndependenceKey") or item.get("evidence_independence_key")
            ),
            owner=_text(item.get("owner")),
            input_contract=_text(item.get("inputContract") or item.get("input_contract")),
            output_contract=_text(item.get("outputContract") or item.get("output_contract")),
            decision_authority=_text(item.get("decisionAuthority") or item.get("decision_authority")),
            migration_disposition=_text(
                item.get("migrationDisposition") or item.get("migration_disposition")
            ),
            ownership_contract_version=_text(
                item.get("ownershipContractVersion") or item.get("ownership_contract_version")
            ),
            plain_language_basis=_text(item.get("plainLanguageBasis") or item.get("plain_language_basis")),
            applicability=_unique(item.get("applicability") or [], 24),
            references=references,
            version=_text(item.get("version")) or RULE_KNOWLEDGE_BASIS_VERSION,
        )


REFERENCE_LIBRARY: Dict[str, RuleKnowledgeReference] = {
    "momentum": RuleKnowledgeReference(
        reference_id="research:jegadeesh-titman-1993",
        title="Returns to Buying Winners and Selling Losers",
        source_type="peer-reviewed-research",
        claim="중기 상대 모멘텀의 존재를 검토할 이론적 출발점입니다.",
        url="https://doi.org/10.1111/j.1540-6261.1993.tb04702.x",
        applicability="연구 기간과 현재 규칙의 짧은 이동평균 임계값은 별도 검증이 필요합니다.",
    ),
    "fundamental": RuleKnowledgeReference(
        reference_id="research:fama-french-2015",
        title="A five-factor asset pricing model",
        source_type="peer-reviewed-research",
        claim="가치, 수익성, 투자 특성을 장기 수익률 차이의 후보 요인으로 다룹니다.",
        url="https://doi.org/10.1016/j.jfineco.2014.10.010",
        applicability="개별 종목 적정가나 단기 매수 시점을 직접 제공하지 않습니다.",
    ),
    "event": RuleKnowledgeReference(
        reference_id="research:bernard-thomas-1989",
        title="Post-Earnings-Announcement Drift",
        source_type="peer-reviewed-research",
        claim="기업 정보가 가격에 지연 반영될 가능성을 검토하는 사건 가설의 출발점입니다.",
        url="https://doi.org/10.2307/2491062",
        applicability="실적 이외 뉴스와 공시는 사건 종류별 재검증이 필요합니다.",
    ),
    "microstructure": RuleKnowledgeReference(
        reference_id="research:cont-kukanov-stoikov-2014",
        title="The Price Impact of Order Book Events",
        source_type="peer-reviewed-research",
        claim="짧은 구간의 주문 흐름 불균형과 가격 충격 관계를 설명합니다.",
        url="https://doi.org/10.1093/jjfinec/nbt003",
        applicability="시장·호가 깊이·거래비용 차이를 반영해야 하며 장기 방향 근거로 사용하지 않습니다.",
    ),
    "portfolio": RuleKnowledgeReference(
        reference_id="research:markowitz-1952",
        title="Portfolio Selection",
        source_type="peer-reviewed-research",
        claim="개별 종목이 아니라 포트폴리오 위험과 분산을 함께 관리하는 기반입니다.",
        url="https://doi.org/10.1111/j.1540-6261.1952.tb01525.x",
        applicability="사용자 계좌의 실제 한도와 목적이 최종 정책을 결정합니다.",
    ),
    "account-policy": RuleKnowledgeReference(
        reference_id="policy:account-investment-mandate",
        title="계좌 투자 성향과 위험 예산",
        source_type="account-policy",
        claim="손실 허용폭, 집중도, 현금 및 노출 한도를 정의합니다.",
        applicability="가격 예측이 아니라 행동 제한에만 사용합니다.",
    ),
    "provenance": RuleKnowledgeReference(
        reference_id="contract:ontology-data-provenance",
        title="온톨로지 데이터 품질 계약",
        source_type="system-contract",
        claim="누락, 신선도, 출처와 공급 가능성을 판단 근거 자격과 분리합니다.",
        applicability="투자 방향을 만들지 않고 판단 가능 여부만 제한합니다.",
    ),
}


CONTEXT_ONLY_RULE_TOKENS = (
    "instrument_profile.strategy_fit",
    "instrument_profile.strategy_mismatch",
    "instrument_profile.averaging_down_policy",
    "benchmark.beta.context",
    "market_proxy.observation",
    "materiality.alert_candidate",
    "portfolio.reentry.review",
    "portfolio.decision_action.divergence",
    "disclosure.event_risk",
)

POLICY_RULE_TOKENS = (
    "strategy_profile.",
    "factor.position_crowding",
    "portfolio.repeated_loss_add",
    "portfolio.activity.concentration",
    "portfolio.risk_policy",
    "portfolio.concentration",
    "portfolio.rebalance",
)


def _rule_id(rule: object) -> str:
    return _text(_value(rule, "rule_id", "ruleId", "id")).lower()


def _action_group(rule: object) -> str:
    return _text(_value(rule, "action_group", "actionGroup")).lower()


def _source_kind(rule: object) -> str:
    return _text(_value(rule, "source_kind", "sourceKind")).lower()


def _assessment_scope(rule: object) -> str:
    direct = _text(_value(rule, "assessment_scope", "assessmentScope")).lower()
    manifest = _value(rule, "domain_manifest", "domainManifest")
    if not direct and isinstance(manifest, Mapping):
        direct = _text(manifest.get("assessmentScope") or manifest.get("assessment_scope")).lower()
    action_group = _action_group(rule)
    source_kind = _source_kind(rule)
    rule_id = _rule_id(rule)
    if direct:
        return direct
    if action_group in {"dataquality", "quality"} or "coverage_gap" in rule_id or "quality." in rule_id:
        return "evidence-quality"
    if action_group.startswith("execution") or action_group == "executionrisk":
        return "execution-readiness"
    if action_group == "rebalance" or source_kind == "portfolio" or "portfolio." in rule_id:
        return "portfolio-fit"
    return "investment-opinion"


def _decision_effects(rule: object) -> List[str]:
    derivations = _items(_value(rule, "derivations"))
    if not derivations and isinstance(rule, Mapping):
        derivations = [rule]
    return _unique(
        _value(item, "decision_effect", "decisionEffect")
        for item in derivations
        if _text(_value(item, "decision_effect", "decisionEffect"))
    )


def _rule_kind(rule: object) -> str:
    rule_id = _rule_id(rule)
    action_group = _action_group(rule)
    source_kind = _source_kind(rule)
    assessment_scope = _assessment_scope(rule)
    if assessment_scope == "evidence-quality":
        return "data-quality-gate"
    if assessment_scope == "execution-readiness":
        return "execution-gate"
    if (
        assessment_scope == "portfolio-fit"
        or source_kind == "portfolio"
        or action_group in {"rebalance", "strategyfit", "lossguard"}
        or any(token in rule_id for token in POLICY_RULE_TOKENS)
    ):
        return "policy-constraint"
    if source_kind == "crypto-asset" or action_group == "alertreview" or any(
        token in rule_id for token in CONTEXT_ONLY_RULE_TOKENS
    ):
        return "context-observation"
    return "predictive-hypothesis"


def _theory_family(rule: object, rule_kind: str) -> str:
    rule_id = _rule_id(rule)
    action_group = _action_group(rule)
    if rule_kind == "context-observation" and (
        rule_id.startswith("graph.notification.")
        or action_group == "alertreview"
    ):
        return "notification-materiality"
    if rule_kind == "data-quality-gate":
        return "data-provenance"
    if rule_kind == "execution-gate":
        return "market-microstructure"
    if rule_kind == "policy-constraint":
        return "portfolio-risk-and-account-mandate"
    if any(token in rule_id for token in ("news.", "disclosure.", "earnings.", "regulatory.", "event.")):
        return "event-information-diffusion"
    if action_group in {"valuation", "fundamental"} or any(token in rule_id for token in ("valuation", "fundamental", "value_trap")):
        return "fundamental-valuation-and-factors"
    if any(token in rule_id for token in ("flow.", "investor_flow", "smart_money", "liquidity")):
        return "market-microstructure-and-investor-flow"
    if any(token in rule_id for token in ("macro.", "fx.", "crypto.", "cross_listing", "market_proxy", "factor.", "benchmark.")):
        return "cross-asset-and-regime-transmission"
    if any(token in rule_id for token in ("rebound", "recovery", "reversal", "overreaction", "pullback", "reclaim")):
        return "behavioral-mean-reversion"
    if any(token in rule_id for token in ("trend", "momentum", "temporal", "price.", "profit_", "loss_")):
        return "behavioral-momentum-and-trend"
    return "authored-investment-thesis"


def _thesis_family(rule: object, rule_kind: str, theory_family: str) -> str:
    rule_id = _rule_id(rule)
    effects = set(_decision_effects(rule))
    risk_path = bool(effects.intersection({"constrain", "block"})) or any(
        token in rule_id for token in ("risk", "decline", "break", "failure", "outflow", "distribution", "dilution")
    )
    if rule_kind == "data-quality-gate":
        return "data-quality"
    if rule_kind == "execution-gate":
        return "liquidity-and-execution"
    if rule_kind == "policy-constraint":
        return "portfolio-risk"
    if rule_kind == "context-observation":
        return "market-context"
    if theory_family == "event-information-diffusion":
        return "event-risk" if risk_path else "event-support"
    if theory_family == "fundamental-valuation-and-factors":
        return "fundamental-deterioration" if risk_path else "fundamental-rerating"
    if theory_family == "market-microstructure-and-investor-flow":
        return "flow-distribution" if risk_path else "flow-accumulation"
    if theory_family == "cross-asset-and-regime-transmission":
        return "cross-asset-risk" if risk_path else "cross-asset-support"
    if theory_family == "behavioral-mean-reversion":
        return "failed-recovery" if risk_path else "mean-reversion"
    if theory_family == "behavioral-momentum-and-trend":
        return "trend-break" if risk_path else "trend-continuation"
    return "thesis-risk" if risk_path else "thesis-support"


def _references_for(theory_family: str, rule_kind: str) -> List[RuleKnowledgeReference]:
    keys: List[str] = []
    if theory_family in {"behavioral-momentum-and-trend", "behavioral-mean-reversion"}:
        keys.append("momentum")
    if theory_family == "fundamental-valuation-and-factors":
        keys.append("fundamental")
    if theory_family == "event-information-diffusion":
        keys.append("event")
    if "microstructure" in theory_family:
        keys.append("microstructure")
    if rule_kind == "policy-constraint":
        keys.extend(["portfolio", "account-policy"])
    if rule_kind == "data-quality-gate":
        keys.append("provenance")
    return [REFERENCE_LIBRARY[key] for key in _unique(keys) if key in REFERENCE_LIBRARY]


def _plain_language_basis(rule_kind: str, theory_family: str) -> str:
    if rule_kind == "data-quality-gate":
        return "데이터 출처와 신선도가 판단에 사용할 수 있는 상태인지 확인하는 운영 계약입니다."
    if rule_kind == "execution-gate":
        return "호가, 체결과 유동성으로 주문 실행 가능성을 제한하며 장기 가격 방향을 예측하지 않습니다."
    if rule_kind == "policy-constraint":
        return "계좌 위험 예산과 포트폴리오 제약을 적용하며 가격 방향 가설과 경쟁하지 않습니다."
    if rule_kind == "context-observation":
        return "현재 시장·종목 문맥을 설명하지만 단독 투자 행동 근거로 사용하지 않습니다."
    labels = {
        "behavioral-momentum-and-trend": "가격 정보가 점진적으로 반영돼 추세가 이어질 수 있다는 가설입니다.",
        "behavioral-mean-reversion": "단기 과잉 반응이나 유동성 충격 뒤 가격이 일부 되돌아올 수 있다는 가설입니다.",
        "fundamental-valuation-and-factors": "이익, 현금흐름, 가치와 기업 품질이 가격 재평가로 이어질 수 있다는 가설입니다.",
        "event-information-diffusion": "기업 사건과 공시 정보가 가격에 지연 또는 과도하게 반영될 수 있다는 가설입니다.",
        "market-microstructure-and-investor-flow": "투자자 수급과 주문 흐름이 단기 가격 경로에 영향을 줄 수 있다는 가설입니다.",
        "cross-asset-and-regime-transmission": "금리, 환율, 크립토, 벤치마크 변화가 민감 종목에 전달될 수 있다는 가설입니다.",
    }
    return labels.get(theory_family, "운영자가 작성한 투자 논리를 시점 재현 데이터로 검증해야 하는 가설입니다.")


def resolved_rule_knowledge_basis(rule: object) -> RuleKnowledgeBasis:
    """Resolve explicit metadata or produce a transparent conservative basis."""

    rule_id = _rule_id(rule)
    try:
        ownership = rule_ownership_contract(rule_id)
    except ValueError:
        ownership = None
    raw = _value(rule, "knowledge_basis", "knowledgeBasis")
    if isinstance(raw, RuleKnowledgeBasis) and raw.rule_kind:
        explicit = raw
    elif isinstance(raw, Mapping):
        explicit = RuleKnowledgeBasis.from_dict(raw)
    else:
        explicit = RuleKnowledgeBasis()
    explicit_contract_complete = bool(
        explicit.owner in RULE_OWNERS
        and explicit.input_contract
        and explicit.output_contract
        and explicit.decision_authority
        and explicit.migration_disposition
        and explicit.ownership_contract_version == RULE_OWNERSHIP_CONTRACT_VERSION
    )
    if explicit.rule_kind and (
        (ownership and explicit.ownership_contract_version == ownership.version)
        or (not ownership and explicit_contract_complete)
    ):
        return explicit
    if explicit.rule_kind and not ownership and explicit.rule_kind in CANDIDATE_OWNER_CONTRACTS:
        owner, input_contract, output_contract, authority, disposition = (
            CANDIDATE_OWNER_CONTRACTS[explicit.rule_kind]
        )
        payload = explicit.to_dict()
        payload.update({
            "owner": owner,
            "inputContract": input_contract,
            "outputContract": output_contract,
            "decisionAuthority": authority,
            "migrationDisposition": disposition,
            "ownershipContractVersion": RULE_OWNERSHIP_CONTRACT_VERSION,
        })
        return RuleKnowledgeBasis.from_dict(payload)
    if explicit.rule_kind and ownership and explicit.basis_origin not in {"", "catalog-derived", "ownership-catalog"}:
        payload = explicit.to_dict()
        payload.update({
            "owner": ownership.owner,
            "inputContract": ownership.input_contract,
            "outputContract": ownership.output_contract,
            "decisionAuthority": ownership.decision_authority,
            "migrationDisposition": ownership.migration_disposition,
            "ownershipContractVersion": ownership.version,
        })
        return RuleKnowledgeBasis.from_dict(payload)

    rule_kind = ownership.rule_kind if ownership else _rule_kind(rule)
    theory_family = _theory_family(rule, rule_kind)
    thesis_family = _thesis_family(rule, rule_kind, theory_family)
    requires_hypothesis = rule_kind == "predictive-hypothesis"
    if rule_kind in {"policy-constraint", "execution-gate", "data-quality-gate"}:
        decision_eligibility = "guardrail-only"
        validation_status = "approved-contract"
        threshold_origin = "policy-or-observation-contract"
    elif rule_kind == "context-observation":
        decision_eligibility = "reference-only"
        validation_status = "reference-only"
        threshold_origin = "observed-context"
    else:
        decision_eligibility = "conditional"
        validation_status = "replay-required"
        threshold_origin = "authored-heuristic"
    # A candidate rule is not admitted to the immutable production ownership
    # catalog yet. It still needs a conservative owner while sandbox
    # validation runs; production bootstrap IDs never use this fallback.
    fallback_owner, fallback_input, fallback_output, fallback_authority, fallback_disposition = (
        CANDIDATE_OWNER_CONTRACTS[rule_kind]
    )
    return RuleKnowledgeBasis(
        rule_kind=rule_kind,
        theory_family=theory_family,
        thesis_family=thesis_family,
        basis_origin="ownership-catalog" if ownership else "legacy-inference",
        threshold_origin=threshold_origin,
        validation_status=validation_status,
        decision_eligibility=decision_eligibility,
        requires_hypothesis=requires_hypothesis,
        outcome_validation_required=requires_hypothesis,
        evidence_independence_key=thesis_family,
        owner=ownership.owner if ownership else fallback_owner,
        input_contract=ownership.input_contract if ownership else fallback_input,
        output_contract=ownership.output_contract if ownership else fallback_output,
        decision_authority=ownership.decision_authority if ownership else fallback_authority,
        migration_disposition=ownership.migration_disposition if ownership else fallback_disposition,
        ownership_contract_version=(
            ownership.version if ownership else RULE_OWNERSHIP_CONTRACT_VERSION
        ),
        plain_language_basis=_plain_language_basis(rule_kind, theory_family),
        applicability=_unique([_source_kind(rule) or "all", _assessment_scope(rule)], 8),
        references=_references_for(theory_family, rule_kind),
    )


def rule_knowledge_basis_from_rows(rule_id: str, *groups: Iterable[Mapping[str, object]]) -> RuleKnowledgeBasis:
    """Resolve the same contract from materialized RuleBox/InferenceBox rows."""

    rows = [dict(item) for group in groups for item in group or [] if isinstance(item, Mapping)]
    for row in rows:
        raw = row.get("knowledgeBasis") or row.get("knowledge_basis")
        if isinstance(raw, Mapping):
            return resolved_rule_knowledge_basis({
                **row,
                "ruleId": rule_id,
                "knowledgeBasis": raw,
            })
    primary = next((row for row in rows if row), {})
    return resolved_rule_knowledge_basis({
        "ruleId": rule_id,
        "sourceKind": primary.get("ruleSourceKind") or primary.get("sourceKind"),
        "actionGroup": primary.get("actionGroup") or primary.get("action_group"),
        "assessmentScope": primary.get("assessmentScope") or primary.get("assessment_scope"),
        "derivations": rows,
    })


def knowledge_basis_violations(basis: RuleKnowledgeBasis, rule_id: str = "") -> List[str]:
    prefix = (_text(rule_id) or "<unknown-rule>") + ": "
    issues = []
    if basis.rule_kind not in RULE_KINDS:
        issues.append(prefix + "knowledge basis has invalid rule_kind")
    if not basis.theory_family:
        issues.append(prefix + "knowledge basis requires theory_family")
    if not basis.thesis_family:
        issues.append(prefix + "knowledge basis requires thesis_family")
    if basis.decision_eligibility not in DECISION_ELIGIBILITY_STATES:
        issues.append(prefix + "knowledge basis has invalid decision_eligibility")
    if basis.owner not in RULE_OWNERS:
        issues.append(prefix + "knowledge basis has invalid or missing owner")
    if not basis.input_contract:
        issues.append(prefix + "knowledge basis requires input_contract")
    if not basis.output_contract:
        issues.append(prefix + "knowledge basis requires output_contract")
    if not basis.decision_authority:
        issues.append(prefix + "knowledge basis requires decision_authority")
    if not basis.migration_disposition:
        issues.append(prefix + "knowledge basis requires migration_disposition")
    if basis.ownership_contract_version != RULE_OWNERSHIP_CONTRACT_VERSION:
        issues.append(prefix + "knowledge basis ownership contract is stale")
    if basis.requires_hypothesis and basis.rule_kind != "predictive-hypothesis":
        issues.append(prefix + "only predictive rules may create hypotheses")
    if basis.rule_kind == "predictive-hypothesis" and not basis.outcome_validation_required:
        issues.append(prefix + "predictive rules require outcome validation")
    if not basis.plain_language_basis:
        issues.append(prefix + "knowledge basis requires a plain-language explanation")
    return issues


def knowledge_basis_summary(bases: Iterable[RuleKnowledgeBasis]) -> Dict[str, object]:
    rows = list(bases or [])
    counts: Dict[str, int] = {}
    theory_counts: Dict[str, int] = {}
    validation_counts: Dict[str, int] = {}
    for basis in rows:
        counts[basis.rule_kind] = counts.get(basis.rule_kind, 0) + 1
        theory_counts[basis.theory_family] = theory_counts.get(basis.theory_family, 0) + 1
        validation_counts[basis.validation_status] = validation_counts.get(basis.validation_status, 0) + 1
    return {
        "version": RULE_KNOWLEDGE_BASIS_VERSION,
        "ruleCount": len(rows),
        "ruleKindCounts": dict(sorted(counts.items())),
        "theoryFamilyCounts": dict(sorted(theory_counts.items())),
        "validationStatusCounts": dict(sorted(validation_counts.items())),
        "hypothesisRuleCount": len([item for item in rows if item.requires_hypothesis]),
        "guardrailRuleCount": len([item for item in rows if item.decision_eligibility == "guardrail-only"]),
        "referenceRuleCount": len([item for item in rows if item.decision_eligibility == "reference-only"]),
    }
