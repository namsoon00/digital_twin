import unittest

from digital_twin.domain.market_data import (
    derived_price_change_facts,
    normalize_position,
    technical_indicators_from_candles,
)


class MarketPriceChangeFactTests(unittest.TestCase):
    def candles(self):
        return [
            {"date": "2026-08-18", "close": 90},
            {"date": "2026-08-19", "close": 92},
            {"date": "2026-08-20", "close": 95},
            {"date": "2026-08-21", "close": 100},
            {"date": "2026-08-24", "close": 110},
        ]

    def test_current_quote_after_latest_candle_uses_latest_adjusted_close(self):
        indicators = technical_indicators_from_candles(
            self.candles(),
            adjustment_status="provider-adjusted",
        )

        facts = derived_price_change_facts(122.97, indicators, False)

        self.assertAlmostEqual(110, facts["previousClose"])
        self.assertAlmostEqual(11.7909, facts["changeRate"], places=3)
        self.assertAlmostEqual(29.4421, facts["return3d"], places=3)
        self.assertAlmostEqual(36.6333, facts["return5d"], places=3)
        self.assertTrue(facts["priceChangeUsable"])
        self.assertEqual("adjusted-daily-candles", facts["priceChangeSource"])

    def test_current_session_candle_uses_prior_adjusted_close(self):
        candles = self.candles() + [{"date": "2026-08-25", "close": 122.97}]
        indicators = technical_indicators_from_candles(
            candles,
            adjustment_status="provider-adjusted",
        )

        facts = derived_price_change_facts(122.97, indicators, True)

        self.assertAlmostEqual(110, facts["previousClose"])
        self.assertAlmostEqual(11.7909, facts["return1d"], places=3)
        self.assertIn("current-session-adjusted-candle", facts["priceChangeBasis"])

    def test_unverified_adjustment_status_cannot_author_return_facts(self):
        indicators = technical_indicators_from_candles(self.candles())

        self.assertEqual({}, derived_price_change_facts(122.97, indicators, False))

    def test_normalized_position_keeps_price_fact_provenance(self):
        position = normalize_position({
            "symbol": "MSTR",
            "currentPrice": 122.97,
            "changeRate": 11.79,
            "previousClose": 110,
            "return3d": 29.44,
            "priceChangeSource": "adjusted-daily-candles",
            "priceChangeBasis": "current-price-vs-previous-session-close",
            "priceHistoryAdjustment": "provider-adjusted",
            "priceChangeUsable": True,
        })

        self.assertEqual(110, position.previous_close)
        self.assertEqual(29.44, position.return_3d)
        self.assertTrue(position.price_change_usable)


if __name__ == "__main__":
    unittest.main()
