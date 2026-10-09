"""Failure and restart rehearsals using synthetic inputs and isolated MySQL."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import subprocess
import unittest
from unittest.mock import Mock

from digital_twin.modules.ai_orchestration.domain.recovery import retry_delays
from digital_twin.modules.ai_orchestration.domain.runtime_coverage import runtime_coverage, runtime_view
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
from digital_twin.modules.ai_orchestration.domain.insight_repair import correction_warranted
from digital_twin.modules.outcomes.contracts import evaluate_observation_conditions


class ResumeChecks(unittest.TestCase):
    def workflow(self):
        from test_ai_control import AIControlTests
        for error in (TimeoutError("private provider response"), subprocess.TimeoutExpired(["private-token"], 240)):
            service, store, _ = AIControlTests().runner(planner=Mock(side_effect=error))
            store.fail.return_value = {"status": "pending"}
            self.assertEqual("deferred", service.run_once()["status"])
            self.assertEqual("ai-execution:timeout", store.fail.call_args.args[1])
            failure = store.fail.call_args.kwargs["result"]["failure"]
            self.assertEqual("author-execution", failure["stage"])
            self.assertNotIn("private", json.dumps(failure))
        for kind in ("TimeoutExpired", "TimeoutError", "ai-execution:timeout"):
            self.assertEqual((60, 300), retry_delays("observe", kind, 1))
            self.assertEqual((900, 300), retry_delays("observe", kind, 99))
        service, store, _ = AIControlTests().runner(planner=Mock(return_value={}))
        service.run_once()
        self.assertEqual("plan-validation", store.fail.call_args.kwargs["result"]["failure"]["stage"])
        self.assertEqual("validation:evidence-reference", store.fail.call_args.args[1])

    def conditions(self):
        from ai_insight_fixtures import observation, packet, plan
        original = observation()
        baseline = {"jobId": "receipt", "followUpConditions": original["followUpConditions"]}
        later = deepcopy(original["input"])
        # Source quote is inside the old window, but wasn't checked until after it.
        start = datetime.fromisoformat(later["capturedAt"].replace("Z", "+00:00"))
        later["capturedAt"] = (start + timedelta(days=2)).isoformat()
        later["facts"][0].update(sourceAsOf=(start + timedelta(hours=1)).isoformat(), currentPrice=120)
        checked = evaluate_observation_conditions(later, baseline)[0]
        self.assertFalse(checked["transitionVerified"])
        self.assertEqual("unevaluable", checked["evaluationState"])
        self.assertEqual("observation-window-missed", checked["reasonCode"])
        source = packet()
        source["facts"][0]["judgementEvidenceUsable"] = False
        rejected = {**validate_plan(plan(), source), "input": source}
        self.assertEqual("condition-baseline-unusable", rejected["conditionValidation"]["reasonCode"])
        self.assertIn(rejected["conditionValidation"], local_quality(rejected)["diagnostics"])
        self.assertFalse(correction_warranted(rejected))
        omitted = plan(); omitted.pop("followUpConditions")
        bad = validate_plan(omitted, packet())
        self.assertEqual("output", bad["conditionValidation"]["category"])

    def coverage(self):
        start = datetime(2026, 10, 9, tzinfo=timezone.utc)
        rows = [{"started_at": start.isoformat(), "last_seen_at": (start + timedelta(minutes=5)).isoformat()},
                {"started_at": (start + timedelta(minutes=3)).isoformat(), "last_seen_at": (start + timedelta(minutes=7)).isoformat()},
                {"started_at": (start + timedelta(hours=2)).isoformat(), "last_seen_at": (start + timedelta(hours=2, minutes=1)).isoformat()}]
        report = runtime_coverage(rows, start, start + timedelta(days=1))
        self.assertEqual(480, report["observedActiveSeconds"])
        self.assertEqual(86400 - 480, report["unobservedSeconds"])
        self.assertIsNone(report["exactDowntimeSeconds"])
        self.assertFalse(runtime_view({"startedAt": start.isoformat(), "lastSeenAt": start.isoformat()}, start + timedelta(minutes=3))["live"])

    def persistence(self, store):
        from test_ai_control import SUBJECT
        store.runtime_settings["aiControlBudgetEnabled"] = "false"
        now = datetime.now(timezone.utc)
        store.record_runtime_pulse(now=now - timedelta(hours=3), worker_id="first")
        store.record_runtime_pulse(now=now - timedelta(hours=3) + timedelta(minutes=1), worker_id="first")
        store.record_runtime_pulse(now=now - timedelta(hours=2), worker_id="first")
        store.record_runtime_pulse(now=now - timedelta(hours=2) + timedelta(minutes=1), worker_id="first")
        store.record_runtime_pulse(now=now - timedelta(seconds=10), worker_id="second")
        store.record_runtime_pulse(now=now, worker_id="second")
        # A backwards pulse cannot merge the missing hours into uptime.
        store.record_runtime_pulse(now=now - timedelta(hours=1), worker_id="first")
        with store.connect() as connection:
            report = store.runtime_report(connection, now - timedelta(days=1), now)
        self.assertEqual(130, report["observedActiveSeconds"])
        health = store.status()["executionHealth"]
        self.assertFalse(health["judgmentSavedSinceResume"])
        self.assertLess(health["callWindowMinutes"], 1)
        # Plain duplicate reservations collapse only after successful fenced completion.
        base = {**SUBJECT, "capability": "observe", "availableAt": "2000-01-01T00:00:00Z"}
        with store.transaction() as connection:
            for job in ({**base, "taskId": "a"}, {**base, "taskId": "b"},
                        {**base, "taskId": "question", "watchQuestions": ["different purpose"]},
                        {**base, "taskId": "wake", "evidenceWake": {"id": "event"}},
                        {**base, "taskId": "world", "worldId": "other-world"},
                        {**base, "taskId": "research", "capability": "research"},
                        {**base, "taskId": "retry"}):
                store.insert(connection, job)
            connection.execute("UPDATE ai_control_tasks SET attempts=1 WHERE task_id='retry'")
        job = store.claim()
        self.assertEqual("a", job["taskId"])
        with store.transaction() as connection:
            store.insert(connection, {**base, "taskId": "new-arrival"})
            store.insert(connection, {**base, "taskId": "future", "availableAt": "2100-01-01T00:00:00Z"})
            store.insert(connection, {**base, "taskId": "active"})
            connection.execute("UPDATE ai_control_tasks SET status='processing',lease_token='other-owner',lease_until='2100-01-01T00:00:00Z' WHERE task_id='active'")
            store.insert(connection, {**base, "taskId": "case-wake"})
            # The agenda wakes plain jobs through SQL priority, not a payload flag.
            connection.execute("UPDATE ai_control_tasks SET priority=3,created_at='2000-01-01T00:00:00Z' WHERE task_id='case-wake'")
        with self.assertRaises(KeyError):
            store.complete(job, {"status": "unchanged"}, [{"taskId": "invalid"}])
        with store.connect() as connection:
            self.assertEqual("pending", connection.execute("SELECT status FROM ai_control_tasks WHERE task_id='b'").fetchone()["status"])
        result = {"status": "unchanged"}
        self.assertTrue(store.complete(job, result, []))
        self.assertEqual(1, result["resumption"]["coalescedTaskCount"])
        self.assertFalse(store.complete(job, {"summary": "late owner"}, []))
        with store.connect() as connection:
            rows = {r["task_id"]: r for r in connection.execute("SELECT task_id,status,result_json FROM ai_control_tasks").fetchall()}
        self.assertEqual("superseded", json.loads(rows["b"]["result_json"])["status"])
        self.assertTrue(all(rows[key]["status"] == "pending" for key in ("question", "wake", "world", "research", "retry", "new-arrival", "future", "case-wake")))
        self.assertEqual("processing", rows["active"]["status"])
        health = store.status()["executionHealth"]
        self.assertTrue(health["observationCheckedSinceResume"])
        self.assertFalse(health["judgmentSavedSinceResume"])
