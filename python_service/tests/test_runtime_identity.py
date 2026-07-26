import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.ontology_diagnostics_service import OntologyDiagnosticsService
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


if __name__ == "__main__":
    unittest.main()
