import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from digital_twin.domain.mysql_maintenance_admission import mysql_maintenance_admission
from digital_twin.infrastructure.mysql_realtime_workload_guard import MySQLRealtimeWorkloadGuard


class MySQLMaintenanceAdmissionTests(unittest.TestCase):
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
