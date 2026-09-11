import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from digital_twin.application.investment_case_query_service import InvestmentCaseQueryService
from digital_twin.application.notification.eligibility import NotificationDispatchEligibilityService
from digital_twin.application.notification.workflow import NotificationQueueRunner
from digital_twin.application.notification_decision_memory import context_with_previous_investment_insight
from digital_twin.domain.investment_insight_assessment import (
    investment_insight_delivery_transition, investment_insight_transition,
)
from digital_twin.domain.notification_ai_delivery import final_ai_delivery_decision
from digital_twin.domain.notifications import NotificationJob
from test_notification_ai_delivery import initial_holding_review_context


def completed_insight_context():
    context = initial_holding_review_context()
    context["ontologyRelationContext"]["targetRole"] = "watchlist"
    context["ontologyRelationContext"]["actionEnvelope"]["targetRole"] = "watchlist"
    context["notificationWriterProvenance"] = {"aiAuthored": True}
    context["notificationAIInsightProvenance"] = {
        "aiAuthored": True, "publicationContractPassed": True, "contractFailureCode": "",
    }
    context["decisionReconciliation"] = {
        "status": "reconciled", "notificationDecision": "send",
        "reason": "근거를 갖춘 새로운 투자 해석입니다.",
        "deliveryPolicy": final_ai_delivery_decision(context),
    }
    return context


class MemoryQueue:
    def __init__(self, job):
        self.job = job

    def pending(self, limit=10):
        return [self.job] if self.job.status == "pending" else []

    def mark_processing(self, job):
        job.status = "processing"

    def mark_done(self, job):
        job.status = "done"

    def mark_suppressed(self, job, reason):
        job.status = "suppressed"
        job.last_error = reason

    mark_failed = mark_suppressed


def episode(identity, direction, delivered=False):
    return {
        "episodeId": identity, "subjectCaseId": identity,
        "accountId": "main", "symbol": "005930",
        "insight": {"insightAssessment": {
            "publishable": True, "direction": direction,
            "horizon": "short-term", "conviction": "moderate", "thesisKey": "recovery",
        }},
        "notificationDelivery": {
            "delivered": delivered, "deliveredAt": "2026-09-11T00:02:00Z" if delivered else "",
            "notificationJobId": "delivery:" + identity,
        },
    }


