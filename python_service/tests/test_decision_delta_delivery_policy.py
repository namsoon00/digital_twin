import unittest

from digital_twin.domain.investment_reasoning.decision_delta import DecisionDelta
from digital_twin.modules.notifications.domain.notification.delivery_policy import evaluate_final_decision_delivery


class DecisionDeltaDeliveryPolicyTests(unittest.TestCase):
    def test_internal_relation_lifecycle_change_is_web_only(self):
        delta = DecisionDelta(
            final_action="HOLD",
            previous_final_action="HOLD",
            ai_transition_kind="unchanged",
            history_available=True,
            graph_transition_present=True,
            graph_transition_kind="relation-strengthened",
            graph_transition_material=True,
            validated_response_present=True,
        )

        decision = evaluate_final_decision_delivery(delta)

        self.assertFalse(decision.should_send)
        self.assertEqual(
            "internal_relation_lifecycle_churn",
            decision.suppression_reason,
        )

    def test_final_action_change_is_deliverable(self):
        delta = DecisionDelta(
            final_action="TRIM",
            previous_final_action="HOLD",
            ai_transition_kind="action-changed",
            history_available=True,
            graph_transition_present=True,
            graph_transition_kind="action-changed",
            graph_transition_material=True,
            validated_response_present=True,
        )

        decision = evaluate_final_decision_delivery(delta)

        self.assertTrue(decision.should_send)
        self.assertEqual("final-action-change", decision.push_value_class)

    def test_material_source_can_reopen_unchanged_action(self):
        delta = DecisionDelta(
            final_action="HOLD",
            previous_final_action="HOLD",
            ai_transition_kind="unchanged",
            history_available=True,
            validated_response_present=True,
            material_source_event_count=1,
        )

        decision = evaluate_final_decision_delivery(delta)

        self.assertTrue(decision.should_send)
        self.assertEqual("material-source-evidence", decision.push_value_class)

    def test_incomplete_canonical_publication_is_never_pushed(self):
        delta = DecisionDelta(
            final_action="BUY",
            previous_final_action="HOLD",
            ai_transition_kind="action-changed",
            history_available=True,
            validated_response_present=True,
            canonical_subject=True,
            publication_outcome="FINAL_DECISION",
            execution_status="completed",
            ai_adoption_state="decision-and-narrative-adopted",
            ai_authored=True,
            customer_action_contract_gaps=("next-condition",),
        )

        decision = evaluate_final_decision_delivery(delta)

        self.assertFalse(decision.should_send)
        self.assertEqual(
            "incomplete_customer_action_contract",
            decision.suppression_reason,
        )


if __name__ == "__main__":
    unittest.main()
