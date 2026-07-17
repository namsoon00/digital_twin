import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

    def test_broker_fx_rate_is_derived_from_account_evaluation_amount(self):
        position = normalize_position(
            {
                "symbol": "MSTR",
                "market": "US",
                "currency": "USD",
                "quantity": 230,
                "currentPrice": 98,
                "marketValue": 22540,
                "evaluationAmount": 32000000,
                "exchangeRate": 1419.7,
                "quoteSource": "Toss holdings",
                "updatedAt": "2026-07-15T09:00:00Z",
            }
        )

        rates = broker_fx_rates_from_positions([position], fetched_at="2026-07-15T09:00:05Z")

        self.assertIn("USDKRW", rates)
        self.assertEqual("Toss", rates["USDKRW"]["provider"])
        self.assertEqual("broker_applied_valuation", rates["USDKRW"]["sourceType"])
        self.assertAlmostEqual(32000000 / 22540, rates["USDKRW"]["rate"])

    def test_external_signals_prioritize_broker_fx_rate_for_account_valuation(self):
        position = normalize_position(
            {
                "symbol": "MSTR",
                "market": "US",
                "currency": "USD",
                "quantity": 230,
                "currentPrice": 98,
                "marketValue": 22540,
                "evaluationAmount": 32000000,
                "quoteSource": "Toss holdings",
            }
        )
        provider = ExternalSignalProvider(
            settings={
                "fxRates": "KRW=1\nUSD=1400",
                "externalAlphaEnabled": "0",
                "externalFxRateEnabled": "1",
                "externalCoinGeckoEnabled": "0",
                "externalFredEnabled": "0",
                "externalDartEnabled": "0",
                "externalNewsEnabled": "0",
                "externalSecEnabled": "0",
                "externalYFinanceEnabled": "0",
            },
            cache=object(),
            evidence_store=object(),
        )

        signals = provider.fetch_signals([position])
        rates = currency_rates_from_external_signals({"fxRates": "KRW=1\nUSD=1400"}, signals)
        summary = portfolio_summary(
            [position],
            fx_rates=rates,
            runtime_fx_currencies=runtime_fx_currencies_from_external_signals(signals),
        )

        self.assertEqual("broker_applied_valuation", signals["fxRates"]["USDKRW"]["sourceType"])
        self.assertEqual("account_applied", signals["fxRates"]["USDKRW"]["evidenceStrength"])
        self.assertEqual(set(), runtime_fx_currencies_from_external_signals(signals))
        self.assertAlmostEqual(32000000 / 22540, rates["USD"])
        self.assertAlmostEqual(32000000, summary.total)

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

    def test_external_provider_error_message_masks_api_keys(self):
        provider = ExternalSignalProvider.__new__(ExternalSignalProvider)
        secret = "ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890"

        message = provider.safe_error_message(RuntimeError("apikey=" + secret + " daily limit reached"))

        self.assertNotIn(secret, message)
        self.assertIn("apikey=***", message)

        state = provider.provider_state_from({"providerState": {"alpha": {"lastError": "api key as " + secret}}})
        self.assertNotIn(secret, state["alpha"]["lastError"])
        self.assertIn("api key as ***", state["alpha"]["lastError"])

    def test_alpha_fx_rate_uses_daily_cache_after_success(self):
        calls = []

        def fake_fetch(url, _headers=None):
            calls.append(url)
            return {
                "Realtime Currency Exchange Rate": {
                    "5. Exchange Rate": "1419.7000",
                    "6. Last Refreshed": "2026-07-15 00:00:00",
                }
            }

        provider = ExternalSignalProvider(
            settings={
                "fxRates": "KRW=1\nUSD=1400",
                "alphaVantageApiKey": "test-key",
                "externalAlphaEnabled": "1",
                "externalFxRateEnabled": "1",
                "externalFxRateFetchIntervalHours": "24",
                "externalApiRetryAttempts": "1",
                "externalAlphaRateLimitSeconds": "0",
                "externalApiRateLimitSeconds": "0",
            },
            cache=object(),
            evidence_store=object(),
            fetch_json=fake_fetch,
            sleep=lambda _seconds: None,
        )
        provider.provider_state = {}

        first = provider.live_fx_rates({"statuses": []}, ["USD"])
        second = provider.live_fx_rates({"statuses": []}, ["USD"])

        self.assertEqual(1, len(calls))
        self.assertEqual(1419.7, first["USD"]["rate"])
        self.assertEqual(1419.7, second["USD"]["rate"])
        self.assertEqual("fresh-fetch", first["USD"]["cacheStatus"])
        self.assertEqual("daily-cache", second["USD"]["cacheStatus"])
        self.assertIn("alpha-vantage:fx-daily:USDKRW", provider.provider_state)

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

    def test_live_fx_backfills_position_base_currency_fields_before_snapshot_storage(self):
        position = normalize_position(
            {
                "symbol": "MSTR",
                "market": "US",
                "currency": "USD",
                "quantity": 230,
                "currentPrice": 94.55,
                "marketValue": 21746.5,
                "profitLoss": 1299.24,
                "evaluationAmount": 0,
                "exchangeRate": 0,
            }
        )
        external_signals = {
            "fxRates": {
                "USDKRW": {
                    "provider": "Alpha Vantage",
                    "base": "USD",
                    "quote": "KRW",
                    "rate": 1418.2,
                }
            }
        }
        rates = currency_rates_from_external_signals({"fxRates": "KRW=1\nUSD=1400"}, external_signals)

        apply_position_base_currency_values(
            [position],
            rates,
            runtime_fx_currencies_from_external_signals(external_signals),
        )
        summary = portfolio_summary(
            [position],
            fx_rates=rates,
            runtime_fx_currencies=runtime_fx_currencies_from_external_signals(external_signals),
        )

        self.assertEqual(1418.2, position.exchange_rate)
        self.assertAlmostEqual(21746.5 * 1418.2, position.market_value_krw)
        self.assertAlmostEqual(1299.24 * 1418.2, position.profit_loss_krw)
        self.assertAlmostEqual(position.market_value_krw, summary.total)

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

    def test_trading_value_snapshot_replaces_inconsistent_reported_value(self):
        snapshot = trading_value_snapshot(95.97, 54577, 1050906655)

        self.assertEqual("estimated_from_price_volume", snapshot["tradingValueQuality"])
        self.assertFalse(snapshot["tradingValueReliable"])
        self.assertAlmostEqual(95.97 * 54577, snapshot["tradingValue"])
        self.assertEqual(1050906655, snapshot["reportedTradingValue"])

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

    def test_flow_context_line_explains_inconsistent_us_trading_value(self):
        monitor = RealtimeMonitor({"fxRates": "KRW=1\nUSD=1400"})
        position = {
            "symbol": "MSTR",
            "market": "US",
            "currency": "USD",
            "currentPrice": 95.97,
            "volume": 54577,
            "volumeRatio": 0.034,
            "tradingValue": 1050906655,
            "updatedAt": "2026-07-16T05:30:00-04:00",
        }

        line = monitor.flow_context_line(position)

        self.assertIn("평균 대비 원본 0.03x", line)
        self.assertIn("가격×거래량 추정", line)
        self.assertIn("제공값 $1,050,906,655", line)
        self.assertIn("거래액 $5,237,755", line)


if __name__ == "__main__":
    unittest.main()
