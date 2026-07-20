import json
from typing import Dict, List

from .market_data import number
from .ontology_contracts import OntologyBelief, OntologyEvidence, OntologyRelation, PortfolioOntology
from .ontology_schema import ontology_abox, ontology_tbox
from .ontology_decision_state import evidence_role_from_relation, without_aggregate_decision_fields
from .ontology_tbox import BOUNDED_CONTEXTS, bounded_contexts_payload
from .portfolio import PortfolioSummary


ONTOLOGY_PROMPT_VERSION = "ontology-investment-v3-hypothesis-learning"


def relation_key(item: OntologyRelation) -> str:
    return "|".join([item.source, item.relation_type, item.target])


def entity_label_map(graph: PortfolioOntology) -> Dict[str, str]:
    return {item.entity_id: item.label for item in graph.entities}


def reasoning_card_data_gaps(evidence_rows: List[OntologyEvidence]) -> List[str]:
    gaps: List[str] = []
    for item in evidence_rows:
        value = item.value or {}
        if item.kind == "data-quality" and item.data_state != "sufficient":
            gaps.append("가격·이동평균·수급 데이터 일부 부족")
        if item.kind == "market-observation" and not number(value.get("currentPrice")):
            gaps.append("현재가 미확인")
    return sorted(set(gaps))


def reasoning_card_coverage_gaps(relations: List[OntologyRelation], entities: Dict[str, object]) -> List[str]:
    gaps: List[str] = []
    for relation in relations:
        if relation.relation_type != "HAS_COVERAGE_GAP":
            continue
        target = entities.get(relation.target)
        labels = (target.properties or {}).get("missingLabels") if target else []
        if labels:
            gaps.append("온톨로지 커버리지 부족: " + ", ".join(str(item) for item in labels[:5] if str(item or "")))
        else:
            gaps.append("온톨로지 커버리지 부족")
    return sorted(set(gaps))


def compact_evidence_row(item: OntologyEvidence) -> Dict[str, object]:
    return without_aggregate_decision_fields(item.to_dict())


def compact_relation_row(item: OntologyRelation, labels: Dict[str, str]) -> Dict[str, object]:
    return {
        "id": relation_key(item),
        "source": item.source,
        "sourceLabel": labels.get(item.source, item.source),
        "target": item.target,
        "targetLabel": labels.get(item.target, item.target),
        "type": item.relation_type,
        "evidenceIds": list(item.evidence_ids or []),
        "properties": without_aggregate_decision_fields(dict(item.properties or {})),
    }


def compact_entity_row(item) -> Dict[str, object]:
    return {
        "id": item.entity_id,
        "label": item.label,
        "kind": item.kind,
        "properties": without_aggregate_decision_fields(dict(item.properties or {})),
    }


def compact_entities_by_kind(graph: PortfolioOntology, kinds: List[str], limit: int = 80) -> List[Dict[str, object]]:
    kind_set = set(kinds or [])
    return [
        compact_entity_row(item)
        for item in graph.entities
        if item.kind in kind_set and ontology_box(item.properties) != "TBox"
    ][:limit]


def ontology_box(properties: Dict[str, object], default: str = "ABox") -> str:
    return str((properties or {}).get("ontologyBox") or default)


