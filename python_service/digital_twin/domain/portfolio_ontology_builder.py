from typing import Dict, Iterable, List

from .market_data import number
from .ontology_contracts import (
    OntologyEntity,
    OntologyRelation,
    PortfolioOntology,
    entity_id,
)
from .ontology_prompting import (
    ONTOLOGY_PROMPT_VERSION,
    build_investment_opinion_prompt,
    build_reasoning_cards,
)
from .ontology_schema import (
    abox_lifecycle_metadata,
    abox_properties,
    apply_abox_lifecycle,
    add_entity,
    add_relation,
    tbox_entities,
    tbox_relations,
)
from .ontology_external_abox import (
    add_external_signal_concepts,
    add_position_macro_context_concepts,
    add_symbol_external_signal_concepts,
)
from .portfolio_ontology_runtime_concepts import (
    add_account_delivery_profile_concepts,
    add_account_investment_strategy_concepts,
    add_decision_item_concepts,
    add_operational_world_concepts,
    add_position_strategy_role_concepts,
    add_runtime_metadata_concepts,
    add_runtime_setting_concepts,
    add_strategy_world_concepts,
    is_holding_position,
    is_watchlist_position,
)
from .portfolio_ontology_market_concepts import (
    add_data_source_concept,
    add_metric_concepts,
    add_price_level_and_liquidity_concepts,
    symbol_key,
)
from .portfolio_ontology_coverage import add_coverage_gap_concepts
from .portfolio_ontology_exposure_concepts import (
    add_instrument_profile_concepts,
    add_market_exposure_concepts,
    add_portfolio_factor_exposure_concepts,
    add_position_factor_concepts,
    position_weight,
)
from .portfolio_ontology_research_concepts import (
    add_research_evidence_concepts,
)
from .portfolio import PortfolioSummary, Position
from .portfolio_ontology_outputs import (
    dedupe_entities,
    dedupe_evidence,
    dedupe_relations,
)
from .portfolio_ontology_state import (
    add_fact_change_concepts,
    add_trend_transition_concepts,
)
from .portfolio_ontology_structure import (
    add_instrument_identity_concepts,
    instrument_tbox_classes,
    observable_position,
)


