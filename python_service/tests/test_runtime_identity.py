import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.ontology_diagnostics_service import OntologyDiagnosticsService
from digital_twin.infrastructure import settings as runtime_settings_module
from digital_twin.infrastructure.runtime_identity import runtime_identity
from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder


class RuntimeIdentityTests(unittest.TestCase):
    def test_runtime_identity_is_stable_and_non_sensitive(self):
        identity = runtime_identity()

        self.assertEqual("orbit-runtime-identity-v1", identity["contract"])
        self.assertTrue(identity["version"])
        self.assertTrue(identity["revision"])
        self.assertTrue(identity["source"])
        self.assertTrue(identity["python"])

    def test_diagnostics_exposes_injected_runtime_identity(self):
        identity = {
            "contract": "orbit-runtime-identity-v1",
            "version": "test",
            "revision": "abc123",
            "source": "test",
            "python": "3.test",
        }
        service = OntologyDiagnosticsService(
            ontology_repository=object(),
            runtime_identity_provider=lambda: identity,
        )

        self.assertEqual(identity, service.status()["runtimeIdentity"])

    def test_runtime_secret_environment_overrides_legacy_database_value(self):
        with patch.object(runtime_settings_module, "load_local_env"), patch.object(
            runtime_settings_module,
            "read_settings_store",
            return_value={"kisAppKey": "legacy-database-secret"},
        ), patch.dict(os.environ, {"KIS_APP_KEY": "environment-secret"}, clear=False):
            settings = runtime_settings_module.runtime_settings()

        self.assertEqual("environment-secret", settings["kisAppKey"])

    def test_isolated_runtime_can_override_persisted_infrastructure_endpoint(self):
        with patch.object(runtime_settings_module, "load_local_env"), patch.object(
            runtime_settings_module,
            "read_settings_store",
            return_value={"typedbAddress": "127.0.0.1:1729"},
        ), patch.dict(os.environ, {
            "ORBIT_INFRASTRUCTURE_OVERRIDE_ENABLED": "1",
            "TYPEDB_ADDRESS": "127.0.0.1:1739",
        }, clear=False):
            settings = runtime_settings_module.runtime_settings()

        self.assertEqual("127.0.0.1:1739", settings["typedbAddress"])

    def test_legacy_shadow_price_signal_release_is_migrated_at_read_time(self):
        with patch.object(runtime_settings_module, "load_local_env"), patch.object(
            runtime_settings_module,
            "read_settings_store",
            return_value={
                "statisticalPriceSignalReleaseId": "price-path-statistics-shadow-v1"
            },
        ), patch.dict(os.environ, {}, clear=True):
            settings = runtime_settings_module.runtime_settings()

        self.assertEqual(
            "price-path-statistics-production-v2",
            settings["statisticalPriceSignalReleaseId"],
        )

    def test_native_rule_parallelism_default_matches_repository_capacity(self):
        with patch.object(runtime_settings_module, "load_local_env"), patch.object(
            runtime_settings_module,
            "read_settings_store",
            return_value={},
        ), patch.dict(os.environ, {}, clear=True):
            settings = runtime_settings_module.runtime_settings()

        self.assertEqual("4", settings["typedbNativeRuleParallelism"])

    def test_projection_writer_replaces_stale_cached_runtime_identity(self):
        current = {
            "contract": "orbit-runtime-identity-v1",
            "version": "current",
            "revision": "current-revision",
            "source": "environment",
            "python": "3.test",
        }
        snapshot = SimpleNamespace(metadata={})
        result = {
            "runtimeIdentity": {
                "contract": "orbit-runtime-identity-v1",
                "version": "stale",
                "revision": "stale-revision",
                "source": "cache",
            },
        }
        recorder = PortfolioOntologyProjectionRecorder(SimpleNamespace(store_key="typedb"))

        with patch(
            "digital_twin.infrastructure.ontology_projection.runtime_identity",
            return_value=current,
        ):
            recorder.store_projection_result(snapshot, result)

        self.assertEqual(current, result["runtimeIdentity"])
        self.assertEqual(current, snapshot.metadata["ontology"]["projection"]["runtimeIdentity"])


if __name__ == "__main__":
    unittest.main()
