"""Immutable RuleBox release migration metadata.

These identifiers describe historical catalog upgrades only. They are never
consulted to decide whether an investment rule matches, how it is ordered, or
whether an alert is sent. Keeping the manifest in the domain control-plane
boundary prevents infrastructure code from becoming a second RuleBox.
"""

from typing import Dict, FrozenSet


RULEBOX_RELEASE_MANIFEST_VERSION = "rulebox-release-manifest-v5"

DEPRECATED_TYPEDB_RULE_IDS: FrozenSet[str] = frozenset({
    "shadow.market_psychology.state.v1",
    # Replaced by change-and-response rules. The old rule treated a static
    # rate level as a recurring stock risk and therefore over-selected macro.
    "graph.macro.regime.risk.v1",
})

RATE_MACRO_RULE_IDS: FrozenSet[str] = frozenset({
    "graph.macro.rate.rise.confirmed_risk.v1",
    "graph.macro.rate.fall.confirmed_support.v1",
    "graph.macro.rate.high_regime_entry.risk.v1",
    "graph.macro.rate.stock_divergence.support.v1",
    "graph.macro.curve.inversion_entry.risk.v1",
})

CRYPTO_MARKET_RULE_IDS: FrozenSet[str] = frozenset({
    "graph.crypto.market.24h.up.watch.v1",
    "graph.crypto.market.24h.down.watch.v1",
    "graph.crypto.market.7d.up.watch.v1",
    "graph.crypto.market.7d.down.watch.v1",
    "graph.crypto.market.24h.up.major.v1",
    "graph.crypto.market.24h.down.major.v1",
    "graph.crypto.market.7d.up.major.v1",
    "graph.crypto.market.7d.down.major.v1",
})

# The listed rules changed from Python-classified relations to raw ABox facts.
# A persisted older shape must be replaced once; administrator enable/disable
# state is preserved by the generic migration use case.
RULEBOX_RAW_ABOX_RUNTIME_RULE_IDS: FrozenSet[str] = frozenset({
    "graph.loss_guard.breakdown.v1",
    "graph.loss_smart_money.defense.v1",
    "graph.investor_flow.smart_money_accumulation.v1",
    "graph.investor_flow.retail_dip_buying_risk.v1",
    "graph.investor_flow.smart_money_outflow_risk.v1",
    "graph.loss_smart_money.add_buy_review.v1",
    "graph.averaging_down.risk_guard.v1",
    "graph.profit_protect.trend_break.v1",
    "graph.winner_momentum.add_buy_review.v1",
    "graph.watchlist.pullback.entry.v1",
    "graph.price.recovery.confirmed_by_flow.v1",
    "graph.price.rebound.failure.v1",
    "graph.flow.recovery_confirmed_by_smart_money.v1",
    "graph.flow.price_up_smart_money_outflow.divergence.v1",
    "graph.news.price_reaction.risk_confirmed.v1",
    "graph.materiality.alert_candidate.v1",
    "graph.holding.trend_transition.risk.v1",
    "graph.watchlist.trend_transition.support.v1",
    "graph.market_proxy.observation.risk_context.v1",
    "graph.market_proxy.observation.support_context.v1",
    "graph.portfolio.concentration.review.v1",
    "graph.price.reclaim.thesis_support.v1",
    "graph.instrument_profile.preferred_income.rate_sensitivity.v1",
    *RATE_MACRO_RULE_IDS,
    "graph.crypto.exposure.volatility_risk.v1",
    *CRYPTO_MARKET_RULE_IDS,
    "graph.liquidity.execution_guard.v1",
    "graph.execution.liquidity_or_slippage_block.v1",
    "graph.execution.capacity_safe.v1",
})

