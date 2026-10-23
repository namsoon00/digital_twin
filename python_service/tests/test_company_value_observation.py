"""Calculation -> frozen graph -> AI explanation -> receipt-safe presentation."""
from copy import deepcopy
import unittest
from unittest.mock import Mock

from ai_insight_fixtures import plan, ref
from test_bounded_evidence_readiness import packet
from test_scoped_knowledge_research import scoped_decision
import test_valuation_snapshot as valuation_fixture
from digital_twin.modules.portfolio.domain.valuation.projection import add_valuation_row_concepts
from digital_twin.modules.portfolio.contracts import Position
from digital_twin.modules.reasoning.contracts import PortfolioOntology, ObservationEvidenceSession, EvidenceContractError
from digital_twin.modules.reasoning.domain.observation_evidence import select_evidence, quote_clock_assessment
from digital_twin.modules.reasoning.domain.observation_value import company_value_context, value_view
from digital_twin.modules.ai_orchestration.domain import company_value, evidence_purpose
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
from digital_twin.modules.ai_orchestration.domain.insight_contract import insight_fingerprint
from digital_twin.modules.notifications.application.ai_observation_message import render_ai_observation


def valuation_packet(eligible=True):
    value = packet()
    value['capturedAt'] = '2026-09-26T01:05:00Z'
    quote = next(f for f in value['facts'] if f['kind'] == 'stock')
    quote.update(currentPrice=42, currency='USD', sourceAsOf=value['capturedAt'], maxAgeMinutes=15)
    source = valuation_fixture.ValuationSnapshotTests().row()
    source.update(valuationModelFamily='earnings-multiple', approvalStatus='approved',
                  source='reported', valuationSourceType='reported', perShare=True, epsPeriod='fy1',
                  targetPER=20, expectedEPS=2, valuationDecisionEligible=eligible)
    source['multipleBand']['comparabilityState'] = 'comparable'
    source['assumptions'] = [{'id': 'target-multiple', 'value': 20, 'unit': 'times', 'status': 'approved'}]
    row = valuation_fixture.ValuationSnapshotTests().bind(source)
    graph = PortfolioOntology('company-value-test')
    add_valuation_row_concepts(graph, 'quote-1', Position(symbol='TEST', name='Test', currency='USD', current_price=42), row)
    facts = [dict(node.properties, id=node.entity_id, kind=node.kind, sourceWorldId=value['worldId'],
                  sourceSnapshotId=value['sourceSnapshotId']) for node in graph.entities
             if node.kind in {'valuation-assessment', 'valuation-input-bundle', 'fair-value-estimate'}]
    value['facts'], value['coverage'] = select_evidence([quote, *facts])
    value['quoteAssessment'] = quote_clock_assessment(value['facts'], value['capturedAt'])
    return company_value.annotate(evidence_purpose.annotate(value))


def value_plan(value):
    model = value['companyValueContext']['models'][0]
    raw = plan()
    raw.update(summary='사업 이익을 유지한다는 가정에 따른 가치 범위를 검토했습니다.',
        hypothesis='이익이 유지되면 기존 가치를 설명할 수 있지만 수익성이 낮아지면 평가 가정을 다시 확인해야 합니다.',
        comparison='이전 가치 평가가 없어 최초 평가로 기록하며 향후 같은 기준으로 비교합니다.',
        portfolioImpact='보유 종목의 가격과 사업 가치를 구분해 손실 회복의 근거를 확인합니다.',
        counterEvidence='가치 범위는 사업 가정을 전제로 하며 수익성 유지가 보장되지는 않습니다.',
        notification={'send': True, 'reason': '사업 가정과 계산 근거가 연결된 가치 범위를 처음 확인했습니다.'},
        observations=[], followUpConditions=[])
    raw['claimEvidence'] = {key: [ref('fairValueBase', fact_id=model['estimateFactId'])] for key in raw['claimEvidence']}
    raw['evidenceIds'] = [model['estimateFactId'], model['assessmentFactId'], model['inputFactId']]
    return raw


