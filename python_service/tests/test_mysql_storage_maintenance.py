import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from digital_twin.infrastructure.cli import run_mysql_minimal_retention, run_mysql_operational_cleanup
from digital_twin.infrastructure.mysql_retention import (
    apply_mysql_operational_history_retention,
    ephemeral_mysql_database_names,
    operational_delivered_notification_keep_count,
    sent_article_delivery_ledger_retention_days,
    operational_history_retention_batch_size,
    operational_large_domain_event_keep_count,
    operational_large_domain_event_names,
    operational_projection_run_keep_count,
    operational_world_projection_outbox_retention_hours,
    mysql_operational_space_reclaim_candidates,
    optimize_mysql_operational_tables,
    safe_mysql_operational_compaction_tables,
)
from digital_twin.infrastructure.mysql_monitoring import mysql_monitoring_schema_bootstrap_enabled
from digital_twin.infrastructure.mysql_operational_connection import (
    MySQLOperationalConnection,
    mysql_is_connection_lost,
    mysql_operational_constructor_retention_enabled,
    mysql_operational_schema_bootstrap_enabled,
)
from digital_twin.infrastructure.mysql_schema_tuning import MYSQL_OPERATIONAL_INDEXES
from digital_twin.infrastructure.schedulers import OperationalHistoryRetentionScheduler
from digital_twin.infrastructure.typedb_ontology import TypeDBOntologyGraphRepository


class Cursor:
    def __init__(self, rowcount=0, rows=None):
        self.rowcount = rowcount
        self._rows = list(rows or [])

    def fetchall(self):
        return list(self._rows)


class RecordingConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((str(sql), tuple(params or ())))
        return Cursor()


class FullBatchRecordingConnection(RecordingConnection):
    def __init__(self, rowcount=50):
        super().__init__()
        self.rowcount = rowcount
        self.executions = {}

    def execute(self, sql, params=()):
        rendered = str(sql)
        values = tuple(params or ())
        self.calls.append((rendered, values))
        if "SELECT `job_id` FROM `ontology_world_projection_outbox`" in rendered:
            return Cursor(rows=[{"job_id": "world-projection-old"}])
        if "SELECT `job_id` FROM `ontology_inference_detail_outbox`" in rendered:
            return Cursor(rows=[{"job_id": "inference-detail-old"}])
        key = (rendered, values)
        self.executions[key] = self.executions.get(key, 0) + 1
        return Cursor(rowcount=self.rowcount)


class CandidateRecordingConnection(RecordingConnection):
    def execute(self, sql, params=()):
        rendered = str(sql)
        values = tuple(params or ())
        self.calls.append((rendered, values))
        if "SELECT event_id" in rendered:
            return Cursor(rows=[{"event_id": "event-old-1"}, {"event_id": "event-old-2"}])
        return Cursor(rowcount=2)


class TableSpaceConnection(RecordingConnection):
    def execute(self, sql, params=()):
        self.calls.append((str(sql), tuple(params or ())))
        return Cursor(rows=[
            {
                "tableName": "ontology_world_projection_outbox",
                "dataBytes": 100 * 1024 * 1024,
                "indexBytes": 10 * 1024 * 1024,
                "statisticalFreeBytes": 2048 * 1024 * 1024,
                "allocatedBytes": 2200 * 1024 * 1024,
            },
            {
                "tableName": "unknown_vendor_table",
                "dataBytes": 1,
                "indexBytes": 1,
                "statisticalFreeBytes": 2048 * 1024 * 1024,
                "allocatedBytes": 2200 * 1024 * 1024,
            },
        ])


class RestrictedTableSpaceConnection(TableSpaceConnection):
    def execute(self, sql, params=()):
        rendered = str(sql)
        if "innodb_tablespaces" in rendered:
            self.calls.append((rendered, tuple(params or ())))
            raise PermissionError("PROCESS privilege required")
        return super().execute(sql, params)


