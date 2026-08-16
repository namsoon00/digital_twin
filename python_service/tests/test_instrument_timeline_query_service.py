import unittest
from types import SimpleNamespace

from digital_twin.application.instrument_timeline_query_service import (
    InstrumentTimelineQueryService,
    mentions_symbol,
)
from digital_twin.domain.instrument_timeline import InstrumentTimelineQuery


class FakeTimeSeriesStore:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.calls = []

    def load_instrument_series(self, account_id, symbol, interval, limit, as_of=""):
        self.calls.append((account_id, symbol, interval, limit, as_of))
        return self.rows if interval == "1d" else []

    def active_backend_id(self):
        return "mysql-primary"


class FakeEvidenceStore:
    def latest(self, symbol="", kind="", limit=50):
        return [SimpleNamespace(to_dict=lambda: {
            "evidenceId": "ev-1",
            "symbol": symbol,
            "title": "실적 전망 변경",
            "summary": "시장 기대가 변경됐습니다.",
            "publishedAt": "2026-08-15T01:00:00Z",
            "source": "OpenDART",
            "polarity": "support",
        })]


class FakeCalendarStore:
    def list(self, **kwargs):
        return [SimpleNamespace(to_dict=lambda: {
            "eventId": "cal-1",
            "title": "실적 발표",
            "startsAt": "2026-08-20T00:00:00Z",
            "source": "OpenDART",
            "importance": 80,
        })]


class FakeDecisionStore:
    def list(self, account_id="", symbol="", limit=50):
        return [SimpleNamespace(to_dict=lambda: {
            "episodeId": "decision-1",
            "action": "HOLD",
            "decisionSummary": "추가 근거를 관찰합니다.",
            "decidedAt": "2026-08-15T02:00:00Z",
            "inferenceGenerationId": "generation-7",
        })]


class FakeHypothesisStore:
    def list_events(self, **kwargs):
        return [SimpleNamespace(to_dict=lambda: {
            "transitionId": "hyp-1",
            "lifecycleKey": "hypothesis:005930:growth",
            "previousState": "observed",
            "currentState": "strengthened",
            "currentStateLabel": "강화",
            "occurredAt": "2026-08-15T03:00:00Z",
            "inferenceGenerationId": "generation-7",
            "materialChange": True,
        })]


class FakeNotificationStore:
    def recent_for_symbol(self, symbol, account_id="", limit=100):
        return []

    def recent(self, limit=200):
        return [
            SimpleNamespace(to_dict=lambda: {
                "jobId": "notification-1",
                "messageType": "investmentInsight",
                "text": "삼성전자 판단 변경",
                "context": {"symbol": "005930"},
                "status": "sent",
                "updatedAt": "2026-08-15T04:00:00Z",
            }),
            SimpleNamespace(to_dict=lambda: {
                "jobId": "notification-other",
                "text": "다른 종목",
                "context": {"symbol": "000660"},
                "status": "sent",
                "updatedAt": "2026-08-15T05:00:00Z",
            }),
        ]


class FakeSymbolStore:
    def get(self, symbol, market=""):
        return SimpleNamespace(to_dict=lambda: {
            "symbol": symbol,
            "name": "삼성전자",
            "market": "KOSPI",
            "currency": "KRW",
        })


class InstrumentTimelineQueryServiceTest(unittest.TestCase):
    def service(self, rows=None):
        return InstrumentTimelineQueryService(
            FakeTimeSeriesStore(rows),
            FakeEvidenceStore(),
            FakeCalendarStore(),
            FakeDecisionStore(),
            FakeHypothesisStore(),
            FakeNotificationStore(),
            FakeSymbolStore(),
        )

    def test_composes_actual_candles_and_traceable_events(self):
        service = self.service([{
            "bucketAt": "2026-08-15T00:00:00Z",
            "openPrice": 70000,
            "highPrice": 71000,
            "lowPrice": 69500,
            "currentPrice": 70500,
            "volume": 1200,
            "provider": "toss-candles",
            "updatedAt": "2026-08-15T00:10:00Z",
        }])

        result = service.query(InstrumentTimelineQuery("005930", "main", "1w", "1h"))

        self.assertEqual(result["instrument"]["name"], "삼성전자")
        self.assertEqual(result["series"]["dataMode"], "actual")
        self.assertEqual(result["series"]["interval"], "1d")
        self.assertEqual(result["series"]["pointCount"], 1)
        self.assertEqual([item["type"] for item in result["events"]], [
            "calendar", "notification", "hypothesis", "decision", "evidence",
        ])
        self.assertEqual(result["events"][2]["metadata"]["inferenceGenerationId"], "generation-7")

    def test_empty_series_is_not_replaced_with_mock_data(self):
        result = self.service([]).query(InstrumentTimelineQuery("AAPL"))

        self.assertEqual(result["series"]["availability"], "no-data")
        self.assertEqual(result["series"]["dataMode"], "actual")
        self.assertEqual(result["series"]["candles"], [])

    def test_structured_symbol_match_does_not_accept_partial_code(self):
        self.assertTrue(mentions_symbol({"symbol": "005930"}, "005930"))
        self.assertFalse(mentions_symbol({"symbol": "10059300"}, "005930"))


if __name__ == "__main__":
    unittest.main()
