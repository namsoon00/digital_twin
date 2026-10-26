"""Answer lineage is reviewed, bounded and transactional, including partial answers."""
from copy import deepcopy
import os
import unittest

from digital_twin.modules.ai_orchestration.domain import research_chain, research_chain_prompt, research_review
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input)
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan, stamp


def fixture(answered=False):
    from test_question_answer_parts import QuestionAnswerPartsTests
    from digital_twin.modules.ai_orchestration.domain import execution_lanes, evidence_readiness, evidence_purpose
    p, memories, trace, case, raw = QuestionAnswerPartsTests().fixture(answered)
    p = evidence_purpose.annotate(evidence_readiness.annotate(execution_lanes.annotate(p, {"capability": "observe"})))
    p.update(researchChainPolicy=research_chain.VERSION, analysisPurpose="")
    raw["questions"] = [{"question": "같은 분기의 매출 증가에서 가격과 판매량의 기여를 공식 자료로 구분할 수 있는가?",
        "capability": "research", "research": {"queryTerms": ["quarterly revenue price volume"],
        "sourceTypes": ["official-filing"], "maxAgeMinutes": 527040}}]
    raw["questionContinuations"] = [{"parentCaseId": case["caseId"], "answerIndex": 0, "questionIndex": 0,
        "relation": "cause", "rationale": "매출 비교에서 확인한 변화를 가격과 판매량의 설명으로 나누어 확인합니다.",
        "expectedInsight": "판매량 확대인지 가격 상승인지에 따라 성장 설명의 지속 조건을 구분할 수 있습니다.",
        "contract": {"target": "매출 증감의 가격 및 판매량 설명", "period": "2026년 2분기와 전년 동분기",
        "answerCriterion": "해당 기간 공식 원문의 가격·판매량 관련 설명과 공개 범위를 확인"}}]
    return p, memories, trace, case, raw


def assessed(packet, memories, raw):
    result = validate_plan(raw, packet, memories)
    result.update(input=packet, executionInputId="chain-author", observedAt=packet["capturedAt"])
    result["researchQuality"] = research_review.accept(result, {"version": research_review.CHAIN_VERSION,
        "accepted": True, "reason": "확인된 부모 답변과 원문에서 새로운 질문으로 이어지는 근거를 검토했습니다."}, "chain-review")
    return result


