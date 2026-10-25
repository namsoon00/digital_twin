"""Native SQL/controller rehearsals; the model is stubbed, source/history are not."""
import copy
from unittest.mock import Mock
from ai_insight_fixtures import plan
from test_business_research import business_packet, raw_research
from test_research_knowledge import proposal
from test_scoped_knowledge_research import scoped_decision, research_session
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.ai_orchestration.domain.planning import stamp
from digital_twin.modules.ai_orchestration.domain import research_review


def completed_call(test, envelope, job, input_id):
    call = test.control.begin_call("independent-observation", envelope["promptHash"], job["taskId"], input_id)
    test.control.finish_call(call)


def check_wait_progress(test):
    test.control.seed(test.fixture.subject); job = test.control.claim()
    p = {**business_packet(), "taskId": job["taskId"], "capturedAt": stamp()}
    p["researchKnowledge"] = test.handoff.context(p)
    def decide(envelope, input_id):
        completed_call(test, envelope, job, input_id)
        if not envelope["retrievalContext"]["trace"]:
            decision = scoped_decision("read", [row["factId"] for row in envelope["retrievalContext"]["knownFacts"]])
            decision["requests"] = [{"tool": "read_report", "factId": "report-2025", "metrics": ["revenue"]}]
            return decision
        decision = scoped_decision("read", [row["factId"] for row in envelope["retrievalContext"]["knownFacts"]],
                                   missing=["원래 질문의 동일 기간 공식 비교 자료가 필요합니다."])
        decision["requests"] = [{"tool": "read_fact", "category": "research", "factId": "report-2025"}]
        return decision
    selected, _, _, trace = retrieve_working_evidence(research_session(p), p, [], [], decide,
        lambda value: test.control.save_execution_input(job, value), 262144, max_rounds=2,
        record_round=lambda step: test.control.save_retrieval_round(job, step))
    result = {"status": "awaiting-evidence", "input": selected, "retrievalInputIds": [row["inputId"] for row in trace]}
    def failing(connection, job, result):
        test.handoff.record(connection, job, result)
        raise RuntimeError("after progress write")
    test.control.knowledge_writer = failing
    with test.assertRaises(RuntimeError):
        test.control.complete(job, result, [])
    test.assertEqual([], test.repository.progress(p, stamp()))
    test.control.knowledge_writer = test.handoff.record
    forged = copy.deepcopy(result); forged["input"]["facts"][-1]["reportedValues"]["revenue"] = 999
    with test.assertRaises(ValueError):
        test.control.complete(job, forged, [])
    test.assertTrue(test.control.complete(job, result, []))
    test.assertEqual("progress-only", result["knowledge"]["status"])
    test.assertEqual([], test.repository.recent(p["accountId"]))
    test.assertEqual([], test.repository.progress(p, p["capturedAt"]))
    later = {**p, "capturedAt": stamp()}
    recalled = test.handoff.context(later)
    test.assertEqual(result["knowledge"]["progress"]["progressId"], recalled["progress"][0]["progressId"])
    test.assertEqual("not-an-answer-or-thesis", recalled["progress"][0]["qualification"])
    test.assertEqual("awaiting-knowledge", test.service.status(p["accountId"])["activity"]["status"])
    test.assertFalse(test.control.complete(job, result, []))
    with test.repository.connect() as c:
        count = c.execute("SELECT COUNT(*) AS n FROM domain_events WHERE name=%s AND aggregate_id=%s",
            ("research_knowledge.progress_recorded", recalled["progress"][0]["progressId"])).fetchone()["n"]
    test.assertEqual(1, count)


