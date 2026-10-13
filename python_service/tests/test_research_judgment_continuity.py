"""Original filing continuity and research/publication authority regressions."""
import copy
import gzip
import json
import unittest
from unittest.mock import Mock

from ai_insight_fixtures import packet, plan
from digital_twin.modules.news_intelligence.domain.investment_research import ResearchEvidence, research_evidence_from_payload
from digital_twin.modules.news_intelligence.domain.research_document_reference import research_document_reference, digest
from digital_twin.modules.reasoning.domain.ontology_projection_input import compact_research_evidence_item
from digital_twin.modules.ai_orchestration.domain import research_review
from digital_twin.modules.ai_orchestration.domain.research_evidence_use import usable_fact, reference_instructions
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, validate_execution_input
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality, quality_block
from digital_twin.modules.ai_orchestration.infrastructure.mysql_brain_agenda import MySQLBrainAgendaStore


def document():
    text = "Three months ended June 30, 2026 and 2025. Revenue USD millions: 100 and 80."
    url = "https://www.sec.gov/Archives/edgar/data/1/report.htm"
    raw = {"researchTaskIds": ["source-task"], "documentVerified": True, "analysisReady": True,
           "officialDocumentState": "document-verified", "evidenceLifecycleState": "active",
           "officialDocumentText": text, "documentHash": digest(text), "sourceDocumentHash": digest(text),
           "documentPassages": [{"quote": text, "passageHash": digest(text)}], "reportDate": "2026-06-30",
           "documentIssuerIdentity": {"symbol": "TEST", "sourceUrl": url, "verification": "sec-discovered-issuer-document"},
           "evidenceGovernance": {"verificationStatus": "verified-primary", "sourceTrustState": "trusted",
                                  "validationState": "ready", "investmentJudgmentEligible": True},
           "promptEvidenceAdmission": {"decisionEligible": False, "promptEligible": False,
                                       "reasonCodes": ["evidence-stale"], "freshnessState": "stale"}}
    return ResearchEvidence("research:TEST:sec-question:1", "TEST", "filing", "SEC EDGAR", "10-Q",
                            url=url, published_at="2026-07-15", observed_at="2026-10-14T00:00:00Z", raw_payload=raw)


