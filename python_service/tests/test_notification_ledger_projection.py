import unittest

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
        if "GROUP BY status" in sql:
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
            "status": "done",
            "attempts": 1,
            "created_at": "2026-07-28T00:00:00Z",
            "updated_at": "2026-07-28T00:01:00Z",
            "last_error": "",
            "text": "투자 판단 결과",
        }])


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
        statements = [sql.lower() for sql, _params in connection.calls]
        self.assertTrue(all("payload_json" not in sql for sql in statements))
        self.assertTrue(any("source_event_name like" in sql for sql in statements))


if __name__ == "__main__":
    unittest.main()
