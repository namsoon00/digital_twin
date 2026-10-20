"""Independent research answers retain exact source receipts and never qualify trades."""
import copy
import unittest
from unittest.mock import Mock

from digital_twin.modules.ai_orchestration.application.research import execute_research
from digital_twin.modules.ai_orchestration.domain.documentary_answer import documentary_answer
from digital_twin.modules.ai_orchestration.domain.research_request import validate_research_request
from digital_twin.modules.ai_orchestration.domain.research_progress import research_progress
from digital_twin.modules.notifications.application.research_progress_message import render_research_progress
from digital_twin.modules.news_intelligence.application.hypothesis_research_planner_service import HypothesisResearchPlanningService
from digital_twin.modules.news_intelligence.application.investment_research_orchestration_service import InvestmentResearchOrchestrationService
from digital_twin.modules.news_intelligence.domain.question_passages import select_question_filings, select_question_passages
from digital_twin.infrastructure.sec_report_passages import question_document_blocks
from test_question_research_progress import Advisor, EvidenceStore, Gateway, RunStore, evidence, packets_for
from test_research_progress import case, event


def reviewed_run():
    row = {"taskId": "sources", "status": "addressed", "semanticReviewState": "addressed",
           "coverageState": "complete", "assessmentFingerprint": "exact-packet", "reason": "매출은 100에서 120으로 증가했습니다.",
           "resultEvidenceIds": ["doc"], "missingRequirements": []}
    return {"taskAssessments": [row], "roundHistory": [{"taskAssessments": [copy.deepcopy(row)],
        "evidencePackets": [{"evidenceId": "doc", "sourceUrl": "https://www.sec.gov/report", "title": "10-Q", "inputFingerprint": "packet"}]}]}


