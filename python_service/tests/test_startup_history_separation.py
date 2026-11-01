"""Startup must not scan history; bounded repair retains exact legacy state."""

from contextlib import contextmanager, nullcontext, ExitStack
import unittest
from unittest.mock import Mock, patch

from digital_twin.infrastructure import mysql_operational_connection as database
from digital_twin.modules.decisions.infrastructure.legacy_flow_projection import MySQLLegacyDecisionFlowRepairStore
from digital_twin.modules.notifications.infrastructure.legacy_receipt_repair import MySQLLegacyDecisionReceiptRepairStore
from stabilization_database import StabilizationDatabaseCase
from test_internal_decision_history import episode


class MySQLBootstrapBoundaryTests(unittest.TestCase):
    def test_preinitialized_stores_do_not_open_bootstrap_connections(self):
        from digital_twin.infrastructure import mysql_monitoring as monitoring
        settings = {"mysqlDatabase": "orbit_alpha_test_read_boundary",
                    "_skipOperationalSchemaBootstrap": "1"}
        for store_class in (database.MySQLOperationalConnection, monitoring.MySQLMonitorAccountJobStore):
            with self.subTest(store=store_class.__name__), \
                 patch.object(monitoring, "_MYSQL_DATABASE_READY", set()), \
                 patch("pymysql.connect", side_effect=AssertionError("constructor opened a bootstrap socket")), \
                 patch.object(store_class, "ensure_schema", side_effect=AssertionError("constructor ran DDL")):
                store = store_class(settings)
                self.assertEqual(settings, store.runtime_settings)

    def test_bootstrap_bounds_every_connection_in_missing_database_recovery(self):
        import pymysql
        from digital_twin.infrastructure import mysql_monitoring as monitoring
        admin, ready = Mock(), Mock()
        admin.cursor.return_value = nullcontext(Mock())
        config = {"host": "fixture", "database": "orbit_alpha_test_bootstrap", "user": "fixture"}
        with patch.object(monitoring, "_MYSQL_DATABASE_READY", set()), \
             patch("pymysql.connect", side_effect=[pymysql.err.OperationalError(1049, "unknown database"), admin, ready]) as connect:
            monitoring.ensure_mysql_database_exists(config, timeout_seconds=3)
            self.assertEqual(3, connect.call_count)
            for call in connect.call_args_list:
                self.assertEqual((3, 3, 3), tuple(call.kwargs[key] for key in ("connect_timeout", "read_timeout", "write_timeout")))
            self.assertNotIn("database", connect.call_args_list[1].kwargs)
            monitoring.ensure_mysql_database_exists(config, timeout_seconds=3)
            self.assertEqual(3, connect.call_count)
        admin.close.assert_called_once()
        ready.close.assert_called_once()

    def test_bootstrap_failure_is_not_cached_or_reinterpreted_as_missing_database(self):
        from digital_twin.infrastructure import mysql_monitoring as monitoring
        import pymysql
        config = {"host": "fixture", "database": "orbit_alpha_test_bootstrap", "user": "fixture"}
        for error in (TimeoutError("database unavailable"), pymysql.err.OperationalError(1045, "access denied")):
            with self.subTest(error=type(error).__name__), \
                 patch.object(monitoring, "_MYSQL_DATABASE_READY", set()), \
                 patch("pymysql.connect", side_effect=[error, Mock()]) as connect:
                with self.assertRaises(type(error)) as caught:
                    monitoring.ensure_mysql_database_exists(config)
                self.assertIs(error, caught.exception)
                self.assertEqual(set(), monitoring._MYSQL_DATABASE_READY)
                monitoring.ensure_mysql_database_exists(config)
                self.assertEqual(2, connect.call_count)
                self.assertTrue(all(call.kwargs.get("database") == config["database"] for call in connect.call_args_list))

    def test_normal_bootstrap_uses_the_configured_timeout_before_schema(self):
        from digital_twin.infrastructure import mysql_monitoring as monitoring
        for module, store_class in ((database, database.MySQLOperationalConnection),
                                    (monitoring, monitoring.MySQLMonitorAccountJobStore)):
            calls = []
            with self.subTest(store=store_class.__name__), \
                 patch.object(module, "ensure_mysql_database_exists", side_effect=lambda *args, **kwargs: calls.append(("database", kwargs))), \
                 patch.object(store_class, "ensure_schema", side_effect=lambda: calls.append(("schema", {}))):
                store_class({"mysqlDatabase": "orbit_alpha_test_bootstrap", "mysqlOperationTimeoutSeconds": "3"})
            self.assertEqual([("database", {"timeout_seconds": 3}), ("schema", {})], calls)

    def test_monitor_sockets_and_pool_identity_obey_operation_timeout(self):
        from digital_twin.infrastructure import mysql_monitoring as monitoring
        keys = []
        with patch("pymysql.connect") as connect, patch.object(monitoring, "pooled_mysql_connection", side_effect=lambda key, *args, **kwargs: keys.append(key)):
            for timeout in (3, 8):
                store = monitoring.MySQLMonitorAccountJobStore({"_skipOperationalSchemaBootstrap": "1",
                    "mysqlDatabase": "orbit_alpha_test_bootstrap", "mysqlOperationTimeoutSeconds": str(timeout)})
                store.raw_connection()
                self.assertEqual((timeout, timeout, timeout), tuple(connect.call_args.kwargs[key] for key in ("connect_timeout", "read_timeout", "write_timeout")))
                store.connect()
        self.assertNotEqual(keys[0], keys[1])

    def test_operation_timeout_always_has_a_finite_bound(self):
        for value, expected in ((None, 10), ("", 10), ("bad", 10), ("inf", 10), ("nan", 10), ("3", 3), ("500", 120), ("-4", 1)):
            with self.subTest(value=value):
                self.assertEqual(expected, database.mysql_operation_timeout_seconds({"mysqlOperationTimeoutSeconds": value}))


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


