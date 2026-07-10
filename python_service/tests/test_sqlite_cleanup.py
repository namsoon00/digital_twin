import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.events import DomainEvent
from digital_twin.domain.notifications import NotificationJob
from digital_twin.infrastructure.sqlite.health import cleanup_old_sqlite_data
from digital_twin.infrastructure.sqlite_monitoring import SQLiteEventLog
from digital_twin.infrastructure.sqlite_notifications import SQLiteNotificationJobStore
from digital_twin.infrastructure.sqlite_runtime import SQLiteAppStore


class SQLiteCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env_patch = mock.patch.dict(os.environ, {
            "DIGITAL_TWIN_DATA_DIR": self.temp.name,
            "SETTINGS_PATH": str(Path(self.temp.name) / "settings.json"),
        }, clear=False)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_cleanup_deletes_old_finished_rows_and_protects_pending(self):
        db_path = Path(self.temp.name) / "service.db"
        queue = SQLiteNotificationJobStore(db_path)
        old_at = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat().replace("+00:00", "Z")
        recent_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        old_done = NotificationJob.create("오래된 완료", account_id="main", message_type="notification")
        old_done.status = "done"
        old_done.created_at = old_at
        old_done.updated_at = old_at
        old_pending = NotificationJob.create("오래된 대기", account_id="main", message_type="notification")
        old_pending.status = "pending"
        old_pending.created_at = old_at
        old_pending.updated_at = old_at
        recent_done = NotificationJob.create("최근 완료", account_id="main", message_type="notification")
        recent_done.status = "done"
        recent_done.created_at = recent_at
        recent_done.updated_at = recent_at
        queue.upsert_job(old_done)
        queue.upsert_job(old_pending)
        queue.upsert_job(recent_done)

        result = cleanup_old_sqlite_data(db_path, retention_days=7, archive_old_data=True, max_delete_per_table=100)

        self.assertEqual(1, result["tables"]["notificationJobs"]["deleted"])
        archive_path = result["tables"]["notificationJobs"]["archivePath"]
        self.assertTrue(Path(archive_path).exists())
        jobs = {job.text: job.status for job in queue.jobs()}
        self.assertNotIn("오래된 완료", jobs)
        self.assertEqual("pending", jobs["오래된 대기"])
        self.assertEqual("done", jobs["최근 완료"])

    def test_cleanup_compacts_app_store_and_creates_query_indexes(self):
        db_path = Path(self.temp.name) / "service.db"
        store = SQLiteAppStore(db_path)
        store.replace({
            "profile": {"ownerName": "Namsoon"},
            "messages": [
                {"id": "msg-" + str(index), "createdAt": "2026-07-10T00:%02d:00Z" % (index % 60)}
                for index in range(150)
            ],
            "items": [
                {"id": "item-" + str(index), "createdAt": "2026-07-10T00:%02d:00Z" % (index % 60)}
                for index in range(620)
            ],
            "memories": [
                {"id": "mem-" + str(index), "createdAt": "2026-07-10T00:%02d:00Z" % (index % 60)}
                for index in range(540)
            ],
        })

        result = cleanup_old_sqlite_data(db_path, retention_days=7, archive_old_data=False, max_delete_per_table=100)

        self.assertEqual(190, result["tables"]["appStore"]["deleted"])
        compacted = SQLiteAppStore(db_path).load()
        self.assertEqual(120, len(compacted["messages"]))
        self.assertEqual("msg-30", compacted["messages"][0]["id"])
        self.assertEqual(500, len(compacted["items"]))
        self.assertEqual("item-0", compacted["items"][0]["id"])
        self.assertEqual(500, len(compacted["memories"]))
        with sqlite3.connect(str(db_path)) as connection:
            index_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }
        self.assertIn("idx_notification_jobs_status_created", index_names)
        self.assertIn("idx_domain_events_occurred_at", index_names)
        self.assertIn("idx_market_quote_cache_account_updated", index_names)

    def test_event_log_summary_queries_do_not_replay_full_stream(self):
        db_path = Path(self.temp.name) / "service.db"
        event_log = SQLiteEventLog(db_path, legacy_path=Path(self.temp.name) / "missing.jsonl")
        event_log.handle(DomainEvent(name="alpha", aggregate_id="a", payload={"index": 1}, occurred_at="2026-07-10T00:00:00Z", event_id="e1"))
        event_log.handle(DomainEvent(name="beta", aggregate_id="b", payload={"index": 2}, occurred_at="2026-07-10T00:01:00Z", event_id="e2"))
        event_log.handle(DomainEvent(name="alpha", aggregate_id="a", payload={"index": 3}, occurred_at="2026-07-10T00:02:00Z", event_id="e3"))

        self.assertEqual({"alpha": 2, "beta": 1}, event_log.event_counts())
        latest = event_log.latest_events(limit=2)
        self.assertEqual(["beta", "alpha"], [event.name for event in latest])
        latest_by_name = event_log.latest_events_by_name(["alpha", "beta"])
        self.assertEqual(3, latest_by_name["alpha"].payload["index"])
        self.assertEqual(2, latest_by_name["beta"].payload["index"])


if __name__ == "__main__":
    unittest.main()
