import unittest
from datetime import datetime, timezone

from digital_twin.application.notification_service import NotificationQueueRunner
from digital_twin.application.notification.admission import NotificationAdmissionPolicy
from digital_twin.domain.notification_ai_delivery import final_ai_delivery_decision
from digital_twin.domain.notification_rules import NotificationRuleDecision
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


def graph_risk_context(material=True):
    return {
        "messageType": "investmentInsight",
        "market": "KR",
        "symbol": "005930",
        "marketHoursEnabled": True,
        "marketHoursMarkets": ["KR", "US"],
        "body": "본문에 손실과 분할축소라는 말이 포함됩니다.",
        "ontologyRelationDiff": {
            "material": material,
            "reason": "행동 범위 변경" if material else "동일 행동 범위",
            "decisionTransition": {
                "kind": "action-changed" if material else "unchanged",
                "material": material,
                "currentAction": "TRIM" if material else "HOLD",
            },
        },
        "ontologyRelationContext": {
            "source": "typedbInferenceBox",
            "graphStoreUsed": True,
            "fallbackUsed": False,
            "decision": {
                "basis": "typedbInferenceBox",
                "decisionStage": "RISK_REVIEW",
                "actionGroup": "lossControl",
                "primaryAction": "TRIM_REVIEW",
            },
            "decisionState": {
                "reviewLevel": "act",
                "dataState": "sufficient",
            },
            "actionEnvelope": {"preferredAction": "TRIM"},
        },
    }


class FinalAIDeliveryTests(unittest.TestCase):
    def test_closed_market_admission_remains_deliverable(self):
        policy = NotificationAdmissionPolicy()
        job = NotificationJob.create(
            "test",
            account_id="main",
            message_type="investmentInsight",
            context=graph_risk_context(material=True),
        )
        decision = NotificationRuleDecision(
            message_type="investmentInsight",
            enabled=True,
            should_send=False,
            delivery_state="suppressed",
            gate_state="blocked",
            gate_reason="미장 닫힘",
            suppression_reason="market_closed",
            market_hours_enabled=True,
            market_hours_status="closed",
            market_hours_reason="미장 닫힘",
        )

        outcome = policy.apply_result(job, decision)

        self.assertTrue(outcome.accepted)
        self.assertEqual("pending", job.status)
        self.assertEqual("eligible", job.context["deliveryDecision"])
        self.assertEqual("advisory", job.context["marketHoursDecision"])
        self.assertNotIn("preDecisionDeliveryGate", job.context)
        self.assertNotIn("deliverySuppressionReason", job.context)

    def test_typedb_fallback_is_sent_even_when_final_action_is_unchanged(self):
        context = watchlist_context()
        context["notificationAiExecutionAudit"] = {"status": "typedb-fallback"}

        decision = final_ai_delivery_decision(context)

        self.assertEqual("send", decision["decision"])
        self.assertTrue(decision["typedbFallback"])

    def test_candidate_only_watchlist_change_is_suppressed(self):
        decision = final_ai_delivery_decision(watchlist_context())

        self.assertEqual("suppress", decision["decision"])
        self.assertIn("최종 AI 행동", decision["reason"])

    def test_final_ai_action_change_is_sent(self):
        decision = final_ai_delivery_decision(watchlist_context(ai_kind="action-changed"))

        self.assertEqual("send", decision["decision"])

    def test_non_material_graph_rebaseline_cannot_send_action_change(self):
        context = watchlist_context(ai_kind="action-changed")
        context["notificationAiValidatedResponse"]["action"] = "BUY"
        context["aiDecisionTransition"].update({
            "previousAction": "HOLD",
            "currentAction": "BUY",
        })
        context["decisionTransition"] = {
            "kind": "initial",
            "material": False,
            "previousAction": "",
            "currentAction": "BUY",
        }
        context["investmentNotificationTransition"] = {
            "changed": True,
            "material": True,
            "kind": "action-changed",
        }

        decision = final_ai_delivery_decision(context)

        self.assertEqual("suppress", decision["decision"])
        self.assertEqual("non_material_action_rebaseline", decision["suppressionReason"])

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

    def test_nearly_expired_investment_snapshot_requests_refresh_without_blocking_ai(self):
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

        self.assertTrue(allowed)
        self.assertEqual(["005930"], requested)
        self.assertEqual("advisory", job.context["aiFreshnessHeadroomGate"]["decision"])
        self.assertTrue(job.context["aiFreshnessHeadroomGate"]["blockingDisabled"])
        self.assertNotIn("deliverySuppressionReason", job.context)
        self.assertEqual("", queue.reason)

    def test_closed_market_is_advisory_before_ai_and_at_dispatch(self):
        queue = SuppressionQueue()
        runner = NotificationQueueRunner(
            queue,
            account_repository=None,
            notifier_factory=lambda account: None,
            now_provider=lambda: datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc),
        )
        context = graph_risk_context(material=False)
        context.pop("ontologyRelationDiff")
        context.pop("ontologyRelationContext")
        job = NotificationJob.create(
            "test",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )

        self.assertTrue(runner.apply_market_hours_gate(job, "AI 판단 전"))
        self.assertEqual("closed", job.context["marketHoursStatus"])
        self.assertFalse(job.context["preAiMarketHoursAssessment"]["blocking"])

        self.assertTrue(runner.apply_market_hours_gate(job, "발송 직전"))
        self.assertEqual("send", job.context["dispatchMarketHoursGate"]["decision"])
        self.assertTrue(job.context["dispatchMarketHoursGate"]["blockingDisabled"])
        self.assertEqual("advisory", job.context["marketHoursDecision"])
        self.assertNotIn("deliverySuppressionReason", job.context)
        self.assertEqual("", queue.reason)

    def test_material_typedb_risk_transition_records_closed_market_without_special_case(self):
        queue = SuppressionQueue()
        runner = NotificationQueueRunner(
            queue,
            account_repository=None,
            notifier_factory=lambda account: None,
            now_provider=lambda: datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc),
        )
        job = NotificationJob.create(
            "test",
            account_id="main",
            message_type="investmentInsight",
            context=graph_risk_context(material=True),
        )

        self.assertTrue(runner.apply_market_hours_gate(job, "발송 직전"))
        self.assertEqual("closed", job.context["marketHoursStatus"])
        self.assertEqual("advisory", job.context["marketHoursDecision"])
        self.assertNotIn("TypeDB 관계", job.context["marketHoursReason"])


if __name__ == "__main__":
    unittest.main()
