import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from digital_twin.infrastructure.cli import run_local_graph_write
from digital_twin.infrastructure.graph_writer_guard import LocalGraphWriterGuard


class LocalGraphWriterGuardTests(unittest.TestCase):
    def test_only_one_guard_owns_a_graph_database(self):
        with tempfile.TemporaryDirectory() as directory:
            first = LocalGraphWriterGuard(
                "typedb-production",
                "delivery",
                Path(directory),
                deployment_id="release-a",
            )
            second = LocalGraphWriterGuard(
                "typedb-production",
                "maintenance",
                Path(directory),
                deployment_id="release-a",
            )

            acquired = first.acquire()
            blocked = second.acquire()

            self.assertTrue(acquired["acquired"])
            self.assertFalse(blocked["acquired"])
            self.assertEqual("held-by-other-process", blocked["status"])
            self.assertEqual("delivery", blocked["owner"]["role"])
            self.assertEqual("released", first.release()["status"])
            self.assertTrue(second.acquire()["acquired"])
            second.release()

    def test_guard_is_reentrant_only_for_the_same_owner_object(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = LocalGraphWriterGuard(
                "typedb-production",
                "delivery",
                Path(directory),
            )

            self.assertEqual("acquired", guard.acquire()["status"])
            self.assertEqual("adopted-local-writer", guard.acquire()["status"])
            self.assertEqual("retained-by-outer-scope", guard.release()["status"])
            self.assertTrue(guard.status()["acquired"])
            self.assertEqual("released", guard.release()["status"])
            self.assertFalse(guard.status()["acquired"])

    def test_admin_graph_write_fails_closed_while_delivery_owns_database(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_directory = Path(directory) / "graph-writer-locks"
            delivery = LocalGraphWriterGuard(
                "typedb-production",
                "delivery",
                lock_directory,
            )
            self.assertTrue(delivery.acquire()["acquired"])
            calls = []

            with patch(
                "digital_twin.infrastructure.cli.data_dir",
                return_value=Path(directory),
            ):
                result = run_local_graph_write(
                    {
                        "typedbDatabase": "typedb-production",
                        "ontologyGraphSingleWriterEnabled": "1",
                    },
                    "cli-maintenance",
                    lambda: calls.append("ran") or {"status": "ok"},
                )

            self.assertEqual("blocked", result["status"])
            self.assertEqual("typedb-graph-writer-owned", result["reasonCode"])
            self.assertEqual([], calls)
            delivery.release()


if __name__ == "__main__":
    unittest.main()
