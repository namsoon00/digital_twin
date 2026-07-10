from typing import Dict, Iterable, List

from .investment_research import build_active_investment_opinion
from .accounts import message_delivery_profile
from .materiality import market_change_materiality
from .market_data import clamp, number
from .ontology_contracts import (
    OntologyBelief,
    OntologyEntity,
    OntologyEvidence,
    OntologyOpinion,
    OntologyRelation,
    PortfolioOntology,
    entity_id,
)
from .ontology_prompting import (
    ONTOLOGY_PROMPT_VERSION,
    build_ai_inference_packet,
    build_investment_opinion_prompt,
    build_reasoning_cards,
    entity_label_map,
    portfolio_worldview,
    prompt_payload,
    relation_key,
)
from .ontology_schema import (
    abox_lifecycle_metadata,
    abox_relation_properties,
    abox_properties,
    apply_abox_lifecycle,
    add_entity,
    add_relation,
    ontology_abox,
    ontology_tbox,
    tbox_entities,
    tbox_relations,
)
from .ontology_external_abox import (
    add_external_signal_concepts,
    add_position_macro_context_concepts,
    add_symbol_external_signal_concepts,
)
from .ontology_reasoning import apply_graph_reasoning
from .ontology_relation_rules import evaluate_position_relation_rules
from .portfolio_ontology_runtime_concepts import (
    add_account_delivery_profile_concepts,
    add_decision_item_concepts,
    add_operational_world_concepts,
    add_runtime_metadata_concepts,
    add_runtime_setting_concepts,
    add_strategy_world_concepts,
    runtime_settings,
    is_holding_position,
    is_watchlist_position,
    position_source,
)
from .portfolio_ontology_market_concepts import (
    add_data_source_concept,
    add_legacy_model_score_concepts,
    add_metric_concepts,
    add_price_level_and_liquidity_concepts,
    data_quality_score,
    pct_distance_safe,
    smart_money_score,
    symbol_key,
    trend_dynamic_facts,
    trend_score,
)
from .portfolio_ontology_exposure_concepts import (
    add_market_exposure_concepts,
    add_portfolio_factor_exposure_concepts,
    add_position_factor_concepts,
    benchmark_for_position,
    factor_labels_for_position,
)
from .portfolio_ontology_research_concepts import (
    add_research_document_concept,
    add_research_evidence_concepts,
    event_relation_properties,
    event_tbox_classes,
    evidence_document_shape,
)
from .portfolio import PortfolioSummary, Position
from .trend_transitions import trend_transition_assessment


def unique_list(values: Iterable[str]) -> List[str]:
    seen = set()
    rows: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append(text)
    return rows


def bounded_number(value: object, lower: float = 0.0, upper: float = 100.0) -> float:
    return clamp(number(value), lower, upper)


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
        "updatedAt": str(position.updated_at or ""),
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
        "updatedAt": ("updatedAt", "updated_at"),
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






def instrument_tbox_classes(position: Position) -> List[str]:
    market = str(position.market or "").lower()
    symbol = str(position.symbol or "").upper()
    if market in {"crypto", "coin"} or symbol in {"BTC", "ETH", "SOL"}:
        return ["Instrument", "CryptoAsset"]
    if "etf" in str(position.name or "").lower():
        return ["Instrument", "ETF"]
    return ["Instrument", "Equity", "Stock"]


def risk_tbox_classes(label: str) -> List[str]:
    text = str(label or "")
    classes = ["Risk"]
    if any(token in text for token in ["비중", "노출", "집중", "단일 종목"]):
        classes.append("ConcentrationRisk")
    if any(token in text for token in ["수급", "유동성", "거래량", "체결"]):
        classes.append("LiquidityRisk")
    if any(token in text for token in ["추세", "손실", "수익", "가격"]):
        classes.append("MarketRisk")
    if any(token in text for token in ["데이터", "부족", "품질"]):
        classes.append("DataQualityRisk")
    if any(token in text for token in ["기존", "모델", "점수"]):
        classes.append("ModelRisk")
    if any(token in text for token in ["공시", "뉴스", "규제", "이벤트"]):
        classes.append("EventRisk")
    return unique_list(classes)


def compact_string_rows(values: object, limit: int = 5) -> List[str]:
    if not isinstance(values, list):
        return []
    rows: List[str] = []
    for value in values:
        text = " ".join(str(value or "").split())
        if text and text not in rows:
            rows.append(text[:180])
        if len(rows) >= limit:
            break
    return rows


def add_execution_plan_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    active_opinion_id: str,
    symbol: str,
    source: str,
    execution_plan: Dict[str, object],
) -> str:
    if not isinstance(execution_plan, dict) or not execution_plan:
        return ""
    plan_id = add_entity(graph, "execution-plan", symbol + ":" + source, "실행 계획 " + symbol, {
        "tboxClass": "ExecutionPlan",
        "tboxClasses": ["ExecutionPlan", "ReasoningCard"],
        "symbol": symbol,
        "source": source,
        "primaryAction": execution_plan.get("primaryAction"),
        "primaryActionLabel": execution_plan.get("primaryActionLabel"),
        "decisionStage": execution_plan.get("decisionStage"),
        "actionGroup": execution_plan.get("actionGroup"),
        "actionLevel": execution_plan.get("actionLevel"),
        "executionPlan": dict(execution_plan),
    })
    add_relation(graph, stock_id, plan_id, "HAS_EXECUTION_PLAN", weight=0.92, properties={"source": "ontology-execution-plan"})
    add_relation(graph, active_opinion_id, plan_id, "HAS_EXECUTION_PLAN", weight=0.95, properties={"source": "active-investment-opinion"})
    primary_label = str(execution_plan.get("primaryActionLabel") or execution_plan.get("primaryAction") or "").strip()
    if primary_label:
        action_id = add_entity(graph, "action-candidate", symbol + ":" + str(execution_plan.get("primaryAction") or primary_label), primary_label, {
            "tboxClass": "ActionCandidate",
            "symbol": symbol,
            "action": execution_plan.get("primaryAction"),
            "label": primary_label,
        })
        add_relation(graph, plan_id, action_id, "HAS_PRIMARY_ACTION", weight=0.95, properties={"source": "ontology-execution-plan"})
    for index, item in enumerate(compact_string_rows(execution_plan.get("blockedActions"), 5)):
        blocked_id = add_entity(graph, "blocked-action", symbol + ":" + str(index) + ":" + item, item, {
            "tboxClass": "BlockedAction",
            "symbol": symbol,
            "label": item,
        })
        add_relation(graph, plan_id, blocked_id, "BLOCKS_ACTION", weight=0.9, properties={"source": "ontology-execution-plan"})
    for index, item in enumerate(compact_string_rows(execution_plan.get("strengthenConditions"), 5)):
        condition_id = add_entity(graph, "execution-condition", symbol + ":strengthen:" + str(index) + ":" + item, item, {
            "tboxClass": "InvalidationCondition",
            "symbol": symbol,
            "conditionType": "strengthen",
            "label": item,
        })
        add_relation(graph, plan_id, condition_id, "STRENGTHENS_ACTION_IF", weight=0.82, properties={"source": "ontology-execution-plan"})
    for index, item in enumerate(compact_string_rows(execution_plan.get("weakenConditions"), 5)):
        condition_id = add_entity(graph, "execution-condition", symbol + ":weaken:" + str(index) + ":" + item, item, {
            "tboxClass": "InvalidationCondition",
            "symbol": symbol,
            "conditionType": "weaken",
            "label": item,
        })
        add_relation(graph, plan_id, condition_id, "WEAKENS_ACTION_IF", weight=0.82, properties={"source": "ontology-execution-plan"})
    for index, item in enumerate(compact_string_rows(execution_plan.get("nextChecks"), 5)):
        check_id = add_entity(graph, "next-check", symbol + ":" + str(index) + ":" + item, item, {
            "tboxClass": "NextCheck",
            "symbol": symbol,
            "label": item,
        })
        add_relation(graph, plan_id, check_id, "REQUIRES_NEXT_CHECK", weight=0.85, properties={"source": "ontology-execution-plan"})
    return plan_id



