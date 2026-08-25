import unittest
from types import SimpleNamespace

from digital_twin.application.notification.workflow import NotificationQueueRunner
from digital_twin.domain.notification_delivery_explanation import (
    build_customer_delivery_explanation,
    customer_delivery_explanation_lines,
    validate_customer_delivery_explanation,
)
from digital_twin.domain.notification_reasoning_report import customer_alert_reason_lines
from digital_twin.domain.notifications import NotificationJob


class NotificationDeliveryExplanationTests(unittest.TestCase):
    @staticmethod
    def unchanged_verification_context():
        return {
            "testDispatch": True,
            "investmentNotificationTransition": {
                "version": "investment-notification-transition-v2",
                "changed": False,
                "material": False,
                "historyAvailable": False,
                "kind": "initial",
                "changedFields": [],
                "previousState": {},
                "currentState": {
                    "action": "HOLD",
                    "actionLabel": "보유 유지",
                    "label": "보유 유지 · 추가 확인",
                },
            },
            "ontologyRelationDiff": {
                "changed": True,
                "material": True,
                "reason": "Meaningful graph relation change: decision, actionEnvelope",
            },
            "deliveryTriggerLedger": [],
            "notificationAiValidatedResponse": {"action": "HOLD"},
        }

    def test_real_regression_verification_does_not_claim_graph_change(self):
        explanation = build_customer_delivery_explanation(
            message_type="investmentInsight",
            source_event_name="notification.verification",
            source_event_id="verification:event:1",
            context=self.unchanged_verification_context(),
        )

        self.assertEqual("valid", explanation["validation"]["state"])
        self.assertEqual("verification", explanation["primaryCause"]["category"])
        lines = customer_delivery_explanation_lines({"customerDeliveryExplanation": explanation})
        self.assertIn("검증 발송", lines[0])
        self.assertIn("새 투자 신호가 아니라", lines[1])
        self.assertFalse(any("graph" in line.lower() for line in lines))
        self.assertFalse(any("관계 변화" in line for line in lines))

    def test_action_transition_uses_previous_and_current_final_actions(self):
        context = {
            "investmentNotificationTransition": {
                "version": "investment-notification-transition-v2",
                "changed": True,
                "material": True,
                "historyAvailable": True,
                "kind": "action-changed",
                "changedFields": ["action"],
                "previousState": {"action": "HOLD", "actionLabel": "보유 유지"},
                "currentState": {"action": "REDUCE", "actionLabel": "분할축소 검토"},
            },
        }

        explanation = build_customer_delivery_explanation(
            message_type="investmentInsight",
            source_event_name="reasoning.decision_completed",
            context=context,
        )

        primary = explanation["primaryCause"]
        self.assertEqual("valid", explanation["validation"]["state"])
        self.assertEqual("action-transition", primary["category"])
        self.assertEqual("HOLD", primary["previousValue"])
        self.assertEqual("REDUCE", primary["currentValue"])
        self.assertIn("보유 유지에서 분할축소 검토", primary["summary"])

    def test_material_evidence_requires_exact_source_reference(self):
        context = {
            "investmentNotificationTransition": {
                "version": "investment-notification-transition-v2",
                "changed": False,
                "material": False,
                "historyAvailable": True,
                "currentState": {"action": "HOLD"},
            },
            "deliveryTriggerLedger": [{
                "triggerId": "material-evidence:disclosure:1",
                "kind": "material-evidence",
                "label": "확인된 새 근거",
                "status": "matched",
                "customerVisible": True,
                "sourceTitle": "분기보고서",
                "sourceProvider": "OpenDART",
                "sourceUrl": "https://example.test/disclosure/1",
                "evidenceIds": ["evidence:disclosure:1"],
            }],
        }

        explanation = build_customer_delivery_explanation(
            message_type="investmentInsight",
            source_event_name="reasoning.decision_completed",
            context=context,
        )

        self.assertEqual("valid", explanation["validation"]["state"])
        self.assertEqual("material-evidence", explanation["primaryCause"]["category"])
        self.assertIn("분기보고서", explanation["primaryCause"]["summary"])
        self.assertIn("evidence:disclosure:1", explanation["primaryCause"]["sourceReferences"])

    def test_material_relation_readiness_change_is_explained_without_claiming_final_action_change(self):
        context = {
            "investmentNotificationTransition": {
                "version": "investment-notification-transition-v2",
                "changed": False,
                "material": False,
                "historyAvailable": False,
                "kind": "initial",
                "currentState": {"action": "HOLD"},
            },
            "ontologyRelationDiff": {
                "changed": True,
                "material": True,
                "previousFingerprint": "previous",
                "currentFingerprint": "current",
                "decisionTransition": {
                    "changed": True,
                    "material": True,
                    "kind": "readiness-changed",
                    "previousAction": "hold",
                    "currentAction": "hold",
                    "previousDataReadiness": "ready",
                    "currentDataReadiness": "partial",
                },
            },
        }

        explanation = build_customer_delivery_explanation(
            message_type="investmentInsight",
            source_event_name="monitoring.alerts_detected",
            context=context,
        )

        primary = explanation["primaryCause"]
        self.assertEqual("valid", explanation["validation"]["state"])
        self.assertEqual("readiness-transition", primary["category"])
        self.assertEqual("relation-decision-transition", primary["basis"])
        self.assertIn("판단 가능에서 일부 자료만 확인", primary["summary"])
        self.assertNotIn("최종 행동", primary["summary"])

    def test_unchanged_normal_alert_without_material_cause_is_invalid(self):
        context = self.unchanged_verification_context()
        context.pop("testDispatch")
        explanation = build_customer_delivery_explanation(
            message_type="investmentInsight",
            source_event_name="reasoning.decision_completed",
            context=context,
        )

        self.assertEqual("invalid", explanation["validation"]["state"])
        self.assertIn("primary-cause-missing", explanation["validation"]["errors"])

    def test_replay_has_new_envelope_reason_without_changing_original_body(self):
        explanation = build_customer_delivery_explanation(
            message_type="investmentInsight",
            source_event_name="notification.replay_requested",
            context={
                "notificationReplay": True,
                "replaySourceJobId": "source-job-1",
                "replaySourceNotificationNumber": "N-SOURCE01",
            },
        )

        self.assertEqual("valid", explanation["validation"]["state"])
        self.assertEqual("replay", explanation["primaryCause"]["category"])
        self.assertIn("N-SOURCE01", explanation["primaryCause"]["summary"])

    def test_validator_rejects_internal_relation_language(self):
        explanation = {
            "primaryCause": {
                "category": "scheduled-repeat",
                "summary": "Meaningful graph relation change: decision",
            },
        }
        validation = validate_customer_delivery_explanation(
            explanation,
            message_type="investmentInsight",
            source_event_name="reasoning.decision_completed",
            context={},
        )

        self.assertEqual("invalid", validation["state"])
        self.assertIn("internal-language-exposed", validation["errors"])

    def test_customer_reason_prefers_canonical_contract_over_legacy_why_now(self):
        explanation = build_customer_delivery_explanation(
            message_type="investmentInsight",
            source_event_name="notification.verification",
            context=self.unchanged_verification_context(),
        )
        rows = customer_alert_reason_lines({
            "customerDeliveryExplanation": explanation,
            "ontologyRelationContext": {
                "whyNow": {
                    "changeDrivers": ["관계 그래프 변화: Meaningful graph relation change"],
                },
            },
        })

        self.assertIn("검증 발송", rows[0])
        self.assertFalse(any("관계 그래프 변화" in row for row in rows))

    def test_required_contract_never_falls_back_to_relation_rule_prose(self):
        rows = customer_alert_reason_lines({
            "customerDeliveryExplanationRequired": True,
            "ontologyRelationContext": {
                "whyNow": {
                    "changeDrivers": ["관계 그래프 변화: Meaningful graph relation change"],
                },
            },
        })

        self.assertEqual([], rows)

    def test_final_delivery_gate_freezes_verified_explanation(self):
        lifecycle = []

        class Queue:
            def record_lifecycle(self, _job, stage, outcome, reason="", metadata=None):
                lifecycle.append((stage, outcome, reason, metadata or {}))

        job = NotificationJob.create(
            "검증 본문",
            message_type="investmentInsight",
            source_event_name="notification.verification",
            context=self.unchanged_verification_context(),
        )
        runner = NotificationQueueRunner(Queue(), None, lambda _account: None)

        self.assertTrue(runner.apply_customer_delivery_explanation_gate(job))
        self.assertEqual("valid", job.context["customerDeliveryExplanationValidationState"])
        self.assertEqual("verification", job.context["customerDeliveryExplanation"]["primaryCause"]["category"])
        self.assertEqual("delivery_reason_validated", lifecycle[0][0])

    def test_final_delivery_gate_suppresses_contradictory_reason_and_reports_operations(self):
        lifecycle = []
        operation_messages = []

        class Queue:
            def mark_suppressed(self, job, reason):
                job.status = "suppressed"
                job.last_error = reason

            def record_lifecycle(self, _job, stage, outcome, reason="", metadata=None):
                lifecycle.append((stage, outcome, reason, metadata or {}))

        class OperationsNotifier:
            def send(self, message):
                operation_messages.append(message)
                return SimpleNamespace(delivered=True, label="operations-test")

        context = self.unchanged_verification_context()
        context.pop("testDispatch")
        job = NotificationJob.create(
            "일반 본문",
            message_type="investmentInsight",
            source_event_name="reasoning.decision_completed",
            context=context,
        )
        runner = NotificationQueueRunner(
            Queue(),
            None,
            lambda _account: None,
            operations_notifier_factory=lambda _account: OperationsNotifier(),
        )

        self.assertFalse(runner.apply_customer_delivery_explanation_gate(job))
        self.assertEqual("suppressed", job.status)
        self.assertEqual("customer_delivery_explanation_invalid", job.context["deliverySuppressionReason"])
        self.assertIn("primary-cause-missing", job.last_error)
        self.assertEqual(1, len(operation_messages))
        self.assertIn("NotificationDeliveryContractError", operation_messages[0])
        self.assertEqual("invalid", lifecycle[0][1])


if __name__ == "__main__":
    unittest.main()
