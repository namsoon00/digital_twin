import copy
import json
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from digital_twin.modules.market_data.application.external_data.research_evidence_projection_service import ExternalFactResearchEvidenceReconciler, ExternalOfficialEvidenceProjectionService
from digital_twin.modules.news_intelligence.domain.disclosure_analysis import DisclosureAnalysisResult
from digital_twin.modules.news_intelligence.domain.disclosure_quality import disclosure_reasoning_eligibility
from digital_twin.modules.market_data.domain.event_types import EXTERNAL_FACT_CHANGED
from digital_twin.modules.market_data.domain.external_data_contracts import OFFICIAL_EVIDENCE_PROJECTOR_VERSION
from digital_twin.modules.news_intelligence.domain.event_types import RESEARCH_EVIDENCE_COLLECTED
from digital_twin.shared_kernel.events import DomainEvent
from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, PortfolioOntology, entity_id
from digital_twin.modules.reasoning.domain.ontology_external_abox import add_symbol_external_signal_concepts
from digital_twin.infrastructure.mysql_research_evidence import merge_derived_evidence_payload
from digital_twin.infrastructure.mysql_operational_events import insert_domain_event_with_connection
from digital_twin.infrastructure.transactions.monitoring import MySQLEventLog


class MemoryFactStore:
    def __init__(self, row):
        self.row = dict(row)
        self.revisions = {}

    def current_fact(self, dataset_id, subject_key):
        if dataset_id == self.row.get("datasetId") and subject_key == self.row.get("subjectKey"):
            return dict(self.row)
        return {}

    def fact_revision(self, revision_id):
        return dict(self.revisions.get(revision_id) or {})

    def list_current(self):
        return [dict(self.row)]


class MemoryEvidenceStore:
    def __init__(self):
        self.items = {}

    def upsert_many_with_events(self, items, event_builder):
        changed = []
        for item in items:
            prior = self.items.get(item.evidence_id)
            if prior is None or prior.to_dict() != item.to_dict():
                self.items[item.evidence_id] = item
                changed.append(item)
        mutation = SimpleNamespace(
            written_count=len(changed),
            changed_items=changed,
            changed_symbols=sorted({item.symbol for item in changed}),
            inference_changed_symbols=sorted({item.symbol for item in changed if item.raw_payload.get("promptEvidenceAdmission", {}).get("promptEligible")}),
            to_dict=lambda: {
                "writtenCount": len(changed),
                "changedSymbols": sorted({item.symbol for item in changed}),
                "inferenceChangedSymbols": sorted({item.symbol for item in changed if item.raw_payload.get("promptEvidenceAdmission", {}).get("promptEligible")}),
                "evidenceDeltas": [],
                "factRevisionsBySymbol": {},
            },
        )
        return len(changed), list(event_builder(mutation) or [])

    def get(self, evidence_id):
        return self.items.get(evidence_id)


class CountingDisclosureAnalyzer:
    def __init__(self):
        self.calls = 0

    def analyze(self, _context):
        self.calls += 1
        return DisclosureAnalysisResult([
            "의미: 회사가 자기주식 취득을 결의했습니다.",
            "영향: 유통 주식 수 감소 가능성을 확인합니다.",
            "확인: 실제 취득 체결 내역을 확인합니다.",
            "대응: 공식 후속 공시와 시장 반응을 점검합니다.",
        ], source="unit")


class MemoryPublisher:
    def __init__(self):
        self.events = []

    def dispatch_recorded(self, event):
        self.events.append(event)


class MemoryEventReader:
    def __init__(self, events):
        self.events = list(events)

    def external_fact_events_after(self, after_occurred_at="", after_event_id="", limit=100):
        return [
            event for event in self.events
            if event.occurred_at > after_occurred_at
            or (event.occurred_at == after_occurred_at and event.event_id > after_event_id)
        ][:limit]


