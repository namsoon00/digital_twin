"""Rehearse source-preserving reads, partial assessment and evidence-only renewal."""
from copy import deepcopy
from unittest.mock import Mock
import hashlib

from ai_insight_fixtures import plan
from test_business_research import business_packet
from test_research_knowledge import context
from test_question_evidence_flow import question
from test_scoped_knowledge_research import scoped_decision
from test_research_judgment_continuity import document
from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession, content_hash
from digital_twin.modules.reasoning.domain.observation_evidence import select_evidence, canonical_json
from digital_twin.modules.news_intelligence.domain.research_document_reference import research_document_reference
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.ai_orchestration.domain import question_assessment, question_retrieval, memory_reference, question_evidence
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, freeze_repair_input, validate_execution_input
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan


def fixture():
    p = business_packet(); p.update(capturedAt="2026-09-01T00:00:00Z", memoryReadPolicy=memory_reference.VERSION,
        questionWorkPolicy=question_assessment.VERSION, taskId="assessment-task")
    item = document(); ref = research_document_reference(item)
    fact = {"id": "official-document", "kind": "research-evidence", "symbol": p["symbol"],
        "sourceWorldId": p["worldId"], "sourceSnapshotId": p["sourceSnapshotId"],
        "url": item.url, "label": "공식 분기 보고서", "documentVerified": True,
        "judgementEvidenceUsable": False, "historicalDocumentReference": ref,
        "analysisSummary": "duplicated archive " * 1500}
    p["facts"], p["coverage"] = select_evidence(p["facts"] + [fact])
    session = ObservationEvidenceSession(p, [*p["facts"], fact])
    q = question(p)
    q["latestSourceAnswer"] = {"status": "answered", "sources": [{"evidenceId": item.evidence_id, "materialFingerprint": "original-body"}]}
    q["answerResolution"] = {"status": "answered", "assessmentFingerprint": "source-review"}
    return p, session, q, fact


def raw_assessment(case, action="wait", ids=("official-document",), missing=("같은 기간의 영업현금흐름 원문이 필요합니다.",)):
    return {**plan(), "questions": [], "observations": [], "followUpConditions": [],
        "notification": {**plan()["notification"], "send": False},
        "businessResearch": {"theses": [], "reviews": [], "coverageNote": "원래 질문의 사실 확인과 남은 자료를 구분합니다."},
        "knowledgeResearch": {"claims": [], "coverageNote": "새 가설을 강제로 만들지 않고 원래 질문을 평가합니다."},
        "caseReviews": [{"caseId": case["caseId"], "action": action,
            "reason": "공식 분기 보고에서 같은 기간의 매출 비교를 확인했으며 현금흐름은 추가 확인이 필요합니다.",
            "evidenceIds": list(ids), "nextCheckMinutes": 1440,
            "research": {"queryTerms": [], "sourceTypes": [], "maxAgeMinutes": 0}}],
        "questionResolutions": [{"caseId": case["caseId"], "disposition": "not-applicable" if action == "answered" else "defer",
            "targetIndex": -1, "reason": "원래 질문의 사실 확인을 평가하고 투자 가설의 검증과 구분합니다.",
            "evidenceIds": list(ids), "missingRequirements": list(missing),
            "judgmentImpact": {"effect": "insufficient", "reason": "매출 사실은 확인했지만 현금 회수에 관한 설명을 바꿀 근거는 부족합니다."}}]}


