"""Real transaction checks with frozen inputs and stubbed completed model calls."""
from copy import deepcopy
from unittest.mock import Mock

from test_business_research import business_packet
from test_question_evidence_flow import question, waiting_scope
from test_scoped_knowledge_research import research_session, scoped_decision
from scoped_knowledge_storage_checks import completed_call
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.ai_orchestration.domain.planning import stamp


def seed_question(test):
    test.control.seed(test.fixture.subject); job = test.control.claim()
    p = {**business_packet(), "taskId": job["taskId"], "capturedAt": stamp()}
    p["researchKnowledge"] = test.handoff.context(p)
    case = question(p)
    case.update(kind="question", createdAt=stamp(), name="Test", reason="원래 기간의 공식 원문을 기다립니다.",
        completionCriterion=case["questionContract"]["answerCriterion"], origin={"taskId": "original", "executionInputId": "original-input",
            "capturedAt": p["capturedAt"], "summary": "원래 조사 질문", "hypothesis": "", "counterEvidence": "", "quality": "research-only"})
    with test.agenda.transaction() as connection:
        test.agenda.save(connection, case, "original", "created")
    return job, p, case


def read_for_case(test, job, p, case, action):
    memories = test.agenda.memory(p["accountId"], p["symbol"], p["worldId"])
    scope = waiting_scope(case) if action == "defer" else {
        "caseId": case["caseId"], "status": "ready", "selectedFactIds": ["report-2025"], "missingEvidence": []}
    def decide(envelope, input_id):
        completed_call(test, envelope, job, input_id)
        return scoped_decision(action, [row["factId"] for row in envelope["retrievalContext"]["knownFacts"]],
            [scope], scope["missingEvidence"])
    return retrieve_working_evidence(research_session(p), p, [], memories, decide,
        lambda envelope: test.control.save_execution_input(job, envelope), 262144,
        record_round=lambda step: test.control.save_retrieval_round(job, step))


def check_question_progress(test):
    job, p, case = seed_question(test)
    selected, _, _, trace = read_for_case(test, job, p, case, "defer")
    result = {"status": "awaiting-evidence", "input": selected, "retrievalInputIds": [step["inputId"] for step in trace]}
    # Mere read checkpoints never invoke the customer research notifier.
    test.agenda.progress_writer = Mock(side_effect=AssertionError("read progress is not an answer notification"))
    def read_case():
        with test.agenda.connect() as connection:
            return test.agenda.read(connection, case["caseId"], p["accountId"], p["symbol"])
    def fail_after_write(connection, task, value):
        test.handoff.record(connection, task, value)
        raise RuntimeError("rollback after question progress")
    test.control.knowledge_writer = fail_after_write
    with test.assertRaises(RuntimeError):
        test.control.complete(job, result, [])
    test.assertNotIn("evidenceProgress", read_case())
    test.control.knowledge_writer = test.handoff.record
    tampered = deepcopy(result)
    tampered["input"]["questionEvidenceIndex"][0]["questionContract"]["period"] = "다른 보고 기간"
    with test.assertRaises(ValueError):
        test.control.complete(job, tampered, [])
    test.assertTrue(test.control.complete(job, result, []))
    test.assertFalse(test.control.complete(job, result, []))
    saved = read_case()
    test.assertEqual([case["caseId"]], result["knowledge"]["questions"]["caseIds"])
    for field in ("questionContract", "answerResolution", "latestSourceAnswer", "researchAttempts", "nextCheckAt", "status"):
        test.assertEqual(case[field], saved[field])
    test.assertEqual(waiting_scope(case)["missingEvidence"], saved["evidenceProgress"]["missingRequirements"])
    test.agenda.progress_writer.assert_not_called()
    from digital_twin.modules.ai_orchestration.domain import research_work, research_repeat
    memory = next(row for row in test.agenda.memory(p["accountId"], p["symbol"], p["worldId"], research_only=True)
                  if row["kind"] == "brain-case")
    frozen = research_work.freeze({**job, "capability": "research-plan"}, [research_repeat.planning_memory(memory)])
    test.assertIn(saved["evidenceProgress"]["missingRequirements"][0], frozen["prompt"])
    test.assertEqual(case["questionContract"], frozen["researchCases"][0]["questionContract"])


