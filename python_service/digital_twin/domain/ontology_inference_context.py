from typing import Dict, Iterable, List, Optional

from .market_data import number
from .ontology_relation_rules import (
    OntologyRuleMatch,
    build_ai_prompt_context,
    decision_stage_by_key,
    execution_plan_from_relation_context,
    position_signal_facts,
    score_band,
    strength_label,
)
from .portfolio import AccountSnapshot, PortfolioSummary, Position


NEO4J_RELATION_CONTEXT_VERSION = "neo4j-inferencebox-relation-context-v1"

RULE_STAGE_BY_ID = {
    "graph.loss_guard.breakdown.v1": "LOSS_REDUCE",
    "graph.profit_protect.trend_break.v1": "PROFIT_PARTIAL",
    "graph.watchlist.pullback.entry.v1": "ENTRY_WATCH",
    "graph.materiality.alert_candidate.v1": "RELATION_WATCH",
    "graph.holding.trend_transition.risk.v1": "LOSS_REDUCE",
    "graph.watchlist.trend_transition.support.v1": "ENTRY_SPLIT_BUY",
    "graph.flow.sell_pressure.v1": "LIQUIDITY_REVIEW",
    "graph.flow.accumulation.entry.v1": "ENTRY_SPLIT_BUY",
    "graph.news.direct_material_risk.v1": "LOSS_REDUCE",
    "graph.news.direct_material_support.v1": "ENTRY_SPLIT_BUY",
    "graph.disclosure.event_risk.v1": "LOSS_REDUCE",
    "entry.pullback.supported.v1": "ENTRY_SPLIT_BUY",
    "entry.momentum.confirmed.v1": "ENTRY_READY",
    "entry.wait_for_confirmation.v1": "ENTRY_WAIT",
    "graph.liquidity.execution_guard.v1": "LIQUIDITY_REVIEW",
}


def inferencebox_from_snapshot(snapshot: AccountSnapshot) -> Dict[str, object]:
    metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
    ontology = metadata.get("ontology") if isinstance(metadata.get("ontology"), dict) else {}
    projection = ontology.get("neo4j") if isinstance(ontology.get("neo4j"), dict) else ontology.get("projection")
    if not isinstance(projection, dict):
        return {}
    inference = projection.get("inferenceBox") if isinstance(projection.get("inferenceBox"), dict) else {}
    return dict(inference or {}) if isinstance(inference, dict) else {}


def relation_contexts_from_snapshot(
    snapshot: AccountSnapshot,
    settings: Optional[Dict[str, object]] = None,
) -> Dict[str, Dict[str, object]]:
    inferencebox = inferencebox_from_snapshot(snapshot)
    if not inferencebox:
        return {}
    positions = [
        item
        for item in list(snapshot.positions or []) + list(snapshot.watchlist or [])
        if getattr(item, "symbol", "") and not item.is_cash()
    ]
    result: Dict[str, Dict[str, object]] = {}
    holding_symbols = {str(item.symbol or "").upper() for item in snapshot.positions or [] if getattr(item, "symbol", "") and not item.is_cash()}
    for position in positions:
        symbol = str(position.symbol or "").upper().strip()
        if not symbol or symbol in result:
            continue
        source = "holding" if symbol in holding_symbols else "watchlist"
        context = relation_context_from_inferencebox(
            position,
            snapshot.portfolio,
            inferencebox,
            external_signals=snapshot.external_signals,
            settings=settings,
            source=source,
        )
        if context:
            result[symbol] = context
    return result


