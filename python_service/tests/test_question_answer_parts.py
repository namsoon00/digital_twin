"""Cited partial answers never waive original gaps or restart parked research."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import Mock

from digital_twin.modules.ai_orchestration.domain import question_answer, question_assessment, research_review, research_work
from digital_twin.modules.ai_orchestration.domain.question_closure import closure
from digital_twin.modules.ai_orchestration.domain.research_repeat import admission, initialize, remember
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, validate_execution_input


class QuestionAnswerPartsTests(unittest.TestCase):
    def fixture(self, answered=False):
        from question_assessment_checks import fixture, raw_assessment
        from test_scoped_knowledge_research import scoped_decision
        from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
        p, session, q, fact = fixture()
        from test_research_knowledge import context
        p["researchKnowledge"] = context(p)
        p["questionAnswerPolicy"] = question_answer.VERSION
        missing = [] if answered else ["같은 기간의 영업현금흐름 원문이 필요합니다."]
        scope = {"caseId": q["caseId"], "status": "ready" if answered else "waiting", "selectedFactIds": [fact["id"]], "missingEvidence": missing}
        selected, _, memories, trace = retrieve_working_evidence(session, p, [], [q],
            Mock(return_value=scoped_decision("finish" if answered else "assess", [fact["id"]], [scope], missing)), lambda _: "read-input", 262144)
        raw = raw_assessment(q, action="answered" if answered else "wait", missing=missing)
        raw["questionResolutions"][0]["answerParts"] = {
            "confirmed": [{"text": "공식 분기 원문에서 해당 기간의 매출 비교를 확인했습니다.", "evidenceIds": [fact["id"]]}],
            "gaps": [{"requirementIndex": 0, "role": "essential", "cause": "unread-source",
                      "nextAction": "같은 보고 기간의 연결 현금흐름표 원문을 확인합니다."}] if missing else []}
        return selected, memories, trace, q, raw

    def test_frozen_cited_partial_answer_survives_independent_review_without_closing_question(self):
        from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
        p, memories, trace, case, raw = self.fixture()
        author = freeze_execution_input(p, [], memories, retrieval_trace=trace)
        validate_execution_input(author)
        self.assertIn("answerParts", author["outputSchema"]["properties"]["questionResolutions"]["items"]["required"])
        result = validate_plan(raw, p, memories)
        result.update(input=p, executionInputId="author-input")
        review = research_review.freeze(result, memories, 262144)
        self.assertEqual(research_review.ANSWER_VERSION, review["promptVersion"])
        research_review.validate_input(review)
        result["researchQuality"] = research_review.accept(result, {"version": research_review.ANSWER_VERSION,
            "accepted": True, "reason": "인용한 원문과 확인한 답변, 남은 항목의 일치를 확인했습니다."}, "review-input")
        saved = question_assessment.assessment(case, {"taskId": "answer-task"}, result, result["caseReviews"][0],
            result["questionResolutions"][0], "2026-10-25T13:00:00Z")
        self.assertEqual("partial", saved["status"])
        self.assertEqual("wait", saved["reviewAction"])
        view = closure(case, {}, saved, True, {}, {})
        self.assertEqual("confirmed-parts", view["answerState"])
        self.assertEqual("필요한 원문 미확보", view["gapDetails"][0]["causeLabel"])
        self.assertEqual(raw["questionResolutions"][0]["answerParts"]["confirmed"], view["confirmedParts"])
        rejected = {**result, "researchQuality": {"status": "rejected", "reviewInputId": "review"}}
        with self.assertRaises(ValueError):
            question_assessment.assessment(case, {"taskId": "answer-task"}, rejected, result["caseReviews"][0], result["questionResolutions"][0], "now")

    def test_wrong_source_dropped_gap_and_supplementary_completion_are_rejected(self):
        from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
        p, memories, _, _, raw = self.fixture()
        for mutation in ("foreign-source", "unusable-source", "drop-gap", "duplicate-gap", "supplementary-close"):
            value, packet = deepcopy(raw), deepcopy(p)
            resolution = value["questionResolutions"][0]
            with self.subTest(mutation=mutation):
                if mutation == "foreign-source": resolution["answerParts"]["confirmed"][0]["evidenceIds"] = ["outside-question"]
                if mutation == "unusable-source":
                    next(fact for fact in packet["facts"] if fact["id"] == "official-document")["historicalDocumentReference"]["text"] += "changed"
                if mutation == "drop-gap": resolution["answerParts"]["gaps"] = []
                if mutation == "duplicate-gap": resolution["answerParts"]["gaps"] *= 2
                if mutation == "supplementary-close":
                    resolution["answerParts"]["gaps"][0]["role"] = "supplementary"
                    value["caseReviews"][0]["action"] = "answered"
                with self.assertRaises(ValueError): validate_plan(value, packet, memories)

    def test_complete_cited_answer_can_finish_only_after_original_scope_is_ready(self):
        from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
        p, memories, trace, case, raw = self.fixture(answered=True)
        validate_execution_input(freeze_execution_input(p, [], memories, retrieval_trace=trace))
        result = validate_plan(raw, p, memories)
        result.update(input=p, executionInputId="completed-input", researchQuality={"status": "accepted", "reviewInputId": "review"})
        saved = question_assessment.assessment(case, {"taskId": "completed-task"}, result, result["caseReviews"][0], result["questionResolutions"][0], "now")
        view = closure(case, {}, saved, True, {}, {})
        self.assertEqual("complete", view["answerState"])
        self.assertEqual([], view["gapDetails"])
        self.assertEqual("closed", view["execution"]["code"])
        from datetime import datetime, timezone
        from digital_twin.modules.ai_orchestration.domain.question_progress import question_progress
        task = {**{key: case[key] for key in ("accountId", "symbol", "worldId")},
                "taskId": "completed-task", "status": "completed", "hasSummary": True}
        for disposition, label in (("not-applicable", "질문 답변으로 정리"), ("business-thesis", "사업 가설에 연결")):
            value = {**case, "questionAssessment": {**saved, "hypothesisDisposition": disposition}}
            workflow = question_progress(value, {task["taskId"]: task}, {}, datetime.now(timezone.utc))
            self.assertEqual(label, next(stage["detail"] for stage in workflow["stages"] if stage["key"] == "hypothesis"))

    def test_answer_and_execution_states_stay_independent_without_queue_writes(self):
        from test_research_work import case
        value = case()
        value.update(researchAttempts=6, latestSourceAnswer={"status": "partial", "text": "확보한 원문 답변입니다.", "sources": [{"evidenceId": "one"}]})
        initialize(value)
        before = deepcopy(value)
        view = closure(value, value["latestSourceAnswer"], {}, False, {}, {})
        self.assertEqual("source-answer", view["answerState"])
        self.assertEqual("limit-reached", view["execution"]["code"])
        self.assertEqual(before, value)
        value["researchAttempts"] = 1
        remember(value, "planned")
        self.assertEqual("unchanged-research-material", admission(value, "planned"))
        value["questionAssessment"] = {"answerParts": {"confirmed": [{"text": "변경한 문구"}], "gaps": []}}
        self.assertEqual("unchanged-research-material", admission(value, "planned"))
        no_answer = {"status": "partial", "answer": "해당 원문을 아직 확인하지 못했습니다.",
            "sources": [{"factId": "one"}], "answerPartsVersion": question_answer.VERSION,
            "answerParts": {"confirmed": [], "gaps": []}}
        self.assertEqual("unavailable", closure(value, {}, no_answer, True, {}, {})["answerState"])
        active = {"taskId": "existing", "status": "pending"}
        running = closure(value, value["latestSourceAnswer"], {}, False, active, {"status": "quota", "label": "사용 한도 대기"})
        self.assertEqual("quota", running["execution"]["code"])
        self.assertEqual("source-answer", running["answerState"])

    def test_old_frozen_planner_and_assessment_schemas_remain_readable(self):
        from test_research_work import case
        value = case()
        job = {**value, "taskId": "planner", "name": "Test"}
        envelope = research_work.freeze(job, [value])
        validate_execution_input(envelope)
        self.assertIn("하나의 확인 대상", envelope["prompt"])
        envelope["promptVersion"] = research_work.LEGACY_PROMPT_VERSION
        envelope["prompt"] = research_work.prompt(envelope["current"], envelope["researchCases"], envelope["gaps"])
        envelope["promptHash"] = hashlib.sha256(envelope["prompt"].encode()).hexdigest()
        validate_execution_input(envelope)
        self.assertNotIn("하나의 확인 대상", envelope["prompt"])
        from question_assessment_checks import fixture
        from digital_twin.modules.ai_orchestration.domain.question_resolution import resolution_schema
        p, _, q, _ = fixture()
        self.assertNotIn("answerParts", resolution_schema(p, [q])["properties"]["questionResolutions"]["items"]["properties"])


class QuestionExhibitRecoveryTests(unittest.TestCase):
    def fixture(self):
        from digital_twin.modules.news_intelligence.contracts import NewsCollectionTarget
        root = "https://www.sec.gov/Archives/edgar/data/1234/000000123426000001/"
        metadata = {"form": "8-K", "accessionNumber": "0000001234-26-000001", "filingDate": "2026-09-01",
                    "url": root + "report.htm", "primaryDocument": "report.htm"}
        source = '<p>The company provided the transaction details in Exhibit 99.1, incorporated by reference. The terms and conditions are described in that attachment.</p>'
        index = '<table><tr><td>Other</td><td><a href="wrong.htm">Other document</a></td><td>EX-10</td></tr><tr><td>Transaction</td><td><a href="opaque.htm">Document</a></td><td>EX-99.1</td></tr></table>'
        exhibit = '<p>The transaction details describe the parties and conditions of the agreement. Completion remains subject to the stated conditions and regulatory approvals.</p>'
        documents = {root + "report.htm": source, root + metadata["accessionNumber"] + "-index.html": index, root + "opaque.htm": exhibit}
        provider = Mock()
        provider.sec_document_access_configured.return_value = True
        provider.sec_document_text_max_chars.return_value = 6000
        provider.guarded_call.side_effect = lambda _, __, work: work()
        provider.fetch_text.side_effect = lambda url, _: documents[url]
        return provider, NewsCollectionTarget(symbol="TEST", name="Test", market="US", currency="USD"), {
            "secFilings": {"TEST": {"cik": "1234", "latestFiling": metadata}}}, [{"taskId": "question", "question": "transaction details", "queryTerms": []}], documents

    def test_index_discovers_exact_exhibit_within_three_calls_and_never_becomes_answer_evidence(self):
        from digital_twin.infrastructure.official_question_research import collect_question_documents
        provider, target, signals, tasks, docs = self.fixture()
        tasks[0]["question"] = "8-K의 transaction details"
        signals["secFilings"]["TEST"]["reportFilings"] = [{"form": "10-Q", "filingDate": "2026-09-02",
            "accessionNumber": "0000001234-26-000002", "primaryDocument": "quarter.htm",
            "url": "https://www.sec.gov/Archives/edgar/data/1234/000000123426000002/quarter.htm"}]
        items, statuses = collect_question_documents(provider, target, signals, tasks)
        self.assertEqual(list(docs), [call.args[0] for call in provider.fetch_text.call_args_list])
        self.assertEqual(3, statuses[-1]["documentsAttempted"])
        self.assertEqual(1, statuses[-1]["filingIndexReads"])
        self.assertEqual(1, statuses[-1]["remainingCandidates"])
        self.assertTrue(any(item.url.endswith("opaque.htm") for item in items))
        self.assertFalse(any(item.url.endswith("index.html") for item in items))
        self.assertEqual(list(docs)[0], next(item.raw_payload["parentDocumentUrl"] for item in items if item.url.endswith("opaque.htm")))

    def test_index_failure_or_unsafe_link_preserves_primary_and_does_not_invent_no_disclosure(self):
        from digital_twin.infrastructure.official_question_research import collect_question_documents
        from digital_twin.modules.market_data.contracts import ExternalCallDeferred
        for response in ("<a href='https://untrusted.example/ex99.htm'>Exhibit 99.1</a>",
                         "Access Denied", ExternalCallDeferred("SEC", "2026-10-26T00:00:00Z", "circuit-open")):
            provider, target, signals, tasks, docs = self.fixture()
            def fetch(url, _):
                if url.endswith("index.html"):
                    if isinstance(response, Exception): raise response
                    return response
                return docs[url]
            provider.fetch_text.side_effect = fetch
            items, statuses = collect_question_documents(provider, target, signals, tasks)
            self.assertEqual(2, provider.fetch_text.call_count)
            self.assertEqual(1, len(items))
            self.assertTrue(items[0].url.endswith("report.htm"))
            self.assertTrue(any(row["status"] in {"exhibit-link-unavailable", "document-unavailable", "deferred"} for row in statuses))
            from digital_twin.modules.news_intelligence.contracts import source_recovery_summary
            diagnostics = source_recovery_summary({}, statuses)
            self.assertTrue(diagnostics["issues"])
            self.assertNotIn("unclassified-failure", diagnostics["causeCounts"])
