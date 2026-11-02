"""Native MySQL: frozen requests, cross-account reads, atomic outbox and receipts."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
import unittest
from unittest.mock import patch

from test_ai_portfolio_report import fixture_report
from digital_twin.modules.ai_orchestration.domain.portfolio_report import digest
from digital_twin.modules.ai_orchestration.infrastructure.mysql_portfolio_reports import MySQLPortfolioReportStore
from digital_twin.infrastructure.transactions.ai_report_publication import AIReportPublication


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class PortfolioReportStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mysql_fixtures import mysql_test_settings
        from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore
        cls.settings = mysql_test_settings()
        cls.queue = MySQLNotificationJobStore(cls.settings)
        cls.store = MySQLPortfolioReportStore(cls.settings, initialize=True)
        cls.publish = AIReportPublication(cls.store, lambda: cls.queue)

    def setUp(self):
        self.clean()

    def tearDown(self):
        self.clean()

    def clean(self):
        with self.store.transaction() as connection:
            connection.execute("DELETE FROM notification_delivery_attempts WHERE job_id IN "
                "(SELECT job_id FROM notification_jobs WHERE account_id='report-test' AND message_type='aiReport')")
            connection.execute("DELETE FROM notification_jobs WHERE account_id='report-test' AND message_type='aiReport'")
            connection.execute("DELETE FROM ai_portfolio_reports WHERE account_id='report-test'")

    def test_request_is_immutable_account_scoped_and_retry_safe(self):
        report = fixture_report(); first = self.store.save_once(report)
        changed = deepcopy(report); changed["createdAt"] = "2026-11-03T00:00:00Z"
        changed["contentHash"] = digest({k: v for k, v in changed.items() if k != "contentHash"})
        self.assertEqual(first, self.store.save_once(changed))
        self.assertIsNone(self.store.get("other-account", report["reportId"]))
        self.assertEqual(1, len(self.store.recent("report-test")))
        self.assertEqual([], self.store.recent("other-account"))

    def test_concurrent_send_requests_create_one_outbox_job(self):
        report = self.store.save_once(fixture_report())
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.publish(report), range(2)))
        self.assertEqual(results[0]["jobId"], results[1]["jobId"])
        with self.store.connect() as c:
            self.assertEqual(1, c.execute("SELECT COUNT(*) n FROM notification_jobs WHERE account_id='report-test' AND message_type='aiReport'").fetchone()["n"])
        self.assertEqual(report, self.store.get("report-test", report["reportId"]))

    def test_outbox_failure_rolls_back_pointer_and_job(self):
        report = self.store.save_once(fixture_report())
        original = self.queue.enqueue_with_connection
        def fail(connection, job):
            original(connection, job)
            raise RuntimeError("injected failure after insert")
        with patch.object(self.queue, "enqueue_with_connection", side_effect=fail):
            with self.assertRaises(RuntimeError): self.publish(report)
        self.assertEqual({"status": "not-requested"}, self.publish.status(report))
        with self.store.connect() as c:
            self.assertEqual(0, c.execute("SELECT COUNT(*) n FROM notification_jobs WHERE account_id='report-test' AND message_type='aiReport'").fetchone()["n"])
        self.assertEqual("pending", self.publish(report)["status"])

    def test_completed_job_without_matching_verified_receipt_is_not_delivered(self):
        from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
        report = self.store.save_once(fixture_report())
        status = self.publish(report); job = self.queue.get(status["jobId"])
        body = NotificationRenderingService().render(job)
        artifact = job.context["aiPortfolioReport"]
        audit = {"receiptVerified": True, "destinationVerified": True, "accountId": "other-account",
            "messageType": "aiReport", "deliveryRoute": "account",
            "renderedMessage": body, "messageSha256": hashlib.sha256(body.encode()).hexdigest(),
            "aiPortfolioReport": {k: artifact[k] for k in ("version", "reportId", "reportHash", "summaryHash")}}
        attempt = self.queue.start_delivery_attempt(job, "accountNotification", "account")
        self.queue.complete_delivery_attempt(job, attempt, True, metadata=audit)
        self.assertNotEqual("delivered", self.publish.status(report)["status"])
        audit["accountId"] = "report-test"; audit["destinationVerified"] = False
        self.queue.complete_delivery_attempt(job, attempt, True, metadata=audit)
        self.assertNotEqual("delivered", self.publish.status(report)["status"])
        audit["destinationVerified"] = True
        audit["deliveryRoute"] = "ai-research"
        self.queue.complete_delivery_attempt(job, attempt, True, metadata=audit)
        self.assertNotEqual("delivered", self.publish.status(report)["status"])
        audit["deliveryRoute"] = "account"
        self.queue.complete_delivery_attempt(job, attempt, True, metadata=audit)
        self.assertEqual("delivered", self.publish.status(report)["status"])

    def test_explicit_customer_correction_is_atomic_once_and_preserves_research_receipt(self):
        report = self.store.save_once(fixture_report())
        # A legacy report already delivered by the research bot.
        job = self.queue.get(self.publish(report)["jobId"])
        original_id = job.job_id
        legacy_id = "ai-report-" + report["reportId"][:38]
        with self.store.transaction() as c:
            c.execute("UPDATE notification_jobs SET job_id=%s WHERE job_id=%s", (legacy_id, original_id))
            c.execute("UPDATE ai_portfolio_reports SET notification_job_id=%s WHERE report_id=%s", (legacy_id, report["reportId"]))
        job.job_id = legacy_id
        job.dedupe_key = legacy_id
        self.queue.update(job)
        artifact = job.context["aiPortfolioReport"]
        body = "frozen historical report"
        audit = {"receiptVerified": True, "destinationVerified": True, "accountId": "report-test",
            "messageType": "aiReport", "deliveryRoute": "ai-research", "renderedMessage": body,
            "messageSha256": hashlib.sha256(body.encode()).hexdigest(),
            "aiPortfolioReport": {k: artifact[k] for k in ("version", "reportId", "reportHash", "summaryHash")}}
        attempt = self.queue.start_delivery_attempt(job, "accountNotification", "account")
        self.queue.complete_delivery_attempt(job, attempt, True, metadata=audit)
        # An in-flight/partially delivered legacy job cannot be reset by send.
        self.assertEqual(legacy_id, self.publish(report)["jobId"])
        self.queue.mark_done(job)
        self.assertEqual("other-channel", self.publish.status(report)["status"])
        enqueue = self.queue.enqueue_with_connection
        def fail(connection, new_job):
            enqueue(connection, new_job)
            raise RuntimeError("injected customer correction rollback")
        with patch.object(self.queue, "enqueue_with_connection", side_effect=fail):
            with self.assertRaises(RuntimeError): self.publish(report)
        self.assertEqual(legacy_id, self.publish.status(report)["jobId"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.publish(report), range(2)))
        self.assertEqual(results[0]["jobId"], results[1]["jobId"])
        self.assertNotEqual(legacy_id, results[0]["jobId"])
        corrected = self.queue.get(results[0]["jobId"])
        self.assertEqual(legacy_id, corrected.context["previousDeliveryJobId"])
        self.assertEqual(artifact, corrected.context["aiPortfolioReport"])
        with self.store.connect() as c:
            self.assertEqual(2, c.execute("SELECT COUNT(*) n FROM notification_jobs WHERE account_id='report-test' AND message_type='aiReport'").fetchone()["n"])
            saved = c.execute("SELECT metadata_json FROM notification_delivery_attempts WHERE attempt_id=%s", (attempt,)).fetchone()
        self.assertEqual("ai-research", json.loads(saved["metadata_json"])["deliveryRoute"])
        self.assertEqual(report, self.store.get("report-test", report["reportId"]))
