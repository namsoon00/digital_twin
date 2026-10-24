"""Real read/author contracts, exact knowledge retention and official originals."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
import os
import unittest
import zipfile
from unittest.mock import Mock, patch

from digital_twin.modules.ai_orchestration.domain import evidence_readiness, execution_lanes, bounded_retrieval, memory_reference, evidence_purpose, purpose_retrieval, compact_retrieval
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input,
)
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.research_knowledge.contracts import exact_knowledge_proposal_schema, validate_knowledge_proposals
from digital_twin.modules.reasoning.contracts import content_hash, ObservationEvidenceSession
from digital_twin.modules.reasoning.domain.observation_evidence import quote_clock_assessment
from digital_twin.modules.news_intelligence.contracts import NewsCollectionTarget, select_dart_question_filings
from digital_twin.infrastructure.official_question_research import collect_question_documents
from digital_twin.infrastructure.dart_question_research import document_members
from test_business_research import business_packet
from test_research_knowledge import context, registered, proposal
from test_scoped_knowledge_research import scoped_decision


def packet():
    value = evidence_readiness.annotate(execution_lanes.annotate(business_packet(), {"capability": execution_lanes.MARKET}))
    from digital_twin.modules.reasoning.domain.observation_evidence import select_evidence
    value['facts'][1]['kind'] = 'company-financial-state'
    value['facts'], value['coverage'] = select_evidence(value['facts'])
    value["memoryReadPolicy"] = memory_reference.VERSION
    return value


def archive(markup):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr('report.xml', markup)
    return stream.getvalue()


def owned_packet(subject, task_id=''):
    value = packet(); value.update(subject)
    value['sourceSnapshots'] = {value['worldId']: value['sourceSnapshotId']}
    for fact in value['facts']:
        fact.update(symbol=value['symbol'], sourceWorldId=value['worldId'], sourceSnapshotId=value['sourceSnapshotId'])
        if fact.get('accountId'): fact['accountId'] = value['accountId']
    if task_id: value['taskId'] = task_id
    return value


class BoundedEvidenceReadinessTests(unittest.TestCase):
    def test_price_only_finish_preserves_the_conservative_wait_and_raw_decision(self):
        from digital_twin.modules.ai_orchestration.domain.validation_diagnostic import task_failure
        value = packet()
        decide = Mock(return_value=scoped_decision('finish', ['quote-1']))
        final, _, _, trace = retrieve_working_evidence(
            ObservationEvidenceSession(value, value['facts']), value, [], [], decide,
            lambda env: 'price-only-read', 262144)
        self.assertEqual('deferred', final['retrieval']['status'])
        self.assertEqual('finish', trace[-1]['decision']['action'])
        self.assertEqual(1, decide.call_count)
        seen = {row['id']: row for row in value['facts']}
        self.assertTrue(bounded_retrieval.price_only_finish(trace[-1]['decision'], seen))
        self.assertFalse(bounded_retrieval.price_only_finish(scoped_decision('finish', ['quote-1', 'report-2025']), seen))
        old = {key: item for key, item in value.items() if key != 'evidenceReadCompletionPolicy'}
        self.assertNotEqual(execution_lanes.material_key(old, []), execution_lanes.material_key(value, []))
        self.assertEqual(execution_lanes.material_key(value, []), execution_lanes.material_key(
            {**value, 'dataRecovery': {'checkedAt': '2099-01-01T00:00:00Z'}}, []))
        reason, diagnostic = task_failure('evidence-retrieval', ValueError('research progress disposition changed'))
        self.assertEqual('validation:retrieval-receipt-disposition', reason)
        self.assertFalse(diagnostic['rawErrorRetained'])

    def test_controller_records_renewal_and_new_author_version(self):
        from test_ai_control import AIControlTests, SUBJECT
        from ai_insight_fixtures import plan
        value = owned_packet(SUBJECT)
        raw = plan(); raw['notification']['send'] = False
        raw.update(knowledgeResearch={'claims': [], 'coverageNote': '현재 자료의 범위와 남은 확인 항목을 보존합니다.'},
                   businessResearch={'theses': [], 'reviews': [], 'coverageNote': '새 사업 가설을 강제로 만들지 않습니다.'},
                   caseReviews=[], questionResolutions=[], questions=[])
        refresh = Mock(return_value={'status': 'requested', 'requested': True, 'authority': 'operational-source-renewal-only'})
        service, store, author = AIControlTests().runner(
            evidence=Mock(return_value=ObservationEvidenceSession(value, value['facts'])),
            read_planner=Mock(return_value={**scoped_decision('finish', ['quote-1', 'report-2025']), 'purpose': 'source-dated', 'blocker': 'none'}),
            knowledge_context=lambda p: context(p), source_refresher=refresh)
        service.settings.update(aiObservationLanePolicy=execution_lanes.VERSION, aiEvidenceReadPolicy=evidence_readiness.VERSION)
        store.claim.return_value['capability'] = execution_lanes.MARKET
        store.last_processing_outcome.return_value = {}
        author.return_value = raw
        outcome = service.run_once()
        self.assertEqual('completed', outcome['status'], outcome)
        result = store.complete.call_args.args[1]
        self.assertEqual(evidence_purpose.author_version(result['input']), result['executionPromptVersion'])
        self.assertNotEqual('data-wait', result['processingOutcome']['status'])
        self.assertEqual('requested', result['dataRecovery']['status'])
        self.assertEqual(compact_retrieval.VERSION, result['input']['retrieval']['version'])
        self.assertEqual(1, refresh.call_count)
        store.memory.return_value = [{**result, 'quality': {'status': 'accepted'}}]
        author.reset_mock(); service.read_planner.reset_mock()
        self.assertEqual('unchanged', service.run_once()['status'])
        self.assertEqual('requested', store.complete.call_args.args[1]['dataRecovery']['status'])
        author.assert_not_called(); service.read_planner.assert_not_called()

    def test_original_question_keeps_partial_answer_and_criteria_in_new_read_and_author(self):
        from question_assessment_checks import fixture, raw_assessment
        from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
        from digital_twin.modules.ai_orchestration.domain import question_evidence
        value, session, case, _ = fixture()
        value = evidence_readiness.annotate(execution_lanes.annotate(value, {'capability': 'observe'}))
        value['researchKnowledge'] = context(value)
        missing = ['같은 기간의 영업현금흐름 원문이 필요합니다.']
        scope = {'caseId': case['caseId'], 'status': 'waiting', 'selectedFactIds': ['official-document'], 'missingEvidence': missing}
        saved = []
        def save(env): validate_execution_input(env); saved.append(env); return 'input-'+str(len(saved))
        selected, history, memory, trace = retrieve_working_evidence(session, value, [], [case],
            Mock(return_value=scoped_decision('assess', ['quote-1', 'official-document'], [scope])), save, 262144)
        self.assertEqual('assessment-ready', selected['retrieval']['status'])
        frozen = freeze_execution_input(selected, history, memory, retrieval_trace=trace)
        validate_execution_input(frozen)
        result = validate_plan(raw_assessment(case, missing=missing), selected, memory, require_research=True, require_resolution=True)
        self.assertFalse(result['notification']['send'])
        self.assertEqual(missing, result['questionResolutions'][0]['missingRequirements'])
        progress = question_evidence.checkpoints(selected, trace)
        self.assertEqual(case['questionContract'], progress[0]['questionContract'])
        self.assertNotEqual('answered', result['caseReviews'][0]['action'])

    def test_market_finish_with_required_gaps_defers_without_paid_correction_or_author(self):
        value = packet()
        decide = Mock(return_value=scoped_decision('finish', ['quote-1'], missing=['신선한 원천 시세를 확보해야 합니다.']))
        saved = []
        def save(envelope):
            validate_execution_input(envelope); saved.append(envelope); return 'input-'+str(len(saved))
        selected, history, memory, trace = retrieve_working_evidence(
            ObservationEvidenceSession(value, value['facts']), value, [], [], decide, save, 262144)
        self.assertEqual('deferred', selected['retrieval']['status'])
        self.assertEqual(1, decide.call_count)
        self.assertFalse(trace[0]['correction'])
        self.assertEqual('defer', trace[0]['decision']['action'])
        self.assertEqual('finish', json.loads(trace[0]['response']['text'])['action'])
        self.assertEqual(bounded_retrieval.VERSION, saved[0]['promptVersion'])
        validate_execution_input(freeze_execution_input(selected, history, memory, retrieval_trace=trace))

    def test_schema_issues_exact_category_ids_report_metrics_and_memory_paths(self):
        from digital_twin.modules.ai_orchestration.application.read_tools import ObservationReadTools
        value = packet()
        tools = ObservationReadTools(ObservationEvidenceSession(value, value['facts']), [], [], indexed=True)
        ctx = {**tools.context(), **tools.issued_context(), 'executionLane': execution_lanes.MARKET,
               'catalog': tools.session.catalog(), 'reportIndex': tools.session.report_index(), 'questionIndex': []}
        schema = bounded_retrieval.schema(ctx)
        branches = schema['properties']['requests']['items']['anyOf']
        for branch in branches:
            props = branch['properties']; tool = props['tool']['enum'][0]
            if tool == 'read_fact':
                for identifier in props['factId']['enum']:
                    self.assertEqual(tools.known[identifier], props['category']['enum'][0])
            if tool == 'read_report':
                report = next(r for r in ctx['reportIndex'] if r['factId'] == props['factId']['enum'][0])
                self.assertEqual(report['metrics'], props['metrics']['items']['enum'])
        self.assertFalse(any(b['properties']['tool']['enum'] == ['recall_memory'] for b in branches))
        self.assertIn('read_report', {b['properties']['tool']['enum'][0] for b in branches})

    def test_new_frozen_author_repair_and_review_replay_and_reject_changed_schema(self):
        from judgment_quality_checks import check_purpose_quality
        check_purpose_quality(self)
        value = packet(); value['researchKnowledge'] = context(value)
        frozen = freeze_execution_input(value, [], [])
        self.assertEqual(evidence_readiness.PROMPT_VERSION, frozen['promptVersion'])
        self.assertEqual(content_hash(frozen), validate_execution_input(frozen))
        corrected = freeze_repair_input(frozen, {}, ['invalid'], 'initial')
        self.assertEqual(evidence_readiness.REPAIR_VERSION, corrected['promptVersion'])
        validate_execution_input(corrected)
        reviewed = freeze_review_input({'input': value, 'summary': '동결 자료의 원천 시점을 유지합니다.'})
        self.assertEqual(evidence_readiness.REVIEW_VERSION, reviewed['promptVersion'])
        validate_execution_input(reviewed)
        altered = deepcopy(frozen); altered['outputSchema']['properties']['knowledgeResearch']['properties']['claims']['maxItems'] = 3
        with self.assertRaises(ValueError): validate_execution_input(altered)

    def test_retain_schema_preserves_original_claim_while_revision_remains_explicit(self):
        value = business_packet(); claim = registered(value)
        value['capturedAt'] = '2026-03-03T00:00:00Z'
        captured = context(value, [claim])
        schema = exact_knowledge_proposal_schema(captured, [f['id'] for f in value['facts']])
        variants = schema['properties']['claims']['items']['anyOf']
        kept = next(v for v in variants if v['properties']['disposition']['enum'] == ['retain'])
        self.assertEqual([claim['mechanism']], kept['properties']['mechanism']['enum'])
        self.assertEqual([claim['claimId']], kept['properties']['claimId']['enum'])
        raw = proposal(claimId=claim['claimId'], disposition='retain')
        validate_knowledge_proposals(raw, captured, value, {f['id'] for f in value['facts']})
        raw['claims'][0]['mechanism'] = '문장을 바꾸어 가설의 설명 내용을 새로 작성했습니다.'
        self.assertNotIn(raw['claims'][0]['mechanism'], kept['properties']['mechanism']['enum'])
        with self.assertRaisesRegex(ValueError, 'retaining knowledge'):
            validate_knowledge_proposals(raw, captured, value, {f['id'] for f in value['facts']})
        from digital_twin.modules.ai_orchestration.domain.knowledge_research import validate_research
        checked = evidence_readiness.annotate(execution_lanes.annotate(value, {'capability': 'observe'}))
        checked['researchKnowledge'] = captured
        with self.assertRaises(ValueError) as rejected:
            validate_research(raw, checked)
        self.assertEqual('knowledge-retain-content-changed', rejected.exception.diagnostic['reasonCode'])
        raw['claims'][0]['disposition'] = 'revise'
        self.assertEqual('revise', validate_knowledge_proposals(raw, captured, value,
            {f['id'] for f in value['facts']})['claims'][0]['disposition'])

    def test_source_refresh_is_advisory_and_excludes_last_close_and_research(self):
        from digital_twin.infrastructure.transactions.ai_observation_source_refresh import AIObservationSourceRefresh
        value = packet(); quote = next(f for f in value['facts'] if f['kind'] == 'stock')
        quote.update(sourceAsOf='2026-02-28T22:00:00Z', maxAgeMinutes=10)
        value['quoteAssessment'] = quote_clock_assessment(value['facts'], value['capturedAt'])
        before = deepcopy(value)
        self.assertTrue(evidence_readiness.source_refresh_requirement(value))
        store = Mock()
        job = {key: value[key] for key in ('accountId', 'symbol', 'worldId')}
        job['taskId'] = 'periodic-monitor-wait'
        waiting = AIObservationSourceRefresh(store)(job, value)
        self.assertEqual('awaiting-periodic-collection', waiting['status'])
        self.assertEqual('periodic-monitor', waiting['collectionMode'])
        self.assertFalse(waiting['requested'])
        store.transaction.assert_not_called()
        self.assertEqual(before, value)
        value['quoteAssessment']['quotes'][0]['referenceState'] = 'last-close'
        self.assertIsNone(evidence_readiness.source_refresh_requirement(value))
        value = evidence_readiness.annotate(execution_lanes.annotate(before, {'capability': 'observe'}))
        self.assertIsNone(evidence_readiness.source_refresh_requirement(value))


class DartQuestionOriginalTests(unittest.TestCase):
    def test_question_provider_does_not_prefetch_unused_prefix_bodies(self):
        from digital_twin.infrastructure.investment_research_gateway import ExistingApiResearchGateway
        gateway = ExistingApiResearchGateway({'externalDartDocumentTextEnabled': '1', 'externalSecDocumentTextEnabled': '1'})
        with patch('digital_twin.infrastructure.investment_research_gateway.ExternalSignalProvider') as provider:
            gateway.provider_for({'official'}, question_directed=True)
            configured = provider.call_args.args[0]
        self.assertEqual('0', configured['externalDartDocumentTextEnabled'])
        self.assertEqual('0', configured['externalSecDocumentTextEnabled'])
        self.assertEqual('1', gateway.settings['externalDartDocumentTextEnabled'])

    def fixture(self):
        filings = [{'receiptNo': '20260814000123', 'receiptDate': '20260814', 'reportName': '반기보고서 (2026.06)'},
                   {'receiptNo': '20250814000123', 'receiptDate': '20250814', 'reportName': '반기보고서 (2025.06)'}]
        signals = {'dartDisclosures': {'000660': {'corpCode': '00164779', 'corpName': '테스트 반도체', 'items': filings}}}
        target = NewsCollectionTarget(symbol='000660', name='테스트 반도체', market='KR', currency='KRW')
        tasks = [{'taskId': 'original-question', 'question': '전년 동기 대비 매출과 영업현금흐름의 변화는 무엇인가?',
                  'queryTerms': ['매출', '영업현금흐름']}]
        markup = '<h2>연결 현금흐름표</h2><p>당반기와 전반기, 단위: 백만원</p><table><tr><th>항목</th><th>당반기</th><th>전반기</th></tr><tr><td>영업현금흐름 및 매출</td><td>100</td><td>90</td></tr><tr><td>본문에 기록된 동일 기간 비교와 현금 회수의 원천 수치를 확인합니다. 연결 보고 범위와 원문 표제는 해당 공시 본문에 기록된 값으로 보존하며 누계와 단일 기간의 차이를 확인한 후 비교합니다.</td><td>70</td><td>60</td></tr></table>'
        provider = Mock(); provider.settings = {'opendartApiKey': 'private-test-key'}
        provider.guarded_call.side_effect = lambda _name, _key, work: work()
        provider.fetch_bytes.return_value = archive(markup)
        return provider, target, signals, tasks

    def test_korean_question_reads_official_comparable_originals_with_table_headers(self):
        provider, target, signals, tasks = self.fixture()
        evidence, statuses = collect_question_documents(provider, target, signals, tasks)
        self.assertEqual(2, len(evidence))
        self.assertEqual(2, provider.fetch_bytes.call_count)
        provider.fetch_json.assert_not_called()
        for item in evidence:
            payload = item.raw_payload
            self.assertEqual('opendart-discovered-issuer-document', payload['documentIssuerIdentity']['verification'])
            self.assertIn('연결 현금흐름표', payload['documentPassages'][0]['quote'])
            self.assertIn('단위: 백만원', payload['documentPassages'][0]['quote'])
            self.assertIn('전반기', payload['documentPassages'][0]['quote'])
            self.assertNotIn('private-test-key', repr(payload))
            self.assertTrue(item.url.startswith('https://dart.fss.or.kr/dsaf001/main.do?rcpNo='))
        self.assertTrue(any(s['status'] == 'passages-collected' for s in statuses))

    def test_verified_original_projects_into_question_reads_without_waiving_governance(self):
        from digital_twin.modules.news_intelligence.domain.investment_evidence_governance import governed_evidence
        from digital_twin.modules.news_intelligence.contracts import research_document_reference
        from digital_twin.modules.news_intelligence.domain.research_document_reference import question_document_changed_symbols
        provider, target, signals, tasks = self.fixture()
        evidence, _ = collect_question_documents(provider, target, signals, tasks)
        accepted, _, rejected = governed_evidence(evidence, target, 999999,
            now=datetime.now(timezone.utc)+timedelta(seconds=1))
        self.assertEqual(2, len(accepted), [x.reasons for x in rejected])
        from digital_twin.modules.news_intelligence.infrastructure.research_material_reader import ResearchMaterialReader
        repository = Mock()
        repository.latest.side_effect = lambda **kw: accepted if kw['kind'] == 'disclosure' else []
        source_state = ResearchMaterialReader(repository)({'symbol': target.symbol,
            'researchRequest': {'sourceTypes': ['official-filing']},
            'questionContract': {'period': '2026-06-30'}})
        self.assertEqual(1, len(source_state['fingerprints']), 'dated originals must reach exact-period repeat admission')
        for item in accepted:
            ref = research_document_reference(item)
            self.assertTrue(ref)
            self.assertEqual(item.evidence_id, ref['evidenceId'])
            self.assertEqual('historical-period-comparison', ref['purpose'])
            self.assertEqual([target.symbol], question_document_changed_symbols([{'symbol': target.symbol, 'evidenceId': item.evidence_id}]))
            broken = deepcopy(item); broken.raw_payload['documentIssuerIdentity']['corpCode'] = 'unknown'
            self.assertEqual({}, research_document_reference(broken))
            broken = deepcopy(item); broken.raw_payload['evidenceGovernance']['investmentJudgmentEligible'] = False
            self.assertEqual({}, research_document_reference(broken))
        from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
        from digital_twin.modules.reasoning.domain.portfolio_ontology_research_concepts import add_research_evidence_concepts
        from digital_twin.modules.reasoning.domain.observation_evidence import select_evidence
        from digital_twin.modules.ai_orchestration.domain.research_evidence_use import usable_fact
        graph = PortfolioOntology('question-original-test')
        add_research_evidence_concepts(graph, 'stock-test', 'thesis-test', 'opinion-test', target.symbol, {},
                                      {'researchEvidence': {target.symbol: [item.to_dict() for item in accepted]}})
        value = owned_packet({'accountId': 'source-test', 'symbol': target.symbol, 'name': target.name, 'worldId': 'portfolio:test'})
        documents = [{**node.properties, 'id': node.entity_id, 'kind': node.kind,
                      'sourceWorldId': value['worldId'], 'sourceSnapshotId': value['sourceSnapshotId']}
                     for node in graph.entities if node.kind == 'research-evidence']
        self.assertEqual(2, len(documents))
        value['facts'], value['coverage'] = select_evidence([value['facts'][0], *documents])
        session = ObservationEvidenceSession(value, [value['facts'][0], *documents])
        index = session.question_document_index({item.evidence_id: ['original-question'] for item in accepted}, document_views=True)
        self.assertEqual(2, len(index))
        view = session.read_document(index[0]['factId'])['facts'][0]
        self.assertTrue(usable_fact(view, 'historicalDocumentReference.text'))
        self.assertFalse(usable_fact(view, 'currentPrice'))

    def test_missing_comparator_discovers_only_matching_issuer_and_rejects_future_receipts(self):
        provider, target, signals, tasks = self.fixture()
        prior = signals['dartDisclosures']['000660']['items'].pop()
        provider.fetch_json.return_value = {'status': '000', 'list': [
            {'corp_code': '00164779', 'rcept_no': prior['receiptNo'], 'rcept_dt': prior['receiptDate'], 'report_nm': prior['reportName']},
            {'corp_code': '99999999', 'rcept_no': '20260815000123', 'rcept_dt': '20260815', 'report_nm': '반기보고서 (2026.06)'},
            {'corp_code': '00164779', 'rcept_no': '20990814000123', 'rcept_dt': '20990814', 'report_nm': '반기보고서 (2099.06)'}]}
        evidence, _ = collect_question_documents(provider, target, signals, tasks)
        self.assertEqual(2, len(evidence))
        self.assertEqual(1, provider.fetch_json.call_count)
        self.assertNotIn('2099', repr([e.published_at for e in evidence]))
        self.assertNotIn('99999999', repr([e.raw_payload for e in evidence]))

    def test_html_error_or_api_error_never_becomes_verified_source_evidence(self):
        provider, target, signals, tasks = self.fixture()
        provider.fetch_bytes.return_value = b'<html>private-test-key access error</html>'
        evidence, statuses = collect_question_documents(provider, target, signals, tasks)
        self.assertEqual([], evidence)
        self.assertTrue(any(s.get('reasonCode') == 'invalid-document-archive' for s in statuses))
        self.assertNotIn('private-test-key', repr(statuses))
        provider.fetch_bytes.return_value = b'<result><status>800</status><message>maintenance</message></result>'
        provider.fetch_bytes.reset_mock()
        evidence, statuses = collect_question_documents(provider, target, signals, tasks)
        self.assertEqual([], evidence)
        self.assertEqual(1, provider.fetch_bytes.call_count)
        self.assertTrue(any(s.get('reasonCode') == 'dart-document-800' for s in statuses))

    def test_archive_expansion_and_member_count_are_bounded_before_read(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as zipped:
            for index in range(13): zipped.writestr(str(index)+'.xml', '<p>source</p>')
        with self.assertRaisesRegex(ValueError, 'document-size-budget'):
            document_members(stream.getvalue())
        provider, target, signals, tasks = self.fixture()
        signals['dartDisclosures']['000660']['corpCode'] = 'unverified'
        evidence, statuses = collect_question_documents(provider, target, signals, tasks)
        self.assertEqual([], evidence)
        provider.guarded_call.assert_not_called()
        self.assertEqual('issuer-identity-unavailable', statuses[0]['status'])


@unittest.skipUnless(os.environ.get('MYSQL_DATABASE') == 'orbit_alpha_test', 'isolated MySQL required')
class SourceRenewalStorageTests(unittest.TestCase):
    def test_company_value_read_author_and_review_persist_completed_call_proof(self):
        from test_company_value_observation import valuation_packet, value_plan
        from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_brain_agenda import MySQLBrainAgendaStore
        from digital_twin.infrastructure.transactions.ai_control_research_knowledge import AIControlResearchKnowledge
        from digital_twin.modules.ai_orchestration.domain import company_value
        from test_evidence_completion import active
        value = active(valuation_packet()); value.update(self.subject); value['taskId'] = self.job['taskId']
        value['sourceSnapshots'] = {value['worldId']: value['sourceSnapshotId']}
        for fact in value['facts']:
            fact.update(sourceWorldId=value['worldId'], sourceSnapshotId=value['sourceSnapshotId'])
            if fact.get('accountId'): fact['accountId'] = value['accountId']
        value = company_value.refresh(value)
        def save(env):
            identifier = self.store.save_execution_input(self.job, env)
            call = self.store.begin_call('independent-observation', env['promptHash'], self.job['taskId'], identifier)
            self.store.finish_call(call)
            return identifier
        selected, history, memory, trace = retrieve_working_evidence(
            ObservationEvidenceSession(value, value['facts']), value, [], [],
            Mock(return_value={**scoped_decision('finish', ['quote-1']), 'purpose':'source-dated','blocker':'none'}),
            save, 262144, record_round=lambda step: self.store.save_retrieval_round(self.job, step))
        with self.store.connect() as connection:
            self.assertEqual(trace, AIControlResearchKnowledge.read_proof(connection, self.job,
                {'input':selected, 'retrievalInputIds':[step['inputId'] for step in trace]}))
        env = freeze_execution_input(selected, history, memory, retrieval_trace=trace)
        result = {**validate_plan(value_plan(selected), selected), 'input':selected, 'executionInputId':save(env)}
        with self.store.connect() as connection:
            self.assertEqual(env, MySQLBrainAgendaStore.proof(connection, self.job, result))
        review = freeze_review_input(result)
        self.assertEqual(self.job['taskId'], self.store.review_proof(save(review))['taskId'])

    def test_purpose_read_author_and_review_keep_completed_call_proof(self):
        from digital_twin.infrastructure.transactions.ai_control_research_knowledge import AIControlResearchKnowledge
        value = evidence_purpose.annotate(owned_packet(self.subject, self.job['taskId']))
        def save(env):
            identifier = self.store.save_execution_input(self.job, env)
            call = self.store.begin_call('independent-observation', env['promptHash'], self.job['taskId'], identifier)
            self.store.finish_call(call)
            return identifier
        selected, history, memory, trace = retrieve_working_evidence(
            ObservationEvidenceSession(value, value['facts']), value, [], [],
            Mock(return_value={**scoped_decision('finish', ['quote-1','report-2025']), 'purpose':'source-dated','blocker':'none'}),
            save, 262144, record_round=lambda step:self.store.save_retrieval_round(self.job, step))
        with self.store.connect() as c:
            self.assertEqual(trace, AIControlResearchKnowledge.read_proof(c, self.job,
                {'input':selected,'retrievalInputIds':[step['inputId'] for step in trace]}))
        env = freeze_execution_input(selected, history, memory, retrieval_trace=trace)
        result = {'input':selected,'summary':'보고 시점을 보존한 관찰','executionInputId':save(env)}
        reviewed = freeze_review_input(result)
        self.assertEqual(self.job['taskId'], self.store.review_proof(save(reviewed))['taskId'])
        from compact_retrieval_checks import check_compact_storage
        check_compact_storage(self, value, save)
        from judgment_quality_checks import check_quality_storage
        check_quality_storage(self, value, save)

    def setUp(self):
        from test_ai_control import AIControlStorageTests, SUBJECT
        from digital_twin.infrastructure.mysql_monitoring import MySQLMonitorAccountJobStore
        from digital_twin.infrastructure.settings import load_local_env
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
        from mysql_fixtures import mysql_test_settings
        load_local_env()
        # Acquire the shared-schema lock before any initialization. Explicit
        # worker schemas stay independent of another session's full suite.
        configured = mysql_test_settings()
        database = configured['mysqlDatabase']
        if database != 'orbit_alpha_test' and not database.startswith('orbit_alpha_test_'):
            raise RuntimeError('isolated managed test database required')
        self.helper = AIControlStorageTests()
        self.store = MySQLAIControlStore({**configured, 'aiControlEnabled': 'true',
            'aiControlDailyTaskBudget': 10, 'aiControlDailyCallBudget': 20})
        self.helper.store = self.store; self.helper.clean()
        before = set(MySQLMonitorAccountJobStore._schema_ready)
        self.monitor = MySQLMonitorAccountJobStore(self.store.runtime_settings)
        added = MySQLMonitorAccountJobStore._schema_ready - before
        self.addCleanup(MySQLMonitorAccountJobStore._schema_ready.difference_update, added)
        self.store.runtime_settings['aiObservationLanePolicy'] = execution_lanes.VERSION
        self.store.runtime_settings['aiControlDailyCallBudget'] = 20
        self.subject = SUBJECT
        self.store.seed(SUBJECT); self.job = self.store.claim()
        self.value = owned_packet(SUBJECT, self.job['taskId'])
        self.value['quoteAssessment'] = {'quotes': [{'evidenceId': 'quote-1', 'sourceAsOf': '2026-01-01T00:00:00Z',
            'status': 'stale', 'maxAgeMinutes': 10, 'referenceState': ''}]}

    def tearDown(self):
        with self.store.transaction() as c:
            c.execute('DELETE FROM monitor_account_jobs WHERE account_id=%s', (self.subject['accountId'],))
        self.helper.tearDown()

    def test_coalesces_same_account_refresh_without_stealing_monitor_lease(self):
        from digital_twin.infrastructure.transactions.ai_observation_source_refresh import AIObservationSourceRefresh
        now = datetime.now(timezone.utc)
        refresh = AIObservationSourceRefresh(self.store, clock=lambda: now, account_queue_enabled=True)
        first = refresh(self.job, self.value)
        self.assertEqual('requested', first['status'], first)
        with self.store.transaction() as c:
            c.execute("UPDATE monitor_account_jobs SET status='processing',locked_by='live-monitor',locked_until='2099' WHERE account_id=%s", (self.subject['accountId'],))
        self.assertEqual('coalesced', refresh(self.job, self.value)['status'])
        refresh.clock = lambda: now + timedelta(minutes=6)
        # The AI lease is extended only in this isolated fixture.
        with self.store.transaction() as c:
            c.execute("UPDATE ai_control_tasks SET lease_until='2099' WHERE task_id=%s", (self.job['taskId'],))
        self.assertEqual('requested', refresh(self.job, self.value)['status'])
        with self.store.connect() as c:
            row = c.execute('SELECT status,locked_by,locked_until FROM monitor_account_jobs WHERE account_id=%s', (self.subject['accountId'],)).fetchone()
        self.assertEqual(('processing', 'live-monitor', '2099'), (row['status'], row['locked_by'], row['locked_until']))

    def test_lost_lease_and_rolled_back_refresh_do_not_commit_a_queue_request(self):
        from digital_twin.infrastructure.transactions.ai_observation_source_refresh import AIObservationSourceRefresh
        refresh = AIObservationSourceRefresh(self.store, account_queue_enabled=True)
        wrong = {**self.job, 'leaseToken': 'previous-worker'}
        self.assertEqual('lease-lost', refresh(wrong, self.value)['status'])
        with self.store.connect() as c:
            self.assertIsNone(c.execute('SELECT account_id FROM monitor_account_jobs WHERE account_id=%s', (self.subject['accountId'],)).fetchone())
        with patch.object(self.store, '_save_execution_state', side_effect=RuntimeError('private error')):
            failed = refresh(self.job, self.value)
        self.assertEqual('unavailable', failed['status'])
        self.assertNotIn('private error', repr(failed))
        with self.store.connect() as c:
            self.assertIsNone(c.execute('SELECT account_id FROM monitor_account_jobs WHERE account_id=%s', (self.subject['accountId'],)).fetchone())

    def test_native_retrieval_and_review_receipts_accept_only_new_frozen_versions(self):
        from digital_twin.infrastructure.transactions.ai_control_research_knowledge import AIControlResearchKnowledge
        # Use a clock-consistent packet; the renewal-specific fixture warning
        # above is deliberately a queue trigger rather than source evidence.
        value = owned_packet(self.subject, self.job['taskId'])
        def save(env):
            identifier = self.store.save_execution_input(self.job, env)
            call = self.store.begin_call('independent-observation', env['promptHash'], self.job['taskId'], identifier)
            self.store.finish_call(call)
            return identifier
        selected, history, memory, trace = retrieve_working_evidence(
            ObservationEvidenceSession(value, value['facts']), value, [], [],
            Mock(return_value=scoped_decision('finish', ['quote-1'], missing=['확인할 현재 시세가 부족합니다.'])),
            save, 262144, record_round=lambda step: self.store.save_retrieval_round(self.job, step))
        wait = {'input': selected, 'retrievalInputIds': [s['inputId'] for s in trace]}
        with self.store.connect() as c:
            self.assertEqual(trace, AIControlResearchKnowledge.read_proof(c, self.job, wait))
        price_value = deepcopy(value)
        price_value['capturedAt'] = (datetime.fromisoformat(value['capturedAt'].replace('Z', '+00:00'))
            + timedelta(seconds=1)).isoformat().replace('+00:00', 'Z')
        price_value['quoteAssessment'] = quote_clock_assessment(price_value['facts'], price_value['capturedAt'])
        selected, _, _, price_trace = retrieve_working_evidence(
            ObservationEvidenceSession(price_value, price_value['facts']), price_value, [], [],
            Mock(return_value=scoped_decision('finish', ['quote-1'])), save, 262144,
            record_round=lambda step: self.store.save_retrieval_round(self.job, step))
        price_wait = {'input': selected, 'retrievalInputIds': [step['inputId'] for step in price_trace]}
        self.assertEqual('deferred', selected['retrieval']['status'])
        with self.store.connect() as c:
            self.assertEqual(price_trace, AIControlResearchKnowledge.read_proof(c, self.job, price_wait))
        forged = deepcopy(price_wait)
        forged['input']['retrieval'] = bounded_retrieval.trace_summary(price_trace, 'ready')
        forged['input']['researchScope'] = bounded_retrieval.research_scope(price_trace, 'ready')
        with self.store.connect() as c:
            with self.assertRaisesRegex(ValueError, 'research progress disposition changed'):
                AIControlResearchKnowledge.read_proof(c, self.job, forged)
        author = freeze_execution_input(value, [], [])
        author_id = save(author)
        result = {'input': value, 'summary': '현재 동결 자료의 확인 범위를 보존합니다.', 'executionInputId': author_id}
        reviewed = freeze_review_input(result)
        review_id = save(reviewed)
        self.assertEqual(self.job['taskId'], self.store.review_proof(review_id)['taskId'])


if __name__ == '__main__': unittest.main()