def relation_context_from_inferencebox(
    position: Position,
    portfolio: PortfolioSummary,
    inferencebox: Dict[str, object],
    external_signals: Optional[Dict[str, object]] = None,
    settings: Optional[Dict[str, object]] = None,
    source: str = "",
    prompt_id: str = "holdingTiming",
) -> Dict[str, object]:
    symbol = str(position.symbol or "").upper().strip()
    if not symbol or not isinstance(inferencebox, dict):
        return {}
    if str(inferencebox.get("status") or "").lower() not in {"ok", "partial", ""}:
        return {}
    if not bool(inferencebox.get("neo4jNativeReasoningUsed")) and not inferencebox.get("relations"):
        return {}
    relations = symbol_inference_relations(symbol, inferencebox.get("relations") or [])
    traces = symbol_inference_traces(symbol, inferencebox.get("traces") or [])
    if not relations and not traces:
        return {}
    facts = position_signal_facts(
        position_with_source(position, source),
        portfolio,
        external_signals or {},
    )
    matches = matches_from_inference(relations, traces)
    if not matches:
        return {}
    decision = decision_from_inference(facts, matches, relations, traces)
    execution_plan = execution_plan_from_relation_context(facts, decision, matches)
    prompt_context = build_ai_prompt_context(prompt_id, facts, matches, settings or {}, execution_plan)
    active_matches = [item for item in matches if item.matched and not item.reference_only]
    max_strength = max([item.strength_score for item in active_matches], default=decision.get("score") or 0)
    evidence_subgraph = evidence_subgraph_packet(position, facts, matches, relations, traces)
    if isinstance(prompt_context, dict):
        prompt_context["evidenceSubgraph"] = evidence_subgraph
    return {
        "engineVersion": NEO4J_RELATION_CONTEXT_VERSION,
        "source": "neo4jInferenceBox",
        "graphStoreUsed": True,
        "fallbackUsed": False,
        "neo4jNativeReasoningUsed": bool(inferencebox.get("neo4jNativeReasoningUsed")),
        "subject": {
            "symbol": facts.get("symbol"),
            "name": facts.get("name"),
            "market": facts.get("market"),
            "sector": facts.get("sector"),
        },
        "facts": facts,
        "matchedRules": [item.to_dict() for item in matches if item.matched],
        "activeRules": [item.to_dict() for item in active_matches],
        "referenceRules": [item.to_dict() for item in matches if item.reference_only],
        "missingData": list(facts.get("missingData") or []),
        "dominantSignals": [item.label for item in active_matches[:3]],
        "signalStrength": round(float(max_strength or 0), 1),
        "signalStrengthLabel": strength_label(max_strength),
        "confidence": round(max([item.confidence for item in active_matches], default=0), 1),
        "decision": decision,
        "executionPlan": execution_plan,
        "evidenceSubgraph": evidence_subgraph,
        "promptContext": prompt_context,
        "neo4jInference": {
            "relations": relations,
            "traces": traces,
            "entityCount": inferencebox.get("entityCount"),
            "relationCount": inferencebox.get("relationCount"),
            "traceCount": inferencebox.get("traceCount"),
            "nativeRelationCount": inferencebox.get("nativeRelationCount"),
        },
    }


def position_with_source(position: Position, source: str) -> Position:
    if not source:
        return position
    try:
        from dataclasses import replace

        return replace(position, source=source)
    except Exception:  # noqa: BLE001 - source is optional context.
        return position


def symbol_inference_relations(symbol: str, rows: Iterable[object]) -> List[Dict[str, object]]:
    result: List[Dict[str, object]] = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        if relation_mentions_symbol(symbol, item):
            result.append(dict(item))
    return result


def symbol_inference_traces(symbol: str, rows: Iterable[object]) -> List[Dict[str, object]]:
    result: List[Dict[str, object]] = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("symbol") or "").upper().strip() == symbol:
            result.append(dict(item))
    return result


def relation_mentions_symbol(symbol: str, item: Dict[str, object]) -> bool:
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return False
    for key in ["symbol", "source", "target"]:
        value = str(item.get(key) or "").upper()
        if value == symbol or value.endswith(":" + symbol):
            return True
    return False


