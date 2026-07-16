import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.notification_ai_gate_message import execution_telegram_message
from digital_twin.domain.external_api_sources import external_api_source_metadata
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notification_templates import NotificationTemplate, alert_context, render_notification
from digital_twin.domain.portfolio import AccountSnapshot, AlertEvent, Position
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.monitoring import RealtimeMonitor


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

        for provider in ["Toss", "KIS", "Alpha Vantage", "CoinGecko", "FRED", "SEC EDGAR", "OpenDART", "GDELT"]:
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
