"""Safety/measurement regressions and a bounded real-MySQL multi-account run."""

from dataclasses import replace
import json
import socket
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from verify_integrated_load import (
    DATABASE_PREFIX,
    LoadConfig,
    Measurements,
    VerificationFailure,
    claim_ai_with_retry,
    isolated_environment,
    local_mysql_only,
    percentile_summary,
    run_rehearsal,
)


class IntegratedLoadSafetyTests(unittest.TestCase):
    def test_bounds_reject_unbounded_invalid_or_single_account_loads(self):
        config = LoadConfig()
        for change in (
            {"accounts": 1}, {"accounts": 65}, {"concurrency": 17},
            {"concurrency": 5}, {"rounds": 0}, {"rounds": 1001},
            {"max_cases": 7}, {"max_cases": 10001}, {"mode": "production"},
            {"duration_seconds": float("inf")}, {"duration_seconds": float("nan")},
            {"duration_seconds": 3601}, {"recovery_seconds": 0},
            {"wave_interval_seconds": -1},
        ):
            with self.subTest(change=change), self.assertRaises(VerificationFailure):
                replace(config, **change).validate()
        self.assertIs(config, config.validate())

    def test_environment_cannot_redirect_to_real_data_or_inherit_api_secrets(self):
        database = DATABASE_PREFIX + "a" * 24
        environment = isolated_environment(database, "/tmp/load-fixture", {
            "MYSQL_HOST": "127.0.0.1", "MYSQL_DATABASE": "real-account-database",
            "MYSQL_TEST_DATABASE": "other-worker-schema",
            "MYSQL_URL": "mysql://private@remote/real", "DATABASE_URL": "mysql://remote/real",
            "TELEGRAM_BOT_TOKEN": "must-not-inherit", "OPENAI_API_KEY": "must-not-inherit",
            "SETTINGS_PATH": "/private/settings.json", "DIGITAL_TWIN_DATA_DIR": "/private/data",
            "PYTHONPATH": "/unrelated/code",
        })
        self.assertEqual(database, environment["MYSQL_DATABASE"])
        self.assertEqual(database, environment["MYSQL_TEST_DATABASE"])
        self.assertEqual("", environment["MYSQL_URL"])
        self.assertEqual("", environment["DATABASE_URL"])
        self.assertEqual("/tmp/load-fixture", environment["DIGITAL_TWIN_DATA_DIR"])
        self.assertNotIn("TELEGRAM_BOT_TOKEN", environment)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("/unrelated/code", environment["PYTHONPATH"])

    def test_remote_server_and_existing_schema_names_are_rejected_before_connecting(self):
        for database in ("orbit_alpha", "orbit_alpha_test", DATABASE_PREFIX + "bad`sql", ""):
            with self.subTest(database=database), self.assertRaises(VerificationFailure):
                isolated_environment(database, "/tmp/load-fixture", {})
        for source in ({"MYSQL_HOST": "db.example.invalid"}, {"MYSQL_PORT": "0"}, {"MYSQL_UNIX_SOCKET": "relative.sock"}):
            with self.subTest(source=source), self.assertRaises(VerificationFailure):
                isolated_environment(DATABASE_PREFIX + "b" * 24, "/tmp/load-fixture", source)

    def test_guard_blocks_external_sockets_subprocesses_and_foreign_schema(self):
        import pymysql

        environment = isolated_environment(DATABASE_PREFIX + "c" * 24, "/tmp/load-fixture", {})
        with local_mysql_only(environment) as blocked:
            with socket.socket() as connection:
                with self.assertRaises(VerificationFailure):
                    connection.connect(("203.0.113.1", 443))
                with self.assertRaises(VerificationFailure):
                    connection.connect_ex(("127.0.0.1", 1739))
            with self.assertRaises(VerificationFailure):
                subprocess.Popen(["not-launched"])
            with self.assertRaises(VerificationFailure):
                pymysql.connect(database="orbit_alpha")
            with self.assertRaises(VerificationFailure):
                socket.getaddrinfo("example.invalid", 443)
        self.assertEqual({"socket": 3, "schema": 1, "process": 1}, blocked)

    def test_percentiles_use_exact_nearest_rank_and_do_not_invent_empty_latency(self):
        summary = percentile_summary(range(1, 101))
        self.assertEqual({"count": 100, "p50Ms": 50, "p95Ms": 95, "p99Ms": 99, "maxMs": 100}, summary)
        self.assertIsNone(percentile_summary([])["p95Ms"])
        self.assertEqual(7, percentile_summary([7])["p99Ms"])

    def test_ai_claim_deadlock_retry_is_counted_but_connection_loss_is_not_retried(self):
        import pymysql

        store = MagicMock(runtime_settings={"mysqlDeadlockRetryCount": "3"})
        store.claim.side_effect = [pymysql.err.OperationalError(1213, "synthetic deadlock"), []]
        measurements = Measurements()
        self.assertEqual([], claim_ai_with_retry(store, "fixture-worker", measurements))
        self.assertEqual(2, store.claim.call_count)
        self.assertEqual(1, measurements.counters["aiClaimDeadlockRetries"])
        self.assertEqual(2, measurements.counters["aiClaimTransactionAttempts"])
        store.claim.reset_mock(side_effect=True)
        store.claim.side_effect = pymysql.err.OperationalError(2013, "synthetic lost acknowledgement")
        with self.assertRaises(pymysql.err.OperationalError):
            claim_ai_with_retry(store, "fixture-worker", measurements)
        self.assertEqual(1, store.claim.call_count)

    def test_schema_collision_never_drops_an_existing_database(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = RuntimeError("existing schema")
        with patch("verify_integrated_load.admin_connection", return_value=connection) as connect:
            with patch("verify_integrated_load.subprocess.run") as worker:
                report = run_rehearsal(LoadConfig(), environment={})
        self.assertEqual("failed", report["status"])
        self.assertEqual(1, connect.call_count)
        self.assertEqual(1, cursor.execute.call_count)
        self.assertNotIn("IF NOT EXISTS", cursor.execute.call_args.args[0])
        worker.assert_not_called()

    def test_owned_child_timeout_still_drops_only_its_unique_schema(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (0,)
        with patch("verify_integrated_load.admin_connection", return_value=connection):
            with patch("verify_integrated_load.subprocess.run", side_effect=subprocess.TimeoutExpired("fixture", 1)):
                report = run_rehearsal(LoadConfig(), environment={})
        self.assertEqual("failed", report["status"])
        self.assertTrue(report["timedOut"])
        self.assertTrue(report["cleanupVerified"])
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual("DROP DATABASE IF EXISTS `" + report["isolatedDatabase"] + "`", statements[1])
        self.assertNotIn("password", json.dumps(report).lower())


class IntegratedLoadMySQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Not skipped or replaced with an in-memory queue if local MySQL is absent.
        cls.report = run_rehearsal(LoadConfig(accounts=3, concurrency=2, rounds=2, max_cases=100))
        if cls.report["status"] != "passed":
            raise AssertionError(json.dumps(cls.report, sort_keys=True))

    def test_real_multiaccount_counts_and_account_lineage_match(self):
        report = self.report
        self.assertTrue(report["cleanupVerified"])
        self.assertEqual(2, report["wavesCompleted"])
        self.assertEqual(6, report["counters"]["completedCases"])
        counts = report["countConsistency"]
        self.assertTrue(counts["matched"])
        self.assertEqual(counts["expected"], counts["actual"])
        self.assertEqual(19, counts["sourceEventCount"])
        self.assertEqual(37, counts["actual"]["reasoning_engine_job_sources"])
        self.assertEqual(6, counts["joinedOutcomeAnchors"])
        self.assertEqual(0, counts["accountViolations"])
        self.assertEqual({"completed": 6, "superseded": 12, "failed": 1}, counts["reasoningStates"])

    def test_expiry_reclaim_retry_rollback_and_terminal_fences_were_exercised(self):
        counters = self.report["counters"]
        for key in ("expiredClaimsReclaimed", "retryScheduled", "publicationRollbackVerified", "sentinelIsolationVerified"):
            self.assertEqual(2, counters[key], key)
        self.assertEqual(1, counters["sourceRollbackVerified"])
        self.assertEqual(1, counters["terminalExhaustionVerified"])
        self.assertEqual(104, counters["lateTransitionsRejected"])
        self.assertEqual(13, counters["lateReleaseBindingsRejected"])
        self.assertEqual(6, counters["receiptRepairs"])
        self.assertEqual(36, counters["sourceDuplicateReplays"])
        self.assertEqual(12, counters["coalescedSourceReplays"])
        interrupted = [row for row in self.report["backlogSamples"] if row["phase"] == "interrupted"]
        self.assertEqual([2, 2], [row["reasoningPending"] for row in interrupted])
        self.assertEqual(0, self.report["backlogSamples"][-1]["reasoningPending"])

    def test_latency_memory_and_scope_are_explicit_not_live_capacity_claims(self):
        report = self.report
        for key in ("sourceEventIngress", "reasoningClaim", "reasoningCompleteWithReceipt", "aiPublicationTransaction", "sourceToOutcome", "expiredClaimRecovery"):
            latency = report["latency"][key]
            self.assertGreater(latency["count"], 0)
            self.assertLessEqual(latency["p50Ms"], latency["p95Ms"])
            self.assertLessEqual(latency["p95Ms"], latency["p99Ms"])
            self.assertLessEqual(latency["p99Ms"], latency["maxMs"])
        self.assertEqual(6, report["latency"]["sourceToOutcome"]["count"])
        self.assertIn("pythonCurrentBytes", report["memory"]["deltaBytes"])
        self.assertIn("processRssHighWaterBytes", report["memory"]["deltaBytes"])
        for key in ("typeDBExecuted", "modelExecuted", "externalDeliveryExecuted", "managedProcessesTouched"):
            self.assertFalse(report[key])
        self.assertEqual(0, report["externalRequests"])


if __name__ == "__main__":
    unittest.main()
