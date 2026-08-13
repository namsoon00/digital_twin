import unittest

from digital_twin.domain.notification_ai_delivery import final_ai_delivery_decision


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
            "actionEnvelope": {"targetRole": "watchlist"},
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


if __name__ == "__main__":
    unittest.main()
