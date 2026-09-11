"""TypeDB static-seed schema owner; no facade or runtime construction."""

from .schema_ports import SchemaStore
from digital_twin.domain.ontology_semantics import (
    SEMANTIC_STORAGE_CONTRACT_VERSION,
    semantic_typeql_schema,
)
from digital_twin.modules.reasoning.infrastructure.typeql.constants import (
    TYPEDB_COMMON_NODE_ATTRIBUTES,
    TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES,
    TYPEDB_PROMOTED_TEXT_ATTRIBUTES,
)
from digital_twin.modules.reasoning.infrastructure.typeql.storage_schema import (
    typedb_rule_schema_capability_contract,
)
from typing import Dict, Iterable
import hashlib
import re


def slim_typeql_node_schema(schema: str) -> str:
    """Replace the universal capability fan-out with bounded-context roots."""
    pattern = re.compile(
        r"entity ontology-node @abstract,\s*(?P<body>.*?)"
        r"\s*plays ontology-assertion:target;\s*\n\s*"
        r"entity ontology-entity, sub ontology-node;\s*\n"
        r"entity ontology-evidence, sub ontology-node;\s*\n"
        r"entity ontology-belief, sub ontology-node;\s*\n"
        r"entity ontology-opinion, sub ontology-node;\s*\n"
        r"entity ontology-reasoning-card, sub ontology-node;",
        re.DOTALL,
    )
    match = pattern.search(str(schema or ""))
    if match is None:
        raise ValueError("The TypeDB ontology-node schema block is unavailable.")
    all_owned = set(re.findall("owns\\s+(ontology-[a-z0-9-]+)", match.group("body")))
    common = sorted(all_owned & TYPEDB_COMMON_NODE_ATTRIBUTES)
    fallback_only = sorted(all_owned - TYPEDB_COMMON_NODE_ATTRIBUTES)

    def ownership(attributes: Iterable[str], unique_storage: bool = False) -> str:
        rows = []
        for attribute in attributes:
            annotation = (
                " @unique"
                if unique_storage and attribute == "ontology-storage-id"
                else ""
            )
            rows.append("    owns " + attribute + annotation)
        return ",\n".join(rows)

    root = (
        "entity ontology-node @abstract,\n"
        + ownership(common, unique_storage=True)
        + ",\n    plays ontology-assertion:source,\n"
        + "    plays ontology-assertion:target;"
    )
    fallback_ownership = ownership(fallback_only)
    fallbacks = []
    for node_type in (
        "ontology-entity",
        "ontology-evidence",
        "ontology-belief",
        "ontology-opinion",
        "ontology-reasoning-card",
    ):
        clause = "entity " + node_type + ", sub ontology-node"
        if fallback_ownership:
            clause += ",\n" + fallback_ownership
        fallbacks.append(clause + ";")
    return (
        schema[: match.start()]
        + root
        + "\n"
        + "\n".join(fallbacks)
        + schema[match.end() :]
    )


