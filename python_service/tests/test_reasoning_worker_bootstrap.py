"""Historical diagnostics must never become the worker's startup dependency."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from digital_twin.modules.reasoning.application.reasoning_engine_platform import ReasoningEnginePlatformService
from digital_twin.modules.reasoning.application.independent_reasoning_engine import IndependentReasoningJobRunner
from digital_twin.modules.reasoning.domain.reasoning_engine_versions import EngineControlState
from stabilization_database import StabilizationDatabaseCase


class ReasoningWorkerBootstrapTests(unittest.TestCase):
    def test_worker_reconciles_control_without_reading_historical_diagnostics(self):
        registry = Mock()
        registry.control.return_value = EngineControlState("active", "active", "", 2)
        registry.list.return_value = [{"deploymentId": "active", "engineVersion": "v2"}]
        registry.retire_unselected.return_value = {"status": "unchanged"}
        platform = ReasoningEnginePlatformService(registry, {})
        platform.descriptors = Mock(return_value=[])
        platform.synchronize_control_capabilities = Mock(return_value={"status": "synchronized"})
        platform.independent_job_store = Mock()
        platform.shadow_queue = Mock()
        platform.comparison_store = Mock()
        for store in (platform.independent_job_store, platform.shadow_queue, platform.comparison_store):
            store.summary.side_effect = TimeoutError("historical metrics unavailable")
        result = platform.initialize(include_diagnostics=False)
        self.assertEqual("active", result["control"]["delivery_deployment_id"])
        self.assertEqual("not-requested", result["diagnosticsStatus"])
        platform.synchronize_control_capabilities.assert_called_once()
        registry.retire_unselected.assert_called_once()
        for store in (platform.independent_job_store, platform.shadow_queue, platform.comparison_store):
            store.summary.assert_not_called()

    def test_live_summary_uses_pending_state_but_candidate_evidence_keeps_full_history(self):
        class Queue:
            def runtime_summary(self, deployment_id):
                return {"deploymentId": deployment_id, "pendingCount": 2, "historyIncluded": False}
            def summary(self, deployment_id, release_fingerprint="", validation_cohort_id=""):
                return {"successfulRunCount": 4, "release": release_fingerprint, "cohort": validation_cohort_id}
        engine = Mock()
        engine.release_identity.return_value = {"releaseFingerprint": "release:exact", "validationCohortId": "cohort:exact"}
        runner = IndependentReasoningJobRunner(Queue(), engine, Mock())
        self.assertEqual(2, runner.queue_summary("active")["pendingCount"])
        self.assertEqual({"successfulRunCount": 4, "release": "release:exact", "cohort": "cohort:exact"},
                         runner.queue_summary("candidate", include_history=True))

    def test_diagnostic_failure_is_unknown_not_empty_or_a_job_retry(self):
        class Queue:
            def runtime_summary(self, deployment_id):
                raise TimeoutError("telemetry timed out")
            def summary(self, deployment_id):
                raise AssertionError("must not retry through the expensive history")
        runner = IndependentReasoningJobRunner(Queue(), Mock(), Mock())
        result = runner.queue_summary("active")
        self.assertEqual("unavailable", result["status"])
        self.assertNotIn("pendingCount", result)
        self.assertNotIn("successfulRunCount", result)

    def test_cli_watch_uses_execution_only_initialization(self):
        from digital_twin.infrastructure import cli, reasoning_engine_factory, service_factory
        platform = Mock()
        args = SimpleNamespace(reasoning_engine_action="v2-watch", worker_id="", role="delivery", deployment_id="")
        with patch.object(cli, "runtime_settings", return_value={}), \
             patch.object(reasoning_engine_factory, "build_reasoning_engine_platform", return_value=platform), \
             patch.object(service_factory, "build_v2_reasoning_job_runner"), \
             patch.object(cli, "watch_v2_reasoning_engine", return_value=0):
            self.assertEqual(0, cli.reasoning_engine_platform_command(args))
        platform.initialize.assert_called_once_with(include_diagnostics=False)


class ReasoningRuntimeSummaryTests(StabilizationDatabaseCase):
    def test_runtime_summary_reads_pending_lane_and_source_age_without_terminal_payloads(self):
        self.events.handle(self.source(suffix="runtime-summary"))
        value = self.jobs.runtime_summary(self.deployment)
        self.assertGreater(value["pendingCount"], 0)
        self.assertFalse(value["historyIncluded"])
        self.assertIsNotNone(value["oldestPendingAgeSeconds"])
        self.assertEqual(sum(value["pendingByLane"].values()), value["pendingCount"])
        self.assertNotIn("successfulRunCount", value)
        self.assertNotIn("durationP95Ms", value)
        self.assertEqual(0, self.jobs.runtime_summary("other-deployment")["pendingCount"])
        with self.assertRaises(ValueError):
            self.jobs.runtime_summary("")
