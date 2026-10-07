"""Shared recovery locking and queue fencing against isolated MySQL only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
import threading
import unittest

from digital_twin.modules.ai_orchestration.domain.execution_resilience import AIExecutionDeferred
from digital_twin.modules.ai_orchestration.infrastructure.mysql_execution import SCOPE, PROGRESS
from test_ai_control import SUBJECT
from ai_execution_resilience_checks import FAILURE


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class ExecutionPersistenceChecks(unittest.TestCase):
    def check_isolated(self, name):
        self.setUp()
        try:
            getattr(self, name)()
        finally:
            self.tearDown()

    def setUp(self):
        from digital_twin.infrastructure.settings import runtime_settings
        from digital_twin.infrastructure.mysql_monitoring import mysql_settings
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
        self.settings = {**runtime_settings(), "mysqlDatabase": "orbit_alpha_test", "aiControlBudgetEnabled": "false"}
        self.assertEqual("orbit_alpha_test", mysql_settings(self.settings)["database"])
        self.store = MySQLAIControlStore(self.settings)
        self.clean()

    def clean(self):
        with self.store.transaction() as c:
            for table in ("ai_control_input_calls", "ai_control_inputs", "ai_control_tasks", "ai_control_budget",
                          "ai_control_calls", "ai_control_call_metrics", "ai_control_execution_state"):
                c.execute("DELETE FROM " + table)

    def tearDown(self):
        self.clean()

    def check_two_workers_share_pause_and_only_one_can_probe(self):
        second = self.store.__class__(self.settings)
        for _ in range(3):
            ticket = self.store.acquire_execution()
            self.store.finish_execution(ticket, "failure", FAILURE)
        with self.assertRaises(AIExecutionDeferred):
            second.acquire_execution()
        with self.store.transaction() as c:
            c.execute("UPDATE ai_control_execution_state SET state_json=JSON_SET(state_json,'$.blockedUntil','2000-01-01T00:00:00Z') WHERE scope_key=%s", (SCOPE,))
        barrier = threading.Barrier(2)
        def try_probe(store):
            barrier.wait(timeout=5)
            try:
                return store.acquire_execution()
            except AIExecutionDeferred:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(try_probe, (self.store, second)))
        winners = [result for result in results if result is not None]
        self.assertEqual(1, len(winners))
        second.finish_execution(winners[0], "success")
        self.store.execution_wait()

    def check_gate_waits_keep_failure_budget_and_reject_old_task_lease(self):
        self.store.seed(SUBJECT)
        stale = None
        for _ in range(5):
            job = self.store.claim()
            self.assertTrue(self.store.defer_execution(job, AIExecutionDeferred("2000-01-01T00:00:00Z", "quota")))
            stale = job
        current = self.store.claim()
        self.assertFalse(self.store.defer_execution(stale, AIExecutionDeferred("2100-01-01T00:00:00Z")))
        self.assertEqual("pending", self.store.fail(current, "ai-execution:network")["status"])
        with self.store.connect() as c:
            row = c.execute("SELECT status,payload_json FROM ai_control_tasks WHERE task_id=%s", (current["taskId"],)).fetchone()
        self.assertEqual(5, json.loads(row["payload_json"])["executionDeferrals"])
        self.assertEqual("pending", row["status"])

    def check_progress_commits_with_result_and_never_moves_backwards(self):
        self.store.seed(SUBJECT)
        job = self.store.claim()
        with self.store.transaction() as c:
            self.store.record_observation_progress(c, job, {"summary": "fixture"}, "2026-10-07T06:02:00Z")
            self.store.record_observation_progress(c, job, {"summary": "fixture"}, "2026-10-07T06:01:00Z")
        with self.store.connect() as c:
            row = c.execute("SELECT state_json FROM ai_control_execution_state WHERE scope_key=%s", (PROGRESS,)).fetchone()
        self.assertEqual("2026-10-07T06:02:00Z", json.loads(row["state_json"])["lastJudgmentAt"])
        with self.store.transaction() as c:
            c.execute("DELETE FROM ai_control_execution_state WHERE scope_key=%s", (PROGRESS,))
        self.assertTrue(self.store.complete(job, {"status": "unchanged"}, []))
        health = self.store.status()["executionHealth"]
        self.assertTrue(health["lastObservationCheckAt"])
        self.assertFalse(health["lastJudgmentAt"])
