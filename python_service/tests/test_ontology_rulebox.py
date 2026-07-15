import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.ontology_prompting import prompt_payload
from digital_twin.domain.instrument_profiles import parse_instrument_profiles_text
from digital_twin.domain.ontology_decision_policy import decision_stage_from_action, relation_stage_priority
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology as _build_portfolio_ontology
from digital_twin.domain.portfolio import Position
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.portfolio_ontology_market_concepts import missing_market_microstructure_fields
from digital_twin.infrastructure.typedb_ontology import (
    TypeDBOntologyGraphRepository,
    inferencebox_snapshot_from_rows,
    NullTypeDBOntologyGraphRepository,
    ontology_seed_graph,
    rulebox_graph_from_rules,
    rulebox_rules_from_payload,
    rulebox_rules_to_payload,
    rulebox_snapshot_from_rows,
)
from digital_twin.domain.ontology_rulebox_governance import rulebox_governance_candidates, rulebox_rules_hash, rulebox_version_payload


def build_portfolio_ontology(*args, **kwargs):
    kwargs.setdefault("include_reasoning_outputs", True)
    return _build_portfolio_ontology(*args, **kwargs)


class OntologyRuleBoxTests(unittest.TestCase):
    def loss_guard_graph(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            quantity=10,
            sellable_quantity=10,
            average_price=80000,
            current_price=69000,
            market_value=690000,
            profit_loss=-110000,
            profit_loss_rate=-12.4,
            ma20=76000,
            ma60=73000,
            ma20_distance=-9.2,
            ma60_distance=-5.5,
            volume_ratio=1.4,
            trading_value=5000000000,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology([position], portfolio, portfolio_id="rulebox-test")

    def strategy_threshold_loss_graph(self, strategy_profile: str, pnl_rate: float):
        current_price = 100000 * (1 + pnl_rate / 100)
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            quantity=5,
            sellable_quantity=5,
            average_price=100000,
            current_price=current_price,
            market_value=current_price * 5,
            profit_loss=(current_price - 100000) * 5,
            profit_loss_rate=pnl_rate,
            ma20=110000,
            ma60=108000,
            ma20_distance=-8.5,
            ma60_distance=-6.0,
            volume_ratio=1.2,
            trading_value=5000000000,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology(
            [position],
            portfolio,
            portfolio_id="rulebox-strategy-threshold-" + strategy_profile,
            runtime_context={"account": {"investmentStrategyProfile": strategy_profile}},
        )

    def strategy_threshold_profit_graph(self, strategy_profile: str, pnl_rate: float):
        current_price = 100000 * (1 + pnl_rate / 100)
        position = Position(
            symbol="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            quantity=2,
            sellable_quantity=2,
            average_price=100000,
            current_price=current_price,
            market_value=current_price * 2,
            profit_loss=(current_price - 100000) * 2,
            profit_loss_rate=pnl_rate,
            ma20=128000,
            ma60=124000,
            ma20_distance=-3.0,
            ma60_distance=1.0,
            volume_ratio=1.1,
            trading_value=100000000,
            sector="AI",
        )
        portfolio = portfolio_summary([position], account_cash=200000, fx_rates={"USD": 1400})
        return build_portfolio_ontology(
            [position],
            portfolio,
            portfolio_id="rulebox-profit-threshold-" + strategy_profile,
            runtime_context={"account": {"investmentStrategyProfile": strategy_profile}},
        )

    def flow_pressure_graph(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            quantity=5,
            sellable_quantity=5,
            average_price=210000,
            current_price=208000,
            market_value=1040000,
            profit_loss=-10000,
            profit_loss_rate=-1.0,
            volume_ratio=1.6,
            bid_ask_imbalance=-24.0,
            trade_strength=91.0,
            trading_value=9000000000,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology([position], portfolio, portfolio_id="rulebox-flow-test")

    def retail_dip_buying_graph(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            quantity=7,
            sellable_quantity=7,
            average_price=2343143,
            current_price=1913000,
            market_value=13391000,
            profit_loss_rate=-18.1,
            ma20=2449050,
            ma60=2015417,
            ma20_distance=-21.9,
            ma60_distance=-5.1,
            change_rate=3.69,
            foreign_net_volume=-665995,
            institution_net_volume=-701427,
            individual_net_volume=1362458,
            volume_ratio=1.3,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology([position], portfolio, portfolio_id="rulebox-retail-dip-risk")

    def smart_money_accumulation_graph(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            quantity=10,
            sellable_quantity=10,
            average_price=327000,
            current_price=296250,
            market_value=2962500,
            profit_loss_rate=-9.4,
            ma20=324112,
            ma60=289838,
            ma20_distance=-8.6,
            ma60_distance=2.2,
            change_rate=1.6,
            foreign_net_volume=845552,
            institution_net_volume=1107761,
            individual_net_volume=-1739937,
            volume_ratio=0.4,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology([position], portfolio, portfolio_id="rulebox-smart-money-accumulation")

    def profitable_momentum_graph(self, symbol="MSTR", settings=None):
        position = Position(
            symbol=symbol,
            name="Strategy" if symbol == "MSTR" else "Tesla",
            market="US",
            currency="USD",
            quantity=10,
            sellable_quantity=10,
            average_price=88,
            current_price=105,
            market_value=1050,
            profit_loss=170,
            profit_loss_rate=19.3,
            change_rate=2.2,
            ma5=101,
            ma20=100,
            ma60=95,
            ma20_distance=5.0,
            ma60_distance=10.5,
            volume_ratio=1.3,
            trade_strength=108,
            bid_ask_imbalance=12,
            trading_value=100000000,
            sector="디지털자산" if symbol == "MSTR" else "모빌리티",
        )
        portfolio = portfolio_summary([position], account_cash=10000, fx_rates={"USD": 1400})
        return build_portfolio_ontology(
            [position],
            portfolio,
            portfolio_id="rulebox-profile-" + symbol.lower(),
            runtime_context={"settings": settings or {}},
        )

    def data_quality_gap_graph(self):
        position = Position(
            symbol="035420",
            name="NAVER",
            market="KR",
            currency="KRW",
            quantity=3,
            sellable_quantity=3,
            average_price=204000,
            current_price=197200,
            market_value=591600,
            profit_loss=-20400,
            profit_loss_rate=-3.6,
            ma20=213940,
            ma60=217075,
            ma20_distance=-7.8,
            ma60_distance=-9.2,
            volume_ratio=0.8,
            trading_value=3000000000,
            sector="플랫폼",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology([position], portfolio, portfolio_id="rulebox-data-quality-test")

    def liquid_small_position_graph(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            quantity=10,
            sellable_quantity=10,
            average_price=327000,
            current_price=254500,
            market_value=2545000,
            profit_loss=-725000,
            profit_loss_rate=-21.6,
            ma20=319375,
            ma60=290467,
            ma20_distance=-20.3,
            ma60_distance=-12.4,
            volume=31882652,
            volume_ratio=1.6,
            trading_value=8455100000000,
            trade_strength=89.1,
            buy_volume=10445338,
            sell_volume=11162345,
            orderbook_bid_volume=1585913,
            orderbook_ask_volume=205943,
            bid_ask_imbalance=77.0,
            foreign_net_volume=-717007,
            institution_net_volume=-3216316,
            individual_net_volume=4177230,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology([position], portfolio, portfolio_id="rulebox-execution-safe-test")

    def illiquid_large_position_graph(self):
        position = Position(
            symbol="123450",
            name="테스트소형주",
            market="KR",
            currency="KRW",
            quantity=100000,
            sellable_quantity=100000,
            average_price=1000,
            current_price=1000,
            market_value=100000000,
            profit_loss=-5000000,
            profit_loss_rate=-5.0,
            ma20=1050,
            ma60=1100,
            ma20_distance=-4.8,
            ma60_distance=-9.1,
            volume=50000,
            volume_ratio=0.4,
            trading_value=500000000,
            trade_strength=72.0,
            buy_volume=18000,
            sell_volume=32000,
            orderbook_bid_volume=1000,
            orderbook_ask_volume=8000,
            bid_ask_imbalance=-77.8,
            foreign_net_volume=-2000,
            institution_net_volume=-1500,
            individual_net_volume=3500,
            sector="테스트",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology([position], portfolio, portfolio_id="rulebox-execution-block-test")

    def direct_context_news_graph(self):
        position = Position(
            symbol="AAPL",
            name="Apple",
            market="NASDAQ",
            currency="USD",
            quantity=0,
            current_price=212,
            market_value=0,
            sector="AI",
            source="watchlist",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology(
            [position],
            portfolio,
            external_signals={
                "researchEvidence": {
                    "AAPL": [
                        {
                            "symbol": "AAPL",
                            "kind": "news",
                            "source": "Reuters",
                            "title": "Apple names a new product operations leader",
                            "summary": "Apple disclosed an executive transition that may affect product launch execution.",
                            "url": "https://example.test/apple-operations",
                            "polarity": "context",
                            "impactScore": 4,
                            "confidence": 0.82,
                            "relationScope": "direct",
                            "materialityPassed": True,
                            "materialityScore": 72,
                            "relevanceScore": 94,
                            "sourceReliability": 82,
                            "eventType": "general",
                        }
                    ]
                }
            },
            portfolio_id="rulebox-context-news-test",
        )

    def direct_ai_risk_news_graph(self):
        position = Position(
            symbol="AAPL",
            name="Apple",
            market="NASDAQ",
            currency="USD",
            quantity=2,
            current_price=212,
            market_value=424,
            sector="AI",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology(
            [position],
            portfolio,
            external_signals={
                "researchEvidence": {
                    "AAPL": [
                        {
                            "symbol": "AAPL",
                            "kind": "news",
                            "source": "Reuters",
                            "title": "Apple shares fall on earnings concern",
                            "summary": "실적 우려와 주가 하락 기사입니다.",
                            "url": "https://example.test/apple-risk",
                            "polarity": "risk",
                            "impactScore": 88,
                            "confidence": 0.82,
                            "relationScope": "direct",
                            "materialityPassed": True,
                            "materialityScore": 88,
                            "relevanceScore": 96,
                            "sourceReliability": 90,
                            "eventType": "earnings",
                            "aiAnalysis": {
                                "version": "news-ai-analysis-v1",
                                "status": "ok",
                                "readScope": "title+rss-summary",
                                "relationScope": "direct",
                                "eventType": "earnings",
                                "impactPolarity": "risk",
                                "impactLabelKo": "악재",
                                "confidence": 0.82,
                                "materialityScore": 88,
                                "relevanceScore": 96,
                                "needsReview": True,
                                "summary": {
                                    "briefKo": "실적 우려가 가격 부담으로 작용할 수 있습니다.",
                                    "watchPoints": ["원문 본문 확보", "가격 반응"],
                                },
                                "riskSignals": ["실적 우려", "하락"],
                            },
                        }
                    ]
                }
            },
            portfolio_id="rulebox-ai-news-test",
        )

    def test_rulebox_materializes_rules_and_inference_relations(self):
        graph = self.loss_guard_graph()

        rule_entities = [
            item
            for item in graph.entities
            if item.kind == "rule" and (item.properties or {}).get("ontologyBox") == "RuleBox"
        ]
        inference_relations = [
            item
            for item in graph.relations
            if (item.properties or {}).get("ontologyBox") == "InferenceBox"
        ]
        loss_guard_relations = [
            item
            for item in inference_relations
            if item.source == "stock:005930" and item.relation_type == "HAS_INFERRED_RISK"
        ]
        loss_trace = next(
            item
            for item in graph.entities
            if item.kind == "inference-trace" and (item.properties or {}).get("ruleId") == "graph.loss_guard.breakdown.v1"
        )
        matched_ids = [
            item.get("conditionId")
            for item in ((loss_trace.properties or {}).get("matchedConditions") or [])
            if isinstance(item, dict)
        ]
        opinion = graph.opinion_for_symbol("005930")

        self.assertTrue(any((item.properties or {}).get("ruleId") == "graph.loss_guard.breakdown.v1" for item in rule_entities))
        self.assertTrue(loss_guard_relations)
        self.assertIn("strategy-risk-budget", matched_ids)
        self.assertTrue(any(item.kind == "relation-rule" and (item.properties or {}).get("ruleId") == "holding.loss_guard.breakdown.v1" for item in graph.entities))
        self.assertTrue(any(item.kind == "inference-trace" for item in graph.entities))
        self.assertTrue(any(item.kind == "inference-trace" for item in graph.evidence))
        self.assertIsNotNone(opinion)
        self.assertTrue(any("손실 방어 추론" in str(item.get("label") or "") for item in opinion.relation_influences))

    def test_loss_guard_uses_account_strategy_loss_threshold(self):
        aggressive_graph = self.strategy_threshold_loss_graph("aggressive", -9.6)
        conservative_graph = self.strategy_threshold_loss_graph("capitalPreservation", -6.2)

        aggressive_stock = next(item for item in aggressive_graph.entities if item.entity_id == "stock:000660")
        aggressive_loss_guard = [
            item
            for item in aggressive_graph.relations
            if item.source == "stock:000660" and item.relation_type == "HAS_INFERRED_RISK" and "loss-guard-breakdown" in str(item.target)
        ]
        conservative_loss_guard = [
            item
            for item in conservative_graph.relations
            if item.source == "stock:000660" and item.relation_type == "HAS_INFERRED_RISK" and "loss-guard-breakdown" in str(item.target)
        ]

        self.assertEqual("aggressive", aggressive_stock.properties["investmentStrategyProfile"])
        self.assertEqual(-15, aggressive_stock.properties["strategyLossTolerancePct"])
        self.assertEqual([], aggressive_loss_guard)
        self.assertTrue(conservative_loss_guard)

    def test_profit_protection_uses_account_strategy_profit_threshold(self):
        aggressive_graph = self.strategy_threshold_profit_graph("aggressive", 15.0)
        balanced_graph = self.strategy_threshold_profit_graph("balanced", 15.0)

        aggressive_profit_protect = [
            item
            for item in aggressive_graph.relations
            if item.source == "stock:AAPL" and item.relation_type == "HAS_ACTION_CANDIDATE" and "profit-protect" in str(item.target)
        ]
        balanced_profit_protect = [
            item
            for item in balanced_graph.relations
            if item.source == "stock:AAPL" and item.relation_type == "HAS_ACTION_CANDIDATE" and "profit-protect" in str(item.target)
        ]

        self.assertEqual([], aggressive_profit_protect)
        self.assertTrue(balanced_profit_protect)

    def test_local_rulebox_uses_flow_value_filters(self):
        graph = self.flow_pressure_graph()

        flow_risk_relations = [
            item
            for item in graph.relations
            if item.source == "stock:000660"
            and item.relation_type == "HAS_INFERRED_RISK"
            and (item.properties or {}).get("ruleId") == "graph.flow.sell_pressure.v1"
        ]
        flow_trace = next(
            item
            for item in graph.entities
            if item.kind == "inference-trace" and (item.properties or {}).get("ruleId") == "graph.flow.sell_pressure.v1"
        )
        matched_ids = [
            item.get("conditionId")
            for item in ((flow_trace.properties or {}).get("matchedConditions") or [])
            if isinstance(item, dict)
        ]

        self.assertTrue(flow_risk_relations)
        self.assertIn("ask-pressure", matched_ids)
        self.assertIn("volume-confirmation", matched_ids)

    def test_investor_flow_psychology_blocks_retail_dip_buying(self):
        graph = self.retail_dip_buying_graph()

        sentiment = next(
            item
            for item in graph.entities
            if item.kind == "investor-flow-sentiment" and (item.properties or {}).get("field") == "retailDipBuyingRisk"
        )
        sentiment_relations = [
            item for item in graph.relations
            if item.source == "stock:000660" and item.target == sentiment.entity_id and item.relation_type == "HAS_INVESTOR_FLOW_SENTIMENT"
        ]
        blocked_relations = [
            item for item in graph.relations
            if item.source == "stock:000660"
            and item.relation_type == "BLOCKS_ACTION"
            and (item.properties or {}).get("ruleId") == "graph.investor_flow.retail_dip_buying_risk.v1"
        ]

        self.assertEqual("risk", sentiment.properties["polarity"])
        self.assertTrue(sentiment_relations)
        self.assertTrue(blocked_relations)

    def test_investor_flow_psychology_supports_smart_money_accumulation(self):
        graph = self.smart_money_accumulation_graph()

        sentiment = next(
            item
            for item in graph.entities
            if item.kind == "investor-flow-sentiment" and (item.properties or {}).get("field") in {"smartMoneyDipAbsorption", "smartMoneyAccumulation"}
        )
        support_relations = [
            item for item in graph.relations
            if item.source == "stock:005930"
            and item.relation_type == "HAS_INFERRED_SUPPORT"
            and (item.properties or {}).get("ruleId") == "graph.investor_flow.smart_money_accumulation.v1"
        ]

        self.assertEqual("support", sentiment.properties["polarity"])
        self.assertTrue(support_relations)

    def test_execution_metrics_keep_small_liquid_holding_from_action_block(self):
        graph = self.liquid_small_position_graph()

        execution_metrics = [
            item for item in graph.entities
            if item.kind == "execution-metric"
        ]
        execution_capacity = [
            item
            for item in graph.relations
            if item.source == "stock:005930"
            and item.relation_type == "HAS_EXECUTION_CAPACITY"
            and (item.properties or {}).get("ruleId") == "graph.execution.capacity_safe.v1"
        ]
        execution_blocks = [
            item
            for item in graph.relations
            if item.source == "stock:005930"
            and item.relation_type == "BLOCKS_ACTION"
            and (item.properties or {}).get("ruleId") == "graph.execution.liquidity_or_slippage_block.v1"
        ]

        self.assertIn("positionToTradingValuePct", {(item.properties or {}).get("field") for item in execution_metrics})
        self.assertIn("slippageRiskScore", {(item.properties or {}).get("field") for item in execution_metrics})
        self.assertTrue(any(item.relation_type == "HAS_LIQUIDITY_PROFILE" for item in graph.relations))
        self.assertTrue(execution_capacity)
        self.assertFalse(execution_blocks)

    def test_execution_block_requires_rulebox_execution_thresholds(self):
        graph = self.illiquid_large_position_graph()

        execution_block = [
            item
            for item in graph.relations
            if item.source == "stock:123450"
            and item.relation_type == "BLOCKS_ACTION"
            and (item.properties or {}).get("ruleId") == "graph.execution.liquidity_or_slippage_block.v1"
        ]
        trace = next(
            item
            for item in graph.entities
            if item.kind == "inference-trace"
            and (item.properties or {}).get("ruleId") == "graph.execution.liquidity_or_slippage_block.v1"
        )
        matched_ids = [
            item.get("conditionId")
            for item in ((trace.properties or {}).get("matchedConditions") or [])
            if isinstance(item, dict)
        ]

        self.assertTrue(execution_block)
        self.assertIn("large-position-block", matched_ids)
        self.assertIn("visible-depth-block", matched_ids)

    def test_local_rulebox_uses_missing_microstructure_data_quality(self):
        graph = self.data_quality_gap_graph()

        missing_nodes = [
            item for item in graph.entities
            if item.kind == "missing-data" and (item.properties or {}).get("field")
        ]
        data_quality_relations = [
            item
            for item in graph.relations
            if item.source == "stock:035420"
            and item.relation_type == "HAS_INFERRED_RISK"
            and (item.properties or {}).get("ruleId") == "graph.data_quality.microstructure_gap.v1"
        ]
        confidence_relations = [
            item
            for item in graph.relations
            if item.source == "stock:035420"
            and item.relation_type == "LOWERS_CONFIDENCE_OF"
            and (item.properties or {}).get("ruleId") == "graph.data_quality.microstructure_gap.v1"
        ]
        blocked_relations = [
            item
            for item in graph.relations
            if item.source == "stock:035420"
            and item.relation_type == "BLOCKS_ACTION"
            and (item.properties or {}).get("ruleId") == "graph.data_quality.action_block.v1"
        ]
        trace = next(
            item
            for item in graph.entities
            if item.kind == "inference-trace" and (item.properties or {}).get("ruleId") == "graph.data_quality.microstructure_gap.v1"
        )
        matched_ids = [
            item.get("conditionId")
            for item in ((trace.properties or {}).get("matchedConditions") or [])
            if isinstance(item, dict)
        ]

        self.assertTrue(missing_nodes)
        self.assertIn("tradeStrength", {(item.properties or {}).get("field") for item in missing_nodes})
        self.assertEqual({"market-microstructure"}, {(item.properties or {}).get("dataScope") for item in missing_nodes})
        self.assertTrue(data_quality_relations)
        self.assertTrue(confidence_relations)
        self.assertTrue(blocked_relations)
        self.assertIn("microstructure-missing", matched_ids)

    def test_microstructure_missing_data_is_market_specific(self):
        us_position = Position(symbol="AAPL", name="Apple", market="NASDAQ", currency="USD")
        kr_position = Position(symbol="005930", name="삼성전자", market="KR", currency="KRW")

        self.assertEqual([], missing_market_microstructure_fields(us_position))
        self.assertIn("tradeStrength", {item["field"] for item in missing_market_microstructure_fields(kr_position)})

    def test_local_rulebox_promotes_direct_material_context_news_to_next_check(self):
        graph = self.direct_context_news_graph()

        context_relations = [
            item
            for item in graph.relations
            if item.source == "stock:AAPL"
            and item.relation_type == "REQUIRES_NEXT_CHECK"
            and (item.properties or {}).get("ruleId") == "graph.news.direct_material_context.v1"
        ]
        trace = next(
            item
            for item in graph.entities
            if item.kind == "inference-trace" and (item.properties or {}).get("ruleId") == "graph.news.direct_material_context.v1"
        )
        matched_ids = [
            item.get("conditionId")
            for item in ((trace.properties or {}).get("matchedConditions") or [])
            if isinstance(item, dict)
        ]

        self.assertTrue(context_relations)
        self.assertIn("direct-material-context", matched_ids)

    def test_local_rulebox_uses_article_ai_analysis_for_news_risk_and_body_gap(self):
        graph = self.direct_ai_risk_news_graph()

        ai_risk_relations = [
            item
            for item in graph.relations
            if item.source == "stock:AAPL"
            and item.relation_type == "HAS_INFERRED_RISK"
            and (item.properties or {}).get("ruleId") == "graph.news.ai_direct_risk.v1"
        ]
        body_check_relations = [
            item
            for item in graph.relations
            if item.source == "stock:AAPL"
            and item.relation_type == "REQUIRES_NEXT_CHECK"
            and (item.properties or {}).get("ruleId") == "graph.news.ai_body_missing_review.v1"
        ]
        confidence_relations = [
            item
            for item in graph.relations
            if item.source == "stock:AAPL"
            and item.relation_type == "LOWERS_CONFIDENCE_OF"
            and (item.properties or {}).get("ruleId") == "graph.news.ai_body_missing_review.v1"
        ]
        risk_trace = next(
            item
            for item in graph.entities
            if item.kind == "inference-trace" and (item.properties or {}).get("ruleId") == "graph.news.ai_direct_risk.v1"
        )
        matched_ids = [
            item.get("conditionId")
            for item in ((risk_trace.properties or {}).get("matchedConditions") or [])
            if isinstance(item, dict)
        ]

        self.assertTrue(ai_risk_relations)
        self.assertTrue(body_check_relations)
        self.assertTrue(confidence_relations)
        self.assertIn("ai-direct-risk", matched_ids)

    def test_prompt_payload_exposes_rulebox_and_inferencebox(self):
        graph = self.loss_guard_graph()
        payload = prompt_payload(graph)

        self.assertGreater(payload["ruleBox"]["ruleCount"], 0)
        self.assertGreater(payload["ruleBox"]["relationRuleCount"], 0)
        self.assertTrue(any(item["properties"]["ruleId"] == "holding.loss_guard.breakdown.v1" for item in payload["ruleBox"]["relationRules"]))
        self.assertGreater(payload["inferenceBox"]["traceCount"], 0)
        self.assertTrue(any(item["type"] == "HAS_INFERRED_RISK" for item in payload["derivedRelations"]))
        self.assertIn("ruleBox", payload["aiInferencePacket"]["inputOrder"])
        self.assertGreater(payload["aiInferencePacket"]["graphInputs"]["inferenceBoxRelationCount"], 0)

    def test_typedb_projection_promotes_rule_and_inference_query_keys(self):
        graph = self.loss_guard_graph()
        repository = TypeDBOntologyGraphRepository("http://typedb.example.test")

        rule_row = next(item for item in repository.rows_for_entities(graph) if item["id"] == "rule:graph.loss_guard.breakdown.v1")
        stock_class_row = next(item for item in repository.rows_for_entities(graph) if item["id"] == "tbox-class:Stock")
        holds_relation_row = next(item for item in repository.rows_for_entities(graph) if item["id"] == "tbox-relation:HOLDS")
        condition_row = next(item for item in repository.rows_for_entities(graph) if item["id"] == "rule-condition:graph.loss_guard.breakdown.v1:ma-break")
        template_row = next(item for item in repository.rows_for_entities(graph) if item["id"] == "relation-template:graph.loss_guard.breakdown.v1:0")
        inference_row = next(item for item in repository.rows_for_entities(graph) if item["kind"] == "inference-trace")
        risk_relation = next(item for item in repository.rows_for_relations(graph) if item["type"] == "HAS_INFERRED_RISK")
        inference_evidence = next(item for item in repository.rows_for_evidence(graph) if item["kind"] == "inference-trace")
        schema_text = repository.schema_query()
        query_text = "\n".join(repository.insert_queries(graph))

        self.assertEqual("Stock", stock_class_row["className"])
        self.assertEqual("HOLDS", holds_relation_row["relationTypeName"])
        self.assertEqual("TBox", stock_class_row["ontologyBox"])
        self.assertEqual("TBox", holds_relation_row["ontologyBox"])
        self.assertEqual("RuleBox", rule_row["ontologyBox"])
        self.assertTrue(rule_row["tboxVersion"])
        self.assertEqual("graph.loss_guard.breakdown.v1", rule_row["ruleId"])
        self.assertEqual("relation", condition_row["conditionKind"])
        self.assertEqual("BREAKS_LEVEL", condition_row["conditionRelationType"])
        self.assertEqual(["ma20", "ma60"], condition_row["conditionTargetLevelTypes"])
        self.assertEqual([], condition_row["conditionTargetFields"])
        self.assertEqual("HAS_INFERRED_RISK", template_row["derivationRelationType"])
        self.assertEqual("risk", template_row["derivationTargetKind"])
        self.assertEqual("LOSS_REDUCE", template_row["derivationDecisionStage"])
        self.assertGreaterEqual(template_row["derivationStagePriority"], 40)
        self.assertEqual("InferenceBox", inference_row["ontologyBox"])
        self.assertTrue(inference_row["tboxVersion"])
        self.assertEqual("InferenceBox", risk_relation["ontologyBox"])
        self.assertTrue(risk_relation["tboxVersion"])
        self.assertEqual("graph.loss_guard.breakdown.v1", risk_relation["ruleId"])
        self.assertEqual("LOSS_REDUCE", risk_relation["decisionStage"])
        self.assertGreaterEqual(risk_relation["stagePriority"], 40)
        self.assertEqual("InferenceBox", inference_evidence["ontologyBox"])
        self.assertIn("attribute ontology-rule-id", schema_text)
        self.assertIn("attribute ontology-json", schema_text)
        self.assertIn("attribute ontology-tbox-class", schema_text)
        self.assertIn('has ontology-box "TBox"', query_text)
        self.assertIn('has ontology-box "ABox"', query_text)
        self.assertIn('has ontology-box "RuleBox"', query_text)
        self.assertIn('has ontology-box "InferenceBox"', query_text)
        self.assertIn('has ontology-tbox-class "Stock"', query_text)
        self.assertIn('has ontology-relation-type "HOLDS"', query_text)

    def test_ontology_seed_graph_contains_tbox_and_rulebox_for_typedb(self):
        graph = ontology_seed_graph()
        repository = TypeDBOntologyGraphRepository("http://typedb.example.test")
        entity_rows = repository.rows_for_entities(graph)
        relation_rows = repository.rows_for_relations(graph)
        result = NullTypeDBOntologyGraphRepository().seed_ontology()

        self.assertTrue(any(row["ontologyBox"] == "TBox" and row["kind"] == "tbox-class" for row in entity_rows))
        self.assertTrue(any(row["ontologyBox"] == "RuleBox" and row["kind"] == "rule" for row in entity_rows))
        self.assertTrue(any(row["ontologyBox"] == "TBox" and row["type"] == "DEFINES_CLASS" for row in relation_rows))
        self.assertTrue(any(row["ontologyBox"] == "RuleBox" and row["type"] == "HAS_CONDITION" for row in relation_rows))
        self.assertFalse(result["saved"])
        self.assertGreater(result["entityCount"], 0)
        self.assertGreater(result["relationCount"], 0)

    def test_typedb_run_rulebox_materializes_inferencebox_from_typedb_projection(self):
        class CapturingTypeDBRepository(TypeDBOntologyGraphRepository):
            def __init__(self, graph):
                super().__init__("127.0.0.1:1729")
                self._last_graph = graph
                self.saved_inferencebox_graph = None

            def write_inferencebox_graph(self, graph):
                self.saved_inferencebox_graph = graph
                return {"configured": True, "saved": True, "status": "ok", "graphStore": "typedb"}

            def read_entity_rows(self, boxes=None, limit=0):
                if list(boxes or []) == ["ABox"]:
                    return [{"id": "stock:005930", "ontologyBox": "ABox"}]
                return []

            def load_graph_from_typedb(self, boxes=None):
                return self._last_graph

            def rulebox_snapshot(self):
                return {
                    "configured": True,
                    "saved": True,
                    "status": "ok",
                    "graphStore": "typedb",
                    "rules": [default_graph_inference_rules()[0].to_dict()],
                    "ruleCount": 1,
                }

            def clear_inferencebox(self):
                return {"configured": True, "status": "ok", "graphStore": "typedb", "clearedBox": "InferenceBox"}

            def sync_typedb_native_rule_functions(self, rules, force=False):
                rule_list = list(rules or [])
                return {
                    "configured": True,
                    "status": "ok",
                    "graphStore": "typedb",
                    "syncedCount": len(rule_list),
                    "syncedFunctionCount": len(rule_list),
                    "skippedCount": 0,
                    "failedCount": 0,
                }

            def match_typedb_native_rules(self, rules, target_symbols=None):
                rule = list(rules or [])[0]
                return {
                    "status": "ok",
                    "graphStore": "typedb",
                    "nativeQueryUsed": True,
                    "schemaFunctionUsed": True,
                    "executedRuleCount": 1,
                    "skippedRuleCount": 0,
                    "matchedCount": 1,
                    "matches": [{
                        "ruleId": rule.rule_id,
                        "nativeRuleId": "typedb.native." + rule.rule_id,
                        "sourceId": "stock:005930",
                        "matchedConditions": [{"conditionId": "holding-source"}],
                        "evidenceRelationIds": [],
                        "confidence": 0.86,
                    }],
                }

        repository = CapturingTypeDBRepository(self.loss_guard_graph())

        result = repository.run_rulebox({})

        self.assertEqual("ok", result["status"])
        self.assertEqual("typedb", result["graphStore"])
        self.assertEqual("typedb-native-rule-materialized", result["reasoningMode"])
        self.assertFalse(result["typedbBootstrapReasoningUsed"])
        self.assertTrue(result["nativeTypeDbReasoningUsed"])
        self.assertTrue(result["pythonBootstrapDisabled"])
        self.assertGreater(result["statementCount"], 0)
        self.assertIn("HAS_INFERRED_RISK", result["relationTypes"])
        self.assertEqual({}, result["clearResult"])
        self.assertIsNotNone(repository.saved_inferencebox_graph)
        self.assertTrue(repository.saved_inferencebox_graph.entities)
        self.assertTrue(all((item.properties or {}).get("nativeTypeDbReasoned") for item in repository.saved_inferencebox_graph.entities))
        self.assertTrue(all((item.properties or {}).get("typedbNativeRuleReasoned") for item in repository.saved_inferencebox_graph.entities))

    def test_default_rulebox_covers_materiality_and_trend_transition_rules(self):
        rules = default_graph_inference_rules()
        rule_ids = {item.rule_id for item in rules}
        graph = rulebox_graph_from_rules(rules)
        repository = TypeDBOntologyGraphRepository("http://typedb.example.test")
        condition_rows = repository.rows_for_entities(graph)
        support_transition = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.watchlist.trend_transition.support.v1:support-transition"
        )
        risk_transition = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.holding.trend_transition.risk.v1:risk-transition"
        )
        sell_pressure = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.flow.sell_pressure.v1:ask-pressure"
        )
        sell_volume = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.flow.sell_pressure.v1:volume-confirmation"
        )
        direct_news_risk = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.news.direct_material_risk.v1:direct-material-risk"
        )
        direct_news_context = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.news.direct_material_context.v1:direct-material-context"
        )
        fact_change_gate = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.materiality.alert_candidate.v1:material-fact-change"
        )
        microstructure_gap = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.data_quality.microstructure_gap.v1:microstructure-missing"
        )
        news_analysis_conflict = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.data_quality.news_analysis_conflict.v1:news-analysis-conflict"
        )
        execution_slippage = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.execution.liquidity_or_slippage_block.v1:slippage-score-block"
        )
        price_reclaim_not = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.price.reclaim.thesis_support.v1:no-severe-microstructure-gap"
        )
        portfolio_concentration = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.portfolio.concentration.review.v1:sector-concentration-risk"
        )
        strategy_risk_budget = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.loss_guard.breakdown.v1:strategy-risk-budget"
        )
        strategy_profit_policy = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.profit_protect.trend_break.v1:strategy-profit-policy"
        )
        watchlist_strategy_role = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.watchlist.trend_transition.support.v1:watchlist-strategy-role"
        )
        loss_smart_money = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.loss_smart_money.defense.v1:joint-smart-money-inflow"
        )
        investor_flow_accumulation = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.investor_flow.smart_money_accumulation.v1:smart-money-accumulation"
        )
        retail_dip_buying = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.investor_flow.retail_dip_buying_risk.v1:retail-dip-buying-risk"
        )
        add_buy_volume = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.loss_smart_money.add_buy_review.v1:volume-confirmation"
        )
        add_buy_gap_guard = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.loss_smart_money.add_buy_review.v1:no-severe-microstructure-gap"
        )
        winner_add_ma5 = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.winner_momentum.add_buy_review.v1:ma5-reclaim"
        )
        winner_add_profile = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.winner_momentum.add_buy_review.v1:instrument-profile-allows-strength-add"
        )
        winner_add_volume = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.winner_momentum.add_buy_review.v1:volume-confirmation"
        )
        profile_averaging_policy = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.instrument_profile.averaging_down_policy.v1:profile-avoid-averaging-down"
        )
        coverage_gap = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.coverage.gap.confidence_limit.v1:coverage-gap"
        )
        macro_regime = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.macro.regime.risk.v1:macro-regime-risk"
        )
        crypto_exposure = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.crypto.exposure.volatility_risk.v1:crypto-exposure-risk"
        )
        news_quality = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.news.quality.confidence_limit.v1:news-quality-risk"
        )

        self.assertIn("graph.materiality.alert_candidate.v1", rule_ids)
        self.assertIn("graph.loss_smart_money.defense.v1", rule_ids)
        self.assertIn("graph.investor_flow.smart_money_accumulation.v1", rule_ids)
        self.assertIn("graph.investor_flow.retail_dip_buying_risk.v1", rule_ids)
        self.assertIn("graph.investor_flow.smart_money_outflow_risk.v1", rule_ids)
        self.assertIn("graph.loss_smart_money.add_buy_review.v1", rule_ids)
        self.assertIn("graph.winner_momentum.add_buy_review.v1", rule_ids)
        self.assertIn("graph.instrument_profile.averaging_down_policy.v1", rule_ids)
        self.assertIn("graph.averaging_down.risk_guard.v1", rule_ids)
        self.assertIn("graph.holding.trend_transition.risk.v1", rule_ids)
        self.assertIn("graph.watchlist.trend_transition.support.v1", rule_ids)
        self.assertIn("graph.flow.sell_pressure.v1", rule_ids)
        self.assertIn("graph.flow.accumulation.entry.v1", rule_ids)
        self.assertIn("graph.news.direct_material_risk.v1", rule_ids)
        self.assertIn("graph.news.direct_material_support.v1", rule_ids)
        self.assertIn("graph.news.direct_material_context.v1", rule_ids)
        self.assertIn("graph.disclosure.event_risk.v1", rule_ids)
        self.assertIn("graph.data_quality.action_block.v1", rule_ids)
        self.assertIn("graph.data_quality.news_analysis_conflict.v1", rule_ids)
        self.assertIn("graph.execution.liquidity_or_slippage_block.v1", rule_ids)
        self.assertIn("graph.factor.position_crowding.v1", rule_ids)
        self.assertIn("graph.benchmark.beta.context.v1", rule_ids)
        self.assertIn("graph.price.reclaim.thesis_support.v1", rule_ids)
        self.assertIn("graph.coverage.gap.confidence_limit.v1", rule_ids)
        self.assertIn("graph.macro.regime.risk.v1", rule_ids)
        self.assertIn("graph.crypto.exposure.volatility_risk.v1", rule_ids)
        self.assertIn("graph.news.quality.confidence_limit.v1", rule_ids)
        self.assertIn("graph.valuation.high_beta_or_expensive.review.v1", rule_ids)
        self.assertIn("graph.portfolio.concentration.review.v1", rule_ids)
        self.assertEqual(["support"], support_transition["conditionRelationPolarities"])
        self.assertEqual(["risk"], risk_transition["conditionRelationPolarities"])
        self.assertEqual(["bidAskImbalance"], sell_pressure["conditionTargetFields"])
        self.assertEqual(-15.0, sell_pressure["conditionTargetMaxValue"])
        self.assertEqual(["volumeRatio"], sell_volume["conditionTargetFields"])
        self.assertEqual(1.2, sell_volume["conditionTargetMinValue"])
        self.assertEqual(["direct"], direct_news_risk["conditionTargetRelationScopes"])
        self.assertEqual(["risk"], direct_news_risk["conditionTargetPolarities"])
        self.assertTrue(direct_news_risk["conditionTargetMaterialityPassed"])
        self.assertEqual(65.0, direct_news_risk["conditionTargetMinMaterialityScore"])
        self.assertEqual(["direct"], direct_news_context["conditionTargetRelationScopes"])
        self.assertEqual(["context"], direct_news_context["conditionTargetPolarities"])
        self.assertEqual(60.0, direct_news_context["conditionTargetMinMaterialityScore"])
        self.assertTrue(fact_change_gate["conditionTargetMaterialityPassed"])
        self.assertEqual(["market-microstructure"], microstructure_gap["conditionTargetDataScopes"])
        self.assertEqual(["news-analysis-conflict"], news_analysis_conflict["conditionTargetDataScopes"])
        self.assertEqual(5.0, news_analysis_conflict["conditionRelationMinRiskImpact"])
        self.assertEqual("any", execution_slippage["conditionRole"])
        self.assertEqual(["slippageRiskScore"], execution_slippage["conditionTargetFields"])
        self.assertEqual(70.0, execution_slippage["conditionTargetMinValue"])
        self.assertEqual("not", price_reclaim_not["conditionRole"])
        self.assertEqual("any", portfolio_concentration["conditionRole"])
        self.assertEqual(["ConcentrationRisk"], portfolio_concentration["conditionTargetTboxClasses"])
        self.assertEqual("HAS_RISK_BUDGET", strategy_risk_budget["conditionRelationType"])
        self.assertEqual("risk-budget", strategy_risk_budget["conditionTargetKind"])
        self.assertEqual("HAS_PROFIT_POLICY", strategy_profit_policy["conditionRelationType"])
        self.assertEqual("profit-policy", strategy_profit_policy["conditionTargetKind"])
        self.assertEqual("HAS_POSITION_ROLE", watchlist_strategy_role["conditionRelationType"])
        self.assertEqual("position-role", watchlist_strategy_role["conditionTargetKind"])
        self.assertEqual("HAS_TRADE_FLOW", loss_smart_money["conditionRelationType"])
        self.assertEqual("smart-money-flow", loss_smart_money["conditionTargetKind"])
        self.assertEqual(["jointSmartMoneyInflow"], loss_smart_money["conditionTargetFields"])
        self.assertEqual(["smartMoney"], loss_smart_money["conditionRelationSignalGroups"])
        self.assertEqual(["support"], loss_smart_money["conditionRelationPolarities"])
        self.assertEqual("HAS_INVESTOR_FLOW_SENTIMENT", investor_flow_accumulation["conditionRelationType"])
        self.assertEqual("investor-flow-sentiment", investor_flow_accumulation["conditionTargetKind"])
        self.assertEqual(["smartMoneyAccumulation", "smartMoneyDipAbsorption", "broadInflowConfirmation"], investor_flow_accumulation["conditionTargetFields"])
        self.assertEqual(["investorPsychology"], investor_flow_accumulation["conditionRelationSignalGroups"])
        self.assertEqual(["support"], investor_flow_accumulation["conditionRelationPolarities"])
        self.assertEqual("HAS_INVESTOR_FLOW_SENTIMENT", retail_dip_buying["conditionRelationType"])
        self.assertEqual(["retailDipBuyingRisk"], retail_dip_buying["conditionTargetFields"])
        self.assertEqual(["risk"], retail_dip_buying["conditionRelationPolarities"])
        self.assertEqual("any", add_buy_volume["conditionRole"])
        self.assertEqual(["volumeRatio"], add_buy_volume["conditionTargetFields"])
        self.assertEqual(1.0, add_buy_volume["conditionTargetMinValue"])
        self.assertEqual("not", add_buy_gap_guard["conditionRole"])
        self.assertEqual("RECLAIMS_LEVEL", winner_add_ma5["conditionRelationType"])
        self.assertEqual(["ma5"], winner_add_ma5["conditionTargetLevelTypes"])
        self.assertEqual("HAS_INSTRUMENT_PROFILE", winner_add_profile["conditionRelationType"])
        self.assertEqual("instrument-profile", winner_add_profile["conditionTargetKind"])
        self.assertEqual(["allowAddOnStrength"], winner_add_profile["conditionTargetFields"])
        self.assertEqual("any", winner_add_volume["conditionRole"])
        self.assertEqual(["volumeRatio"], winner_add_volume["conditionTargetFields"])
        self.assertEqual(1.0, winner_add_volume["conditionTargetMinValue"])
        self.assertEqual("HAS_INSTRUMENT_PROFILE", profile_averaging_policy["conditionRelationType"])
        self.assertEqual(["avoidAveragingDown"], profile_averaging_policy["conditionTargetFields"])
        self.assertEqual("HAS_COVERAGE_GAP", coverage_gap["conditionRelationType"])
        self.assertEqual("coverage-gap", coverage_gap["conditionTargetKind"])
        self.assertEqual(4.0, coverage_gap["conditionRelationMinRiskImpact"])
        self.assertEqual("HAS_MACRO_REGIME", macro_regime["conditionRelationType"])
        self.assertEqual(["risk"], macro_regime["conditionRelationPolarities"])
        self.assertEqual("HAS_CRYPTO_EXPOSURE", crypto_exposure["conditionRelationType"])
        self.assertEqual("crypto-exposure", crypto_exposure["conditionTargetKind"])
        self.assertEqual("HAS_DATA_QUALITY", news_quality["conditionRelationType"])
        self.assertEqual(["news-quality"], news_quality["conditionTargetDataScopes"])

    def test_rulebox_admin_payload_roundtrips_to_graph(self):
        rules = default_graph_inference_rules()
        payload = {"rules": rulebox_rules_to_payload(rules)}
        parsed = rulebox_rules_from_payload(payload)
        graph = rulebox_graph_from_rules(parsed)

        self.assertEqual([rule.rule_id for rule in rules], [rule.rule_id for rule in parsed])
        self.assertTrue(any(item.entity_id == "ontology-box:RuleBox" for item in graph.entities))
        self.assertTrue(any(item.kind == "rule" and (item.properties or {}).get("ontologyBox") == "RuleBox" for item in graph.entities))
        self.assertTrue(any(item.relation_type == "DERIVES_RELATION" for item in graph.relations))

    def test_rulebox_hash_is_independent_of_rule_read_order(self):
        payload = rulebox_rules_to_payload(default_graph_inference_rules())

        self.assertEqual(rulebox_rules_hash(payload), rulebox_rules_hash(list(reversed(payload))))

    def test_rulebox_hash_matches_store_enriched_decision_policy(self):
        payload = rulebox_rules_to_payload(default_graph_inference_rules())
        enriched = []
        for rule in payload:
            copy = dict(rule)
            copy["conditions"] = [dict(item) for item in (rule.get("conditions") or [])]
            derivations = []
            for derivation in (rule.get("derivations") or []):
                item = dict(derivation)
                if not item.get("action_group"):
                    item["action_group"] = rule.get("action_group")
                if not item.get("action_level"):
                    item["action_level"] = rule.get("action_level")
                if not item.get("decision_stage"):
                    item["decision_stage"] = decision_stage_from_action(item.get("action_group"), item.get("action_level"))
                if not item.get("stage_priority"):
                    item["stage_priority"] = float(relation_stage_priority({
                        "decisionStage": item.get("decision_stage"),
                        "actionGroup": item.get("action_group"),
                        "actionLevel": item.get("action_level"),
                        "riskImpact": item.get("risk_impact"),
                        "supportImpact": item.get("support_impact"),
                    }))
                derivations.append(item)
            copy["derivations"] = derivations
            enriched.append(copy)

        self.assertEqual(rulebox_rules_hash(payload), rulebox_rules_hash(enriched))

    def test_rulebox_save_graph_can_skip_tbox_for_lightweight_sync(self):
        graph = rulebox_graph_from_rules(default_graph_inference_rules(), include_tbox=False)

        self.assertTrue(any(item.kind == "rule" and (item.properties or {}).get("ontologyBox") == "RuleBox" for item in graph.entities))
        self.assertFalse(any((item.properties or {}).get("ontologyBox") == "TBox" for item in graph.entities))
        self.assertTrue(all((item.properties or {}).get("ontologyBox") == "RuleBox" for item in graph.entities))
        self.assertTrue(all((item.properties or {}).get("ontologyBox") == "RuleBox" for item in graph.relations))

    def test_instrument_profile_is_projected_to_abox(self):
        graph = self.profitable_momentum_graph("MSTR")
        profiles = [item for item in graph.entities if item.kind == "instrument-profile"]
        profile = next(item for item in profiles if (item.properties or {}).get("symbol") == "MSTR")
        relation_types = [item.relation_type for item in graph.relations if item.source == "stock:MSTR" or item.target == profile.entity_id]

        self.assertIn("BitcoinProxy", profile.properties["archetypes"])
        self.assertTrue(profile.properties["allowAddOnStrength"])
        self.assertIn("HAS_INSTRUMENT_PROFILE", relation_types)
        self.assertTrue(any(item.relation_type == "HAS_ARCHETYPE" and item.source == "stock:MSTR" for item in graph.relations))

    def test_instrument_profile_policy_controls_winner_add_buy_rulebox(self):
        mstr_graph = self.profitable_momentum_graph("MSTR")
        tsla_graph = self.profitable_momentum_graph("TSLA")

        mstr_actions = [
            item
            for item in mstr_graph.relations
            if item.relation_type == "ALLOWS_ACTION"
            and "winner-momentum-add-buy" in str(item.target)
        ]
        tsla_actions = [
            item
            for item in tsla_graph.relations
            if item.relation_type == "ALLOWS_ACTION"
            and "winner-momentum-add-buy" in str(item.target)
        ]

        self.assertTrue(mstr_actions)
        self.assertEqual([], tsla_actions)

    def test_instrument_profile_text_parser(self):
        profiles = parse_instrument_profiles_text(
            "ABC|테스트 성장주|GrowthStock,PlatformGrowth|core|rate:medium,fx:high|allowAddOnStrength=0,trimOnTrendBreak=1"
        )

        profile = profiles["ABC"]
        self.assertEqual(["GrowthStock", "PlatformGrowth"], profile.archetypes)
        self.assertFalse(profile.allow_add_on_strength)
        self.assertTrue(profile.trim_on_trend_break)
        self.assertEqual("high", profile.sensitivities["fx"])

    def test_watchlist_rulebox_templates_carry_entry_only_action_policy(self):
        rules = default_graph_inference_rules()
        watchlist_rule = next(item for item in rules if item.rule_id == "graph.watchlist.trend_transition.support.v1")
        watchlist_derivations = watchlist_rule.derivations
        graph = rulebox_graph_from_rules([watchlist_rule])
        repository = TypeDBOntologyGraphRepository("http://typedb.example.test")
        entity_rows = repository.rows_for_entities(graph)
        template_rows = [
            item
            for item in entity_rows
            if item["kind"] == "relation-template" and item["ontologyBox"] == "RuleBox"
        ]

        self.assertTrue(watchlist_derivations)
        for derivation in watchlist_derivations:
            self.assertEqual("watchlist", derivation.target_role)
            self.assertEqual("ENTRY_ONLY", derivation.action_policy)
            self.assertEqual(["BUY", "HOLD", "AVOID"], derivation.allowed_actions)
            self.assertEqual(["ADD", "TRIM", "SELL"], derivation.blocked_actions)
        self.assertTrue(template_rows)
        self.assertEqual("watchlist", template_rows[0]["derivationTargetRole"])
        self.assertEqual("ENTRY_ONLY", template_rows[0]["derivationActionPolicy"])
        self.assertEqual(["BUY", "HOLD", "AVOID"], template_rows[0]["derivationAllowedActions"])
        self.assertEqual(["ADD", "TRIM", "SELL"], template_rows[0]["derivationBlockedActions"])

    def test_rulebox_snapshot_reconstructs_rules_from_typedb_rows(self):
        graph = self.loss_guard_graph()
        repository = TypeDBOntologyGraphRepository("http://typedb.example.test")
        entity_rows = repository.rows_for_entities(graph)
        default_rules = default_graph_inference_rules()
        rowsets = {
            "rules": [item for item in entity_rows if item["kind"] == "rule" and item["ontologyBox"] == "RuleBox"],
            "conditions": [item for item in entity_rows if item["kind"] == "rule-condition" and item["ontologyBox"] == "RuleBox"],
            "derivations": [item for item in entity_rows if item["kind"] == "relation-template" and item["ontologyBox"] == "RuleBox"],
            "relationTypes": [{"relationType": "HAS_INFERRED_RISK"}],
            "versions": [
                {
                    "id": "rulebox-version:test001",
                    "versionLabel": "test001",
                    "rulesHash": "test001",
                    "ruleCount": len(default_rules),
                    "conditionCount": sum(len(rule.conditions) for rule in default_rules),
                    "derivationCount": sum(len(rule.derivations) for rule in default_rules),
                    "status": "saved",
                    "changeReason": "baseline",
                    "author": "test",
                    "engineVersion": "typedb-rulebox-graph-reasoner-v1",
                    "createdAt": "2026-07-10T00:00:00Z",
                }
            ],
        }

        snapshot = rulebox_snapshot_from_rows(rowsets, source="test")
        loss_guard = next(item for item in snapshot["rules"] if item["rule_id"] == "graph.loss_guard.breakdown.v1")
        sell_pressure = next(item for item in snapshot["rules"] if item["rule_id"] == "graph.flow.sell_pressure.v1")
        ask_pressure = next(item for item in sell_pressure["conditions"] if item["condition_id"] == "ask-pressure")

        self.assertEqual("ok", snapshot["status"])
        self.assertEqual("test", snapshot["source"])
        self.assertFalse(snapshot["defaultsFallbackUsed"])
        self.assertTrue(loss_guard["conditions"])
        self.assertTrue(loss_guard["derivations"])
        self.assertEqual("LOSS_REDUCE", loss_guard["derivations"][0]["decision_stage"])
        self.assertGreaterEqual(loss_guard["derivations"][0]["stage_priority"], 40)
        self.assertEqual("bidAskImbalance", ask_pressure["target_property_filters"]["field"])
        self.assertEqual(-15, ask_pressure["target_property_filters"]["maxValue"])
        self.assertIn("HAS_INFERRED_RISK", snapshot["relationTypes"])
        self.assertEqual("test001", snapshot["versions"][0]["versionLabel"])
        self.assertTrue(snapshot["changeCandidates"])

    def test_rulebox_governance_versions_and_candidates_are_reviewable(self):
        rules = default_graph_inference_rules()
        version = rulebox_version_payload(rules, "2026-07-10T00:00:00Z", "baseline", "test")
        candidates = rulebox_governance_candidates(rulebox_rules_to_payload(rules), [version])
        factor_candidate = next(item for item in candidates if item["id"] == "candidate.factor-concentration-context.v1")

        self.assertEqual("baseline", version["changeReason"])
        self.assertTrue(version["id"].startswith("rulebox-version:"))
        self.assertEqual("covered", factor_candidate["status"])
        self.assertFalse(factor_candidate["proposedRule"]["enabled"])
        self.assertEqual("append-disabled-rule", factor_candidate["action"])

    def test_empty_typedb_rulebox_snapshot_does_not_fallback_to_runtime_defaults(self):
        snapshot = rulebox_snapshot_from_rows(
            {"rules": [], "conditions": [], "derivations": [], "relationTypes": []},
            source="typedb-http",
        )

        self.assertEqual("empty", snapshot["status"])
        self.assertEqual([], snapshot["rules"])
        self.assertEqual(0, snapshot["ruleCount"])
        self.assertFalse(snapshot["defaultsFallbackUsed"])
        self.assertTrue(snapshot["bootstrapAvailable"])
        self.assertGreater(snapshot["bootstrapRuleCount"], 0)
        self.assertEqual([], snapshot["versions"])
        self.assertTrue(snapshot["changeCandidates"])

    def test_inferencebox_snapshot_payload_marks_native_typedb_reasoning(self):
        rowsets = {
            "entityCounts": [{"entityCount": 2, "nativeEntityCount": 1}],
            "relationCounts": [{"relationCount": 3, "nativeRelationCount": 2}],
            "traceCounts": [{"traceCount": 1, "nativeTraceCount": 1}],
            "entities": [
                {
                    "id": "risk:005930:loss-guard-breakdown",
                    "label": "삼성전자 손실 방어 리스크",
                    "kind": "risk",
                    "symbol": "005930",
                    "ruleId": "graph.loss_guard.breakdown.v1",
                    "tboxClass": "MarketRisk",
                    "polarity": "risk",
                    "actionGroup": "lossControl",
                    "actionLevel": "review",
                    "nativeTypeDbReasoned": True,
                }
            ],
            "relations": [
                {
                    "type": "HAS_INFERRED_RISK",
                    "source": "stock:005930",
                    "sourceLabel": "삼성전자",
                    "target": "risk:005930:loss-guard-breakdown",
                    "targetLabel": "삼성전자 손실 방어 리스크",
                    "ruleId": "graph.loss_guard.breakdown.v1",
                    "polarity": "risk",
                    "riskImpact": 13,
                    "weight": 0.86,
                    "decisionStage": "LOSS_REDUCE",
                    "stagePriority": 43,
                    "aiInfluenceLabel": "손실 방어 추론",
                    "nativeTypeDbReasoned": True,
                }
            ],
            "traces": [
                {
                    "id": "inference-trace:005930:graph.loss_guard.breakdown.v1",
                    "label": "삼성전자 · 손실 보유 + 기준선 이탈 -> 손실 방어 추론",
                    "symbol": "005930",
                    "ruleId": "graph.loss_guard.breakdown.v1",
                    "confidence": 0.86,
                    "matchedConditionIds": ["holding-loss", "holding-source", "ma-break"],
                    "nativeTypeDbReasoned": True,
                }
            ],
        }

        payload = inferencebox_snapshot_from_rows(rowsets, source="test", symbols=["005930"])

        self.assertEqual("ok", payload["status"])
        self.assertTrue(payload["nativeTypeDbReasoningUsed"])
        self.assertEqual(2, payload["nativeRelationCount"])
        self.assertEqual("HAS_INFERRED_RISK", payload["relations"][0]["type"])
        self.assertEqual("LOSS_REDUCE", payload["relations"][0]["decisionStage"])
        self.assertEqual(43, payload["relations"][0]["stagePriority"])
        self.assertEqual(["holding-loss", "holding-source", "ma-break"], payload["traces"][0]["matchedConditionIds"])


if __name__ == "__main__":
    unittest.main()
