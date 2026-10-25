"""Source arithmetic, immutable forecasts and the live author/review contract."""
from copy import deepcopy
import unittest

from test_business_research import business_packet, raw_research, report
from test_business_insight_progress import source_packet, retrieve
from digital_twin.modules.news_intelligence.domain.cash_flow_bridge import cash_flow_bridge
from digital_twin.modules.ai_orchestration.domain import quantitative_research as quantitative, company_research_cycle as cycle, company_retrieval
from digital_twin.modules.ai_orchestration.domain.business_research import validate_business, raw_business, thesis_case, evaluate_thesis
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input
from digital_twin.modules.reasoning.contracts import EvidenceContractError


def cash_report(year, ocf, capex, fcf=None):
    value = report(business_packet(), year, 100)
    source = value['metricProvenance']['revenue']
    value['reportedValues'] = {'revenue': 100, 'operatingCashFlow': ocf, 'capitalExpenditure': capex}
    if fcf is not None:
        value['reportedValues']['freeCashFlow'] = fcf
    value['metricProvenance'] = {key: deepcopy(source) for key in value['reportedValues']}
    return value


def numeric_research():
    value = raw_research()
    value['theses'][0]['checkpoints'] = [{
        'factId': 'report-2025', 'metric': 'revenue', 'targetPeriodStart': '2026-01-01',
        'targetPeriodEnd': '2026-12-31', 'expectedLow': 110, 'expectedHigh': 130,
        'expectationKind': 'ai-assumption', 'assumption': '기존 매출 기준에서 수요 확대가 이어진다는 가정을 검토합니다.',
        'evidenceIds': ['report-2025'], 'meaning': '다음 연간 매출이 사전에 정한 범위에 들어오는지 확인합니다.'}]
    return value


def numeric_case():
    packet = business_packet(); packet['companyResearchPolicy'] = cycle.VERSION
    business = validate_business(numeric_research(), packet, [])
    case = thesis_case({**packet, 'taskId': 'research'}, {'input': packet, 'executionInputId': 'authored'},
                       business['theses'][0], packet['capturedAt'])
    return packet, business, case


