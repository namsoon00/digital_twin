"""Business research contracts across source, graph, input and durable memory."""
import copy
import hashlib
import json
import os
import unittest
from unittest.mock import Mock

from ai_insight_fixtures import packet, plan
from digital_twin.modules.ai_orchestration.domain.business_research import (
    financial_metrics, validate_business, raw_business, thesis_case, evaluate_thesis, business_schema)
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
from digital_twin.modules.ai_orchestration.domain.insight_repair import correction_warranted
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, validate_execution_input
from digital_twin.modules.reasoning.domain.observation_evidence import select_evidence
from digital_twin.modules.reasoning.domain.observation_session import ObservationEvidenceSession
from digital_twin.modules.news_intelligence.domain.company_relationships import extract_relationships, resolve_listed_company, valid_assertions
from digital_twin.modules.news_intelligence.contracts import ResearchEvidence


def report(p, year=2025, value=100, **changes):
    observed = str(year + 1) + '-02-01T00:00:00Z'
    return {'id':'report-' + str(year), 'kind':'research-evidence', 'symbol':p['symbol'],
        'sourceWorldId':p['worldId'], 'sourceSnapshotId':p['sourceSnapshotId'],
        'historicalReport':True, 'frequency':'annual', 'periodEnd':str(year) + '-12-31',
        'reportObservationId':'official-' + str(year), 'reportedValues':{'revenue':value},
        'publishedAt':observed, 'observedAt':observed, 'sourceAsOf':observed,
        'metricProvenance':{'revenue':{'provider':'Official', 'currency':'USD', 'scope':'consolidated',
            'durationBasis':'annual', 'periodStart':str(year) + '-01-01',
            'sourceReferences':[{'datasetId':'official-source', 'revisionId':'r' + str(year),
                                 'subjectKey':p['symbol'], 'fetchedAt':observed}]}}, **changes}


def business_packet():
    p=packet(); p['capturedAt']='2026-03-01T00:00:00Z'
    p['facts'][0]['sourceAsOf']=p['capturedAt']
    p['facts'],p['coverage']=select_evidence(p['facts'] + [report(p)])
    return p


def thesis():
    return {'question':'매출 확대가 현금흐름 개선으로 연결될 수 있는가?',
        'mechanism':'수요 확대가 매출로 이어지고 현금 회수로 연결될 가능성을 확인합니다.',
        'assumption':'판매 확대가 대금 회수의 지연을 동반하지 않아야 합니다.',
        'alternative':'할인 판매와 외상 증가가 외형 성장만 만들었을 가능성이 있습니다.',
        'invalidation':'매출 확대 뒤 현금 회수의 악화가 이어지면 설명을 다시 검토합니다.',
        'evidenceIds':['report-2025'], 'horizonDays':540,
        'missingEvidence':['현금흐름과 매출채권의 공시 내역을 추가 확인해야 합니다.'],
        'checkpoints':[{'factId':'report-2025','metric':'revenue','direction':'increase','meaning':'매출 확대가 다음 연간 보고에서 이어지는지 확인합니다.'}]}


def raw_research():
    return {'theses':[thesis()], 'reviews':[], 'coverageNote':'공식 연간 매출을 확인했으나 현금 회수의 근거가 부족합니다.'}


def relationship_source(text='Our suppliers include Example Devices. Other material describes production.'):
    return ResearchEvidence(evidence_id='official-doc',symbol='TEST',kind='filing',source='SEC EDGAR',title='Annual report',summary='Annual report',
        url='https://www.sec.gov/Archives/edgar/data/123/000123260001/report.htm', published_at='2026-02-01',observed_at='2026-03-01T00:00:00Z',
        raw_payload={'documentVerified':True, 'officialDocumentText':text, 'documentIssuerIdentity':{'symbol':'TEST','cik':'123','accessionNumber':'000123260001','sourceUrl':'https://www.sec.gov/Archives/edgar/data/123/000123260001/report.htm','verification':'sec-discovered-issuer-document'}})


def resolver(name):
    return resolve_listed_company(name, [{'name':'Example Devices','symbol':'EXMP','market':'NASDAQ','assetType':'STOCK',
        'sourceUrl':'https://www.nasdaq.com/market-activity/stocks/exmp','fetchedAt':'2026-03-01T00:00:00Z'}])


