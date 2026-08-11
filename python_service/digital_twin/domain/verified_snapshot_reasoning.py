"""Create safe ontology work only from a persisted monitor snapshot.

Realtime providers can update a cache many times while a full TypeDB turn is
running.  Those ticks are useful source facts, but they are not themselves a
replayable ABox boundary: the reasoning worker reads the persisted monitor
snapshot, not a vendor cache.  This module turns one committed snapshot into a
bounded latest-state request after comparing it with the previously committed
snapshot.

The comparison is an operational ingress gate only. It decides whether a
source fact warrants one new TypeDB turn; it never evaluates a RuleBox rule,
assigns a recommendation, or changes the facts persisted in the ABox.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Tuple

from .events import DomainEvent, ontology_reasoning_requested_event, snapshot_collected_event
from .evidence_delta import evidence_inference_signature, inference_eligible
from .fact_changes import changed_fields, fact_revision_id, fact_signature
from .crypto_market_signals import crypto_market_transitions, crypto_transition_targets
from .investment_research import research_evidence_from_payload
from .materiality import market_change_materiality
from .ontology_projection_input import compact_external_signals_for_ontology
from .portfolio import AccountSnapshot, Position


VERIFIED_MONITOR_SNAPSHOT_TRIGGER = "verified-monitor-snapshot"
VERIFIED_MONITOR_SNAPSHOT_SLOT_FAMILY = "VerifiedMonitorSnapshot"
VERIFIED_MONITOR_SNAPSHOT_VERSION = "verified-monitor-snapshot-v3"


# Deliberately keep the source contract close to the Position domain object.
# Collection timestamps are excluded below; the scheduler reacts to changed
# facts, not to a provider polling again with the same values.
POSITION_FACT_FIELDS = (
    "symbol",
    "source",
    "market",
    "currency",
    "sector",
    "quantity",
    "sellable_quantity",
    "average_price",
    "current_price",
    "change_rate",
    "quote_status",
    "data_quality",
    "source_timestamp_state",
    "freshness_status",
    "latency_status",
    "market_session",
    "market_session_label",
    "real_time",
    "exchange_rate",
    "market_value",
    "profit_loss",
    "profit_loss_rate",
    "trade_strength",
    "trading_value",
    "volume",
    "volume_ratio",
    "buy_volume",
    "sell_volume",
    "orderbook_bid_volume",
    "orderbook_ask_volume",
    "bid_ask_imbalance",
    "foreign_buy_volume",
    "foreign_sell_volume",
    "foreign_net_volume",
    "foreign_net_amount",
    "institution_buy_volume",
    "institution_sell_volume",
    "institution_net_volume",
    "institution_net_amount",
    "individual_buy_volume",
    "individual_sell_volume",
    "individual_net_volume",
    "individual_net_amount",
    "ma5",
    "ma20",
    "ma60",
    "ma120",
    "ma200",
    "ma20_slope",
    "ma60_slope",
    "ma5_distance",
    "ma20_distance",
    "ma60_distance",
)

TECHNICAL_FIELDS = {
    "ma5", "ma20", "ma60", "ma120", "ma200",
    "ma20_slope", "ma60_slope", "ma5_distance", "ma20_distance", "ma60_distance",
}
FLOW_FIELDS = {
    "trade_strength", "trading_value", "volume", "volume_ratio", "buy_volume", "sell_volume",
    "foreign_buy_volume", "foreign_sell_volume", "foreign_net_volume", "foreign_net_amount",
    "institution_buy_volume", "institution_sell_volume", "institution_net_volume", "institution_net_amount",
    "individual_buy_volume", "individual_sell_volume", "individual_net_volume", "individual_net_amount",
}
ORDERBOOK_FIELDS = {"orderbook_bid_volume", "orderbook_ask_volume", "bid_ask_imbalance"}
POSITION_CONTEXT_FIELDS = {"quantity", "sellable_quantity", "average_price", "source"}

EXTERNAL_REFRESH_FIELDS = {
    "fetchedAt", "fetched_at", "sourceAsOf", "source_as_of", "sourceFetchedAt", "source_fetched_at",
    "lastUpdated", "last_updated", "indicatorFetchedAt", "indicator_fetched_at",
    "freshnessAgeMinutes", "freshness_age_minutes", "checkedAt", "checked_at",
    # External quality/freshness payloads are rebuilt at every monitor poll.
    # These clock-derived values must not fan one unchanged provider refresh
    # out into a TypeDB request for every holding.  Their discrete status,
    # coverage, and error fields intentionally remain part of the signature.
    "generatedAt", "generated_at", "collectedAt", "collected_at",
    "observedAt", "observed_at", "updatedAt", "updated_at",
    "ageMinutes", "age_minutes", "ageSeconds", "age_seconds",
    "elapsedMinutes", "elapsed_minutes", "elapsedSeconds", "elapsed_seconds",
    "lastCheckedAt", "last_checked_at", "lastPolledAt", "last_polled_at",
    "lastSuccessAt", "last_success_at", "lastFailureAt", "last_failure_at",
    "lastHealthyAt", "last_healthy_at", "lastObservedAt", "last_observed_at",
    "firstObservedAt", "first_observed_at", "stateSince", "state_since",
    "refreshedAt", "refreshed_at", "nextRefreshAt", "next_refresh_at",
    "expiresAt", "expires_at", "validFrom", "valid_from", "validUntil", "valid_until",
}

# Quote/fundamental provider cache payloads overlap with the durable Position
# fact. Re-running TypeDB for a small quote refresh through both paths creates
# duplicate work without adding a new ABox condition. Primary quote fields and
# explicit research events remain the source of an inference request.
SUPPLEMENTAL_EXTERNAL_GROUPS = {
    "equityQuotes",
    "yfinanceData",
    "companyOverviews",
}
# These are shared operational-context facts, not a change to one stock's
# investment case. They remain in the persisted ABox and are evaluated with
# the next material subject request. A provider outage already has its own
# operational alert; duplicating it as a TypeDB turn for every holding turns a
# single state change into a queue flood.
CONTEXT_ONLY_EXTERNAL_GROUPS = {
    "quality", "freshness", "provenance", "statuses", "macro", "fxRates",
}


def _clean_symbol(value: object) -> str:
    return str(value or "").upper().strip()


def _number(value: object):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _setting_number(settings: Mapping[str, object], key: str, default: float) -> float:
    value = _number((settings or {}).get(key))
    return max(0.0, value if value is not None else default)


def _macro_series_values(external: Mapping[str, object]) -> Dict[str, float]:
    macro = external.get("macro") if isinstance(external.get("macro"), Mapping) else {}
    series = macro.get("series") if isinstance(macro.get("series"), Mapping) else {}
    values = {}
    for series_id, raw in series.items():
        payload = raw if isinstance(raw, Mapping) else {"value": raw}
        value = _number(payload.get("value"))
        if value is not None:
            values[str(series_id or "").upper().strip()] = value
    return values


def _fx_values(external: Mapping[str, object]) -> Dict[str, float]:
    rows = external.get("fxRates") if isinstance(external.get("fxRates"), Mapping) else {}
    values = {}
    for currency, raw in rows.items():
        payload = raw if isinstance(raw, Mapping) else {"value": raw}
        value = _number(
            payload.get("value")
            if payload.get("value") is not None
            else payload.get("rate")
        )
        if value is not None and value > 0:
            values[str(currency or "").upper().strip()] = value
    return values


def systemic_macro_transition(
    previous: Mapping[str, object],
    current: Mapping[str, object],
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Detect only an explicit shared-market shock for operational routing."""

    rate_threshold_bp = _setting_number(
        settings or {},
        "ontologyMacroSystemicRateDeltaBp",
        25.0,
    )
    fx_threshold_pct = _setting_number(
        settings or {},
        "ontologyMacroSystemicFxChangePct",
        2.0,
    )
    previous_rates = _macro_series_values(previous)
    current_rates = _macro_series_values(current)
    rate_changes = {
        series_id: round((current_rates[series_id] - previous_rates[series_id]) * 100.0, 3)
        for series_id in sorted(set(previous_rates) & set(current_rates))
    }
    previous_fx = _fx_values(previous)
    current_fx = _fx_values(current)
    fx_changes = {
        currency: round(
            ((current_fx[currency] - previous_fx[currency]) / previous_fx[currency]) * 100.0,
            4,
        )
        for currency in sorted(set(previous_fx) & set(current_fx))
        if previous_fx[currency] > 0
    }
    breached_rates = {
        key: value for key, value in rate_changes.items() if abs(value) >= rate_threshold_bp
    }
    breached_fx = {
        key: value for key, value in fx_changes.items() if abs(value) >= fx_threshold_pct
    }
    return {
        "systemic": bool(breached_rates or breached_fx),
        "rateThresholdBp": rate_threshold_bp,
        "fxThresholdPct": fx_threshold_pct,
        "rateChangesBp": breached_rates,
        "fxChangesPct": breached_fx,
    }


