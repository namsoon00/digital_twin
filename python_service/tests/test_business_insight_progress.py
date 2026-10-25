"""Stale-quarter selection and price-only novelty cannot certify business insight."""
from copy import deepcopy
import unittest
from unittest.mock import Mock

from ai_insight_fixtures import plan, ref, review
from test_evidence_completion import active
from test_scoped_knowledge_research import scoped_decision
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession, EvidenceContractError, source_packet_bytes
from digital_twin.modules.reasoning.domain.observation_evidence import select_evidence
from digital_twin.modules.ai_orchestration.domain import business_insight, business_progress, business_retrieval
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input)
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality, accept_review, quality_block
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.notifications.application.ai_observation_message import render_ai_observation


def source_packet():
    value = active()
    value.update(businessInsightPolicy=business_insight.VERSION, capturedAt='2026-10-24T00:00:00Z')
    quote = value['facts'][0]; quote['sourceAsOf'] = value['capturedAt']
    template = value['facts'][1]
    rows = []
    for period in ('2025-12-31', '2026-03-31', '2026-06-30'):
        row = deepcopy(template)
        row.update(id='report-' + period, frequency='quarterly', periodEnd=period,
                   sourceAsOf='2026-10-23T00:00:00Z', publishedAt='2026-07-29T00:00:00Z',
                   isLatestPeriod=period == '2026-06-30')
        row['reportedValues'].update(operatingIncome=60, operatingCashFlow=50,
            capitalExpenditure=-30, changeInWorkingCapital=-5, freeCashFlow=20, equity=500)
        # An unrelated bulky metric used to displace the next quarter entirely.
        row['metricProvenance']['equity'] = {'sourcePassage': 'e' * 9000}
        rows.append(row)
    value['facts'], value['coverage'] = select_evidence([quote, *rows])
    from digital_twin.modules.ai_orchestration.domain.evidence_purpose import annotate
    return annotate(ObservationEvidenceSession(value, [quote, *rows]).select([row['id'] for row in rows]))


def retrieve(value, ids=None, decide=None):
    saved = []
    def save(env):
        validate_execution_input(env); saved.append(deepcopy(env)); return 'input-' + str(len(saved))
    output = retrieve_working_evidence(ObservationEvidenceSession(value, value['facts']), value, [], [],
        decide or Mock(return_value={**scoped_decision('finish', ids or ['quote-1', 'report-2026-06-30', 'report-2026-03-31']),
                                     'purpose': 'source-dated', 'blocker': 'none'}), save, 262144)
    final, history, research, trace = output
    validate_execution_input(freeze_execution_input(final, history, research, retrieval_trace=trace))
    from digital_twin.modules.ai_orchestration.domain.question_evidence import checkpoints
    checkpoints(final, trace)
    return final, saved, output


def business_result(value=None):
    value = value or retrieve(source_packet())[0]
    raw = plan()
    basis = ref('reportedValues.operatingCashFlow', fact_id='report-2026-06-30')
    raw.update(summary='새 보고서에서 영업현금흐름과 투자 지출을 함께 확인해 잔여 현금의 설명을 보완합니다.',
        hypothesis='투자 지출이 잔여 현금에 영향을 주었을 수 있습니다. 회수 지연의 영향도 함께 확인해야 합니다.',
        observations=[], followUpConditions=[], evidenceIds=['quote-1', basis['factId']],
        questions=[{'question': '다음 같은 기준의 분기 보고서에서 영업현금흐름과 투자 지출을 비교합니다.', 'capability': 'observe'}],
        judgmentChange={'kind': 'business', 'impact': 'supports',
                        'reason': '새 현금흐름 원문을 확인해 투자 지출 설명을 보완했습니다.', 'evidence': [basis]})
    for section in ('summary', 'hypothesis'):
        raw['claimEvidence'][section] = [basis]
    return {**validate_plan(raw, value), 'input': value, 'observedAt': value['capturedAt'], 'executionInputId': 'author'}


