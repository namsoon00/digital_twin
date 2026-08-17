import unittest

from digital_twin.application.notification.workflow import NotificationAIValidatedGateEnricher
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notifications import NotificationJob


class Reviewer:
    def review(self, _context):
        return NotificationAIValidatedResponse(
            action="HOLD",
            validation_state="conditional",
            data_state="partial",
            summary="추가 검증 중입니다.",
            hypotheses=[{
                "hypothesisId": "hypothesis:support",
                "verdict": "supported",
                "supportingEvidenceIds": ["evidence:1"],
            }],
            selected_hypothesis_id="hypothesis:support",
            hypothesis_comparison_state="completed",
            hypothesis_selection_source="ai-comparison",
        )


class DecisionStore:
    saved = None

    def latest_decision_memory(self, account_id, symbol, exclude_episode_id=""):
        return {
            "episodeId": "decision-episode:previous",
            "accountId": account_id,
            "symbol": symbol,
            "action": "BUY",
            "validationState": "blocked",
        }

    def record_observation(self, *_args):
        return None

    def save(self, episode):
        self.saved = episode


class NotificationInvestmentFlowContextTests(unittest.TestCase):
    def test_investment_alert_records_decision_and_validation_transition(self):
        context = {
            "messageType": "investmentInsight",
            "accountId": "main",
            "rawSymbol": "AAPL",
            "referenceDate": "2026-08-18T01:00:00Z",
            "ontologyRelationContext": {
                "subject": {"symbol": "AAPL", "name": "Apple"},
                "facts": {"symbol": "AAPL"},
                "sourceAboxSnapshotId": "abox:1",
                "inferenceGenerationId": "generation:1",
                "investmentBrain": {
                    "question": {
                        "questionId": "question:1",
                        "text": "보유 판단을 유지해야 하나?",
                        "subjectSymbol": "AAPL",
                        "subjectName": "Apple",
                        "accountId": "main",
                    },
                    "hypothesisSet": {
                        "hypothesisSetId": "set:1",
                        "subjectSymbol": "AAPL",
                        "questionId": "question:1",
                        "hypotheses": [{
                            "hypothesisId": "hypothesis:support",
                            "claim": "수요가 유지됩니다.",
                            "stance": "support",
                            "supportingEvidenceIds": ["evidence:1"],
                            "supportingRuleIds": ["rule:1"],
                        }],
                    },
                },
            },
        }
        job = NotificationJob.create(
            "flow transition test",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )
        store = DecisionStore()

        NotificationAIValidatedGateEnricher(
            Reviewer(),
            {
                "notificationAiGateEnabled": "1",
                "notificationAiGateMessageTypes": "investmentInsight",
            },
            store,
        )(job)

        flow = job.context["investmentFlow"]
        self.assertTrue(flow["flowId"].startswith("flow:"))
        self.assertEqual(store.saved.episode_id, flow["episodeId"])
        self.assertEqual(("BUY", "HOLD"), (flow["previousAction"], flow["currentAction"]))
        self.assertTrue(flow["decisionChanged"])
        self.assertEqual(("blocked", "conditional"), (
            flow["previousValidationState"],
            flow["currentValidationState"],
        ))
        self.assertTrue(flow["validationChanged"])


if __name__ == "__main__":
    unittest.main()
