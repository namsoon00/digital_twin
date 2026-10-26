"""Original receipts, focused recovery and bounded partial business conclusions."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from digital_twin.modules.news_intelligence.domain.dart_filing_history import build_history, verified_history
from digital_twin.modules.news_intelligence.domain.filing_history import history_material
from digital_twin.modules.ai_orchestration.domain import partial_judgment, question_answer, research_review


def listing(page=1, total_pages=1):
    return {"status": "000", "page_no": page, "total_page": total_pages, "total_count": 3,
        "list": [{"corp_code": "00164779", "rcept_no": receipt, "rcept_dt": receipt[:8], "report_nm": title}
                 for receipt, title in [("20260820000123", "[기재정정]반기보고서 (2026.06)"),
                     ("20260814000123", "반기보고서 (2026.06)"), ("20250814000123", "반기보고서 (2025.06)")]]}


class EvidenceToJudgmentTests(unittest.TestCase):
    def test_dart_correction_history_survives_original_collection_graph_and_question_packet(self):
        from test_bounded_evidence_readiness import DartQuestionOriginalTests
        from digital_twin.infrastructure.official_question_research import collect_question_documents
        from digital_twin.modules.news_intelligence.domain.investment_evidence_governance import governed_evidence
        from digital_twin.modules.news_intelligence.domain.research_progress import evidence_packets
        from digital_twin.modules.news_intelligence.domain.research_document_reference import research_document_reference
        provider, target, signals, tasks = DartQuestionOriginalTests().fixture()
        tasks[0]['question'] += ' 정정 및 제출 이력도 확인합니다.'
        provider.fetch_json.return_value = listing()
        items, statuses = collect_question_documents(provider, target, signals, tasks)
        self.assertEqual(1, provider.fetch_json.call_count, 'cached originals do not prove correction history')
        self.assertIn('last_reprt_at=N', provider.fetch_json.call_args.args[0])
        self.assertEqual(2, provider.fetch_bytes.call_count)
        # Include the uncorrected same-period original: only that receipt may
        # be retired, while the prior-year comparison must remain available.
        old_provider, _, old_signals, old_tasks = DartQuestionOriginalTests().fixture()
        originals, _ = collect_question_documents(old_provider, target, old_signals, old_tasks)
        items += [originals[0]]
        accepted, claims, _ = governed_evidence(items, target, 999999,
            now=datetime.now(timezone.utc)+timedelta(seconds=1))
        self.assertEqual(2, len(accepted))
        self.assertFalse(originals[0].raw_payload['evidenceGovernance']['investmentJudgmentEligible'])
        self.assertIn('superseded-by-correction', originals[0].raw_payload['evidenceGovernance']['reasons'])
        self.assertIn('20260820000123', items[0].url)
        for item, packet in zip(accepted, evidence_packets(accepted, claims)):
            history = item.raw_payload['filingHistory']
            self.assertTrue(history['complete']); self.assertTrue(history['rows'][0]['amendment'])
            self.assertEqual(3, len(history['rows']))
            self.assertEqual(history, research_document_reference(item)['filingHistory'])
            self.assertEqual(history, packet['filingHistory'])
            self.assertNotIn('private-test-key', repr((item.raw_payload, statuses)))
            from digital_twin.modules.ai_orchestration.domain.research_evidence_use import usable_fact
            fact = {'symbol': item.symbol, 'url': item.url, 'historicalDocumentReference': research_document_reference(item)}
            self.assertTrue(usable_fact(fact, 'historicalDocumentReference.filingHistory.summary'))
            fact['historicalDocumentReference']['filingHistory']['summary'] += ' changed'
            self.assertFalse(usable_fact(fact, 'historicalDocumentReference.filingHistory.summary'))
        from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
        from digital_twin.modules.reasoning.domain.portfolio_ontology_research_concepts import add_research_evidence_concepts
        from digital_twin.modules.reasoning.domain.observation_document_view import document_view
        from digital_twin.modules.ai_orchestration.domain.insight_schema import evidence_references
        graph = PortfolioOntology('history-proof')
        add_research_evidence_concepts(graph, 'stock', 'thesis', 'opinion', target.symbol, {},
                                      {'researchEvidence': {target.symbol: [row.to_dict() for row in accepted]}})
        documents = [document_view({**node.properties, 'id': node.entity_id, 'kind': node.kind})
                     for node in graph.entities if node.kind == 'research-evidence']
        self.assertEqual(2, len(documents))
        for fact in documents:
            self.assertTrue(usable_fact(fact, 'historicalDocumentReference.filingHistory.summary'))
        self.assertIn('historicalDocumentReference.filingHistory.summary',
                      repr(evidence_references({'facts': documents}, usable=True)))

    def test_history_bounds_identity_and_poll_clock_cannot_claim_new_evidence(self):
        now = '2026-10-26T08:00:00Z'
        value = build_history([listing()], '000660', '00164779', '2025-01-01', now)
        later = build_history([listing()], '000660', '00164779', '2025-01-01', '2026-10-26T09:00:00Z')
        self.assertEqual(history_material(value), history_material(later))
        self.assertNotEqual(value['receiptHash'], later['receiptHash'])
        self.assertFalse(verified_history(value, '111111', '00164779'))
        broken = deepcopy(value); broken['rows'][0]['amendment'] = False
        self.assertFalse(verified_history(broken, '000660', '00164779'))
        for mutation in ('issuer', 'future', 'duplicate'):
            payload = listing()
            if mutation == 'issuer': payload['list'][0]['corp_code'] = '00999999'
            if mutation == 'future': payload['list'][0].update(rcept_no='20271026000123', rcept_dt='20271026')
            if mutation == 'duplicate': payload['list'].append(payload['list'][0])
            with self.assertRaises(ValueError): build_history([payload], '000660', '00164779', '2025-01-01', now)
        from digital_twin.infrastructure.dart_question_history import collect_history
        pages = [listing(1, 3), listing(2, 3)]
        pages[0]['list'] = pages[0]['list'][:1]; pages[1]['list'] = pages[1]['list'][1:2]
        provider = Mock(); provider.fetch_json.side_effect = pages
        provider.guarded_call.side_effect = lambda _, __, work: work()
        _, limited, _ = collect_history(provider, '000660', '00164779', 'private-test-key', '20250101', now, [], True)
        self.assertEqual(2, provider.fetch_json.call_count)
        self.assertFalse(limited['complete'])
        self.assertIn('listing-page-budget-or-incomplete', limited['coverageGaps'])
        provider.fetch_json.side_effect = None
        provider.fetch_json.return_value = {'status': '000', 'list': listing()['list']}
        _, unverified, statuses = collect_history(provider, '000660', '00164779', 'private-test-key', '20250101', now, [], True)
        self.assertEqual({}, unverified)
        from digital_twin.modules.news_intelligence.domain.source_recovery import source_recovery_summary
        self.assertIn('metadata-unverified', source_recovery_summary({}, statuses)['causeCounts'])

    def test_long_review_gap_reaches_collection_without_changing_contract_or_repeating_review(self):
        from test_question_research_progress import TARGET, EvidenceStore, Gateway, RunStore, task, evidence
        from test_research_review_reuse import PartialAdvisor
        from digital_twin.modules.decisions.contracts import InvestmentQuestion
        from digital_twin.modules.news_intelligence.application.hypothesis_research_planner_service import HypothesisResearchPlanningService
        from digital_twin.modules.news_intelligence.application.investment_research_orchestration_service import InvestmentResearchOrchestrationService
        advisor = PartialAdvisor(); original_plan = advisor.plan
        gap = '해당 연결 원문의 보고기간과 비교표 및 현금흐름의 구분을 확인합니다. ' * 5 + '마지막 정정 제출일도 확인합니다.'
        self.assertGreater(len(gap), 180)
        def review(context):
            value = original_plan(context)
            for row in value['taskReviews']: row['missingRequirements'] = [gap]
            return value
        advisor.plan = review
        gateway = Gateway(); original = task(); frozen = deepcopy(original)
        service = InvestmentResearchOrchestrationService(EvidenceStore([evidence()]), gateway, research_store=RunStore(),
            hypothesis_research_planner=HypothesisResearchPlanningService(advisor))
        question = InvestmentQuestion.create(original['question'], subject_symbol='AAPL', source='ai-control')
        run = service.run(question, TARGET, {'documentaryReviewOnly': True, 'researchPlan': {'tasks': [original]}})
        self.assertEqual(frozen, original)
        self.assertEqual([gap], gateway.requests[0]['tasks'][0]['sourceFocus']['missingRequirements'])
        self.assertIn(gap, run.task_assessments[0]['missingRequirements'])
        self.assertEqual(1, len(advisor.calls))
        self.assertEqual('collection-only', run.executed_plan['collectionFocus'][0]['tasks'][0]['authority'])
        self.assertNotIn('sourceFocus', run.executed_plan['tasks'][0])

    def test_deferred_exploration_keeps_original_focus_and_does_not_pay_for_same_decision(self):
        from test_source_exploration import TARGET, TASK, URL, tools_for, SourceExplorationService
        from digital_twin.infrastructure.local_ai_process_guard import LocalAICapacityUnavailable
        tools = tools_for(); tools.seeds = Mock(return_value=[{'url': URL}])
        receipt = tools.fetch.return_value
        tools.fetch.side_effect = [LocalAICapacityUnavailable('capacity'), receipt]
        contexts, checkpoints = [], []
        def choose(context, timeout):
            contexts.append(deepcopy(context))
            if len(contexts) == 1:
                return {'action': 'read', 'reason': 'Read the original missing table',
                        'candidateId': context['candidates'][0]['candidateId']}
            return {'action': 'finish', 'reason': 'Keep collected receipts for independent review'}
        assessment = {'taskId': TASK['taskId'], 'semanticReviewState': 'partial',
                      'assessmentFingerprint': 'original-review', 'missingRequirements': ['같은 기간의 연결 재무표 단위']}
        service = SourceExplorationService(SimpleNamespace(choose=choose), tools, deferred_errors=(LocalAICapacityUnavailable,))
        with self.assertRaises(LocalAICapacityUnavailable):
            service.explore(TARGET, [TASK], [], [assessment], checkpoint=checkpoints.append)
        changed = {**assessment, 'missingRequirements': ['다른 새 질문으로 바뀐 범위']}
        _, audit = service.explore(TARGET, [TASK], [], [changed], resume=checkpoints[-1])
        self.assertEqual(2, len(contexts)); self.assertEqual(1, audit['readCount'])
        for context in contexts:
            self.assertEqual(assessment['missingRequirements'], context['tasks'][0]['sourceFocus']['missingRequirements'])
            self.assertEqual(TASK['question'], context['tasks'][0]['question'])

    def partial_fixture(self):
        from test_question_answer_parts import QuestionAnswerPartsTests
        from ai_insight_fixtures import ref
        from digital_twin.modules.ai_orchestration.domain.company_research_cycle import VERSION
        p, memories, trace, case, raw = QuestionAnswerPartsTests().fixture()
        p.update(companyResearchPolicy=VERSION, partialJudgmentPolicy=partial_judgment.VERSION)
        raw['summary'] = '같은 기간의 매출 비교를 원문에서 확인했습니다. 현금 회수의 개선은 아직 확인하지 못했습니다.'
        raw['hypothesis'] = '확인한 매출 비교는 매출 변화에 관한 설명을 뒷받침하지만 현금 회수 개선까지 설명하지 않습니다.'
        raw['evidenceIds'].append('official-document')
        for section in ('summary', 'hypothesis'):
            raw['claimEvidence'][section] = [ref('historicalDocumentReference.text', fact_id='official-document')]
        raw['questionResolutions'][0]['judgmentImpact'] = {'effect': 'supports',
            'reason': '매출 비교에 관한 설명을 뒷받침하지만 현금 회수 설명은 원문 추가 확인 전까지 보류합니다.'}
        return p, memories, trace, case, raw

    def test_verified_partial_claim_reaches_approved_memory_while_original_question_stays_open(self):
        from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
        from digital_twin.modules.ai_orchestration.domain.question_assessment import assessment
        p, memories, _, case, raw = self.partial_fixture()
        result = validate_plan(raw, p, memories)
        result.update(input=p, executionInputId='new-author')
        frozen = research_review.freeze(result, memories, 262144)
        self.assertEqual(research_review.PARTIAL_VERSION, frozen['promptVersion'])
        research_review.validate_input(frozen)
        result['researchQuality'] = research_review.accept(result, {'version': research_review.PARTIAL_VERSION,
            'accepted': True, 'reason': '확인한 매출 사실과 한정된 설명만 연결하며 현금흐름 부족을 보존했습니다.'}, 'independent-review')
        saved = assessment(case, {'taskId': 'assessed'}, result, result['caseReviews'][0],
                           result['questionResolutions'][0], '2026-10-26T08:00:00Z')
        self.assertEqual('partial', saved['status']); self.assertEqual('wait', saved['reviewAction'])
        self.assertEqual('supports', saved['judgmentImpact']['effect'])
        self.assertTrue(saved['missingRequirements'])

    def test_partial_impact_rejects_unconfirmed_baseline_or_metadata_only_citations(self):
        from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
        for mutation in ('no-confirmed', 'baseline', 'metadata-only', 'date-only', 'unrelated-claim', 'close-question'):
            p, memories, _, _, raw = self.partial_fixture()
            if mutation == 'no-confirmed': raw['questionResolutions'][0]['answerParts']['confirmed'] = []
            if mutation == 'baseline': raw['claimEvidence']['hypothesis'][0]['period'] = 'baseline'
            if mutation == 'metadata-only': raw['claimEvidence']['hypothesis'][0]['field'] = 'historicalDocumentReference.documentHash'
            if mutation == 'date-only': raw['claimEvidence']['hypothesis'][0]['field'] = 'historicalDocumentReference.publishedAt'
            if mutation == 'unrelated-claim': raw['claimEvidence']['hypothesis'][0].update(factId='quote-1', field='currentPrice')
            if mutation == 'close-question': raw['caseReviews'][0]['action'] = 'answered'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): validate_plan(raw, p, memories)

    def test_new_author_repair_review_are_frozen_and_old_company_input_stays_replayable(self):
        from test_business_insight_progress import source_packet, retrieve
        from ai_insight_fixtures import plan
        from digital_twin.modules.ai_orchestration.domain import company_research_cycle as old
        from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input
        p = source_packet(); p['companyResearchPolicy'] = old.VERSION
        _, _, (selected, history, memories, trace) = retrieve(p)
        previous = freeze_execution_input(selected, history, memories, retrieval_trace=trace)
        p.update(questionAnswerPolicy=question_answer.VERSION, partialJudgmentPolicy=partial_judgment.VERSION)
        _, _, (selected, history, memories, trace) = retrieve(p)
        current = freeze_execution_input(selected, history, memories, retrieval_trace=trace)
        self.assertEqual(old.PROMPT_VERSION, previous['promptVersion'])
        self.assertEqual(partial_judgment.PROMPT_VERSION, current['promptVersion'])
        repair = freeze_repair_input(current, plan(), ['evidence needs checking'], 'parent')
        review = freeze_review_input({**plan(), 'input': current['current']}, 262144)
        self.assertEqual(partial_judgment.REPAIR_VERSION, repair['promptVersion'])
        self.assertEqual(partial_judgment.REVIEW_VERSION, review['promptVersion'])
        for envelope in (previous, current, repair, review): validate_execution_input(envelope)
        broken = deepcopy(current); broken['current'].pop('partialJudgmentPolicy')
        with self.assertRaises(ValueError): validate_execution_input(broken)