def relation_relation_label(relation: OntologyRelation, labels: Dict[str, str]) -> str:
    properties = relation.properties or {}
    explicit = str(properties.get("aiInfluenceLabel") or properties.get("label") or "").strip()
    if explicit:
        return explicit
    source = labels.get(relation.source, relation.source)
    target = labels.get(relation.target, relation.target)
    return source + " " + relation.relation_type + " " + target




def observable_position(position: Position) -> bool:
    return not position.is_cash() and bool(symbol_key(position))


def sector_ratio(portfolio: PortfolioSummary, sector: str) -> float:
    for item in portfolio.sectors:
        if item.get("sector") == sector:
            return number(item.get("ratio"))
    return 0.0


def position_weight(position: Position, portfolio: PortfolioSummary) -> float:
    base = number(portfolio.total) or number(portfolio.invested)
    return (number(position.market_value) / base) * 100 if base else 0.0






def evidence_id(symbol: str, kind: str) -> str:
    return "evidence:" + str(symbol or "portfolio").upper() + ":" + kind


def ontology_action_label(pressure: float, pnl: float, contradictions: List[str], risks: List[str]) -> (str, str):
    if pressure >= 72:
        if pnl < 0:
            return "관계 판단: 손실 구간 보유 이유 재확인", "danger"
        return "관계 판단: 일부 이익 보호", "danger"
    if pressure >= 55:
        if contradictions:
            return "관계 판단: 보유 이유와 반대 신호 점검", "caution"
        return "관계 판단: 비중 축소 후보", "caution"
    if pressure >= 38:
        return "관계 판단: 조건부 보유", "hold"
    if risks:
        return "관계 판단: 보유 이유 유지", "watch"
    return "관계 판단: 보유 유지", "watch"


def build_position_opinion(
    position: Position,
    portfolio: PortfolioSummary,
    legacy_model: Dict[str, object],
) -> OntologyOpinion:
    symbol = symbol_key(position)
    pnl = number(position.profit_loss_rate)
    weight = position_weight(position, portfolio)
    sector_weight = sector_ratio(portfolio, position.sector)
    trend = trend_score(position)
    flow = smart_money_score(position)
    quality = data_quality_score(position)
    supporting: List[str] = []
    contradictions: List[str] = []
    risks: List[str] = []
    opportunities: List[str] = []

    if sector_weight >= 50:
        risks.append(position.sector + " 관련 종목 비중이 매우 높음")
    elif sector_weight >= 35:
        risks.append(position.sector + " 노출이 높은 편")
    if weight >= 30:
        risks.append("단일 종목 비중이 큼")
    if pnl <= -8:
        risks.append("손실이 보유 이유를 다시 확인할 구간")
    elif pnl >= 20:
        risks.append("큰 수익 구간으로 이익 보호 필요")
    if trend <= -8:
        risks.append("추세 관계가 약화")
    elif trend >= 8:
        supporting.append("추세 흐름이 보유 이유를 뒷받침")
        opportunities.append("가격 추세가 우호적")
    if flow <= -25:
        risks.append("외국인·기관 수급 관계가 부정적")
    elif flow >= 25:
        supporting.append("외국인·기관 수급 관계가 우호적")
        opportunities.append("외국인·기관 수급이 보유 이유를 뒷받침")
    if quality < 60:
        contradictions.append("핵심 데이터가 부족해 AI 판단 신뢰도가 낮음")

    risk_score = 18.0
    risk_score += 15.0 if sector_weight >= 50 else 8.0 if sector_weight >= 35 else 0.0
    risk_score += 12.0 if weight >= 30 else 6.0 if weight >= 20 else 0.0
    risk_score += 18.0 if pnl <= -15 else 11.0 if pnl <= -8 else 8.0 if pnl >= 20 else 0.0
    risk_score += clamp(-trend * 0.45, -8.0, 16.0)
    risk_score += clamp(-flow * 0.12, -6.0, 10.0)
    risk_score += clamp((70 - quality) * 0.25, 0.0, 12.0)
    risk_score += min(10.0, len(contradictions) * 5.0)
    ontology_pressure = clamp(risk_score, 0.0, 100.0)
    action, tone = ontology_action_label(ontology_pressure, pnl, contradictions, risks)
    evidence_ids = [
        evidence_id(symbol, "portfolio-exposure"),
        evidence_id(symbol, "trend"),
        evidence_id(symbol, "flow"),
        evidence_id(symbol, "data-quality"),
    ]
    thesis_parts = []
    if supporting:
        thesis_parts.append("지지: " + ", ".join(supporting[:2]))
    if risks:
        thesis_parts.append("리스크: " + ", ".join(risks[:2]))
    if contradictions:
        thesis_parts.append("충돌: " + ", ".join(contradictions[:1]))
    thesis = "; ".join(thesis_parts) or "관계 분석에서 강한 반대 신호는 없고 보유 이유를 유지합니다."
    confidence = clamp(quality * 0.006 + len(evidence_ids) * 0.06 - len(contradictions) * 0.08, 0.2, 0.92)
    return OntologyOpinion(
        symbol=symbol,
        action=action,
        tone=tone,
        conviction=round(confidence * 100, 1),
        ontology_pressure=round(ontology_pressure, 1),
        thesis=thesis,
        supporting_beliefs=supporting[:4],
        contradictions=contradictions[:4],
        dominant_risks=risks[:5],
        opportunities=opportunities[:4],
        legacy_model={
            "role": "not-used-for-scoring",
            "reason": "최종 점수는 온톨로지 관계 규칙과 관계 사실만 사용합니다.",
        },
        evidence_ids=evidence_ids,
    )


