import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.notifications import NotificationJob
from digital_twin.infrastructure.sqlite.health import cleanup_old_sqlite_data
from digital_twin.infrastructure.sqlite_notifications import SQLiteNotificationJobStore


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


if __name__ == "__main__":
    unittest.main()
