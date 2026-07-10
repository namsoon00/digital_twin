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
            settings={"newsCollectionRateLimitSeconds": "0"},
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


if __name__ == "__main__":
    unittest.main()
