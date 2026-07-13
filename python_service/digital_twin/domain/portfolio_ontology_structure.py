from typing import Dict, Iterable, List

from .market_data import number
from .ontology_contracts import PortfolioOntology
from .ontology_schema import add_entity, add_relation
from .portfolio import Position
from .portfolio_ontology_market_concepts import symbol_key


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

def compact_decision_drivers(values: object, limit: int = 8) -> List[Dict[str, object]]:
    if not isinstance(values, list):
        return []
    rows: List[Dict[str, object]] = []
    seen = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        summary = " ".join(str(value.get("summary") or value.get("text") or value.get("label") or "").split())
        if not summary:
            continue
        key = (
            str(value.get("category") or ""),
            str(value.get("direction") or ""),
            summary,
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "category": str(value.get("category") or "decision"),
            "direction": str(value.get("direction") or "neutral"),
            "label": str(value.get("label") or "")[:120],
            "summary": summary[:240],
            "importance": number(value.get("importance")),
            "dataKeys": [str(item) for item in value.get("dataKeys") or [] if str(item or "").strip()][:12],
            "source": str(value.get("source") or "ontology-execution-plan")[:120],
        })
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
        "targetRole": execution_plan.get("targetRole"),
        "actionPolicy": execution_plan.get("actionPolicy"),
        "allowedActions": list(execution_plan.get("allowedActions") or []),
        "blockedActionCodes": list(execution_plan.get("blockedActionCodes") or []),
        "actionGroup": execution_plan.get("actionGroup"),
        "actionLevel": execution_plan.get("actionLevel"),
        "executionPlan": dict(execution_plan),
    })
    add_relation(graph, stock_id, plan_id, "HAS_EXECUTION_PLAN", weight=0.92, properties={"source": "ontology-execution-plan"})
    add_relation(graph, active_opinion_id, plan_id, "HAS_EXECUTION_PLAN", weight=0.95, properties={"source": "active-investment-opinion"})
    target_role = str(execution_plan.get("targetRole") or "").strip()
    if target_role:
        role_id = add_entity(graph, "target-role", symbol + ":" + target_role, "대상 역할 " + target_role, {
            "tboxClass": "TargetRole",
            "symbol": symbol,
            "role": target_role,
        })
        add_relation(graph, stock_id, role_id, "HAS_TARGET_ROLE", weight=0.95, properties={"source": "ontology-execution-plan"})
        add_relation(graph, plan_id, role_id, "HAS_TARGET_ROLE", weight=0.95, properties={"source": "ontology-execution-plan"})
    action_policy = str(execution_plan.get("actionPolicy") or "").strip()
    if action_policy:
        policy_id = add_entity(graph, "action-policy", symbol + ":" + action_policy, "행동 정책 " + action_policy, {
            "tboxClass": "ActionPolicy",
            "symbol": symbol,
            "policy": action_policy,
            "targetRole": target_role,
            "allowedActions": list(execution_plan.get("allowedActions") or []),
            "blockedActionCodes": list(execution_plan.get("blockedActionCodes") or []),
        })
        add_relation(graph, stock_id, policy_id, "USES_ACTION_POLICY", weight=0.95, properties={"source": "ontology-execution-plan"})
        add_relation(graph, plan_id, policy_id, "USES_ACTION_POLICY", weight=0.95, properties={"source": "ontology-execution-plan"})
        for index, item in enumerate(compact_string_rows(execution_plan.get("allowedActions"), 6)):
            allowed_id = add_entity(graph, "allowed-action", symbol + ":" + str(index) + ":" + item, item, {
                "tboxClass": "AllowedAction",
                "symbol": symbol,
                "action": item,
            })
            add_relation(graph, policy_id, allowed_id, "ALLOWS_ACTION", weight=0.9, properties={"source": "ontology-execution-plan"})
        for index, item in enumerate(compact_string_rows(execution_plan.get("blockedActionCodes"), 6)):
            blocked_code_id = add_entity(graph, "blocked-action", symbol + ":policy:" + str(index) + ":" + item, item, {
                "tboxClass": "BlockedAction",
                "symbol": symbol,
                "action": item,
            })
            add_relation(graph, policy_id, blocked_code_id, "BLOCKS_ACTION", weight=0.9, properties={"source": "ontology-execution-plan", "policyCode": item})
    primary_label = str(execution_plan.get("primaryActionLabel") or execution_plan.get("primaryAction") or "").strip()
    if primary_label:
        action_id = add_entity(graph, "action-candidate", symbol + ":" + str(execution_plan.get("primaryAction") or primary_label), primary_label, {
            "tboxClass": "ActionCandidate",
            "symbol": symbol,
            "action": execution_plan.get("primaryAction"),
            "label": primary_label,
        })
        add_relation(graph, plan_id, action_id, "HAS_PRIMARY_ACTION", weight=0.95, properties={"source": "ontology-execution-plan"})
    for index, item in enumerate(compact_decision_drivers(execution_plan.get("decisionDrivers"), 8)):
        label = item.get("label") or item.get("summary") or "판단 근거"
        driver_id = add_entity(graph, "decision-driver", symbol + ":" + source + ":" + str(index) + ":" + str(label), str(label), {
            "tboxClass": "DecisionDriver",
            "tboxClasses": ["DecisionDriver", "ReasoningCard"],
            "symbol": symbol,
            "category": item.get("category"),
            "direction": item.get("direction"),
            "label": label,
            "summary": item.get("summary"),
            "importance": item.get("importance"),
            "dataKeys": item.get("dataKeys"),
            "source": item.get("source"),
        })
        weight = max(0.35, min(0.98, (number(item.get("importance")) or 60.0) / 100.0))
        add_relation(
            graph,
            plan_id,
            driver_id,
            "HAS_DECISION_DRIVER",
            weight=weight,
            properties={
                "source": "ontology-execution-plan",
                "category": item.get("category"),
                "direction": item.get("direction"),
                "importance": item.get("importance"),
                "aiInfluenceLabel": item.get("summary"),
            },
        )
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

def observable_position(position: Position) -> bool:
    return not position.is_cash() and bool(symbol_key(position))

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
