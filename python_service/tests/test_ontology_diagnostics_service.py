import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.ontology_diagnostics_service import OntologyDiagnosticsService
from digital_twin.domain.events import DomainEvent, MONITORING_ALERTS_DETECTED, ONTOLOGY_REASONING_COMPLETED
from digital_twin.domain.notifications import NotificationJob


class FakeOntologyRepository:
    def scoped_abox_storage_diagnostics(self):
        return {
            "configured": True,
            "status": "ok",
            "graphStore": "typedb",
            "persistenceMode": "immutable-scoped-manifest",
            "worldviewManifestId": "abox-manifest:test",
            "activeScopeCount": 3,
            "scopeTypeCounts": {"symbol": 2, "macro": 1},
            "logicalActiveEntityCount": 120,
            "logicalActiveRelationCount": 160,
            "physicalAboxEntityCount": 150,
            "physicalAboxRelationCount": 190,
            "storedManifestCount": 2,
            "inactiveManifestCount": 1,
        }

    def active_tbox_metadata(self):
        return {
            "configured": True,
            "status": "ok",
            "source": "typedb-typeql",
            "graphStore": "typedb",
            "entityCount": 7,
            "relationCount": 3,
        }

    def rulebox_snapshot(self):
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "source": "typedb-typeql",
            "graphStore": "typedb",
            "ruleCount": 2,
            "conditionCount": 3,
            "derivationCount": 2,
            "ruleboxRulesHash": "hash-1",
            "ruleboxShortHash": "hash-1",
            "ruleboxRuleCount": 2,
            "ruleboxConditionCount": 3,
            "ruleboxDerivationCount": 2,
            "nativeReasoningProfile": {
                "status": "partial",
                "supportedRuleCount": 1,
                "unsupportedRuleCount": 1,
                "functionCount": 1,
            },
        }

    def inferencebox_snapshot(self, symbols=None, limit=80):
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "source": "typedbInferenceBox",
            "graphStore": "typedb",
            "reasoningMode": "typedb-native-rule-materialized",
            "materializationSource": "typedb-abox-native-rule",
            "querySource": "typedb-typeql",
            "typedbReadStatus": "ok",
            "entityCount": 4,
            "relationCount": 2,
            "traceCount": 1,
            "nativeRelationCount": 2,
            "nativeTypeDbReasoningUsed": True,
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "inferenceGenerationId": "inference-generation:test",
            "sourceAboxSnapshotId": "abox-material:test",
            "generationAligned": True,
            "ruleboxRulesHash": "hash-1",
            "ruleboxRuleCount": 2,
            "symbols": list(symbols or []),
        }

    def read_entity_rows(self, boxes=None):
        return [
            {
                "id": "stock:TSLA",
                "label": "Tesla",
                "kind": "stock",
                "ontologyBox": "ABox",
                "symbol": "TSLA",
                "tboxClass": "Stock",
            },
            {
                "id": "flow:TSLA:volume",
                "label": "Tesla volume",
                "kind": "trade-flow",
                "ontologyBox": "ABox",
                "symbol": "TSLA",
                "tboxClass": "TradeFlow",
            },
        ]

    def read_relation_rows(self, boxes=None):
        return [
            {"source": "stock:TSLA", "target": "price:TSLA:current", "type": "HAS_PRICE", "ontologyBox": "ABox", "symbol": "TSLA"},
            {"source": "stock:TSLA", "target": "flow:TSLA:volume", "type": "HAS_TRADE_FLOW", "ontologyBox": "ABox", "symbol": "TSLA"},
            {"source": "stock:TSLA", "target": "quality:TSLA", "type": "HAS_DATA_QUALITY", "ontologyBox": "ABox", "symbol": "TSLA"},
        ]


class FakeEventLog:
    def __init__(self, events):
        self.events = {event.name: event for event in events}

    def latest_events_by_name(self, names):
        return {name: self.events[name] for name in names if name in self.events}


class FakeNotificationQueue:
    def __init__(self, jobs):
        self.jobs = list(jobs)

    def recent(self, limit=80):
        return self.jobs[:limit]


class FakeStrategyProposalService:
    def status(self):
        return {
            "count": 1,
            "proposedCount": 0,
            "validatedCount": 1,
            "approvedCount": 0,
            "deployedCount": 0,
            "retiredCount": 0,
            "statuses": {"validated": 1},
        }

    def list(self):
        return {
            "proposals": [{
                "id": "strategy-proposal-test",
                "title": "테스트 전략",
                "status": "validated",
                "updatedAt": "2026-07-17T00:00:00Z",
                "ruleIds": ["graph.test.v1"],
                "validation": {
                    "status": "completed",
                    "promotionReadiness": {"status": "needs-review"},
                },
            }]
        }


