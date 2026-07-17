from typing import Dict, Iterable, List, Optional

from .market_data import clamp, number
from .ontology_decision_policy import decision_stage_from_action, relation_stage_priority
from .ontology_rulebox_contracts import (
    HOLDING_TARGET_ROLE,
    WATCHLIST_ACTION_POLICY,
    WATCHLIST_ALLOWED_ACTIONS,
    WATCHLIST_BLOCKED_ACTIONS,
    WATCHLIST_TARGET_ROLE,
)
from .ontology_threshold_policy import ontology_threshold_policy_from_context
from .ontology_relation_reasoning import (
    OntologyRuleMatch,
    build_ai_prompt_context,
    decision_stage_by_key,
    execution_plan_from_relation_context,
    position_signal_facts,
    score_band,
    strength_label,
)
from .portfolio import AccountSnapshot, PortfolioSummary, Position


TYPEDB_RELATION_CONTEXT_VERSION = "typedb-inferencebox-relation-context-v1"
GRAPH_STORE_RELATION_CONTEXT_VERSION = "graph-store-inferencebox-relation-context-v1"


def ontology_projection_from_metadata(metadata: Dict[str, object]) -> Dict[str, object]:
    metadata = metadata if isinstance(metadata, dict) else {}
    ontology = metadata.get("ontology") if isinstance(metadata.get("ontology"), dict) else {}
    preferred = str(ontology.get("activeGraphStore") or "").strip()
    candidates = []
    if preferred:
        candidates.append(ontology.get(preferred))
    candidates.extend([
        ontology.get("projection"),
        ontology.get("typedb"),
    ])
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def inferencebox_from_snapshot(snapshot: AccountSnapshot) -> Dict[str, object]:
    metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
    projection = ontology_projection_from_metadata(metadata)
    if not isinstance(projection, dict):
        return {}
    inference = projection.get("inferenceBox") if isinstance(projection.get("inferenceBox"), dict) else {}
    if isinstance(inference, dict) and inference:
        inference = dict(inference)
        inference.setdefault("graphStore", projection.get("graphStore") or "")
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
    graph_store = str(inferencebox.get("graphStore") or "").strip() or "graph-store"
    if graph_store.lower() == "typedb" and not bool(inferencebox.get("nativeTypeDbReasoningUsed")):
        return {}
    if not bool(inferencebox.get("nativeTypeDbReasoningUsed")) and not inferencebox.get("relations") and not inferencebox.get("traces"):
        return {}
    source_name = inferencebox_source_name(inferencebox)
    context_version = relation_context_version(source_name)
    relations = symbol_inference_relations(symbol, inferencebox.get("relations") or [])
    traces = symbol_inference_traces(symbol, inferencebox.get("traces") or [])
    if not relations and not traces:
        return {}
    facts = position_signal_facts(
        position_with_source(position, source),
        portfolio,
        external_signals or {},
        settings=settings or {},
    )
    threshold_policy = ontology_threshold_policy_from_context(settings or {})
    matches = matches_from_inference(relations, traces, facts=facts, source_name=source_name, context_version=context_version, threshold_policy=threshold_policy)
    if not matches:
        return {}
    decision = decision_from_inference(facts, matches, relations, traces, source_name=source_name)
    execution_plan = execution_plan_from_relation_context(facts, decision, matches)
    prompt_context = build_ai_prompt_context(prompt_id, facts, matches, settings or {}, execution_plan)
    active_matches = [item for item in matches if item.matched and not item.reference_only]
    max_strength = max([item.strength_score for item in active_matches], default=decision.get("score") or 0)
    score_breakdown = aggregate_score_breakdown(active_matches, threshold_policy)
    evidence_subgraph = evidence_subgraph_packet(position, facts, matches, relations, traces)
    why_now = why_now_packet(facts, active_matches, score_breakdown, decision, relations, traces, inferencebox, threshold_policy)
    signal_conflicts = signal_conflict_packet(facts, active_matches, score_breakdown, relations, threshold_policy)
    inference_timeline = inference_timeline_packet(facts, active_matches, decision, inferencebox)
    threshold_policy_payload = threshold_policy.to_dict()
    if isinstance(prompt_context, dict):
        prompt_context["evidenceSubgraph"] = evidence_subgraph
        prompt_context["scoreBreakdown"] = score_breakdown
        prompt_context["whyNow"] = why_now
        prompt_context["signalConflicts"] = signal_conflicts
        prompt_context["inferenceTimeline"] = inference_timeline
        prompt_context["thresholdPolicy"] = threshold_policy_payload
    return {
        "engineVersion": context_version,
        "source": source_name,
        "graphStore": graph_store,
        "graphStoreUsed": True,
        "fallbackUsed": False,
        "nativeTypeDbReasoningUsed": bool(inferencebox.get("nativeTypeDbReasoningUsed")),
        "typedbBootstrapReasoningUsed": bool(inferencebox.get("typedbBootstrapReasoningUsed")),
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
        "scoreBreakdown": score_breakdown,
        "thresholdPolicy": threshold_policy_payload,
        "whyNow": why_now,
        "signalConflicts": signal_conflicts,
        "inferenceTimeline": inference_timeline,
        "inferenceGenerationId": str(inferencebox.get("inferenceGenerationId") or ""),
        "inferenceGenerationAt": str(inferencebox.get("inferenceGenerationAt") or ""),
        "ruleboxRulesHash": str(inferencebox.get("ruleboxRulesHash") or ""),
        "ruleboxShortHash": str(inferencebox.get("ruleboxShortHash") or ""),
        "ruleboxRuleCount": inferencebox.get("ruleboxRuleCount"),
        "ruleboxConditionCount": inferencebox.get("ruleboxConditionCount"),
        "ruleboxDerivationCount": inferencebox.get("ruleboxDerivationCount"),
        "targetRole": decision.get("targetRole"),
        "actionPolicy": decision.get("actionPolicy"),
        "allowedActions": decision.get("allowedActions") or [],
        "blockedActions": decision.get("blockedActions") or [],
        "decision": decision,
        "executionPlan": execution_plan,
        "evidenceSubgraph": evidence_subgraph,
        "promptContext": prompt_context,
        "graphStoreInference": {
            "source": source_name,
            "graphStore": graph_store,
            "relations": relations,
            "traces": traces,
            "entityCount": inferencebox.get("entityCount"),
            "relationCount": inferencebox.get("relationCount"),
            "traceCount": inferencebox.get("traceCount"),
            "nativeRelationCount": inferencebox.get("nativeRelationCount"),
            "inferenceGenerationId": inferencebox.get("inferenceGenerationId"),
            "inferenceGenerationAt": inferencebox.get("inferenceGenerationAt"),
            "ruleboxRulesHash": inferencebox.get("ruleboxRulesHash"),
            "ruleboxRuleCount": inferencebox.get("ruleboxRuleCount"),
        },
        "typedbInference": {
            "source": source_name,
            "graphStore": graph_store,
            "reasoningMode": str(inferencebox.get("reasoningMode") or ""),
            "relations": relations,
            "traces": traces,
            "entityCount": inferencebox.get("entityCount"),
            "relationCount": inferencebox.get("relationCount"),
            "traceCount": inferencebox.get("traceCount"),
            "nativeRelationCount": inferencebox.get("nativeRelationCount"),
            "inferenceGenerationId": inferencebox.get("inferenceGenerationId"),
            "inferenceGenerationAt": inferencebox.get("inferenceGenerationAt"),
            "ruleboxRulesHash": inferencebox.get("ruleboxRulesHash"),
            "ruleboxRuleCount": inferencebox.get("ruleboxRuleCount"),
        },
    }