# Portfolio allocation constraints moved to dedicated rebalance rules. These
# symbol decision rules now consume only instrument-market facts and need one
# persisted RuleBox replacement while preserving administrator enable state.
RULEBOX_DECISION_SCOPE_RULE_IDS: FrozenSet[str] = frozenset({
    "graph.aggressive.loss_recovery.add_buy_review.v1",
    "graph.profit_momentum.hold_add_review.v1",
    "graph.instrument_profile.strategy_fit.support.v1",
    "graph.strategy_profile.loss_tolerance_breach.v1",
    "graph.strategy_profile.aggressive_recovery_room.v1",
})

# Market/provider capability guards became part of these persisted native
# rules. Older rows can mistake an unsupported feed for a neutral value, so
# this is a runtime contract migration rather than a presentation-only edit.
RULEBOX_MARKET_EVIDENCE_GUARD_RULE_VERSIONS: Dict[str, str] = {
    "graph.loss_smart_money.defense.v1": "v3",
    "graph.investor_flow.smart_money_accumulation.v1": "v3",
    "graph.investor_flow.retail_dip_buying_risk.v1": "v3",
    "graph.investor_flow.smart_money_outflow_risk.v1": "v3",
    "graph.loss_smart_money.add_buy_review.v1": "v3",
    "graph.loss_rebound.trim_moderation.v1": "v2",
    "graph.aggressive.loss_recovery.add_buy_review.v1": "v3",
    "graph.averaging_down.risk_guard.v1": "v3",
    "graph.winner_momentum.add_buy_review.v1": "v3",
    "graph.profit_momentum.hold_add_review.v1": "v5",
    "graph.instrument_profile.cyclical_growth.recovery_add_review.v1": "v2",
    "graph.strategy_profile.aggressive_recovery_room.v1": "v3",
    "graph.flow.sell_pressure.v1": "v2",
    "graph.flow.accumulation.entry.v1": "v2",
    "graph.price.recovery.confirmed_by_flow.v1": "v3",
    "graph.price.rebound.failure.v1": "v3",
    "graph.flow.recovery_confirmed_by_smart_money.v1": "v3",
    "graph.flow.price_up_smart_money_outflow.divergence.v1": "v3",
    "graph.watchlist.direct_momentum.entry.v1": "v2",
    "graph.news.price_reaction.risk_confirmed.v1": "v3",
}
RULEBOX_MARKET_EVIDENCE_GUARD_RULE_IDS: FrozenSet[str] = frozenset(
    RULEBOX_MARKET_EVIDENCE_GUARD_RULE_VERSIONS
)

# Partial evidence gaps used to inherit the historical DATA_CONFLICT=block
# policy in persisted RuleBox rows. They now constrain only the evidence they
# describe. The primary quote-failure derivation remains an explicit block.
# Versioning these rows makes the persisted TypeDB catalog receive the change
# once without treating presentation metadata as runtime policy.
RULEBOX_DECISION_EFFECT_CONTRACT_RULE_VERSIONS: Dict[str, str] = {
    "graph.temporal.stale_observation.block.v1": "v2",
    "graph.temporal.coverage_gap.v1": "v2",
    "graph.security_line.coverage_gap.v1": "v2",
    "graph.news.quality.validation_state.v1": "v2",
    "graph.news.ai_body_missing_review.v1": "v2",
    "graph.data_quality.news_analysis_conflict.v1": "v2",
    "graph.data_quality.microstructure_gap.v1": "v2",
    "graph.data_quality.market_snapshot_failure_block.v1": "v2",
    "graph.data_quality.market_snapshot_degraded.v1": "v2",
    "graph.coverage.gap.validation_state.v1": "v2",
}
RULEBOX_DECISION_EFFECT_CONTRACT_RULE_IDS: FrozenSet[str] = frozenset(
    RULEBOX_DECISION_EFFECT_CONTRACT_RULE_VERSIONS
)