def check_question_assessment(test):
    check_active_binding(test)
    p, session, q, fact = fixture(); p["researchKnowledge"] = context(p)
    source_ref = deepcopy(fact["historicalDocumentReference"])
    view = session.read_document(fact["id"])["facts"][0]
    test.assertEqual(source_ref, view["historicalDocumentReference"])
    test.assertEqual(content_hash({**fact, "evidenceCategory": "research"}), view["evidenceView"]["sourceFactHash"])
    test.assertLess(len(canonical_json(view)), len(canonical_json(fact)) / 2)
    bad = deepcopy(view); bad["historicalDocumentReference"]["text"] += " invented"
    with test.assertRaises(ValueError):session.select([fact["id"]], views={fact["id"]: bad})
    with test.assertRaises(ValueError):session.read_document("another-account-document")
    frozen = []
    scope = {"caseId": q["caseId"], "status": "waiting", "selectedFactIds": [fact["id"]],
             "missingEvidence": ["같은 기간의 영업현금흐름 원문이 필요합니다."]}
    selected, history, memories, trace = retrieve_working_evidence(session, p, [], [q],
        Mock(return_value=scoped_decision("assess", [fact["id"]], [scope], scope["missingEvidence"])),
        lambda envelope: frozen.append(envelope) or "read-input", 262144)
    test.assertEqual(question_retrieval.VERSION, frozen[0]["promptVersion"])
    test.assertEqual(source_ref, next(row for row in frozen[0]["current"]["facts"] if row["id"] == fact["id"])["historicalDocumentReference"])
    test.assertEqual("assessment-ready", selected["retrieval"]["status"])
    for requests in (None, 7, "not-an-array"):
        malformed = scoped_decision("assess", [fact["id"]], [scope], scope["missingEvidence"])
        malformed["requests"] = requests
        from digital_twin.modules.ai_orchestration.domain.retrieval import ReadRequestError
        with test.assertRaises(ReadRequestError):question_retrieval.validate_decision(malformed)
    def explicit_document_read(envelope, _):
        if not envelope["retrievalContext"]["trace"]:
            decision = scoped_decision("read", [fact["id"]], [scope], scope["missingEvidence"])
            decision["requests"] = [{"tool": "read_document", "factId": fact["id"]}]
            return decision
        return scoped_decision("assess", [fact["id"]], [scope], scope["missingEvidence"])
    reread = retrieve_working_evidence(session, p, [], [q], explicit_document_read, lambda _:"document-read", 262144)
    test.assertEqual(source_ref, reread[3][0]["reads"][0]["result"]["facts"][0]["historicalDocumentReference"])
    validate_execution_input(freeze_execution_input(reread[0], reread[1], reread[2], retrieval_trace=reread[3]))
    test.assertTrue(selected["researchScope"]["assessmentOnly"])
    raw = raw_assessment(q)
    result = validate_plan(raw, selected, memories)
    test.assertEqual(q["questionContract"], result["questionResolutions"][0]["sourceQuestion"]["questionContract"])
    from digital_twin.modules.ai_orchestration.domain import research_review
    from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
    result.update(input=selected, executionInputId="author-input")
    test.assertEqual("not-applicable", local_quality(result)["status"])
    test.assertTrue(research_review.needed(result))
    factual = {**q, "question": "공식 분기 보고에서 같은 기간의 매출을 어떻게 비교하는가?",
        "questionContract": {"target": "동일 분기 연결 매출 비교", "period": "2026-06-30 및 2025-06-30 종료 3개월",
                             "answerCriterion": "공식 원문의 같은 기간 매출 금액과 USD 표시 단위를 확인"}}
    ready = {"caseId": factual["caseId"], "status": "ready", "selectedFactIds": [fact["id"]], "missingEvidence": []}
    answered = retrieve_working_evidence(session, p, [], [factual],
        Mock(return_value=scoped_decision("finish", [fact["id"]], [ready])), lambda _:"factual-read", 262144)
    answer_raw = raw_assessment(factual, "answered", [fact["id"]], [])
    answer_raw["caseReviews"][0]["reason"] = "공식 분기 원문의 동일 기간 매출과 USD 표시 단위를 확인했습니다."
    answer_result = validate_plan(answer_raw, answered[0], answered[2])
    answer_result.update(input=answered[0], executionInputId="factual-author")
    test.assertNotIn("conditionValidation", answer_result)
    test.assertEqual("not-applicable", local_quality(answer_result)["status"])
    test.assertTrue(research_review.needed(answer_result))
    envelope = freeze_execution_input(selected, history, memories, retrieval_trace=trace)
    for value in (frozen[0], envelope, freeze_repair_input(envelope, raw, ["review partial"], "parent"),
                  research_review.freeze(result, memories, 262144)):
        validate_execution_input(value)
    for mutate in (lambda r:r["caseReviews"][0].update(action="answered"),
                   lambda r:r["questionResolutions"][0].update(missingRequirements=[]),
                   lambda r:r["questionResolutions"][0].update(judgmentImpact={"effect": "guaranteed", "reason": "기존 설명이 입증되었습니다."}),
                   lambda r:r["notification"].update(send=True)):
        broken = deepcopy(raw); mutate(broken)
        with test.assertRaises(ValueError):validate_plan(broken, selected, memories)
    progress = question_evidence.checkpoints(selected, trace)[0]["progress"]
    test.assertTrue(progress["workKey"])
    parked = {**q, "evidenceProgress": progress, "revision": 99, "nextCheckAt": "2030-01-01T00:00:00Z"}
    prepared = question_assessment.prepare([parked], session.question_work_inventory())
    test.assertTrue(prepared[0]["questionWorkUnchanged"])
    same_quote = deepcopy(p)
    next(row for row in same_quote["facts"] if row["kind"] == "stock")["currentPrice"] = 999
    same_session = ObservationEvidenceSession(same_quote, [*same_quote["facts"], fact])
    test.assertEqual(session.question_work_inventory(), same_session.question_work_inventory())
    polled = deepcopy(fact);polled["historicalDocumentReference"]["observedAt"] = "2026-10-19T00:00:00Z"
    polled_session = ObservationEvidenceSession(p, [row for row in p["facts"] if row["id"] != fact["id"]] + [polled])
    test.assertEqual(session.question_work_inventory(), polled_session.question_work_inventory())
    newcomers = [{**q, "caseId": "unreviewed-" + str(i)} for i in range(6)]
    active, window = question_retrieval.research_window(question_assessment.prepare([parked, *newcomers], session.question_work_inventory()))
    test.assertEqual(3, len(active));test.assertNotIn(q["caseId"], window["includedCaseIds"])
    test.assertEqual([q["caseId"]], window["unchangedCaseIds"])
    from test_ai_control import AIControlTests
    from digital_twin.modules.ai_orchestration.domain.observation_wait import retry_key, disposition
    first_work = question_assessment.prepare([q], session.question_work_inventory())
    first_packet = {**p, "questionWorkPending": [first_work[0]["questionWorkKey"]]}
    prior = disposition(first_packet, retry_key(first_packet, first_work), "research-reviewed", [])
    service, store, planner = AIControlTests().runner(evidence=Mock(return_value=session),
        brain_memory=Mock(return_value=[parked]), read_planner=Mock(side_effect=AssertionError("no repeated reads")),
        knowledge_context=lambda current: deepcopy(p["researchKnowledge"]))
    store.last_processing_outcome = Mock(return_value=prior)
    test.assertEqual("unchanged-wait", service.run_once()["status"])
    planner.assert_not_called();service.read_planner.assert_not_called()
    changed_fact = deepcopy(fact);changed_fact["historicalDocumentReference"]["text"] += " New exact source passage."
    changed_fact["historicalDocumentReference"]["passageTextHash"] = hashlib.sha256(changed_fact["historicalDocumentReference"]["text"].encode()).hexdigest()
    changed = ObservationEvidenceSession(p, [row for row in p["facts"] if row["id"] != fact["id"]] + [changed_fact])
    test.assertFalse(question_assessment.prepare([parked], changed.question_work_inventory())[0]["questionWorkUnchanged"])
    from digital_twin.modules.ai_orchestration.domain.research_evidence_use import usable_fact
    report = {**deepcopy(fact), "id": "structured-report", "historicalDocumentReference": None,
        "reportObservationId": "original-report", "periodEnd": "2025-12-31", "reportedValues": {"revenue": 100},
        "metricUnits": {"revenue": "USD"}, "metricProvenance": {"revenue": {"periodStart": "2025-01-01"}},
        "questionSourceReference": {"version": "question-source-reference-v1", "sourceKind": "structured-report",
            "reportObservationId": "original-report", "periodEnd": "2025-12-31", "evidenceId": "structured-source"},
        "evidenceGovernance": {"verificationStatus": "verified-primary", "sourceTrustState": "trusted", "validationState": "ready"},
        "promptEvidenceAdmission": {"reasonCodes": ["evidence-stale"]}}
    from digital_twin.modules.reasoning.domain.observation_document_view import document_view
    report_view = document_view(report)
    test.assertEqual(report["reportedValues"], report_view["reportedValues"])
    test.assertEqual(report["metricProvenance"], report_view["metricProvenance"])
    test.assertTrue(usable_fact(report_view, "reportedValues.revenue"))
    test.assertFalse(usable_fact(report_view, "currentPrice"))
    report_view["evidenceGovernance"]["conflictingEvidenceIds"] = ["conflicting-source"]
    test.assertFalse(usable_fact(report_view))
    from digital_twin.modules.ai_orchestration.domain.execution_input import INDEXED_PROMPT_VERSION, INDEXED_REPAIR_PROMPT_VERSION
    old_packet = business_packet();old_packet["memoryReadPolicy"] = memory_reference.VERSION
    older = freeze_execution_input(old_packet, [], [])
    older["promptVersion"] = INDEXED_PROMPT_VERSION;validate_execution_input(older)
    old_repair = freeze_repair_input(older, plan(), ["historical repair"], "old-parent")
    old_repair["promptVersion"] = INDEXED_REPAIR_PROMPT_VERSION;validate_execution_input(old_repair)
    from digital_twin.modules.ai_orchestration.domain import indexed_retrieval
    old = indexed_retrieval.freeze(business_packet(), {"trace": [], "questionIndex": [], "knownFacts": [], "reportIndex": []}, 262144)
    validate_execution_input(old)


