import unittest

from digital_twin.domain.notifications import NotificationJob
from digital_twin.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore


class Cursor:
    def __init__(self, one=None, rows=None):
        self.one = one
        self.rows = rows or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


class RecordingConnection:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, list(params or [])))
        if "GROUP BY notification_jobs.status" in sql:
            return Cursor(rows=[{"status": "done", "count": 1}])
        if "COUNT(*)" in sql:
            return Cursor(one={"count": 1})
        return Cursor(rows=[{
            "job_id": "job-1",
            "account_id": "main",
            "account_label": "Main",
            "message_type": "investmentInsight",
            "source_event_id": "event-1",
            "source_event_name": "투자 판단",
            "symbol": "AAPL",
            "decision_episode_id": "episode-1",
            "decision_key": "decision-1",
            "status": "done",
            "attempts": 1,
            "created_at": "2026-07-28T00:00:00Z",
            "updated_at": "2026-07-28T00:01:00Z",
            "last_error": "",
            "text": "투자 판단 결과",
        }])


class ReceiptConnection(RecordingConnection):
    def execute(self, sql, params=()):
        self.calls.append((sql, list(params or [])))
        if sql.startswith("SELECT job_id, read_at"):
            return Cursor(rows=[])
        return Cursor()


class NotificationLedgerProjectionTests(unittest.TestCase):
    def test_list_projection_never_selects_or_searches_audit_payload_json(self):
        store = MySQLNotificationJobStore.__new__(MySQLNotificationJobStore)
        connection = RecordingConnection()
        store.connect = lambda: connection

        jobs, total, summary = store.recent_list_page_with_summary(
            limit=20,
            query="판단",
            scope="all",
        )

        self.assertEqual(1, total)
        self.assertEqual({"done": 1}, summary)
        self.assertEqual("job-1", jobs[0].job_id)
        self.assertEqual("투자 판단 결과", jobs[0].text)
        self.assertEqual("AAPL", jobs[0].context["symbol"])
        self.assertEqual("episode-1", jobs[0].context["investmentDecisionEpisodeId"])
        self.assertEqual("decision-1", jobs[0].context["decisionKey"])
        statements = [sql.lower() for sql, _params in connection.calls]
        self.assertTrue(all("payload_json" not in sql for sql in statements))
        self.assertTrue(any("source_event_name like" in sql for sql in statements))

    def test_cursor_and_unread_filter_use_recipient_receipt_without_payload_scan(self):
        store = MySQLNotificationJobStore.__new__(MySQLNotificationJobStore)
        connection = RecordingConnection()
        store.connect = lambda: connection

        jobs, total, _summary = store.recent_list_page_with_summary(
            limit=20,
            offset=40,
            scope="investment",
            recipient_id="owner-1",
            inbox="unread",
            cursor_updated_at="2026-07-28T00:01:00Z",
            cursor_job_id="job-2",
        )

        self.assertEqual(1, total)
        self.assertEqual("job-1", jobs[0].job_id)
        list_sql, list_params = connection.calls[1]
        self.assertIn("notification_inbox_receipts", list_sql)
        self.assertIn("COALESCE(receipt.read_at, '') = ''", list_sql)
        self.assertIn("notification_jobs.updated_at < %s", list_sql)
        self.assertEqual([20, 0], list_params[-2:])
        self.assertTrue(all("payload_json" not in sql.lower() for sql, _params in connection.calls))

    def test_acknowledging_notification_persists_per_recipient_and_marks_read(self):
        store = MySQLNotificationJobStore.__new__(MySQLNotificationJobStore)
        connection = ReceiptConnection()
        store.connect = lambda: connection

        receipt = store.update_receipt("job-1", "owner-1", acknowledged=True, important=True)

        self.assertEqual("job-1", receipt["jobId"])
        self.assertEqual("owner-1", receipt["recipientId"])
        self.assertTrue(receipt["readAt"])
        self.assertEqual(receipt["readAt"], receipt["acknowledgedAt"])
        self.assertTrue(receipt["important"])
        insert_sql, insert_params = connection.calls[-1]
        self.assertIn("ON DUPLICATE KEY UPDATE", insert_sql)
        self.assertEqual(["owner-1", "job-1"], insert_params[:2])
        self.assertEqual(1, insert_params[4])

    def test_notification_linkage_derives_same_stable_decision_key(self):
        job = NotificationJob.create(
            "Apple 판단 변화",
            account_id="account-1",
            context={"symbol": "AAPL", "investmentDecisionEpisodeId": "episode-aapl-1"},
        )

        linkage = MySQLNotificationJobStore.notification_linkage(job)

        self.assertEqual("AAPL", linkage["symbol"])
        self.assertEqual("episode-aapl-1", linkage["decisionEpisodeId"])
        self.assertTrue(linkage["decisionKey"].startswith("decision:"))


if __name__ == "__main__":
    unittest.main()
