import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.notification_ai_gate_message import execution_telegram_message
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.external_api_sources import external_api_source_metadata
from digital_twin.domain.investment_research import research_evidence_from_external_signals
from digital_twin.domain.investor_flow_psychology import investor_flow_psychology
from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notification_templates import NotificationTemplate, alert_context, render_notification
from digital_twin.domain.ontology_observation_quality import position_observation_profiles
from digital_twin.domain.portfolio import AccountSnapshot, AlertEvent, Position
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.monitoring import RealtimeMonitor
from digital_twin.infrastructure.external_signals import ExternalSignalProvider
from digital_twin.infrastructure.external_signal_provider_yfinance import (
    normalized_yfinance_earnings_estimates,
    normalized_yfinance_growth_data,
)
from digital_twin.infrastructure.external_signal_utils import sanitize_sensitive_text
from digital_twin.infrastructure.kis_market_signals import (
    KIS_FUNDAMENTAL_NORMALIZATION_VERSION,
    KISMarketSignalProvider,
    investor_estimate_selection,
    kis_fundamental_external_rows,
    normalize_estimate_perform,
    normalize_investment_opinions,
    stage_coverage,
)
from digital_twin.infrastructure.toss_snapshots import TossProvider, normalize_price_payload


class MemoryQuoteCache:
    def __init__(self, payloads=None):
        self.payloads = dict(payloads or {})

    def load(self, provider, account_id, symbol):
        return dict(self.payloads.get(str(symbol), {}))

    def save(self, provider, account_id, symbol, payload):
        self.payloads[str(symbol)] = dict(payload or {})