def build_watchlist_opinion(position: Position, legacy_model: Dict[str, object]) -> OntologyOpinion:
    symbol = symbol_key(position)
    trend = trend_score(position)
    flow = smart_money_score(position)
    quality = data_quality_score(position)
    risks: List[str] = []
    supporting: List[str] = []
    opportunities: List[str] = []
    contradictions: List[str] = []

    if quality < 60:
        contradictions.append("관심 종목 판단에 필요한 가격·추세 데이터가 부족함")
    if trend <= -8:
        risks.append("진입 후보로 보기에는 추세 관계가 약함")
    elif trend >= 8:
        supporting.append("추세가 진입 관찰 근거를 뒷받침")
        opportunities.append("가격 추세가 우호적")
    if flow <= -25:
        risks.append("외국인·기관 수급 관계가 부정적")
    elif flow >= 25:
        supporting.append("외국인·기관 수급 관계가 우호적")
        opportunities.append("수급이 진입 관찰 근거를 보강")
    if not number(position.current_price):
        contradictions.append("현재가가 없어 가격 기준을 확정할 수 없음")

    observation_pressure = 26.0
    observation_pressure += clamp(trend * 0.35, -10.0, 18.0)
    observation_pressure += clamp(flow * 0.08, -8.0, 12.0)
    observation_pressure += clamp((quality - 55) * 0.18, -8.0, 10.0)
    observation_pressure = clamp(observation_pressure, 0.0, 100.0)
    if observation_pressure >= 55 and not contradictions:
        action = "관심 종목: 관계 우호 관찰"
        tone = "watch"
    elif risks or contradictions:
        action = "관심 종목: 진입 조건 재확인"
        tone = "hold"
    else:
        action = "관심 종목: 진입 기준 대기"
        tone = "hold"
    evidence_ids = [
        evidence_id(symbol, "market-observation"),
        evidence_id(symbol, "trend"),
        evidence_id(symbol, "flow"),
        evidence_id(symbol, "data-quality"),
    ]
    thesis_parts = []
    if supporting:
        thesis_parts.append("지지: " + ", ".join(supporting[:2]))
    if risks:
        thesis_parts.append("리스크: " + ", ".join(risks[:2]))
    if contradictions:
        thesis_parts.append("공백: " + ", ".join(contradictions[:1]))
    thesis = "; ".join(thesis_parts) or "보유가 아닌 관심 종목이므로 현재가, 추세, 수급이 채워질 때 진입 기준을 확인합니다."
    confidence = clamp(quality * 0.006 + len(evidence_ids) * 0.05 - len(contradictions) * 0.08, 0.2, 0.88)
    return OntologyOpinion(
        symbol=symbol,
        action=action,
        tone=tone,
        conviction=round(confidence * 100, 1),
        ontology_pressure=round(observation_pressure, 1),
        thesis=thesis,
        supporting_beliefs=supporting[:4],
        contradictions=contradictions[:4],
        dominant_risks=risks[:5],
        opportunities=opportunities[:4],
        legacy_model={
            "exitPressure": round(number(legacy_model.get("exitPressure")), 1),
            "decisionBasis": legacy_model.get("decisionBasis") or "watchlist-observation",
        },
        evidence_ids=evidence_ids,
    )






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
    current_score = number(current_decision.get("score") or relation_context.get("signalStrength"))
    previous_pressure = number(previous_decision.get("exit_pressure") or previous_decision.get("exitPressure"))
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
        "relationScore": round(current_score, 1),
        "price": round(current_price, 4),
        "profitLossRate": round(current_pnl, 2),
        "ma20Distance": round(current_ma20_distance, 2),
        "activeRuleIds": [
            str(item.get("ruleId") or item.get("rule_id") or "")
            for item in relation_context.get("activeRules") or []
            if isinstance(item, dict)
        ][:8],
    })
    add_relation(graph, stock_id, state_id, "HAS_REASONING_CARD", weight=round(current_score / 100, 4), properties={"source": "relation-state", "aiInfluenceLabel": "현재 관계 상태"})
    if not previous_position and not previous_decision:
        return
    previous_state_id = add_entity(graph, "relation-state", symbol + ":previous", (position.name or symbol) + " 이전 관계 상태", {
        "tboxClass": "PreviousInsight",
        "tboxClasses": ["PreviousInsight", "RelationStateSnapshot"],
        "symbol": symbol,
        "source": source,
        "decisionLabel": previous_decision.get("decision"),
        "relationScore": round(previous_pressure, 1),
        "price": round(previous_price, 4),
        "profitLossRate": round(previous_pnl, 2),
        "ma20Distance": round(previous_ma20_distance, 2),
    })
    add_relation(graph, state_id, previous_state_id, "CHANGED_FROM", weight=1.0, properties={"source": "previous-monitor-state", "aiInfluenceLabel": "이전 상태 대비 변화"})
    transition_score = 0.0
    transition_labels: List[str] = []
    price_delta = pct_distance_safe(current_price, previous_price)
    pnl_delta = current_pnl - previous_pnl if previous_position else 0.0
    ma20_delta = current_ma20_distance - previous_ma20_distance if previous_position else 0.0
    if abs(price_delta) >= 1.5:
        transition_score += min(20.0, abs(price_delta) * 4.0)
        transition_labels.append("가격 " + signed_pct_text(price_delta))
    if abs(pnl_delta) >= 1.0:
        transition_score += min(18.0, abs(pnl_delta) * 5.0)
        transition_labels.append("손익률 " + signed_pct_text(pnl_delta, suffix="%p"))
    if abs(ma20_delta) >= 2.0:
        transition_score += min(16.0, abs(ma20_delta) * 3.0)
        transition_labels.append("20일선 괴리 " + signed_pct_text(ma20_delta, suffix="%p"))
    selected_rule = str(current_decision.get("selectedRuleId") or "")
    if selected_rule and selected_rule != str(previous_decision.get("selectedRuleId") or ""):
        transition_score += 18.0
        transition_labels.append("선택 규칙 변화 " + selected_rule)
    if facts.get("breakdownAcceleration"):
        transition_score += 14.0
    transition_score = clamp(transition_score, 0.0, 100.0)
    if transition_score <= 0:
        return
    transition_id = add_entity(graph, "signal-transition", symbol + ":state-change", "관계 상태 변화", {
        "tboxClass": "SignalTransition",
        "symbol": symbol,
        "changeScore": round(transition_score, 1),
        "priceDeltaPct": round(price_delta, 2),
        "profitLossRateDeltaPct": round(pnl_delta, 2),
        "ma20DistanceDeltaPct": round(ma20_delta, 2),
        "labels": transition_labels[:6],
    })
    props = {"source": "previous-monitor-state", "aiInfluenceLabel": "관계 상태 변화", "polarity": "context"}
    if current_score >= 55 and (pnl_delta < 0 or ma20_delta < 0 or facts.get("breakdownAcceleration")):
        props.update({"polarity": "risk", "opinionImpact": min(16.0, transition_score * 0.22)})
    elif current_score < 55 and (price_delta > 0 or ma20_delta > 0):
        props.update({"polarity": "support", "supportImpact": min(10.0, transition_score * 0.16)})
    add_relation(graph, stock_id, transition_id, "HAS_OBSERVATION", weight=round(transition_score / 100, 4), properties=props)
    add_relation(graph, transition_id, state_id, "CONFIRMED_OVER", weight=round(transition_score / 100, 4), properties=props)
    if selected_rule and any(token in selected_rule for token in ["breakdown", "blocked", "loss"]):
        add_relation(graph, transition_id, previous_state_id, "FAILED_AFTER", weight=round(transition_score / 100, 4), properties=props)


def add_trend_transition_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    thesis_id: str,
    symbol: str,
    position: Position,
    source: str,
    runtime_context: Dict[str, object],
) -> None:
    metadata = runtime_context.get("metadata") if isinstance(runtime_context, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    history = metadata.get("monitorStateHistory") if isinstance(metadata.get("monitorStateHistory"), list) else []
    previous = metadata.get("previousMonitorState") if isinstance(metadata.get("previousMonitorState"), dict) else {}
    assessment = trend_transition_assessment(position, history=history, previous_state=previous, source=source)
    points = list(assessment.get("points") or [])
    if not points:
        return
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
        "confidence": round(number(current_phase.get("confidence")), 3),
    })
    add_relation(graph, path_id, current_phase_id, "HAS_TREND_PHASE", weight=round(number(current_phase.get("confidence")), 4), properties={
        "source": "trend-transition",
        "aiInfluenceLabel": "현재 추세 국면",
    })
    previous_phase_key = str(previous_phase.get("phase") or "unknown")
    if previous_phase_key != "unknown":
        previous_phase_id = add_entity(graph, "trend-phase", symbol + ":previous:" + previous_phase_key, str(previous_phase.get("label") or previous_phase_key), {
            "tboxClass": "TrendPhase",
            "symbol": symbol,
            "phase": previous_phase_key,
            "role": "previous",
            "confidence": round(number(previous_phase.get("confidence")), 3),
        })
        add_relation(graph, current_phase_id, previous_phase_id, "CHANGED_FROM", weight=1.0, properties={
            "source": "trend-transition",
            "aiInfluenceLabel": "추세 국면 전이",
        })
    score = number(assessment.get("score"))
    transition_type = str(assessment.get("transitionType") or "none")
    if score <= 0 or transition_type == "none":
        return
    transition_id = add_entity(graph, "trend-transition", symbol + ":" + transition_type, str(assessment.get("label") or transition_type), {
        "tboxClass": "TrendTransition",
        "tboxClasses": phase_classes.get(transition_type, ["TrendTransition"]),
        "symbol": symbol,
        "source": source,
        "transitionType": transition_type,
        "score": round(score, 1),
        "previousPhase": previous_phase_key,
        "currentPhase": current_phase_key,
        "lastPriceDeltaPct": assessment.get("lastPriceDeltaPct"),
        "ma20DistanceDeltaPct": assessment.get("ma20DistanceDeltaPct"),
        "volumeRatio": assessment.get("volumeRatio"),
    })
    polarity = str(assessment.get("polarity") or "context")
    props = {
        "source": "trend-transition",
        "polarity": polarity,
        "aiInfluenceLabel": str(assessment.get("label") or "추세 전이"),
        "transitionType": transition_type,
        "supportImpact": round(number(assessment.get("supportImpact")), 2),
        "riskImpact": round(number(assessment.get("riskImpact")), 2),
    }
    if props["riskImpact"]:
        props["opinionImpact"] = props["riskImpact"]
    weight = round(min(1.0, score / 100.0), 4)
    add_relation(graph, stock_id, transition_id, "HAS_TREND_TRANSITION", weight=weight, properties=props)
    add_relation(graph, transition_id, path_id, "CONFIRMED_OVER", weight=weight, properties=props)
    hint = str(assessment.get("relationHint") or "")
    if hint:
        add_relation(graph, transition_id, current_phase_id, hint, weight=weight, properties=props)
    if not thesis_id:
        return
    if polarity == "support":
        add_relation(graph, transition_id, thesis_id, "SUPPORTS_THESIS", weight=weight, properties=props)
    elif polarity == "risk":
        add_relation(graph, transition_id, thesis_id, "WEAKENS_THESIS", weight=weight, properties=props)


