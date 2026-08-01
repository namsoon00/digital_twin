import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.infrastructure.typedb_storage_guard import typedb_storage_health


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
