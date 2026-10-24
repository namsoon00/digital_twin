"""A missing API answer can trigger AI-selected searches and original reads."""
import copy
import io
import json
import socket
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from digital_twin.modules.ai_orchestration.application.research import execute_research
from digital_twin.modules.ai_orchestration.domain.research_request import validate_research_request
from digital_twin.modules.news_intelligence.application.source_exploration_service import SourceExplorationService
from digital_twin.modules.news_intelligence.application.investment_research_orchestration_service import InvestmentResearchOrchestrationService
from digital_twin.modules.news_intelligence.application.hypothesis_research_planner_service import HypothesisResearchPlanningService
from digital_twin.modules.news_intelligence.domain.investment_research import NewsCollectionTarget
from digital_twin.modules.news_intelligence.domain.investment_evidence_governance import governed_evidence
from digital_twin.modules.news_intelligence.domain.research_progress import assess_tasks, evidence_packets
from digital_twin.modules.news_intelligence.domain.source_exploration import exploration_action, public_url
from digital_twin.modules.news_intelligence.domain.source_recovery import source_recovery_summary
from digital_twin.modules.news_intelligence.infrastructure.source_document_tools import SourceDocumentTools, publication_metadata, pdf_pages
from digital_twin.modules.news_intelligence.infrastructure.source_exploration_ai import PublicSourceSearch, search_receipt, source_command
from digital_twin.modules.news_intelligence.infrastructure.public_document_http import fetch_public_document, public_addresses, _PinnedHTTPS
from test_question_research_progress import Advisor, EvidenceStore, Gateway, RunStore


TARGET = NewsCollectionTarget("NVDA", "NVIDIA", "US", "USD")
URL = "https://investor.nvidia.com/fixture-results"
STAMP = datetime.now(timezone.utc).date().isoformat()
TASK = {"taskId": "original-question", "question": "NVIDIA data center revenue comparison and units?",
        "queryTerms": ["data center revenue"], "sourceTypes": ["company-ir", "official-filing"],
        "requiredEvidenceTypes": ["official-document"], "requiresDocumentBody": True, "maxAgeMinutes": 1440}
TABLE = "<table><tr><th>NVIDIA revenue (USD in millions)</th><th>Current quarter</th><th>Prior year quarter</th></tr>" + "".join(
    "<tr><td>Data Center revenue comparative reporting line %s</td><td>120</td><td>100</td></tr>" % i for i in range(6)) + "</table>"
HTML = ('<html><head><title>NVIDIA fixture results</title><meta property="article:published_time" content="' + STAMP + '"></head>'
        '<body><h1>NVIDIA fixture results</h1>' + TABLE + '</body></html>').encode()


def tools_for(body=HTML, url=URL):
    search = Mock()
    search.search.return_value = {"status": "search-completed", "candidates": [{"url": url, "title": "Fixture results"}]}
    fetch = Mock(return_value={"url": url, "body": body, "contentType": "text/html", "redirects": []})
    return SourceDocumentTools(search, fetch=fetch)


class Explorer:
    def __init__(self):
        self.contexts = []

    def choose(self, context, timeout):
        self.contexts.append(copy.deepcopy(context))
        base = {"reason": "Find the missing comparison in the issuer's original report.", "missingRequirements": ["prior-year units"]}
        if len(self.contexts) == 1:
            return {**base, "action": "search", "query": "NVIDIA data center revenue prior year quarterly results"}
        if len(self.contexts) == 2:
            key = next(row["candidateId"] for row in context["candidates"] if row["url"] == URL)
            return {**base, "action": "read", "candidateId": key}
        return {**base, "action": "finish", "missingRequirements": []}