def rulebox_payload(graph: PortfolioOntology) -> Dict[str, object]:
    labels = entity_label_map(graph)
    entities = [item for item in graph.entities if ontology_box(item.properties) == "RuleBox"]
    relations = [item for item in graph.relations if ontology_box(item.properties) == "RuleBox"]
    rules = [item for item in entities if item.kind == "rule"]
    conditions = [item for item in entities if item.kind == "rule-condition"]
    templates = [item for item in entities if item.kind == "relation-template"]
    relation_rules = [item for item in entities if item.kind == "relation-rule"]
    relation_rule_conditions = [item for item in entities if item.kind == "relation-rule-condition"]
    relation_rule_templates = [item for item in entities if item.kind == "relation-rule-template"]
    return {
        "box": "RuleBox",
        "description": "Executable graph rules represented as ontology nodes.",
        "entityCount": len(entities),
        "relationCount": len(relations),
        "ruleCount": len(rules),
        "conditionCount": len(conditions),
        "relationTemplateCount": len(templates),
        "relationRuleCount": len(relation_rules),
        "relationRuleConditionCount": len(relation_rule_conditions),
        "relationRuleTemplateCount": len(relation_rule_templates),
        "rules": [item.to_dict() for item in rules[:24]],
        "conditions": [item.to_dict() for item in conditions[:40]],
        "relationTemplates": [item.to_dict() for item in templates[:40]],
        "relationRules": [item.to_dict() for item in relation_rules[:40]],
        "relationRuleConditions": [item.to_dict() for item in relation_rule_conditions[:40]],
        "relationRuleTemplates": [item.to_dict() for item in relation_rule_templates[:40]],
        "relations": [compact_relation_row(item, labels) for item in relations[:80]],
    }


def inferencebox_payload(graph: PortfolioOntology) -> Dict[str, object]:
    labels = entity_label_map(graph)
    entities = [item for item in graph.entities if ontology_box(item.properties) == "InferenceBox"]
    relations = [item for item in graph.relations if ontology_box(item.properties) == "InferenceBox"]
    traces = [item for item in entities if item.kind == "inference-trace"]
    evidence = [
        item
        for item in graph.evidence
        if item.kind == "inference-trace" or ontology_box(item.value) == "InferenceBox"
    ]
    return {
        "box": "InferenceBox",
        "description": "Derived assertions and inference traces produced from RuleBox rules.",
        "entityCount": len(entities),
        "relationCount": len(relations),
        "traceCount": len(traces),
        "evidenceCount": len(evidence),
        "traces": [item.to_dict() for item in traces[:40]],
        "derivedRelations": [compact_relation_row(item, labels) for item in relations[:100]],
        "evidence": [compact_evidence_row(item) for item in evidence[:60]],
    }


