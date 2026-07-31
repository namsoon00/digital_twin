import unittest
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

    def test_scheduler_cools_down_schema_compile_errors_before_retrying(self):
        scheduler = OntologyRuleboxPrewarmScheduler(FakeRepository(), 15)

        self.assertEqual(60, scheduler.retry_interval_seconds({"status": "error"}))
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