class AnswerLedResearchTests(unittest.TestCase):
    def test_new_author_repair_and_independent_review_are_frozen_while_legacy_inputs_stay_exact(self):
        p, memories, trace, _, raw = fixture()
        author = freeze_execution_input(p, [], memories, retrieval_trace=trace)
        self.assertEqual(research_chain_prompt.PROMPT_VERSION, author["promptVersion"])
        result = assessed(p, memories, raw)
        for envelope in (author, freeze_repair_input(author, raw, ["후속 질문의 연결 검토"], "parent"),
                         freeze_review_input(result), research_review.freeze(result, memories, 262144)):
            validate_execution_input(envelope)
            self.assertIn("후속", envelope["prompt"])
            damaged = deepcopy(envelope); damaged["current"]["researchChainPolicy"] = "changed"
            with self.assertRaises(ValueError): validate_execution_input(damaged)
        old = deepcopy(p); old.pop("researchChainPolicy")
        legacy = freeze_execution_input(old, [], memories, retrieval_trace=trace)
        saved = deepcopy(legacy)
        validate_execution_input(legacy)
        self.assertNotIn("questionContinuations", legacy["outputSchema"]["required"])
        self.assertEqual(saved, legacy)
        from test_business_insight_progress import source_packet
        from digital_twin.modules.ai_orchestration.domain import partial_judgment, question_answer, execution_lanes
        for company in (False, True):
            active = execution_lanes.annotate(source_packet(), {"capability": "observe"})
            active.update(questionAnswerPolicy=question_answer.VERSION, researchChainPolicy=research_chain.VERSION,
                          questionWorkPolicy="original-question-assessment-v1")
            if company:
                active.update(companyResearchPolicy="company-research-cycle-v1", partialJudgmentPolicy=partial_judgment.VERSION)
            current = freeze_execution_input(active, [], [])
            self.assertEqual(research_chain_prompt.PROMPT_VERSION, current["promptVersion"])
            validate_execution_input(current)
            validate_execution_input(freeze_repair_input(current, {}, ["추가 질문의 원문 확인"], "parent"))

    def test_partial_answer_advances_without_dropping_original_gaps_and_rejects_false_lineage(self):
        p, memories, _, _, raw = fixture()
        value = assessed(p, memories, raw)
        self.assertEqual("wait", value["caseReviews"][0]["action"])
        self.assertEqual(raw["questionResolutions"][0]["missingRequirements"], value["questionResolutions"][0]["missingRequirements"])
        for change in ("foreign-parent", "missing-answer", "foreign-evidence", "drop-gap", "same-question", "same-contract", "unlinked"):
            with self.subTest(change=change):
                bad = deepcopy(raw)
                link = bad["questionContinuations"][0]
                if change == "foreign-parent": link["parentCaseId"] = "other-account-question"
                if change == "missing-answer": link["answerIndex"] = 2
                if change == "foreign-evidence": bad["questionResolutions"][0]["answerParts"]["confirmed"][0]["evidenceIds"] = ["previous-ai-answer"]
                if change == "drop-gap": bad["questionResolutions"][0]["missingRequirements"] = []
                if change == "same-question": bad["questions"][0]["question"] = memories[0]["question"]
                if change == "same-contract": link["contract"] = deepcopy(memories[0]["questionContract"])
                if change == "unlinked": bad["questionContinuations"] = []
                with self.assertRaises(ValueError): validate_plan(bad, p, memories)

    def test_silence_existing_successor_and_depth_limit_do_not_force_more_questions(self):
        p, memories, _, _, raw = fixture()
        raw["questions"], raw["questionContinuations"] = [], []
        self.assertEqual([], validate_plan(raw, p, memories)["questionContinuations"])
        for state in ({"inquiryContinuation": {"caseId": "existing-child"}}, {"inquiryLineage": {"depth": research_chain.MAX_DEPTH}}):
            p, memories, _, _, raw = fixture()
            memories[0].update(state)
            with self.assertRaises(ValueError): validate_plan(raw, p, memories)

    def test_second_step_retains_question_answer_path_in_bounded_memory_without_making_it_evidence(self):
        from digital_twin.modules.ai_orchestration.domain.memory_reference import brief
        p, memories, _, case, raw = fixture()
        result = assessed(p, memories, raw)
        first = research_chain.lineage(case, result["questionContinuations"][0], result)
        child = {**case, "caseId": "second", "question": raw["questions"][0]["question"],
                 "questionContract": raw["questionContinuations"][0]["contract"], "inquiryLineage": first}
        result["questionResolutions"][0]["caseId"] = "second"
        continuation = {**result["questionContinuations"][0], "parentCaseId": "second"}
        second = research_chain.lineage(child, continuation, result)
        self.assertEqual(2, second["depth"])
        self.assertEqual(case["caseId"], second["rootCaseId"])
        self.assertEqual([case["question"], child["question"]], [r["question"] for r in second["path"]])
        self.assertEqual(first["parentAnswer"], brief(child, "memories")["inquiryLineage"]["parentAnswer"])
        self.assertEqual("historical-research-lineage-only", first["authority"])
        compact = research_chain.memory_lineage(second)
        self.assertEqual(1, len(compact["path"]))
        self.assertEqual(1, compact["omittedAncestorCount"])
        self.assertEqual(2, len(compact["ancestorQuestionKeys"]))
        from digital_twin.modules.ai_orchestration.domain.memory_reference import continuity
        large = deepcopy(second)
        large.update(rationale="가" * 400, expectedInsight="나" * 400,
                     parentAnswer={"text": "다" * 400, "evidenceIds": ["e" * 64 + str(i) for i in range(4)]})
        large["path"] = [{"caseId": str(i) * 64, "question": "라" * 500, "answer": "마" * 400,
                          "contractKey": "f" * 64} for i in range(6)]
        continuity([], [{**case, "inquiryLineage": large}] * 5)

    def test_generic_or_rejected_research_review_does_not_approve_continuation(self):
        p, memories, _, _, raw = fixture()
        result = assessed(p, memories, raw)
        self.assertTrue(research_review.needed(result))
        for version, accepted in ((research_review.ANSWER_VERSION, True), (research_review.CHAIN_VERSION, False)):
            quality = research_review.accept(result, {"version": version, "accepted": accepted,
                "reason": "다음 질문의 새로운 의미를 근거와 함께 확인할 수 없습니다."}, "review")
            self.assertEqual("rejected", quality["status"])
        frozen = research_review.freeze(result, memories, 262144)
        frozen["proposal"]["questionContinuations"][0]["rationale"] = "다른 근거를 가진 요청으로 교체했습니다."
        with self.assertRaises(ValueError): validate_execution_input(frozen)

    def test_progress_notification_shows_parent_answer_and_expected_insight_without_new_authority(self):
        from digital_twin.modules.ai_orchestration.domain.research_progress import research_progress
        from digital_twin.modules.notifications.application.research_progress_message import render_research_progress
        p, memories, _, case, raw = fixture()
        result = assessed(p, memories, raw)
        case = {**case, "inquiryLineage": research_chain.lineage(case, result["questionContinuations"][0], result)}
        event = {"eventId": "event", "taskId": "task", "stage": "created", "revision": 1, "at": p["capturedAt"]}
        value = research_progress(case, event)
        self.assertEqual("research-status-only", value["authority"])
        text = render_research_progress(value)
        self.assertIn("출발점이 된 답변", text)
        self.assertIn(raw["questionContinuations"][0]["expectedInsight"], text)


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class ResearchChainStorageTests(unittest.TestCase):
    def setUp(self):
        from digital_twin.infrastructure.settings import load_local_env
        from mysql_fixtures import mysql_test_settings
        load_local_env(); mysql_test_settings()
        from test_ai_brain_agenda import BrainAgendaStorageTests
        self.storage = BrainAgendaStorageTests(); self.storage.setUp()
        self.addCleanup(self.storage.tearDown)
        self.control, self.agenda = self.storage.control, self.storage.brain

    def draft(self, accepted=True):
        from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
        p, memories, trace, case, raw = fixture()
        self.control.seed(self.storage.subject); job = self.control.claim()
        p.update(taskId=job["taskId"])
        case.update(createdAt=stamp(), kind="question", researchAttempts=6,
                    completionCriterion=case["questionContract"]["answerCriterion"], origin={key: "original"
                        for key in ("taskId", "executionInputId", "capturedAt", "summary", "hypothesis", "counterEvidence", "quality")})
        with self.agenda.transaction() as connection: self.agenda.save(connection, case, "initial", "created")
        memories[0]["revision"] = case["revision"]
        p["questionEvidenceIndex"][0]["revision"] = case["revision"]
        envelope = freeze_execution_input(p, [], memories, retrieval_trace=trace)
        input_id = self.control.save_execution_input(job, envelope)
        call = self.control.begin_call("independent-observation", envelope["promptHash"], job["taskId"], input_id)
        self.control.finish_call(call)
        result = assessed(p, memories, raw); result["executionInputId"] = input_id
        result["quality"] = local_quality(result)
        review = research_review.freeze(result, envelope["researchResults"], 262144)
        review_id = self.control.save_execution_input(job, review)
        call = self.control.begin_call("independent-observation", review["promptHash"], job["taskId"], review_id)
        self.control.finish_call(call)
        result["researchQuality"] = research_review.accept(result, {"version": research_review.CHAIN_VERSION,
            "accepted": accepted, "reason": "확인된 답과 새 질문의 연결을 공식 근거 및 기존 조사 범위와 대조했습니다."}, review_id)
        return job, case, result

    def test_parent_answer_child_and_source_queue_commit_together_and_retry_creates_no_duplicate(self):
        job, before, result = self.draft()
        original = self.control.agenda_writer
        def fail_after_write(connection, job, result, children):
            original(connection, job, result, children)
            raise RuntimeError("rollback chain")
        self.control.agenda_writer = fail_after_write
        with self.assertRaisesRegex(RuntimeError, "rollback chain"): self.control.complete(job, result, [])
        with self.agenda.connect() as connection:
            parent = self.agenda.read(connection, before["caseId"], job["accountId"], job["symbol"])
            self.assertNotIn("inquiryContinuation", parent)
            self.assertEqual(1, connection.execute("SELECT COUNT(*) n FROM ai_brain_cases").fetchone()["n"])
        self.control.agenda_writer = original
        self.assertTrue(self.control.complete(job, result, []))
        self.assertFalse(self.control.complete(job, result, []))
        with self.agenda.connect() as connection:
            parent = self.agenda.read(connection, before["caseId"], job["accountId"], job["symbol"])
            child = self.agenda.read(connection, parent["inquiryContinuation"]["caseId"], job["accountId"], job["symbol"])
            queued = connection.execute("SELECT payload_json FROM ai_control_tasks WHERE task_id=%s", (child["lastResearch"]["taskId"],)).fetchone()
        import json
        self.assertEqual(before["questionContract"], parent["questionContract"])
        self.assertEqual(6, parent["researchAttempts"])
        self.assertEqual("partial", parent["questionAssessment"]["status"])
        self.assertEqual(1, child["researchAttempts"])
        self.assertEqual(parent["caseId"], child["inquiryLineage"]["parentCaseId"])
        self.assertEqual(child["questionContract"], json.loads(queued["payload_json"])["questionContract"])
        self.assertEqual(result["researchQuality"]["reviewInputId"], child["inquiryLineage"]["researchReviewInputId"])
        memory = {row["caseId"]: row for row in self.agenda.memory(job["accountId"], job["symbol"], job["worldId"])
                  if row.get("kind") == "brain-case"}
        self.assertEqual(child["inquiryLineage"], memory[child["caseId"]]["inquiryLineage"])
        self.assertEqual(parent["inquiryContinuation"], memory[parent["caseId"]]["inquiryContinuation"])

    def test_rejected_review_keeps_parent_gaps_without_creating_a_child_or_source_job(self):
        job, before, result = self.draft(accepted=False)
        self.assertTrue(self.control.complete(job, result, []))
        with self.agenda.connect() as connection:
            parent = self.agenda.read(connection, before["caseId"], job["accountId"], job["symbol"])
            self.assertEqual(1, connection.execute("SELECT COUNT(*) n FROM ai_brain_cases").fetchone()["n"])
            self.assertEqual(0, connection.execute("SELECT COUNT(*) n FROM ai_control_tasks WHERE capability='research'").fetchone()["n"])
        self.assertNotIn("inquiryContinuation", parent)
        self.assertEqual(before["questionContract"], parent["questionContract"])

    def test_changed_parent_review_identity_rolls_back_instead_of_attaching_to_another_answer(self):
        job, before, result = self.draft()
        with self.agenda.transaction() as connection:
            changed = self.agenda.read(connection, before["caseId"], job["accountId"], job["symbol"], lock=True)
            self.agenda.save(connection, changed, "concurrent-review", "assessment")
        with self.assertRaises(ValueError): self.control.complete(job, result, [])
        with self.agenda.connect() as connection:
            self.assertEqual(1, connection.execute("SELECT COUNT(*) n FROM ai_brain_cases").fetchone()["n"])
