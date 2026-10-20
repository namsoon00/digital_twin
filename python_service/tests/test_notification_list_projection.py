"""Inbox reads must be independent of evidence packet size and keep writer parity."""
import json
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.infrastructure.list_projection import (
    backfill_list_presentations, presentation_json,
)
from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore
from digital_twin.modules.notifications.infrastructure.transaction_writes import set_ai_delivery_status


class NotificationListProjectionTests(unittest.TestCase):
    def test_projection_preserves_identity_and_metadata_precedence_without_evidence(self):
        context = {
            "notificationWriterProvenance": {"aiAuthored": False},
            "notificationContent": {"summary": "", "kind": None},
            "metadata": {"notificationWriterProvenance": {"aiAuthored": True},
                         "notificationContent": {"kind": "news", "summary": "old"},
                         "customerInvestmentDocument": {"headline": "saved", "sections": ["x" * 1000000]}},
            "rawSymbol": "null", "unrelated": "x" * 1000000,
        }
        encoded = presentation_json(context)
        result = json.loads(encoded)
        self.assertFalse(result["notificationWriterProvenance.aiAuthored"])
        self.assertEqual("", result["notificationContent.summary"])
        self.assertEqual("news", result["notificationContent.kind"])
        self.assertEqual("saved", result["customerInvestmentDocument.headline"])
        self.assertNotIn("rawSymbol", result)
        self.assertLess(len(encoded), 1000)
        self.assertEqual("{}", presentation_json(None))

    def test_list_query_never_reads_or_parses_evidence_payload(self):
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = {"count": 0}
        connection.execute.return_value.fetchall.return_value = []
        store = object.__new__(MySQLNotificationJobStore)
        store.connect = lambda: nullcontext(connection)
        self.assertEqual(([], 0, {}), store.recent_list_page_with_summary(recipient_id="local-owner"))
        sql = "\n".join(call.args[0] for call in connection.execute.call_args_list)
        self.assertIn("list_presentation_json", sql)
        self.assertNotIn("payload_json", sql)
        self.assertNotIn("JSON_TABLE", sql)

    def test_both_insert_and_update_save_projection_with_the_original_payload(self):
        store = object.__new__(MySQLNotificationJobStore)
        job = NotificationJob.create("body", account_id="demo", message_type="newsDigest",
                                     context={"notificationContent": {"kind": "news", "summary": "authored"}})
        for rowcount in (0, 1):
            with self.subTest(rowcount=rowcount):
                connection = Mock()
                connection.execute.return_value.rowcount = rowcount
                store.upsert_job_with_connection(connection, job)
                writes = [call for call in connection.execute.call_args_list if "list_presentation_json" in call.args[0]]
                self.assertEqual(2 if rowcount == 0 else 1, len(writes))
                for call in writes:
                    sql, params = call.args
                    self.assertEqual(sql.count("%s"), len(params))
                    self.assertIn(presentation_json(job.context), params)
                    self.assertTrue(any(isinstance(value, str) and '"context"' in value for value in params))

    def test_ai_transaction_replaces_projection_atomically(self):
        job = NotificationJob.create("body", context={"notificationContent": {"kind": "notice"}})
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = {"status": "awaiting_ai"}
        connection.execute.return_value.rowcount = 1
        self.assertTrue(set_ai_delivery_status(
            connection, job.job_id, "pending", "", {"notificationContent": {"kind": "news"}}, None, None, 0,
            _bound__clean=lambda value: str(value or ""),
            _bound__compact_notification_payload=lambda item: item.to_dict(),
            _bound__notification_job_from_row=lambda row: job,
        ))
        sql, params = connection.execute.call_args.args
        self.assertIn("list_presentation_json = %s", sql)
        self.assertEqual("news", json.loads(params[-2])["notificationContent.kind"])
        self.assertEqual(sql.count("%s"), len(params))

    def test_backfill_is_resumable_and_does_not_overwrite_a_newer_writer(self):
        rows = [{"job_id": "old", "updated_at": "stamp"}, {"job_id": "changed", "updated_at": "stamp"}]
        packet = json.dumps({"context": {"notificationContent": {"kind": "news"}}, "text": "original"})
        connection = Mock()
        connection.execute.side_effect = [
            SimpleNamespace(fetchall=lambda: rows),
            SimpleNamespace(fetchone=lambda: {"payload_json": packet}),
            SimpleNamespace(rowcount=1),
            SimpleNamespace(fetchone=lambda: None),
        ]
        self.assertEqual(1, backfill_list_presentations(connection))
        sql, params = connection.execute.call_args_list[2].args
        self.assertIn("AND updated_at = %s AND list_presentation_json IS NULL", sql)
        self.assertNotIn("payload_json =", sql)
        self.assertNotIn("updated_at =", sql.split("WHERE")[0])
        self.assertEqual(("old", "stamp"), params[1:])


if __name__ == "__main__":
    unittest.main()
