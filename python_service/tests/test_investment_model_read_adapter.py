import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from digital_twin.infrastructure.stale_read_model import StaleReadModelCache
from digital_twin.infrastructure.web.adapters import investment_model
from digital_twin.infrastructure.web.cache import cached_api_payload
from digital_twin.modules.model_registry.domain.investment_model import investment_model_projection


CONTROL = {"active_deployment_id": "release-1", "delivery_deployment_id": "release-1",
           "candidate_deployment_id": "", "version": 7}


def model_payload(control=CONTROL):
    payload = investment_model_projection({}, {}, {}, {}, {})
    payload["controlIdentity"] = dict(control)
    payload["activeRelease"]["deploymentId"] = control["active_deployment_id"]
    payload["validation"]["promotionReady"] = True
    payload["candidate"]["eligibleForPromotion"] = True
    payload["productReadiness"]["releaseRecommended"] = True
    return payload


class InvestmentModelReadAdapterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = StaleReadModelCache("test", root=Path(temporary.name), ttl_seconds=60)
        cached = patch.object(investment_model, "INVESTMENT_MODEL_READ_MODEL", self.cache)
        cached.start()
        self.addCleanup(cached.stop)
        control = patch.object(investment_model, "_current_control", return_value=dict(CONTROL))
        self.control = control.start()
        self.addCleanup(control.stop)

    def wait_for_refresh(self):
        deadline = time.monotonic() + 2
        while self.cache.snapshot("active")["refreshing"] and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertFalse(self.cache.snapshot("active")["refreshing"])

    def test_slow_refresh_is_nonblocking_single_flight_and_revokes_cached_pass(self):
        self.cache.store_success("active", model_payload())
        release = threading.Event()
        entered = threading.Event()

        def load():
            entered.set()
            release.wait(2)
            return model_payload()

        with patch.object(investment_model, "_investment_model_source_payload", side_effect=load) as loader:
            try:
                start = time.monotonic()
                result = investment_model.investment_model_api_payload(force=True)
                self.assertLess(time.monotonic() - start, 0.5)
                self.assertTrue(entered.wait(1))
                second = investment_model.investment_model_api_payload(force=True)
                self.assertTrue(second["cache"]["refreshing"])
                self.assertEqual(1, loader.call_count)
                self.assertTrue(result["cache"]["stale"])
                self.assertFalse(result["validation"]["promotionReady"])
                self.assertFalse(result["candidate"]["eligibleForPromotion"])
                self.assertFalse(result["productReadiness"]["releaseRecommended"])
                self.assertEqual("release-1", result["activeRelease"]["deploymentId"])
            finally:
                release.set()
                self.wait_for_refresh()
        current = investment_model.investment_model_api_payload()
        self.assertFalse(current["cache"]["stale"])
        self.assertTrue(current["validation"]["promotionReady"])

    def test_failed_forced_refresh_preserves_details_but_not_pass(self):
        self.cache.store_success("active", model_payload())
        with patch.object(investment_model, "_investment_model_source_payload", side_effect=RuntimeError("offline")):
            investment_model.investment_model_api_payload(force=True)
            self.wait_for_refresh()
            result = investment_model.investment_model_api_payload()
        self.assertEqual("release-1", result["activeRelease"]["deploymentId"])
        self.assertTrue(result["cache"]["stale"])
        self.assertFalse(result["validation"]["promotionReady"])
        self.assertEqual("offline", result["cache"]["lastError"])
        self.assertGreater(result["cache"]["retryAfterSeconds"], 0)

    def test_live_control_change_or_outage_never_reuses_old_pass(self):
        self.cache.store_success("active", model_payload())
        for control in ({**CONTROL, "active_deployment_id": "release-2", "version": 8},
                        {**CONTROL, "version": 8}, None):
            with self.subTest(control=control), patch.object(self.cache, "refresh_async", return_value=False):
                self.control.return_value = control
                self.control.side_effect = RuntimeError("offline") if control is None else None
                result = investment_model.investment_model_api_payload()
                self.assertTrue(result["cache"]["stale"])
                self.assertFalse(result["validation"]["promotionReady"])
                self.assertEqual("", result["cache"]["lastSuccessAt"])
                self.assertEqual((control or {}).get("active_deployment_id", ""), result["activeRelease"]["deploymentId"])
                self.assertEqual(control is not None, result["cache"]["controlVerified"])

    def test_expired_snapshot_is_not_labeled_current_during_refresh(self):
        self.cache.store_success("active", model_payload())
        entry = self.cache.read_entry("active")
        entry["lastSuccessEpoch"] = time.time() - 120
        self.cache.write_entry("active", entry)
        with patch.object(self.cache, "refresh_async", return_value=True):
            result = investment_model.investment_model_api_payload()
        self.assertTrue(result["cache"]["stale"])
        self.assertFalse(result["validation"]["promotionReady"])
        self.assertEqual("release-1", result["activeRelease"]["deploymentId"])

    def test_partial_diagnostics_cannot_certify_a_release(self):
        payload = model_payload()
        payload["diagnostics"] = {"partial": True, "errors": ["rulebox: timeout"]}
        self.cache.store_success("active", payload)
        with patch.object(self.cache, "refresh_async", return_value=False):
            result = investment_model.investment_model_api_payload()
        self.assertTrue(result["cache"]["stale"])
        self.assertFalse(result["validation"]["promotionReady"])

    def test_component_deadline_returns_partial_instead_of_waiting_for_a_stuck_query(self):
        release = threading.Event()
        finished = threading.Event()
        def blocked():
            release.wait(2)
            finished.set()
            return {"status": "ok"}
        with ExitStack() as patches:
            for name in ("reasoning_engine_platform_status_payload", "time_series_platform_status_payload",
                         "ontology_catalog_api_payload", "ontology_experiments_status_payload"):
                patches.enter_context(patch.object(investment_model, name, return_value={"status": "ok"}))
            patches.enter_context(patch.object(investment_model, "ontology_rulebox_summary_payload", side_effect=blocked))
            patches.enter_context(patch.object(investment_model, "operational_read_settings", return_value={}))
            patches.enter_context(patch.object(investment_model, "notification_queue_store", return_value=SimpleNamespace(message_quality_summary=lambda *args: {})))
            patches.enter_context(patch.object(investment_model, "build_investment_brain_service", return_value=SimpleNamespace(learning_proposals=lambda **kwargs: {})))
            patches.enter_context(patch.object(investment_model, "investment_model_projection", return_value=model_payload()))
            patches.enter_context(patch.object(investment_model, "MODEL_COMPONENT_BUDGET_SECONDS", 0.05))
            try:
                started = time.monotonic()
                result = investment_model._investment_model_source_payload()
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertTrue(result["diagnostics"]["partial"])
                self.assertTrue(any("rulebox" in error for error in result["diagnostics"]["errors"]))
            finally:
                release.set()
                self.assertTrue(finished.wait(1))

    def test_fresh_outer_cache_does_not_hide_stale_inner_status(self):
        result = cached_api_payload(self.cache, "health", lambda: {
            "status": "healthy",
            "componentFreshness": {"external": {"readCache": {"stale": True, "ageSeconds": 7200}}},
        })
        self.assertFalse(result["readCache"]["stale"])
        self.assertEqual("stale", result["effectiveFreshness"]["status"])
        self.assertEqual(["external"], result["effectiveFreshness"]["staleComponents"])


if __name__ == "__main__":
    unittest.main()
