"""Persistent question ownership, old evidence, outcome review and feedback authority."""
import copy
import hashlib
import json
import os
import unittest
from unittest.mock import Mock, patch

from ai_insight_fixtures import packet, plan
from digital_twin.modules.ai_orchestration.domain.brain_management import case_identity, validate_management, later
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_repair_input, validate_execution_input,
    DEVELOPMENT_PROMPT_VERSION, DEVELOPMENT_REPAIR_PROMPT_VERSION,
)
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan, planning_prompt, stamp, observation_fingerprint, identity
from digital_twin.modules.ai_orchestration.domain.insight_schema import planning_schema
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality


QUESTION = {"question": "공식 발표 원문에서 현재 설명을 뒷받침하는 근거를 확인할 수 있는가?", "capability": "research",
            "research": {"queryTerms": ["최근 분기 실적"], "sourceTypes": ["official-filing", "news"], "maxAgeMinutes": 1440}}
FEEDBACK = {"category": "data", "problem": "가격 설명에 비해 원인 자료의 확인 범위가 부족합니다.",
            "proposal": "출처별 자료 부족 상태와 확인 경로를 함께 표시합니다.",
            "verification": "자료가 없는 경우와 조회에 실패한 경우가 구분되어 표시되는지 확인합니다.", "evidenceIds": ["quote-1"]}


def review_case(case, action="wait"):
    return {"caseId": case["caseId"], "action": action, "reason": "현재 근거로 원래 질문에 대한 추가 확인이 필요합니다.",
            "evidenceIds": ["quote-1"], "nextCheckMinutes": 180,
            "research": QUESTION["research"] if action == "research" else {"queryTerms": [], "sourceTypes": [], "maxAgeMinutes": 0}}


