import copy
import unittest

from digital_twin.domain.notification_reasoning_report import customer_alert_reason_lines
from digital_twin.domain.notification_rule_evaluator import evaluate_notification_rule
from digital_twin.domain.notification_rule_models import (
    NotificationRuleCondition,
    NotificationRuleConfig,
)
from digital_twin.domain.notifications import NotificationJob
from digital_twin.application.notification.admission import (
    NotificationAdmissionPolicy,
    _relation_trigger_provenance,
)
from digital_twin.domain.notification_rules import default_notification_rule


class NotificationTriggerLedgerTests(unittest.TestCase):
    @staticmethod
    def context_observation_context():
        rule = {
            "ruleId": "graph.news.direct_material_context.v1",
            "label": "직접 중요 맥락 뉴스 확인",
            "knowledgeBasis": {
                "ruleKind": "context-observation",
                "decisionEligibility": "reference-only",
                "requiresHypothesis": False,
            },
        }
        return {
            "messageType": "investmentInsight",
            "symbol": "005380",
            "ontologyRelationContext": {
                "source": "typedbInferenceBox",
                "graphStore": "typedb",
                "graphStoreUsed": True,
                "fallbackUsed": False,
                "sourceAboxSnapshotId": "abox:hyundai:1",
                "inferenceGenerationId": "generation:hyundai:1",
                "generationAligned": True,
                "subject": {"symbol": "005380", "market": "KR"},
                "facts": {"currentPrice": 413500, "market": "KR"},
                "activeRules": [rule],
                "matchedRules": [rule],
                "decision": {"selectedRuleId": rule["ruleId"], "basis": "typedbInferenceBox"},
                "graphStoreInference": {
                    "graphStore": "typedb",
                    "sourceAboxSnapshotId": "abox:hyundai:1",
                    "inferenceGenerationId": "generation:hyundai:1",
                    "relations": [rule],
                    "traces": [{"id": "trace:hyundai:1", **rule}],
                },
            },
        }

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
            "notification-delivery-trigger-ledger-v2",
            context["deliveryTriggerLedgerVersion"],
        )
        condition = next(
            item for item in context["deliveryTriggerLedger"]
            if item["triggerId"] == "condition:severity-watch"
        )
        self.assertEqual("WATCH", condition["currentValue"])
        self.assertEqual("WATCH", condition["threshold"])
        self.assertFalse(condition["customerVisible"])
        self.assertEqual("internal-gate", condition["triggerCategory"])
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
                "customerVisible": True,
                "triggerCategory": "material-change",
            }],
            "ontologyRelationContext": {
                "decision": {"label": "위험 점검", "reviewLevel": "check"},
            },
        })

        self.assertEqual(
            "관계 판단 변화: 보유 유지에서 분할축소 검토로 바뀌었습니다.",
            rows[0],
        )

    def test_customer_reason_hides_internal_configured_conditions(self):
        rows = customer_alert_reason_lines({
            "deliveryTriggerLedger": [{
                "triggerId": "condition:body-present",
                "kind": "configured-condition",
                "label": "본문 있음",
                "reason": "본문 있음",
                "status": "matched",
                "customerVisible": False,
                "triggerCategory": "internal-gate",
            }],
            "ontologyRelationContext": {
                "decision": {"label": "중요 자료 확인", "reviewLevel": "observe"},
            },
        })

        self.assertFalse(any("본문 있음" in row for row in rows))

    def test_initial_reference_only_observation_without_source_is_suppressed(self):
        policy = NotificationAdmissionPolicy()
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "중요 자료 확인",
            account_id="main",
            message_type="investmentInsight",
            context=self.context_observation_context(),
        )

        decision = policy.prepare(job, rule)
        decision = policy.evaluate(job, rule, decision)

        self.assertFalse(decision.should_send)
        self.assertEqual("unresolved_material_evidence", decision.suppression_reason)

    def test_changed_material_context_without_exact_source_is_suppressed(self):
        policy = NotificationAdmissionPolicy()
        rule = default_notification_rule("investmentInsight")
        current = self.context_observation_context()
        previous = copy.deepcopy(current)
        previous_rule_id = "graph.market_proxy.observation.risk_context.v1"
        previous["ontologyRelationContext"]["decision"]["selectedRuleId"] = previous_rule_id
        for key in ("activeRules", "matchedRules"):
            previous["ontologyRelationContext"][key][0]["ruleId"] = previous_rule_id
        previous["ontologyRelationContext"]["graphStoreInference"]["relations"][0]["ruleId"] = previous_rule_id
        previous["ontologyRelationContext"]["graphStoreInference"]["traces"][0]["ruleId"] = previous_rule_id
        job = NotificationJob.create(
            "중요 자료 확인",
            account_id="main",
            message_type="investmentInsight",
            context=current,
        )

        decision = policy.prepare(job, rule)
        decision = policy.evaluate(
            job,
            rule,
            decision,
            relation_previous_context=previous,
        )

        self.assertFalse(decision.should_send)
        self.assertEqual("unresolved_material_evidence", decision.suppression_reason)

    def test_nested_news_source_is_bound_to_rule_and_evidence_provenance(self):
        context = self.context_observation_context()
        context["newsHeadlines"] = {
            "items": [{
                "title": "현대차 공급 계약 공시",
                "source": "공식 IR",
                "url": "https://example.test/hyundai-ir",
                "publishedAt": "2026-08-25T00:00:00Z",
            }],
        }
        trace = context["ontologyRelationContext"]["graphStoreInference"]["traces"][0]
        trace["evidenceRelationIds"] = ["evidence:hyundai-ir"]

        provenance = _relation_trigger_provenance(context)

        self.assertEqual("현대차 공급 계약 공시", provenance["sourceTitle"])
        self.assertEqual("공식 IR", provenance["sourceProvider"])
        self.assertIn("graph.news.direct_material_context.v1", provenance["ruleIds"])
        self.assertIn("evidence:hyundai-ir", provenance["evidenceIds"])


if __name__ == "__main__":
    unittest.main()
