"""Admission and bounded execution under a continuously nonempty live queue."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from digital_twin.modules.reasoning.application.independent_reasoning_engine import IndependentReasoningJobRunner
from digital_twin.modules.reasoning.application.ontology_maintenance_service import OntologyMaintenanceRunner


class MaintenanceFairnessTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.state = {
            "reasoningQueueDeferredSinceAt": (self.now - timedelta(minutes=10)).isoformat(),
            "knownWorlds": [{"worldId": "portfolio:test", "worldType": "portfolio"}],
        }
        self.queue = {"effectivePendingCount": 6, "processingCount": 0}
        self.repository = SimpleNamespace(
            run_deferred_maintenance=Mock(return_value={
                "status": "ok",
                "abox": {
                    "status": "ok", "completedInactiveManifestCount": 3,
                    "remainingInactiveManifestCount": 1,
                    "removedManifestIds": ["retired-1", "retired-2"],
                },
            }),
            acquire_projection_coordinator_lease=Mock(return_value={"acquired": True}),
            release_projection_coordinator_lease=Mock(return_value={"released": True}),
        )
        self.maintenance = OntologyMaintenanceRunner(
            self.repository,
            state_store=SimpleNamespace(
                load=lambda: dict(self.state), replace=self.replace_state,
            ),
            reasoning_queue_probe=lambda: dict(self.queue),
        )

    def replace_state(self, value):
        self.state = dict(value)

    def test_candidate_cursor_survives_worker_recreation_and_failed_turns(self):
        self.queue = {"effectivePendingCount": 0, "processingCount": 0}
        cursor = {"updatedAt": "2026-09-30T01:00:00Z", "manifestId": "retired-2"}
        self.repository.run_deferred_maintenance.return_value = {
            "status": "partial", "abox": {
                "status": "partial", "completedInactiveManifestCount": 10,
                "remainingInactiveManifestCount": 10, "deletedBatchCount": 0,
                "nextCandidateCursor": cursor, "candidateScanComplete": True,
                "candidateSelectionMode": "rotating-retired-manifests-v1",
            },
        }
        self.maintenance.run_once()
        self.assertEqual({}, self.repository.run_deferred_maintenance.call_args.args[0]["candidateCursor"])
        self.assertEqual(cursor, self.state["backlogByWorld"]["portfolio:test"]["candidateCursor"])
        self.assertFalse(self.state["backlogByWorld"]["portfolio:test"]["lastProgress"])
        restarted = OntologyMaintenanceRunner(
            self.repository,
            state_store=SimpleNamespace(load=lambda: dict(self.state), replace=self.replace_state),
            reasoning_queue_probe=lambda: dict(self.queue),
        )
        for abox in [
            {"status": "blocked-protection-metadata", "nextCandidateCursor": {}},
            {"status": "skipped"},
            {"status": "error"},
        ]:
            self.repository.run_deferred_maintenance.return_value = {"status": "partial", "abox": abox}
            restarted.run_once()
            self.assertEqual(cursor, self.repository.run_deferred_maintenance.call_args.args[0]["candidateCursor"])
            self.assertEqual(cursor, self.state["backlogByWorld"]["portfolio:test"]["candidateCursor"])

    def test_fairness_preserves_active_lease_age_and_cooldown_guards(self):
        scenarios = [
            ({"processingCount": 1}, {}, "active-reasoning-lease"),
            ({}, {}, "active-lease-unknown"),
            ({"processingCount": 0}, {
                "reasoningQueueDeferredSinceAt": self.now.isoformat(),
            }, "background-within-deferral-budget"),
            ({"processingCount": 0}, {
                "lastFairnessCompletedAt": self.now.isoformat(),
            }, "fairness-cooldown"),
        ]
        original = dict(self.state)
        for counts, state, reason in scenarios:
            with self.subTest(reason=reason):
                self.queue = {"effectivePendingCount": 6, **counts}
                self.state = {**original, **state}
                result = self.maintenance.run_once()
                self.assertEqual("deferred-reasoning-queue", result["status"])
                self.assertEqual(reason, result["backgroundFairness"]["reasonCode"])
                self.repository.run_deferred_maintenance.assert_not_called()
                self.repository.acquire_projection_coordinator_lease.assert_not_called()
        self.queue = {"effectivePendingCount": 6, "processingCount": 0}
        self.state = original
        self.maintenance.settings["ontologyAboxMaintenanceStrictReasoningPriority"] = "1"
        self.assertEqual("strict-live-reasoning-priority",
                         self.maintenance.run_once()["backgroundFairness"]["reasonCode"])
        self.repository.run_deferred_maintenance.assert_not_called()

    def test_embedded_turn_retries_failure_then_completes_bounded_retention(self):
        runner = IndependentReasoningJobRunner(
            SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
            background_graph_tasks=[{
                "name": "abox-maintenance", "runner": self.maintenance, "intervalSeconds": 60,
            }],
        )
        events = []

        def inference():
            events.append("inference-completed")
            return {"status": "ready", "processedCount": 1}

        runner._run_once = inference
        self.repository.acquire_projection_coordinator_lease.return_value = {"acquired": False}
        runner.run_watch_turn()
        self.repository.run_deferred_maintenance.assert_not_called()
        self.assertNotIn("lastFairnessCompletedAt", self.state)

        self.repository.acquire_projection_coordinator_lease.return_value = {"acquired": True}
        self.repository.run_deferred_maintenance.side_effect = RuntimeError("delete unavailable")
        runner._background_graph_next_at.clear()
        runner.run_watch_turn()
        self.assertEqual("error", self.state["lastResult"]["status"])
        self.assertNotIn("lastFairnessCompletedAt", self.state)
        self.repository.release_projection_coordinator_lease.assert_called_once()

        def cleanup(options):
            self.assertEqual("inference-completed", events[-1])
            events.append("maintenance")
            self.assertEqual(1, options["keepInactiveManifests"])
            self.assertEqual(2, options["maxAboxDeleteBatches"])
            self.assertEqual(45, options["maxDurationSeconds"])
            return self.repository.run_deferred_maintenance.return_value

        self.repository.run_deferred_maintenance.side_effect = cleanup
        runner._background_graph_next_at.clear()
        runner.run_watch_turn()
        self.assertEqual("ok", self.state["lastResult"]["status"])
        self.assertEqual(2, self.state["lastResult"]["removedManifestCount"])
        self.assertTrue(self.state["lastFairnessCompletedAt"])
        self.assertEqual(6, self.queue["effectivePendingCount"])
        self.assertEqual(2, self.repository.release_projection_coordinator_lease.call_count)
        runner._background_graph_next_at.clear()
        runner.run_watch_turn()
        self.assertEqual("inference-completed", events[-1])
        self.assertEqual("fairness-cooldown",
                         self.state["lastResult"]["backgroundFairness"]["reasonCode"])
        self.assertEqual(2, self.repository.run_deferred_maintenance.call_count)

    def test_measured_cleanup_budget_requires_safe_recent_world_evidence(self):
        self.queue = {"effectivePendingCount": 0, "processingCount": 0}
        self.maintenance.settings.update({
            "ontologyAboxMaintenanceCriticalInactiveManifestCount": 24,
            "ontologyAboxMaintenanceAdaptiveDrainMaxDeleteBatchesPerRun": 4,
        })
        self.repository.run_deferred_maintenance.return_value = {
            "status": "partial", "durationMs": 3200,
            "abox": {
                "status": "partial", "completedInactiveManifestCount": 55,
                "remainingInactiveManifestCount": 55, "deletedBatchCount": 2,
                "removedRetiredScopeGenerationCount": 2, "deleteBatchSize": 150,
            },
        }
        # Exercise persistence and selection, not only the budget helper.
        for expected_budget in [2, 2, 2, 3, 4]:
            self.maintenance.run_once()
            options = self.repository.run_deferred_maintenance.call_args.args[0]
            self.assertEqual(expected_budget, options["maxAboxDeleteBatches"])
            self.assertEqual(45, options["maxDurationSeconds"])
            self.assertEqual(1, options["keepInactiveManifests"])
        world = self.state["backlogByWorld"]["portfolio:test"]
        samples = world["recentDeleteTimings"]
        self.assertEqual(3, len(samples))
        self.assertEqual("verified-world-turns", self.state["lastResult"]["capacityBudget"]["deleteBatchEstimateSource"])
        policy = self.maintenance.policy()
        adaptive = self.maintenance.adaptive_drain(policy, self.state, "portfolio:test")
        for requested, allowed in [(8, 8), (16, 9)]:
            enlarged = {**adaptive, "effectiveMaxDeleteBatches": requested}
            budget = self.maintenance.capacity_maintenance_budget(policy, enlarged, {}, samples)
            self.assertEqual(allowed, budget["maxAboxDeleteBatches"])
            self.assertEqual(45, budget["maxDurationSeconds"])
            self.assertEqual(2, self.maintenance.capacity_maintenance_budget(
                policy, enlarged, {}, [],
            )["maxAboxDeleteBatches"])
        for label, evidence in [
            ("cold", []), ("insufficient", samples[:2]),
            ("expired", [{**row, "observedAt": (self.now - timedelta(hours=1)).isoformat()} for row in samples]),
            ("future", [{**row, "observedAt": (self.now + timedelta(hours=1)).isoformat()} for row in samples]),
            ("changed-row-size", [{**row, "deleteBatchSize": 50} for row in samples]),
            ("invalid", [None, *samples[:2]]),
        ]:
            with self.subTest(label=label):
                budget = self.maintenance.capacity_maintenance_budget(policy, adaptive, {}, evidence)
                self.assertEqual(2, budget["maxAboxDeleteBatches"])
                self.assertEqual("configured", budget["deleteBatchEstimateSource"])
        slow = [{**row, "durationMs": 30000} for row in samples]
        self.assertEqual(1, self.maintenance.capacity_maintenance_budget(policy, adaptive, {}, slow)["maxAboxDeleteBatches"])
        self.assertEqual("configured", self.maintenance.capacity_maintenance_budget(
            policy, adaptive, {"capacityPriority": True}, samples)["deleteBatchEstimateSource"])
        # Another world cannot inherit this world's estimate.
        other = self.maintenance.adaptive_drain(policy, self.state, "other-world")
        self.assertEqual(2, self.maintenance.capacity_maintenance_budget(policy, other, {})["maxAboxDeleteBatches"])
        good = self.state["lastResult"]
        for change in [{"status": "error"}, {"status": "timeout"},
                       {"timeBudgetExhausted": True}, {"inventoryAvailable": False},
                       {"deletedBatchCount": 0}, {"removedRetiredScopeGenerationCount": 0}]:
            with self.subTest(change=change):
                self.assertEqual([], self.maintenance.updated_delete_timings(world, {**good, **change}))
        self.repository.run_deferred_maintenance.return_value = {"status": "error"}
        self.maintenance.run_once()
        self.assertEqual([], self.state["backlogByWorld"]["portfolio:test"]["recentDeleteTimings"])
