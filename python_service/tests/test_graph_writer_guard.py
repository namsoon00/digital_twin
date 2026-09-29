import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from digital_twin.infrastructure.cli import run_local_graph_write
from digital_twin.infrastructure.graph_writer_guard import LocalGraphWriterGuard


class LocalGraphWriterGuardTests(unittest.TestCase):
    def test_only_one_guard_owns_a_graph_database(self):
        with self.subTest(scenario="different_databases_share_server_and_delivery_gets_next_turn"):
            self._assert_different_databases_share_server_and_delivery_gets_next_turn()
        with self.subTest(scenario="server_backoff_survives_guard_replacement_and_idle_turns"):
            self._assert_server_backoff_survives_guard_replacement_and_idle_turns()
        with self.subTest(scenario="backoff_is_bounded_and_does_not_hide_permanent_errors"):
            self._assert_backoff_is_bounded_and_does_not_hide_permanent_errors()
        with self.subTest(scenario="process_exit_releases_server_and_delivery_intent"):
            self._assert_process_exit_releases_server_and_delivery_intent()
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
            self.assertEqual("delivery-waiting", blocked["status"])
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

    def test_same_database_on_isolated_typedb_instances_has_separate_writer_locks(self):
        with tempfile.TemporaryDirectory() as directory:
            active = LocalGraphWriterGuard(
                "typedb-production",
                "delivery",
                Path(directory),
                graph_address="127.0.0.1:1729",
            )
            candidate = LocalGraphWriterGuard(
                "typedb-production",
                "candidate-seed",
                Path(directory),
                graph_address="127.0.0.1:1730",
            )

            self.assertTrue(active.acquire()["acquired"])
            candidate_result = candidate.acquire()
            self.assertTrue(candidate_result["acquired"])
            self.assertEqual("127.0.0.1:1730", candidate_result["graphAddress"])
            candidate.release()
            active.release()

    def test_localhost_and_loopback_share_one_writer_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            first = LocalGraphWriterGuard(
                "typedb-production",
                "delivery",
                Path(directory),
                graph_address="localhost:1729",
            )
            second = LocalGraphWriterGuard(
                "typedb-production",
                "maintenance",
                Path(directory),
                graph_address="127.0.0.1:1729",
            )

            self.assertTrue(first.acquire()["acquired"])
            self.assertFalse(second.acquire()["acquired"])
            first.release()

    def _assert_different_databases_share_server_and_delivery_gets_next_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = LocalGraphWriterGuard("candidate", "candidate", Path(directory))
            delivery = LocalGraphWriterGuard("active", "delivery", Path(directory))
            self.assertTrue(candidate.acquire()["acquired"])
            self.assertFalse(delivery.acquire()["acquired"])
            candidate.release()
            self.assertEqual("delivery-waiting", candidate.acquire()["status"])
            self.assertTrue(delivery.acquire()["acquired"])
            delivery.release()
            self.assertTrue(candidate.acquire()["acquired"])
            candidate.release()

    def _assert_server_backoff_survives_guard_replacement_and_idle_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = LocalGraphWriterGuard("active", "delivery", Path(directory))
            failure = {"result": {"status": "deferred", "retryable": True,
                                  "reason_code": "typedbRequestError"}}
            with patch("digital_twin.infrastructure.graph_writer_guard.time.time", return_value=100), \
                 patch("digital_twin.infrastructure.graph_writer_guard.random.uniform", return_value=2):
                self.assertTrue(guard.acquire()["acquired"])
                self.assertEqual(7, guard.record_result(failure)["retryAfterSeconds"])
                guard.release()
                other = LocalGraphWriterGuard("candidate", "candidate", Path(directory))
                self.assertEqual("server-cooling-down", other.acquire()["status"])
            with patch("digital_twin.infrastructure.graph_writer_guard.time.time", return_value=110), \
                 patch("digital_twin.infrastructure.graph_writer_guard.random.uniform", return_value=2):
                self.assertTrue(other.acquire()["acquired"])
                other.record_result({"status": "idle"})
                self.assertEqual(12, other.record_result(failure)["retryAfterSeconds"])
                other.release()
            with patch("digital_twin.infrastructure.graph_writer_guard.time.time", return_value=130):
                self.assertTrue(guard.acquire()["acquired"])
                self.assertEqual(0, guard.record_result({"result": {"status": "completed"}})["consecutiveFailures"])
                guard.release()
                self.assertTrue(other.acquire()["acquired"])
                other.release()

    def _assert_backoff_is_bounded_and_does_not_hide_permanent_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = LocalGraphWriterGuard("active", "delivery", Path(directory))
            self.assertTrue(guard.acquire()["acquired"])
            for _ in range(10):
                backoff = guard.record_result({"status": "deferred", "retryable": True,
                                               "reason_code": "typedbRequestError"})
            self.assertEqual(120, backoff["retryAfterSeconds"])
            permanent = guard.record_result({"status": "blocked", "retryable": False,
                                             "reason_code": "typedbQueryError"})
            self.assertEqual({}, permanent)
            self.assertEqual(6, guard.status()["serverBackoff"]["failures"])
            guard.release()

    def _assert_process_exit_releases_server_and_delivery_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            code = """
import sys
from pathlib import Path
from digital_twin.infrastructure.graph_writer_guard import LocalGraphWriterGuard
x = LocalGraphWriterGuard('active', 'delivery', Path(sys.argv[1]))
print(x.acquire()['acquired'], flush=True)
sys.stdin.read()
"""
            child = subprocess.Popen([sys.executable, "-c", code, directory],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual("True", child.stdout.readline().strip())
                guard = LocalGraphWriterGuard("candidate", "candidate", Path(directory))
                self.assertFalse(guard.acquire()["acquired"])
                child.kill()
                child.wait(timeout=5)
                self.assertTrue(guard.acquire()["acquired"])
                guard.release()
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                child.stdin.close()
                child.stdout.close()

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
