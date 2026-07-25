"""Raw ABox market-state delta projection.

This module intentionally records observed state changes only.  Materiality,
trend transitions, and investment implications belong to RuleBox/TypeDB native
rules.  The operational scheduler may still use its own admission-control
materiality check, but that result is never projected as an investment ABox
fact or fed back into investment inference.
"""

from typing import Dict, List

from .market_data import number
from .ontology_contracts import PortfolioOntology
from .ontology_schema import add_entity, add_relation
from .portfolio import Position


def prior_monitor_state(runtime_context: Dict[str, object]) -> Dict[str, object]:
    metadata = runtime_context.get("metadata") if isinstance(runtime_context, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    previous = {}
    if isinstance(runtime_context, dict):
        previous = (
            metadata.get("previousMonitorState")
            or metadata.get("previousState")
            or runtime_context.get("previousMonitorState")
            or {}
        )
    return previous if isinstance(previous, dict) else {}


def previous_position_state(runtime_context: Dict[str, object], symbol: str, source: str = "holding") -> Dict[str, object]:
    previous = prior_monitor_state(runtime_context)
    container_key = "watchlist" if source == "watchlist" else "positions"
    rows = previous.get(container_key) if isinstance(previous.get(container_key), dict) else {}
    item = rows.get(str(symbol or "").upper()) if isinstance(rows, dict) else {}
    return item if isinstance(item, dict) else {}


def position_market_state_payload(position: Position) -> Dict[str, object]:
    return {
        "currentPrice": number(position.current_price),
        "profitLossRate": number(position.profit_loss_rate),
        "ma20Distance": number(position.ma20_distance),
        "ma60Distance": number(position.ma60_distance),
        "ma20Slope": number(position.ma20_slope),
        "ma60Slope": number(position.ma60_slope),
        "changeRate": number(position.change_rate),
        "volumeRatio": number(position.volume_ratio),
        "tradeStrength": number(position.trade_strength),
        "tradingValue": number(position.trading_value),
        "orderbookImbalance": number(position.bid_ask_imbalance),
        "dataQuality": str(position.data_quality or ""),
    }


def previous_market_state_payload(previous: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(previous, dict):
        return {}
    pairs = {
        "currentPrice": ("currentPrice", "current_price", "price"),
        "profitLossRate": ("profitLossRate", "profit_loss_rate"),
        "ma20Distance": ("ma20Distance", "ma20_distance"),
        "ma60Distance": ("ma60Distance", "ma60_distance"),
        "ma20Slope": ("ma20Slope", "ma20_slope"),
        "ma60Slope": ("ma60Slope", "ma60_slope"),
        "changeRate": ("changeRate", "change_rate", "priceChangeRate"),
        "volumeRatio": ("volumeRatio", "volume_ratio"),
        "tradeStrength": ("tradeStrength", "trade_strength"),
        "tradingValue": ("tradingValue", "trading_value"),
        "orderbookImbalance": ("orderbookImbalance", "bidAskImbalance", "bid_ask_imbalance"),
        "dataQuality": ("dataQuality", "data_quality"),
    }
    normalized: Dict[str, object] = {}
    for target_key, keys in pairs.items():
        for key in keys:
            if key in previous and previous.get(key) not in (None, ""):
                normalized[target_key] = previous.get(key)
                break
    return normalized


def changed_market_fields(previous: Dict[str, object], current: Dict[str, object]) -> List[str]:
    if not current:
        return []
    if not previous:
        return [key for key, value in current.items() if value not in (None, "", 0, 0.0)]
    fields: List[str] = []
    numeric_fields = [
        "currentPrice",
        "profitLossRate",
        "ma20Distance",
        "ma60Distance",
        "ma20Slope",
        "ma60Slope",
        "changeRate",
        "volumeRatio",
        "tradeStrength",
        "tradingValue",
        "orderbookImbalance",
    ]
    for key in numeric_fields:
        if abs(number(current.get(key)) - number(previous.get(key))) >= 0.0001:
            fields.append(key)
    if str(current.get("dataQuality") or "") != str(previous.get("dataQuality") or ""):
        fields.append("dataQuality")
    return fields


def numeric_delta(previous: object, current: object) -> Dict[str, object]:
    previous_number = number(previous)
    current_number = number(current)
    has_numeric_value = any(value not in (None, "") for value in [previous, current]) and (
        previous_number != 0
        or current_number != 0
        or str(previous).strip() in {"0", "0.0"}
        or str(current).strip() in {"0", "0.0"}
    )
    if not has_numeric_value:
        return {"delta": None, "deltaPct": None, "value": None}
    delta = current_number - previous_number
    delta_pct = ((current_number / previous_number) - 1) * 100 if previous_number else 0.0
    return {
        "delta": round(delta, 6),
        "deltaPct": round(delta_pct, 3),
        "value": round(current_number, 6),
    }


def add_fact_change_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    symbol: str,
    position: Position,
    source: str,
    runtime_context: Dict[str, object],
) -> None:
    """Persist raw current-vs-prior facts without a Python materiality verdict."""
    previous = previous_market_state_payload(previous_position_state(runtime_context, symbol, source))
    current = position_market_state_payload(position)
    changed_fields = changed_market_fields(previous, current)
    trigger = "market-update" if previous else "first-observation"
    fact_id = add_entity(graph, "fact-change", symbol + ":market-data-update", (position.name or symbol) + " 시장 데이터 델타", {
        "tboxClass": "FactChange",
        "tboxClasses": ["Observation", "FactChange"],
        "symbol": symbol,
        "source": source,
        "trigger": trigger,
        "field": "marketData",
        # The aggregate count is only a raw bookkeeping value. RuleBox rules
        # use the per-field delta nodes below for meaningful change patterns.
        "value": len(changed_fields) if previous else 0,
        "changedFields": changed_fields,
        "previous": previous,
        "current": current,
        "hasPreviousObservation": bool(previous),
        "dataState": "sufficient" if previous else "partial",
    })
    relation_props = {
        "source": "market-data-delta",
        "field": "marketData",
        "evidenceRole": "context",
        "dataState": "sufficient" if previous else "partial",
        "aiInfluenceLabel": "시장 데이터 원시 델타",
    }
    add_relation(graph, stock_id, fact_id, "HAS_OBSERVATION", weight=1.0, properties=relation_props)
    add_relation(graph, fact_id, stock_id, "CHANGES_FACT", weight=1.0, properties=relation_props)

    for field_name in changed_fields:
        field_name = str(field_name or "").strip()
        if not field_name:
            continue
        previous_value = previous.get(field_name)
        current_value = current.get(field_name)
        delta = numeric_delta(previous_value, current_value)
        field_entity_props = {
            "tboxClass": "FactChange",
            "tboxClasses": ["Observation", "FactChange"],
            "symbol": symbol,
            "source": source,
            "trigger": trigger,
            "field": field_name,
            "previousValue": previous_value,
            "currentValue": current_value,
            "value": delta.get("value"),
            "delta": delta.get("delta"),
            "deltaPct": delta.get("deltaPct"),
            "changedFields": [field_name],
            "hasPreviousObservation": bool(previous),
            "dataState": "sufficient" if previous else "partial",
        }
        field_fact_id = add_entity(
            graph,
            "fact-change",
            symbol + ":market-data-update:" + field_name,
            (position.name or symbol) + " " + field_name + " 원시 델타",
            field_entity_props,
        )
        field_props = {
            **relation_props,
            "field": field_name,
            "delta": delta.get("delta"),
            "deltaPct": delta.get("deltaPct"),
            "aiInfluenceLabel": field_name + " 원시 델타",
        }
        add_relation(graph, stock_id, field_fact_id, "HAS_OBSERVATION", weight=1.0, properties=field_props)
        add_relation(graph, field_fact_id, stock_id, "CHANGES_FACT", weight=1.0, properties=field_props)
        add_relation(graph, fact_id, field_fact_id, "AFFECTS", weight=1.0, properties=field_props)