def check_active_binding(test):
    from digital_twin.modules.reasoning.domain.observation_binding import observation_binding
    from digital_twin.modules.reasoning.infrastructure.observation_binding import active_observation_binding
    from digital_twin.modules.reasoning.application.observation_evidence.deployment import DeploymentObservationEvidenceReader
    row = {"active_deployment_id": "current", "delivery_deployment_id": "current", "version": 4,
           "graph_store_binding": "current-graph", "deployment_status": "active", "engine_version": "v2"}
    binding = observation_binding(row)
    connection = Mock();connection.execute.return_value.fetchone.return_value = row
    test.assertEqual(binding, active_observation_binding(connection))
    test.assertTrue(connection.execute.call_args.args[0].startswith("SELECT"))
    for changed in ({}, {**row, "delivery_deployment_id": "old"}, {**row, "graph_store_binding": ""},
                    {**row, "deployment_status": "candidate"}):
        with test.assertRaises(ValueError):observation_binding(changed)
    p, session, _, _ = fixture()
    promoted = {**binding, "deploymentId": "promoted", "graphStoreBinding": "promoted-graph", "controlVersion": 5}
    read_binding = Mock(side_effect=[binding, binding, promoted, promoted])
    factory = Mock(return_value=Mock(capture_session=Mock(return_value=session)))
    reader = DeploymentObservationEvidenceReader(read_binding, factory)
    test.assertEqual(binding, reader.capture_session(p).packet()["reasoningBinding"])
    test.assertEqual(promoted, reader.capture_session(p).packet()["reasoningBinding"])
    test.assertEqual([binding, promoted], [call.args[0] for call in factory.call_args_list])
    test.assertNotIn("reasoningBinding", session.packet())
    reader.binding_reader = Mock(side_effect=[binding, promoted])
    with test.assertRaisesRegex(ValueError, "deployment changed"):
        reader.capture_session(p)
    from digital_twin.modules.reasoning.contracts import EvidenceReadError
    reader.binding_reader = Mock(side_effect=RuntimeError("private registry detail"))
    with test.assertRaises(EvidenceReadError) as error:reader.capture_session(p)
    test.assertNotIn("private", str(error.exception))
    from digital_twin.modules.ai_orchestration.domain.recovery import retry_delays
    for code in ("evidence-contract:active-observation-graph-binding-unavailable",
                 "evidence-contract:observation-graph-deployment-changed-during-capture"):
        test.assertEqual((30, 1800), retry_delays("observe", code, 1))