def signed_pct_text(value: object, suffix: str = "%") -> str:
    numeric = round(number(value), 2)
    return ("+" if numeric > 0 else "") + str(numeric).rstrip("0").rstrip(".") + suffix










def add_instrument_identity_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    position: Position,
    source: str,
) -> None:
    symbol = str(position.symbol or "").upper().strip()
    if not symbol:
        return
    company_label = str(position.name or symbol)
    company_id = add_entity(graph, "company", symbol, company_label, {
        "tboxClass": "Company",
        "symbol": symbol,
        "market": position.market,
        "sector": position.sector,
        "source": source,
    })
    security_id = add_entity(graph, "security", symbol, company_label + " 보통주", {
        "tboxClass": "Security",
        "tboxClasses": instrument_tbox_classes(position) + ["Security"],
        "symbol": symbol,
        "market": position.market,
        "currency": position.currency,
        "source": source,
    })
    peer_key = str(position.market or "unknown") + ":" + str(position.sector or "기타")
    peer_id = add_entity(graph, "peer-group", peer_key, str(position.market or "unknown") + " " + str(position.sector or "기타") + " 피어 그룹", {
        "tboxClass": "PeerGroup",
        "market": position.market,
        "sector": position.sector,
    })
    props = {"source": "instrument-identity", "polarity": "context", "aiInfluenceLabel": "회사-증권 정체성"}
    add_relation(graph, company_id, security_id, "ISSUES", weight=1.0, properties=props)
    add_relation(graph, security_id, stock_id, "REPRESENTS_STOCK", weight=1.0, properties=props)
    add_relation(graph, stock_id, security_id, "REPRESENTS_INSTRUMENT", weight=1.0, properties=props)
    add_relation(graph, company_id, peer_id, "BELONGS_TO", weight=0.85, properties={"source": "peer-map", "aiInfluenceLabel": "피어 그룹"})
    add_relation(graph, stock_id, peer_id, "BELONGS_TO", weight=0.85, properties={"source": "peer-map", "aiInfluenceLabel": "피어 그룹"})


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
    score = number(payload.get("score"))
    fact_id = add_entity(graph, "fact-change", symbol + ":market-data-update", (position.name or symbol) + " 시장 데이터 변경", {
        "tboxClass": "FactChange",
        "tboxClasses": ["Observation", "FactChange"],
        "symbol": symbol,
        "source": source,
        "trigger": payload.get("trigger"),
        "changedFields": list(payload.get("changedFields") or []),
        "previous": previous,
        "current": current,
        "materialityScore": score,
        "materialityPassed": bool(payload.get("passed")),
        "materialityGrade": payload.get("grade"),
        "reason": payload.get("reason"),
    })
    relation_props = {
        "source": "materiality-gate",
        "polarity": "context",
        "aiInfluenceLabel": "시장 데이터 의미 변화",
        "materialityScore": score,
        "materialityPassed": bool(payload.get("passed")),
        "materialityGrade": payload.get("grade"),
    }
    if bool(payload.get("passed")):
        relation_props.update({"polarity": "risk" if score >= 82 else "context", "opinionImpact": min(12.0, score * 0.08)})
    fact_weight = round(max(0.05, min(1.0, score / 100.0)), 4)
    add_relation(graph, stock_id, fact_id, "HAS_OBSERVATION", weight=fact_weight, properties=relation_props)
    add_relation(graph, fact_id, stock_id, "CHANGES_FACT", weight=fact_weight, properties=relation_props)

    assessment_id = add_entity(graph, "materiality-assessment", symbol + ":market-data-update", (position.name or symbol) + " 중요 변경 평가", {
        "tboxClass": "MaterialityAssessment",
        "tboxClasses": ["MaterialityAssessment", "ConfidenceAssessment", "ActionabilityAssessment"],
        **payload,
    })
    add_relation(graph, fact_id, assessment_id, "TRIGGERS_MATERIALITY_ASSESSMENT", weight=fact_weight, properties=relation_props)
    gate_relation = "PASSES_IMPORTANCE_GATE" if bool(payload.get("passed")) else "BLOCKED_BY_IMPORTANCE_GATE"
    add_relation(graph, assessment_id, entity_id("importance-gate", "materiality-first"), gate_relation, weight=fact_weight, properties=relation_props)

    components = payload.get("components") if isinstance(payload.get("components"), dict) else {}
    threshold_components = [key for key in ["priceMove", "ma20Threshold", "ma60Threshold", "volumeConfirmation", "tradeStrength", "orderbookImbalance"] if number(components.get(key))]
    if threshold_components:
        threshold_id = add_entity(graph, "threshold-crossing", symbol + ":market-data-update", (position.name or symbol) + " 기준선/변동성 변화", {
            "tboxClass": "ThresholdCrossing",
            "symbol": symbol,
            "components": threshold_components,
            "facts": payload.get("facts") if isinstance(payload.get("facts"), dict) else {},
            "score": score,
        })
        add_relation(graph, assessment_id, threshold_id, "HAS_THRESHOLD_CROSSING", weight=fact_weight, properties=relation_props)


