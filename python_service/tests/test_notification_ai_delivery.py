import unittest

from digital_twin.application.notification_service import NotificationQueueRunner
from digital_twin.domain.notification_ai_delivery import final_ai_delivery_decision
from digital_twin.domain.notifications import NotificationJob


class SuppressionQueue:
    def __init__(self):
        self.reason = ""

    def mark_suppressed(self, job, reason):
        self.reason = reason


def watchlist_context(ai_kind="unchanged", material_sources=None):
    return {
        "notificationAiValidatedResponse": {"action": "HOLD"},
        "aiDecisionTransition": {
            "historyAvailable": True,
            "kind": ai_kind,
            "previousAction": "BUY" if ai_kind == "action-changed" else "HOLD",
            "currentAction": "HOLD",
        },
        "decisionTransition": {
            "kind": "action-changed",
            "material": True,
            "previousAction": "BUY",
            "currentAction": "HOLD",
        },
        "ontologyRelationContext": {
            "targetRole": "watchlist",
            "actionEnvelope": {
                "targetRole": "watchlist",
                "selectedRuleId": "graph.recovery.v1",
                "dataReadiness": {"eligibleRuleIds": ["graph.recovery.v1"]},
            },
        },
        "ontologyInsight": {
            "semanticComponents": {
                "materialSourceEventKeys": list(material_sources or []),
            },
        },
    }


class FinalAIDeliveryTests(unittest.TestCase):
    def test_candidate_only_watchlist_change_is_suppressed(self):
        decision = final_ai_delivery_decision(watchlist_context())

        self.assertEqual("suppress", decision["decision"])
        self.assertIn("최종 AI 행동", decision["reason"])

    def test_final_ai_action_change_is_sent(self):
        decision = final_ai_delivery_decision(watchlist_context(ai_kind="action-changed"))

        self.assertEqual("send", decision["decision"])

    def test_decision_changing_source_is_sent_even_when_action_is_unchanged(self):
        decision = final_ai_delivery_decision(
            watchlist_context(material_sources=["main:news:035720:article-1"]),
        )

        self.assertEqual("send", decision["decision"])
        self.assertEqual(1, decision["materialSourceEventCount"])

    def test_candidate_only_holding_change_is_also_suppressed(self):
        context = watchlist_context()
        context["ontologyRelationContext"]["targetRole"] = "holding"
        context["ontologyRelationContext"]["actionEnvelope"]["targetRole"] = "holding"

        decision = final_ai_delivery_decision(context)

        self.assertEqual("suppress", decision["decision"])
        self.assertEqual("graph_candidate_only_change", decision["suppressionReason"])

    def test_nearly_expired_snapshot_is_refreshed_before_ai_queue(self):
        queue = SuppressionQueue()
        requested = []
        runner = NotificationQueueRunner(
            queue,
            account_repository=None,
            notifier_factory=lambda account: None,
            settings={
                "notificationAiGateEnabled": "1",
                "notificationAiFreshnessReserveMinutes": "4",
            },
            ai_request_enqueuer=object(),
            fresh_data_recheck_requester=lambda account, symbol, job_id: requested.append(symbol) or {"requested": True},
        )
        job = NotificationJob.create(
            "test",
            account_id="main",
            message_type="investmentInsight",
            context={
                "messageType": "investmentInsight",
                "rawSymbol": "005930",
                "dataFreshnessAgeMinutes": 7,
                "dataFreshnessMaxAgeMinutes": 10,
            },
        )

        allowed = runner.apply_ai_freshness_headroom_gate(job)

        self.assertFalse(allowed)
        self.assertEqual(["005930"], requested)
        self.assertEqual("ai_freshness_headroom_recheck", job.context["deliverySuppressionReason"])


if __name__ == "__main__":
    unittest.main()
