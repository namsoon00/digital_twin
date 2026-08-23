import unittest

from digital_twin.domain.investment_product_readiness import investment_product_readiness


class InvestmentProductReadinessTests(unittest.TestCase):
    def ready_payload(self):
        return investment_product_readiness(
            operational_promotion_ready=True,
            rule_inventory={"releaseReady": True},
            catalog={
                "decisionPerformance": {
                    "status": "ok",
                    "calibrationEligibleEpisodeCount": 80,
                    "outcomeCoveragePct": 92.0,
                    "governance": {"quarantineRecommendedRuleIds": []},
                },
                "statisticalSignals": {"migrationCounts": {"shadow-signal-required": 0}},
            },
            experiments={"activeCount": 4},
            active_health={"p95TotalDurationMs": 42000},
            comparison={"sampleCount": 30},
            settings={
                "investmentProductSoakTestPassed": True,
                "investmentProductComplianceReviewed": True,
            },
        )

    def test_all_gates_produce_general_availability_candidate(self):
        result = self.ready_payload()

        self.assertEqual("general-availability-candidate", result["stage"])
        self.assertTrue(result["generalAvailabilityReady"])
        self.assertTrue(result["releaseRecommended"])
        self.assertFalse(result["automaticPromotion"])

    def test_runtime_success_does_not_hide_quality_and_latency_blockers(self):
        result = investment_product_readiness(
            operational_promotion_ready=True,
            rule_inventory={"releaseReady": True},
            catalog={
                "decisionPerformance": {
                    "status": "ok",
                    "calibrationEligibleEpisodeCount": 12,
                    "outcomeCoveragePct": 60.0,
                    "governance": {"quarantineRecommendedRuleIds": ["rule:poor"]},
                },
                "statisticalSignals": {"migrationCounts": {"shadow-signal-required": 5}},
            },
            experiments={"activeCount": 1},
            active_health={"p95TotalDurationMs": 65000},
            comparison={"sampleCount": 0},
            settings={},
        )

        self.assertEqual("internal-validation", result["stage"])
        self.assertFalse(result["closedBetaReady"])
        self.assertIn("outcome-calibration", result["blockers"])
        self.assertIn("rule-performance", result["blockers"])
        self.assertIn("latency-slo", result["blockers"])
        self.assertEqual(1, result["metrics"]["quarantineRecommendedRuleCount"])

    def test_latency_reads_the_active_queue_percentile(self):
        result = investment_product_readiness(
            operational_promotion_ready=True,
            rule_inventory={"releaseReady": True},
            catalog={
                "decisionPerformance": {
                    "status": "ok",
                    "calibrationEligibleEpisodeCount": 80,
                    "outcomeCoveragePct": 90.0,
                    "governance": {"quarantineRecommendedRuleIds": []},
                },
                "statisticalSignals": {"migrationCounts": {"shadow-signal-required": 0}},
            },
            experiments={"activeCount": 1},
            active_health={"queue": {"durationP95Ms": 64544}},
            comparison={"sampleCount": 30},
            settings={
                "investmentProductSoakTestPassed": True,
                "investmentProductComplianceReviewed": True,
            },
        )

        self.assertEqual(64544, result["metrics"]["p95TotalDurationMs"])
        self.assertIn("latency-slo", result["blockers"])

    def test_latency_prefers_end_to_end_queue_time(self):
        result = investment_product_readiness(
            operational_promotion_ready=True,
            rule_inventory={"releaseReady": True},
            catalog={
                "decisionPerformance": {
                    "status": "ok",
                    "calibrationEligibleEpisodeCount": 80,
                    "outcomeCoveragePct": 90.0,
                    "governance": {"quarantineRecommendedRuleIds": []},
                },
                "statisticalSignals": {"migrationCounts": {"shadow-signal-required": 0}},
            },
            experiments={"activeCount": 1},
            active_health={"queue": {"durationP95Ms": 1000, "endToEndP95Ms": 65000}},
            comparison={"sampleCount": 30},
            settings={
                "investmentProductSoakTestPassed": True,
                "investmentProductComplianceReviewed": True,
            },
        )

        self.assertEqual(65000, result["metrics"]["p95TotalDurationMs"])
        self.assertIn("latency-slo", result["blockers"])


if __name__ == "__main__":
    unittest.main()