def build_portfolio_ontology(
    positions: Iterable[Position],
    portfolio: PortfolioSummary,
    legacy_by_symbol: Dict[str, Dict[str, object]] = None,
    external_signals: Dict[str, object] = None,
    portfolio_id: str = "portfolio",
    runtime_context: Dict[str, object] = None,
    include_reasoning_outputs: bool = True,
) -> PortfolioOntology:
    legacy_by_symbol = legacy_by_symbol or {}
    external_signals = external_signals or {}
    runtime_context = runtime_context or {}
    lifecycle_metadata = abox_lifecycle_metadata(
        portfolio_id,
        runtime_context,
        runtime_context.get("activeTBox") if isinstance(runtime_context, dict) else None,
    )
    observed_by_symbol: Dict[str, Position] = {}
    for item in positions:
        if not observable_position(item):
            continue
        key = symbol_key(item)
        previous = observed_by_symbol.get(key)
        if previous is None or (is_watchlist_position(previous) and is_holding_position(item)):
            observed_by_symbol[key] = item
    observed_positions = list(observed_by_symbol.values())
    include_legacy_score_model = bool(legacy_by_symbol) and include_reasoning_outputs
    graph = PortfolioOntology(portfolio_id=portfolio_id)
    graph.entities.extend(tbox_entities())
    graph.relations.extend(tbox_relations())
    portfolio_node_id = entity_id("portfolio", portfolio_id)
    account_context = runtime_context.get("account") if isinstance(runtime_context, dict) else {}
    account_context = account_context if isinstance(account_context, dict) else {}
    account_value = str(account_context.get("accountId") or account_context.get("id") or portfolio_id or "account")
    account_label = str(account_context.get("accountLabel") or account_context.get("label") or account_value or "투자 계좌")
    account_id_value = add_entity(graph, "account", account_value, account_label, {
        "tboxClass": "Account",
        "provider": account_context.get("provider") or (runtime_context.get("provider") if isinstance(runtime_context, dict) else ""),
        "mode": account_context.get("mode") or (runtime_context.get("mode") if isinstance(runtime_context, dict) else ""),
        "status": account_context.get("status") or "",
    })
    graph.entities.append(OntologyEntity(portfolio_node_id, "투자 포트폴리오", "portfolio", abox_properties({
        "total": number(portfolio.total),
        "invested": number(portfolio.invested),
        "cash": number(portfolio.cash),
        "concentration": number(portfolio.concentration),
        "tboxClass": "Portfolio",
    })))
    add_relation(graph, account_id_value, portfolio_node_id, "MANAGES_PORTFOLIO", weight=1.0, properties={"source": "account-context"})
    add_account_delivery_profile_concepts(graph, account_id_value, portfolio_node_id, account_context)
    if include_legacy_score_model:
        graph.entities.append(OntologyEntity(entity_id("concept", "legacy-score-model"), "관계 규칙 점수 모델", "model", abox_properties({
            "role": "research-only",
            "tboxClass": "LegacyScoreModel",
        })))
    graph.entities.append(OntologyEntity(entity_id("concept", "ai-investment-review"), "AI 투자 의견", "ai-review", abox_properties({
        "promptVersion": ONTOLOGY_PROMPT_VERSION,
        "tboxClass": "AIReview",
    })))
    if portfolio.cash:
        graph.entities.append(OntologyEntity(entity_id("asset", "cash"), "대기 현금", "cash", abox_properties({
            "value": number(portfolio.cash),
            "cashRatio": round((number(portfolio.cash) / number(portfolio.total)) * 100, 2) if number(portfolio.total) else 0,
            "tboxClass": "Cash",
        })))
        graph.relations.append(OntologyRelation(
            portfolio_node_id,
            entity_id("asset", "cash"),
            "HOLDS_CASH",
            weight=1.0,
            properties=abox_properties(),
        ))
    add_market_exposure_concepts(graph, portfolio_node_id, portfolio)
    add_portfolio_factor_exposure_concepts(graph, portfolio_node_id, portfolio, observed_positions)
    add_runtime_setting_concepts(graph, portfolio_node_id, runtime_context)
    add_runtime_metadata_concepts(graph, portfolio_node_id, runtime_context)
    add_operational_world_concepts(graph, portfolio_node_id, runtime_context, observed_positions)
    strategy_id = add_strategy_world_concepts(graph, portfolio_node_id, runtime_context)
    add_external_signal_concepts(graph, portfolio_node_id, external_signals, runtime_context)
    watchlist_id = ""
    if any(is_watchlist_position(item) for item in observed_positions):
        watchlist_id = add_entity(graph, "watchlist", portfolio_id, "관심 종목 목록", {
            "tboxClass": "Watchlist",
            "candidateCount": len([item for item in observed_positions if is_watchlist_position(item)]),
        })
        add_relation(graph, portfolio_node_id, watchlist_id, "HAS_WATCHLIST", weight=1.0, properties={"source": "watchlist"})
    sector_weights: Dict[str, float] = {}
    for sector in portfolio.sectors:
        label = str(sector.get("sector") or "기타")
        sector_weights[label] = number(sector.get("ratio"))
        graph.entities.append(OntologyEntity(entity_id("sector", label), label, "sector", abox_properties({**dict(sector), "tboxClass": "Sector"})))
        graph.relations.append(OntologyRelation(
            portfolio_node_id,
            entity_id("sector", label),
            "EXPOSED_TO",
            weight=round(number(sector.get("ratio")) / 100, 4),
            properties=abox_properties({"basis": "sector-weight"}),
        ))
    for position in observed_positions:
        label = str(position.sector or "기타").strip() or "기타"
        if label in sector_weights:
            continue
        sector_weights[label] = 0.0
        graph.entities.append(OntologyEntity(entity_id("sector", label), label, "sector", abox_properties({
            "sector": label,
            "ratio": 0,
            "tboxClass": "Sector",
            "source": "observed-position",
        })))
    for position in observed_positions:
        symbol = symbol_key(position)
        stock_id = entity_id("stock", symbol)
        source = "watchlist" if is_watchlist_position(position) else "holding"
        holding = is_holding_position(position)
        legacy = legacy_by_symbol.get(symbol) or legacy_by_symbol.get(position.symbol) or {}
        stock_tbox_classes = instrument_tbox_classes(position) + (["WatchlistCandidate"] if source == "watchlist" else [])
        graph.entities.append(OntologyEntity(stock_id, position.name or symbol, "stock", abox_properties({
            "symbol": symbol,
            "market": position.market,
            "currency": position.currency,
            "sector": position.sector,
            "source": source,
            "marketValue": number(position.market_value),
            "profitLossRate": number(position.profit_loss_rate),
            "tboxClass": "Stock",
            "tboxClasses": stock_tbox_classes,
        })))
        position_id = add_entity(graph, "position", portfolio_id + ":" + symbol, (position.name or symbol) + (" 관심 행" if source == "watchlist" else " 보유 행"), {
            "tboxClass": "Position",
            "tboxClasses": ["Position"] + (["WatchlistCandidate"] if source == "watchlist" else []),
            "symbol": symbol,
            "source": source,
            "quantity": number(position.quantity),
            "marketValue": number(position.market_value),
            "profitLossRate": number(position.profit_loss_rate),
            "updatedAt": position.updated_at,
        })
        if holding:
            add_relation(graph, portfolio_node_id, position_id, "HAS_POSITION", weight=round(position_weight(position, portfolio) / 100, 4), properties={"source": source})
        elif watchlist_id:
            add_relation(graph, watchlist_id, position_id, "HAS_POSITION", weight=0.15, properties={"source": source})
        add_relation(graph, position_id, stock_id, "REPRESENTS_STOCK", weight=1.0, properties={"source": source})
        for kind, label in [("market", position.market or "unknown"), ("currency", position.currency or "unknown")]:
            tbox_class = "Market" if kind == "market" else "Currency"
            graph.entities.append(OntologyEntity(entity_id(kind, label), label, kind, abox_properties({"tboxClass": tbox_class})))
        graph.relations.append(OntologyRelation(
            portfolio_node_id,
            stock_id,
            "HOLDS" if holding else "WATCHES",
            weight=round(position_weight(position, portfolio) / 100, 4) if holding else 0.15,
            properties=abox_properties({"source": source, "basis": "portfolio-position" if holding else "watchlist"}),
        ))
        graph.relations.extend([
            OntologyRelation(stock_id, entity_id("sector", position.sector or "기타"), "BELONGS_TO", weight=1.0, properties=abox_properties({"source": source})),
            OntologyRelation(stock_id, entity_id("market", position.market or "unknown"), "TRADED_IN", weight=1.0, properties=abox_properties({"source": source})),
            OntologyRelation(stock_id, entity_id("currency", position.currency or "unknown"), "DENOMINATED_IN", weight=1.0, properties=abox_properties({"source": source})),
            OntologyRelation(stock_id, entity_id("concept", "ai-investment-review"), "REQUESTS_OPINION_FROM", weight=1.0, properties=abox_properties({"source": source})),
        ])
        if holding and include_legacy_score_model:
            graph.relations.append(OntologyRelation(
                stock_id,
                entity_id("concept", "legacy-score-model"),
                "USES_EVIDENCE_FROM",
                weight=0.55,
                properties=abox_properties({"source": source}),
            ))
        add_instrument_identity_concepts(graph, stock_id, position, source)
        add_data_source_concept(graph, stock_id, position, source)
        add_metric_concepts(graph, stock_id, position, source)
        add_price_level_and_liquidity_concepts(graph, stock_id, position, source)
        if include_legacy_score_model:
            add_legacy_model_score_concepts(graph, stock_id, symbol, legacy)
        add_symbol_external_signal_concepts(graph, stock_id, symbol, external_signals)
        add_position_factor_concepts(graph, stock_id, portfolio_node_id, position, portfolio)
        add_position_macro_context_concepts(graph, stock_id, position, portfolio, external_signals, runtime_context)
        add_fact_change_concepts(graph, stock_id, symbol, position, source, runtime_context)
        add_trend_transition_concepts(
            graph,
            stock_id,
            "",
            symbol,
            position,
            source,
            runtime_context,
        )
        if not include_reasoning_outputs:
            continue
        opinion = build_position_opinion(position, portfolio, legacy) if holding else build_watchlist_opinion(position, legacy)
        graph.opinions.append(opinion)
        thesis_id = add_entity(graph, "investment-thesis", symbol, (position.name or symbol) + " 투자 가설", {
            "tboxClass": "InvestmentThesis",
            "symbol": symbol,
            "source": source,
            "thesis": opinion.thesis,
            "action": opinion.action,
            "confidence": number(opinion.conviction),
            "ontologyPressure": number(opinion.ontology_pressure),
        })
        active_relation_context = evaluate_position_relation_rules(
            position,
            portfolio,
            external_signals=external_signals,
            settings=runtime_context.get("settings") if isinstance(runtime_context.get("settings"), dict) else {},
            legacy_model=legacy,
            previous_state=previous_position_state(runtime_context, symbol, source),
            previous_decision=previous_decision_state(runtime_context, symbol),
        )
        active_opinion_payload = build_active_investment_opinion(
            position,
            relation_context=active_relation_context,
            ontology_opinion=opinion.to_dict(),
            legacy_model=legacy,
            external_signals=external_signals,
        ).to_dict()
        active_opinion_id = add_entity(graph, "active-opinion", symbol, (position.name or symbol) + " 적극 투자 의견", {
            "tboxClass": "Opinion",
            "tboxClasses": ["Opinion", "ActiveInvestmentOpinion", "AIReview", "Insight"],
            "symbol": symbol,
            "source": source,
            "action": active_opinion_payload.get("action"),
            "actionLabel": active_opinion_payload.get("actionLabel"),
            "conviction": active_opinion_payload.get("conviction"),
            "activeInvestmentOpinion": active_opinion_payload,
        })
        execution_plan_payload = active_opinion_payload.get("executionPlan") if isinstance(active_opinion_payload.get("executionPlan"), dict) else {}
        execution_plan_id = add_execution_plan_concepts(
            graph,
            stock_id,
            active_opinion_id,
            symbol,
            source,
            execution_plan_payload,
        )
        add_research_evidence_concepts(
            graph,
            stock_id,
            thesis_id,
            active_opinion_id,
            symbol,
            active_relation_context.get("facts") if isinstance(active_relation_context.get("facts"), dict) else {},
            external_signals,
        )
        add_relation_state_concepts(
            graph,
            stock_id,
            symbol,
            position,
            source,
            runtime_context,
            active_relation_context,
        )
        add_trend_transition_concepts(
            graph,
            stock_id,
            thesis_id,
            symbol,
            position,
            source,
            runtime_context,
        )
        horizon_id = add_entity(graph, "signal-horizon", symbol + ":" + source, "보유 점검 기간" if holding else "관심 관찰 기간", {
            "tboxClass": "SignalHorizon",
            "symbol": symbol,
            "source": source,
            "horizon": "position-risk-review" if holding else "watchlist-entry-check",
            "validity": "until-next-data-update",
        })
        add_relation(graph, stock_id, thesis_id, "BASED_ON_THESIS", weight=round(number(opinion.conviction) / 100, 4), properties={"source": "ontology-opinion"})
        add_relation(graph, strategy_id, thesis_id, "BASED_ON_THESIS", weight=round(number(opinion.conviction) / 100, 4), properties={"source": "ontology-opinion"})
        add_relation(graph, stock_id, active_opinion_id, "HAS_OPINION", weight=round(number(active_opinion_payload.get("conviction")) / 100, 4), properties={
            "source": "active-investment-opinion",
            "polarity": "context",
            "aiInfluenceLabel": str(active_opinion_payload.get("actionLabel") or active_opinion_payload.get("action") or "적극 투자 의견"),
        })
        add_relation(graph, active_opinion_id, thesis_id, "IMPACTS_OPINION", weight=round(number(active_opinion_payload.get("conviction")) / 100, 4), properties={
            "source": "active-investment-opinion",
            "opinionImpact": number(active_opinion_payload.get("conviction")) / 10,
            "aiInfluenceLabel": str(active_opinion_payload.get("thesis") or "적극 투자 의견"),
        })
        if execution_plan_id:
            add_relation(graph, execution_plan_id, thesis_id, "IMPACTS_OPINION", weight=0.86, properties={
                "source": "ontology-execution-plan",
                "opinionImpact": number(active_opinion_payload.get("conviction")) / 12,
                "aiInfluenceLabel": str(execution_plan_payload.get("primaryActionLabel") or "실행 계획"),
            })
        add_relation(graph, stock_id, horizon_id, "HAS_TIME_HORIZON", weight=1.0, properties={"source": "ontology-opinion"})
        add_relation(graph, thesis_id, horizon_id, "APPLIES_TO_HORIZON", weight=1.0, properties={"source": "ontology-opinion"})
        weight = position_weight(position, portfolio)
        trend = trend_score(position)
        trend_dynamic = trend_dynamic_facts(position)
        flow = smart_money_score(position)
        quality = data_quality_score(position)
        if holding:
            evidence_rows = [
                ("relation-rule", "relationRule", "관계 규칙과 관계 사실을 최종 점수 근거로 사용", opinion.legacy_model, 0.75),
                ("portfolio-exposure", "portfolio", "포트폴리오/섹터 노출 관계", {
                    "positionWeight": round(weight, 2),
                    "sectorWeight": round(sector_weights.get(position.sector, 0.0), 2),
                }, 0.85),
                ("trend", "market-data", "이동평균과 가격 추세 관계", {"trendScore": round(trend, 2), "trendDynamics": trend_dynamic}, 0.65),
                ("flow", "market-data", "외국인·기관 수급 관계", {"smartMoneyScore": round(flow, 2)}, 0.6),
                ("data-quality", "data-quality", "AI 판단에 투입할 데이터 완성도", {"qualityScore": round(quality, 2)}, 0.7),
            ]
        else:
            evidence_rows = [
                ("market-observation", "watchlist", "관심 종목 현재가와 관찰 상태", {
                    "currentPrice": round(number(position.current_price), 4),
                    "market": position.market,
                    "currency": position.currency,
                }, 0.62),
                ("trend", "market-data", "관심 종목 이동평균과 가격 추세 관계", {"trendScore": round(trend, 2), "trendDynamics": trend_dynamic}, 0.55),
                ("flow", "market-data", "관심 종목 외국인·기관 수급 관계", {"smartMoneyScore": round(flow, 2)}, 0.5),
                ("data-quality", "data-quality", "진입 관찰에 투입할 데이터 완성도", {"qualityScore": round(quality, 2)}, 0.65),
            ]
        for kind, source, summary, value, confidence in evidence_rows:
            graph.evidence.append(OntologyEvidence(
                evidence_id(symbol, kind),
                stock_id,
                kind,
                source,
                summary,
                value,
                confidence,
            ))
        for index, label in enumerate(opinion.supporting_beliefs):
            graph.beliefs.append(OntologyBelief("belief:" + symbol + ":support:" + str(index), stock_id, label, "support", 0.72, opinion.evidence_ids))
        for index, label in enumerate(opinion.dominant_risks):
            graph.beliefs.append(OntologyBelief("belief:" + symbol + ":risk:" + str(index), stock_id, label, "risk", 0.7, opinion.evidence_ids))
        for risk in opinion.dominant_risks:
            risk_id = entity_id("risk", risk)
            graph.entities.append(OntologyEntity(risk_id, risk, "risk", abox_properties({
                "tboxClass": "Risk",
                "tboxClasses": risk_tbox_classes(risk),
            })))
            graph.relations.append(OntologyRelation(stock_id, risk_id, "EXPOSED_TO", weight=0.75, evidence_ids=opinion.evidence_ids, properties=abox_relation_properties("EXPOSED_TO")))
            graph.relations.append(OntologyRelation(risk_id, thesis_id, "WEAKENS_THESIS", weight=0.72, evidence_ids=opinion.evidence_ids, properties=abox_relation_properties("WEAKENS_THESIS", {
                "polarity": "context",
                "aiInfluenceLabel": risk,
            })))
            graph.relations.append(OntologyRelation(risk_id, stock_id, "AMPLIFIES_RISK", weight=0.62, evidence_ids=opinion.evidence_ids, properties=abox_relation_properties("AMPLIFIES_RISK", {
                "polarity": "context",
                "aiInfluenceLabel": risk,
            })))
        if opinion.opportunities:
            opportunity_id = entity_id("opportunity", opinion.opportunities[0])
            graph.entities.append(OntologyEntity(opportunity_id, opinion.opportunities[0], "opportunity", abox_properties({"tboxClass": "Opportunity"})))
            graph.relations.append(OntologyRelation(stock_id, opportunity_id, "SUPPORTED_BY", weight=0.65, evidence_ids=opinion.evidence_ids, properties=abox_relation_properties("SUPPORTED_BY")))
            graph.relations.append(OntologyRelation(opportunity_id, thesis_id, "SUPPORTS_THESIS", weight=0.62, evidence_ids=opinion.evidence_ids, properties=abox_relation_properties("SUPPORTS_THESIS", {
                "polarity": "context",
                "aiInfluenceLabel": opinion.opportunities[0],
            })))
        if opinion.contradictions:
            contradiction_id = entity_id("contradiction", opinion.contradictions[0])
            graph.entities.append(OntologyEntity(contradiction_id, opinion.contradictions[0], "contradiction", abox_properties({"tboxClass": "Contradiction"})))
            graph.relations.append(OntologyRelation(stock_id, contradiction_id, "CONTRADICTS", weight=0.8, evidence_ids=opinion.evidence_ids, properties=abox_relation_properties("CONTRADICTS")))
            graph.relations.append(OntologyRelation(contradiction_id, thesis_id, "INVALIDATES_THESIS", weight=0.7, evidence_ids=opinion.evidence_ids, properties=abox_relation_properties("INVALIDATES_THESIS", {
                "polarity": "context",
                "aiInfluenceLabel": opinion.contradictions[0],
            })))
    add_decision_item_concepts(graph, runtime_context)
    if not include_reasoning_outputs:
        graph.entities = dedupe_entities(graph.entities)
        graph.relations = dedupe_relations(graph.relations)
        graph.evidence = dedupe_evidence(graph.evidence)
        apply_abox_lifecycle(graph, lifecycle_metadata)
        graph.worldview = {
            "model": "ontology-abox-facts",
            "runtimeProjectionMode": "abox-facts-only-neo4j-rulebox",
            "description": "Runtime ABox facts are projected for Neo4j RuleBox reasoning; opinions, insights, and inference are produced after graph-store reasoning.",
            "positionCount": len([item for item in observed_positions if is_holding_position(item)]),
            "watchlistCount": len([item for item in observed_positions if is_watchlist_position(item)]),
            "aboxLifecycle": dict(lifecycle_metadata),
            "activeTBox": dict(runtime_context.get("activeTBox") or {}),
        }
        return graph
    apply_graph_reasoning(graph)
    graph.entities = dedupe_entities(graph.entities)
    graph.relations = dedupe_relations(graph.relations)
    graph.evidence = dedupe_evidence(graph.evidence)
    apply_relation_driven_opinions(graph)
    add_ontology_insight_concepts(graph)
    graph.entities = dedupe_entities(graph.entities)
    graph.relations = dedupe_relations(graph.relations)
    apply_abox_lifecycle(graph, lifecycle_metadata)
    graph.reasoning_cards = build_reasoning_cards(graph)
    graph.worldview = portfolio_worldview(graph, portfolio, external_signals)
    graph.worldview["aboxLifecycle"] = dict(lifecycle_metadata)
    graph.worldview["activeTBox"] = dict(runtime_context.get("activeTBox") or {})
    graph.prompt = build_investment_opinion_prompt(graph)
    return graph


