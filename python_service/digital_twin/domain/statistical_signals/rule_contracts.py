"""Map predictive RuleBox hypotheses to governed statistical-signal families.

This catalog is routing and migration metadata only. It never evaluates a
TypeDB condition or changes the current action envelope.
"""

from typing import Dict, Iterable, Mapping

from .registry import (
    DEFAULT_AUTHORED_THESIS_SIGNAL_RELEASE_ID,
    DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID,
    DEFAULT_EVENT_SIGNAL_RELEASE_ID,
    DEFAULT_FLOW_SIGNAL_RELEASE_ID,
    DEFAULT_PRICE_SIGNAL_RELEASE_ID,
    DEFAULT_VALUATION_SIGNAL_RELEASE_ID,
    model_release,
)


RULE_SIGNAL_CONTRACT_VERSION = "rule-statistical-signal-contract-v2"

# A production release may emit a broad signal family, but that does not prove
# every historical rule in the same theory family. Keep this mapping explicit
# so a trend-break score cannot silently become an event-cluster, failed-
# rebound, or support-rejection fact.
PRODUCTION_RULE_SIGNAL_TYPES = {
    "graph.loss_guard.breakdown.v1": "price-trend-break-risk",
    "graph.holding.trend_transition.risk.v1": "price-trend-break-risk",
    "graph.profit_protect.trend_break.v1": "price-trend-break-risk",
    "graph.temporal.downside_acceleration.risk.v1": "price-downside-acceleration-risk",
    "graph.loss_rebound.trim_moderation.v1": (
        "price-recovery-support",
        "flow-accumulation-support",
    ),
    "graph.aggressive.loss_recovery.add_buy_review.v1": (
        "price-recovery-support",
        "flow-accumulation-support",
    ),
    "graph.watchlist.temporal.recovery_entry.v1": "price-recovery-support",
    "graph.profit_momentum.hold_add_review.v1": "price-trend-continuation-support",
    "graph.winner_momentum.add_buy_review.v1": (
        "price-trend-continuation-support",
        "flow-accumulation-support",
    ),
    "graph.watchlist.direct_momentum.entry.v1": "price-trend-continuation-support",
    "graph.watchlist.trend_transition.support.v1": "price-trend-continuation-support",
    "graph.investor_flow.smart_money_accumulation.v1": "flow-accumulation-support",
    "graph.loss_smart_money.defense.v1": "flow-accumulation-support",
    "graph.flow.accumulation.entry.v1": "flow-accumulation-support",
    "graph.flow.sell_pressure.v1": "flow-distribution-risk",
    "graph.flow.price_up_smart_money_outflow.divergence.v1": "flow-price-divergence-risk",
    "graph.profit_harvest.path_deceleration.v1": "price-trend-break-risk",
    "graph.profit_protect.short_term_recovery.counter.v1": "price-recovery-support",
    "graph.instrument_profile.bitcoin_sensitive.crypto_linkage.v1": "cross-asset-residual-support",
    "graph.instrument_profile.preferred_income.rate_sensitivity.v1": "regime-transition-risk",
    "graph.security_line.leveraged_flow_amplification.v1": "flow-distribution-risk",
    "graph.watchlist.pullback.entry.v1": "price-recovery-support",
    "graph.temporal.weakness_accumulation.defense.v1": "price-recovery-support",
    "graph.temporal.risk_event_absorption.support.v1": "price-recovery-support",
    "graph.valuation.high_beta_or_expensive.review.v1": "valuation-relative-stretch-risk",
}

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


def model_signal_type_for_rule(rule_id: object, theory_family: object) -> str:
    """Return one auditable family signal for a predictive hypothesis contract.

    The exact RuleBox rule id is carried separately on the emitted model
    evidence. A broad signal therefore cannot prove another rule merely
    because both rules belong to the same theory family.
    """

    identifier = _text(rule_id).lower()
    theory = _text(theory_family)
    configured = PRODUCTION_RULE_SIGNAL_TYPES.get(_text(rule_id))
    if configured:
        return configured if isinstance(configured, str) else tuple(configured)[0]
    risk = any(value in identifier for value in (
        "risk", "break", "failure", "failed", "outflow", "sell_pressure",
        "distribution", "dilution", "stretch", "decline", "underperformance",
        "fragile", "trap", "unsupported", "negative", "inversion",
    ))
    if theory == "market-microstructure-and-investor-flow":
        if "divergence" in identifier or "price_up" in identifier:
            return "flow-price-divergence-risk"
        return "flow-distribution-risk" if risk else "flow-accumulation-support"
    if theory == "cross-asset-and-regime-transmission":
        if any(value in identifier for value in ("regime", "inversion", "volatility")):
            return "regime-transition-risk"
        return "cross-asset-residual-risk" if risk else "cross-asset-residual-support"
    if theory == "fundamental-valuation-and-factors":
        return "valuation-relative-stretch-risk" if risk else "valuation-relative-opportunity"
    if theory == "event-information-diffusion":
        if risk:
            return "event-abnormal-return-risk"
        if any(value in identifier for value in ("support", "surprise")):
            return "event-abnormal-return-support"
        return "event-response-persistence"
    if any(value in identifier for value in ("acceleration", "persistent_decline", "weakness_accumulation")):
        return "price-downside-acceleration-risk"
    if any(value in identifier for value in ("break", "failure", "failed", "distribution", "protect", "risk")):
        return "price-trend-break-risk"
    if any(value in identifier for value in ("rebound", "recovery", "reclaim", "reversal", "deceleration")):
        return "price-recovery-support"
    return "price-trend-continuation-support"


