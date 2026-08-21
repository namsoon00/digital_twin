import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.investment_research import ResearchEvidence
from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.portfolio import utc_now_iso
from digital_twin.infrastructure.external_signals import ExternalSignalProvider
from digital_twin.infrastructure.mysql_research_evidence import MySQLResearchEvidenceStore, merge_derived_evidence_payload
from mysql_fixtures import TestResearchEvidenceStore, mysql_test_settings, reset_mysql_test_database, test_store_seed


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
    def test_replayed_source_payload_preserves_verified_enrichment(self):
        previous = {
            "articleText": "Apple reported audited revenue and guidance.",
            "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True, "bodyQualityIssues": []},
            "entityResolution": {"targetSubjectConfirmed": True, "version": "news-entity-resolution-v2"},
            "newsEligibility": {"alertEligible": True, "reasoningEligible": False},
            "qualityGate": {"passed": True, "targetSubjectConfirmed": True},
        }
        replayed = {
            "articleText": "Apple reported audited revenue and guidance.",
            "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
            "qualityGate": {"passed": True},
        }

        merged = merge_derived_evidence_payload(previous, replayed)
        changed = merge_derived_evidence_payload(previous, {**replayed, "articleText": "A corrected article body."})

        self.assertTrue(merged["entityResolution"]["targetSubjectConfirmed"])
        self.assertIn("bodyQualityIssues", merged["articleFacts"])
        self.assertTrue(merged["qualityGate"]["targetSubjectConfirmed"])
        self.assertNotIn("entityResolution", changed)

    @staticmethod
    def news_evidence(evidence_id: str, published_at: str = "2026-07-08T01:00:00Z") -> ResearchEvidence:
        return ResearchEvidence(
            evidence_id,
            "005930",
            "news",
            "Reuters",
            "뉴스 " + evidence_id,
            "본문 " + evidence_id,
            "https://example.test/news/" + evidence_id,
            published_at,
            "context",
            published_at=published_at,
        )

    @staticmethod
    def direct_news_evidence(
        evidence_id: str,
        source: str = "Reuters",
        url: str = "https://example.test/news/direct",
        polarity: str = "support",
        fetched_at: str = "",
    ) -> ResearchEvidence:
        payload = {
            "relationScope": "direct",
            "articleReadStatus": "body",
            "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
            "evidenceGovernance": {"investmentJudgmentEligible": True, "dataState": "sufficient"},
            "sourceTrustState": "verified",
            "materialityState": "material",
            "dataState": "sufficient",
            "validationState": "verified",
            "stockImpactPolarity": polarity,
        }
        if fetched_at:
            payload["sourceFetchedAt"] = fetched_at
        return ResearchEvidence(
            evidence_id,
            "005930",
            "news",
            source,
            "삼성전자 HBM 수요 전망",
            "본문이 확인된 HBM 수요 전망 기사입니다.",
            url,
            "2026-07-08T01:00:00Z",
            polarity,
            published_at="2026-07-08T01:00:00Z",
            raw_payload=payload,
        )

    def test_upsert_sorts_and_splits_large_evidence_writes_into_short_transactions(self):
        with tempfile.TemporaryDirectory() as temp:
            reset_mysql_test_database(temp)
            settings = mysql_test_settings(test_store_seed(temp))
            settings["researchEvidenceWriteBatchSize"] = "2"
            store = MySQLResearchEvidenceStore(settings)
            captured = []

            saved, events = store.upsert_many_with_events(
                [
                    self.news_evidence("research:005930:news:3"),
                    self.news_evidence("research:005930:news:1"),
                    self.news_evidence("research:005930:news:4"),
                    self.news_evidence("research:005930:news:2"),
                ],
                lambda mutation: captured.append(mutation) or [],
            )

            self.assertEqual(4, saved)
            self.assertEqual([], events)
            self.assertEqual(2, len(captured))
            self.assertEqual(
                [
                    ["research:005930:news:1", "research:005930:news:2"],
                    ["research:005930:news:3", "research:005930:news:4"],
                ],
                [[item.evidence_id for item in mutation.changed_items] for mutation in captured],
            )
            self.assertEqual(
                ["research:005930:news:1", "research:005930:news:2", "research:005930:news:3", "research:005930:news:4"],
                [item.evidence_id for item in store.last_changed_items],
            )

    def test_news_analysis_work_queue_round_trips_against_mysql_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            reset_mysql_test_database(temp)
            store = MySQLResearchEvidenceStore(mysql_test_settings(test_store_seed(temp)))

            self.assertEqual(1, store.enqueue_news_analysis_work([{
                "evidenceId": "research:005930:news:queue",
                "subjectRevision": "news-analysis:0123456789abcdef0123456789abcdef",
                "workClass": "model",
                "priority": 90,
            }]))
            claimed = store.claim_news_analysis_work("test-worker", "model", 1, lease_seconds=60)

            self.assertEqual(1, len(claimed))
            self.assertEqual("research:005930:news:queue", claimed[0]["evidenceId"])
            self.assertEqual(1, store.finish_news_analysis_work(claimed, "test-worker"))
            completed = [
                row for row in store.news_analysis_work_status()["states"]
                if row["state"] == "completed" and row["workClass"] == "model"
            ]
            self.assertEqual(1, completed[0]["count"])

    def test_stale_cleanup_skips_a_row_locked_by_another_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            reset_mysql_test_database(temp)
            store = TestResearchEvidenceStore(test_store_seed(temp))
            locked = self.news_evidence("research:005930:news:locked")
            available = self.news_evidence("research:005930:news:available")
            self.assertEqual(2, store.upsert_many([locked, available]))
            connection = store.raw_connection(autocommit=False)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT evidence_id FROM research_evidence WHERE evidence_id = %s FOR UPDATE",
                        (locked.evidence_id,),
                    )
                    self.assertEqual(1, store.delete_stale_news("2026-07-10T00:00:00Z", limit=2))
            finally:
                connection.rollback()
                connection.close()

            self.assertEqual("active", store.get(locked.evidence_id).lifecycle_state)
            self.assertEqual("expired", store.get(available.evidence_id).lifecycle_state)

    def test_research_evidence_store_upserts_and_summarizes(self):
        with tempfile.TemporaryDirectory() as temp:
            reset_mysql_test_database(temp)
            store = TestResearchEvidenceStore(test_store_seed(temp))
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
            self.assertEqual(0, store.upsert_many([evidence]))
            updated = ResearchEvidence(
                "research:005930:news:1",
                "005930",
                "news",
                "Naver News",
                "삼성전자 실적 개선 기대",
                "반도체 업황 개선과 수요 회복 보도",
                "https://example.test/news/1",
                "2026-07-08T01:00:00Z",
                "support",
                8.5,
                0.72,
            )
            self.assertEqual(1, store.upsert_many([updated]))

            latest = store.latest(symbol="005930")
            summary = store.summary()

            self.assertEqual(1, len(latest))
            self.assertEqual("삼성전자 실적 개선 기대", latest[0].title)
            self.assertEqual("반도체 업황 개선과 수요 회복 보도", latest[0].summary)
            self.assertEqual(1, summary["total"])
            self.assertEqual("005930", summary["bySymbol"][0]["name"])
            self.assertEqual("news", summary["byKind"][0]["name"])

    def test_store_keeps_audit_rows_without_requeueing_refreshes_or_syndication(self):
        with tempfile.TemporaryDirectory() as temp:
            reset_mysql_test_database(temp)
            store = TestResearchEvidenceStore(test_store_seed(temp))
            first = self.direct_news_evidence("research:005930:news:direct")

            self.assertEqual(1, store.upsert_many([first]))
            first_revision = store.last_eligible_evidence_revisions["005930"]

            clock_refresh = self.direct_news_evidence(
                first.evidence_id,
                fetched_at="2026-07-08T01:05:00Z",
            )
            self.assertEqual(0, store.upsert_many([clock_refresh]))
            self.assertEqual({}, store.last_eligible_evidence_revisions)

            syndicated = self.direct_news_evidence(
                "research:005930:news:syndicated",
                source="Bloomberg",
                url="https://example.test/news/syndicated",
            )
            self.assertEqual(1, store.upsert_many([syndicated]))
            self.assertEqual({}, store.last_eligible_evidence_revisions)
            self.assertFalse(store.last_evidence_deltas[0]["changesInferenceEligibleSet"])
            self.assertEqual(2, len(store.latest(symbol="005930", limit=10)))

            risk_update = self.direct_news_evidence(first.evidence_id, polarity="risk")
            self.assertEqual(1, store.upsert_many([risk_update]))
            self.assertEqual(["005930"], sorted(store.last_eligible_evidence_revisions))
            self.assertNotEqual(first_revision, store.last_eligible_evidence_revisions["005930"])
            self.assertTrue(store.last_evidence_deltas[0]["changesInferenceEligibleSet"])

    def test_research_evidence_store_deletes_by_id(self):
        with tempfile.TemporaryDirectory() as temp:
            reset_mysql_test_database(temp)
            store = TestResearchEvidenceStore(test_store_seed(temp))
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

    def test_research_evidence_store_deletes_stale_news_only(self):
        with tempfile.TemporaryDirectory() as temp:
            reset_mysql_test_database(temp)
            store = TestResearchEvidenceStore(test_store_seed(temp))
            old_news = ResearchEvidence(
                "research:005930:news:old",
                "005930",
                "news",
                "Naver News",
                "오래된 뉴스",
                "삭제 대상",
                "https://example.test/news/old",
                "2026-07-08T01:00:00Z",
                "context",
                1.0,
                0.5,
            )
            fresh_news = ResearchEvidence(
                "research:005930:news:fresh",
                "005930",
                "news",
                "Naver News",
                "최신 뉴스",
                "보존 대상",
                "https://example.test/news/fresh",
                "2026-07-13T01:00:00Z",
                "support",
                8.0,
                0.8,
            )
            disclosure = ResearchEvidence(
                "research:005930:dart:old",
                "005930",
                "disclosure",
                "OpenDART",
                "오래된 공시",
                "뉴스 정리 대상이 아님",
                "https://example.test/dart/old",
                "2026-07-08T01:00:00Z",
                "context",
                1.0,
                0.7,
            )

            self.assertEqual(3, store.upsert_many([old_news, fresh_news, disclosure]))

            deleted = store.delete_stale_news("2026-07-10T00:00:00Z", limit=10)
            remaining = {item.evidence_id for item in store.latest(symbol="005930", limit=10)}

            self.assertEqual(1, deleted)
            self.assertNotIn("research:005930:news:old", remaining)
            self.assertIn("research:005930:news:fresh", remaining)
            self.assertIn("research:005930:dart:old", remaining)

    def test_expiration_keeps_audit_row_and_emits_an_eligible_set_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            reset_mysql_test_database(temp)
            store = TestResearchEvidenceStore(test_store_seed(temp))
            evidence = ResearchEvidence(
                "research:005930:news:lifecycle",
                "005930",
                "news",
                "Reuters",
                "삼성전자 실적 전망 상향",
                "본문이 확인된 실적 전망 기사",
                "https://example.test/news/lifecycle",
                "2026-07-08T01:00:00Z",
                "support",
                published_at="2026-07-08T01:00:00Z",
                raw_payload={
                    "relationScope": "direct",
                    "articleReadStatus": "body",
                    "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
                    "evidenceGovernance": {"investmentJudgmentEligible": True, "dataState": "sufficient"},
                },
            )

            self.assertEqual(1, store.upsert_many([evidence]))
            self.assertEqual(["005930"], sorted(store.last_eligible_evidence_revisions))

    def test_transaction_event_builder_receives_its_own_evidence_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            reset_mysql_test_database(temp)
            store = TestResearchEvidenceStore(test_store_seed(temp))
            evidence = ResearchEvidence(
                "research:005930:news:atomic-event",
                "005930",
                "news",
                "Reuters",
                "원자적 근거 변경",
                "본문이 확인된 실적 전망 기사",
                "https://example.test/news/atomic-event",
                "2026-07-08T01:00:00Z",
                "support",
                published_at="2026-07-08T01:00:00Z",
                raw_payload={
                    "relationScope": "direct",
                    "articleReadStatus": "body",
                    "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
                    "evidenceGovernance": {"investmentJudgmentEligible": True, "dataState": "sufficient"},
                },
            )
            captured = []

            saved, events = store.upsert_many_with_events(
                [evidence],
                lambda mutation: captured.append(mutation) or [],
            )

            self.assertEqual(1, saved)
            self.assertEqual([], events)
            self.assertEqual(["005930"], captured[0].inference_changed_symbols)
            self.assertIn("005930", captured[0].eligible_set_revisions)
            self.assertTrue(captured[0].deltas[0].changes_inference_eligible_set)
            self.assertTrue(store.last_evidence_deltas[0]["changesInferenceEligibleSet"])

            self.assertEqual(1, store.delete_stale_news("2026-07-10T00:00:00Z", limit=10))
            archived = store.get(evidence.evidence_id)

            self.assertEqual([], store.latest(symbol="005930"))
            self.assertEqual("expired", archived.lifecycle_state)
            self.assertEqual(1, store.summary()["auditTotal"])
            self.assertEqual("expiration", store.last_evidence_deltas[0]["transition"])
            self.assertTrue(store.last_evidence_deltas[0]["changesInferenceEligibleSet"])
            self.assertEqual(["005930"], sorted(store.last_eligible_evidence_revisions))

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
            reset_mysql_test_database(temp)
            store = TestResearchEvidenceStore(test_store_seed(temp))
            provider = FixedCacheKeyExternalSignalProvider(
                settings={
                    "externalApiFetchIntervalMinutes": "30",
                    "externalCoinGeckoEnabled": "0",
                },
                cache=cache,
                evidence_store=store,
            )

            result = provider.signals_for_positions([
                normalize_position({"symbol": "005930", "name": "삼성전자"})
            ])

            latest = store.latest(symbol="005930")

            self.assertEqual(signals["fetchedAt"], result["fetchedAt"])
            self.assertEqual(signals["dartDisclosures"], result["dartDisclosures"])
            self.assertEqual(signals["newsHeadlines"], result["newsHeadlines"])
            self.assertIsNone(cache.replaced)
            self.assertEqual(2, len(latest))
            self.assertEqual({"disclosure", "news"}, {item.kind for item in latest})


if __name__ == "__main__":
    unittest.main()