class BusinessResearchTests(unittest.TestCase):
    def test_business_contract_has_metric_baselines_without_forced_price_predictions(self):
        p=business_packet(); raw={**plan(),'businessResearch':raw_research(),'followUpConditions':[],'observations':[]}
        raw['claimEvidence']['hypothesis']=[{'factId':'report-2025','field':'reportedValues.revenue','period':'current'}]
        raw['evidenceIds'].append('report-2025')
        validated=validate_plan(raw,p)
        self.assertNotIn('conditionValidation',validated)
        self.assertEqual(100,validated['businessResearch']['theses'][0]['checkpoints'][0]['baseline']['value'])
        result={**validated,'input':p,'observedAt':p['capturedAt']}
        self.assertEqual([],local_quality(result)['errors'])
        self.assertFalse(correction_warranted(result))
        for mutate in (lambda r:r['theses'][0].update(evidenceIds=['quote-1']),
                       lambda r:r['theses'][0]['checkpoints'][0].update(metric='invented'),
                       lambda r:r['theses'][0].update(horizonDays=90)):
            bad=raw_research();mutate(bad)
            with self.assertRaises(ValueError):validate_business(bad,p,[])
        self.assertEqual(raw_research(),raw_business(validated['businessResearch']))

    def test_future_report_comparison_preserves_misses_and_excludes_backfills_and_restatements(self):
        p=business_packet(); business=validate_business(raw_research(),p,[])
        case=thesis_case({**p,'taskId':'task'}, {'input':p,'executionInputId':'input'},business['theses'][0],p['capturedAt'])
        future=copy.deepcopy(p);future['capturedAt']='2027-03-01T00:00:00Z'
        future['facts'],future['coverage']=select_evidence([future['facts'][0],report(p,2026,90)])
        miss=evaluate_thesis(case,future)
        self.assertEqual('direction-not-observed',miss[0]['status'])
        self.assertEqual(-10,miss[0]['change'])
        case['observations']=miss
        future['facts'][-1]['reportedValues']['revenue']=150
        self.assertEqual(miss,evaluate_thesis(case,future))
        case['observations']=[]
        for changed in (report(p,2025,150), report(p,2026,150,publishedAt='2026-02-01'),
                        report(p,2026,150,publishedAt='2028-01-01')):
            future['facts'],future['coverage']=select_evidence([future['facts'][0],changed])
            self.assertEqual('awaiting-report',evaluate_thesis(case,future)[0]['status'])
        future['facts'],future['coverage']=select_evidence([future['facts'][0],report(p,2026,150)])
        future['facts'][-1]['metricProvenance']['revenue']['currency']='KRW'
        self.assertEqual('awaiting-report',evaluate_thesis(case,future)[0]['status'])
        future['capturedAt']='2029-01-01T00:00:00Z'
        self.assertEqual('expired-unobserved',evaluate_thesis(case,future)[0]['status'])

    def test_directed_reads_cannot_silently_drop_available_business_reports(self):
        from digital_twin.modules.ai_orchestration.application.retrieval import retrieve_evidence
        from test_ai_directed_retrieval import FINISH
        from digital_twin.modules.news_intelligence.domain.financial_reporting import bind_financial_report_contract, FINANCIAL_REPORTING_VERSION
        from digital_twin.modules.reasoning.domain.portfolio_ontology_company_concepts import add_company_knowledge_concepts
        from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
        source = bind_financial_report_contract({"period":"2025", "periodEnd":"2025-12-31", "frequency":"annual",
            "provider":"SEC EDGAR", "financialReportingVersion":FINANCIAL_REPORTING_VERSION, "revenue":100, "officialSource":True,
            "metricProvenance":{"revenue":{"provider":"SEC EDGAR", "period":"2025-12-31", "periodStart":"2025-01-01",
                "currency":"USD", "scope":"CFS", "durationBasis":"annual", "filed":"2026-02-01", "official":True}}},
            [{"datasetId":"sec.company_facts", "revisionId":"source", "subjectKey":"TEST", "fetchedAt":"2026-02-02T00:00:00Z"}])
        graph = PortfolioOntology("test")
        from digital_twin.modules.reasoning.domain.ontology_schema import add_entity
        from digital_twin.modules.reasoning.domain.projection_facts import graph_for_graph_store_persistence
        from digital_twin.modules.reasoning.domain.ontology_projection_input import compact_external_signals_for_ontology
        add_entity(graph,'stock','TEST','Test',{'symbol':'TEST'})
        source_signals=compact_external_signals_for_ontology({'companyKnowledge':{'TEST':{'financials':{'annual':[source]}}}},target_symbols=['TEST'])
        add_company_knowledge_concepts(graph,"stock:TEST","TEST",source_signals)
        graph=graph_for_graph_store_persistence(graph,{'inputRelationTypes':['HAS_PRICE']})
        projected = [dict(row.properties,id=row.entity_id,kind=row.kind) for row in graph.entities if row.properties.get("historicalReport")]
        self.assertTrue(projected)
        self.assertTrue(all(row['periodEnd']=='2025-12-31' for row in projected))
        self.assertTrue(financial_metrics({'symbol':'TEST','capturedAt':'2026-03-01T00:00:00Z','facts':projected}))
        p=business_packet(); session=ObservationEvidenceSession(p,p['facts'])
        selected,_,_,_=retrieve_evidence(session,p,[],[],lambda *_:FINISH,lambda _: 'input',262144)
        self.assertIn('report-2025',[row['id'] for row in selected['facts']])
        self.assertIn('report-2025',selected['businessEvidence']['factIds'])
        self.assertTrue(financial_metrics(selected))
        quote_only = session.select([])
        fallback,_,_,_=retrieve_evidence(session,quote_only,[],[],lambda *_:FINISH,lambda _: 'input',262144,max_rounds=0)
        self.assertTrue(financial_metrics(fallback))

    def test_business_input_and_readable_predecessor_replay_separately(self):
        from digital_twin.modules.ai_orchestration.domain.execution_input import READABLE_PROMPT_VERSION
        from digital_twin.modules.ai_orchestration.domain.observation_wording import readable_planning_prompt
        from digital_twin.modules.ai_orchestration.domain.research_request import continuous_planning_schema
        p=business_packet(); frozen=freeze_execution_input(p,[],[])
        validate_execution_input(frozen)
        self.assertIn('businessResearch',frozen['outputSchema']['required'])
        old=copy.deepcopy(frozen);old['promptVersion']=READABLE_PROMPT_VERSION
        old['prompt']=readable_planning_prompt(p,[],[]);old['promptHash']=hashlib.sha256(old['prompt'].encode()).hexdigest()
        old['outputSchema']=continuous_planning_schema(p,[])
        validate_execution_input(old)
        self.assertNotIn('businessResearch',old['outputSchema']['required'])

    def test_relationship_assertions_require_explicit_source_and_exact_entity_resolution(self):
        item=relationship_source(); extraction=extract_relationships(item,resolver,'2026-03-01T00:00:00Z')
        row=extraction['assertions'][0]
        self.assertEqual(('SUPPLIES_TO','inbound','EXMP'),(row['relationType'],row['direction'],row['counterparty']['symbol']))
        self.assertEqual('not-reconfirmed',row['currentness'])
        self.assertIsNone(row['exposure']['value'])
        item.raw_payload['companyRelationships']=extraction
        self.assertEqual([row],valid_assertions(item))
        altered=copy.deepcopy(item);altered.raw_payload['companyRelationships']['assertions'][0]['direction']='outbound'
        self.assertEqual([],valid_assertions(altered))
        altered=copy.deepcopy(item);altered.lifecycle_state='retracted'
        self.assertEqual([],valid_assertions(altered))
        item.raw_payload['officialDocumentText']='Source was corrected.'
        self.assertEqual([],valid_assertions(item))
        for text in ('Example Devices and TEST attended a meeting.', 'Our potential suppliers include Example Devices.',
                     'Our suppliers include Example Devices if the agreement is signed.', 'Our suppliers do not include Example Devices.'):
            self.assertFalse(extract_relationships(relationship_source(text),resolver,'2026-03-01T00:00:00Z')['assertions'])
        self.assertEqual('unresolved',resolver('Example Devices Subsidiary')['status'])
        item=relationship_source();item.raw_payload['documentVerified']=False
        self.assertEqual('source-unavailable',extract_relationships(item,resolver,'2026-03-01T00:00:00Z')['status'])

    def test_relationship_graph_keeps_public_provenance_and_unresolved_names_without_edges(self):
        from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
        from digital_twin.modules.reasoning.domain.ontology_schema import add_entity
        from digital_twin.modules.reasoning.domain.portfolio_ontology_relationship_concepts import add_company_relationship_concepts
        item=relationship_source(); item.raw_payload['companyRelationships']=extract_relationships(item,resolver,'2026-03-01T00:00:00Z')
        from digital_twin.modules.reasoning.domain.ontology_projection_input import compact_research_evidence_item
        from digital_twin.modules.news_intelligence.domain.investment_research import research_evidence_from_payload
        item=research_evidence_from_payload(compact_research_evidence_item(item.to_dict()))
        graph=PortfolioOntology('test')
        stock=add_entity(graph,'stock','TEST','TEST',{'symbol':'TEST','tboxClass':'Stock'})
        source=add_entity(graph,'research-evidence',item.evidence_id,'Report',{'symbol':'TEST','tboxClass':'ResearchEvidence'})
        add_company_relationship_concepts(graph,stock,source,item)
        from digital_twin.modules.reasoning.domain.projection_facts import graph_for_graph_store_persistence
        graph=graph_for_graph_store_persistence(graph,{'inputRelationTypes':['HAS_PRICE']})
        rel=next(row for row in graph.relations if row.relation_type=='SUPPLIES_TO')
        self.assertEqual('source-stated',rel.properties['assertionState'])
        self.assertFalse(rel.properties['investmentActionAuthority'])
        self.assertNotIn('accountId',rel.properties)
        candidate=next(row for row in graph.entities if row.kind=='company-relationship')
        self.assertEqual(item.url,candidate.properties['sourceUrl'])
        item.raw_payload['companyRelationships']=extract_relationships(item,lambda name:resolve_listed_company(name,[]),'2026-03-01T00:00:00Z')
        graph=PortfolioOntology('unresolved');add_company_relationship_concepts(graph,stock,source,item)
        self.assertFalse(any(row.relation_type=='SUPPLIES_TO' for row in graph.relations))
        self.assertTrue(any(row.kind=='company-relationship' for row in graph.entities))

    def test_business_review_requires_original_scope_and_explicit_replacement(self):
        p=business_packet(); b=validate_business(raw_research(),p,[])
        case=thesis_case({**p,'taskId':'t'}, {'input':p,'executionInputId':'i'}, b['theses'][0],p['capturedAt'])
        memory={**case,'reviewDue':True,'revision':1}
        empty={**raw_research(),'theses':[]}
        with self.assertRaises(ValueError):validate_business(empty,p,[memory])
        review={'thesisId':case['caseId'],'disposition':'retain','reason':'공식 실적 변화가 없어 원래 사업 가설을 유지합니다.','evidenceIds':['report-2025']}
        result=validate_business({**empty,'reviews':[review]},p,[memory])
        self.assertEqual(1,result['reviews'][0]['expectedRevision'])
        with self.assertRaises(ValueError):validate_business({**empty,'reviews':[review]},p,[{**memory,'accountId':'foreign'}])
        with self.assertRaises(ValueError):validate_business({**empty,'reviews':[{**review,'disposition':'revise'}]},p,[memory])
        from test_ai_control import AIControlTests
        subject={key:p[key] for key in ('accountId','symbol','worldId')}
        company_memory=Mock(return_value={'kind':'company-research-record','status':'available'})
        service,store,planner=AIControlTests().runner(subjects=lambda:[subject],evidence=Mock(return_value=copy.deepcopy(p)),
            brain_memory=lambda *_:[copy.deepcopy(memory)],company_memory=company_memory)
        store.claim.return_value.update(subject)
        raw={**plan(),'businessResearch':{**empty,'reviews':[review]},'followUpConditions':[],'observations':[]}
        raw['notification']['send']=False
        planner.return_value=raw
        self.assertEqual('completed',service.run_once()['status'])
        company_memory.assert_called_once_with(p['accountId'],p['symbol'],p['capturedAt'])
        frozen=planner.call_args.args[0]
        self.assertEqual('awaiting-report',frozen['current']['businessThesisMemory'][0]['observations'][0]['status'])
        self.assertEqual(case['contract'],frozen['current']['businessThesisMemory'][0]['contract'])
        self.assertTrue(any(row.get('kind')=='company-research-record' for row in frozen['researchResults']))


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class BusinessResearchStorageTests(unittest.TestCase):
    def test_business_registration_is_atomic_with_task_and_retains_exact_contract(self):
        from test_ai_brain_agenda import BrainAgendaStorageTests
        from digital_twin.modules.ai_orchestration.domain.planning import stamp
        fixture=BrainAgendaStorageTests();fixture.setUp()
        try:
            fixture.control.seed(fixture.subject);job=fixture.control.claim()
            p={**business_packet(), 'taskId':job['taskId']}
            raw={**plan(), 'businessResearch':raw_research(), 'followUpConditions':[], 'observations':[]}
            raw['notification']['send']=False
            envelope=freeze_execution_input(p,[],[])
            input_id=fixture.control.save_execution_input(job,envelope)
            call=fixture.control.begin_call('independent-observation',envelope['promptHash'],job['taskId'],input_id)
            fixture.control.finish_call(call)
            result={**validate_plan(raw,p,envelope['researchResults']), 'input':p,'executionInputId':input_id,'observedAt':stamp()}
            result['quality']=local_quality(result)
            fixture.control.outbox_writer=Mock(side_effect=RuntimeError('publication rolled back'))
            with self.assertRaises(RuntimeError):fixture.control.complete(job,result,[])
            self.assertEqual([],fixture.brain.status()['cases'])
            fixture.control.outbox_writer=None
            self.assertTrue(fixture.control.complete(job,result,[]))
            cases=fixture.brain.status()['cases'];self.assertEqual(1,len(cases))
            saved=cases[0]
            self.assertEqual(result['businessResearch']['theses'][0],saved['contract'])
            self.assertEqual(p['facts'][1:],saved['origin']['evidence'])
            memory=fixture.brain.memory(p['accountId'],p['symbol'],p['worldId'])
            self.assertEqual(saved['contract'],memory[0]['contract'])
            self.assertEqual([],fixture.brain.memory(p['accountId'],p['symbol'],'other-world'))
            frozen=freeze_execution_input(p,[],memory);validate_execution_input(frozen)
            self.assertEqual(saved['contract'],frozen['researchResults'][0]['contract'])
            self.assertFalse(fixture.control.complete(job,result,[]))
        finally:fixture.tearDown()

    def test_relationship_ledger_preserves_first_known_and_rolls_back_with_source(self):
        from digital_twin.infrastructure.settings import runtime_settings
        from mysql_fixtures import mysql_test_settings
        from digital_twin.modules.news_intelligence.infrastructure.mysql_research_evidence import MySQLResearchEvidenceStore
        from digital_twin.modules.news_intelligence.infrastructure.mysql_company_relationships import persist_relationship_assertions
        store=MySQLResearchEvidenceStore({**runtime_settings(),**mysql_test_settings()})
        item=relationship_source();item.raw_payload['companyRelationships']=extract_relationships(item,resolver,'2026-03-01T00:00:00Z')
        key=item.raw_payload['companyRelationships']['assertions'][0]['assertionId']
        try:
            with self.assertRaises(RuntimeError):
                with store.transaction() as c:
                    persist_relationship_assertions(c,item)
                    raise RuntimeError('source transaction failed')
            with store.connect() as c:
                self.assertIsNone(c.execute('SELECT assertion_id FROM company_relationship_assertions WHERE assertion_id=%s',(key,)).fetchone())
            with store.transaction() as c:persist_relationship_assertions(c,item)
            original=copy.deepcopy(item.raw_payload['companyRelationships']['assertions'])
            item.raw_payload['companyRelationships']=extract_relationships(item,resolver,'2026-04-01T00:00:00Z')
            with store.transaction() as c:persist_relationship_assertions(c,item)
            self.assertEqual(original,item.raw_payload['companyRelationships']['assertions'])
        finally:
            with store.transaction() as c:c.execute('DELETE FROM company_relationship_assertions WHERE assertion_id=%s',(key,))
