"""Cross-owner loop regressions grouped under the existing curated test budget."""
import copy
import hashlib
import json
from unittest.mock import Mock, patch

from ai_insight_fixtures import packet, plan
from test_ai_brain_agenda import QUESTION, review_case
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan, stamp
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, validate_execution_input


def resolution(case, disposition="experiment", target=0):
    return {"caseId": case["caseId"], "disposition": disposition, "targetIndex": target,
            "reason": "확인된 근거의 충돌을 원래 질문에 연결하여 검증할 필요가 있습니다.", "evidenceIds": ["quote-1"]}


def assert_resolution_contracts(t):
    from digital_twin.modules.ai_orchestration.domain.execution_input import BUSINESS_PROMPT_VERSION, BUSINESS_REPAIR_PROMPT_VERSION
    from digital_twin.modules.ai_orchestration.domain.business_research import business_prompt, business_schema
    from digital_twin.modules.ai_orchestration.domain.insight_repair import repair_prompt
    p = packet()
    case = {**{key: p[key] for key in ("accountId", "symbol", "worldId")}, "caseId": "question", "kind": "brain-case",
        "capability": "research", "question": QUESTION["question"], "revision": 2, "reviewDue": True,
        "lastResearch": {"taskId": "research-task", "result": {"runId": "research-run", "status": "completed"}}}
    raw = {**plan(), "questions": [{**QUESTION, "capability": "develop-hypothesis"}],
           "caseReviews": [review_case(case, "answered")], "questionResolutions": [resolution(case)]}
    raw["questions"][0].pop("research")
    valid = validate_plan(raw, p, [case], require_resolution=True)
    t.assertEqual("research-run", valid["questionResolutions"][0]["sourceQuestion"]["research"]["result"]["runId"])
    from digital_twin.modules.ai_orchestration.domain.insight_contract import narrative_digest
    altered = copy.deepcopy(valid)
    altered["questionResolutions"][0]["disposition"] = "defer"
    t.assertNotEqual(narrative_digest(valid), narrative_digest(altered))
    for rows in ([], [resolution(case, target=1)], [resolution(case), resolution(case)],
                 [{**resolution(case), "caseId": "other"}], [resolution(case, "defer", 0)]):
        with t.subTest(rows=rows), t.assertRaises(ValueError):
            validate_plan({**raw, "questionResolutions": rows}, p, [case], require_resolution=True)
    stale = copy.deepcopy(p); stale["facts"][0]["judgementEvidenceUsable"] = False
    raw["caseReviews"][0]["action"] = "wait"
    with t.assertRaises(ValueError):
        validate_plan(raw, stale, [case], require_resolution=True)
    raw["questionResolutions"] = [resolution(case, "defer", -1)]
    t.assertEqual("defer", validate_plan(raw, stale, [case], require_resolution=True)["questionResolutions"][0]["disposition"])
    for version in (BUSINESS_PROMPT_VERSION, BUSINESS_REPAIR_PROMPT_VERSION):
        old = freeze_execution_input(p, [], [case]); old["promptVersion"] = version
        old["prompt"] = business_prompt(old["current"], old["previousAnalyses"], old["researchResults"])
        old["outputSchema"] = business_schema(old["current"], old["researchResults"])
        if version == BUSINESS_REPAIR_PROMPT_VERSION:
            old["repair"] = {"errors": [], "rejectedDraft": plan(), "parentInputId": "old", "comparisons": []}
            old["prompt"] = repair_prompt(old["prompt"], old["repair"])
        old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
        validate_execution_input(old)
        t.assertNotIn("questionResolutions", old["outputSchema"]["properties"])


