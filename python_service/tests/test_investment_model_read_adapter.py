import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from digital_twin.infrastructure.stale_read_model import StaleReadModelCache
from digital_twin.infrastructure.web.adapters import investment_model
from digital_twin.infrastructure.web.cache import cached_api_payload
from digital_twin.modules.model_registry.domain.investment_model import (
    INVESTMENT_MODEL_VERSION,
)


class InvestmentModelReadAdapterTests(unittest.TestCase):
    def test_failed_forced_refresh_does_not_label_old_release_fresh(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = StaleReadModelCache("test", root=Path(temporary))
            cache.store_success("active", {"version": INVESTMENT_MODEL_VERSION, "activeRelease": "old"})
            with patch.object(investment_model, "INVESTMENT_MODEL_READ_MODEL", cache), patch.object(
                investment_model, "_investment_model_source_payload", side_effect=RuntimeError("offline"),
            ):
                result = investment_model.investment_model_api_payload(force=True)
            self.assertEqual("old", result["activeRelease"])
            self.assertTrue(result["cache"]["stale"])
            self.assertEqual("offline", result["cache"]["lastError"])

    def test_fresh_outer_cache_does_not_hide_stale_inner_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = StaleReadModelCache("test", root=Path(temporary))
            result = cached_api_payload(cache, "health", lambda: {
                "status": "healthy",
                "componentFreshness": {"external": {"readCache": {"stale": True, "ageSeconds": 7200}}},
            })
            self.assertFalse(result["readCache"]["stale"])
            self.assertEqual("stale", result["effectiveFreshness"]["status"])
            self.assertEqual(["external"], result["effectiveFreshness"]["staleComponents"])

    def test_stale_release_gate_is_refreshed_before_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = StaleReadModelCache(
                "investment-model-test",
                root=Path(temporary),
                ttl_seconds=1,
            )
            cache.store_success("active", {
                "version": INVESTMENT_MODEL_VERSION,
                "activeRelease": {"deploymentId": "old-release"},
                "validation": {"promotionReady": False},
            })
            entry = cache.read_entry("active")
            entry["lastSuccessEpoch"] = time.time() - 60
            cache.write_entry("active", entry)
            current = {
                "version": INVESTMENT_MODEL_VERSION,
                "activeRelease": {"deploymentId": "current-release"},
                "validation": {"promotionReady": True},
            }

            with patch.object(
                investment_model,
                "INVESTMENT_MODEL_READ_MODEL",
                cache,
            ), patch.object(
                investment_model,
                "_investment_model_source_payload",
                return_value=current,
            ):
                result = investment_model.investment_model_api_payload()

        self.assertEqual("current-release", result["activeRelease"]["deploymentId"])
        self.assertTrue(result["validation"]["promotionReady"])
        self.assertFalse(result["cache"]["stale"])


if __name__ == "__main__":
    unittest.main()