class PrimaryCoverageRepository(FakeOntologyRepository):
    def read_entity_rows(self, boxes=None):
        return [
            {
                "id": "stock:AAPL",
                "label": "Apple",
                "kind": "stock",
                "ontologyBox": "ABox",
                "symbol": "AAPL",
                "source": "watchlist",
                "tboxClass": "Stock",
                "tboxClasses": ["ActionPolicy", "WatchlistCandidate"],
            },
            {
                "id": "stock:SPY",
                "label": "SPY",
                "kind": "market-proxy",
                "ontologyBox": "ABox",
                "symbol": "SPY",
                "tboxClass": "ETF",
                "tboxClasses": ["MarketProxyETF", "MarketProxyInstrument"],
            },
        ]

    def read_relation_rows(self, boxes=None):
        return [
            {"source": "stock:AAPL", "target": "price:AAPL", "type": "HAS_PRICE", "ontologyBox": "ABox", "symbol": "AAPL"},
            {"source": "stock:AAPL", "target": "path:AAPL", "type": "HAS_PRICE_PATH", "ontologyBox": "ABox", "symbol": "AAPL"},
            {"source": "stock:AAPL", "target": "flow:AAPL", "type": "HAS_TRADE_FLOW", "ontologyBox": "ABox", "symbol": "AAPL"},
            {"source": "stock:AAPL", "target": "quality:AAPL", "type": "HAS_DATA_QUALITY", "ontologyBox": "ABox", "symbol": "AAPL"},
            {"source": "stock:AAPL", "target": "evidence:AAPL", "type": "HAS_EXTERNAL_SIGNAL", "ontologyBox": "ABox", "symbol": "AAPL"},
            {"source": "stock:AAPL", "target": "macro:AAPL", "type": "HAS_MACRO_REGIME", "ontologyBox": "ABox", "symbol": "AAPL"},
            {"source": "stock:AAPL", "target": "valuation:AAPL", "type": "HAS_VALUATION", "ontologyBox": "ABox", "symbol": "AAPL"},
        ]


class ScopedCoverageRepository(PrimaryCoverageRepository):
    def __init__(self):
        self.source_ids = []

    def read_relation_rows(self, boxes=None):
        raise AssertionError("coverage should use the scoped ABox relation reader")

    def read_relation_rows_by_source_ids(self, source_ids, boxes=None):
        self.source_ids = list(source_ids or [])
        return PrimaryCoverageRepository.read_relation_rows(self, boxes)


