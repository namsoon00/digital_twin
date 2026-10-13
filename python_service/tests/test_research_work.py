"""Research can progress without authoring or accepting a market explanation."""
from copy import deepcopy
import os
import unittest
from unittest.mock import Mock

from digital_twin.modules.ai_orchestration.domain import research_work as work
from digital_twin.modules.ai_orchestration.domain.planning import identity, stamp
from digital_twin.modules.ai_orchestration.domain.brain_management import later
from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input

SUBJECT = {"accountId": "control-test", "symbol": "TEST", "worldId": "portfolio:local:control-test", "name": "Test"}
CONTRACT = {"target": "영업현금흐름 개선", "period": "2026년 이분기", "answerCriterion": "공식 분기 보고서의 동일 기준 비교"}
REQUEST = {"queryTerms": ["영업현금흐름 분기 보고서"], "sourceTypes": ["official-filing"], "maxAgeMinutes": 527040}


def case():
    return {**SUBJECT, "caseId": "question", "kind": "brain-case", "capability": "research", "status": "review-needed",
            "question": "영업 현금흐름이 개선되었는가?", "revision": 2, "nextCheckAt": stamp(), "planDue": True,
            "answerResolution": {"status": "partial", "missingRequirements": ["현금흐름 원문"]}}


def plan(value, action="wait"):
    return {"reviews": [{"caseId": value["caseId"], "action": action, "reason": "공식 현금흐름 원문을 확인해야 합니다.",
        "resumeWhen": "누락된 분기 공시 원문을 확보하면 다시 조사합니다.", "nextCheckMinutes": 1440,
        "contract": CONTRACT, "research": REQUEST if action == "research" else work.EMPTY_REQUEST, "duplicateOf": ""}], "questions": []}


def answer(task_id, status="answered"):
    return {"version": "documentary-answer-v1", "taskId": identity(task_id, "sources"), "status": status,
        "authority": "source-review-only", "assessmentFingerprint": "reviewed-source-version", "text": "공식 자료에서 확인한 답변입니다.",
        "sources": [{"evidenceId": "official-report", "sourceUrl": "https://example.com/report"}],
        "missingRequirements": ["현금흐름 원문"] if status == "partial" else [], "assessedAt": stamp()}