def check_partial_controller(test):
    from digital_twin.modules.ai_orchestration.public import AIControlService
    subject = test.fixture.subject
    source = {**business_packet(), "capturedAt": stamp()}
    recorded_authors = []
    def evidence(job):
        p = {**copy.deepcopy(source), "taskId": job["taskId"]}
        return research_session(p)
    def execute(envelope, input_id):
        from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input
        validate_execution_input(envelope)
        with test.control.connect() as connection:
            row = connection.execute("SELECT payload_json,attempts,lease_token FROM ai_control_tasks WHERE task_id=%s",
                (envelope["current"]["taskId"],)).fetchone()
        import json
        job = {**json.loads(row["payload_json"]), "attempts": row["attempts"], "leaseToken": row["lease_token"]}
        completed_call(test, envelope, job, input_id)
        if envelope["promptVersion"] in research_review.SUPPORTED_VERSIONS:
            return {"version": envelope["promptVersion"], "accepted": True,
                    "reason": "선택 근거와 미확인 조건을 분리한 잠정 가설이며 반론과 반증 조건이 보존됐습니다."}
        if envelope["promptVersion"].startswith("independent-observation-retrieval"):
            return scoped_decision("research", [row["factId"] for row in envelope["retrievalContext"]["knownFacts"]],
                missing=["동일 기간 현금 회수의 비교 자료가 필요합니다."])
        recorded_authors.append(copy.deepcopy(envelope))
        old = envelope["current"]["researchKnowledge"]["claims"]
        knowledge = proposal(businessThesisIndex=0) if not old else proposal(claimId=old[0]["claimId"], disposition="revise",
            reason="새 보고 자료를 확인해 기존 설명의 적용 조건과 반론을 다시 검토합니다.")
        raw = {**plan(), "businessResearch": raw_research() if not old else {"theses": [], "reviews": [],
            "coverageNote": "이전 설명을 새 근거로 재검토하되 추가 사업 지표 계약은 등록하지 않습니다."},
            "knowledgeResearch": knowledge, "questionResolutions": [], "questions": [],
            "followUpConditions": [], "observations": []}
        raw["notification"]["send"] = False
        return raw
    service = AIControlService(test.control, lambda: [subject], evidence, execute, lambda _: {}, lambda *_: [],
        {**test.control.runtime_settings, "aiControlBudgetEnabled": "false"},
        reviewer=execute, read_planner=execute, read_round_budget=lambda: 3,
        knowledge_context=test.handoff.context, knowledge_refresh=test.handoff.refresh)
    test.assertEqual("completed", service.run_once()["status"])
    first = test.repository.recent(subject["accountId"])[0]
    test.assertEqual(1, first["revision"])
    test.assertEqual("accepted", first["reviewReceipt"]["status"])
    test.assertEqual(first["executionInputId"], first["reviewReceipt"]["authorInputId"])
    test.assertEqual("research-only", recorded_authors[0]["current"]["researchScope"]["mode"])
    test.assertEqual(1, len(test.repository.progress(source, stamp())))
    # A completed internal research draft must not create another paid read,
    # author or reviewer simply because its next observation is due.
    with test.control.transaction() as connection:
        connection.execute("UPDATE ai_control_tasks SET available_at=%s WHERE account_id=%s AND symbol=%s AND capability='observe' AND status='pending'",
            (stamp(), subject["accountId"], subject["symbol"]))
    test.assertEqual("unchanged-wait", service.run_once()["status"])
    test.assertEqual(1, len(recorded_authors))
    test.assertEqual(1, len(test.repository.progress(source, stamp())))
    with test.control.transaction() as connection:
        connection.execute("UPDATE ai_control_tasks SET available_at=%s WHERE account_id=%s AND symbol=%s AND capability='observe' AND status='pending'",
            (stamp(), subject["accountId"], subject["symbol"]))
    source["capturedAt"] = stamp()
    source["sourceSnapshotId"] = "new-independent-source-snapshot"
    source["sourceSnapshots"][source["worldId"]] = source["sourceSnapshotId"]
    for fact in source["facts"]:
        fact["sourceSnapshotId"] = source["sourceSnapshotId"]
    source["facts"][-1]["reportedValues"]["revenue"] = 90
    from test_business_research import select_evidence
    source["facts"], source["coverage"] = select_evidence(source["facts"])
    test.assertEqual("completed", service.run_once()["status"])
    second = test.repository.recent(subject["accountId"])[0]
    test.assertEqual(2, second["revision"])
    test.assertEqual(first["revisionId"], second["previousRevisionId"])
    test.assertNotEqual(first["reviewReceipt"]["reviewInputId"], second["reviewReceipt"]["reviewInputId"])
    test.assertEqual(first["revisionId"], recorded_authors[1]["current"]["researchKnowledge"]["claims"][0]["revisionId"])
    test.assertTrue(recorded_authors[1]["current"]["researchKnowledge"]["progress"])
    test.assertEqual("knowledge-recalled", test.service.status(subject["accountId"])["activity"]["status"])
    with test.repository.connect() as connection:
        original = connection.execute("SELECT payload_json FROM research_knowledge_revisions WHERE revision_id=%s", (first["revisionId"],)).fetchone()
    import json
    test.assertEqual(first, json.loads(original["payload_json"]))