class ResearchJudgmentContinuityTests(unittest.TestCase):
    def test_dated_filing_survives_projection_without_event_promotion(self):
        item = document()
        compact = compact_research_evidence_item(item.to_dict())
        restored = research_evidence_from_payload(compact)
        ref = research_document_reference(restored)
        self.assertEqual(research_document_reference(item), ref)
        self.assertEqual(item.raw_payload["officialDocumentText"], ref["text"])
        self.assertFalse(restored.raw_payload["promptEvidenceAdmission"]["decisionEligible"])
        from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
        from digital_twin.modules.reasoning.domain.portfolio_ontology_research_concepts import add_research_evidence_concepts
        graph = PortfolioOntology("test")
        add_research_evidence_concepts(graph, "stock", "thesis", "opinion", "TEST", {},
                                      {"researchEvidence": {"TEST": [compact]}})
        node = next(row for row in graph.entities if row.kind == "research-evidence")
        self.assertEqual(ref, node.properties["historicalDocumentReference"])
        self.assertFalse(node.properties["investmentJudgmentEligible"])
        self.assertFalse(node.properties["reasoningEligible"])
        fact = {"symbol": item.symbol, "url": item.url, "judgementEvidenceUsable": False,
                "historicalDocumentReference": ref}
        self.assertTrue(usable_fact(fact, "historicalDocumentReference.text"))
        self.assertFalse(usable_fact(fact, "analysisSummary"))
        self.assertFalse(usable_fact({**fact, "valuationDecisionEligible": False}))
        self.assertIn("보고 기간", reference_instructions({"facts": [fact]}))
        self.assertEqual("", reference_instructions(packet()))

    def test_damaged_untrusted_superseded_future_and_oversized_sources_fail_closed(self):
        for key, value in (("documentHash", "changed"), ("documentVerified", False),
                           ("evidenceLifecycleState", "retracted"), ("reportDate", "2027-01-01"),
                           ("researchTaskIds", []), ("officialDocumentText", "x" * 20000)):
            item = document(); item.raw_payload[key] = value
            with self.subTest(key=key):
                self.assertEqual({}, research_document_reference(item))
        for change in ({"supersededByEvidenceId": "new"}, {"sourceTrustState": "untrusted"},
                       {"conflictingEvidenceIds": ["conflict"]}):
            item = document(); item.raw_payload["evidenceGovernance"].update(change)
            self.assertEqual({}, research_document_reference(item))
        item = document(); item.raw_payload["documentPassages"][0]["quote"] = "invented"
        self.assertEqual({}, research_document_reference(item))
        item = document(); item.raw_payload["promptEvidenceAdmission"]["reasonCodes"] += ["document-unverified"]
        self.assertEqual({}, research_document_reference(item))

    def test_original_question_sources_resolve_only_inside_captured_graph(self):
        from test_ai_directed_retrieval import session, SUBJECT
        from digital_twin.modules.reasoning.public import ObservationEvidenceReader
        from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
        from working_evidence_checks import decision
        _, source = session()
        item = document()
        source.candidates.return_value.append({"id": "original", "kind": "research-evidence", "symbol": "TEST",
            "url": item.url, "historicalDocumentReference": research_document_reference(item),
            "judgementEvidenceUsable": False, "documentVerified": True})
        captured = ObservationEvidenceReader(source).capture_session(SUBJECT)
        index = captured.question_document_index({item.evidence_id: ["question"]})
        self.assertEqual(1, len(index))
        self.assertEqual([], captured.question_document_index({"missing-source": ["question"]}))
        self.assertIn(index[0]["factId"], captured.business_baseline(index)["factIds"])
        def decide(envelope, _):
            validate_execution_input(envelope)
            self.assertEqual(index, envelope["retrievalContext"]["questionDocumentIndex"])
            return decision("finish", [index[0]["factId"]])
        memory = [{"kind": "brain-case", "caseId": "question", "latestSourceAnswer": {
            "sources": [{"evidenceId": item.evidence_id}]}}]
        result = retrieve_working_evidence(captured, captured.packet(), [], memory, decide, lambda _: "input", 262144)
        self.assertEqual("ready", result[0]["retrieval"]["status"])
        self.assertTrue(any(row.get("historicalDocumentReference") for row in result[0]["facts"]))

    def test_research_review_does_not_approve_rejected_notification(self):
        result = {**plan(), "input": packet(), "executionInputId": "author", "followUpConditions": [],
                  "caseReviews": [{"caseId": "question", "action": "answered"}]}
        result["notification"]["send"] = False
        result["quality"] = local_quality(result)
        self.assertEqual("rejected", result["quality"]["status"])
        envelope = research_review.freeze(result, [], 262144)
        validate_execution_input(envelope)
        review = {"version": research_review.VERSION, "accepted": True, "reason": "원래 질문의 기간과 원문 근거가 일치합니다."}
        result["researchQuality"] = research_review.accept(result, review, "research-review")
        self.assertEqual("accepted", result["researchQuality"]["status"])
        self.assertTrue(quality_block(result))
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = {"artifact_gzip": gzip.compress(json.dumps(envelope).encode())}
        job = {"taskId": "task", "attempts": 1}
        self.assertTrue(MySQLBrainAgendaStore.research_proof(connection, job, result, {"researchResults": []}))
        result["caseReviews"][0]["action"] = "dismissed"
        with self.assertRaises(ValueError):
            MySQLBrainAgendaStore.research_proof(connection, job, result, {"researchResults": []})
        connection.execute.return_value.fetchone.return_value = None
        with self.assertRaises(ValueError):
            MySQLBrainAgendaStore.research_proof(connection, job, result, {"researchResults": []})
        self.assertEqual("rejected", research_review.accept(result, {**review, "accepted": False}, "r")["status"])
        changed = copy.deepcopy(envelope); changed["proposal"]["caseReviews"][0]["action"] = "dismissed"
        with self.assertRaises(ValueError):
            validate_execution_input(changed)