class MySQLStorageMaintenanceTests(unittest.TestCase):
    def test_realtime_fast_path_skips_schema_and_constructor_retention(self):
        self.assertTrue(mysql_operational_schema_bootstrap_enabled({}))
        self.assertTrue(mysql_monitoring_schema_bootstrap_enabled({}))
        self.assertFalse(mysql_operational_schema_bootstrap_enabled({"_skipOperationalSchemaBootstrap": "1"}))
        self.assertFalse(mysql_monitoring_schema_bootstrap_enabled({"_skipOperationalSchemaBootstrap": "true"}))
        self.assertFalse(mysql_operational_constructor_retention_enabled({}))
        self.assertTrue(mysql_operational_constructor_retention_enabled({"_runOperationalHistoryRetentionOnInit": "1"}))

    def test_dedicated_retention_scheduler_runs_cleanup_without_store_construction(self):
        calls = []
        scheduler = OperationalHistoryRetentionScheduler(
            lambda: calls.append("cleanup") or {"deleted": 3},
            interval_seconds=1,
        )

        result = scheduler.run_once()

        self.assertEqual(["cleanup"], calls)
        self.assertEqual(3, result["deleted"])
        self.assertEqual(60, scheduler.interval_seconds)

    def test_projection_audit_retention_is_bounded_per_world(self):
        connection = RecordingConnection()
        result = apply_mysql_operational_history_retention(
            connection,
            {
                "operationalHistoryRetentionEnabled": "1",
                "operationalHistoryRetentionHours": "24",
                "operationalProjectionRunKeepCount": "12",
            },
            now=datetime(2026, 7, 26, tzinfo=timezone.utc),
            use_lock=False,
        )

        self.assertEqual(12, result["projectionRunKeepCount"])
        self.assertIn("time:ontology_projection_runs", result["policies"])
        self.assertIn("count:ontology_projection_runs", result["policies"])
        self.assertIn("count:delivered_notification_jobs", result["policies"])
        self.assertIn("time:notification_article_delivery_ledger", result["policies"])
        projection_queries = [sql for sql, _params in connection.calls if "ontology_projection_runs" in sql]
        self.assertGreaterEqual(len(projection_queries), 2)
        self.assertTrue(any("status <> 'projecting'" in sql for sql in projection_queries))
        self.assertFalse(any("ROW_NUMBER() OVER" in sql for sql in projection_queries))
        self.assertTrue(any("SELECT DISTINCT world_id" in sql for sql in projection_queries))
        notification_queries = [sql for sql, _params in connection.calls if "notification_jobs" in sql]
        self.assertTrue(any("WHERE `status` = 'done'" in sql for sql in notification_queries))

    def test_projection_audit_keep_count_is_safely_bounded(self):
        self.assertEqual(2, operational_projection_run_keep_count({}))
        self.assertEqual(2, operational_projection_run_keep_count({"operationalProjectionRunKeepCount": "0"}))
        self.assertEqual(500, operational_projection_run_keep_count({"operationalProjectionRunKeepCount": "9999"}))

    def test_article_delivery_ledger_retention_is_compact_and_bounded(self):
        self.assertEqual(30, sent_article_delivery_ledger_retention_days({}))
        self.assertEqual(1, sent_article_delivery_ledger_retention_days({"sentArticleDeliveryLedgerRetentionDays": "0"}))
        self.assertEqual(365, sent_article_delivery_ledger_retention_days({"sentArticleDeliveryLedgerRetentionDays": "9999"}))

    def test_history_retention_limits_legacy_batches_and_large_outbox_audit_window(self):
        self.assertEqual(50, operational_history_retention_batch_size({"operationalHistoryRetentionBatchSize": "1000"}))
        self.assertEqual(1, operational_world_projection_outbox_retention_hours({}))
        self.assertEqual(
            1,
            operational_world_projection_outbox_retention_hours(
                {"ontologyWorldProjectionCompletedRetentionHours": "168"}
            ),
        )

    def test_history_retention_runs_one_small_batch_per_policy(self):
        connection = FullBatchRecordingConnection()

        result = apply_mysql_operational_history_retention(
            connection,
            {"operationalHistoryRetentionEnabled": "1", "operationalHistoryRetentionBatchSize": "1000"},
            now=datetime(2026, 7, 30, tzinfo=timezone.utc),
            use_lock=False,
        )

        self.assertEqual(50, result["batchSize"])
        self.assertEqual(1, result["outboxBatchSize"])
        self.assertEqual("bounded-single-batch-per-policy", result["mode"])
        self.assertTrue(connection.executions)
        self.assertTrue(all(count == 1 for count in connection.executions.values()))
        outbox_candidate_params = [
            params
            for sql, params in connection.calls
            if "SELECT `job_id` FROM `ontology_world_projection_outbox`" in sql
        ]
        self.assertEqual([1], [params[-1] for params in outbox_candidate_params])
        self.assertTrue(any(
            "DELETE FROM `ontology_world_projection_outbox` WHERE `job_id` IN" in sql
            for sql, _params in connection.calls
        ))

    def test_large_domain_event_retention_selects_then_deletes_small_primary_key_set(self):
        connection = CandidateRecordingConnection()
        result = apply_mysql_operational_history_retention(
            connection,
            {"operationalHistoryRetentionEnabled": "1", "operationalHistoryRetentionBatchSize": "50"},
            now=datetime(2026, 7, 30, tzinfo=timezone.utc),
            use_lock=False,
        )

        domain_queries = [
            (sql, params)
            for sql, params in connection.calls
            if "domain_events" in sql
        ]
        self.assertTrue(any("SELECT event_id" in sql for sql, _params in domain_queries))
        delete_params = [
            params
            for sql, params in domain_queries
            if "DELETE FROM `domain_events` WHERE `event_id` IN" in sql
        ]
        self.assertEqual([("event-old-1", "event-old-2")], delete_params)
        self.assertEqual(2, result["policies"]["count:domain_events"])

    def test_completed_outbox_indexes_and_connection_loss_codes_are_registered(self):
        index_names = {
            index.name
            for definitions in (
                MYSQL_OPERATIONAL_INDEXES["ontology_world_projection_outbox"],
                MYSQL_OPERATIONAL_INDEXES["ontology_inference_detail_outbox"],
            )
            for index in definitions
        }

        self.assertIn("idx_world_projection_outbox_completed", index_names)
        self.assertIn("idx_inference_detail_outbox_completed", index_names)
        lifecycle_index_names = {
            index.name
            for index in MYSQL_OPERATIONAL_INDEXES["investment_hypothesis_lifecycle_events"]
        }
        self.assertIn("idx_hypothesis_lifecycle_events_occurred", lifecycle_index_names)
        evidence_index_names = {
            index.name for index in MYSQL_OPERATIONAL_INDEXES["research_evidence"]
        }
        self.assertIn("idx_research_evidence_lifecycle_kind_latest", evidence_index_names)
        notification_index_names = {
            index.name for index in MYSQL_OPERATIONAL_INDEXES["notification_jobs"]
        }
        self.assertIn("idx_notification_jobs_account_status_updated", notification_index_names)
        self.assertTrue(mysql_is_connection_lost(Exception(2013, "Lost connection")))
        self.assertFalse(mysql_is_connection_lost(Exception(1213, "Deadlock")))

    def test_operational_cleanup_retries_one_connection_loss_with_a_fresh_store(self):
        class Store:
            def connect(self):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with patch(
            "digital_twin.infrastructure.cli.MySQLOperationalConnection",
            side_effect=[Store(), Store()],
        ) as stores, patch(
            "digital_twin.infrastructure.cli.apply_mysql_operational_history_retention",
            side_effect=[Exception(2013, "Lost connection"), {"deleted": 0, "tables": {}}],
        ) as retention, patch(
            "digital_twin.infrastructure.cli.mysql_operational_compaction_tables",
            return_value=[],
        ), patch(
            "digital_twin.infrastructure.cli.observe_operational_storage_capacity",
            return_value={},
        ), patch("digital_twin.infrastructure.cli.time.sleep") as sleep:
            result = run_mysql_operational_cleanup({"operationalHistoryRetentionEnabled": "1"})

        self.assertEqual(2, stores.call_count)
        self.assertEqual(2, retention.call_count)
        self.assertEqual(1, result["transientConnectionRetryCount"])
        self.assertTrue(all(
            call.args[0]["_skipOperationalSchemaBootstrap"]
            for call in stores.call_args_list
        ))
        self.assertTrue(all(
            int(call.args[0]["mysqlOperationTimeoutSeconds"]) >= 60
            for call in stores.call_args_list
        ))
        sleep.assert_called_once_with(0.25)

    def test_explicit_minimal_retention_drain_uses_bounded_accelerated_passes(self):
        class Store:
            def connect(self):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with patch(
            "digital_twin.infrastructure.cli.MySQLOperationalConnection",
            return_value=Store(),
        ), patch(
            "digital_twin.infrastructure.cli.MySQLMinimalRetentionService",
        ) as service:
            service.return_value.run_once.side_effect = [
                {
                    "status": "ok",
                    "deleted": 1200,
                    "compacted": 4,
                    "estimatedBytes": 1024,
                    "tables": {"investment_hypothesis_lifecycle_events": 1200},
                    "policies": {"lifecycle:events": 1200},
                },
                {
                    "status": "ok",
                    "deleted": 0,
                    "compacted": 0,
                    "estimatedBytes": 0,
                    "tables": {},
                    "policies": {},
                },
            ]

            result = run_mysql_minimal_retention({}, apply=True, drain=True, drain_max_passes=5)

        self.assertEqual(1200, result["deleted"])
        self.assertEqual(4, result["compacted"])
        self.assertEqual(2, result["drain"]["completedPasses"])
        self.assertTrue(result["drain"]["exhausted"])
        self.assertEqual("1000", service.call_args.args[1].get("_effectiveMysqlMinimalRetentionBatchSize"))
        self.assertEqual(2, service.return_value.run_once.call_count)

    def test_minimal_retention_drain_requires_explicit_apply(self):
        result = run_mysql_minimal_retention({}, drain=True)

        self.assertEqual("invalid", result["status"])

    def test_connection_pool_key_keeps_maintenance_timeout_separate(self):
        store = object.__new__(MySQLOperationalConnection)
        store.runtime_settings = {"mysqlOperationTimeoutSeconds": "60"}
        store.mysql_config = {
            "host": "127.0.0.1",
            "port": 3306,
            "user": "orbit_alpha_app",
            "database": "orbit_alpha",
            "unix_socket": "",
        }

        with patch(
            "digital_twin.infrastructure.mysql_operational_connection.pooled_mysql_connection",
            return_value=(object(), lambda _connection: None),
        ) as pooled:
            store.pooled_connection()

        self.assertEqual(60, pooled.call_args[0][0][-1])

    def test_transaction_preserves_original_error_when_rollback_connection_is_closed(self):
        class BrokenConnection:
            def __init__(self):
                self.rollback_count = 0

            def rollback(self):
                self.rollback_count += 1
                raise RuntimeError("socket already closed")

        connection = BrokenConnection()
        store = object.__new__(MySQLOperationalConnection)
        store.pooled_connection = lambda autocommit=False: (
            connection,
            lambda _connection: None,
        )

        with self.assertRaisesRegex(ValueError, "original query timeout"):
            with store.transaction():
                raise ValueError("original query timeout")

        self.assertEqual(1, connection.rollback_count)

    def test_operational_cleanup_retries_deadlock_with_configured_backoff(self):
        class Store:
            def connect(self):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with patch(
            "digital_twin.infrastructure.cli.MySQLOperationalConnection",
            side_effect=[Store(), Store()],
        ), patch(
            "digital_twin.infrastructure.cli.apply_mysql_operational_history_retention",
            side_effect=[Exception(1213, "Deadlock"), {"deleted": 0, "tables": {}}],
        ), patch(
            "digital_twin.infrastructure.cli.mysql_operational_compaction_tables",
            return_value=[],
        ), patch(
            "digital_twin.infrastructure.cli.mysql_deadlock_retry_delay_milliseconds",
            return_value=17,
        ), patch(
            "digital_twin.infrastructure.cli.observe_operational_storage_capacity",
            return_value={},
        ), patch("digital_twin.infrastructure.cli.time.sleep") as sleep:
            result = run_mysql_operational_cleanup({"operationalHistoryRetentionEnabled": "1"})

        self.assertEqual(1, result["deadlockRetryCount"])
        sleep.assert_called_once_with(0.017)

    def test_duplicated_event_and_delivery_history_defaults_are_compact(self):
        self.assertEqual(20, operational_large_domain_event_keep_count({}))
        self.assertEqual(5, operational_delivered_notification_keep_count({}))
        self.assertEqual(5, operational_delivered_notification_keep_count({"operationalDeliveredNotificationKeepCount": "1"}))
        self.assertEqual(500, operational_delivered_notification_keep_count({"operationalDeliveredNotificationKeepCount": "9999"}))

    def test_critical_high_volume_event_retention_survives_legacy_settings(self):
        names = operational_large_domain_event_names({
            "operationalLargeDomainEventNames": "monitoring.alerts_detected,research_evidence.collected",
        })

        self.assertIn("market_data.collected", names)
        self.assertIn("ontology.reasoning_requested", names)

    def test_explicit_compaction_only_accepts_known_tables(self):
        connection = RecordingConnection()

        result = optimize_mysql_operational_tables(
            connection,
            ["domain_events", "unknown_table; DROP DATABASE orbit_alpha"],
        )

        self.assertEqual(["domain_events"], result["optimizedTables"])
        self.assertEqual(["unknown_table; DROP DATABASE orbit_alpha"], result["rejectedTables"])
        self.assertEqual(1, len(connection.calls))
        self.assertEqual("OPTIMIZE TABLE `domain_events`", connection.calls[0][0])

    def test_compaction_plan_uses_metadata_and_preserves_disk_reserve(self):
        candidates = mysql_operational_space_reclaim_candidates(TableSpaceConnection())

        self.assertEqual(["ontology_world_projection_outbox"], [item["table"] for item in candidates])
        safe = safe_mysql_operational_compaction_tables(
            candidates,
            free_bytes=20 * 1024 * 1024 * 1024,
            reserve_bytes=16 * 1024 * 1024 * 1024,
        )
        blocked = safe_mysql_operational_compaction_tables(
            candidates,
            free_bytes=16 * 1024 * 1024 * 1024,
            reserve_bytes=16 * 1024 * 1024 * 1024,
        )

        self.assertEqual(["ontology_world_projection_outbox"], safe["selectedTables"])
        self.assertEqual([], blocked["selectedTables"])
        self.assertEqual("insufficient-headroom", blocked["skippedTables"][0]["reason"])

    def test_compaction_plan_falls_back_without_process_privilege(self):
        connection = RestrictedTableSpaceConnection()

        candidates = mysql_operational_space_reclaim_candidates(connection)

        self.assertEqual(["ontology_world_projection_outbox"], [item["table"] for item in candidates])
        self.assertEqual("information-schema-tables", candidates[0]["metadataSource"])
        self.assertEqual(2, len(connection.calls))

    def test_only_disposable_test_and_smoke_databases_are_candidates(self):
        result = ephemeral_mysql_database_names(
            [
                "orbit_alpha",
                "orbit_alpha_smoke_20260726",
                "orbit_alpha_test",
                "orbit_alpha_test_worker_123",
                "orbit_alpha_debug_smoke",
                "mysql",
                "customer_database",
            ],
            protected_databases=["orbit_alpha_test"],
        )

        self.assertEqual(
            ["orbit_alpha_debug_smoke", "orbit_alpha_smoke_20260726", "orbit_alpha_test_worker_123"],
            result,
        )

    def test_typedb_idle_maintenance_has_a_separate_bounded_drain_limit(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        self.assertEqual(10, repository.deferred_maintenance_abox_max_manifests({}))
        self.assertEqual(
            4,
            repository.deferred_maintenance_abox_max_manifests(
                {"typedbDeferredMaintenanceMaxManifests": "4"}
            ),
        )


if __name__ == "__main__":
    unittest.main()