def matches_from_inference(relations: List[Dict[str, object]], traces: List[Dict[str, object]]) -> List[OntologyRuleMatch]:
    trace_by_rule = {
        str(item.get("ruleId") or ""): item
        for item in traces or []
        if isinstance(item, dict) and str(item.get("ruleId") or "")
    }
    matches: List[OntologyRuleMatch] = []
    seen = set()
    for relation in relations or []:
        rule_id = str(relation.get("ruleId") or "").strip()
        if not rule_id:
            continue
        key = rule_id + "|" + str(relation.get("type") or "") + "|" + str(relation.get("target") or "")
        if key in seen:
            continue
        seen.add(key)
        trace = trace_by_rule.get(rule_id, {})
        score = inference_strength_score(relation)
        label = str(relation.get("aiInfluenceLabel") or relation.get("targetLabel") or trace.get("label") or rule_id)
        evidence = [
            value
            for value in [
                label,
                str(relation.get("type") or ""),
                str(trace.get("label") or ""),
            ]
            if value
        ][:4]
        matches.append(OntologyRuleMatch(
            rule_id=rule_id,
            label=label,
            version=NEO4J_RELATION_CONTEXT_VERSION,
            relation_type=str(relation.get("type") or "INFERRED_RELATION"),
            signal_type="neo4j_inference",
            matched=True,
            strength_score=score,
            strength_label=strength_label(score),
            confidence=round(number(trace.get("confidence") or relation.get("weight") or 0) * 100, 1),
            evidence=evidence,
            missing=[],
            reference_only=False,
            prompt_hint="Neo4j RuleBox InferenceBox에서 생성된 관계를 우선 근거로 사용합니다.",
        ))
    if matches:
        return sorted(matches, key=lambda item: (-item.strength_score, item.rule_id))
    for trace in traces or []:
        rule_id = str(trace.get("ruleId") or "").strip()
        if not rule_id:
            continue
        score = max(55.0, min(100.0, number(trace.get("confidence")) * 100))
        matches.append(OntologyRuleMatch(
            rule_id=rule_id,
            label=str(trace.get("label") or rule_id),
            version=NEO4J_RELATION_CONTEXT_VERSION,
            relation_type="HAS_INFERENCE_TRACE",
            signal_type="neo4j_inference",
            matched=True,
            strength_score=score,
            strength_label=strength_label(score),
            confidence=round(number(trace.get("confidence")) * 100, 1),
            evidence=[str(trace.get("label") or rule_id)],
            missing=[],
            reference_only=False,
            prompt_hint="Neo4j RuleBox InferenceBox trace를 우선 근거로 사용합니다.",
        ))
    return sorted(matches, key=lambda item: (-item.strength_score, item.rule_id))


def inference_strength_score(relation: Dict[str, object]) -> float:
    weight_score = number(relation.get("weight")) * 100
    impact = max(number(relation.get("riskImpact")), number(relation.get("supportImpact")))
    impact_score = 50 + impact * 2.5 if impact else 0
    return round(max(55.0, min(100.0, max(weight_score, impact_score))), 1)


def decision_from_inference(
    facts: Dict[str, object],
    matches: List[OntologyRuleMatch],
    relations: List[Dict[str, object]],
    traces: List[Dict[str, object]],
) -> Dict[str, object]:
    selected = max(matches, key=lambda item: (stage_priority(item.rule_id), item.strength_score, item.confidence))
    relation = next((item for item in relations if str(item.get("ruleId") or "") == selected.rule_id), {})
    stage_key = stage_key_for_inference(selected.rule_id, relation)
    stage = decision_stage_by_key(stage_key)
    band = score_band(selected.strength_score)
    trace = next((item for item in traces if str(item.get("ruleId") or "") == selected.rule_id), {})
    return {
        "label": stage.label,
        "tone": stage.tone,
        "score": round(float(selected.strength_score or 0), 1),
        "basis": "neo4jInferenceBox",
        "selectedRuleId": selected.rule_id,
        "selectedInferenceTraceId": str(trace.get("id") or ""),
        "decisionStage": stage.stage_key,
        "actionGroup": stage.action_group,
        "actionLevel": stage.action_level,
        "scoreBand": band.to_dict(),
        "nextStageAt": stage.next_stage_at,
        "sourceRelationType": str(relation.get("type") or ""),
        "nativeNeo4jReasoned": bool(relation.get("nativeNeo4jReasoned") or trace.get("nativeNeo4jReasoned")),
    }


def stage_key_for_inference(rule_id: str, relation: Dict[str, object]) -> str:
    explicit = stage_key_from_action(str(relation.get("actionGroup") or ""), str(relation.get("actionLevel") or ""))
    if explicit:
        return explicit
    return RULE_STAGE_BY_ID.get(str(rule_id or ""), "RELATION_WATCH")


def stage_key_from_action(action_group: str, action_level: str) -> str:
    group = str(action_group or "")
    level = str(action_level or "")
    if group == "lossControl":
        return "LOSS_CUT" if level in {"action", "urgent"} else "LOSS_REDUCE"
    if group == "profitTake":
        return "PROFIT_SPLIT" if level in {"action", "urgent"} else "PROFIT_PARTIAL"
    if group == "entry":
        return "ENTRY_READY" if level in {"action", "urgent"} else "ENTRY_SPLIT_BUY" if level == "review" else "ENTRY_WATCH"
    if group == "entryWait":
        return "ENTRY_WAIT" if level in {"review", "action", "urgent"} else "ENTRY_WATCH"
    if group == "entryRisk":
        return "ADD_BUY_BLOCKED"
    if group == "executionRisk":
        return "LIQUIDITY_ACTION" if level in {"action", "urgent"} else "LIQUIDITY_REVIEW"
    return ""


