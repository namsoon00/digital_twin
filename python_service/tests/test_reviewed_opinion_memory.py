"""Approved opinion continuity, source-bound display and bounded active reads."""
from copy import deepcopy
import unittest
from unittest.mock import Mock

from digital_twin.modules.ai_orchestration.domain import (
    opinion_memory as opinions, opinion_prompt, opinion_retrieval, question_purpose,
    question_assessment, research_review, research_review_parts, research_repeat, memory_reference)
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input)
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.ai_orchestration.application.read_tools import ObservationReadTools
from digital_twin.modules.ai_orchestration.application.opinion_preparation import quote_blocker, gap_reads
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession, content_hash


def fixture():
    from test_question_purpose_flow import fixture as old
    p, memories, trace, case, raw = old()
    p["opinionMemoryPolicy"] = opinions.VERSION
    raw["questionResolutions"][0]["judgmentImpact"].update(evidenceRelation="insufficient", opinionChange="new")
    return p, memories, trace, case, raw


def approved(p, memories, raw):
    result = validate_plan(raw, p, memories)
    result.update(input=p, executionInputId="opinion-author", observedAt=p["capturedAt"])
    result["researchQuality"] = research_review.accept(result, {
        "version": research_review_parts.OPINION_VERSION,
        "assessment": {"accepted": True, "reason": "확인한 답과 원래 질문의 부족분을 함께 검토했습니다."},
        "knowledge": {"accepted": False, "reason": "별도 지식 변경에는 검증이 더 필요합니다."}}, "opinion-review")
    return result


