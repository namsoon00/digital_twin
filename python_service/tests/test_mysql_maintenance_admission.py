import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from digital_twin.platform.domain.mysql_maintenance_admission import mysql_maintenance_admission
from digital_twin.infrastructure.mysql_realtime_workload_guard import MySQLRealtimeWorkloadGuard


class MySQLMaintenanceAdmissionTests(unittest.TestCase):
    def test_capacity_refresh_runs_even_when_busy_queue_defers_retention(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from digital_twin.infrastructure import cli
        scheduler = Mock()
        with patch.object(cli, 'runtime_settings', return_value={}), \
             patch.object(cli, 'build_ontology_reasoning_queue_probe', return_value=lambda: {'effectivePendingCount': 3}), \
             patch.object(cli, 'operational_storage_inventory', return_value={'freeMb': 8000}) as inventory, \
             patch.object(cli, 'observe_operational_storage_capacity') as observe, \
             patch.object(cli, 'run_mysql_operational_cleanup') as cleanup, \
             patch.object(cli, 'OperationalHistoryRetentionScheduler', return_value=scheduler) as factory:
            scheduler.run_forever.side_effect = lambda: factory.call_args.args[0]()
            cli.maintenance_command(SimpleNamespace(maintenance_action='watch', interval='60'))
        inventory.assert_called_once()
        observe.assert_called_once_with({}, snapshot={'freeMb': 8000})
        cleanup.assert_not_called()

    def test_realtime_queue_defers_cleanup_until_maximum_deferral(self):
        settings = {"mysqlMaintenanceMaxRealtimeDeferralSeconds": "900"}

        first = mysql_maintenance_admission(
            settings,
            pending_count=3,
            now_epoch=1000,
        )
        still_busy = mysql_maintenance_admission(
            settings,
            pending_count=2,
            now_epoch=1899,
            deferral_started_at=first.deferral_started_at,
        )

        self.assertFalse(first.run_cleanup)
        self.assertFalse(still_busy.run_cleanup)
        self.assertEqual("realtime-queue-deferred", still_busy.status)

        with TemporaryDirectory() as directory:
            first_guard = MySQLRealtimeWorkloadGuard(Path(directory) / "mysql-workload.lock")
            second_guard = MySQLRealtimeWorkloadGuard(Path(directory) / "mysql-workload.lock")
            with first_guard.monitor_cycle() as monitor_lease:
                with second_guard.maintenance_turn() as maintenance_lease:
                    self.assertTrue(monitor_lease.acquired)
                    self.assertFalse(maintenance_lease.acquired)
            with second_guard.maintenance_turn() as released_lease:
                self.assertTrue(released_lease.acquired)

    def test_sustained_queue_allows_only_bounded_cleanup(self):
        result = mysql_maintenance_admission(
            {"mysqlMaintenanceMaxRealtimeDeferralSeconds": "900"},
            pending_count=4,
            now_epoch=1900,
            deferral_started_at=1000,
            last_legacy_at=1,
        )

        self.assertTrue(result.run_cleanup)
        self.assertFalse(result.include_legacy)
        self.assertEqual("bounded-cleanup-after-max-deferral", result.status)

    def test_idle_queue_runs_legacy_only_on_hourly_cadence(self):
        settings = {"mysqlLegacyRetentionIntervalSeconds": "3600"}

        bounded = mysql_maintenance_admission(
            settings,
            pending_count=0,
            now_epoch=4000,
            last_legacy_at=1000,
        )
        full = mysql_maintenance_admission(
            settings,
            pending_count=0,
            now_epoch=4600,
            last_legacy_at=1000,
        )

        self.assertTrue(bounded.run_cleanup)
        self.assertFalse(bounded.include_legacy)
        self.assertTrue(full.include_legacy)


if __name__ == "__main__":
    unittest.main()
