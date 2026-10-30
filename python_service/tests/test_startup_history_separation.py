"""Startup must not scan history; bounded repair retains exact legacy state."""

from contextlib import contextmanager, nullcontext, ExitStack
import unittest
from unittest.mock import Mock, patch

from digital_twin.infrastructure import mysql_operational_connection as database
from digital_twin.modules.decisions.infrastructure.legacy_flow_projection import MySQLLegacyDecisionFlowRepairStore
from digital_twin.modules.notifications.infrastructure.legacy_receipt_repair import MySQLLegacyDecisionReceiptRepairStore
from stabilization_database import StabilizationDatabaseCase
from test_internal_decision_history import episode


class StartupHistorySeparationTests(unittest.TestCase):
    def test_tuning_does_not_replay_comparisons_notifications_or_external_facts(self):
        from digital_twin.infrastructure import mysql_schema_tuning as tuning
        with ExitStack() as stack:
            for name in ("ensure_mysql_columns", "ensure_reasoning_rule_slot_namespace_primary_key",
                         "ensure_mysql_indexes", "retire_mysql_unique_indexes", "ensure_mysql_column_compatibility",
                         "ensure_mysql_column_widths", "retire_mysql_columns", "ensure_mysql_key_partitions"):
                stack.enter_context(patch.object(tuning, name, return_value=[]))
            comparison = stack.enter_context(patch.object(tuning, "backfill_reasoning_comparison_identity", side_effect=AssertionError("history")))
            external = stack.enter_context(patch.object(tuning, "backfill_external_fact_revision_identities", side_effect=AssertionError("history")))
            presentation = stack.enter_context(patch("digital_twin.modules.notifications.infrastructure.list_projection.backfill_list_presentations", side_effect=AssertionError("history")))
            result = tuning.ensure_mysql_operational_schema_tuning(Mock())
            self.assertEqual("deferred-to-bounded-maintenance", result["historicalBackfillsStatus"])
            for method in (comparison, external, presentation):
                method.assert_not_called()

    def test_bootstrap_keeps_ddl_and_control_seeds_without_reading_history(self):
        store = object.__new__(database.MySQLOperationalConnection)
        store.runtime_settings = {}
        store.schema_key = lambda: ("fixture",)
        connection = Mock()
        store.transaction = lambda: nullcontext(connection)
        with patch.object(database.MySQLOperationalConnection, "_schema_ready", set()), \
             patch.object(database, "ensure_mysql_operational_schema_tuning"), \
             patch("digital_twin.modules.outcomes.infrastructure.prepared_input_schema.ensure_prepared_input_schema") as prepared_schema, \
             patch("digital_twin.infrastructure.mysql_snapshot_storage.compression_supported", return_value=False):
            store.ensure_schema()
            prepared_schema.assert_called_once_with(connection)
            executed = [call.args[0] for call in connection.execute.call_args_list]
            self.assertFalse(any(database.historical_bootstrap_statement(sql) for sql in executed))
            self.assertTrue(any("INSERT IGNORE INTO reasoning_engine_control" in sql for sql in executed))
            self.assertTrue(any("CREATE TABLE IF NOT EXISTS investment_flow_heads" in sql for sql in executed))
            self.assertEqual(3, sum(database.historical_bootstrap_statement(sql) for sql in database.MYSQL_SCHEMA))


