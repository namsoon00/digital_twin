import unittest

from digital_twin.domain.notification_reasoning_report import customer_alert_reason_lines
from digital_twin.domain.notification_rule_evaluator import evaluate_notification_rule
from digital_twin.domain.notification_rule_models import (
    NotificationRuleCondition,
    NotificationRuleConfig,
)
from digital_twin.domain.notifications import NotificationJob


class NotificationTriggerLedgerTests(unittest.TestCase):
    def test_matched_condition_and_final_gate_are_recorded_separately(self):
        job = NotificationJob.create(
            "시장 상태 확인",
            message_type="notification",
            context={"severity": "WATCH", "symbol": "MSTR"},
        )
        rule = NotificationRuleConfig(
            message_type="notification",
            conditions=[
                NotificationRuleCondition(
                    "severity-watch",
                    "관찰 단계",
                    "context_equals",
                    field="severity",
                    value="WATCH",
                ),
            ],
            similarity_enabled=False,
        )

        context = evaluate_notification_rule(job, rule).to_context()

        self.assertEqual(
            "notification-delivery-trigger-ledger-v1",
            context["deliveryTriggerLedgerVersion"],
        )
        condition = next(
            item for item in context["deliveryTriggerLedger"]
            if item["triggerId"] == "condition:severity-watch"
        )
        self.assertEqual("WATCH", condition["currentValue"])
        self.assertEqual("WATCH", condition["threshold"])
        self.assertTrue(any(
            item["triggerId"] == "delivery-gate"
            for item in context["deliveryTriggerLedger"]
        ))

    def test_customer_reason_uses_structured_trigger_before_internal_relation_text(self):
        rows = customer_alert_reason_lines({
            "deliveryTriggerLedger": [{
                "triggerId": "typedb-relation-change",
                "kind": "typedb-relation-diff",
                "label": "관계 판단 변화",
                "reason": "보유 유지에서 분할축소 검토로 바뀌었습니다.",
                "status": "matched",
            }],
            "ontologyRelationContext": {
                "decision": {"label": "위험 점검", "reviewLevel": "check"},
            },
        })

        self.assertEqual(
            "관계 판단 변화: 보유 유지에서 분할축소 검토로 바뀌었습니다.",
            rows[0],
        )


if __name__ == "__main__":
    unittest.main()