def schema_query(_store: SchemaStore) -> str:
    schema = """
define
attribute ontology-id, value string;
attribute ontology-storage-id, value string;
attribute ontology-content-fingerprint, value string;
attribute ontology-label, value string;
attribute ontology-kind, value string;
attribute ontology-box, value string;
attribute ontology-symbol, value string;
attribute ontology-rule-id, value string;
attribute ontology-account-id, value string;
attribute ontology-tenant-id, value string;
attribute ontology-world-id, value string;
attribute ontology-world-type, value string;
attribute ontology-snapshot-id, value string;
attribute ontology-scope-id, value string;
attribute ontology-scope-type, value string;
attribute ontology-manifest-id, value string;
attribute ontology-tbox-class, value string;
attribute ontology-semantic-type, value string;
attribute ontology-relation-type, value string;
attribute ontology-updated-at, value string;
attribute ontology-json, value string;
attribute ontology-weight, value double;
attribute ontology-source-value, value string;
attribute ontology-field, value string;
attribute ontology-level-type, value string;
attribute ontology-data-scope, value string;
attribute ontology-domain-scope, value string;
attribute ontology-relation-scope, value string;
attribute ontology-group, value string;
attribute ontology-polarity, value string;
attribute ontology-evidence-role, value string;
attribute ontology-review-level, value string;
attribute ontology-data-state, value string;
attribute ontology-change-state, value string;
attribute ontology-conflict-state, value string;
attribute ontology-validation-state, value string;
attribute ontology-transition-type, value string;
attribute ontology-signal-group, value string;
attribute ontology-event-type, value string;
attribute ontology-materiality-passed, value string;
attribute ontology-materiality-state, value string;
attribute ontology-relevance-state, value string;
attribute ontology-source-trust-state, value string;
attribute ontology-value-number, value double;
attribute ontology-profit-loss-rate, value double;
attribute ontology-allow-add-on-strength, value string;
attribute ontology-trim-on-trend-break, value string;
attribute ontology-avoid-averaging-down, value string;
attribute ontology-impact-polarity, value string;
attribute ontology-needs-review, value string;
attribute ontology-read-scope, value string;
attribute ontology-pe-ratio, value double;
attribute ontology-beta, value double;
attribute ontology-delta, value double;
attribute ontology-delta-pct, value double;
attribute ontology-delta-bp, value double;
attribute ontology-previous-value, value double;
attribute ontology-delta-1d-bp, value double;
attribute ontology-delta-5d-bp, value double;
attribute ontology-delta-20d-bp, value double;
attribute ontology-change-24h, value double;
attribute ontology-change-7d, value double;
attribute ontology-surprise-percentage, value double;
attribute ontology-current-price, value double;
attribute ontology-average-price, value double;
attribute ontology-market-value, value double;
attribute ontology-quantity, value double;
attribute ontology-sellable-quantity, value double;
attribute ontology-position-weight-pct, value double;
attribute ontology-position-account-weight-pct, value double;
attribute ontology-exposure-ratio, value double;
attribute ontology-position-count, value double;
attribute ontology-change-rate, value double;
attribute ontology-price-change-rate, value double;
attribute ontology-ma5, value double;
attribute ontology-ma20, value double;
attribute ontology-ma60, value double;
attribute ontology-ma5-distance, value double;
attribute ontology-ma20-distance, value double;
attribute ontology-ma60-distance, value double;
attribute ontology-ma20-slope, value double;
attribute ontology-ma60-slope, value double;
attribute ontology-trend-curve, value double;
attribute ontology-volume, value double;
attribute ontology-volume-ratio, value double;
attribute ontology-raw-volume-ratio, value double;
attribute ontology-time-adjusted-volume-ratio, value double;
attribute ontology-expected-volume-ratio-now, value double;
attribute ontology-trade-strength, value double;
attribute ontology-trading-value, value double;
attribute ontology-reported-trading-value, value double;
attribute ontology-estimated-trading-value, value double;
attribute ontology-trading-value-mismatch-pct, value double;
attribute ontology-trading-value-quality, value string;
attribute ontology-trading-value-basis, value string;
attribute ontology-bid-ask-imbalance, value double;
attribute ontology-foreign-net-volume, value double;
attribute ontology-foreign-net-amount, value double;
attribute ontology-institution-net-volume, value double;
attribute ontology-institution-net-amount, value double;
attribute ontology-individual-net-volume, value double;
attribute ontology-individual-net-amount, value double;
attribute ontology-smart-money-net-volume, value double;
attribute ontology-adr-ratio, value double;
attribute ontology-adr-price-usd, value double;
attribute ontology-adr-volume, value double;
attribute ontology-usd-krw-rate, value double;
attribute ontology-local-price-krw, value double;
attribute ontology-local-equivalent-krw, value double;
attribute ontology-leverage-factor, value double;
attribute ontology-price, value double;
attribute ontology-fair-value, value double;
attribute ontology-fair-value-price, value double;
attribute ontology-fair-value-low, value double;
attribute ontology-fair-value-base, value double;
attribute ontology-fair-value-high, value double;
attribute ontology-margin-of-safety-pct, value double;
attribute ontology-conservative-margin-of-safety-pct, value double;
attribute ontology-optimistic-margin-of-safety-pct, value double;
attribute ontology-expensive-premium-pct, value double;
attribute ontology-minimum-margin-of-safety-pct, value double;
attribute ontology-valuation-decision-eligible, value double;
attribute ontology-valuation-model-count, value double;
attribute ontology-valuation-consensus-price, value double;
attribute ontology-valuation-disagreement-pct, value double;
attribute ontology-expected-eps, value double;
attribute ontology-reported-eps, value double;
attribute ontology-estimated-eps, value double;
attribute ontology-target-per, value double;
attribute ontology-forward-pe, value double;
attribute ontology-peg-ratio, value double;
attribute ontology-dividend-yield, value double;
attribute ontology-peer-per, value double;
attribute ontology-historical-median-per, value double;
attribute ontology-lookback-days, value double;
attribute ontology-required-sample-count, value double;
attribute ontology-sample-count, value double;
attribute ontology-coverage-ratio, value double;
attribute ontology-elapsed-hours, value double;
attribute ontology-start-price, value double;
attribute ontology-price-change-pct, value double;
attribute ontology-relative-return-pct, value double;
attribute ontology-proxy-change-rate, value double;
attribute ontology-peak-price, value double;
attribute ontology-trough-price, value double;
attribute ontology-peak-return-pct, value double;
attribute ontology-trough-return-pct, value double;
attribute ontology-drawdown-from-peak-pct, value double;
attribute ontology-rebound-from-trough-pct, value double;
attribute ontology-prior-price-change-pct, value double;
attribute ontology-recent-price-change-pct, value double;
attribute ontology-price-velocity-change-pct, value double;
attribute ontology-consecutive-decline-count, value double;
attribute ontology-consecutive-advance-count, value double;
attribute ontology-direction-change-count, value double;
attribute ontology-valid-observation-count, value double;
attribute ontology-invalid-observation-count, value double;
attribute ontology-stale-observation-count, value double;
attribute ontology-valid-observation-ratio, value double;
attribute ontology-profit-loss-rate-start, value double;
attribute ontology-profit-loss-rate-end, value double;
attribute ontology-profit-loss-rate-change-pct, value double;
attribute ontology-ma20-distance-start, value double;
attribute ontology-ma20-distance-end, value double;
attribute ontology-ma20-distance-change, value double;
attribute ontology-ma20-distance-peak, value double;
attribute ontology-ma20-distance-trough, value double;
attribute ontology-ma20-reclaim-count, value double;
attribute ontology-ma20-break-count, value double;
attribute ontology-ma20-observation-count, value double;
attribute ontology-ma60-distance-start, value double;
attribute ontology-ma60-distance-end, value double;
attribute ontology-volume-ratio-end, value double;
attribute ontology-trade-strength-end, value double;
attribute ontology-bid-ask-imbalance-end, value double;
attribute ontology-smart-money-net-latest, value double;
attribute ontology-smart-money-net-change, value double;
attribute ontology-smart-money-net-cumulative, value double;
attribute ontology-smart-money-net-amount-cumulative, value double;
attribute ontology-smart-money-trading-value-ratio-pct, value double;
attribute ontology-smart-money-positive-session-ratio, value double;
attribute ontology-smart-money-negative-session-ratio, value double;
attribute ontology-smart-money-flow-persistence-ratio, value double;
attribute ontology-smart-money-flow-acceleration, value double;
attribute ontology-smart-money-observation-count, value double;
attribute ontology-smart-money-distinct-observation-count, value double;
attribute ontology-smart-money-distinct-session-count, value double;
attribute ontology-individual-net-latest, value double;
attribute ontology-event-count, value double;
attribute ontology-risk-event-count, value double;
attribute ontology-support-event-count, value double;
attribute ontology-investment-strategy-profile, value string;
attribute ontology-investment-strategy-profile-label, value string;
attribute ontology-position-role, value string;
attribute ontology-target-position-role, value string;
attribute ontology-position-intent, value string;
attribute ontology-position-intent-label, value string;
attribute ontology-position-intent-description, value string;
attribute ontology-instrument-archetype, value string;
attribute ontology-instrument-archetype-label, value string;
attribute ontology-factor, value string;
attribute ontology-sensitivity-level, value string;
attribute ontology-rate-series-id, value string;
attribute ontology-observation-date, value string;
attribute ontology-previous-observation-date, value string;
attribute ontology-source-as-of, value string;
attribute ontology-change-basis, value string;
attribute ontology-crypto-symbol, value string;
attribute ontology-fx-pair, value string;
attribute ontology-action-policy, value string;
attribute ontology-security-line-role, value string;
attribute ontology-local-symbol, value string;
attribute ontology-company-name, value string;
attribute ontology-market, value string;
attribute ontology-currency, value string;
attribute ontology-exchange, value string;
attribute ontology-adr-symbol, value string;
attribute ontology-etf-symbol, value string;
attribute ontology-underlying-symbol, value string;
attribute ontology-conversion-start-date, value string;
attribute ontology-listing-date, value string;
attribute ontology-source-url, value string;
attribute ontology-valuation-method, value string;
attribute ontology-formula, value string;
attribute ontology-eps-period, value string;
attribute ontology-multiple-period, value string;
attribute ontology-valuation-as-of, value string;
attribute ontology-valuation-freshness-status, value string;
attribute ontology-valuation-data-state-label, value string;
attribute ontology-valuation-source-type, value string;
attribute ontology-valuation-currency, value string;
attribute ontology-valuation-consensus-status, value string;
attribute ontology-per-valuation-status, value string;
attribute ontology-per-valuation-reason, value string;
attribute ontology-preferred-valuation-metric, value string;
attribute ontology-fundamental-data-source-priority, value string;
attribute ontology-window-key, value string;
attribute ontology-has-sufficient-history, value string;
attribute ontology-latest-observation-quality, value string;
attribute ontology-sequence-role, value string;
attribute ontology-observation-quality, value string;
attribute ontology-observed-at, value string;
attribute ontology-provider, value string;
attribute ontology-price-path-pattern, value string;
attribute ontology-flow-pattern, value string;
attribute ontology-event-cluster-type, value string;
attribute ontology-trend-episode-type, value string;
attribute ontology-language-registry-version, value string;
attribute ontology-language-term-id, value string;
attribute ontology-language-term-category, value string;
attribute ontology-language-term-status, value string;
attribute ontology-language-term-version, value string;
attribute ontology-language-preferred-label, value string;
attribute ontology-language-delivery-level, value string;
attribute ontology-language-delivery-level-label, value string;
attribute ontology-language-rendered-label, value string;
attribute ontology-smart-money-direction, value string;
attribute ontology-smart-money-flow-direction, value string;
attribute ontology-smart-money-flow-basis, value string;
attribute ontology-investor-flow-psychology, value string;
attribute ontology-investor-flow-evidence-role, value string;
attribute ontology-investor-flow-data-state, value string;
attribute ontology-investor-flow-review-level, value string;
attribute ontology-investor-flow-measurement-type, value string;
attribute ontology-investor-flow-is-estimate, value string;
attribute ontology-investor-flow-source-as-of, value string;
attribute ontology-investor-flow-provider-update-slot, value string;
attribute ontology-investor-flow-freshness-status, value string;
attribute ontology-trend-risk-state, value string;
attribute ontology-trend-review-level, value string;
attribute ontology-trend-evidence-role, value string;
attribute ontology-trend-data-state, value string;
attribute ontology-liquidity-state, value string;
attribute ontology-liquidity-review-level, value string;
attribute ontology-liquidity-data-state, value string;
attribute ontology-source-data-state, value string;
attribute ontology-external-signal-data-state, value string;
attribute ontology-valuation-data-state, value string;
attribute ontology-valuation-input-state, value string;
attribute ontology-valuation-reliability-state, value string;

entity ontology-node @abstract,
    owns ontology-id,
    owns ontology-storage-id @unique,
    owns ontology-content-fingerprint,
    owns ontology-label,
    owns ontology-kind,
    owns ontology-box,
    owns ontology-symbol,
    owns ontology-rule-id,
    owns ontology-account-id,
    owns ontology-tenant-id,
    owns ontology-world-id,
    owns ontology-world-type,
    owns ontology-snapshot-id,
    owns ontology-scope-id,
    owns ontology-scope-type,
    owns ontology-manifest-id,
    owns ontology-tbox-class,
    owns ontology-semantic-type,
    owns ontology-updated-at,
    owns ontology-json,
    owns ontology-source-value,
    owns ontology-field,
    owns ontology-level-type,
    owns ontology-data-scope,
    owns ontology-domain-scope,
    owns ontology-relation-type,
    owns ontology-relation-scope,
    owns ontology-group,
    owns ontology-polarity,
    owns ontology-evidence-role,
    owns ontology-review-level,
    owns ontology-data-state,
    owns ontology-change-state,
    owns ontology-conflict-state,
    owns ontology-validation-state,
    owns ontology-event-type,
    owns ontology-materiality-passed,
    owns ontology-materiality-state,
    owns ontology-relevance-state,
    owns ontology-source-trust-state,
    owns ontology-value-number,
    owns ontology-profit-loss-rate,
    owns ontology-allow-add-on-strength,
    owns ontology-trim-on-trend-break,
    owns ontology-avoid-averaging-down,
    owns ontology-impact-polarity,
    owns ontology-needs-review,
    owns ontology-read-scope,
    owns ontology-pe-ratio,
    owns ontology-beta,
    owns ontology-delta,
    owns ontology-delta-pct,
    owns ontology-delta-bp,
    owns ontology-previous-value,
    owns ontology-delta-1d-bp,
    owns ontology-delta-5d-bp,
    owns ontology-delta-20d-bp,
    owns ontology-change-24h,
    owns ontology-change-7d,
    owns ontology-surprise-percentage,
    owns ontology-current-price,
    owns ontology-average-price,
    owns ontology-market-value,
    owns ontology-quantity,
    owns ontology-sellable-quantity,
    owns ontology-position-weight-pct,
    owns ontology-position-account-weight-pct,
    owns ontology-exposure-ratio,
    owns ontology-position-count,
    owns ontology-change-rate,
    owns ontology-price-change-rate,
    owns ontology-ma5,
    owns ontology-ma20,
    owns ontology-ma60,
    owns ontology-ma5-distance,
    owns ontology-ma20-distance,
    owns ontology-ma60-distance,
    owns ontology-ma20-slope,
    owns ontology-ma60-slope,
    owns ontology-trend-curve,
    owns ontology-volume,
    owns ontology-volume-ratio,
    owns ontology-raw-volume-ratio,
    owns ontology-time-adjusted-volume-ratio,
    owns ontology-expected-volume-ratio-now,
    owns ontology-trade-strength,
    owns ontology-trading-value,
    owns ontology-reported-trading-value,
    owns ontology-estimated-trading-value,
    owns ontology-trading-value-mismatch-pct,
    owns ontology-trading-value-quality,
    owns ontology-trading-value-basis,
    owns ontology-bid-ask-imbalance,
    owns ontology-foreign-net-volume,
    owns ontology-foreign-net-amount,
    owns ontology-institution-net-volume,
    owns ontology-institution-net-amount,
    owns ontology-individual-net-volume,
    owns ontology-individual-net-amount,
    owns ontology-smart-money-net-volume,
    owns ontology-adr-ratio,
    owns ontology-adr-price-usd,
    owns ontology-adr-volume,
    owns ontology-usd-krw-rate,
    owns ontology-local-price-krw,
    owns ontology-local-equivalent-krw,
    owns ontology-leverage-factor,
    owns ontology-price,
    owns ontology-fair-value,
    owns ontology-fair-value-price,
    owns ontology-fair-value-low,
    owns ontology-fair-value-base,
    owns ontology-fair-value-high,
    owns ontology-margin-of-safety-pct,
    owns ontology-conservative-margin-of-safety-pct,
    owns ontology-optimistic-margin-of-safety-pct,
    owns ontology-expensive-premium-pct,
    owns ontology-minimum-margin-of-safety-pct,
    owns ontology-valuation-decision-eligible,
    owns ontology-valuation-model-count,
    owns ontology-valuation-consensus-price,
    owns ontology-valuation-disagreement-pct,
    owns ontology-expected-eps,
    owns ontology-reported-eps,
    owns ontology-estimated-eps,
    owns ontology-target-per,
    owns ontology-forward-pe,
    owns ontology-peg-ratio,
    owns ontology-dividend-yield,
    owns ontology-peer-per,
    owns ontology-historical-median-per,
    owns ontology-lookback-days,
    owns ontology-required-sample-count,
    owns ontology-sample-count,
    owns ontology-coverage-ratio,
    owns ontology-elapsed-hours,
    owns ontology-start-price,
    owns ontology-price-change-pct,
    owns ontology-relative-return-pct,
    owns ontology-proxy-change-rate,
    owns ontology-peak-price,
    owns ontology-trough-price,
    owns ontology-peak-return-pct,
    owns ontology-trough-return-pct,
    owns ontology-drawdown-from-peak-pct,
    owns ontology-rebound-from-trough-pct,
    owns ontology-prior-price-change-pct,
    owns ontology-recent-price-change-pct,
    owns ontology-price-velocity-change-pct,
    owns ontology-consecutive-decline-count,
    owns ontology-consecutive-advance-count,
    owns ontology-direction-change-count,
    owns ontology-valid-observation-count,
    owns ontology-invalid-observation-count,
    owns ontology-stale-observation-count,
    owns ontology-valid-observation-ratio,
    owns ontology-profit-loss-rate-start,
    owns ontology-profit-loss-rate-end,
    owns ontology-profit-loss-rate-change-pct,
    owns ontology-ma20-distance-start,
    owns ontology-ma20-distance-end,
    owns ontology-ma20-distance-change,
    owns ontology-ma20-distance-peak,
    owns ontology-ma20-distance-trough,
    owns ontology-ma20-reclaim-count,
    owns ontology-ma20-break-count,
    owns ontology-ma20-observation-count,
    owns ontology-ma60-distance-start,
    owns ontology-ma60-distance-end,
    owns ontology-volume-ratio-end,
    owns ontology-trade-strength-end,
    owns ontology-bid-ask-imbalance-end,
    owns ontology-smart-money-net-latest,
    owns ontology-smart-money-net-change,
    owns ontology-smart-money-net-cumulative,
    owns ontology-smart-money-net-amount-cumulative,
    owns ontology-smart-money-trading-value-ratio-pct,
    owns ontology-smart-money-positive-session-ratio,
    owns ontology-smart-money-negative-session-ratio,
    owns ontology-smart-money-flow-persistence-ratio,
    owns ontology-smart-money-flow-acceleration,
    owns ontology-smart-money-observation-count,
    owns ontology-smart-money-distinct-observation-count,
    owns ontology-smart-money-distinct-session-count,
    owns ontology-individual-net-latest,
    owns ontology-event-count,
    owns ontology-risk-event-count,
    owns ontology-support-event-count,
    owns ontology-investment-strategy-profile,
    owns ontology-investment-strategy-profile-label,
    owns ontology-position-role,
    owns ontology-target-position-role,
    owns ontology-position-intent,
    owns ontology-position-intent-label,
    owns ontology-position-intent-description,
    owns ontology-instrument-archetype,
    owns ontology-instrument-archetype-label,
    owns ontology-factor,
    owns ontology-sensitivity-level,
    owns ontology-rate-series-id,
    owns ontology-observation-date,
    owns ontology-previous-observation-date,
    owns ontology-source-as-of,
    owns ontology-change-basis,
    owns ontology-crypto-symbol,
    owns ontology-fx-pair,
    owns ontology-action-policy,
    owns ontology-security-line-role,
    owns ontology-local-symbol,
    owns ontology-company-name,
    owns ontology-market,
    owns ontology-currency,
    owns ontology-exchange,
    owns ontology-adr-symbol,
    owns ontology-etf-symbol,
    owns ontology-underlying-symbol,
    owns ontology-conversion-start-date,
    owns ontology-listing-date,
    owns ontology-source-url,
    owns ontology-valuation-method,
    owns ontology-formula,
    owns ontology-eps-period,
    owns ontology-multiple-period,
    owns ontology-valuation-as-of,
    owns ontology-valuation-freshness-status,
    owns ontology-valuation-data-state-label,
    owns ontology-valuation-source-type,
    owns ontology-valuation-currency,
    owns ontology-valuation-consensus-status,
    owns ontology-per-valuation-status,
    owns ontology-per-valuation-reason,
    owns ontology-preferred-valuation-metric,
    owns ontology-fundamental-data-source-priority,
    owns ontology-window-key,
    owns ontology-has-sufficient-history,
    owns ontology-latest-observation-quality,
    owns ontology-sequence-role,
    owns ontology-observation-quality,
    owns ontology-observed-at,
    owns ontology-provider,
    owns ontology-price-path-pattern,
    owns ontology-flow-pattern,
    owns ontology-event-cluster-type,
    owns ontology-trend-episode-type,
    owns ontology-language-registry-version,
    owns ontology-language-term-id,
    owns ontology-language-term-category,
    owns ontology-language-term-status,
    owns ontology-language-term-version,
    owns ontology-language-preferred-label,
    owns ontology-language-delivery-level,
    owns ontology-language-delivery-level-label,
    owns ontology-language-rendered-label,
    owns ontology-smart-money-direction,
    owns ontology-smart-money-flow-direction,
    owns ontology-smart-money-flow-basis,
    owns ontology-investor-flow-psychology,
    owns ontology-investor-flow-evidence-role,
    owns ontology-investor-flow-data-state,
    owns ontology-investor-flow-review-level,
    owns ontology-investor-flow-measurement-type,
    owns ontology-investor-flow-is-estimate,
    owns ontology-investor-flow-source-as-of,
    owns ontology-investor-flow-provider-update-slot,
    owns ontology-investor-flow-freshness-status,
    owns ontology-trend-risk-state,
    owns ontology-trend-review-level,
    owns ontology-trend-evidence-role,
    owns ontology-trend-data-state,
    owns ontology-liquidity-state,
    owns ontology-liquidity-review-level,
    owns ontology-liquidity-data-state,
    owns ontology-source-data-state,
    owns ontology-external-signal-data-state,
    owns ontology-valuation-data-state,
    owns ontology-valuation-input-state,
    owns ontology-valuation-reliability-state,
    plays ontology-assertion:source,
    plays ontology-assertion:target;

entity ontology-entity, sub ontology-node;
entity ontology-evidence, sub ontology-node;
entity ontology-belief, sub ontology-node;
entity ontology-opinion, sub ontology-node;
entity ontology-reasoning-card, sub ontology-node;

relation ontology-assertion,
    relates source,
    relates target,
    owns ontology-id,
    owns ontology-storage-id @unique,
    owns ontology-content-fingerprint,
    owns ontology-relation-type,
    owns ontology-box,
    owns ontology-symbol,
    owns ontology-rule-id,
    owns ontology-account-id,
    owns ontology-tenant-id,
    owns ontology-world-id,
    owns ontology-world-type,
    owns ontology-snapshot-id,
    owns ontology-scope-id,
    owns ontology-scope-type,
    owns ontology-manifest-id,
    owns ontology-tbox-class,
    owns ontology-semantic-type,
    owns ontology-updated-at,
    owns ontology-json,
    owns ontology-weight,
    owns ontology-field,
    owns ontology-polarity,
    owns ontology-evidence-role,
    owns ontology-review-level,
    owns ontology-data-state,
    owns ontology-change-state,
    owns ontology-conflict-state,
    owns ontology-validation-state,
    owns ontology-transition-type,
    owns ontology-signal-group,
    owns ontology-materiality-passed,
    owns ontology-materiality-state,
    owns ontology-relevance-state,
    owns ontology-source-trust-state,
    owns ontology-delta,
    owns ontology-delta-pct,
    owns ontology-exposure-ratio,
    owns ontology-position-count;
""".strip()
    promoted_types = {
        **{
            attribute: "double"
            for attribute in TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES.values()
        },
        **{
            attribute: "string"
            for attribute in TYPEDB_PROMOTED_TEXT_ATTRIBUTES.values()
        },
    }
    missing_promoted_types = {
        attribute: value_type
        for (attribute, value_type) in promoted_types.items()
        if "attribute " + attribute + ", value " not in schema
    }
    if missing_promoted_types:
        declarations = "\n".join(
            (
                "attribute " + attribute + ", value " + value_type + ";"
                for (attribute, value_type) in sorted(missing_promoted_types.items())
            )
        )
        ownership = "\n".join(
            (
                "    owns " + attribute + ","
                for attribute in sorted(missing_promoted_types)
            )
        )
        schema = schema.replace("define\n", "define\n" + declarations + "\n", 1)
        schema = schema.replace(
            "    plays ontology-assertion:source,",
            ownership + "\n    plays ontology-assertion:source,",
            1,
        )
    schema = slim_typeql_node_schema(schema)
    capability_contract = typedb_rule_schema_capability_contract()
    semantic_schema = semantic_typeql_schema(
        context_attribute_ownership=capability_contract.get("contextAttributes") or {},
        physical_class_names=capability_contract.get("physicalClassNames") or [],
        physical_relation_names=capability_contract.get("physicalRelationNames") or [],
    )
    return schema + "\n\n" + semantic_schema.replace("define\n", "", 1).strip()


def base_schema_contract_metadata(_store: SchemaStore) -> Dict[str, str]:
    """Return the immutable TypeDB schema contract required by this build.

    A current static graph does not prove that every promoted TypeQL
    attribute required by the active RuleBox exists. Keep a compact,
    content-addressed schema contract beside the static manifest so a
    costly schema inspection happens only after the actual definition
    changes.
    """
    schema = _store.schema_query()
    return {
        "schemaContractVersion": "typedb-base-schema-contract-v2:"
        + SEMANTIC_STORAGE_CONTRACT_VERSION,
        "schemaContractFingerprint": "typedb-base-schema:"
        + hashlib.sha256(schema.encode("utf-8")).hexdigest()[:24],
    }