def assert_question_loop(t):
    from digital_twin.infrastructure.transactions.ai_control_development import AIControlDevelopment
    from digital_twin.modules.news_intelligence.infrastructure.mysql_observation_development import MySQLObservationDevelopmentStore
    from digital_twin.modules.ai_orchestration.infrastructure.mysql_question_resolution import refresh_development
    from digital_twin.modules.model_registry.application.hypothesis_proposal_service import HypothesisProposalService
    from digital_twin.infrastructure.settings import runtime_settings
    source = MySQLObservationDevelopmentStore(runtime_settings())
    from digital_twin.modules.model_registry.domain.hypothesis_development import HypothesisDevelopmentCase
    candidate = HypothesisDevelopmentCase(case_id="candidate-case", fingerprint="candidate-fingerprint",
        account_id=t.subject["accountId"], symbol="TEST", title="검증할 가설", claim="관측한 충돌이 반복될 수 있습니다.",
        status="needs-revision", stage="compilation", blocked_reason="검증 조건에 필요한 자료가 부족합니다.")
    bridge = AIControlDevelopment(source, lambda key: candidate if key == candidate.case_id else None)
    t.control.development_writer = bridge.record
    try:
        task, result, children = t.observation()
        t.assertTrue(t.control.complete(task, result, children))
        research = t.control.claim()
        t.assertTrue(t.control.complete(research, {"status": "completed", "runId": "question-source-run"}, []))
        case = next(row for row in t.brain.memory(t.subject["accountId"], "TEST") if row["kind"] == "brain-case")
        raw = {**plan(), "questions": [{"question": "확인된 충돌이 반복되는지 격리된 가설로 시험할 수 있는가?", "capability": "develop-hypothesis"}],
               "caseReviews": [review_case(case, "answered")], "questionResolutions": [resolution(case)]}
        job, result, children = t.observation(raw, seed=False)
        from digital_twin.modules.ai_orchestration.domain.planning import observation_fingerprint
        result["inputFingerprint"] = observation_fingerprint(result["input"], [case])
        corrupted = copy.deepcopy(result); corrupted["questionResolutions"][0]["sourceQuestion"]["question"] = "altered"
        with t.assertRaises(ValueError):
            t.control.complete(job, corrupted, children)
        t.assertEqual([], source.observation_development_records(t.subject["accountId"], "TEST"))
        t.assertTrue(t.control.complete(job, result, children))
        record = source.observation_development_records(t.subject["accountId"], "TEST")[0]
        lineage = record["request"]["observationContext"]["sourceQuestions"][0]
        t.assertEqual(case["caseId"], lineage["caseId"])
        t.assertEqual("question-source-run", lineage["research"]["result"]["runId"])
        from digital_twin.modules.model_registry.domain.observation_development import observation_development_request
        from digital_twin.modules.ai_orchestration.domain.brain_management import case_identity, source_memory
        other = copy.deepcopy(result)
        other["developmentQuestions"] = ["다른 질문도 같은 날 기존 실험의 결과를 자신의 답으로 사용할 수 있는가?"]
        other["workQuestions"] = [{"question": other["developmentQuestions"][0], "capability": "develop-hypothesis"}]
        with t.control.transaction() as c:
            receipt = source.enqueue_observation_development_with_connection(c, observation_development_request(job, other))
            t.assertFalse(receipt["requestedQuestionMatched"])
            other["development"] = receipt
            t.brain.record_questions(c, job, other, [], source_memory(job, other), stamp(), {"caseIds": [], "deferred": []})
            other_id = case_identity(job["accountId"], job["symbol"], job["worldId"], "develop-hypothesis", other["developmentQuestions"][0])
            other_case = t.brain.read(c, other_id, job["accountId"], job["symbol"])
            t.assertNotIn("development", other_case)
            t.assertEqual("deferred-daily-budget", other_case["hypothesisResolution"]["state"])
        advisor, proposals, development = Mock(), Mock(), Mock()
        proposals.list_hypothesis_proposals.return_value = []
        advisor.propose.return_value = [{"claim": "단기 회복과 중기 흐름의 충돌이 반복될 수 있다.",
            "causalPath": ["단기 회복", "중기 흐름 충돌"], "supportingEvidenceIds": ["quote-1"]}]
        development.ingest_proposal.return_value = {"caseId": candidate.case_id, "status": candidate.status}
        service = HypothesisProposalService(proposals, advisor=advisor, development_service=development)
        output = service.propose(t.subject["accountId"], "TEST", record["request"]["question"], {},
                                 observation_context=record["request"]["observationContext"])
        t.assertEqual(record["request"]["question"]["questionId"], output["proposals"][0]["sourceQuestionId"])
        derived_case = HypothesisDevelopmentCase.from_proposal(output["proposals"][0])
        t.assertIn(record["request"]["question"]["questionId"], derived_case.source_question_ids)
        persisted = []
        proposals.hypothesis_proposals_for_question.side_effect = lambda *args: list(persisted)
        proposals.save_hypothesis_proposal.side_effect = lambda item: persisted.append(item.to_dict())
        advisor.reset_mock()
        development.ingest_proposal.side_effect = [RuntimeError("temporary development failure"), {"caseId": candidate.case_id, "status": candidate.status}]
        with t.assertRaises(RuntimeError):
            service.propose(t.subject["accountId"], "TEST", record["request"]["question"], {}, observation_context=record["request"]["observationContext"])
        retried = service.propose(t.subject["accountId"], "TEST", record["request"]["question"], {}, observation_context=record["request"]["observationContext"])
        advisor.propose.assert_called_once()
        t.assertTrue(retried["reusedProposals"])
        t.assertEqual(1, len(persisted))
        from digital_twin.modules.news_intelligence.infrastructure.mysql_investment_research import MySQLInvestmentResearchStore
        queue = MySQLInvestmentResearchStore(runtime_settings())
        with t.control.transaction() as c:
            c.execute("UPDATE investment_hypothesis_proposal_requests SET created_at='2000-01-01T00:00:00Z' WHERE request_id=%s", (record["requestId"],))
        first = queue.claim_hypothesis_proposal_requests("worker-one")[0]
        t.assertEqual(record["requestId"], first["requestId"])
        t.assertTrue(queue.renew_hypothesis_proposal_request(first))
        t.assertFalse(queue.complete_hypothesis_proposal_request(record["requestId"], output, claim={**first, "leaseOwner": "other"}))
        with t.control.transaction() as c:
            c.execute("UPDATE investment_hypothesis_proposal_requests SET lease_expires_at='2000-01-01T00:00:00Z' WHERE request_id=%s", (record["requestId"],))
        second = queue.claim_hypothesis_proposal_requests("worker-two")[0]
        t.assertFalse(queue.complete_hypothesis_proposal_request(record["requestId"], output, claim=first))
        t.assertFalse(queue.fail_hypothesis_proposal_request(record["requestId"], "late error", claim=first))
        with queue.hypothesis_proposal_keep_alive(second):
            t.assertTrue(queue.renew_hypothesis_proposal_request(second))
        t.assertTrue(queue.complete_hypothesis_proposal_request(record["requestId"], output, claim=second))
        t.assertFalse(queue.fail_hypothesis_proposal_request(record["requestId"], "after completion", claim=second))
        t.assertEqual(2, refresh_development(t.brain, [t.subject], bridge.progress))
        t.assertEqual(0, refresh_development(t.brain, [t.subject], bridge.progress))
        memories = t.brain.memory(t.subject["accountId"], "TEST", t.subject["worldId"])
        original = next(row for row in memories if row.get("caseId") == case["caseId"])
        t.assertEqual("review-needed", original["status"])
        t.assertEqual(candidate.blocked_reason, original["developmentProgress"]["cases"][0]["blockedReason"])
        t.assertEqual(record["requestId"], original["hypothesisResolution"]["requestId"])
        frozen = freeze_execution_input({**packet(), "taskId": "followup"}, [], memories)
        validate_execution_input(frozen)
        t.assertTrue(any(row.get("developmentProgress", {}).get("cases") for row in frozen["researchResults"]))
        with t.assertRaises(ValueError):
            bridge.progress(record["requestId"], t.subject["accountId"], "TEST", "other-world")
        candidate.status = "shadow-observing"; candidate.experiment_id = "isolated-experiment"
        t.assertEqual(2, refresh_development(t.brain, [t.subject], bridge.progress))
        t.assertEqual("experiment-status-only", bridge.progress(record["requestId"], t.subject["accountId"], "TEST", t.subject["worldId"])["authority"])
        # A changed revision while fetching the cross-owner result is not overwritten.
        candidate.status = "invalidated"
        def race(*args):
            progress = bridge.progress(*args)
            with t.control.transaction() as c:
                for row in c.execute("SELECT payload_json FROM ai_brain_cases WHERE kind='question'").fetchall():
                    value = json.loads(row["payload_json"]); t.brain.save(c, value, "concurrent", "concurrent-review")
            return progress
        t.assertEqual(0, refresh_development(t.brain, [t.subject], race))
        # Missing historical question links can be restored without re-running
        # the request or touching its immutable source input.
        before = copy.deepcopy(record)
        with t.control.transaction() as c:
            c.execute("DELETE FROM ai_brain_case_events")
            c.execute("DELETE FROM ai_brain_cases")
        restored = bridge.restore_question(t.brain, record["requestId"], t.subject["accountId"], "TEST", t.subject["worldId"])
        t.assertEqual("restored", restored["status"])
        t.assertEqual("existing", bridge.restore_question(t.brain, record["requestId"], t.subject["accountId"], "TEST", t.subject["worldId"])["status"])
        t.assertEqual(before["request"], source.observation_development_records(t.subject["accountId"], "TEST")[0]["request"])
    finally:
        t.control.development_writer = None
        with t.control.transaction() as c:
            c.execute("DELETE FROM investment_hypothesis_proposal_requests WHERE account_id=%s", (t.subject["accountId"],))
        t.clean()
    assert_business_question_loop(t)
    t.clean()
    assert_research_admission(t)


