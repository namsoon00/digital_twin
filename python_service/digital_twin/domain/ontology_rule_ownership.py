"""Explicit bounded-context ownership for every production RuleBox rule.

Rule ownership is governance data, not an inference shortcut.  Exact IDs are
listed deliberately so a renamed or newly added rule fails closed instead of
being classified from tokens in its name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Tuple


RULE_OWNERSHIP_CONTRACT_VERSION = "ontology-rule-ownership-v1"
RULE_OWNERS = frozenset({
    "statistical-model",
    "market-observation",
    "ontology-semantic",
    "portfolio-policy",
    "data-quality",
    "trade-execution",
    "notification-policy",
})


@dataclass(frozen=True)
class RuleOwnershipContract:
    owner: str
    rule_kind: str
    input_contract: str
    output_contract: str
    decision_authority: str
    migration_disposition: str
    version: str = RULE_OWNERSHIP_CONTRACT_VERSION

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "owner": self.owner,
            "ruleKind": self.rule_kind,
            "inputContract": self.input_contract,
            "outputContract": self.output_contract,
            "decisionAuthority": self.decision_authority,
            "migrationDisposition": self.migration_disposition,
        }


STATISTICAL_MODEL_RULE_IDS = frozenset({
    "graph.aggressive.loss_recovery.add_buy_review.v1",
    "graph.averaging_down.risk_guard.v1",
    "graph.company.capital.dilution.risk.v1",
    "graph.company.market.forward_expectation.review.v1",
    "graph.company.market.fragile_rally.risk.v1",
    "graph.company.market.fundamental_confirmation.support.v1",
    "graph.company.market.overreaction_candidate.support.v1",
    "graph.company.market.quality_valuation.support.v1",
    "graph.company.market.structural_decline.risk.v1",
    "graph.company.market.unsupported_rerating.risk.v1",
    "graph.company.market.valuation_stretch.risk.v1",
    "graph.company.market.value_trap.risk.v1",
    "graph.cross_listing.adr_discount_risk.v1",
    "graph.cross_listing.adr_premium_risk.v1",
    "graph.crypto.exposure.volatility_risk.v1",
    "graph.disclosure.event_risk.v1",
    "graph.disclosure.financing_or_dilution.risk.v1",
    "graph.earnings.surprise.risk.v1",
    "graph.earnings.surprise.support.v1",
    "graph.flow.accumulation.entry.v1",
    "graph.flow.price_up_smart_money_outflow.divergence.v1",
    "graph.flow.recovery_confirmed_by_smart_money.v1",
    "graph.flow.sell_pressure.v1",
    "graph.fx.usdkrw.exposure.regime.v1",
    "graph.holding.trend_transition.risk.v1",
    "graph.instrument_profile.bitcoin_sensitive.crypto_linkage.v1",
    "graph.instrument_profile.cyclical_growth.recovery_add_review.v1",
    "graph.instrument_profile.preferred_income.rate_sensitivity.v1",
    "graph.investor_flow.retail_dip_buying_risk.v1",
    "graph.investor_flow.smart_money_accumulation.v1",
    "graph.investor_flow.smart_money_outflow_risk.v1",
    "graph.loss_guard.breakdown.v1",
    "graph.loss_rebound.trim_moderation.v1",
    "graph.loss_smart_money.add_buy_review.v1",
    "graph.loss_smart_money.defense.v1",
    "graph.macro.curve.inversion_entry.risk.v1",
    "graph.macro.rate.fall.confirmed_support.v1",
    "graph.macro.rate.high_regime_entry.risk.v1",
    "graph.macro.rate.rise.confirmed_risk.v1",
    "graph.macro.rate.stock_divergence.support.v1",
    "graph.market_proxy.relative_resilience.support.v1",
    "graph.market_proxy.relative_underperformance.risk.v1",
    "graph.news.ai_direct_risk.v1",
    "graph.news.direct_material_risk.v1",
    "graph.news.direct_material_support.v1",
    "graph.news.price_reaction.risk_confirmed.v1",
    "graph.news.price_reaction.support_confirmed.v1",
    "graph.price.rebound.failure.v1",
    "graph.price.reclaim.thesis_support.v1",
    "graph.price.recovery.confirmed_by_flow.v1",
    "graph.profit_harvest.path_deceleration.v1",
    "graph.profit_momentum.hold_add_review.v1",
    "graph.profit_protect.short_term_recovery.counter.v1",
    "graph.profit_protect.trend_break.v1",
    "graph.regulatory.event.risk.v1",
    "graph.security_line.leveraged_flow_amplification.v1",
    "graph.temporal.bounce_distribution.risk.v1",
    "graph.temporal.decline_deceleration.defense.v1",
    "graph.temporal.downside_acceleration.risk.v1",
    "graph.temporal.event_cluster.risk.v1",
    "graph.temporal.failed_recovery.risk.v1",
    "graph.temporal.intraday_downside_acceleration.risk.v1",
    "graph.temporal.intraday_reversal.defense.v1",
    "graph.temporal.persistent_decline.risk.v1",
    "graph.temporal.risk_event_absorption.support.v1",
    "graph.temporal.support_event_rejection.risk.v1",
    "graph.temporal.weakness_accumulation.defense.v1",
    "graph.valuation.high_beta_or_expensive.review.v1",
    "graph.valuation.margin_of_safety.opportunity.v1",
    "graph.valuation.negative_margin.risk.v1",
    "graph.watchlist.direct_momentum.entry.v1",
    "graph.watchlist.pullback.entry.v1",
    "graph.watchlist.temporal.recovery_entry.v1",
    "graph.watchlist.trend_transition.support.v1",
    "graph.winner_momentum.add_buy_review.v1",
})

MARKET_OBSERVATION_RULE_IDS = frozenset()

ONTOLOGY_SEMANTIC_RULE_IDS = frozenset({
    "graph.benchmark.beta.context.v1",
    "graph.crypto.market.24h.down.major.v1",
    "graph.crypto.market.24h.down.watch.v1",
    "graph.crypto.market.24h.up.major.v1",
    "graph.crypto.market.24h.up.watch.v1",
    "graph.crypto.market.7d.down.major.v1",
    "graph.crypto.market.7d.down.watch.v1",
    "graph.crypto.market.7d.up.major.v1",
    "graph.crypto.market.7d.up.watch.v1",
    "graph.market_proxy.observation.risk_context.v1",
    "graph.market_proxy.observation.support_context.v1",
    "graph.news.direct_material_context.v1",
})

PORTFOLIO_POLICY_RULE_IDS = frozenset({
    "graph.factor.position_crowding.v1",
    "graph.instrument_profile.averaging_down_policy.v1",
    "graph.instrument_profile.strategy_fit.support.v1",
    "graph.instrument_profile.strategy_mismatch.risk.v1",
    "graph.portfolio.activity.concentration.review.v1",
    "graph.portfolio.concentration.review.v1",
    "graph.portfolio.decision_action.divergence.v1",
    "graph.portfolio.rebalance.opened.v1",
    "graph.portfolio.rebalance.resolved.v1",
    "graph.portfolio.rebalance.updated.v1",
    "graph.portfolio.reentry.review.v1",
    "graph.portfolio.repeated_loss_add.guard.v1",
    "graph.portfolio.risk_policy.review.v1",
    "graph.strategy_profile.aggressive_recovery_room.v1",
    "graph.strategy_profile.loss_tolerance_breach.v1",
})

DATA_QUALITY_RULE_IDS = frozenset({
    "graph.company.governance.coverage_gap.v1",
    "graph.coverage.gap.validation_state.v1",
    "graph.data_quality.action_block.v1",
    "graph.data_quality.market_snapshot_degraded.v1",
    "graph.data_quality.market_snapshot_failure_block.v1",
    "graph.data_quality.microstructure_gap.v1",
    "graph.data_quality.news_analysis_conflict.v1",
    "graph.news.ai_body_missing_review.v1",
    "graph.news.quality.validation_state.v1",
    "graph.security_line.coverage_gap.v1",
    "graph.temporal.coverage_gap.v1",
    "graph.temporal.stale_observation.block.v1",
})

TRADE_EXECUTION_RULE_IDS = frozenset({
    "graph.execution.capacity_safe.v1",
    "graph.execution.liquidity_or_slippage_block.v1",
    "graph.liquidity.execution_guard.v1",
})

NOTIFICATION_POLICY_RULE_IDS = frozenset({
    "graph.materiality.alert_candidate.v1",
})


OWNER_RULE_IDS: Mapping[str, frozenset] = {
    "statistical-model": STATISTICAL_MODEL_RULE_IDS,
    "market-observation": MARKET_OBSERVATION_RULE_IDS,
    "ontology-semantic": ONTOLOGY_SEMANTIC_RULE_IDS,
    "portfolio-policy": PORTFOLIO_POLICY_RULE_IDS,
    "data-quality": DATA_QUALITY_RULE_IDS,
    "trade-execution": TRADE_EXECUTION_RULE_IDS,
    "notification-policy": NOTIFICATION_POLICY_RULE_IDS,
}

OWNER_DEFAULTS: Mapping[str, Tuple[str, str, str, str, str]] = {
    "statistical-model": (
        "predictive-hypothesis",
        "point-in-time-feature-snapshot",
        "governed-model-signal",
        "typedb-model-signal-rule",
        "model-signal-production-or-disabled",
    ),
    "market-observation": (
        "context-observation",
        "normalized-market-observation",
        "material-market-event",
        "observation-context-only",
        "move-detector-outside-rulebox",
    ),
    "ontology-semantic": (
        "context-observation",
        "verified-abox-facts",
        "semantic-context-relation",
        "typedb-semantic-context",
        "retain-in-typedb",
    ),
    "portfolio-policy": (
        "policy-constraint",
        "portfolio-state-and-account-mandate",
        "portfolio-action-guardrail",
        "typedb-policy-guardrail",
        "retain-in-typedb",
    ),
    "data-quality": (
        "data-quality-gate",
        "provenance-freshness-and-coverage-state",
        "evidence-eligibility-guardrail",
        "typedb-data-quality-guardrail",
        "retain-in-typedb",
    ),
    "trade-execution": (
        "execution-gate",
        "order-capacity-liquidity-and-slippage",
        "execution-action-guardrail",
        "typedb-execution-guardrail",
        "retain-in-typedb",
    ),
    "notification-policy": (
        "context-observation",
        "material-decision-or-observation-change",
        "notification-delivery-candidate",
        "notification-delivery-only",
        "move-outside-rulebox",
    ),
}


def ownership_rule_ids() -> Tuple[str, ...]:
    return tuple(sorted({rule_id for values in OWNER_RULE_IDS.values() for rule_id in values}))


def rule_ownership_contract(rule_id: object) -> RuleOwnershipContract:
    requested = str(rule_id or "").strip()
    owners = [owner for owner, values in OWNER_RULE_IDS.items() if requested in values]
    if len(owners) != 1:
        reason = "missing" if not owners else "ambiguous"
        raise ValueError("Rule ownership is " + reason + ": " + (requested or "<unknown-rule>"))
    owner = owners[0]
    rule_kind, input_contract, output_contract, authority, disposition = OWNER_DEFAULTS[owner]
    return RuleOwnershipContract(
        owner=owner,
        rule_kind=rule_kind,
        input_contract=input_contract,
        output_contract=output_contract,
        decision_authority=authority,
        migration_disposition=disposition,
    )


def validate_rule_ownership(rule_ids: Iterable[object]) -> Dict[str, object]:
    requested = [str(value or "").strip() for value in rule_ids or [] if str(value or "").strip()]
    duplicates = sorted({value for value in requested if requested.count(value) > 1})
    missing = []
    ambiguous = []
    for rule_id in sorted(set(requested)):
        owners = [owner for owner, values in OWNER_RULE_IDS.items() if rule_id in values]
        if not owners:
            missing.append(rule_id)
        elif len(owners) > 1:
            ambiguous.append(rule_id)
    orphaned = sorted(set(ownership_rule_ids()) - set(requested))
    return {
        "version": RULE_OWNERSHIP_CONTRACT_VERSION,
        "valid": not duplicates and not missing and not ambiguous and not orphaned,
        "ruleCount": len(requested),
        "ownedRuleCount": len(set(requested)) - len(missing) - len(ambiguous),
        "ownerCounts": {
            owner: len(set(requested).intersection(values))
            for owner, values in sorted(OWNER_RULE_IDS.items())
        },
        "duplicateRuleIds": duplicates,
        "missingRuleIds": missing,
        "ambiguousRuleIds": ambiguous,
        "orphanedCatalogRuleIds": orphaned,
    }