def _signal_mapping(rule_id: str, theory_family: str):
    signal_type = model_signal_type_for_rule(rule_id, theory_family)
    if not signal_type:
        return (), "unmapped", (), 9
    if theory_family == "market-microstructure-and-investor-flow":
        release_id, priority = DEFAULT_FLOW_SIGNAL_RELEASE_ID, 3
    elif theory_family == "cross-asset-and-regime-transmission":
        release_id, priority = DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID, 2
    elif theory_family == "fundamental-valuation-and-factors":
        release_id, priority = DEFAULT_VALUATION_SIGNAL_RELEASE_ID, 4
    elif theory_family == "event-information-diffusion":
        release_id, priority = DEFAULT_EVENT_SIGNAL_RELEASE_ID, 5
    elif theory_family == "authored-investment-thesis":
        if "bitcoin" in rule_id or "crypto" in rule_id or "rate_sensitivity" in rule_id:
            release_id, priority = DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID, 2
        elif "leveraged_flow" in rule_id:
            release_id, priority = DEFAULT_FLOW_SIGNAL_RELEASE_ID, 3
        else:
            release_id, priority = DEFAULT_AUTHORED_THESIS_SIGNAL_RELEASE_ID, 6
    else:
        release_id, priority = DEFAULT_PRICE_SIGNAL_RELEASE_ID, 1
    return (signal_type,), "model-signal-production", (release_id,), priority


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
    signal_types, state, release_ids, priority = _signal_mapping(rule_id, theory_family)
    releases = [model_release(release_id) for release_id in release_ids]
    release_by_type = {
        signal_type: (
            str(release_ids[0])
            if len(release_ids) == 1
            else DEFAULT_FLOW_SIGNAL_RELEASE_ID
            if signal_type in FLOW_SIGNALS
            else DEFAULT_PRICE_SIGNAL_RELEASE_ID
        )
        for signal_type in signal_types
    }
    promotion_blockers = []
    if any(
        signal_type not in release.signal_types
        for signal_type, release in zip(signal_types, releases)
    ):
        promotion_blockers.append("signal-type-not-declared-by-model-release")
    if any(release.status != "production" for release in releases):
        promotion_blockers.append("model-release-not-production")
    if any(
        release.validation_status not in {"calibrated", "validated-deterministic"}
        for release in releases
    ):
        promotion_blockers.append("point-in-time-replay-and-calibration-required")
    if any(release.decision_eligibility not in {"eligible", "conditional"} for release in releases):
        promotion_blockers.append("model-release-reference-only")
    production_eligible = bool(
        state == "model-signal-production"
        and releases
        and all(release.status == "production" for release in releases)
        and all(
            release.validation_status in {"calibrated", "validated-deterministic"}
            for release in releases
        )
        and all(release.decision_eligibility in {"eligible", "conditional"} for release in releases)
        and not promotion_blockers
    )
    return {
        "version": RULE_SIGNAL_CONTRACT_VERSION,
        "required": True,
        "migrationState": state,
        "currentDecisionAuthority": (
            "typedb-model-signal-rule" if production_eligible
            else "disabled-awaiting-model-signal"
        ),
        "candidateDecisionAuthority": "typedb-model-signal-rule",
        "hypothesisContractBinding": "exact-rule-id",
        "hypothesisContractId": rule_id,
        "signalTypes": list(signal_types),
        "releaseIds": list(release_ids),
        "signalReleaseIdsByType": release_by_type,
        "releaseStatus": (
            releases[0].status
            if releases and len({release.status for release in releases}) == 1
            else "mixed" if releases else "unmapped"
        ),
        "releaseValidationStatus": (
            releases[0].validation_status
            if releases and len({release.validation_status for release in releases}) == 1
            else "mixed" if releases else "unmapped"
        ),
        "releaseDecisionEligibility": (
            releases[0].decision_eligibility
            if releases and len({release.decision_eligibility for release in releases}) == 1
            else "mixed" if releases else "reference-only"
        ),
        "signalAvailability": "implemented" if state == "model-signal-production" else "missing",
        "migrationPriority": priority,
        "productionEligible": production_eligible,
        "shadowOnly": not production_eligible,
        "promotionGates": [
            "point-in-time-input-contract-verified",
            "deterministic-contract-replay-approved",
            "outcome-monitoring-active",
            "no-action-envelope-regression",
            "latency-slo-not-worse",
        ],
        "promotionBlockers": promotion_blockers,
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