class SourceExplorationTests(unittest.TestCase):
    def test_focused_extraction_recovers_table_units_without_changing_source_or_admission(self):
        markup = HTML.replace(b'NVIDIA revenue (USD in millions)', b'NVIDIA revenue').replace(
            b'<table>', b'<h2>NVIDIA consolidated financial statements</h2><p>USD in millions, unaudited</p><table>')
        tools = tools_for(markup)
        candidate = {"url": URL, "title": "NVIDIA original results"}
        first = tools.read(TARGET, candidate, [TASK], 10)
        self.assertNotIn("USD in millions", first["evidence"].raw_payload["officialDocumentText"])
        focused = tools.extract(TARGET, candidate, [TASK], first["document"], ["revenue units"], 10)
        text = focused["evidence"].raw_payload["officialDocumentText"]
        for value in ("USD in millions, unaudited", "Current quarter", "Prior year quarter", "120", "100"):
            self.assertIn(value, text)
        self.assertEqual(first["document"]["sourceDocumentHash"], focused["document"]["sourceDocumentHash"])
        self.assertEqual(first["evidence"].published_at, focused["evidence"].published_at)
        self.assertFalse(focused["evidence"].raw_payload["valuationInputEligible"])
        # A focused read cannot promote an undated source or silently replace
        # the original document with a newer revision.
        undated = tools_for(markup.replace(b'article:published_time', b'unknown-date'))
        read = undated.read(TARGET, candidate, [TASK], 10)
        extracted = undated.extract(TARGET, candidate, [TASK], read["document"], ["revenue units"], 10)
        self.assertNotIn("evidence", extracted)
        self.assertIn("publication-date-unverified", extracted["document"]["admissionBlockers"])
        tools.fetch.return_value = {**tools.fetch.return_value, "body": markup + b'changed'}
        with self.assertRaisesRegex(ValueError, "document-revision-changed"):
            tools.extract(TARGET, candidate, [TASK], first["document"], ["revenue units"], 10)

    def test_extraction_resumes_once_with_original_contract_and_real_progress(self):
        from digital_twin.infrastructure.local_ai_process_guard import LocalAICapacityUnavailable
        tools = tools_for()
        tools.seeds = Mock(return_value=[{"url": URL, "title": "NVIDIA results"}])
        receipt = tools.fetch.return_value
        tools.fetch.side_effect = [receipt, LocalAICapacityUnavailable("capacity"), receipt]
        contexts, checkpoints = [], []
        def choose(context, timeout):
            contexts.append(copy.deepcopy(context))
            base = {"reason": "Inspect missing source context", "missingRequirements": ["units"]}
            if len(contexts) == 1:
                return {**base, "action": "read", "candidateId": context["candidates"][0]["candidateId"]}
            if len(contexts) == 2:
                self.assertIn("extract", context["budget"]["allowedActions"])
                return {**base, "action": "extract", "candidateId": context["extractableCandidateIds"][0], "focus": ["revenue units"]}
            self.assertNotIn("extract", context["budget"]["allowedActions"])
            return {**base, "action": "finish"}
        advisor = Mock(); advisor.choose.side_effect = choose
        service = SourceExplorationService(advisor, tools, deferred_errors=(LocalAICapacityUnavailable,))
        task = {**TASK, "purpose": "Original quarter, issuer and units must remain unchanged"}
        with self.assertRaises(LocalAICapacityUnavailable):
            service.explore(TARGET, [task], [], [], checkpoint=checkpoints.append)
        evidence, audit = service.explore(TARGET, [task], [], [], resume=checkpoints[-1])
        self.assertEqual((2, 1), (audit["readCount"], audit["extractionCount"]))
        self.assertEqual(3, tools.fetch.call_count)
        self.assertEqual(task["purpose"], contexts[0]["tasks"][0]["purpose"])
        self.assertGreater(audit["recovery"]["progress"]["newPassageCount"], 0)
        self.assertEqual("requires-independent-review", audit["recovery"]["progress"]["answerCompletion"])
        self.assertEqual(len(evidence), len({row.evidence_id for row in evidence}))
        self.assertTrue(audit["complete"])

    def test_recovery_reaches_central_memory_without_claiming_absence_or_answer_success(self):
        from digital_twin.modules.ai_orchestration.domain.research_return import research_return
        tools = tools_for(); tools.fetch.side_effect = ValueError("document-http-403")
        explorer = Explorer()
        _, audit = SourceExplorationService(explorer, tools).explore(TARGET, [TASK], [], [],
            provider_statuses=[{"status": "unsupported-document-source"}],
            previous_recovery={"version": "source-recovery-v1", "issues": [{"cause": "date-unverified"}]})
        self.assertIn("unsupported-source", explorer.contexts[0]["recovery"]["causeCounts"])
        self.assertNotIn("budget-exhausted", explorer.contexts[0]["recovery"]["causeCounts"])
        self.assertIn("access-denied", explorer.contexts[-1]["recovery"]["causeCounts"])
        self.assertTrue(explorer.contexts[-1]["previousRecovery"])
        result = research_return({"status": "evidence-unavailable", "executedPlan": {"sourceExploration": audit}},
                                 "run", TASK["taskId"], {})
        self.assertEqual("unavailable", result["documentaryAnswer"]["status"])
        self.assertIn("access-denied", result["sourceRecovery"]["causeCounts"])
        self.assertEqual(0, result["sourceRecovery"]["progress"]["collectedEvidenceCount"])
        self.assertNotIn("unpublished", str(result))
        # Historical receipts have no extraction instrumentation; absent
        # measurements cannot become claims of zero progress.
        historical = source_recovery_summary({"steps": [{"step": 1, "errorKind": "unexpected-error"}]})
        self.assertFalse(historical["progress"]["measured"])
        self.assertEqual("unclassified-failure", historical["issues"][0]["cause"])

    def _assert_original_publication_dates_accept_iso_and_exclude_related_stories(self):
        from digital_twin.infrastructure.external_api.adapters.issuer_ir import _date
        for value in ('2026-01-29T16:00:00.000+0900', '2026-01-29T07:00:00Z', '2026-01-29'):
            self.assertEqual('2026-01-29', _date(value))
        markup = '<script type="application/ld+json">{"datePublished":"2026-01-29T16:00:00.000+0900"}</script>'
        self.assertEqual('2026-01-29', publication_metadata(markup)[0])
        main = '<meta property="article:published_time" content="2026-07-29T09:00:00+09:00">'
        self.assertEqual('2026-07-29', publication_metadata(main + markup)[0])
        self.assertFalse(publication_metadata(main + '<meta property="article:published_time" content="2026-07-28">')[0])
        header = '<article><ul class="post-info"><li><span class="date">July 29, 2026</span></li></ul></article>'
        related = '<article class="item"><div class="date">April 22, 2026</div></article>'
        self.assertEqual('2026-07-29', publication_metadata(header + related)[0])
        self.assertFalse(publication_metadata('<meta property="article:published_time" content="2026-02-31T09:00:00Z">')[0])

    def _assert_exact_official_index_date_can_complete_html_or_pdf_provenance(self):
        digest = 'ab' * 32
        candidate = {'url': URL, 'discovery': 'official-page-link', 'discoveredFrom': 'https://investor.nvidia.com/',
            'parentDocumentHash': digest, 'publishedAt': STAMP, 'publicationProof': {'kind': 'official-index-label',
                'sourceUrl': 'https://investor.nvidia.com/', 'sourceDocumentHash': digest, 'value': 'NVIDIA results ' + STAMP}}
        reader = tools_for(HTML.replace(b'article:published_time', b'unknown-date'))
        self.assertIn('evidence', reader.read(TARGET, candidate, [TASK], 10))
        for change in ({'discovery': 'web-search-lead'}, {'parentDocumentHash': 'wrong'},
                       {'discoveredFrom': 'https://example.org/'}, {'publishedAt': '2020-01-01'}):
            self.assertNotIn('evidence', reader.read(TARGET, {**candidate, **change}, [TASK], 10))
        conflicting = HTML.replace(STAMP.encode(), b'2026-01-29') + b'<meta property="article:published_time" content="2026-01-28">'
        self.assertNotIn('evidence', tools_for(conflicting).read(TARGET, candidate, [TASK], 10))
        pdf = SourceDocumentTools(None, fetch=lambda *_: {'url': URL, 'body': b'%PDF-fixture', 'contentType': 'application/pdf'},
            pdf_reader=lambda *_: {'pages': [{'page': 1, 'text': 'NVIDIA data center revenue and units. ' * 20}],
                'pageCount': 1, 'omittedPages': [], 'unreadPageCount': 0})
        result = pdf.read(TARGET, candidate, [TASK], 10)
        self.assertEqual(STAMP, result['evidence'].published_at)
        self.assertEqual('official-index-label', result['evidence'].raw_payload['publicationProof']['kind'])

    def test_capacity_wait_resumes_decision_search_or_review_without_refetching_sources(self):
        from digital_twin.infrastructure.local_ai_process_guard import LocalAICapacityUnavailable
        busy = LocalAICapacityUnavailable("fixture capacity wait")
        deferred = (LocalAICapacityUnavailable,)
        for phase in ("decision", "search", "review"):
            with self.subTest(phase=phase):
                tools, explorer, evidence, runs, gateway = tools_for(), Explorer(), EvidenceStore(), RunStore(), Gateway([[]])
                advisor = Mock()
                chooser, reviewer = explorer.choose, Advisor().plan
                waited = []
                def choose(context, timeout):
                    if phase == "decision" and not waited:
                        waited.append(True)
                        raise busy
                    return chooser(context, timeout)
                def review(context):
                    if phase == "review" and not waited:
                        waited.append(True)
                        raise busy
                    return reviewer(context)
                advisor.choose.side_effect = choose
                planner = Mock(); planner.plan.side_effect = review
                if phase == "search":
                    tools.search_provider.search.side_effect = [busy, tools.search_provider.search.return_value]
                service = SourceExplorationService(advisor, tools, deferred_errors=deferred)
                orchestrator = InvestmentResearchOrchestrationService(evidence, gateway, research_store=runs,
                    hypothesis_research_planner=HypothesisResearchPlanningService(planner, deferred_errors=deferred),
                    source_exploration=service, deferred_errors=deferred,
                    settings={"investmentBrainResearchCooldownMinutes": 0})
                lookup = Mock(); lookup.get_run.side_effect = lambda _: runs.rows[-1].to_dict() if runs.rows else None
                job = {"accountId": "private-account", "symbol": "NVDA", "name": "NVIDIA", "taskId": "resumable-job",
                    "question": TASK["question"], "researchRequest": validate_research_request({
                        "sourceTypes": ["official-filing"], "queryTerms": ["data center revenue"], "maxAgeMinutes": 1440}, True)}
                with self.assertRaises(LocalAICapacityUnavailable):
                    execute_research(job, lookup, lambda: orchestrator)
                checkpoint = runs.rows[-1].to_dict()
                self.assertEqual("processing", checkpoint["status"])
                self.assertFalse(checkpoint["completedAt"])
                audit = checkpoint["executedPlan"]["sourceExploration"]
                if phase != "review":
                    self.assertEqual("deferred", audit["steps"][-1]["status"])
                    self.assertFalse(audit["complete"])
                result = execute_research(job, lookup, lambda: orchestrator)
                self.assertEqual("answered", result["documentaryAnswer"]["status"])
                self.assertEqual(1, len(gateway.requests))
                self.assertEqual(1, tools.fetch.call_count)
                self.assertEqual((1, 1), (result["sourceExploration"]["searchCount"], result["sourceExploration"]["readCount"]))
                self.assertEqual(3, len(runs.rows[-1].to_dict()["executedPlan"]["sourceExploration"]["steps"]))
                self.assertEqual(4 if phase == "decision" else 3, advisor.choose.call_count)

    def test_original_question_survives_api_miss_search_read_review_and_projection(self):
        tools, explorer, evidence, runs, gateway = tools_for(), Explorer(), EvidenceStore(), RunStore(), Gateway([[]])
        service = SourceExplorationService(explorer, tools)
        orchestrator = InvestmentResearchOrchestrationService(evidence, gateway, research_store=runs,
            hypothesis_research_planner=HypothesisResearchPlanningService(Advisor()), source_exploration=service,
            settings={"investmentBrainResearchCooldownMinutes": 0})
        lookup = Mock(); lookup.get_run.return_value = None
        job = {"accountId": "private-account", "symbol": "NVDA", "name": "NVIDIA", "taskId": "research-job",
            "question": TASK["question"], "researchRequest": validate_research_request({
                "sourceTypes": ["official-filing"], "queryTerms": ["data center revenue"], "maxAgeMinutes": 1440}, True)}
        result = execute_research(job, lookup, lambda: orchestrator)
        self.assertEqual("answered", result["documentaryAnswer"]["status"])
        self.assertEqual("requirements-met", result["stopReason"])
        self.assertEqual(1, len(gateway.requests), "a failed fixed source must not be fetched repeatedly")
        self.assertEqual((1, 1), (result["sourceExploration"]["searchCount"], result["sourceExploration"]["readCount"]))
        self.assertEqual(URL, result["documentaryAnswer"]["sources"][0]["sourceUrl"])
        run = runs.rows[-1].to_dict()
        self.assertEqual(1, len(run["taskIds"]))
        self.assertEqual(job["question"], run["taskAssessments"][0]["question"])
        self.assertEqual(run["taskIds"], result["sourceExploration"]["taskIds"])
        self.assertNotIn("private-account", json.dumps(explorer.contexts))
        self.assertTrue(any(row.to_dict()["executedPlan"].get("sourceExploration", {}).get("steps", [{}])[-1].get("status") == "deciding"
                            for row in runs.rows if row.to_dict()["executedPlan"].get("sourceExploration", {}).get("steps")))
        stored = evidence.saved[0]
        self.assertEqual("issuer-ir", stored.kind)
        self.assertTrue(stored.raw_payload["evidenceGovernance"]["investmentJudgmentEligible"])
        from digital_twin.modules.reasoning.domain.portfolio_ontology_research_concepts import add_research_evidence_concepts
        from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
        graph = PortfolioOntology("fixture")
        add_research_evidence_concepts(graph, "stock:NVDA", "", "", "NVDA", {},
            {"researchEvidence": {"NVDA": [stored.to_dict()]}})
        self.assertTrue(any("IssuerIRDocument" in row.properties.get("tboxClasses", []) for row in graph.entities))

    def test_search_leads_require_a_real_search_receipt_and_never_carry_trust(self):
        message = {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps({"candidates": [
            {"url": URL, "title": "Found", "officialSource": True}, {"url": "file:///etc/passwd"}]})}}
        with self.assertRaisesRegex(ValueError, "receipt-missing"):
            search_receipt(json.dumps(message))
        result = search_receipt(json.dumps({"type": "item.completed", "item": {"type": "web_search", "id": "s1", "query": "NVIDIA"}}) + "\n" + json.dumps(message))
        self.assertEqual(1, len(result["candidates"]))
        self.assertNotIn("officialSource", result["candidates"][0])
        self.assertEqual("discovery-only", result["authority"])
        receipt = json.dumps({"type": "item.completed", "item": {"type": "web_search", "query": "NVIDIA"}}) + "\n" + json.dumps(message)
        invoke = Mock(return_value=SimpleNamespace(returncode=0, stdout=receipt))
        filtered = PublicSourceSearch(invoke).search(TARGET, "NVIDIA results site:nvidianews.nvidia.com", 10)
        self.assertFalse(filtered["candidates"], "the search model cannot drop the requested alternative host")
        self.assertEqual(1, filtered["omittedBySiteConstraint"])

    def test_completed_exploration_resumes_without_repeating_model_or_source_operations(self):
        tools, explorer = tools_for(), Explorer()
        evidence, audit = SourceExplorationService(explorer, tools).explore(TARGET, [TASK], [], [])
        self.assertTrue(audit["complete"])
        no_calls = Mock(); no_calls.choose.side_effect = AssertionError("must reuse checkpoint")
        recovered, restored = SourceExplorationService(no_calls, tools).explore(TARGET, [TASK], [], [], resume=audit)
        self.assertEqual([row.evidence_id for row in evidence], [row.evidence_id for row in recovered])
        self.assertEqual(audit, restored)
        with self.assertRaisesRegex(ValueError, "resume-contract-mismatch"):
            SourceExplorationService(no_calls, tools).explore(TARGET, [{**TASK, "question": "Different question"}], [], [], resume=audit)

    def test_official_pdf_redirect_preserves_identity_but_does_not_invent_a_publication_date(self):
        url = "https://cdn.example.org/issuer-report.pdf"
        original = "https://investor.nvidia.com/files/report.pdf"
        tools = SourceDocumentTools(None, fetch=Mock(return_value={"url": url, "body": b"%PDF-fixture",
            "contentType": "application/pdf", "redirects": [{"from": original, "to": url}]}),
            pdf_reader=Mock(return_value={"pages": [{"page": 1, "text": "NVIDIA data center revenue in USD millions. " * 15}],
                "pageCount": 1, "omittedPages": [], "unreadPageCount": 0}))
        result = tools.read(TARGET, {"url": original, "title": "NVIDIA report"}, [TASK], 10)
        self.assertEqual("official-issuer-redirect", result["document"]["issuerVerification"])
        self.assertEqual(["publication-date-unverified"], result["document"]["admissionBlockers"])
        self.assertNotIn("evidence", result)

        # The same URL may first arrive as an unverified search lead and later
        # acquire proof from an actually read issuer index.
        reader = Mock()
        reader.seeds.return_value = [{"url": URL}]
        reader.search.return_value = {"candidates": [{"url": url, "discovery": "web-search-lead"}]}
        reader.read.side_effect = [
            {"candidates": [{"url": url, "discovery": "official-page-link", "discoveredFrom": URL,
                "parentDocumentHash": "original-index-hash", "publicationProof": {"value": STAMP}, "publishedAt": STAMP}]},
            {"status": "document-read"}]
        def choose(context, timeout):
            index = len(context["previousSteps"])
            if index == 0:
                return {"action": "search", "query": "NVIDIA official report", "reason": "Find report"}
            if index == 3:
                return {"action": "finish", "reason": "Review receipts"}
            selected = URL if index == 1 else url
            return {"action": "read", "reason": "Read original source",
                "candidateId": next(row["candidateId"] for row in context["candidates"] if row["url"] == selected)}
        SourceExplorationService(SimpleNamespace(choose=choose), reader).explore(TARGET, [TASK], [], [])
        admitted_candidate = reader.read.call_args.args[1]
        self.assertEqual(STAMP, admitted_candidate["publishedAt"])
        self.assertEqual("official-page-link", admitted_candidate["discovery"])

    def test_public_read_cannot_promote_unknown_issuer_undated_or_wrong_subject_documents(self):
        for url, body, reason in (
                ("https://example.org/report", HTML, "issuer-source-unverified"),
                (URL, HTML.replace(b'article:published_time', b'unknown-date'), "publication-date-unverified"),
                (URL, HTML.replace(b'NVIDIA', b'Other Company'), "document-subject-unconfirmed")):
            with self.subTest(reason=reason):
                result = tools_for(body, url).read(TARGET, {"url": url, "title": "report"}, [TASK], 10)
                self.assertNotIn("evidence", result)
                self.assertIn(reason, result["document"]["admissionBlockers"])
        stale = tools_for(HTML.replace(STAMP.encode(), b'2020-01-01')).read(TARGET, {"url": URL}, [TASK], 10)["evidence"]
        accepted, _, rejected = governed_evidence([stale], TARGET, 1440)
        self.assertFalse(accepted)
        self.assertIn("evidence-stale", rejected[0].reasons)

    def test_read_preserves_whole_tables_publication_proof_and_page_number(self):
        self._assert_original_publication_dates_accept_iso_and_exclude_related_stories()
        self._assert_exact_official_index_date_can_complete_html_or_pdf_provenance()
        # Related-story dates must not replace the original article's visible
        # publication label; two conflicting article dates still fail closed.
        markup = '<title>Report</title><div class="article-date">August 26, 2026</div><div class="date">October 8, 2026</div>'
        self.assertEqual("2026-08-26", publication_metadata(markup)[0])
        self.assertEqual("Report", publication_metadata(markup + '<svg><title>Navigation control</title></svg>')[2])
        self.assertEqual("", publication_metadata(markup + '<div class="article-date">August 25, 2026</div>')[0])
        result = tools_for().read(TARGET, {"url": URL}, [TASK], 10)
        evidence = result["evidence"]
        quote = evidence.raw_payload["documentPassages"][0]["quote"]
        self.assertIn("USD in millions", quote)
        self.assertIn("Prior year quarter", quote)
        self.assertIn("reporting line 5", quote)
        self.assertEqual(STAMP, evidence.published_at)
        self.assertTrue(evidence.to_dict()["metadataVerified"])
        self.assertTrue(evidence.to_dict()["analysisReady"])
        self.assertEqual("original-publication-metadata", evidence.raw_payload["publicationProof"]["kind"])
        evidence.raw_payload["documentPassages"][0]["page"] = 4
        accepted, claims, _ = governed_evidence([evidence], TARGET, 1440)
        self.assertEqual(4, evidence_packets(accepted, claims)[0]["documentPassages"][0]["page"])

    def test_ai_missing_requirements_prevent_false_completion(self):
        from judgment_quality_checks import check_source_requirements
        check_source_requirements(self)
        evidence = tools_for().read(TARGET, {"url": URL}, [TASK], 10)["evidence"]
        accepted, claims, _ = governed_evidence([evidence], TARGET, 1440)
        packets = evidence_packets(accepted, claims)
        now = datetime.now(timezone.utc).isoformat()
        baseline = assess_tasks([TASK], packets, "NVDA", now)[0]
        review = {"taskId": TASK["taskId"], "assessmentFingerprint": baseline["assessmentFingerprint"],
            "status": "addressed", "evidenceIds": [evidence.evidence_id], "reason": "Some information found.",
            "missingRequirements": ["사업부 영업이익", "전년 동기 금액 단위"]}
        assessed = assess_tasks([TASK], packets, "NVDA", now, [review])[0]
        self.assertNotEqual("addressed", assessed["status"])
        self.assertEqual(review["missingRequirements"], assessed["missingRequirements"])

    def test_duplicate_operations_unknown_candidates_and_private_queries_do_not_execute(self):
        tools = tools_for()
        advisor = Mock(); advisor.choose.return_value = {"action": "search", "query": "NVIDIA same query", "reason": "find data"}
        _, audit = SourceExplorationService(advisor, tools).explore(TARGET, [TASK], [], [])
        self.assertEqual(1, tools.search_provider.search.call_count)
        self.assertEqual(2, len(audit["steps"]))
        self.assertEqual("operation-repeated", audit["stopReason"])
        self.assertEqual(2, advisor.choose.call_count)
        self.assertIn("duplicate-exploration-operation", str(audit))
        for value in ({"action": "read", "candidateId": "invented", "reason": "read"},
                      {"action": "search", "query": "private-account data", "reason": "search"},
                      {"action": "search", "query": "NVIDIA user@example.org", "reason": "search"}):
            with self.assertRaises(ValueError):
                exploration_action(value, [], ["private-account"])

    def test_budget_and_failure_are_not_reported_as_no_public_information(self):
        tools = tools_for()
        explorer = Explorer()
        tools.fetch.side_effect = ValueError("document-http-403")
        evidence, audit = SourceExplorationService(explorer, tools).explore(TARGET, [TASK], [], [])
        self.assertFalse(evidence)
        self.assertEqual("ai-finished", audit["stopReason"])
        self.assertEqual("failed", audit["steps"][1]["status"])
        self.assertEqual("document-http-403", audit["steps"][1]["errorKind"])
        clock = Mock(side_effect=[0, 361, 362])
        evidence, audit = SourceExplorationService(Explorer(), tools, clock=clock).explore(TARGET, [TASK], [], [])
        self.assertEqual("time-budget-exhausted", audit["stopReason"])
        self.assertFalse(audit["steps"])

    def test_reader_blocks_internal_hosts_and_redirects_and_pins_public_address(self):
        for url in ("http://example.org/", "https://user:secret@example.org/", "https://localhost/", "https://example.org:22/", "file:///etc/passwd"):
            with self.assertRaises(ValueError):
                public_url(url)
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaisesRegex(ValueError, "non-public"):
                public_addresses("example.org")
        response = Mock(status=302); response.getheader.return_value = "https://127.0.0.1/private"
        connection = Mock(); connection.getresponse.return_value = response
        module = "digital_twin.modules.news_intelligence.infrastructure.public_document_http"
        with patch(module + "._PinnedHTTPS", return_value=connection) as factory, patch(module + ".public_addresses", side_effect=[["1.1.1.1"], ValueError("non-public-document-address")]):
            with self.assertRaisesRegex(ValueError, "non-public"):
                fetch_public_document(URL)
            self.assertEqual(1, factory.call_count)
            connection.close.assert_called_once()
        pinned = _PinnedHTTPS("example.org", "1.1.1.1", 3)
        pinned._context = Mock()
        with patch("socket.create_connection") as connect:
            pinned.connect()
            connect.assert_called_once_with(("1.1.1.1", 443), 3)
            self.assertEqual("example.org", pinned._context.wrap_socket.call_args.kwargs["server_hostname"])

    def test_search_mode_is_local_and_central_command_model_is_preserved(self):
        base = ["codex", "--model", "configured-model", "exec", "--ephemeral", "-"]
        self.assertIn('web_search="live"', source_command(base, True))
        self.assertIn("--json", source_command(base, True))
        self.assertIn('features.shell_tool=false', source_command(base, True))
        self.assertEqual("configured-model", source_command(base, True)[2])
        self.assertNotIn("--json", base)

    def test_pdf_worker_extracts_complete_page_and_reports_empty_scans(self):
        try:
            from pypdf import PdfWriter
            from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        except ImportError:
            self.skipTest("optional PDF text dependency is not installed")
        writer = PdfWriter(); page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject(); stream.set_data(b'BT /F1 10 Tf 40 750 Td (NVIDIA revenue - USD millions - current / prior year) Tj ET')
        page[NameObject('/Contents')] = stream
        buffer = io.BytesIO(); writer.write(buffer)
        result = pdf_pages(buffer.getvalue(), 10)
        self.assertEqual(1, result["pageCount"])
        self.assertIn("USD millions", result["pages"][0]["text"])
        empty = PdfWriter(); empty.add_blank_page(width=600, height=800)
        buffer = io.BytesIO(); empty.write(buffer)
        with self.assertRaisesRegex(ValueError, "ocr-required"):
            pdf_pages(buffer.getvalue(), 10)


if __name__ == "__main__":
    unittest.main()
