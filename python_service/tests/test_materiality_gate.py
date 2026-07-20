import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.news_collection_service import NewsCollectionRunner
from digital_twin.application.ontology_reasoning_service import OntologyReasoningRunner
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.events import DomainEvent, ONTOLOGY_REASONING_REQUESTED, RESEARCH_EVIDENCE_COLLECTED, ontology_reasoning_requested_event
from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence
from digital_twin.domain.materiality import evidence_materiality, market_change_materiality
from digital_twin.domain.portfolio import AlertEvent
from digital_twin.infrastructure.event_bus import EventBus


class MaterialityGateTests(unittest.TestCase):
    def test_reasoning_worker_marks_async_research_run_refreshed(self):
        class ResearchStore:
            def __init__(self):
                self.ids = []

            def mark_reasoning_refreshed(self, run_id, refreshed):
                self.ids.append((run_id, refreshed))
                return {"runId": run_id, "reasoningRefreshed": refreshed}

        research_store = ResearchStore()
        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=None,
            monitor_runner_factory=lambda: None,
            research_store=research_store,
        )
        request = DomainEvent(
            name=ONTOLOGY_REASONING_REQUESTED,
            aggregate_id="ontology:005930",
            payload={"researchRunId": "research-run-1", "symbols": ["005930"], "changedCount": 1},
        )

        refreshed = runner.mark_research_runs_refreshed([request])

        self.assertEqual(["research-run-1"], refreshed)
        self.assertEqual([("research-run-1", True)], research_store.ids)

    def test_market_materiality_blocks_small_refresh_and_passes_threshold_crossing(self):
        small = market_change_materiality(
            "AAPL",
            {"currentPrice": 100, "ma20Distance": 1.0, "volumeRatio": 1.0},
            {"currentPrice": 100.2, "ma20Distance": 1.1, "volumeRatio": 1.0},
            {"fields": ["currentPrice"]},
            {},
        )
        material = market_change_materiality(
            "AAPL",
            {"currentPrice": 100, "ma20Distance": 1.0, "volumeRatio": 1.0},
            {"currentPrice": 96, "ma20Distance": -2.5, "volumeRatio": 2.0},
            {"fields": ["currentPrice", "ma20Distance", "volumeRatio"]},
            {},
        )

        self.assertFalse(small.passed)
        self.assertEqual("record", small.grade)
        self.assertTrue(material.passed)
        self.assertIn("ma20Threshold", material.components)
        self.assertIn("volumeConfirmation", material.components)

    def test_evidence_materiality_requires_direct_reliable_material_news(self):
        weak = ResearchEvidence(
            "weak",
            "TSLA",
            "news",
            "Blog",
            "Tesla mentioned in broad EV roundup",
            "시장 일반 기사",
            "https://example.test/weak",
            "2026-07-10T01:00:00Z",
            "context",
            1.0,
            0.4,
            raw_payload={"relationScope": "sector", "relevanceScore": 40, "sourceReliability": 20, "materialityScore": 20},
        )
        strong = ResearchEvidence(
            "strong",
            "AAPL",
            "news",
            "Reuters",
            "Apple earnings guidance beats estimates",
            "실적 가이던스 상향",
            "https://example.test/strong",
            "2026-07-10T01:00:00Z",
            "support",
            8.0,
            0.8,
            raw_payload={"relationScope": "direct", "relevanceScore": 92, "sourceReliability": 88, "materialityScore": 82},
        )

        self.assertFalse(evidence_materiality(weak).passed)
        self.assertTrue(evidence_materiality(strong).passed)

    def test_news_collection_requests_reasoning_for_all_changed_evidence_and_keeps_materiality_gate(self):
        weak = ResearchEvidence(
            "weak",
            "TSLA",
            "news",
            "Blog",
            "Tesla mentioned in broad EV roundup",
            "시장 일반 기사",
            "https://example.test/weak",
            "2026-07-10T01:00:00Z",
            "context",
            1.0,
            0.4,
            raw_payload={"relationScope": "sector", "relevanceScore": 40, "sourceReliability": 20, "materialityScore": 20},
        )
        strong = ResearchEvidence(
            "strong",
            "AAPL",
            "news",
            "Reuters",
            "Apple earnings guidance beats estimates",
            "실적 가이던스 상향",
            "https://example.test/strong",
            "2026-07-10T01:00:00Z",
            "support",
            8.0,
            0.8,
            raw_payload={"relationScope": "direct", "relevanceScore": 92, "sourceReliability": 88, "materialityScore": 82},
        )

        class MemoryEvidenceStore:
            def upsert_many(self, items):
                self.last_changed_items = list(items)
                self.last_changed_symbols = [item.symbol for item in items]
                return len(items)

        class Gateway:
            def collect_for_target(self, target: NewsCollectionTarget):
                return ([strong] if target.symbol == "AAPL" else [weak]), []

            def providers(self):
                return ["unit"]

        events = EventBus()
        runner = NewsCollectionRunner(
            account_repository=SimpleNamespace(load=lambda: [AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL", "TSLA"])]),
            monitor_store=SimpleNamespace(previous={}),
            symbol_store=SimpleNamespace(get=lambda *_args: None),
            evidence_store=MemoryEvidenceStore(),
            gateway=Gateway(),
            settings={"newsCollectionRateLimitSeconds": "0", "newsEvidenceCleanupEnabled": "0", "newsEvidenceMaxAgeMinutes": "100000"},
            event_publisher=events,
            sleep_fn=lambda _seconds: None,
        )

        result = runner.run_once()

        self.assertEqual(2, result["changedCount"])
        self.assertEqual(["AAPL"], result["materialChangedSymbols"])
        self.assertEqual([RESEARCH_EVIDENCE_COLLECTED, ONTOLOGY_REASONING_REQUESTED], [event.name for event in events.published])
        self.assertEqual(["AAPL", "TSLA"], events.published[-1].payload["symbols"])
        self.assertEqual(2, events.published[-1].payload["changedCount"])
        self.assertEqual(2, len(events.published[-1].payload["materialityAssessments"]))

    def test_news_collection_adds_ai_analysis_before_store_and_event(self):
        first = ResearchEvidence(
            "first",
            "AAPL",
            "news",
            "Reuters",
            "Apple shares fall on earnings concern",
            "실적 우려",
            "https://example.test/first",
            "2026-07-10T01:00:00Z",
            "context",
            2.0,
            0.7,
            raw_payload={"relationScope": "direct", "relevanceScore": 95, "sourceReliability": 90, "materialityScore": 82},
        )
        second = ResearchEvidence(
            "second",
            "AAPL",
            "news",
            "Reuters",
            "Apple buyback plan improves sentiment",
            "자사주 매입",
            "https://example.test/second",
            "2026-07-10T01:01:00Z",
            "context",
            2.0,
            0.7,
            raw_payload={"relationScope": "direct", "relevanceScore": 92, "sourceReliability": 90, "materialityScore": 80},
        )

        class MemoryEvidenceStore:
            def __init__(self):
                self.saved_items = []

            def upsert_many(self, items):
                self.saved_items = list(items)
                self.last_changed_items = list(items)
                self.last_changed_symbols = ["AAPL"]
                return len(items)

        class Gateway:
            def collect_for_target(self, target: NewsCollectionTarget):
                return [first, second], []

            def providers(self):
                return ["unit"]

        class AnalysisService:
            def analyze_many(self, target, items):
                result = []
                for item in items:
                    payload = dict(item.raw_payload)
                    payload["aiAnalysis"] = {
                        "version": "news-ai-analysis-v1",
                        "impactPolarity": "risk" if item.evidence_id == "first" else "support",
                        "impactLabelKo": "악재" if item.evidence_id == "first" else "호재",
                        "confidence": 0.8,
                        "materialityScore": 88,
                        "summary": {"briefKo": item.title, "watchPoints": ["가격 반응"]},
                    }
                    payload["stockImpactLabel"] = payload["aiAnalysis"]["impactLabelKo"]
                    result.append(ResearchEvidence(
                        item.evidence_id,
                        item.symbol,
                        item.kind,
                        item.source,
                        item.title,
                        item.summary,
                        item.url,
                        item.observed_at,
                        item.polarity,
                        item.impact_score,
                        item.confidence,
                        item.published_at,
                        payload,
                    ))
                return result

        store = MemoryEvidenceStore()
        events = EventBus()
        runner = NewsCollectionRunner(
            account_repository=SimpleNamespace(load=lambda: [AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"])]),
            monitor_store=SimpleNamespace(previous={}),
            symbol_store=SimpleNamespace(get=lambda *_args: None),
            evidence_store=store,
            gateway=Gateway(),
            settings={"newsCollectionRateLimitSeconds": "0", "newsEvidenceCleanupEnabled": "0", "newsEvidenceMaxAgeMinutes": "100000"},
            event_publisher=events,
            article_analysis_service=AnalysisService(),
            sleep_fn=lambda _seconds: None,
        )

        result = runner.run_once()

        self.assertEqual(2, result["savedCount"])
        self.assertTrue(all(item.raw_payload.get("aiAnalysis") for item in store.saved_items))
        self.assertEqual("악재", result["changedItems"][0]["aiAnalysis"]["impactLabelKo"])
        self.assertEqual("호재", result["changedItems"][1]["aiAnalysis"]["impactLabelKo"])

    def test_news_collection_deletes_and_skips_stale_news(self):
        stale = ResearchEvidence(
            "stale-news",
            "AAPL",
            "news",
            "Reuters",
            "Apple old news",
            "오래된 기사",
            "https://example.test/stale",
            "2026-07-10T00:00:00Z",
            "context",
            2.0,
            0.7,
            published_at="2026-07-10T00:00:00Z",
            raw_payload={"relationScope": "direct", "relevanceScore": 90, "sourceReliability": 90, "materialityScore": 80},
        )
        fresh = ResearchEvidence(
            "fresh-news",
            "AAPL",
            "news",
            "Reuters",
            "Apple fresh news",
            "새 기사",
            "https://example.test/fresh",
            "2099-07-10T00:00:00Z",
            "support",
            8.0,
            0.8,
            published_at="2099-07-10T00:00:00Z",
            raw_payload={
                "relationScope": "direct",
                "relevanceScore": 95,
                "sourceReliability": 90,
                "materialityScore": 82,
                "articleReadStatus": "feed-summary",
                "articleFacts": {"bodyAvailable": False},
                "aiAnalysis": {"version": "news-ai-analysis-v1", "status": "ok"},
            },
        )

        class MemoryEvidenceStore:
            def __init__(self):
                self.saved_items = []

            def delete_stale_news(self, cutoff_iso, limit=500):
                self.cutoff_iso = cutoff_iso
                self.limit = limit
                return 3

            def upsert_many(self, items):
                self.saved_items = list(items)
                self.last_changed_items = list(items)
                self.last_changed_symbols = [item.symbol for item in items]
                return len(items)

        class Gateway:
            def collect_for_target(self, target: NewsCollectionTarget):
                return [stale, fresh], []

            def providers(self):
                return ["unit"]

        store = MemoryEvidenceStore()
        events = EventBus()
        runner = NewsCollectionRunner(
            account_repository=SimpleNamespace(load=lambda: [AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"])]),
            monitor_store=SimpleNamespace(previous={}),
            symbol_store=SimpleNamespace(get=lambda *_args: None),
            evidence_store=store,
            gateway=Gateway(),
            settings={
                "newsCollectionRateLimitSeconds": "0",
                "newsEvidenceMaxAgeMinutes": "60",
                "newsEvidenceCleanupBatchSize": "7",
                "newsArticleBodyFailureWarnRate": "0.1",
                "newsArticleBodyFailureMinimumCount": "1",
            },
            event_publisher=events,
            sleep_fn=lambda _seconds: None,
        )

        result = runner.run_once()

        self.assertEqual(1, result["savedCount"])
        self.assertEqual(["fresh-news"], [item.evidence_id for item in store.saved_items])
        self.assertEqual(1, result["staleSkippedCount"])
        self.assertEqual(3, result["staleDeletedCount"])
        self.assertEqual(7, store.limit)
        self.assertEqual("degraded", result["articleAnalysisHealth"]["status"])
        self.assertEqual(1, result["articleAnalysisHealth"]["bodyMissingCount"])
        self.assertEqual(3, result["articleAnalysisHealth"]["staleDeletedCount"])

    def test_ontology_reasoning_limits_monitoring_to_material_symbols(self):
        source = DomainEvent(
            name="research_evidence.collected",
            aggregate_id="news:AAPL",
            payload={"changedCount": 1, "symbols": ["AAPL"]},
        )
        request = ontology_reasoning_requested_event(
            source,
            "research-evidence-update",
            ["AAPL"],
            changed_count=1,
            observed_count=1,
            fact_types=["ResearchEvidence"],
        )

        class Reader:
            def events(self, name="", aggregate_id="", limit=0):
                return [request] if name == ONTOLOGY_REASONING_REQUESTED else []

        class Cursor:
            def __init__(self):
                self.ids = []

            def processed_event_ids(self):
                return list(self.ids)

            def mark_processed(self, event_ids):
                self.ids.extend(event_ids)

        class FakeMonitorRunner:
            def __init__(self):
                self.accounts = [AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", [])]
                self.symbol_filter = None

            def run_once(self, dry_run=False, force=False, symbol_filter=None):
                self.symbol_filter = list(symbol_filter or [])
                return [AlertEvent("main", "메인", "WATCH", "investmentInsight", "key", "Apple", ["관계 변화"], symbol="AAPL")]

        fake_monitor = FakeMonitorRunner()
        runner = OntologyReasoningRunner(
            Reader(),
            Cursor(),
            monitor_runner_factory=lambda: fake_monitor,
            event_publisher=EventBus(),
            settings={"ontologyReasoningEnabled": "1"},
        )

        result = runner.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual(["AAPL"], fake_monitor.symbol_filter)

    def test_ontology_reasoning_processes_large_events_by_symbol_cursor(self):
        request = ontology_reasoning_requested_event(
            DomainEvent(
                name="market_data.collected",
                aggregate_id="market:US",
                payload={"changedCount": 3, "symbols": ["AAPL", "MSFT", "TSLA"]},
            ),
            "market-data-update",
            ["AAPL", "MSFT", "TSLA"],
            changed_count=3,
            observed_count=3,
            fact_types=["MarketQuote"],
        )

        class Reader:
            def events(self, name="", aggregate_id="", limit=0):
                return [request] if name == ONTOLOGY_REASONING_REQUESTED else []

        class Cursor:
            def __init__(self):
                self.payload = {"processedEventIds": []}

            def processed_event_ids(self):
                return list(self.payload.get("processedEventIds") or [])

            def mark_processed(self, event_ids):
                payload = dict(self.payload)
                payload["processedEventIds"] = list(event_ids or [])
                self.payload = payload

            def load(self):
                return dict(self.payload)

            def save(self, payload):
                self.payload = dict(payload or {})

        class FakeMonitorRunner:
            def __init__(self):
                self.accounts = []
                self.calls = []

            def run_once(self, dry_run=False, force=False, symbol_filter=None):
                self.calls.append(list(symbol_filter or []))
                return []

        cursor = Cursor()
        fake_monitor = FakeMonitorRunner()
        runner = OntologyReasoningRunner(
            Reader(),
            cursor,
            monitor_runner_factory=lambda: fake_monitor,
            event_publisher=EventBus(),
            settings={
                "ontologyReasoningEnabled": "1",
                "ontologyReasoningMaxSymbolsPerRun": "2",
                "ontologyRuleCandidateAiEnabled": "0",
            },
        )

        first = runner.run_once()
        second = runner.run_once()

        self.assertEqual(["AAPL", "MSFT"], fake_monitor.calls[0])
        self.assertEqual(["TSLA"], fake_monitor.calls[1])
        self.assertEqual(1, first["partialEventCount"])
        self.assertEqual(1, second["completedEventCount"])
        self.assertEqual([request.event_id], cursor.processed_event_ids())

    def test_ontology_reasoning_reads_recent_events_when_supported(self):
        request = ontology_reasoning_requested_event(
            DomainEvent(name="market_data.collected", aggregate_id="market:KR", payload={}),
            "market-data-update",
            ["035420"],
            changed_count=1,
            fact_types=["MarketQuote"],
        )

        class Reader:
            def events(self, **_kwargs):
                raise AssertionError("oldest-first event scan must not be used")

            def recent_events(self, name="", aggregate_id="", limit=0):
                return [request] if name == ONTOLOGY_REASONING_REQUESTED else []

        class Cursor:
            def processed_event_ids(self):
                return []

        runner = OntologyReasoningRunner(
            Reader(),
            Cursor(),
            monitor_runner_factory=lambda: None,
            settings={"ontologyReasoningEnabled": "1"},
        )

        self.assertEqual([request.event_id], [event.event_id for event in runner.pending_requests()])


if __name__ == "__main__":
    unittest.main()
