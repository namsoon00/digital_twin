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
                patch.object(service_manager, "supervisor_running", return_value=False), \
                patch.object(service_manager, "stop") as stop, \
                patch.object(service_manager, "run_typedb_data_retention", return_value={"status": "reset"}) as reset, \
                patch.object(service_manager, "start", return_value=0) as start:
            status = service_manager.typedb_rotate()

        self.assertEqual(0, status)
        stop.assert_called_once_with(include_supervisor=False)
        reset.assert_called_once_with(spec, force=True)
        start.assert_called_once_with()

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

    def test_typedb_restart_clears_the_durable_rulebox_compiler_handoff(self):
        with patch.object(service_manager, "runtime_settings", return_value={
            "mysqlHost": "127.0.0.1",
            "mysqlDatabase": "orbit_alpha",
        }), patch.object(service_manager, "MySQLOntologyRuleboxPrewarmStateStore") as state_store:
            self.assertTrue(service_manager.clear_typedb_rulebox_prewarm_activity())

        settings = state_store.call_args.args[0]
        self.assertEqual("1", settings["_skipOperationalHistoryRetention"])
        self.assertEqual("1", settings["_skipOperationalSchemaBootstrap"])
        self.assertEqual({
            "status": "idle",
            "active": False,
            "expiresAtEpoch": 0,
            "reason": "typedb-server-restarted",
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


if __name__ == "__main__":
    unittest.main()