class CompanyResearchCycleTests(unittest.TestCase):
    def test_cash_flow_bridge_reconciles_components_without_replacing_reported_fcf(self):
        before, after = cash_report(2024, 100, -20, 80), cash_report(2025, 130, -80, 55)
        frozen = deepcopy((before, after))
        result = cash_flow_bridge(before, after)
        self.assertEqual('calculated', result['status'])
        self.assertEqual((30, -60, -30), tuple(result[key] for key in
            ('operatingCashFlowContribution', 'capitalExpenditureContribution', 'freeCashFlowChange')))
        self.assertEqual(5, result['current']['reconciliationDifference'])
        self.assertEqual('not-established', result['causalValidation'])
        self.assertEqual(frozen, (before, after))
        self.assertEqual(result, cash_flow_bridge(before, after))
        after['metricProvenance']['freeCashFlow']['currency'] = 'KRW'
        incompatible = cash_flow_bridge(before, after)
        self.assertIsNone(incompatible['current']['reportedFreeCashFlow'])
        self.assertEqual(55, after['reportedValues']['freeCashFlow'])
        self.assertIsNone(incompatible['current']['reconciliationDifference'])
        self.assertEqual('missing-or-incompatible-basis', incompatible['current']['reportedFcfReconciliationState'])

    def test_cash_flow_bridge_rejects_mixed_periods_sources_and_later_corrections(self):
        before, after = cash_report(2024, 100, 20), cash_report(2025, 130, 80)
        for mutate in (
            lambda row: row['metricProvenance']['operatingCashFlow'].update(currency='KRW'),
            lambda row: row['metricProvenance']['capitalExpenditure'].update(periodStart='2025-07-01'),
            lambda row: row['metricProvenance']['capitalExpenditure'].update(sourceReferences=[]),
            lambda row: row['reportedValues'].update(capitalExpenditure=None),
            lambda row: row['reportedValues'].update(operatingCashFlow=True),
            lambda row: row['reportedValues'].update(operatingCashFlow=1e300),
        ):
            broken = deepcopy(after); mutate(broken)
            self.assertEqual('unavailable', cash_flow_bridge(before, broken)['status'])
        for item in before['metricProvenance'].values():
            item['sourceReferences'][0]['fetchedAt'] = '2027-01-01T00:00:00Z'
        self.assertIn('previous-revision-known-later', cash_flow_bridge(before, after)['reasons'])
        after['metricProvenance']['capitalExpenditure'].pop('periodStart')
        required = cash_flow_bridge(before, after)['requiredEvidence']
        self.assertIn({'reportObservationId': 'official-2025', 'periodEnd': '2025-12-31', 'frequency': 'annual',
                      'metric': 'capitalExpenditure', 'missingFields': ['periodStart']}, required)

    def test_cash_flow_zero_is_data_and_different_ytd_durations_are_not_comparable(self):
        before, after = cash_report(2024, 0, 0), cash_report(2025, -10, 0)
        self.assertEqual(-10, cash_flow_bridge(before, after)['freeCashFlowChange'])
        before['frequency'] = after['frequency'] = 'interim'
        for row in (before, after):
            for basis in row['metricProvenance'].values(): basis['durationBasis'] = 'year-to-date'
        after['periodEnd'] = '2025-09-30'
        self.assertEqual('unavailable', cash_flow_bridge(before, after)['status'])

    def test_only_verified_cash_flow_contributions_are_business_claim_evidence(self):
        from digital_twin.modules.ai_orchestration.domain.business_progress import business_ref
        from digital_twin.modules.ai_orchestration.domain.research_evidence_use import usable_fact
        bridge = cash_flow_bridge(cash_report(2024, 100, 20), cash_report(2025, 130, 80))
        fact = {'kind': 'company-financial-state', 'derivedMetricEvidence': {'cashFlowBridge': bridge}}
        ref = {'field': 'derivedMetricEvidence.cashFlowBridge.freeCashFlowChange'}
        self.assertTrue(usable_fact(fact, ref['field'])); self.assertTrue(business_ref(ref, fact))
        self.assertFalse(usable_fact(fact, 'derivedMetricEvidence.cashFlowBridge.calculationId'))
        bridge['freeCashFlowChange'] = 999
        self.assertFalse(usable_fact(fact, ref['field'])); self.assertFalse(business_ref(ref, fact))

    def test_cash_flow_owner_projection_retains_formula_sources_and_exact_metric_reads(self):
        from digital_twin.modules.news_intelligence.domain.financial_reporting import bind_financial_report_contract, FINANCIAL_REPORTING_VERSION
        from digital_twin.modules.news_intelligence.domain.financial_research_evidence import financial_research_evidence
        from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
        from digital_twin.modules.reasoning.domain.ontology_schema import add_entity
        from digital_twin.modules.reasoning.domain.portfolio_ontology_company_concepts import add_company_knowledge_concepts
        from digital_twin.modules.reasoning.domain.observation_report_view import report_view
        rows = []
        for year, ocf, capex in ((2025, 130, -80), (2024, 100, -20)):
            source = cash_report(year, ocf, capex)
            provenance = source['metricProvenance']
            for basis in provenance.values(): basis.update(filed=f'{year+1}-02-01', provider='SEC EDGAR', official=True)
            rows.append(bind_financial_report_contract({**source['reportedValues'], 'period': f'{year}-12-31',
                'frequency': 'annual', 'financialReportingVersion': FINANCIAL_REPORTING_VERSION, 'provider': 'SEC EDGAR',
                'metricProvenance': provenance}, [{'datasetId': 'sec.company_facts', 'revisionId': 'annual-source',
                'subjectKey': 'TEST', 'fetchedAt': '2026-02-02T00:00:00Z'}]))
        company = {'financials': {'annual': rows}}
        evidence = financial_research_evidence('TEST', company)
        self.assertEqual(-30, evidence[0].raw_payload['derivedMetricEvidence']['cashFlowBridge']['freeCashFlowChange'])
        graph = PortfolioOntology('test'); add_entity(graph, 'stock', 'TEST', 'Test', {'symbol': 'TEST'})
        add_company_knowledge_concepts(graph, 'stock:TEST', 'TEST', {'companyKnowledge': {'TEST': company}})
        fact = next(dict(row.properties, id=row.entity_id, kind=row.kind) for row in graph.entities
                    if row.kind == 'company-financial-state' and row.properties.get('periodEnd') == '2025-12-31')
        view = report_view(fact, ['cashFlowBridge'])
        bridge = view['derivedMetricEvidence']['cashFlowBridge']
        self.assertEqual(-30, bridge['freeCashFlowChange']); self.assertTrue(bridge['sourceReferences'])
        self.assertEqual('FinancialState', fact['tboxClass'])
        self.assertEqual({}, view['reportedValues'])

    def test_company_cycle_freezes_new_phases_and_preserves_previous_author_contract(self):
        old = freeze_execution_input(source_packet(), [], []); frozen = deepcopy(old)
        value = source_packet(); value['companyResearchPolicy'] = cycle.VERSION
        authored = freeze_execution_input(value, [], [])
        self.assertEqual(cycle.PROMPT_VERSION, authored['promptVersion'])
        for envelope in (authored, freeze_repair_input(authored, {}, ['근거 확인 필요'], 'parent'),
                         freeze_review_input({'input': value, 'summary': '동일 기준의 현금흐름 기여를 확인합니다.'})):
            validate_execution_input(envelope)
            bad = deepcopy(envelope); bad['current'].pop('companyResearchPolicy')
            with self.assertRaises(EvidenceContractError): validate_execution_input(bad)
        validate_execution_input(old); self.assertEqual(frozen, old)

    def test_company_cycle_retrieval_uses_existing_bounded_reads_and_keeps_policy(self):
        value = source_packet(); value['companyResearchPolicy'] = cycle.VERSION
        final, saved, _ = retrieve(value)
        self.assertEqual(company_retrieval.VERSION, saved[0]['promptVersion'])
        self.assertEqual(cycle.VERSION, final['companyResearchPolicy'])
        self.assertIn('cashFlowBridge', saved[0]['prompt'])

    def test_quantitative_contract_roundtrip_retains_absolute_units_and_fixed_period(self):
        packet, business, case = numeric_case()
        self.assertEqual(numeric_research(), raw_business(business))
        check = case['contract']['checkpoints'][0]
        self.assertEqual('USD', check['baseline']['basis']['currency'])
        self.assertEqual(100, check['baseline']['value'])
        self.assertEqual('ai-assumption', check['expectationKind'])
        self.assertEqual('specified-future-period', check['comparison'])
        self.assertTrue(check['checkpointId'])

    def test_quantitative_contract_rejects_retrospective_unbounded_and_unsourced_ranges(self):
        packet = business_packet(); packet['companyResearchPolicy'] = cycle.VERSION
        for changes in ({'expectedLow': 150}, {'expectedHigh': float('nan')}, {'expectedLow': True},
                        {'targetPeriodEnd': '2025-12-31'}, {'targetPeriodStart': '2026-07-01'},
                        {'expectationKind': 'company-guidance'}, {'evidenceIds': ['quote-1']}):
            value = numeric_research(); value['theses'][0]['checkpoints'][0].update(changes)
            with self.assertRaises(ValueError): validate_business(value, packet, [])

    def test_quantitative_actuals_preserve_first_miss_across_report_restatement(self):
        packet, _, case = numeric_case()
        future = {**packet, 'capturedAt': '2027-03-01T00:00:00Z', 'facts': [report(packet, 2026, 150)]}
        actual = evaluate_thesis(case, future)
        self.assertEqual('above-expected-range', actual[0]['status'])
        self.assertEqual(20, actual[0]['rangeDeviation'])
        self.assertEqual(50, actual[0]['changeFromBaseline'])
        case['observations'] = actual
        future['facts'][0]['reportedValues']['revenue'] = 120
        self.assertEqual(actual, evaluate_thesis(case, future))

    def test_quantitative_missing_or_incomparable_actuals_are_not_failed_forecasts(self):
        packet, _, case = numeric_case()
        for changes in ({'publishedAt': '2026-02-01'}, {'publishedAt': '2028-01-01'}, {'periodEnd': '2026-09-30'}):
            future = {**packet, 'capturedAt': '2027-03-01T00:00:00Z', 'facts': [report(packet, 2026, 120, **changes)]}
            self.assertEqual('awaiting-report', evaluate_thesis(case, future)[0]['status'])
        self.assertEqual('expired-unobserved', evaluate_thesis(case, {**packet, 'capturedAt': '2029-01-01T00:00:00Z', 'facts': []})[0]['status'])
        # A delayed evaluator can use a source already acquired before expiry;
        # a report first acquired after the horizon cannot backfill a success.
        future = {**packet, 'capturedAt': '2029-01-01T00:00:00Z', 'facts': [report(packet, 2026, 120)]}
        self.assertEqual('within-expected-range', evaluate_thesis(case, future)[0]['status'])
        future['facts'][0]['observedAt'] = '2028-01-01T00:00:00Z'
        self.assertEqual('expired-unobserved', evaluate_thesis(case, future)[0]['status'])

    def test_quantitative_notification_contains_frozen_range_and_observed_deviation(self):
        from digital_twin.modules.ai_orchestration.domain.research_progress import research_progress
        from digital_twin.modules.notifications.application.research_progress_message import render_research_progress
        from digital_twin.modules.notifications.application.ai_observation_message import render_ai_observation
        from ai_insight_fixtures import plan
        packet, business, case = numeric_case()
        event = {'stage': 'business-registered', 'eventId': 'registered', 'taskId': 'research', 'revision': 1, 'at': packet['capturedAt']}
        notice = research_progress(case, event)
        text = render_research_progress(notice)
        self.assertIn('AI 가정 범위 110~130 USD', text)
        self.assertIn('2026-01-01~2026-12-31', text)
        zero = deepcopy(notice); zero['contract']['checkpoints'][0]['expectedLow'] = 0
        self.assertIn('AI 가정 범위 0~130 USD', render_research_progress(zero))
        self.assertIn('AI 가정 범위', render_ai_observation({**plan(), 'input': packet, 'observedAt': packet['capturedAt'],
                                                         'businessResearch': business, 'followUpConditions': []}))
        future = {**packet, 'capturedAt': '2027-03-01T00:00:00Z', 'facts': [report(packet, 2026, 90)]}
        case['observations'] = evaluate_thesis(case, future)
        event.update(stage='business-reviewed', eventId='reviewed', revision=2, at=future['capturedAt'])
        text = render_research_progress(research_progress(case, event))
        self.assertIn('범위 밖 1건', text); self.assertIn('매출: 90USD', text); self.assertIn('-20USD', text)
        self.assertIsNone(research_progress(case, event))

    def test_quantitative_review_is_versioned_and_cannot_use_an_old_review_receipt(self):
        from digital_twin.modules.ai_orchestration.domain import research_review
        packet, business, _ = numeric_case()
        result = {'input': packet, 'businessResearch': business, 'executionInputId': 'author'}
        frozen = research_review.freeze(result, [], 262144)
        self.assertEqual(research_review.QUANTITATIVE_VERSION, frozen['promptVersion'])
        validate_execution_input(frozen)
        review = {'version': research_review.VERSION, 'accepted': True, 'reason': '수치 범위와 가정의 연결을 확인했습니다.'}
        self.assertEqual('rejected', research_review.accept(result, review, 'review')['status'])
        review['version'] = research_review.QUANTITATIVE_VERSION
        self.assertEqual('accepted', research_review.accept(result, review, 'review')['status'])

    def test_quantitative_miss_updates_knowledge_without_adopting_a_method(self):
        from digital_twin.modules.research_knowledge.domain.assessments import assessment_for_claim
        packet, _, case = numeric_case()
        case['observations'] = evaluate_thesis(case, {**packet, 'capturedAt': '2027-03-01T00:00:00Z', 'facts': [report(packet, 2026, 90)]})
        result = assessment_for_claim({'businessThesisId': case['caseId'], 'claimId': 'claim', 'frameworkId': 'cash',
                                      'symbol': packet['symbol'], 'purpose': 'company-value'}, [case])
        self.assertEqual('checkpoint-not-observed', result['status'])
        self.assertFalse(result['methodProposal']['adopted'])
        self.assertIn('예상 범위', result['methodProposal']['proposal'])
        self.assertEqual(-20, result['observations'][0]['rangeDeviation'])
