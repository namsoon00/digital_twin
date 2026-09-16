import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from digital_twin.modules.notifications.application.notification.query import NotificationTraceQueryService
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore


class FinalDeliveryTraceTests(unittest.TestCase):
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
        store = SimpleNamespace(lifecycle_for_job=lambda _: [], delivery_attempts_for_job=lambda _: [])
        trace = NotificationTraceQueryService(store).trace_for_job("missing")
        self.assertEqual("unknown", trace["finalDelivery"]["state"])
        self.assertEqual("missing", trace["pipeline"]["stages"][-1]["status"])

    def test_persisted_failure_is_still_visible_after_attempt_cleanup(self):
        event = {"stage": "failed", "createdAt": "2026-09-16T00:00:00Z", "reason": "transport failed", "metadata": {}}
        store = SimpleNamespace(lifecycle_for_job=lambda _: [event], delivery_attempts_for_job=lambda _: [])
        trace = NotificationTraceQueryService(store).trace_for_job("failed")
        self.assertEqual("failed", trace["pipeline"]["stages"][-1]["status"])
