"""Prevent foreign recovery, preserve completed audit, and group only scheduling notices."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
import uuid
from unittest.mock import Mock

from stabilization_database import StabilizationDatabaseCase
from digital_twin.modules.news_intelligence.domain.investment_evidence_governance import ResearchRun
from digital_twin.modules.news_intelligence.application.investment_research_orchestration_service import (
    InvestmentResearchOrchestrationService, InvestmentResearchQueueRunner)
from digital_twin.modules.news_intelligence.infrastructure.mysql_investment_research import MySQLInvestmentResearchStore
from digital_twin.modules.ai_orchestration.domain.planning import identity, stamp
from digital_twin.modules.ai_orchestration.domain.research_execution_repair import replacement_execution, VERSION
from digital_twin.modules.ai_orchestration.infrastructure.research_execution_repair import repair_source_execution
from digital_twin.modules.ai_orchestration.infrastructure.mysql_brain_agenda import MySQLBrainAgendaStore
from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
from digital_twin.infrastructure.transactions.research_progress_publication import ResearchProgressPublication
from digital_twin.modules.notifications.domain.research_thread import research_thread_key
from test_research_progress import case, event


def damaged_execution():
    key = identity("lost-plan", uuid.uuid4().hex)
    job = {"taskId": key, "capability": "research", "brainCaseId": identity("case", key),
        "accountId": "test-owner", "symbol": "TEST", "name": "테스트 기업", "worldId": "test-world",
        "question": "공식 현금흐름의 변동 원인은?", "researchRequest": {"version": "observation-research-request-v2", "queryTerms": ["cash flow"],
        "sourceTypes": ["official-filing"], "maxAgeMinutes": 1440}, "questionContract": {
        "target": "TEST", "period": "2025 annual", "answerCriterion": "현금흐름과 주석 확인"}}
    run = {"runId": "ai-control-" + key[:40], "accountId": job["accountId"], "symbol": job["symbol"],
        "status": "not-required", "stopReason": "not-required", "taskIds": [], "taskAssessments": [],
        "executedPlan": {"tasks": []}, "requestContext": {"source": "ai-control", "aiControlTaskId": key}}
    result = {"runId": run["runId"], "status": "not-required", "documentaryAnswer": {"status": "unavailable"}}
    value = {**{k: job[k] for k in ("accountId", "symbol", "name", "worldId", "question", "questionContract")},
        "caseId": job["brainCaseId"], "capability": "research", "kind": "question", "status": "review-needed",
        "revision": 1, "createdAt": stamp(), "nextCheckAt": stamp(), "researchAttempts": 6,
        "researchIntentAttempts": {"original-intent": 2}, "lastResearch": {"taskId": key,
        "requestedAt": stamp(), "result": result}, "latestSourceAnswer": {"status": "partial", "text": "확보한 일부 수치",
        "sources": [{"materialFingerprint": "old-report"}]}, "researchRepeat": {"version": "research-repeat-v1",
        "authorizedTaskId": key, "scheduledMaterials": ["old-report"], "gapRecovery": {"scheduled": "used"},
        "sourceRoutes": {"original-route": {"planned": True, "scheduled": True}}},
        "answerResolution": {"status": "unavailable", "taskId": key}}
    return job, result, value, run


def followup(index, planner="one-plan", **scope):
    value = {**case(), "name": "테스트 <기업>", **scope, "caseId": "question-" + str(index),
             "lastResearch": {"taskId": "source-" + str(index)}}
    e = {**event("research-planned", "scheduled-" + str(index)), "taskId": planner,
         "details": {"action": "research", "scheduledTaskId": "source-" + str(index),
                     "reason": "공식 원문을 확인합니다.", "missingRequirements": ["현금흐름"]}}
    from digital_twin.modules.ai_orchestration.domain.research_progress import research_progress
    return research_progress(value, e)


class ResearchOwnershipTests(unittest.TestCase):
    def test_foreign_runs_fail_before_collection_or_persistence_even_if_store_returns_them(self):
        for run_id, context in (("ai-control-legacy", {}), ("legacy-run", {"source": "ai-control"}),
                                ("legacy-run", {"aiControlTaskId": "task"})):
            run = ResearchRun.from_dict({"runId": run_id, "status": "processing", "requestContext": context})
            store, gateway = Mock(), Mock()
            service = InvestmentResearchOrchestrationService(Mock(), gateway, research_store=store)
            with self.assertRaisesRegex(ValueError, "owner-mismatch"):
                service.execute_queued(run)
            store.save_run.assert_not_called(); gateway.collect_for_target.assert_not_called()
            store.claim_queued_runs.return_value = [run]
            result = InvestmentResearchQueueRunner(store, service).run_once()
            self.assertEqual(0, result["processedCount"])
            self.assertEqual(1, result["foreignOwnerSkippedCount"])
            store.save_run.assert_not_called()

    def test_missing_queued_context_and_lost_terminal_plan_cannot_be_reused_as_an_answer(self):
        service = InvestmentResearchOrchestrationService(Mock(), Mock(), research_store=Mock())
        with self.assertRaisesRegex(ValueError, "context-missing"):
            service.execute_queued(ResearchRun.from_dict({"runId": "normal"}))
        from digital_twin.modules.ai_orchestration.application.research import execute_research
        job, _, _, run = damaged_execution()
        store, factory = Mock(), Mock()
        store.get_run.return_value = run
        with self.assertRaisesRegex(ValueError, "source-plan-lost"):
            execute_research(job, store, factory)
        factory.assert_not_called()

    def test_repair_keeps_question_partial_answer_limits_and_rejects_changed_state(self):
        job, result, value, run = damaged_execution()
        child, after = replacement_execution(job, result, value, run, stamp())
        self.assertEqual(job["questionContract"], child["questionContract"])
        for field in ("question", "questionContract", "latestSourceAnswer", "researchAttempts", "researchIntentAttempts"):
            self.assertEqual(value[field], after[field])
        for field in ("scheduledMaterials", "gapRecovery", "sourceRoutes"):
            self.assertEqual(value["researchRepeat"][field], after["researchRepeat"][field])
        self.assertEqual(child["taskId"], after["researchRepeat"]["authorizedTaskId"])
        self.assertEqual(value["lastResearch"], after["executionRepairs"][job["taskId"]]["previousResearch"])
        variants = [({**value, "worldId": "another-world"}, run),
                    ({**value, "question": "different"}, run),
                    ({**value, "latestSourceAnswer": {"status": "answered"}}, run),
                    ({**value, "lastResearch": {"taskId": "newer-execution"}}, run),
                    (value, {**run, "accountId": "foreign"}),
                    (value, {**run, "taskAssessments": [{"status": "partial"}]}),
                    (value, {**run, "status": "evidence-collected"})]
        for changed, source in variants:
            with self.subTest(changed=changed.get("worldId")), self.assertRaises(ValueError):
                replacement_execution(job, result, changed, source, stamp())


class ResearchOwnershipStorageTests(StabilizationDatabaseCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sources = MySQLInvestmentResearchStore(cls.settings)
        cls.control = MySQLAIControlStore(cls.settings)
        cls.brain = MySQLBrainAgendaStore(cls.settings)

    def test_stale_recovery_and_queue_claim_exclude_every_central_ownership_marker(self):
        suffix = uuid.uuid4().hex
        runs = []
        for index, (run_id, context, status) in enumerate([
            ("ai-control-" + suffix, {}, "processing"),
            ("source-" + suffix, {"source": "ai-control"}, "processing"),
            ("id-" + suffix, {"aiControlTaskId": "task"}, "queued"),
            ("queued-" + suffix, {"brain": {"researchPlan": {"tasks": [{"taskId": "original"}]}}}, "queued"),
            ("stale-" + suffix, {"brain": {"researchPlan": {"tasks": [{"taskId": "original"}]}}}, "processing"),
        ]):
            run = ResearchRun.from_dict({"runId": run_id, "status": status, "startedAt": "2020-01-01T00:00:00Z",
                "requestContext": context, "executedPlan": {"sourceExploration": {"readCount": 2}}, "taskIds": ["original"]})
            self.sources.save_run(run); runs.append(run)
            with self.sources.connect() as c:
                c.execute("UPDATE investment_research_runs SET updated_at='2020-01-01T00:00:00Z' WHERE run_id=%s", (run_id,))
        self.assertEqual(1, self.sources.queued_count())
        claimed = self.sources.claim_queued_runs(10)
        self.assertEqual({r.run_id for r in runs[3:]}, {r.run_id for r in claimed})
        for original in runs[:3]:
            self.assertEqual(original.to_dict(), self.sources.get_run(original.run_id))
        for row in claimed:
            self.assertEqual(2, row.executed_plan["sourceExploration"]["readCount"])

    def test_repair_is_atomic_idempotent_and_preserves_original_completed_execution(self):
        job, result, value, run = damaged_execution()
        with self.control.transaction() as c:
            MySQLAIControlStore.insert(c, job)
            c.execute("UPDATE ai_control_tasks SET status='completed',result_json=%s WHERE task_id=%s", (json.dumps(result), job["taskId"]))
            MySQLBrainAgendaStore.save_with_connection(c, value, job["taskId"], "research-returned")
        self.sources.save_run(ResearchRun.from_dict(run))
        source_before = self.sources.get_run(run["runId"])
        def read_case():
            with self.control.connect() as c:
                return MySQLBrainAgendaStore.read(c, job["brainCaseId"], job["accountId"], job["symbol"])
        before = read_case()
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with self.control.transaction() as c:
                repair_source_execution(c, job["taskId"], self.sources.read_run_for_repair, apply=True)
                raise RuntimeError("rollback")
        self.assertEqual(before, read_case())
        with self.control.transaction() as c:
            self.assertEqual("eligible", repair_source_execution(c, job["taskId"], self.sources.read_run_for_repair)["status"])
        self.assertEqual(before, read_case())
        with self.control.transaction() as c:
            repaired = repair_source_execution(c, job["taskId"], self.sources.read_run_for_repair, apply=True)
        self.assertEqual("repaired", repaired["status"])
        after = read_case()
        self.assertEqual(before["latestSourceAnswer"], after["latestSourceAnswer"])
        self.assertEqual(6, after["researchAttempts"])
        with self.control.transaction() as c:
            again = repair_source_execution(c, job["taskId"], self.sources.read_run_for_repair, apply=True)
            self.assertEqual("already-repaired", again["status"])
            original = c.execute("SELECT status,result_json FROM ai_control_tasks WHERE task_id=%s", (job["taskId"],)).fetchone()
            self.assertEqual("completed", original["status"])
            self.assertEqual(result, json.loads(original["result_json"]))
            child = c.execute("SELECT status,payload_json FROM ai_control_tasks WHERE task_id=%s", (repaired["taskId"],)).fetchone()
            self.assertEqual("pending", child["status"])
            self.assertEqual(job["questionContract"], json.loads(child["payload_json"])["questionContract"])
        self.assertEqual(source_before, self.sources.get_run(run["runId"]))

    def test_scheduling_batch_rolls_back_coalesces_and_does_not_rewrite_sent_body(self):
        publisher = ResearchProgressPublication(self.notifications)
        planner = uuid.uuid4().hex
        notices = [followup(i, planner) for i in range(4)]
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with self.notifications.transaction() as c:
                abandoned = publisher.publish(c, notices[0])
                publisher.publish(c, notices[1])
                raise RuntimeError("rollback")
        with self.notifications.connect() as c:
            self.assertIsNone(c.execute("SELECT job_id FROM notification_jobs WHERE job_id=%s", (abandoned["jobId"],)).fetchone())
        with self.notifications.transaction() as c:
            results = [publisher.publish(c, n) for n in notices]
        self.assertEqual(["queued", "coalesced", "coalesced", "coalesced"], [r["status"] for r in results])
        self.assertEqual(1, len({r["jobId"] for r in results}))
        key = results[0]["jobId"]
        with self.notifications.transaction() as c:
            row = c.execute("SELECT text,payload_json FROM notification_jobs WHERE job_id=%s", (key,)).fetchone()
            job = self.notifications.job_from_row(row)
            batch = job.context["researchProgress"]
            self.assertEqual(4, len(batch["items"]))
            self.assertIn("질문 4개", job.text); self.assertIn("&lt;기업&gt;", job.text)
            self.assertEqual([], batch["sourceQuestionIds"])
            thread = research_thread_key(job)
            self.assertEqual("suppressed", publisher.publish(c, notices[0])["status"])
            other = publisher.publish(c, followup(5, planner, accountId="another-account"))
            self.assertNotEqual(key, other["jobId"])
            c.execute("UPDATE notification_jobs SET status='processing' WHERE job_id=%s", (key,))
            self.assertEqual("research-batch-sealed", publisher.publish(c, followup(6, planner))["reason"])
            unchanged = c.execute("SELECT text,payload_json FROM notification_jobs WHERE job_id=%s", (key,)).fetchone()
            self.assertEqual(row, unchanged)
            self.assertEqual(thread, research_thread_key(self.notifications.job_from_row(unchanged)))
        from digital_twin.modules.ai_orchestration.domain.research_progress import research_progress
        answered = research_progress({**case(), "status": "answered"}, event("assessment", "answer-" + planner))
        with self.notifications.transaction() as c:
            answer = publisher.publish(c, answered)
        self.assertNotEqual(key, answer["jobId"])


if __name__ == "__main__":
    unittest.main()