def check_persisted_question_assessment(test, answered=False, accepted=True):
    from question_evidence_storage_checks import seed_question
    from scoped_knowledge_storage_checks import completed_call
    from digital_twin.modules.ai_orchestration.domain import research_review, research_work, research_repeat
    from digital_twin.modules.ai_orchestration.domain.planning import stamp
    from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
    job, p, case = seed_question(test)
    p.update(memoryReadPolicy=memory_reference.VERSION, questionWorkPolicy=question_assessment.VERSION)
    if answered:
        case.update(question="공식 연간 보고에서 매출과 통화를 확인할 수 있는가?", questionContract={
            "target": "2025년 연결 매출과 통화", "period": "2025-01-01부터 2025-12-31까지 연간",
            "answerCriterion": "공식 연간 보고의 매출 금액과 통화 확인"})
        case["completionCriterion"] = case["questionContract"]["answerCriterion"]
    case["answerResolution"] = {"status": "answered", "assessmentFingerprint": "previous-source-review"}
    with test.agenda.transaction() as connection:test.agenda.save(connection, case, "source-review", "research-returned")
    memory = test.agenda.memory(p["accountId"], p["symbol"], p["worldId"])
    scope = {"caseId": case["caseId"], "status": "ready" if answered else "waiting", "selectedFactIds": ["report-2025"],
             "missingEvidence": [] if answered else ["같은 기간의 영업현금흐름 원문이 필요합니다."]}
    def decide(envelope, input_id):
        completed_call(test, envelope, job, input_id)
        return scoped_decision("finish" if answered else "assess", ["report-2025"], [scope], scope["missingEvidence"])
    selected, history, memories, trace = retrieve_working_evidence(ObservationEvidenceSession(p, p["facts"]), p, [], memory,
        decide, lambda value:test.control.save_execution_input(job, value), 262144,
        record_round=lambda step:test.control.save_retrieval_round(job, step))
    envelope = freeze_execution_input(selected, history, memories, retrieval_trace=trace)
    input_id = test.control.save_execution_input(job, envelope);completed_call(test, envelope, job, input_id)
    raw = raw_assessment(case, "answered" if answered else "wait", ["report-2025"], scope["missingEvidence"])
    if answered:
        raw["caseReviews"][0]["reason"] = "원래 연간 보고 기간의 매출과 동일 통화를 확인했습니다."
    result = {**validate_plan(raw, selected, memories), "input": selected, "executionInputId": input_id, "observedAt": stamp()}
    result["quality"] = local_quality(result)
    test.assertEqual("not-applicable", result["quality"]["status"], "a factual answer must not need a market-price condition or a forced thesis")
    test.assertNotIn("conditionValidation", result)
    review = research_review.freeze(result, memories, 262144)
    review_id = test.control.save_execution_input(job, review);completed_call(test, review, job, review_id)
    result["researchQuality"] = research_review.accept(result, {"version": research_review.VERSION, "accepted": accepted,
        "reason": "원래 질문의 현재 답변 범위와 부족분 및 설명에 미친 영향을 함께 대조했습니다."}, review_id)
    original_writer = test.control.agenda_writer
    def fail_after_agenda(connection, task, value, children):
        original_writer(connection, task, value, children)
        raise RuntimeError("rollback question assessment")
    test.control.agenda_writer = fail_after_agenda
    with test.assertRaises(RuntimeError):test.control.complete(job, result, [])
    with test.agenda.connect() as connection:
        test.assertNotIn("questionAssessment", test.agenda.read(connection, case["caseId"], p["accountId"], p["symbol"]))
    test.control.agenda_writer = original_writer
    test.assertTrue(test.control.complete(job, result, []));test.assertFalse(test.control.complete(job, result, []))
    with test.agenda.connect() as connection:
        saved = test.agenda.read(connection, case["caseId"], p["accountId"], p["symbol"])
    test.assertEqual(case["questionContract"], saved["questionContract"])
    if not accepted:
        test.assertNotIn("questionAssessment", saved)
        test.assertIn("question-independent-review-required", saved["reviewBlocker"]["errors"])
        return
    test.assertEqual("answered" if answered else "partial", saved["questionAssessment"]["status"])
    test.assertEqual(input_id, saved["questionAssessment"]["executionInputId"])
    test.assertEqual(result["summary"], saved["questionAssessment"]["judgment"]["summary"])
    test.assertEqual(raw["questionResolutions"][0]["judgmentImpact"], saved["questionAssessment"]["judgmentImpact"])
    test.assertEqual("answered", saved["answerResolution"]["status"], "the source owner's original receipt is preserved")
    if answered:
        from digital_twin.modules.ai_orchestration.domain.brain_management import new_case
        with test.agenda.transaction() as connection:
            for index in range(41):
                other = new_case(job, result, "새 자료를 기다리는 별개 질문 " + str(index), "research", stamp())
                test.agenda.save(connection, other, job["taskId"], "created")
        visible = test.agenda.status()["cases"]
        test.assertEqual(40, len(visible))
        test.assertEqual(saved["caseId"], visible[0]["caseId"], "completed answers stay visible despite a larger active backlog")
    if not answered:
        test.assertTrue(research_work.needs_research_plan(saved))
        test.assertTrue(any(row["caseId"] == saved["caseId"] for row in test.agenda.memory(p["accountId"], p["symbol"], p["worldId"], research_only=True)))
        research_repeat.initialize(saved)
        from digital_twin.modules.ai_orchestration.domain.question_recovery import gap_opportunity
        test.assertTrue(gap_opportunity(saved, "planned"))
        research_repeat.remember(saved, "planned")
        test.assertFalse(gap_opportunity(saved, "planned"))
        test.assertEqual("unchanged-research-material", research_repeat.admission(saved, "planned"))
