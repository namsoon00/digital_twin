import unittest

from digital_twin.domain.ontology_performance_contract import (
    ontology_performance_assessment,
)


class OntologyPerformanceContractTests(unittest.TestCase):
    def test_identifies_native_inference_as_measured_bottleneck(self):
        result = ontology_performance_assessment({
            "graphAssemblyMs": 4_000,
            "projectionMs": 8_000,
            "nativeInferenceMs": 91_000,
            "totalMs": 155_000,
        })

        self.assertEqual("critical", result["status"])
        self.assertEqual("nativeInferenceMs", result["bottleneckStage"])
        self.assertFalse(result["withinBudget"])

    def test_accepts_a_turn_within_every_observed_budget(self):
        result = ontology_performance_assessment({
            "graphAssemblyMs": 1_000,
            "projectionMs": 2_000,
            "nativeInferenceMs": 3_000,
            "totalMs": 8_000,
        })

        self.assertEqual("within-budget", result["status"])
        self.assertTrue(result["withinBudget"])


if __name__ == "__main__":
    unittest.main()
