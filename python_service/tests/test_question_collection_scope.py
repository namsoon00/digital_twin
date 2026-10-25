"""Original-period collection and finite recovery of an actually unsupported route."""
from copy import deepcopy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from digital_twin.modules.news_intelligence.domain.collection_scope import required_report_periods, collection_scope
from digital_twin.modules.news_intelligence.domain.collection_recovery import source_route_opportunity, DART_ROUTE
from digital_twin.modules.news_intelligence.domain.source_focus import source_requirements
from digital_twin.modules.ai_orchestration.domain import research_repeat as repeat, research_work as work
from digital_twin.modules.ai_orchestration.domain.planning import identity, stamp
from digital_twin.modules.ai_orchestration.domain.brain_management import later

CONTRACT = {"target": "현금흐름 비교", "period": "2025-06-30 종료 상반기와 2026-06-30 종료 상반기",
            "answerCriterion": "동일 연결 범위의 현금흐름과 운전자본을 공식 원문으로 비교"}
PERIODS = ["2025-06-30", "2026-06-30"]


def scoped_task():
    return {"taskId": "sources", "question": "최근 현금흐름의 변화는 무엇인가?", "queryTerms": ["영업현금흐름"],
            "sourceRequirements": source_requirements(CONTRACT, ["비교 기간 현금흐름표"])}


def recovery_fixture():
    from test_research_work import case, REQUEST, answer
    from digital_twin.modules.ai_orchestration.domain.research_request import research_intent_identity
    value = {**case(), "symbol": "000660", "questionContract": deepcopy(CONTRACT), "researchAttempts": 4,
             "researchRequest": deepcopy(REQUEST), "researchPlan": {"action": "blocked"},
             "latestSourceAnswer": answer("previous", "partial"),
             "questionAssessment": {"missingRequirements": ["비교 기간 현금흐름표"]},
             "researchSourceState": {"status": "ready", "fingerprints": ["same-body"]},
             "lastResearch": {"taskId": "previous", "requestedAt": later(stamp(), -400),
                 "requestFingerprint": research_intent_identity(REQUEST), "result": {"runId": "previous-run"}},
             "nextCheckAt": later(stamp(), 400)}
    reference = {**{k: value[k] for k in ("caseId", "accountId", "symbol", "questionContract")},
                 "runId": "previous-run", "taskId": identity("previous", "sources")}
    run = {**{k: reference[k] for k in ("accountId", "symbol", "runId")}, "status": "evidence-collected",
           "taskIds": [reference["taskId"]], "providerStatuses": [{"source": "official-question-research",
               "symbol": value["symbol"], "status": "unsupported-document-source"}]}
    return value, reference, run