class ReviewedOpinionTests(unittest.TestCase):
    def test_new_contracts_replay_and_old_contracts_keep_their_original_schema(self):
        from test_question_purpose_flow import fixture as old
        p, memories, trace, _, raw = fixture()
        author = freeze_execution_input(p, [], memories, retrieval_trace=trace)
        self.assertEqual(opinion_prompt.PROMPT_VERSION, author["promptVersion"])
        result = approved(p, memories, raw)
        for env in (author, freeze_repair_input(author, raw, ["판단 영향 점검"], "parent"),
                    freeze_review_input(result), research_review.freeze(result, memories, 262144)):
            validate_execution_input(env)
            broken = deepcopy(env); broken["current"]["opinionMemoryPolicy"] = "changed"
            with self.assertRaises(ValueError): validate_execution_input(broken)
        p, memories, trace, _, raw = old()
        legacy = freeze_execution_input(p, [], memories, retrieval_trace=trace)
        self.assertEqual("independent-observation-v30-question-purpose", legacy["promptVersion"])
        self.assertNotIn("opinionChange", legacy["outputSchema"]["properties"]["questionResolutions"]["items"]["properties"]["judgmentImpact"]["properties"])
        validate_execution_input(legacy); validate_plan(raw, p, memories)

    def test_support_can_leave_opinion_unchanged_and_rejected_drafts_cannot_replace_baseline(self):
        p, memories, _, case, raw = fixture(); result = approved(p, memories, raw)
        assessment = question_assessment.assessment(case, {"taskId": "accepted-task"}, result,
            result["caseReviews"][0], result["questionResolutions"][0], p["capturedAt"])
        case.update(questionAssessment=assessment, hypothesisResolution={"state": "quality-blocked", "reason": "new rejected draft"})
        current = opinions.current_question(case)
        self.assertEqual("accepted-task", current["taskId"])
        self.assertNotIn("new rejected draft", str(current))
        memories[0].update(questionAssessment=assessment, approvedOpinion=current, questionWorkUnchanged=True)
        impact = raw["questionResolutions"][0]["judgmentImpact"]
        impact.update(effect="unchanged", evidenceRelation="supports", opinionChange="unchanged")
        with self.assertRaisesRegex(ValueError, "confirmed current evidence"): validate_plan(raw, p, memories)
        for section in ("summary", "hypothesis"):
            raw["claimEvidence"][section] = [{"period": "current", "factId": "official-document", "field": "historicalDocumentReference.text"}]
        validate_plan(raw, p, memories)
        impact.update(effect="supports", opinionChange="strengthened")
        with self.assertRaisesRegex(ValueError, "unchanged question evidence"): validate_plan(raw, p, memories)
        case["questionContract"]["period"] = "a different period"
        self.assertEqual("unavailable", opinions.current_question(case)["status"])
        result["quality"] = {"status": "rejected", "errors": ["market price not usable"]}
        self.assertTrue(opinions.approved_result(result))  # Independent research admission survives.
        result["researchQuality"]["admissions"]["assessment"] = False
        self.assertFalse(opinions.approved_result(result))

    def test_archive_search_then_exact_detail_is_frozen_without_promoting_memory_to_facts(self):
        from test_efficient_retrieval import packet, decision
        p = packet(); p.update(opinionMemoryPolicy=opinions.VERSION, memoryReadPolicy=memory_reference.VERSION,
                               questionPurposePolicy=question_purpose.VERSION)
        record = {**{k: p[k] for k in ("accountId", "symbol", "worldId")}, "kind": "opinion-memory",
            "knownAt": p["capturedAt"], "answer": "당시 매출과 이익을 비교한 과거 답변입니다.",
            "summary": "이전 매출 분석", "authority": "historical-reviewed-opinion-only"}
        search, saved = Mock(return_value={"records": [record], "nextOffset": None}), []
        def save(env):
            validate_execution_input(env); saved.append(deepcopy(env)); return "read-" + str(len(saved))
        def decide(env, _):
            value = decision("finish")
            if len(saved) == 1:
                value.update(action="read", requests=[{"tool": "search_memory", "category": "memories", "query": "매출", "cursor": ""}])
            elif len(saved) == 2:
                section = next(r for r in env["retrievalContext"]["readableMemorySections"] if "/answer" in r["paths"])
                value.update(action="read", requests=[{"tool": "read_memory", "category": "memories", "memoryId": section["memoryId"], "path": "/answer"}])
            return value
        final, history, memories, trace = retrieve_working_evidence(ObservationEvidenceSession(p, p["facts"]),
            p, [], [], decide, save, 262144, memory_search=search)
        self.assertEqual("ready", final["retrieval"]["status"]); search.assert_called_once()
        self.assertEqual(opinion_retrieval.VERSION, final["retrieval"]["version"])
        self.assertIn(record["answer"], [r.get("content") for r in memories])
        self.assertNotIn("opinion-memory", [f["kind"] for f in final["facts"]])
        validate_execution_input(freeze_execution_input(final, history, memories, retrieval_trace=trace))

    def test_archive_scope_future_cutoff_and_cursor_ownership_are_enforced(self):
        from test_efficient_retrieval import packet
        from digital_twin.modules.ai_orchestration.domain.retrieval import ReadRequestError
        p = packet(); scope = {**{k: p[k] for k in ("accountId", "symbol", "worldId")}, "asOf": p["capturedAt"]}
        source = {**scope, "knownAt": p["capturedAt"], "kind": "opinion-memory", "authority": "historical-reviewed-opinion-only"}
        search = Mock(return_value={"records": [source], "nextOffset": 80})
        reader = ObservationReadTools(ObservationEvidenceSession(p, p["facts"]), [], [], True, search, scope)
        request = {"tool": "search_memory", "category": "memories", "query": "매출", "cursor": ""}
        reply = reader.read(reader.resolve(request)); reader.admit(reply)
        with self.assertRaises(ReadRequestError): reader.resolve({**request, "query": "다른 질문", "cursor": reply["nextCursor"]})
        for change in ({"worldId": "another-world"}, {"accountId": "another-account"}, {"knownAt": "2099-01-01T00:00:00Z"}):
            search.return_value = {"records": [{**source, **change}], "nextOffset": None}
            reader = ObservationReadTools(ObservationEvidenceSession(p, p["facts"]), [], [], True, search, scope)
            with self.assertRaises(ValueError): reader.read(reader.resolve(request))

    def test_gap_reads_are_real_bounded_reads_and_keep_missing_requirements(self):
        from question_assessment_checks import fixture as source_fixture
        p, session, case, document = source_fixture(); p["opinionMemoryPolicy"] = opinions.VERSION
        case["questionAssessment"] = {"missingRequirements": ["원문 현금흐름 표"], "answerParts": {"gaps": [
            {"requirementIndex": 0, "role": "essential", "cause": "unread-source", "nextAction": "보고서의 현금흐름 표를 읽습니다."}]}}
        documents = session.question_document_index({document["historicalDocumentReference"]["evidenceId"]: [case["caseId"]]}, document_views=True)
        views, work = gap_reads(session, p, [case], documents, [], set())
        self.assertIn(document["id"], views)
        self.assertEqual("read_document", work["reads"][0]["request"]["tool"])
        self.assertEqual("read-needs-assessment", work["status"])
        self.assertEqual(["원문 현금흐름 표"], case["questionAssessment"]["missingRequirements"])
        self.assertEqual({}, gap_reads(session, {**p, "executionLane": "market-observe"}, [case], documents, [], set())[0])
        self.assertEqual("no-additional-issued-source", gap_reads(session, p, [case], documents, [], {document["id"]})[1]["status"])

    def test_price_preflight_skips_model_but_dated_question_is_not_blocked(self):
        from test_ai_control import AIControlTests
        from unittest.mock import patch
        service, store, planner = AIControlTests().runner()
        with patch("digital_twin.modules.ai_orchestration.application.opinion_preparation.quote_blocker",
                   return_value={"reasonCode": "current-quote", "reason": "원천 시세 시각을 기다립니다."}):
            self.assertEqual("awaiting-evidence", service.run_once()["status"])
        planner.assert_not_called(); store.save_execution_input.assert_not_called()
        p = {"opinionMemoryPolicy": opinions.VERSION, "executionLane": "market-observe", "priceInvestigationPolicy": "active",
             "evidenceUses": {"quotes": [{"currentStateEligible": False, "datedReferenceEligible": False}]}}
        self.assertEqual("current-quote", quote_blocker(p)["reasonCode"])
        p["evidenceUses"]["quotes"][0]["datedReferenceEligible"] = True
        self.assertEqual({}, quote_blocker(p))
        p["executionLane"] = "question-research"; p["evidenceUses"]["quotes"] = []
        self.assertEqual({}, quote_blocker(p))

    def test_self_authored_bookkeeping_does_not_wake_research_but_new_source_does(self):
        p, _, _, case, _ = fixture(); memories = [case]
        before = research_repeat.observation_key(p, memories)
        changed = deepcopy(memories)
        changed[0].update(revision=999, nextCheckAt="2099", questionAssessment={"answer": "동일 근거를 다시 쓴 설명"},
                          approvedOpinion={"status": "reviewed"}, hypothesisResolution={"reason": "새 문장"})
        self.assertEqual(before, research_repeat.observation_key(p, changed))
        changed[0]["latestSourceAnswer"]["sources"][0]["materialFingerprint"] = "new-source-body"
        self.assertNotEqual(before, research_repeat.observation_key(p, changed))

    def test_empty_plan_is_reused_until_the_gap_changes(self):
        from test_ai_control import AIControlTests
        service, mock, planner = AIControlTests().runner()
        class Store:
            outcome = {}
            def __getattr__(self, name): return getattr(mock, name)
            def last_processing_outcome(self, *_): return self.outcome
        store = Store(); service.store = store
        job = mock.claim.return_value; job.update(capability="research-plan", researchGaps=["구체적인 원문 공개 경로 확인 필요"])
        service.brain_memory = Mock(return_value=[]); planner.return_value = {"reviews": [], "questions": []}
        self.assertEqual("completed", service.run_once()["status"])
        store.outcome = mock.complete.call_args.args[1]["processingOutcome"]
        self.assertEqual("completed", service.run_once()["status"])
        self.assertEqual(1, planner.call_count)
        self.assertFalse(mock.complete.call_args.args[1]["processingOutcome"]["modelCalled"])
        job["researchGaps"] = ["새 보고서의 현금흐름 비교 필요"]
        service.run_once(); self.assertEqual(2, planner.call_count)

    def test_notification_contribution_requires_matching_current_source_and_citation(self):
        p, memories, _, case, raw = fixture(); result = approved(p, memories, raw)
        assessment = question_assessment.assessment(case, {"taskId": "accepted-task"}, result,
            result["caseReviews"][0], result["questionResolutions"][0], p["capturedAt"])
        case["questionAssessment"] = assessment
        p["reviewedQuestionContext"] = opinions.question_context([{"kind": "research-context", "body": case}])
        result["questionResolutions"] = []
        refs = [{"factId": "official-document", "period": "current", "field": "title"}]
        result["claimEvidence"] = {"summary": refs}
        self.assertEqual("prior-answer-context", opinions.contribution(result)[0]["kind"])
        result["claimEvidence"] = {}
        self.assertEqual([], opinions.contribution(result))
        result["claimEvidence"] = {"summary": refs}
        p["reviewedQuestionContext"][0]["basis"] = []
        p["reviewedQuestionContext"][0]["sources"][0]["documentHash"] = "another-revision"
        self.assertEqual([], opinions.contribution(result))

    def test_market_retrieval_retains_price_survey_and_optional_gap_contract(self):
        from test_price_investigation_flow import packet, decision
        p = packet(); p.update(opinionMemoryPolicy=opinions.VERSION, questionPurposePolicy=question_purpose.VERSION)
        final, h, r, trace = retrieve_working_evidence(ObservationEvidenceSession(p, p["facts"]), p, [], [],
            lambda e, _: decision("finish", [f["factId"] for f in e["retrievalContext"]["knownFacts"]]),
            validate_execution_input, 262144)
        self.assertEqual("ready", final["retrieval"]["status"])
        validate_execution_input(freeze_execution_input(final, h, r, retrieval_trace=trace))


if __name__ == "__main__": unittest.main()
