from types import SimpleNamespace
import unittest

from digital_twin.modules.reasoning.application.projection_input.temporal import (
    temporal_feature_input_from_packet,
    temporal_observation_windows,
)
from digital_twin.modules.reasoning.domain.reasoning_shadow import (
    pack_projection_runtime_contexts,
)


class Position:
    symbol = "MSTR"

    @staticmethod
    def is_cash():
        return False


class FailingTimeSeriesStore:
    @staticmethod
    def load_temporal_windows(*_args, **_kwargs):
        raise RuntimeError("daily partition unavailable")


class TemporalObservationWindowTests(unittest.TestCase):
    def test_feature_input_identity_is_recovered_from_frozen_packet(self):
        packet = pack_projection_runtime_contexts({
            "account:test": {
                "temporalFeatureSnapshot": {
                    "snapshotId": "temporal-feature:abc",
                    "payloadHash": "payload-hash",
                    "backendId": "mysql-primary",
                    "featureSetVersion": "features-v1",
                    "asOf": "2026-09-22T00:00:00Z",
                    "symbols": ["MSTR"],
                }
            }
        })

        result = temporal_feature_input_from_packet(packet, "account:test")

        self.assertEqual("temporal-feature:abc", result["snapshotId"])
        self.assertEqual("payload-hash", result["payloadHash"])
        self.assertEqual("mysql-primary", result["backendId"])
        self.assertEqual(["MSTR"], result["symbols"])

    def test_backend_failure_is_not_downgraded_to_empty_market_history(self):
        inputs = SimpleNamespace(
            market_time_series_store=FailingTimeSeriesStore(),
            settings={},
        )
        snapshot = SimpleNamespace(
            account_id="account:test",
            generated_at="2026-09-22T00:00:00Z",
            positions=[Position()],
            watchlist=[],
        )

        with self.assertRaisesRegex(RuntimeError, "daily partition unavailable"):
            temporal_observation_windows(inputs, snapshot)


if __name__ == "__main__":
    unittest.main()
