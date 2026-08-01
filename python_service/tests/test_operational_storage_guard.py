import unittest
from pathlib import Path
from types import SimpleNamespace

from digital_twin.infrastructure.operational_storage_guard import (
    accelerated_mysql_cleanup_settings,
    operational_storage_health,
)


class OperationalStorageGuardTests(unittest.TestCase):
    def usage(self, free_gb, total_gb=100):
        return lambda _path: SimpleNamespace(
            free=free_gb * 1024 * 1024 * 1024,
            total=total_gb * 1024 * 1024 * 1024,
        )

    def test_blocks_optional_writes_before_the_critical_reserve_is_consumed(self):
        state = operational_storage_health(
            {
                "operationalMinimumFreeSpaceMb": "16384",
                "operationalCriticalFreeSpaceMb": "8192",
            },
            probe_path=Path("/tmp/orbit-alpha-storage-test"),
            disk_usage_provider=self.usage(12),
        )

        self.assertTrue(state["ready"])
        self.assertFalse(state["nonEssentialWritesAllowed"])
        self.assertEqual("guarded-low-disk", state["status"])
        self.assertEqual("accelerated", state["cleanupMode"])

    def test_marks_low_free_ratio_for_accelerated_cleanup_without_blocking_writes(self):
        state = operational_storage_health(
            {"operationalMinimumFreeSpaceMb": "16384"},
            probe_path=Path("/tmp/orbit-alpha-storage-test"),
            disk_usage_provider=self.usage(20, total_gb=500),
        )

        self.assertTrue(state["nonEssentialWritesAllowed"])
        self.assertEqual("pressure", state["status"])
        self.assertEqual("accelerated", state["cleanupMode"])

        configured = accelerated_mysql_cleanup_settings({}, state)
        self.assertEqual("500", configured["_effectiveMysqlMinimalRetentionBatchSize"])
        self.assertEqual("500", configured["_effectiveOperationalHistoryRetentionBatchSize"])

    def test_critical_pressure_uses_the_bounded_emergency_profile(self):
        state = operational_storage_health(
            {},
            probe_path=Path("/tmp/orbit-alpha-storage-test"),
            disk_usage_provider=self.usage(4),
        )
        configured = accelerated_mysql_cleanup_settings({}, state)

        self.assertFalse(state["ready"])
        self.assertEqual("emergency", state["cleanupMode"])
        self.assertEqual("1000", configured["_effectiveMysqlMinimalRetentionBatchSize"])
        self.assertEqual(str(256 * 1024 * 1024), configured["_effectiveMysqlMinimalRetentionMaxDeleteBytes"])


if __name__ == "__main__":
    unittest.main()