class PreinitializedStoreIntegrationTests(StabilizationDatabaseCase):
    def test_readers_preserve_calendar_data_queue_state_and_source_lineage(self):
        from types import SimpleNamespace
        from digital_twin.infrastructure import mysql_monitoring as monitoring
        from digital_twin.modules.investment_calendar.infrastructure.mysql_investment_calendar import MySQLInvestmentCalendarStore
        from digital_twin.modules.investment_calendar.domain.investment_calendar import InvestmentCalendarEvent
        calendar = MySQLInvestmentCalendarStore(self.settings)
        event = calendar.upsert(InvestmentCalendarEvent.from_payload({
            "eventId": "calendar:bootstrap-boundary", "title": "Preserved source event",
            "startsAt": "2030-01-02T03:00:00Z", "eventType": "earnings", "symbols": ["AAPL"],
            "accountIds": ["fixture"], "source": "fixture", "payload": {"sourceRevision": "original:1"},
        }))
        queue = monitoring.MySQLMonitorAccountJobStore(self.settings)
        queue.sync_accounts([SimpleNamespace(account_id="fixture"), SimpleNamespace(account_id="other")], 60)
        source = self.source(suffix="bootstrap-boundary")
        self.events.handle(source)
        queue_before = self.sql("SELECT * FROM monitor_account_jobs ORDER BY account_id", all_rows=True)
        lineage_before = self.sql("SELECT * FROM reasoning_engine_job_sources WHERE source_event_id=%s", (source.event_id,), all_rows=True)
        self.assertTrue(lineage_before)
        settings = {**self.settings, "_skipOperationalSchemaBootstrap": "1"}
        with patch.object(database, "ensure_mysql_database_exists", side_effect=AssertionError("bootstrap on read")), \
             patch.object(monitoring, "ensure_mysql_database_exists", side_effect=AssertionError("bootstrap on read")), \
             patch.object(database.MySQLOperationalConnection, "ensure_schema", side_effect=AssertionError("DDL on read")), \
             patch.object(monitoring.MySQLMonitorAccountJobStore, "ensure_schema", side_effect=AssertionError("DDL on read")):
            self.assertEqual(event.to_dict(), MySQLInvestmentCalendarStore(settings).get(event.event_id).to_dict())
            self.assertEqual(2, monitoring.MySQLMonitorAccountJobStore(settings).summary()["total"])
        self.assertEqual(queue_before, self.sql("SELECT * FROM monitor_account_jobs ORDER BY account_id", all_rows=True))
        self.assertEqual(lineage_before, self.sql("SELECT * FROM reasoning_engine_job_sources WHERE source_event_id=%s", (source.event_id,), all_rows=True))

    def test_missing_database_stays_missing_after_read_failure(self):
        import uuid
        import pymysql
        from digital_twin.infrastructure.mysql_monitoring import MySQLMonitorAccountJobStore
        from mysql_fixtures import mysql_test_database_config, register_mysql_test_database
        for store_class in (database.MySQLOperationalConnection, MySQLMonitorAccountJobStore):
            name = "orbit_alpha_test_missing_" + uuid.uuid4().hex[:12]
            settings = {**self.settings, "mysqlDatabase": name, "_skipOperationalSchemaBootstrap": "1"}
            # Also clean up an incorrectly created schema if this regression fails.
            register_mysql_test_database(mysql_test_database_config(settings))
            with self.subTest(store=store_class.__name__):
                store = store_class(settings)
                with self.assertRaises(pymysql.err.OperationalError) as caught:
                    connection = store.raw_connection()
                    connection.close()
                self.assertEqual(1049, caught.exception.args[0])
                self.assertIsNone(self.sql("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (name,)))


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
