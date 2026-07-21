from typing import Dict, Iterable, List, Optional

from .investment_brain import hypothesis_set_from_relation_context
from .market_data import number
from .ontology_decision_state import (
    CHANGE_STATE_LABELS,
    CONFLICT_STATE_LABELS,
    DATA_STATE_LABELS,
    REVIEW_LEVEL_LABELS,
    change_state_from_facts,
    conflict_state_from_roles,
    data_state_from_evidence,
    evidence_role_from_relation,
    review_level_for,
    semantic_relation_sort_key,
    state_payload,
)
from .ontology_rulebox_contracts import (
    HOLDING_TARGET_ROLE,
    WATCHLIST_ACTION_POLICY,
    WATCHLIST_ALLOWED_ACTIONS,
    WATCHLIST_BLOCKED_ACTIONS,
    WATCHLIST_TARGET_ROLE,
)
from .ontology_threshold_policy import ontology_threshold_policy_from_context
from .ontology_relation_decisions import decision_stage_from_relation
from .ontology_relation_reasoning import (
    OntologyRuleMatch,
    build_ai_prompt_context,
    execution_plan_from_relation_context,
    position_signal_facts,
)
from .portfolio import AccountSnapshot, PortfolioSummary, Position


TYPEDB_RELATION_CONTEXT_VERSION = "typedb-inferencebox-relation-context-v1"
GRAPH_STORE_RELATION_CONTEXT_VERSION = "graph-store-inferencebox-relation-context-v1"

META_INFERENCE_RELATION_TYPES = {
    "EXPLAINED_BY_TRACE",
    "HAS_INFERENCE_TIMELINE",
    "HAS_INFERENCE_TRACE",
    "HAS_SIGNAL_CONFLICT",
    "HAS_WHY_NOW",
    "TRIGGERED_INFERENCE",
}

EVIDENCE_DOMAIN_TOKENS = {
    "position": {"holding", "loss", "profit", "pnl", "position", "rebalance", "concentration"},
    "trend": {"breakdown", "breakout", "ma5", "ma20", "ma60", "recovery", "support", "trend"},
    "flow": {"flow", "investor", "orderbook", "smart-money", "trade-strength", "volume"},
    "news": {"disclosure", "event", "filing", "news", "research"},
    "macro": {"crypto", "fx", "macro", "rate", "regime"},
    "valuation": {"fair-value", "margin-of-safety", "multiple", "per", "valuation"},
    "execution": {"capacity", "execution", "liquidity", "slippage"},
    "portfolio": {"allocation", "concentration", "exposure", "factor", "portfolio", "rebalance"},
    "temporal": {"episode", "timeline", "temporal", "transition"},
    "data-quality": {"conflict", "coverage", "data-quality", "freshness", "missing", "stale"},
}