class BrainManagementContractTests(unittest.TestCase):
    def test_frozen_memory_scope_due_review_and_historical_versions(self):
        p = packet()
        memory = {**{key: p[key] for key in ("accountId", "symbol", "worldId")}, "kind": "brain-case",
            "caseId": "old-question", "capability": "research", "revision": 3, "reviewDue": True,
            "origin": {"capturedAt": "2026-01-01T00:00:00Z", "hypothesis": "오래된 미해결 질문"}}
        value = {"caseReviews": [review_case(memory)], "serviceFeedback": [FEEDBACK]}
        result = validate_management(value, p, [memory])
        self.assertEqual(3, result["caseReviews"][0]["expectedRevision"])
        self.assertEqual("unverified-proposal", result["serviceFeedback"][0]["qualification"])
        for bad in ({**value, "caseReviews": []}, {**value, "caseReviews": [review_case(memory), review_case(memory)]},
                    {**value, "serviceFeedback": [{**FEEDBACK, "evidenceIds": ["invented"]}]},
                    {**value, "serviceFeedback": [{**FEEDBACK, "command": "write-code"}]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_management(bad, p, [memory])
        with self.assertRaises(ValueError):
            validate_management(value, p, [{**memory, "accountId": "other"}])
        reference = copy.deepcopy(p); reference["facts"][0]["judgementEvidenceUsable"] = False
        with self.assertRaises(ValueError):
            validate_management({**value, "caseReviews": [review_case(memory, "answered")]}, reference, [memory])
        envelope = freeze_execution_input(p, [], [memory])
        validate_execution_input(envelope)
        self.assertIn("old-question", envelope["prompt"])
        repair = freeze_repair_input(envelope, {**plan(), **value}, ["needs repair"], "parent")
        validate_execution_input(repair)
        self.assertEqual(envelope["researchResults"], repair["researchResults"])
        crowded = freeze_execution_input(p, [{"largeHistory": "x" * 58000}], [{"optionalResearch": "y" * 50000}, memory], 65536)
        self.assertEqual([memory], crowded["researchResults"])
        repaired = freeze_repair_input(crowded, {**plan(), **value, "longDraft": "z" * 30000}, ["repair"], "parent")
        self.assertEqual([memory], repaired["researchResults"])
        validate_execution_input(repaired)
        from digital_twin.modules.reasoning.contracts import EvidenceContractError
        with self.assertRaises(EvidenceContractError):
            freeze_execution_input(p, [], [{**memory, "origin": {"oversized": "x" * 70000}}], 65536)
        with self.assertRaises(EvidenceContractError):
            freeze_repair_input(envelope, {"oversized": "x" * 270000}, ["repair"], "parent")
        self.assertNotEqual(case_identity("a", "S", "world1", "research", "Question"), case_identity("a", "S", "world2", "research", "Question"))
        self.assertEqual(case_identity("a", "S", "world1", "research", "Question  One"), case_identity("a", "S", "world1", "research", " question one "))
        for version in (DEVELOPMENT_PROMPT_VERSION, DEVELOPMENT_REPAIR_PROMPT_VERSION):
            old = copy.deepcopy(envelope)
            old["promptVersion"] = version
            old["prompt"] = planning_prompt(p, [], [memory])
            if version == DEVELOPMENT_REPAIR_PROMPT_VERSION:
                from digital_twin.modules.ai_orchestration.domain.insight_repair import repair_prompt
                old["repair"] = {"errors": [], "rejectedDraft": plan(), "parentInputId": "parent", "comparisons": []}
                old["prompt"] = repair_prompt(old["prompt"], old["repair"])
            old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
            old["outputSchema"] = planning_schema(p)
            validate_execution_input(old)

    def test_due_agenda_is_reviewed_without_new_market_data_and_http_review_is_owner_only(self):
        from test_ai_control import AIControlTests, PACKET, PLAN
        memory = {**{key: PACKET[key] for key in ("accountId", "symbol", "worldId")}, "kind": "brain-case", "caseId": "case",
                  "capability": "research", "revision": 1, "reviewDue": True}
        service, store, planner = AIControlTests().runner(brain_memory=Mock(return_value=[memory]), brain_waker=Mock())
        from digital_twin.modules.ai_orchestration.domain.execution_input import PROMPT_VERSION
        store.memory.return_value = [{"inputFingerprint": observation_fingerprint(PACKET, [memory]), "observedAt": stamp(),
            "executionPromptVersion": PROMPT_VERSION, "quality": {"status": "observation-only"}}]
        planner.return_value = {**PLAN, "caseReviews": [review_case(memory)], "serviceFeedback": [],
            "questionResolutions": [{"caseId": "case", "disposition": "defer", "targetIndex": -1,
                "reason": "공식 자료가 부족하여 원래 질문의 가설화를 보류합니다.", "evidenceIds": ["quote-1"]}]}
        self.assertEqual("completed", service.run_once()["status"])
        planner.assert_called_once()
        service.brain_waker.assert_called_once()
        self.assertEqual("case", store.complete.call_args.args[1]["caseReviews"][0]["caseId"])
        from test_web_router_boundaries import WebRouterBoundaryTests, SHARE_ROLE_VIEWER
        http = WebRouterBoundaryTests(); http.setUp()
        try:
            with patch("digital_twin.infrastructure.composition.ai_orchestration.review_ai_service_feedback", return_value={"saved": True}) as save:
                denied = http.request("/api/ai-control/feedback", "PUT", b"{}", role=SHARE_ROLE_VIEWER)
                self.assertEqual(403, denied.response[0]); save.assert_not_called()
                allowed = http.request("/api/ai-control/feedback", "PUT", b"{}")
                self.assertEqual(200, allowed.response[0]); save.assert_called_once_with({})
        finally:
            http.doCleanups()


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class BrainAgendaStorageTests(unittest.TestCase):
    def setUp(self):
        from digital_twin.infrastructure.settings import runtime_settings
        from mysql_fixtures import mysql_test_settings
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_brain_agenda import MySQLBrainAgendaStore
        settings = {**runtime_settings(), **mysql_test_settings(), "aiControlBudgetEnabled": "false"}
        self.control, self.brain = MySQLAIControlStore(settings), MySQLBrainAgendaStore(settings)
        self.control.agenda_writer, self.control.agenda_failure = self.brain.record, self.brain.failed
        self.subject = {key: packet()[key] for key in ("accountId", "symbol", "worldId", "name")}
        self.clean()

    def clean(self):
        with self.control.transaction() as c:
            for table in ("ai_brain_case_events", "ai_brain_cases", "ai_control_input_calls", "ai_control_inputs", "ai_control_tasks", "ai_control_calls", "ai_control_budget"):
                c.execute("DELETE FROM " + table)

    def tearDown(self):
        self.clean()

    def observation(self, raw=None, seed=True):
        if seed:
            self.control.seed(self.subject)
        task = self.control.claim()
        self.assertEqual("observe", task["capability"])
        evidence = {**packet(), "taskId": task["taskId"]}
        memory = self.brain.memory(self.subject["accountId"], self.subject["symbol"])
        envelope = freeze_execution_input(evidence, [], memory)
        raw = raw or {**plan(), "questions": [QUESTION], "serviceFeedback": [FEEDBACK], "caseReviews": []}
        raw["notification"]["send"] = False
        result = {**validate_plan(raw, evidence, envelope["researchResults"]), "input": evidence,
            "executionInputId": self.control.save_execution_input(task, envelope), "observedAt": stamp()}
        result["quality"] = local_quality(result)
        self.assertEqual([], result["quality"]["errors"])
        call = self.control.begin_call("independent-observation", envelope["promptHash"], task["taskId"], result["executionInputId"])
        self.control.finish_call(call)
        child = {**self.subject, "capability": "observe", "taskId": identity(task["taskId"], "next"),
                 "availableAt": later(stamp(), 180)}
        return task, result, [child]

    def test_research_progress_outbox_rolls_back_and_retries_with_case_event(self):
        from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore
        from digital_twin.infrastructure.transactions.research_progress_publication import ResearchProgressPublication
        queue = MySQLNotificationJobStore(self.control.runtime_settings)
        publisher = ResearchProgressPublication(queue)
        account = self.subject["accountId"]

        def rows():
            with self.control.connect() as connection:
                return list(connection.execute("SELECT * FROM notification_jobs WHERE account_id=%s AND message_type='researchProgress'", (account,)).fetchall())

        def clean_queue():
            with self.control.transaction() as connection:
                connection.execute("DELETE FROM notification_delivery_attempts WHERE job_id IN "
                    "(SELECT job_id FROM notification_jobs WHERE account_id=%s AND message_type='researchProgress')", (account,))
                connection.execute("DELETE FROM notification_jobs WHERE account_id=%s AND message_type='researchProgress'", (account,))

        clean_queue()
        self.addCleanup(clean_queue)
        task, result, children = self.observation()

        def fail_after_enqueue(connection, progress):
            publisher.publish(connection, progress)
            raise RuntimeError("simulated rollback after enqueue")

        self.brain.progress_writer = fail_after_enqueue
        with self.assertRaises(RuntimeError):
            self.control.complete(task, result, children)
        self.assertEqual([], rows())
        self.assertEqual([], self.brain.status()["cases"])
        self.brain.progress_writer = publisher.publish
        self.assertFalse(self.control.complete({**task, "leaseToken": "lost"}, result, children))
        self.assertEqual([], rows())
        self.assertTrue(self.control.complete(task, result, children))
        self.assertFalse(self.control.complete(task, result, children))
        saved = rows()
        self.assertEqual(1, len(saved))
        self.assertEqual("pending", saved[0]["status"])
        payload = json.loads(saved[0]["payload_json"])
        progress = payload["context"]["researchProgress"]
        with self.control.connect() as connection:
            stored = connection.execute("SELECT case_id FROM ai_brain_case_events WHERE event_id=%s", (progress["eventId"],)).fetchone()
        self.assertEqual(progress["caseId"], stored["case_id"])
        from types import SimpleNamespace
        from digital_twin.modules.notifications.public import NotificationQueueRunner
        notifier = Mock(supports_delivery_checkpoints=False)
        notifier.send.return_value = SimpleNamespace(delivered=True, label="test", reason="", metadata={})
        owner = SimpleNamespace(account_id=account, quiet_hours_active=lambda *_: False)
        old_ai = Mock()
        runner = NotificationQueueRunner(queue, SimpleNamespace(load_all=lambda: [owner]), lambda _: notifier,
            settings=self.control.runtime_settings, include_message_types=["researchProgress"], ai_request_enqueuer=old_ai)
        self.assertEqual(1, runner.run_once())
        notifier.send.assert_called_once()
        old_ai.enqueue.assert_not_called()
        self.assertEqual("done", rows()[0]["status"])

    def test_atomic_question_research_reassessment_preserves_origin_and_blocks_duplicate_work(self):
        from question_resolution_checks import assert_question_loop
        assert_question_loop(self)
        self.clean()
        self.assert_research_return_and_due_agenda_wake_only_the_owning_world()
        self.clean()
        task, result, children = self.observation()
        self.assertFalse(self.control.complete({**task, "leaseToken": "lost"}, result, children))
        self.assertEqual([], self.brain.status()["cases"])
        self.control.outbox_writer = Mock(side_effect=RuntimeError("outbox failed"))
        with self.assertRaises(RuntimeError):
            self.control.complete(task, result, children)
        self.assertEqual([], self.brain.status()["cases"])
        self.control.outbox_writer = None
        children[:] = children[:1]
        self.assertTrue(self.control.complete(task, result, children))
        self.assertFalse(self.control.complete(task, result, children))
        rows = self.brain.status()["cases"]
        case = next(row for row in rows if row.get("kind") != "service-feedback")
        origin = copy.deepcopy(case["origin"])
        self.assertEqual("waiting", case["status"])
        self.assertEqual([], self.brain.memory("another-account", "TEST"))
        self.assertEqual([], [row for row in self.brain.memory("control-test", "TEST", "another-world") if row["kind"] == "brain-case"])
        research = self.control.claim()
        self.assertEqual(case["caseId"], research["brainCaseId"])
        self.assertEqual(QUESTION["research"]["queryTerms"], research["researchRequest"]["queryTerms"])
        self.assertEqual(research["researchRequest"], case["researchRequest"])
        with self.control.transaction() as c:
            jobs, pending = [], copy.deepcopy(case)
            self.brain.schedule_research(c, pending, task, result, jobs, stamp())
            self.assertEqual([], jobs)
            self.assertIn("진행 중", pending["reason"])
        assessment = {"version": "question-research-return-v1", "status": "needs-evidence",
            "coverageState": "incomplete", "missingRequirements": ["official-filing"],
            "authority": "historical-work-status-only"}
        self.assertTrue(self.control.complete(research, {"runId": "source-run", "status": "completed",
            "changedEvidenceCount": 0, "questionAssessment": assessment}, []))
        memory = next(row for row in self.brain.memory("control-test", "TEST") if row["kind"] == "brain-case")
        self.assertEqual(assessment, memory["lastResearch"]["result"]["questionAssessment"])
        frozen = freeze_execution_input({**packet(), "taskId": "next-assessment"}, [], [memory])
        validate_execution_input(frozen)
        self.assertEqual(assessment, frozen["researchResults"][0]["lastResearch"]["result"]["questionAssessment"])
        self.assertEqual(research["researchRequest"], memory["researchRequest"])
        self.assertEqual("review-needed", memory["status"])
        self.assertTrue(memory["reviewDue"])
        raw = {**plan(), "questions": [QUESTION], "caseReviews": [review_case(memory, "answered")], "serviceFeedback": []}
        follow, evaluated, next_jobs = self.observation(raw, seed=False)
        self.assertTrue(self.control.complete(follow, evaluated, next_jobs))
        updated = next(row for row in self.brain.status()["cases"] if row["caseId"] == case["caseId"])
        self.assertEqual("answered", updated["status"])
        self.assertEqual(origin, updated["origin"])
        self.assertEqual("ai-assessment", updated["lastAssessment"]["qualification"])
        self.assertEqual(3, len(updated["history"]))
        with self.control.connect() as c:
            count = c.execute("SELECT COUNT(*) AS count FROM ai_control_tasks WHERE capability='research'").fetchone()["count"]
        self.assertEqual(1, count)
        feedback = next(row for row in rows if row.get("kind") == "service-feedback")
        payload = {**{key: feedback[key] for key in ("caseId", "accountId", "symbol", "revision")},
                   "status": "planned", "note": "출처별 조회 상태를 표시하는 개선을 계획합니다."}
        with self.assertRaises(ValueError):
            self.brain.review_feedback({**payload, "accountId": "other"})
        with self.assertRaises(ValueError):
            self.brain.review_feedback({**payload, "revision": True})
        self.assertTrue(self.brain.review_feedback(payload)["saved"])
        with self.assertRaises(ValueError):
            self.brain.review_feedback(payload)
        saved = next(row for row in self.brain.status()["cases"] if row["caseId"] == feedback["caseId"])
        self.assertEqual(feedback["origin"], saved["origin"])
        self.assertEqual("owner-reported-not-empirical", saved["ownerReview"]["qualification"])

    def assert_research_return_and_due_agenda_wake_only_the_owning_world(self):
        task, result, children = self.observation()
        self.assertTrue(self.control.complete(task, result, children))
        research = self.control.claim()
        due = later(stamp(), 180)
        other = {**self.subject, "worldId": "another-world", "taskId": "other-world-observation",
                 "capability": "observe", "availableAt": due}
        with self.control.transaction() as c:
            self.control.insert(c, other)
        self.assertTrue(self.control.complete(research, {"status": "completed"}, []))

        def check_only_owner_woken():
            with self.control.connect() as c:
                rows = c.execute("SELECT task_id,available_at,priority FROM ai_control_tasks WHERE status='pending'").fetchall()
            foreign = next(row for row in rows if row["task_id"] == other["taskId"])
            owner = next(row for row in rows if row["task_id"] != other["taskId"])
            self.assertEqual(due, foreign["available_at"])
            self.assertEqual(0, foreign["priority"])
            self.assertLessEqual(owner["available_at"], stamp())
            self.assertEqual(3, owner["priority"])

        check_only_owner_woken()
        with self.control.transaction() as c:
            c.execute("UPDATE ai_control_tasks SET available_at=%s,priority=0 WHERE status='pending'", (due,))
        self.brain.wake_due([self.subject, {**self.subject, "worldId": "another-world"}])
        check_only_owner_woken()

    def test_failed_research_wakes_review_and_rejected_assessment_cannot_close_or_spin(self):
        task, result, children = self.observation()
        altered = copy.deepcopy(result); altered["executionInputId"] = "not-persisted"
        with self.assertRaises(ValueError):
            self.control.complete(task, altered, children)
        self.assertTrue(self.control.complete(task, result, children))
        research = self.control.claim()
        research["attempts"] = 3
        self.control.fail(research, "TimeoutError")
        memory = next(row for row in self.brain.memory("control-test", "TEST") if row["kind"] == "brain-case")
        self.assertEqual("failed", memory["lastResearch"]["result"]["status"])
        raw = {**plan(), "questions": [], "caseReviews": [review_case(memory, "answered")], "serviceFeedback": []}
        follow, rejected, next_jobs = self.observation(raw, seed=False)
        rejected["quality"]["status"] = "rejected"
        self.assertTrue(self.control.complete(follow, rejected, next_jobs))
        saved = next(row for row in self.brain.status()["cases"] if row["caseId"] == memory["caseId"])
        self.assertEqual("review-needed", saved["status"])
        self.assertGreater(saved["nextCheckAt"], stamp())
        self.assertNotIn("lastAssessment", saved)
        self.assertEqual("quality-blocked", rejected["brain"]["status"])
        case = copy.deepcopy(saved); case["researchAttempts"] = 3
        with self.control.transaction() as c:
            jobs = []
            self.brain.schedule_research(c, case, follow, rejected, jobs, stamp())
            same = copy.deepcopy(saved)
            same["lastResearch"]["result"]["status"] = "completed"
            reworded = {**rejected, "input": {**rejected["input"], "questionsToCheck": ["다른 다음 질문을 정하더라도 근거가 변한 것은 아닙니다."]}}
            self.brain.schedule_research(c, same, follow, reworded, jobs, stamp())
            self.assertIn("같은 근거", same["reason"])
            cooldown = copy.deepcopy(saved); cooldown["lastResearch"]["inputFingerprint"] = "previous-input"
            self.brain.schedule_research(c, cooldown, follow, rejected, jobs, stamp())
            self.assertIn("재시도 간격", cooldown["reason"])
        self.assertEqual([], jobs)
        self.assertEqual("blocked", case["status"])
        self.assertEqual(3, case["researchAttempts"])
        with self.control.transaction() as c:
            for changed_intent in (False, True):
                retry_case = copy.deepcopy(saved)
                retry_case["lastResearch"]["requestedAt"] = later(stamp(), -361)
                if changed_intent:
                    retry_case["lastResearch"]["result"]["status"] = "completed"
                    retry_case["researchRequest"]["queryTerms"] = ["공식 사업보고서 매출 구성"]
                retry_jobs = []
                self.brain.schedule_research(c, retry_case, follow, rejected, retry_jobs, stamp())
                self.assertEqual(1, len(retry_jobs))
                self.assertEqual(2, retry_case["researchAttempts"])
        with self.control.transaction() as c:
            saved["nextCheckAt"] = later(stamp(), -5)
            self.brain.save(c, saved, follow["taskId"], "test-due")
        self.brain.wake_due([{**self.subject, "worldId": "other-world"}])
        self.assertIsNone(self.control.claim())
        self.brain.wake_due([self.subject])
        retry = self.control.claim()
        self.control.fail(retry, "TimeoutError")
        self.brain.wake_due([self.subject])
        self.assertIsNone(self.control.claim(), "due agenda must preserve task failure backoff")
        with self.control.transaction() as c:
            c.execute("UPDATE ai_control_tasks SET available_at=%s,attempts=2 WHERE task_id=%s", (stamp(), retry["taskId"]))
        retry = self.control.claim()
        self.control.fail(retry, "TimeoutError")
        self.brain.wake_due([self.subject])
        self.assertIsNone(self.control.claim(), "terminal failure must preserve recovery delay")
        postponed = next(row for row in self.brain.status()["cases"] if row["caseId"] == saved["caseId"])
        self.assertGreater(postponed["nextCheckAt"], later(stamp(), 4))
        self.assertLessEqual(postponed["nextCheckAt"], later(stamp(), 6))
        self.assertEqual("review-failed", postponed["history"][0]["stage"])


if __name__ == "__main__":
    unittest.main()
