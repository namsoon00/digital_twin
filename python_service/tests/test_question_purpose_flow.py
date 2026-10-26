"""Purposeful questions keep source lineage, partial answers and independent admission."""
from copy import deepcopy
import os
import unittest
from unittest.mock import Mock

from digital_twin.modules.ai_orchestration.domain import (
    question_purpose as purpose, question_source_handoff as handoff, research_review,
    research_review_parts as parts, question_assessment, research_work)
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input)
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan, stamp


def fixture():
    from test_answer_led_research import fixture as previous
    p, memories, trace, case, raw = previous()
    p["questionPurposePolicy"] = purpose.VERSION
    p["questionEvidenceIndex"][0]["sourceAnswer"]["sources"] = deepcopy(case["latestSourceAnswer"]["sources"])
    p["questionSourceHandoff"] = handoff.build(p, memories, p["facts"])
    raw["questionPurposes"] = [{"questionIndex": 0,
        "decisionRelevance": "매출 확대의 지속성이 기업 가치 설명을 바꾸는지 확인합니다.",
        "uncertainty": "매출 확대가 가격과 판매량 중 어느 변화에서 발생했는지 구분하지 못했습니다.",
        "expectedInsight": "판매량 증가라면 수요 지속성을, 가격 상승이라면 가격 유지 조건을 더 확인합니다.",
        "evidenceTasks": [deepcopy(raw["questionContinuations"][0]["contract"])],
        "evidenceIds": ["official-document"]}]
    return p, memories, trace, case, raw


def reviewed(p, memories, raw, assessment=True, knowledge=False):
    result = validate_plan(raw, p, memories)
    result.update(input=p, executionInputId="purpose-author", observedAt=p["capturedAt"])
    result["researchQuality"] = research_review.accept(result, {"version": parts.VERSION,
        "assessment": {"accepted": assessment, "reason": "원래 질문의 확인된 답과 미확인 조건 및 다음 질문의 목적을 검토했습니다."},
        "knowledge": {"accepted": knowledge, "reason": "별도 지식 수정이 기존 미확인 조건을 누락하므로 승인할 수 없습니다."}}, "purpose-review")
    return result