def build_portfolio_ontology(
    positions: Iterable[Position],
    portfolio: PortfolioSummary,
    legacy_by_symbol: Dict[str, Dict[str, object]] = None,
    external_signals: Dict[str, object] = None,
    portfolio_id: str = "portfolio",
    runtime_context: Dict[str, object] = None,
) -> PortfolioOntology:
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
    strategy_context = add_account_investment_strategy_concepts(graph, account_id_value, portfolio_node_id, account_context)
    strategy_profile = strategy_context.get("profile") if isinstance(strategy_context.get("profile"), dict) else {}
    strategy_fact_props = {
        "investmentStrategyProfile": strategy_profile.get("profile"),
        "investmentStrategyProfileLabel": strategy_profile.get("label"),
        "strategyLossTolerancePct": number(strategy_profile.get("lossTolerancePct")),
        "strategyProfitProtectionPct": number(strategy_profile.get("profitProtectionPct")),
        "strategyMaxPositionWeightPct": number(strategy_profile.get("maxPositionWeightPct")),
        "strategyMaxSectorWeightPct": number(strategy_profile.get("maxSectorWeightPct")),
        "strategyFxExposureReviewPct": number(strategy_profile.get("fxExposureReviewPct")),
        "strategyAddBuyWatchSignalMin": number(strategy_profile.get("addBuyWatchSignalMin")),
        "strategyAddBuyReviewSignalMin": number(strategy_profile.get("addBuyReviewSignalMin")),
        "strategyAllowLossAddBuyReview": bool(strategy_profile.get("allowLossAddBuyReview")),
    }
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
    if strategy_context.get("profileId"):
        add_relation(graph, strategy_id, str(strategy_context.get("profileId")), "USES_INVESTMENT_STRATEGY_PROFILE", weight=1.0, properties={"source": "account-context"})
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
        stock_tbox_classes = instrument_tbox_classes(position) + (["WatchlistCandidate"] if source == "watchlist" else [])
        graph.entities.append(OntologyEntity(stock_id, position.name or symbol, "stock", abox_properties({
            "symbol": symbol,
            "market": position.market,
            "currency": position.currency,
            "sector": position.sector,
            "source": source,
            "positionRole": source,
            "targetPositionRole": source,
            "currentPrice": number(position.current_price),
            "averagePrice": number(position.average_price),
            "quantity": number(position.quantity),
            "sellableQuantity": number(position.sellable_quantity),
            "marketValue": number(position.market_value),
            "profitLossRate": number(position.profit_loss_rate),
            "profitLoss": number(position.profit_loss),
            "positionWeight": round(position_weight(position, portfolio), 2),
            "positionAccountWeight": round(position_weight(position, portfolio), 2),
            "changeRate": number(position.change_rate),
            "priceChangeRate": number(position.change_rate),
            "ma5": number(position.ma5),
            "ma20": number(position.ma20),
            "ma60": number(position.ma60),
            "ma5Distance": number(getattr(position, "ma5_distance", 0.0)),
            "ma20Distance": number(position.ma20_distance),
            "ma60Distance": number(position.ma60_distance),
            "ma20Slope": number(position.ma20_slope),
            "ma60Slope": number(position.ma60_slope),
            "volume": number(position.volume),
            "volumeRatio": number(position.volume_ratio),
            "tradeStrength": number(position.trade_strength),
            "tradingValue": number(position.trading_value),
            "bidAskImbalance": number(position.bid_ask_imbalance),
            "foreignNetVolume": number(position.foreign_net_volume),
            "foreignNetAmount": number(position.foreign_net_amount),
            "institutionNetVolume": number(position.institution_net_volume),
            "institutionNetAmount": number(position.institution_net_amount),
            "individualNetVolume": number(position.individual_net_volume),
            "individualNetAmount": number(position.individual_net_amount),
            "smartMoneyNetVolume": number(position.foreign_net_volume) + number(position.institution_net_volume),
            "tboxClass": "Stock",
            "tboxClasses": stock_tbox_classes,
            **strategy_fact_props,
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
            **strategy_fact_props,
        })
        if holding:
            add_relation(graph, portfolio_node_id, position_id, "HAS_POSITION", weight=round(position_weight(position, portfolio) / 100, 4), properties={"source": source})
        elif watchlist_id:
            add_relation(graph, watchlist_id, position_id, "HAS_POSITION", weight=0.15, properties={"source": source})
        add_relation(graph, position_id, stock_id, "REPRESENTS_STOCK", weight=1.0, properties={"source": source})
        add_position_strategy_role_concepts(graph, position_id, stock_id, strategy_context, position)
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
        add_instrument_identity_concepts(graph, stock_id, position, source)
        add_data_source_concept(graph, stock_id, position, source)
        add_metric_concepts(graph, stock_id, position, source)
        add_price_level_and_liquidity_concepts(graph, stock_id, position, source)
        add_symbol_external_signal_concepts(graph, stock_id, symbol, external_signals)
        add_position_factor_concepts(graph, stock_id, portfolio_node_id, position, portfolio)
        add_instrument_profile_concepts(graph, stock_id, portfolio_node_id, position, runtime_context)
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
    add_decision_item_concepts(graph, runtime_context)
    add_coverage_gap_concepts(graph, observed_positions, portfolio_id)
    graph.entities = dedupe_entities(graph.entities)
    graph.relations = dedupe_relations(graph.relations)
    graph.evidence = dedupe_evidence(graph.evidence)
    apply_abox_lifecycle(graph, lifecycle_metadata)
    graph.reasoning_cards = build_reasoning_cards(graph)
    graph.prompt = build_investment_opinion_prompt(graph)
    graph.worldview = {
        "model": "ontology-abox-facts",
        "runtimeProjectionMode": "abox-facts-only-typedb-native-rules",
        "description": "Runtime ABox facts are projected for TypeDB schema-function inference. Python graph reasoning is not available in this path.",
        "positionCount": len([item for item in observed_positions if is_holding_position(item)]),
        "watchlistCount": len([item for item in observed_positions if is_watchlist_position(item)]),
        "aboxLifecycle": dict(lifecycle_metadata),
        "activeTBox": dict(runtime_context.get("activeTBox") or {}),
    }
    return graph
