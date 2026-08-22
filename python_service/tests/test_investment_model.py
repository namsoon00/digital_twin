import unittest

from digital_twin.domain.investment_model import investment_model_projection


class InvestmentModelProjectionTests(unittest.TestCase):
    def test_projection_exposes_one_active_release_without_private_settings(self):
        result = investment_model_projection(
            {
                "control": {
                    "active_deployment_id": "ontology-v2-production-r15",
                    "candidate_deployment_id": "ontology-v2-production-r16",
                },
                "deployments": [{
                    "deploymentId": "ontology-v2-production-r15",
                    "engineFamily": "ontology-investment-brain",
                    "engineVersion": "v2",
                    "status": "active",
                    "graphStoreBinding": "graph-v2",
                    "timeSeriesBackendId": "questdb",
                    "releaseBundle": {
                        "release_id": "ontology-v2-release-r15",
                        "runtime_revision": "runtime-r15",
                    },
                    "health": {
                        "releaseFingerprint": "active1234567890",
                        "ruleboxFingerprint": "active-rulebox-15",
                        "validationCohortId": "cohort:r15",
                        "ruleInventoryReleaseReady": True,
                    },
                    "updatedAt": "2026-08-22T01:00:00Z",
                }],
                "promotionReadiness": {
                    "ready": True,
                    "blockers": [],
                    "health": {
                        "candidateReleaseId": "ontology-v2-release-r15",
                        "releaseFingerprint": "abcdef1234567890",
                        "ruleboxFingerprint": "rulebox1234567890",
                        "ruleInventoryReleaseReady": True,
                    },
                },
            },
            {"ruleCount": 118, "conditionCount": 240, "derivationCount": 70},
            {"counts": {"classes": 562, "relations": 426, "hypotheses": 657}},
            {"total": 66, "activeCount": 3},
            {
                "modelName": "관계 기반 모델",
                "modelHypothesis": "반대 근거를 함께 비교한다.",
                "tossToken": "must-not-leak",
            },
        )

        self.assertEqual("ready", result["status"])
        self.assertEqual("ontology-v2-production-r15", result["activeRelease"]["deploymentId"])
        self.assertEqual("ontology-v2-release-r15", result["activeRelease"]["releaseId"])
        self.assertEqual("active123456", result["activeRelease"]["releaseShortHash"])
        self.assertEqual("runtime-r15", result["activeRelease"]["runtimeRevision"])
        self.assertEqual(118, result["inventory"]["rules"])
        self.assertTrue(result["validation"]["promotionReady"])
        self.assertFalse(result["governance"]["automaticPromotion"])
        self.assertNotIn("tossToken", str(result))

    def test_missing_active_release_is_unavailable(self):
        result = investment_model_projection({}, {}, {}, {}, {})

        self.assertEqual("unavailable", result["status"])
        self.assertFalse(result["validation"]["promotionReady"])


if __name__ == "__main__":
    unittest.main()
