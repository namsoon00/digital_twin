"""Research lifecycle visibility must not imply empirical or delivery success."""
import unittest
from unittest.mock import Mock

from digital_twin.modules.ai_orchestration.domain.research_progress import research_progress
from digital_twin.modules.notifications.application.research_progress_message import render_research_progress
from digital_twin.infrastructure.transactions.research_progress_publication import ResearchProgressPublication


def case():
    return {"caseId": "case-1", "accountId": "account-1", "symbol": "TEST", "worldId": "world-1",
            "kind": "question", "capability": "research", "status": "waiting",
            "question": "매출 확대가 현금 회수로 이어지는가?", "reason": "공식 자료 조사를 예약했습니다.",
            "nextCheckAt": "2026-10-11T00:00:00Z"}


def event(stage="created", key="event-1"):
    return {"stage": stage, "eventId": key, "taskId": "task-1", "revision": 1, "at": "2026-10-10T00:00:00Z"}


class ResearchProgressTests(unittest.TestCase):
    def test_real_milestones_only_and_snapshot_does_not_mutate(self):
        value = case()
        notice = research_progress(value, event())
        self.assertEqual("연구 질문 등록", notice["label"])
        value["reason"] = "표현만 바뀐 이유"
        self.assertIsNone(research_progress(value, event(key="event-2")))
        self.assertNotEqual(value["reason"], notice["reason"])
        for stage in ("review-deferred", "business-review-deferred", "review-failed", "assessment"):
            self.assertIsNone(research_progress(value, event(stage)))
        value.update(status="answered", lastAssessment={"evidenceIds": ["official-1"]})
        answered = research_progress(value, event("assessment"))
        self.assertEqual(["official-1"], answered["evidenceIds"])
        self.assertEqual("not-empirically-qualified", answered["qualification"])
        self.assertIsNone(research_progress(value, event("assessment")))

    def test_collection_is_not_answer_and_failures_do_not_flood(self):
        value = case()
        for status, count in (("failed", 3), ("research-cooldown", 3), ("completed", 0)):
            value["lastResearch"] = {"result": {"status": status, "changedEvidenceCount": count}}
            self.assertIsNone(research_progress(value, event("research-returned")))
        value["lastResearch"]["result"]["changedEvidenceCount"] = 3
        notice = research_progress(value, event("research-returned"))
        body = render_research_progress(notice)
        self.assertIn("답변 검토 대기", body)
        self.assertIn("아직 검토 중", body)
        self.assertIsNone(research_progress(value, event("research-returned", "another-run")))

    def test_business_revision_and_negative_observation_survive_dedup(self):
        value = {**case(), "kind": "business-thesis", "status": "tracking", "observations": [],
                 "contract": {"mechanism": "수요 확대가 현금 회수로 연결될 가능성을 연구합니다.",
                              "alternative": "외상 증가일 수 있습니다.", "invalidation": "현금 회수가 악화되면 재검토합니다."}}
        research_progress(value, event("business-registered"))
        self.assertIsNone(research_progress(value, event("business-reviewed")))
        value["observations"] = [{"status": "direction-not-observed"}]
        notice = research_progress(value, event("business-reviewed"))
        self.assertIn("불일치 1건", render_research_progress(notice))
        self.assertIsNone(research_progress(value, event("business-reviewed")))
        value["status"] = "superseded"
        self.assertEqual("사업 가설 수정", research_progress(value, event("business-reviewed"))["label"])
        value["status"] = "retired"
        self.assertEqual("사업 가설 종료", research_progress(value, event("business-reviewed"))["label"])

    def test_frozen_escaped_body_account_routing_and_user_setting(self):
        value = case(); value["question"] = '<script>alert("x")</script>'
        notice = research_progress(value, event())
        queue = Mock(); queue.enqueue_with_connection.return_value = True
        publisher = ResearchProgressPublication(queue)
        connection = object()
        receipt = publisher.publish(connection, notice)
        job = queue.enqueue_with_connection.call_args.args[1]
        self.assertEqual("queued", receipt["status"])
        self.assertEqual("account-1", job.account_id)
        self.assertEqual("event-1", job.source_event_id)
        self.assertNotIn("<script>", job.text)
        self.assertIn("&lt;script&gt;", job.text)
        from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
        renderer = NotificationRenderingService(context_enricher=Mock(side_effect=AssertionError("no new facts")))
        self.assertEqual(job.text, renderer.render(job))
        from digital_twin.modules.notifications.domain.message_types import (
            user_managed_notification_types, notification_message_types, is_operations_delivery_message_type)
        from digital_twin.modules.notifications.domain.notification_rule_models import default_notification_rule
        self.assertIn("researchProgress", user_managed_notification_types())
        self.assertIn("researchProgress", notification_message_types())
        self.assertFalse(is_operations_delivery_message_type("researchProgress"))
        self.assertFalse(default_notification_rule("researchProgress").similarity_enabled)
        queue.enqueue_with_connection.return_value = False
        self.assertEqual("suppressed", publisher.publish(connection, notice)["status"])
        queue.reset_mock()
        disabled = ResearchProgressPublication(queue, {"alertRules": "researchProgress=0"})
        self.assertEqual("disabled", disabled.publish(connection, notice)["status"])
        queue.enqueue_with_connection.assert_not_called()
        from digital_twin.modules.notifications.domain.notification_rule_evaluator import evaluate_notification_rule
        rule = default_notification_rule("researchProgress")
        self.assertTrue(evaluate_notification_rule(job, rule).should_send)
        job.account_id = "another-account"
        self.assertFalse(evaluate_notification_rule(job, rule).should_send)

    def test_research_status_cannot_smuggle_a_trading_directive(self):
        value = case(); value["reason"] = "지금 매수하세요."
        notice = research_progress(value, event())
        self.assertNotIn("매수하세요", render_research_progress(notice))
        self.assertIn("표현 검토", render_research_progress(notice))