def inferencebox_source_name(inferencebox: Dict[str, object]) -> str:
    graph_store = str((inferencebox or {}).get("graphStore") or "").strip().lower()
    source = str((inferencebox or {}).get("source") or "").strip()
    if (
        graph_store == "typedb"
        or source == "typedbInferenceBox"
        or bool((inferencebox or {}).get("nativeTypeDbReasoningUsed"))
    ):
        return "typedbInferenceBox"
    return "graphStoreInferenceBox"


def relation_context_version(source_name: str) -> str:
    return TYPEDB_RELATION_CONTEXT_VERSION if source_name == "typedbInferenceBox" else GRAPH_STORE_RELATION_CONTEXT_VERSION


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


def matches_from_inference(
    relations: List[Dict[str, object]],
    traces: List[Dict[str, object]],
    facts: Optional[Dict[str, object]] = None,
    source_name: str = "typedbInferenceBox",
    context_version: str = TYPEDB_RELATION_CONTEXT_VERSION,
    threshold_policy=None,
) -> List[OntologyRuleMatch]:
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
        score_breakdown = inference_score_breakdown(relation, facts or {}, trace, threshold_policy)
        score = float(score_breakdown.get("finalStrength") or inference_strength_score(relation))
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
            version=context_version,
            relation_type=str(relation.get("type") or "INFERRED_RELATION"),
            signal_type=inference_signal_type(source_name),
            matched=True,
            strength_score=score,
            strength_label=strength_label(score),
            confidence=round(float(score_breakdown.get("dataConfidence") or 0), 1),
            evidence=evidence,
            missing=[],
            reference_only=False,
            prompt_hint=inference_prompt_hint(source_name, "relation"),
            score_breakdown=score_breakdown,
        ))
    if matches:
        return sorted(matches, key=lambda item: (-item.strength_score, item.rule_id))
    for trace in traces or []:
        rule_id = str(trace.get("ruleId") or "").strip()
        if not rule_id:
            continue
        score_policy = (threshold_policy or ontology_threshold_policy_from_context({})).score_breakdown
        score = max(score_policy.trace_floor_score, min(100.0, number(trace.get("confidence")) * 100))
        matches.append(OntologyRuleMatch(
            rule_id=rule_id,
            label=str(trace.get("label") or rule_id),
            version=context_version,
            relation_type="HAS_INFERENCE_TRACE",
            signal_type=inference_signal_type(source_name),
            matched=True,
            strength_score=score,
            strength_label=strength_label(score),
            confidence=round(number(trace.get("confidence")) * 100, 1),
            evidence=[str(trace.get("label") or rule_id)],
            missing=[],
            reference_only=False,
            prompt_hint=inference_prompt_hint(source_name, "trace"),
            score_breakdown={
                "ruleReliability": round(max(0.0, min(100.0, number(trace.get("confidence")) * 100)), 1),
                "riskPressure": 0.0,
                "supportEvidence": 0.0,
                "netRiskPressure": 0.0,
                "dataConfidence": round(max(0.0, min(100.0, number(trace.get("confidence")) * 100)), 1),
                "actionability": score_policy.trace_actionability_score,
                "novelty": score_policy.trace_novelty_score,
                "finalStrength": round(score, 1),
                "opposingPressurePenalty": 0.0,
                "thresholdPolicyId": score_policy.policy_id,
                "thresholdPolicyVersion": score_policy.version,
                "thresholdPolicySource": score_policy.source,
                "drivers": ["TypeDB trace confidence"],
            },
        ))
    return sorted(matches, key=lambda item: (-item.strength_score, item.rule_id))