class BoundedLegacyRepairTests(StabilizationDatabaseCase):
    def setUp(self):
        super().setUp()
        for table in ("investment_flow_current", "investment_flow_heads", "investment_decision_episodes",
                      "notification_jobs", "decision_notification_receipts", "reasoning_engine_comparisons", "service_runtime_checkpoints"):
            self.sql("DELETE FROM " + table)

    def test_comparison_cursor_bounds_json_work_and_revisits_insertions_behind_it(self):
        from digital_twin.infrastructure.mysql_schema_tuning import backfill_reasoning_comparison_identity
        def insert(key):
            self.sql("INSERT INTO reasoning_engine_comparisons (comparison_id,baseline_deployment_id,candidate_deployment_id,payload_json,created_at,updated_at) "
                     "VALUES (%s,'old','new',%s,'2026-09-10T14:00:00Z','2026-09-10T14:00:00Z')",
                     (key, '{"sourceInput":{"baselineJobId":"b","candidateJobId":"c","contractVersion":"fixture"}}'))
        insert("b"); insert("c")
        with self.jobs.connect() as connection:
            self.assertEqual(1, backfill_reasoning_comparison_identity(connection, limit=1))
            insert("a")
            self.assertEqual(1, backfill_reasoning_comparison_identity(connection, limit=1))
            self.assertEqual(0, backfill_reasoning_comparison_identity(connection, limit=1))
            self.assertEqual(1, backfill_reasoning_comparison_identity(connection, limit=1))
        rows = self.sql("SELECT baseline_job_id,candidate_job_id,comparison_contract_version FROM reasoning_engine_comparisons", all_rows=True)
        self.assertTrue(all(row == {"baseline_job_id":"b", "candidate_job_id":"c", "comparison_contract_version":"fixture"} for row in rows))

    def legacy_episodes(self):
        for key, stamp, account in (("a-old", "2026-09-10T12:00:00Z", "fixture"),
                                     ("b-new", "2026-09-10T14:00:00Z", "fixture"),
                                     ("c-other", "2026-09-10T15:00:00Z", "other")):
            value = episode(key)
            value.decided_at, value.account_id = stamp, account
            self.decisions.save(value)
        self.sql("DELETE FROM investment_flow_current")
        self.sql("DELETE FROM investment_flow_heads")

    def test_partial_batch_cannot_publish_an_old_decision_as_current(self):
        self.legacy_episodes()
        store = MySQLLegacyDecisionFlowRepairStore(self.settings)
        self.assertEqual(1, store.repair(limit=1)["headCount"])
        self.assertIsNone(self.sql("SELECT * FROM investment_flow_current"))
        self.assertEqual(1, store.repair(limit=1)["currentCount"])
        self.assertEqual("b-new", self.sql("SELECT decision_episode_id FROM investment_flow_current WHERE account_id='fixture'")["decision_episode_id"])
        store.repair(limit=1)
        self.assertEqual("c-other", self.sql("SELECT decision_episode_id FROM investment_flow_current WHERE account_id='other'")["decision_episode_id"])
        self.assertEqual(0, store.repair()["headCount"])

    def test_existing_current_decision_is_never_overwritten_by_legacy_repair(self):
        older, newer = episode("older"), episode("newer")
        older.decided_at = "2026-09-10T12:00:00Z"
        self.decisions.save(older)
        self.decisions.save(newer)
        expected = self.sql("SELECT * FROM investment_flow_current")
        self.sql("DELETE FROM investment_flow_heads WHERE decision_episode_id='older'")
        result = MySQLLegacyDecisionFlowRepairStore(self.settings).repair(limit=1)
        self.assertEqual(1, result["headCount"])
        self.assertEqual(expected, self.sql("SELECT * FROM investment_flow_current"))

    def test_repair_failure_rolls_back_every_head_in_the_batch(self):
        self.legacy_episodes()
        store = MySQLLegacyDecisionFlowRepairStore(self.settings)
        original = store.transaction
        @contextmanager
        def failing_transaction():
            with original() as connection:
                execute = connection.execute
                inserted = 0
                def fault(sql, params=None):
                    nonlocal inserted
                    result = execute(sql, params)
                    if sql.lstrip().startswith("INSERT IGNORE INTO investment_flow_heads"):
                        inserted += 1
                        if inserted == 2:
                            raise RuntimeError("injected repair failure")
                    return result
                connection.execute = fault
                yield connection
        with patch.object(store, "transaction", failing_transaction):
            with self.assertRaisesRegex(RuntimeError, "injected repair failure"):
                store.repair(limit=2)
        self.assertIsNone(self.sql("SELECT * FROM investment_flow_heads"))
        self.assertIsNone(self.sql("SELECT * FROM investment_flow_current"))

    def test_receipts_are_bounded_idempotent_and_do_not_replace_existing_delivery(self):
        for number in range(3):
            self.sql("INSERT INTO notification_jobs (job_id, decision_episode_id, created_at, last_error, text, payload_json) "
                     "VALUES (%s, %s, '2026-09-10T14:00:00Z', '', '', '{}')", ("job:"+str(number), "episode:"+str(number)))
        store = MySQLLegacyDecisionReceiptRepairStore(self.settings)
        self.assertEqual(1, store.repair(limit=1)["receiptCount"])
        self.sql("UPDATE decision_notification_receipts SET status='delivered' WHERE job_id='job:0'")
        for _ in range(2):
            self.assertEqual(1, store.repair(limit=1)["receiptCount"])
        self.assertEqual(0, store.repair(limit=1)["receiptCount"])
        self.assertEqual("delivered", self.sql("SELECT status FROM decision_notification_receipts WHERE job_id='job:0'")["status"])
