"""Map predictive RuleBox hypotheses to governed statistical-signal families.

This catalog is routing and migration metadata only. It never evaluates a
TypeDB condition or changes the current action envelope.
"""

from typing import Dict, Iterable, Mapping

from .registry import DEFAULT_PRICE_SIGNAL_RELEASE_ID


RULE_SIGNAL_CONTRACT_VERSION = "rule-statistical-signal-contract-v1"

PRICE_TREND_SIGNALS = (
    "price-trend-continuation-support",
    "price-trend-break-risk",
    "price-downside-acceleration-risk",
    "price-recovery-support",
)
FLOW_SIGNALS = (
    "flow-accumulation-support",
    "flow-distribution-risk",
    "flow-price-divergence-risk",
)
CROSS_ASSET_SIGNALS = (
    "cross-asset-residual-support",
    "cross-asset-residual-risk",
    "regime-transition-risk",
)
VALUATION_SIGNALS = (
    "valuation-relative-opportunity",
    "valuation-relative-stretch-risk",
)
EVENT_SIGNALS = (
    "event-abnormal-return-support",
    "event-abnormal-return-risk",
    "event-response-persistence",
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _knowledge_basis(rule: object) -> Dict[str, object]:
    if isinstance(rule, Mapping):
        value = rule.get("knowledgeBasis") or rule.get("knowledge_basis") or {}
        return dict(value or {}) if isinstance(value, Mapping) else {}
    value = getattr(rule, "resolved_knowledge_basis", None)
    if hasattr(value, "to_dict"):
        return dict(value.to_dict() or {})
    return {}


def _rule_id(rule: object) -> str:
    if isinstance(rule, Mapping):
        return _text(rule.get("ruleId") or rule.get("rule_id"))
    return _text(getattr(rule, "rule_id", ""))


def _signal_mapping(rule_id: str, theory_family: str):
    if theory_family in {"behavioral-momentum-and-trend", "behavioral-mean-reversion"}:
        return PRICE_TREND_SIGNALS, "shadow-signal-available", DEFAULT_PRICE_SIGNAL_RELEASE_ID, 1
    if theory_family == "market-microstructure-and-investor-flow":
        return FLOW_SIGNALS, "planned", "flow-statistics-candidate-v1", 3
    if theory_family == "cross-asset-and-regime-transmission":
        return CROSS_ASSET_SIGNALS, "planned", "cross-asset-statistics-candidate-v1", 2
    if theory_family == "fundamental-valuation-and-factors":
        return VALUATION_SIGNALS, "planned", "valuation-statistics-candidate-v1", 4
    if theory_family == "event-information-diffusion":
        return EVENT_SIGNALS, "planned", "event-response-statistics-candidate-v1", 5
    if theory_family == "authored-investment-thesis":
        if "bitcoin" in rule_id or "crypto" in rule_id or "rate_sensitivity" in rule_id:
            return CROSS_ASSET_SIGNALS, "planned", "cross-asset-statistics-candidate-v1", 2
        if "leveraged_flow" in rule_id:
            return FLOW_SIGNALS, "planned", "flow-statistics-candidate-v1", 3
        return PRICE_TREND_SIGNALS, "planned", "authored-thesis-statistics-candidate-v1", 6
    return (), "unmapped", "", 9


def rule_statistical_signal_contract(rule: object) -> Dict[str, object]:
    rule_id = _rule_id(rule)
    basis = _knowledge_basis(rule)
    rule_kind = _text(basis.get("ruleKind"))
    theory_family = _text(basis.get("theoryFamily"))
    if rule_kind != "predictive-hypothesis":
        return {
            "version": RULE_SIGNAL_CONTRACT_VERSION,
            "required": False,
            "migrationState": "not-applicable",
            "currentDecisionAuthority": "typedb-contract-rule",
            "signalTypes": [],
            "releaseIds": [],
            "migrationPriority": 0,
        }
    signal_types, state, release_id, priority = _signal_mapping(rule_id, theory_family)
    return {
        "version": RULE_SIGNAL_CONTRACT_VERSION,
        "required": True,
        "migrationState": state,
        "currentDecisionAuthority": "typedb-raw-fact-rule",
        "candidateDecisionAuthority": "typedb-model-signal-rule",
        "signalTypes": list(signal_types),
        "releaseIds": [release_id] if release_id else [],
        "migrationPriority": priority,
        "productionEligible": False,
        "shadowOnly": True,
        "promotionGates": [
            "point-in-time-replay-complete",
            "minimum-outcome-sample-count-met",
            "probability-calibration-approved",
            "economic-utility-not-worse",
            "no-action-envelope-regression",
            "latency-slo-not-worse",
        ],
    }


def statistical_signal_reverse_index(rules: Iterable[object]) -> Dict[str, object]:
    by_signal = {}
    by_state = {}
    for rule in rules or []:
        rule_id = _rule_id(rule)
        contract = rule_statistical_signal_contract(rule)
        state = str(contract.get("migrationState") or "unknown")
        by_state.setdefault(state, []).append(rule_id)
        for signal_type in contract.get("signalTypes") or []:
            by_signal.setdefault(str(signal_type), []).append(rule_id)
    return {
        "version": RULE_SIGNAL_CONTRACT_VERSION,
        "shadowBySignalType": {
            key: sorted(set(values))
            for key, values in sorted(by_signal.items())
        },
        "byMigrationState": {
            key: sorted(set(values))
            for key, values in sorted(by_state.items())
        },
    }