class ResearchWorkTests(unittest.TestCase):
    def test_source_completion_is_independent_and_requires_original_receipt(self):
        result = {"runId": "run", "documentaryAnswer": answer("task")}
        resolution = work.answer_resolution(result, "task")
        self.assertEqual("answered", resolution["status"])
        self.assertEqual("not-empirically-qualified", resolution["qualification"])
        for patch in ({"missingRequirements": ["still-missing"]}, {"sources": []}, {"assessmentFingerprint": ""}, {"taskId": "foreign"}):
            with self.assertRaises(ValueError):
                work.answer_resolution({"documentaryAnswer": {**result["documentaryAnswer"], **patch}}, "task")
        self.assertEqual("partial", work.answer_resolution({"documentaryAnswer": answer("task", "partial")}, "task")["status"])

    def test_plan_freezes_work_and_rejects_scope_mutation_and_hypothesis_authority(self):
        job = {**SUBJECT, "taskId": "plan-task"}
        envelope = work.freeze(job, [case()])
        validate_execution_input(envelope)
        accepted = work.validate_plan(plan(case()), envelope)
        self.assertEqual(2, accepted["reviews"][0]["expectedRevision"])
        self.assertEqual(accepted, work.validate_plan(work.raw_plan(accepted), envelope))
        for raw in ({**plan(case()), "hypothesis": "approved"}, {"reviews": [], "questions": []}):
            with self.assertRaises(ValueError): work.validate_plan(raw, envelope)
        tampered = deepcopy(envelope); tampered["researchCases"][0]["question"] = "다른 질문으로 교체"
        with self.assertRaises(ValueError): validate_execution_input(tampered)
        with self.assertRaises(ValueError): work.freeze(job, [{**case(), "accountId": "other"}])
        with self.assertRaises(ValueError): work.freeze(job, [case()], max_bytes=1)

    def test_merge_requires_existing_equal_period_and_target_and_keeps_lineage(self):
        first = {**case(), "questionContract": CONTRACT}
        second = {**first, "caseId": "keep", "planDue": False}
        envelope = work.freeze({**SUBJECT, "taskId": "plan"}, [first, second])
        raw = plan(first, "merge"); raw["reviews"][0]["duplicateOf"] = "keep"
        self.assertEqual("keep", work.validate_plan(raw, envelope)["reviews"][0]["duplicateOf"])
        second["questionContract"] = {**CONTRACT, "period": "2025년 이분기"}
        with self.assertRaises(ValueError):
            work.validate_plan(raw, work.freeze({**SUBJECT, "taskId": "plan"}, [first, second]))
        raw["reviews"][0]["contract"] = {**CONTRACT, "period": "변경된 기간"}
        with self.assertRaises(ValueError): work.validate_plan(raw, envelope)

    def test_research_planner_runs_even_if_graph_and_market_author_are_unavailable(self):
        from test_ai_control import AIControlTests
        service, store, planner = AIControlTests().runner(research_plan_memory=lambda *_: [case()])
        store.claim.return_value["capability"] = "research-plan"
        service.evidence.side_effect = AssertionError("market data must not be required")
        service.reviewer = Mock(side_effect=AssertionError("no narrative critique"))
        planner.return_value = plan(case(), "research")
        self.assertEqual("completed", service.run_once()["status"])
        result = store.complete.call_args.args[1]
        self.assertEqual("research-planned", result["status"])
        self.assertNotIn("summary", result)
        self.assertNotIn("hypothesis", result)
        service.evidence.assert_not_called()
        service.reviewer.assert_not_called()
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_research_work import record_plan
        connection = Mock()
        self.assertEqual("obsolete", record_plan(Mock(), connection, {}, {"status": "retired"}, [])["status"])
        connection.execute.assert_not_called()

    def test_expiry_includes_unmeasurable_theses_and_preserves_observed_results(self):
        from digital_twin.modules.ai_orchestration.domain.business_research import expire_thesis
        deadline = "2026-10-13T00:00:00Z"
        original = {"status": "data-needed", "createdAt": "2025-10-13T00:00:00Z", "expiresAt": deadline,
                    "contract": {"checkpoints": []}, "observations": []}
        self.assertIsNone(expire_thesis(original, "2026-10-12T23:59:59Z"))
        expired = expire_thesis(original, deadline)
        self.assertEqual("expired", expired["status"])
        self.assertEqual("data-needed", original["status"])
        self.assertIsNone(expire_thesis(expired, deadline))
        original.update(status="tracking", contract={"checkpoints": [{}]}, observations=[{"status": "direction-not-observed", "value": 7}])
        self.assertEqual(original["observations"], expire_thesis(original, deadline)["observations"])

    def test_original_question_contract_reaches_source_review_fingerprint(self):
        from digital_twin.modules.ai_orchestration.application.research import execute_research
        from digital_twin.modules.ai_orchestration.domain.research_request import validate_research_request
        from digital_twin.modules.news_intelligence.domain.research_progress import task_contract
        from types import SimpleNamespace
        owner = Mock()
        owner.run.return_value = SimpleNamespace(run_id="run", to_dict=lambda: {"status": "completed"})
        store = Mock(); store.get_run.return_value = None
        job = {**SUBJECT, "taskId": "research", "question": case()["question"], "questionContract": CONTRACT,
               "researchRequest": validate_research_request(REQUEST, True, SUBJECT["accountId"])}
        execute_research(job, store, lambda: owner)
        task = owner.run.call_args.args[2]["researchPlan"]["tasks"][0]
        self.assertEqual(job["question"], task["question"])
        for value in CONTRACT.values(): self.assertIn(value, task_contract(task)["purpose"])

    def test_changed_source_plan_has_bounded_retries_and_preserves_partial_answers(self):
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_brain_agenda import MySQLBrainAgendaStore
        from digital_twin.modules.ai_orchestration.domain.research_request import research_intent_identity
        connection = Mock(); connection.execute.return_value.fetchone.return_value = None
        value = {**case(), "researchAttempts": 3, "researchRequest": deepcopy(REQUEST),
            "lastResearch": {"taskId": "previous", "requestedAt": later(stamp(), -400), "inputFingerprint": "old",
                "requestFingerprint": research_intent_identity(REQUEST), "result": {"documentaryAnswer": answer("previous", "partial")}}}
        job = {**SUBJECT, "taskId": "planner"}
        children = []
        MySQLBrainAgendaStore.schedule_research(connection, value, job, {"researchBasis": "question-basis"}, children, stamp())
        self.assertEqual([], children, "the old search cannot bypass its exhausted budget")
        value["researchRequest"] = {**REQUEST, "queryTerms": ["공식 분기 보고서 현금흐름 부록"]}
        value["questionContract"] = CONTRACT
        MySQLBrainAgendaStore.schedule_research(connection, value, job, {"researchBasis": "question-basis"}, children, stamp())
        self.assertEqual(1, len(children))
        self.assertEqual(4, value["researchAttempts"])
        self.assertEqual(CONTRACT, children[0]["questionContract"])
        self.assertEqual("partial", value["answerMemory"][0]["answer"]["status"])
        self.assertEqual(["현금흐름 원문"], value["answerMemory"][0]["answer"]["missingRequirements"])
        value["researchAttempts"] = 6
        value["researchRequest"]["queryTerms"] = ["다른 공식 원문 경로"]
        children = []
        MySQLBrainAgendaStore.schedule_research(connection, value, job, {"researchBasis": "new-basis"}, children, stamp())
        self.assertEqual([], children)
        self.assertEqual("blocked", value["status"])


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class ResearchWorkStorageTests(unittest.TestCase):
    def test_partial_answer_planning_atomic_retry_and_wait_deadline(self):
        from test_ai_brain_agenda import BrainAgendaStorageTests
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_research_work import reconcile_and_schedule
        fixture = BrainAgendaStorageTests(); fixture.setUp()
        try:
            job, result, children = fixture.observation()
            fixture.control.complete(job, result, children)
            research = fixture.control.claim()
            fixture.control.complete(research, {"status": "completed", "documentaryAnswer": answer(research["taskId"], "partial")}, [])
            with fixture.brain.transaction() as connection:
                counts = reconcile_and_schedule(fixture.brain, connection, [fixture.subject])
            self.assertEqual(1, counts["plansScheduled"])
            task = fixture.control.claim()
            self.assertEqual("research-plan", task["capability"])
            memories = [row for row in fixture.brain.memory(*[task[key] for key in ("accountId", "symbol", "worldId")], research_only=True) if row["kind"] == "brain-case"]
            for row in memories: row["planDue"] = True
            envelope = work.freeze(task, memories)
            input_id = fixture.control.save_execution_input(task, envelope)
            call = fixture.control.begin_call("independent-observation", envelope["promptHash"], task["taskId"], input_id)
            fixture.control.finish_call(call)
            result = {"status": "research-planned", "executionInputId": input_id, "researchPlan": work.validate_plan(plan(memories[0]), envelope)}
            self.assertFalse(fixture.control.complete({**task, "leaseToken": "lost"}, result, []))
            invalid = deepcopy(result); invalid["researchPlan"]["reviews"][0]["reason"] = "변조된 계획을 저장할 수 없습니다."
            # The normalized result is checked against the captured case contract;
            # the persisted model input/call, lease and revision are all required.
            invalid["executionInputId"] = "missing-input"
            with self.assertRaises(ValueError): fixture.control.complete(task, invalid, [])
            fixture.control.outbox_writer = Mock(side_effect=AssertionError("research cannot publish a market observation"))
            self.assertTrue(fixture.control.complete(task, result, []))
            self.assertFalse(fixture.control.complete(task, result, []))
            saved = next(row for row in fixture.brain.status()["cases"] if row["caseId"] == memories[0]["caseId"])
            self.assertEqual("partial", saved["answerResolution"]["status"])
            self.assertEqual(CONTRACT, saved["questionContract"])
            self.assertIn("누락된 분기", saved["researchPlan"]["resumeWhen"])
            # An old case deadline cannot advance a newly scheduled wait job.
            with fixture.control.transaction() as connection:
                fixture.brain.wake_observation(connection, task["accountId"], task["symbol"], task["worldId"], later(stamp(), -60), changed=False)
            self.assertIsNone(fixture.control.claim())
        finally:
            fixture.tearDown()

    def test_full_answer_reconciles_once_without_new_ai_or_hypothesis_qualification(self):
        from test_ai_brain_agenda import BrainAgendaStorageTests
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_research_work import reconcile_and_schedule
        fixture = BrainAgendaStorageTests(); fixture.setUp()
        try:
            job, result, children = fixture.observation()
            fixture.control.complete(job, result, children)
            research = fixture.control.claim()
            fixture.control.complete(research, {"status": "completed", "documentaryAnswer": answer(research["taskId"])}, [])
            saved = next(row for row in fixture.brain.status()["cases"] if row.get("capability") == "research")
            origin = deepcopy(saved["origin"])
            with fixture.brain.transaction() as connection:
                saved.pop("answerResolution")
                fixture.brain.save(connection, saved, "legacy", "legacy-fixture")
                first = reconcile_and_schedule(fixture.brain, connection, [fixture.subject])
                second = reconcile_and_schedule(fixture.brain, connection, [fixture.subject])
            self.assertEqual(1, first["answersReconciled"])
            self.assertEqual(0, second["answersReconciled"])
            self.assertEqual(0, first["plansScheduled"])
            saved = next(row for row in fixture.brain.status()["cases"] if row.get("capability") == "research")
            self.assertEqual("answered", saved["answerResolution"]["status"])
            self.assertEqual(origin, saved["origin"])
            self.assertEqual("review-needed", saved["status"])
            self.assertEqual("not-empirically-qualified", saved["answerResolution"]["qualification"])
        finally:
            fixture.tearDown()
