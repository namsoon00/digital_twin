import unittest
import time
from datetime import datetime, timezone

from digital_twin.application.ontology_rulebox_prewarm_service import (
    OntologyRuleboxPrewarmRunner,
)
from digital_twin.infrastructure.schedulers import OntologyRuleboxPrewarmScheduler


class FakeRepository:
    def __init__(self, result=None):
        self.result = dict(result or {"status": "ok", "functionsReady": True})
        self.calls = []
        self.status_calls = 0

    def prewarm_typedb_native_rule_functions(self, force=False):
        self.calls.append(bool(force))
        return dict(self.result)

    def schema_function_prewarm_status(self):
        self.status_calls += 1
        return dict(self.result)


class MemoryPrewarmStateStore:
    def __init__(self):
        self.payloads = []

    def replace(self, payload):
        self.payloads.append(dict(payload or {}))

    def load(self):
        return dict(self.payloads[-1]) if self.payloads else {}


class FakeIsolatedCycle:
    def __init__(self, result):
        self.result = dict(result or {})
        self.calls = []

    def run_once(self, *args):
        self.calls.append(args)
        return dict(self.result)


class QueueGuardedRunner(FakeRepository):
    def __init__(self, queue):
        super().__init__()
        self.queue = dict(queue or {})

    def reasoning_queue_state(self):
        return dict(self.queue)

    @staticmethod
    def pending_reasoning_count(payload):
        return int(dict(payload or {}).get("effectivePendingCount") or 0)

    @staticmethod
    def idle_quiet_seconds():
        return 300


