"""Continuity, targeted collection and source-backed macro context across owners."""
import copy
import hashlib
import unittest
from unittest.mock import Mock

from digital_twin.modules.ai_orchestration.application.research import execute_research
from digital_twin.modules.ai_orchestration.application.retrieval import retrieve_evidence
from digital_twin.modules.ai_orchestration.domain.continuity import continuity_memory
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_repair_input, validate_execution_input,
    DIRECTED_PROMPT_VERSION, DIRECTED_REPAIR_PROMPT_VERSION,
)
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
from digital_twin.modules.ai_orchestration.domain.research_request import validate_research_request
from digital_twin.modules.reasoning.contracts import EvidenceContractError, evidence_change_identity
from digital_twin.modules.reasoning.public import ObservationEvidenceReader
from test_ai_directed_retrieval import session, read, request, FINISH
from test_ai_control import SUBJECT, PLAN


INTENT = {"queryTerms": ["export restrictions product scope"], "sourceTypes": ["news"], "maxAgeMinutes": 4320}


class AIContinuityResearchTests(unittest.TestCase):
    def assert_historical_filings_require_explicit_window_without_extending_news_freshness(self):
        from digital_twin.modules.ai_orchestration.domain.research_request import executable_research_request
        filing = {'queryTerms': ['2026 quarterly net loss'], 'sourceTypes': ['official-filing'], 'maxAgeMinutes': 259200}
        requested = validate_research_request(filing, True)
        self.assertEqual('observation-research-request-v2', requested['version'])
        self.assertEqual(requested, executable_research_request(requested))
        for invalid in (dict(filing, sourceTypes=['news']), dict(filing, sourceTypes=['news', 'official-filing']),
                        dict(filing, maxAgeMinutes=527041)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_research_request(invalid, True)
        old = {**INTENT, 'version': 'observation-research-request-v1'}
        self.assertEqual(old, executable_research_request(old))
        with self.assertRaises(ValueError):
            executable_research_request({**requested, 'version': 'observation-research-request-v1'})

    def assert_official_questions_select_different_complete_passages_after_document_prefix(self):
        from digital_twin.infrastructure.investment_research_gateway import ExistingApiResearchGateway
        from digital_twin.modules.news_intelligence.contracts import NewsCollectionTarget, select_question_filings
        from digital_twin.modules.news_intelligence.contracts import select_question_passages
        accession = '0001834584-26-000123'
        url = 'https://www.sec.gov/Archives/edgar/data/1834584/000183458426000123/report.htm'
        filing = {'form': '10-Q', 'accessionNumber': accession, 'filingDate': '2026-08-07',
                  'reportDate': '2026-06-30', 'primaryDocument': 'report.htm', 'url': url}
        loss = 'Net loss increased primarily due to a one-time legal provision in the current quarter. This provision does not describe a recurring operating expense or a change in cash receipts.'
        demand = 'Consumer demand and spending remained uneven across the domestic market. Active customers increased, while management cautioned that these observations do not establish a forecast.'
        raw = '<p>' + ('An unrelated document cover page. ' * 250) + '</p><p>' + loss + '</p><p>' + demand + '</p>'
        provider = Mock()
        provider.signals_for_positions.return_value = {'secFilings': {'CPNG': {'cik': '0001834584',
            'latestFiling': dict(filing, form='4'), 'reportFilings': [filing]}}}
        provider.sec_document_access_configured.return_value = True
        provider.sec_document_text_max_chars.return_value = 6000
        provider.guarded_call.side_effect = lambda _provider, _key, work: work()
        provider.fetch_text.return_value = raw
        target = NewsCollectionTarget(symbol='CPNG', name='Coupang', market='US', currency='USD')
        gathered = []
        for terms, expected, absent in ((['순손실 원인'], loss, demand), (['소비 수요'], demand, loss)):
            items, statuses = ExistingApiResearchGateway(provider=provider).collect_for_target(target,
                source_types=['official-filing'], research_tasks=[{'taskId': 'question-1', 'question': '', 'queryTerms': terms}])
            selected = [item for item in items if ':sec-question:' in item.evidence_id]
            self.assertEqual(1, len(selected))
            item = selected[0]
            self.assertEqual(url, item.url)
            self.assertEqual('2026-08-07', item.published_at)
            self.assertIn(expected, item.raw_payload['officialDocumentText'])
            self.assertNotIn(absent, item.raw_payload['officialDocumentText'])
            self.assertEqual([expected], [p['quote'] for p in item.raw_payload['documentPassages']])
            self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(), item.raw_payload['sourceDocumentHash'])
            self.assertEqual(['question-1'], item.raw_payload['researchTaskIds'])
            self.assertEqual('question-selected-passages', item.raw_payload['officialDocumentScope'])
            self.assertTrue(any(s.get('status') == 'passages-collected' for s in statuses))
            from digital_twin.modules.news_intelligence.domain.investment_evidence_governance import verification_for_evidence
            from datetime import datetime, timezone
            cutoff = datetime(2026, 10, 5, tzinfo=timezone.utc)
            item.observed_at = cutoff.isoformat()
            fresh_claim, fresh_ok = verification_for_evidence(item, target, 10080, now=cutoff)
            historical_claim, historical_ok = verification_for_evidence(item, target, 259200, now=cutoff)
            self.assertFalse(fresh_ok)
            self.assertIn('evidence-stale', fresh_claim.reasons)
            self.assertTrue(historical_ok, historical_claim.reasons)
            self.assertEqual('2026-08-07', historical_claim.published_at)
            gathered.append(item.evidence_id)
        self.assertNotEqual(*gathered)
        paragraphs = [loss + str(i) + 'x' * 1100 for i in range(8)]
        bounded = select_question_passages(paragraphs, [{'queryTerms': ['net loss']}], limit=2600)
        self.assertLessEqual(len('\n\n'.join(p['quote'] for p in bounded)), 2600)
        self.assertTrue(all(p['quote'] in paragraphs for p in bounded))
        self.assertEqual([filing], select_question_filings([None, dict(filing, form='4'),
            dict(filing, accessionNumber='future', filingDate='2099-01-01'),
            dict(filing, accessionNumber='invalid-date', filingDate='2026-99-99'), filing], [], '2026-10-05'))

    def assert_official_exhibits_preserve_identity_and_provider_deferrals(self):
        from digital_twin.infrastructure.official_question_research import collect_question_documents, official_document_url
        from digital_twin.modules.news_intelligence.contracts import NewsCollectionTarget
        from digital_twin.modules.market_data.contracts import ExternalCallDeferred
        root = 'https://www.sec.gov/Archives/edgar/data/1834584/000183458426000123/'
        filing = {'form': '8-K', 'accessionNumber': '0001834584-26-000123', 'filingDate': '2026-09-01',
                  'primaryDocument': 'filing.htm', 'url': root + 'filing.htm'}
        target = NewsCollectionTarget(symbol='CPNG', name='Coupang', market='US', currency='USD')
        signals = {'secFilings': {'CPNG': {'cik': '1834584', 'latestFiling': filing}}}
        tasks = [{'taskId': 'q', 'question': 'Net loss', 'queryTerms': []}]
        paragraph = 'Net loss includes a provision recognized during the quarter, which is described in the notes to the financial statements. The provision has not been presented as a change in consumer demand.'
        html = '<a href="https://untrusted.example/ex99.htm">Exhibit 99</a><a href="../other/ex99.htm">Exhibit 99</a><a href="ex99.htm">Exhibit 99.1</a>'
        provider = Mock()
        provider.sec_document_access_configured.return_value = True
        provider.sec_document_text_max_chars.return_value = 6000
        provider.guarded_call.side_effect = lambda _provider, _key, work: work()
        provider.fetch_text.side_effect = lambda url, _headers: html if url.endswith('filing.htm') else '<p>' + paragraph + '</p>'
        items, statuses = collect_question_documents(provider, target, signals, tasks)
        self.assertEqual([root + 'filing.htm', root + 'ex99.htm'], [call.args[0] for call in provider.fetch_text.call_args_list])
        self.assertEqual(root + 'ex99.htm', items[0].url)
        self.assertEqual(root + 'filing.htm', items[0].raw_payload['parentDocumentUrl'])
        self.assertEqual(paragraph, items[0].raw_payload['documentPassages'][0]['quote'])
        for url in ('http://www.sec.gov/Archives/edgar/data/1834584/000183458426000123/ex99.htm',
                    root + '../ex99.htm', root + 'ex99.htm?token=x', 'https://www.sec.gov@untrusted.example/ex99.htm', 'https://[bad'):
            self.assertFalse(official_document_url(url, '1834584', filing['accessionNumber']))
        provider.guarded_call.side_effect = ExternalCallDeferred('circuit', '2026-10-05T03:00:00Z', 'circuit-open')
        items, statuses = collect_question_documents(provider, target, signals, tasks)
        self.assertEqual([], items)
        self.assertEqual('2026-10-05T03:00:00Z', next(s for s in statuses if s['status'] == 'deferred')['retryAt'])
        self.assertEqual('unresolved', statuses[-1]['status'])
        provider.guarded_call.reset_mock()
        self.assertEqual(([], []), collect_question_documents(provider, target, signals, []))
        provider.guarded_call.assert_not_called()

    def test_no_recall_still_delivers_prior_judgment_open_work_and_feedback(self):
        captured, _ = session()
        past = {"summary": "이전 판단의 핵심", "hypothesis": "이전의 미확인 설명", "observedAt": "2026-01-01T00:00:00Z",
            "quality": {"status": "observation-only"}, "previousFacts": [{"id": "old-quote", "currentPrice": 90, "currency": "KRW"}]}
        memories = [{"kind": "brain-case", "caseId": "open", "question": "진행 중인 질문", "reviewDue": False},
            {"kind": "service-feedback", "caseId": "feedback", "status": "proposed", "problem": "원인 근거 부족"},
            {"runId": "research", "status": "completed", "verifiedClaimCount": 2, "verifiedClaims": [{"statement": "not current evidence"}]}]
        inputs = []
        def save(value):
            inputs.append(copy.deepcopy(value)); return "i" + str(len(inputs))
        packet, history, research, trace = retrieve_evidence(captured, captured.packet(), [past], memories,
            Mock(side_effect=[read(request("flow")), FINISH]), save, 256 * 1024)
        self.assertEqual("이전 판단의 핵심", inputs[0]["retrievalContext"]["continuity"]["previousAnalyses"][0]["summary"])
        self.assertEqual(past["previousFacts"], history[0]["previousFacts"])
        self.assertEqual(3, len(research))
        self.assertNotIn("verifiedClaims", research[2])
        frozen = freeze_execution_input(packet, history, research, retrieval_trace=trace)
        validate_execution_input(frozen)
        self.assertEqual(1, frozen["memoryCoverage"]["requiredAnalyses"])
        self.assertEqual(3, frozen["memoryCoverage"]["requiredResearch"])
        self.assertEqual("historical-context-only", frozen["previousAnalyses"][0]["authority"])

    def test_required_memory_survives_repair_and_oversize_is_explicit(self):
        captured, _ = session()
        historical = {"summary": "판단 요약" * 1000, "quality": {"status": "observation-only"}}
        required = continuity_memory([historical], [])
        self.assertEqual(["summary"], required["previousAnalyses"][0]["truncatedFields"])
        frozen = freeze_execution_input(captured.packet(), required["previousAnalyses"], [], 65536)
        repaired = freeze_repair_input(frozen, {"draft": "x" * 20000}, ["검토"], "parent")
        self.assertEqual(frozen["previousAnalyses"], repaired["previousAnalyses"])
        validate_execution_input(repaired)
        from digital_twin.modules.ai_orchestration.domain.continuity import judgment_continuity
        from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_review_input, CITABLE_REVIEW_PROMPT_VERSION
        from digital_twin.modules.ai_orchestration.domain.observation_clock import citable_review_prompt
        review = freeze_review_input({"input": frozen["current"], "summary": "점검", "judgmentContinuity": judgment_continuity(frozen)})
        self.assertEqual(frozen["previousAnalyses"], review["draft"]["judgmentContinuity"]["previousAnalyses"])
        validate_execution_input(review)
        old = copy.deepcopy(review)
        old["promptVersion"] = CITABLE_REVIEW_PROMPT_VERSION
        old["prompt"] = citable_review_prompt(old["current"], old["draft"])
        old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
        validate_execution_input(old)
        with self.assertRaises(EvidenceContractError):
            continuity_memory([{**historical, "followUpConditions": [{"body": "x" * 30000}]}], [])

    def test_question_contract_rejects_unsupported_and_private_searches(self):
        self.assert_historical_filings_require_explicit_window_without_extending_news_freshness()
        captured, _ = session()
        from digital_twin.modules.ai_orchestration.domain.research_request import continuous_planning_schema
        schema = continuous_planning_schema(captured.packet(), [])
        for name, maximum in (("caseReviews", 5), ("serviceFeedback", 1)):
            self.assertEqual(maximum, schema["properties"][name]["maxItems"])
            evidence_schema = schema["properties"][name]["items"]["properties"]["evidenceIds"]
            self.assertEqual((1, 8), (evidence_schema["minItems"], evidence_schema["maxItems"]))
        value = {**PLAN, "evidenceIds": [captured.packet()["facts"][0]["id"]],
            "questions": [{"question": "이번 수출 규제가 해당 제품에도 적용되는가?", "capability": "research", "research": INTENT}]}
        result = validate_plan(value, captured.packet(), require_research=True)
        self.assertEqual(INTENT["queryTerms"], result["workQuestions"][0]["researchRequest"]["queryTerms"])
        for invalid in ({**INTENT, "sourceTypes": ["execute-code"]}, {**INTENT, "queryTerms": ["https://unknown.example"]},
                        {**INTENT, "queryTerms": [SUBJECT["accountId"]]}, {**INTENT, "maxAgeMinutes": True}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_research_request(invalid, True, SUBJECT["accountId"])
        with self.assertRaises(ValueError):
            validate_research_request(INTENT, False)
        del value["questions"][0]["research"]
        with self.assertRaises(ValueError):
            validate_plan(value, captured.packet(), require_research=True)

    def test_question_terms_reach_real_research_orchestrator_and_news_query(self):
        self.assert_official_questions_select_different_complete_passages_after_document_prefix()
        self.assert_official_exhibits_preserve_identity_and_provider_deferrals()
        from digital_twin.modules.news_intelligence.application.investment_research_orchestration_service import InvestmentResearchOrchestrationService
        from digital_twin.infrastructure.news_sources import NewsSourceGateway
        queries = []
        for index, terms in enumerate((["export restrictions product scope"], ["quarterly gross margin guidance"])):
            store, evidence, gateway = Mock(), Mock(), Mock()
            store.get_run.return_value = None
            evidence.latest.return_value = []
            gateway.collect_for_target.return_value = ([], [])
            orchestrator = InvestmentResearchOrchestrationService(evidence, gateway,
                settings={"investmentBrainResearchMaxRounds": 1, "investmentBrainResearchCooldownMinutes": 0})
            job = {**SUBJECT, "taskId": "research-" + str(index), "question": "공식 자료에서 질문에 답할 근거를 확인할 수 있는가?",
                   "researchRequest": validate_research_request({**INTENT, "queryTerms": terms}, True)}
            result = execute_research(job, store, lambda: orchestrator)
            self.assertEqual("question-specific", result["searchScope"])
            gateway.collect_for_target.assert_called_once()
            target = gateway.collect_for_target.call_args.args[0]
            self.assertEqual(terms, target.research_query_terms)
            task = gateway.collect_for_target.call_args.kwargs["research_tasks"][0]
            self.assertEqual(["news-full-text"], task["requiredEvidenceTypes"])
            self.assertEqual(4320, task["maxAgeMinutes"])
            query = NewsSourceGateway.search_query_for_target(object.__new__(NewsSourceGateway), target)
            self.assertIn(terms[0], query)
            self.assertNotIn(SUBJECT["accountId"], query)
            from urllib.parse import urlparse, parse_qs
            transport = Mock(return_value="<rss><channel/></rss>")
            NewsSourceGateway(fetch_text=transport).fetch_google_news_rss(target, "US")
            sent = parse_qs(urlparse(transport.call_args.args[0]).query)["q"][0]
            self.assertEqual(query, sent)
            queries.append(query)
        self.assertNotEqual(*queries)

    def test_completed_research_is_reused_without_another_source_call(self):
        store, factory = Mock(), Mock()
        store.get_run.return_value = {"status": "verified-no-change"}
        result = execute_research({**SUBJECT, "taskId": "already-done", "question": "공식 자료를 추가 확인하는가?",
            "researchRequest": validate_research_request(INTENT, True)}, store, factory)
        self.assertTrue(result["reused"])
        self.assertEqual("unavailable", result["questionAssessment"]["status"])
        self.assertNotIn("changedEvidenceCount", result)
        factory.assert_not_called()
        self.assert_research_retry_preserves_question_assessment_without_promoting_claims()

    def assert_research_retry_preserves_question_assessment_without_promoting_claims(self):
        from digital_twin.modules.ai_orchestration.domain.planning import identity
        from digital_twin.modules.ai_orchestration.domain.research_return import research_return
        job = {**SUBJECT, "taskId": "question-return", "question": "공식 분기보고서 원문에 근거가 있는가?",
               "researchRequest": validate_research_request(INTENT, True)}
        run_id = "ai-control-" + job["taskId"]
        assessment = {"taskId": identity(job["taskId"], "sources"), "status": "needs-review",
            "coverageState": "complete", "semanticReviewState": "unreviewed", "assessmentFingerprint": "frozen-assessment",
            "assessedAt": "2026-10-10T00:00:00Z", "missingRequirements": [], "candidateEvidenceIds": ["doc-1"],
            "resultEvidenceIds": [], "counterEvidenceIds": [], "reason": "", "excludedEvidence": [{"raw": "not-memory"}]}
        payload = {"runId": run_id, "status": "evidence-collected", "changedEvidenceCount": 12,
            "stopReason": "no-new-research-path", "completedAt": "2026-10-10T00:00:01Z", "taskAssessments": [assessment],
            "verifiedClaims": [{"statement": "must not enter current facts"}]}
        store, orchestrator, run = Mock(), Mock(), Mock()
        store.get_run.return_value = None
        run.to_dict.return_value = payload
        run.run_id = run_id
        orchestrator.run.return_value = run
        fresh = execute_research(job, store, lambda: orchestrator)
        store.get_run.return_value = payload
        factory = Mock()
        reused = execute_research(job, store, factory)
        self.assertEqual({**fresh, "reused": True}, reused)
        factory.assert_not_called()
        self.assertEqual("needs-review", fresh["questionAssessment"]["status"])
        self.assertEqual(1, fresh["questionAssessment"]["candidateEvidenceCount"])
        self.assertNotIn("verifiedClaims", fresh)
        self.assertNotIn("candidateEvidenceIds", fresh["questionAssessment"])
        self.assertNotIn("excludedEvidence", fresh["questionAssessment"])
        oversized = {**assessment, "missingRequirements": ["x" * 170] * 20, "reason": "y" * 700}
        compact = research_return({**payload, "taskAssessments": [oversized]}, run_id, assessment["taskId"], {})["questionAssessment"]
        self.assertEqual(20, compact["missingRequirementCount"])
        self.assertEqual(8, len(compact["missingRequirements"]))
        self.assertEqual({"reason", "missingRequirements"}, set(compact["truncatedFields"]))
        for rows in ([{**assessment, "taskId": "other-question"}], [assessment, assessment], []):
            with self.subTest(rows=len(rows)):
                returned = research_return({**payload, "taskAssessments": rows}, run_id, assessment["taskId"], {})
                self.assertEqual("unavailable", returned["questionAssessment"]["status"])

    def test_macro_print_reaches_first_and_final_input_with_original_units(self):
        from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
        from digital_twin.modules.reasoning.domain.ontology_external_abox import add_portfolio_macro_and_cross_asset_concepts
        from digital_twin.modules.ai_orchestration.domain.evidence_wake import evidence_wake_targets
        from digital_twin.modules.reasoning.contracts import completed_observation_evidence_event
        from test_ai_evidence_wake import completed_result
        graph = PortfolioOntology("test")
        macro = {"series": {"KR_ALL_INDUSTRY_PRODUCTION": {"provider": "KOSIS", "value": 118.7, "unit": "2020=100",
            "observationDate": "2026-08", "sourceAsOf": "2026-08", "date": "2026-08", "sourceUrl": "https://kosis.kr/openapi/"}}}
        add_portfolio_macro_and_cross_asset_concepts(graph, "portfolio:test", {"macro": macro})
        node = next(row for row in graph.entities if row.kind == "macro-print")
        self.assertEqual("MacroPrint", node.properties["tboxClass"])
        source = Mock()
        source.metadata.return_value = {"status": "ok", "aboxSnapshotId": "source-1", "accountId": SUBJECT["accountId"]}
        source.snapshot_id.return_value = "source-1"
        source.candidates.return_value = [{"id": "quote", "kind": "stock", "symbol": "TEST", "currentPrice": 100},
            {**node.properties, "id": node.entity_id, "kind": node.kind}]
        captured = ObservationEvidenceReader(source).capture_session(SUBJECT)
        seen = []
        packet, history, memory, trace = retrieve_evidence(captured, captured.packet(), [], [], Mock(return_value=FINISH),
            lambda envelope: seen.append(envelope) or "route-1", 256 * 1024)
        self.assertEqual("ready", packet["retrieval"]["status"])
        initial = next(row for row in seen[0]["current"]["facts"] if row["kind"] == "macro-print")
        self.assertEqual((118.7, "2020=100", "2026-08"), (initial["value"], initial["unit"], initial["observationDate"]))
        before = freeze_execution_input(packet, history, memory, retrieval_trace=trace)
        from digital_twin.modules.reasoning.domain.ontology_scopes import apply_scoped_abox_identity
        from digital_twin.modules.reasoning.domain.ontology_change_impact import family_for_entity
        from digital_twin.modules.reasoning.domain.market_world_projection import is_market_entity
        apply_scoped_abox_identity(graph)
        self.assertEqual("macro:market", node.properties["aboxScopeId"])
        self.assertEqual("macro-market", family_for_entity(node.kind, node.properties, node.entity_id))
        self.assertTrue(is_market_entity(node))
        from digital_twin.modules.reasoning.domain.projection_facts import graph_for_graph_store_persistence
        persisted = graph_for_graph_store_persistence(graph, {"rules": [{"source_kind": "stock",
            "conditions": [{"kind": "relation", "relation_type": "HAS_RATE_SENSITIVITY"}]}]})
        self.assertIn(node.entity_id, {row.entity_id for row in persisted.entities})
        self.assertEqual([], persisted.relations, "macro context must not manufacture subject sensitivity")
        from digital_twin.modules.reasoning.infrastructure.observation_evidence import TypeDBObservationEvidenceSource
        from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
        import json
        native = Mock()
        native.active_abox_metadata.return_value = {"status": "ok", "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
            "scopeGenerationIds": {"macro:market": "active-macro", "symbol:OTHER:state": "other"}}
        native.read_rows.return_value = [{"id": node.entity_id, "kind": node.kind, "label": "monthly",
            "json": json.dumps(node.properties)}]
        public = TypeDBObservationEvidenceSource(native).monthly_macro_candidates("market:shared:global")
        self.assertEqual(118.7, public[0]["value"])
        self.assertIn('has ontology-snapshot-id "active-macro"', native.read_rows.call_args.args[0])
        self.assertIn('has ontology-world-id "market:shared:global"', native.read_rows.call_args.args[0])
        native.active_abox_members_clause.assert_not_called()
        # Monthly prints live in the public market world, independently of
        # the stock's account overlay and any matched rule.
        shared_source = Mock()
        shared_source.metadata.side_effect = lambda world: {"status": "ok", "aboxSnapshotId": world + ":snapshot",
            "accountId": SUBJECT["accountId"] if world == SUBJECT["worldId"] else "projection-source-account"}
        shared_source.snapshot_id.side_effect = lambda world: world + ":snapshot"
        shared_source.candidates.side_effect = lambda world, symbol: source.candidates.return_value if world == SUBJECT["worldId"] else [source.candidates.return_value[1]]
        reader = ObservationEvidenceReader(shared_source, macro_world_id="market:shared:global")
        shared_packet = reader.capture_session(SUBJECT).select([])
        self.assertEqual(1, shared_packet["coverage"]["macro"]["available"])
        shared_print = next(row for row in shared_packet["facts"] if row["kind"] == "macro-print")
        self.assertEqual("market:shared:global", shared_print["sourceWorldId"])
        self.assertEqual("market:shared:global:snapshot", shared_print["sourceSnapshotId"])
        shared_source.candidates.assert_any_call("market:shared:global", "")
        source.candidates.return_value[1]["accountId"] = SUBJECT["accountId"]
        with self.assertRaises(EvidenceContractError):
            reader.capture_session(SUBJECT)
        del source.candidates.return_value[1]["accountId"]
        shared_source.snapshot_id.side_effect = ["source", "market-before", "source", "market-after"]
        shared_source.metadata.side_effect = lambda world: {"status": "ok", "aboxSnapshotId": "source", "accountId": ""}
        with self.assertRaises(EvidenceContractError):
            reader.capture_session(SUBJECT)
        source.candidates.return_value[1]["value"] = 119.0
        updated = ObservationEvidenceReader(source).capture_session(SUBJECT).packet()
        self.assertNotEqual(evidence_change_identity(captured.packet(), []), evidence_change_identity(updated, []))
        self.assertEqual(118.7, next(row for row in before["current"]["facts"] if row["kind"] == "macro-print")["value"])
        event = completed_observation_evidence_event({"job_id": "macro-change", "deployment_id": "live", "source_event_id": "macro-revision"}, completed_result())
        self.assertEqual(1, len(evidence_wake_targets(event.payload, [SUBJECT])))
        validate_execution_input(before)

    def test_v10_author_and_v6_repair_replay_with_legacy_profile(self):
        from digital_twin.modules.ai_orchestration.domain.retrieval import directed_planning_prompt
        from digital_twin.modules.ai_orchestration.domain.observation_clock import citable_management_schema
        from digital_twin.modules.ai_orchestration.domain.insight_repair import repair_prompt
        captured, _ = session()
        frozen = freeze_execution_input(captured.packet(), [], [])
        for version in (DIRECTED_PROMPT_VERSION, DIRECTED_REPAIR_PROMPT_VERSION):
            old = copy.deepcopy(frozen)
            old["current"]["profile"] = "independent-observation-v1"
            old["promptVersion"] = version
            old["prompt"] = directed_planning_prompt(old["current"], [], [])
            if version == DIRECTED_REPAIR_PROMPT_VERSION:
                old["repair"] = {"errors": [], "rejectedDraft": PLAN, "parentInputId": "parent", "comparisons": []}
                old["prompt"] = repair_prompt(old["prompt"], old["repair"])
            old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
            old["outputSchema"] = citable_management_schema(old["current"], [])
            validate_execution_input(old)
        from digital_twin.modules.ai_orchestration.domain.execution_input import (
            CONTINUITY_PROMPT_VERSION, CONTINUITY_REPAIR_PROMPT_VERSION,
            MANAGEMENT_BOUNDS_PROMPT_VERSION, MANAGEMENT_BOUNDS_REPAIR_PROMPT_VERSION,
            FILING_PROMPT_VERSION, FILING_REPAIR_PROMPT_VERSION, CONTINUITY_REVIEW_PROMPT_VERSION,
            freeze_review_input,
        )
        from digital_twin.modules.ai_orchestration.domain.continuity import continuous_planning_prompt
        from digital_twin.modules.ai_orchestration.domain.research_request import continuous_planning_schema
        repaired = freeze_repair_input(frozen, PLAN, ["verify"], "parent")
        for original, version in ((frozen, CONTINUITY_PROMPT_VERSION), (repaired, CONTINUITY_REPAIR_PROMPT_VERSION),
                                  (frozen, MANAGEMENT_BOUNDS_PROMPT_VERSION), (repaired, MANAGEMENT_BOUNDS_REPAIR_PROMPT_VERSION),
                                  (frozen, FILING_PROMPT_VERSION), (repaired, FILING_REPAIR_PROMPT_VERSION)):
            old = copy.deepcopy(original)
            old["promptVersion"] = version
            bounded = version not in {CONTINUITY_PROMPT_VERSION, CONTINUITY_REPAIR_PROMPT_VERSION}
            legacy_research = version not in {FILING_PROMPT_VERSION, FILING_REPAIR_PROMPT_VERSION}
            old["prompt"] = continuous_planning_prompt(old['current'], old['previousAnalyses'], old['researchResults'], legacy_research=legacy_research)
            if 'repair' in old:
                from digital_twin.modules.ai_orchestration.domain.insight_repair import repair_prompt
                old['prompt'] = repair_prompt(old['prompt'], old['repair'])
            old['promptHash'] = hashlib.sha256(old['prompt'].encode()).hexdigest()
            old["outputSchema"] = continuous_planning_schema(old["current"], old["researchResults"], bounded_evidence=bounded, legacy_research=legacy_research)
            validate_execution_input(old)
            if legacy_research:
                self.assertEqual(10080, old['outputSchema']['properties']['questions']['items']['properties']['research']['properties']['maxAgeMinutes']['maximum'])
            if not bounded:
                self.assertNotIn("minItems", old["outputSchema"]["properties"]["caseReviews"]["items"]["properties"]["evidenceIds"])
        from digital_twin.modules.ai_orchestration.domain.continuity import continuous_review_prompt
        current_review = freeze_review_input({'input': frozen['current'], 'summary': '이전 관찰 문장입니다.'})
        old_review = copy.deepcopy(current_review)
        old_review['promptVersion'] = CONTINUITY_REVIEW_PROMPT_VERSION
        old_review['prompt'] = continuous_review_prompt(old_review['current'], old_review['draft'])
        old_review['promptHash'] = hashlib.sha256(old_review['prompt'].encode()).hexdigest()
        validate_execution_input(old_review)
        for current in (frozen, repaired, current_review):
            validate_execution_input(current)
            self.assertIn('독자가 바로 이해할 수 있는 관찰 문장', current['prompt'])
        self.assertNotIn('독자가 바로 이해할 수 있는 관찰 문장', old_review['prompt'])
