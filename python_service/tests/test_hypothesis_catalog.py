import unittest

from digital_twin.domain.hypothesis_catalog import (
    hypothesis_catalog_payload,
    hypothesis_family_definition,
    validate_hypothesis_catalog,
)
from digital_twin.domain.investment_brain import InvestmentQuestion, build_competing_hypotheses


class HypothesisCatalogTests(unittest.TestCase):
    def test_catalog_has_reciprocal_competitors_and_outcome_contracts(self):
        self.assertEqual((), validate_hypothesis_catalog())
        payload = hypothesis_catalog_payload()
        self.assertGreaterEqual(payload["familyCount"], 12)
        recovery = hypothesis_family_definition("mean-reversion")
        self.assertEqual(("failed-recovery",), recovery.competing_family_ids)
        self.assertTrue(recovery.outcome_metric)
        self.assertTrue(recovery.falsification_contract)

    def test_typedb_hypothesis_inherits_predictive_family_contract(self):
        question = InvestmentQuestion.create(
            "NAVER의 회복이 이어질까?",
            subject_symbol="035420",
            account_id="account:1",
        )
        knowledge = {
            "ruleKind": "predictive-hypothesis",
            "theoryFamily": "behavioral-mean-reversion",
            "thesisFamily": "mean-reversion",
            "decisionEligibility": "conditional",
            "requiresHypothesis": True,
            "evidenceIndependenceKey": "mean-reversion",
        }
        relation = {
            "id": "relation:recovery",
            "ruleId": "graph.price.reclaim.thesis_support.v1",
            "type": "SUPPORTS_RECOVERY",
            "polarity": "support",
            "knowledgeBasis": knowledge,
        }
        trace = {
            "id": "trace:recovery",
            "ruleId": "graph.price.reclaim.thesis_support.v1",
            "label": "가격 회복",
            "knowledgeBasis": knowledge,
            "matchedConditionIds": ["price-reclaim"],
        }
        match = {
            "ruleId": "graph.price.reclaim.thesis_support.v1",
            "label": "가격 회복",
            "knowledgeBasis": knowledge,
        }

        hypothesis_set, _research = build_competing_hypotheses(
            subject={"symbol": "035420", "name": "NAVER"},
            facts={"symbol": "035420", "accountId": "account:1"},
            relations=[relation],
            traces=[trace],
            matches=[match],
            signal_conflicts={},
            missing_data=[],
            inference_generation_id="generation:naver:1",
            question=question,
            policy={"minimumComparisonCount": 1},
            scope_context={"accountId": "account:1"},
        )

        hypothesis = hypothesis_set.hypotheses[0]
        self.assertEqual(1, hypothesis_set.minimum_comparison_count)
        self.assertEqual("primary-counter-or-explicit-gap", hypothesis_set.comparison_policy)
        self.assertEqual("price-recovery", hypothesis.prediction_target)
        self.assertEqual(["failed-recovery"], hypothesis.competing_family_ids)
        self.assertEqual("recovery-return", hypothesis.outcome_metric)
        self.assertEqual("generation:naver:1", hypothesis.inference_generation_id)


if __name__ == "__main__":
    unittest.main()