class OntologyRuleboxPrewarmRunnerTests(unittest.TestCase):
    def test_low_disk_guard_defers_before_opening_the_typedb_compiler(self):
        repository = FakeRepository()
        runner = OntologyRuleboxPrewarmRunner(
            repository,
            settings={"ontologyRuleboxPrewarmEnabled": "1"},
            storage_guard=lambda: {"ready": False, "status": "blocked-low-disk"},
        )

        result = runner.run_once()

        self.assertEqual("deferred-low-disk", result["status"])
        self.assertEqual([], repository.calls)
        self.assertEqual(0, repository.status_calls)

    def test_runs_background_prewarm_only_when_explicitly_enabled_and_queue_is_empty(self):
        repository = FakeRepository({
            "status": "provisioning",
            "functionsReady": False,
            "pendingRuleCount": 3,
        })
        runner = OntologyRuleboxPrewarmRunner(
            repository,
            settings={"ontologyRuleboxPrewarmEnabled": "1"},
            reasoning_queue_probe=lambda: {"status": "ok", "effectivePendingCount": 0},
        )

        result = runner.run_once()

        self.assertEqual("provisioning", result["status"])
        self.assertTrue(result["background"])
        self.assertTrue(result["liveInferenceDeploymentAvoided"])
        self.assertEqual([False], repository.calls)

    def test_force_is_available_only_to_the_background_worker_command(self):
        repository = FakeRepository()
        runner = OntologyRuleboxPrewarmRunner(
            repository,
            settings={"ontologyRuleboxPrewarmEnabled": "1"},
            reasoning_queue_probe=lambda: {"status": "ok", "effectivePendingCount": 0},
        )

        result = runner.run_once(force=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual([True], repository.calls)

    def test_defers_compilation_while_live_reasoning_is_pending(self):
        repository = FakeRepository()
        runner = OntologyRuleboxPrewarmRunner(
            repository,
            settings={"ontologyRuleboxPrewarmEnabled": "1"},
            reasoning_queue_probe=lambda: {"status": "ok", "effectivePendingCount": 4},
        )

        result = runner.run_once()

        self.assertEqual("deferred-reasoning-pending", result["status"])
        self.assertEqual(4, result["reasoningPendingCount"])
        self.assertTrue(result["prewarmReadinessDeferred"])
        self.assertEqual([], repository.calls)
        self.assertEqual(0, repository.status_calls)

    def test_defers_compilation_while_reasoning_is_running_or_retrying(self):
        repository = FakeRepository()
        runner = OntologyRuleboxPrewarmRunner(
            repository,
            settings={"ontologyRuleboxPrewarmEnabled": "1"},
            reasoning_queue_probe=lambda: {
                "status": "idle",
                "effectivePendingCount": 0,
                "runningEntryCount": 1,
                "retryingEntryCount": 2,
            },
        )

        result = runner.run_once()

        self.assertEqual("deferred-reasoning-pending", result["status"])
        self.assertEqual(2, result["reasoningPendingCount"])
        self.assertEqual([], repository.calls)
        self.assertEqual(0, repository.status_calls)

    def test_defers_missing_function_compilation_while_a_live_queue_is_pending(self):
        repository = FakeRepository({
            "status": "provisioning",
            "functionsReady": False,
            "pendingRuleCount": 3,
        })
        runner = OntologyRuleboxPrewarmRunner(
            repository,
            settings={"ontologyRuleboxPrewarmEnabled": "1"},
            reasoning_queue_probe=lambda: {"status": "ok", "effectivePendingCount": 4},
        )

        result = runner.run_once()

        self.assertEqual("deferred-reasoning-pending", result["status"])
        self.assertIsNone(result["functionsReady"])
        self.assertIsNone(result["pendingRuleCount"])
        self.assertTrue(result["prewarmReadinessDeferred"])
        self.assertEqual([], repository.calls)
        self.assertEqual(0, repository.status_calls)

    def test_aged_multi_entry_queue_keeps_schema_compilation_out_of_active_live_work(self):
        repository = FakeRepository({
            "status": "provisioning",
            "functionsReady": False,
            "pendingRuleCount": 4,
        })
        runner = OntologyRuleboxPrewarmRunner(
            repository,
            settings={
                "ontologyRuleboxPrewarmEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryAgeSeconds": "90",
                "ontologyRuleboxPrewarmBacklogRecoveryMinPendingEntries": "2",
                "ontologyRuleboxPrewarmBacklogRecoveryRetrySeconds": "5",
                "typedbNativeRuleDirectQueryFallbackEnabled": "0",
            },
            reasoning_queue_probe=lambda: {
                "status": "pending",
                "effectivePendingCount": 4,
                "runningEntryCount": 1,
                "oldestRequestAt": "2026-07-24T00:00:00Z",
            },
            now_provider=lambda: datetime(2026, 7, 24, 0, 5, tzinfo=timezone.utc),
        )

        result = runner.run_once()

        self.assertEqual("deferred-aged-reasoning-backlog-active", result["status"])
        self.assertEqual([], repository.calls)
        self.assertTrue(result["backlogRecovery"]["eligible"])
        self.assertEqual(1, result["backlogRecovery"]["activeEntryCount"])
        self.assertEqual(15, result["recommendedRetryAfterSeconds"])

    def test_aged_retrying_queue_runs_compiler_recovery_when_no_inference_lease_is_active(self):
        repository = FakeRepository({
            "status": "provisioning",
            "functionsReady": False,
            "pendingRuleCount": 4,
        })
        runner = OntologyRuleboxPrewarmRunner(
            repository,
            settings={
                "ontologyRuleboxPrewarmEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryAgeSeconds": "90",
                "ontologyRuleboxPrewarmBacklogRecoveryMinPendingEntries": "2",
                "ontologyRuleboxPrewarmBacklogRecoveryRetrySeconds": "5",
                "typedbNativeRuleDirectQueryFallbackEnabled": "0",
            },
            reasoning_queue_probe=lambda: {
                "status": "retrying",
                "effectivePendingCount": 4,
                "retryingEntryCount": 4,
                "oldestRequestAt": "2026-07-24T00:00:00Z",
            },
            now_provider=lambda: datetime(2026, 7, 24, 0, 5, tzinfo=timezone.utc),
        )

        result = runner.run_once()

        self.assertEqual("provisioning", result["status"])
        self.assertEqual([False], repository.calls)
        self.assertTrue(result["backlogRecovery"]["canRecover"])
        self.assertTrue(result["backlogRecoveryGranted"])
        self.assertEqual("aged-backlog-no-active-inference-lease", result["recoveryMode"])

    def test_aged_retrying_queue_keeps_compiler_idle_when_direct_typeql_fallback_is_available(self):
        repository = FakeRepository({
            "status": "provisioning",
            "functionsReady": False,
            "pendingRuleCount": 4,
        })
        runner = OntologyRuleboxPrewarmRunner(
            repository,
            settings={
                "ontologyRuleboxPrewarmEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryAgeSeconds": "90",
                "ontologyRuleboxPrewarmBacklogRecoveryMinPendingEntries": "2",
                "typedbNativeRuleDirectQueryFallbackEnabled": "1",
            },
            reasoning_queue_probe=lambda: {
                "status": "retrying",
                "effectivePendingCount": 4,
                "retryingEntryCount": 4,
                "oldestRequestAt": "2026-07-24T00:00:00Z",
            },
            now_provider=lambda: datetime(2026, 7, 24, 0, 5, tzinfo=timezone.utc),
        )

        result = runner.run_once()

        self.assertEqual("deferred-reasoning-pending", result["status"])
        self.assertFalse(result["backlogRecovery"]["eligible"])
        self.assertTrue(result["backlogRecovery"]["directTypeqlFallbackEnabled"])
        self.assertEqual([], repository.calls)

    def test_status_reports_isolation_and_current_prewarm_state(self):
        repository = FakeRepository({"status": "ok", "functionsReady": True})
        runner = OntologyRuleboxPrewarmRunner(
            repository,
            settings={
                "ontologyRuleboxPrewarmEnabled": "1",
                "ontologyRuleboxPrewarmIntervalSeconds": "7",
                "ontologyRuleboxPrewarmProcessIsolationEnabled": "1",
            },
        )

        status = runner.status()

        self.assertTrue(status["enabled"])
        self.assertEqual(7, status["intervalSeconds"])
        self.assertEqual(300, status["idleQuietSeconds"])
        self.assertTrue(status["processIsolationEnabled"])
        self.assertTrue(status["deferWhenReasoningPending"])
        self.assertTrue(status["prewarm"]["functionsReady"])

    def test_default_execution_limit_matches_the_environment_contract(self):
        runner = OntologyRuleboxPrewarmRunner(FakeRepository())

        self.assertEqual(1500, runner.execution_timeout_seconds())
        self.assertEqual(10, runner.execution_timeout_grace_seconds())

    def test_publishes_a_durable_cooldown_after_an_interrupted_compiler_call(self):
        now = datetime(2026, 7, 24, 0, 5, tzinfo=timezone.utc)
        state_store = MemoryPrewarmStateStore()
        runner = OntologyRuleboxPrewarmRunner(
            FakeRepository({
                "status": "error",
                "functionsReady": False,
                "reason": "http2 error: transport error: keep-alive timed out",
            }),
            settings={"ontologyRuleboxPrewarmEnabled": "1"},
            reasoning_queue_probe=lambda: {"status": "idle", "effectivePendingCount": 0},
            now_provider=lambda: now,
            prewarm_state_store=state_store,
        )

        result = runner.run_once()

        self.assertEqual("cooldown", result["prewarmActivity"]["status"])
        self.assertTrue(result["prewarmActivity"]["active"])
        self.assertEqual(2, len(state_store.payloads))
        self.assertEqual("running", state_store.payloads[0]["status"])
        self.assertEqual("cooldown", state_store.payloads[-1]["status"])
        self.assertEqual(900, int(state_store.payloads[-1]["expiresAtEpoch"] - now.timestamp()))

    def test_defers_a_second_compiler_while_a_durable_cooldown_is_active(self):
        now = datetime(2026, 7, 24, 0, 5, tzinfo=timezone.utc)
        state_store = MemoryPrewarmStateStore()
        state_store.replace({
            "status": "cooldown",
            "active": True,
            "expiresAtEpoch": now.timestamp() + 120,
        })
        repository = FakeRepository()
        runner = OntologyRuleboxPrewarmRunner(
            repository,
            settings={"ontologyRuleboxPrewarmEnabled": "1"},
            now_provider=lambda: now,
            prewarm_state_store=state_store,
        )

        result = runner.run_once()

        self.assertEqual("deferred-compiler-activity", result["status"])
        self.assertEqual("cooldown", result["prewarmActivity"]["status"])
        self.assertEqual(15, result["recommendedRetryAfterSeconds"])
        self.assertEqual([], repository.calls)

    def test_scheduler_keeps_an_isolated_compiler_child_idle_during_activity(self):
        now = datetime(2026, 7, 24, 0, 5, tzinfo=timezone.utc)
        state_store = MemoryPrewarmStateStore()
        state_store.replace({
            "status": "running",
            "active": True,
            "expiresAtEpoch": now.timestamp() + 120,
        })
        repository = FakeRepository()
        runner = OntologyRuleboxPrewarmRunner(
            repository,
            settings={"ontologyRuleboxPrewarmEnabled": "1"},
            now_provider=lambda: now,
            prewarm_state_store=state_store,
        )
        scheduler = OntologyRuleboxPrewarmScheduler(runner, 15)

        result = scheduler.run_once()

        self.assertEqual("deferred-compiler-activity", result["status"])
        self.assertEqual([], repository.calls)

    def test_scheduler_publishes_cooldown_when_an_isolated_child_times_out(self):
        now = datetime(2026, 7, 24, 0, 5, tzinfo=timezone.utc)
        state_store = MemoryPrewarmStateStore()
        runner = OntologyRuleboxPrewarmRunner(
            FakeRepository(),
            settings={
                "ontologyRuleboxPrewarmEnabled": "1",
                "ontologyRuleboxPrewarmIdleQuietSeconds": "30",
            },
            reasoning_queue_probe=lambda: {"status": "idle", "effectivePendingCount": 0},
            now_provider=lambda: now,
            prewarm_state_store=state_store,
        )
        isolated_cycle = FakeIsolatedCycle({"status": "timeout", "functionsReady": False})
        scheduler = OntologyRuleboxPrewarmScheduler(runner, 15, isolated_cycle=isolated_cycle)
        scheduler.last_reasoning_activity_at = time.monotonic() - 31

        result = scheduler.run_once()

        self.assertEqual("timeout", result["status"])
        self.assertEqual(1, len(isolated_cycle.calls))
        self.assertEqual("cooldown", result["prewarmActivity"]["status"])
        self.assertEqual(900, int(result["prewarmActivity"]["expiresAtEpoch"] - now.timestamp()))

    def test_status_uses_compiler_activity_without_opening_a_typedb_readiness_connection(self):
        now = datetime(2026, 7, 24, 0, 5, tzinfo=timezone.utc)
        state_store = MemoryPrewarmStateStore()
        state_store.replace({
            "status": "running",
            "active": True,
            "expiresAtEpoch": now.timestamp() + 120,
        })
        repository = FakeRepository()
        runner = OntologyRuleboxPrewarmRunner(
            repository,
            now_provider=lambda: now,
            prewarm_state_store=state_store,
        )

        status = runner.status()

        self.assertEqual("compiler-running", status["prewarm"]["status"])
        self.assertTrue(status["prewarmActivity"]["active"])
        self.assertEqual(0, repository.status_calls)

    def test_scheduler_cools_down_schema_compile_errors_before_retrying(self):
        scheduler = OntologyRuleboxPrewarmScheduler(FakeRepository(), 15)

        self.assertEqual(60, scheduler.retry_interval_seconds({"status": "error"}))
        self.assertEqual(300, scheduler.retry_interval_seconds({
            "status": "error",
            "reason": "http2 error: transport error: keep-alive timed out",
        }))
        self.assertEqual(30, scheduler.retry_interval_seconds({"status": "provisioning"}))
        self.assertEqual(300, scheduler.retry_interval_seconds({"status": "timeout"}))
        self.assertEqual(30, scheduler.retry_interval_seconds({"status": "deferred-reasoning-pending"}))
        self.assertEqual(15, scheduler.retry_interval_seconds({"status": "ok"}))

    def test_scheduler_does_not_promote_an_aged_backlog_compile_retry(self):
        scheduler = OntologyRuleboxPrewarmScheduler(FakeRepository(), 15)

        retry = scheduler.retry_interval_seconds({
            "status": "provisioning",
            "recommendedRetryAfterSeconds": 5,
            "backlogRecovery": {"eligible": True},
        })

        self.assertEqual(30, retry)

    def test_scheduler_promotes_only_a_granted_aged_backlog_recovery(self):
        scheduler = OntologyRuleboxPrewarmScheduler(FakeRepository(), 15)

        retry = scheduler.retry_interval_seconds({
            "status": "provisioning",
            "recommendedRetryAfterSeconds": 5,
            "backlogRecoveryGranted": True,
        })

        self.assertEqual(5, retry)

    def test_scheduler_never_starts_an_isolated_compiler_while_queue_is_nonempty(self):
        runner = QueueGuardedRunner({"effectivePendingCount": 3})
        scheduler = OntologyRuleboxPrewarmScheduler(runner, 15)

        result = scheduler.run_once()

        self.assertEqual("deferred-reasoning-pending", result["status"])
        self.assertEqual(3, result["reasoningPendingCount"])
        self.assertEqual([], runner.calls)

    def test_scheduler_allows_only_an_unleased_aged_backlog_recovery(self):
        repository = FakeRepository({
            "status": "provisioning",
            "functionsReady": False,
            "pendingRuleCount": 2,
        })
        runner = OntologyRuleboxPrewarmRunner(
            repository,
            settings={
                "ontologyRuleboxPrewarmEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryEnabled": "1",
                "ontologyRuleboxPrewarmBacklogRecoveryAgeSeconds": "90",
                "ontologyRuleboxPrewarmBacklogRecoveryMinPendingEntries": "2",
                "typedbNativeRuleDirectQueryFallbackEnabled": "0",
            },
            reasoning_queue_probe=lambda: {
                "status": "retrying",
                "effectivePendingCount": 2,
                "retryingEntryCount": 2,
                "oldestRequestAt": "2026-07-24T00:00:00Z",
            },
            now_provider=lambda: datetime(2026, 7, 24, 0, 5, tzinfo=timezone.utc),
        )
        scheduler = OntologyRuleboxPrewarmScheduler(runner, 15)

        result = scheduler.run_once()

        self.assertEqual("provisioning", result["status"])
        self.assertTrue(result["backlogRecoveryGranted"])
        self.assertEqual([False], repository.calls)


if __name__ == "__main__":
    unittest.main()
