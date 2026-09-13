import copy
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.market_data.domain.bok_release import latest_bok_statement_url, parse_bok_statement
from digital_twin.modules.market_data.domain.bls_statistics import BLS_SERIES, parse_bls_statistics, verified_bls_statistics
from digital_twin.modules.market_data.application.external_data.document_recovery import document_recovery_request
from digital_twin.modules.market_data.application.external_data.document_recovery_service import OfficialDocumentRecoveryService
from digital_twin.modules.market_data.domain.information_observation import price_observation
from digital_twin.modules.market_data.application.information_observation_service import InformationObservationService
from digital_twin.modules.market_data.application.external_data.fact_transition_service import ExternalFactTransitionService
from digital_twin.modules.investment_calendar.domain.release_information import calendar_release_information
from digital_twin.modules.investment_calendar.application.investment_calendar_service import InvestmentCalendarService
from digital_twin.modules.news_intelligence.domain.investment_research import ResearchEvidence
from digital_twin.modules.news_intelligence.domain.information_brief import build_information_brief
from digital_twin.infrastructure.external_api.adapters import default_external_dataset_registry


NOW = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
BOK_URL = 'https://www.bok.or.kr/portal/bbs/P0000559/view.do?nttId=123&menuNo=200690'
BOK = '<title>통화정책방향(2026.8.27)</title><p>등록일 2026.08.27</p><p>금융통화위원회는 다음 통화정책방향 결정시까지 한국은행 기준금리를 현재의 2.75% 수준에서 3.00%로 상향 조정하여 통화정책을 운용하기로 하였다.</p>'


def bls_payload():
    rows = []
    for identity in BLS_SERIES:
        points = []
        for year, month, value in [(2026, 8, 110), (2026, 7, 100), (2025, 8, 100)]:
            points.append({'year': str(year), 'period': 'M' + str(month).zfill(2), 'value': str(value if identity != BLS_SERIES[3] else 4.1)})
        rows.append({'seriesID': identity, 'data': points})
    return {'status': 'REQUEST_SUCCEEDED', 'Results': {'series': rows}}


def disclosure(kind='disclosure'):
    return ResearchEvidence('research:005930:dart:20260827000001', '005930', kind, 'OpenDART', 'Quarterly filing',
        published_at='20260827', url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260827000001',
        raw_payload={'receiptNo': '20260827000001', 'receiptDate': '20260827'})


def quote(stamp, price=100, **updates):
    return {'symbol': 'EXAMPLE', 'currentPrice': price, 'sourceAsOf': stamp, 'generatedAt': stamp,
            'currency': 'USD', 'provider': 'test', 'observationGranularity': '3m', 'dataQuality': 'live', **updates}


class KoreanReleaseTests(unittest.TestCase):
    def test_reported_rate_previous_and_date_not_clock(self):
        result = parse_bok_statement(BOK, BOK_URL, NOW)
        self.assertEqual(result['metrics'][0]['actual'], 3)
        self.assertEqual(result['metrics'][0]['previous'], 2.75)
        self.assertEqual(result['metrics'][0]['changePercentagePoints'], .25)
        self.assertEqual(result['releasedAt'], '')

    def test_unchanged_and_cut(self):
        hold = BOK.replace('2.75% 수준에서 3.00%로 상향 조정', '2.75% 수준에서 유지')
        self.assertEqual(parse_bok_statement(hold, BOK_URL, NOW)['metrics'][0]['actual'], 2.75)
        cut = BOK.replace('3.00%로 상향', '2.50%로 하향')
        self.assertEqual(parse_bok_statement(cut, BOK_URL, NOW)['metrics'][0]['changePercentagePoints'], -.25)

    def test_wrong_date_host_direction_and_future(self):
        for markup, url in [(BOK.replace('등록일 2026.08.27', '등록일 2026.08.28'), BOK_URL), (BOK, BOK_URL.replace('www.bok.or.kr', 'example.org')), (BOK.replace('상향', '하향'), BOK_URL), (BOK.replace('2026', '2027'), BOK_URL)]:
            with self.assertRaises(ValueError): parse_bok_statement(markup, url, NOW)

    def test_latest_official_link_by_title_date(self):
        markup = '<a href="' + BOK_URL + '"><span>통화정책방향(2026.8.27)</span></a><a href="https://example.org">통화정책방향(2026.9.1)</a>'
        self.assertEqual(latest_bok_statement_url(markup, NOW), BOK_URL)
        with self.assertRaises(ValueError): latest_bok_statement_url('<a href="' + BOK_URL + '">통화정책방향(2027.1.1)</a>', NOW)

    def test_korean_event_local_date_join(self):
        release = parse_bok_statement(BOK, BOK_URL, NOW)
        fact = {'datasetId':'official.bok-release','subjectKey':'release:bok','fetchedAt':'2026-09-13T00:00:00Z','payload':{'officialRelease':release}}
        event = {'startsAt':'2026-08-26T23:00:00Z','payload':{'sourceProvider':'BOK','country':'KR','meetingType':'monetaryPolicyDecision','officialSource':True}}
        result = calendar_release_information(event, {'facts':[fact]}, NOW)
        self.assertEqual(result['status'], 'released')
        fact['fetchedAt'] = '2026-08-26T23:00:00Z'
        self.assertEqual(calendar_release_information(event, {'facts':[fact]}, datetime(2026,8,26,23,tzinfo=timezone.utc))['status'], 'released')
        event['startsAt'] = '2026-10-15T00:00:00Z'
        self.assertIsNone(calendar_release_information(event, {'facts':[fact]}, NOW)['release'])