def dedupe_entities(items: List[OntologyEntity]) -> List[OntologyEntity]:
    merged: Dict[str, OntologyEntity] = {}
    for item in items:
        if item.entity_id in merged:
            merged[item.entity_id].properties.update(item.properties or {})
            continue
        merged[item.entity_id] = item
    return list(merged.values())


def dedupe_relations(items: List[OntologyRelation]) -> List[OntologyRelation]:
    merged: Dict[str, OntologyRelation] = {}
    for item in items:
        if (item.properties or {}).get("ontologyBox") != "TBox":
            item.properties = abox_relation_properties(item.relation_type, item.properties or {})
        key = "|".join([item.source, item.relation_type, item.target])
        if key in merged:
            merged[key].weight = max(number(merged[key].weight), number(item.weight))
            merged[key].evidence_ids = sorted(set(merged[key].evidence_ids + item.evidence_ids))
            merged[key].properties.update(item.properties or {})
            continue
        merged[key] = item
    return list(merged.values())


def dedupe_evidence(items: List[OntologyEvidence]) -> List[OntologyEvidence]:
    merged: Dict[str, OntologyEvidence] = {}
    for item in items:
        merged[item.evidence_id] = item
    return list(merged.values())


def relation_influence_score(relation: OntologyRelation) -> (float, float):
    properties = relation.properties or {}
    polarity = str(properties.get("polarity") or properties.get("signalPolarity") or "").lower()
    if polarity == "context":
        return 0.0, 0.0
    risk = number(properties.get("opinionImpact") or properties.get("riskImpact") or properties.get("impactScore"))
    support = number(properties.get("supportImpact"))
    if not risk and polarity == "risk":
        risk = number(relation.weight) * 8
    if not support and polarity == "support":
        support = number(relation.weight) * 8
    if relation.relation_type in {"CONTRADICTS", "EXPOSED_TO"} and not support:
        risk = max(risk, number(relation.weight) * (12 if relation.relation_type == "CONTRADICTS" else 8))
    if relation.relation_type == "SUPPORTED_BY" and not risk:
        support = max(support, number(relation.weight) * 8)
    return max(0.0, risk), max(0.0, support)


