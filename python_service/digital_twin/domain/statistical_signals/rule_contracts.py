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


RULE_SIGNAL_CONTRACT_VERSION = "rule-statistical-signal-contract-v1"

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


def _signal_mapping(rule_id: str, theory_family: str):
    production_signal_types = PRODUCTION_RULE_SIGNAL_TYPES.get(rule_id)
    if production_signal_types:
        if isinstance(production_signal_types, str):
            production_signal_types = (production_signal_types,)
        release_ids = tuple(dict.fromkeys(
            DEFAULT_FLOW_SIGNAL_RELEASE_ID
            if signal_type in FLOW_SIGNALS
            else DEFAULT_PRICE_SIGNAL_RELEASE_ID
            for signal_type in production_signal_types
        ))
        return tuple(production_signal_types), "model-signal-production", release_ids, 1
    if theory_family in {"behavioral-momentum-and-trend", "behavioral-mean-reversion"}:
        return PRICE_TREND_SIGNALS, "shadow-signal-required", (DEFAULT_PRICE_SIGNAL_RELEASE_ID,), 1
    if theory_family == "market-microstructure-and-investor-flow":
        return FLOW_SIGNALS, "shadow-signal-required", (DEFAULT_FLOW_SIGNAL_RELEASE_ID,), 3
    if theory_family == "cross-asset-and-regime-transmission":
        return CROSS_ASSET_SIGNALS, "shadow-signal-required", (DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID,), 2
    if theory_family == "fundamental-valuation-and-factors":
        return VALUATION_SIGNALS, "shadow-signal-required", (DEFAULT_VALUATION_SIGNAL_RELEASE_ID,), 4
    if theory_family == "event-information-diffusion":
        return EVENT_SIGNALS, "shadow-signal-required", (DEFAULT_EVENT_SIGNAL_RELEASE_ID,), 5
    if theory_family == "authored-investment-thesis":
        if "bitcoin" in rule_id or "crypto" in rule_id or "rate_sensitivity" in rule_id:
            return CROSS_ASSET_SIGNALS, "shadow-signal-required", (DEFAULT_CROSS_ASSET_SIGNAL_RELEASE_ID,), 2
        if "leveraged_flow" in rule_id:
            return FLOW_SIGNALS, "shadow-signal-required", (DEFAULT_FLOW_SIGNAL_RELEASE_ID,), 3
        return PRICE_TREND_SIGNALS, "shadow-signal-required", (DEFAULT_AUTHORED_THESIS_SIGNAL_RELEASE_ID,), 6
    return (), "unmapped", (), 9


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
    if state == "shadow-signal-required":
        promotion_blockers.append("governed-scorer-not-implemented")
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
            "point-in-time-replay-complete",
            "minimum-outcome-sample-count-met",
            "probability-calibration-approved",
            "economic-utility-not-worse",
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