class MemoryDurableEventReader:
    def __init__(self, events):
        self.pending = {event.event_id: event for event in events}
        self.processing = {}
        self.completed = set()
        self.failed = set()

    def claim_external_fact_projection_events(
        self, consumer_id, projector_version, worker_id, limit=100, lease_seconds=120,
    ):
        del consumer_id, projector_version, worker_id, lease_seconds
        rows = []
        for event_id in sorted(self.pending)[:limit]:
            event = self.pending.pop(event_id)
            token = "lease:" + event_id
            self.processing[event_id] = token
            rows.append({"event": event, "leaseToken": token})
        return rows

    def complete_external_fact_projection_event(
        self, consumer_id, projector_version, event_id, lease_token,
    ):
        del consumer_id, projector_version
        if self.processing.get(event_id) != lease_token:
            return False
        self.processing.pop(event_id, None)
        self.completed.add(event_id)
        return True

    def fail_external_fact_projection_event(
        self, consumer_id, projector_version, event_id, lease_token, error,
    ):
        del consumer_id, projector_version, error
        if self.processing.get(event_id) != lease_token:
            return False
        self.processing.pop(event_id, None)
        self.failed.add(event_id)
        return True


class MemoryCursor:
    def __init__(self):
        self.state = {}

    def load(self):
        return dict(self.state)

    def replace(self, payload):
        self.state = dict(payload)


class RecordingEventConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((str(sql), tuple(params or ())))
        return SimpleNamespace(rowcount=1)


class ProjectionDeliveryConnection(RecordingEventConnection):
    def __init__(self, event):
        super().__init__()
        self.event = event

    def execute(self, sql, params=()):
        rendered = str(sql)
        self.calls.append((rendered, tuple(params or ())))
        if "FROM external_fact_projection_deliveries delivery" in rendered:
            event = self.event
            return SimpleNamespace(fetchall=lambda: [{
                "event_id": event.event_id,
                "name": event.name,
                "aggregate_id": event.aggregate_id,
                "occurred_at": event.occurred_at,
                "correlation_id": event.correlation_id,
                "payload_json": json.dumps(event.payload),
                "event_json": json.dumps({
                    key: value for key, value in event.to_dict().items() if key != "payload"
                }),
            }])
        return SimpleNamespace(rowcount=1)


class ProjectionDeliveryEventLog(MySQLEventLog):
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def transaction(self):
        yield self.connection


def dart_fact(dataset_id="opendart.document"):
    document = (
        "삼성전자는 2026년 8월 25일 이사회에서 보통주 1,000,000주를 취득하기로 결정했다. "
        "취득 예정 금액은 100,000,000,000원이며 취득 기간은 2026-08-26부터 2026-11-25까지다. "
        "취득 목적은 주주가치 제고이며 실제 집행 결과는 추후 공시한다."
    )
    return {
        "datasetId": dataset_id,
        "subjectKey": "005930",
        "providerId": "opendart",
        "sourceRevision": "dart-batch-20260825",
        "sourceAsOf": "2026-08-25T00:00:00Z",
        "fetchedAt": "2026-08-25T00:02:00Z",
        "payloadHash": "fact-hash",
        "payload": {
            "dartDisclosures": {
                "005930": {
                    "provider": "OpenDART",
                    "corpName": "삼성전자",
                    "reportName": "자기주식 취득 결정",
                    "receiptNo": "202608250001",
                    "receiptDate": "20260825",
                    "documentText": document,
                    "documentTextQuality": "body",
                    "items": [{
                        "provider": "OpenDART",
                        "corpName": "삼성전자",
                        "reportName": "자기주식 취득 결정",
                        "receiptNo": "202608250001",
                        "receiptDate": "20260825",
                        "documentText": document,
                        "documentTextQuality": "body",
                    }],
                },
            },
        },
    }