def _camel_case(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(item[:1].upper() + item[1:] for item in tail)


def _value(payload: Mapping[str, object], field: str) -> object:
    if field in payload:
        return payload.get(field)
    return payload.get(_camel_case(field))


def _position_payload(value: object) -> Dict[str, object]:
    if isinstance(value, Position):
        source = value.to_dict()
    else:
        source = dict(value or {}) if isinstance(value, Mapping) else {}
    return {
        field: _value(source, field)
        for field in POSITION_FACT_FIELDS
        if _value(source, field) not in (None, "")
    }


def _state_position_map(state: Mapping[str, object]) -> Dict[str, Dict[str, object]]:
    result: Dict[str, Dict[str, object]] = {}
    for key in ("positions", "watchlist"):
        rows = state.get(key) if isinstance(state, Mapping) else {}
        values = rows.values() if isinstance(rows, Mapping) else rows if isinstance(rows, list) else []
        for raw in values:
            if not isinstance(raw, Mapping):
                continue
            symbol = _clean_symbol(raw.get("symbol"))
            sector = str(raw.get("sector") or "")
            if not symbol or symbol == "CASH" or sector == "현금":
                continue
            # A holding and a watchlist entry can share a symbol. The holding
            # carries the account context, so retain it when both exist.
            if key == "watchlist" and symbol in result:
                continue
            result[symbol] = _position_payload(raw)
    return result


def _snapshot_position_map(snapshot: AccountSnapshot) -> Dict[str, Dict[str, object]]:
    result: Dict[str, Dict[str, object]] = {}
    for position in list(snapshot.positions or []) + list(snapshot.watchlist or []):
        if position.is_cash():
            continue
        symbol = position.key()
        if not symbol or (symbol in result and str(position.source or "") == "watchlist"):
            continue
        result[symbol] = _position_payload(position)
    return result


def _portfolio_context(snapshot: AccountSnapshot) -> Dict[str, object]:
    """Return portfolio facts whose change can affect other subjects.

    Total valuation and concentration intentionally stay out of this global
    context: one price tick would otherwise fan out to every holding. TypeDB
    still receives those values in the current ABox; this contract only
    decides which target subject needs a new native-rule turn.
    """

    holdings_by_symbol: Dict[str, Dict[str, object]] = {}
    for position in list(snapshot.positions or []) + list(snapshot.watchlist or []):
        if position.is_cash():
            continue
        symbol = position.key()
        if not symbol or (symbol in holdings_by_symbol and str(position.source or "") == "watchlist"):
            continue
        holdings_by_symbol[symbol] = {
            "symbol": position.key(),
            "source": str(position.source or ""),
            "quantity": position.quantity,
            "sellable_quantity": position.sellable_quantity,
            "average_price": position.average_price,
        }
    return {
        "cash": snapshot.portfolio.cash,
        "holdings": sorted(holdings_by_symbol.values(), key=lambda item: (str(item.get("symbol") or ""), str(item.get("source") or ""))),
    }


def _previous_portfolio_context(state: Mapping[str, object]) -> Dict[str, object]:
    source = state if isinstance(state, Mapping) else {}
    portfolio = source.get("portfolio") if isinstance(source.get("portfolio"), Mapping) else {}
    positions = _state_position_map(source)
    holdings = [
        {
            "symbol": symbol,
            "source": str(payload.get("source") or ""),
            "quantity": payload.get("quantity"),
            "sellable_quantity": payload.get("sellable_quantity"),
            "average_price": payload.get("average_price"),
        }
        for symbol, payload in positions.items()
    ]
    return {
        "cash": portfolio.get("cash"),
        "holdings": sorted(holdings, key=lambda item: (str(item.get("symbol") or ""), str(item.get("source") or ""))),
    }


def _research_lifecycle_state(value: object) -> str:
    source = value if isinstance(value, Mapping) else {}
    payload = source.get("payload") if isinstance(source.get("payload"), Mapping) else {}
    for key in (
        "lifecycleState",
        "lifecycle_state",
        "evidenceLifecycleState",
        "evidence_lifecycle_state",
    ):
        state = source.get(key)
        if state in (None, ""):
            state = payload.get(key)
        text = str(state or "").strip()
        if text:
            return text
    return "active"


def _eligible_research_evidence_projection(
    value: object,
    symbol: str,
    settings: Mapping[str, object] = None,
) -> List[Dict[str, object]]:
    """Return the active, inference-eligible research set for one symbol.

    ``researchEvidence`` is a read-model cache that is rebuilt on every
    monitor pass. Its newest-N order can change when a yfinance supplement is
    refreshed, even though no TypeDB-eligible fact changed. The evidence
    store already defines the admission boundary, so reuse its semantic
    inference signature here instead of scheduling on cache ordering or
    collection clocks.
    """

    rows = value if isinstance(value, list) else []
    clean_symbol = _clean_symbol(symbol)
    projections: Dict[str, Dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        evidence = research_evidence_from_payload(dict(row), fallback_symbol=clean_symbol)
        if _clean_symbol(getattr(evidence, "symbol", "")) != clean_symbol:
            continue
        lifecycle_state = _research_lifecycle_state(row)
        if not inference_eligible(evidence, lifecycle_state, dict(settings or {})):
            continue
        signature = evidence_inference_signature(evidence)
        if not signature:
            continue
        projections[signature] = {
            "evidenceId": str(getattr(evidence, "evidence_id", "") or ""),
            "inferenceSignature": signature,
            "lifecycleState": str(lifecycle_state or "active").strip().lower() or "active",
        }
    return [projections[key] for key in sorted(projections)]


def _external_for_symbol(
    compact: Mapping[str, object],
    symbol: str,
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    result: Dict[str, object] = {}
    # Crypto price paths have an explicit transition contract below.  Keeping
    # this global source in every stock's generic external delta made one
    # CoinGecko poll enqueue the complete account even when no crypto rule was
    # eligible to change.
    for key in ("macro", "fxRates", "quality", "freshness", "provenance", "statuses"):
        if key in compact:
            value = compact.get(key)
            if key == "statuses" and isinstance(value, list):
                value = [
                    item for item in value
                    if not isinstance(item, Mapping) or str(item.get("source") or "") != "CoinGecko"
                ]
            elif key == "quality" and isinstance(value, Mapping):
                value = dict(value)
                coverage = value.get("sourceCoverage")
                if isinstance(coverage, list):
                    value["sourceCoverage"] = [
                        item for item in coverage
                        if not isinstance(item, Mapping) or str(item.get("key") or "") != "cryptoMarkets"
                    ]
                value.pop("cryptoMarketCount", None)
            elif key == "provenance" and isinstance(value, Mapping):
                value = dict(value)
                for nested_key in ("sources", "unavailableSources"):
                    rows = value.get(nested_key)
                    if isinstance(rows, list):
                        value[nested_key] = [item for item in rows if str(item or "") != "CoinGecko"]
            if value not in (None, "", [], {}):
                result[key] = value
    for group in (
        "secFilings", "equityQuotes", "yfinanceData", "newsHeadlines", "dartDisclosures",
        "earningsReports", "companyOverviews", "companyKnowledge", "researchEvidence",
    ):
        rows = compact.get(group)
        if not isinstance(rows, Mapping) or symbol not in rows:
            continue
        if group == "researchEvidence":
            eligible = _eligible_research_evidence_projection(
                rows.get(symbol),
                symbol,
                settings,
            )
            if eligible:
                result[group] = {symbol: eligible}
            continue
        result[group] = {symbol: rows.get(symbol)}
    return result


def _changed_external_groups(previous: Mapping[str, object], current: Mapping[str, object]) -> List[str]:
    groups = []
    for key in sorted(set(previous) | set(current)):
        before = fact_signature({key: previous.get(key)}, EXTERNAL_REFRESH_FIELDS)
        after = fact_signature({key: current.get(key)}, EXTERNAL_REFRESH_FIELDS)
        if before != after:
            groups.append(key)
    return groups


def _reasoning_external_groups(
    changed_groups: Iterable[str],
    include_shared_context: bool = False,
) -> List[str]:
    """Return only subject-scoped external changes that require a TypeDB turn."""

    selected = []
    for group in changed_groups or []:
        if group in SUPPLEMENTAL_EXTERNAL_GROUPS:
            continue
        if group in CONTEXT_ONLY_EXTERNAL_GROUPS and not include_shared_context:
            continue
        selected.append(group)
    return sorted(set(selected))


def _fact_types_for_change(fields: Iterable[str], external_groups: Iterable[str], portfolio_changed: bool) -> List[str]:
    selected = set()
    field_set = set(fields or [])
    if field_set - TECHNICAL_FIELDS - FLOW_FIELDS - ORDERBOOK_FIELDS - POSITION_CONTEXT_FIELDS:
        selected.add("MarketQuote")
    if field_set & TECHNICAL_FIELDS:
        selected.add("TechnicalIndicator")
    if field_set & FLOW_FIELDS:
        selected.add("ExecutionFlow")
    if field_set & ORDERBOOK_FIELDS:
        selected.add("OrderBook")
    if field_set & POSITION_CONTEXT_FIELDS or "positionRemoved" in field_set or portfolio_changed:
        selected.add("PortfolioSnapshot")
    groups = set(external_groups or [])
    if groups & {"macro"}:
        selected.add("InterestRate")
    if groups & {"fxRates"}:
        selected.add("FxRate")
    if groups & {"equityQuotes"}:
        selected.add("MarketQuote")
    if groups & {
        "secFilings", "newsHeadlines", "dartDisclosures", "earningsReports",
        "researchEvidence",
    }:
        selected.add("ResearchEvidence")
    if "companyKnowledge" in groups:
        selected.update({
            "CompanyProfile",
            "FinancialFact",
            "GovernanceChange",
            "CapitalStructureChange",
            "ValuationObservation",
        })
    if groups & {"quality", "freshness", "provenance", "statuses"}:
        selected.add("DataQuality")
    return sorted(selected or {"PortfolioSnapshot"})


def verified_monitor_snapshot_reasoning_event(
    snapshot: AccountSnapshot,
    previous_state: Mapping[str, object] = None,
    settings: Mapping[str, object] = None,
    observation_followup_symbols: Iterable[str] = None,
) -> DomainEvent | None:
    """Create one current-state reasoning request for changed facts or a queued quote follow-up.

    The request's ``sourceObservedAt`` is the snapshot generation time via
    ``snapshot_collected_event``. That is the exact persisted source boundary
    the replay worker reads, so the freshness check can accept it without
    relaxing stale-data protection for raw provider events.
    """

    previous = dict(previous_state or {}) if isinstance(previous_state, Mapping) else {}
    current_positions = _snapshot_position_map(snapshot)
    previous_positions = _state_position_map(previous)
    subjects = sorted(set(current_positions) | set(previous_positions))

    current_portfolio = _portfolio_context(snapshot)
    previous_portfolio = _previous_portfolio_context(previous)
    portfolio_changed = fact_signature(previous_portfolio) != fact_signature(current_portfolio)
    previous_raw_external = previous.get("externalSignals") if isinstance(previous, Mapping) and isinstance(previous.get("externalSignals"), Mapping) else {}
    crypto_transitions = crypto_market_transitions(
        previous_raw_external,
        snapshot.external_signals,
        settings=settings,
    )
    if not subjects and not crypto_transitions:
        return None
    current_external = compact_external_signals_for_ontology(
        snapshot.external_signals,
        target_symbols=subjects,
        settings=settings,
    )
    previous_external = compact_external_signals_for_ontology(
        previous.get("externalSignals") if isinstance(previous, Mapping) else {},
        target_symbols=subjects,
        settings=settings,
    )
    macro_transition = systemic_macro_transition(
        previous_external,
        current_external,
        settings=settings,
    )

    changed_symbols: List[str] = []
    changed_fields_by_symbol: Dict[str, List[str]] = {}
    revisions: Dict[str, str] = {}
    all_fact_types = set()
    fact_types_by_symbol: Dict[str, List[str]] = {}
    changed_external_groups_by_symbol: Dict[str, List[str]] = {}
    materiality_assessments: Dict[str, Dict[str, object]] = {}
    deferred_market_symbols: List[str] = []
    deferred_supplemental_external_symbols: List[str] = []
    position_changed_count = 0

    for symbol in subjects:
        before_position = previous_positions.get(symbol, {})
        after_position = current_positions.get(symbol, {})
        position_fields = changed_fields(before_position, after_position, POSITION_FACT_FIELDS)
        if before_position and not after_position:
            position_fields = ["positionRemoved"]
        if position_fields:
            position_changed_count += 1

        before_external = _external_for_symbol(previous_external, symbol, settings)
        after_external = _external_for_symbol(current_external, symbol, settings)
        raw_external_groups = _changed_external_groups(before_external, after_external)
        external_groups = _reasoning_external_groups(
            raw_external_groups,
            include_shared_context=bool(macro_transition.get("systemic")),
        )
        assessment = None
        if position_fields and before_position and after_position:
            assessment = market_change_materiality(
                symbol,
                before_position,
                after_position,
                {"fields": position_fields},
                dict(settings or {}),
            )
        bootstrap_or_position_context = bool(
            not before_position
            or not after_position
            or set(position_fields).intersection(POSITION_CONTEXT_FIELDS)
            or "positionRemoved" in position_fields
        )
        market_requires_reasoning = bool(assessment and assessment.passed)
        if assessment and not assessment.passed and position_fields and not bootstrap_or_position_context:
            deferred_market_symbols.append(symbol)
        if raw_external_groups and not external_groups and not position_fields:
            deferred_supplemental_external_symbols.append(symbol)
        if not (bootstrap_or_position_context or market_requires_reasoning or external_groups or portfolio_changed):
            continue

        # A direct evidence change can share a monitor snapshot with a small
        # quote refresh. Keep the source families exact: the evidence needs a
        # TypeDB turn, while the non-material quote does not become an
        # implicit MarketQuote/ExecutionFlow trigger merely because the same
        # subject was selected for another reason.
        selected_position_fields = (
            list(position_fields)
            if (bootstrap_or_position_context or market_requires_reasoning or portfolio_changed)
            else []
        )
        fields = list(selected_position_fields)
        if portfolio_changed:
            fields.append("portfolioContext")
        fields.extend("external." + group for group in external_groups)

        fact_types = _fact_types_for_change(
            selected_position_fields,
            external_groups,
            portfolio_changed,
        )
        all_fact_types.update(fact_types)
        changed_symbols.append(symbol)
        changed_fields_by_symbol[symbol] = fields[:80]
        changed_external_groups_by_symbol[symbol] = external_groups
        fact_types_by_symbol[symbol] = fact_types
        if assessment:
            materiality_assessments[symbol] = assessment.to_dict()
        revision_payload = {
            "position": (
                after_position or {"removed": True}
                if selected_position_fields
                else {}
            ),
            "portfolioContext": current_portfolio if portfolio_changed else {},
            "external": after_external if external_groups else {},
        }
        revisions[symbol] = fact_revision_id(
            "VerifiedMonitorSnapshot",
            symbol,
            revision_payload,
            EXTERNAL_REFRESH_FIELDS,
        )

    # BTC/ETH are independent market subjects.  The same transition may also
    # affect an actually held or watched crypto-sensitive stock, but it must
    # never fan out through unrelated account symbols.
    transition_targets = crypto_transition_targets(
        crypto_transitions,
        list(snapshot.positions or []) + list(snapshot.watchlist or []),
    )
    transitions_by_symbol: Dict[str, List[Dict[str, object]]] = {}
    for transition in crypto_transitions:
        target = _clean_symbol(transition.get("symbol"))
        if target:
            transitions_by_symbol.setdefault(target, []).append(dict(transition))
    for symbol in transition_targets:
        applicable = list(crypto_transitions if symbol not in transitions_by_symbol else transitions_by_symbol[symbol])
        if not applicable:
            continue
        fields = changed_fields_by_symbol.setdefault(symbol, [])
        for field in ["external.cryptoMarkets", "cryptoMarketTransition"]:
            if field not in fields:
                fields.append(field)
        groups = changed_external_groups_by_symbol.setdefault(symbol, [])
        if "cryptoMarkets" not in groups:
            groups.append("cryptoMarkets")
        if symbol not in changed_symbols:
            changed_symbols.append(symbol)
        all_fact_types.add("MarketQuote")
        fact_types_by_symbol[symbol] = ["MarketQuote"]
        revisions[symbol] = fact_revision_id(
            "VerifiedCryptoMarketTransition",
            symbol,
            {
                "position": current_positions.get(symbol, {}),
                "cryptoTransitions": applicable,
                "cryptoMarkets": current_external.get("cryptoMarkets", {}),
            },
            EXTERNAL_REFRESH_FIELDS,
        )

    # A material price observation compares the current quote with its durable
    # source-boundary anchor, not necessarily with the immediately preceding
    # snapshot. By the time that cumulative threshold is crossed, the latest
    # snapshot can be fact-identical to the previous one. Keep the queued
    # observation in the replay request so its TypeDB follow-up cannot be
    # silently dropped by that ordinary fact-delta optimization.
    observation_followups = sorted({
        _clean_symbol(symbol)
        for symbol in (observation_followup_symbols or [])
        if _clean_symbol(symbol) in current_positions
    })
    fact_changed_count = len(changed_symbols)
    for symbol in observation_followups:
        if symbol in changed_symbols:
            continue
        changed_symbols.append(symbol)
        changed_fields_by_symbol[symbol] = ["marketObservationFollowup"]
        changed_external_groups_by_symbol[symbol] = []
        all_fact_types.add("MarketQuote")
        fact_types_by_symbol[symbol] = ["MarketQuote"]
        revisions[symbol] = fact_revision_id(
            "VerifiedMonitorObservationFollowup",
            symbol,
            {
                "position": current_positions.get(symbol, {}),
                # This value is delivery provenance only. TypeDB evaluates
                # the persisted current ABox, never this request marker.
                "marketObservationSnapshot": str(snapshot.generated_at or ""),
            },
            EXTERNAL_REFRESH_FIELDS,
        )

    if not changed_symbols:
        return None

    external_groups = sorted({group for groups in changed_external_groups_by_symbol.values() for group in groups})
    source_event = snapshot_collected_event(snapshot)
    return ontology_reasoning_requested_event(
        source_event,
        VERIFIED_MONITOR_SNAPSHOT_TRIGGER,
        symbols=changed_symbols,
        changed_count=fact_changed_count,
        observed_count=len(subjects),
        fact_types=sorted(all_fact_types or {"PortfolioSnapshot"}),
        fact_types_by_symbol=fact_types_by_symbol,
        reason=(
            "확정 저장된 계좌 스냅샷에서 의미 있는 시세·수급 전환, 직접 근거, "
            "데이터 상태 변화와 시세 관측 후속 확인을 TypeDB 현재 상태 추론에 반영합니다."
        ),
        materiality_assessments=materiality_assessments,
        fact_revisions_by_symbol=revisions,
        changed_fields_by_symbol=changed_fields_by_symbol,
        snapshot_barrier={
            "version": VERIFIED_MONITOR_SNAPSHOT_VERSION,
            "generatedAt": str(snapshot.generated_at or ""),
            "accountId": str(snapshot.account_id or ""),
            "positionChangedCount": position_changed_count,
            "portfolioContextChanged": portfolio_changed,
            "externalSignalGroups": external_groups,
            "deferredImmaterialMarketSymbolCount": len(deferred_market_symbols),
            "deferredImmaterialMarketSymbols": deferred_market_symbols[:20],
            "deferredSupplementalExternalSymbolCount": len(deferred_supplemental_external_symbols),
            "deferredSupplementalExternalSymbols": deferred_supplemental_external_symbols[:20],
            "cryptoTransitions": crypto_transitions[:12],
            "cryptoTransitionTargetSymbols": transition_targets[:20],
            "systemicMacroTransition": macro_transition,
        },
        observation_followup_symbols=observation_followups,
        importance_gate="materiality-or-context-transition",
        materiality_role="scheduling-gate-only",
    )
