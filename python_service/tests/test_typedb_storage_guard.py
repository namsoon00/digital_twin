import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.infrastructure.typedb_storage_guard import (
    TypeDBCapacityGuard,
    typedb_storage_health,
    typedb_storage_inventory,
)


class TypeDBStorageGuardTests(unittest.TestCase):
    def test_blocks_reasoning_when_free_space_is_below_reserve(self):
        state = typedb_storage_health(
            {"typedbMinimumFreeSpaceMb": "4096"},
            data_path=Path("/tmp/typedb-test"),
            disk_usage_provider=lambda _path: SimpleNamespace(
                free=1024 * 1024 * 1024,
                total=32 * 1024 * 1024 * 1024,
            ),
        )

        self.assertFalse(state["ready"])
        self.assertEqual("blocked-low-disk", state["status"])

    def test_allows_reasoning_when_free_space_meets_reserve(self):
        state = typedb_storage_health(
            {"typedbMinimumFreeSpaceMb": "1024"},
            data_path=Path("/tmp/typedb-test"),
            disk_usage_provider=lambda _path: SimpleNamespace(
                free=2 * 1024 * 1024 * 1024,
                total=32 * 1024 * 1024 * 1024,
            ),
        )

        self.assertTrue(state["ready"])
        self.assertEqual("ready", state["status"])

    def test_shared_operational_reserve_overrides_a_smaller_typedb_reserve(self):
        state = typedb_storage_health(
            {
                "typedbMinimumFreeSpaceMb": "4096",
                "operationalMinimumFreeSpaceMb": "16384",
            },
            data_path=Path("/tmp/typedb-test"),
            disk_usage_provider=lambda _path: SimpleNamespace(
                free=12 * 1024 * 1024 * 1024,
                total=100 * 1024 * 1024 * 1024,
            ),
        )

        self.assertFalse(state["ready"])
        self.assertEqual(16384, state["minimumFreeMb"])

    def test_inventory_reports_only_typedb_storage_components(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            wal = root / "db" / "wal"
            checkpoint = root / "db" / "checkpoint"
            wal.mkdir(parents=True)
            checkpoint.mkdir(parents=True)
            (wal / "segment").write_bytes(b"w" * 1024 * 1024)
            (checkpoint / "segment").write_bytes(b"c" * 2 * 1024 * 1024)
            inventory = typedb_storage_inventory(
                {"typedbDataMaxSizeMb": "16"},
                data_path=root,
                disk_usage_provider=lambda _path: SimpleNamespace(
                    free=20 * 1024 * 1024 * 1024,
                    total=32 * 1024 * 1024 * 1024,
                ),
            )

        self.assertGreater(inventory["typedbSizeMb"], 2)
        self.assertGreater(inventory["typedbWalMb"], 0)
        self.assertGreater(inventory["typedbCheckpointMb"], 0)

    def test_inventory_deduplicates_checkpoint_hard_links_for_capacity(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            storage = root / "db" / "storage"
            checkpoint = root / "db" / "checkpoint"
            storage.mkdir(parents=True)
            checkpoint.mkdir(parents=True)
            source = storage / "segment"
            source.write_bytes(b"x" * (2 * 1024 * 1024))
            os.link(source, checkpoint / "segment")
            inventory = typedb_storage_inventory(
                {"typedbDataMaxSizeMb": "16"},
                data_path=root,
                disk_usage_provider=lambda _path: SimpleNamespace(
                    free=20 * 1024 * 1024 * 1024,
                    total=32 * 1024 * 1024 * 1024,
                ),
            )

        self.assertGreater(inventory["typedbApparentSizeMb"], inventory["typedbSizeMb"])
        self.assertGreater(inventory["typedbSharedLinkedMb"], 0)
        self.assertGreater(inventory["typedbCheckpointReferencedMb"], 0)

    def test_cached_guard_throttles_background_work_at_component_pressure(self):
        clock = [0.0]
        calls = []

        def inventory(_settings, **_kwargs):
            calls.append(True)
            return {"typedbSizeMb": 800, "typedbLimitMb": 1000, "typedbWalMb": 400, "typedbCheckpointMb": 300}

        guard = TypeDBCapacityGuard(
            {"typedbCapacityGuardCheckIntervalSeconds": "30"},
            role="world-projection",
            data_path=Path("/tmp/typedb-test"),
            disk_usage_provider=lambda _path: SimpleNamespace(
                free=20 * 1024 * 1024 * 1024,
                total=32 * 1024 * 1024 * 1024,
            ),
            inventory_provider=inventory,
            monotonic_provider=lambda: clock[0],
        )

        first = guard()
        clock[0] = 10.0
        second = guard()

        self.assertFalse(first["ready"])
        self.assertEqual("write-throttled", first["mode"])
        self.assertEqual(1, len(calls))
        self.assertEqual(10.0, second["capacitySampleAgeSeconds"])

    def test_guard_uses_a_fresh_operational_capacity_sample_before_scanning_typedb(self):
        def unexpected_inventory(*_args, **_kwargs):
            raise AssertionError("direct TypeDB scan should not run")

        guard = TypeDBCapacityGuard(
            {"typedbCapacitySharedSampleMaxAgeSeconds": "180"},
            role="world-projection",
            data_path=Path("/tmp/typedb-test"),
            disk_usage_provider=lambda _path: SimpleNamespace(
                free=20 * 1024 * 1024 * 1024,
                total=32 * 1024 * 1024 * 1024,
            ),
            inventory_provider=unexpected_inventory,
            capacity_state_loader=lambda: {
                "operationalStorageCapacity": {
                    "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "typedbSizeMb": 500,
                    "typedbLimitMb": 1000,
                    "typedbWalMb": 400,
                    "typedbCheckpointMb": 300,
                },
            },
        )

        state = guard()

        self.assertEqual("operational-capacity", state["capacitySampleSource"])
        self.assertEqual("normal", state["mode"])

    def test_guard_confirms_a_high_shared_sample_with_direct_filesystem_inventory(self):
        calls = []

        def inventory(_settings, **_kwargs):
            calls.append(True)
            return {
                "typedbSizeMb": 300,
                "typedbLimitMb": 1000,
                "typedbWalMb": 20,
                "typedbCheckpointMb": 30,
            }

        guard = TypeDBCapacityGuard(
            {"typedbCapacityThrottlePercent": "80"},
            role="world-projection",
            data_path=Path("/tmp/typedb-test"),
            disk_usage_provider=lambda _path: SimpleNamespace(
                free=20 * 1024 * 1024 * 1024,
                total=32 * 1024 * 1024 * 1024,
            ),
            inventory_provider=inventory,
            capacity_state_loader=lambda: {
                "operationalStorageCapacity": {
                    "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "typedbSizeMb": 900,
                    "typedbLimitMb": 1000,
                },
            },
        )

        state = guard()

        self.assertEqual(1, len(calls))
        self.assertEqual("direct-filesystem", state["capacitySampleSource"])
        self.assertTrue(state["ready"])
