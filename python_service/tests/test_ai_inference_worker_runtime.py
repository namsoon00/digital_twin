import unittest
from unittest.mock import patch

from digital_twin import service_manager
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

    def test_service_manager_keeps_ai_workers_off_until_runtime_settings_are_available(self):
        with patch.object(service_manager, "runtime_settings", return_value={}):
            specs = service_manager.worker_specs()

        self.assertFalse([name for name in specs if name.startswith("notification-ai")])

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

    def test_service_stop_reaps_configured_out_ai_workers(self):
        active = {"monitor": {"label": "monitor", "role": "monitor"}}
        disabled = {
            "notification-ai": {"label": "notification ai", "role": "notification-ai"},
        }
        with patch.object(service_manager, "worker_specs", return_value=active), \
             patch.object(service_manager, "disabled_notification_ai_worker_specs", return_value=disabled), \
             patch.object(service_manager, "stop_worker") as stop_worker:
            self.assertEqual(0, service_manager.stop(include_supervisor=False))

        self.assertEqual([disabled["notification-ai"], active["monitor"]], [
            call.args[0] for call in stop_worker.call_args_list
        ])

    def test_service_start_keeps_other_workers_running_when_web_port_is_owned(self):
        specs = {
            "monitor": {"label": "monitor", "role": "monitor"},
            "web": {"label": "web", "role": "web"},
            "notifications": {"label": "notifications", "role": "notifications"},
        }
        with patch.object(service_manager, "worker_specs", return_value=specs), \
             patch.object(service_manager, "start_worker", side_effect=[0, 1, 0]) as start_worker:
            self.assertEqual(0, service_manager.start())

        self.assertEqual(3, start_worker.call_count)

    def test_supervisor_recognizes_an_unmanaged_web_server_as_healthy(self):
        spec = {
            "label": "web",
            "role": "web",
            "healthAddress": "127.0.0.1:3000",
            "needle": "python_service/service.py web",
        }
        with patch.object(service_manager, "pid_exists", return_value=False), \
             patch.object(service_manager, "tcp_ready", return_value=True):
            self.assertTrue(service_manager.is_running(0, spec))

    def test_first_worker_recognizes_pre_queue_process_for_safe_upgrade_stop(self):
        with patch.object(service_manager, "runtime_settings", return_value={
            "notificationAiQueueWorkerCount": "2",
            "ontologyTypeDbEnabled": "0",
            "mysqlRuntimeManaged": "0",
        }):
            spec = service_manager.worker_specs()["notification-ai"]

        self.assertTrue(service_manager.is_worker_command(
            "python3 python_service/service.py notifications watch --lane ai --limit 1",
            spec,
        ))
        self.assertTrue(service_manager.is_worker_command(
            "python3 python_service/service.py ai-inference watch --worker-id ai-1 --limit 1",
            spec,
        ))

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