def build_reasoning_cards(graph: PortfolioOntology) -> List[Dict[str, object]]:
    labels = entity_label_map(graph)
    entities = {item.entity_id: item for item in graph.entities}
    stocks = [
        item
        for item in graph.entities
        if item.kind == "stock" and (item.properties or {}).get("ontologyBox") != "TBox"
    ]
    cards: List[Dict[str, object]] = []
    for stock in sorted(stocks, key=lambda item: item.label):
        properties = dict(stock.properties or {})
        symbol = str(properties.get("symbol") or stock.label or "").upper()
        if not symbol:
            continue
        relations = [
            item
            for item in graph.relations
            if item.source == stock.entity_id or item.target == stock.entity_id
        ]
        evidence_rows = [item for item in graph.evidence if item.subject == stock.entity_id]
        belief_rows = [item for item in graph.beliefs if item.subject == stock.entity_id]
        opinion = graph.opinion_for_symbol(symbol)
        opinion_payload = opinion.to_dict() if opinion else {}
        neighbor_ids = sorted(set(
            [stock.entity_id]
            + [item.source for item in relations]
            + [item.target for item in relations]
        ))
        tbox_classes = sorted(set(
            [
                str(value)
                for entity_id_value in neighbor_ids
                for value in (
                    (entities.get(entity_id_value).properties or {}).get("tboxClasses")
                    or [(entities.get(entity_id_value).properties or {}).get("tboxClass")]
                    if entities.get(entity_id_value)
                    else []
                )
                if value
            ]
            + ["Evidence", "Belief", "Opinion", "AIReview"]
        ))
        bounded_contexts = sorted(set(
            [
                str((entities.get(entity_id_value).properties or {}).get("boundedContext") or "")
                for entity_id_value in neighbor_ids
                if entities.get(entity_id_value) and (entities.get(entity_id_value).properties or {}).get("boundedContext")
            ]
            + [
                str((relation.properties or {}).get("boundedContext") or "")
                for relation in relations
                if (relation.properties or {}).get("boundedContext")
            ]
        ))
        gaps = sorted(set(reasoning_card_data_gaps(evidence_rows) + reasoning_card_coverage_gaps(relations, entities)))
        source = str(properties.get("source") or "holding")
        portfolio_relation = next((
            item.relation_type
            for item in relations
            if item.target == stock.entity_id and item.relation_type in {"HOLDS", "WATCHES"}
        ), "HOLDS" if source != "watchlist" else "WATCHES")
        execution_plans = [
            dict((entities.get(item.target).properties or {}).get("executionPlan") or {})
            for item in relations
            if item.relation_type == "HAS_EXECUTION_PLAN"
            and entities.get(item.target)
            and entities.get(item.target).kind == "execution-plan"
        ]
        cards.append({
            "id": "reasoning-card:" + symbol,
            "symbol": symbol,
            "companyName": stock.label,
            "displayName": stock.label,
            "source": source,
            "portfolioRelation": portfolio_relation,
            "status": "needsData" if gaps else "readyForAiReview",
            "finalOpinion": {
                "action": opinion_payload.get("action") or "",
                "tone": opinion_payload.get("tone") or "",
                "reviewLevel": opinion_payload.get("review_level") or opinion_payload.get("reviewLevel") or "check",
                "dataState": opinion_payload.get("data_state") or opinion_payload.get("dataState") or "partial",
                "validationState": opinion_payload.get("validation_state") or opinion_payload.get("validationState") or "conditional",
                "thesis": opinion_payload.get("thesis") or "",
            },
            "relationInfluences": list(opinion_payload.get("relation_influences") or opinion_payload.get("relationInfluences") or []),
            "executionPlans": execution_plans,
            "strategyEvidence": [compact_evidence_row(item) for item in evidence_rows],
            "relationEvidence": [compact_relation_row(item, labels) for item in relations],
            "beliefs": [item.to_dict() for item in belief_rows],
            "dataGaps": gaps,
            "graphContext": {
                "stockEntityId": stock.entity_id,
                "boundedContexts": bounded_contexts,
                "tboxClasses": tbox_classes,
                "aboxEntityIds": neighbor_ids,
                "relationIds": [relation_key(item) for item in relations],
                "evidenceIds": [item.evidence_id for item in evidence_rows],
                "beliefIds": [item.belief_id for item in belief_rows],
                "opinionId": "opinion:" + symbol,
            },
            "aiInference": {
                "role": "ontology-first-investment-opinion",
                "promptVersion": ONTOLOGY_PROMPT_VERSION,
                "stateContract": "review-data-change-evidence-validation",
                "question": "전략 근거와 관계 근거를 함께 읽고 보유/관심 상태에 맞는 투자 의견, 반대 신호, 다음 검증 순서를 설명합니다.",
            },
        })
    return cards


