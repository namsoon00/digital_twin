import unittest

from digital_twin.application.ontology_rulebox_prewarm_service import (
    OntologyRuleboxPrewarmRunner,
)
from digital_twin.infrastructure.schedulers import OntologyRuleboxPrewarmScheduler


class FakeRepository:
    def __init__(self, result=None):
        self.result = dict(result or {"status": "ok", "functionsReady": True})
        self.calls = []

    def prewarm_typedb_native_rule_functions(self, force=False):
        self.calls.append(bool(force))
        return dict(self.result)

    def schema_function_prewarm_status(self):
        return dict(self.result)


class OntologyRuleboxPrewarmRunnerTests(unittest.TestCase):
    def test_runs_background_prewarm_without_waiting_for_reasoning_queue(self):
        repository = FakeRepository({
            "status": "provisioning",
            "functionsReady": False,
            "pendingRuleCount": 3,
        })
        runner = OntologyRuleboxPrewarmRunner(repository)

        result = runner.run_once()

        self.assertEqual("provisioning", result["status"])
        self.assertTrue(result["background"])
        self.assertTrue(result["liveInferenceDeploymentAvoided"])
        self.assertEqual([False], repository.calls)

    def test_force_is_available_only_to_the_background_worker_command(self):
        repository = FakeRepository()
        runner = OntologyRuleboxPrewarmRunner(repository)

        result = runner.run_once(force=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual([True], repository.calls)

    def test_status_reports_isolation_and_current_prewarm_state(self):
        repository = FakeRepository({"status": "ok", "functionsReady": True})
        runner = OntologyRuleboxPrewarmRunner(
            repository,
            settings={
                "ontologyRuleboxPrewarmIntervalSeconds": "7",
                "ontologyRuleboxPrewarmProcessIsolationEnabled": "1",
            },
        )

        status = runner.status()

        self.assertTrue(status["enabled"])
        self.assertEqual(7, status["intervalSeconds"])
        self.assertTrue(status["processIsolationEnabled"])
        self.assertFalse(status["deferWhenReasoningPending"])
        self.assertTrue(status["prewarm"]["functionsReady"])

    def test_scheduler_cools_down_schema_compile_errors_before_retrying(self):
        scheduler = OntologyRuleboxPrewarmScheduler(FakeRepository(), 15)

        self.assertEqual(60, scheduler.retry_interval_seconds({"status": "error"}))
        self.assertEqual(30, scheduler.retry_interval_seconds({"status": "provisioning"}))
        self.assertEqual(15, scheduler.retry_interval_seconds({"status": "ok"}))


if __name__ == "__main__":
    unittest.main()
