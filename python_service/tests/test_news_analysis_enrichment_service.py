import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.news_ai_analysis_service import NewsAiAnalysisService
from digital_twin.application.news_analysis_enrichment_service import NewsAnalysisEnrichmentRunner
from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence
from digital_twin.domain.news_ai_analysis import apply_news_ai_analysis, local_news_ai_analysis, news_ai_analysis_is_current
from digital_twin.domain.evidence_delta import EvidenceDelta, EvidenceMutation


class NewsAnalysisEnrichmentRunnerTests(unittest.TestCase):
    def evidence(self):
        evidence = ResearchEvidence(
            "research:AAPL:news:english-title",
            "AAPL",
            "news",
            "Reuters",
            "Apple launches a new services bundle for subscribers",
            "Apple announced a new services bundle with a lower monthly price for subscribers.",
            "https://example.test/apple-services",
            "2026-07-25T01:00:00Z",
            "context",
            published_at="2026-07-25T01:00:00Z",
            raw_payload={
                "name": "Apple",
                "market": "NASDAQ",
                "currency": "USD",
                "relationScope": "direct",
                "articleReadStatus": "body",
                "articleText": "Apple announced a new services bundle with a lower monthly price for subscribers.",
                "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
            },
        )
        target = NewsCollectionTarget("AAPL", "Apple", "NASDAQ", "USD", "Technology")
        local = local_news_ai_analysis(target, evidence).to_dict()
        local["status"] = "deferred"
        return apply_news_ai_analysis(evidence, local)

    def test_worker_enriches_deferred_english_article_and_persists_translation(self):
        item = self.evidence()

        class ExternalAnalyzer:
            def analyze_with_timeout(self, _target, _evidence, _timeout_seconds):
                return {
                    "status": "ok",
                    "model": "test-ai",
                    "sourceLanguage": "en",
                    "originalTitle": "Apple launches a new services bundle for subscribers",
                    "translatedTitleKo": "애플, 구독자용 신규 서비스 번들 출시",
                    "translationStatus": "complete",
                    "summary": {
                        "oneLineKo": "애플이 구독자용 신규 서비스 번들을 출시했습니다.",
                        "briefKo": "애플은 구독자를 위한 신규 서비스 번들을 출시하고 월 구독료를 낮췄다고 발표했습니다.",
                        "keyTakeaways": ["월 구독료 인하"],
                        "whyItMatters": "서비스 매출과 가입자 유지율 변화에 영향을 줄 수 있습니다.",
                        "watchPoints": ["다음 분기 서비스 매출"],
                    },
                }

            def analyze(self, target, evidence):
                return self.analyze_with_timeout(target, evidence, 30)

        class Store:
            def __init__(self):
                self.saved = []
                self.last_changed_items = []
                self.last_changed_symbols = []

            def latest(self, **_kwargs):
                return [item]

            def upsert_many(self, rows):
                self.saved = list(rows)
                self.last_changed_items = list(rows)
                self.last_changed_symbols = ["AAPL"]
                return len(self.saved)

        store = Store()
        runner = NewsAnalysisEnrichmentRunner(
            evidence_store=store,
            analysis_service=NewsAiAnalysisService(ExternalAnalyzer(), {"newsAiAnalysisEnabled": "1"}),
            settings={
                "newsAiAnalysisAsyncEnabled": "1",
                "newsAiAnalysisWorkerBatchSize": "1",
                "newsAiAnalysisTimeoutSeconds": "15",
                "newsAiAnalysisRetryMinutes": "1",
            },
        )

        result = runner.run_once()

        self.assertEqual(1, result["processedCount"])
        self.assertEqual(1, result["savedCount"])
        self.assertEqual(1, result["translatedCount"])
        saved = store.saved[0]
        self.assertEqual("complete", saved.raw_payload["translationStatus"])
        self.assertEqual("애플, 구독자용 신규 서비스 번들 출시", saved.raw_payload["translatedTitleKo"])
        self.assertEqual("ok", saved.raw_payload["aiAnalysis"]["status"])
        self.assertTrue(saved.raw_payload["aiAnalysis"]["lastExternalAttemptAt"])

    def test_worker_defers_before_querying_news_when_storage_reserve_is_low(self):
        class Store:
            def latest(self, **_kwargs):
                raise AssertionError("low-disk guard must run before the evidence query")

        runner = NewsAnalysisEnrichmentRunner(
            evidence_store=Store(),
            analysis_service=object(),
            settings={"newsAiAnalysisAsyncEnabled": "1"},
            storage_guard=lambda: {
                "status": "guarded-low-disk",
                "nonEssentialWritesAllowed": False,
                "freeMb": 12000,
            },
        )

        result = runner.run_once()

        self.assertEqual("deferred-low-disk", result["status"])
        self.assertEqual(0, result["processedCount"])
        self.assertEqual("guarded-low-disk", result["storage"]["status"])

    def test_worker_event_payload_serializes_evidence_deltas(self):
        item = self.evidence()
        runner = NewsAnalysisEnrichmentRunner(
            evidence_store=object(),
            analysis_service=None,
            settings={},
        )
        mutation = EvidenceMutation(
            written_count=1,
            changed_symbols=["AAPL"],
            changed_items=[item],
            deltas=[EvidenceDelta(
                evidence_id=item.evidence_id,
                symbol="AAPL",
                transition="modified",
                lifecycle_state="active",
            )],
        )

        events = runner._events_for_mutation(mutation, 1)

        self.assertEqual(item.evidence_id, events[0].payload["evidenceDeltas"][0]["evidenceId"])
        json.dumps(events[0].payload, ensure_ascii=False)

    def test_ready_korean_local_analysis_leaves_the_async_queue(self):
        evidence = ResearchEvidence(
            "research:005930:news:korean-ready",
            "005930",
            "news",
            "연합뉴스",
            "삼성전자가 신규 반도체 생산라인 투자를 발표했다",
            "삼성전자가 반도체 생산라인 투자 계획을 발표했습니다.",
            "https://example.test/samsung-investment",
            "2026-07-25T01:00:00Z",
            "context",
            published_at="2026-07-25T01:00:00Z",
            raw_payload={
                "name": "삼성전자",
                "relationScope": "direct",
                "articleReadStatus": "body",
                "articleText": "삼성전자가 신규 반도체 생산라인 투자를 발표하고 내년 양산 계획을 공개했습니다.",
                "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
            },
        )
        target = NewsCollectionTarget("005930", "삼성전자", "KOSPI", "KRW", "반도체")
        applied = apply_news_ai_analysis(evidence, local_news_ai_analysis(target, evidence).to_dict())
        runner = NewsAnalysisEnrichmentRunner(
            evidence_store=object(),
            analysis_service=None,
            settings={"newsAiAnalysisAsyncEnabled": "1"},
        )

        self.assertEqual("ready", applied.raw_payload["summaryQualityState"])
        self.assertEqual("local", applied.raw_payload["aiAnalysis"]["status"])
        self.assertTrue(news_ai_analysis_is_current(applied))
        self.assertFalse(runner.should_retry(applied))

    def test_worker_retries_stored_summary_with_navigation_headline_contamination(self):
        evidence = ResearchEvidence(
            "research:066570:news:navigation-contamination",
            "066570",
            "news",
            "Example News",
            "LG전자, 미국 주방가전 신뢰도 1위",
            "LG전자가 미국 주방가전 시장 공략을 강화합니다.",
            "https://example.test/lg-kitchen",
            "2026-07-25T01:00:00Z",
            "context",
            published_at="2026-07-25T01:00:00Z",
            raw_payload={
                "name": "LG전자",
                "relationScope": "direct",
                "articleReadStatus": "body",
                "articleText": "LG전자가 미국 소비자 평가에서 주방가전 신뢰도 1위에 올랐습니다.",
                "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
            },
        )
        target = NewsCollectionTarget("066570", "LG전자", "KOSPI", "KRW", "가전/전자")
        applied = apply_news_ai_analysis(evidence, local_news_ai_analysis(target, evidence).to_dict())
        applied.raw_payload["articleSummaryKo"] = "한화오션 수주… 방산 확대… 엔비디아 메모리… LG전자 주방가전"
        applied.raw_payload["articleSummaryQuality"] = {"state": "ready", "issues": []}
        applied.raw_payload["summaryQualityState"] = "ready"
        runner = NewsAnalysisEnrichmentRunner(
            evidence_store=object(),
            analysis_service=None,
            settings={"newsAiAnalysisAsyncEnabled": "1"},
        )

        self.assertTrue(runner.should_retry(applied))

    def test_worker_skips_stale_compact_timestamp_before_spending_ai_budget(self):
        item = self.evidence()
        item.published_at = "20260719T091519"
        item.observed_at = "20260719T091519"
        runner = NewsAnalysisEnrichmentRunner(
            evidence_store=object(),
            analysis_service=object(),
            settings={"newsAiAnalysisAsyncEnabled": "1", "newsEvidenceMaxAgeMinutes": "4320"},
        )

        self.assertFalse(runner.should_retry(item))

    def test_worker_prioritizes_material_direct_news_before_body_repair(self):
        material = self.evidence()
        material.raw_payload.update({
            "materialityState": "material",
            "relevanceState": "direct",
            "sourceTrustState": "trusted",
            "validationState": "conditional",
        })
        context = self.evidence()
        context.evidence_id = "research:AAPL:news:context-repair"
        context.raw_payload.update({
            "materialityState": "context",
            "relevanceState": "related",
            "sourceTrustState": "trusted",
            "validationState": "conditional",
        })

        runner = NewsAnalysisEnrichmentRunner(
            evidence_store=object(),
            analysis_service=object(),
            settings={"newsAiAnalysisAsyncEnabled": "1"},
        )

        self.assertGreater(runner.priority(material), runner.priority(context))

    def test_service_repairs_legacy_quarter_grounding_without_another_model_call(self):
        item = self.evidence()
        item.raw_payload["articleSummaryKo"] = "애플은 3분기 서비스 매출이 증가했다고 발표했습니다."
        item.raw_payload["articleText"] = "Apple reported that services revenue increased in the third quarter."
        item.raw_payload["aiAnalysis"]["sourceTextHash"] = local_news_ai_analysis(
            NewsCollectionTarget("AAPL", "Apple", "NASDAQ", "USD", "Technology"),
            item,
        ).source_text_hash
        item.raw_payload["aiAnalysis"]["status"] = "ok"
        item.raw_payload["translationStatus"] = "complete"
        item.raw_payload["articleSummaryQuality"] = {
            "state": "blocked",
            "issues": ["summary-number-not-grounded"],
        }
        item.raw_payload["summaryQualityState"] = "blocked"

        class Analyzer:
            def analyze(self, *_args, **_kwargs):
                raise AssertionError("deterministic quality repair must not call the model")

        class Store:
            def __init__(self):
                self.saved = []
                self.last_changed_items = []
                self.last_changed_symbols = []

            def latest(self, **_kwargs):
                return [item]

            def upsert_many(self, rows):
                self.saved = list(rows)
                self.last_changed_items = list(rows)
                self.last_changed_symbols = ["AAPL"]
                return len(self.saved)

        store = Store()
        runner = NewsAnalysisEnrichmentRunner(
            evidence_store=store,
            analysis_service=NewsAiAnalysisService(Analyzer(), {"newsAiAnalysisEnabled": "1"}),
            settings={
                "newsAiAnalysisAsyncEnabled": "1",
                "newsAiAnalysisWorkerBatchSize": "1",
                "newsAiAnalysisLocalRepairBatchSize": "25",
            },
        )

        result = runner.run_once()
        repaired = store.saved[0]

        self.assertEqual(1, result["localRepairCount"])
        self.assertEqual(0, result["modelProcessedCount"])
        self.assertEqual("ready", repaired.raw_payload["summaryQualityState"])
        self.assertNotIn(
            "summary-number-not-grounded",
            repaired.raw_payload["articleSummaryQuality"]["issues"],
        )


if __name__ == "__main__":
    unittest.main()