def stage_priority(rule_id: str) -> int:
    return {
        "graph.loss_guard.breakdown.v1": 40,
        "graph.holding.trend_transition.risk.v1": 39,
        "graph.news.direct_material_risk.v1": 39,
        "graph.materiality.alert_candidate.v1": 38,
        "entry.wait_for_confirmation.v1": 39,
        "entry.momentum.confirmed.v1": 38,
        "entry.pullback.supported.v1": 38,
        "entry.add_buy.blocked.v1": 37,
        "averaging_down.block.v1": 37,
        "graph.flow.sell_pressure.v1": 36,
        "graph.disclosure.event_risk.v1": 36,
        "graph.profit_protect.trend_break.v1": 35,
        "graph.liquidity.execution_guard.v1": 34,
        "graph.news.direct_material_support.v1": 33,
        "graph.flow.accumulation.entry.v1": 32,
        "graph.watchlist.trend_transition.support.v1": 30,
        "graph.watchlist.pullback.entry.v1": 20,
    }.get(str(rule_id or ""), 10)


def evidence_subgraph_packet(
    position: Position,
    facts: Dict[str, object],
    matches: List[OntologyRuleMatch],
    relations: List[Dict[str, object]],
    traces: List[Dict[str, object]],
) -> Dict[str, object]:
    symbol = str(position.symbol or facts.get("symbol") or "").upper().strip()
    target_id = "stock:" + symbol if symbol else ""
    nodes: Dict[str, Dict[str, object]] = {}

    def add_node(node_id: str, label: str, kind: str, **properties: object) -> None:
        if not node_id:
            return
        nodes[node_id] = {
            "id": node_id,
            "label": str(label or node_id),
            "kind": str(kind or "node"),
            "properties": {key: value for key, value in properties.items() if value not in (None, "", [], {})},
        }

    add_node(target_id, position.name or symbol, "stock", symbol=symbol, market=position.market, sector=position.sector)
    edges: List[Dict[str, object]] = []
    for relation in relations or []:
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("source") or target_id)
        target = str(relation.get("target") or "")
        add_node(source, relation.get("sourceLabel") or source, "source")
        add_node(
            target,
            relation.get("targetLabel") or target,
            str(relation.get("targetKind") or "inference"),
            ruleId=relation.get("ruleId"),
            polarity=relation.get("polarity"),
        )
        edges.append({
            "source": source,
            "target": target,
            "type": str(relation.get("type") or "INFERRED_RELATION"),
            "ruleId": str(relation.get("ruleId") or ""),
            "weight": number(relation.get("weight")),
            "polarity": str(relation.get("polarity") or ""),
            "riskImpact": number(relation.get("riskImpact")),
            "supportImpact": number(relation.get("supportImpact")),
            "label": str(relation.get("aiInfluenceLabel") or relation.get("targetLabel") or ""),
        })
    return {
        "packetId": "ai-context:" + symbol if symbol else "ai-context",
        "target": {
            "id": target_id,
            "symbol": symbol,
            "name": position.name,
            "market": position.market,
            "sector": position.sector,
        },
        "nodes": list(nodes.values())[:24],
        "edges": edges[:32],
        "matchedRuleIds": [item.rule_id for item in matches if item.matched][:12],
        "traces": [
            {
                "id": str(item.get("id") or ""),
                "ruleId": str(item.get("ruleId") or ""),
                "label": str(item.get("label") or ""),
                "confidence": number(item.get("confidence")),
                "matchedConditionIds": list(item.get("matchedConditionIds") or [])[:12],
            }
            for item in traces[:12]
            if isinstance(item, dict)
        ],
        "factSummary": {
            "profitLossRate": facts.get("profitLossRate"),
            "ma20Distance": facts.get("ma20Distance"),
            "ma60Distance": facts.get("ma60Distance"),
            "volumeRatio": facts.get("volumeRatio"),
            "dataQuality": facts.get("dataQuality"),
        },
        "missingData": list(facts.get("missingData") or [])[:8],
    }