def build_ai_inference_packet(graph: PortfolioOntology) -> Dict[str, object]:
    pipeline_count = len([item for item in graph.entities if item.kind == "data-pipeline"])
    insight_count = len([item for item in graph.entities if item.kind == "insight"])
    active_opinion_count = len([item for item in graph.entities if item.kind == "active-opinion"])
    execution_plan_count = len([item for item in graph.entities if item.kind == "execution-plan"])
    coverage_gap_count = len([item for item in graph.entities if item.kind in {"coverage-gap", "temporal-coverage-gap"}])
    macro_regime_count = len([item for item in graph.entities if item.kind == "macro-regime"])
    crypto_exposure_count = len([item for item in graph.entities if item.kind == "crypto-exposure"])
    article_quality_risk_count = len([item for item in graph.entities if item.kind == "article-quality-risk"])
    valuation_context_count = len([item for item in graph.entities if item.kind in {"valuation-assumption", "revenue-exposure", "analyst-revision"}])
    temporal_window_count = len([item for item in graph.entities if item.kind == "temporal-window"])
    temporal_episode_count = len([item for item in graph.entities if item.kind == "trend-episode"])
    market_proxy_count = len([item for item in graph.entities if item.kind in {"market-proxy-instrument", "market-proxy-observation"}])
    investment_question_count = len([item for item in graph.entities if item.kind in {"investment-question", "self-question"}])
    competing_hypothesis_count = len([item for item in graph.entities if item.kind == "competing-hypothesis"])
    decision_episode_count = len([item for item in graph.entities if item.kind == "decision-episode"])
    observed_outcome_count = len([item for item in graph.entities if item.kind == "observed-outcome"])
    hypothesis_calibration_count = len([item for item in graph.entities if item.kind == "hypothesis-calibration"])
    rulebox_entity_count = len([item for item in graph.entities if ontology_box(item.properties) == "RuleBox"])
    inferencebox_entity_count = len([item for item in graph.entities if ontology_box(item.properties) == "InferenceBox"])
    inferencebox_relation_count = len([item for item in graph.relations if ontology_box(item.properties) == "InferenceBox"])
    return {
        "contract": "investment-ontology-ai-inference-v1",
        "promptVersion": ONTOLOGY_PROMPT_VERSION,
        "role": "ontology-first-investment-opinion",
        "stateContract": "review-data-change-evidence-validation",
        "notificationRole": "insight-driven-dispatch",
        "inputOrder": ["tbox", "boundedContexts", "ruleBox", "abox", "inferenceBox", "derivedRelations", "inferenceTraces", "investmentQuestions", "hypothesisSets", "decisionEpisodes", "decisionPerformance", "observedOutcomes", "operationalOntology", "temporalWindows", "coverageGaps", "macroRegimes", "marketProxyContext", "cryptoExposures", "valuationContext", "newsQuality", "reasoningCards", "relationInfluences", "researchEvidence", "signalTransitions", "factorExposure", "liquidityConstraints", "insights", "activeInvestmentOpinions", "executionPlans", "relations", "evidence", "beliefs", "opinions"],
        "reasoningCardCount": len(graph.reasoning_cards),
        "reasoningCardIds": [item.get("id") for item in graph.reasoning_cards],
        "graphInputs": {
            "boundedContextCount": len(BOUNDED_CONTEXTS),
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "evidenceCount": len(graph.evidence),
            "beliefCount": len(graph.beliefs),
            "opinionCount": len(graph.opinions),
            "ruleBoxEntityCount": rulebox_entity_count,
            "inferenceBoxEntityCount": inferencebox_entity_count,
            "inferenceBoxRelationCount": inferencebox_relation_count,
            "pipelineCount": pipeline_count,
            "insightCount": insight_count,
            "activeOpinionCount": active_opinion_count,
            "executionPlanCount": execution_plan_count,
            "coverageGapCount": coverage_gap_count,
            "macroRegimeCount": macro_regime_count,
            "cryptoExposureCount": crypto_exposure_count,
            "articleQualityRiskCount": article_quality_risk_count,
            "valuationContextCount": valuation_context_count,
            "temporalWindowCount": temporal_window_count,
            "temporalEpisodeCount": temporal_episode_count,
            "marketProxyCount": market_proxy_count,
            "investmentQuestionCount": investment_question_count,
            "competingHypothesisCount": competing_hypothesis_count,
            "decisionEpisodeCount": decision_episode_count,
            "observedOutcomeCount": observed_outcome_count,
            "hypothesisCalibrationCount": hypothesis_calibration_count,
        },
        "outputSchema": {
            "portfolioView": "string",
            "relationThesis": "string",
            "companyOpinions": ["symbol", "action", "thesis", "relationInfluences", "executionPlan", "contradictions", "nextChecks"],
            "activeInvestmentOpinions": ["symbol", "action", "reviewLevel", "dataState", "validationState", "evidence", "counterEvidence", "executionPlan", "invalidationCondition"],
            "executionPlans": ["symbol", "primaryAction", "decisionDrivers", "blockedActions", "riskSignals", "supportSignals", "counterSignals", "strengthenConditions", "weakenConditions", "nextChecks"],
            "insightDispatch": ["subject", "insightType", "changeState", "deliveryState", "dispatchDecision"],
            "missingDataImpact": ["string"],
            "hypothesisComparison": ["hypothesisId", "claim", "supportingEvidenceIds", "counterEvidenceIds", "evidenceState", "verdict"],
            "selectedHypothesisId": "string",
            "unresolvedQuestions": ["string"],
        },
        "guardrails": [
            "제공된 TBox, ABox, reasoning card, 관계 행만 사용합니다.",
            "RuleBox의 조건과 InferenceBox의 파생 관계를 우선 읽고, 어떤 규칙이 결론을 만들었는지 설명합니다.",
            "보유 종목 HOLDS와 관심 종목 WATCHES를 다른 판단 단계로 설명합니다.",
            "합산 점수나 확률을 만들지 말고 확인 단계, 자료 상태, 변화, 근거 역할로 판단합니다.",
            "알림 타입 이름보다 온톨로지 인사이트, 신규성, 쿨다운, 억제 정책을 우선합니다.",
            "BUY, ADD, HOLD, TRIM, SELL, AVOID 중 하나의 투자 의견을 고르되 자동 주문 지시로 표현하지 않습니다.",
            "뉴스·공시·SEC/OpenDART 출처와 반대 근거, 무효화 조건을 함께 제시합니다.",
            "이전 상태와 현재 상태의 SignalTransition을 읽고 새 변화인지 반복 상태인지 구분합니다.",
            "temporalWindows를 읽고 단일 현재값보다 기간 경로, 수급 패턴, 이벤트 군집, 히스토리 부족 여부를 우선 구분합니다.",
            "팩터/상관/유동성/슬리피지 제약이 있으면 투자 의견과 실행 계획을 분리해 설명합니다.",
            "coverageGaps, newsQuality, source freshness가 있으면 결론 강도를 낮추고 필요한 수집 과제를 먼저 제시합니다.",
            "macroRegimes와 cryptoExposures는 종목 가격 신호의 상위 환경으로만 사용하고 단독 매수·매도 결론으로 쓰지 않습니다.",
            "marketProxyContext는 위험선호, 금리, 크레딧, IPO, 변동성, 달러, 원자재, 섹터 사이클의 배경 맥락이며 단독 매수·매도 결론으로 쓰지 않습니다.",
            "최소 세 개의 경쟁 가설을 지지·반대 근거로 비교하고, 과거 DecisionEpisode와 ObservedOutcome에서 반복 반증된 가설을 그대로 재사용하지 않습니다.",
            "hypothesis-calibration은 서로 다른 판단 에피소드의 사후 결과 표본입니다. 표본이 3개 미만이면 상태 계약을 바꾸지 않고, 그 이상이어도 제안된 보정은 설명에만 반영하며 규칙을 자동 변경하지 않습니다.",
        ],
    }