def assert_research_admission(t):
    from digital_twin.modules.ai_orchestration.domain.brain_management import later
    task, result, children = t.observation()
    result["quality"]["status"] = "rejected"
    t.assertTrue(t.control.complete(task, result, children))
    t.assertEqual("quality-blocked", result["brain"]["status"])
    research = t.control.claim()
    t.assertEqual("research", research["capability"])
    t.assertTrue(t.control.complete(research, {"status": "failed"}, []))
    with t.control.transaction() as c:
        case = t.brain.read(c, research["brainCaseId"], t.subject["accountId"], "TEST", lock=True)
        case["lastResearch"]["requestedAt"] = later(stamp(), -361)
        t.brain.save(c, case, research["taskId"], "test-retry-due")
    memory = next(row for row in t.brain.memory(t.subject["accountId"], "TEST") if row["kind"] == "brain-case")
    raw = {**plan(), "questions": [], "caseReviews": [review_case(memory, "research")],
           "questionResolutions": [resolution(memory, "defer", -1)]}
    job, result, children = t.observation(raw, seed=False)
    result["quality"]["status"] = "rejected"
    t.assertTrue(t.control.complete(job, result, children))
    with t.control.connect() as c:
        saved = t.brain.read(c, memory["caseId"], t.subject["accountId"], "TEST")
        theses = c.execute("SELECT COUNT(*) AS count FROM ai_brain_cases WHERE kind='business-thesis'").fetchone()["count"]
    t.assertEqual(0, theses)
    t.assertNotIn("lastAssessment", saved)
    t.assertEqual("quality-blocked", saved["hypothesisResolution"]["state"])
    t.assertEqual(2, saved["researchAttempts"])
    next_research = t.control.claim()
    t.assertEqual(saved["caseId"], next_research["brainCaseId"])


