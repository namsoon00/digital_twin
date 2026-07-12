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
from .portfolio_ontology_outputs import (
    add_ontology_insight_concepts,
    apply_relation_driven_opinions,
    dedupe_entities,
    dedupe_evidence,
    dedupe_relations,
)
from .portfolio_ontology_opinions import (
    build_position_opinion,
    build_watchlist_opinion,
    evidence_id,
    position_weight,
)
from .portfolio_ontology_state import (
    add_fact_change_concepts,
    add_relation_state_concepts,
    add_trend_transition_concepts,
)
from .portfolio_ontology_structure import (
    add_execution_plan_concepts,
    add_instrument_identity_concepts,
    compact_decision_drivers,
    compact_string_rows,
    instrument_tbox_classes,
    observable_position,
    risk_tbox_classes,
    unique_list,
)


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
            properties=abox_properties({"basis": "sector-weight", "polarity": "context"}),
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
        add_research_evidence_concepts(
            graph,
            stock_id,
            "",
            "",
            symbol,
            {},
            external_signals,
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
        active_relation_context = {}
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
    apply_graph_reasoning(graph)
    if not include_reasoning_outputs:
        graph.entities = dedupe_entities(graph.entities)
        graph.relations = dedupe_relations(graph.relations)
        graph.evidence = dedupe_evidence(graph.evidence)
        apply_abox_lifecycle(graph, lifecycle_metadata)
        graph.reasoning_cards = build_reasoning_cards(graph)
        graph.prompt = build_investment_opinion_prompt(graph)
        graph.worldview = {
            "model": "ontology-abox-facts",
            "runtimeProjectionMode": "abox-facts-only-graph-store-rulebox",
            "description": "Runtime ABox facts are projected for graph-store RuleBox reasoning; opinions, insights, and inference are produced after graph-store reasoning.",
            "positionCount": len([item for item in observed_positions if is_holding_position(item)]),
            "watchlistCount": len([item for item in observed_positions if is_watchlist_position(item)]),
            "aboxLifecycle": dict(lifecycle_metadata),
            "activeTBox": dict(runtime_context.get("activeTBox") or {}),
        }
        return graph
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
