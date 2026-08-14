import unittest

from digital_twin.domain.investment_notification_state import (
    context_with_investment_notification_state,
)
from digital_twin.domain.notification_ai_delivery import final_ai_delivery_decision


def context_for_state(
    *,
    action="HOLD",
    review_level="check",
    data_state="partial",
    validation_state="conditional",
    previous=None,
):
    return {
        "notificationAiValidatedResponse": {
            "action": action,
            "reviewLevel": review_level,
            "dataState": data_state,
            "validationState": validation_state,
        },
        "previousInvestmentDecisionEpisode": previous or {},
        "aiDecisionTransition": {
            "historyAvailable": bool(previous),
            "kind": "unchanged" if previous and previous.get("action") == action else "action-changed",
            "previousAction": (previous or {}).get("action", ""),
            "currentAction": action,
        },
        "decisionTransition": {
            "kind": "initial",
            "material": False,
            "currentAction": "hold",
        },
        "ontologyRelationContext": {
            "targetRole": "watchlist",
            "actionEnvelope": {"targetRole": "watchlist"},
        },
        "ontologyInsight": {"semanticComponents": {"materialSourceEventKeys": []}},
        "investmentStateTransitionNotificationsEnabled": True,
    }


class InvestmentNotificationStateTests(unittest.TestCase):
    def test_same_action_with_readiness_change_is_a_user_state_transition(self):
        previous = {
            "episodeId": "decision-episode:lg-previous",
            "action": "HOLD",
            "reviewLevel": "check",
            "dataState": "partial",
            "validationState": "conditional",
            "decidedAt": "2026-08-14T00:02:00Z",
        }
        context = context_with_investment_notification_state(context_for_state(
            review_level="act",
            data_state="sufficient",
            validation_state="ready",
            previous=previous,
        ))

        transition = context["investmentNotificationTransition"]
        self.assertTrue(transition["changed"])
        self.assertEqual("readiness-changed", transition["kind"])
        self.assertEqual(["reviewLevel", "dataState", "validationState"], transition["changedFields"])
        self.assertIn("관심 유지 · 추가 확인 → 관심 유지 · 판단 가능", transition["summary"])
        self.assertEqual("send", final_ai_delivery_decision(context)["decision"])

    def test_unchanged_initial_graph_state_is_not_realerted(self):
        previous = {
            "episodeId": "decision-episode:lg-previous",
            "action": "HOLD",
            "reviewLevel": "check",
            "dataState": "partial",
            "validationState": "conditional",
        }
        context = context_with_investment_notification_state(context_for_state(previous=previous))

        transition = context["investmentNotificationTransition"]
        self.assertFalse(transition["changed"])
        decision = final_ai_delivery_decision(context)
        self.assertEqual("suppress", decision["decision"])
        self.assertEqual("final_ai_state_unchanged", decision["suppressionReason"])

    def test_first_non_executable_state_becomes_a_baseline_after_ai(self):
        context = context_with_investment_notification_state(context_for_state())

        decision = final_ai_delivery_decision(context)
        self.assertEqual("suppress", decision["decision"])
        self.assertEqual("initial_graph_baseline", decision["suppressionReason"])

    def test_action_change_remains_material(self):
        previous = {
            "episodeId": "decision-episode:lg-previous",
            "action": "HOLD",
            "reviewLevel": "check",
            "dataState": "partial",
            "validationState": "conditional",
        }
        context = context_with_investment_notification_state(context_for_state(
            action="BUY",
            review_level="act",
            data_state="sufficient",
            validation_state="ready",
            previous=previous,
        ))

        self.assertEqual("action-changed", context["investmentNotificationTransition"]["kind"])
        self.assertEqual("send", final_ai_delivery_decision(context)["decision"])

    def test_state_transition_setting_can_store_without_sending(self):
        previous = {
            "episodeId": "decision-episode:lg-previous",
            "action": "HOLD",
            "reviewLevel": "check",
            "dataState": "partial",
            "validationState": "conditional",
        }
        context = context_with_investment_notification_state(context_for_state(
            data_state="sufficient",
            validation_state="ready",
            previous=previous,
        ))
        context["investmentStateTransitionNotificationsEnabled"] = False

        decision = final_ai_delivery_decision(context)
        self.assertEqual("suppress", decision["decision"])
        self.assertEqual("inference_state_notification_disabled", decision["suppressionReason"])


if __name__ == "__main__":
    unittest.main()
