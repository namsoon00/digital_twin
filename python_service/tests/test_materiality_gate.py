import sys
import unittest
from datetime import datetime, timezone
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
from digital_twin.infrastructure.kis_realtime_ws import KISRealtimeSymbolSelector


class MaterialityGateTests(unittest.TestCase):
    def test_reasoning_worker_defaults_to_small_time_bounded_symbol_batch(self):
        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=None,
            monitor_runner_factory=lambda: None,
        )

        self.assertEqual(3, runner.max_symbols_per_run())

    def test_reasoning_worker_serializes_native_typedb_rule_subjects(self):
        request = ontology_reasoning_requested_event(
            DomainEvent(name="market_data.collected", aggregate_id="market:KR", payload={}),
            "market-data-update",
            ["005930", "000660", "035420"],
            changed_count=3,
            fact_types=["MarketQuote"],
        )
        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=None,
            monitor_runner_factory=lambda: None,
            settings={
                "ontologyReasoningMaxSymbolsPerRun": "3",
                "ontologyReasoningTypeDbNativeRuleExecutionEnabled": "1",
                "typedbNativeRuleTargetSymbolLimit": "1",
            },
        )

        batches, symbols, omitted = runner.request_symbol_batches([request])

        self.assertEqual(1, runner.effective_max_symbols_per_run())
        self.assertEqual(1, len(symbols))
        self.assertIn(symbols[0], ["005930", "000660", "035420"])
        self.assertEqual(symbols, batches[request.event_id])
        self.assertEqual(2, omitted)

    def test_reasoning_worker_keeps_configured_batch_without_native_typedb_rules(self):
        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=None,
            monitor_runner_factory=lambda: None,
            settings={
                "ontologyReasoningMaxSymbolsPerRun": "3",
                "ontologyReasoningTypeDbNativeRuleExecutionEnabled": "0",
                "typedbNativeRuleTargetSymbolLimit": "1",
            },
        )

        self.assertEqual(3, runner.effective_max_symbols_per_run())

    def test_reasoning_worker_prioritizes_live_holdings_over_background_realtime_ticks(self):
        background = ontology_reasoning_requested_event(
            DomainEvent(name="market_data.collected", aggregate_id="market:000150", payload={}),
            "kis-realtime-websocket",
            ["000150"],
            changed_count=1,
            fact_types=["MarketQuote", "ExecutionFlow"],
            materiality_assessments=[{"reviewLevel": "act", "changeState": "worsening"}],
        )
        holding = ontology_reasoning_requested_event(
            DomainEvent(name="market_data.collected", aggregate_id="market:005930", payload={}),
            "market-data-update",
            ["005930"],
            changed_count=1,
            fact_types=["MarketQuote"],
            materiality_assessments=[{"reviewLevel": "check", "changeState": "improving"}],
        )

        class Reader:
            def recent_events(self, **_kwargs):
                return [background, holding]

        class Cursor:
            def __init__(self):
                self.payload = {}
                self.ids = []

            def processed_event_ids(self):
                return list(self.ids)

            def mark_processed(self, event_ids):
                self.ids.extend(event_ids)

            def load(self):
                return dict(self.payload)

            def save(self, payload):
                self.payload = dict(payload or {})

        class FakeMonitorRunner:
            def __init__(self):
                self.accounts = []
                self.symbol_filter = []

            def run_once(self, dry_run=False, force=False, symbol_filter=None):
                self.symbol_filter = list(symbol_filter or [])
                return []

        fake_monitor = FakeMonitorRunner()
        runner = OntologyReasoningRunner(
            Reader(),
            Cursor(),
            monitor_runner_factory=lambda: fake_monitor,
            settings={
                "ontologyReasoningEnabled": "1",
                "ontologyReasoningMaxSymbolsPerRun": "1",
                "ontologyRuleCandidateAiEnabled": "0",
            },
            priority_symbols_provider=lambda: {"holdingSymbols": ["005930"]},
        )

        result = runner.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual(["005930"], fake_monitor.symbol_filter)

    def test_reasoning_worker_does_not_starve_a_new_holding_behind_old_event_residue(self):
        old_event = ontology_reasoning_requested_event(
            DomainEvent(name="market_data.collected", aggregate_id="market:residual", payload={}),
            "kis-realtime-update",
            ["005930", "000150", "000151"],
            changed_count=3,
            fact_types=["MarketQuote"],
            materiality_assessments=[{"reviewLevel": "act", "changeState": "worsening"}],
        )
        new_holding_event = ontology_reasoning_requested_event(
            DomainEvent(name="market_data.collected", aggregate_id="market:000660", payload={}),
            "market-data-update",
            ["000660"],
            changed_count=1,
            fact_types=["MarketQuote"],
            materiality_assessments=[{"reviewLevel": "check", "changeState": "new-condition"}],
        )

        class Cursor:
            def load(self):
                return {"eventSymbolProgress": {old_event.event_id: ["005930"]}}

        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=Cursor(),
            monitor_runner_factory=lambda: None,
            settings={"ontologyReasoningMaxSymbolsPerRun": "1"},
            priority_symbols_provider=lambda: {"holdingSymbols": ["005930", "000660"]},
        )

        batches, symbols, omitted = runner.request_symbol_batches([old_event, new_holding_event])

        self.assertEqual(["000660"], symbols)
        self.assertEqual(["000660"], batches[new_holding_event.event_id])
        self.assertNotIn(old_event.event_id, batches)
        self.assertEqual(2, omitted)

    def test_kis_configured_transport_symbols_do_not_enter_reasoning_queue_by_default(self):
        class Accounts:
            def load(self):
                return [SimpleNamespace(watchlist_symbols=["000660"])]

        monitor_store = SimpleNamespace(previous={
            "account": {"positions": [{"symbol": "005930", "market": "KR"}]},
        })
        cache = SimpleNamespace(stale_universe_symbols=lambda *_args, **_kwargs: [])
        selector = KISRealtimeSymbolSelector(
            Accounts(),
            monitor_store,
            cache,
            {"kisRealtimeWebSocketSymbols": "000150,000151"},
        )

        self.assertEqual(["005930", "000660"], selector.reasoning_symbols())

        selector.settings["kisRealtimeWebSocketIncludeConfiguredInReasoning"] = "1"
        self.assertEqual(["005930", "000660", "000150", "000151"], selector.reasoning_symbols())

    def test_reasoning_worker_coalesces_recent_symbol_events_and_releases_them_when_due(self):
        request = ontology_reasoning_requested_event(
            DomainEvent(name="market_data.collected", aggregate_id="market:AAPL", payload={}),
            "market-data-update",
            ["AAPL"],
            changed_count=1,
            fact_types=["MarketQuote"],
        )

        class Reader:
            def recent_events(self, **_kwargs):
                return [request]

        class Cursor:
            def __init__(self):
                self.payload = {"lastReasonedAtBySymbol": {"AAPL": "2026-07-20T00:00:00Z"}}

            def processed_event_ids(self):
                return []

            def load(self):
                return dict(self.payload)

            def save(self, payload):
                self.payload = dict(payload)

        cursor = Cursor()
        now = {"value": datetime(2026, 7, 20, 0, 2, tzinfo=timezone.utc)}
        runner = OntologyReasoningRunner(
            Reader(),
            cursor,
            monitor_runner_factory=lambda: None,
            settings={
                "ontologyReasoningMinIntervalSeconds": "180",
                "ontologyReasoningUrgentMinIntervalSeconds": "60",
            },
            now_provider=lambda: now["value"],
        )

        self.assertEqual([], runner.pending_requests())
        now["value"] = datetime(2026, 7, 20, 0, 3, tzinfo=timezone.utc)
        self.assertEqual([request.event_id], [event.event_id for event in runner.pending_requests()])

    def test_reasoning_worker_uses_shorter_interval_for_high_materiality_events(self):
        request = ontology_reasoning_requested_event(
            DomainEvent(name="market_data.collected", aggregate_id="market:AAPL", payload={}),
            "market-data-update",
            ["AAPL"],
            changed_count=1,
            fact_types=["MarketQuote"],
            materiality_assessments=[{"reviewLevel": "act", "changeState": "new-condition"}],
        )

        class Reader:
            def recent_events(self, **_kwargs):
                return [request]

        class Cursor:
            def processed_event_ids(self):
                return []

            def load(self):
                return {"lastReasonedAtBySymbol": {"AAPL": "2026-07-20T00:00:00Z"}}

        runner = OntologyReasoningRunner(
            Reader(),
            Cursor(),
            monitor_runner_factory=lambda: None,
            settings={
                "ontologyReasoningMinIntervalSeconds": "180",
                "ontologyReasoningUrgentMinIntervalSeconds": "60",
                "ontologyReasoningUrgentReviewLevels": "act,immediate,blocked",
            },
            now_provider=lambda: datetime(2026, 7, 20, 0, 1, tzinfo=timezone.utc),
        )

        self.assertEqual([request.event_id], [event.event_id for event in runner.pending_requests()])

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
        self.assertEqual("normal", small.grade)
        self.assertTrue(material.passed)
        self.assertIn("ma20-cross", material.matched_conditions)
        self.assertIn("volume-confirmation", material.matched_conditions)

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
            raw_payload={
                "relationScope": "sector",
                "relevanceState": "related",
                "sourceTrustState": "limited",
                "materialityState": "context",
                "articleReadStatus": "feed-summary",
            },
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
            raw_payload={
                "relationScope": "direct",
                "relevanceState": "direct",
                "sourceTrustState": "trusted",
                "materialityState": "material",
                "articleReadStatus": "body",
                "evidenceGovernance": {"investmentJudgmentEligible": True, "dataState": "sufficient"},
            },
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
            raw_payload={
                "relationScope": "sector",
                "relevanceState": "related",
                "sourceTrustState": "limited",
                "materialityState": "context",
                "articleReadStatus": "feed-summary",
            },
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
            raw_payload={
                "relationScope": "direct",
                "relevanceState": "direct",
                "sourceTrustState": "trusted",
                "materialityState": "material",
                "articleReadStatus": "body",
                "evidenceGovernance": {"investmentJudgmentEligible": True, "dataState": "sufficient"},
            },
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
                        "relevanceState": "direct",
                        "sourceTrustState": "standard",
                        "materialityState": "material",
                        "dataState": "partial",
                        "validationState": "conditional",
                        "summary": {"briefKo": item.title, "watchPoints": ["가격 반응"]},
                    }
                    payload["stockImpactLabel"] = payload["aiAnalysis"]["impactLabelKo"]
                    result.append(ResearchEvidence(
                        evidence_id=item.evidence_id,
                        symbol=item.symbol,
                        kind=item.kind,
                        source=item.source,
                        title=item.title,
                        summary=item.summary,
                        url=item.url,
                        observed_at=item.observed_at,
                        polarity=item.polarity,
                        published_at=item.published_at,
                        raw_payload=payload,
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

    def test_ontology_reasoning_projects_a_large_event_as_one_coherent_snapshot(self):
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
                self.payload = {
                    "processedEventIds": [],
                    "eventSymbolProgress": {"old-realtime-event": ["005930"]},
                }

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
                "ontologyReasoningCoherentSnapshotEnabled": "1",
                "ontologyReasoningCoherentSnapshotMaxSymbols": "20",
                "ontologyRuleCandidateAiEnabled": "0",
            },
        )

        first = runner.run_once()
        second = runner.run_once(force=True)
        third = runner.run_once()

        self.assertEqual([["AAPL", "MSFT"], ["TSLA"]], fake_monitor.calls)
        self.assertEqual(1, first["partialEventCount"])
        self.assertEqual(0, first["completedEventCount"])
        self.assertEqual(0, second["partialEventCount"])
        self.assertEqual(1, second["completedEventCount"])
        self.assertEqual("idle", third["status"])
        self.assertEqual([request.event_id], cursor.processed_event_ids())

    def test_ontology_reasoning_coalesces_superseded_realtime_snapshots_after_projection(self):
        source = DomainEvent(
            name="market_data.collected",
            aggregate_id="market:KR",
            payload={"changedCount": 2, "symbols": ["005930", "000660"]},
        )
        old_payload = ontology_reasoning_requested_event(
            source,
            "kis-realtime-websocket",
            ["005930", "000660"],
            changed_count=2,
            fact_types=["MarketQuote", "ExecutionFlow", "OrderBook"],
        ).payload
        new_payload = ontology_reasoning_requested_event(
            source,
            "kis-realtime-websocket",
            ["005930", "000660"],
            changed_count=2,
            fact_types=["MarketQuote", "ExecutionFlow", "OrderBook"],
        ).payload
        old_request = DomainEvent(
            name=ONTOLOGY_REASONING_REQUESTED,
            aggregate_id="ontology:old",
            payload=old_payload,
            occurred_at="2026-07-21T00:00:00Z",
            event_id="old-realtime-event",
        )
        new_request = DomainEvent(
            name=ONTOLOGY_REASONING_REQUESTED,
            aggregate_id="ontology:new",
            payload=new_payload,
            occurred_at="2026-07-21T00:00:15Z",
            event_id="new-realtime-event",
        )

        class Reader:
            def recent_events(self, **_kwargs):
                return [old_request, new_request]

        class Cursor:
            def __init__(self):
                self.payload = {"processedEventIds": []}

            def processed_event_ids(self):
                return list(self.payload["processedEventIds"])

            def mark_processed(self, event_ids):
                self.payload["processedEventIds"].extend(event_ids)

            def load(self):
                return dict(self.payload)

            def save(self, payload):
                self.payload = dict(payload or {})

        class FakeMonitorRunner:
            def __init__(self):
                self.accounts = []
                self.calls = []

            def run_once(self, dry_run=False, force=False, symbol_filter=None):
                self.calls.append({"force": force, "symbols": list(symbol_filter or [])})
                return []

        cursor = Cursor()
        monitor = FakeMonitorRunner()
        runner = OntologyReasoningRunner(
            Reader(),
            cursor,
            monitor_runner_factory=lambda: monitor,
            event_publisher=EventBus(),
            settings={"ontologyRuleCandidateAiEnabled": "0"},
        )

        result = runner.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["coalescedEventCount"])
        self.assertEqual([{"force": False, "symbols": ["000660", "005930"]}], monitor.calls)
        self.assertEqual({"old-realtime-event", "new-realtime-event"}, set(cursor.processed_event_ids()))
        self.assertNotIn("old-realtime-event", cursor.payload.get("eventSymbolProgress") or {})

    def test_ontology_reasoning_retries_type_db_projection_without_advancing_event_cursor(self):
        request = ontology_reasoning_requested_event(
            DomainEvent(
                name="market_data.collected",
                aggregate_id="market:KR",
                payload={"changedCount": 1, "symbols": ["000660"]},
            ),
            "market-data-update",
            ["000660"],
            changed_count=1,
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
                self.payload["processedEventIds"] = list(event_ids or [])

            def load(self):
                return dict(self.payload)

            def save(self, payload):
                self.payload = dict(payload or {})

        class FakeMonitorRunner:
            def __init__(self):
                self.accounts = [AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", [])]
                self.calls = []
                self.last_ontology_projection_results = {
                    "main": {
                        "status": "error",
                        "reason": "ABox staging verification failed",
                    },
                }

            def run_once(self, dry_run=False, force=False, symbol_filter=None):
                self.calls.append(list(symbol_filter or []))
                return []

        cursor = Cursor()
        monitor = FakeMonitorRunner()
        runner = OntologyReasoningRunner(
            Reader(),
            cursor,
            monitor_runner_factory=lambda: monitor,
            settings={
                "ontologyReasoningEnabled": "1",
                "ontologyReasoningProjectionRetrySeconds": "30",
                "ontologyRuleCandidateAiEnabled": "0",
            },
        )

        result = runner.run_once()

        self.assertEqual("deferred", result["status"])
        self.assertEqual(["000660"], monitor.calls[0])
        self.assertEqual([], cursor.processed_event_ids())
        self.assertNotIn("eventSymbolProgress", cursor.payload)
        self.assertNotIn("lastReasonedAtBySymbol", cursor.payload)
        self.assertIn("000660", cursor.payload["lastProjectionAttemptAtBySymbol"])

    def test_ontology_reasoning_applies_global_abox_projection_cooldown(self):
        request = ontology_reasoning_requested_event(
            DomainEvent(
                name="market_data.collected",
                aggregate_id="market:KR",
                payload={"changedCount": 1, "symbols": ["005930"]},
            ),
            "market-data-update",
            ["005930"],
            changed_count=1,
            fact_types=["MarketQuote"],
        )

        class Reader:
            def events(self, name="", aggregate_id="", limit=0):
                return [request] if name == ONTOLOGY_REASONING_REQUESTED else []

        class Cursor:
            def __init__(self):
                self.payload = {
                    "processedEventIds": [],
                    "lastSuccessfulProjectionAt": "2026-07-21T00:02:50Z",
                }

            def processed_event_ids(self):
                return list(self.payload.get("processedEventIds") or [])

            def mark_processed(self, event_ids):
                self.payload["processedEventIds"] = list(event_ids or [])

            def load(self):
                return dict(self.payload)

            def save(self, payload):
                self.payload = dict(payload or {})

        class FakeMonitorRunner:
            def run_once(self, **_kwargs):
                raise AssertionError("a whole ABox projection must not run during cooldown")

        runner = OntologyReasoningRunner(
            Reader(),
            Cursor(),
            monitor_runner_factory=FakeMonitorRunner,
            settings={
                "ontologyReasoningEnabled": "1",
                "ontologyReasoningMinIntervalSeconds": "180",
                "ontologyReasoningUrgentMinIntervalSeconds": "60",
                "ontologyRuleCandidateAiEnabled": "0",
            },
            now_provider=lambda: datetime(2026, 7, 21, 0, 3, 0, tzinfo=timezone.utc),
        )

        result = runner.run_once()

        self.assertEqual("cooldown", result["status"])
        self.assertEqual(170, result["retryAfterSeconds"])
        self.assertEqual([], runner.cursor_store.processed_event_ids())

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