def check_storage(test, save, company_cycle=False):
    from digital_twin.infrastructure.transactions.ai_control_research_knowledge import AIControlResearchKnowledge
    from digital_twin.modules.ai_orchestration.domain.evidence_purpose import annotate
    from digital_twin.modules.reasoning.contracts import quote_clock_assessment
    # This fixture stores several historical/new contracts in one job. Calls
    # are synthetic and otherwise each consumes the unknown-usage allowance.
    test.store.runtime_settings['aiDailyTokenBudget'] = 10000000
    value = source_packet(); value.update(test.subject, taskId=test.job['taskId'])
    if company_cycle:
        from digital_twin.modules.ai_orchestration.domain.company_research_cycle import VERSION
        value['companyResearchPolicy'] = VERSION
    value['sourceSnapshots'] = {value['worldId']: value['sourceSnapshotId']}
    for fact in value['facts']:
        fact.update(symbol=value['symbol'], sourceWorldId=value['worldId'], sourceSnapshotId=value['sourceSnapshotId'])
        if fact.get('accountId'): fact['accountId'] = value['accountId']
    value = annotate(value)
    for index, (ids, expected) in enumerate(((['quote-1', 'report-2026-06-30', 'report-2026-03-31'], 'ready'),
                                           (['quote-1', 'report-2026-03-31'], 'deferred'))):
        value = {**value, 'capturedAt': '2026-10-24T00:00:0' + str(index) + 'Z'}
        value['quoteAssessment'] = quote_clock_assessment(value['facts'], value['capturedAt'])
        value = annotate(value)
        final, history, research, trace = retrieve_working_evidence(ObservationEvidenceSession(value, value['facts']),
            value, [], [], Mock(return_value={**scoped_decision('finish', ids), 'purpose': 'source-dated', 'blocker': 'none'}),
            save, 262144, record_round=lambda step: test.store.save_retrieval_round(test.job, step))
        test.assertEqual(expected, final['retrieval']['status'])
        result = {'input': final, 'retrievalInputIds': [step['inputId'] for step in trace]}
        with test.store.connect() as connection:
            test.assertEqual(trace, AIControlResearchKnowledge.read_proof(connection, test.job, result))
            if expected == 'deferred':
                bad = deepcopy(result); bad['input']['retrieval']['status'] = 'ready'
                with test.assertRaises(ValueError): AIControlResearchKnowledge.read_proof(connection, test.job, bad)
        authored = freeze_execution_input(final, history, research, retrieval_trace=trace)
        save(authored)
        reviewed = freeze_review_input({'input': final, 'summary': '동일 기간의 현금흐름 비교 기록입니다.'})
        test.assertEqual(test.job['taskId'], test.store.review_proof(save(reviewed))['taskId'])