def relation_influence_rows(graph: PortfolioOntology, stock_id: str) -> List[Dict[str, object]]:
    labels = entity_label_map(graph)
    portfolio_id = entity_id("portfolio", graph.portfolio_id)
    neighbor_ids = {
        relation.source if relation.target == stock_id else relation.target
        for relation in graph.relations
        if relation.source == stock_id or relation.target == stock_id
    }
    rows: List[Dict[str, object]] = []
    for relation in graph.relations:
        if relation.properties.get("ontologyBox") == "TBox":
            continue
        direct = relation.source == stock_id or relation.target == stock_id
        neighbor = relation.source in neighbor_ids or relation.target in neighbor_ids
        portfolio_scope = relation.source == portfolio_id or relation.target == portfolio_id
        if not direct and not neighbor and not portfolio_scope:
            continue
        risk, support = relation_influence_score(relation)
        if not risk and not support:
            continue
        rows.append({
            "relationId": relation_key(relation),
            "scope": "direct" if direct else "neighbor" if neighbor else "portfolio",
            "type": relation.relation_type,
            "source": relation.source,
            "sourceLabel": labels.get(relation.source, relation.source),
            "target": relation.target,
            "targetLabel": labels.get(relation.target, relation.target),
            "riskImpact": round(risk, 2),
            "supportImpact": round(support, 2),
            "label": relation_relation_label(relation, labels),
            "properties": dict(relation.properties or {}),
        })
    return rows


