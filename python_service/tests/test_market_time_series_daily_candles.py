import unittest

from digital_twin.domain.market_time_series import MarketTimeSeriesObservation


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


if __name__ == "__main__":
    unittest.main()