def portfolio_worldview(
    graph: PortfolioOntology,
    portfolio: PortfolioSummary,
    external_signals: Dict[str, object],
) -> Dict[str, object]:
    risk_count = len([item for item in graph.beliefs if item.polarity == "risk"])
    support_count = len([item for item in graph.beliefs if item.polarity == "support"])
    contradictions = sum(len(item.contradictions) for item in graph.opinions)
    action_required = [item.symbol for item in graph.opinions if item.review_level in {"act", "immediate", "blocked"}]
    relation_influence_count = sum(len(item.relation_influences or []) for item in graph.opinions)
    pipeline_nodes = [item for item in graph.entities if item.kind == "data-pipeline"]
    insight_nodes = [item for item in graph.entities if item.kind == "insight"]
    dispatch_nodes = [item for item in graph.entities if item.kind == "notification-dispatch"]
    bounded_context_counts: Dict[str, int] = {}
    ontology_box_counts: Dict[str, int] = {}
    ontology_relation_box_counts: Dict[str, int] = {}
    for item in graph.entities:
        box = ontology_box(item.properties)
        ontology_box_counts[box] = ontology_box_counts.get(box, 0) + 1
        context = str((item.properties or {}).get("boundedContext") or "")
        if context and (item.properties or {}).get("ontologyBox") != "TBox":
            bounded_context_counts[context] = bounded_context_counts.get(context, 0) + 1
    for item in graph.relations:
        box = ontology_box(item.properties)
        ontology_relation_box_counts[box] = ontology_relation_box_counts.get(box, 0) + 1
    top_sector = portfolio.sectors[0] if portfolio.sectors else {}
    return {
        "model": "ontology-first",
        "ontologyBoxes": {
            "tbox": ontology_tbox(),
            "abox": ontology_abox(graph),
            "rulebox": {
                "box": "RuleBox",
                "entityCount": ontology_box_counts.get("RuleBox", 0),
                "relationCount": ontology_relation_box_counts.get("RuleBox", 0),
            },
            "inferencebox": {
                "box": "InferenceBox",
                "entityCount": ontology_box_counts.get("InferenceBox", 0),
                "relationCount": ontology_relation_box_counts.get("InferenceBox", 0),
            },
        },
        "ontologyBoxCounts": ontology_box_counts,
        "ontologyRelationBoxCounts": ontology_relation_box_counts,
        "boundedContexts": bounded_contexts_payload(),
        "aboxBoundedContextCounts": bounded_context_counts,
        "stateContract": "review-data-change-evidence-validation",
        "dominantSector": top_sector.get("sector") or "",
        "dominantSectorRatio": number(top_sector.get("ratio")) if top_sector else 0.0,
        "cash": number(portfolio.cash),
        "riskBeliefCount": risk_count,
        "supportBeliefCount": support_count,
        "contradictionCount": contradictions,
        "relationInfluenceCount": relation_influence_count,
        "operationalOntology": {
            "collectionPipelineCount": len(pipeline_nodes),
            "insightCount": len(insight_nodes),
            "dispatchMode": str((dispatch_nodes[0].properties or {}).get("mode") or "insight-driven-only") if dispatch_nodes else "",
            "pipelines": [
                {
                    "key": str((item.properties or {}).get("key") or item.entity_id),
                    "targetMinutes": number((item.properties or {}).get("targetMinutes")),
                    "configuredMinutes": number((item.properties or {}).get("configuredMinutes")),
                }
                for item in pipeline_nodes
            ],
        },
        "actionRequiredSymbols": action_required,
        "externalSignalKeys": sorted(str(key) for key in external_signals.keys()) if isinstance(external_signals, dict) else [],
    }