RULEBOX_RUNTIME_CONTRACT_RULE_IDS: FrozenSet[str] = frozenset(
    RULEBOX_RAW_ABOX_RUNTIME_RULE_IDS
    | RULEBOX_DECISION_SCOPE_RULE_IDS
    | RULEBOX_MARKET_EVIDENCE_GUARD_RULE_IDS
    | RULEBOX_DECISION_EFFECT_CONTRACT_RULE_IDS
)

RULEBOX_RAW_ABOX_RUNTIME_RULE_VERSIONS: Dict[str, str] = {
    rule_id: "v2"
    for rule_id in RULEBOX_RAW_ABOX_RUNTIME_RULE_IDS
}
RULEBOX_RAW_ABOX_RUNTIME_RULE_VERSIONS["graph.execution.capacity_safe.v1"] = "v3"
RULEBOX_RAW_ABOX_RUNTIME_RULE_VERSIONS["graph.price.reclaim.thesis_support.v1"] = "v3"
RULEBOX_RAW_ABOX_RUNTIME_RULE_VERSIONS["graph.portfolio.concentration.review.v1"] = "v3"
for _rule_id in CRYPTO_MARKET_RULE_IDS | RATE_MACRO_RULE_IDS:
    RULEBOX_RAW_ABOX_RUNTIME_RULE_VERSIONS[_rule_id] = "v1"
for _rule_id in CRYPTO_MARKET_RULE_IDS:
    RULEBOX_RAW_ABOX_RUNTIME_RULE_VERSIONS[_rule_id] = "v2"

RULEBOX_RUNTIME_CONTRACT_RULE_VERSIONS: Dict[str, str] = {
    **RULEBOX_RAW_ABOX_RUNTIME_RULE_VERSIONS,
    **{rule_id: "v2" for rule_id in RULEBOX_DECISION_SCOPE_RULE_IDS},
    **RULEBOX_MARKET_EVIDENCE_GUARD_RULE_VERSIONS,
    **RULEBOX_DECISION_EFFECT_CONTRACT_RULE_VERSIONS,
}

# Rules introduced after TypeDB became the persisted source of truth. Missing
# platform rules are appended without replacing edited or disabled rows.
RULEBOX_PLATFORM_RELEASE_ADDITION_IDS: FrozenSet[str] = frozenset({
    *RATE_MACRO_RULE_IDS,
    "graph.fx.usdkrw.exposure.regime.v1",
    "graph.crypto.exposure.volatility_risk.v1",
    *CRYPTO_MARKET_RULE_IDS,
    "graph.earnings.surprise.risk.v1",
    "graph.earnings.surprise.support.v1",
    "graph.regulatory.event.risk.v1",
    "graph.temporal.intraday_downside_acceleration.risk.v1",
    "graph.temporal.intraday_reversal.defense.v1",
    "graph.temporal.risk_event_absorption.support.v1",
    "graph.temporal.support_event_rejection.risk.v1",
    "graph.market_proxy.relative_underperformance.risk.v1",
    "graph.market_proxy.relative_resilience.support.v1",
})


def rulebox_release_manifest() -> Dict[str, object]:
    """Return a public, read-only migration summary without decision policy."""

    return {
        "version": RULEBOX_RELEASE_MANIFEST_VERSION,
        "deprecatedRuleIds": sorted(DEPRECATED_TYPEDB_RULE_IDS),
        "rawAboxContractRuleIds": sorted(RULEBOX_RAW_ABOX_RUNTIME_RULE_IDS),
        "decisionScopeContractRuleIds": sorted(RULEBOX_DECISION_SCOPE_RULE_IDS),
        "marketEvidenceGuardRuleIds": sorted(RULEBOX_MARKET_EVIDENCE_GUARD_RULE_IDS),
        "decisionEffectContractRuleIds": sorted(RULEBOX_DECISION_EFFECT_CONTRACT_RULE_IDS),
        "platformAdditionRuleIds": sorted(RULEBOX_PLATFORM_RELEASE_ADDITION_IDS),
        "runtimeDecisionUse": False,
    }
