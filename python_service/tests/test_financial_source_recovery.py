"""Source-to-AI regressions for previously collected but lost statement facts."""
from copy import deepcopy
import io
import unittest
import zipfile

from digital_twin.modules.news_intelligence.domain.company_knowledge import (
    build_company_knowledge, dart_statement_periods, merge_company_knowledge_rows,
)
from digital_twin.modules.news_intelligence.domain.financial_reporting import bind_financial_report_contract, financial_period_sort_key
from digital_twin.modules.news_intelligence.domain.financial_report_selection import bind_summary_report_lineage
from digital_twin.modules.news_intelligence.domain.financial_research_evidence import financial_research_evidence
from digital_twin.infrastructure.external_api.opendart_xbrl import parse_opendart_xbrl_archive
from digital_twin.infrastructure.external_api.opendart_xbrl_collection import collect_xbrl_reports
from digital_twin.infrastructure.external_api.adapters.opendart import OpenDartCompanyFactsAdapter


TAGS = {'operatingCashFlow': 'CashFlowsFromUsedInOperatingActivities',
        'capitalExpenditure': 'PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'}
RECEIPT = '20260814000001'


def statements():
    return [{'account_id': 'ifrs-full_' + TAGS[metric], 'account_nm': name,
             'bsns_year': '2026', 'reprt_code': '11012', 'rcept_no': RECEIPT,
             'currency': 'KRW', 'sj_div': 'CF', 'thstrm_amount': str(value),
             'frmtrm_q_amount': str(prior), 'frmtrm_q_nm': '제 1 기 반기',
             'frmtrm_nm': '제 1 기', 'frmtrm_amount': '9999'}
            for metric, name, value, prior in (
                ('operatingCashFlow', '영업활동 현금흐름', 130, 100),
                ('capitalExpenditure', '유형자산의 취득', 80, 20))]


def archive(wrong_value=False):
    contexts, facts = [], []
    for year, values in ((2026, (130, 80)), (2025, (100, 20))):
        contexts.append(f'''<x:context id="c{year}"><x:entity><x:identifier scheme="test">1</x:identifier>
          <x:segment><d:explicitMember dimension="f:ConsolidationAxis">f:ConsolidatedMember</d:explicitMember></x:segment>
          </x:entity><x:period><x:startDate>{year}-01-01</x:startDate><x:endDate>{year}-06-30</x:endDate></x:period></x:context>''')
        for tag, value in zip(TAGS.values(), values):
            facts.append(f'<f:{tag} contextRef="c{year}" unitRef="money">{value + (1 if wrong_value else 0)}</f:{tag}>')
    xml = '''<x:xbrl xmlns:x="http://www.xbrl.org/2003/instance" xmlns:d="http://xbrl.org/2006/xbrldi" xmlns:f="urn:ifrs">
      <x:unit id="money"><x:measure>iso4217:KRW</x:measure></x:unit>''' + ''.join(contexts + facts) + '</x:xbrl>'
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        z.writestr('report.xbrl', xml)
    return output.getvalue()


def company_with_original(wrong_value=False):
    parsed = parse_opendart_xbrl_archive(archive(wrong_value), receipt_no=RECEIPT,
        symbol='TEST', business_year='2026', report_code='11012')
    parsed['receiptDate'] = '2026-08-14'
    return build_company_knowledge('TEST', dart_disclosure={
        'financialStatements': statements(), 'financialStatementBasis': {'scope': 'CFS'}},
        dart_xbrl={'documents': [parsed]}, source_references=[
            {'datasetId': ds, 'revisionId': ds + '-revision', 'subjectKey': 'TEST',
             'fetchedAt': '2026-08-15T00:00:00Z', 'availability': 'observed'}
            for ds in ('opendart.company_facts', 'opendart.xbrl_facts')])


def report(provider, period, dataset, **values):
    return bind_financial_report_contract({'period': period, 'periodEnd': period,
        'provider': provider, 'frequency': 'annual', 'financialReportingVersion': 'financial-reporting-v2',
        'metricProvenance': {k: {'provider': provider, 'currency': 'USD', 'scope': 'CFS',
            'durationBasis': 'annual', 'period': period} for k in values}, **values},
        [{'datasetId': dataset, 'revisionId': dataset + '-revision', 'subjectKey': 'TEST',
          'fetchedAt': '2026-08-15T00:00:00Z'}])