class AIInsightDeliveryBoundaryTests(unittest.TestCase):
    def test_completed_ai_authority_survives_unchanged_graph_dispatch(self):
        job = NotificationJob.create("검증된 투자 해석", message_type="investmentInsight",
                                     context=completed_insight_context())
        self.assertEqual("send", final_ai_delivery_decision(job.context)["decision"])
        self.assertFalse(job.context["ontologyRelationDiff"]["material"])
        self.assertTrue(NotificationDispatchEligibilityService(MemoryQueue(job)).apply_inference_change_gate(job))
        self.assertEqual("validated-ai-insight", job.context["inferenceChangeGate"]["deliveryAuthorization"])

    def test_ai_dispatch_authority_requires_all_publication_proofs(self):
        cases = [
            ("notificationAIInsightProvenance", "publicationContractPassed", False),
            ("notificationAIInsightProvenance", "contractFailureCode", "unsupported-claim"),
            ("notificationAIInsightProvenance", "aiAuthored", False),
            ("notificationWriterProvenance", "aiAuthored", False),
            ("notificationAiExecutionAudit", "status", "processing"),
            ("notificationAiExecutionAudit", "fallback", {"used": True}),
            ("investmentInsightTransition", "material", False),
            ("decisionReconciliation", "notificationDecision", "suppress"),
            ("decisionPublication", "outcomeKind", "ABSTAIN"),
            ("notificationAiValidatedResponse", "insightAssessment", {"publishable": False}),
        ]
        for group, key, value in cases:
            with self.subTest(group=group, key=key):
                context = completed_insight_context()
                context[group][key] = value
                job = NotificationJob.create("검증 실패", message_type="investmentInsight", context=context)
                self.assertFalse(NotificationDispatchEligibilityService(MemoryQueue(job)).apply_inference_change_gate(job))
                self.assertEqual("suppressed", job.status)

    def test_completed_insight_reaches_transport_once_without_rerunning_ai(self):
        context = completed_insight_context()
        context["notificationAiQueue"] = {"status": "completed"}
        job = NotificationJob.create("검증된 투자 해석", account_id="main",
                                     message_type="investmentInsight", context=context)
        queue = MemoryQueue(job)
        transport = Mock()
        transport.send.return_value = SimpleNamespace(delivered=True, label="test", reason="", metadata={})
        ai = Mock()
        account = SimpleNamespace(account_id="main", quiet_hours_active=lambda *args: False)
        runner = NotificationQueueRunner(queue, SimpleNamespace(load_all=lambda: [account]),
                                         lambda account: transport, ai_request_enqueuer=ai)
        self.assertEqual(1, runner.run_once())
        self.assertEqual("done", job.status, job.last_error)
        self.assertEqual(0, runner.run_once())
        transport.send.assert_called_once()
        ai.enqueue.assert_not_called()
        self.assertEqual("NO_ACTION", job.context["notificationAiValidatedResponse"]["action"])

    def test_completed_insight_still_respects_account_quiet_hours_and_repeat_policy(self):
        for quiet, deferred in ((True, False), (False, True)):
            with self.subTest(quiet=quiet):
                context = completed_insight_context()
                if deferred:
                    context["preDecisionDeliveryGate"] = {"status": "deferred", "reasonCode": "state_cooldown"}
                job = NotificationJob.create("검증된 해석", account_id="main", message_type="investmentInsight", context=context)
                transport = Mock()
                account = SimpleNamespace(account_id="main", quiet_hours_active=lambda *args: quiet,
                                          quiet_hours_reason=lambda: "quiet hours",
                                          quiet_hours_start="22:00", quiet_hours_end="07:00",
                                          quiet_hours_timezone="Asia/Seoul")
                runner = NotificationQueueRunner(MemoryQueue(job), SimpleNamespace(load_all=lambda: [account]), lambda account: transport)
                runner.run_once()
                self.assertEqual("suppressed", job.status)
                transport.send.assert_not_called()

    def test_delivery_memory_retains_analysis_but_compares_only_delivered_insight(self):
        analysis = episode("analysis:blocked", "positive")
        delivered = episode("analysis:delivered", "negative", True)
        store = SimpleNamespace(latest_insight_episodes=Mock(return_value=[analysis]),
                                latest_delivered_insight_episodes=Mock(return_value=[delivered]))
        context = {"accountId": "main", "symbol": "005930"}
        memory = context_with_previous_investment_insight(context, store)
        self.assertEqual(analysis["episodeId"], memory["previousInvestmentAIInsightEpisode"]["episodeId"])
        self.assertEqual(delivered["episodeId"], memory["previousDeliveredInvestmentAIInsightEpisode"]["episodeId"])
        current = analysis["insight"]["insightAssessment"]
        self.assertFalse(investment_insight_transition(analysis, current)["material"])
        self.assertTrue(investment_insight_delivery_transition(memory, current)["material"])
        again = context_with_previous_investment_insight(memory, store)
        self.assertEqual(memory, again)
        store.latest_delivered_insight_episodes.assert_called_once_with(account_id="main", symbol="005930", limit=8)
        store.latest_delivered_insight_episodes.return_value = [episode("now-delivered", "positive", True)]
        after_delivery = context_with_previous_investment_insight(context, store)
        self.assertFalse(investment_insight_delivery_transition(after_delivery, current)["material"])

    def test_delivery_memory_rejects_foreign_scope_and_unconfirmed_receipts(self):
        wrong_account = episode("foreign", "negative", True)
        wrong_account["accountId"] = "other"
        wrong_symbol = episode("foreign-symbol", "negative", True)
        wrong_symbol["symbol"] = "MSTR"
        analysis = episode("analysis:unsent", "positive")
        store = SimpleNamespace(latest_insight_episodes=lambda **kwargs: [analysis],
                                latest_delivered_insight_episodes=lambda **kwargs: [wrong_account, wrong_symbol, analysis])
        memory = context_with_previous_investment_insight({"accountId": "main", "symbol": "005930"}, store)
        transition = investment_insight_delivery_transition(memory, analysis["insight"]["insightAssessment"])
        self.assertEqual("not-found", memory["investmentInsightDeliveryHistory"]["status"])
        self.assertEqual("initial-insight", transition["kind"])
        store.latest_delivered_insight_episodes = Mock(side_effect=RuntimeError("unavailable"))
        memory = context_with_previous_investment_insight({"accountId": "main", "symbol": "005930"}, store)
        transition = investment_insight_delivery_transition(memory, analysis["insight"]["insightAssessment"])
        self.assertEqual("error", memory["investmentInsightDeliveryHistory"]["status"])
        self.assertFalse(transition["material"])
        self.assertEqual("analysis-history-fallback", transition["comparisonBasis"])

    def test_web_does_not_label_semantic_send_as_actual_delivery(self):
        for status, label in (("pending", "발송 대기"), ("suppressed", "발송 안 됨"),
                              ("failed", "전달 실패"), ("delivered", "알림 전달 완료")):
            with self.subTest(status=status):
                saved = episode("test", "positive")
                saved["reconciliation"] = {"notificationDecision": "send", "reason": "새 해석"}
                saved["notificationDelivery"] = {"status": status, "reason": "실제 전송 상태"}
                projection = InvestmentCaseQueryService._ai_insight_projection({}, saved)
                self.assertEqual(label, projection["notificationDeliveryLabel"])
                self.assertEqual("실제 전송 상태", projection["deliveryReason"])
                self.assertEqual("send", projection["notificationDecision"])
