import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from digital_twin import service_manager


class TypeDBServiceManagerTests(unittest.TestCase):
    def test_wait_for_typedb_ready_bootstraps_only_pending_fresh_store(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB ontology graph store",
                "role": "typedb",
                "pid": Path(temp) / "typedb.pid",
                "log": Path(temp) / "typedb.log",
                "healthAddress": "127.0.0.1:1729",
                "startupWaitSeconds": "2",
            }
            spec["pid"].write_text("123\n", encoding="utf-8")
            with patch.object(service_manager, "pid_exists", return_value=True), \
                    patch.object(service_manager, "tcp_ready", return_value=True), \
                    patch.object(service_manager, "typedb_driver_ready", side_effect=[False, True]), \
                    patch.object(service_manager, "typedb_credentials_bootstrap_pending", side_effect=[True, False]), \
                    patch.object(service_manager, "bootstrap_typedb_credentials_after_reset", return_value=True) as bootstrap, \
                    patch.object(service_manager, "clear_typedb_credentials_bootstrap_pending") as clear, \
                    patch.object(service_manager.time, "sleep", return_value=None):
                self.assertTrue(service_manager.wait_for_typedb_ready(spec))

        bootstrap.assert_called_once_with(spec)
        clear.assert_called_once_with()

    def test_reset_marker_requires_one_time_credential_bootstrap(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "role": "typedb",
                "dataPath": Path(temp) / "typedb-data",
                "retentionHours": "24",
                "maxSizeMb": "2048",
            }
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=Path(temp) / "marker.json"):
                result = service_manager.run_typedb_data_retention(spec, force=True)
                self.assertEqual("reset", result["status"])
                self.assertTrue(service_manager.typedb_credentials_bootstrap_pending())
                self.assertTrue(service_manager.typedb_shared_world_projection_rebuild_pending())
                service_manager.clear_typedb_credentials_bootstrap_pending()
                service_manager.clear_typedb_shared_world_projection_rebuild_pending()
                self.assertFalse(service_manager.typedb_credentials_bootstrap_pending())
                self.assertFalse(service_manager.typedb_shared_world_projection_rebuild_pending())

    def test_controlled_rotation_detects_capacity_while_auto_reset_is_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data"
            data_path.mkdir()
            (data_path / "segment").write_bytes(b"x" * (2 * 1024 * 1024))
            spec = {
                "role": "typedb",
                "dataPath": data_path,
                "autoResetEnabled": "0",
                "maxSizeMb": "0.001",
                "retentionHours": "24",
            }

            automatic = service_manager.typedb_reset_needed(spec)
            controlled = service_manager.typedb_reset_needed(spec, ignore_auto_reset=True)

        self.assertFalse(automatic["needed"])
        self.assertEqual("disabled", automatic["reason"])
        self.assertTrue(controlled["needed"])
        self.assertIn("size", controlled["reason"])

    def test_automatic_rotation_uses_a_pre_limit_threshold_and_cooldown(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data"
            data_path.mkdir()
            (data_path / "segment").write_bytes(b"x" * (9 * 1024 * 1024))
            spec = {
                "dataPath": data_path,
                "maxSizeMb": "10",
                "autoRotationEnabled": "1",
                "autoRotationPercent": "90",
                "autoRotationCooldownMinutes": "60",
            }
            marker = Path(temp) / "marker.json"
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=marker):
                due = service_manager.typedb_auto_rotation_needed(spec, now_epoch=1000)
                service_manager.write_typedb_retention_marker({"lastAutoRotationAttemptEpoch": 1000})
                cooling = service_manager.typedb_auto_rotation_needed(spec, now_epoch=1001)

        self.assertTrue(due["needed"])
        self.assertEqual(90.0, due["typedbUsagePercent"])
        self.assertFalse(cooling["needed"])
        self.assertEqual("cooldown", cooling["reason"])

    def test_automatic_rotation_uses_physical_size_not_checkpoint_hard_link_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data"
            storage = data_path / "db" / "storage"
            checkpoint = data_path / "db" / "checkpoint"
            storage.mkdir(parents=True)
            checkpoint.mkdir(parents=True)
            segment = storage / "segment"
            segment.write_bytes(b"x" * (6 * 1024 * 1024))
            os.link(segment, checkpoint / "segment")
            decision = service_manager.typedb_auto_rotation_needed({
                "dataPath": data_path,
                "maxSizeMb": "10",
                "autoRotationEnabled": "1",
                "autoRotationPercent": "90",
            })

        self.assertFalse(decision["needed"])
        self.assertLess(decision["typedbUsagePercent"], 90)

    def test_automatic_rotation_ignores_a_malformed_previous_attempt_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data"
            data_path.mkdir()
            (data_path / "segment").write_bytes(b"x" * (9 * 1024 * 1024))
            spec = {
                "dataPath": data_path,
                "maxSizeMb": "10",
                "autoRotationEnabled": "1",
                "autoRotationPercent": "90",
                "autoRotationCooldownMinutes": "60",
            }
            marker = Path(temp) / "marker.json"
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=marker):
                service_manager.write_typedb_retention_marker({"lastAutoRotationAttemptEpoch": "not-a-time"})
                due = service_manager.typedb_auto_rotation_needed(spec, now_epoch=1001)

        self.assertTrue(due["needed"])

    def test_hard_limit_bypasses_the_automatic_rotation_cooldown(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data"
            data_path.mkdir()
            (data_path / "segment").write_bytes(b"x" * (11 * 1024 * 1024))
            spec = {
                "dataPath": data_path,
                "maxSizeMb": "10",
                "autoRotationEnabled": "1",
                "autoRotationPercent": "90",
                "autoRotationCooldownMinutes": "60",
            }
            marker = Path(temp) / "marker.json"
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=marker):
                service_manager.write_typedb_retention_marker({"lastAutoRotationAttemptEpoch": 1000})
                due = service_manager.typedb_auto_rotation_needed(spec, now_epoch=1001)

        self.assertTrue(due["needed"])
        self.assertTrue(due["hardLimitReached"])

    def test_failed_automatic_rotation_uses_short_retry_window(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data"
            data_path.mkdir()
            (data_path / "segment").write_bytes(b"x" * (9 * 1024 * 1024))
            spec = {
                "dataPath": data_path,
                "maxSizeMb": "10",
                "autoRotationEnabled": "1",
                "autoRotationPercent": "80",
                "autoRotationCooldownMinutes": "60",
                "autoRotationFailureRetrySeconds": "120",
            }
            marker = Path(temp) / "marker.json"
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=marker):
                service_manager.write_typedb_retention_marker({
                    "lastAutoRotationAttemptEpoch": 1000,
                    "lastAutoRotationStatus": "reset-failed",
                })
                cooling = service_manager.typedb_auto_rotation_needed(spec, now_epoch=1100)
                due = service_manager.typedb_auto_rotation_needed(spec, now_epoch=1121)

        self.assertFalse(cooling["needed"])
        self.assertEqual(20, cooling["cooldownRemainingSeconds"])
        self.assertEqual(120, cooling["retryWindowSeconds"])
        self.assertTrue(due["needed"])

    def test_automatic_rotation_requires_a_healthy_managed_mysql_source(self):
        mysql_spec = {"pid": Path("/tmp/mysql.pid"), "healthAddress": "127.0.0.1:3306"}
        with patch.object(service_manager, "read_pid", return_value=123), \
                patch.object(service_manager, "is_running", return_value=True), \
                patch.object(service_manager, "tcp_ready", return_value=True):
            ready = service_manager.typedb_auto_rotation_recovery_preflight({"mysql": mysql_spec})
        unavailable = service_manager.typedb_auto_rotation_recovery_preflight({})

        self.assertTrue(ready["ready"])
        self.assertFalse(unavailable["ready"])

    def test_typedb_rotate_pauses_workers_and_restarts_after_reset(self):
        spec = {
            "role": "typedb",
            "dataPath": Path("/tmp/orbit-alpha-typedb-test"),
            "startupWaitSeconds": "60",
            "seedTimeoutSeconds": "30",
            "seedRetryCount": "0",
            "sharedWorldProjectionRebuildTimeoutSeconds": "30",
        }
        with patch.object(service_manager, "worker_specs", return_value={"typedb": spec}), \
                patch.object(service_manager, "typedb_reset_needed", return_value={"needed": True, "reason": "size"}), \
                patch.object(service_manager, "acquire_typedb_rotation_lock", return_value={"acquired": True}), \
                patch.object(service_manager, "release_typedb_rotation_lock"), \
                patch.object(service_manager, "supervisor_running", return_value=False), \
                patch.object(service_manager, "stop") as stop, \
                patch.object(service_manager, "run_typedb_data_retention", return_value={"status": "reset"}) as reset, \
                patch.object(service_manager, "start", return_value=0) as start:
            status = service_manager.typedb_rotate()

        self.assertEqual(0, status)
        stop.assert_called_once_with(include_supervisor=False)
        reset.assert_called_once_with(spec, force=True)
        start.assert_called_once_with()

    def test_typedb_rotate_recovers_workers_and_alerts_when_reset_fails(self):
        spec = {"role": "typedb", "dataPath": Path("/tmp/orbit-alpha-typedb-test")}
        with patch.object(service_manager, "worker_specs", return_value={"typedb": spec}), \
                patch.object(service_manager, "typedb_reset_needed", return_value={"needed": True, "reason": "size"}), \
                patch.object(service_manager, "acquire_typedb_rotation_lock", return_value={"acquired": True}), \
                patch.object(service_manager, "release_typedb_rotation_lock"), \
                patch.object(service_manager, "supervisor_running", return_value=False), \
                patch.object(service_manager, "stop") as stop, \
                patch.object(service_manager, "run_typedb_data_retention", return_value={"status": "reset-failed"}), \
                patch.object(service_manager, "start", return_value=0) as start, \
                patch.object(service_manager, "record_typedb_auto_rotation_incident", return_value={"recorded": True}) as incident:
            status = service_manager.typedb_rotate(force=True)

        self.assertEqual(1, status)
        stop.assert_called_once_with(include_supervisor=False)
        start.assert_called_once_with()
        incident.assert_called_once_with(
            spec,
            {"needed": True, "reason": "size"},
            alert_kind="typedb-auto-rotation-failed",
        )

    def test_cli_start_restores_configured_supervisor_instead_of_leaving_unmanaged_workers(self):
        with patch.object(service_manager, "configured_supervisor_available", return_value=True), \
                patch.object(service_manager, "restore_configured_supervisor", return_value=0) as restore, \
                patch.object(service_manager, "start") as direct_start:
            status = service_manager.main(["start"])

        self.assertEqual(0, status)
        restore.assert_called_once_with()
        direct_start.assert_not_called()

    def test_cli_restart_restores_configured_supervisor_after_worker_restart(self):
        with patch.object(service_manager, "restart", return_value=0) as restart, \
                patch.object(service_manager, "restore_configured_supervisor", return_value=0) as restore:
            status = service_manager.main(["restart"])

        self.assertEqual(0, status)
        restart.assert_called_once_with(restart_typedb=False, restart_mysql=False)
        restore.assert_called_once_with()

    def test_shared_world_rebuild_runs_once_for_a_fresh_typedb_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            marker_path = Path(temp) / "marker.json"
            spec = {
                "label": "TypeDB ontology graph store",
                "role": "typedb",
                "log": Path(temp) / "typedb.log",
                "sharedWorldProjectionRebuildTimeoutSeconds": "30",
                "sharedWorldProjectionRebuildLimit": "12",
            }
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=marker_path), \
                    patch.object(service_manager.subprocess, "run", return_value=SimpleNamespace(
                        returncode=0,
                        stdout='{"status":"ok","replayedCount":2}',
                        stderr="",
                    )) as run:
                service_manager.write_typedb_retention_marker({
                    "sharedWorldProjectionRebuildPending": True,
                    "sharedWorldProjectionRebuildReason": "fresh-data-directory",
                })

                self.assertTrue(service_manager.ensure_typedb_shared_world_projection_rebuilt(spec))

                command = run.call_args[0][0]
                self.assertEqual([
                    service_manager.sys.executable,
                    "-u",
                    "python_service/service.py",
                    "ontology-world-projection",
                    "rebuild",
                    "--limit",
                    "12",
                ], command)
                self.assertFalse(service_manager.typedb_shared_world_projection_rebuild_pending())

    def test_mysql_runtime_spec_carries_local_application_provisioning_input(self):
        spec = service_manager.mysql_worker_spec({
            "mysqlHost": "127.0.0.1",
            "mysqlPort": "3306",
            "mysqlDatabase": "orbit_alpha",
            "mysqlUser": "orbit_alpha_app",
            "mysqlPassword": "test-secret",
        })

        self.assertEqual("orbit_alpha", spec["mysqlDatabase"])
        self.assertEqual("orbit_alpha_app", spec["mysqlUser"])
        self.assertEqual("test-secret", spec["mysqlPassword"])
        self.assertEqual("'a\\\\b\\'c'", service_manager.mysql_sql_literal("a\\b'c"))

    def test_mysql_schema_bootstrap_is_explicit_before_fast_workers_start(self):
        spec = {
            "label": "MySQL operational store",
            "log": Path("/tmp/orbit-alpha-mysql-schema-bootstrap.log"),
            "operationalSettings": {
                "mysqlHost": "127.0.0.1",
                "mysqlDatabase": "orbit_alpha",
                "_skipOperationalSchemaBootstrap": "1",
            },
        }
        with patch.object(service_manager, "MySQLOperationalConnection") as connection, \
                patch.object(service_manager, "MySQLMonitorAccountJobStore") as monitor_store, \
                patch.object(service_manager, "append_log"):
            self.assertTrue(service_manager.ensure_mysql_operational_schema(spec))

        connection.assert_called_once()
        monitor_store.assert_called_once()
        bootstrap_settings = connection.call_args.args[0]
        self.assertEqual("1", bootstrap_settings["_skipOperationalHistoryRetention"])
        self.assertNotIn("_skipOperationalSchemaBootstrap", bootstrap_settings)

    def test_typedb_restart_maintenance_window_covers_full_bounded_startup(self):
        window = service_manager.typedb_restart_maintenance_window_seconds({
            "startupWaitSeconds": "600",
            "seedTimeoutSeconds": "360",
            "seedRetryCount": "2",
            "sharedWorldProjectionRebuildTimeoutSeconds": "900",
        })

        self.assertEqual(2640, window)

    def test_typedb_restart_marks_the_durable_rulebox_compiler_handoff_cold(self):
        with patch.object(service_manager, "runtime_settings", return_value={
            "mysqlHost": "127.0.0.1",
            "mysqlDatabase": "orbit_alpha",
        }), patch.object(service_manager, "MySQLOntologyRuleboxPrewarmStateStore") as state_store:
            self.assertTrue(service_manager.clear_typedb_rulebox_prewarm_activity())

        settings = state_store.call_args.args[0]
        self.assertEqual("1", settings["_skipOperationalHistoryRetention"])
        self.assertEqual("1", settings["_skipOperationalSchemaBootstrap"])
        self.assertEqual({
            "status": "bootstrap-required",
            "active": False,
            "expiresAtEpoch": 0,
            "reason": "typedb-server-restarted-require-rulebox-receipt",
            "lastResult": {
                "status": "bootstrap-required",
                "functionsReady": False,
                "reason": "TypeDB server restarted; RuleBox receipts must be verified before native investment inference.",
            },
        }, {
            key: value
            for key, value in state_store.return_value.replace.call_args.args[0].items()
            if key != "updatedAt"
        })

    def test_supervisor_honors_explicit_maintenance_deadline_while_owner_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "maintenance.json"
            with patch.object(service_manager, "supervisor_maintenance_path", return_value=marker), \
                    patch.object(service_manager, "pid_exists", return_value=True), \
                    patch.object(service_manager.time, "time", side_effect=[1000.0, 1301.0]):
                service_manager.begin_supervisor_maintenance("restart", max_age_seconds=900)
                self.assertTrue(service_manager.supervisor_maintenance_active())

    def test_supervisor_removes_explicit_maintenance_marker_when_owner_is_gone(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "maintenance.json"
            with patch.object(service_manager, "supervisor_maintenance_path", return_value=marker), \
                    patch.object(service_manager, "supervisor_log_path", return_value=Path(temp) / "supervisor.log"), \
                    patch.object(service_manager, "pid_exists", return_value=False), \
                    patch.object(service_manager.time, "time", return_value=1000.0):
                service_manager.write_supervisor_maintenance_payload({
                    "pid": 12345,
                    "expiresAtEpoch": 1600.0,
                })
                self.assertFalse(service_manager.supervisor_maintenance_active())

            self.assertFalse(marker.exists())

    def test_blue_green_candidate_uses_an_isolated_port_and_data_path(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "command": ["/tmp/typedb", "server"],
                "healthAddress": "127.0.0.1:1729",
                "httpAddress": "127.0.0.1:8000",
                "dataPath": Path(temp) / "typedb-data",
                "blueGreenStagePortOffset": "3",
            }

            candidate = service_manager.typedb_blue_green_stage_spec(spec)

        self.assertEqual("127.0.0.1:1732", candidate["healthAddress"])
        self.assertEqual("127.0.0.1:8003", candidate["httpAddress"])
        self.assertTrue(str(candidate["dataPath"]).endswith("typedb-data-candidate"))
        self.assertIn("--storage.data-directory", candidate["command"])

        self.assertNotIn("--recover-scoped-write-lease", service_manager.typedb_seed_command(candidate))
        self.assertIn(
            "--read-only-source",
            service_manager.typedb_shared_world_projection_rebuild_command(candidate),
        )

    def test_blue_green_swap_keeps_a_retired_rollback_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            active = root / "typedb-data"
            candidate = root / "typedb-data-candidate"
            active.mkdir()
            candidate.mkdir()
            (active / "old").write_text("old", encoding="utf-8")
            (candidate / "new").write_text("new", encoding="utf-8")
            marker = root / "marker.json"
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=marker):
                result = service_manager.swap_typedb_blue_green_data_paths(
                    {"dataPath": active},
                    {"dataPath": candidate},
                )
                marker_payload = service_manager.read_typedb_retention_marker()

            retired = Path(result["retiredPath"])
            self.assertEqual("swapped", result["status"])
            self.assertTrue((active / "new").exists())
            self.assertTrue((retired / "old").exists())
            self.assertTrue(marker_payload["blueGreenCutoverPending"])

    def test_blue_green_rollback_restores_the_retired_store(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            active = root / "typedb-data"
            retired = root / "typedb-data-retired-1"
            active.mkdir()
            retired.mkdir()
            (active / "candidate").write_text("candidate", encoding="utf-8")
            (retired / "previous").write_text("previous", encoding="utf-8")
            marker = root / "marker.json"
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=marker):
                result = service_manager.rollback_typedb_blue_green_data_paths(
                    {"dataPath": active},
                    retired,
                )
                marker_payload = service_manager.read_typedb_retention_marker()

            failed = Path(result["failedPath"])
            self.assertEqual("rolled-back", result["status"])
            self.assertTrue((active / "previous").exists())
            self.assertTrue((failed / "candidate").exists())
            self.assertFalse(marker_payload["blueGreenCutoverPending"])


if __name__ == "__main__":
    unittest.main()