def prompt_relation_sort_key(item: OntologyRelation):
    if (item.properties or {}).get("ontologyBox") == "TBox":
        return (0, 0, 0, 0, relation_key(item))
    properties = item.properties or {}
    box_order = {"InferenceBox": 3, "ABox": 2, "RuleBox": 1}.get(str(properties.get("ontologyBox") or "ABox"), 1)
    role_order = {"blocking": 5, "risk": 4, "counter": 3, "support": 2, "context": 1}.get(evidence_role_from_relation(properties), 0)
    focus = item.relation_type in {
        "CHANGED_FROM",
        "CONFIRMED_OVER",
        "FAILED_AFTER",
        "MATERIAL_TO",
        "HAS_TEMPORAL_WINDOW",
        "HAS_PRICE_PATH_PATTERN",
        "HAS_FLOW_PATTERN",
        "HAS_EVENT_CLUSTER",
        "DERIVES_TREND_EPISODE",
        "HAS_LIQUIDITY_PROFILE",
        "HAS_EXECUTION_METRIC",
        "LIMITED_BY_LIQUIDITY",
        "HAS_EXIT_CAPACITY",
        "HAS_EXECUTION_CAPACITY",
        "HAS_SLIPPAGE_RISK",
        "BREAKS_LEVEL",
        "RECLAIMS_LEVEL",
        "RETESTS_LEVEL",
        "HAS_COVERAGE_GAP",
        "HAS_MACRO_REGIME",
        "HAS_CRYPTO_EXPOSURE",
    }
    return (box_order, role_order, 1 if focus else 0, 1 if item.evidence_ids else 0, relation_key(item))