FIELD_EVIDENCE_DOMAINS = {
    "profitLossRate": {"position"},
    "positionWeight": {"position", "portfolio"},
    "positionAccountWeight": {"position", "portfolio"},
    "quantity": {"position", "execution"},
    "sellableQuantity": {"position", "execution"},
    "priceChangeRate": {"trend"},
    "changeRate": {"trend"},
    "ma5Distance": {"trend"},
    "ma20Distance": {"trend"},
    "ma60Distance": {"trend"},
    "ma20Slope": {"trend"},
    "ma60Slope": {"trend"},
    "trendCurve": {"trend"},
    "volume": {"flow"},
    "volumeRatio": {"flow"},
    "tradeStrength": {"flow"},
    "bidAskImbalance": {"flow", "execution"},
    "smartMoneyNetVolume": {"flow"},
    "directNewsCount": {"news"},
    "usdKrwRate": {"macro"},
    "us10yRate": {"macro"},
    "us2yRate": {"macro"},
    "btcChange24h": {"macro"},
    "btcChange7d": {"macro"},
    "valuationFairValue": {"valuation"},
    "marginOfSafetyPct": {"valuation"},
}


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
    matches = matches_from_inference(relations, traces, facts=facts, source_name=source_name, context_version=context_version)
    if not matches:
        return {}
    decision = decision_from_inference(facts, matches, relations, traces, source_name=source_name)
    execution_plan = execution_plan_from_relation_context(facts, decision, matches)
    prompt_context = build_ai_prompt_context(prompt_id, facts, matches, settings or {}, execution_plan)
    active_matches = [item for item in matches if item.matched and not item.reference_only]
    evidence_state = aggregate_evidence_state(active_matches or matches)
    evidence_subgraph = evidence_subgraph_packet(position, facts, matches, relations, traces)
    why_now = why_now_packet(facts, active_matches, decision, relations, traces, inferencebox, threshold_policy)
    signal_conflicts = signal_conflict_packet(facts, active_matches, relations, threshold_policy)
    data_state = str(evidence_state.get("dataState") or "partial")
    review_level = (
        "blocked"
        if decision.get("judgementBlocked")
        else review_level_for(decision.get("actionLevel"), data_state)
    )
    decision_state = state_payload(
        review_level,
        data_state,
        str(why_now.get("changeState") or "unchanged"),
        str(signal_conflicts.get("conflictState") or "context-only"),
    )
    decision.update(decision_state)
    inference_timeline = inference_timeline_packet(facts, active_matches, decision, inferencebox)
    investment_brain = hypothesis_set_from_relation_context({
        "subject": {
            "symbol": facts.get("symbol"),
            "name": facts.get("name"),
            "market": facts.get("market"),
            "sector": facts.get("sector"),
        },
        "facts": facts,
        "activeRules": [item.to_dict() for item in active_matches],
        "matchedRules": [item.to_dict() for item in matches if item.matched],
        "missingData": list(facts.get("missingData") or []),
        "signalConflicts": signal_conflicts,
        "inferenceGenerationId": str(inferencebox.get("inferenceGenerationId") or ""),
        "graphStoreInference": {
            "relations": relations,
            "traces": traces,
        },
        "hypothesisPolicy": {
            "minimumComparisonCount": (settings or {}).get("investmentBrainMinimumHypothesisCount") or 3,
            "maximumComparisonCount": (settings or {}).get("investmentBrainMaximumHypothesisCount") or 8,
        },
    })
    if isinstance(prompt_context, dict):
        prompt_context["evidenceSubgraph"] = evidence_subgraph
        prompt_context["evidenceState"] = evidence_state
        prompt_context["decisionState"] = decision_state
        prompt_context["whyNow"] = why_now
        prompt_context["signalConflicts"] = signal_conflicts
        prompt_context["inferenceTimeline"] = inference_timeline
        prompt_context["investmentBrain"] = investment_brain
        prompt_context["hypothesisSet"] = investment_brain.get("hypothesisSet") or {}
        prompt_context["researchPlan"] = investment_brain.get("researchPlan") or {}
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
        "reviewLevel": decision_state["reviewLevel"],
        "reviewLevelLabel": decision_state["reviewLevelLabel"],
        "dataState": decision_state["dataState"],
        "dataStateLabel": decision_state["dataStateLabel"],
        "changeState": decision_state["changeState"],
        "changeStateLabel": decision_state["changeStateLabel"],
        "conflictState": decision_state["conflictState"],
        "conflictStateLabel": decision_state["conflictStateLabel"],
        "decisionState": decision_state,
        "evidenceState": evidence_state,
        "whyNow": why_now,
        "signalConflicts": signal_conflicts,
        "inferenceTimeline": inference_timeline,
        "investmentBrain": investment_brain,
        "hypothesisSet": investment_brain.get("hypothesisSet") or {},
        "researchPlan": investment_brain.get("researchPlan") or {},
        "hypothesisTemplates": investment_brain.get("hypothesisTemplates") or [],
        "selfQuestions": investment_brain.get("selfQuestions") or [],
        "epistemicState": investment_brain.get("epistemicState") or {},
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
) -> List[OntologyRuleMatch]:
    trace_by_rule: Dict[str, Dict[str, object]] = {}
    for item in traces or []:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("ruleId") or "").strip()
        if not rule_id:
            continue
        existing = trace_by_rule.get(rule_id)
        if existing is None or trace_grounding_key(item) > trace_grounding_key(existing):
            trace_by_rule[rule_id] = item
    matches: List[OntologyRuleMatch] = []
    seen = set()
    primary_relations = [item for item in relations or [] if is_primary_inference_relation(item)]
    for relation in sorted(primary_relations, key=semantic_relation_sort_key):
        rule_id = str(relation.get("ruleId") or "").strip()
        if not rule_id:
            continue
        key = inference_causal_path_key(relation)
        if key in seen:
            continue
        seen.add(key)
        trace = trace_by_rule.get(rule_id, {})
        evidence_state = inference_evidence_state(relation, facts or {}, trace)
        data_state = str(evidence_state.get("dataState") or "partial")
        stage = decision_stage_from_relation(relation)
        policy_missing = stage is None
        if policy_missing:
            evidence_state.update({
                "dataState": "unavailable",
                "evidenceUsableForJudgement": False,
                "judgementBlocked": True,
                "freshnessGateReason": "TypeDB 추론 관계에 판단 단계·행동 그룹·행동 수준이 완전하지 않습니다.",
                "drivers": ["TypeDB 판단 정책 누락"],
            })
            data_state = "unavailable"
        review_level = "blocked" if policy_missing else review_level_for(stage.action_level, data_state)
        role = evidence_role_from_relation(relation)
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
            review_level=review_level,
            review_label=REVIEW_LEVEL_LABELS[review_level],
            data_state=data_state,
            evidence_role=role,
            evidence=evidence,
            missing=list((facts or {}).get("missingData") or []),
            reference_only=bool(evidence_state.get("judgementBlocked")),
            prompt_hint=inference_prompt_hint(source_name, "relation"),
            evidence_state=evidence_state,
            decision_stage=stage.stage_key if stage else "",
            action_group=stage.action_group if stage else "",
            action_level=stage.action_level if stage else "",
            decision_label=stage.label if stage else "",
            decision_tone=stage.tone if stage else "",
        ))
    if matches:
        return sorted(matches, key=lambda item: semantic_relation_sort_key(relation_for_match(item, primary_relations)))
    for trace in traces or []:
        rule_id = str(trace.get("ruleId") or "").strip()
        if not rule_id:
            continue
        trace_blocked = True
        data_state = data_state_from_evidence(
            usable=not trace_blocked,
            freshness_status=trace.get("freshnessStatus"),
            missing=(facts or {}).get("missingData") or [],
            has_evidence=bool(trace.get("matchedConditions") or trace.get("evidenceRelationIds")),
        )
        review_level = "blocked"
        matches.append(OntologyRuleMatch(
            rule_id=rule_id,
            label=str(trace.get("label") or rule_id),
            version=context_version,
            relation_type="HAS_INFERENCE_TRACE",
            signal_type=inference_signal_type(source_name),
            matched=True,
            review_level=review_level,
            review_label=REVIEW_LEVEL_LABELS[review_level],
            data_state=data_state,
            evidence_role="blocking",
            evidence=[str(trace.get("label") or rule_id)],
            missing=list((facts or {}).get("missingData") or []),
            reference_only=True,
            prompt_hint=inference_prompt_hint(source_name, "trace"),
            evidence_state={
                "dataState": data_state,
                "evidenceRole": "blocking",
                "evidenceUsableForJudgement": False,
                "judgementBlocked": True,
                "freshnessStatus": str(trace.get("freshnessStatus") or "unknown"),
                "freshnessGateReason": str(trace.get("freshnessGateReason") or ""),
                "drivers": ["TypeDB 조건 추적은 있으나 판단 관계가 없습니다."],
            },
        ))
    return matches


