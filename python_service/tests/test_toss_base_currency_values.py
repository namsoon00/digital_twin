import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.investor_flow_psychology import investor_flow_values_reliable
from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.monitoring import RealtimeMonitor
from digital_twin.domain.portfolio_calculations import (
    apply_position_base_currency_values,
    broker_fx_rates_from_positions,
    portfolio_summary,
    runtime_fx_currencies_from_external_signals,
)
from digital_twin.domain.volume_time_adjustment import trading_value_snapshot, volume_pace_snapshot
from digital_twin.infrastructure.external_signals import ExternalSignalProvider
from digital_twin.infrastructure.toss_snapshots import TossProvider, currency_rates_from_external_signals


class TossBaseCurrencyValueTests(unittest.TestCase):
    def test_volume_pace_requires_source_timestamp_for_time_adjustment(self):
        pace = volume_pace_snapshot("US", 0.3, volume=8737438, trading_value=1050906655)

        self.assertEqual("unavailable", pace["volumePaceStatus"])
        self.assertEqual("시간 보정 기준시각 없음", pace["volumePaceLabel"])
        self.assertEqual(0.3, pace["rawVolumeRatio"])
        self.assertNotIn("timeAdjustedVolumeRatio", pace)

    def test_live_quote_reprices_stale_holding_value_and_profit_rate(self):
        position = normalize_position(
            {
                "symbol": "000660",
                "market": "KR",
                "currency": "KRW",
                "quantity": 7,
                "averagePrice": 2343143,
                "currentPrice": 2074285.7,
                "marketValue": 14520000,
                "profitLossRate": -11.5,
            }
        )
        provider = TossProvider.__new__(TossProvider)

        merged = provider.merge_market_data(
            position,
            {"currentPrice": 1913000, "currency": "KRW", "market": "KR"},
            {},
            {},
            quote_live=True,
            indicators_live=False,
        )
        summary = portfolio_summary([merged], fx_rates={"KRW": 1})
        monitor = RealtimeMonitor({"fxRates": "KRW=1\nUSD=1400"})

        self.assertEqual(1913000, merged.current_price)
        self.assertEqual(13391000, merged.market_value)
        self.assertEqual(13391000, merged.market_value_krw)
        self.assertAlmostEqual(((1913000 - 2343143) / 2343143) * 100, merged.profit_loss_rate, places=4)
        self.assertEqual(
            "계좌 평가금액: 1,339만 원",
            monitor.account_market_value_line(summary, {merged.symbol: merged.to_dict()}),
        )

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

    def test_broker_fx_rate_backfills_missing_base_value_without_runtime_refresh_flag(self):
        position = normalize_position(
            {
                "symbol": "AAPL",
                "market": "US",
                "currency": "USD",
                "quantity": 1,
                "currentPrice": 315.0,
                "marketValue": 315.0,
                "exchangeRate": 1419.7,
                "quoteSource": "Toss holdings",
            }
        )
        external_signals = {
            "fxRates": {
                "USDKRW": {
                    "provider": "Toss",
                    "base": "USD",
                    "quote": "KRW",
                    "rate": 1419.7,
                    "sourceType": "broker_applied_valuation",
                    "evidenceStrength": "account_applied",
                }
            }
        }
        rates = currency_rates_from_external_signals({"fxRates": "KRW=1\nUSD=1400"}, external_signals)

        apply_position_base_currency_values(
            [position],
            rates,
            runtime_fx_currencies_from_external_signals(external_signals),
        )

        self.assertEqual(set(), runtime_fx_currencies_from_external_signals(external_signals))
        self.assertAlmostEqual(315.0 * 1419.7, position.market_value_krw)

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

    def test_trading_value_snapshot_replaces_inconsistent_reported_value(self):
        snapshot = trading_value_snapshot(95.97, 54577, 1050906655)

        self.assertEqual("estimated_from_price_volume", snapshot["tradingValueQuality"])
        self.assertFalse(snapshot["tradingValueReliable"])
        self.assertAlmostEqual(95.97 * 54577, snapshot["tradingValue"])
        self.assertEqual(1050906655, snapshot["reportedTradingValue"])

    def test_flow_context_line_includes_directional_execution_volumes(self):
        monitor = RealtimeMonitor()
        position = {
            "symbol": "005930",
            "market": "KR",
            "currency": "KRW",
            "tradeStrength": 89.2,
            "buyVolume": 1250000,
            "sellVolume": 2110000,
        }

        line = monitor.flow_context_line(position)

        self.assertIn("체결강도 89.2(매도 체결 우세)", line)
        self.assertIn("매수 체결 1,250,000주/매도 체결 2,110,000주", line)


if __name__ == "__main__":
    unittest.main()
