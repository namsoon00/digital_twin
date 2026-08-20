import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from digital_twin.infrastructure.stale_read_model import StaleReadModelCache


class StaleReadModelCacheTests(unittest.TestCase):
    def test_failure_preserves_last_success_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = StaleReadModelCache("ontology", root=Path(temp), ttl_seconds=10)
            cache.store_success("default", {"rows": [1, 2]})
            failed = cache.refresh("default", lambda: (_ for _ in ()).throw(RuntimeError("TypeDB unavailable")))

        self.assertTrue(failed["hasData"])
        self.assertEqual([1, 2], failed["payload"]["rows"])
        self.assertIn("TypeDB unavailable", failed["lastError"])

    def test_async_refresh_is_single_flight(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = StaleReadModelCache("ontology", root=Path(temp))
            release = threading.Event()
            calls = []

            def loader():
                calls.append(True)
                release.wait(1)
                return {"status": "ok"}

            self.assertTrue(cache.refresh_async("default", loader))
            self.assertFalse(cache.refresh_async("default", loader))
            release.set()
            for _index in range(100):
                if cache.snapshot("default")["hasData"]:
                    break
                threading.Event().wait(0.01)

        self.assertEqual(1, len(calls))

    def test_stale_state_uses_persisted_success_age(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = StaleReadModelCache("ontology", root=Path(temp), ttl_seconds=10)
            cache.store_success("default", {"status": "ok"})
            entry = cache.read_entry("default")
            entry["lastSuccessEpoch"] = 100.0
            cache.write_entry("default", entry)
            with patch("digital_twin.infrastructure.stale_read_model.time.time", return_value=111.0):
                snapshot = cache.snapshot("default")

        self.assertTrue(snapshot["stale"])
        self.assertEqual(11, snapshot["ageSeconds"])


if __name__ == "__main__":
    unittest.main()
