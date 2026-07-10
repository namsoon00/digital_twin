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

        decisions = decisions_for_positions(
            [position],
            portfolio,
            relation_contexts_by_symbol=contexts,
        )
        self.assertEqual(1, len(decisions))
        self.assertEqual("neo4jInferenceBox", decisions[0].relation_rule_context["decision"]["basis"])
        self.assertTrue(decisions[0].relation_rule_context["graphStoreUsed"])


if __name__ == "__main__":
    unittest.main()