class ExternalApiSourceTests(unittest.TestCase):
    def test_fred_uses_dedicated_timeout_on_default_transport(self):
        settings = {
            "externalAlphaEnabled": "0",
            "externalCoinGeckoEnabled": "0",
            "externalFredEnabled": "1",
            "externalFredSeries": "DGS10",
            "externalFredMaxSeries": "1",
            "externalFredTimeoutSeconds": "7",
            "fredApiKey": "fred-key",
            "externalYFinanceEnabled": "0",
            "externalSecEnabled": "0",
            "externalDartEnabled": "0",
            "externalNewsEnabled": "0",
            "externalFxRateEnabled": "0",
            "externalApiRetryAttempts": "1",
            "externalApiRateLimitSeconds": "0",
        }
        provider = ExternalSignalProvider(
            settings=settings,
            cache=object(),
            evidence_store=object(),
        )

        with patch(
            "digital_twin.infrastructure.external_signal_provider_core.default_json_fetcher",
            return_value={"observations": [{"date": "2026-07-01", "value": "4.35"}]},
        ) as fetch:
            signals = provider.fetch_signals([])

        self.assertEqual(4.35, signals["macro"]["series"]["DGS10"]["value"])
        self.assertEqual(7.0, fetch.call_args.kwargs["timeout"])

    def test_kis_estimate_perform_maps_fiscal_blocks_to_valuation_evidence(self):
        def row(*values):
            return {f"data{index}": value for index, value in enumerate(values, start=1)}

        payload = {
            "output1": {"sht_cd": "035720", "item_kor_nm": "카카오", "estdate": "20260812"},
            "output2": [
                row(100, 110, 120, 130, 140),
                row(10, 20, 30, 40, 50),
                row(20, 30, 40, 50, 60),
                row(15, 25, 35, 45, 55),
                row(10, 15, 20, 25, 30),
                row(5, 10, 15, 20, 25),
            ],
            "output3": [
                row(30, 40, 50, 60, 70),
                row(66610, 127020, 130090, 138616, 101017),
                row(10, 20, 30, 40, 50),
                row(80, 100, 120, 160, 130),
                row(40, 50, 60, 70, 80),
                row(100, 110, 120, 130, 140),
                row(200, 190, 180, 170, 160),
                row(30, 40, 50, 60, 70),
            ],
            "output4": [{"dt": value} for value in ("202212", "202312", "202412", "202512", "202612")],
        }
        payload["output2"][1]["data4"] = ""

        normalized = normalize_estimate_perform(
            "035720",
            payload,
            "2026-08-13T00:00:00Z",
            now=datetime(2026, 8, 13, tzinfo=timezone.utc),
        )

        self.assertEqual("035720", normalized["symbol"])
        self.assertEqual(10101.7, normalized["earningsEstimates"][0]["base"])
        self.assertEqual("fy1", normalized["earningsEstimates"][0]["period"])
        self.assertEqual(KIS_FUNDAMENTAL_NORMALIZATION_VERSION, normalized["normalizationVersion"])
        self.assertEqual([8, 10, 12, 16], [item["value"] for item in normalized["multipleObservations"][:4]])
        self.assertEqual("historical", normalized["multipleObservations"][0]["basis"])
        self.assertEqual("current-market", normalized["multipleObservations"][-1]["basis"])
        self.assertIsNone(normalized["cycleData"][3]["revenueGrowthPct"])
        self.assertEqual(5.0, normalized["cycleData"][-1]["revenueGrowthPct"])

        rows = kis_fundamental_external_rows("035720", {
            "symbol": "035720",
            "name": "카카오",
            "currentPrice": 39000,
            "updatedAt": "2026-08-13T00:00:00Z",
            "fundamentalEstimates": normalized,
        })
        self.assertEqual(10101.7, rows["companyOverview"]["forwardEPS"])
        self.assertEqual(5, len(rows["companyOverview"]["multipleObservations"]))
        self.assertEqual(5, len(rows["earningsReport"]["cycleData"]))

    def test_kis_cached_quote_refreshes_fundamentals_without_refetching_quote(self):
        now = datetime(2026, 8, 16, 3, 0, tzinfo=timezone.utc)
        cache = MemoryQuoteCache()
        cached = {
            "symbol": "035420",
            "updatedAt": "2026-08-16T02:59:00Z",
            "fundamentalEstimates": {
                "fetchedAt": "2026-08-16T02:58:00Z",
                "earningsEstimates": [{"base": 101017}],
            },
        }
        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "key",
                "kisAppSecret": "secret",
                "kisFundamentalEstimatesEnabled": "1",
                "kisFundamentalRefreshSymbolsPerCycle": "2",
            },
            quote_cache=cache,
            now_provider=lambda: now,
        )
        provider.token = "token"
        refreshed = {
            "normalizationVersion": KIS_FUNDAMENTAL_NORMALIZATION_VERSION,
            "fetchedAt": "2026-08-16T03:00:00Z",
            "earningsEstimates": [{"base": 10101.7}],
        }

        with patch.object(provider, "fetch_fundamental_estimates", return_value=refreshed) as fetch:
            updated = provider.refresh_cached_fundamental_estimates("035420", cached)

        fetch.assert_called_once_with("035420")
        self.assertEqual(10101.7, updated["fundamentalEstimates"]["earningsEstimates"][0]["base"])
        self.assertEqual("2026-08-16T02:59:00Z", updated["updatedAt"])
        self.assertEqual(updated, cache.load("kis", "__market_signals__", "035420"))

    def test_kis_incompatible_fundamentals_are_removed_when_refresh_fails(self):
        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "key",
                "kisAppSecret": "secret",
                "kisFundamentalEstimatesEnabled": "1",
            },
            quote_cache=MemoryQuoteCache(),
            now_provider=lambda: datetime(2026, 8, 16, 3, 0, tzinfo=timezone.utc),
        )
        provider.token = "token"
        cached = {
            "symbol": "035420",
            "updatedAt": "2026-08-16T02:59:00Z",
            "fundamentalEstimates": {
                "fetchedAt": "2026-08-16T02:58:00Z",
                "earningsEstimates": [{"base": 101017}],
            },
        }

        with patch.object(provider, "fetch_fundamental_estimates", return_value={}):
            updated = provider.refresh_cached_fundamental_estimates("035420", cached)

        self.assertNotIn("fundamentalEstimates", updated)

    def test_optional_kis_fundamental_failure_does_not_open_quote_circuit(self):
        calls = []

        def failing_fetch(method, url, headers=None, body=None, query=None, timeout=12):
            calls.append({"method": method, "url": url, "timeout": timeout})
            raise TimeoutError("optional fundamental timeout")

        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "key",
                "kisAppSecret": "secret",
                "kisFundamentalEstimatesEnabled": "1",
                "kisFundamentalTimeoutSeconds": "5",
                "externalApiCircuitFailures": "1",
                "kisMarketSignalGapSeconds": "0",
            },
            quote_cache=MemoryQuoteCache(),
            fetch_json=failing_fetch,
            sleep=lambda _seconds: None,
        )
        provider.token = "token"

        result = provider.fetch_full_stage(
            "035720",
            "fundamental-estimate",
            "/uapi/domestic-stock/v1/quotations/estimate-perform",
            "HHKST668300C0",
            {"SHT_CD": "035720"},
        )

        self.assertEqual({}, result)
        self.assertEqual(1, len(calls))
        self.assertEqual(5, calls[0]["timeout"])
        self.assertTrue(provider.fundamental_circuit_open())
        self.assertTrue(provider.fundamental_circuit_open("fundamental-estimate"))
        self.assertFalse(provider.fundamental_circuit_open("fundamental-opinion"))
        self.assertFalse(provider.circuit_open())
        self.assertEqual(0, provider.consecutive_failures)
        self.assertEqual(1, len(provider.diagnostics["fundamentalFailures"]))

    def test_kis_last_close_does_not_replace_fresh_toss_premarket_quote(self):
        provider = KISMarketSignalProvider(
            settings={"kisMarketSignalsEnabled": "0"},
            quote_cache=MemoryQuoteCache(),
        )
        position = normalize_position({
            "symbol": "035420",
            "name": "NAVER",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 231500,
            "changeRate": 11.57,
            "quoteSource": "Toss /api/v1/prices",
            "quoteStatus": "토스 prices 반영",
            "quoteMessage": "현재가는 토스 prices 기준입니다.",
            "updatedAt": "2026-07-26T23:49:15Z",
            "freshnessStatus": "fresh",
            "marketSession": "open",
            "ma20": 197595,
            "ma60": 213982,
            "volume": 1234,
        })
        signal = {
            "currentPrice": 207500,
            "changeRate": 0,
            "volume": 70,
            "quoteSource": "KIS Open API",
            "quoteStatus": "KIS 현재가 반영",
            "quoteMessage": "KIS 장 마감 기준값입니다.",
            "updatedAt": "2026-07-26T23:50:13Z",
            "marketSignalCoverage": {
                "price": {
                    "status": "available",
                    "freshnessStatus": "last-close",
                    "cadence": "market-close-reference",
                    "latencyStatus": "market-closed-reference",
                },
                "orderbook": {"status": "available", "fields": ["orderbookBidVolume"]},
            },
        }

        merged = provider.merge_position(position, signal)

        self.assertEqual(231500, merged.current_price)
        self.assertEqual(11.57, merged.change_rate)
        self.assertEqual(1234, merged.volume)
        self.assertEqual("Toss /api/v1/prices", merged.quote_source)
        self.assertEqual("토스 prices 반영", merged.quote_status)
        self.assertEqual("2026-07-26T23:49:15Z", merged.updated_at)
        self.assertIn("신선한 현재가", merged.quote_message)
        self.assertAlmostEqual(8.19, merged.ma60_distance, places=2)

    def test_kis_rest_price_does_not_replace_fresh_toss_quote_without_websocket_tick(self):
        provider = KISMarketSignalProvider(
            settings={"kisMarketSignalsEnabled": "0"},
            quote_cache=MemoryQuoteCache(),
        )
        position = normalize_position({
            "symbol": "035420",
            "name": "NAVER",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 223500,
            "freshnessStatus": "fresh",
            "quoteSource": "Toss /api/v1/prices",
        })
        signal = {
            "currentPrice": 207500,
            "quoteSource": "KIS Open API",
            "marketSignalCoverage": {
                "price": {
                    "status": "available",
                    "freshnessStatus": "near-live",
                    "transport": "rest",
                },
                "ccnl": {"status": "unavailable", "fields": []},
            },
        }

        merged = provider.merge_position(position, signal)

        self.assertEqual(223500, merged.current_price)
        self.assertEqual("Toss /api/v1/prices", merged.quote_source)

    def test_kis_reuses_estimate_until_next_official_update_slot(self):
        cached = {
            "035720": {
                "foreignNetVolume": -226000,
                "institutionNetVolume": -4000,
                "updatedAt": "2026-08-10T01:01:00Z",
                "marketSignalCoverage": {
                    "investor": {
                        "status": "available",
                        "fields": ["foreignNetVolume", "institutionNetVolume"],
                        "measurementType": "intraday-estimate",
                        "providerUpdateCode": "2",
                        "providerUpdateSlot": "10:00",
                        "providerUpdateCurrent": True,
                        "sourceAsOf": "2026-08-10T10:00:00+09:00",
                        "validUntil": "2026-08-10T11:30:00+09:00",
                        "judgementEvidenceUsable": True,
                    }
                },
            }
        }
        calls = []

        def fake_fetch_json(method, url, headers=None, body=None, query=None, timeout=12):
            path = url.split("?", 1)[0]
            calls.append(path)
            if path.endswith("/oauth2/tokenP"):
                return {"access_token": "token"}
            if path.endswith("/inquire-price"):
                return {"rt_cd": "0", "output": {"stck_prpr": "39000", "acml_vol": "1000"}}
            if path.endswith("/inquire-ccnl"):
                return {"rt_cd": "0", "output": [{"stck_prpr": "39000", "tday_rltv": "88"}]}
            if path.endswith("/inquire-asking-price-exp-ccn"):
                return {"rt_cd": "0", "output1": {"total_bidp_rsqn": "1200", "total_askp_rsqn": "900"}}
            raise AssertionError("estimate should remain cached until the next official slot")

        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "key",
                "kisAppSecret": "secret",
                "kisMarketSignalGapSeconds": "0",
            },
            quote_cache=MemoryQuoteCache(cached),
            fetch_json=fake_fetch_json,
            now_provider=lambda: datetime(2026, 8, 10, 1, 30, tzinfo=timezone.utc),
        )

        signal = provider.fetch_symbol_signal("035720")

        self.assertFalse(any(path.endswith("/investor-trend-estimate") for path in calls))
        self.assertEqual(-226000, signal["foreignNetVolume"])
        self.assertEqual(-4000, signal["institutionNetVolume"])

    def snapshot_with_sources(self) -> AccountSnapshot:
        samsung = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            market_value=1000000,
            current_price=70000,
            quote_source="Toss /api/v1/prices + KIS Open API",
            market_signal_coverage={
                "ccnl": {"status": "available"},
                "orderbook": {"status": "available"},
                "investor": {"status": "available"},
            },
        )
        apple = Position(
            symbol="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            market_value=315,
            current_price=315,
            quote_source="Toss /api/v1/prices",
        )
        external_signals = {
            "fetchedAt": "2026-07-15T00:00:00Z",
            "fxRates": {
                "USDKRW": {
                    "provider": "RuntimeSettings",
                    "sourceType": "fallback_setting",
                    "rate": 1400,
                    "fallbackRate": 1400,
                    "marketProvider": "Alpha Vantage",
                    "marketSourceType": "market_daily",
                }
            },
            "equityQuotes": {"AAPL": {"provider": "Alpha Vantage", "price": 315}},
            "companyOverviews": {"AAPL": {"provider": "Alpha Vantage", "sector": "Technology"}},
            "earningsReports": {"AAPL": {"provider": "Alpha Vantage", "latestQuarter": {}}},
            "yfinanceData": {
                "AAPL": {
                    "provider": "yfinance",
                    "modulesCollected": ["history", "incomeStatement", "optionChains"],
                }
            },
            "cryptoMarkets": {"bitcoin": {"provider": "CoinGecko", "price": 100000}},
            "macro": {"series": {"DGS10": {"provider": "FRED", "value": 4.5}, "DGS2": {"provider": "FRED", "value": 4.1}}},
            "secFilings": {"AAPL": {"provider": "SEC EDGAR", "latestFiling": {"form": "10-Q"}}},
            "dartDisclosures": {"005930": {"provider": "OpenDART", "reportName": "주요사항보고서"}},
            "newsHeadlines": {
                "AAPL": {"provider": "Alpha Vantage", "items": [{"title": "Apple downgrade"}]},
                "005930": {"provider": "GDELT", "items": [{"title": "Samsung chip news"}]},
            },
            "statuses": [
                {"source": "Alpha Vantage", "ok": False, "message": "fx:USDKRW rate limit"},
                {"source": "GDELT News", "ok": True, "message": "doc:005930"},
            ],
        }
        return AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            "2026-07-15T00:00:00Z",
            portfolio_summary([samsung, apple], fx_rates={"USD": 1400, "KRW": 1}),
            [samsung, apple],
            [],
            external_signals=external_signals,
        )

    def test_monitor_stamps_external_api_metadata_but_renderer_hides_block(self):
        snapshot = self.snapshot_with_sources()
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "investmentInsight",
            "main:insight:AAPL",
            "Apple",
            ["상태: 보유 점검", "현재가: $315"],
            "AAPL",
        )
        stamped = RealtimeMonitor().stamp_events(snapshot, [event])[0]
        self.assertIn("externalApiSourceLines", stamped.metadata)

        message = render_notification(
            NotificationTemplate("investmentInsight", "{telegramMessage}"),
            alert_context(stamped),
        )

        self.assertNotIn("사용한 데이터 API", message)
        self.assertNotIn("API 조회 정보", message)
        self.assertNotIn("Alpha Vantage", message)
        self.assertNotIn("CoinGecko", message)
        self.assertNotIn("KIS", message)

    def test_yfinance_missing_fundamentals_keeps_quote_without_error_status(self):
        class FakeRecordsFrame:
            def __init__(self, rows):
                self.rows = list(rows)

            @property
            def empty(self):
                return not self.rows

            def reset_index(self):
                return self

            def tail(self, limit):
                return FakeRecordsFrame(self.rows[-int(limit):])

            def to_dict(self, orient="records"):
                return list(self.rows)

        class FakeTicker:
            options = []

            def __init__(self, _symbol):
                pass

            def history(self, **_kwargs):
                return FakeRecordsFrame([
                    {"Date": "2026-07-01", "Close": 94.0, "Volume": 1000},
                    {"Date": "2026-07-02", "Close": 98.0, "Volume": 1300},
                ])

            def get_info(self):
                raise RuntimeError(
                    'HTTP Error 404: {"quoteSummary":{"result":null,"error":{"code":"Not Found",'
                    '"description":"No fundamentals data found for symbol: MSTR"}}}'
                )

        previous_module = sys.modules.get("yfinance")
        sys.modules["yfinance"] = SimpleNamespace(Ticker=FakeTicker)
        try:
            provider = ExternalSignalProvider(
                settings={
                    "externalAlphaEnabled": "0",
                    "externalCoinGeckoEnabled": "0",
                    "externalFredEnabled": "0",
                    "externalDartEnabled": "0",
                    "externalSecEnabled": "0",
                    "externalNewsEnabled": "0",
                    "externalFxRateEnabled": "0",
                    "externalYFinanceEnabled": "1",
                    "externalYFinanceMaxSymbols": "1",
                    "externalYFinanceHistoryRows": "2",
                    "externalYFinanceOptionExpirations": "0",
                    "externalYFinanceNewsLimit": "0",
                    "externalApiRateLimitSeconds": "0",
                    "externalApiRetryAttempts": "1",
                },
                cache=object(),
                evidence_store=object(),
                fetch_json=lambda *_args, **_kwargs: {},
                sleep=lambda _: None,
            )
            signals = provider.fetch_signals([
                normalize_position({"symbol": "MSTR", "name": "Strategy", "market": "US", "currency": "USD"})
            ])
        finally:
            if previous_module is None:
                sys.modules.pop("yfinance", None)
            else:
                sys.modules["yfinance"] = previous_module

        payload = signals["yfinanceData"]["MSTR"]
        self.assertEqual(98.0, signals["equityQuotes"]["MSTR"]["price"])
        self.assertNotIn("info", payload)
        self.assertNotIn("errors", payload)
        self.assertEqual("expected-missing", payload["dataQualityNotes"][0]["status"])
        self.assertEqual("fundamentals-not-available", payload["dataQualityNotes"][0]["reason"])
        self.assertFalse([
            item for item in signals["statuses"]
            if item.get("source") == "yfinance" and not item.get("ok")
        ])

if __name__ == "__main__":
    unittest.main()
