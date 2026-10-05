import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from digital_twin.modules.notifications.application.notification.query import NotificationTraceQueryService
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.domain.delivery_recovery import (
    delivery_progress,
    notification_failure_retry_at,
    terminal_delivery_recovery_decision,
)
from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore


class FinalDeliveryTraceTests(unittest.TestCase):
    def assert_queue_retry_and_terminal_failure_are_distinct_from_transport_receipts(self):
        now = datetime(2026, 10, 5, 0, 45, tzinfo=timezone.utc)
        job = {"status": "failed", "attempts": 3, "retry_at": "2026-10-05T01:00:46Z",
               "created_at": "2026-10-05T00:29:36Z", "message_type": "aiObservation",
               "last_error": "circuit open until 2026-10-05T01:00:46Z"}
        progress = delivery_progress(job, max_attempts=5, now=now)
        self.assertEqual("retry-wait", progress["deliveryStatus"])
        self.assertEqual(job["retry_at"], progress["deliveryRetryAt"])
        self.assertEqual(3, progress["deliveryAttempts"])
        exhausted = dict(job, attempts=5)
        self.assertEqual("recovery-wait", delivery_progress(exhausted, max_attempts=5, now=now)["deliveryStatus"])
        exhausted["context"] = {"deliveryRecovery": {"count": 1}}
        self.assertEqual("failed", delivery_progress(exhausted, max_attempts=5, now=now)["deliveryStatus"])
        self.assertEqual("failed", delivery_progress(dict(job, attempts=5, last_error="HTTP 401"), max_attempts=5, now=now)["deliveryStatus"])
        self.assertEqual("expired", delivery_progress(dict(job, attempts=5, created_at="2026-10-04T00:00:00Z"), max_attempts=5, now=now)["deliveryStatus"])
        self.assertEqual("receipt-unconfirmed", delivery_progress({"status": "done"}, max_attempts=5)["deliveryStatus"])
        receipt = {"source": "transport-receipt", "deliveredAt": "2026-10-05T01:00:49Z", "body": "received"}
        confirmed = delivery_progress(job, receipt, max_attempts=5, now=now)
        self.assertEqual("done", confirmed["deliveryStatus"])
        self.assertEqual(receipt, confirmed["receipt"])
        self.assertEqual("", confirmed["deliveryRetryAt"])

    def test_final_suppression_reason_survives_outbox_cleanup(self):
        job = NotificationJob.create("fixture", account_id="fixture", message_type="investmentInsight", context={
            "deliverySuppressionReason": "account_quiet_hours", "investmentSubjectDecisionCaseId": "subject:1",
            "notificationAiQueue": {"requestId": "ai:1"}, "apiKey": "never-retain",
        })
        job.status = "suppressed"
        store = object.__new__(MySQLNotificationJobStore)
        event = store.record_lifecycle_with_connection(Mock(), job, "suppressed", "suppressed", "account quiet hours")
        read_store = SimpleNamespace(lifecycle_for_job=lambda _: [event], delivery_attempts_for_job=lambda _: [])
        trace = NotificationTraceQueryService(read_store).trace_for_job(job.job_id)
        self.assertEqual("account_quiet_hours", trace["finalDelivery"]["reasonCode"])
        self.assertEqual("subject:1", trace["finalDelivery"]["subjectCaseId"])
        self.assertEqual("ai:1", trace["finalDelivery"]["aiRequestId"])
        self.assertEqual("blocked", trace["pipeline"]["stages"][-1]["status"])
        self.assertNotIn("never-retain", str(event))

    def test_legacy_missing_reason_is_not_guessed_from_qualification(self):
        event = {"stage": "suppressed", "createdAt": "2026-09-16T00:00:00Z", "reason": "legacy", "metadata": {}}
        store = SimpleNamespace(lifecycle_for_job=lambda _: [event], delivery_attempts_for_job=lambda _: [])
        trace = NotificationTraceQueryService(store).trace_for_job("legacy")
        self.assertEqual("historical-reason-unrecorded", trace["finalDelivery"]["reasonCode"])

    def test_no_lifecycle_row_does_not_claim_success(self):
        self.assert_queue_retry_and_terminal_failure_are_distinct_from_transport_receipts()
        store = SimpleNamespace(lifecycle_for_job=lambda _: [], delivery_attempts_for_job=lambda _: [])
        trace = NotificationTraceQueryService(store).trace_for_job("missing")
        self.assertEqual("unknown", trace["finalDelivery"]["state"])
        self.assertEqual("missing", trace["pipeline"]["stages"][-1]["status"])

    def test_persisted_failure_is_still_visible_after_attempt_cleanup(self):
        event = {"stage": "failed", "createdAt": "2026-09-16T00:00:00Z", "reason": "transport failed", "metadata": {}}
        store = SimpleNamespace(lifecycle_for_job=lambda _: [event], delivery_attempts_for_job=lambda _: [])
        trace = NotificationTraceQueryService(store).trace_for_job("failed")
        self.assertEqual("failed", trace["pipeline"]["stages"][-1]["status"])

        now = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
        fresh = NotificationJob.create(
            "fresh",
            message_type="investmentInsight",
            context={},
        ).to_dict()
        fresh.update({
            "attempts": 5,
            "createdAt": (now - timedelta(minutes=20)).isoformat().replace("+00:00", "Z"),
            "lastError": "circuit open until 2026-09-27T03:10:00Z",
        })
        recovery = terminal_delivery_recovery_decision(fresh, now=now)
        self.assertEqual("retry", recovery["action"])
        self.assertEqual("2026-09-27T03:10:00Z", recovery["retryAt"])
        stale = dict(fresh, createdAt="2026-09-26T20:00:00Z")
        self.assertEqual(
            "supersede",
            terminal_delivery_recovery_decision(stale, now=now)["action"],
        )
        self.assertTrue(notification_failure_retry_at("HTTP 503", 2, now=now))

        store = object.__new__(MySQLNotificationJobStore)
        transaction = MagicMock()
        connection = transaction.__enter__.return_value
        transaction.__exit__.return_value = False
        store.transaction = Mock(return_value=transaction)

        def execute(sql, params=()):
            cursor = MagicMock()
            cursor.fetchall.return_value = []
            if "WHERE status = 'pending'" in sql:
                self.assertIn("retry_at = '' OR retry_at <= %s", sql)
                self.assertTrue(str(params[0]).endswith("Z"))
            return cursor

        connection.execute.side_effect = execute
        self.assertEqual([], store.claim_pending(limit=1))
