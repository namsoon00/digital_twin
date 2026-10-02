"""Cutover, grounded publication and receipt-backed delivery regressions."""
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from digital_twin.modules.ai_orchestration.domain.planning import identity, stamp, validate_plan
from digital_twin.modules.ai_orchestration.domain.publication import publication_block, repeat_block
from digital_twin.modules.ai_orchestration.domain.observation_diagnostic import observation_diagnostic
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
from digital_twin.infrastructure.transactions.ai_control_publication import AIControlPublication, validate_narrative
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.domain.delivery_suppression import NotificationDeliverySuppressed
from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
from digital_twin.modules.notifications.application.notification.dispatch import NotificationDispatchService
from digital_twin.modules.notifications.application.ai_observation_diagnostic import render_ai_observation_diagnostic
from digital_twin.modules.notifications.domain.message_types import AI_OBSERVATION_DIAGNOSTIC
from test_ai_control import SUBJECT, PLAN


from ai_insight_fixtures import observation, persist_review


class CentralPublicationPolicyTests(unittest.TestCase):
    def test_silence_stays_internal_but_rejected_send_drafts_are_diagnostic(self):
        result = observation()
        self.assertEqual("", publication_block(result))
        self.assertEqual("", validate_narrative(result))
        for key, value in (("summary", "내일은 900원에 도달합니다."), ("hypothesis", "지금 매수하세요.")):
            self.assertTrue(validate_narrative({**result, key: value}))
        compared = {**result, "comparison": "지난 관찰의 102원보다 낮아졌습니다.",
                    "comparisonFacts": [{"id": "past-quote", "currentPrice": 102, "sourceAsOf": "2026-09-01T00:00:00Z"}]}
        self.assertTrue(validate_narrative(compared))
        result["notification"]["send"] = False
        self.assertTrue(publication_block(result))
        self.assertEqual({}, observation_diagnostic(result, "task", publication_block(result)))
        result["notification"]["send"] = True
        result["input"]["facts"][0]["sourceAsOf"] = "2000-01-01T00:00:00Z"
        self.assertTrue(publication_block(result))
        result["summary"] = '내일은 900원입니다. <b>확정</b>'
        result["quality"] = local_quality(result)
        diagnostic = observation_diagnostic(result, "task", publication_block(result))
        self.assertEqual(result["summary"], diagnostic["draft"]["summary"])
        rendered = render_ai_observation_diagnostic(diagnostic, debug_number="N-TEST")
        self.assertIn("검증 미통과 초안", rendered)
        self.assertIn("현재가: 100원", rendered)
        self.assertIn("900원입니다. &lt;b&gt;확정&lt;/b&gt;", rendered)
        self.assertIn("01/01 09:00 KST", rendered)
        self.assertIn("오차단", rendered)
        self.assertEqual({}, observation_diagnostic(result, "task", ""))
        for malformed_review in ("invalid JSON contract", {"sections": ["invalid section"]}):
            result["quality"]["review"] = malformed_review
            self.assertIn("900원", render_ai_observation_diagnostic(observation_diagnostic(result, "task", "검토 응답 형식 오류")))
        from digital_twin.modules.accounts.contracts import AccountConfig
        account = AccountConfig("test", "test", "toss", "", "", "", "", [])
        night = datetime(2026, 10, 1, 14, tzinfo=timezone.utc)
        self.assertTrue(account.quiet_hours_active(night, "aiObservation"))
        self.assertFalse(account.quiet_hours_active(night, AI_OBSERVATION_DIAGNOSTIC))

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
        latest = {**receipt, "inputFingerprint": "last", "insightFingerprint": "opposite-state",
                  "deliveredAt": (now - timedelta(days=1)).isoformat()}
        older = {**latest, "inputFingerprint": "older", "insightFingerprint": result["quality"]["insightFingerprint"],
                 "deliveredAt": (now - timedelta(days=2)).isoformat()}
        # A return to an older state after a different delivered explanation is
        # eligible for review; only a repeat of the latest meaning is silent.
        self.assertFalse(repeat_block(result, [older, latest], SUBJECT["accountId"], "TEST", now))
        self.assertTrue(repeat_block(result, [older], SUBJECT["accountId"], "TEST", now))

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

    def publish(self, result=None, expected="queued"):
        self.control.seed(SUBJECT)
        task = self.control.claim()
        result = result or observation()
        persist_review(self.control, task, result)
        self.assertTrue(self.control.complete(task, result, []))
        self.assertEqual(expected, result["publication"]["status"], result.get("publication"))
        publication = result["publication"] if expected == "queued" else result["publication"]["diagnostic"]
        with self.queue.connect() as connection:
            row = connection.execute("SELECT text,payload_json FROM notification_jobs WHERE job_id=%s", (publication["jobId"],)).fetchone()
        return task, result, self.queue.job_from_row(row)

    def test_successful_send_is_only_baseline_and_cannot_be_replayed(self):
        task, result, job = self.publish()
        self.assertEqual({}, self.publication.memory(job.account_id, "TEST"))
        renderer = NotificationRenderingService(context_enricher=Mock(side_effect=AssertionError("must not enrich")))
        message = renderer.render(job)
        self.assertIn("현재가: 100원", message)
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
        # Simulate a pre-contract receipt that kept only a compact price tuple.
        # The actual completed task remains the only source of missing fields.
        import json
        with self.queue.transaction() as connection:
            row = connection.execute("SELECT metadata_json FROM notification_delivery_attempts WHERE job_id=%s AND status='delivered'", (job.job_id,)).fetchone()
            metadata = json.loads(row['metadata_json'])
            legacy = metadata['aiControlObservation']; legacy.pop('insightVersion')
            legacy['facts'] = [{key: baseline['facts'][0][key] for key in ('id', 'currentPrice', 'sourceAsOf')}]
            connection.execute("UPDATE notification_delivery_attempts SET metadata_json=%s WHERE job_id=%s AND status='delivered'", (json.dumps(metadata), job.job_id))
        restored = self.publication.memory(job.account_id, 'TEST')
        self.assertEqual('KRW', restored['facts'][0]['currency'])
        self.assertEqual(98, restored['facts'][0]['ma5'])
        self.assertEqual(task['taskId'], restored['evidenceRestoration']['taskId'])
        self.assertNotIn('currency', self.publication.receipts(job.account_id, 'TEST')[0]['facts'][0])

    def test_aged_quote_receipt_preserves_capture_and_send_clocks(self):
        from digital_twin.modules.reasoning.contracts import quote_clock_assessment
        result = observation()
        now = datetime.now(timezone.utc)
        result["input"]["capturedAt"] = (now - timedelta(minutes=10)).isoformat()
        result["input"]["facts"][0].update(sourceAsOf=(now - timedelta(minutes=19)).isoformat(), maxAgeMinutes=10)
        result["input"]["quoteAssessment"] = quote_clock_assessment(result["input"]["facts"], result["input"]["capturedAt"])
        task, result, job = self.publish(result)
        message = NotificationRenderingService().render(job)
        self.assertIn("기준 시점 가격: 100원", message)
        self.assertIn("과거 시점 참고 자료", message)
        notifier = Mock(supports_delivery_checkpoints=False)
        notifier.send.return_value = SimpleNamespace(delivered=True, label="test", reason="", metadata={})
        dispatcher = NotificationDispatchService(self.queue, lambda _: notifier, delivery_guard=self.publication.delivery_guard)
        with patch("digital_twin.infrastructure.settings.runtime_settings", return_value=self.settings):
            dispatcher.deliver(job, {job.account_id: object()}, message)
        baseline = self.publication.memory(job.account_id, "TEST")
        self.assertEqual("fresh", baseline["captureQuoteAssessment"]["quotes"][0]["status"])
        self.assertEqual("stale", baseline["deliveryQuoteAssessment"]["quotes"][0]["status"])
        self.assertEqual("fresh", baseline["facts"][0]["freshnessStatus"])
        self.assertEqual("fresh", result["input"]["quoteAssessment"]["quotes"][0]["status"])
        notifier.send.assert_called_once()

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
        baseline = self.publication.memory(job.account_id, "TEST")
        # A separate task produces an unsupported draft. Its operations delivery
        # cannot advance the real customer baseline, limits or follow-up memory.
        with self.control.transaction() as connection:
            self.control.insert(connection, {**SUBJECT, "taskId": "rejected-task", "capability": "observe", "availableAt": stamp()})
        rejected = observation()
        rejected["summary"] = "이 종목은 내일 900원에 도달합니다."
        rejected["repair"] = {"status": "rejected", "initialErrors": ["확인되지 않은 수치"]}
        task, rejected, diagnostic_job = self.publish(rejected, expected="recorded")
        self.assertEqual(AI_OBSERVATION_DIAGNOSTIC, diagnostic_job.message_type)
        account.quiet_hours_active = lambda _now, kind: kind not in {AI_OBSERVATION_DIAGNOSTIC}
        operations = Mock(supports_delivery_checkpoints=False)
        operations.send.return_value = SimpleNamespace(delivered=True, label="test operations", reason="", metadata={})
        operations_factory = Mock(return_value=operations)
        account_factory = Mock(side_effect=AssertionError("unverified draft reached an account transport"))
        renderer = NotificationRenderingService(template_renderer=Mock(side_effect=AssertionError("must preserve failed prose")))
        diagnostic_message = renderer.render(diagnostic_job)
        with self.assertRaises(RuntimeError):
            NotificationDispatchService(self.queue, account_factory).deliver(diagnostic_job, {job.account_id: account}, diagnostic_message)
        with self.assertRaises(NotificationDeliverySuppressed), self.publication.delivery_guard(diagnostic_job, diagnostic_message + "extra"):
            self.fail("tampered diagnostic")
        runner = NotificationQueueRunner(self.queue, SimpleNamespace(load_all=lambda: [account]), account_factory,
            operations_notifier_factory=operations_factory, settings=self.settings,
            include_message_types=[AI_OBSERVATION_DIAGNOSTIC], ai_request_enqueuer=old_ai,
            delivery_guard=self.publication.delivery_guard)
        self.assertEqual(1, runner.run_once())
        account_factory.assert_not_called()
        operations_factory.assert_called_once_with(None)
        operations.send.assert_called_once()
        self.assertIn(rejected["summary"], operations.send.call_args.args[0])
        self.assertIn("한 차례 수정 후에도", operations.send.call_args.args[0])
        self.assertEqual(baseline, self.publication.memory(job.account_id, "TEST"))
        self.assertEqual(1, len(self.publication.receipts(job.account_id)))
        old_ai.enqueue.assert_not_called()
        with self.assertRaises(NotificationDeliverySuppressed), self.publication.delivery_guard(diagnostic_job, diagnostic_message):
            self.fail("duplicate diagnostic")
        import json
        with self.queue.connect() as connection:
            receipt = connection.execute("SELECT channel,metadata_json FROM notification_delivery_attempts WHERE job_id=%s AND status='delivered'", (diagnostic_job.job_id,)).fetchone()
        self.assertEqual("operationsTelegram", receipt["channel"])
        metadata = json.loads(receipt["metadata_json"])
        self.assertNotIn("aiControlObservation", metadata)
        self.assertEqual(task["taskId"], metadata["aiObservationDiagnostic"]["taskId"])

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
        for rejected in (False, True):
            with self.subTest(rejected=rejected), self.assertRaises(RuntimeError):
                result = observation()
                if rejected:
                    result["summary"] = "근거 없는 900원 전망입니다."
                persist_review(self.control, task, result)
                self.control.complete(task, result, [])
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
