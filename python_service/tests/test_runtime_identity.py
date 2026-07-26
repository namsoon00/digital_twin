import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.ontology_diagnostics_service import OntologyDiagnosticsService
from digital_twin.infrastructure import settings as runtime_settings_module
from digital_twin.infrastructure.runtime_identity import runtime_identity


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


if __name__ == "__main__":
    unittest.main()
