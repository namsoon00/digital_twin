"""Failed usage, scoped question progress and repeat notices without live AI."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from stabilization_database import StabilizationDatabaseCase
from digital_twin.modules.ai_orchestration.infrastructure.execution import execute_ai_work
from digital_twin.modules.ai_orchestration.domain.execution_resilience import AIExecutionError, AIExecutionDeferred
from digital_twin.modules.ai_orchestration.domain.question_progress import question_progress, task_wait
from digital_twin.modules.ai_orchestration.domain.question_assessment import VERSION
from digital_twin.modules.ai_orchestration.domain.research_progress import research_progress
from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
from digital_twin.modules.ai_orchestration.infrastructure.mysql_brain_agenda import MySQLBrainAgendaStore
from digital_twin.modules.ai_orchestration.infrastructure.mysql_question_progress import attach_question_progress
from test_research_progress import case, event

NOW = datetime(2026, 10, 25, 8, tzinfo=timezone.utc)
PAUSED = {"lastFailureCategory": "quota", "blockedUntil": "2026-10-25T10:00:00Z"}


class ResearchExecutionProgressTests(unittest.TestCase):
    def test_failed_terminal_usage_requires_complete_numeric_receipts_for_every_turn(self):
        zero = {"input_tokens": 0, "output_tokens": 0}
        completed = {"type": "turn.completed", "usage": {"input_tokens": 7, "output_tokens": 3}}
        failed = {"type": "turn.failed", "error": {"message": "usage limit private-secret"}}
        store = Mock(); store.begin_call.return_value = "call"
        variants = [([failed], None), ([{**failed, "usage": zero}], zero),
                    ([completed, failed], None),
                    ([completed, {**failed, "usage": zero}], completed["usage"]),
                    ([completed, {"type": "turn.started"}], None),
                    ([{"type": []}, {**failed, "usage": zero}], zero),
                    ([{**failed, "usage": {"input_tokens": -1, "output_tokens": 0}}], None)]
        for events, expected in variants:
            stdout = "\n".join(json.dumps(row) for row in events)
            with self.subTest(events=events), patch("digital_twin.modules.ai_orchestration.infrastructure.execution._store", return_value=store):
                with self.assertRaises(AIExecutionError):
                    execute_ai_work("research-planning", "", lambda: SimpleNamespace(returncode=1, stdout=stdout, stderr="usage limit"), {})
            metrics = store.finish_call.call_args.kwargs["metrics"]
            self.assertEqual(expected, metrics["usage"])
            self.assertNotIn("private-secret", json.dumps(metrics))
            self.assertNotIn("processNotStarted", metrics)

    def test_question_milestones_require_scoped_review_and_never_infer_answer_from_queue_completion(self):
        value = {**case(), "questionContract": {"period": "2026-Q2"}, "lastResearch": {"taskId": "source"},
                 "latestSourceAnswer": {"status": "partial", "sources": [{"materialFingerprint": "report"}]}}
        task = {**{k: value[k] for k in ("accountId", "symbol", "worldId")}, "taskId": "source",
                "brainCaseId": value["caseId"], "status": "pending", "capability": "research"}
        original = deepcopy(value)
        progress = question_progress(value, {"source": task}, PAUSED, NOW)
        self.assertEqual("quota", progress["wait"]["errorCategory"])
        self.assertFalse(progress["answerComplete"])
        self.assertEqual("partial", progress["stages"][1]["status"])
        self.assertEqual(original, value)
        value["questionAssessment"] = {"version": VERSION, "questionContract": value["questionContract"],
            "taskId": "judgment", "status": "answered", "researchReviewInputId": "review",
            "hypothesisDisposition": "answer", "judgmentImpact": {"reason": "답변을 통해 설명을 유지했습니다."}}
        judgment = {**task, "taskId": "judgment", "status": "completed", "hasSummary": True,
                    "publication": {"status": "queued", "jobId": "notification"}}
        tasks = {"source": {**task, "status": "completed"}, "judgment": judgment}
        progress = question_progress(value, tasks, {}, NOW)
        self.assertTrue(progress["answerComplete"])
        self.assertNotIn("receipt", progress["publication"])
        for field in ("accountId", "symbol", "worldId"):
            tasks["judgment"] = {**judgment, field: "foreign"}
            self.assertFalse(question_progress(value, tasks, {}, NOW)["answerComplete"])
            self.assertEqual({}, question_progress(value, tasks, {}, NOW)["publication"])
        tasks["judgment"] = {**judgment, "status": "failed"}
        self.assertFalse(question_progress(value, tasks, {}, NOW)["answerComplete"])
        internal = task_wait({"status": "pending", "lastError": "ai-token-allocation-budget-exhausted"}, {}, NOW)
        self.assertEqual("project-budget", internal["scope"])
        self.assertEqual({}, task_wait({"status": "completed"}, PAUSED, NOW))

    def test_followup_notice_identity_survives_replans_outages_and_legacy_upgrade(self):
        value = {**case(), "questionContract": {"period": "2026-Q2"},
                 "lastResearch": {"taskId": "one"}, "researchSourceState": {"fingerprints": ["report-1"]}}
        def notice(key):
            value["lastResearch"]["taskId"] = key
            return research_progress(value, {**event("research-planned", key), "taskId": "planner-" + key,
                "details": {"action": "research", "scheduledTaskId": key}})
        self.assertIsNotNone(notice("one"))
        self.assertIsNone(notice("two"))
        value["researchSourceState"]["fingerprints"] = []
        self.assertIsNone(notice("outage"))
        value["researchSourceState"]["fingerprints"] = ["report-1"]
        self.assertIsNone(notice("restored"))
        value["researchSourceState"]["fingerprints"].append("corrected-report")
        self.assertIsNotNone(notice("correction"))
        value["questionContract"]["period"] = "2026-Q3"
        self.assertIsNotNone(notice("new-period"))
        value["researchProgressMarkers"] = {"source-followup": "legacy-task-hash"}
        self.assertIsNone(notice("upgrade"))
        self.assertIsNone(notice("upgrade-again"))


class ResearchExecutionProgressStorageTests(StabilizationDatabaseCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.control = MySQLAIControlStore({**cls.settings, "aiTokenBudgetEnabled": "false"})
        cls.brain = MySQLBrainAgendaStore(cls.settings)

    def test_failed_zero_usage_settlement_is_atomic_idempotent_and_does_not_refund_unknowns(self):
        from digital_twin.modules.ai_orchestration.domain.planning import stamp
        from digital_twin.modules.ai_orchestration.domain.token_budget import usage_view
        first = self.control.begin_call("research-planning", "fixture")
        self.control.finish_call(first, "ai-execution:quota", {"usage": {"input_tokens": 0, "output_tokens": 0}})
        second = self.control.begin_call("research-planning", "fixture")
        self.control.finish_call(second, "ai-execution:quota", {"usage": None})
        self.control.finish_call(second, "ai-execution:quota", {"usage": {"input_tokens": 0, "output_tokens": 0}})
        with self.control.connect() as c:
            raw = c.execute("SELECT state_json FROM ai_control_execution_state WHERE scope_key=%s", ("token-usage:" + stamp()[:10],)).fetchone()
            metrics = c.execute("SELECT metrics_json FROM ai_control_call_metrics WHERE call_id=%s", (first,)).fetchone()
        state = json.loads(raw["state_json"])
        view = usage_view({}, state, datetime.now(timezone.utc))
        self.assertEqual(100000, view["failedUnknownTokensAllowance"])
        self.assertEqual(100000, view["unknownTokensAllowance"])
        self.assertEqual(0, view["reportedTokens"])
        self.assertEqual(0, view["reservedTokens"])
        self.assertEqual("reported", json.loads(metrics["metrics_json"])["usageStatus"])
        third = self.control.begin_call("research-planning", "fixture")
        self.control.finish_call(third, "provider-error", {"processNotStarted": True,
            "usage": {"input_tokens": 7, "output_tokens": 3}})
        with self.control.connect() as c:
            updated = self.control.token_usage(c, datetime.now(timezone.utc))
        self.assertEqual(10, updated["reportedTokens"])
        self.assertEqual(100000, updated["unknownTokensAllowance"])

    def test_question_progress_reads_current_linked_task_without_mutating_case_or_old_partial_answer(self):
        from digital_twin.modules.ai_orchestration.domain.planning import stamp, identity
        value = {**case(), "caseId": identity(self.deployment), "revision": 0, "createdAt": stamp(),
                 "lastResearch": {"taskId": identity(self.deployment, "source")},
                 "latestSourceAnswer": {"status": "partial", "sources": [{"materialFingerprint": "old"}]}}
        value.pop("kind")  # Legacy question rows use the owner's default kind.
        task = {**value, "taskId": value["lastResearch"]["taskId"], "brainCaseId": value["caseId"], "capability": "research"}
        with self.control.transaction() as c:
            self.control.insert(c, task)
            self.brain.save(c, value, task["taskId"], "fixture")
            c.execute("INSERT INTO ai_control_execution_state (scope_key,state_json,updated_at) VALUES (%s,%s,%s) "
                      "ON DUPLICATE KEY UPDATE state_json=VALUES(state_json)", ("shared-local-model", json.dumps(PAUSED), stamp()))
        with self.control.connect() as c:
            before = c.execute("SELECT payload_json FROM ai_brain_cases WHERE case_id=%s", (value["caseId"],)).fetchone()
            attach_question_progress(c, [value], NOW)
            after = c.execute("SELECT payload_json FROM ai_brain_cases WHERE case_id=%s", (value["caseId"],)).fetchone()
        self.assertEqual(before, after)
        self.assertEqual("quota", value["workflow"]["wait"]["errorCategory"])
        self.assertEqual(task["taskId"], value["workflow"]["activeTaskId"])
        self.assertFalse(value["workflow"]["answerComplete"])

    def test_execution_wait_details_are_fenced_and_preserve_existing_result(self):
        from digital_twin.modules.ai_orchestration.domain.planning import stamp, identity
        task = {**case(), "taskId": identity(self.deployment, "wait"), "capability": "research", "attempts": 1, "leaseToken": "current"}
        prior = {"documentaryAnswer": {"status": "partial", "text": "기존 답변"}}
        with self.control.transaction() as c:
            self.control.insert(c, task)
            c.execute("UPDATE ai_control_tasks SET status='processing',lease_token='current',lease_until='2100-01-01T00:00:00Z',result_json=%s WHERE task_id=%s", (json.dumps(prior), task["taskId"]))
        wait = AIExecutionDeferred("2026-10-25T10:00:00Z", "quota")
        self.assertFalse(self.control.defer_execution({**task, "leaseToken": "stale"}, wait))
        self.assertTrue(self.control.defer_execution(task, wait))
        with self.control.connect() as c:
            row = c.execute("SELECT result_json,payload_json FROM ai_control_tasks WHERE task_id=%s", (task["taskId"],)).fetchone()
        self.assertEqual(prior, json.loads(row["result_json"]))
        self.assertFalse(json.loads(row["payload_json"])["executionWait"]["retryIsReset"])
