import unittest

from digital_twin.modules.model_registry.domain.investment_model import investment_model_projection
from digital_twin.modules.read_models.domain.investment_product_readiness import (
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
            {"sampleCount": 12, "helpfulPct": 75},
            {
                "proposals": [{
                    "proposalId": "learning-proposal:1",
                    "status": "review-required",
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
        self.assertEqual("review-required", result["evolution"]["state"])
        self.assertEqual(1, result["evolution"]["proposals"]["reviewRequiredCount"])
        self.assertTrue(result["evolution"]["proposals"]["automaticGeneration"])
        self.assertFalse(result["evolution"]["promotion"]["automatic"])
        self.assert_runtime_failover_blocks_a_release_that_declares_a_different_backend()
        self.assert_tbox_fingerprint_drift_blocks_model_release_readiness()
        self.assert_candidate_release_management_drives_validation_projection()

    def assert_candidate_release_management_drives_validation_projection(self):
        result = investment_model_projection(
            {
                "status": "ready",
                "control": {
                    "activeDeploymentId": "ontology-v2-production-r110",
                    "deliveryDeploymentId": "ontology-v2-production-r110",
                    "candidateDeploymentId": "ontology-v2-production-r113",
                },
                "activeDeployment": {
                    "deploymentId": "ontology-v2-production-r110",
                    "status": "active",
                },
                "candidateDeployment": {
                    "deploymentId": "ontology-v2-production-r113",
                    "status": "shadow",
                },
                "releaseManagement": {
                    "control": {
                        "active_deployment_id": "ontology-v2-production-r110",
                        "delivery_deployment_id": "ontology-v2-production-r110",
                        "candidate_deployment_id": "ontology-v2-production-r113",
                    },
                    "deployments": [
                        {
                            "deploymentId": "ontology-v2-production-r110",
                            "status": "active",
                        },
                        {
                            "deploymentId": "ontology-v2-production-r113",
                            "status": "shadow",
                            "releaseBundle": {
                                "release_id": "ontology-v2-release-r113",
                                "runtime_revision": "revision-r113",
                            },
                            "health": {
                                "candidateReleaseFingerprint": "fingerprint-r113",
                                "validationCohortId": "cohort-r113",
                                "ruleInventoryReleaseReady": True,
                            },
                        },
                    ],
                    "comparisonSummary": {
                        "sampleCount": 1,
                        "statusCounts": {"reasoning-parity-gap": 1},
                    },
                    "promotionReadiness": {
                        "ready": False,
                        "blockers": ["reasoning-rule-slot-parity-gap"],
                        "comparison": {
                            "sampleCount": 1,
                            "statusCounts": {"reasoning-parity-gap": 1},
                        },
                    },
                },
            },
            {"ruleInventory": {"releaseReady": True}},
            {},
            {},
            {},
        )

        self.assertEqual("ontology-v2-release-r113", result["candidate"]["releaseId"])
        self.assertEqual("fingerprint-r113", result["candidate"]["releaseFingerprint"])
        self.assertEqual("revision-r113", result["candidate"]["runtimeRevision"])
        self.assertEqual("cohort-r113", result["validation"]["cohortId"])
        self.assertEqual(1, result["evolution"]["validation"]["comparisonSampleCount"])
        self.assertEqual(
            1,
            result["productReadiness"]["metrics"]["comparisonSampleCount"],
        )
        self.assertIn("reasoning-rule-slot-parity-gap", result["validation"]["blockers"])

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
            active_health={"queue": {"endToEndP95Ms": 45000, "uniqueCompletedRunCount": 20}},
            comparison={"sampleCount": 20},
            settings={
                "investmentProductSoakTestPassed": "1",
                "investmentProductComplianceReviewed": "1",
            },
            message_quality={"sampleCount": 12, "helpfulPct": 75},
        )

        latency = next(gate for gate in result["gates"] if gate["id"] == "latency-slo")
        self.assertTrue(latency["passed"])
        self.assertEqual(45000, result["metrics"]["p95TotalDurationMs"])
        usefulness = next(gate for gate in result["gates"] if gate["id"] == "message-usefulness")
        self.assertTrue(usefulness["passed"])
        self.assertEqual(12, result["metrics"]["messageFeedbackSampleCount"])

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
