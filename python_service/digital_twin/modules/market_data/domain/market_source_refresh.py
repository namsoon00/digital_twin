"""Merge source observations without renewing account or evidence clocks."""
from copy import deepcopy
from dataclasses import replace

from digital_twin.modules.portfolio.contracts import portfolio_summary
from .data_freshness import parse_datetime


PRICE_FIELDS = (
    "current_price change_rate previous_close return_1d volume trading_value price_change_source price_change_basis "
    "price_change_usable quote_source quote_status quote_message data_quality updated_at "
    "source_as_of source_fetched_at source_timestamp_state freshness_status freshness_reason "
    "freshness_age_minutes freshness_max_age_minutes latency_status latency_reason "
    "market_session market_session_label source_transport real_time"
).split()
INDICATOR_FIELDS = (
    "ma5 ma20 ma60 ma120 ma200 ma20_slope ma60_slope return_3d return_5d "
    "price_history_adjustment indicator_as_of indicator_fetched_at"
).split()
STAGE_FIELDS = {
    "volume": ["volume_ratio"],
    "ccnl": "trade_strength buy_volume sell_volume".split(),
    "orderbook": "orderbook_bid_volume orderbook_ask_volume bid_ask_imbalance".split(),
    "investor": [party + "_" + field for party in ("foreign", "institution", "individual")
                 for field in ("buy_volume", "sell_volume", "net_volume", "net_amount")],
}


def _clock(value):
    return parse_datetime(value)


def _newer(incoming, current, cutoff, *, equal=False):
    new, old, end = _clock(incoming), _clock(current), _clock(cutoff)
    return bool(new and end and new <= end and (not old or new > old or (equal and new == old)))


def merge_market_position(position, incoming, cutoff, *, equal=False):
    """Only market fields cross this boundary; holdings and broker values cannot."""
    if incoming.symbol != position.symbol:
        raise ValueError("Market observation subject mismatch")
    updates = {}
    coverage = deepcopy(position.market_signal_coverage or {})
    incoming_coverage = incoming.market_signal_coverage or {}
    if incoming.current_price > 0 and _newer(incoming.source_as_of, position.source_as_of, cutoff, equal=equal):
        updates.update({key: getattr(incoming, key) for key in PRICE_FIELDS})
        # Keep the normalized quote's cumulative values together. Its daily
        # volume ratio and investor-flow clocks remain independent.
        for stage in ("price", "selectedPrice"):
            coverage.pop(stage, None)
            if stage in incoming_coverage:
                coverage[stage] = deepcopy(incoming_coverage[stage])
    if _newer(incoming.indicator_as_of, position.indicator_as_of, cutoff, equal=equal):
        updates.update({key: getattr(incoming, key) for key in INDICATOR_FIELDS})
    for stage, fields in STAGE_FIELDS.items():
        new = incoming_coverage.get(stage) or {}
        old = coverage.get(stage) or {}
        if _newer(new.get("sourceAsOf"), old.get("sourceAsOf"), cutoff, equal=equal):
            updates.update({key: getattr(incoming, key) for key in fields})
            coverage[stage] = deepcopy(new)
    if not updates:
        return position
    merged = replace(position, **updates, market_signal_coverage=coverage)
    for window in (5, 20, 60):
        average = getattr(merged, "ma" + str(window))
        if average > 0:
            setattr(merged, "ma" + str(window) + "_distance", (merged.current_price / average - 1) * 100)
    return merged


def market_position_from_cache(position, payload):
    """Public collector DTO -> market-only proposal, retaining missing fields."""
    names = PRICE_FIELDS + INDICATOR_FIELDS + [key for group in STAGE_FIELDS.values() for key in group]
    aliases = {"source_transport": "transport", "return_1d": "return1d", "return_3d": "return3d", "return_5d": "return5d"}
    values = {}
    for key in names:
        parts = key.split("_")
        alias = aliases.get(key, parts[0] + "".join(part.title() for part in parts[1:]))
        if alias in payload:
            values[key] = payload[alias]
    # Missing source clocks are unknown, never the previous observation's time.
    values["source_as_of"] = str(payload.get("sourceAsOf") or "")
    values["indicator_as_of"] = str(payload.get("indicatorAsOf") or "")
    values["market_signal_coverage"] = deepcopy(payload.get("marketSignalCoverage") or {})
    return replace(position, **values)


def revalue_market_snapshot(snapshot):
    """Refresh marked values; preserve broker totals, account basis and cash."""
    rates = {"KRW": 1.0}
    for position in snapshot.positions + snapshot.watchlist:
        if position.exchange_rate > 0:
            rates[position.currency] = position.exchange_rate
        if position.is_cash() or position.current_price <= 0:
            continue
        value = position.quantity * position.current_price
        position.market_value = position.mark_to_market_value = value
        position.market_value_krw = position.mark_to_market_value_krw = value * position.exchange_rate
        position.profit_loss = position.quantity * (position.current_price - position.average_price)
        position.profit_loss_krw = position.profit_loss * position.exchange_rate
        if position.average_price > 0:
            position.profit_loss_rate = (position.current_price / position.average_price - 1) * 100
        if position.account_value_basis == "mark-to-market":
            position.account_value_krw = position.mark_to_market_value_krw
    basis = str(snapshot.portfolio.valuation_basis or "")
    if not basis:
        basis = next((p.account_value_basis for p in snapshot.positions if p.account_value_basis), "")
    snapshot.portfolio = portfolio_summary(
        snapshot.positions, snapshot.portfolio.cash, "KRW", rates,
        valuation_basis=basis, account_id=snapshot.account_id,
        observed_at=snapshot.generated_at, external_signals=snapshot.external_signals,
    )
    snapshot.metadata["valuation"] = {key: value for key, value in snapshot.portfolio.valuation.items() if key != "positions"}


def preserve_newer_market_observations(snapshot, previous, cutoff):
    """Late account reconciliation keeps newer quotes, with new quantities."""
    changed = False
    for group in ("positions", "watchlist"):
        old = getattr(previous, group)
        by_symbol = {p.symbol: p for p in old}
        current = getattr(snapshot, group)
        merged = [merge_market_position(p, by_symbol[p.symbol], cutoff)
                  if p.symbol in by_symbol else p for p in current]
        changed |= any(a != b for a, b in zip(current, merged))
        setattr(snapshot, group, merged)
    if changed:
        snapshot.generated_at = cutoff
        revalue_market_snapshot(snapshot)
    return changed