def inference_signal_type(source_name: str) -> str:
    if source_name == "typedbInferenceBox":
        return "typedb_inference"
    return "graph_store_inference"


def inference_prompt_hint(source_name: str, unit: str) -> str:
    store_label = "TypeDB" if source_name == "typedbInferenceBox" else "그래프 저장소"
    suffix = "trace" if unit == "trace" else "관계"
    return f"{store_label} RuleBox InferenceBox에서 생성된 {suffix}를 우선 근거로 사용합니다."


def _bounded_score(value: object, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return round(clamp(number(value), minimum, maximum), 1)


def _add_driver(drivers: List[str], label: str, value: float, threshold: float = 0.1) -> None:
    if abs(float(value or 0)) >= threshold and label not in drivers:
        drivers.append(label)


def _impact_pressure(value: float, reliability: float, polarity_active: bool) -> float:
    if not value and not polarity_active:
        return 0.0
    base = number(value) * 4.2
    if polarity_active:
        base += reliability * 0.25
    return base


def inference_score_breakdown(
    relation: Dict[str, object],
    facts: Dict[str, object],
    trace: Optional[Dict[str, object]] = None,
    threshold_policy=None,
) -> Dict[str, object]:
    """Explain a TypeDB inference score with data-driven pressure components.

    TypeDB remains the source of the semantic match. This function does not
    decide whether a rule is true; it interprets a true relation with current
    ABox fact magnitudes so equal rule weights do not hide different situations.
    """
    relation = relation or {}
    facts = facts or {}
    trace = trace or {}
    policy = (threshold_policy or ontology_threshold_policy_from_context({})).score_breakdown
    polarity = str(relation.get("polarity") or "").strip().lower()
    relation_type = str(relation.get("type") or "").strip().upper()
    trace_confidence = number(trace.get("confidence")) * 100
    relation_weight = number(relation.get("weight")) * 100
    rule_reliability = _bounded_score(relation_weight or trace_confidence or policy.default_rule_reliability, 0.0, 100.0)
    risk_impact = number(relation.get("riskImpact"))
    support_impact = number(relation.get("supportImpact"))
    risk = _impact_pressure(risk_impact, rule_reliability, polarity in {"risk", "negative"} or "RISK" in relation_type)
    support = _impact_pressure(support_impact, rule_reliability, polarity in {"support", "positive"} or "SUPPORT" in relation_type)
    drivers: List[str] = []
    if risk_impact:
        _add_driver(drivers, "TypeDB 위험 관계 강도", risk_impact)
    if support_impact:
        _add_driver(drivers, "TypeDB 지지 관계 강도", support_impact)

    pnl = number(facts.get("profitLossRate"))
    if facts.get("isHolding"):
        if pnl <= policy.holding_loss_pressure_rate:
            addition = min(36.0, abs(pnl) * 1.15 + max(0.0, abs(pnl) - 8.0) * 0.65)
            risk += addition
            _add_driver(drivers, "손실률 확대", addition)
        elif pnl > 0:
            addition = min(18.0, pnl * 0.8)
            support += addition
            _add_driver(drivers, "수익 구간", addition)

    ma5_distance = number(facts.get("ma5Distance"))
    ma20_distance = number(facts.get("ma20Distance"))
    ma60_distance = number(facts.get("ma60Distance"))
    for key, distance, risk_weight, support_weight, risk_cap, support_cap in [
        ("5일 평균", ma5_distance, 0.7, 0.45, 7.0, 5.0),
        ("20일 평균", ma20_distance, 1.0, 0.75, 18.0, 13.0),
        ("60일 평균", ma60_distance, 1.1, 0.75, 20.0, 14.0),
    ]:
        if distance < 0:
            addition = min(risk_cap, abs(distance) * risk_weight)
            risk += addition
            _add_driver(drivers, key + " 아래", addition)
        elif distance > 0:
            addition = min(support_cap, distance * support_weight)
            support += addition
            _add_driver(drivers, key + " 위", addition)

    price_change = number(facts.get("priceChangeRate"))
    if price_change <= -policy.price_change_pressure_pct:
        addition = min(12.0, abs(price_change) * 1.5)
        risk += addition
        _add_driver(drivers, "당일 가격 약화", addition)
    elif price_change >= policy.price_change_pressure_pct:
        addition = min(10.0, price_change * 1.2)
        support += addition
        _add_driver(drivers, "당일 가격 회복", addition)

    dynamic_risk = number(facts.get("trendDynamicRiskScore"))
    if dynamic_risk:
        addition = min(14.0, max(0.0, dynamic_risk - policy.trend_dynamic_risk_baseline) * 0.35)
        risk += addition
        _add_driver(drivers, "하락 속도/추세 약화", addition)

    trade_strength = number(facts.get("tradeStrength"))
    if trade_strength >= policy.support_trade_strength:
        addition = min(8.0, (trade_strength - 100.0) * 0.2)
        support += addition
        _add_driver(drivers, "체결강도 우위", addition)
    elif 0.0 < trade_strength <= policy.weak_trade_strength:
        addition = min(8.0, (100.0 - trade_strength) * 0.25)
        risk += addition
        _add_driver(drivers, "체결강도 약화", addition)

    bid_ask_imbalance = number(facts.get("bidAskImbalance"))
    if bid_ask_imbalance > 0:
        addition = min(8.0, bid_ask_imbalance * 0.12)
        support += addition
        _add_driver(drivers, "매수 호가 우위", addition)
    elif bid_ask_imbalance < 0:
        addition = min(8.0, abs(bid_ask_imbalance) * 0.12)
        risk += addition
        _add_driver(drivers, "매도 호가 우위", addition)

    investor_score = number(facts.get("investorFlowScore"))
    if investor_score > 0:
        addition = min(14.0, investor_score * 0.14)
        support += addition
        _add_driver(drivers, "외국인·기관 수급 지지", addition)
    elif investor_score < 0:
        addition = min(14.0, abs(investor_score) * 0.14)
        risk += addition
        _add_driver(drivers, "외국인·기관 수급 부담", addition)

    news_momentum = number(facts.get("newsMomentumScore"))
    if news_momentum > 0:
        addition = min(12.0, news_momentum * 0.4)
        support += addition
        _add_driver(drivers, "뉴스 근거 지지", addition)
    elif news_momentum < 0:
        addition = min(12.0, abs(news_momentum) * 0.4)
        risk += addition
        _add_driver(drivers, "뉴스 근거 부담", addition)

    position_weight = number(facts.get("positionAccountWeight") or facts.get("positionWeight"))
    sellable_quantity = number(facts.get("sellableQuantity"))
    quantity = number(facts.get("quantity"))
    actionability = 30.0
    if facts.get("isHolding"):
        actionability += min(24.0, position_weight * 0.9)
        if sellable_quantity or quantity:
            actionability += min(14.0, (sellable_quantity / quantity) * 14.0 if quantity else 8.0)
        if pnl <= policy.loss_actionability_rate:
            actionability += min(18.0, abs(pnl - policy.loss_actionability_rate) * 1.1 + 8.0)
        elif pnl >= policy.profit_actionability_rate:
            actionability += min(12.0, pnl * 0.5)
    elif facts.get("isWatchlist"):
        actionability += 10.0
        if ma5_distance >= 0 and ma20_distance >= 0:
            actionability += 10.0
        if number(facts.get("volumeRatio")) >= policy.watchlist_volume_ratio or number(facts.get("timeAdjustedVolumeRatio")) >= policy.watchlist_volume_ratio:
            actionability += 8.0
    actionability = _bounded_score(actionability, 0.0, 100.0)

    data_quality = number(facts.get("dataQualityScore")) or 100.0
    missing_count = len(facts.get("missingData") or []) if isinstance(facts.get("missingData"), list) else 0
    warning_count = len(facts.get("dataQualityWarnings") or []) if isinstance(facts.get("dataQualityWarnings"), list) else 0
    conflict_penalty = min(10.0, number(facts.get("newsConflictScore")) * 0.4)
    data_confidence = _bounded_score(
        (trace_confidence or rule_reliability) * 0.55
        + data_quality * 0.45
        - missing_count * 2.5
        - warning_count * 3.0
        - conflict_penalty,
        20.0,
        100.0,
    )

    novelty = 25.0
    pnl_delta = number(facts.get("profitLossRateDeltaPct"))
    novelty += min(24.0, abs(pnl_delta) * 5.0)
    novelty += min(18.0, abs(price_change) * 2.2)
    if number(facts.get("directNewsCount")):
        latest_age = number(facts.get("latestDirectNewsAgeMinutes"))
        novelty += 14.0 if not latest_age or latest_age <= policy.recent_news_age_minutes else 6.0
    if relation.get("inferenceTraceId") or trace.get("id"):
        novelty += 6.0
    novelty = _bounded_score(novelty, 0.0, 100.0)

    risk = _bounded_score(risk, 0.0, 100.0)
    support = _bounded_score(support, 0.0, 100.0)
    net_risk = round(risk - support, 1)
    dominant = max(risk, support)
    opposing_penalty = 0.0
    if risk and support:
        opposing_penalty = min(14.0, min(risk, support) * 0.18)
    final = (
        rule_reliability * 0.25
        + dominant * 0.42
        + actionability * 0.18
        + novelty * 0.10
        + data_confidence * 0.05
        - opposing_penalty
    )
    if abs(net_risk) >= policy.net_risk_bonus_threshold:
        final += 4.0
    if data_confidence < policy.data_confidence_penalty_threshold:
        final -= (policy.data_confidence_penalty_threshold - data_confidence) * 0.18
    final = _bounded_score(max(policy.minimum_final_strength, final), 0.0, 100.0)
    return {
        "ruleReliability": rule_reliability,
        "riskPressure": risk,
        "supportEvidence": support,
        "netRiskPressure": net_risk,
        "dataConfidence": data_confidence,
        "actionability": actionability,
        "novelty": novelty,
        "finalStrength": final,
        "opposingPressurePenalty": round(opposing_penalty, 1),
        "thresholdPolicyId": policy.policy_id,
        "thresholdPolicyVersion": policy.version,
        "thresholdPolicySource": policy.source,
        "drivers": drivers[:policy.max_drivers],
    }


def aggregate_score_breakdown(matches: List[OntologyRuleMatch], threshold_policy=None) -> Dict[str, object]:
    policy = (threshold_policy or ontology_threshold_policy_from_context({})).score_breakdown
    breakdowns = [
        item.score_breakdown
        for item in matches or []
        if isinstance(getattr(item, "score_breakdown", None), dict) and item.score_breakdown
    ]
    if not breakdowns:
        return {}
    def max_value(key: str) -> float:
        return round(max([number(item.get(key)) for item in breakdowns], default=0.0), 1)

    risk = max_value("riskPressure")
    support = max_value("supportEvidence")
    final = max_value("finalStrength")
    drivers: List[str] = []
    for item in sorted(breakdowns, key=lambda row: number(row.get("finalStrength")), reverse=True):
        for driver in item.get("drivers") or []:
            text = str(driver or "").strip()
            if text and text not in drivers:
                drivers.append(text)
            if len(drivers) >= policy.max_drivers:
                break
        if len(drivers) >= policy.max_drivers:
            break
    return {
        "ruleReliability": max_value("ruleReliability"),
        "riskPressure": risk,
        "supportEvidence": support,
        "netRiskPressure": round(risk - support, 1),
        "dataConfidence": max_value("dataConfidence"),
        "actionability": max_value("actionability"),
        "novelty": max_value("novelty"),
        "finalStrength": final,
        "opposingPressurePenalty": max_value("opposingPressurePenalty"),
        "thresholdPolicyId": policy.policy_id,
        "thresholdPolicyVersion": policy.version,
        "thresholdPolicySource": policy.source,
        "drivers": drivers,
    }


def why_now_packet(
    facts: Dict[str, object],
    active_matches: List[OntologyRuleMatch],
    score_breakdown: Dict[str, object],
    decision: Dict[str, object],
    relations: List[Dict[str, object]],
    traces: List[Dict[str, object]],
    inferencebox: Dict[str, object],
    threshold_policy=None,
) -> Dict[str, object]:
    policy = (threshold_policy or ontology_threshold_policy_from_context({})).why_now
    facts = facts or {}
    score_breakdown = score_breakdown or {}
    decision = decision or {}
    drivers: List[str] = []
    changed_facts: List[Dict[str, object]] = []

    def add_driver(label: str) -> None:
        text = str(label or "").strip()
        if text and text not in drivers:
            drivers.append(text)

    def add_change(key: str, label: str, current: object, previous: object = None, delta: object = None, threshold: float = 0.0) -> None:
        delta_value = number(delta)
        if threshold and abs(delta_value) < threshold:
            return
        if current in (None, "") and previous in (None, "") and delta in (None, ""):
            return
        changed_facts.append({
            "key": key,
            "label": label,
            "current": current,
            "previous": previous,
            "delta": round(delta_value, 3) if delta not in (None, "") else "",
        })

    pnl_delta = number(facts.get("profitLossRateDeltaPct"))
    if pnl_delta:
        add_change(
            "profitLossRate",
            "손익률 변화",
            facts.get("profitLossRate"),
            facts.get("previousProfitLossRate"),
            pnl_delta,
            threshold=policy.profit_loss_delta_change_pct,
        )
        add_driver("손익률이 이전 관측 대비 " + signed_number_text(pnl_delta) + "%p 변했습니다.")
    price_change = number(facts.get("priceChangeRate"))
    if abs(price_change) >= policy.price_change_driver_pct:
        add_change("priceChangeRate", "현재 가격 변화", facts.get("priceChangeRate"), delta=price_change, threshold=policy.price_change_driver_pct)
        add_driver("현재 가격 변화율이 " + signed_number_text(price_change) + "%입니다.")
    novelty = number(score_breakdown.get("novelty"))
    if novelty >= policy.novelty_driver_score:
        add_driver("새 변화 점수가 " + str(round(novelty, 1)) + "점으로 의미 있는 변경에 가깝습니다.")
    elif novelty:
        add_driver("새 변화 점수는 " + str(round(novelty, 1)) + "점이라 반복 상태인지 함께 봐야 합니다.")
    direct_news_count = number(facts.get("directNewsCount"))
    if direct_news_count:
        add_driver("직접 뉴스/리서치 근거 " + str(int(direct_news_count)) + "건이 추론에 포함됐습니다.")
    stage = str(decision.get("decisionStage") or "").strip()
    if stage:
        add_driver("현재 판단 단계는 " + stage + "입니다.")
    relation_rule_ids = unique_texts([item.rule_id for item in active_matches or []])
    trace_ids = unique_texts([str(item.get("id") or "") for item in traces or [] if isinstance(item, dict)])
    if relation_rule_ids:
        add_driver("이번 세대에서 성립한 핵심 룰: " + ", ".join(relation_rule_ids[:3]))
    should_escalate = bool(
        novelty >= policy.novelty_driver_score
        or abs(pnl_delta) >= policy.escalate_profit_loss_delta_pct
        or abs(price_change) >= policy.escalate_price_change_pct
        or number(score_breakdown.get("riskPressure")) >= policy.escalate_pressure_score
        or number(score_breakdown.get("supportEvidence")) >= policy.escalate_pressure_score
        or number(decision.get("stagePriority")) >= policy.escalate_stage_priority
    )
    return {
        "tboxClass": "WhyNow",
        "thresholdPolicyId": policy.policy_id,
        "thresholdPolicyVersion": policy.version,
        "thresholdPolicySource": policy.source,
        "reasoningQuestion": "왜 지금 다시 봐야 하는가",
        "noveltyScore": round(novelty, 1),
        "shouldEscalate": should_escalate,
        "changeDrivers": drivers[:policy.max_change_drivers],
        "changedFacts": changed_facts[:policy.max_changed_facts],
        "activeRuleIds": relation_rule_ids[:policy.max_rule_ids],
        "traceIds": trace_ids[:policy.max_rule_ids],
        "decisionStage": stage,
        "inferenceGenerationId": str(inferencebox.get("inferenceGenerationId") or ""),
        "inferenceGenerationAt": str(inferencebox.get("inferenceGenerationAt") or ""),
    }


def signal_conflict_packet(
    facts: Dict[str, object],
    active_matches: List[OntologyRuleMatch],
    score_breakdown: Dict[str, object],
    relations: List[Dict[str, object]],
    threshold_policy=None,
) -> Dict[str, object]:
    policy = (threshold_policy or ontology_threshold_policy_from_context({})).signal_conflict
    facts = facts or {}
    score_breakdown = score_breakdown or {}
    risk = number(score_breakdown.get("riskPressure"))
    support = number(score_breakdown.get("supportEvidence"))
    risk_drivers: List[str] = []
    support_drivers: List[str] = []

    def add(target: List[str], label: str) -> None:
        text = str(label or "").strip()
        if text and text not in target:
            target.append(text)

    if number(facts.get("profitLossRate")) <= policy.loss_risk_profit_loss_rate:
        add(risk_drivers, "손실 구간")
    if number(facts.get("ma20Distance")) < 0:
        add(risk_drivers, "20일 평균 아래")
    if number(facts.get("ma60Distance")) < 0:
        add(risk_drivers, "60일 평균 아래")
    if number(facts.get("tradeStrength")) and number(facts.get("tradeStrength")) < policy.weak_trade_strength:
        add(risk_drivers, "체결강도 약화")
    if number(facts.get("investorFlowScore")) < 0:
        add(risk_drivers, "외국인·기관 수급 부담")
    if number(facts.get("tradeStrength")) >= policy.support_trade_strength:
        add(support_drivers, "체결강도 우위")
    if number(facts.get("bidAskImbalance")) >= policy.support_bid_ask_imbalance:
        add(support_drivers, "매수 호가 우위")
    if number(facts.get("investorFlowScore")) > 0:
        add(support_drivers, "외국인·기관 수급 지지")
    if number(facts.get("ma5Distance")) > 0:
        add(support_drivers, "5일 평균 위")
    if number(facts.get("priceChangeRate")) > 0:
        add(support_drivers, "현재 가격 반등")
    for relation in relations or []:
        polarity = str((relation or {}).get("polarity") or "").lower()
        label = str((relation or {}).get("aiInfluenceLabel") or (relation or {}).get("targetLabel") or "").strip()
        if polarity == "risk":
            add(risk_drivers, label)
        elif polarity == "support":
            add(support_drivers, label)

    has_conflict = bool(risk >= policy.minimum_risk_pressure and support >= policy.minimum_support_evidence and risk_drivers and support_drivers)
    if has_conflict and risk > support + policy.dominance_gap:
        conflict_type = "risk-dominant-with-support"
        effect = "위험이 우세하지만 지지 근거 때문에 전량 판단보다 강도 조절이 필요합니다."
    elif has_conflict and support > risk + policy.dominance_gap:
        conflict_type = "support-dominant-with-risk"
        effect = "지지 근거가 우세하지만 위험 근거 때문에 확인 조건이 필요합니다."
    elif has_conflict:
        conflict_type = "mixed-signal"
        effect = "위험과 지지 근거가 동시에 강해 단정적 판단을 낮춰야 합니다."
    else:
        conflict_type = "none"
        effect = "뚜렷한 신호 충돌은 약합니다."
    return {
        "tboxClass": "SignalConflict",
        "thresholdPolicyId": policy.policy_id,
        "thresholdPolicyVersion": policy.version,
        "thresholdPolicySource": policy.source,
        "hasConflict": has_conflict,
        "conflictType": conflict_type,
        "riskPressure": round(risk, 1),
        "supportEvidence": round(support, 1),
        "netRiskPressure": round(risk - support, 1),
        "opposingPressurePenalty": round(number(score_breakdown.get("opposingPressurePenalty")), 1),
        "riskDrivers": risk_drivers[:8],
        "supportDrivers": support_drivers[:8],
        "decisionEffect": effect,
        "activeRuleIds": unique_texts([item.rule_id for item in active_matches or []])[:8],
    }


def inference_timeline_packet(
    facts: Dict[str, object],
    active_matches: List[OntologyRuleMatch],
    decision: Dict[str, object],
    inferencebox: Dict[str, object],
) -> Dict[str, object]:
    facts = facts or {}
    decision = decision or {}
    phases: List[Dict[str, object]] = []
    if facts.get("previousProfitLossRate") not in (None, ""):
        phases.append({
            "phase": "previous-observation",
            "label": "이전 손익 상태",
            "profitLossRate": facts.get("previousProfitLossRate"),
        })
    phases.append({
        "phase": "current-facts",
        "label": "현재 관측 상태",
        "profitLossRate": facts.get("profitLossRate"),
        "priceChangeRate": facts.get("priceChangeRate"),
        "ma20Distance": facts.get("ma20Distance"),
        "ma60Distance": facts.get("ma60Distance"),
    })
    phases.append({
        "phase": "current-inference",
        "label": "현재 추론 세대",
        "decisionStage": decision.get("decisionStage"),
        "selectedRuleId": decision.get("selectedRuleId"),
        "score": decision.get("score"),
        "inferenceGenerationId": str(inferencebox.get("inferenceGenerationId") or ""),
        "inferenceGenerationAt": str(inferencebox.get("inferenceGenerationAt") or ""),
    })
    return {
        "tboxClass": "InferenceTimeline",
        "timelineBasis": "previous-fact-delta-and-current-inference-generation",
        "currentStateKey": timeline_state_key(decision, active_matches),
        "phases": phases,
        "activeRuleIds": unique_texts([item.rule_id for item in active_matches or []])[:8],
    }


def timeline_state_key(decision: Dict[str, object], active_matches: List[OntologyRuleMatch]) -> str:
    return "|".join([
        str((decision or {}).get("decisionStage") or ""),
        str((decision or {}).get("selectedRuleId") or ""),
        ",".join(unique_texts([item.rule_id for item in active_matches or []])[:4]),
    ]).strip("|")


def signed_number_text(value: object) -> str:
    parsed = number(value)
    prefix = "+" if parsed > 0 else ""
    return prefix + str(round(parsed, 2))


def unique_texts(values: Iterable[object]) -> List[str]:
    result: List[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def inference_strength_score(relation: Dict[str, object]) -> float:
    return float(inference_score_breakdown(relation, {}, {}).get("finalStrength") or 0)


def decision_from_inference(
    facts: Dict[str, object],
    matches: List[OntologyRuleMatch],
    relations: List[Dict[str, object]],
    traces: List[Dict[str, object]],
    source_name: str = "typedbInferenceBox",
) -> Dict[str, object]:
    selected = max(matches, key=lambda item: (stage_priority_for_match(item, relations), item.strength_score, item.confidence))
    relation = relation_for_match(selected, relations)
    action_policy = action_policy_from_relation_or_facts(facts, relation)
    stage_key = stage_key_for_inference(selected.rule_id, relation)
    action_policy_applied = False
    if action_policy.get("targetRole") == WATCHLIST_TARGET_ROLE:
        candidate_stage = decision_stage_by_key(stage_key)
        if candidate_stage.action_group in {"lossControl", "profitTake", "rebalance", "distributionRisk"}:
            stage_key = "ADD_BUY_BLOCKED"
            action_policy_applied = True
    stage = decision_stage_by_key(stage_key)
    band = score_band(selected.strength_score)
    trace = next((item for item in traces if str(item.get("ruleId") or "") == selected.rule_id), {})
    label = "신규 진입 보류" if action_policy_applied and action_policy.get("targetRole") == WATCHLIST_TARGET_ROLE else stage.label
    return {
        "label": label,
        "tone": stage.tone,
        "score": round(float(selected.strength_score or 0), 1),
        "scoreBreakdown": dict(selected.score_breakdown or {}),
        "basis": source_name,
        "selectedRuleId": selected.rule_id,
        "selectedInferenceTraceId": str(trace.get("id") or ""),
        "decisionStage": stage.stage_key,
        "actionGroup": stage.action_group,
        "actionLevel": stage.action_level,
        "scoreBand": band.to_dict(),
        "nextStageAt": stage.next_stage_at,
        "sourceRelationType": str(relation.get("type") or ""),
        "stagePriority": relation_stage_priority(relation),
        "stagePolicySource": inference_relation_policy_source(source_name) if relation.get("decisionStage") or relation.get("stagePriority") else "actionFallback",
        **action_policy,
        "actionPolicyApplied": action_policy_applied,
        "nativeTypeDbReasoned": bool(relation.get("nativeTypeDbReasoned") or trace.get("nativeTypeDbReasoned")),
    }


def inference_relation_policy_source(source_name: str) -> str:
    if source_name == "typedbInferenceBox":
        return "typedbInferenceRelation"
    return "graphStoreInferenceRelation"


def action_policy_from_relation_or_facts(facts: Dict[str, object], relation: Dict[str, object]) -> Dict[str, object]:
    facts = facts or {}
    relation = relation or {}
    target_role = str(relation.get("targetRole") or relation.get("target_role") or "").strip()
    if not target_role:
        if facts.get("isWatchlist") is True or str(facts.get("source") or "").strip().lower() == "watchlist":
            target_role = WATCHLIST_TARGET_ROLE
        elif facts.get("isHolding") is True or str(facts.get("source") or "").strip().lower() == "holding":
            target_role = HOLDING_TARGET_ROLE
    action_policy = str(relation.get("actionPolicy") or relation.get("action_policy") or "").strip()
    allowed_actions = string_list(relation.get("allowedActions") or relation.get("allowed_actions"))
    blocked_actions = string_list(relation.get("blockedActions") or relation.get("blocked_actions"))
    if target_role == WATCHLIST_TARGET_ROLE:
        action_policy = action_policy or WATCHLIST_ACTION_POLICY
        allowed_actions = allowed_actions or list(WATCHLIST_ALLOWED_ACTIONS)
        blocked_actions = blocked_actions or list(WATCHLIST_BLOCKED_ACTIONS)
    return {
        "targetRole": target_role,
        "actionPolicy": action_policy,
        "allowedActions": allowed_actions,
        "blockedActions": blocked_actions,
    }


def string_list(value: object) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if value is None or value == "":
        return []
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item or "").strip()]
    return [item.strip() for item in str(value).replace("\n", ",").split(",") if item.strip()]


