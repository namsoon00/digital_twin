import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from digital_twin import service_manager


class TypeDBServiceManagerTests(unittest.TestCase):
    def test_maintenance_lock_fences_seed_and_rotation_with_one_token(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "typedb-maintenance.lock"
            with patch.object(service_manager, "typedb_rotation_lock_path", return_value=path):
                first = service_manager.acquire_typedb_maintenance_lock("candidate-seed")
                second = service_manager.acquire_typedb_rotation_lock()

                self.assertTrue(first["acquired"])
                self.assertFalse(second["acquired"])
                self.assertEqual("candidate-seed", second["ownerOperation"])

                replacement = {
                    "pid": os.getpid(),
                    "operation": "new-owner",
                    "token": "replacement-token",
                }
                path.write_text(json.dumps(replacement), encoding="utf-8")
                service_manager.release_typedb_rotation_lock(first)

                self.assertTrue(path.exists())
                self.assertEqual("replacement-token", json.loads(path.read_text(encoding="utf-8"))["token"])

    def test_active_seed_defers_while_rotation_owns_maintenance_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB ontology graph store",
                "role": "typedb",
                "log": Path(temp) / "typedb.log",
                "seedOnStart": "1",
            }
            lock = {
                "acquired": False,
                "ownerOperation": "blue-green-rotation",
                "ownerPid": 123,
            }
            with patch.object(service_manager, "acquire_typedb_maintenance_lock", return_value=lock), \
                    patch.object(service_manager.subprocess, "run") as run:
                self.assertTrue(service_manager.ensure_typedb_seeded(spec))

            run.assert_not_called()
            self.assertIn("seed deferred", spec["log"].read_text(encoding="utf-8"))

    def test_candidate_seed_fails_closed_without_parent_maintenance_token(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB candidate",
                "role": "typedb-stage",
                "log": Path(temp) / "typedb.log",
                "seedOnStart": "1",
            }
            with patch.object(service_manager, "acquire_typedb_maintenance_lock", return_value={
                "acquired": False,
                "ownerOperation": "active-seed",
            }), patch.object(service_manager.subprocess, "run") as run:
                self.assertFalse(service_manager.ensure_typedb_seeded(spec))

            run.assert_not_called()

    def test_mysql_schema_bootstrap_uses_recovery_timeout_floor(self):
        spec = {
            "label": "MySQL operational store",
            "log": Path("/tmp/mysql-schema-test.log"),
            "operationalSettings": {"mysqlOperationTimeoutSeconds": "10"},
            "schemaBootstrapAttempts": "1",
        }
        with patch.object(service_manager, "MySQLOperationalConnection") as connection, \
                patch.object(service_manager, "MySQLMonitorAccountJobStore"):
            self.assertTrue(service_manager.ensure_mysql_operational_schema(spec))

        self.assertEqual("60", connection.call_args.args[0]["mysqlOperationTimeoutSeconds"])

    def test_web_is_started_before_optional_graph_dependencies(self):
        with patch.object(service_manager, "runtime_settings", return_value={
            "mysqlRuntimeManaged": "1",
            "ontologyTypeDbEnabled": "1",
            "timeSeriesQuestDbEnabled": "1",
            "notificationAiQueueWorkerCount": "0",
        }):
            names = list(service_manager.worker_specs())

        self.assertLess(names.index("mysql"), names.index("web"))
        self.assertLess(names.index("web"), names.index("typedb"))
        self.assertLess(names.index("web"), names.index("questdb"))

    def test_cold_start_keeps_collection_available_before_typedb_seed(self):
        specs = {
            "mysql": {"role": "mysql"},
            "web": {"role": "web"},
            "typedb": {"role": "typedb"},
            "market-data": {},
            "news": {},
            "reasoning-engine-delivery": {},
            "reasoning-engine-shadow": {},
            "ontology-world-projection": {},
        }

        names = [name for name, _spec in service_manager.ordered_worker_specs(specs)]

        self.assertEqual("mysql", names[0])
        self.assertLess(names.index("market-data"), names.index("typedb"))
        self.assertLess(names.index("news"), names.index("typedb"))
        self.assertLess(names.index("typedb"), names.index("reasoning-engine-shadow"))
        self.assertLess(names.index("typedb"), names.index("reasoning-engine-delivery"))
        self.assertLess(names.index("typedb"), names.index("ontology-world-projection"))

    def test_managed_executable_finds_explicit_binary_outside_process_path(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "node"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o700)
            with patch.object(service_manager.shutil, "which", return_value=None):
                resolved = service_manager.managed_executable("node", executable)

            self.assertEqual(str(executable), resolved)

    def test_typedb_worker_rotates_only_active_database_by_default(self):
        spec = service_manager.typedb_worker_spec({
            "typedbPassword": "test-strong-password",
            "typedbDatabase": "ontology_primary",
            "reasoningEngineV1TypeDbDatabase": "ontology_primary",
            "reasoningEngineV2TypeDbDatabase": "ontology_v2",
            "reasoningEngineShadowTypeDbDatabase": "ontology_v2",
        })

        self.assertEqual(
            ["ontology_primary"],
            spec["managedTypeDbDatabases"],
        )
        database_specs = service_manager.typedb_blue_green_database_specs(
            service_manager.typedb_blue_green_stage_spec(spec)
        )
        self.assertEqual(
            ["ontology_primary"],
            [item["typedbDatabase"] for item in database_specs],
        )

    def test_typedb_worker_can_explicitly_rotate_compatibility_databases(self):
        spec = service_manager.typedb_worker_spec({
            "typedbPassword": "test-strong-password",
            "typedbDatabase": "ontology_primary",
            "reasoningEngineV1TypeDbDatabase": "ontology_primary",
            "reasoningEngineV2TypeDbDatabase": "ontology_v2",
            "reasoningEngineShadowTypeDbDatabase": "ontology_v2",
            "typedbBlueGreenSeedCompatibilityDatabasesEnabled": "1",
        })

        self.assertEqual(
            ["ontology_primary", "ontology_v2"],
            spec["managedTypeDbDatabases"],
        )
        self.assertEqual(
            ["ontology_primary", "ontology_v2"],
            [
                item["typedbDatabase"]
                for item in service_manager.typedb_blue_green_database_specs(
                    service_manager.typedb_blue_green_stage_spec(spec)
                )
            ],
        )

    def test_typedb_rotation_resource_guard_blocks_saturated_host(self):
        result = service_manager.typedb_rotation_resource_preflight(
            {
                "blueGreenResourceGuardEnabled": "1",
                "blueGreenMaxLoadPerCpu": "1.25",
                "blueGreenMinimumAvailableMemoryPercent": "15",
            },
            loadavg_provider=lambda: (24.0, 10.0, 5.0),
            cpu_count_provider=lambda: 8,
            memory_percent_provider=lambda: 10.0,
        )

        self.assertFalse(result["ready"])
        self.assertEqual(["system-load", "available-memory"], result["blockers"])
        self.assertEqual(3.0, result["loadPerCpu"])

    def test_typedb_rotation_resource_guard_allows_healthy_host(self):
        result = service_manager.typedb_rotation_resource_preflight(
            {
                "blueGreenResourceGuardEnabled": "1",
                "blueGreenMaxLoadPerCpu": "1.25",
                "blueGreenMinimumAvailableMemoryPercent": "15",
            },
            loadavg_provider=lambda: (4.0, 3.0, 2.0),
            cpu_count_provider=lambda: 8,
            memory_percent_provider=lambda: 40.0,
        )

        self.assertTrue(result["ready"])
        self.assertEqual([], result["blockers"])

    def test_candidate_commands_run_with_lower_os_priority(self):
        command = service_manager.low_priority_command(
            {"processNice": "10"},
            ["typedb", "server"],
        )

        if os.name == "nt" or not service_manager.shutil.which("nice"):
            self.assertEqual(["typedb", "server"], command)
        else:
            self.assertEqual("-n", command[1])
            self.assertEqual("10", command[2])
            self.assertEqual(["typedb", "server"], command[3:])

    def test_runtime_workers_yield_cpu_to_interactive_processes(self):
        with patch.object(service_manager, "runtime_settings", return_value={
            "mysqlRuntimeManaged": "0",
            "ontologyTypeDbEnabled": "0",
            "timeSeriesQuestDbEnabled": "0",
            "reasoningEngineActiveVersion": "v2",
            "reasoningEngineV2IndependentEnabled": "1",
            "reasoningEngineV2DeploymentId": "v2-r2",
            "reasoningEngineCandidateDeploymentId": "v2-r2",
            "notificationAiQueueWorkerCount": "0",
            "managedBackgroundProcessNice": "7",
        }):
            workers = service_manager.worker_specs()

        self.assertEqual("7", workers["reasoning-engine-shadow"]["processNice"])
        self.assertEqual("7", workers["news"]["processNice"])
        self.assertNotIn("processNice", workers["web"])

    def test_blue_green_candidate_validates_each_reasoning_database(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB ontology graph store",
                "role": "typedb",
                "pid": Path(temp) / "typedb.pid",
                "log": Path(temp) / "typedb.log",
                "command": ["typedb", "server"],
                "needle": "typedb_server_bin",
                "dataPath": Path(temp) / "typedb-data",
                "healthAddress": "127.0.0.1:1729",
                "httpAddress": "127.0.0.1:8000",
                "typedbDatabase": "ontology_primary",
                "managedTypeDbDatabases": ["ontology_primary", "ontology_v2"],
            }
            seen = []

            def remember(database_spec, *args, **kwargs):
                seen.append(str(database_spec.get("typedbDatabase") or ""))
                return True

            with patch.object(service_manager, "stop_worker", return_value=0), \
                    patch.object(service_manager, "launch_typedb_stage_process", return_value=True), \
                    patch.object(service_manager, "ensure_typedb_seeded", side_effect=remember), \
                    patch.object(service_manager, "validate_typedb_candidate_inference_runtime", return_value={
                        "ready": True,
                        "mode": "direct-typeql-fallback",
                    }), \
                    patch.object(service_manager, "ensure_typedb_shared_world_projection_rebuilt", return_value=True), \
                    patch.object(service_manager, "ensure_typedb_portfolio_world_projection_rebuilt", return_value=True), \
                    patch.object(service_manager, "typedb_driver_ready", return_value=True):
                prepared = service_manager.prepare_typedb_blue_green_candidate(spec)

        self.assertEqual("prepared", prepared["status"])
        self.assertEqual(["ontology_primary", "ontology_v2"], seen)
        self.assertEqual(["ontology_primary", "ontology_v2"], prepared["validatedDatabases"])
        self.assertEqual({
            "ontology_primary": "direct-typeql-fallback",
            "ontology_v2": "direct-typeql-fallback",
        }, prepared["validatedInferenceModes"])

    def test_blue_green_candidate_requires_functions_or_direct_typeql_fallback(self):
        command_result = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "prewarm": {
                    "functionsReady": False,
                    "ruleCount": 118,
                },
            }),
            stderr="",
        )
        spec = {
            "schemaFunctionDirectQueryFallbackEnabled": "1",
            "typedbDatabase": "ontology_v2",
        }

        with patch.object(service_manager.subprocess, "run", return_value=command_result):
            ready = service_manager.validate_typedb_candidate_inference_runtime(spec)

        self.assertTrue(ready["ready"])
        self.assertEqual("direct-typeql-fallback", ready["mode"])
        self.assertEqual(118, ready["ruleCount"])

        spec["schemaFunctionDirectQueryFallbackEnabled"] = "0"
        with patch.object(service_manager.subprocess, "run", return_value=command_result):
            blocked = service_manager.validate_typedb_candidate_inference_runtime(spec)

        self.assertFalse(blocked["ready"])
        self.assertEqual("blocked", blocked["status"])

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

    def test_automatic_rotation_can_be_triggered_by_shared_disk_pressure(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data"
            data_path.mkdir()
            (data_path / "segment").write_bytes(b"x" * (9 * 1024 * 1024))
            marker = Path(temp) / "marker.json"
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=marker):
                decision = service_manager.typedb_auto_rotation_needed(
                    {
                        "dataPath": data_path,
                        "maxSizeMb": "100",
                        "autoRotationEnabled": "1",
                        "autoRotationPercent": "90",
                        "autoRotationFreeSpaceMb": "1500",
                        "blueGreenMinimumHeadroomMb": "1024",
                        "blueGreenEstimatedCandidateMaxMb": "100",
                    },
                    disk_usage_provider=lambda _path: SimpleNamespace(
                        free=1200 * 1024 * 1024,
                        total=2000 * 1024 * 1024,
                    ),
                )

        self.assertTrue(decision["needed"])
        self.assertEqual("shared-disk", decision["trigger"])
        self.assertTrue(decision["stagingReady"])
        self.assertEqual(1200.0, decision["freeSpaceMb"])

    def test_automatic_rotation_detects_wal_amplification_before_size_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data"
            data_path.mkdir()
            (data_path / "segment").write_bytes(b"x" * 1024)
            marker = Path(temp) / "marker.json"
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=marker):
                decision = service_manager.typedb_auto_rotation_needed(
                    {
                        "dataPath": data_path,
                        "maxSizeMb": "10000",
                        "autoRotationEnabled": "1",
                        "autoRotationPercent": "90",
                        "autoRotationWalMb": "2048",
                        "blueGreenMinimumHeadroomMb": "1024",
                    },
                    inventory_provider=lambda *_args, **_kwargs: {"typedbWalMb": 4096},
                )

        self.assertTrue(decision["needed"])
        self.assertEqual("typedb-wal", decision["trigger"])
        self.assertTrue(decision["walPressureReached"])
        self.assertEqual(4096.0, decision["typedbWalMb"])

    def test_automatic_rotation_reports_insufficient_blue_green_headroom(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data"
            data_path.mkdir()
            (data_path / "segment").write_bytes(b"x" * (100 * 1024 * 1024))
            marker = Path(temp) / "marker.json"
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=marker):
                decision = service_manager.typedb_auto_rotation_needed(
                    {
                        "dataPath": data_path,
                        "maxSizeMb": "1000",
                        "autoRotationEnabled": "1",
                        "autoRotationPercent": "90",
                        "autoRotationFreeSpaceMb": "1500",
                        "blueGreenMinimumHeadroomMb": "1024",
                        "blueGreenEstimatedCandidateMaxMb": "200",
                    },
                    disk_usage_provider=lambda _path: SimpleNamespace(
                        free=1050 * 1024 * 1024,
                        total=2000 * 1024 * 1024,
                    ),
                )

        self.assertTrue(decision["needed"])
        self.assertFalse(decision["stagingReady"])
        self.assertIn("insufficient blue-green staging headroom", decision["reason"])

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
                patch.object(service_manager, "typedb_maintenance_lock_owned", return_value=True), \
                patch.object(service_manager, "release_typedb_rotation_lock"), \
                patch.object(service_manager, "supervisor_running", return_value=False), \
                patch.object(service_manager, "stop") as stop, \
                patch.object(service_manager, "run_typedb_data_retention", return_value={"status": "reset"}) as reset, \
                patch.object(service_manager, "record_typedb_auto_rotation_state") as record_state, \
                patch.object(service_manager, "start", return_value=0) as start:
            status = service_manager.typedb_rotate()

        self.assertEqual(0, status)
        stop.assert_called_once_with(include_supervisor=False)
        reset.assert_called_once_with(spec, force=True)
        start.assert_called_once_with()
        self.assertEqual("ok", record_state.call_args_list[-1].kwargs["lastAutoRotationStatus"])

    def test_typedb_rotate_recovers_workers_and_alerts_when_reset_fails(self):
        spec = {"role": "typedb", "dataPath": Path("/tmp/orbit-alpha-typedb-test")}
        with patch.object(service_manager, "worker_specs", return_value={"typedb": spec}), \
                patch.object(service_manager, "typedb_reset_needed", return_value={"needed": True, "reason": "size"}), \
                patch.object(service_manager, "acquire_typedb_rotation_lock", return_value={"acquired": True}), \
                patch.object(service_manager, "typedb_maintenance_lock_owned", return_value=True), \
                patch.object(service_manager, "release_typedb_rotation_lock"), \
                patch.object(service_manager, "supervisor_running", return_value=False), \
                patch.object(service_manager, "stop") as stop, \
                patch.object(service_manager, "run_typedb_data_retention", return_value={"status": "reset-failed"}), \
                patch.object(service_manager, "start", return_value=0) as start, \
                patch.object(service_manager, "record_typedb_auto_rotation_state") as record_state, \
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
        self.assertEqual("reset-failed", record_state.call_args_list[-1].kwargs["lastAutoRotationStatus"])

    def test_cli_start_restores_configured_supervisor_instead_of_leaving_unmanaged_workers(self):
        with patch.object(service_manager, "configured_supervisor_available", return_value=True), \
                patch.object(service_manager, "restore_configured_supervisor", return_value=0) as restore, \
                patch.object(service_manager, "start") as direct_start:
            status = service_manager.main(["start"])

        self.assertEqual(0, status)
        restore.assert_called_once_with()
        direct_start.assert_not_called()

    def test_cli_restart_reloads_configured_supervisor_after_worker_restart(self):
        with patch.object(service_manager, "restart", return_value=0) as restart, \
                patch.object(service_manager, "reload_configured_supervisor", return_value=0) as reload_supervisor:
            status = service_manager.main(["restart"])

        self.assertEqual(0, status)
        restart.assert_called_once_with(restart_typedb=False, restart_mysql=False, restart_share=False)
        reload_supervisor.assert_called_once_with()

    def test_cli_can_explicitly_restart_share_tunnel(self):
        with patch.object(service_manager, "restart", return_value=0) as restart, \
                patch.object(service_manager, "reload_configured_supervisor", return_value=0):
            status = service_manager.main(["restart", "--restart-share"])

        self.assertEqual(0, status)
        restart.assert_called_once_with(restart_typedb=False, restart_mysql=False, restart_share=True)

    def test_reload_configured_supervisor_replaces_running_process(self):
        with patch.object(service_manager, "configured_supervisor_available", return_value=True), \
                patch.object(service_manager, "supervisor_running", return_value=True), \
                patch.object(service_manager, "read_pid", return_value=123), \
                patch.object(service_manager.os, "kill") as kill, \
                patch.object(service_manager, "wait_for_supervisor_replacement", return_value=True) as wait, \
                patch.object(service_manager, "install_supervisor", return_value=0) as install:
            status = service_manager.reload_configured_supervisor()

        self.assertEqual(0, status)
        kill.assert_called_once_with(123, service_manager.signal.SIGHUP)
        wait.assert_called_once_with(123)
        install.assert_not_called()

    def test_reload_configured_supervisor_reinstalls_when_handoff_times_out(self):
        with patch.object(service_manager, "configured_supervisor_available", return_value=True), \
                patch.object(service_manager, "supervisor_running", return_value=True), \
                patch.object(service_manager, "read_pid", return_value=123), \
                patch.object(service_manager.os, "kill"), \
                patch.object(service_manager, "wait_for_supervisor_replacement", return_value=False), \
                patch.object(service_manager, "install_supervisor", return_value=0) as install:
            status = service_manager.reload_configured_supervisor()

        self.assertEqual(0, status)
        install.assert_called_once_with()

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

    def test_mysql_schema_bootstrap_retries_transient_startup_failure(self):
        spec = {
            "label": "MySQL operational store",
            "log": Path("/tmp/orbit-alpha-mysql-schema-bootstrap-retry.log"),
            "operationalSettings": {},
            "schemaBootstrapAttempts": "3",
            "schemaBootstrapRetrySeconds": "0",
        }
        with patch.object(
            service_manager,
            "MySQLOperationalConnection",
            side_effect=[TimeoutError("recovering"), object()],
        ) as connection, patch.object(
            service_manager,
            "MySQLMonitorAccountJobStore",
        ) as monitor_store, patch.object(service_manager, "append_log"):
            self.assertTrue(service_manager.ensure_mysql_operational_schema(spec))

        self.assertEqual(2, connection.call_count)
        monitor_store.assert_called_once()

    def test_running_mysql_rechecks_schema_after_a_prior_bootstrap_timeout(self):
        spec = {
            "label": "MySQL operational store",
            "role": "mysql",
            "pid": Path("/tmp/orbit-alpha-running-mysql.pid"),
            "log": Path("/tmp/orbit-alpha-running-mysql.log"),
            "command": ["mysqld"],
        }
        with patch.object(service_manager, "read_pid", return_value=123), patch.object(
            service_manager,
            "is_running",
            return_value=True,
        ), patch.object(
            service_manager,
            "ensure_mysql_operational_schema",
            return_value=True,
        ) as bootstrap, patch.object(service_manager, "status_worker", return_value=0):
            self.assertEqual(0, service_manager.start_worker(spec))

        bootstrap.assert_called_once_with(spec)

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
        self.assertEqual(
            [
                service_manager.sys.executable,
                "-u",
                "python_service/service.py",
                "ontology-world-projection",
                "rebuild-portfolios",
                "--limit",
                "20",
            ],
            service_manager.typedb_portfolio_world_projection_rebuild_command(candidate),
        )
        self.assertEqual("1200", candidate["seedTimeoutSeconds"])

    def test_candidate_seed_retry_stops_all_data_path_owners_before_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB candidate",
                "role": "typedb-stage",
                "pid": Path(temp) / "candidate.pid",
                "log": Path(temp) / "candidate.log",
                "dataPath": Path(temp) / "typedb-data-candidate",
            }
            with patch.object(service_manager, "stop_worker") as stop, \
                    patch.object(
                        service_manager,
                        "stop_typedb_stage_data_path_processes",
                        return_value=True,
                    ) as stop_owners, \
                    patch.object(
                        service_manager,
                        "clear_typedb_stage_incomplete_checkpoints",
                        return_value=[],
                    ) as clear_checkpoints, \
                    patch.object(
                        service_manager,
                        "launch_typedb_stage_process",
                        return_value=True,
                    ) as launch:
                restarted = service_manager.restart_typedb_stage_for_seed_retry(spec)

        self.assertTrue(restarted)
        stop.assert_called_once_with(spec)
        stop_owners.assert_called_once_with(spec)
        clear_checkpoints.assert_called_once_with(spec)
        launch.assert_called_once_with(spec, "seed retry candidate restart")

    def test_candidate_cleanup_stops_orphaned_data_path_owner_before_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data-candidate"
            data_path.mkdir()
            spec = {
                "pid": Path(temp) / "candidate.pid",
                "dataPath": data_path,
            }
            with patch.object(service_manager, "stop_worker") as stop, \
                    patch.object(
                        service_manager,
                        "stop_typedb_stage_data_path_processes",
                        return_value=True,
                    ) as stop_owners:
                service_manager.cleanup_typedb_candidate(spec, remove_data=True)

            self.assertFalse(data_path.exists())

        stop.assert_called_once_with(spec)
        stop_owners.assert_called_once_with(spec)

    def test_candidate_retry_removes_only_incomplete_checkpoint_workdirs(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data-candidate"
            checkpoint = data_path / "ontology" / "checkpoint"
            checkpoint.mkdir(parents=True)
            incomplete = checkpoint / "checkpoint-1.tmp"
            complete = checkpoint / "checkpoint-2"
            incomplete.mkdir()
            complete.mkdir()
            (incomplete / "MANIFEST").write_text("partial", encoding="utf-8")
            (complete / "MANIFEST").write_text("complete", encoding="utf-8")

            removed = service_manager.clear_typedb_stage_incomplete_checkpoints({
                "dataPath": data_path,
            })
            self.assertEqual([str(incomplete)], removed)
            self.assertFalse(incomplete.exists())
            self.assertTrue(complete.exists())

    def test_candidate_portfolio_rebuild_uses_isolated_typedb_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB candidate",
                "role": "typedb-stage",
                "log": Path(temp) / "candidate.log",
                "healthAddress": "127.0.0.1:1730",
                "portfolioWorldProjectionRebuildTimeoutSeconds": "120",
                "portfolioWorldProjectionRebuildLimit": "8",
            }
            with patch.object(service_manager.subprocess, "run", return_value=SimpleNamespace(
                returncode=0,
                stdout='{"status":"ok","projectedPortfolioWorldCount":2}',
                stderr="",
            )) as run:
                self.assertTrue(service_manager.ensure_typedb_portfolio_world_projection_rebuilt(spec))

        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertEqual("rebuild-portfolios", command[4])
        self.assertEqual("8", command[-1])
        self.assertEqual("127.0.0.1:1730", environment["TYPEDB_ADDRESS"])
        self.assertEqual("1", environment["TYPEDB_FRESH_CANDIDATE_REBUILD"])

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

    def test_blue_green_retired_path_uses_cutover_time_for_retention(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            active = root / "typedb-data"
            retired = root / "typedb-data-retired-1900"
            retired.mkdir()
            os.utime(retired, (100, 100))
            spec = {
                "dataPath": active,
                "blueGreenRetiredRetentionMinutes": "2",
            }

            with patch.object(service_manager.time, "time", return_value=2000):
                retained = service_manager.prune_retired_typedb_data_paths(spec)
            self.assertEqual([], retained)
            self.assertTrue(retired.exists())

            with patch.object(service_manager.time, "time", return_value=3000):
                removed = service_manager.prune_retired_typedb_data_paths(spec)
            self.assertEqual([str(retired)], removed)
            self.assertFalse(retired.exists())

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
