import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from digital_twin import service_manager
from digital_twin.application.ontology_reasoning_service import OntologyReasoningRunner
from digital_twin.infrastructure.schedulers import AIInferenceQueueScheduler


class AIInferenceWorkerRuntimeTests(unittest.TestCase):
    def test_service_manager_builds_configured_parallel_ai_workers(self):
        with patch.object(service_manager, "runtime_settings", return_value={
            "notificationAiQueueWorkerCount": "3",
            "ontologyTypeDbEnabled": "0",
            "mysqlRuntimeManaged": "0",
        }):
            specs = service_manager.worker_specs()

        names = [name for name in specs if name.startswith("notification-ai")]
        self.assertEqual(["notification-ai", "notification-ai-2", "notification-ai-3"], names)
        self.assertIn("ai-inference watch --worker-id ai-1 --limit 1", " ".join(specs["notification-ai"]["command"]))

    def test_service_manager_allows_ai_workers_to_be_paused(self):
        with patch.object(service_manager, "runtime_settings", return_value={
            "notificationAiQueueWorkerCount": "0",
            "ontologyTypeDbEnabled": "0",
            "mysqlRuntimeManaged": "0",
        }):
            specs = service_manager.worker_specs()

        self.assertFalse([name for name in specs if name.startswith("notification-ai")])

    def test_service_manager_manages_cloudflare_evidence_share_when_enabled(self):
        with patch.object(service_manager, "runtime_settings", return_value={
            "notificationAiQueueWorkerCount": "0",
            "ontologyTypeDbEnabled": "0",
            "mysqlRuntimeManaged": "0",
            "cloudflareShareManagedEnabled": "1",
            "webPort": "3100",
            "cloudflareSharePort": "3101",
            "cloudflareShareRotationMinutes": "240",
            "cloudflareShareHealthCheckSeconds": "45",
            "cloudflareShareRotationGraceSeconds": "90",
        }), patch.object(
            service_manager,
            "share_credentials_environment",
            return_value={"SHARE_VIEW_TOKEN": "private-view-token"},
        ), patch.object(
            service_manager,
            "managed_executable",
            side_effect=lambda command, _explicit="": "/usr/local/bin/" + command,
        ):
            specs = service_manager.worker_specs()

        share = specs["cloudflare-share"]
        self.assertEqual("cloudflare-share", share["role"])
        self.assertEqual("3100", share["env"]["PORT"])
        self.assertEqual("cloudflared", share["env"]["TUNNEL_PROVIDER"])
        self.assertEqual("240", share["env"]["SHARE_TUNNEL_ROTATION_MINUTES"])
        self.assertEqual("45", share["env"]["SHARE_TUNNEL_HEALTH_CHECK_SECONDS"])
        self.assertEqual("90", share["env"]["SHARE_TUNNEL_ROTATION_GRACE_SECONDS"])
        self.assertEqual("private-view-token", specs["web"]["env"]["SHARE_VIEW_TOKEN"])
        self.assertIn("scripts/share-local.js", " ".join(share["command"]))

    def test_normal_restart_preserves_active_cloudflare_tunnel(self):
        share_spec = {
            "role": "cloudflare-share",
            "pid": Path("/tmp/cloudflare-share.pid"),
            "label": "share",
        }
        with patch.object(service_manager, "worker_specs", return_value={"cloudflare-share": share_spec}), patch.object(
            service_manager, "read_pid", return_value=123
        ), patch.object(service_manager, "is_running", return_value=True), patch.object(
            service_manager, "supervisor_running", return_value=False
        ), patch.object(service_manager, "stop", return_value=0) as stop, patch.object(
            service_manager, "start", return_value=0
        ) as start:
            result = service_manager.restart()

        self.assertEqual(0, result)
        self.assertIn("cloudflare-share", stop.call_args.kwargs["excluded_roles"])
        self.assertIn("cloudflare-share", start.call_args.kwargs["excluded_roles"])

    def test_explicit_share_restart_restarts_cloudflare_tunnel(self):
        share_spec = {
            "role": "cloudflare-share",
            "pid": Path("/tmp/cloudflare-share.pid"),
            "label": "share",
        }
        with patch.object(service_manager, "worker_specs", return_value={"cloudflare-share": share_spec}), patch.object(
            service_manager, "supervisor_running", return_value=False
        ), patch.object(service_manager, "stop", return_value=0) as stop, patch.object(
            service_manager, "start", return_value=0
        ) as start:
            result = service_manager.restart(restart_share=True)

        self.assertEqual(0, result)
        self.assertNotIn("cloudflare-share", stop.call_args.kwargs["excluded_roles"])
        self.assertNotIn("cloudflare-share", start.call_args.kwargs["excluded_roles"])

    def test_service_manager_switches_off_v1_worker_when_v2_is_active(self):
        with patch.object(service_manager, "runtime_settings", return_value={
            "notificationAiQueueWorkerCount": "0",
            "ontologyTypeDbEnabled": "0",
            "mysqlRuntimeManaged": "0",
            "reasoningEngineActiveVersion": "v2",
            "reasoningEngineV2IndependentEnabled": "1",
            "reasoningEngineV2DeploymentId": "v2-r2",
            "reasoningEngineCandidateDeploymentId": "v2-r2",
        }):
            specs = service_manager.worker_specs()

        self.assertNotIn("ontology-reasoning", specs)
        self.assertIn("reasoning-engine-delivery", specs)
        self.assertIn("reasoning-engine-shadow", specs)
        self.assertNotIn("--worker-id", specs["reasoning-engine-delivery"]["command"])
        self.assertNotIn("--worker-id", specs["reasoning-engine-shadow"]["command"])

    def test_service_manager_finds_configured_out_ai_workers_with_pid_files(self):
        active_specs = service_manager.notification_ai_worker_specs(1)

        with patch.object(service_manager, "read_pid", return_value=123):
            disabled = service_manager.disabled_notification_ai_worker_specs(active_specs)

        self.assertNotIn("notification-ai", disabled)
        self.assertEqual(
            ["notification-ai-2", "notification-ai-3", "notification-ai-4",
             "notification-ai-5", "notification-ai-6", "notification-ai-7",
             "notification-ai-8"],
            list(disabled),
        )

    def test_v1_reasoning_runner_fails_closed_after_engine_switch(self):
        monitor_runner_factory = Mock()
        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=None,
            monitor_runner_factory=monitor_runner_factory,
            settings={
                "_reasoningEngineDeploymentId": "ontology-v1-active",
                "_reasoningEngineVersion": "v1",
            },
            execution_authorized_provider=lambda: False,
        )

        result = runner.run_once()

        self.assertEqual("inactive-engine", result["status"])
        self.assertEqual(0, result["processedCount"])
        monitor_runner_factory.assert_not_called()

    def test_service_stop_removes_stale_web_pid_without_signaling_external_listener(self):
        with tempfile.TemporaryDirectory() as temp:
            pid_path = Path(temp) / "web.pid"
            pid_path.write_text("123\n", encoding="utf-8")
            spec = {
                "label": "web",
                "role": "web",
                "pid": pid_path,
                "log": Path(temp) / "web.log",
                "healthAddress": "127.0.0.1:3000",
            }
            with patch.object(service_manager, "pid_exists", return_value=False), \
                 patch.object(service_manager.os, "kill") as kill:
                self.assertEqual(0, service_manager.stop_worker(spec))

            kill.assert_not_called()
            self.assertFalse(pid_path.exists())

    def test_scheduler_stops_active_reviewer_through_runner(self):
        class Runner:
            stopped = False

            def stop(self):
                self.stopped = True

        runner = Runner()
        scheduler = AIInferenceQueueScheduler(runner, 5)
        scheduler.stop()

        self.assertFalse(scheduler.running)
        self.assertTrue(runner.stopped)


if __name__ == "__main__":
    unittest.main()