class BlsStatisticsTests(unittest.TestCase):
    def test_values_have_inputs_and_formulas_not_consensus(self):
        payload = bls_payload()
        payload['Results']['series'][0]['data'].append({'year':'2026','period':'M09','value':'-'})
        result = parse_bls_statistics(payload, NOW)
        self.assertEqual(result['indicators']['cpi']['metrics'][0]['actual'], 10)
        self.assertEqual(result['indicators']['employment']['metrics'][0]['actual'], 10000)
        self.assertEqual(result['indicators']['employment']['metrics'][1]['actual'], 4.1)
        self.assertFalse(result['originalReleaseVintage'])
        self.assertIsNone(result['indicators']['cpi']['metrics'][0]['consensus'])
        self.assertTrue(verified_bls_statistics(result, NOW))

    def test_missing_duplicate_nonfinite_future_are_errors(self):
        for case in ['missing','duplicate','nan','future']:
            payload = bls_payload()
            if case == 'missing': payload['Results']['series'].pop()
            if case == 'duplicate': payload['Results']['series'].append(copy.deepcopy(payload['Results']['series'][0]))
            if case == 'nan': payload['Results']['series'][0]['data'][0]['value'] = 'nan'
            if case == 'future': payload['Results']['series'][0]['data'][0]['year'] = '2027'
            with self.assertRaises(ValueError): parse_bls_statistics(payload, NOW)

    def test_tampered_calculation_rejected(self):
        result = parse_bls_statistics(bls_payload(), NOW)
        result['indicators']['cpi']['metrics'][0]['actual'] = 999
        self.assertIsNone(verified_bls_statistics(result, NOW))

    def test_latest_statistics_do_not_become_calendar_release(self):
        result = parse_bls_statistics(bls_payload(), NOW)
        fact = {'datasetId':'official.bls-statistics','subjectKey':'release:bls-statistics','fetchedAt':'2026-09-13T00:00:00Z','payload':{'officialStatistics':result}}
        event = {'startsAt':'2026-09-11T12:30:00Z','payload':{'officialSource':True,'country':'US','indicator':'cpi','sourceProvider':'BLS'}}
        info = calendar_release_information(event, {'facts':[fact]}, NOW)
        self.assertIsNone(info['release'])
        self.assertEqual(info['latestStatistics']['referencePeriod'], '2026-08')
        self.assertFalse(info['latestStatistics']['originalReleaseVintage'])
        self.assertEqual(info['latestStatistics']['freshnessState'], 'fresh')
        fact['fetchedAt'] = '2026-09-11T00:00:00Z'
        self.assertEqual(calendar_release_information(event, {'facts':[fact]}, NOW)['latestStatistics']['freshnessState'], 'stale')

    def test_new_datasets_have_independent_limits_and_no_investment_event(self):
        registry = default_external_dataset_registry({})
        self.assertLessEqual(registry.adapter('official.bls-statistics').descriptor.daily_request_budget, 25)
        for key in ['official.bok-release','official.bls-statistics']:
            result = ExternalFactTransitionService().assess(key, {'payload':{'x':1}}, {'x':2}, 'changed')
            self.assertFalse(result.material)


