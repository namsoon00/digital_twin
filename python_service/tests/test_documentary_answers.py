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
        good = reviewed_run()
        self.assertEqual("answered", documentary_answer(good, "sources")["status"])
        self.assertEqual("unavailable", documentary_answer(good, "other")["status"])
        for field, value in (("assessmentFingerprint", "stale"), ("resultEvidenceIds", ["invented"]), ("reason", "")):
            changed = copy.deepcopy(good); changed["taskAssessments"][0][field] = value
            result = documentary_answer(changed, "sources")
            self.assertEqual("unavailable", result["status"])
            self.assertFalse(result["sources"])

    def test_missing_metric_stays_partial_and_no_data_explains_blocker(self):
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
        self.assertIn("비교 근거 부족", render_research_progress(blocked))
        value["nextCheckAt"] = "2026-10-12T00:00:00Z"
        self.assertIsNone(research_progress(value, event("review-deferred", "retry")))
        self.assertEqual("waiting", value["status"])