def prompt_evidence_sort_key(item: OntologyEvidence):
    payload = item.value or {}
    kind_order = {
        "inference-trace": 8,
        "disclosure": 7,
        "filing": 7,
        "financial-fact": 6,
        "market-move": 5,
        "news": 4,
        "data-quality": 3,
    }.get(item.kind, 1)
    box_order = 2 if ontology_box(payload) == "InferenceBox" else 1
    freshness = str(payload.get("publishedAt") or payload.get("asOf") or payload.get("updatedAt") or "")
    return (box_order, kind_order, 1 if item.summary else 0, freshness, item.evidence_id)


def prompt_belief_sort_key(item: OntologyBelief):
    polarity_order = {"risk": 3, "support": 2, "context": 1}.get(item.polarity, 0)
    return (polarity_order, 1 if item.evidence_ids else 0, item.belief_id)


def prompt_payload(graph: PortfolioOntology) -> Dict[str, object]:
    relations = sorted(graph.relations, key=prompt_relation_sort_key, reverse=True)
    evidence = sorted(graph.evidence, key=prompt_evidence_sort_key, reverse=True)
    beliefs = sorted(graph.beliefs, key=prompt_belief_sort_key, reverse=True)
    inferencebox = inferencebox_payload(graph)
    return {
        "tbox": ontology_tbox(),
        "boundedContexts": bounded_contexts_payload(),
        "ruleBox": rulebox_payload(graph),
        "abox": ontology_abox(graph),
        "inferenceBox": inferencebox,
        "derivedRelations": list(inferencebox.get("derivedRelations") or []),
        "inferenceTraces": list(inferencebox.get("traces") or []),
        "investmentQuestions": compact_entities_by_kind(graph, ["investment-question", "self-question"], 80),
        "hypothesisSets": compact_entities_by_kind(graph, ["hypothesis-set", "competing-hypothesis", "assumption", "hypothesis-calibration"], 160),
        "decisionEpisodes": compact_entities_by_kind(graph, ["decision-episode"], 80),
        "decisionPerformance": compact_entities_by_kind(graph, ["decision-performance", "rule-performance", "hypothesis-performance"], 120),
        "observedOutcomes": compact_entities_by_kind(graph, ["observed-outcome"], 120),
        "worldview": graph.worldview,
        "aiInferencePacket": build_ai_inference_packet(graph),
        "coverageGaps": compact_entities_by_kind(graph, ["coverage-gap", "temporal-coverage-gap"], 80),
        "temporalWindows": compact_entities_by_kind(
            graph,
            ["temporal-window", "price-path-pattern", "flow-pattern", "event-cluster", "trend-episode"],
            120,
        ),
        "macroRegimes": compact_entities_by_kind(graph, ["macro-regime", "interest-rate", "yield-curve", "fx-rate"], 60),
        "marketProxyContext": compact_entities_by_kind(graph, ["market-proxy-instrument", "market-proxy-theme", "market-proxy-observation"], 140),
        "cryptoExposures": compact_entities_by_kind(graph, ["crypto-asset", "crypto-market-signal", "crypto-exposure", "price-path"], 80),
        "valuationContext": compact_entities_by_kind(
            graph,
            [
                "valuation-assumption",
                "valuation-metric",
                "fair-value-estimate",
                "margin-of-safety",
                "relative-valuation",
                "revenue-exposure",
                "analyst-revision",
                "earnings-calendar-event",
            ],
            100,
        ),
        "newsQuality": compact_entities_by_kind(graph, ["article-quality-risk", "article-analysis-conflict", "article-ai-analysis"], 80),
        "reasoningCards": list(graph.reasoning_cards),
        "insights": [item.to_dict() for item in graph.entities if item.kind == "insight"],
        "trendTransitions": [
            item.to_dict()
            for item in graph.entities
            if item.kind == "trend-transition"
        ],
        "activeInvestmentOpinions": [
            dict((item.properties or {}).get("activeInvestmentOpinion") or {})
            for item in graph.entities
            if item.kind == "active-opinion"
        ],
        "executionPlans": [
            dict((item.properties or {}).get("executionPlan") or {})
            for item in graph.entities
            if item.kind == "execution-plan"
        ],
        "operationalOntology": dict((graph.worldview or {}).get("operationalOntology") or {}),
        "opinions": [item.to_dict() for item in graph.opinions],
        "relations": [item.to_dict() for item in relations[:120]],
        "evidence": [item.to_dict() for item in evidence[:120]],
        "beliefs": [item.to_dict() for item in beliefs[:100]],
    }


