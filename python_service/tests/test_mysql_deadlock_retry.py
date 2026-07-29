import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.infrastructure.mysql_operational_connection import (
    MySQLDeadlockRetryExhausted,
    run_mysql_deadlock_retry,
)


class MySQLDeadlockRetryTests(unittest.TestCase):
    def test_retries_deadlock_with_bounded_backoff_then_returns_result(self):
        attempts = []
        delays = []

        def callback():
            attempts.append("run")
            if len(attempts) < 3:
                raise RuntimeError(1213, "Deadlock found when trying to get lock")
            return "saved"

        value, receipt = run_mysql_deadlock_retry(
            {
                "mysqlDeadlockRetryCount": "3",
                "mysqlDeadlockRetryBaseMilliseconds": "20",
                "mysqlDeadlockRetryMaxMilliseconds": "100",
            },
            "research-evidence-upsert",
            callback,
            sleep_fn=lambda seconds: delays.append(seconds),
            random_fn=lambda: 0.5,
        )

        self.assertEqual("saved", value)
        self.assertEqual(3, receipt.attempts)
        self.assertTrue(receipt.recovered)
        self.assertEqual([0.02, 0.04], delays)

    def test_exhausted_deadlock_reports_operation_and_attempts(self):
        attempts = []

        def callback():
            attempts.append("run")
            raise RuntimeError(1213, "Deadlock found when trying to get lock")

        with self.assertRaises(MySQLDeadlockRetryExhausted) as raised:
            run_mysql_deadlock_retry(
                {"mysqlDeadlockRetryCount": "2"},
                "research-evidence-expire-stale-news",
                callback,
                sleep_fn=lambda _seconds: None,
                random_fn=lambda: 0.5,
            )

        self.assertEqual(3, len(attempts))
        self.assertEqual("research-evidence-expire-stale-news", raised.exception.operation)
        self.assertEqual(3, raised.exception.receipt.attempts)

    def test_non_deadlock_error_is_not_retried(self):
        attempts = []

        def callback():
            attempts.append("run")
            raise RuntimeError(1064, "syntax error")

        with self.assertRaises(RuntimeError):
            run_mysql_deadlock_retry(
                {"mysqlDeadlockRetryCount": "3"},
                "research-evidence-upsert",
                callback,
                sleep_fn=lambda _seconds: None,
            )

        self.assertEqual(1, len(attempts))


if __name__ == "__main__":
    unittest.main()