class DocumentRecoveryTests(unittest.TestCase):
    def test_exact_document_identity_and_existing_partition_key(self):
        item = disclosure()
        request = document_recovery_request(item)
        self.assertEqual(request.partition_key, '005930:20260827000001:body-v1')
        item.raw_payload.pop('receiptNo')
        self.assertEqual(document_recovery_request(item).partition_key, request.partition_key)
        item.url = item.url.replace('20260827000001','20260827000002')
        self.assertIsNone(document_recovery_request(item))
        item = disclosure()
        item.raw_payload['receiptNo'] = 'bad'
        self.assertIsNone(document_recovery_request(item))
        item.raw_payload['documentVerified'] = True
        self.assertIsNone(document_recovery_request(item))

    def test_sec_url_scope_and_no_credential_transport(self):
        item = disclosure('filing'); item.symbol = 'EXAMPLE'
        item.evidence_id = 'research:EXAMPLE:sec:0000123456-26-000001'
        item.raw_payload = {'accessionNumber':'0000123456-26-000001'}
        item.url = 'https://www.sec.gov/Archives/edgar/data/123456/000012345626000001/report.htm'
        self.assertEqual(document_recovery_request(item).watermark['cik'], '123456')
        item.raw_payload.pop('accessionNumber')
        self.assertEqual(document_recovery_request(item).watermark['accessionNumber'], '0000123456-26-000001')
        for url in [item.url.replace('www.sec.gov','example.org'), item.url + '?secret=x', item.url.replace('report.htm','../x')]:
            item.url = url
            self.assertIsNone(document_recovery_request(item))

    def test_recovery_queues_without_direct_fetch_and_respects_access(self):
        store = MagicMock(); store.retained_document_fact.return_value = None; store.enqueue_followups.return_value = 1
        registry = default_external_dataset_registry({})
        reader = MagicMock(return_value=[disclosure()])
        service = OfficialDocumentRecoveryService({}, reader, store, registry, MagicMock(), lambda _: True, now=lambda: NOW)
        self.assertEqual(service.run_once()['queuedCount'], 1)
        service.run_once(); self.assertEqual(store.enqueue_followups.call_count, 1)
        service.access_ready = lambda _: False
        self.assertEqual(service.run_once(force=True)['blockedCount'], 1)
        self.assertEqual(store.enqueue_followups.call_args.args[0], [])

    def test_existing_body_replayed_without_old_alert(self):
        store = MagicMock(); store.retained_document_fact.return_value = {'datasetId':'opendart.document'}; store.enqueue_followups.return_value = 0
        projector = MagicMock(); projector.project_fact.return_value = {'writtenCount':1}
        service = OfficialDocumentRecoveryService({}, lambda **kw:[disclosure()], store, default_external_dataset_registry({}), projector, lambda _:False, now=lambda: NOW)
        self.assertEqual(service.run_once()['restoredCount'], 1)
        projector.project_fact.assert_called_once_with(store.retained_document_fact.return_value, allow_alert=False)
        service.candidate_reader = lambda **kw:[disclosure(),disclosure()]
        self.assertEqual(service.run_once(force=True)['deferredCount'], 1)
        service.record_failure(ValueError('source mismatch'))
        self.assertEqual(service.run_once()['status'], 'error')

    def test_compact_publication_date_is_not_unknown_or_future(self):
        item = disclosure(); brief = build_information_brief(item, NOW)
        self.assertIsNotNone(brief['sourceAgeHours'])
        item.published_at = '20270101'
        self.assertEqual(build_information_brief(item, NOW)['state'], 'content-invalid')


class InformationPriceTests(unittest.TestCase):
    def test_raw_observation_and_no_causal_claim(self):
        before = quote('2026-09-12T11:57:00Z')
        after = quote('2026-09-12T13:03:00Z',102)
        result = price_observation('EXAMPLE','2026-09-12T12:00:00Z',60,before,after,NOW)
        self.assertEqual(result['priceChangePercent'], 2)
        self.assertEqual(result['targetDelayMinutes'], 3)

    def test_daily_late_baseline_and_mismatched_currency_are_missing(self):
        before = quote('2026-09-12T11:57:00Z'); after = quote('2026-09-12T13:00:00Z',102)
        for change in [{'observationGranularity':'1d'},{'generatedAt':'2026-09-12T14:00:00Z'},{'currency':'KRW'},{'dataQuality':'mock'},{'currentPrice':float('inf')}]:
            self.assertEqual(price_observation('EXAMPLE','2026-09-12T12:00:00Z',60,{**before,**change},after,NOW)['status'],'missing')

    def test_late_future_and_unknown_clocks(self):
        before = quote('2026-09-12T11:57:00Z')
        self.assertEqual(price_observation('EXAMPLE','2026-09-12T12:00:00Z',60,before,quote('2026-09-12T18:00:00Z'),NOW)['status'],'missing')
        self.assertEqual(price_observation('EXAMPLE','2026-09-13T12:00:00Z',60,{}, {},NOW)['status'],'pending')
        self.assertEqual(price_observation('EXAMPLE','2026-09-12',60,{}, {},NOW)['status'],'unknown-time')

    def test_read_service_does_not_register_followup_alerts(self):
        reader = MagicMock(); reader.load_baseline_observations.return_value = {}; reader.load_outcome_observations.return_value = {}
        result = InformationObservationService(reader, now=lambda:NOW).observe('2026-09-12T12:00:00Z',['EXAMPLE'])
        self.assertFalse(result['notificationRegistered']); self.assertFalse(result['decisionAuthority'])
        self.assertEqual(len(result['observations']), 2)
        reader.load_baseline_observations.assert_called_once()
        reader.load_outcome_observations.assert_called_once()

    def test_calendar_detail_is_not_limited_to_future_events(self):
        repository = MagicMock(); event = MagicMock()
        event.to_dict.return_value = {'eventId':'past','startsAt':'2026-07-01T00:00:00Z','payload':{}, 'symbols':[]}
        repository.get.return_value = event
        service = InvestmentCalendarService(repository, release_reader=lambda:{})
        self.assertEqual(service.get_event('past')['eventId'], 'past')
        repository.list.assert_not_called()


if __name__ == '__main__':
    unittest.main()
