import unittest

from digital_twin.domain.decision_evidence_contract import (
    decision_readiness_contract,
    hypothesis_set_evidence_summary,
    material_action_transition_contract,
    temporal_evidence_summary,
)


def hypothesis(hypothesis_id, family_id, stance, evidence_state="supported"):
    return {
        "hypothesisId": hypothesis_id,
        "familyId": family_id,
        "stance": stance,
        "evidenceState": evidence_state,
        "approvalStatus": "approved-active",
        "verificationStatus": "verified-by-current-evidence",
        "supportingRuleIds": ["graph.rule." + hypothesis_id],
    }


class DecisionEvidenceContractTests(unittest.TestCase):
    def test_blocked_reference_hypothesis_does_not_satisfy_minimum_comparison(self):
        hypothesis_set = {
            "minimumComparisonCount": 3,
            "hypotheses": [
                hypothesis("support", "family:support", "support"),
                hypothesis("risk", "family:risk", "risk", "contested"),
                hypothesis("valuation", "family:valuation", "context", "blocked"),
            ],
        }
        context = {
            "ontologyRelationContext": {
                "investmentBrain": {"hypothesisSet": hypothesis_set},
                "actionEnvelope": {
                    "selectedRuleId": "graph.rule.support",
                    "dataReadiness": {
                        "state": "ready",
                        "usable": True,
                        "eligibleRuleIds": ["graph.rule.support"],
                    },
                },
            },
        }

        summary = hypothesis_set_evidence_summary(hypothesis_set)
        readiness = decision_readiness_contract(context)

        self.assertEqual(3, summary["totalHypothesisCount"])
        self.assertEqual(2, summary["eligibleFamilyCount"])
        self.assertEqual(1, summary["referenceHypothesisCount"])
        self.assertEqual("conditional", readiness["state"])
        self.assertIn("valuation", summary["referenceHypothesisIds"])

    def test_loaded_windows_are_not_counted_as_rule_matches(self):
        keys = ["15M", "1H", "SESSION", "1D", "3D", "5D", "20D"]
        windows = [
            {"windowKey": key, "hasSufficientHistory": True}
            for key in keys
        ]
        relation = {
            "actionEnvelope": {
                "selectedRuleId": "graph.watchlist.temporal.recovery_entry.v1",
                "dataReadiness": {
                    "eligibleRuleIds": ["graph.watchlist.temporal.recovery_entry.v1"],
                },
            },
            "graphStoreInference": {
                "traces": [{
                    "id": "trace:recovery",
                    "ruleId": "graph.watchlist.temporal.recovery_entry.v1",
                    "matchedConditions": [{
                        "conditionId": "condition:20d-rebound",
                        "relationType": "HAS_TEMPORAL_WINDOW",
                        "targetKind": "temporal-window",
                        "matchedTargetProperties": {"windowKey": "20D"},
                    }],
                }],
            },
        }

        summary = temporal_evidence_summary(windows, relation)

        self.assertEqual(7, summary["loadedWindowCount"])
        self.assertEqual(7, summary["sufficientWindowCount"])
        self.assertEqual(1, summary["matchedWindowCount"])
        self.assertEqual(["20D"], summary["matchedWindowKeys"])
        self.assertEqual(1, summary["temporalEvidenceFamilyCount"])

    def test_initial_generation_without_material_delta_cannot_change_action(self):
        context = {
            "previousInvestmentDecisionEpisode": {"action": "HOLD"},
            "decisionTransition": {"kind": "initial", "material": False},
            "ontologyRelationContext": {"whyNow": {"changedFacts": []}},
        }

        transition = material_action_transition_contract(context, "BUY")

        self.assertTrue(transition["evaluated"])
        self.assertTrue(transition["rebaseline"])
        self.assertFalse(transition["allowsActionChange"])


if __name__ == "__main__":
    unittest.main()
