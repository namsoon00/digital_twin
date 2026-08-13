import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.hypothesis_outcome_contract import (
    default_directional_criteria,
    merge_outcome_contracts,
    outcome_contract_fingerprint,
)
from digital_twin.domain.hypothesis_outcome_evaluation import evaluate_hypothesis_outcome
from digital_twin.domain.hypothesis_lifecycle import lifecycle_policy_from_rows


def criterion(criterion_id, role, metric, operator, threshold, **extra):
    return {
        "criterionId": criterion_id,
        "label": criterion_id,
        "role": role,
        "metric": metric,
        "operator": operator,
        "threshold": threshold,
        "unit": "%",
        "required": True,
        **extra,
    }


class HypothesisOutcomeEvaluationTests(unittest.TestCase):
    def test_cause_and_result_must_both_pass(self):
        contract = {
            "criteria": [
                criterion("share-count-increased", "cause", "shareCountChangePct", ">=", 1),
                criterion("material-decline", "result", "instrumentReturnPct", "<=", -0.5),
            ],
        }

        result = evaluate_hypothesis_outcome(
            contract,
            "risk",
            {"shareCountChangePct": 2.3},
            -1.2,
            1440,
        )

        self.assertEqual("contract-criteria", result["mode"])
        self.assertEqual("directionally-corroborated", result["selectedHypothesisStatus"])
        self.assertEqual(2, result["passedCriterionCount"])

    def test_missing_required_cause_is_not_treated_as_success(self):
        contract = {
            "criteria": [
                criterion("share-count-increased", "cause", "shareCountChangePct", ">=", 1),
                criterion("material-decline", "result", "instrumentReturnPct", "<=", -0.5),
            ],
        }

        result = evaluate_hypothesis_outcome(contract, "risk", {}, -2.0, 1440)

        self.assertEqual("inconclusive", result["selectedHypothesisStatus"])
        self.assertEqual(["share-count-increased"], result["missingRequiredMetricIds"])

    def test_invalidation_has_priority_over_support(self):
        contract = {
            "criteria": [
                criterion("material-rise", "result", "instrumentReturnPct", ">=", 0.5),
                criterion("counter-evidence", "invalidation", "counterEvidenceCount", ">=", 1),
            ],
        }

        result = evaluate_hypothesis_outcome(
            contract,
            "support",
            {"counterEvidenceIds": ["evidence:counter"]},
            2.0,
            60,
        )

        self.assertEqual("directionally-contradicted", result["selectedHypothesisStatus"])

    def test_default_material_move_avoids_counting_noise_as_support(self):
        contract = {"criteria": default_directional_criteria("support")}

        result = evaluate_hypothesis_outcome(contract, "support", {}, 0.1, 60)

        self.assertEqual("inconclusive", result["selectedHypothesisStatus"])
        self.assertEqual(0.5, result["criterionAssessments"][0]["threshold"])

    def test_excess_return_requires_benchmark_data(self):
        contract = {
            "criteria": [criterion("relative-decline", "result", "excessReturnPct", "<=", -1)],
        }

        missing = evaluate_hypothesis_outcome(contract, "risk", {}, -3.0, 1440)
        observed = evaluate_hypothesis_outcome(
            contract,
            "risk",
            {"benchmarkReturnPct": -0.5},
            -3.0,
            1440,
        )

        self.assertEqual("inconclusive", missing["selectedHypothesisStatus"])
        self.assertEqual("directionally-corroborated", observed["selectedHypothesisStatus"])
        self.assertEqual(-2.5, observed["criterionAssessments"][0]["observedValue"])

    def test_contract_fingerprint_ignores_episode_metadata(self):
        base = {
            "outcomeHorizonMinutes": [60],
            "criteria": default_directional_criteria("risk"),
        }
        first = outcome_contract_fingerprint({**base, "selectedHypothesisId": "one"})
        second = outcome_contract_fingerprint({**base, "selectedHypothesisId": "two"})

        self.assertEqual(first, second)

    def test_merge_retains_criteria_and_required_domains(self):
        merged = merge_outcome_contracts([{
            "criteria": [criterion(
                "flow-confirmation",
                "cause",
                "foreignNetVolume",
                ">",
                0,
                requiredObservationDomains=["flow"],
            )],
        }])

        self.assertEqual("flow-confirmation", merged["criteria"][0]["criterionId"])
        self.assertIn("flow", merged["requiredObservationDomains"])

    def test_lifecycle_policy_merge_does_not_drop_outcome_contract(self):
        policy = lifecycle_policy_from_rows([{
            "hypothesisLifecycle": {
                "formationConditionIds": ["condition:one"],
                "outcomeContract": {
                    "outcomeHorizonMinutes": [1440],
                    "criteria": [criterion(
                        "official-event",
                        "cause",
                        "verifiedEventCount",
                        ">=",
                        1,
                        requiredObservationDomains=["research"],
                    )],
                },
            },
        }])

        self.assertEqual([1440], policy["outcomeContract"]["outcomeHorizonMinutes"])
        self.assertEqual("official-event", policy["outcomeContract"]["criteria"][0]["criterionId"])
        self.assertIn("research", policy["outcomeContract"]["requiredObservationDomains"])


if __name__ == "__main__":
    unittest.main()
