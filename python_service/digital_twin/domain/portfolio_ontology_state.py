from typing import Dict, List

from .materiality import market_change_materiality
from .market_data import number
from .ontology_decision_state import (
    CHANGE_STATE_LABELS,
    CONFLICT_STATE_LABELS,
    DATA_STATE_LABELS,
    REVIEW_LEVEL_LABELS,
)
from .ontology_contracts import PortfolioOntology, entity_id
from .ontology_observation_quality import profile_for_domain
from .ontology_schema import add_entity, add_relation
from .ontology_threshold_policy import ontology_threshold_policy_from_context
from .portfolio import Position
from .portfolio_ontology_market_concepts import pct_distance_safe
from .portfolio_ontology_runtime_concepts import runtime_settings
from .trend_transitions import trend_transition_assessment


def prior_monitor_state(runtime_context: Dict[str, object]) -> Dict[str, object]:
    metadata = runtime_context.get("metadata") if isinstance(runtime_context, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    previous = {}
    if isinstance(runtime_context, dict):
        previous = metadata.get("previousMonitorState") or metadata.get("previousState") or runtime_context.get("previousMonitorState") or {}
    return previous if isinstance(previous, dict) else {}

def previous_position_state(runtime_context: Dict[str, object], symbol: str, source: str = "holding") -> Dict[str, object]:
    previous = prior_monitor_state(runtime_context)
    container_key = "watchlist" if source == "watchlist" else "positions"
    rows = previous.get(container_key) if isinstance(previous.get(container_key), dict) else {}
    item = rows.get(str(symbol or "").upper()) if isinstance(rows, dict) else {}
    return item if isinstance(item, dict) else {}

def previous_decision_state(runtime_context: Dict[str, object], symbol: str) -> Dict[str, object]:
    previous = prior_monitor_state(runtime_context)
    rows = previous.get("decisions") if isinstance(previous.get("decisions"), dict) else {}
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
    for key in ["dataQuality"]:
        if str(current.get(key) or "") != str(previous.get(key) or ""):
            fields.append(key)
    return fields

def add_relation_state_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    symbol: str,
    position: Position,
    source: str,
    runtime_context: Dict[str, object],
    relation_context: Dict[str, object],
) -> None:
    previous_position = previous_position_state(runtime_context, symbol, source)
    previous_decision = previous_decision_state(runtime_context, symbol)
    current_decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    facts = relation_context.get("facts") if isinstance(relation_context.get("facts"), dict) else {}
    review_level = str(relation_context.get("reviewLevel") or current_decision.get("reviewLevel") or "observe")
    data_state = str(relation_context.get("dataState") or current_decision.get("dataState") or "partial")
    context_change_state = str(relation_context.get("changeState") or current_decision.get("changeState") or "unchanged")
    conflict_state = str(relation_context.get("conflictState") or current_decision.get("conflictState") or "context-only")
    current_price = number(position.current_price)
    previous_price = number(previous_position.get("current_price") or previous_position.get("currentPrice"))
    previous_pnl = number(previous_position.get("profit_loss_rate") or previous_position.get("profitLossRate"))
    current_pnl = number(position.profit_loss_rate)
    previous_ma20_distance = number(previous_position.get("ma20_distance") or previous_position.get("ma20Distance"))
    current_ma20_distance = number(position.ma20_distance)
    state_id = add_entity(graph, "relation-state", symbol + ":current", (position.name or symbol) + " 현재 관계 상태", {
        "tboxClass": "RelationStateSnapshot",
        "tboxClasses": ["RelationStateSnapshot", "ReasoningCard"],
        "symbol": symbol,
        "source": source,
        "decisionLabel": current_decision.get("label"),
        "reviewLevel": review_level,
        "reviewLevelLabel": REVIEW_LEVEL_LABELS.get(review_level, REVIEW_LEVEL_LABELS["observe"]),
        "dataState": data_state,
        "dataStateLabel": DATA_STATE_LABELS.get(data_state, DATA_STATE_LABELS["partial"]),
        "changeState": context_change_state,
        "changeStateLabel": CHANGE_STATE_LABELS.get(context_change_state, CHANGE_STATE_LABELS["unchanged"]),
        "conflictState": conflict_state,
        "conflictStateLabel": CONFLICT_STATE_LABELS.get(conflict_state, CONFLICT_STATE_LABELS["context-only"]),
        "price": round(current_price, 4),
        "profitLossRate": round(current_pnl, 2),
        "ma20Distance": round(current_ma20_distance, 2),
        "activeRuleIds": [
            str(item.get("ruleId") or item.get("rule_id") or "")
            for item in relation_context.get("activeRules") or []
            if isinstance(item, dict)
        ][:8],
    })
    add_relation(graph, stock_id, state_id, "HAS_REASONING_CARD", weight=1.0, properties={"source": "relation-state", "aiInfluenceLabel": "현재 관계 상태", "evidenceRole": "context"})
    if not previous_position and not previous_decision:
        return
    previous_state_id = add_entity(graph, "relation-state", symbol + ":previous", (position.name or symbol) + " 이전 관계 상태", {
        "tboxClass": "PreviousInsight",
        "tboxClasses": ["PreviousInsight", "RelationStateSnapshot"],
        "symbol": symbol,
        "source": source,
        "decisionLabel": previous_decision.get("decision"),
        "reviewLevel": previous_decision.get("review_level") or previous_decision.get("reviewLevel") or "normal",
        "dataState": previous_decision.get("data_state") or previous_decision.get("dataState") or "partial",
        "changeState": previous_decision.get("change_state") or previous_decision.get("changeState") or "unchanged",
        "price": round(previous_price, 4),
        "profitLossRate": round(previous_pnl, 2),
        "ma20Distance": round(previous_ma20_distance, 2),
    })
    add_relation(graph, state_id, previous_state_id, "CHANGED_FROM", weight=1.0, properties={"source": "previous-monitor-state", "aiInfluenceLabel": "이전 상태 대비 변화"})
    transition_labels: List[str] = []
    price_delta = pct_distance_safe(current_price, previous_price)
    pnl_delta = current_pnl - previous_pnl if previous_position else 0.0
    ma20_delta = current_ma20_distance - previous_ma20_distance if previous_position else 0.0
    if abs(price_delta) >= 1.5:
        transition_labels.append("가격 " + signed_pct_text(price_delta))
    if abs(pnl_delta) >= 1.0:
        transition_labels.append("손익률 " + signed_pct_text(pnl_delta, suffix="%p"))
    if abs(ma20_delta) >= 2.0:
        transition_labels.append("20일선 괴리 " + signed_pct_text(ma20_delta, suffix="%p"))
    selected_rule = str(current_decision.get("selectedRuleId") or "")
    if selected_rule and selected_rule != str(previous_decision.get("selectedRuleId") or ""):
        transition_labels.append("선택 규칙 변화 " + selected_rule)
    current_action = str(current_decision.get("label") or "")
    previous_action = str(previous_decision.get("decision") or previous_decision.get("label") or "")
    if current_action and previous_action and current_action != previous_action:
        transition_labels.append("판단 변화 " + previous_action + " → " + current_action)
    if facts.get("breakdownAcceleration"):
        transition_labels.append("하락 속도 증가 조건 성립")
    if context_change_state != "unchanged" and not transition_labels:
        transition_labels.append(CHANGE_STATE_LABELS.get(context_change_state, context_change_state))
    if not transition_labels:
        return
    if current_action and previous_action and current_action != previous_action:
        change_state = "direction-changed"
    elif context_change_state != "unchanged":
        change_state = context_change_state
    elif facts.get("breakdownAcceleration") or pnl_delta <= -1.0 or price_delta <= -1.5 or ma20_delta <= -2.0:
        change_state = "worsening"
    elif pnl_delta >= 1.0 or price_delta >= 1.5 or ma20_delta >= 2.0:
        change_state = "improving"
    else:
        change_state = "new-condition"
    transition_id = add_entity(graph, "signal-transition", symbol + ":state-change", "관계 상태 변화", {
        "tboxClass": "SignalTransition",
        "symbol": symbol,
        "changeState": change_state,
        "changeStateLabel": CHANGE_STATE_LABELS.get(change_state, CHANGE_STATE_LABELS["new-condition"]),
        "priceDeltaPct": round(price_delta, 2),
        "profitLossRateDeltaPct": round(pnl_delta, 2),
        "ma20DistanceDeltaPct": round(ma20_delta, 2),
        "labels": transition_labels[:6],
    })
    evidence_role = "risk" if change_state == "worsening" else "support" if change_state == "improving" else "context"
    props = {"source": "previous-monitor-state", "aiInfluenceLabel": "관계 상태 변화", "polarity": evidence_role, "evidenceRole": evidence_role, "changeState": change_state}
    add_relation(graph, stock_id, transition_id, "HAS_OBSERVATION", weight=1.0, properties=props)
    add_relation(graph, transition_id, state_id, "CONFIRMED_OVER", weight=1.0, properties=props)
    if selected_rule and any(token in selected_rule for token in ["breakdown", "blocked", "loss"]):
        add_relation(graph, transition_id, previous_state_id, "FAILED_AFTER", weight=1.0, properties=props)

