import unittest

from digital_twin.domain.investment_reasoning_detail import (
    reasoning_detail_from_episode,
    reasoning_detail_snapshot,
)


class InvestmentReasoningDetailTests(unittest.TestCase):
    def test_exact_snapshot_freezes_matched_values_relations_rules_and_traces(self):
        relation_context = {
            "sourceAboxSnapshotId": "abox:005930:1",
            "inferenceGenerationId": "generation:005930:1",
            "inferenceGenerationAt": "2026-08-22T01:00:00Z",
            "graphStore": "typedb",
            "facts": {
                "currentPrice": 70000,
                "profitLossRate": -8.4,
                "observedAt": "2026-08-22T00:59:58Z",
                "source": "KIS Open API",
            },
            "activeRules": [{
                "ruleId": "rule:holding.loss-risk.v2",
                "label": "손실 보유 위험 점검",
                "description": "손실 구간과 추세 이탈이 함께 확인되면 위험 가설을 지지합니다.",
                "evidenceRole": "risk",
                "candidateAction": "TRIM",
            }],
            "graphStoreInference": {
                "relations": [{
                    "id": "relation:risk:1",
                    "source": "stock:005930",
                    "sourceLabel": "삼성전자",
                    "target": "risk:loss-control",
                    "targetLabel": "손실 관리 위험",
                    "type": "HAS_INFERRED_RISK",
                    "ruleId": "rule:holding.loss-risk.v2",
                    "polarity": "risk",
                }],
                "traces": [{
                    "id": "trace:risk:1",
                    "ruleId": "rule:holding.loss-risk.v2",
                    "matchedConditionIds": ["condition:loss"],
                    "evidenceRelationIds": ["relation:risk:1"],
                    "matchedConditions": [{
                        "conditionId": "condition:loss",
                        "label": "보유 수익률 손실 기준",
                        "kind": "subject_property",
                        "field": "profitLossRate",
                        "operator": "<=",
                        "value": -8,
                        "observedValue": -8.4,
                        "provider": "Toss holdings",
                        "observedAt": "2026-08-22T00:59:58Z",
                    }],
                }],
            },
        }
        hypothesis_set = {"hypotheses": [{
            "hypothesisId": "hypothesis:loss-risk",
            "templateLabel": "손실 확대 가설",
            "claim": "손실과 추세 이탈이 이어져 비중 축소가 필요할 수 있습니다.",
            "supportingRuleIds": ["rule:holding.loss-risk.v2"],
            "supportingEvidenceIds": ["relation:risk:1"],
            "causalPathIds": ["trace:risk:1"],
            "candidateAction": "TRIM",
        }]}

        result = reasoning_detail_snapshot(
            relation_context,
            hypothesis_set,
            {"selectedHypothesisId": "hypothesis:loss-risk"},
        )

        self.assertEqual("exact", result["snapshotState"])
        self.assertEqual(-8.4, result["facts"][0]["observedValue"])
        self.assertEqual("<= -8", result["facts"][0]["expected"])
        self.assertEqual("relation:risk:1", result["relations"][0]["id"])
        self.assertEqual("rule:holding.loss-risk.v2", result["rules"][0]["id"])
        self.assertEqual("condition:loss", result["rules"][0]["conditions"][0]["id"])
        self.assertEqual("trace:risk:1", result["traces"][0]["id"])
        self.assertTrue(result["hypotheses"][0]["selected"])
        self.assertEqual("exact", result["recordCompleteness"])
        self.assertEqual([], result["limitations"])

    def test_legacy_episode_is_marked_reconstructed_without_inventing_values(self):
        episode = {
            "symbol": "AAPL",
            "subjectName": "Apple",
            "sourceAboxSnapshotId": "abox:legacy",
            "inferenceGenerationId": "generation:legacy",
            "factsAtDecision": {"currentPrice": 200.5, "source": "legacy quote"},
        }
        scenarios = [{
            "id": "hypothesis:legacy",
            "title": "가격 회복 가설",
            "claim": "가격 회복을 관찰합니다.",
            "ruleIds": ["rule:recovery"],
            "supportingRuleIds": ["rule:recovery"],
            "counterRuleIds": [],
            "relationIds": ["trace:legacy"],
            "marketConditionIds": ["condition:recovery"],
        }]

        result = reasoning_detail_from_episode(episode, scenarios, [])

        self.assertEqual("reconstructed", result["snapshotState"])
        self.assertEqual(200.5, result["facts"][0]["observedValue"])
        self.assertIsNone(result["traces"][0]["conditions"][0]["observedValue"])
        self.assertIn("미저장 속성은 확정하지 않습니다", result["snapshotReason"])
        self.assertEqual({}, result["rules"][0]["knowledgeBasis"])
        self.assertEqual("unavailable-in-legacy-episode", result["rules"][0]["knowledgeBasisScope"])
        self.assertEqual("partial", result["recordCompleteness"])
        self.assertGreaterEqual(len(result["limitations"]), 1)


if __name__ == "__main__":
    unittest.main()
