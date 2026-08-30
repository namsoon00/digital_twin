import unittest

from digital_twin.domain.investment_product_readiness import (
    investment_product_readiness,
)


class InvestmentProductReadinessTests(unittest.TestCase):
    def test_queue_end_to_end_latency_is_used_for_launch_gate(self):
        result = investment_product_readiness(
            operational_promotion_ready=True,
            rule_inventory={"releaseReady": True},
            catalog={
                "decisionPerformance": {
                    "status": "ok",
                    "calibrationEligibleEpisodeCount": 50,
                    "outcomeCoveragePct": 90,
                    "governance": {},
                },
                "statisticalSignals": {"migrationCounts": {}},
            },
            experiments={},
            active_health={"queue": {"endToEndP95Ms": 45000}},
            comparison={"sampleCount": 20},
            settings={
                "investmentProductSoakTestPassed": "1",
                "investmentProductComplianceReviewed": "1",
            },
        )

        latency = next(gate for gate in result["gates"] if gate["id"] == "latency-slo")
        self.assertTrue(latency["passed"])
        self.assertEqual(45000, result["metrics"]["p95TotalDurationMs"])


if __name__ == "__main__":
    unittest.main()
