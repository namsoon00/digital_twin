"""Purpose changes admission, never source time, source authority or old audits."""
from copy import deepcopy
from datetime import datetime, timedelta
import unittest
from unittest.mock import Mock

from digital_twin.modules.ai_orchestration.domain import evidence_purpose as purpose, purpose_retrieval as retrieval
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession, EvidenceContractError, quote_clock_assessment
from digital_twin.modules.reasoning.domain.observation_evidence import select_evidence
from digital_twin.modules.ai_orchestration.domain.diagnostic_progress import progress_state, progress_fingerprint, should_notify
from digital_twin.modules.ai_orchestration.domain.retrieval import ReadRequestError
from test_bounded_evidence_readiness import packet, owned_packet
from test_scoped_knowledge_research import scoped_decision
from test_source_exploration import TARGET, TASK, HTML, URL, tools_for


def stale(value):
    value = deepcopy(value)
    now = datetime.fromisoformat(value['capturedAt'].replace('Z', '+00:00'))
    quote = next(row for row in value['facts'] if row['kind'] == 'stock')
    quote.update(sourceAsOf=(now-timedelta(minutes=60)).isoformat(), maxAgeMinutes=10)
    value['facts'], value['coverage'] = select_evidence(value['facts'])
    value['quoteAssessment'] = quote_clock_assessment(value['facts'], value['capturedAt'])
    return purpose.annotate(value)