def is_primary_inference_relation(relation: Dict[str, object]) -> bool:
    if not isinstance(relation, dict):
        return False
    relation_type = str(relation.get("type") or relation.get("relationType") or "").strip().upper()
    if not relation_type or relation_type in META_INFERENCE_RELATION_TYPES:
        return False
    if relation.get("derivationIndex") not in (None, ""):
        return True
    if bool(str(relation.get("decisionStage") or relation.get("actionGroup") or relation.get("polarity") or "").strip()):
        return True
    return False


def inference_causal_path_key(relation: Dict[str, object]) -> str:
    return "|".join([
        str(relation.get("ruleId") or "").strip(),
        str(relation.get("decisionStage") or "").strip(),
        str(relation.get("actionGroup") or "").strip(),
        str(relation.get("polarity") or "context").strip(),
    ])


def trace_grounding_key(trace: Dict[str, object]):
    conditions = [item for item in (trace or {}).get("matchedConditions") or [] if isinstance(item, dict)]
    grounded = sum(
        1
        for item in conditions
        if item.get("observedValue") not in (None, "")
        or str(item.get("relationId") or "").strip()
        or bool(item.get("absenceSatisfied"))
    )
    return grounded, len((trace or {}).get("evidenceRelationIds") or [])


def inference_evidence_domains(
    relation: Dict[str, object],
    trace: Optional[Dict[str, object]] = None,
) -> List[str]:
    relation = relation or {}
    trace = trace or {}
    domains = set()
    for condition in trace.get("matchedConditions") or []:
        if not isinstance(condition, dict):
            continue
        domains.update(FIELD_EVIDENCE_DOMAINS.get(str(condition.get("field") or ""), set()))
        condition_text = " ".join([
            str(condition.get("relationType") or ""),
            str(condition.get("targetKind") or ""),
            str(condition.get("dataScope") or ""),
            str(condition.get("domainScope") or ""),
        ]).lower()
        for domain, tokens in EVIDENCE_DOMAIN_TOKENS.items():
            if any(token in condition_text for token in tokens):
                domains.add(domain)
    semantic_text = " ".join([
        str(relation.get("ruleId") or ""),
        str(relation.get("type") or ""),
        str(relation.get("decisionStage") or ""),
        str(relation.get("actionGroup") or ""),
        str(relation.get("target") or ""),
        str(trace.get("ruleLabel") or trace.get("label") or ""),
    ]).lower().replace("_", "-").replace(".", "-")
    for domain, tokens in EVIDENCE_DOMAIN_TOKENS.items():
        if any(token in semantic_text for token in tokens):
            domains.add(domain)
    return sorted(domains or {"semantic"})


