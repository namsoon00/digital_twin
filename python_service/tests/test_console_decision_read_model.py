import unittest

from digital_twin.application.console_read_model_service import ConsoleReadModelService


class ConsoleDecisionReadModelTest(unittest.TestCase):
    def test_subject_case_keeps_bounded_detail_identity_and_payload(self):
        subject_case = {
            "version": "investment-case-v5",
            "detailType": "subject-decision-case",
            "subjectCaseId": "subject-decision-case:abc123",
            "batchCaseId": "reasoning-case:batch123",
            "accountId": "default",
            "symbol": "TSLA",
            "name": "Tesla",
            "phase": "case",
            "readinessState": "warning",
            "headline": "TypeDB candidate",
            "subjectDecisionCase": {
                "stage": "SYNTHESIZED",
                "sourceAboxSnapshotId": "abox-manifest:1",
                "inferenceGenerationId": "inference-generation:1",
                "hypotheses": [{"hypothesisId": "hypothesis-instance:1"}],
            },
        }

        result = ConsoleReadModelService().decision_heads({
            "version": "investment-case-v5",
            "items": [subject_case],
        })

        self.assertEqual(result["count"], 1)
        item = result["items"][0]
        self.assertEqual(item["detailType"], "subject-decision-case")
        self.assertEqual(item["subjectCaseId"], "subject-decision-case:abc123")
        self.assertEqual(item["batchCaseId"], "reasoning-case:batch123")
        self.assertEqual(item["subjectDecisionCase"]["stage"], "SYNTHESIZED")
        self.assertEqual(
            item["subjectDecisionCase"]["hypotheses"][0]["hypothesisId"],
            "hypothesis-instance:1",
        )

    def test_episode_head_does_not_invent_subject_case_identity(self):
        result = ConsoleReadModelService().decision_heads({
            "version": "investment-case-v5",
            "items": [{
                "version": "investment-case-v5",
                "caseId": "case:default.TSLA",
                "episodeId": "decision:episode1",
                "accountId": "default",
                "symbol": "TSLA",
                "name": "Tesla",
            }],
        })

        item = result["items"][0]
        self.assertFalse(item.get("subjectCaseId"))
        self.assertEqual(item["subjectDecisionCase"], {})


if __name__ == "__main__":
    unittest.main()
