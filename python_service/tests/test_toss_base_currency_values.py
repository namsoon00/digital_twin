import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.monitoring import RealtimeMonitor
from digital_twin.domain.portfolio_calculations import portfolio_summary


class TossBaseCurrencyValueTests(unittest.TestCase):
    def test_us_position_keeps_toss_krw_evaluation_amount_separate_from_native_value(self):
        position = normalize_position(
            {
                "symbol": "MSTR",
                "market": "US",
                "currency": "USD",
                "quantity": 230,
                "currentPrice": 98,
                "marketValue": 22540,
                "evaluationAmount": 32000000,
                "profitLoss": 2093,
                "profitLossKrw": 2930200,
                "exchangeRate": 1419.7,
            }
        )

        self.assertEqual("USD", position.currency)
        self.assertEqual(22540, position.market_value)
        self.assertEqual(32000000, position.market_value_krw)
        self.assertEqual(2930200, position.profit_loss_krw)
        self.assertEqual(1419.7, position.exchange_rate)

    def test_portfolio_summary_prefers_toss_krw_value_over_runtime_fx_conversion(self):
        position = normalize_position(
            {
                "symbol": "MSTR",
                "market": "US",
                "currency": "USD",
                "quantity": 230,
                "currentPrice": 98,
                "marketValue": 22540,
                "evaluationAmount": 32000000,
            }
        )

        summary = portfolio_summary([position], fx_rates={"USD": 1400, "KRW": 1})

        self.assertEqual(32000000, summary.invested)
        self.assertEqual(32000000, summary.total)

    def test_us_position_alert_value_shows_native_and_toss_krw_value(self):
        position = normalize_position(
            {
                "symbol": "MSTR",
                "market": "US",
                "currency": "USD",
                "quantity": 230,
                "currentPrice": 98,
                "marketValue": 22540,
                "evaluationAmount": 32000000,
            }
        )

        line = RealtimeMonitor().position_market_value_line(position.to_dict())

        self.assertEqual("종목 평가금액: $22,540 (약 3,200만 원)", line)


if __name__ == "__main__":
    unittest.main()
