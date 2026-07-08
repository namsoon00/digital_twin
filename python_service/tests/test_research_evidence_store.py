import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.investment_research import ResearchEvidence
from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.portfolio import utc_now_iso
from digital_twin.infrastructure.external_signals import ExternalSignalProvider
from digital_twin.infrastructure.sqlite_monitoring import SQLiteResearchEvidenceStore


class MemoryExternalSignalCache:
    def __init__(self, payload):
        self.payload = payload
        self.replaced = None

    def load(self):
        return self.payload

    def replace(self, payload):
        self.replaced = payload


class FixedCacheKeyExternalSignalProvider(ExternalSignalProvider):
    def cache_key_for_positions(self, positions):
        return "fixed-cache-key"


class ResearchEvidenceStoreTests(unittest.TestCase):
    def test_research_evidence_store_upserts_and_summarizes(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SQLiteResearchEvidenceStore(Path(temp) / "service.db")
            evidence = ResearchEvidence(
                "research:005930:news:1",
                "005930",
                "news",
                "Naver News",
                "삼성전자 실적 개선 기대",
                "반도체 업황 개선 보도",
                "https://example.test/news/1",
                "2026-07-08T01:00:00Z",
                "support",
                8.0,
                0.7,
            )

            self.assertEqual(1, store.upsert_many([evidence]))
            self.assertEqual(1, store.upsert_many([evidence]))

            latest = store.latest(symbol="005930")
            summary = store.summary()

            self.assertEqual(1, len(latest))
            self.assertEqual("삼성전자 실적 개선 기대", latest[0].title)
            self.assertEqual(1, summary["total"])
            self.assertEqual("005930", summary["bySymbol"][0]["name"])
            self.assertEqual("news", summary["byKind"][0]["name"])

    def test_research_evidence_store_deletes_by_id(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SQLiteResearchEvidenceStore(Path(temp) / "service.db")
            evidence = ResearchEvidence(
                "research:005930:news:delete",
                "005930",
                "news",
                "Naver News",
                "삭제 대상 근거",
                "품질 확인 후 제외할 근거",
                "https://example.test/news/delete",
                "2026-07-08T01:00:00Z",
                "context",
                0.0,
                0.5,
            )

            self.assertEqual(1, store.upsert_many([evidence]))
            self.assertTrue(store.delete("research:005930:news:delete"))
            self.assertFalse(store.delete("research:005930:news:delete"))
            self.assertEqual([], store.latest(symbol="005930"))
            self.assertEqual(0, store.summary()["total"])

    def test_external_signal_provider_records_evidence_from_fresh_cache(self):
        fetched_at = utc_now_iso()
        signals = {
            "fetchedAt": fetched_at,
            "equityQuotes": {},
            "cryptoMarkets": {},
            "macro": {},
            "secFilings": {},
            "dartDisclosures": {
                "005930": {
                    "provider": "OpenDART",
                    "reportName": "주요사항보고서(자기주식처분결정)",
                    "receiptNo": "20260707000403",
                    "receiptDate": "20260707",
                }
            },
            "newsHeadlines": {
                "005930": {
                    "provider": "Naver News",
                    "items": [
                        {
                            "title": "삼성전자 반도체 업황 개선 기대",
                            "summary": "메모리 가격 회복 기대",
                            "url": "https://example.test/news/semiconductor",
                            "seenDate": "20260708T090000Z",
                            "domain": "example.test",
                        }
                    ],
                }
            },
            "statuses": [],
        }
        cache = MemoryExternalSignalCache({
            "entries": {
                "fixed-cache-key": {
                    "fetchedAt": fetched_at,
                    "signals": signals,
                }
            },
            "providerState": {},
        })
        with tempfile.TemporaryDirectory() as temp:
            store = SQLiteResearchEvidenceStore(Path(temp) / "service.db")
            provider = FixedCacheKeyExternalSignalProvider(
                settings={"externalApiFetchIntervalMinutes": "30"},
                cache=cache,
                evidence_store=store,
            )

            result = provider.signals_for_positions([
                normalize_position({"symbol": "005930", "name": "삼성전자"})
            ])

            latest = store.latest(symbol="005930")

            self.assertIs(result, signals)
            self.assertIsNone(cache.replaced)
            self.assertEqual(2, len(latest))
            self.assertEqual({"disclosure", "news"}, {item.kind for item in latest})


if __name__ == "__main__":
    unittest.main()