def opinion_action_from_relation_pressure(opinion: OntologyOpinion, source: str, pressure: float) -> (str, str):
    if source == "watchlist":
        if pressure >= 65:
            return "관심 종목: 리스크 관계 우선 점검", "caution"
        if pressure >= 45:
            return "관심 종목: 진입 조건 재확인", "hold"
        return "관심 종목: 진입 기준 대기", "hold"
    return ontology_action_label(pressure, number((opinion.legacy_model or {}).get("profitLossRate")), opinion.contradictions, opinion.dominant_risks)


def apply_relation_driven_opinions(graph: PortfolioOntology) -> None:
    stock_entities = {
        str((item.properties or {}).get("symbol") or "").upper(): item
        for item in graph.entities
        if item.kind == "stock"
    }
    for opinion in graph.opinions:
        stock = stock_entities.get(str(opinion.symbol or "").upper())
        if not stock:
            continue
        properties = stock.properties or {}
        source = str(properties.get("source") or "holding")
        influences = relation_influence_rows(graph, stock.entity_id)
        base_pressure = number((opinion.legacy_model or {}).get("baseOntologyPressure") or opinion.ontology_pressure)
        base_thesis = str((opinion.legacy_model or {}).get("baseThesis") or opinion.thesis or "")
        opinion.legacy_model.setdefault("baseOntologyPressure", round(base_pressure, 1))
        opinion.legacy_model.setdefault("baseThesis", base_thesis)
        opinion.legacy_model.setdefault("profitLossRate", properties.get("profitLossRate", 0))
        risk_impact = sum(number(item.get("riskImpact")) for item in influences)
        support_impact = sum(number(item.get("supportImpact")) for item in influences)
        opinion.relation_influences = influences
        opinion.ontology_pressure = round(clamp(base_pressure + risk_impact - min(18.0, support_impact * 0.65), 0.0, 100.0), 1)
        action, tone = opinion_action_from_relation_pressure(opinion, source, opinion.ontology_pressure)
        opinion.action = action
        opinion.tone = tone
        risk_labels = [item["label"] for item in influences if number(item.get("riskImpact")) > 0]
        support_labels = [item["label"] for item in influences if number(item.get("supportImpact")) > 0]
        for label in risk_labels[:4]:
            if label not in opinion.dominant_risks:
                opinion.dominant_risks.append(label)
        for label in support_labels[:4]:
            if label not in opinion.supporting_beliefs:
                opinion.supporting_beliefs.append(label)
        relation_summary = []
        if risk_labels:
            relation_summary.append("관계 리스크: " + ", ".join(risk_labels[:2]))
        if support_labels:
            relation_summary.append("관계 지지: " + ", ".join(support_labels[:2]))
        opinion.thesis = "; ".join([item for item in [base_thesis] + relation_summary if item])


def insight_type_for_opinion(opinion: OntologyOpinion, stock_source: str) -> str:
    if opinion.contradictions:
        return "contradictionDetected"
    if any("데이터" in str(item) or "부족" in str(item) for item in opinion.dominant_risks + opinion.contradictions):
        return "dataQualityWarning"
    if stock_source == "watchlist":
        return "watchlistEntrySignal"
    if opinion.ontology_pressure >= 55 or opinion.tone in {"danger", "caution"}:
        return "riskIncrease"
    if opinion.opportunities or opinion.supporting_beliefs:
        return "opportunityDetected"
    return "portfolioExposureShift"


def add_ontology_insight_concepts(graph: PortfolioOntology) -> None:
    stock_entities = {
        str((item.properties or {}).get("symbol") or "").upper(): item
        for item in graph.entities
        if item.kind == "stock"
    }
    reasoning_id = entity_id("reasoning-cycle", "ontologyReasoning")
    dispatch_id = entity_id("notification-dispatch", "investmentInsight")
    insight_policy_id = entity_id("insight-policy", "meaningful-change")
    importance_gate_id = entity_id("importance-gate", "materiality-first")
    ai_review_id = entity_id("concept", "ai-investment-review")
    for opinion in graph.opinions:
        stock = stock_entities.get(str(opinion.symbol or "").upper())
        if not stock:
            continue
        source = str((stock.properties or {}).get("source") or "holding")
        insight_type = insight_type_for_opinion(opinion, source)
        materiality_score = max(number(opinion.ontology_pressure), number(opinion.conviction))
        if opinion.contradictions:
            materiality_score = max(materiality_score, 78)
        materiality_threshold = 55.0
        dispatch_candidate = bool(materiality_score >= materiality_threshold or opinion.contradictions or source == "watchlist")
        insight_id = add_entity(graph, "insight", opinion.symbol + ":" + insight_type, stock.label + " " + opinion.action, {
            "tboxClass": "Insight",
            "symbol": opinion.symbol,
            "insightType": insight_type,
            "severity": opinion.tone,
            "score": number(opinion.ontology_pressure),
            "confidence": number(opinion.conviction),
            "thesis": opinion.thesis,
            "relationInfluenceCount": len(opinion.relation_influences or []),
            "dispatchCandidate": dispatch_candidate,
        })
        assessment_id = add_entity(graph, "materiality-assessment", opinion.symbol + ":" + insight_type, stock.label + " 중요 변경 평가", {
            "tboxClass": "MaterialityAssessment",
            "symbol": opinion.symbol,
            "score": round(materiality_score, 1),
            "threshold": materiality_threshold,
            "passed": dispatch_candidate,
            "grade": "watch" if dispatch_candidate else "record",
            "components": {
                "relationStrength": round(number(opinion.ontology_pressure), 1),
                "confidence": round(number(opinion.conviction), 1),
                "contradiction": 78 if opinion.contradictions else 0,
            },
        })
        add_relation(graph, reasoning_id, insight_id, "PRODUCES_INSIGHT", weight=round(number(opinion.conviction) / 100, 4), properties={"source": "ontology-reasoning"})
        add_relation(graph, stock.entity_id, insight_id, "CREATED_FROM_RELATION", weight=round(number(opinion.ontology_pressure) / 100, 4), properties={"source": "ontology-reasoning"})
        add_relation(graph, insight_id, entity_id("insight-type", insight_type), "HAS_INSIGHT_TYPE", weight=1.0, properties={"source": "ontology-reasoning"})
        add_relation(graph, insight_id, insight_policy_id, "EVALUATED_BY", weight=1.0, properties={"source": "ontology-reasoning"})
        add_relation(graph, insight_id, assessment_id, "EVALUATED_BY", weight=round(materiality_score / 100, 4), properties={"source": "materiality-gate"})
        add_relation(graph, assessment_id, importance_gate_id, "PASSES_IMPORTANCE_GATE" if dispatch_candidate else "BLOCKED_BY_IMPORTANCE_GATE", weight=round(materiality_score / 100, 4), properties={"source": "materiality-gate"})
        add_relation(graph, insight_id, dispatch_id, "DISPATCHED_BY", weight=1.0, properties={"source": "ontology-reasoning", "mode": "insight-driven-only"})
        if dispatch_candidate:
            intent_id = add_entity(graph, "notification-intent", opinion.symbol + ":" + insight_type, stock.label + " 알림 의도", {
                "tboxClass": "NotificationIntent",
                "symbol": opinion.symbol,
                "insightType": insight_type,
                "materialityScore": round(materiality_score, 1),
                "status": "send-candidate",
            })
            add_relation(graph, insight_id, intent_id, "CREATES_NOTIFICATION_INTENT", weight=round(materiality_score / 100, 4), properties={"source": "materiality-gate"})
            add_relation(graph, intent_id, dispatch_id, "DISPATCHED_BY", weight=round(materiality_score / 100, 4), properties={"source": "materiality-gate"})
        add_relation(graph, insight_id, ai_review_id, "REQUESTS_OPINION_FROM", weight=1.0, properties={"source": "ontology-reasoning"})
