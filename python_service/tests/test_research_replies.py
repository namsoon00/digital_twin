"""Reply lineage, delivery retry isolation and Korea display clock regressions."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import Mock

from stabilization_database import StabilizationDatabaseCase
from test_research_progress import case, event
from test_notification_delivery_reliability import receipt
from digital_twin.infrastructure.transactions.research_progress_publication import ResearchProgressPublication
from digital_twin.modules.ai_orchestration.domain.research_progress import research_progress
from digital_twin.modules.notifications.domain.research_thread import research_thread_key, ResearchDeliveryDeferred
from digital_twin.modules.notifications.domain.display_time import kst_timestamp, notification_times_kst
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
from digital_twin.modules.notifications.application.notification.dispatch import NotificationDispatchService
from digital_twin.modules.notifications.infrastructure.notification.transport import TelegramNotifier, NotificationResult


class ResearchReplyTests(unittest.TestCase):
    def test_reply_survives_format_fallback_chunks_restart_and_target_change(self):
        notifier = TelegramNotifier("123:fake", "456")
        failure = notifier.api_failure({"error_code": 400, "description": "can't parse entities"}, "bad")
        notifier.post_message = Mock(side_effect=[failure, receipt("42")])
        notifier.send_resumable("<b>result</b>", reply_to_message_id="10")
        for call in notifier.post_message.call_args_list:
            self.assertEqual({"message_id": 10, "allow_sending_without_reply": True}, call.args[0]["reply_parameters"])
        saved = {}
        body = "a" * 2500 + "\n" + "b" * 2500
        notifier.post_message = Mock(side_effect=[receipt("43"), NotificationResult(False, "fake", "503")])
        self.assertFalse(notifier.send_resumable(body, reply_to_message_id="10", on_checkpoint=saved.update).delivered)
        notifier.post_message = Mock(return_value=receipt("44"))
        self.assertFalse(notifier.send_resumable(body, checkpoint=saved, reply_to_message_id="11").delivered)
        notifier.post_message.assert_not_called()
        self.assertTrue(notifier.send_resumable(body, checkpoint=saved, reply_to_message_id="10").delivered)
        self.assertEqual(1, notifier.post_message.call_count)
        self.assertEqual(10, notifier.post_message.call_args.args[0]["reply_parameters"]["message_id"])

    def test_kst_boundary_rollover_offsets_dates_links_and_frozen_audit(self):
        self.assertEqual("2026-01-01 08:30 KST", kst_timestamp("2025-12-31T23:30:00Z"))
        for value in ("2026-01-01T08:30:00+09:00", "2025-12-31T23:30:00", "2025-12-31 18:30:00-05:00"):
            self.assertEqual("2026-01-01 08:30 KST", kst_timestamp(value))
        text = '<a href="https://example.com/2025-12-31T23:30:00Z">2025-12-31T23:30:00Z</a> https://example.com/2025-12-31T23:30:00Z 2025-12-31 bad 2025-99-99T00:00:00Z'
        rendered = notification_times_kst(text)
        self.assertIn('>2026-01-01 08:30 KST</a>', rendered)
        self.assertEqual(2, rendered.count("https://example.com/2025-12-31T23:30:00Z"))
        self.assertIn("2025-12-31 bad 2025-99-99T00:00:00Z", rendered)
        self.assertEqual(rendered, notification_times_kst(rendered))
        renderer = NotificationRenderingService(now_provider=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
        for kind in ("workHandoff", "newsDigest", "notification"):
            job = NotificationJob.create("발생 2025-12-31T23:30:00Z", message_type=kind)
            self.assertIn("2026-01-01 08:30 KST", renderer.render(job))
            self.assertEqual("Asia/Seoul", job.context["notificationDisplayTimezone"])
            job.context["transportDelivery"] = {"message": "archived 2025-12-31T23:30:00Z"}
            self.assertEqual("archived 2025-12-31T23:30:00Z", renderer.render(job))
        self.assertEqual("2025-12-31 (시각 미기록)", kst_timestamp("2025-12-31"))


class ResearchReplyStorageTests(StabilizationDatabaseCase):
    def job(self, suffix, stage="created", question="thread-question", **updates):
        value = {**case(), "caseId": question, **updates}
        notice = research_progress(value, event(stage, suffix))
        with self.notifications.transaction() as connection:
            result = ResearchProgressPublication(self.notifications, self.settings).publish(connection, notice)
            row = connection.execute("SELECT text,payload_json FROM notification_jobs WHERE job_id=%s", (result["jobId"],)).fetchone()
        return self.notifications.job_from_row(row)

    def test_pending_parent_verified_anchor_restart_hypothesis_and_scope(self):
        question = self.job("reply-question")
        answer = self.job("reply-answer", "assessment", status="answered")
        notifier = TelegramNotifier("123:fake", "456")
        notifier.post_message = Mock(side_effect=[receipt("10"), receipt("11"), receipt("12"), receipt("13")])
        service = NotificationDispatchService(self.notifications, lambda _: notifier)
        with self.assertRaises(ResearchDeliveryDeferred):
            service.deliver(answer, {"account-1": object()}, answer.text)
        notifier.post_message.assert_not_called()
        answer.attempts = 1
        self.notifications.defer_research_delivery(answer, "wait for question")
        self.assertEqual(0, answer.attempts)
        self.assertEqual("pending", answer.status)
        with self.notifications.connect() as connection:
            row = connection.execute("SELECT retry_at FROM notification_jobs WHERE job_id=%s", (answer.job_id,)).fetchone()
        self.assertTrue(row["retry_at"])
        with self.notifications.research_delivery_thread(question, notifier.destination_fingerprint):
            with self.assertRaises(ResearchDeliveryDeferred):
                service.deliver(question, {"account-1": object()}, question.text)
        service.deliver(question, {"account-1": object()}, question.text)
        self.notifications.mark_done(question)
        service.deliver(answer, {"account-1": object()}, answer.text)
        self.assertNotIn("reply_parameters", notifier.post_message.call_args_list[0].args[0])
        self.assertEqual(10, notifier.post_message.call_args.args[0]["reply_parameters"]["message_id"])
        thesis = self.job("reply-thesis", "business-registered", question="thesis", kind="business-thesis", status="tracking", origin={"sourceQuestions": [{"caseId": "thread-question"}]})
        self.assertEqual(research_thread_key(question), research_thread_key(thesis))
        # The small anchor must survive removal of the original job payload.
        with self.notifications.transaction() as connection:
            connection.execute("DELETE FROM notification_jobs WHERE job_id=%s", (question.job_id,))
        service.deliver(thesis, {"account-1": object()}, thesis.text)
        self.assertEqual(10, notifier.post_message.call_args.args[0]["reply_parameters"]["message_id"])
        other = self.job("reply-world", worldId="other-world")
        self.assertNotEqual(research_thread_key(question), research_thread_key(other))
        service.deliver(other, {"account-1": object()}, other.text)
        self.assertNotIn("reply_parameters", notifier.post_message.call_args.args[0])
        notifier.chat_id = "789"
        another = self.job("reply-channel", "assessment", question="thread-question", status="blocked")
        notifier.post_message = Mock(return_value=receipt("90"))
        service.deliver(another, {"account-1": object()}, another.text)
        self.assertNotIn("reply_parameters", notifier.post_message.call_args.args[0])

    def test_legacy_receipt_adoption_and_partial_root_recovery(self):
        question = self.job("legacy-question", question="legacy-case")
        notifier = TelegramNotifier("123:fake", "456")
        # Simulate the receipt format from before reply support.
        question.context["transportDelivery"] = {"checkpoint": {"messageIds": ["20"], "destinationFingerprint": notifier.destination_fingerprint}}
        self.notifications.mark_done(question)
        with self.notifications.transaction() as connection:
            connection.execute("DELETE FROM notification_research_threads WHERE thread_key=%s", (research_thread_key(question),))
        answer = self.job("legacy-answer", "assessment", question="legacy-case", status="answered")
        notifier.post_message = Mock(return_value=receipt("21"))
        NotificationDispatchService(self.notifications, lambda _: notifier).deliver(answer, {"account-1": object()}, answer.text)
        self.assertEqual(20, notifier.post_message.call_args.args[0]["reply_parameters"]["message_id"])
        question = self.job("partial-question", question="partial-case")
        body = "a" * 2500 + "\n" + "b" * 2500
        notifier.post_message = Mock(side_effect=[receipt("30"), NotificationResult(False, "fake", "503")])
        service = NotificationDispatchService(self.notifications, lambda _: notifier)
        with self.assertRaisesRegex(RuntimeError, "503"):
            service.deliver(question, {"account-1": object()}, body)
        notifier.post_message = Mock(return_value=receipt("31"))
        service.deliver(question, {"account-1": object()}, "changed")
        self.assertEqual(1, notifier.post_message.call_count)
        answer = self.job("partial-answer", "assessment", question="partial-case", status="answered")
        service.deliver(answer, {"account-1": object()}, answer.text)
        self.assertEqual(30, notifier.post_message.call_args.args[0]["reply_parameters"]["message_id"])
