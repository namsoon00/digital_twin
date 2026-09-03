import unittest

from digital_twin.domain.investment_model import investment_model_projection
from digital_twin.domain.investment_product_readiness import (
    investment_product_readiness,
)


class InvestmentProductReadinessTests(unittest.TestCase):
    def test_compact_operational_status_projects_active_release_and_runtime_readiness(self):
        result = investment_model_projection(
            {
                "status": "ready",
                "reasons": [],
                "control": {
                    "activeDeploymentId": "ontology-v2-production-r88",
                    "deliveryDeploymentId": "ontology-v2-production-r88",
                    "candidateDeploymentId": "",
                },
                "activeDeployment": {
                    "deploymentId": "ontology-v2-production-r88",
                    "status": "active",
                    "releaseId": "ontology-v2-release-r88",
                    "releaseFingerprint": "release-fingerprint",
                    "graphStoreBinding": "orbit-alpha-r88",
                    "timeSeriesBackendId": "questdb-shadow",
                    "capabilities": {
                        "productionDelivery": True,
                        "directSourceEvents": True,
                        "independentExecution": True,
                    },
                    "ruleExecutionReadiness": {
                        "status": "ready",
                        "mode": "typedb-direct-typeql",
                    },
                },
                "queue": {"endToEndP95Ms": 745526},
            },
            {
                "ruleCount": 118,
                "ruleInventory": {
                    "ruleCount": 118,
                    "invalidRuleCount": 0,
                    "releaseReady": True,
                },
            },
            {},
            {},
            {},
            {
                "control": {"activeBackendId": "questdb-shadow"},
                "runtimeResolution": {
                    "requestedBackendId": "questdb-shadow",
                    "effectiveBackendId": "questdb-shadow",
                    "failedOver": False,
                },
                "health": {"questdb-shadow": {"status": "ready"}},
                "deployments": [{
                    "backendId": "questdb-shadow",
                    "adapterName": "questdb",
                    "status": "active",
                    "health": {"status": "ready"},
                }],
            },
        )

        self.assertEqual("ontology-v2-production-r88", result["activeRelease"]["deploymentId"])
        self.assertEqual("ontology-v2-release-r88", result["activeRelease"]["releaseId"])
        self.assertEqual("release-fingerprint", result["activeRelease"]["releaseFingerprint"])
        self.assertEqual("orbit-alpha-r88", result["bindings"]["graphStore"])
        self.assertTrue(result["bindings"]["sourceEventsDirect"])
        self.assertTrue(result["validation"]["promotionReady"])
        self.assertTrue(result["validation"]["ruleInventoryReady"])
        rule_contract = next(
            gate for gate in result["productReadiness"]["gates"]
            if gate["id"] == "rule-contract"
        )
        self.assertTrue(rule_contract["passed"])
        self.assertEqual(745526, result["productReadiness"]["metrics"]["p95TotalDurationMs"])
        self.assert_runtime_failover_blocks_a_release_that_declares_a_different_backend()
        self.assert_tbox_fingerprint_drift_blocks_model_release_readiness()

    def assert_runtime_failover_blocks_a_release_that_declares_a_different_backend(self):
        result = investment_model_projection(
            {
                "status": "ready",
                "reasons": [],
                "control": {
                    "activeDeploymentId": "ontology-v2-production-r88",
                    "deliveryDeploymentId": "ontology-v2-production-r88",
                },
                "activeDeployment": {
                    "deploymentId": "ontology-v2-production-r88",
                    "status": "active",
                    "graphStoreBinding": "orbit-alpha-r88",
                    "timeSeriesBackendId": "questdb-shadow",
                    "capabilities": {"productionDelivery": True},
                    "ruleExecutionReadiness": {"status": "ready"},
                },
            },
            {"ruleInventory": {"releaseReady": True}},
            {},
            {},
            {},
            {
                "control": {"activeBackendId": "questdb-shadow"},
                "runtimeResolution": {
                    "requestedBackendId": "questdb-shadow",
                    "effectiveBackendId": "mysql-primary",
                    "failedOver": True,
                    "reason": "selected-backend-unavailable",
                },
                "health": {"mysql-primary": {"status": "ready"}},
                "deployments": [{
                    "backendId": "mysql-primary",
                    "adapterName": "mysql",
                    "status": "active",
                    "health": {"status": "ready"},
                }],
            },
        )

        self.assertEqual("review", result["status"])
        self.assertFalse(result["validation"]["promotionReady"])
        self.assertIn("time-series-backend-mismatch", result["validation"]["blockers"])
        self.assertEqual("mysql-primary", result["bindings"]["timeSeriesEffective"])
        self.assertTrue(result["bindings"]["timeSeriesFailedOver"])

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

    def assert_tbox_fingerprint_drift_blocks_model_release_readiness(self):
        result = investment_model_projection(
            {
                "status": "ready",
                "reasons": [],
                "control": {
                    "activeDeploymentId": "ontology-v2-production-r88",
                    "deliveryDeploymentId": "ontology-v2-production-r88",
                },
                "activeDeployment": {
                    "deploymentId": "ontology-v2-production-r88",
                    "status": "active",
                    "capabilities": {"productionDelivery": True},
                    "ruleExecutionReadiness": {"status": "ready"},
                },
            },
            {"ruleInventory": {"releaseReady": True}},
            {
                "deployedTBox": {
                    "alignment": "drift",
                    "sourceFingerprint": "source-new",
                    "deployedFingerprint": "deployed-old",
                },
            },
            {},
            {},
        )

        self.assertEqual("review", result["status"])
        self.assertFalse(result["validation"]["promotionReady"])
        self.assertIn("tbox-deployment-drift", result["validation"]["blockers"])
        self.assertEqual("drift", result["validation"]["tboxAlignment"])


if __name__ == "__main__":
    unittest.main()