class QuestionPurposeFlowTests(unittest.TestCase):
    def test_purpose_contract_survives_author_repair_reviews_and_keeps_old_inputs_exact(self):
        p, memories, trace, _, raw = fixture()
        author = freeze_execution_input(p, [], memories, retrieval_trace=trace)
        self.assertEqual("independent-observation-v30-question-purpose", author["promptVersion"])
        result = reviewed(p, memories, raw)
        for env in (author, freeze_repair_input(author, raw, ["질문 목적 확인"], "parent"),
                    freeze_review_input(result), research_review.freeze(result, memories, 262144)):
            validate_execution_input(env)
            broken = deepcopy(env); broken["current"]["questionPurposePolicy"] = "changed"
            with self.assertRaises(ValueError): validate_execution_input(broken)
        review = research_review.freeze(result, memories, 262144)
        self.assertIn("assessment", review["outputSchema"]["required"])
        self.assertNotIn("모든 연구 변경이 근거와 일치할 때만", review["prompt"])
        old = deepcopy(p); old.pop("questionPurposePolicy"); old.pop("questionSourceHandoff")
        legacy = freeze_execution_input(old, [], memories, retrieval_trace=trace)
        before = deepcopy(legacy); validate_execution_input(legacy)
        self.assertEqual(before, legacy)
        self.assertNotIn("questionPurposes", legacy["outputSchema"]["required"])

    def test_new_question_needs_a_cited_purpose_but_silence_requires_no_invented_question(self):
        p, memories, _, _, raw = fixture()
        for mutation in ("missing-purpose", "wrong-index", "unread-source", "duplicate-work", "missing-period"):
            bad = deepcopy(raw)
            if mutation == "missing-purpose": bad["questionPurposes"] = []
            if mutation == "wrong-index": bad["questionPurposes"][0]["questionIndex"] = 1
            if mutation == "unread-source": bad["questionPurposes"][0]["evidenceIds"] = ["invented-source"]
            if mutation == "duplicate-work": bad["questionPurposes"][0]["evidenceTasks"] *= 2
            if mutation == "missing-period": bad["questionPurposes"][0]["evidenceTasks"][0].pop("period")
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): validate_plan(bad, p, memories)
        raw.update(questions=[], questionContinuations=[], questionPurposes=[])
        self.assertEqual([], validate_plan(raw, p, memories)["questionPurposes"])

    def test_source_references_survive_later_partial_or_failed_collection_without_resetting_repeat_budget(self):
        from digital_twin.modules.ai_orchestration.domain import research_repeat, question_evidence
        case = {"researchAttempts": 6, "latestSourceAnswer": {"status": "answered", "sources": [
            {"evidenceId": "q1", "materialFingerprint": "q1-body"}, {"evidenceId": "q2", "materialFingerprint": "q2-body"}]}}
        research_repeat.preserve_answer(case, {"status": "partial", "sources": [{"evidenceId": "q2", "materialFingerprint": "q2-revised"}]})
        self.assertEqual(["q2", "q1"], [r["evidenceId"] for r in handoff.source_references(case)])
        before = deepcopy(case["latestSourceAnswer"])
        self.assertFalse(research_repeat.preserve_answer(case, {"status": "unavailable", "sources": []}))
        self.assertEqual(before, case["latestSourceAnswer"])
        self.assertEqual(6, case["researchAttempts"])
        identity = question_evidence.source_identity(case)
        case["latestSourceAnswer"]["text"] = "같은 자료를 다르게 설명합니다."
        self.assertEqual(identity, question_evidence.source_identity(case))
        self.assertEqual(2, len(identity["sources"]))
        legacy = {"latestSourceAnswer": before, "answerMemory": [{"answer": {"sources": [{"evidenceId": "older"}]}}]}
        self.assertEqual(["q2", "older"], [r["evidenceId"] for r in handoff.source_references(legacy)])
        crowded = {"kind": "brain-case", "capability": "research", "caseId": "one",
            "latestSourceAnswer": {"sources": [{"evidenceId": str(i)} for i in range(8)]},
            "answerMemory": [{"answer": {"sources": [{"evidenceId": "older"}]}}]}
        self.assertEqual(8, len(handoff.source_references(crowded)))
        self.assertEqual(1, question_evidence.question_index([crowded])[0]["sourceAnswer"]["omittedSourceCount"])

    def test_read_question_document_cannot_be_dropped_or_used_to_waive_waiting_requirements(self):
        from question_assessment_checks import fixture as source_fixture
        from digital_twin.modules.ai_orchestration.application.working_retrieval import author_packet
        from digital_twin.modules.ai_orchestration.domain import question_retrieval
        p, memories, trace, _, raw = fixture()
        base, session, _, _ = source_fixture()
        seen = {f["id"]: f for f in p["facts"]}
        report = next(f for f in base["facts"] if f["id"] == "report-2025")
        seen[report["id"]] = report
        # Replay the observed defect: a document was read, then the router kept only reports.
        ids = {"quote-1", report["id"]}
        trace = deepcopy(trace)
        trace[-1]["decision"]["selectedFactIds"] = sorted(ids)
        trace[-1]["decision"]["questionScopes"][0]["selectedFactIds"] = [report["id"]]
        selected = author_packet(session, p, ids, seen, memories, trace,
                                 p["retrieval"]["status"], contract=question_retrieval)
        self.assertIn("official-document", {f["id"] for f in selected["facts"]})
        self.assertEqual(["official-document"], selected["workingEvidence"]["requiredQuestionSourceFactIds"])
        self.assertEqual(question_retrieval.research_scope(trace, p["retrieval"]["status"]), selected["researchScope"])
        self.assertNotIn("official-document", selected["researchScope"]["questions"][0]["selectedFactIds"])
        handoff.validate(selected, memories)
        self.assertEqual("wait", validate_plan(raw, selected, memories)["caseReviews"][0]["action"])
        # Even if source delivery adds readable evidence, it cannot change ready/waiting.
        raw["caseReviews"][0]["action"] = "answered"
        with self.assertRaises(ValueError): validate_plan(raw, p, memories)
        broken = deepcopy(p); broken["questionSourceHandoff"]["questions"][0]["factIds"] = ["foreign"]
        with self.assertRaises(ValueError): handoff.validate(broken, memories)

    def test_separate_review_keeps_valid_partial_answer_and_rejects_dependent_knowledge(self):
        p, memories, _, case, raw = fixture()
        result = reviewed(p, memories, raw)
        self.assertEqual("partially-accepted", result["researchQuality"]["status"])
        self.assertTrue(parts.approved(result)); self.assertFalse(parts.approved(result, "knowledge"))
        answer = question_assessment.assessment(case, {"taskId": "task"}, result,
            result["caseReviews"][0], result["questionResolutions"][0], stamp())
        self.assertEqual("partial", answer["status"])
        from digital_twin.modules.ai_orchestration.domain.memory_reference import brief
        remembered = brief({**case, "questionAssessment": answer}, "memories")
        self.assertEqual(answer["answerParts"], remembered["questionAssessment"]["answerParts"])
        self.assertEqual(raw["questionResolutions"][0]["missingRequirements"], answer["missingRequirements"])
        # The separate writer cannot treat question approval as knowledge approval.
        from digital_twin.infrastructure.transactions.ai_control_research_knowledge import AIControlResearchKnowledge
        from unittest.mock import patch
        agenda = Mock(); agenda.proof.return_value = {"current": p, "retrievalTrace": []}
        agenda.research_proof.return_value = False
        writer = AIControlResearchKnowledge(Mock(), agenda, Mock()); writer.question_progress = Mock(return_value={})
        proposal = deepcopy(result); proposal["knowledgeResearch"] = {"claims": [{"businessThesisIndex": -1}]}
        with patch("digital_twin.infrastructure.transactions.ai_control_research_knowledge.write_progress", return_value={"events": []}), \
             patch("digital_twin.infrastructure.transactions.ai_control_research_knowledge.write_research") as save:
            self.assertEqual("review-required", writer.record(Mock(), {"taskId": "task"}, proposal)["status"])
            save.assert_not_called()
            self.assertEqual("knowledge", agenda.research_proof.call_args.kwargs["section"])
        bad = deepcopy(result); bad["knowledgeResearch"] = {"claims": [{"businessThesisIndex": 0}]}
        reply = deepcopy(result["researchQuality"]["review"])
        reply["assessment"]["accepted"] = False; reply["knowledge"]["accepted"] = True
        self.assertEqual({"assessment": False, "knowledge": False}, parts.admissions(bad, reply))
        bad["knowledgeResearch"] = {"claims": [{"businessThesisIndex": -1}]}
        self.assertEqual({"assessment": False, "knowledge": True}, parts.admissions(bad, reply))
        for reply in ({"version": research_review.CHAIN_VERSION, "accepted": True, "reason": "이전 버전의 전체 승인입니다."},
                      {"version": parts.VERSION, "assessment": {"accepted": True, "reason": "짧음"}, "knowledge": {}}):
            self.assertEqual("rejected", research_review.accept(result, reply, "review")["status"])

    def test_purpose_is_remembered_and_source_planner_uses_it_without_spawning_each_evidence_task(self):
        from digital_twin.modules.ai_orchestration.domain.brain_management import new_case
        from digital_twin.modules.ai_orchestration.domain.memory_reference import brief
        p, memories, _, case, raw = fixture()
        result = reviewed(p, memories, raw)
        job = {**case, "taskId": "task", "name": "Test"}
        child = new_case(job, result, raw["questions"][0]["question"], "research", stamp())
        child["kind"] = "brain-case"
        self.assertEqual(raw["questionPurposes"][0]["evidenceTasks"], child["questionPurpose"]["evidenceTasks"])
        self.assertEqual(child["questionPurpose"], brief(child, "memories")["questionPurpose"])
        plan = research_work.freeze(job, [child]); validate_execution_input(plan)
        self.assertEqual(research_work.PURPOSE_PROMPT_VERSION, plan["promptVersion"])
        self.assertIn("자료 작업마다 새 질문·작업을 생성하지 마세요", plan["prompt"])
        self.assertNotIn("questionContract", child)  # Source planner still owns the immutable completion contract.
        from digital_twin.modules.ai_orchestration.application.research import execute_research
        from types import SimpleNamespace
        source_job = {**child, "taskId": "source-work", "name": "Test"}
        owner = Mock(); owner.run.return_value = SimpleNamespace(run_id="run", to_dict=lambda: {"status": "completed"})
        store = Mock(); store.get_run.return_value = None
        execute_research(source_job, store, lambda: owner)
        tasks = owner.run.call_args.args[2]["researchPlan"]["tasks"]
        self.assertEqual(1, len(tasks))
        self.assertIn(child["questionPurpose"]["decisionRelevance"], tasks[0]["purpose"])
        self.assertEqual(1, len(tasks[0]["sourceFocus"]["missingRequirements"]))
        legacy = deepcopy(tasks[0]); legacy["purpose"] = "이미 동결된 조사 목적입니다."
        store.get_run.return_value = {"status": "research-cooldown", "executedPlan": {
            "tasks": [legacy], "sourceExploration": {"version": "question-source-exploration-v1"}}}
        execute_research(source_job, store, lambda: owner)
        self.assertEqual(legacy, owner.run.call_args.args[2]["researchPlan"]["tasks"][0])


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class QuestionPurposeStorageTests(unittest.TestCase):
    def setUp(self):
        from digital_twin.infrastructure.settings import load_local_env
        from mysql_fixtures import mysql_test_settings
        load_local_env(); mysql_test_settings()
        from test_ai_brain_agenda import BrainAgendaStorageTests
        self.storage = BrainAgendaStorageTests(); self.storage.setUp(); self.addCleanup(self.storage.tearDown)
        self.control, self.agenda = self.storage.control, self.storage.brain

    def draft(self, assessment=True):
        from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
        p, memories, trace, case, raw = fixture()
        self.control.seed(self.storage.subject); job = self.control.claim(); p["taskId"] = job["taskId"]
        case.update(createdAt=stamp(), kind="question", researchAttempts=6,
                    completionCriterion=case["questionContract"]["answerCriterion"], origin={key: "original"
                        for key in ("taskId", "executionInputId", "capturedAt", "summary", "hypothesis", "counterEvidence", "quality")})
        with self.agenda.transaction() as connection: self.agenda.save(connection, case, "initial", "created")
        memories[0]["revision"] = case["revision"]; p["questionEvidenceIndex"][0]["revision"] = case["revision"]
        author = freeze_execution_input(p, [], memories, retrieval_trace=trace)
        author_id = self.control.save_execution_input(job, author)
        call = self.control.begin_call("independent-observation", author["promptHash"], job["taskId"], author_id)
        self.control.finish_call(call)
        result = reviewed(p, memories, raw, assessment=assessment); result["executionInputId"] = author_id
        result["quality"] = local_quality(result)
        frozen = research_review.freeze(result, author["researchResults"], 262144)
        review_id = self.control.save_execution_input(job, frozen)
        call = self.control.begin_call("independent-observation", frozen["promptHash"], job["taskId"], review_id)
        self.control.finish_call(call)
        result["researchQuality"] = research_review.accept(result, result["researchQuality"]["review"], review_id)
        return job, case, result

    def test_partial_review_commits_parent_answer_and_one_purposeful_child_atomically(self):
        job, case, result = self.draft()
        original = self.control.agenda_writer
        def failing(connection, job, result, children):
            original(connection, job, result, children)
            raise RuntimeError("rollback purpose")
        self.control.agenda_writer = failing
        with self.assertRaisesRegex(RuntimeError, "rollback purpose"): self.control.complete(job, result, [])
        with self.agenda.connect() as c:
            self.assertEqual(1, c.execute("SELECT COUNT(*) n FROM ai_brain_cases").fetchone()["n"])
        self.control.agenda_writer = original
        self.assertTrue(self.control.complete(job, result, [])); self.assertFalse(self.control.complete(job, result, []))
        with self.agenda.connect() as c:
            parent = self.agenda.read(c, case["caseId"], job["accountId"], job["symbol"])
            child = self.agenda.read(c, parent["inquiryContinuation"]["caseId"], job["accountId"], job["symbol"])
            self.assertEqual(1, c.execute("SELECT COUNT(*) n FROM ai_control_tasks WHERE capability='research'").fetchone()["n"])
        self.assertEqual("partial", parent["questionAssessment"]["status"])
        self.assertEqual(case["questionContract"], parent["questionContract"])
        self.assertEqual(6, parent["researchAttempts"])
        self.assertEqual(result["questionPurposes"][0]["decisionRelevance"], child["questionPurpose"]["decisionRelevance"])
        memory = {r["caseId"]: r for r in self.agenda.memory(job["accountId"], job["symbol"], job["worldId"])
                  if r.get("kind") == "brain-case"}
        self.assertEqual(child["questionPurpose"], memory[child["caseId"]]["questionPurpose"])

    def test_rejected_question_review_and_forged_partial_receipt_cannot_write_answers_or_children(self):
        job, case, result = self.draft(assessment=False)
        forged = deepcopy(result); forged["researchQuality"].update(status="partially-accepted", admissions={"assessment": True, "knowledge": False})
        with self.assertRaises(ValueError): self.control.complete(job, forged, [])
        self.assertTrue(self.control.complete(job, result, []))
        with self.agenda.connect() as c:
            parent = self.agenda.read(c, case["caseId"], job["accountId"], job["symbol"])
            self.assertEqual(1, c.execute("SELECT COUNT(*) n FROM ai_brain_cases").fetchone()["n"])
            self.assertEqual(0, c.execute("SELECT COUNT(*) n FROM ai_control_tasks WHERE capability='research'").fetchone()["n"])
        self.assertNotIn("questionAssessment", parent); self.assertNotIn("inquiryContinuation", parent)
