import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.ontology_inference_context import relation_contexts_from_snapshot
from digital_twin.domain.portfolio import AccountSnapshot, Position
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.strategy import decisions_for_positions


class OntologyInferenceContextTests(unittest.TestCase):
    def test_neo4j_inferencebox_context_replaces_python_relation_rule_path(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            quantity=10,
            sellable_quantity=10,
            average_price=80000,
            current_price=70000,
            market_value=700000,
            profit_loss_rate=-12.5,
            ma20=76000,
            ma60=72000,
            ma20_distance=-7.9,
            ma60_distance=-2.8,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], fx_rates={"KRW": 1})
        snapshot = AccountSnapshot(
            "acct",
            "계좌",
            "test",
            "live",
            "ok",
            "2026-07-10T00:00:00Z",
            portfolio,
            positions=[position],
            metadata={
                "ontology": {
                    "neo4j": {
                        "inferenceBox": {
                            "status": "ok",
                            "neo4jNativeReasoningUsed": True,
                            "entityCount": 2,
                            "relationCount": 1,
                            "traceCount": 1,
                            "nativeRelationCount": 1,
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
                                    "supportImpact": 0,
                                    "weight": 0.86,
                                    "aiInfluenceLabel": "손실 방어 추론",
                                    "inferenceTraceId": "inference-trace:005930:graph.loss_guard.breakdown.v1",
                                    "nativeNeo4jReasoned": True,
                                }
                            ],
                            "traces": [
                                {
                                    "id": "inference-trace:005930:graph.loss_guard.breakdown.v1",
                                    "label": "삼성전자 · 손실 보유 + 기준선 이탈 -> 손실 방어 추론",
                                    "symbol": "005930",
                                    "ruleId": "graph.loss_guard.breakdown.v1",
                                    "confidence": 0.86,
                                    "matchedConditionIds": ["holding-source", "holding-loss", "ma-break"],
                                    "nativeNeo4jReasoned": True,
                                }
                            ],
                        }
                    }
                }
            },
        )

        contexts = relation_contexts_from_snapshot(snapshot)
        self.assertIn("005930", contexts)
        self.assertEqual("neo4jInferenceBox", contexts["005930"]["source"])
        self.assertEqual("neo4jInferenceBox", contexts["005930"]["decision"]["basis"])
        self.assertEqual("graph.loss_guard.breakdown.v1", contexts["005930"]["decision"]["selectedRuleId"])
        self.assertEqual("stock:005930", contexts["005930"]["evidenceSubgraph"]["target"]["id"])
        self.assertEqual(["graph.loss_guard.breakdown.v1"], contexts["005930"]["evidenceSubgraph"]["matchedRuleIds"])
        self.assertTrue(any(item["type"] == "HAS_INFERRED_RISK" for item in contexts["005930"]["evidenceSubgraph"]["edges"]))
        self.assertIn("evidenceSubgraph", contexts["005930"]["promptContext"])

        decisions = decisions_for_positions(
            [position],
            portfolio,
            relation_contexts_by_symbol=contexts,
        )
        self.assertEqual(1, len(decisions))
        self.assertEqual("neo4jInferenceBox", decisions[0].relation_rule_context["decision"]["basis"])
        self.assertTrue(decisions[0].relation_rule_context["graphStoreUsed"])

    def test_strict_decision_path_blocks_python_relation_rule_fallback(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            quantity=10,
            current_price=70000,
            market_value=700000,
            profit_loss_rate=-12.5,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], fx_rates={"KRW": 1})

        decisions = decisions_for_positions(
            [position],
            portfolio,
            require_inference_context=True,
        )

        self.assertEqual(1, len(decisions))
        self.assertEqual("ontologyInferenceRequired", decisions[0].decision_basis)
        self.assertEqual(0, decisions[0].exit_pressure)
        self.assertTrue(decisions[0].relation_rule_context["blocked"])
        self.assertFalse(decisions[0].relation_rule_context["fallbackUsed"])

    def test_typedb_inferencebox_context_is_valid_graph_decision_source(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            quantity=10,
            sellable_quantity=10,
            average_price=80000,
            current_price=70000,
            market_value=700000,
            profit_loss_rate=-12.5,
            ma20=76000,
            ma60=72000,
            ma20_distance=-7.9,
            ma60_distance=-2.8,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], fx_rates={"KRW": 1})
        snapshot = AccountSnapshot(
            "acct",
            "계좌",
            "test",
            "live",
            "ok",
            "2026-07-10T00:00:00Z",
            portfolio,
            positions=[position],
            metadata={
                "ontology": {
                    "activeGraphStore": "typedb",
                    "typedb": {
                        "graphStore": "typedb",
                        "inferenceBox": {
                            "status": "ok",
                            "source": "typedbInferenceBox",
                            "graphStore": "typedb",
                            "typedbBootstrapReasoningUsed": True,
                            "entityCount": 2,
                            "relationCount": 1,
                            "traceCount": 1,
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
                                    "aiInfluenceLabel": "손실 방어 추론",
                                    "decisionStage": "LOSS_REDUCE",
                                    "stagePriority": 90,
                                }
                            ],
                            "traces": [
                                {
                                    "id": "inference-trace:005930:graph.loss_guard.breakdown.v1",
                                    "label": "삼성전자 · 손실 보유 + 기준선 이탈 -> 손실 방어 추론",
                                    "symbol": "005930",
                                    "ruleId": "graph.loss_guard.breakdown.v1",
                                    "confidence": 0.86,
                                    "matchedConditionIds": ["holding-source", "holding-loss", "ma-break"],
                                }
                            ],
                        },
                    },
                }
            },
        )

        contexts = relation_contexts_from_snapshot(snapshot)
        self.assertIn("005930", contexts)
        self.assertEqual("typedbInferenceBox", contexts["005930"]["source"])
        self.assertEqual("typedbInferenceBox", contexts["005930"]["decision"]["basis"])
        self.assertEqual("typedbInferenceRelation", contexts["005930"]["decision"]["stagePolicySource"])

        decisions = decisions_for_positions(
            [position],
            portfolio,
            relation_contexts_by_symbol=contexts,
        )

        self.assertEqual(1, len(decisions))
        self.assertEqual("typedbInferenceBox", decisions[0].decision_basis)
        self.assertTrue(decisions[0].relation_rule_context["graphStoreUsed"])

    def test_neo4j_entry_wait_inference_maps_to_entry_wait_stage(self):
        watch = Position(
            symbol="NVDA",
            name="NVIDIA",
            market="US",
            currency="USD",
            current_price=201.84,
            ma5=201.7,
            ma20=201.5,
            ma60=208.2,
            volume_ratio=0,
            source="watchlist",
            sector="반도체",
        )
        snapshot = AccountSnapshot(
            "acct",
            "계좌",
            "test",
            "live",
            "ok",
            "2026-07-10T00:00:00Z",
            portfolio_summary([], fx_rates={"KRW": 1, "USD": 1400}),
            watchlist=[watch],
            metadata={
                "ontology": {
                    "neo4j": {
                        "inferenceBox": {
                            "status": "ok",
                            "neo4jNativeReasoningUsed": True,
                            "relations": [
                                {
                                    "type": "HAS_INFERRED_ENTRY_WAIT",
                                    "source": "stock:NVDA",
                                    "target": "entry:NVDA:wait",
                                    "targetLabel": "NVIDIA 신규 진입 대기",
                                    "ruleId": "entry.wait_for_confirmation.v1",
                                    "polarity": "risk",
                                    "riskImpact": 8,
                                    "weight": 0.72,
                                    "decisionStage": "ENTRY_WAIT",
                                    "stagePriority": 31,
                                    "actionGroup": "entryWait",
                                    "actionLevel": "review",
                                    "nativeNeo4jReasoned": True,
                                }
                            ],
                            "traces": [
                                {
                                    "id": "inference-trace:NVDA:entry.wait_for_confirmation.v1",
                                    "label": "NVIDIA · 관심종목 + 확인 부족/거시 부담 -> 신규 진입 대기",
                                    "symbol": "NVDA",
                                    "ruleId": "entry.wait_for_confirmation.v1",
                                    "confidence": 0.72,
                                }
                            ],
                        }
                    }
                }
            },
        )

        contexts = relation_contexts_from_snapshot(snapshot)

        self.assertEqual("신규 진입 대기", contexts["NVDA"]["decision"]["label"])
        self.assertEqual("ENTRY_WAIT", contexts["NVDA"]["decision"]["decisionStage"])
        self.assertEqual("entryWait", contexts["NVDA"]["decision"]["actionGroup"])

    def test_neo4j_entry_momentum_inference_maps_to_entry_ready_stage(self):
        watch = Position(
            symbol="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            current_price=210,
            ma5=207,
            ma20=205,
            ma60=202,
            volume_ratio=1.3,
            source="watchlist",
            sector="AI/플랫폼",
        )
        snapshot = AccountSnapshot(
            "acct",
            "계좌",
            "test",
            "live",
            "ok",
            "2026-07-10T00:00:00Z",
            portfolio_summary([], fx_rates={"KRW": 1, "USD": 1400}),
            watchlist=[watch],
            metadata={
                "ontology": {
                    "neo4j": {
                        "inferenceBox": {
                            "status": "ok",
                            "neo4jNativeReasoningUsed": True,
                            "relations": [
                                {
                                    "type": "HAS_INFERRED_ENTRY_OPPORTUNITY",
                                    "source": "stock:AAPL",
                                    "target": "entry:AAPL:momentum",
                                    "targetLabel": "Apple 신규 진입 후보",
                                    "ruleId": "entry.momentum.confirmed.v1",
                                    "polarity": "support",
                                    "supportImpact": 12,
                                    "weight": 0.82,
                                    "decisionStage": "ENTRY_READY",
                                    "stagePriority": 37,
                                    "actionGroup": "entry",
                                    "actionLevel": "action",
                                    "nativeNeo4jReasoned": True,
                                }
                            ],
                            "traces": [
                                {
                                    "id": "inference-trace:AAPL:entry.momentum.confirmed.v1",
                                    "label": "Apple · 5/20/60일선 회복 + 거래 증가 + 거시 확인 -> 신규 진입 후보",
                                    "symbol": "AAPL",
                                    "ruleId": "entry.momentum.confirmed.v1",
                                    "confidence": 0.82,
                                }
                            ],
                        }
                    }
                }
            },
        )

        contexts = relation_contexts_from_snapshot(snapshot)

        self.assertEqual("소액 분할매수 검토", contexts["AAPL"]["decision"]["label"])
        self.assertEqual("ENTRY_READY", contexts["AAPL"]["decision"]["decisionStage"])
        self.assertEqual("entry", contexts["AAPL"]["decision"]["actionGroup"])
        self.assertEqual("neo4jInferenceRelation", contexts["AAPL"]["decision"]["stagePolicySource"])


if __name__ == "__main__":
    unittest.main()
