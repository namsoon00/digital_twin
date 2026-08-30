import unittest

from digital_twin.domain.operational_health import (
    OperationalHealthSignal,
    assess_operational_health,
    reasoning_engine_health_signals,
)


class OperationalHealthDomainTests(unittest.TestCase):
    def test_historical_debt_does_not_degrade_current_service(self):
        signals = reasoning_engine_health_signals({
            "status": "degraded",
            "reasons": ["reasoning-failures-present"],
            "control": {
                "activeDeploymentId": "ontology-v2-production-r88",
                "deliveryDeploymentId": "ontology-v2-production-r88",
            },
            "activeDeployment": {"deploymentId": "ontology-v2-production-r88"},
            "queue": {
                "pendingCount": 0,
                "unresolvedFailureCount": 1,
                "recentFailureCount24h": 0,
            },
        })

        result = assess_operational_health(signals)

        self.assertEqual("healthy", result["serviceState"])
        self.assertEqual("warning", result["attentionState"])
        self.assertEqual(1, result["historicalDebtCount"])
        self.assertEqual("healthy", result["signals"][0]["state"])
        self.assertTrue(result["signals"][1]["historical"])

    def test_recent_reasoning_failure_remains_a_current_warning(self):
        result = assess_operational_health(reasoning_engine_health_signals({
            "status": "degraded",
            "reasons": ["reasoning-failures-present"],
            "control": {
                "activeDeploymentId": "ontology-v2-production-r88",
                "deliveryDeploymentId": "ontology-v2-production-r88",
            },
            "activeDeployment": {"deploymentId": "ontology-v2-production-r88"},
            "queue": {
                "unresolvedFailureCount": 1,
                "recentFailureCount24h": 1,
            },
        }))

        self.assertEqual("warning", result["serviceState"])
        self.assertFalse(result["signals"][1]["historical"])

    def test_stale_candidate_is_actionable_historical_debt(self):
        result = assess_operational_health(reasoning_engine_health_signals({
            "status": "ready",
            "control": {
                "activeDeploymentId": "ontology-v2-production-r88",
                "deliveryDeploymentId": "ontology-v2-production-r88",
                "candidateDeploymentId": "ontology-v2-production-r75",
            },
            "activeDeployment": {"deploymentId": "ontology-v2-production-r88"},
            "candidateDeployment": {"deploymentId": "ontology-v2-production-r75"},
            "queue": {},
        }))

        candidate = next(item for item in result["signals"] if item["id"] == "reasoning-candidate")
        self.assertEqual("stale-reasoning-candidate", candidate["reasonCode"])
        self.assertEqual("retire-stale-candidate", candidate["action"]["id"])
        self.assertEqual("healthy", result["serviceState"])

    def test_capacity_attention_does_not_claim_an_active_outage(self):
        result = assess_operational_health([
            OperationalHealthSignal(
                signal_id="storage",
                label="저장공간",
                dimension="capacity",
                state="warning",
                detail="정리 필요",
                impact="operational",
                action={"id": "open-storage-maintenance", "label": "정리 확인"},
            )
        ])

        self.assertEqual("healthy", result["serviceState"])
        self.assertEqual("warning", result["attentionState"])
        self.assertEqual(1, result["dimensions"]["capacity"]["attentionCount"])


if __name__ == "__main__":
    unittest.main()
