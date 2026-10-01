"""Cutover, grounded publication and receipt-backed delivery regressions."""
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from digital_twin.modules.ai_orchestration.domain.planning import identity, stamp, validate_plan
from digital_twin.modules.ai_orchestration.domain.publication import publication_block, repeat_block
from digital_twin.infrastructure.transactions.ai_control_publication import AIControlPublication, validate_narrative
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.domain.delivery_suppression import NotificationDeliverySuppressed
from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
from digital_twin.modules.notifications.application.notification.dispatch import NotificationDispatchService
from test_ai_control import SUBJECT, PLAN


def observation():
    packet = {**SUBJECT, "sourceSnapshotId": "verified-snapshot", "capturedAt": stamp(),
              "facts": [{"id": "quote-1", "currentPrice": 100, "changeRate": -1.2, "currency": "KRW",
                         "quantity": 2, "averagePrice": 110, "profitLossRate": -9.09, "ma20": 108,
                         "foreignNetVolume": -30, "sourceAsOf": stamp()}]}
    plan = {**PLAN, "summary": "주가가 100원으로 내려왔지만 수급과의 관계는 더 확인해야 합니다.",
            "notification": {"send": True, "reason": "가격과 수급이 함께 약해져 가설을 다시 확인할 시점입니다."}}
    return {**validate_plan(plan, packet), "input": packet, "observedAt": stamp(), "inputFingerprint": identity(packet["facts"])}