class PurposeScopedEvidenceTests(unittest.TestCase):
    def test_dated_publication_does_not_require_quote_but_current_state_expires(self):
        from digital_twin.modules.ai_orchestration.domain.publication import publication_block
        from digital_twin.modules.notifications.application.ai_observation_message import render_ai_observation
        value = stale(packet()); value['analysisPurpose'] = 'source-dated'
        now = datetime.fromisoformat(value['capturedAt'].replace('Z','+00:00'))
        result = {'input':value, 'notification':{'send':True,'reason':'공시 기간의 새로운 설명'}, 'evidenceIds':['report-2025'],
            'observedAt':value['capturedAt'], 'summary':'보고서 기간 기준의 관찰', 'comparison':'기간을 구분합니다.',
            'claimEvidence':{}, 'questions':[], 'followUpConditions':[]}
        self.assertEqual('', publication_block(result, now))
        body = render_ai_observation(result)
        self.assertIn('각 근거에 표시된 시점', body)
        self.assertNotIn('현재가:', body)
        self.assertNotIn('시세 기준 시각 확인 필요', body)
        value['analysisPurpose'] = 'current-state'; result['evidenceIds'].append('quote-1')
        self.assertTrue(publication_block(result, now))
        # Capture freshness is not delivery freshness.
        quote = next(row for row in value['facts'] if row['kind'] == 'stock')
        quote.update(sourceAsOf=value['capturedAt'], judgementEvidenceUsable=True, freshnessReferenceState='')
        value['quoteAssessment'] = quote_clock_assessment(value['facts'], value['capturedAt'])
        result['input'] = purpose.annotate(value)
        self.assertTrue(publication_block(result, now+timedelta(minutes=11)))

    def test_dated_observation_reads_and_all_frozen_phases_preserve_old_source_clock(self):
        from compact_retrieval_checks import check_compact_routing
        check_compact_routing(self)
        value = stale(packet()); saved = []
        def save(env):
            validate_execution_input(env); saved.append(env); return 'input-'+str(len(saved))
        read = {**scoped_decision('finish', ['quote-1', 'report-2025']), 'purpose':'source-dated', 'blocker':'none'}
        result, history, memory, trace = retrieve_working_evidence(ObservationEvidenceSession(value, value['facts']),
            value, [], [], Mock(return_value=read), save, 262144)
        self.assertEqual('ready', result['retrieval']['status'])
        self.assertEqual('source-dated', result['analysisPurpose'])
        self.assertFalse(result['evidenceUses']['quotes'][0]['currentStateEligible'])
        self.assertTrue(result['evidenceUses']['quotes'][0]['datedReferenceEligible'])
        env = freeze_execution_input(result, history, memory, retrieval_trace=trace)
        for frozen in (env, freeze_repair_input(env, {}, ['검토 근거 확인'], 'parent'),
                       freeze_review_input({'input':result, 'summary':'원천 시점을 표시한 관찰'})):
            validate_execution_input(frozen)
        changed = deepcopy(env)
        changed['current']['evidenceUses']['quotes'][0]['currentStateEligible'] = True
        with self.assertRaises(EvidenceContractError): validate_execution_input(changed)
        current = {**read, 'purpose':'current-state'}
        with self.assertRaises(ReadRequestError):
            retrieval.validate_scope(current, saved[0]['retrievalContext'], {r['id']:r for r in value['facts']})
        contradictory = {**read, 'missingEvidence':['같은 기간의 원문이 필요합니다.']}
        parked = retrieval.validate_decision(contradictory)
        self.assertEqual(('defer','required-source'), (parked['action'],parked['blocker']))
        self.assertEqual('finish',contradictory['action'])

    def test_stale_ticks_skip_paid_read_and_research_plan_then_fresh_quote_resumes(self):
        from test_ai_control import AIControlTests, SUBJECT
        value = stale(owned_packet(SUBJECT))
        read = Mock(return_value={**scoped_decision('defer', ['quote-1'], missing=['현재 가격 비교에는 최신 시세가 필요합니다.']),
                                  'purpose':'current-state', 'blocker':'current-quote'})
        service, store, author = AIControlTests().runner(evidence=Mock(return_value=ObservationEvidenceSession(value, value['facts'])), read_planner=read)
        from digital_twin.modules.ai_orchestration.domain import evidence_readiness, execution_lanes
        service.settings.update(aiEvidenceReadPolicy=evidence_readiness.VERSION, aiObservationLanePolicy=execution_lanes.VERSION)
        store.claim.return_value['capability'] = execution_lanes.MARKET
        store.last_processing_outcome.return_value = {}
        self.assertEqual('awaiting-evidence', service.run_once()['status'])
        result, children = store.complete.call_args.args[1:]
        self.assertEqual('current-quote', result['processingOutcome']['reasonCode'])
        self.assertFalse(any(row['capability'] == 'research-plan' for row in children))
        store.last_processing_outcome.return_value = result['processingOutcome']
        quote = next(row for row in value['facts'] if row['kind'] == 'stock')
        quote['currentPrice'] += 1
        value['facts'], value['coverage'] = select_evidence(value['facts'])
        service.evidence.return_value = ObservationEvidenceSession(value, value['facts'])
        self.assertEqual('unchanged-wait', service.run_once()['status'])
        self.assertEqual(1, read.call_count); author.assert_not_called()
        # Source revision and readiness each wake the gate; prose and stale ticks do not.
        changed = deepcopy(value); changed['facts'][1]['label'] = '정정 보고서'
        changed['facts'], changed['coverage'] = select_evidence(changed['facts'])
        self.assertNotEqual(purpose.quote_wait_key(value, []), purpose.quote_wait_key(changed, []))
        next(row for row in value['facts'] if row['kind'] == 'stock')['sourceAsOf'] = value['capturedAt']
        value['quoteAssessment'] = quote_clock_assessment(value['facts'], value['capturedAt'])
        value = purpose.annotate(value)
        self.assertEqual('', purpose.quote_wait_key(value, []))
        service.evidence.return_value = ObservationEvidenceSession(value, value['facts'])
        service.run_once()
        self.assertGreater(read.call_count, 1)

    def test_wait_messages_ignore_rephrasing_and_intermediate_work_is_quiet(self):
        task = {'taskId':'child', 'updatedAt':'clock', 'status':'completed', 'result':{
            'processingOutcome':{'status':'data-wait', 'reasonCode':'current-quote', 'requirements':['시세 오래됨']}}}
        first = progress_state(task)
        task['result']['processingOutcome']['requirements'] = ['실시간 가격이 아직 필요함']
        second = progress_state(task)
        self.assertEqual(progress_fingerprint(first), progress_fingerprint(second))
        self.assertFalse(should_notify({'notifiedStates':[progress_fingerprint(first)]}, second))
        for stage in ('reviewing','validated'):
            self.assertFalse(should_notify({}, {'stage':stage}))
        self.assertTrue(should_notify({}, {'stage':'delivered','terminal':True}))


