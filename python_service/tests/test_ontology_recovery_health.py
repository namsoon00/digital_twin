import unittest

from digital_twin.infrastructure.service_factory import typedb_projection_recovery_health


class OntologyRecoveryHealthTests(unittest.TestCase):
    def test_recovery_health_reopens_from_current_abox_even_when_old_inference_is_for_another_symbol(self):
        class Repository:
            def active_abox_metadata(self, world_id=""):
                return {"status": "ok", "aboxSnapshotId": "abox-manifest:current", "worldId": world_id}

            def inferencebox_recovery_metadata(self, world_id=""):
                return {
                    "status": "ok",
                    "worldId": world_id,
                    "inferenceGenerationId": "inference-generation:prior",
                    "sourceAboxSnapshotId": "abox-manifest:prior",
                    "targetSymbols": ["AAPL"],
                }

        payload = typedb_projection_recovery_health(Repository(), "portfolio:local:default")

        self.assertTrue(payload["ready"])
        self.assertEqual("active-abox-health-probe", payload["recoveryMode"])
        self.assertFalse(payload["inferenceGenerationAligned"])
        self.assertTrue(payload["requiresFreshProjection"])

    def test_recovery_health_stays_closed_when_active_abox_is_not_complete(self):
        class Repository:
            def active_abox_metadata(self, world_id=""):
                return {"status": "incomplete", "aboxSnapshotId": "abox-manifest:pending", "worldId": world_id}

            def inferencebox_recovery_metadata(self, world_id=""):
                return {"status": "missing", "worldId": world_id}

        payload = typedb_projection_recovery_health(Repository(), "portfolio:local:default")

        self.assertFalse(payload["ready"])
        self.assertEqual("incomplete", payload["activeAboxStatus"])


if __name__ == "__main__":
    unittest.main()