class CompanyValueObservationTests(unittest.TestCase):
    def test_graph_calculation_reaches_final_input_even_when_model_deselects_it(self):
        value = valuation_packet(); saved = []
        def save(env):
            validate_execution_input(env); saved.append(env); return 'input'
        decision = {**scoped_decision('finish', ['quote-1']), 'purpose': 'source-dated', 'blocker': 'none'}
        final, history, research, trace = retrieve_working_evidence(ObservationEvidenceSession(value, value['facts']),
            value, [], [], Mock(return_value=decision), save, 262144)
        self.assertEqual('available', final['companyValueContext']['status'])
        self.assertEqual('ready', final['retrieval']['status'])
        self.assertEqual(3, len(final['companyValueEvidence']['factIds']))
        self.assertTrue(set(final['companyValueEvidence']['factIds']).issubset(f['id'] for f in final['facts']))
        self.assertEqual(company_value.RETRIEVAL_VERSION, saved[0]['promptVersion'])
        self.assertEqual(1, len(trace))
        env = freeze_execution_input(final, history, research, retrieval_trace=trace)
        self.assertEqual(company_value.PROMPT_VERSION, env['promptVersion'])
        for frozen in (env, freeze_repair_input(env, {}, ['근거 확인'], 'parent'),
                       freeze_review_input({'input': final, 'summary': '기업 가치 검토'})):
            validate_execution_input(frozen)
        tampered = deepcopy(env); tampered['current']['companyValueContext']['models'][0]['scenarios'][1]['valuePerShare'] += 1
        with self.assertRaises(EvidenceContractError): validate_execution_input(tampered)

    def test_value_narrative_needs_no_price_prediction_and_renders_only_calculated_values(self):
        value = valuation_packet(); raw = value_plan(value)
        result = {**validate_plan(raw, value), 'input': value, 'observedAt': value['capturedAt']}
        self.assertNotIn('conditionValidation', result)
        self.assertEqual([], local_quality(result)['errors'])
        message = render_ai_observation(result)
        for expected in ('기업의 주당 가치', '보수적 36달러', '기준 40달러', '낙관적 44달러', '자료 기준', '시세 기준'):
            self.assertIn(expected, message)
        self.assertNotIn('20일 평균 가격:', message)
        old = deepcopy(result); old['companyValue']['models'][0]['scenarios'][0]['valuePerShare'] = 1
        self.assertTrue(local_quality(old)['errors'])

    def test_unreviewed_future_currency_and_non_per_share_values_fail_closed(self):
        for mutation, reason in (
            (lambda f: f.update(valuationDecisionEligible=False), 'valuation-not-admitted'),
            (lambda f: f.update(valuationCurrency='KRW'), 'per-share-currency-unconfirmed'),
            (lambda f: f.update(perShare=False), 'per-share-currency-unconfirmed'),
            (lambda f: f.update(fairValueBase=999), 'estimate-calculation-mismatch')):
            value = valuation_packet()
            estimate = next(f for f in value['facts'] if f['kind'] == 'fair-value-estimate'); mutation(estimate)
            value = company_value.refresh(value); model = value['companyValueContext']['models'][0]
            self.assertEqual('blocked', model['status']); self.assertIn(reason, model['blockedReasons'])
            self.assertEqual([], model['scenarios'])
        value = valuation_packet()
        bundle = next(f for f in value['facts'] if f['kind'] == 'valuation-input-bundle')
        bundle['payload']['knowledgeCutoffAt'] = '2099-01-01T00:00:00Z'
        self.assertEqual('blocked', company_value_context(value)['status'])
        value = company_value.refresh(value)
        raw = plan(); raw['notification']['send'] = False
        result = {**validate_plan(raw, value), 'input': value, 'observedAt': value['capturedAt']}
        message = render_ai_observation(result)
        self.assertIn('평가 보류', message); self.assertNotIn('기준 40달러', message)
        value = valuation_packet()
        value['facts'] = [f for f in value['facts'] if f['kind'] != 'fair-value-estimate']
        reasons = company_value_context(value)['models'][0]['blockedReasons']
        self.assertIn('estimate-lineage-unavailable', reasons)
        self.assertNotIn('per-share-currency-unconfirmed', reasons)

    def test_price_changes_reuse_business_basis_and_do_not_reannounce_same_range(self):
        first = valuation_packet(); original = {**validate_plan(value_plan(first), first), 'input': first}
        next_value = deepcopy(first); next_value['previousCompanyValue'] = deepcopy(first['companyValueContext'])
        next(f for f in next_value['facts'] if f['kind'] == 'stock')['currentPrice'] = 43
        next_value = company_value.refresh(next_value)
        self.assertEqual('unchanged', next_value['companyValueContext']['models'][0]['change'])
        current = {**validate_plan(value_plan(next_value), next_value), 'input': next_value}
        self.assertEqual(insight_fingerprint(original, first), insight_fingerprint(current, next_value))
        next(f for f in next_value['facts'] if f['kind'] == 'stock')['currentPrice'] = 30
        next_value = company_value.refresh(next_value)
        self.assertNotEqual(insight_fingerprint(original, first), insight_fingerprint(current, next_value))
        self.assertEqual('unchanged', next_value['companyValueContext']['models'][0]['change'])

    def test_future_sources_and_unadmitted_values_cannot_support_a_conclusion(self):
        value = valuation_packet()
        bundle = next(f for f in value['facts'] if f['kind'] == 'valuation-input-bundle')
        bundle['payload']['sourceReferences'][0]['availableAt'] = '2099-01-01'
        value = company_value.refresh(value)
        self.assertIn('future-source-reference', value['companyValueContext']['models'][0]['blockedReasons'])
        result = {**validate_plan(value_plan(value), value), 'input': value}
        self.assertIn('검증되지 않은 가치평가를 결론의 근거로 사용했습니다.', company_value.quality_errors(result))

    def test_changed_assumption_is_distinct_from_quote_movement(self):
        value = valuation_packet()
        original = deepcopy(value['companyValueContext'])
        value['previousCompanyValue'] = original
        bundle = next(f for f in value['facts'] if f['kind'] == 'valuation-input-bundle')
        bundle['payload']['assumptions'][0]['value'] = 21
        value = company_value.refresh(value)
        self.assertEqual('basis-changed', value['companyValueContext']['models'][0]['change'])
        self.assertNotEqual(original['models'][0]['basisFingerprint'], value['companyValueContext']['models'][0]['basisFingerprint'])

    def test_exact_view_budget_is_audited_and_cannot_change_a_value(self):
        value = valuation_packet(); session = ObservationEvidenceSession(value, value['facts'])
        baseline = session.company_value_baseline()
        self.assertLessEqual(baseline['bytes'], 32000)
        fact_id = baseline['factIds'][0]; baseline['views'][fact_id]['symbol'] = 'OTHER'
        with self.assertRaises(EvidenceContractError): session.select(baseline['factIds'], baseline['views'])
        from digital_twin.modules.reasoning.domain.observation_value import value_baseline
        small = value_baseline(value['facts'], max_bytes=1)
        self.assertFalse(small['factIds']); self.assertTrue(small['omitted'])
        assessment = next(f for f in value['facts'] if f['kind'] == 'valuation-assessment')
        assessment['payload']['impliedExpectations'] = {'status': 'solved', 'impliedEbitMarginPct': 18,
            'solverTrace': [{'detail': 'x' * 6000}] * 8, 'financialEvidence': {'duplicatedInputs': 'x' * 12000}}
        compact = value_baseline(value['facts'])
        self.assertEqual(3, len(compact['factIds']))
        projected = compact['views'][assessment['id']]
        self.assertEqual(18, projected['payload']['impliedExpectations']['impliedEbitMarginPct'])
        self.assertIn('solverTrace', projected['evidenceView']['omittedNestedFields']['impliedExpectations'])
        self.assertEqual('available', company_value_context({**value, 'facts': list(compact['views'].values())})['status'])

    def test_predecessor_input_remains_replayable_and_crypto_is_not_valued(self):
        old = evidence_purpose.annotate(packet()); frozen = freeze_execution_input(old, [], [])
        self.assertEqual(evidence_purpose.PROMPT_VERSION, frozen['promptVersion'])
        validate_execution_input(frozen)
        crypto = deepcopy(old); crypto['facts'][0]['kind'] = 'crypto-asset'
        self.assertNotIn('companyValuePolicy', company_value.annotate(crypto))


if __name__ == '__main__':
    unittest.main()