class QuestionCollectionScopeTests(unittest.TestCase):
    def test_historical_original_reaches_cited_review_without_promoting_current_facts(self):
        from test_bounded_evidence_readiness import DartQuestionOriginalTests
        from test_question_research_progress import EvidenceStore, Gateway, RunStore, Advisor
        from digital_twin.infrastructure.official_question_research import collect_question_documents
        from digital_twin.modules.news_intelligence.application.hypothesis_research_planner_service import HypothesisResearchPlanningService
        from digital_twin.modules.news_intelligence.application.investment_research_orchestration_service import InvestmentResearchOrchestrationService
        from digital_twin.modules.decisions.contracts import InvestmentQuestion
        from digital_twin.modules.ai_orchestration.domain.documentary_answer import documentary_answer
        provider, target, signals, _ = DartQuestionOriginalTests().fixture()
        task = {**scoped_task(), 'status': 'blocked-by-data', 'sourceTypes': ['official-filing'], 'maxAgeMinutes': 60, 'requiredPeriodEnds': PERIODS,
                'requiredEvidenceTypes': ['official-document'], 'requiresDocumentBody': True}
        items, _ = collect_question_documents(provider, target, signals, [task])
        class PartialAdvisor(Advisor):
            def plan(self, context):
                result = super().plan(context)
                for review in result['taskReviews']:
                    review.update(status='partial', missingRequirements=['정정 여부 원문 확인'])
                return result
        advisor, gateway, store = PartialAdvisor(), Gateway(), EvidenceStore(items)
        service = InvestmentResearchOrchestrationService(store, gateway,
            hypothesis_research_planner=HypothesisResearchPlanningService(advisor), research_store=RunStore(),
            settings={'investmentBrainResearchCooldownMinutes': 0})
        result = service.run(InvestmentQuestion.create(task['question'], subject_symbol=target.symbol, source='ai-control'),
            target, {'documentaryReviewOnly': True, 'researchPlan': {'tasks': [task]}}).to_dict()
        answer = documentary_answer(result, task['taskId'])
        self.assertEqual('partial', answer['status'])
        self.assertEqual(2, len(answer['sources']))
        self.assertEqual(['정정 여부 원문 확인'], answer['missingRequirements'])
        self.assertFalse(any('오래된 일부 자료' in blocker for blocker in answer['blockers']))
        self.assertTrue(any(p.get('historicalOriginal') for c in advisor.contexts for p in c['researchProgress']['evidencePackets']))
        self.assertTrue(all(not item.raw_payload['evidenceGovernance']['investmentJudgmentEligible'] for item in store.saved))

    def test_old_original_is_only_admitted_for_its_explicit_historical_question(self):
        from datetime import datetime, timezone
        from test_bounded_evidence_readiness import DartQuestionOriginalTests
        from digital_twin.infrastructure.official_question_research import collect_question_documents
        from digital_twin.modules.news_intelligence.domain.investment_evidence_governance import governed_evidence
        from digital_twin.modules.news_intelligence.domain.question_document_packets import historical_packets
        from digital_twin.modules.news_intelligence.domain.research_progress import eligible_packets, assess_tasks
        from digital_twin.modules.news_intelligence.domain.research_document_reference import research_document_reference
        from digital_twin.infrastructure.hypothesis_research_planner_ai import hypothesis_research_planning_prompt
        provider, target, signals, _ = DartQuestionOriginalTests().fixture()
        task = {**scoped_task(), 'sourceTypes': ['official-filing'], 'maxAgeMinutes': 60, 'requiredPeriodEnds': PERIODS}
        items, _ = collect_question_documents(provider, target, signals, [task])
        cutoff = datetime(2028, 10, 24, tzinfo=timezone.utc)
        accepted, verified, rejected = governed_evidence(items, target, 60, now=cutoff)
        self.assertEqual([], accepted); self.assertEqual([], verified)
        packets = historical_packets(items, rejected, [task])
        self.assertEqual(2, len(packets))
        for item in items:
            self.assertFalse(item.raw_payload['evidenceGovernance']['investmentJudgmentEligible'])
            self.assertFalse(item.raw_payload['promptEvidenceAdmission']['promptEligible'])
            self.assertTrue(research_document_reference(item))
            for mutation in ('hash', 'conflict', 'non-age-rejection'):
                altered = deepcopy(item)
                if mutation == 'hash': altered.raw_payload['documentHash'] = 'invalid'
                if mutation == 'conflict': altered.raw_payload['evidenceGovernance']['conflictingEvidenceIds'] = ['conflict']
                if mutation == 'non-age-rejection': altered.raw_payload['evidenceGovernance']['reasons'].append('source-quality-gate-failed')
                self.assertFalse(research_document_reference(altered))
                self.assertEqual([], historical_packets([altered], rejected, [task]))
        allowed, denied = eligible_packets(task, packets, target.symbol, cutoff.isoformat())
        self.assertEqual(2, len(allowed)); self.assertEqual([], denied)
        assessment = assess_tasks([task], packets, target.symbol, cutoff.isoformat())[0]
        self.assertEqual('needs-review', assessment['status'], 'reading a dated original is not a completed answer')
        other = {**task, 'requiredPeriodEnds': ['2024-06-30'], 'sourceRequirements': {}}
        self.assertEqual([], eligible_packets(other, packets, target.symbol, cutoff.isoformat())[0])
        self.assertEqual([], eligible_packets(task, packets, 'OTHER', cutoff.isoformat())[0])
        self.assertEqual([], eligible_packets(task, packets, target.symbol, '2020-01-01T00:00:00Z')[0])
        prompt = hypothesis_research_planning_prompt({'researchProgress': {'evidencePackets': packets}})
        self.assertIn('현재 상황이나 투자 적합성이 검증됐다는 뜻은 아닙니다', prompt)

    def test_dart_xml_keeps_period_units_and_cells_with_their_own_statement(self):
        from digital_twin.infrastructure.dart_document_passages import document_passages
        source = '''<DOCUMENT><P>S&P의 평가 및 &lt;공시&gt; 문구 <신 설></P><SECTION-2><TITLE>연결재무제표</TITLE><TABLE-GROUP>
        <TITLE>연결 현금흐름표</TITLE><TABLE BORDER="0"><TBODY>
        <TR><TE>2026.01.01 부터 2026.06.30 까지</TE></TR><TR><TE>단위: 백만원</TE></TR></TBODY></TABLE>
        <TABLE BORDER="1"><THEAD><TR><TH>항목</TH><TH>당반기</TH><TH>전반기</TH></TR></THEAD><TBODY>
        <TR><TE>영업활동으로 인한 현금흐름</TE><TE>1,000</TE><TE>(500)</TE></TR>
        <TR><TE>유형자산의 취득</TE><TE>(200)</TE><TE>(100)</TE></TR></TBODY></TABLE></TABLE-GROUP></SECTION-2>
        <SECTION-2><TITLE>별도재무제표</TITLE><TABLE-GROUP><TITLE>별도 현금흐름표</TITLE>
        <TABLE BORDER="0"><TR><TE>2025.01.01 부터 2025.06.30 까지</TE></TR><TR><TE>단위: 천원</TE></TR></TABLE>
        <TABLE BORDER="1"><TR><TE>영업활동으로 인한 현금흐름</TE><TE>50</TE><TE>60</TE></TR></TABLE>
        </TABLE-GROUP></SECTION-2></DOCUMENT>'''
        tables = [b for b in document_passages(source) if ' | ' in b]
        self.assertEqual(2, len(tables))
        self.assertIn('영업활동으로 인한 현금흐름 | 1,000 | (500)', tables[0])
        self.assertIn('2026.01.01 부터 2026.06.30 까지', tables[0])
        self.assertIn('단위: 백만원', tables[0])
        self.assertIn('별도재무제표', tables[1]); self.assertIn('단위: 천원', tables[1])
        self.assertNotIn('연결', tables[1]); self.assertNotIn('2026.01.01', tables[1])
        self.assertNotIn('백만원', tables[1])

    def test_cash_question_prefers_exact_consolidated_rows_over_hedging_prose(self):
        from digital_twin.modules.news_intelligence.domain.question_passages import select_question_passages, question_terms
        task = scoped_task(); task['question'] = '연결 영업현금과 운전자본, 설비투자를 비교해 주세요.'
        table = ('연결 현금흐름표\n2026.01.01 부터 2026.06.30 까지 (단위: 백만원)\n항목 | 당반기 | 전반기\n'
                 '영업활동으로 인한 현금흐름 | 1,000 | (500)\n유형자산의 취득 | (200) | (100)')
        other = table.replace('연결', '별도')
        hedge = ('연결실체의 현금흐름위험회피와 신용손상은 현금흐름 추정에 따라 판단합니다. ' * 15)
        selected = select_question_passages([hedge, other, table], [task], 500)
        self.assertEqual(table, selected[0]['quote'])
        self.assertIn('영업활동', question_terms([task])); self.assertIn('매출채권', question_terms([task]))
        self.assertIn('(200)', selected[0]['quote'], 'keep signs, whole rows and original units')
        policy = {'question': '현금흐름위험회피와 신용손상 판단 정책은?', 'queryTerms': []}
        self.assertEqual(hedge, select_question_passages([table, hedge], [policy], 2000)[0]['quote'])

    def test_explicit_original_periods_reach_new_source_work_and_preserve_checkpoint(self):
        from digital_twin.modules.ai_orchestration.application.research import execute_research
        from digital_twin.modules.ai_orchestration.domain.research_request import validate_research_request
        from test_research_work import REQUEST
        value, _, _ = recovery_fixture()
        job = {**value, "taskId": "new-research", "name": "Test",
               "researchRequest": validate_research_request(REQUEST, True),
               "knownSourceAnswer": value["latestSourceAnswer"], "evidenceRequirements": ["비교 기간 현금흐름표"]}
        owner = Mock(); owner.run.return_value = SimpleNamespace(run_id="run", to_dict=lambda: {"status": "completed"})
        store = Mock(); store.get_run.return_value = None
        execute_research(job, store, lambda: owner)
        task = owner.run.call_args.args[2]["researchPlan"]["tasks"][0]
        self.assertEqual(PERIODS, task["requiredPeriodEnds"])
        self.assertEqual(CONTRACT, task["sourceRequirements"]["originalContract"])
        self.assertEqual(["비교 기간 현금흐름표"], task["sourceFocus"]["missingRequirements"])
        # An interrupted legacy exploration keeps its captured task identity.
        legacy = {**task}; legacy.pop("requiredPeriodEnds"); legacy.pop("sourceRequirements")
        store.get_run.return_value = {"status": "research-cooldown", "executedPlan": {
            "tasks": [legacy], "sourceExploration": {"version": "question-source-exploration-v1"}}}
        execute_research(job, store, lambda: owner)
        resumed = owner.run.call_args.args[2]["researchPlan"]["tasks"][0]
        self.assertNotIn("requiredPeriodEnds", resumed)
        self.assertNotIn("sourceRequirements", resumed)

    def test_dart_reads_requested_periods_and_reports_uncollected_periods(self):
        from test_bounded_evidence_readiness import DartQuestionOriginalTests
        from digital_twin.infrastructure.official_question_research import collect_question_documents
        provider, target, signals, _ = DartQuestionOriginalTests().fixture()
        filings = signals["dartDisclosures"][target.symbol]["items"]
        filings.insert(0, {"receiptNo": "20260814000124", "receiptDate": "20260814", "reportName": "분기보고서 (2026.03)"})
        signals["dartDisclosures"][target.symbol]["items"] = filings[:2]
        provider.fetch_json.return_value = {"status": "000", "list": [
            {"corp_code": "00164779", "rcept_no": "20250814000123", "rcept_dt": "20250814", "report_nm": "반기보고서 (2025.06)"}]}
        task = scoped_task()
        evidence, statuses = collect_question_documents(provider, target, signals, [task])
        self.assertEqual(set(PERIODS), {r.raw_payload["reportPeriodEnd"] for r in evidence})
        self.assertEqual(1, provider.fetch_json.call_count)
        self.assertEqual(2, provider.fetch_bytes.call_count)
        self.assertEqual([], statuses[-1]["collectionScope"]["uncollectedPeriodEnds"])
        self.assertEqual("requires-independent-review", statuses[-1]["collectionScope"]["answerCompletion"])
        provider.fetch_bytes.side_effect = lambda url, *_: (_ for _ in ()).throw(TimeoutError()) if "20250814" in url else provider.fetch_bytes.return_value
        _, statuses = collect_question_documents(provider, target, signals, [task])
        self.assertEqual(["2025-06-30"], statuses[-1]["collectionScope"]["uncollectedPeriodEnds"])

    def test_sec_exact_periods_precede_amendments_and_future_reports_are_excluded(self):
        from digital_twin.modules.news_intelligence.domain.question_passages import select_question_filings
        rows = [{"accessionNumber": str(i), "form": form, "reportDate": period, "filingDate": filed}
                for i, (form, period, filed) in enumerate([
                    ("10-Q", "2026-09-30", "2026-10-10"), ("10-Q/A", "2026-06-30", "2026-09-01"),
                    ("10-Q", "2026-06-30", "2026-08-01"), ("10-Q", "2025-06-30", "2025-08-01"),
                    ("10-Q/A", "2025-06-30", "2026-11-01")])]
        selected = select_question_filings(rows, [scoped_task()], "2026-10-24T00:00:00Z")
        self.assertEqual(set(PERIODS), {row["reportDate"] for row in selected[:2]})
        self.assertFalse(any(row["filingDate"] > "2026-10-24" for row in selected))
        self.assertNotIn("2026-09-30", {row["reportDate"] for row in selected})
        task = scoped_task(); task["sourceRequirements"]["originalContract"]["period"] = "2025-01-01부터 2025-06-30까지"
        self.assertEqual(["2025-06-30"], required_report_periods([task]))
        task["sourceRequirements"]["originalContract"]["period"] = "2026-01-26 to 2026-07-26"
        self.assertEqual(["2026-07-26"], required_report_periods([task]))
        task["sourceRequirements"]["originalContract"]["period"] = (
            "2026-07-01~2026-09-30 및 2026-01-01~2026-09-30, 비교기간 "
            "2026-04-01~2026-06-30·2025-07-01~2025-09-30·2025-01-01~2025-09-30, "
            "정정 여부는 2026-10-07 이후")
        self.assertEqual(["2025-09-30", "2026-06-30", "2026-09-30"], required_report_periods([task]))
        task["sourceRequirements"]["originalContract"]["period"] = "2025-07-27 및 2026-07-26"
        self.assertEqual(["2025-07-27", "2026-07-26"], required_report_periods([task]))
        self.assertEqual(PERIODS, collection_scope([scoped_task()], [], [])['unselectedPeriodEnds'])

    def test_sec_history_window_includes_original_comparison_not_latest_filing_only(self):
        from digital_twin.infrastructure.question_filing_history import collect_filing_history
        from unittest.mock import patch
        provider = Mock(); provider.guarded_call.side_effect = lambda _p, _k, f: f()
        provider.fetch_json.return_value = {"cik": "1045810"}
        filings = [{"filingDate": "2026-08-01", "reportDate": "2026-06-30", "url": "https://www.sec.gov/example"}]
        with patch('digital_twin.infrastructure.question_filing_history.build_filing_history', return_value={}) as build:
            collect_filing_history(provider, {"cik": "1045810"}, "NVDA", [scoped_task()], filings)
        self.assertEqual("2025-06-30", build.call_args.args[3])
        self.assertEqual(PERIODS, build.call_args.kwargs["report_periods"])
        provider.fetch_json.assert_called_once()
        from filing_history_checks import submissions, CIK, SYMBOL, DATE
        from digital_twin.modules.news_intelligence.domain.filing_history import build_filing_history, verified_filing_history
        payload = submissions(); recent = payload["filings"]["recent"]
        for index in range(30):
            for key, field in {"form": "8-K", "filingDate": "2026-10-01", "reportDate": "2026-09-30",
                               "accessionNumber": f"0001045810-26-{index+100:06d}", "primaryDocument": "event.htm"}.items():
                recent[key].append(field)
        receipt = build_filing_history(payload, SYMBOL, CIK, "2026-04-26", DATE, report_periods=["2026-04-26", "2026-07-26"])
        self.assertEqual({"2026-04-26", "2026-07-26"}, {row["reportDate"] for row in receipt["rows"][:2]})
        self.assertEqual(24, len(receipt["rows"]))
        self.assertFalse(receipt["complete"], "retaining requested reports does not prove unbounded correction coverage")
        self.assertEqual(receipt, verified_filing_history(receipt, SYMBOL, CIK))

    def test_recovery_requires_owned_unsupported_receipt_and_configured_route(self):
        value, reference, run = recovery_fixture()
        opportunity = source_route_opportunity(reference, run, [DART_ROUTE])
        self.assertEqual("collection-retry-only", opportunity["authority"])
        self.assertEqual("unverified", opportunity["sourceAvailability"])
        self.assertEqual({}, source_route_opportunity(reference, run, []))
        for changed in ({**run, "accountId": "foreign"}, {**run, "taskIds": ["other"]},
                        {**run, "status": "processing"}, {**run, "providerStatuses": []},
                        {**run, "providerStatuses": [{**run["providerStatuses"][0], "version": "dart-question-original-v1"}]}):
            self.assertEqual({}, source_route_opportunity(reference, changed, [DART_ROUTE]))
        changed = {**run, "runId": "later-run"}
        self.assertEqual(opportunity["key"], source_route_opportunity({**reference, "runId": "later-run"}, changed, [DART_ROUTE])["key"],
                         "a new receipt cannot reset this route's allowance")

    def test_one_route_replan_preserves_limits_partial_answer_and_original_question(self):
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_brain_agenda import MySQLBrainAgendaStore
        value, reference, run = recovery_fixture()
        original = deepcopy(value["latestSourceAnswer"])
        repeat.initialize(value)
        self.assertFalse(repeat.planning_due(value, stamp()))
        opportunity = source_route_opportunity(reference, run, [DART_ROUTE])
        value["researchSourceState"]["sourceRouteOpportunity"] = opportunity
        self.assertTrue(repeat.planning_due(value, stamp()))
        recent = deepcopy(value); recent["lastResearch"]["requestedAt"] = stamp()
        self.assertFalse(repeat.planning_due(recent, stamp()))
        capped = deepcopy(value); capped["researchAttempts"] = 6
        self.assertFalse(repeat.planning_due(capped, stamp()))
        remembered = repeat.planning_memory(value)
        self.assertEqual(opportunity, remembered["sourceRouteOpportunity"])
        self.assertNotIn("researchSourceState", remembered)
        repeat.remember(value, "planned")
        self.assertFalse(repeat.planning_due(value, later(stamp(), 1000)))
        value["researchPlan"] = {"action": "research", "sourceRouteOpportunityKey": opportunity["key"]}
        connection = Mock(); connection.execute.return_value.fetchone.return_value = None
        from digital_twin.modules.ai_orchestration.domain.research_request import research_intent_identity
        intent = research_intent_identity(value["researchRequest"])
        value["researchIntentAttempts"] = {intent: 1}
        capped = deepcopy(value); capped["researchIntentAttempts"][intent] = 2
        denied = []
        MySQLBrainAgendaStore.schedule_research(connection, capped, {**capped, "taskId": "capped", "name": "Test"},
                                                {"researchBasis": "basis"}, denied, stamp())
        self.assertEqual([], denied, "a new route cannot reset the per-intent ceiling")
        self.assertEqual("blocked", capped["status"])
        children = []
        job = {**value, "taskId": "planner", "name": "Test"}
        MySQLBrainAgendaStore.schedule_research(connection, value, job, {"researchBasis": "basis"}, children, stamp())
        self.assertEqual(1, len(children))
        self.assertEqual(5, value["researchAttempts"])
        self.assertEqual(CONTRACT, children[0]["questionContract"])
        self.assertEqual(["비교 기간 현금흐름표"], children[0]["evidenceRequirements"])
        self.assertEqual(original, value["latestSourceAnswer"])
        MySQLBrainAgendaStore.schedule_research(connection, value, job, {"researchBasis": "changed-prose"}, children, later(stamp(), 1000))
        self.assertEqual(1, len(children), "timers, wording and deployment cannot buy another retry")

    def test_route_reader_caches_only_exact_scope_and_never_changes_material_fingerprints(self):
        from digital_twin.modules.ai_orchestration.infrastructure.research_route_reader import ResearchRouteReader
        from digital_twin.modules.news_intelligence.infrastructure.research_material_reader import ResearchMaterialReader
        value, _, run = recovery_fixture()
        runs = Mock(return_value=run)
        routes = ResearchRouteReader(runs, [DART_ROUTE])
        repo = Mock(); repo.latest.return_value = []
        reader = ResearchMaterialReader(repo, route_reader=routes)
        state = reader(value)
        self.assertEqual([], state["fingerprints"])
        self.assertIn("sourceRouteOpportunity", state)
        self.assertEqual(state, reader(value)); self.assertEqual(1, runs.call_count)
        self.assertNotIn("sourceRouteOpportunity", reader({**value, "accountId": "foreign"}))
        self.assertEqual(2, runs.call_count)
        self.assertNotIn("sourceRouteOpportunity", value["researchSourceState"])


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class QuestionCollectionStorageTests(unittest.TestCase):
    def test_route_recovery_commits_once_with_lease_and_rolls_back_with_source_enqueue(self):
        from test_ai_brain_agenda import BrainAgendaStorageTests
        from test_research_work import answer, plan, REQUEST
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_research_work import reconcile_and_schedule
        fixture = BrainAgendaStorageTests(); fixture.setUp()
        try:
            job, result, children = fixture.observation()
            fixture.control.complete(job, result, children)
            source = fixture.control.claim()
            partial = answer(source["taskId"], "partial")
            fixture.control.complete(source, {"status": "completed", "documentaryAnswer": partial}, [])
            value = next(row for row in fixture.brain.status()["cases"] if row["caseId"] == source["brainCaseId"])
            value.update(questionContract=CONTRACT, researchAttempts=4, researchRequest=REQUEST,
                         nextCheckAt=later(stamp(), 1440), researchPlan={"action": "blocked"},
                         researchSourceState={"status": "ready", "fingerprints": ["same-body"]})
            value["lastResearch"]["requestedAt"] = later(stamp(), -400)
            value.pop("researchRepeat", None); repeat.initialize(value)
            with fixture.brain.transaction() as c: fixture.brain.save(c, value, "fixture", "legacy-source-blocked")
            _, reference, run = recovery_fixture()
            opportunity = source_route_opportunity(reference, run, [DART_ROUTE])
            # The reader's ownership proof is independently covered above.
            fixture.brain.research_route_reader = Mock(return_value=opportunity)
            fixture.brain.research_source_reader = Mock(return_value={
                "status": "ready", "fingerprints": ["same-body"], "sourceRouteOpportunity": opportunity})
            with fixture.brain.transaction() as c:
                self.assertEqual(1, reconcile_and_schedule(fixture.brain, c, [fixture.subject])["plansScheduled"])
                self.assertEqual(0, reconcile_and_schedule(fixture.brain, c, [fixture.subject])["plansScheduled"])
            task = fixture.control.claim()
            if task["capability"] == "observe":
                fixture.control.complete(task, {"status": "unchanged"}, []); task = fixture.control.claim()
            self.assertEqual("research-plan", task["capability"])
            values = [x for x in fixture.brain.memory(*[task[k] for k in ("accountId", "symbol", "worldId")], research_only=True)
                      if x.get("kind") == "brain-case"]
            for x in values: x["planDue"] = repeat.planning_due(x, stamp())
            envelope = work.freeze(task, [repeat.planning_memory(x) for x in values])
            self.assertEqual(opportunity, envelope["researchCases"][0]["sourceRouteOpportunity"])
            input_id = fixture.control.save_execution_input(task, envelope)
            call = fixture.control.begin_call("research-planning", envelope["promptHash"], task["taskId"], input_id)
            fixture.control.finish_call(call)
            raw = plan(values[0], "research"); raw["reviews"][0]["contract"] = CONTRACT
            completed = {"status": "research-planned", "executionInputId": input_id,
                         "researchPlan": work.validate_plan(raw, envelope)}
            self.assertFalse(fixture.control.complete({**task, "leaseToken": "lost"}, completed, []))

            def stored_case_and_events():
                # status() adds a new workflow.checkedAt on every read. Verify
                # rollback against durable state and the full event history.
                with fixture.brain.connect() as connection:
                    case = fixture.brain.read(connection, source["brainCaseId"], source["accountId"], source["symbol"])
                    events = connection.execute("SELECT payload_json FROM ai_brain_case_events WHERE case_id=%s ORDER BY event_id",
                                                (source["brainCaseId"],)).fetchall()
                    return {"case": case, "events": events}

            before = stored_case_and_events()
            with patch.object(fixture.brain, "save", side_effect=RuntimeError("rollback after source enqueue")):
                with self.assertRaises(RuntimeError): fixture.control.complete(task, completed, [])
            self.assertEqual(before, stored_case_and_events())
            self.assertTrue(fixture.control.complete(task, completed, []))
            self.assertFalse(fixture.control.complete(task, completed, []))
            queued = fixture.control.claim()
            self.assertEqual("research", queued["capability"])
            self.assertEqual(CONTRACT, queued["questionContract"])
            admission = fixture.brain.research_admission(queued)
            self.assertTrue(admission["allowed"])
            self.assertEqual(partial, admission["knownSourceAnswer"])
            saved = next(x for x in fixture.brain.status()["cases"] if x["caseId"] == source["brainCaseId"])
            self.assertEqual(5, saved["researchAttempts"])
            self.assertEqual(partial, saved["latestSourceAnswer"])
            self.assertEqual({"planned": True, "scheduled": True}, saved["researchRepeat"]["sourceRoutes"][opportunity["key"]])
            self.assertEqual("unchanged-research-material", repeat.admission(saved, "scheduled"))
            with fixture.brain.transaction() as c:
                self.assertEqual(0, reconcile_and_schedule(fixture.brain, c, [fixture.subject])["plansScheduled"])
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
