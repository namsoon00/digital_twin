import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.ontology_prompting import prompt_payload
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
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
from digital_twin.domain.ontology_rulebox_governance import rulebox_governance_candidates, rulebox_version_payload


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

        repository = CapturingTypeDBRepository(self.loss_guard_graph())

        result = repository.run_rulebox({})

        self.assertEqual("ok", result["status"])
        self.assertEqual("typedb", result["graphStore"])
        self.assertEqual("typedb-rulebox-materialized", result["reasoningMode"])
        self.assertFalse(result["typedbBootstrapReasoningUsed"])
        self.assertTrue(result["nativeTypeDbReasoningUsed"])
        self.assertTrue(result["pythonBootstrapDisabled"])
        self.assertGreater(result["statementCount"], 0)
        self.assertIn("HAS_INFERRED_RISK", result["relationTypes"])
        self.assertEqual({}, result["clearResult"])
        self.assertIsNotNone(repository.saved_inferencebox_graph)
        self.assertTrue(repository.saved_inferencebox_graph.entities)
        self.assertTrue(all((item.properties or {}).get("nativeTypeDbReasoned") for item in repository.saved_inferencebox_graph.entities))

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

        self.assertIn("graph.materiality.alert_candidate.v1", rule_ids)
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

    def test_rulebox_admin_payload_roundtrips_to_graph(self):
        rules = default_graph_inference_rules()
        payload = {"rules": rulebox_rules_to_payload(rules)}
        parsed = rulebox_rules_from_payload(payload)
        graph = rulebox_graph_from_rules(parsed)

        self.assertEqual([rule.rule_id for rule in rules], [rule.rule_id for rule in parsed])
        self.assertTrue(any(item.entity_id == "ontology-box:RuleBox" for item in graph.entities))
        self.assertTrue(any(item.kind == "rule" and (item.properties or {}).get("ontologyBox") == "RuleBox" for item in graph.entities))
        self.assertTrue(any(item.relation_type == "DERIVES_RELATION" for item in graph.relations))

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
