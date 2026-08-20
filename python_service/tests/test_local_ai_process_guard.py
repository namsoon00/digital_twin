import tempfile
import threading
import unittest
from pathlib import Path

from digital_twin.infrastructure.local_ai_process_guard import (
    LocalAICapacityUnavailable,
    local_ai_capacity_lease,
)


class LocalAIProcessGuardTests(unittest.TestCase):
    def test_one_slot_blocks_a_second_process_boundary_lease(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with local_ai_capacity_lease(root, max_concurrent=1, wait_seconds=0):
                with self.assertRaises(LocalAICapacityUnavailable):
                    with local_ai_capacity_lease(root, max_concurrent=1, wait_seconds=0):
                        pass

            with local_ai_capacity_lease(root, max_concurrent=1, wait_seconds=0) as path:
                self.assertEqual("slot-1.lock", path.name)

    def test_waiting_lease_runs_after_the_active_slot_is_released(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            acquired = threading.Event()

            def waiter():
                with local_ai_capacity_lease(root, max_concurrent=1, wait_seconds=1, poll_seconds=0.01):
                    acquired.set()

            with local_ai_capacity_lease(root, max_concurrent=1, wait_seconds=0):
                thread = threading.Thread(target=waiter)
                thread.start()
                self.assertFalse(acquired.wait(0.05))
            thread.join(1)

            self.assertTrue(acquired.is_set())


if __name__ == "__main__":
    unittest.main()