class PublicQuestionDocumentsTests(unittest.TestCase):
    def test_public_release_date_uses_article_clock_not_footer_revision(self):
        from digital_twin.modules.news_intelligence.infrastructure.source_document_tools import publication_metadata
        markup = '<p class="article__time">September 18, 2024</p><div id="lastUpdate">October 1, 2024</div>'
        date, proof, _ = publication_metadata(markup)
        self.assertTrue(date.startswith('2024-09-18'))
        self.assertEqual('September 18, 2024', proof['value'])
        date, proof, _ = publication_metadata(markup+'<p class="article__time">September 19, 2024</p>')
        self.assertEqual('',date)
        self.assertEqual('conflicting-publication-metadata',proof['kind'])

    def test_news_only_api_miss_can_explore_originals_without_changing_question(self):
        from digital_twin.modules.ai_orchestration.application.research import execute_research
        from digital_twin.modules.ai_orchestration.domain.research_request import validate_research_request
        from digital_twin.modules.news_intelligence.application.source_exploration_service import SourceExplorationService
        from digital_twin.modules.news_intelligence.application.investment_research_orchestration_service import InvestmentResearchOrchestrationService
        from digital_twin.modules.news_intelligence.application.hypothesis_research_planner_service import HypothesisResearchPlanningService
        from test_source_exploration import Explorer, Advisor, EvidenceStore, RunStore, Gateway
        evidence, runs, gateway, explorer = EvidenceStore(), RunStore(), Gateway([[]]), Explorer()
        service = InvestmentResearchOrchestrationService(evidence, gateway, research_store=runs,
            hypothesis_research_planner=HypothesisResearchPlanningService(Advisor()),
            source_exploration=SourceExplorationService(explorer, tools_for()),
            settings={'investmentBrainResearchCooldownMinutes':0})
        lookup = Mock(); lookup.get_run.return_value = None
        job = {'accountId':'private-fixture', 'symbol':'NVDA','name':'NVIDIA','taskId':'news-question',
            'question':TASK['question'], 'researchRequest':validate_research_request({
                'sourceTypes':['news'],'queryTerms':['data center revenue'],'maxAgeMinutes':1440},True)}
        result = execute_research(job, lookup, lambda:service)
        self.assertEqual(1,result['sourceExploration']['readCount'])
        self.assertEqual(job['question'],explorer.contexts[0]['tasks'][0]['question'])
        self.assertEqual(['news'],explorer.contexts[0]['tasks'][0]['sourceTypes'])

    def test_news_original_is_admitted_as_attributed_article_not_official_filing(self):
        url = 'https://www.reuters.com/technology/nvidia-fixture/'
        tools = tools_for(url=url)
        task = {**TASK, 'sourceTypes':['news']}
        result = tools.read(TARGET, {'url':url}, [task], 10)
        item = result['evidence']; raw = item.raw_payload
        self.assertEqual('news', item.kind)
        self.assertEqual('Reuters', item.source)
        self.assertFalse(raw['officialSource']); self.assertFalse(raw['valuationInputEligible'])
        self.assertNotEqual('ready', raw['validationState'])
        self.assertEqual('question-selected-passages', raw['readScope'])
        self.assertNotIn('officialDocumentText', raw)
        from digital_twin.modules.news_intelligence.domain.investment_evidence_governance import governed_evidence
        from digital_twin.modules.news_intelligence.domain.research_progress import evidence_packets
        # Article enrichment may call this a body read. The question owner must
        # still retain the narrower excerpt boundary, including its exact hash.
        raw['articleReadStatus'] = 'body'
        governed, accepted, rejected = governed_evidence([item], TARGET, 1440)
        source_packet = evidence_packets(governed, accepted+rejected)[0]
        self.assertNotIn('news-full-text', source_packet['evidenceTypes'])
        self.assertNotIn('official-filing', source_packet['evidenceTypes'])
        self.assertEqual('question-selected-passages', source_packet['readScope'])
        self.assertEqual(raw['sourceDocumentHash'], source_packet['sourceDocumentHash'])
        self.assertNotIn('evidence', tools.read(TARGET, {'url':url}, [TASK], 10))
        tools.fetch.return_value['body'] = HTML + b'<!-- changing transport metadata -->'
        same = tools.read(TARGET, {'url':url}, [task], 10)['evidence']
        self.assertNotEqual(raw['sourceDocumentHash'], same.raw_payload['sourceDocumentHash'])
        self.assertEqual(raw['sourceRevision'], same.raw_payload['sourceRevision'])
        # Revisions share article identity but carry a new immutable source hash.
        tools.fetch.return_value['body'] = HTML.replace(b'120', b'121')
        revised = tools.read(TARGET, {'url':url}, [task], 10)['evidence']
        self.assertEqual(item.evidence_id, revised.evidence_id)
        self.assertNotEqual(raw['sourceRevision'], revised.raw_payload['sourceRevision'])

    def test_official_context_requires_question_topic_and_cannot_claim_company_results(self):
        url = 'https://www.federalreserve.gov/newsevents/pressreleases/fixture.htm'
        body = HTML.replace(b'NVIDIA', b'Monetary policy').replace(b'Data Center revenue', b'Interest rates inflation')
        tools = tools_for(body, url)
        task = {**TASK, 'sourceTypes':['news'], 'queryTerms':['interest rates','inflation']}
        result = tools.read(TARGET, {'url':url}, [task], 10)
        self.assertEqual('market', result['evidence'].raw_payload['relationScope'])
        self.assertNotIn('officialDocumentText', result['evidence'].raw_payload)
        self.assertNotIn('evidence', tools.read(TARGET, {'url':url}, [{**task, 'queryTerms':['unrelated']}], 10))

    def test_undated_unknown_and_deceptive_redirect_sources_stay_reference_only(self):
        for url, body in [('https://reuters.com.evil.example/story',HTML),
                          ('https://www.reuters.com/story',HTML.replace(b'article:published_time',b'unknown-date'))]:
            result = tools_for(body,url).read(TARGET, {'url':URL}, [{**TASK,'sourceTypes':['news']}], 10)
            self.assertNotIn('evidence', result)
