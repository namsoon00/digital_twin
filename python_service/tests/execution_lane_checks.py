"""Regressions for independent market work and single-question source assessment."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from ai_insight_fixtures import plan, review
from question_assessment_checks import fixture, raw_assessment
from test_scoped_knowledge_research import scoped_decision
from test_research_knowledge import context
from digital_twin.modules.ai_orchestration.domain import execution_lanes as lanes, lane_retrieval, question_assessment
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input, LANE_PROMPT_VERSION,
)
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.ai_orchestration.domain.observation_wait import disposition, retry_key
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
from digital_twin.modules.ai_orchestration.domain.retrieval import ReadRequestError


def check_lanes(test):
    p, session, q, fact = fixture()
    p = lanes.annotate(p, {"capability": "observe"})
    p["researchKnowledge"] = lanes.compact_knowledge(context(p))
    scope = {"caseId": q["caseId"], "status": "waiting", "selectedFactIds": [fact["id"]],
             "missingEvidence": ["같은 기간 영업현금흐름 원문이 필요합니다."]}
    saved = []
    def save(value):
        validate_execution_input(value); saved.append(value); return "read-" + str(len(saved))
    question = retrieve_working_evidence(session, p, [], [q],
        Mock(return_value=scoped_decision("assess", [fact["id"]], [scope])), save, 262144)
    test.assertEqual("assessment-ready", question[0]["retrieval"]["status"])
    test.assertEqual(scope["missingEvidence"], question[3][0]["decision"]["missingEvidence"])
    result = validate_plan(raw_assessment(q, missing=scope["missingEvidence"]), question[0], question[2])
    test.assertEqual("not-applicable", local_quality({**result, "input": question[0]})["status"])
    ready = {**scope, "status": "ready", "missingEvidence": []}
    test.assertEqual("finish", lane_retrieval.validate_decision(scoped_decision("assess", [fact["id"]], [ready]))["action"])
    for bad in (scoped_decision("assess", [fact["id"]], [{**ready, "missingEvidence": ["still missing"]}]),
                scoped_decision("assess", [fact["id"]], [scope], ["contradictory gap"]),
                scoped_decision("assess", [fact["id"]], [scope, {**scope, "caseId": "other"}])):
        with test.assertRaises(ReadRequestError):lane_retrieval.validate_decision(bad)
    cases = question_assessment.prepare([{**q, "caseId": "case-" + str(i)} for i in range(6)], session.question_work_inventory())
    selected, window = lane_retrieval.research_window(cases)
    test.assertEqual(1, len(selected));test.assertEqual(5, window["excludedCaseCount"])
    test.assertEqual(5, len(window["excludedCaseIds"]))
    test.assertEqual(q["questionContract"], selected[0]["questionContract"])
    market = lanes.annotate(p, {"capability": lanes.MARKET});market.pop("questionWorkPolicy", None)
    frozen = []
    read = Mock(return_value=scoped_decision("finish", ["quote-1", fact["id"]]))
    observation = retrieve_working_evidence(session, market, [], lanes.market_memories([q]), read,
        lambda value: frozen.append(value) or "market-read", 262144)
    test.assertEqual([], frozen[0]["retrievalContext"]["questionIndex"])
    test.assertEqual([], observation[0]["researchScope"]["questions"])
    test.assertFalse(observation[0]["researchScope"].get("assessmentOnly"))
    test.assertTrue(any(row.get("kind") == "research-context" for row in observation[2]))
    test.assertEqual(0, frozen[0]["outputSchema"]["properties"]["questionScopes"]["maxItems"])
    raw = {**plan(), "caseReviews": [], "questionResolutions": [],
           "knowledgeResearch": {"claims": [], "coverageNote": "과거 연구는 현재 자료의 범위 안에서만 참고합니다."}}
    validated = validate_plan(raw, observation[0], observation[2])
    test.assertNotEqual("not-applicable", local_quality({**validated, "input": observation[0]})["status"])
    author = freeze_execution_input(observation[0], observation[1], observation[2], retrieval_trace=observation[3])
    test.assertEqual(LANE_PROMPT_VERSION, author["promptVersion"])
    for value in [*saved, *frozen, author, freeze_repair_input(author, raw, ["bounded repair"], "parent"),
                  freeze_review_input({**validated, "input": observation[0]})]:
        validate_execution_input(value)
    test.assertEqual(0, author["outputSchema"]["properties"]["caseReviews"]["maxItems"])
    check_identities(test, market, q, session)
    check_controller(test, session, q, raw)
    from test_ai_control import AIControlTests
    prepared = question_assessment.prepare([q], session.question_work_inventory())[0]
    parked = {**q, "evidenceProgress": {"workKey": prepared["questionWorkKey"]}}
    quiet, quiet_store, quiet_author = AIControlTests().runner(evidence=Mock(return_value=session),
        brain_memory=Mock(return_value=[parked]), read_planner=Mock(side_effect=AssertionError("parked question must not read")))
    quiet.settings["aiObservationLanePolicy"] = lanes.VERSION
    quiet_store.last_processing_outcome.return_value = {}
    test.assertEqual("unchanged-wait", quiet.run_once()["status"])
    quiet_author.assert_not_called();quiet.read_planner.assert_not_called()
    # An oversized valid selection is saved as capacity wait, with no extra
    # correction call and no false question progress/answer.
    from digital_twin.modules.reasoning.contracts import EvidenceContractError
    from digital_twin.modules.ai_orchestration.domain.question_evidence import checkpoints
    with patch("digital_twin.modules.ai_orchestration.application.working_retrieval.freeze_execution_input",
               side_effect=EvidenceContractError("observation execution exceeds context budget")):
        decide = Mock(return_value=scoped_decision("assess", [fact["id"]], [scope]))
        blocked = retrieve_working_evidence(session, p, [], [q], decide, save, 262144)
    test.assertEqual(1, decide.call_count)
    test.assertEqual("context-budget", blocked[0]["retrieval"]["status"])
    test.assertEqual([], checkpoints(blocked[0], blocked[3]))


def check_identities(test, market, q, session):
    original = retry_key(market, lanes.market_memories([q]))
    test.assertEqual(original, retry_key(market, lanes.market_memories([{**q, "question": "바뀐 연구 표현입니다."}])))
    changed = deepcopy(market);changed["facts"][0]["currentPrice"] = 999
    test.assertNotEqual(original, retry_key(changed, []))
    question = lanes.annotate(market, {"capability": "observe"})
    prepared = question_assessment.prepare([q], session.question_work_inventory())
    first = retry_key(question, prepared)
    test.assertEqual(first, retry_key(lanes.annotate(changed, {"capability": "observe"}), prepared))
    revised = deepcopy(q);revised["questionContract"]["period"] = "새로운 원래 대상 기간"
    test.assertNotEqual(first, retry_key(question, question_assessment.prepare([revised], session.question_work_inventory())))
    test.assertNotEqual(first, original)
    reviewed = {**q, "questionAssessment": {"workKey": "original-source-work", "researchReviewInputId": "accepted-review"}}
    test.assertNotEqual(original, retry_key(market, lanes.market_memories([reviewed])))
    reworded = deepcopy(reviewed);reworded["questionAssessment"]["answer"] = "같은 검토의 문장만 바꿨습니다."
    test.assertEqual(retry_key(market, lanes.market_memories([reviewed])), retry_key(market, lanes.market_memories([reworded])))
    observation_case = {**q, "capability": "observe", "reviewDue": True}
    test.assertEqual(observation_case, lanes.market_memories([observation_case])[0])
    test.assertEqual("research-context", lanes.scoped_memories([observation_case], False)[0]["kind"])


def check_controller(test, session, q, raw):
    from test_ai_control import AIControlTests
    service, store, author = AIControlTests().runner(evidence=Mock(return_value=session), brain_memory=Mock(return_value=[q]),
        read_planner=Mock(return_value=scoped_decision("finish", ["quote-1", "official-document"])),
        reviewer=Mock(return_value=review()), knowledge_context=lambda p: context(p))
    service.settings["aiObservationLanePolicy"] = lanes.VERSION
    store.claim.return_value["capability"] = lanes.MARKET
    store.last_processing_outcome.return_value = {}
    author.return_value = raw
    outcome = service.run_once()
    test.assertEqual("completed", outcome["status"], outcome)
    result = store.complete.call_args.args[1]
    test.assertEqual(LANE_PROMPT_VERSION, result["executionPromptVersion"])
    test.assertNotEqual("not-applicable", result["quality"]["status"])
    test.assertEqual(lanes.MARKET, store.complete.call_args.args[2][-1]["capability"])
    store.memory.assert_called_with(q["accountId"], q["symbol"], lanes.MARKET)
    packet = result["input"]
    # Stable failures are parked by lane; a changed price/source opens only
    # the market lane. No failed question can lend it a repeat outcome.
    original_capture = lanes.annotate(session.packet(), {"capability": lanes.MARKET})
    store.last_processing_outcome.return_value = disposition(packet, retry_key(original_capture, []), "output-invalid", ["invalid-output"])
    author.reset_mock();service.read_planner.reset_mock()
    test.assertEqual("unchanged-wait", service.run_once()["status"])
    author.assert_not_called();service.read_planner.assert_not_called()
    store.last_processing_outcome.return_value = {}
    author.return_value = {**raw, "summary": ""}
    store.fail.return_value = {"status": "failed"}
    test.assertEqual("failed", service.run_once()["status"])
    test.assertTrue(store.fail.call_args.kwargs["terminal"])
    failure = store.fail.call_args.kwargs["result"]["processingOutcome"]
    test.assertEqual("output-invalid", failure["status"])
    store.last_processing_outcome.return_value = failure
    author.reset_mock();service.read_planner.reset_mock()
    test.assertEqual("unchanged-wait", service.run_once()["status"])
    author.assert_not_called();service.read_planner.assert_not_called()


def check_native_lanes(test):
    test.clean()
    test.store.runtime_settings["aiObservationLanePolicy"] = lanes.VERSION
    future = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat().replace("+00:00", "Z")
    question = test.insert("question-wait", "observe", available_at=future)
    test.insert("market-now", lanes.MARKET)
    job = test.store.claim()
    test.assertEqual(lanes.MARKET, job["capability"])
    test.assertTrue(test.store.complete(job, {"summary": "현재 시세의 확인된 의미입니다.", "quality": {"status": "observation-only"},
        "input": {"facts": [], "worldId": "test-world", "sourceSnapshotId": "market-snapshot"}}, []))
    test.assertEqual(1, len(test.store.memory("test-dispatch", "TEST", lanes.MARKET)))
    test.assertEqual([], test.store.memory("test-dispatch", "TEST"))
    from digital_twin.modules.ai_orchestration.infrastructure.mysql_company_preparation import MySQLPreparationAI
    preparation = MySQLPreparationAI(test.store.runtime_settings).progress(question)
    test.assertEqual("complete", preparation["status"])
    test.assertEqual("market-snapshot", preparation["sourceSnapshotId"])
    status = test.store.status("test-dispatch")
    test.assertEqual(lanes.MARKET, status["executionHealth"]["executionLane"])
    test.assertTrue(status["executionHealth"]["lastJudgmentAt"])
    market, research = status["executionLanes"]["lanes"]
    test.assertEqual(1, market["authored"]);test.assertEqual(0, research["authored"])
    test.assertIsNone(test.store.claim())  # the question's cooldown was preserved
    test.insert("market-fail", lanes.MARKET)
    failed = test.store.claim()
    test.store.fail(failed, "validation:test", terminal=True,
        result={"processingOutcome": {"version": "test", "retryKey": "market-only"}})
    test.assertEqual("market-only", test.store.last_processing_outcome("test-dispatch", "TEST", lanes.MARKET)["retryKey"])
    test.assertEqual({}, test.store.last_processing_outcome("test-dispatch", "TEST"))
    with test.store.connect() as c:
        row = c.execute("SELECT capability,available_at FROM ai_control_tasks WHERE status='pending' AND capability=%s", (lanes.MARKET,)).fetchone()
    test.assertEqual(lanes.MARKET, row["capability"])
    test.assertLess(datetime.fromisoformat(row["available_at"].replace("Z", "+00:00")), datetime.now(timezone.utc) + timedelta(minutes=16))
    test.clean()
    subject = {key: question[key] for key in ("accountId", "symbol", "name", "worldId")}
    for _ in range(3):test.store.seed(subject)
    with test.store.connect() as c:
        test.assertEqual(2, c.execute("SELECT COUNT(*) AS n FROM ai_control_tasks").fetchone()["n"])


def check_market_publication(test):
    from ai_insight_fixtures import observation, persist_review
    test.clean()
    result = observation()
    subject = result["input"]
    job = {**{key: subject[key] for key in ("accountId", "symbol", "name", "worldId")},
           "taskId": "market-publication", "capability": lanes.MARKET}
    with test.control.transaction() as c:test.control.insert(c, job)
    leased = test.control.claim()
    result["input"] = lanes.annotate({**result["input"], "taskId": job["taskId"]}, job)
    author = freeze_execution_input(result["input"], [], [])
    input_id = test.control.save_execution_input(leased, author)
    call_id = test.control.begin_call("independent-observation", author["promptHash"], leased["taskId"], input_id)
    test.control.finish_call(call_id)
    result.update(executionInputId=input_id, executionPromptVersion=LANE_PROMPT_VERSION)
    persist_review(test.control, leased, result)
    test.assertTrue(test.control.complete(leased, result, []))
    test.assertEqual("queued", result["publication"]["status"])
    test.assertTrue(test.control.review_proof(result["quality"]["reviewInputId"]))
    test.clean()


def check_market_knowledge_storage(test):
    import json
    from test_business_research import business_packet
    from test_question_evidence_flow import question
    from scoped_knowledge_storage_checks import completed_call
    from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession
    from digital_twin.modules.ai_orchestration.application.control import AIControlService
    from digital_twin.modules.ai_orchestration.domain.planning import stamp
    test.control.runtime_settings["aiObservationLanePolicy"] = lanes.VERSION
    source = business_packet();source["capturedAt"] = stamp();source["facts"][0]["sourceAsOf"] = source["capturedAt"]
    case = question(source);case.update(kind="question", createdAt=stamp(), reason="원래 원문을 기다립니다.",
        completionCriterion=case["questionContract"]["answerCriterion"], origin={"taskId": "original", "executionInputId": "original-input",
            "capturedAt": source["capturedAt"], "summary": "원래 연구 질문입니다.", "hypothesis": "", "counterEvidence": "", "quality": "research-only"})
    with test.agenda.transaction() as c:test.agenda.save(c, case, "original", "created")
    test.control.seed(test.fixture.subject)
    with test.control.transaction() as c:
        c.execute("UPDATE ai_control_tasks SET available_at='2099-01-01T00:00:00Z' WHERE capability='observe'")
    def execute(envelope, input_id):
        validate_execution_input(envelope)
        with test.control.connect() as c:
            row = c.execute("SELECT payload_json,attempts,lease_token FROM ai_control_tasks WHERE task_id=%s",
                (envelope["current"]["taskId"],)).fetchone()
        job = {**json.loads(row["payload_json"]), "attempts": row["attempts"], "leaseToken": row["lease_token"]}
        completed_call(test, envelope, job, input_id)
        if envelope["promptVersion"] == lane_retrieval.VERSION:
            return scoped_decision("finish", [row["factId"] for row in envelope["retrievalContext"]["knownFacts"]])
        if envelope["promptVersion"].startswith("independent-observation-review"):
            return review()
        return {**plan(), "caseReviews": [], "questionResolutions": [],
            "knowledgeResearch": {"claims": [], "coverageNote": "현재 관찰에서는 기존 연구를 읽기만 합니다."}}
    service = AIControlService(test.control, lambda:[test.fixture.subject],
        lambda _:ObservationEvidenceSession(source, source["facts"]), execute, lambda _: {}, lambda *_: [],
        test.control.runtime_settings, reviewer=execute, read_planner=execute,
        brain_memory=test.agenda.memory, knowledge_context=test.handoff.context, knowledge_refresh=test.handoff.refresh)
    outcome = service.run_once()
    test.assertEqual("completed", outcome["status"], outcome)
    tasks = test.control.status(test.fixture.subject["accountId"])["tasks"]
    result = next(task["result"] for task in tasks if task["capability"] == lanes.MARKET and task["result"].get("summary"))
    test.assertEqual("accepted", result["quality"]["status"])
    test.assertEqual("recorded", result["brain"]["status"])
    test.assertTrue(result["knowledge"]["progress"]["progressId"])
    test.assertNotIn("questions", result["knowledge"])
    with test.agenda.connect() as c:
        stored = test.agenda.read(c, case["caseId"], source["accountId"], source["symbol"])
    test.assertEqual(case["revision"], stored["revision"])
    test.assertNotIn("evidenceProgress", stored)