def assert_business_question_loop(t):
    from test_business_research import business_packet, raw_research
    with patch.dict(t.observation.__func__.__globals__, {"packet": business_packet}):
        task, result, children = t.observation()
        t.assertTrue(t.control.complete(task, result, children))
        job = t.control.claim(); t.assertTrue(t.control.complete(job, {"status": "completed", "runId": "business-source"}, []))
        case = next(row for row in t.brain.memory(t.subject["accountId"], "TEST") if row["kind"] == "brain-case")
        link = {**resolution(case, "business-thesis"), "evidenceIds": ["report-2025"]}
        raw = {**plan(), "questions": [], "businessResearch": raw_research(), "caseReviews": [review_case(case, "answered")],
               "questionResolutions": [link]}
        task, result, children = t.observation(raw, seed=False)
        t.assertTrue(t.control.complete(task, result, children))
        with t.control.connect() as c:
            saved = t.brain.read(c, case["caseId"], t.subject["accountId"], "TEST")
            target = t.brain.read(c, saved["hypothesisResolution"]["thesisId"], t.subject["accountId"], "TEST")
        t.assertEqual(case["caseId"], target["origin"]["sourceQuestions"][0]["caseId"])
        t.assertEqual("not-empirically-qualified", target["qualification"])
        memory = next(row for row in t.brain.memory(t.subject["accountId"], "TEST") if row["kind"] == "business-thesis")
        t.assertEqual("business-source", memory["origin"]["sourceQuestions"][0]["research"]["result"]["runId"])
