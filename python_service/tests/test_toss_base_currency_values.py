import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.monitoring import RealtimeMonitor
from digital_twin.domain.portfolio_calculations import portfolio_summary, runtime_fx_currencies_from_external_signals
from digital_twin.domain.volume_time_adjustment import volume_pace_snapshot
from digital_twin.infrastructure.toss_snapshots import currency_rates_from_external_signals


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

    def test_portfolio_summary_uses_live_fx_over_toss_krw_value_when_external_rate_is_fresh(self):
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
        external_signals = {
            "fxRates": {
                "USDKRW": {
                    "provider": "Alpha Vantage",
                    "base": "USD",
                    "quote": "KRW",
                    "rate": 1425.5,
                }
            }
        }
        rates = currency_rates_from_external_signals({"fxRates": "KRW=1\nUSD=1400"}, external_signals)

        summary = portfolio_summary(
            [position],
            fx_rates=rates,
            runtime_fx_currencies=runtime_fx_currencies_from_external_signals(external_signals),
        )

        self.assertAlmostEqual(22540 * 1425.5, summary.invested)
        self.assertAlmostEqual(22540 * 1425.5, summary.total)

    def test_portfolio_summary_keeps_toss_krw_value_when_fx_is_only_runtime_setting(self):
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
        external_signals = {
            "fxRates": {
                "USDKRW": {
                    "provider": "RuntimeSettings",
                    "base": "USD",
                    "quote": "KRW",
                    "rate": 1425.5,
                }
            }
        }
        rates = currency_rates_from_external_signals({"fxRates": "KRW=1\nUSD=1400"}, external_signals)

        summary = portfolio_summary(
            [position],
            fx_rates=rates,
            runtime_fx_currencies=runtime_fx_currencies_from_external_signals(external_signals),
        )

        self.assertEqual(set(), runtime_fx_currencies_from_external_signals(external_signals))
        self.assertEqual(32000000, summary.invested)
        self.assertEqual(32000000, summary.total)

    def test_us_position_alert_value_uses_live_fx_over_stale_toss_krw_value(self):
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

        monitor = RealtimeMonitor({"fxRates": "KRW=1\nUSD=1400"})
        monitor.use_external_fx_rates({
            "fxRates": {
                "USDKRW": {
                    "base": "USD",
                    "quote": "KRW",
                    "rate": 1425.5,
                }
            }
        })
        line = monitor.position_market_value_line(position.to_dict())

        self.assertEqual("종목 평가금액: $22,540 (약 3,213만 원)", line)

    def test_external_fx_rate_is_used_when_toss_krw_value_is_missing(self):
        position = normalize_position(
            {
                "symbol": "STRC",
                "market": "US",
                "currency": "USD",
                "quantity": 24,
                "currentPrice": 87.4,
                "marketValue": 2097.6,
            }
        )
        external_signals = {
            "fxRates": {
                "USDKRW": {
                    "provider": "Alpha Vantage",
                    "base": "USD",
                    "quote": "KRW",
                    "rate": 1425.5,
                }
            }
        }

        rates = currency_rates_from_external_signals({"fxRates": "KRW=1\nUSD=1400"}, external_signals)
        summary = portfolio_summary([position], fx_rates=rates)
        monitor = RealtimeMonitor({"fxRates": "KRW=1\nUSD=1400"})
        monitor.use_external_fx_rates(external_signals)
        line = monitor.position_market_value_line(position.to_dict())

        self.assertEqual(1425.5, rates["USD"])
        self.assertAlmostEqual(2097.6 * 1425.5, summary.total)
        self.assertEqual("종목 평가금액: $2,098 (약 299만 원)", line)

    def test_volume_pace_snapshot_adjusts_raw_volume_ratio_by_market_session_time(self):
        pace = volume_pace_snapshot(
            "US",
            0.3,
            volume=8737438,
            trading_value=1050906655,
            observed_at="2026-07-13T10:00:00-04:00",
        )

        self.assertEqual("open", pace["volumePaceStatus"])
        self.assertEqual("regular", pace["volumePaceSession"])
        self.assertGreater(pace["timeAdjustedVolumeRatio"], pace["rawVolumeRatio"])
        self.assertIn("시간 대비", pace["volumePaceLabel"])

    def test_flow_context_line_shows_raw_and_time_adjusted_volume_ratio(self):
        monitor = RealtimeMonitor({"fxRates": "KRW=1\nUSD=1400"})
        position = {
            "symbol": "AAPL",
            "market": "US",
            "currency": "USD",
            "volume": 8737438,
            "volumeRatio": 0.3,
            "tradingValue": 1050906655,
            "updatedAt": "2026-07-13T10:00:00-04:00",
        }

        line = monitor.flow_context_line(position)

        self.assertIn("원본 0.3x", line)
        self.assertIn("시간보정", line)
        self.assertIn("미장 정규장", line)
        self.assertIn("현시점 기대", line)


if __name__ == "__main__":
    unittest.main()