def stage_key_for_inference(rule_id: str, relation: Dict[str, object]) -> str:
    explicit = str((relation or {}).get("decisionStage") or "").strip()
    if explicit:
        return explicit
    explicit = stage_key_from_action(str((relation or {}).get("actionGroup") or ""), str((relation or {}).get("actionLevel") or ""))
    return explicit or "RELATION_WATCH"


def stage_key_from_action(action_group: str, action_level: str) -> str:
    return decision_stage_from_action(action_group, action_level)


def stage_priority_for_match(match: OntologyRuleMatch, relations: List[Dict[str, object]]) -> int:
    return relation_stage_priority(relation_for_match(match, relations))


def relation_for_match(match: OntologyRuleMatch, relations: List[Dict[str, object]]) -> Dict[str, object]:
    matched = next(
        (
            item
            for item in relations or []
            if str(item.get("ruleId") or "") == match.rule_id
            and str(item.get("type") or "") == match.relation_type
        ),
        None,
    )
    if matched:
        return matched
    return next((item for item in relations or [] if str(item.get("ruleId") or "") == match.rule_id), {})


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
            "decisionStage": str(relation.get("decisionStage") or ""),
            "stagePriority": number(relation.get("stagePriority")),
            "targetRole": str(relation.get("targetRole") or ""),
            "actionPolicy": str(relation.get("actionPolicy") or ""),
            "allowedActions": string_list(relation.get("allowedActions")),
            "blockedActions": string_list(relation.get("blockedActions")),
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
            "profitLossRateDeltaPct": facts.get("profitLossRateDeltaPct"),
            "ma5Distance": facts.get("ma5Distance"),
            "ma20Distance": facts.get("ma20Distance"),
            "ma60Distance": facts.get("ma60Distance"),
            "priceChangeRate": facts.get("priceChangeRate"),
            "volumeRatio": facts.get("volumeRatio"),
            "timeAdjustedVolumeRatio": facts.get("timeAdjustedVolumeRatio"),
            "tradeStrength": facts.get("tradeStrength"),
            "bidAskImbalance": facts.get("bidAskImbalance"),
            "investorFlowScore": facts.get("investorFlowScore"),
            "dataQuality": facts.get("dataQuality"),
            "dataQualityScore": facts.get("dataQualityScore"),
            "targetRole": WATCHLIST_TARGET_ROLE if facts.get("isWatchlist") else (HOLDING_TARGET_ROLE if facts.get("isHolding") else ""),
        },
        "missingData": list(facts.get("missingData") or [])[:8],
    }