class OntologyDiagnosticsServiceTests(unittest.TestCase):
    def test_status_reports_native_reasoning_and_outbox_boundary(self):
        alert_event = DomainEvent(
            name=MONITORING_ALERTS_DETECTED,
            aggregate_id="main",
            payload={"count": 1, "accountIds": ["main"], "symbols": ["TSLA"]},
            event_id="event-alert-1",
        )
        reasoning_event = DomainEvent(
            name=ONTOLOGY_REASONING_COMPLETED,
            aggregate_id="all",
            payload={"status": "ok"},
            event_id="event-reasoning-1",
        )
        job = NotificationJob.create(
            "본문",
            account_id="main",
            message_type="ontologyInferenceMissing",
            source_event_id=alert_event.event_id,
            source_event_name=alert_event.name,
            context={},
        )
        service = OntologyDiagnosticsService(
            ontology_repository=FakeOntologyRepository(),
            settings={"typeDbAddress": "127.0.0.1:1729", "typeDbDatabase": "orbit"},
            event_log=FakeEventLog([alert_event, reasoning_event]),
            notification_queue=FakeNotificationQueue([job]),
        )

        payload = service.status(symbols=["tsla"], limit=20)

        self.assertEqual(payload["contract"], "typedb-ontology-diagnostics-v1")
        self.assertEqual(payload["activeGraphStore"], "typedb")
        self.assertTrue(payload["typedb"]["addressConfigured"])
        self.assertEqual(payload["inferenceBox"]["reasoningMode"], "typedb-native-rule-materialized")
        self.assertEqual(payload["inferenceBox"]["ruleboxRulesHash"], "hash-1")
        self.assertEqual(payload["inferenceBox"]["sourceAboxSnapshotId"], "abox-material:test")
        self.assertTrue(payload["inferenceBox"]["generationAligned"])
        self.assertEqual("immutable-scoped-manifest", payload["aboxStorage"]["persistenceMode"])
        self.assertEqual(3, payload["aboxStorage"]["activeScopeCount"])
        self.assertEqual(150, payload["aboxStorage"]["physicalAboxEntityCount"])
        self.assertTrue(payload["reasoningBoundary"]["nativeTypeDbReasoningUsed"])
        self.assertFalse(payload["reasoningBoundary"]["typedbBootstrapReasoningUsed"])
        self.assertEqual("ok", payload["reasoningBoundary"]["ruleboxHashStatus"])
        self.assertEqual("warning", payload["aboxCoverage"]["status"])
        self.assertEqual("TSLA", payload["aboxCoverage"]["symbols"][0]["symbol"])
        self.assertIn("price", payload["aboxCoverage"]["symbols"][0]["present"])
        self.assertEqual(payload["notificationBoundary"]["status"], "ok")
        self.assertEqual(payload["notificationBoundary"]["jobsForLatestAlert"][0]["jobId"], job.job_id)

    def test_notification_boundary_warns_when_latest_alert_has_no_outbox_job(self):
        alert_event = DomainEvent(
            name=MONITORING_ALERTS_DETECTED,
            aggregate_id="main",
            payload={"count": 1},
            event_id="event-alert-2",
        )
        unrelated = NotificationJob.create("본문", message_type="investmentInsight", source_event_id="other")
        service = OntologyDiagnosticsService(
            ontology_repository=FakeOntologyRepository(),
            event_log=FakeEventLog([alert_event]),
            notification_queue=FakeNotificationQueue([unrelated]),
        )

        payload = service.status()

        self.assertEqual(payload["notificationBoundary"]["status"], "warning")
        self.assertIn("no recent notification job", payload["notificationBoundary"]["reason"])

    def test_abox_coverage_separates_primary_symbols_from_context_proxies(self):
        service = OntologyDiagnosticsService(ontology_repository=PrimaryCoverageRepository())

        payload = service.status()

        coverage = payload["aboxCoverage"]
        self.assertEqual("ok", coverage["status"])
        self.assertEqual(2, coverage["symbolCount"])
        self.assertEqual(1, coverage["primarySymbolCount"])
        self.assertEqual(1, coverage["contextSymbolCount"])
        self.assertEqual(1.0, coverage["primaryCoverageRatio"])
        self.assertEqual("primary", coverage["primarySymbols"][0]["diagnosticScope"])
        self.assertEqual("context", coverage["contextSymbols"][0]["diagnosticScope"])
        self.assertIn("do not lower the primary status", coverage["interpretation"])

    def test_abox_coverage_uses_scoped_relations_when_repository_supports_it(self):
        repository = ScopedCoverageRepository()
        service = OntologyDiagnosticsService(ontology_repository=repository)

        payload = service.status()

        self.assertEqual("ok", payload["aboxCoverage"]["status"])
        self.assertEqual(["stock:AAPL", "stock:SPY"], repository.source_ids)

    def test_abox_coverage_prefers_root_holding_source_over_later_context_evidence(self):
        class RootHoldingRepository(PrimaryCoverageRepository):
            def read_entity_rows(self, boxes=None):
                return [
                    {
                        "id": "stock:000660",
                        "label": "SK hynix",
                        "kind": "stock",
                        "ontologyBox": "ABox",
                        "symbol": "000660",
                        "source": "holding",
                        "tboxClass": "Stock",
                    },
                    {
                        "id": "research:000660:latest",
                        "label": "Research evidence",
                        "kind": "research-evidence",
                        "ontologyBox": "ABox",
                        "symbol": "000660",
                        "source": "external-signal",
                        "tboxClass": "ResearchEvidence",
                    },
                ]

            def read_relation_rows(self, boxes=None):
                return [
                    {"source": "stock:000660", "target": "price:000660", "type": "HAS_PRICE", "ontologyBox": "ABox", "symbol": "000660"},
                    {"source": "stock:000660", "target": "path:000660", "type": "HAS_PRICE_PATH", "ontologyBox": "ABox", "symbol": "000660"},
                    {"source": "stock:000660", "target": "flow:000660", "type": "HAS_TRADE_FLOW", "ontologyBox": "ABox", "symbol": "000660"},
                    {"source": "stock:000660", "target": "liquidity:000660", "type": "HAS_LIQUIDITY_PROFILE", "ontologyBox": "ABox", "symbol": "000660"},
                    {"source": "stock:000660", "target": "execution:000660", "type": "HAS_EXECUTION_METRIC", "ontologyBox": "ABox", "symbol": "000660"},
                    {"source": "stock:000660", "target": "quality:000660", "type": "HAS_DATA_QUALITY", "ontologyBox": "ABox", "symbol": "000660"},
                    {"source": "stock:000660", "target": "external:000660", "type": "HAS_EXTERNAL_SIGNAL", "ontologyBox": "ABox", "symbol": "000660"},
                    {"source": "stock:000660", "target": "valuation:000660", "type": "HAS_VALUATION", "ontologyBox": "ABox", "symbol": "000660"},
                    {"source": "stock:000660", "target": "macro:000660", "type": "HAS_MACRO_REGIME", "ontologyBox": "ABox", "symbol": "000660"},
                ]

        coverage = OntologyDiagnosticsService(ontology_repository=RootHoldingRepository()).status()["aboxCoverage"]

        self.assertEqual("primary", coverage["symbols"][0]["diagnosticScope"])
        self.assertEqual(1, coverage["primarySymbolCount"])
        self.assertEqual(0, coverage["contextSymbolCount"])

    def test_strategy_proposal_boundary_reports_validated_backlog(self):
        service = OntologyDiagnosticsService(
            ontology_repository=FakeOntologyRepository(),
            strategy_proposal_service=FakeStrategyProposalService(),
        )

        payload = service.status()

        boundary = payload["strategyProposalBoundary"]
        self.assertEqual("warning", boundary["status"])
        self.assertEqual(1, boundary["pendingApprovalCount"])
        self.assertEqual(0, boundary["pendingDeploymentCount"])
        self.assertIn("human approval", boundary["nextAction"])
        self.assertEqual("needs-review", boundary["proposals"][0]["promotionReadinessStatus"])


if __name__ == "__main__":
    unittest.main()
