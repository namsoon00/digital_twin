from types import SimpleNamespace
import unittest

from digital_twin.modules.reasoning.application.projection_input.temporal import (
    temporal_observation_windows,
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
