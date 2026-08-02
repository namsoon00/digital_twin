import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from digital_twin.domain.message_types import NEWS_DIGEST
from digital_twin.domain.notifications import NotificationJob
from mysql_fixtures import TestNotificationJobStore, mysql_execute, mysql_test_settings, reset_mysql_test_database, test_store_seed


class SentArticleDeliveryLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = mysql_test_settings(self.temp.name)
        self.env_patch = mock.patch.dict(os.environ, {
            "DIGITAL_TWIN_DATA_DIR": self.temp.name,
            "SETTINGS_PATH": str(Path(self.temp.name) / "settings.json"),
            "MYSQL_HOST": settings["mysqlHost"],
            "MYSQL_PORT": settings["mysqlPort"],
            "MYSQL_DATABASE": settings["mysqlDatabase"],
            "MYSQL_USER": settings["mysqlUser"],
            "MYSQL_PASSWORD": settings["mysqlPassword"],
            "MYSQL_UNIX_SOCKET": settings["mysqlUnixSocket"],
        }, clear=False)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        reset_mysql_test_database(self.temp.name)

    @staticmethod
    def context(fact_id="fact:skhynix-wage-proposal"):
        return {
            "messageType": NEWS_DIGEST,
            "newsDigest": {
                "items": [{
                    "kind": "news",
                    "title": '"적자 땐 임금조정"…SK하이닉스 제안',
                    "url": "https://www.hankyung.com/article/2026073186381",
                    "storyClusterId": "story:skhynix-wage-proposal",
                    "factId": fact_id,
                }],
            },
        }

    def test_done_article_remains_suppressed_after_notification_payload_retention(self):
        seed = test_store_seed(self.temp.name)
        queue = TestNotificationJobStore(seed)
        delivered = NotificationJob.create(
            "news delivered",
            account_id="main",
            message_type=NEWS_DIGEST,
            context=self.context(),
        )
        queue.upsert_job(delivered)
        queue.mark_done(delivered)

        mysql_execute(seed, "DELETE FROM notification_jobs WHERE job_id = ?", (delivered.job_id,))
        sent_keys = queue.sent_article_identity_keys("main")
        self.assertTrue(sent_keys)

        repeat = NotificationJob.create(
            "same news",
            account_id="main",
            message_type=NEWS_DIGEST,
            context=self.context(),
        )
        with queue.transaction() as connection:
            blocked = queue.apply_sent_article_filter_with_connection(connection, repeat)

        self.assertTrue(blocked)
        self.assertEqual("suppressed", repeat.status)
        self.assertEqual("sent_article_repeat", repeat.context["deliverySuppressionReason"])

    def test_new_verified_fact_in_the_same_story_is_not_suppressed(self):
        seed = test_store_seed(self.temp.name)
        queue = TestNotificationJobStore(seed)
        delivered = NotificationJob.create(
            "news delivered",
            account_id="main",
            message_type=NEWS_DIGEST,
            context=self.context(),
        )
        queue.upsert_job(delivered)
        queue.mark_done(delivered)

        follow_up = NotificationJob.create(
            "story update",
            account_id="main",
            message_type=NEWS_DIGEST,
            context=self.context("fact:skhynix-wage-vote-date"),
        )
        with queue.transaction() as connection:
            blocked = queue.apply_sent_article_filter_with_connection(connection, follow_up)

        self.assertFalse(blocked)
        self.assertNotEqual("suppressed", follow_up.status)


if __name__ == "__main__":
    unittest.main()
