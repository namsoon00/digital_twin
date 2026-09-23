import unittest

from digital_twin.modules.reasoning.domain.incremental_inference_equivalence import (
    compare_incremental_rule_states,
    incremental_selection_safety_state,
)


class IncrementalInferenceEquivalenceTests(unittest.TestCase):
    def execution(self, matches):
        return {
            "status": "ok",
            "nativeRuleSelectionApplied": False,
            "nativeInferenceEvaluationComplete": True,
            "nativeMatchResult": {"matches": matches},
        }

    def test_full_pass_verifies_reused_deferred_rule_states_per_symbol(self):
        result = compare_incremental_rule_states(
            {
                "005930": {"rule.flow": "matched", "rule.news": "not-matched"},
                "000660": {"rule.flow": "not-matched", "rule.news": "matched"},
            },
            self.execution([
                {"ruleId": "rule.flow", "sourceId": "stock:005930"},
                {"ruleId": "rule.news", "sourceId": "stock:000660"},
            ]),
            ["005930", "000660"],
            ["rule.flow", "rule.news"],
        )

        self.assertEqual("verified-equivalent", result["status"])
        self.assertTrue(result["verified"])
        self.assertEqual(4, result["comparedRuleCount"])

    def test_full_pass_reconciles_a_stale_slot(self):
        result = compare_incremental_rule_states(
            {"005930": {"rule.flow": "matched"}},
            self.execution([]),
            ["005930"],
            ["rule.flow"],
        )

        self.assertEqual("mismatch-reconciled", result["status"])
        self.assertFalse(result["verified"])
        self.assertTrue(result["reconciledByFullEvaluation"])
        self.assertEqual("matched", result["mismatches"][0]["priorState"])
        self.assertEqual("not-matched", result["mismatches"][0]["fullEvaluationState"])

        mismatch = {
            "runId": "run:mismatch",
            "result": {
                "status": "ok",
                "incrementalEquivalenceAudit": {
                    "status": "mismatch-reconciled",
                    "mismatchCount": 1,
                },
            },
        }
        full = {
            "result": {
                "status": "ok",
                "nativeReplayValidation": {
                    "verified": True,
                    "selectionApplied": False,
                    "nativeEvaluationComplete": True,
                    "coverageComplete": True,
                },
            },
        }
        suspended = incremental_selection_safety_state(
            [{**full, "runId": "run:full-1"}, mismatch],
            required_full_recovery_runs=2,
        )
        recovered = incremental_selection_safety_state(
            [
                {**full, "runId": "run:full-2"},
                {**full, "runId": "run:full-1"},
                mismatch,
            ],
            required_full_recovery_runs=2,
        )
        self.assertEqual("suspended", suspended["status"])
        self.assertFalse(suspended["selectionAllowed"])
        self.assertEqual("healthy", recovered["status"])
        self.assertTrue(recovered["selectionAllowed"])

    def test_selected_execution_cannot_claim_equivalence(self):
        execution = self.execution([])
        execution["nativeRuleSelectionApplied"] = True

        result = compare_incremental_rule_states(
            {"005930": {"rule.flow": "not-matched"}},
            execution,
            ["005930"],
            ["rule.flow"],
        )

        self.assertEqual("inconclusive", result["status"])
        self.assertFalse(result["verified"])


if __name__ == "__main__":
    unittest.main()
