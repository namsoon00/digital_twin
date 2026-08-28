import socket
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.news_collection_service import NewsCollectionRunner
from digital_twin.application.kis_realtime_service import KISRealtimeWebSocketRunner
from digital_twin.application.news_ai_analysis_service import NewsAiAnalysisService
from digital_twin.application.ontology_reasoning_service import (
    OntologyReasoningRunner,
    event_order_key,
    event_review_level,
)
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.events import DomainEvent, NEWS_ARTICLE_ANALYZED, ONTOLOGY_REASONING_REQUESTED, RESEARCH_EVIDENCE_COLLECTED, ontology_reasoning_requested_event
from digital_twin.domain.fact_changes import market_fact_change
from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence
from digital_twin.domain.materiality import evidence_materiality, market_change_materiality
from digital_twin.domain.news_ai_analysis import local_news_ai_analysis
from digital_twin.domain.portfolio import AlertEvent
from digital_twin.infrastructure.event_bus import EventBus
from digital_twin.infrastructure.kis_realtime_ws import KISRealtimeSymbolSelector, KISRealtimeWebSocketClient
from digital_twin.infrastructure.news_sources import NewsSourceGateway


class MaterialityGateTests(unittest.TestCase):
    def test_market_fact_revision_ignores_refresh_timestamp_but_tracks_market_value(self):
        first = {
            "symbol": "AAPL",
            "currentPrice": 100,
            "volume": 1000,
            "ma20": 98,
            "updatedAt": "2026-07-24T00:00:00Z",
        }
        refresh = {**first, "updatedAt": "2026-07-24T00:01:00Z"}
        changed = {**refresh, "currentPrice": 101}

        first_revision = market_fact_change({}, first)["revisionId"]
        refresh_revision = market_fact_change(first, refresh)["revisionId"]
        changed_revision = market_fact_change(refresh, changed)["revisionId"]

        self.assertEqual(first_revision, refresh_revision)
        self.assertNotEqual(first_revision, changed_revision)

    def test_blocked_materiality_is_not_scheduled_as_urgent_investment_work(self):
        source = DomainEvent(name="market_data.collected", aggregate_id="market:KR", payload={})
        blocked = ontology_reasoning_requested_event(
            source,
            "market-data-update",
            ["005930"],
            changed_count=1,
            fact_types=["MarketQuote"],
            materiality_assessments=[{"reviewLevel": "blocked"}],
        )
        immediate = ontology_reasoning_requested_event(
            source,
            "market-data-update",
            ["000660"],
            changed_count=1,
            fact_types=["MarketQuote"],
            materiality_assessments=[{"reviewLevel": "immediate"}],
        )
        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=None,
            monitor_runner_factory=lambda: None,
            settings={"ontologyReasoningUrgentReviewLevels": "act,immediate,blocked"},
        )

        self.assertEqual("blocked", event_review_level(blocked))
        self.assertLess(event_order_key(blocked)[1], event_order_key(immediate)[1])
        self.assertEqual({"act", "immediate"}, runner.urgent_review_levels())

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
                "ontologyReasoningMarketMinIntervalSeconds": "180",
            },
            now_provider=lambda: now["value"],
        )

        self.assertEqual([], runner.pending_requests())
        now["value"] = datetime(2026, 7, 20, 0, 3, tzinfo=timezone.utc)
        self.assertEqual([request.event_id], [event.event_id for event in runner.pending_requests()])

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

    def test_market_materiality_does_not_requeue_a_stable_trend_for_a_volume_refresh(self):
        assessment = market_change_materiality(
            "MSTR",
            {
                "currentPrice": 100,
                "ma20Distance": 5.1,
                "ma60Distance": -24.4,
                "volume": 1000,
                "volumeRatio": 0.003,
            },
            {
                "currentPrice": 100,
                "ma20Distance": 5.1,
                "ma60Distance": -24.4,
                "volume": 1100,
                "volumeRatio": 0.003,
            },
            {"fields": ["volume"]},
            {},
        )

        self.assertFalse(assessment.passed)
        self.assertEqual([], assessment.matched_conditions)
        self.assertIn("기존 상태가 유지", assessment.reason)

    def test_market_materiality_does_not_queue_last_close_transition_for_every_holding(self):
        previous = {
            "symbol": "005930",
            "currentPrice": 70000,
            "dataQuality": "actual",
            "freshnessStatus": "realtime",
            "sourceTimestampState": "websocket-received",
            "latencyStatus": "live-transport",
            "marketSession": "open",
            "marketSessionLabel": "국장 정규장",
            "realTime": True,
        }
        current = {
            **previous,
            "dataQuality": "reference",
            "freshnessStatus": "last-close",
            "sourceTimestampState": "provider-last-close",
            "latencyStatus": "last-close",
            "marketSession": "closed",
            "marketSessionLabel": "국장 휴장",
            "realTime": False,
        }

        change = market_fact_change(previous, current)
        assessment = market_change_materiality("005930", previous, current, change, {})

        self.assertTrue(change["changed"])
        self.assertIn("marketSession", change["fields"])
        self.assertFalse(assessment.passed)
        self.assertEqual("normal", assessment.grade)
        self.assertEqual("reference-transition", assessment.change_state)
        self.assertIn("source-validity-state-change", assessment.matched_conditions)

    def test_news_collection_requests_reasoning_only_for_eligible_evidence_set_changes(self):
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
            "https://www.reuters.com/technology/apple-earnings-guidance",
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
                "bodyQualityPassed": True,
                "articleText": "Apple reported earnings and raised its full-year guidance after audited revenue beat estimates. " * 5,
                "articleSummaryKo": "애플이 실적 예상치를 웃돌고 연간 가이던스를 상향했습니다.",
                "summaryQualityState": "ready",
                "articleSummaryQuality": {"state": "ready", "issues": []},
                "aiAnalysis": {
                    "status": "ok", "needsReview": False, "decisionInlineEligible": True,
                    "analyzedAt": "2026-07-10T01:30:00Z",
                    "version": "news-ai-analysis-test-v1",
                    "sourceTextHash": "test-article-body-hash",
                    "validationState": "ready",
                    "dataState": "sufficient",
                },
                "articleAiAnalysisVersion": "news-ai-analysis-test-v1",
                "newsEligibility": {
                    "displayEligible": True, "alertEligible": True, "reasoningEligible": True,
                },
                "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True, "readStatus": "body"},
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
        company_by_symbol = {
            "AAPL": SimpleNamespace(symbol="AAPL", name="Apple", market="NASDAQ", currency="USD", sector="Technology"),
            "TSLA": SimpleNamespace(symbol="TSLA", name="Tesla", market="NASDAQ", currency="USD", sector="Automotive"),
        }
        runner = NewsCollectionRunner(
            account_repository=SimpleNamespace(load=lambda: [AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL", "TSLA"])]),
            monitor_store=SimpleNamespace(previous={}),
            symbol_store=SimpleNamespace(get=lambda symbol, *_args: company_by_symbol.get(symbol)),
            evidence_store=MemoryEvidenceStore(),
            gateway=Gateway(),
            settings={"newsCollectionRateLimitSeconds": "0", "newsEvidenceCleanupEnabled": "0", "newsEvidenceMaxAgeMinutes": "100000"},
            event_publisher=events,
            sleep_fn=lambda _seconds: None,
            now_provider=lambda: datetime(2026, 7, 10, 2, 0, tzinfo=timezone.utc),
        )

        result = runner.run_once()

        self.assertEqual(2, result["changedCount"])
        self.assertEqual(["AAPL"], result["materialChangedSymbols"])
        self.assertEqual([RESEARCH_EVIDENCE_COLLECTED, NEWS_ARTICLE_ANALYZED, ONTOLOGY_REASONING_REQUESTED], [event.name for event in events.published])
        self.assertEqual(["AAPL"], events.published[-1].payload["symbols"])
        self.assertEqual(1, events.published[-1].payload["changedCount"])
        self.assertEqual(2, len(events.published[-1].payload["materialityAssessments"]))

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

    def test_ontology_reasoning_durably_supersedes_stale_ticks_before_storage_deferral(self):
        source = DomainEvent(name="market_data.collected", aggregate_id="market:KR", payload={})
        old_request = ontology_reasoning_requested_event(
            source,
            "market-data-update",
            ["005930"],
            changed_count=1,
            fact_types=["MarketQuote"],
        )
        new_request = ontology_reasoning_requested_event(
            source,
            "market-data-update",
            ["005930"],
            changed_count=1,
            fact_types=["MarketQuote"],
        )
        old_request = DomainEvent(
            name=old_request.name,
            aggregate_id=old_request.aggregate_id,
            payload=old_request.payload,
            correlation_id=old_request.correlation_id,
            occurred_at="2026-07-21T00:00:00Z",
            event_id="old-realtime-event",
        )
        new_request = DomainEvent(
            name=new_request.name,
            aggregate_id=new_request.aggregate_id,
            payload=new_request.payload,
            correlation_id=new_request.correlation_id,
            occurred_at="2026-07-21T00:00:15Z",
            event_id="new-realtime-event",
        )

        class Reader:
            def recent_events(self, **_kwargs):
                return [old_request, new_request]

        class Cursor:
            def __init__(self):
                self.payload = {"processedEventIds": [], "eventSymbolProgress": {}}
                self.superseded = []

            def processed_event_ids(self):
                return list(self.payload["processedEventIds"])

            def mark_superseded(self, event_ids):
                self.superseded.extend(event_ids)
                self.payload["processedEventIds"].extend(event_ids)
                for event_id in event_ids:
                    self.payload["eventSymbolProgress"].pop(event_id, None)

            def load(self):
                return dict(self.payload)

            def save(self, payload):
                self.payload = dict(payload or {})

        cursor = Cursor()
        runner = OntologyReasoningRunner(
            Reader(),
            cursor,
            monitor_runner_factory=lambda: self.fail("monitor must not start while TypeDB storage is blocked"),
            settings={"ontologyReasoningEnabled": "1"},
            storage_guard=lambda: {"ready": False, "reason": "disk reserve"},
        )

        result = runner.run_once()

        self.assertEqual("deferred", result["status"])
        self.assertEqual(["old-realtime-event"], cursor.superseded)
        self.assertNotIn("new-realtime-event", cursor.processed_event_ids())

    def test_ontology_reasoning_retries_scoped_write_lease_without_opening_circuit(self):
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
                self.accounts = []
                self.last_ontology_projection_results = {
                    "main": {
                        "status": "deferred-scoped-write-lease",
                        "reason": "Another scoped ABox projection is still staging.",
                    },
                }

            def run_once(self, dry_run=False, force=False, symbol_filter=None):
                return []

        cursor = Cursor()
        runner = OntologyReasoningRunner(
            Reader(),
            cursor,
            monitor_runner_factory=FakeMonitorRunner,
            settings={
                "ontologyReasoningEnabled": "1",
                "ontologyReasoningProjectionRetrySeconds": "30",
                "ontologyRuleCandidateAiEnabled": "0",
            },
        )

        result = runner.run_once()

        self.assertEqual("deferred", result["status"])
        self.assertEqual(30, result["retryAfterSeconds"])
        self.assertEqual(0, result["projectionCircuit"].get("consecutiveFailures", 0))
        self.assertEqual([], cursor.processed_event_ids())
        self.assertIn("000660", cursor.payload["lastProjectionAttemptAtBySymbol"])

    def test_ontology_reasoning_opens_projection_circuit_after_repeated_failures(self):
        request = ontology_reasoning_requested_event(
            DomainEvent(name="market_data.collected", aggregate_id="market:KR", payload={}),
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
                self.payload = {"processedEventIds": []}

            def processed_event_ids(self):
                return []

            def load(self):
                return dict(self.payload)

            def save(self, payload):
                self.payload = dict(payload or {})

        cursor = Cursor()
        runner = OntologyReasoningRunner(
            Reader(),
            cursor,
            monitor_runner_factory=lambda: (_ for _ in ()).throw(AssertionError("open circuit must not project")),
            settings={
                "ontologyReasoningEnabled": "1",
                "ontologyProjectionCircuitFailureThreshold": "3",
                "ontologyProjectionCircuitCooldownSeconds": "300",
                "ontologyProjectionCircuitProbeRetrySeconds": "15",
            },
            now_provider=lambda: datetime(2026, 7, 21, 0, 3, 0, tzinfo=timezone.utc),
        )
        for _index in range(3):
            runner.record_projection_failure("native inference timeout", [{"stage": "native-rule", "status": "error"}])

        result = runner.run_once()

        self.assertEqual("circuit-open", result["status"])
        self.assertEqual(15, result["retryAfterSeconds"])
        self.assertEqual(15, result["projectionCircuitProbeRetrySeconds"])
        self.assertEqual(3, result["projectionCircuit"]["consecutiveFailures"])

    def test_news_collection_fills_a_symbol_only_watchlist_entry_from_known_instrument_data(self):
        runner = NewsCollectionRunner(
            account_repository=SimpleNamespace(load=lambda: [AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["005380"])]),
            monitor_store=SimpleNamespace(previous={}),
            symbol_store=SimpleNamespace(get=lambda *_args: None),
            evidence_store=SimpleNamespace(),
            gateway=SimpleNamespace(),
            settings={},
        )

        target = runner.all_targets()[0]

        self.assertEqual("현대차", target.name)
        self.assertEqual("모빌리티", target.sector)

    def test_kis_runner_persists_no_tick_as_transport_telemetry_only(self):
        class QuoteCache:
            def __init__(self):
                self.rows = {}

            def load(self, provider, account_id, symbol):
                return dict(self.rows.get((provider, account_id, symbol), {}))

            def save(self, provider, account_id, symbol, payload):
                self.rows[(provider, account_id, symbol)] = dict(payload)

        class NoTickClient:
            def enabled(self):
                return True

            def configured(self):
                return True

            def collect(self, _symbols, _duration, on_update):
                return {"status": "ok", "savedCount": 0}

        cache = QuoteCache()
        events = EventBus()
        runner = KISRealtimeWebSocketRunner(
            client=NoTickClient(),
            symbol_selector=SimpleNamespace(symbols=lambda: ["005930"]),
            quote_cache=cache,
            settings={},
            event_publisher=events,
        )

        result = runner.run_once(duration_seconds=1, force=True)
        telemetry = result["transportStatus"]

        self.assertEqual("no-tick", result["status"])
        self.assertEqual("reference", telemetry["dataQuality"])
        self.assertFalse(telemetry["realTime"])
        self.assertEqual("empty", result["eventFlush"]["status"])
        self.assertEqual([], events.published)

    def test_ontology_reasoning_prioritizes_an_overdue_symbol_over_a_fresh_higher_priority_symbol(self):
        higher_priority = ontology_reasoning_requested_event(
            DomainEvent(name="market_data.collected", aggregate_id="market:AAPL", payload={}),
            "market-data-update",
            ["AAPL"],
            changed_count=1,
            fact_types=["MarketQuote"],
        )
        overdue = ontology_reasoning_requested_event(
            DomainEvent(name="market_data.collected", aggregate_id="market:MSFT", payload={}),
            "market-data-update",
            ["MSFT"],
            changed_count=1,
            fact_types=["MarketQuote"],
        )

        class Cursor:
            def __init__(self):
                self.payload = {
                    "lastReasonedAtBySymbol": {
                        "AAPL": "2026-07-23T00:15:00Z",
                        "MSFT": "2026-07-23T00:00:00Z",
                    },
                }

            def load(self):
                return dict(self.payload)

        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=Cursor(),
            monitor_runner_factory=lambda: None,
            settings={
                "ontologyReasoningMaxSymbolsPerRun": "1",
                "ontologyReasoningFairnessMaxWaitSeconds": "900",
            },
            priority_symbols_provider=lambda: {"holdingSymbols": ["AAPL"], "watchlistSymbols": ["MSFT"]},
            now_provider=lambda: datetime(2026, 7, 23, 0, 20, tzinfo=timezone.utc),
        )

        batches, symbols, omitted = runner.request_symbol_batches([higher_priority, overdue])

        self.assertEqual(["MSFT"], symbols)
        self.assertEqual(["MSFT"], batches[overdue.event_id])
        self.assertEqual(1, omitted)


if __name__ == "__main__":
    unittest.main()