def add_trend_transition_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    thesis_id: str,
    symbol: str,
    position: Position,
    source: str,
    runtime_context: Dict[str, object],
    observation_profiles: Dict[str, Dict[str, object]] = None,
) -> None:
    metadata = runtime_context.get("metadata") if isinstance(runtime_context, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    history = metadata.get("monitorStateHistory") if isinstance(metadata.get("monitorStateHistory"), list) else []
    previous = metadata.get("previousMonitorState") if isinstance(metadata.get("previousMonitorState"), dict) else {}
    threshold_policy = ontology_threshold_policy_from_context(runtime_context).temporal_pattern
    assessment = trend_transition_assessment(position, history=history, previous_state=previous, source=source, threshold_policy=threshold_policy)
    points = list(assessment.get("points") or [])
    if not points:
        return
    trend_observation = profile_for_domain(observation_profiles or {}, "trend")
    phase_classes = {
        "falling_to_rebound": ["TrendTransition", "ReversalSignal"],
        "sideways_to_breakout": ["TrendTransition", "ConsolidationBreak"],
        "sideways_to_breakdown": ["TrendTransition", "ConsolidationBreak"],
        "rising_to_distribution": ["TrendTransition", "DecelerationSignal"],
        "falling_acceleration": ["TrendTransition", "AccelerationSignal"],
    }
    path_id = add_entity(graph, "price-path", symbol, symbol + " 가격 경로", {
        "tboxClass": "PricePath",
        "symbol": symbol,
        "source": source,
        "pointCount": len(points),
        "points": points,
        "lastPriceDeltaPct": assessment.get("lastPriceDeltaPct"),
        "ma20DistanceDeltaPct": assessment.get("ma20DistanceDeltaPct"),
        **trend_observation,
    })
    add_relation(graph, stock_id, path_id, "HAS_PRICE_PATH", weight=min(1.0, len(points) / 6.0), properties={
        "source": "monitor-state-history",
        "aiInfluenceLabel": "최근 가격 경로",
    })
    previous_phase = assessment.get("previousPhase") if isinstance(assessment.get("previousPhase"), dict) else {}
    current_phase = assessment.get("currentPhase") if isinstance(assessment.get("currentPhase"), dict) else {}
    current_phase_key = str(current_phase.get("phase") or "unknown")
    current_phase_id = add_entity(graph, "trend-phase", symbol + ":current:" + current_phase_key, str(current_phase.get("label") or current_phase_key), {
        "tboxClass": "TrendPhase",
        "symbol": symbol,
        "phase": current_phase_key,
        "role": "current",
        "dataState": str(current_phase.get("dataState") or "partial"),
    })
    add_relation(graph, path_id, current_phase_id, "HAS_TREND_PHASE", weight=1.0, properties={
        "source": "trend-transition",
        "aiInfluenceLabel": "현재 추세 국면",
        "evidenceRole": "context",
        "dataState": str(current_phase.get("dataState") or "partial"),
    })
    previous_phase_key = str(previous_phase.get("phase") or "unknown")
    if previous_phase_key != "unknown":
        previous_phase_id = add_entity(graph, "trend-phase", symbol + ":previous:" + previous_phase_key, str(previous_phase.get("label") or previous_phase_key), {
            "tboxClass": "TrendPhase",
            "symbol": symbol,
            "phase": previous_phase_key,
            "role": "previous",
            "dataState": str(previous_phase.get("dataState") or "partial"),
        })
        add_relation(graph, current_phase_id, previous_phase_id, "CHANGED_FROM", weight=1.0, properties={
            "source": "trend-transition",
            "aiInfluenceLabel": "추세 국면 전이",
        })
    transition_type = str(assessment.get("transitionType") or "none")
    if transition_type == "none":
        return
    transition_id = add_entity(graph, "trend-transition", symbol + ":" + transition_type, str(assessment.get("label") or transition_type), {
        "tboxClass": "TrendTransition",
        "tboxClasses": phase_classes.get(transition_type, ["TrendTransition"]),
        "symbol": symbol,
        "source": source,
        "transitionType": transition_type,
        "evidenceRole": str(assessment.get("evidenceRole") or assessment.get("polarity") or "context"),
        "reviewLevel": str(assessment.get("reviewLevel") or "observe"),
        "dataState": str(assessment.get("dataState") or "partial"),
        "changeState": str(assessment.get("changeState") or "new-condition"),
        "previousPhase": previous_phase_key,
        "currentPhase": current_phase_key,
        "lastPriceDeltaPct": assessment.get("lastPriceDeltaPct"),
        "ma20DistanceDeltaPct": assessment.get("ma20DistanceDeltaPct"),
        "volumeRatio": assessment.get("volumeRatio"),
        **trend_observation,
    })
    polarity = str(assessment.get("polarity") or "context")
    props = {
        "source": "trend-transition",
        "polarity": polarity,
        "aiInfluenceLabel": str(assessment.get("label") or "추세 전이"),
        "transitionType": transition_type,
        "evidenceRole": str(assessment.get("evidenceRole") or polarity or "context"),
        "reviewLevel": str(assessment.get("reviewLevel") or "observe"),
        "dataState": str(assessment.get("dataState") or "partial"),
        "changeState": str(assessment.get("changeState") or "new-condition"),
    }
    add_relation(graph, stock_id, transition_id, "HAS_TREND_TRANSITION", weight=1.0, properties=props)
    add_relation(graph, transition_id, path_id, "CONFIRMED_OVER", weight=1.0, properties=props)
    hint = str(assessment.get("relationHint") or "")
    if hint:
        add_relation(graph, transition_id, current_phase_id, hint, weight=1.0, properties=props)
    if not thesis_id:
        return
    if polarity == "support":
        add_relation(graph, transition_id, thesis_id, "SUPPORTS_THESIS", weight=1.0, properties=props)
    elif polarity == "risk":
        add_relation(graph, transition_id, thesis_id, "WEAKENS_THESIS", weight=1.0, properties=props)

def signed_pct_text(value: object, suffix: str = "%") -> str:
    numeric = round(number(value), 2)
    return ("+" if numeric > 0 else "") + str(numeric).rstrip("0").rstrip(".") + suffix

def numeric_delta(previous: object, current: object) -> Dict[str, object]:
    previous_number = number(previous)
    current_number = number(current)
    has_numeric_value = any(value not in (None, "") for value in [previous, current]) and (
        previous_number != 0 or current_number != 0 or str(previous).strip() in {"0", "0.0"} or str(current).strip() in {"0", "0.0"}
    )
    if not has_numeric_value:
        return {"delta": None, "deltaPct": None, "value": None}
    delta = current_number - previous_number
    delta_pct = pct_distance_safe(current_number, previous_number) if previous_number else 0.0
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
    previous = previous_market_state_payload(previous_position_state(runtime_context, symbol, source))
    current = position_market_state_payload(position)
    fields = changed_market_fields(previous, current)
    assessment = market_change_materiality(
        symbol,
        previous,
        current,
        {"fields": fields},
        runtime_settings(runtime_context),
    )
    payload = assessment.to_dict()
    changed_fields = list(payload.get("changedFields") or [])
    matched_conditions = list(payload.get("matchedConditions") or [])
    review_level = str(payload.get("reviewLevel") or "normal")
    data_state = str(payload.get("dataState") or "partial")
    change_state = str(payload.get("changeState") or "unchanged")
    evidence_role = str(payload.get("evidenceRole") or "context")
    fact_id = add_entity(graph, "fact-change", symbol + ":market-data-update", (position.name or symbol) + " 시장 데이터 변경", {
        "tboxClass": "FactChange",
        "tboxClasses": ["Observation", "FactChange"],
        "symbol": symbol,
        "source": source,
        "trigger": payload.get("trigger"),
        "field": "marketData",
        "changedFields": changed_fields,
        "previous": previous,
        "current": current,
        "materialityPassed": bool(payload.get("passed")),
        "reviewLevel": review_level,
        "dataState": data_state,
        "changeState": change_state,
        "evidenceRole": evidence_role,
        "matchedConditions": matched_conditions,
        "reason": payload.get("reason"),
    })
    relation_props = {
        "source": "materiality-gate",
        "polarity": evidence_role,
        "aiInfluenceLabel": "시장 데이터 의미 변화",
        "materialityPassed": bool(payload.get("passed")),
        "reviewLevel": review_level,
        "dataState": data_state,
        "changeState": change_state,
        "evidenceRole": evidence_role,
        "matchedConditions": matched_conditions,
    }
    add_relation(graph, stock_id, fact_id, "HAS_OBSERVATION", weight=1.0, properties=relation_props)
    add_relation(graph, fact_id, stock_id, "CHANGES_FACT", weight=1.0, properties=relation_props)

    for field in changed_fields:
        field_name = str(field or "").strip()
        if not field_name:
            continue
        previous_value = previous.get(field_name)
        current_value = current.get(field_name)
        delta = numeric_delta(previous_value, current_value)
        field_props = {
            **relation_props,
            "field": field_name,
            "delta": delta.get("delta"),
            "deltaPct": delta.get("deltaPct"),
            "aiInfluenceLabel": field_name + " 의미 변화",
        }
        field_entity_props = {
            "tboxClass": "FactChange",
            "tboxClasses": ["Observation", "FactChange"],
            "symbol": symbol,
            "source": source,
            "trigger": payload.get("trigger"),
            "field": field_name,
            "previousValue": previous_value,
            "currentValue": current_value,
            "value": delta.get("value"),
            "delta": delta.get("delta"),
            "deltaPct": delta.get("deltaPct"),
            "materialityPassed": bool(payload.get("passed")),
            "reviewLevel": review_level,
            "dataState": data_state,
            "changeState": change_state,
            "evidenceRole": evidence_role,
            "reason": payload.get("reason"),
            "changedFields": [field_name],
        }
        field_fact_id = add_entity(
            graph,
            "fact-change",
            symbol + ":market-data-update:" + field_name,
            (position.name or symbol) + " " + field_name + " 변경",
            field_entity_props,
        )
        add_relation(graph, stock_id, field_fact_id, "HAS_OBSERVATION", weight=1.0, properties=field_props)
        add_relation(graph, field_fact_id, stock_id, "CHANGES_FACT", weight=1.0, properties=field_props)
        add_relation(graph, fact_id, field_fact_id, "AFFECTS", weight=1.0, properties=field_props)

    assessment_id = add_entity(graph, "materiality-assessment", symbol + ":market-data-update", (position.name or symbol) + " 중요 변경 평가", {
        "tboxClass": "MaterialityAssessment",
        "tboxClasses": ["MaterialityAssessment", "ValidationAssessment", "ActionabilityAssessment"],
        **payload,
    })
    add_relation(graph, fact_id, assessment_id, "TRIGGERS_MATERIALITY_ASSESSMENT", weight=1.0, properties=relation_props)
    gate_relation = "PASSES_IMPORTANCE_GATE" if bool(payload.get("passed")) else "BLOCKED_BY_IMPORTANCE_GATE"
    add_relation(graph, assessment_id, entity_id("importance-gate", "materiality-first"), gate_relation, weight=1.0, properties=relation_props)

    if matched_conditions:
        threshold_id = add_entity(graph, "threshold-crossing", symbol + ":market-data-update", (position.name or symbol) + " 기준선/변동성 변화", {
            "tboxClass": "ThresholdCrossing",
            "symbol": symbol,
            "matchedConditions": matched_conditions,
            "facts": payload.get("facts") if isinstance(payload.get("facts"), dict) else {},
            "reviewLevel": review_level,
            "dataState": data_state,
            "changeState": change_state,
            "evidenceRole": evidence_role,
        })
        add_relation(graph, assessment_id, threshold_id, "HAS_THRESHOLD_CROSSING", weight=1.0, properties=relation_props)