def inference_signal_type(source_name: str) -> str:
    if source_name == "typedbInferenceBox":
        return "typedb_inference"
    return "graph_store_inference"


def inference_prompt_hint(source_name: str, unit: str) -> str:
    store_label = "TypeDB" if source_name == "typedbInferenceBox" else "그래프 저장소"
    suffix = "trace" if unit == "trace" else "관계"
    return f"{store_label}의 추론 결과에서 생성된 {suffix}를 우선 근거로 사용합니다."


def inference_evidence_state(
    relation: Dict[str, object],
    facts: Dict[str, object],
    trace: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    relation = relation or {}
    facts = facts or {}
    trace = trace or {}
    matched_conditions = [item for item in trace.get("matchedConditions") or [] if isinstance(item, dict)]
    applied_fields = unique_texts([item.get("field") for item in matched_conditions])
    domains = inference_evidence_domains(relation, trace)
    freshness = str(trace.get("freshnessStatus") or relation.get("freshnessStatus") or "unknown")
    usable = trace.get("evidenceUsableForJudgement")
    if usable is None:
        usable = relation.get("evidenceUsableForJudgement")
    if usable is None:
        usable = True
    missing = list(facts.get("missingData") or [])
    data_state = data_state_from_evidence(
        usable=usable,
        freshness_status=freshness,
        missing=missing,
        has_evidence=bool(matched_conditions or trace.get("evidenceRelationIds") or relation.get("ruleId")),
    )
    role = evidence_role_from_relation(relation)
    drivers = unique_texts([
        relation.get("aiInfluenceLabel"),
        relation.get("targetLabel"),
        trace.get("label"),
        trace.get("freshnessGateReason") if usable is False else "",
    ])
    return {
        "dataState": data_state,
        "dataStateLabel": DATA_STATE_LABELS[data_state],
        "evidenceRole": role,
        "evidenceDomains": domains,
        "appliedFactFields": applied_fields,
        "evidenceCount": len(matched_conditions) or len(trace.get("evidenceRelationIds") or []) or 1,
        "evidenceUsableForJudgement": usable is not False,
        "judgementBlocked": data_state in {"unavailable", "insufficient"},
        "freshnessStatus": freshness,
        "freshnessGateReason": str(trace.get("freshnessGateReason") or relation.get("freshnessGateReason") or ""),
        "drivers": drivers[:6],
    }


def aggregate_evidence_state(matches: List[OntologyRuleMatch]) -> Dict[str, object]:
    rows = [item.evidence_state for item in matches or [] if isinstance(item.evidence_state, dict)]
    if not rows:
        return {
            "dataState": "insufficient",
            "dataStateLabel": DATA_STATE_LABELS["insufficient"],
            "conflictState": "context-only",
            "conflictStateLabel": CONFLICT_STATE_LABELS["context-only"],
            "evidenceRoles": [],
            "drivers": [],
        }
    states = {str(item.get("dataState") or "partial") for item in rows}
    if states == {"unavailable"}:
        data_state = "unavailable"
    elif not (states & {"sufficient", "partial"}):
        data_state = "insufficient"
    elif "partial" in states or "unavailable" in states or "insufficient" in states:
        data_state = "partial"
    else:
        data_state = "sufficient"
    roles = unique_texts([item.get("evidenceRole") for item in rows])
    conflict_state = conflict_state_from_roles(roles)
    drivers = unique_texts([
        driver
        for item in rows
        for driver in item.get("drivers") or []
    ])
    return {
        "dataState": data_state,
        "dataStateLabel": DATA_STATE_LABELS[data_state],
        "conflictState": conflict_state,
        "conflictStateLabel": CONFLICT_STATE_LABELS[conflict_state],
        "evidenceRoles": roles,
        "evidenceDomains": unique_texts([
            domain
            for item in rows
            for domain in item.get("evidenceDomains") or []
        ]),
        "appliedFactFields": unique_texts([
            field
            for item in rows
            for field in item.get("appliedFactFields") or []
        ]),
        "usableEvidenceCount": sum(1 for item in rows if item.get("evidenceUsableForJudgement") is not False),
        "blockedEvidenceCount": sum(1 for item in rows if item.get("judgementBlocked")),
        "drivers": drivers[:8],
    }


def why_now_packet(
    facts: Dict[str, object],
    active_matches: List[OntologyRuleMatch],
    decision: Dict[str, object],
    relations: List[Dict[str, object]],
    traces: List[Dict[str, object]],
    inferencebox: Dict[str, object],
    threshold_policy=None,
) -> Dict[str, object]:
    policy = (threshold_policy or ontology_threshold_policy_from_context({})).why_now
    facts = facts or {}
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
    has_new_evidence = bool(direct_news_count or facts.get("disclosure") or facts.get("sourceAlertEvents"))
    change_state = change_state_from_facts(facts, has_new_evidence=has_new_evidence)
    review_level = review_level_for(decision.get("actionLevel"))
    should_escalate = bool(
        change_state != "unchanged"
        or abs(pnl_delta) >= policy.escalate_profit_loss_delta_pct
        or abs(price_change) >= policy.escalate_price_change_pct
        or review_level in {"act", "immediate", "blocked"}
    )
    return {
        "tboxClass": "WhyNow",
        "reasoningQuestion": "왜 지금 다시 봐야 하는가",
        "changeState": change_state,
        "changeStateLabel": CHANGE_STATE_LABELS[change_state],
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
    relations: List[Dict[str, object]],
    threshold_policy=None,
) -> Dict[str, object]:
    policy = (threshold_policy or ontology_threshold_policy_from_context({})).signal_conflict
    facts = facts or {}
    applied_fields = {
        str(field)
        for match in active_matches or []
        for field in (match.evidence_state or {}).get("appliedFactFields") or []
        if str(field or "").strip()
    }
    risk_drivers: List[str] = []
    support_drivers: List[str] = []

    def add(target: List[str], label: str) -> None:
        text = str(label or "").strip()
        if text and text not in target:
            target.append(text)

    if "profitLossRate" in applied_fields and number(facts.get("profitLossRate")) <= policy.loss_risk_profit_loss_rate:
        add(risk_drivers, "손실 구간")
    if "ma20Distance" in applied_fields and number(facts.get("ma20Distance")) < 0:
        add(risk_drivers, "20일 평균 아래")
    if "ma60Distance" in applied_fields and number(facts.get("ma60Distance")) < 0:
        add(risk_drivers, "60일 평균 아래")
    if "tradeStrength" in applied_fields and number(facts.get("tradeStrength")) and number(facts.get("tradeStrength")) < policy.weak_trade_strength:
        add(risk_drivers, "체결강도 약화")
    smart_money = number(facts.get("foreignNetVolume")) + number(facts.get("institutionNetVolume"))
    if smart_money < 0:
        add(risk_drivers, "외국인·기관 수급 부담")
    if "tradeStrength" in applied_fields and number(facts.get("tradeStrength")) >= policy.support_trade_strength:
        add(support_drivers, "체결강도 우위")
    if "bidAskImbalance" in applied_fields and number(facts.get("bidAskImbalance")) >= policy.support_bid_ask_imbalance:
        add(support_drivers, "매수 호가 우위")
    if smart_money > 0:
        add(support_drivers, "외국인·기관 수급 지지")
    if "ma5Distance" in applied_fields and number(facts.get("ma5Distance")) > 0:
        add(support_drivers, "5일 평균 위")
    if "priceChangeRate" in applied_fields and number(facts.get("priceChangeRate")) > 0:
        add(support_drivers, "현재 가격 반등")
    for relation in relations or []:
        if not is_primary_inference_relation(relation):
            continue
        polarity = evidence_role_from_relation(relation)
        label = str((relation or {}).get("aiInfluenceLabel") or (relation or {}).get("targetLabel") or "").strip()
        if polarity == "risk":
            add(risk_drivers, label)
        elif polarity in {"support", "counter"}:
            add(support_drivers, label)

    roles = [item.evidence_role for item in active_matches or []]
    if risk_drivers:
        roles.append("risk")
    if support_drivers:
        roles.append("support")
    conflict_state = conflict_state_from_roles(roles)
    has_conflict = conflict_state == "mixed"
    if has_conflict:
        effect = "위험과 지지 근거가 동시에 강해 단정적 판단을 낮춰야 합니다."
    elif conflict_state == "risk-only":
        effect = "현재 확인된 근거는 위험 관리 쪽입니다."
    elif conflict_state == "support-only":
        effect = "현재 확인된 근거는 버티거나 좋아질 가능성을 확인하는 쪽입니다."
    else:
        effect = "방향을 정하기보다 참고 자료로 확인할 근거입니다."
    return {
        "tboxClass": "SignalConflict",
        "hasConflict": has_conflict,
        "conflictState": conflict_state,
        "conflictStateLabel": CONFLICT_STATE_LABELS[conflict_state],
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
        "reviewLevel": decision.get("reviewLevel"),
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


def decision_from_inference(
    facts: Dict[str, object],
    matches: List[OntologyRuleMatch],
    relations: List[Dict[str, object]],
    traces: List[Dict[str, object]],
    source_name: str = "typedbInferenceBox",
) -> Dict[str, object]:
    active = [
        item
        for item in matches
        if item.matched
        and not item.reference_only
        and decision_stage_from_relation(relation_for_match(item, relations)) is not None
    ]
    candidates = active
    if not candidates:
        return {
            "label": "TypeDB 판단 정책 누락",
            "tone": "caution",
            "basis": source_name,
            "selectedRuleId": "",
            "selectionRole": "blocked-missing-typedb-decision-policy",
            "finalDecisionOwner": "typedb-schema-function-rules",
            "candidateRuleIds": unique_texts([item.rule_id for item in matches if item.matched])[:12],
            "candidateDecisionStages": [],
            "selectedInferenceTraceId": "",
            "decisionStage": "",
            "actionGroup": "dataQuality",
            "actionLevel": "reference",
            "reviewLevel": "blocked",
            "reviewLevelLabel": REVIEW_LEVEL_LABELS["blocked"],
            "dataState": "unavailable",
            "dataStateLabel": DATA_STATE_LABELS["unavailable"],
            "evidenceRole": "blocking",
            "sourceRelationType": "",
            "stagePolicySource": "missingTypeDbDecisionStage",
            "judgementBlocked": True,
            "actionPolicyApplied": False,
            "nativeTypeDbReasoned": False,
        }
    selected = min(candidates, key=lambda item: semantic_relation_sort_key(relation_for_match(item, relations)))
    relation = relation_for_match(selected, relations)
    action_policy = action_policy_from_relation_or_facts(facts, relation)
    stage = decision_stage_from_relation(relation)
    if stage is None:
        # ``active`` above already excludes this state.  Keep the guard here so
        # a malformed relation can never be converted into a default hold.
        return {
            "label": "TypeDB 판단 정책 누락",
            "tone": "caution",
            "basis": source_name,
            "selectedRuleId": selected.rule_id,
            "selectionRole": "blocked-missing-typedb-decision-policy",
            "finalDecisionOwner": "typedb-schema-function-rules",
            "candidateRuleIds": unique_texts([item.rule_id for item in matches if item.matched])[:12],
            "candidateDecisionStages": [],
            "selectedInferenceTraceId": "",
            "decisionStage": "",
            "actionGroup": "dataQuality",
            "actionLevel": "reference",
            "reviewLevel": "blocked",
            "reviewLevelLabel": REVIEW_LEVEL_LABELS["blocked"],
            "dataState": "unavailable",
            "dataStateLabel": DATA_STATE_LABELS["unavailable"],
            "evidenceRole": "blocking",
            "sourceRelationType": str(relation.get("type") or ""),
            "stagePolicySource": "missingTypeDbDecisionMetadata",
            "judgementBlocked": True,
            "actionPolicyApplied": False,
            "nativeTypeDbReasoned": False,
        }
    action_policy_applied = False
    if action_policy.get("targetRole") == WATCHLIST_TARGET_ROLE:
        if stage.action_group in {"lossControl", "profitTake", "rebalance", "distributionRisk"}:
            action_policy_applied = True
    trace = next((item for item in traces if str(item.get("ruleId") or "") == selected.rule_id), {})
    label = "신규 진입 보류" if action_policy_applied and action_policy.get("targetRole") == WATCHLIST_TARGET_ROLE else stage.label
    review_level = review_level_for(stage.action_level, selected.data_state)
    candidate_stages = []
    for item in matches:
        if not item.matched:
            continue
        candidate_stage = decision_stage_from_relation(relation_for_match(item, relations))
        if candidate_stage is not None:
            candidate_stages.append(candidate_stage.stage_key)
    return {
        "label": label,
        "tone": stage.tone,
        "basis": source_name,
        "selectedRuleId": selected.rule_id,
        "selectionRole": "rule-derived-baseline-not-final-opinion",
        "finalDecisionOwner": "ai-hypothesis-competition",
        "candidateRuleIds": unique_texts([item.rule_id for item in matches if item.matched])[:12],
        "candidateDecisionStages": unique_texts(candidate_stages)[:8],
        "selectedInferenceTraceId": str(trace.get("id") or ""),
        "decisionStage": stage.stage_key,
        "actionGroup": stage.action_group,
        "actionLevel": stage.action_level,
        "reviewLevel": review_level,
        "reviewLevelLabel": REVIEW_LEVEL_LABELS[review_level],
        "dataState": selected.data_state,
        "dataStateLabel": DATA_STATE_LABELS.get(selected.data_state, DATA_STATE_LABELS["partial"]),
        "evidenceRole": selected.evidence_role,
        "sourceRelationType": str(relation.get("type") or ""),
        "stagePolicySource": inference_relation_policy_source(source_name),
        "judgementBlocked": False,
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
    return str((relation or {}).get("decisionStage") or "").strip()


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
            "evidenceRole": evidence_role_from_relation(relation),
            "decisionStage": str(relation.get("decisionStage") or ""),
            "actionLevel": str(relation.get("actionLevel") or ""),
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
                "dataState": data_state_from_evidence(
                    usable=item.get("evidenceUsableForJudgement") is not False,
                    freshness_status=item.get("freshnessStatus"),
                    has_evidence=bool(item.get("matchedConditions") or item.get("evidenceRelationIds")),
                ),
                "matchedConditionIds": list(item.get("matchedConditionIds") or [])[:12],
                "evidenceRelationIds": list(item.get("evidenceRelationIds") or [])[:12],
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
            "foreignNetVolume": facts.get("foreignNetVolume"),
            "institutionNetVolume": facts.get("institutionNetVolume"),
            "individualNetVolume": facts.get("individualNetVolume"),
            "dataQuality": facts.get("dataQuality"),
            "targetRole": WATCHLIST_TARGET_ROLE if facts.get("isWatchlist") else (HOLDING_TARGET_ROLE if facts.get("isHolding") else ""),
        },
        "missingData": list(facts.get("missingData") or [])[:8],
    }
