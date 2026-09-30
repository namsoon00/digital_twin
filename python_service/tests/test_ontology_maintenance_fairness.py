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