class DocumentaryAnswerTests(unittest.TestCase):
    def test_remaining_requirements_reach_collection_without_changing_original_question(self):
        from digital_twin.modules.news_intelligence.domain.research_progress import task_contract
        from test_ai_control import SUBJECT
        from types import SimpleNamespace
        missing = ["부문 정의와 반올림·재분류 차이의 근거를 확인해야 합니다."]
        contract = {"target": "동일 기준 재무 비교", "period": "2026-07-26 및 2025-07-27",
                    "answerCriterion": "동일 범위·기간·단위의 지표와 정정 여부 확인"}
        job = {**SUBJECT, "taskId": "focus-test", "question": "매출과 순이익은 같은 기준으로 어떻게 달라졌는가?",
               "researchRequest": validate_research_request({"queryTerms": ["segment information rounding"],
                    "sourceTypes": ["official-filing"], "maxAgeMinutes": 527040}, True),
               "questionContract": contract, "evidenceRequirements": missing,
               "knownSourceAnswer": {"status": "partial", "text": "매출 표는 확보했습니다.", "sources": []}}
        store, orchestrator = Mock(), Mock()
        store.get_run.return_value = None
        orchestrator.run.return_value = SimpleNamespace(run_id="run", to_dict=lambda: {"status": "evidence-unavailable"})
        execute_research(job, store, lambda: orchestrator)
        task = orchestrator.run.call_args.args[2]["researchPlan"]["tasks"][0]
        self.assertEqual(job["question"], task["question"])
        self.assertEqual(missing, task["sourceFocus"]["missingRequirements"])
        self.assertIn(contract["answerCriterion"], task["purpose"])
        self.assertEqual(task["sourceFocus"], task_contract(task)["sourceFocus"])
        generic = "Revenue net income operating income operating cash flow profitability and data center sales. " * 5
        focused = "Segment information: our reportable segments differ from the data center platform. Amounts may differ due to rounding and reclassification of comparable periods."
        selected = select_question_passages([generic, focused], [task], limit=600)
        self.assertEqual(focused, selected[0]["quote"])
        self.assertEqual(missing, job["evidenceRequirements"])
        for prior_focus in (None, task["sourceFocus"]):
            previous_task = copy.deepcopy(task)
            if prior_focus is None:
                previous_task.pop("sourceFocus")
            audit = {"taskFingerprint": "preserved-resume", "readCount": 2}
            store.get_run.return_value = {"status": "research-cooldown", "executedPlan": {
                "tasks": [previous_task], "sourceExploration": audit}}
            execute_research(job, store, lambda: orchestrator)
            resumed = orchestrator.run.call_args.args[2]
            self.assertEqual(prior_focus, resumed["researchPlan"]["tasks"][0].get("sourceFocus"))
            self.assertEqual(audit, resumed["sourceExplorationResume"])

    def test_submission_listing_cannot_assert_absence_beyond_captured_coverage(self):
        from filing_history_checks import check_history_coverage
        check_history_coverage(self)

    def test_submission_history_and_focused_original_passages_survive_the_graph_read_boundary(self):
        from filing_history_checks import check_source_to_graph
        check_source_to_graph(self)

    def test_material_identity_ignores_source_polling_but_preserves_values_and_units(self):
        from digital_twin.modules.news_intelligence.contracts import research_material_fingerprint
        packet = {"symbol": "TEST", "kind": "financial-fact", "source": "official", "periodEnd": "2026-06-30",
            "reportedValues": {"operatingCashFlow": 120}, "metricUnits": {"operatingCashFlow": "KRW"},
            "metricProvenance": {"operatingCashFlow": {"scope": "CFS", "periodStart": "2026-01-01",
                "sourceReferences": [{"revisionId": "r1", "fetchedAt": "2026-10-13"}]}}}
        original = research_material_fingerprint(packet)
        changed = copy.deepcopy(packet)
        changed.update(evidenceId="another-id", inputFingerprint="another-capture", observedAt="2026-10-15",
                       title="이름 표기 수정", statement="AI 문장 변경")
        changed["metricProvenance"]["operatingCashFlow"]["sourceReferences"][0].update(revisionId="r2", fetchedAt="2026-10-15")
        self.assertEqual(original, research_material_fingerprint(changed))
        for field, replacement in (("reportedValues", {"operatingCashFlow": 121}),
            ("metricUnits", {"operatingCashFlow": "USD"}), ("periodEnd", "2025-06-30")):
            self.assertNotEqual(original, research_material_fingerprint({**changed, field: replacement}))
        changed["metricProvenance"]["operatingCashFlow"]["scope"] = "OFS"
        self.assertNotEqual(original, research_material_fingerprint(changed))
        self.assertEqual("", research_material_fingerprint({"title": "머리글만 있는 자료", "statement": "AI 요약"}))

    def test_material_reader_is_scoped_bounded_and_has_no_ai_or_network_dependency(self):
        from types import SimpleNamespace
        from digital_twin.modules.news_intelligence.infrastructure.research_material_reader import ResearchMaterialReader
        def source(period, number, symbol="TEST"):
            return SimpleNamespace(symbol=symbol, kind="financial-fact", source="official", url="", raw_payload={
                "periodEnd": period, "reportedValues": {"revenue": number}, "metricUnits": {"revenue": "USD"}})
        repo = Mock()
        repo.latest.side_effect = lambda **kw: [source("2026-06-30", 100), source("2026-06-30", 100),
            source("2025-06-30", 90), source("2026-06-30", 999, "OTHER")] if kw["kind"] == "financial-fact" else []
        reader = ResearchMaterialReader(repo)
        question = {"symbol": "TEST", "researchRequest": {"sourceTypes": ["official-filing"]},
                    "questionContract": {"period": "2026-06-30 종료 반기"}}
        state = reader(question)
        self.assertEqual(1, len(state["fingerprints"]))
        self.assertTrue(all(call.kwargs["symbol"] == "TEST" and call.kwargs["limit"] == 16 for call in repo.latest.call_args_list))
        calls = repo.latest.call_count
        self.assertEqual(state, reader(question))
        self.assertEqual(calls, repo.latest.call_count)
        state["fingerprints"].append("caller-mutation")
        self.assertEqual(1, len(reader(question)["fingerprints"]))

    def _assert_final_review_replaces_explorers_earlier_missing_requirements(self):
        run = reviewed_run()
        for row in (run['taskAssessments'][0], run['roundHistory'][0]['taskAssessments'][0]):
            row.update(status='needs-evidence', semanticReviewState='partial', coverageState='incomplete',
                       missingRequirements=['전년 동기 현금 납세액'])
        run['executedPlan'] = {'sourceExploration': {'missingRequirements': ['이미 확보한 영업현금흐름', '전년 동기 현금 납세액']}}
        self.assertEqual(['전년 동기 현금 납세액'], documentary_answer(run, 'sources')['missingRequirements'])

    def _assert_missing_citations_are_distinct_from_a_changed_source_version(self):
        from digital_twin.modules.news_intelligence.domain.research_progress import assess_tasks
        task = {'taskId': 'sources', 'question': 'Explain the reported cause', 'sourceTypes': ['official-filing'], 'maxAgeMinutes': 1440}
        packets = packets_for([evidence()])
        from digital_twin.modules.decisions.contracts import utc_now_iso
        now = utc_now_iso()
        initial = assess_tasks([task], packets, 'AAPL', now)[0]
        review = {'taskId': 'sources', 'status': 'unresolved', 'assessmentFingerprint': initial['assessmentFingerprint'],
                  'evidenceIds': [], 'counterEvidenceIds': [], 'reason': '제공된 자료에 질문에 해당하는 설명이 없습니다.'}
        assessment = assess_tasks([task], packets, 'AAPL', now, [review])[0]
        self.assertEqual(['missing-citations'], assessment['reviewRejectionReasons'])
        run = {'taskAssessments': [assessment], 'roundHistory': [{'taskAssessments': [assessment], 'evidencePackets': packets}]}
        answer = documentary_answer(run, 'sources')
        self.assertEqual('unavailable', answer['status'])
        self.assertIn('근거를 인용하지 못했습니다', answer['text'])
        self.assertNotIn('자료 버전이 맞지 않아', answer['text'])

    def test_central_research_returns_answer_without_market_observation_and_cannot_expand_scope(self):
        gateway, runs, advisor = Gateway([[evidence(kind="news")]]), RunStore(), Advisor(expand=True)
        planner = HypothesisResearchPlanningService(advisor)
        orchestrator = InvestmentResearchOrchestrationService(EvidenceStore(), gateway,
            hypothesis_research_planner=planner, research_store=runs,
            settings={"investmentBrainResearchCooldownMinutes": 0})
        lookup = Mock(); lookup.get_run.return_value = None
        job = {"accountId": "account-test", "symbol": "AAPL", "name": "Apple", "taskId": "document-review",
            "question": "현금흐름은 어떻게 바뀌었는가?", "researchRequest": validate_research_request({
                "sourceTypes": ["news"], "queryTerms": ["cash conversion"], "maxAgeMinutes": 1440}, True)}
        result = execute_research(job, lookup, lambda: orchestrator)
        self.assertEqual("answered", result["documentaryAnswer"]["status"])
        self.assertEqual("source-review-only", result["documentaryAnswer"]["authority"])
        self.assertEqual(1, len(gateway.requests))
        self.assertEqual(1, len(runs.rows[-1].task_ids))
        self.assertEqual(0, advisor.contexts[-1]["guardrails"]["maximumAdditionalTaskCount"])
        self.assertIs(planner, orchestrator.hypothesis_research_planner)
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_brain_agenda import MySQLBrainAgendaStore
        agenda = object.__new__(MySQLBrainAgendaStore)
        record = {**case(), "lastResearch": {"taskId": job["taskId"]}}
        agenda.read = Mock(return_value=record)
        agenda.save, agenda.wake_observation = Mock(), Mock()
        linked = {**job, "brainCaseId": record["caseId"], "worldId": record["worldId"]}
        agenda.research_completed(Mock(), linked, result)
        self.assertEqual("answered", record["lastResearch"]["result"]["documentaryAnswer"]["status"])
        self.assertEqual("review-needed", record["status"], "source answer must not qualify the hypothesis")
        self.assertNotIn("lastAssessment", record)
        self.assertEqual("research-returned", agenda.save.call_args.args[3])

    def test_wrong_task_stale_review_and_uncited_answer_fail_closed(self):
        self._assert_missing_citations_are_distinct_from_a_changed_source_version()
        good = reviewed_run()
        self.assertEqual("answered", documentary_answer(good, "sources")["status"])
        self.assertEqual("unavailable", documentary_answer(good, "other")["status"])
        for field, value in (("assessmentFingerprint", "stale"), ("resultEvidenceIds", ["invented"]), ("reason", "")):
            changed = copy.deepcopy(good); changed["taskAssessments"][0][field] = value
            result = documentary_answer(changed, "sources")
            self.assertEqual("unavailable", result["status"])
            self.assertFalse(result["sources"])

    def test_missing_metric_stays_partial_and_no_data_explains_blocker(self):
        self._assert_final_review_replaces_explorers_earlier_missing_requirements()
        run = reviewed_run()
        for row in (run["taskAssessments"][0], run["roundHistory"][0]["taskAssessments"][0]):
            row.update(status="needs-evidence", semanticReviewState="partial", coverageState="incomplete",
                       missingRequirements=["metric:operatingCashFlow:2025-06-30"])
        partial = documentary_answer(run, "sources")
        self.assertEqual("partial", partial["status"])
        self.assertTrue(partial["sources"])
        for row in (run["taskAssessments"][0], run["roundHistory"][0]["taskAssessments"][0]):
            row["semanticReviewState"] = "unresolved"
        self.assertEqual("unavailable", documentary_answer(run, "sources")["status"])
        unavailable = documentary_answer({"providerStatuses": [{"status": "configuration-required"}]}, "sources")
        self.assertIn("접근 설정", unavailable["text"])
        self.assertFalse(unavailable["sources"])
        from digital_twin.modules.news_intelligence.domain.research_progress import missing_requirements
        self.assertIn("공식 원문 본문 또는 출처가 확인된 보고 수치", missing_requirements(
            {"requiresDocumentBody": True}, [{"evidenceId": "metadata", "evidenceTypes": ["official-filing"]}]))

    def test_citation_repair_rechecks_current_receipts_once_and_retains_rejections(self):
        from test_question_research_progress import TARGET, task
        from digital_twin.modules.decisions.contracts import InvestmentQuestion
        for wrong_field, persistent in (('evidenceIds', False), ('assessmentFingerprint', False), ('evidenceIds', True)):
            with self.subTest(wrong_field=wrong_field, persistent=persistent):
                original = Advisor()
                def review(context):
                    answer = original.plan(context)
                    if persistent or len(original.contexts) == 1:
                        answer['taskReviews'][0][wrong_field] = ['superseded-source'] if wrong_field == 'evidenceIds' else 'old-assessment'
                    return answer
                advisor = Mock(); advisor.plan.side_effect = review
                gateway, runs = Gateway(), RunStore()
                orchestrator = InvestmentResearchOrchestrationService(EvidenceStore([evidence()]), gateway,
                    hypothesis_research_planner=HypothesisResearchPlanningService(advisor), research_store=runs)
                result = orchestrator.run(InvestmentQuestion.create('Did operating cash flow improve?', subject_symbol='AAPL'),
                    TARGET, {'documentaryReviewOnly': True, 'researchPlan': {'tasks': [task()]}}).to_dict()
                self.assertEqual(1, sum(row.get('phase') == 'citation-repair' for row in result['roundHistory']))
                self.assertTrue(result['roundHistory'][0]['rejectedReviews'])
                self.assertTrue(original.contexts[1]['reviewRepair'])
                answer = documentary_answer(result, 'cash-flow')
                if persistent:
                    self.assertEqual('unavailable', answer['status'])
                    self.assertFalse(answer['sources'])
                    self.assertEqual(3, advisor.plan.call_count)
                else:
                    self.assertEqual('answered', answer['status'])
                    self.assertEqual(2, advisor.plan.call_count)
                    self.assertFalse(gateway.requests)
                    self.assertEqual(['financial-1'], [row['evidenceId'] for row in answer['sources']])

    def test_whole_table_headers_units_and_values_reach_review_packet(self):
        html = '<table><tr><th>USD millions</th><th>Three months 2026</th><th>Three months 2025</th></tr><tr><td>Data center revenue</td><td>120</td><td>100</td></tr></table>'
        passages = select_question_passages(question_document_blocks(html), [{"question": "데이터센터 매출 전년 동기 비교"}])
        self.assertEqual(1, len(passages))
        quote = passages[0]["quote"]
        for expected in ("USD millions", "Three months 2025", "Data center revenue", "120", "100"):
            self.assertIn(expected, quote)
        packets = packets_for([evidence(documentPassages=passages)])
        self.assertEqual(quote, packets[0]["documentPassages"][0]["quote"])
        self.assertFalse(select_question_passages(question_document_blocks('<table><tr><td>' + 'revenue ' * 3000 + '</td></tr></table>'), [{"question": "revenue"}]))

    def test_year_ago_period_precedes_intermediate_quarters_and_insider_forms(self):
        rows = [{"form": "10-Q", "accessionNumber": str(i), "filingDate": filed, "reportDate": period}
                for i, (filed, period) in enumerate((("2026-08-01", "2026-06-30"), ("2026-05-01", "2026-03-31"),
                    ("2025-11-01", "2025-09-30"), ("2025-08-01", "2025-06-29")))]
        selected = select_question_filings(rows + [{"form": "4", "accessionNumber": "insider", "filingDate": "2026-09-01"}],
            [{"question": "최신 분기와 전년 동기 매출"}], "2026-10-10")
        self.assertEqual(["0", "3"], [r["accessionNumber"] for r in selected[:2]])
        self.assertEqual(3, len(selected))
        from digital_twin.infrastructure.external_signal_provider_sec import ExternalSignalSecMixin
        provider = object.__new__(ExternalSignalSecMixin)
        provider.latest_sec_filing = lambda payload, cik: {**{k: v[0] for k, v in payload["filings"]["recent"].items()}, "url": "https://www.sec.gov/report"}
        forms = ["10-Q"] * 4 + ["10-K"] + ["10-Q"] * 6 + ["10-K"] * 2
        inventory = provider.report_sec_filings({"filings": {"recent": {"form": forms}}}, "1")
        self.assertEqual(["10-Q", "10-K"], [row["form"] for row in inventory[:2]])
        self.assertEqual(8, sum(row["form"] == "10-Q" for row in inventory))
        self.assertEqual(2, sum(row["form"] == "10-K" for row in inventory))

    def test_answer_and_blocker_reply_even_without_changed_collection_and_deduplicate(self):
        value = case()
        answer = documentary_answer(reviewed_run(), "sources")
        value["lastResearch"] = {"result": {"changedEvidenceCount": 0, "documentaryAnswer": answer}}
        notice = research_progress(value, event("research-returned"))
        body = render_research_progress(notice)
        self.assertIn("100에서 120", body)
        self.assertIn("https://www.sec.gov/report", body)
        self.assertNotIn("아직 검토 중", body)
        self.assertIn("2026-10-11 09:00 KST", body)
        self.assertIsNone(research_progress(value, event("research-returned", "retry")))
        value["reviewBlocker"] = {"reason": "관찰 품질 미달", "errors": ["비교 근거 부족"]}
        blocked = research_progress(value, event("review-deferred"))
        self.assertIsNone(blocked, "draft validation remains in the audit, not repeated research notifications")
        self.assertIn("비교 근거 부족", value["reviewBlocker"]["errors"])
        value["nextCheckAt"] = "2026-10-12T00:00:00Z"
        self.assertIsNone(research_progress(value, event("review-deferred", "retry")))
        self.assertEqual("waiting", value["status"])