class CentralPublicationPolicyTests(unittest.TestCase):
    def test_silence_stale_quotes_and_ungrounded_numbers_remain_internal(self):
        result = observation()
        self.assertEqual("", publication_block(result))
        self.assertEqual("", validate_narrative(result))
        for key, value in (("summary", "내일은 900원에 도달합니다."), ("hypothesis", "지금 매수하세요.")):
            self.assertTrue(validate_narrative({**result, key: value}))
        compared = {**result, "comparison": "지난 관찰의 102원보다 낮아졌습니다.",
                    "comparisonFacts": [{"id": "past-quote", "currentPrice": 102, "sourceAsOf": "2026-09-01T00:00:00Z"}]}
        self.assertEqual("", validate_narrative(compared))
        result["notification"]["send"] = False
        self.assertTrue(publication_block(result))
        result["notification"]["send"] = True
        result["input"]["facts"][0]["sourceAsOf"] = "2000-01-01T00:00:00Z"
        self.assertTrue(publication_block(result))

    def test_repeat_policy_uses_successful_subject_receipts_and_exact_boundaries(self):
        result = observation()
        now = datetime.now(timezone.utc)
        receipt = {"accountId": SUBJECT["accountId"], "symbol": "TEST", "inputFingerprint": "different",
                   "deliveredAt": (now - timedelta(minutes=179)).isoformat()}
        self.assertTrue(repeat_block(result, [receipt], SUBJECT["accountId"], "TEST", now))
        receipt["deliveredAt"] = (now - timedelta(minutes=180)).isoformat()
        self.assertFalse(repeat_block(result, [receipt], SUBJECT["accountId"], "TEST", now))
        receipt["inputFingerprint"] = result["inputFingerprint"]
        self.assertTrue(repeat_block(result, [receipt], SUBJECT["accountId"], "TEST", now))
        self.assertFalse(repeat_block(result, [receipt], "other-account", "TEST", now))
        today = [{**receipt, "inputFingerprint": str(i), "deliveredAt": now.replace(hour=0, minute=0, second=0).isoformat()} for i in range(8)]
        self.assertTrue(repeat_block(result, today, SUBJECT["accountId"], "OTHER", now))

    def test_legacy_manual_analysis_and_replayed_delivery_are_blocked(self):
        from digital_twin.modules.decisions.application.ai_inference_queue_service import NotificationAIRequestEnqueuer, AIInferenceQueueRunner
        settings = {"investmentNotificationRoute": "ai-control"}
        queue, prepare, reviewer = Mock(), Mock(), Mock()
        enqueuer = NotificationAIRequestEnqueuer(queue, prepare, settings)
        old = NotificationJob.create("old text", message_type="investmentInsight", context={"notificationReplayPreserveOriginal": True})
        self.assertEqual("retired", enqueuer.enqueue(old)["status"])
        self.assertEqual("retired", enqueuer.enqueue_subject_decision(old)["status"])
        prepare.assert_not_called()
        queue.enqueue.assert_not_called()
        runner = AIInferenceQueueRunner(queue, reviewer, settings=settings)
        self.assertEqual(0, runner.run_once())
        queue.claim.assert_not_called()
        publication = AIControlPublication(settings, Mock(), Mock(), lambda: [])
        with self.assertRaises(NotificationDeliverySuppressed), publication.delivery_guard(old, "old text"):
            self.fail("must not reach transport")
        with publication.delivery_guard(NotificationJob.create("handoff", message_type="workHandoff"), "handoff"):
            pass


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class CentralPublicationStorageTests(unittest.TestCase):
    def setUp(self):
        from digital_twin.infrastructure.settings import runtime_settings
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
        from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore
        self.settings = {**runtime_settings(), "aiControlDailyTaskBudget": 100, "aiControlEnabled": "true"}
        self.control, self.queue = MySQLAIControlStore(self.settings), MySQLNotificationJobStore(self.settings)
        self.publication = AIControlPublication(self.settings, self.control, self.queue, lambda: [SUBJECT])
        self.control.outbox_writer = self.publication.publish
        self.clean()

    def clean(self):
        with self.control.transaction() as connection:
            connection.execute("DELETE FROM notification_delivery_attempts WHERE job_id IN (SELECT job_id FROM notification_jobs WHERE account_id=%s)", (SUBJECT["accountId"],))
            connection.execute("DELETE FROM notification_jobs WHERE account_id=%s", (SUBJECT["accountId"],))
            connection.execute("DELETE FROM ai_control_tasks")
            connection.execute("DELETE FROM ai_control_budget")

    def tearDown(self):
        self.clean()

    def publish(self, result=None):
        self.control.seed(SUBJECT)
        task = self.control.claim()
        result = result or observation()
        self.assertTrue(self.control.complete(task, result, []))
        self.assertEqual("queued", result["publication"]["status"], result.get("publication"))
        with self.queue.connect() as connection:
            row = connection.execute("SELECT text,payload_json FROM notification_jobs WHERE job_id=%s", (result["publication"]["jobId"],)).fetchone()
        return task, result, self.queue.job_from_row(row)

    def test_successful_send_is_only_baseline_and_cannot_be_replayed(self):
        task, result, job = self.publish()
        self.assertEqual({}, self.publication.memory(job.account_id, "TEST"))
        renderer = NotificationRenderingService(context_enricher=Mock(side_effect=AssertionError("must not enrich")))
        message = renderer.render(job)
        self.assertIn("시세: 100원", message)
        self.assertIn("평가 손익률: -9.09%", message)
        notifier = Mock(supports_delivery_checkpoints=False)
        notifier.send.return_value = SimpleNamespace(delivered=True, label="test", reason="", metadata={})
        dispatcher = NotificationDispatchService(self.queue, lambda _: notifier, delivery_guard=self.publication.delivery_guard)
        with patch("digital_twin.infrastructure.settings.runtime_settings", return_value=self.settings):
            dispatcher.deliver(job, {job.account_id: object()}, message)
            with self.assertRaises(NotificationDeliverySuppressed):
                dispatcher.deliver(job, {job.account_id: object()}, message)
        notifier.send.assert_called_once()
        baseline = self.publication.memory(job.account_id, "TEST")
        self.assertEqual(job.job_id, baseline["jobId"])
        self.assertEqual(100, baseline["facts"][0]["currentPrice"])
        self.assertEqual({}, self.publication.memory("another", "TEST"))

    def test_notification_worker_delivers_new_type_without_legacy_ai_review(self):
        from digital_twin.modules.notifications.application.notification.workflow import NotificationQueueRunner
        _, result, job = self.publish()
        account = SimpleNamespace(account_id=SUBJECT["accountId"], quiet_hours_active=lambda *_: False)
        notifier = Mock(supports_delivery_checkpoints=False)
        notifier.send.return_value = SimpleNamespace(delivered=True, label="test", reason="", metadata={})
        old_ai = Mock()
        runner = NotificationQueueRunner(self.queue, SimpleNamespace(load_all=lambda: [account]), lambda _: notifier,
            settings=self.settings, include_message_types=["aiObservation"], ai_request_enqueuer=old_ai,
            delivery_guard=self.publication.delivery_guard)
        with patch("digital_twin.infrastructure.settings.runtime_settings", return_value=self.settings):
            self.assertEqual(1, runner.run_once())
        with self.queue.connect() as connection:
            self.assertEqual("done", connection.execute("SELECT status FROM notification_jobs WHERE job_id=%s", (job.job_id,)).fetchone()["status"])
        old_ai.enqueue.assert_not_called()
        notifier.send.assert_called_once()

    def test_tampered_subject_body_or_retired_subject_cannot_reach_transport(self):
        _, result, job = self.publish()
        message = NotificationRenderingService().render(job)
        with patch("digital_twin.infrastructure.settings.runtime_settings", return_value=self.settings):
            with self.assertRaises(NotificationDeliverySuppressed), self.publication.delivery_guard(job, message + "extra"):
                self.fail("tampered body")
            job.account_id = "another"
            with self.assertRaises(NotificationDeliverySuppressed), self.publication.delivery_guard(job, message):
                self.fail("wrong account")
            job.account_id = SUBJECT["accountId"]
            self.publication.subjects = lambda: []
            with self.assertRaises(NotificationDeliverySuppressed), self.publication.delivery_guard(job, message):
                self.fail("retired subject")

    def test_outbox_failure_rolls_back_task_and_notification_together(self):
        self.control.seed(SUBJECT)
        task = self.control.claim()
        def failing_writer(connection, task, result):
            self.publication.publish(connection, task, result)
            raise RuntimeError("transaction crash")
        self.control.outbox_writer = failing_writer
        with self.assertRaises(RuntimeError):
            self.control.complete(task, observation(), [])
        with self.control.connect() as connection:
            self.assertEqual("processing", connection.execute("SELECT status FROM ai_control_tasks WHERE task_id=%s", (task["taskId"],)).fetchone()["status"])
            self.assertEqual(0, connection.execute("SELECT COUNT(*) AS n FROM notification_jobs WHERE account_id=%s", (SUBJECT["accountId"],)).fetchone()["n"])

    def test_retirement_preserves_history_and_prevents_direct_enqueue_and_claim(self):
        from digital_twin.infrastructure.transactions.ai_publication import MySQLAIInferenceQueueStore
        old = NotificationJob.create("previous", account_id=SUBJECT["accountId"], message_type="investmentInsight")
        with self.queue.transaction() as connection:
            self.queue.upsert_job_with_connection(connection, old)
        self.assertEqual(1, self.publication.retire_legacy_work()["notifications"])
        self.assertEqual(0, self.publication.retire_legacy_work()["notifications"])
        delivered = NotificationJob.create("archived original", account_id=SUBJECT["accountId"], message_type="investmentInsight")
        delivered.status = "done"
        with self.queue.transaction() as connection:
            self.queue.upsert_job_with_connection(connection, delivered)
        self.assertFalse(self.queue.enqueue(delivered))
        with self.queue.connect() as connection:
            self.assertEqual("done", connection.execute("SELECT status FROM notification_jobs WHERE job_id=%s", (delivered.job_id,)).fetchone()["status"])
        next_job = NotificationJob.create("new legacy attempt", account_id=SUBJECT["accountId"], message_type="investmentInsight")
        self.assertFalse(self.queue.enqueue(next_job))
        self.assertEqual("suppressed", next_job.status)
        legacy = MySQLAIInferenceQueueStore(self.settings)
        self.assertEqual([], legacy.claim("manual"))
        self.assertEqual("retired", legacy.enqueue(next_job, None)["status"])
        self.assertFalse(legacy.complete(None, "manual", None, {}))
