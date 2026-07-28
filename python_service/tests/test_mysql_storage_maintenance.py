import unittest
from datetime import datetime, timezone

from digital_twin.infrastructure.mysql_retention import (
    apply_mysql_operational_history_retention,
    ephemeral_mysql_database_names,
    operational_delivered_notification_keep_count,
    operational_large_domain_event_keep_count,
    operational_projection_run_keep_count,
    optimize_mysql_operational_tables,
)
from digital_twin.infrastructure.mysql_monitoring import mysql_monitoring_schema_bootstrap_enabled
from digital_twin.infrastructure.mysql_operational_connection import (
    mysql_operational_constructor_retention_enabled,
    mysql_operational_schema_bootstrap_enabled,
)
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
        projection_queries = [sql for sql, _params in connection.calls if "ontology_projection_runs" in sql]
        self.assertGreaterEqual(len(projection_queries), 2)
        self.assertTrue(any("status <> 'projecting'" in sql for sql in projection_queries))
        self.assertTrue(any("ROW_NUMBER() OVER" in sql for sql in projection_queries))
        notification_queries = [sql for sql, _params in connection.calls if "notification_jobs" in sql]
        self.assertTrue(any("WHERE `status` = 'done'" in sql for sql in notification_queries))

    def test_projection_audit_keep_count_is_safely_bounded(self):
        self.assertEqual(48, operational_projection_run_keep_count({}))
        self.assertEqual(2, operational_projection_run_keep_count({"operationalProjectionRunKeepCount": "0"}))
        self.assertEqual(500, operational_projection_run_keep_count({"operationalProjectionRunKeepCount": "9999"}))

    def test_duplicated_event_and_delivery_history_defaults_are_compact(self):
        self.assertEqual(20, operational_large_domain_event_keep_count({}))
        self.assertEqual(30, operational_delivered_notification_keep_count({}))
        self.assertEqual(5, operational_delivered_notification_keep_count({"operationalDeliveredNotificationKeepCount": "1"}))
        self.assertEqual(500, operational_delivered_notification_keep_count({"operationalDeliveredNotificationKeepCount": "9999"}))

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