class FinancialSourceRecoveryTests(unittest.TestCase):
    def test_actual_dates_and_source_detail_survive_every_merge_order(self):
        self.assertEqual(financial_period_sort_key('20251231'), financial_period_sort_key('2025-12-31'))
        detailed = report('OpenDART', '2025-12-31', 'opendart.company_facts', revenue=100, operatingCashFlow=20, capitalExpenditure=10)
        summary = report('금융위원회·공공데이터포털', '20251231', 'public-data.kr-company-financials', revenue=99)
        sources = [{'symbol': 'TEST', 'financials': {'annual': [r]}} for r in (detailed, summary)]
        frozen = deepcopy(sources)
        for rows in (sources, list(reversed(sources))):
            merged = merge_company_knowledge_rows(*rows)
            self.assertEqual(20, merged['financials']['annual'][0]['operatingCashFlow'])
            self.assertEqual(2, len(merged['financialReportCandidates']))
            self.assertEqual(merged, merge_company_knowledge_rows(merged))
        self.assertEqual(frozen, sources)

    def test_vendor_month_end_cannot_displace_exact_fiscal_report(self):
        primary = report('SEC EDGAR', '2026-01-25', 'sec.company_facts', revenue=120, operatingCashFlow=20)
        vendor = report('yfinance', '2026-01-31', 'yfinance.fundamental', revenue=119)
        merged = merge_company_knowledge_rows({'financials': {'annual': [primary]}}, {'financials': {'annual': [vendor]}})
        self.assertEqual('SEC EDGAR', merged['financials']['annual'][0]['provider'])
        self.assertEqual({'SEC EDGAR', 'yfinance'}, {r['provider'] for r in merged['financialReportCandidates']})

    def test_dart_prior_half_fields_are_used_and_annual_totals_are_rejected(self):
        raw = statements(); frozen = deepcopy(raw)
        rows = dart_statement_periods(raw, {'scope': 'CFS'})
        self.assertEqual([130, 100], [r['operatingCashFlow'] for r in rows])
        self.assertEqual([80, 20], [r['capitalExpenditure'] for r in rows])
        self.assertEqual('frmtrm_q_amount', rows[1]['metricProvenance']['operatingCashFlow']['amountField'])
        for row in raw: row.pop('frmtrm_q_nm')
        self.assertEqual(1, len(dart_statement_periods(raw, {'scope': 'CFS'})))
        self.assertEqual(frozen, statements())

    def test_original_periods_complete_cash_flow_and_graph_report_read(self):
        company = company_with_original()
        self.assertEqual(4, company['financialIntegrity']['officialXbrlCandidateFacts'])
        evidence = financial_research_evidence('TEST', company)
        bridge = evidence[0].raw_payload['derivedMetricEvidence']['cashFlowBridge']
        self.assertEqual('calculated', bridge['status'])
        self.assertEqual(-30, bridge['freeCashFlowChange'])
        self.assertEqual('2026-01-01', evidence[0].raw_payload['metricProvenance']['operatingCashFlow']['periodStart'])
        from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
        from digital_twin.modules.reasoning.domain.ontology_schema import add_entity
        from digital_twin.modules.reasoning.domain.portfolio_ontology_company_concepts import add_company_knowledge_concepts
        from digital_twin.modules.reasoning.domain.observation_report_view import report_view
        graph = PortfolioOntology('test'); add_entity(graph, 'stock', 'TEST', 'Test', {'symbol': 'TEST'})
        add_company_knowledge_concepts(graph, 'stock:TEST', 'TEST', {'companyKnowledge': {'TEST': company}})
        fact = next(dict(e.properties, id=e.entity_id, kind=e.kind) for e in graph.entities
            if e.kind == 'company-financial-state' and e.properties.get('periodEnd') == '2026-06-30')
        self.assertEqual(-30, report_view(fact, ['cashFlowBridge'])['derivedMetricEvidence']['cashFlowBridge']['freeCashFlowChange'])

    def test_conflicting_original_amount_cannot_supply_missing_period(self):
        evidence = financial_research_evidence('TEST', company_with_original(wrong_value=True))
        self.assertEqual('unavailable', evidence[0].raw_payload['derivedMetricEvidence']['cashFlowBridge']['status'])
        self.assertFalse(evidence[0].raw_payload['metricProvenance']['operatingCashFlow']['periodStart'])

    def test_summary_contract_uses_exact_committed_revision_and_clock(self):
        row = report('금융위원회·공공데이터포털', '2025-12-31', 'public-data.kr-company-financials', revenue=100)
        fragment = {'companyKnowledge': {'TEST': {'financials': {'annual': [row]}}}}; frozen = deepcopy(fragment)
        reference = {'datasetId': 'public-data.kr-company-financials', 'revisionId': 'committed-revision',
            'subjectKey': 'TEST', 'fetchedAt': '2026-08-16T00:00:00Z'}
        bound = bind_summary_report_lineage(fragment, reference)
        evidence = financial_research_evidence('TEST', bound['companyKnowledge']['TEST'])
        self.assertEqual('committed-revision', evidence[0].raw_payload['sourceRevision'])
        self.assertEqual('2026-08-16T00:00:00Z', evidence[0].observed_at)
        self.assertEqual(frozen, fragment)

    def test_discovered_reports_share_one_bounded_versioned_followup(self):
        from types import SimpleNamespace
        annual = dict(statements()[0], rcept_no='20260317000001', reprt_code='11011', bsns_year='2025')
        source = SimpleNamespace(subject_key='TEST', payload={'dartDisclosures': {'TEST': {'financialStatements': statements() + [annual]}}})
        requests = OpenDartCompanyFactsAdapter().followup_requests(source, {})
        self.assertEqual(1, len(requests)); self.assertIn('xbrl-v2', requests[0].partition_key)
        self.assertEqual(['11012', '11011'], [r['reportCode'] for r in requests[0].watermark['reports']])
        class Provider:
            def guarded_call(self, _provider, _key, operation): return operation()
            def fetch_bytes(self, url, headers): return archive() if RECEIPT in url else b'<result><status>013</status></result>'
        result = collect_xbrl_reports(Provider(), 'test', 'TEST', requests[0].watermark['reports'])
        self.assertEqual(1, len(result['documents'])); self.assertEqual(1, len(result['unavailableReports']))
        with self.assertRaises(ValueError): collect_xbrl_reports(Provider(), 'test', 'TEST', [{'receiptNo': 'bad'}])
