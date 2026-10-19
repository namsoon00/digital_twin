"""Real queue regressions for starvation, restart recovery and atomic dispatch."""
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import patch

from digital_twin.modules.ai_orchestration.domain.budget import AIControlBudgetWait
from digital_twin.modules.ai_orchestration.domain.planning import stamp
from digital_twin.modules.ai_orchestration.domain.execution_resilience import stamp as clock_stamp
from digital_twin.modules.ai_orchestration.domain.task_dispatch import SCOPE


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class AITaskDispatchTests(unittest.TestCase):
    def setUp(self):
        from digital_twin.infrastructure.settings import runtime_settings
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
        self.store_type = MySQLAIControlStore
        self.store = self.store_type({**runtime_settings(), "aiControlBudgetEnabled": "false"})
        self.clean()

    def clean(self):
        with self.store.transaction() as c:
            for table in ("ai_control_input_calls", "ai_control_inputs", "ai_control_tasks", "ai_control_budget", "ai_control_execution_state"):
                c.execute("DELETE FROM " + table)

    def tearDown(self):
        self.clean()

    def insert(self, task_id, capability, priority=0, available_at=None, account="test-dispatch"):
        job = {"taskId": task_id, "accountId": account, "symbol": "TEST", "name": "Test",
               "worldId": "test-world", "capability": capability, "priority": priority,
               "availableAt": available_at or stamp()}
        with self.store.transaction() as c:
            self.store.insert(c, job)
        return job

    def cursor(self):
        with self.store.connect() as c:
            row = c.execute("SELECT state_json FROM ai_control_execution_state WHERE scope_key=%s", (SCOPE,)).fetchone()
        return json.loads(row["state_json"]) if row else {}

    def test_continuous_source_arrivals_preserve_observation_and_planning_turns_across_restart(self):
        old = clock_stamp(datetime.now(timezone.utc) - timedelta(hours=2))
        self.insert("old-observation", "observe", available_at=old)
        self.insert("urgent-observation", "observe", priority=3)
        claimed = []
        for number in range(12):
            self.insert("source-" + str(number), "research", priority=100)
            self.insert("plan-" + str(number), "research-plan", priority=50)
            self.insert("observe-" + str(number), "observe", priority=3)
            # A new store instance must continue the shared sequence.
            self.store = self.store_type(self.store.runtime_settings)
            job = self.store.claim()
            claimed.append(job)
            self.assertTrue(self.store.complete(job, {"status": "test-completed"}, []))
        self.assertEqual("old-observation", claimed[0]["taskId"])
        self.assertEqual(["observe", "research", "observe", "research-plan"] * 3,
                         [j["capability"] for j in claimed])
        self.assertEqual(12, self.cursor()["dispatchCount"])
        from execution_lane_checks import check_native_lanes
        check_native_lanes(self)

    def test_expired_plan_lease_recovers_amid_backlog_without_reviving_stale_writer(self):
        self.insert("expired-plan", "research-plan")
        previous = self.store.claim()
        self.insert("live-plan", "research-plan")
        live = self.store.claim()
        with self.store.connect() as c:
            c.execute("UPDATE ai_control_tasks SET lease_until='2000-01-01T00:00:00Z' WHERE task_id=%s", (previous["taskId"],))
        for number in range(6):
            self.insert("source-" + str(number), "research", priority=100)
        self.insert("normal-observation", "observe")
        self.store = self.store_type(self.store.runtime_settings)
        before = self.store.status()["taskScheduling"]
        self.assertEqual(1, sum(q["expiredLeases"] for q in before["queues"]))
        recovered = self.store.claim()
        self.assertEqual(previous["taskId"], recovered["taskId"])
        self.assertEqual(2, recovered["attempts"])
        self.assertEqual("recovery", recovered["taskDispatch"]["lane"])
        self.assertEqual("2000-01-01T00:00:00Z", recovered["taskDispatch"]["recoveredLeaseUntil"])
        self.assertNotEqual(previous["leaseToken"], recovered["leaseToken"])
        self.assertFalse(self.store.heartbeat(previous))
        child = {**self.insert("future-child", "observe", available_at="2099"), "taskId": "stale-child"}
        self.assertFalse(self.store.complete(previous, {"summary": "stale"}, [child]))
        self.assertTrue(self.store.heartbeat(live))
        self.assertTrue(self.store.complete(recovered, {"status": "research-planned"}, []))
        self.assertEqual("observe", self.store.claim()["capability"])
        with self.store.connect() as c:
            self.assertIsNone(c.execute("SELECT task_id FROM ai_control_tasks WHERE task_id='stale-child'").fetchone())
            row = c.execute("SELECT payload_json FROM ai_control_tasks WHERE task_id='expired-plan'").fetchone()
        self.assertEqual(recovered["taskDispatch"], json.loads(row["payload_json"])["taskDispatch"])
        self.assertEqual(1, self.cursor()["recoveredLeaseCount"])

    def test_dispatch_respects_cooldown_call_capacity_and_rolls_back_failed_claim(self):
        self.insert("future-source", "research", priority=100, available_at="2099")
        self.insert("due-observation", "observe")
        self.insert("due-source", "research")
        self.store.runtime_settings.update(aiControlBudgetEnabled="true", aiControlDailyTaskBudget=10, aiControlDailyCallBudget=1)
        first = self.store.claim()
        self.assertEqual("due-source", first["taskId"])
        saved = self.cursor()
        for _ in range(2):
            with self.assertRaises(AIControlBudgetWait):
                self.store.claim()
        self.assertEqual(saved, self.cursor())
        self.assertEqual(1, self.store.status()["tasksStartedToday"])
        self.store.runtime_settings["aiControlDailyCallBudget"] = 2
        with patch.object(self.store, "_save_execution_state", side_effect=RuntimeError("rollback after lease update")):
            with self.assertRaises(RuntimeError):
                self.store.claim()
        self.assertEqual(saved, self.cursor())
        status = self.store.status()
        self.assertEqual(1, status["tasksStartedToday"])
        self.assertEqual(0, next(t for t in status["tasks"] if t["taskId"] == "due-observation")["attempts"])
        self.assertEqual("due-observation", self.store.claim()["taskId"])
        self.assertIsNone(self.store.claim())
        # A budget deferral is the sole exception to future availability; a
        # raised limit must still resume it without waking source cooldowns.
        with self.store.connect() as c:
            c.execute("UPDATE ai_control_tasks SET last_error='ai-call-budget-exhausted' WHERE task_id='future-source'")
        self.assertEqual("future-source", self.store.claim()["taskId"])

    def test_concurrent_dispatches_are_distinct_and_queue_status_is_account_scoped(self):
        for number in range(4):
            self.insert("observe-" + str(number), "observe")
            self.insert("source-" + str(number), "research", priority=5)
            self.insert("plan-" + str(number), "research-plan", priority=4)
        self.insert("other-account", "research", available_at="2099", account="other")
        workers = [self.store_type(self.store.runtime_settings) for _ in range(4)]
        barrier = Barrier(4)
        def claim(store):
            barrier.wait(timeout=10)
            return store.claim()
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(pool.map(claim, workers))
        self.assertEqual(4, len({j["taskId"] for j in jobs}))
        self.assertEqual(["observe", "observe", "research", "research-plan"], sorted(j["capability"] for j in jobs))
        self.assertEqual(4, self.cursor()["dispatchCount"])
        scoped = self.store.status("test-dispatch")["taskScheduling"]
        self.assertEqual(8, sum(q["due"] for q in scoped["queues"]))
        self.assertEqual(4, sum(q["processing"] for q in scoped["queues"]))
        other = self.store.status("other")["taskScheduling"]
        self.assertEqual(1, sum(q["pending"] for q in other["queues"]))
        self.assertEqual(0, sum(q["due"] for q in other["queues"]))
        self.assertNotIn("lastDispatch", other)
