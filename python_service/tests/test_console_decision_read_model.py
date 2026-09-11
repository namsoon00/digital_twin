import unittest
from datetime import datetime, timedelta, timezone

from digital_twin.modules.read_models.application.console_read_model_service import ConsoleReadModelService


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
            "statusDimensions": [{
                "id": "ai",
                "label": "AI 해석",
                "state": "pass",
                "stateLabel": "해석 완료",
                "reason": "현재 TypeDB 세대와 일치하는 AI 해석입니다.",
            }],
            "subjectDecisionCase": {
                "stage": "SYNTHESIZED",
                "sourceAboxSnapshotId": "abox-manifest:1",
                "inferenceGenerationId": "inference-generation:1",
                "hypotheses": [{"hypothesisId": "hypothesis-instance:1"}],
                "aiInsight": {
                    "status": "completed",
                    "model": "gpt-5.6-sol",
                    "reasoningEffort": "max",
                },
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
        self.assertEqual(item["subjectDecisionCase"]["aiInsight"]["status"], "completed")
        self.assertEqual(item["statusDimensions"][0]["stateLabel"], "해석 완료")

        now = datetime.now(timezone.utc)
        cases = {
            "status": "ok",
            "items": [
                {
                    "caseId": "case:fresh",
                    "symbol": "AAPL",
                    "name": "Apple",
                    "updatedAt": (now - timedelta(hours=2)).isoformat(),
                    "decision": {"action": "BUY"},
                    "attention": {"userActionable": True},
                },
                {
                    "caseId": "case:old",
                    "symbol": "SKHY",
                    "name": "SK하이닉스(ADR)",
                    "updatedAt": (now - timedelta(days=8)).isoformat(),
                    "decision": {"action": "HOLD"},
                    "attention": {"userReviewable": True},
                },
            ],
        }
        dashboard = ConsoleReadModelService().dashboard_summary({}, {}, cases, {})
        self.assertEqual(["case:fresh"], [row["id"] for row in dashboard["tasks"]])
        self.assertEqual(1, dashboard["taskSummary"]["actionable"])
        self.assertEqual(1, dashboard["taskSummary"]["historical"])
        self.assertEqual(96, dashboard["taskSummary"]["freshnessWindowHours"])

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
