import unittest

from digital_twin.modules.market_data.domain.market_time_series import (
    MarketTimeSeriesObservation,
    preserved_daily_observed_at,
)
from digital_twin.infrastructure.mysql_market_time_series import MySQLMarketTimeSeriesStore


class RecordingCursor:
    rowcount = 1


class RecordingConnection:
    def __init__(self):
        self.sql = ""
        self.params = ()

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return RecordingCursor()


class MarketTimeSeriesDailyCandleTests(unittest.TestCase):
    def candle(self, date="2026-09-10"):
        return {
            "date": date,
            "openPrice": 100,
            "highPrice": 110,
            "lowPrice": 95,
            "closePrice": 105,
            "volume": 1234,
        }

    def test_rejects_provider_date_that_is_still_a_future_us_session(self):
        observation = MarketTimeSeriesObservation.from_daily_candle(
            "__market_data__",
            "AAPL",
            self.candle(),
            market="US",
            currency="USD",
            received_at="2026-09-10T03:00:00Z",
        )

        self.assertFalse(observation.valid())
        self.assertEqual("", observation.bucket_at)

    def test_rejects_current_us_session_before_regular_close(self):
        observation = MarketTimeSeriesObservation.from_daily_candle(
            "__market_data__",
            "AAPL",
            self.candle(),
            market="US",
            currency="USD",
            received_at="2026-09-10T15:00:00Z",
        )

        self.assertFalse(observation.valid())

    def test_completed_us_candle_keeps_session_and_availability_clocks_separate(self):
        observation = MarketTimeSeriesObservation.from_daily_candle(
            "__market_data__",
            "AAPL",
            self.candle(),
            market="US",
            currency="USD",
            received_at="2026-09-10T21:00:00Z",
        )

        self.assertTrue(observation.valid())
        self.assertEqual("2026-09-10T04:00:00Z", observation.bucket_at)
        self.assertEqual("2026-09-10T20:00:00Z", observation.source_as_of)
        self.assertEqual("2026-09-10T21:00:00Z", observation.observed_at)

    def test_completed_korean_candle_uses_krx_close_time(self):
        observation = MarketTimeSeriesObservation.from_daily_candle(
            "__market_data__",
            "000660",
            self.candle(),
            market="KR",
            currency="KRW",
            received_at="2026-09-10T07:00:00Z",
        )

        self.assertTrue(observation.valid())
        self.assertEqual("2026-09-09T15:00:00Z", observation.bucket_at)
        self.assertEqual("2026-09-10T06:30:00Z", observation.source_as_of)
        self.assertEqual("2026-09-10T07:00:00Z", observation.observed_at)

    def test_daily_upsert_replaces_legacy_observation_before_market_close(self):
        observation = MarketTimeSeriesObservation.from_daily_candle(
            "__market_data__",
            "AAPL",
            self.candle(),
            market="US",
            currency="USD",
            received_at="2026-09-10T21:00:00Z",
        )
        connection = RecordingConnection()
        store = MySQLMarketTimeSeriesStore.__new__(MySQLMarketTimeSeriesStore)

        inserted = store.insert_observation_with_connection(
            connection,
            observation,
            replace=True,
            preserve_first_observed=True,
        )

        self.assertTrue(inserted)
        self.assertIn(
            "observed_at = IF(observed_at < VALUES(source_as_of), "
            "VALUES(observed_at), LEAST(observed_at, VALUES(observed_at)))",
            connection.sql,
        )

    def test_projection_preserves_existing_close_time_for_historical_daily_row(self):
        self.assertEqual(
            "2026-09-09T20:00:00Z",
            preserved_daily_observed_at(
                "2026-09-09T20:00:00Z",
                "2026-09-10T04:07:57Z",
                "2026-09-09T20:00:00Z",
            ),
        )

    def test_projection_replaces_observation_that_precedes_daily_close(self):
        self.assertEqual(
            "2026-09-10T04:07:57Z",
            preserved_daily_observed_at(
                "2026-09-09T19:00:00Z",
                "2026-09-10T04:07:57Z",
                "2026-09-09T20:00:00Z",
            ),
        )


if __name__ == "__main__":
    unittest.main()