class BusinessInsightProgressTests(unittest.TestCase):
    def test_same_collection_clock_reserves_latest_and_comparison_quarters(self):
        value = source_packet(); session = ObservationEvidenceSession(value, value['facts'])
        old = session.business_baseline(document_views=True)
        self.assertEqual('report-2025-12-31', old['factIds'][0])
        final, saved, _ = retrieve(value)
        self.assertEqual(business_retrieval.VERSION, saved[0]['promptVersion'])
        self.assertEqual(['report-2026-06-30', 'report-2026-03-31'], final['businessEvidence']['factIds'][:2])
        self.assertEqual('2026-06-30', saved[0]['retrievalContext']['reportIndex'][0]['periodEnd'])
        for fact in final['facts']:
            if fact['kind'] == 'company-financial-state':
                self.assertNotIn('equity', fact['reportedValues'])
                self.assertEqual(-30, fact['reportedValues']['capitalExpenditure'])
                self.assertIn('equity', fact['evidenceView']['omittedMetrics'])
                self.assertTrue(fact['evidenceView']['sourceFactHash'])
        self.assertEqual('ready', final['retrieval']['status'])
        self.assertLessEqual(source_packet_bytes(final), 96000)

    def test_old_only_finish_defers_but_original_period_research_keeps_its_scope(self):
        final, _, _ = retrieve(source_packet(), ['quote-1', 'report-2026-03-31'])
        self.assertEqual('deferred', final['retrieval']['status'])
        self.assertEqual('2026-06-30', business_progress.report_gaps(final)[0]['periodEnd'])
        self.assertEqual([], business_progress.report_gaps(final, purpose='original-question'))
        self.assertEqual([], business_progress.report_gaps(final, facts=[final['facts'][0]]))
        # A budget-omitted newer report stays a known obligation, not a missing source.
        value = source_packet()
        next(row for row in value['facts'] if row['id'] == 'report-2026-06-30')['unprojectedAudit'] = 'a' * 33000
        selected, _, _ = retrieve(value, ['quote-1', 'report-2026-03-31'])
        self.assertIn('report-2026-06-30', selected['businessEvidence']['omittedByBudget'])
        self.assertEqual('deferred', selected['retrieval']['status'])

    def test_new_prompts_replay_without_changing_frozen_v25(self):
        old = active(); old_env = freeze_execution_input(old, [], []); before = deepcopy(old_env)
        # A trace-bearing envelope must retain its exact trace; simple phase checks use the source packet.
        value = source_packet()
        env = freeze_execution_input(value, [], [])
        self.assertEqual(business_insight.PROMPT_VERSION, env['promptVersion'])
        for frozen in (env, freeze_repair_input(env, {}, ['근거 확인 필요'], 'author'),
                       freeze_review_input({'input': value, 'summary': '같은 보고기간의 현금흐름을 확인합니다.'})):
            validate_execution_input(frozen)
            changed = deepcopy(frozen); changed['current'].pop('businessInsightPolicy')
            with self.assertRaises(EvidenceContractError): validate_execution_input(changed)
            changed = deepcopy(frozen); changed['outputSchema']['required'].remove('judgmentChange')
            with self.assertRaises(EvidenceContractError): validate_execution_input(changed)
        validate_execution_input(old_env); self.assertEqual(before, old_env)

    def test_price_change_cannot_approve_insufficient_business_judgment(self):
        result = business_result()
        result['judgmentChange']['impact'] = 'insufficient'
        verdict = {**review(), 'judgmentChange': {'supported': True, 'reason': '가격 위치와 계정 표시가 달라졌습니다.'}}
        self.assertEqual('rejected', accept_review(result, verdict, 'review')['status'])
        result['notification']['send'] = False
        self.assertEqual('observation-only', local_quality(result)['status'])

    def test_business_change_needs_content_evidence_and_independent_progress_review(self):
        result = business_result()
        self.assertEqual([], local_quality(result)['errors'])
        self.assertEqual('rejected', accept_review(result, review(), 'review')['status'])
        verdict = {**review(), 'judgmentChange': {'supported': True, 'reason': '새 원문의 현금흐름과 지출이 기존 설명을 보완합니다.'}}
        result['quality'] = accept_review(result, verdict, 'review')
        self.assertEqual('', quality_block(result))
        changed = deepcopy(result); changed['judgmentChange']['impact'] = 'weakens'
        self.assertTrue(quality_block(changed))
        for basis in (ref('currentPrice'), ref('sourceAsOf', fact_id='report-2026-06-30')):
            changed = deepcopy(result); changed['judgmentChange']['evidence'] = [basis]
            self.assertTrue(business_progress.quality_errors(changed))
        changed = deepcopy(result)
        old = ref('reportedValues.operatingCashFlow', fact_id='report-2026-03-31')
        changed['judgmentChange']['evidence'] = [old]
        changed['evidenceIds'].append(old['factId'])
        for section in ('summary', 'hypothesis'): changed['claimEvidence'][section] = [old]
        self.assertTrue(business_progress.quality_errors(changed))
        self.assertTrue(business_progress.review_errors(result, {**verdict, 'judgmentChange': None}))

    def test_same_business_scalar_cannot_be_renewed_by_quote_tick(self):
        result = business_result()
        packet = result['input']; packet['lastDeliveredNotification'] = {'facts': deepcopy(packet['facts'])}
        packet['facts'][0]['currentPrice'] += 1
        self.assertTrue(business_progress.quality_errors(result))
        fact = next(row for row in packet['facts'] if row['id'] == 'report-2026-06-30')
        fact['reportedValues']['operatingCashFlow'] += 10
        self.assertEqual([], business_progress.quality_errors(result))

    def test_market_observation_has_no_business_checklist_and_old_rendering_stays(self):
        value = source_packet(); raw = plan()
        raw['judgmentChange'] = {'kind': 'market', 'impact': 'weakens', 'reason': '단기 가격 회복이 기존 약세 설명을 일부 약화했습니다.',
                                 'evidence': [ref('currentPrice')]}
        result = {**validate_plan(raw, value), 'input': value, 'observedAt': value['capturedAt']}
        self.assertEqual([], business_progress.quality_errors(result))
        result['businessResearch'] = {'theses': [], 'reviews': []}
        self.assertEqual([], local_quality(result)['errors'])
        legacy = deepcopy(result); legacy['input'].pop('businessInsightPolicy')
        self.assertTrue(local_quality(legacy)['errors'])
        result['companyValue'] = {'models': [{'status': 'blocked', 'blockedReasons': ['input-lineage-unavailable']}]}
        result['businessResearch'] = {'reviews': [{'reason': '사업 판단은 여전히 근거 부족입니다.'}]}
        rendered = render_ai_observation(result)
        self.assertIn('AI 시장 관찰', rendered)
        self.assertNotIn('기업의 주당 가치', rendered)
        self.assertNotIn('이전 사업 가설 재검토', rendered)
        self.assertNotIn('거래량 비율은', rendered)
        before = deepcopy(result); before['input'].pop('businessInsightPolicy')
        self.assertIn('기업의 주당 가치', render_ai_observation(before))
        result['claimEvidence']['summary'] = [ref('reportedValues.revenue', fact_id='report-2026-06-30')]
        self.assertTrue(business_progress.quality_errors(result))

    def test_report_period_and_receive_clock_are_distinct_in_message(self):
        rendered = render_ai_observation(business_result())
        self.assertIn('보고기간 2026-06-30', rendered)
        self.assertIn('출처 기준 10/23', rendered)
        self.assertIn('AI 기업 관찰', rendered)

    def test_policy_renews_retry_once_and_crypto_has_no_report_requirement(self):
        from digital_twin.modules.ai_orchestration.domain.execution_lanes import material_key
        value = source_packet(); old = deepcopy(value); old.pop('businessInsightPolicy')
        self.assertNotEqual(material_key(old, []), material_key(value, []))
        self.assertEqual(material_key(value, []), material_key(deepcopy(value), []))
        quote = deepcopy(value['facts'][0]); quote['kind'] = 'crypto-asset'
        value['facts'] = [quote]
        self.assertEqual([], business_progress.report_gaps(value))

    def test_original_question_can_preserve_partial_research_without_customer_delivery(self):
        from question_assessment_checks import fixture, raw_assessment
        from test_research_knowledge import context
        from digital_twin.modules.ai_orchestration.domain import evidence_readiness, execution_lanes, question_evidence
        value, session, case, document = fixture()
        value = active(evidence_readiness.annotate(execution_lanes.annotate(value, {'capability': 'observe'})))
        value['businessInsightPolicy'] = business_insight.VERSION
        value['researchKnowledge'] = context(value)
        missing = ['원래 보고 기간의 공식 현금흐름 비교 원문이 필요합니다.']
        scope = {'caseId': case['caseId'], 'status': 'waiting', 'selectedFactIds': ['official-document'], 'missingEvidence': missing}
        final, history, memory, trace = retrieve_working_evidence(session,
            value, [], [case], Mock(return_value={**scoped_decision('assess', ['quote-1', 'official-document'], [scope]),
                                                 'purpose': 'original-question', 'blocker': 'required-source'}),
            lambda env: validate_execution_input(env) or 'original-question', 262144)
        self.assertEqual('assessment-ready', final['retrieval']['status'])
        progress = question_evidence.checkpoints(final, trace)
        self.assertEqual(case['questionContract'], progress[0]['questionContract'])
        raw = raw_assessment(case, missing=missing)
        raw['judgmentChange'] = {'kind': 'none', 'impact': 'insufficient',
                                'reason': '원래 질문의 미확인 항목을 보존해 추가 자료를 기다립니다.', 'evidence': []}
        result = validate_plan(raw, final, memory, require_research=True, require_resolution=True)
        self.assertFalse(result['notification']['send'])
        self.assertEqual(missing, result['questionResolutions'][0]['missingRequirements'])
        # Structured question sources retain the owner's exact document view
        # and can support a business judgment when their field is eligible.
        report = {**document, 'id': 'official-report', 'historicalDocumentReference': None,
            'reportObservationId': 'original-report', 'periodEnd': '2025-12-31', 'reportedValues': {'revenue': 100},
            'questionSourceReference': {'version': 'question-source-reference-v1', 'sourceKind': 'structured-report',
                'reportObservationId': 'original-report', 'periodEnd': '2025-12-31'},
            'evidenceGovernance': {'verificationStatus': 'verified-primary', 'sourceTrustState': 'trusted', 'validationState': 'ready'},
            'promptEvidenceAdmission': {'reasonCodes': ['evidence-stale']}}
        report_session = ObservationEvidenceSession(value, [*value['facts'], report])
        baseline = report_session.business_baseline([{'factId': report['id']}], document_views=True, period_priority=True)
        self.assertIn(report['id'], baseline['viewFactIds'])
        view = report_session.read_document(report['id'])['facts'][0]
        self.assertEqual('exact-question-document-v1', view['evidenceView']['version'])
        self.assertTrue(business_progress.business_ref(ref('reportedValues.revenue', fact_id=report['id']), view))
        self.assertFalse(business_progress.business_ref(ref('periodEnd', fact_id=report['id']), view))