def build_investment_opinion_prompt(graph: PortfolioOntology) -> str:
    payload = json.dumps(prompt_payload(graph), ensure_ascii=False, sort_keys=True)
    return "\n".join([
        "너는 투자전략 관계 분석 데이터를 읽는 AI 투자 의견 리뷰어다.",
        "규칙 구조는 투자 핵심, 관측 데이터, 전략 가설, 리스크, 추론 인사이트, 운영/알림 바운디드 컨텍스트와 RuleBox로 나뉜 세계관이다.",
        "현재 데이터는 계좌의 실제 보유, 근거, 판단 근거, 운영 정책, 의견 기록이며 InferenceBox는 RuleBox가 파생한 관계와 추론 경로다.",
        "위험 지속, 회복·지지, 데이터 불확실성 가설을 동시에 비교한 뒤 현재 사실을 가장 잘 설명하는 잠정 가설을 선택해라.",
        "과거 DecisionEpisode, ObservedOutcome, hypothesis-calibration을 읽어 반복 반증된 가정과 규칙을 경고하되 표본 3건 미만의 보정값은 사용하지 말고, 답하지 못한 질문은 다음 수집 과제로 남겨라.",
        "제공된 근거 안에서 BUY, ADD, HOLD, TRIM, SELL, AVOID 중 하나의 투자 의견을 반드시 고르되 자동 주문 지시로 표현하지 마라.",
        "최종 판단은 확인 단계, 자료 상태, 변화, 근거 충돌을 기준으로 설명하고 숫자 점수나 확률을 만들지 마라.",
        "뉴스, 공시, SEC/OpenDART 근거와 출처 URL을 적극적으로 반영하고, 반대 근거와 무효화 조건을 함께 제시해라.",
        "새 관측값이나 관계가 추가되면 어떤 RuleBox 조건이 켜지고 어떤 InferenceBox 관계가 생겨 투자 가설, 리스크, 인사이트, 알림 정책에 영향을 주는지 먼저 추론해라.",
        "알림은 알림 타입별 주기가 아니라 새 조건·방향 변경·새 근거 여부와 쿨다운·억제 정책으로 설명해라.",
        "계좌번호, API 키, 토큰, 개인 식별정보를 추정하거나 요청하지 마라.",
        "응답 섹션은 반드시 투자 관점, 핵심 관계, 보유 이유와 반대 신호, 종목별 의견, 다음 검증 순서로 작성해라.",
        "",
        "프롬프트 버전: " + ONTOLOGY_PROMPT_VERSION,
        "관계 분석 데이터 JSON:",
        payload,
    ])