def check_ready_question_answer(test, accepted=True):
    from ai_insight_fixtures import plan
    from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input
    from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
    from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
    from digital_twin.modules.ai_orchestration.domain import research_review
    from test_business_research import raw_research
    job, p, case = seed_question(test)
    # Test setup supplies an original factual question that the source can answer.
    case.update(question="공식 연간 매출의 보고값과 통화는 무엇인가?", questionContract={
        "target": "공식 연간 매출", "period": "2025년 연간", "answerCriterion": "공식 연간 보고의 매출 금액과 통화"})
    with test.agenda.transaction() as connection:
        test.agenda.save(connection, case, "original-contract", "created")
    selected, history, memories, trace = read_for_case(test, job, p, case, "finish")
    envelope = freeze_execution_input(selected, history, memories, retrieval_trace=trace)
    input_id = test.control.save_execution_input(job, envelope)
    completed_call(test, envelope, job, input_id)
    raw = {**plan(), "knowledgeResearch": {"claims": [], "coverageNote": "공식 보고의 매출 사실 확인만 완료합니다."},
        "businessResearch": raw_research(),
        "questions": [], "observations": [], "followUpConditions": [],
        "caseReviews": [{"caseId": case["caseId"], "action": "answered", "reason": "공식 연간 보고에서 매출 보고값과 통화를 확인했습니다.",
            "evidenceIds": ["report-2025"], "nextCheckMinutes": 1440,
            "research": {"queryTerms": [], "sourceTypes": [], "maxAgeMinutes": 0}}],
        "questionResolutions": [{"caseId": case["caseId"], "disposition": "not-applicable", "targetIndex": -1,
            "reason": "공식 수치 확인에 답했으며 투자 가설의 경험적 검증을 뜻하지 않습니다.", "evidenceIds": ["report-2025"]}]}
    raw["notification"]["send"] = False
    result = {**validate_plan(raw, selected, memories), "input": selected, "executionInputId": input_id, "observedAt": stamp()}
    result["quality"] = local_quality(result)
    test.assertEqual("observation-only", result["quality"]["status"], "customer observation quality cannot substitute for answer review")
    review = research_review.freeze(result, memories, 262144)
    review_id = test.control.save_execution_input(job, review)
    completed_call(test, review, job, review_id)
    result["researchQuality"] = research_review.accept(result, {"version": research_review.VERSION, "accepted": accepted,
        "reason": ("원래 보고 기간과 매출 지표 및 통화가 현재 인용한 공식 원문과 일치합니다." if accepted
                   else "원래 질문에 답하는 기간과 지표를 인용 원문에서 모두 확인하지 못했습니다.")}, review_id)
    test.control.outbox_writer = None
    test.assertTrue(test.control.complete(job, result, []))
    test.assertFalse(test.control.complete(job, result, []))
    with test.agenda.connect() as connection:
        saved = test.agenda.read(connection, case["caseId"], p["accountId"], p["symbol"])
        count = connection.execute("SELECT COUNT(*) AS n FROM ai_brain_case_events WHERE case_id=%s AND task_id=%s",
            (case["caseId"], job["taskId"])).fetchone()["n"]
    if accepted:
        test.assertEqual("answered", saved["status"])
        test.assertEqual(review_id, saved["lastAssessment"]["researchReviewInputId"])
        test.assertEqual("not-applicable", saved["hypothesisResolution"]["disposition"])
    else:
        test.assertEqual("waiting", saved["status"])
        test.assertNotIn("lastAssessment", saved)
        test.assertIn("question-independent-review-required", saved["reviewBlocker"]["errors"])
    test.assertEqual(case["questionContract"], saved["questionContract"])
    test.assertEqual(1, count, "the ready question is reviewed once; progress cannot overwrite that revision")