def sec_fact(dataset_id="sec.document"):
    document = (
        "Apple Inc. reported quarterly revenue of $100,000,000,000 for the period ended 2026-06-30. "
        "The filing states that operating income increased and identifies product demand and foreign exchange as material factors. "
        "Management will discuss the results in the next earnings call."
    )
    return {
        "datasetId": dataset_id,
        "subjectKey": "AAPL",
        "providerId": "sec-edgar",
        "sourceRevision": "0000320193-26-000100",
        "sourceAsOf": "2026-08-25T00:00:00Z",
        "fetchedAt": "2026-08-25T00:02:00Z",
        "payloadHash": "sec-fact-hash",
        "payload": {
            "secFilings": {
                "AAPL": {
                    "provider": "SEC EDGAR",
                    "companyName": "Apple Inc.",
                    "cik": "0000320193",
                    "latestFiling": {
                        "form": "10-Q",
                        "filingDate": "2026-08-25",
                        "reportDate": "2026-06-30",
                        "accessionNumber": "0000320193-26-000100",
                        "primaryDocument": "aapl-20260630.htm",
                        "documentText": document,
                        "documentTextQuality": "body",
                    },
                },
            },
        },
    }


class ExternalOfficialEvidenceProjectionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)
        self.fact_store = MemoryFactStore(dart_fact())
        self.evidence_store = MemoryEvidenceStore()
        self.publisher = MemoryPublisher()
        self.projector = ExternalOfficialEvidenceProjectionService(
            self.fact_store,
            self.evidence_store,
            self.publisher,
            settings={"materialityGateEnabled": "1"},
            now_provider=lambda: self.now,
        )

    def event(self):
        return DomainEvent(
            name=EXTERNAL_FACT_CHANGED,
            aggregate_id="opendart.document:005930",
            payload={"datasetId": "opendart.document", "subjectKey": "005930"},
            occurred_at="2026-08-25T00:05:00Z",
            event_id="event-dart-1",
        )

    def test_projects_verified_disclosure_and_publishes_independent_alert_contract(self):
        result = self.projector.project_event(self.event())

        self.assertEqual("ok", result["status"])
        evidence = self.evidence_store.items["research:005930:dart:202608250001"]
        self.assertTrue(evidence.raw_payload["documentVerified"])
        self.assertTrue(evidence.raw_payload["analysisReady"])
        self.assertTrue(evidence.raw_payload["documentHash"])
        self.assertTrue(
            disclosure_reasoning_eligibility(evidence.raw_payload)["reasoningEligible"]
        )
        self.assertTrue(evidence.raw_payload["disclosureAnalysis"]["confirmedFacts"])
        self.assertEqual("202608250001", evidence.raw_payload["sourceRevision"])
        self.assertEqual("20260825", evidence.raw_payload["sourceAsOf"])
        self.assertEqual("dart-batch-20260825", evidence.raw_payload["externalFactSourceRevision"])
        collected = next(event for event in self.publisher.events if event.name == RESEARCH_EVIDENCE_COLLECTED)
        self.assertEqual("research-evidence-change-v2", collected.payload["eventContract"])
        self.assertEqual(1, collected.payload["alertEligibleCount"])
        self.assertEqual(1, len(collected.payload["alertEligibleItems"]))

    def test_projection_is_idempotent(self):
        first = self.projector.project_event(self.event())
        second = self.projector.project_event(self.event())

        self.assertEqual(1, first["writtenCount"])
        self.assertEqual(0, second["writtenCount"])

    def test_recovery_document_stays_silent_after_durable_event_replay(self):
        for factory in (dart_fact, sec_fact):
            with self.subTest(factory=factory.__name__):
                row = factory()
                row["quality"] = {"dataUsable": True, "collectionSource": "document-recovery"}
                publisher = MemoryPublisher()
                evidence = MemoryEvidenceStore()
                projector = ExternalOfficialEvidenceProjectionService(MemoryFactStore(row), evidence, publisher, {}, now_provider=lambda: self.now)
                event = DomainEvent(EXTERNAL_FACT_CHANGED, "test", payload={"datasetId": row["datasetId"], "subjectKey": row["subjectKey"], "sourceRevision": row["sourceRevision"]})
                result = projector.project_event(event)
                self.assertEqual(1, result["writtenCount"])
                self.assertFalse(result["alertReplayAllowed"])
                collected = next(e for e in publisher.events if e.name == RESEARCH_EVIDENCE_COLLECTED)
                self.assertEqual([], collected.payload["alertEligibleItems"])
                self.assertEqual(0, collected.payload["alertEligibleCount"])
                self.assertEqual(1, collected.payload["savedCount"])
                self.assertTrue(next(iter(evidence.items.values())).raw_payload["documentVerified"])
                self.assertEqual("document-recovery", next(iter(evidence.items.values())).raw_payload["officialDocumentCollectionSource"])

    def test_event_reads_exact_document_revision_not_latest_symbol_document(self):
        row = dart_fact()
        calls = []
        self.fact_store.row = {**row, "sourceRevision": "different-document"}
        self.fact_store.official_document_fact = lambda *args: calls.append(args) or row
        event = self.event()
        event.payload["sourceRevision"] = row["sourceRevision"]
        self.assertEqual(1, self.projector.project_event(event)["writtenCount"])
        self.assertEqual([(row["datasetId"], row["subjectKey"], row["sourceRevision"])], calls)

    def test_missing_exact_document_revision_never_uses_newer_body(self):
        event = self.event()
        event.payload["sourceRevision"] = "missing-version"
        with self.assertRaisesRegex(RuntimeError, "revision does not match"):
            self.projector.project_event(event)
        self.assertEqual({}, self.evidence_store.items)
        self.assertEqual([], self.publisher.events)
        self.setUp()
        self._assert_v2_event_reads_internal_revision_and_rejects_current_substitution()
        self.setUp()
        self._assert_v2_event_fails_closed_when_internal_revision_is_missing()

    def _assert_v2_event_reads_internal_revision_and_rejects_current_substitution(self):
        exact = dart_fact()
        exact["revisionId"] = "internal-revision-1"
        exact["payloadHash"] = "exact-payload-hash"
        self.fact_store.revisions["internal-revision-1"] = exact
        self.fact_store.row = {**dart_fact(), "sourceRevision": "newer-current"}
        event = self.event()
        event.payload.update({
            "eventContract": "external-fact-change-v2",
            "sourceRevision": exact["sourceRevision"],
            "sourceRef": {
                "revisionId": "internal-revision-1",
                "datasetId": exact["datasetId"],
                "subjectKey": exact["subjectKey"],
                "payloadHash": exact["payloadHash"],
            },
        })

        self.assertEqual(1, self.projector.project_event(event)["writtenCount"])
        evidence = self.evidence_store.items["research:005930:dart:202608250001"]
        self.assertEqual("dart-batch-20260825", evidence.raw_payload["externalFactSourceRevision"])

    def _assert_v2_event_fails_closed_when_internal_revision_is_missing(self):
        event = self.event()
        event.payload["sourceRef"] = {
            "revisionId": "missing-internal-revision",
            "datasetId": "opendart.document",
            "subjectKey": "005930",
            "payloadHash": "missing-hash",
        }

        with self.assertRaisesRegex(RuntimeError, "revision does not match"):
            self.projector.project_event(event)
        self.assertEqual({}, self.evidence_store.items)

    def test_metadata_refresh_preserves_verified_document_provenance(self):
        previous = {
            "officialDocumentText": "verified official filing body",
            "documentVerified": True,
            "analysisReady": True,
            "documentHash": "document-hash",
            "officialDocumentDatasetId": "opendart.document",
            "officialDocumentFactRevision": "receipt:document-hash",
            "officialDocumentCollectionSource": "document-recovery",
            "promptEvidenceAdmission": {"promptEligible": True, "alertEligible": True},
            "disclosureDocumentQuality": {"documentVerified": True},
        }
        incoming = {
            "officialDocumentText": "",
            "documentVerified": False,
            "analysisReady": False,
            "documentHash": "",
            "promptEvidenceAdmission": {"promptEligible": False, "alertEligible": False},
            "disclosureDocumentQuality": {"documentVerified": False},
        }

        merged = merge_derived_evidence_payload(previous, incoming)

        self.assertTrue(merged["documentVerified"])
        self.assertEqual("document-hash", merged["documentHash"])
        self.assertEqual("opendart.document", merged["officialDocumentDatasetId"])
        self.assertEqual("document-recovery", merged["officialDocumentCollectionSource"])
        self.assertTrue(merged["promptEvidenceAdmission"]["promptEligible"])

    def test_metadata_only_disclosure_is_stored_but_cannot_alert_or_enter_prompt(self):
        row = copy.deepcopy(dart_fact("opendart.disclosures"))
        disclosure = row["payload"]["dartDisclosures"]["005930"]
        disclosure["documentText"] = ""
        disclosure["documentTextQuality"] = "metadata-only"
        disclosure["items"][0]["documentText"] = ""
        disclosure["items"][0]["documentTextQuality"] = "metadata-only"
        projector = ExternalOfficialEvidenceProjectionService(
            MemoryFactStore(row),
            MemoryEvidenceStore(),
            self.publisher,
            settings={},
            now_provider=lambda: self.now,
        )

        event = DomainEvent(
            name=EXTERNAL_FACT_CHANGED,
            aggregate_id="opendart.disclosures:005930",
            payload={"datasetId": "opendart.disclosures", "subjectKey": "005930"},
            occurred_at="2026-08-25T00:05:00Z",
            event_id="event-dart-metadata-1",
        )
        result = projector.project_event(event)

        self.assertEqual("ok", result["status"])
        evidence = next(iter(projector.evidence_store.items.values()))
        admission = evidence.raw_payload["promptEvidenceAdmission"]
        self.assertFalse(admission["promptEligible"])
        collected = self.publisher.events[-1]
        self.assertEqual(0, collected.payload["alertEligibleCount"])

    def test_metadata_only_disclosure_cannot_materialize_reasoning_filing_or_action(self):
        stock_id = entity_id("stock", "005930")
        graph = PortfolioOntology(
            "account:1",
            entities=[OntologyEntity(stock_id, "삼성전자", "stock", {
                "ontologyBox": "ABox",
                "symbol": "005930",
                "source": "holding",
            })],
        )
        metadata = {
            "provider": "OpenDART",
            "reportName": "유상증자 결정",
            "receiptNo": "202608250002",
            "receiptDate": "20260825",
            "documentVerified": False,
            "analysisReady": False,
            "officialDocumentState": "metadata-only",
            "documentHash": "",
        }

        add_symbol_external_signal_concepts(
            graph,
            stock_id,
            "005930",
            {"dartDisclosures": {"005930": metadata}},
        )

        self.assertTrue(any(item.kind == "fundamental-event" for item in graph.entities))
        self.assertFalse(any(item.kind == "disclosure-filing" for item in graph.entities))
        self.assertFalse(any(item.kind == "corporate-action" for item in graph.entities))
        self.assertFalse(disclosure_reasoning_eligibility(metadata)["reasoningEligible"])

    def test_verified_governed_disclosure_materializes_normalized_reasoning_contract(self):
        self.projector.project_event(self.event())
        evidence = self.evidence_store.items["research:005930:dart:202608250001"]
        stock_id = entity_id("stock", "005930")
        graph = PortfolioOntology(
            "account:1",
            entities=[OntologyEntity(stock_id, "삼성전자", "stock", {
                "ontologyBox": "ABox",
                "symbol": "005930",
                "source": "holding",
            })],
        )

        add_symbol_external_signal_concepts(
            graph,
            stock_id,
            "005930",
            {"dartDisclosures": {"005930": evidence.raw_payload}},
        )

        filing = next(item for item in graph.entities if item.kind == "disclosure-filing")
        self.assertEqual("verified", filing.properties["documentVerificationState"])
        self.assertEqual("ready", filing.properties["documentAnalysisState"])
        self.assertEqual("eligible", filing.properties["evidenceEligibilityState"])
        filing_relations = [
            item for item in graph.relations
            if item.target == filing.entity_id and item.relation_type == "HAS_EXTERNAL_SIGNAL"
        ]
        self.assertEqual(1, len(filing_relations))
        self.assertEqual(
            "eligible",
            filing_relations[0].properties["evidenceEligibilityState"],
        )

    def test_current_fact_backfill_projects_without_alert_replay(self):
        cursor = MemoryCursor()
        reconciler = ExternalFactResearchEvidenceReconciler(
            MemoryEventReader([]),
            self.projector,
            cursor,
            initial_lookback_minutes=120,
            now_provider=lambda: self.now,
        )

        first = reconciler.run_once()
        second = reconciler.run_once()

        self.assertEqual(1, first["currentFactBackfill"]["processedCount"])
        self.assertEqual(0, second["currentFactBackfill"]["processedCount"])
        self.assertTrue(cursor.state["currentFactBackfillCompleted"])
        self.assertEqual(OFFICIAL_EVIDENCE_PROJECTOR_VERSION, cursor.state["currentFactBackfillVersion"])
        collected = next(event for event in self.publisher.events if event.name == RESEARCH_EVIDENCE_COLLECTED)
        self.assertEqual(0, collected.payload["alertEligibleCount"])

    def test_reconciler_advances_only_after_projecting_durable_event(self):
        cursor = MemoryCursor()
        reconciler = ExternalFactResearchEvidenceReconciler(
            MemoryEventReader([self.event()]),
            self.projector,
            cursor,
            initial_lookback_minutes=120,
            now_provider=lambda: self.now,
        )

        result = reconciler.run_once()

        self.assertEqual(1, result["processedCount"])
        self.assertEqual("event-dart-1", cursor.state["lastEventId"])

        restarted = ExternalFactResearchEvidenceReconciler(
            MemoryEventReader([]),
            self.projector,
            cursor,
            initial_lookback_minutes=120,
            now_provider=lambda: self.now,
        )
        status = restarted.status()
        self.assertTrue(status["durable"])
        self.assertEqual("ok", status["status"])
        self.assertEqual("event-dart-1", status["cursorEventId"])
        self.setUp()
        self._assert_reconciler_prefers_leased_membership_and_completes_once()
        self.setUp()
        self._assert_official_fact_event_registers_delivery_in_same_connection()
        self.setUp()
        self._assert_non_official_fact_event_does_not_register_official_delivery()
        self.setUp()
        self._assert_mysql_delivery_claim_returns_lease_and_exact_event()

    def _assert_reconciler_prefers_leased_membership_and_completes_once(self):
        cursor = MemoryCursor()
        event_reader = MemoryDurableEventReader([self.event()])
        reconciler = ExternalFactResearchEvidenceReconciler(
            event_reader,
            self.projector,
            cursor,
            initial_lookback_minutes=120,
            now_provider=lambda: self.now,
        )

        first = reconciler.run_once()
        second = reconciler.run_once()

        self.assertEqual("leased-membership", first["deliveryMode"])
        self.assertEqual(1, first["processedCount"])
        self.assertEqual(0, second["processedCount"])
        self.assertEqual({"event-dart-1"}, event_reader.completed)

    def _assert_official_fact_event_registers_delivery_in_same_connection(self):
        connection = RecordingEventConnection()

        insert_domain_event_with_connection(connection, self.event())

        self.assertEqual(2, len(connection.calls))
        self.assertIn("INSERT IGNORE INTO domain_events", connection.calls[0][0])
        self.assertIn("external_fact_projection_deliveries", connection.calls[1][0])

    def _assert_non_official_fact_event_does_not_register_official_delivery(self):
        connection = RecordingEventConnection()
        event = self.event()
        event.payload["datasetId"] = "yfinance.price"

        insert_domain_event_with_connection(connection, event)

        self.assertEqual(1, len(connection.calls))

    def _assert_mysql_delivery_claim_returns_lease_and_exact_event(self):
        event = self.event()
        connection = ProjectionDeliveryConnection(event)
        event_log = ProjectionDeliveryEventLog(connection)

        claimed = event_log.claim_external_fact_projection_events(
            "external-official-evidence",
            OFFICIAL_EVIDENCE_PROJECTOR_VERSION,
            "worker:test",
            limit=1,
        )

        self.assertEqual(1, len(claimed))
        self.assertEqual(event.event_id, claimed[0]["event"].event_id)
        self.assertTrue(claimed[0]["leaseToken"])


if __name__ == "__main__":
    unittest.main()
