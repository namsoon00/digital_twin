import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.notification_ai_gate_message import execution_telegram_message
from digital_twin.domain.external_api_sources import external_api_source_metadata
from digital_twin.domain.investment_research import research_evidence_from_external_signals
from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notification_templates import NotificationTemplate, alert_context, render_notification
from digital_twin.domain.portfolio import AccountSnapshot, AlertEvent, Position
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.monitoring import RealtimeMonitor
from digital_twin.infrastructure.external_signals import ExternalSignalProvider


class ExternalApiSourceTests(unittest.TestCase):
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

    def test_external_api_source_metadata_lists_all_used_sources(self):
        metadata = external_api_source_metadata(self.snapshot_with_sources())
        text = "\n".join(metadata["externalApiSourceLines"])

        for provider in ["Toss", "KIS", "Alpha Vantage", "yfinance", "CoinGecko", "FRED", "SEC EDGAR", "OpenDART", "GDELT"]:
            self.assertIn(provider, text)
        self.assertIn("RuntimeSettings", text)
        self.assertIn("환율", text)
        self.assertIn("실패", text)

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

    def test_yfinance_provider_collects_data_without_real_network_in_unit_test(self):
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

        class FakeStatementFrame:
            def __init__(self, rows):
                self.rows = dict(rows)
                first = next(iter(self.rows.values()), {})
                self.columns = list(first.keys())

            @property
            def empty(self):
                return not self.rows

            def iterrows(self):
                for metric, values in self.rows.items():
                    yield metric, values

        class FakeSeries:
            def __init__(self, values):
                self.values = list(values)

            @property
            def empty(self):
                return not self.values

            def tail(self, limit):
                return FakeSeries(self.values[-int(limit):])

            def items(self):
                return list(self.values)

        class FakeTicker:
            history_metadata = {"currency": "USD"}
            fast_info = {"lastPrice": 110.0}
            calendar = {"Earnings Date": "2026-07-30"}
            income_stmt = FakeStatementFrame({"Total Revenue": {"2025-12-31": 1000}})
            quarterly_income_stmt = FakeStatementFrame({"Total Revenue": {"2026-03-31": 260}})
            balance_sheet = FakeStatementFrame({"Total Assets": {"2025-12-31": 3000}})
            quarterly_balance_sheet = FakeStatementFrame({"Total Assets": {"2026-03-31": 3100}})
            cashflow = FakeStatementFrame({"Operating Cash Flow": {"2025-12-31": 400}})
            quarterly_cashflow = FakeStatementFrame({"Operating Cash Flow": {"2026-03-31": 105}})
            earnings_estimate = FakeRecordsFrame([{"period": "0q", "avg": 1.3}])
            revenue_estimate = FakeRecordsFrame([{"period": "0q", "avg": 1000}])
            eps_trend = FakeRecordsFrame([{"period": "0q", "current": 1.3}])
            eps_revisions = FakeRecordsFrame([{"period": "0q", "upLast7days": 2}])
            recommendations_summary = FakeRecordsFrame([{"period": "0m", "buy": 12, "hold": 6}])
            analyst_price_targets = {"mean": 125.0}
            institutional_holders = FakeRecordsFrame([{"Holder": "Fund A", "Shares": 1000}])
            options = ["2026-08-21"]
            actions = FakeRecordsFrame([])
            dividends = FakeSeries([])
            splits = FakeSeries([])
            capital_gains = FakeSeries([])
            funds_data = None
            news = []

            def __init__(self, _symbol):
                pass

            def history(self, **_kwargs):
                return FakeRecordsFrame([
                    {"Date": "2026-07-01", "Close": 100.0, "Volume": 1000},
                    {"Date": "2026-07-02", "Close": 110.0, "Volume": 1300},
                ])

            def get_info(self):
                return {
                    "longName": "Apple Inc.",
                    "quoteType": "EQUITY",
                    "currency": "USD",
                    "sector": "Technology",
                    "marketCap": 3200000000000,
                    "totalRevenue": 391000000000,
                    "currentPrice": 110.0,
                    "targetMeanPrice": 125.0,
                }

            def get_earnings_dates(self, limit=16):
                return FakeRecordsFrame([{
                    "Earnings Date": "2026-07-30",
                    "Reported EPS": 1.65,
                    "EPS Estimate": 1.58,
                }])

            def get_shares_full(self):
                return FakeSeries([("2026-07-01", 15000000000)])

            def option_chain(self, _expiration):
                return SimpleNamespace(
                    calls=FakeRecordsFrame([{"contractSymbol": "AAPL260821C00110000", "openInterest": 100, "volume": 20}]),
                    puts=FakeRecordsFrame([{"contractSymbol": "AAPL260821P00110000", "openInterest": 50, "volume": 10}]),
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
                    "externalYFinanceOptionExpirations": "1",
                    "externalApiRateLimitSeconds": "0",
                    "externalApiRetryAttempts": "1",
                },
                cache=object(),
                evidence_store=object(),
                fetch_json=lambda *_args, **_kwargs: {},
                sleep=lambda _: None,
            )
            signals = provider.fetch_signals([
                normalize_position({"symbol": "AAPL", "name": "Apple", "market": "US", "currency": "USD"})
            ])
        finally:
            if previous_module is None:
                sys.modules.pop("yfinance", None)
            else:
                sys.modules["yfinance"] = previous_module

        payload = signals["yfinanceData"]["AAPL"]
        self.assertEqual(110.0, signals["equityQuotes"]["AAPL"]["price"])
        self.assertEqual(125.0, signals["companyOverviews"]["AAPL"]["analystTargetPrice"])
        self.assertEqual(1.65, signals["earningsReports"]["AAPL"]["latestQuarter"]["reportedEPS"])
        self.assertEqual(0.5, payload["optionChains"][0]["summary"]["putCallOpenInterestRatio"])
        self.assertIn("incomeStatement", payload["modulesCollected"])
        self.assertEqual("fresh", payload["freshness"]["status"])
        self.assertEqual("fresh", payload["moduleFreshness"]["optionChains"]["status"])
        self.assertEqual(30, payload["moduleFreshness"]["optionChains"]["maxAgeMinutes"])
        evidence = research_evidence_from_external_signals("AAPL", signals)
        self.assertTrue(any(item.kind == "financial-fact" and item.raw_payload.get("provider") == "yfinance" for item in evidence))

    def test_yfinance_stale_modules_reduce_financial_fact_confidence(self):
        signals = {
            "yfinanceData": {
                "AAPL": {
                    "provider": "yfinance",
                    "querySymbol": "AAPL",
                    "collectedAt": "2000-01-01T00:00:00Z",
                    "modulesCollected": ["quote", "optionChains", "incomeStatement"],
                    "quote": {"price": 110.0},
                    "options": ["2026-08-21"],
                    "optionChains": [{"summary": {"putCallOpenInterestRatio": 0.5}}],
                    "incomeStatement": [{"metric": "Total Revenue", "values": {"2025-12-31": 1000}}],
                    "freshness": {
                        "status": "stale",
                        "reason": "quote 기준 30분 초과",
                        "staleModules": ["quote", "optionChains"],
                    },
                    "moduleFreshness": {
                        "quote": {"status": "stale", "maxAgeMinutes": 30},
                        "optionChains": {"status": "stale", "maxAgeMinutes": 30},
                        "incomeStatement": {"status": "fresh", "maxAgeMinutes": 129600},
                    },
                }
            }
        }

        evidence = research_evidence_from_external_signals("AAPL", signals)
        item = next(row for row in evidence if row.kind == "financial-fact")

        self.assertEqual(0.42, item.confidence)
        self.assertEqual("stale", item.raw_payload["freshness"]["status"])
        self.assertIn("stale-yfinance-modules", item.raw_payload["dataQualityRisk"])

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

    def test_ai_rewritten_message_hides_api_sources_from_alert_body(self):
        snapshot = self.snapshot_with_sources()
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "investmentInsight",
            "main:insight:AAPL",
            "Apple",
            ["현재가: $315", "수익률: +0.5%"],
            "AAPL",
        )
        stamped = RealtimeMonitor().stamp_events(snapshot, [event])[0]
        context = alert_context(stamped)
        context["telegramMessage"] = execution_telegram_message(
            context,
            NotificationAIValidatedResponse(
                action="HOLD",
                action_label="보유",
                confidence=70,
                summary="보유 판단입니다.",
                evidence=["가격 흐름을 확인했습니다."],
                opinion="바로 매매보다 확인이 우선입니다.",
            ),
        )

        message = render_notification(NotificationTemplate("investmentInsight", "{telegramMessage}"), context)

        self.assertNotIn("API 조회 정보", message)
        self.assertNotIn("사용한 데이터 API", message)
        self.assertNotIn("Alpha Vantage", message)
        self.assertNotIn("CoinGecko", message)
        self.assertNotIn("KIS", message)


if __name__ == "__main__":
    unittest.main()
