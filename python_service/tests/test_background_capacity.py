import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from digital_twin.infrastructure import background_capacity as api


class BackgroundCapacityTests(unittest.TestCase):
    def test_pressure_defers_before_claim_and_resumes_after_stable_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Mock(return_value=0)
            pressure = Mock(return_value="critical")
            guard = api.BackgroundCapacity(directory, pressure_reader=pressure, clock=clock, emit=Mock())
            with guard.acquire() as admitted:
                self.assertFalse(admitted)
            pressure.return_value = "normal"
            clock.return_value = 6
            with guard.acquire() as admitted:
                self.assertFalse(admitted)
            clock.return_value = 31
            with guard.acquire() as admitted:
                self.assertTrue(admitted)

    def test_workers_share_capacity_and_exception_releases_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            first = api.BackgroundCapacity(directory, pressure_reader=lambda: "normal", emit=Mock())
            second = api.BackgroundCapacity(directory, pressure_reader=lambda: "unknown", emit=Mock())
            with self.assertRaisesRegex(RuntimeError, "turn failed"):
                with first.acquire() as admitted:
                    self.assertTrue(admitted)
                    with second.acquire() as other:
                        self.assertFalse(other)
                    raise RuntimeError("turn failed")
            with second.acquire() as admitted:
                self.assertTrue(admitted)
                self.assertEqual("unknown", second.pressure)

    def test_manual_commands_and_realtime_are_not_throttled(self):
        callback = Mock(return_value=42)
        with patch.dict(os.environ, {}, clear=True), patch.object(api, "BackgroundCapacity") as factory:
            self.assertEqual(42, api.managed_background_call(callback))
            factory.assert_not_called()
            self.assertFalse(api.managed_background_should_retire())
        self.assertFalse(api.REALTIME_WORKERS.intersection(api.BACKGROUND_WORKERS))

    def test_managed_deferral_never_claims_jobs(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ORBIT_BACKGROUND_ADMISSION": "1"}):
            guard = api.BackgroundCapacity(directory, pressure_reader=lambda: "critical", emit=Mock())
            callback = Mock()
            with patch.object(api, "_managed_capacity", guard):
                self.assertEqual({"status": "deferred"}, api.managed_background_call(callback, {"status": "deferred"}))
            callback.assert_not_called()

    def test_linux_uses_available_memory_and_missing_read_remains_unknown(self):
        with patch.object(api.sys, "platform", "linux"), patch.object(Path, "read_text", return_value="MemTotal: 1000 kB\nMemAvailable: 40 kB\n"):
            self.assertEqual("critical", api.host_memory_pressure())
        with patch.object(api.sys, "platform", "linux"), patch.object(Path, "read_text", side_effect=OSError):
            self.assertEqual("unknown", api.host_memory_pressure())

    def test_manager_reserves_priority_and_admission_only_for_heavy_work(self):
        from digital_twin import service_manager as manager
        with patch.object(manager, "runtime_settings", return_value={"mysqlRuntimeManaged": "0", "reasoningEngineAutoRetireStaleCandidateEnabled": "0"}), \
             patch.object(manager, "web_worker_spec", return_value={}), \
             patch.object(manager, "active_reasoning_engine_version", return_value="v2"), \
             patch.object(manager, "candidate_reasoning_worker_enabled", return_value=False), \
             patch.object(manager, "configured_supervisor_available", return_value=True):
            specs = manager.worker_specs()
        self.assertEqual("0", specs["reasoning-engine-delivery"]["processNice"])
        self.assertEqual("0", specs["notifications"]["processNice"])
        self.assertNotIn("ORBIT_BACKGROUND_ADMISSION", specs["monitor"]["env"])
        self.assertEqual("1", specs["investment-research"]["env"]["ORBIT_BACKGROUND_ADMISSION"])
        self.assertEqual("512", specs["investment-research"]["env"]["ORBIT_WORKER_MAX_MEMORY_MB"])


class ReadyDeliveryPriorityTests(unittest.TestCase):
    def runner(self):
        from types import SimpleNamespace
        from digital_twin.modules.reasoning.application.independent_reasoning_engine import IndependentReasoningJobRunner
        class Queue:
            lane = "REALTIME"
            fail = False
            def next_lane(self, deployment_id):
                if self.fail:
                    raise TimeoutError("advisory probe unavailable")
                return self.lane
        engine = Mock()
        engine.descriptor.return_value = SimpleNamespace(deployment_id="active")
        return IndependentReasoningJobRunner(Queue(), engine, Mock(), deployment_role="delivery")

    def test_ready_delivery_gets_two_turns_then_background_cannot_starve(self):
        runner = self.runner()
        result = {"status": "completed", "processedCount": 1}
        self.assertEqual([True, True, False, True], [runner.defer_background_for_ready_delivery(result) for _ in range(4)])

    def test_missing_source_and_failed_probe_leave_background_repair_available(self):
        runner = self.runner()
        result = {"status": "completed", "processedCount": 1}
        runner.queue.lane = None
        self.assertFalse(runner.defer_background_for_ready_delivery(result))
        runner.queue.fail = True
        self.assertFalse(runner.defer_background_for_ready_delivery(result))
        self.assertEqual(0, runner._consecutive_background_deferrals)

    def test_failed_idle_and_candidate_turns_never_defer_background(self):
        runner = self.runner()
        for result in ({"status": "idle"}, {"status": "deferred"},
                       {"status": "completed", "processedCount": 1, "result": {"retryable": True}}):
            self.assertFalse(runner.defer_background_for_ready_delivery(result))
        runner.deployment_role = "candidate"
        self.assertFalse(runner.defer_background_for_ready_delivery({"status": "completed", "processedCount": 1}))
