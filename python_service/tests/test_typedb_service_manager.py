import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from digital_twin import service_manager
from digital_twin.platform.domain.typedb_capacity_policy import evaluate_typedb_capacity_policy


class TypeDBServiceManagerTests(unittest.TestCase):
    def _assert_candidate_cleanup_rejects_active_typedb_specification(self):
        with tempfile.TemporaryDirectory() as temp:
            active_path = Path(temp) / "typedb-data"
            active_path.mkdir()
            active = {
                "role": "typedb",
                "dataPath": active_path,
                "pid": Path(temp) / "typedb.pid",
            }
            with patch.object(service_manager, "stop_worker") as stop_worker:
                with self.assertRaisesRegex(ValueError, "non-staged TypeDB"):
                    service_manager.cleanup_typedb_candidate(active)

            stop_worker.assert_not_called()
            self.assertTrue(active_path.exists())

    def _assert_candidate_pid_never_matches_active_typedb_process(self):
        candidate = {
            "label": "TypeDB candidate",
            "role": "typedb-stage",
            "needle": "typedb_server_bin",
            "dataPath": "/workspace/data/typedb-data-candidate",
            "healthAddress": "127.0.0.1:1730",
        }
        active_command = (
            "/opt/typedb/typedb_server_bin "
            "--server.listen-address 127.0.0.1:1729 "
            "--storage.data-directory /workspace/data/typedb-data"
        )

        self.assertFalse(
            service_manager.is_worker_command(active_command, candidate)
        )

    def assert_typedb_runtime_health_requires_consecutive_service_failures(self):
        first = service_manager.typedb_runtime_health_decision(
            process_running=True,
            startup_finalized=True,
            probe_due=True,
            service_ready=False,
            consecutive_failures=0,
            failure_threshold=2,
        )
        second = service_manager.typedb_runtime_health_decision(
            process_running=True,
            startup_finalized=True,
            probe_due=True,
            service_ready=False,
            consecutive_failures=first["consecutiveFailures"],
            failure_threshold=2,
        )
        recovered = service_manager.typedb_runtime_health_decision(
            process_running=True,
            startup_finalized=True,
            probe_due=True,
            service_ready=True,
            consecutive_failures=first["consecutiveFailures"],
            failure_threshold=2,
        )

        self.assertEqual("retry", first["action"])
        self.assertEqual("restart", second["action"])
        self.assertEqual({"action": "continue", "consecutiveFailures": 0}, recovered)

    def test_capacity_policy_rotates_before_legacy_eighty_percent_default(self):
        self.assert_typedb_runtime_health_requires_consecutive_service_failures()
        with patch.dict(
            os.environ,
            {
                "TYPEDB_RUNTIME_HEALTH_PROBE_INTERVAL_SECONDS": "",
                "TYPEDB_RUNTIME_HEALTH_PROBE_TIMEOUT_MILLIS": "",
                "TYPEDB_STARTUP_HEALTH_PROBE_TIMEOUT_MILLIS": "",
                "TYPEDB_RUNTIME_HEALTH_FAILURE_THRESHOLD": "",
            },
            clear=False,
        ):
            typedb_spec = service_manager.typedb_worker_spec({})
        self.assertEqual("30", typedb_spec["runtimeHealthProbeIntervalSeconds"])
        self.assertEqual("10000", typedb_spec["runtimeHealthProbeTimeoutMillis"])
        self.assertEqual("30000", typedb_spec["startupHealthProbeTimeoutMillis"])
        self.assertEqual("10", typedb_spec["runtimeHealthFailureThreshold"])
        self.assertEqual("0", typedb_spec["processNice"])
        self.assertEqual("0", typedb_spec["blueGreenSchemaBuildProcessNice"])
        self._assert_typedb_health_probe_timeouts_allow_loaded_store_recovery()
        result = evaluate_typedb_capacity_policy({
            "typedbSizeMb": 75,
            "typedbLimitMb": 100,
        })

        self.assertEqual(75, result["rotationPercent"])
        self.assertTrue(result["rotationRequired"])
        self._assert_candidate_cleanup_rejects_active_typedb_specification()
        self._assert_candidate_pid_never_matches_active_typedb_process()
        self._assert_fresh_blue_green_candidate_restarts_after_schema_seed()

    def test_failed_typedb_candidate_is_retired_and_delivery_settings_are_restored(self):
        class FakeRegistry:
            def __init__(self):
                self.control_state = SimpleNamespace(
                    active_deployment_id="v2-active",
                    delivery_deployment_id="v2-active",
                    candidate_deployment_id="v2-failed",
                    version=7,
                )
                self.set_control_args = None
                self.retire_args = None

            def control(self):
                return self.control_state

            def get(self, deployment_id):
                return {
                    "v2-active": {
                        "deploymentId": "v2-active",
                        "graphStoreBinding": "ontology-active",
                    },
                    "v2-failed": {
                        "deploymentId": "v2-failed",
                        "graphStoreBinding": "ontology-candidate",
                    },
                }.get(deployment_id, {})

            def set_control(self, active, delivery, candidate, expected_version=None):
                self.set_control_args = (active, delivery, candidate, expected_version)
                return SimpleNamespace(
                    active_deployment_id=active,
                    delivery_deployment_id=delivery,
                )

            def retire_unselected(self, engine_version, keep):
                self.retire_args = (engine_version, list(keep))
                return {
                    "retiredDeploymentIds": ["v2-failed"],
                    "supersededJobCount": 52,
                }

        registry = FakeRegistry()
        saved = []
        result = service_manager.retire_failed_typedb_reasoning_candidate(
            "ontology-candidate",
            settings_provider=lambda **kwargs: {},
            registry_factory=lambda settings: registry,
            settings_saver=lambda values: saved.append(values),
        )

        self.assertEqual("retired-failed-candidate", result["status"])
        self.assertEqual(("v2-active", "v2-active", "", 7), registry.set_control_args)
        self.assertEqual(("v2", ["v2-active", "v2-active"]), registry.retire_args)
        self.assertEqual("v2-active", saved[0]["reasoningEngineV2DeploymentId"])
        self.assertEqual("", saved[0]["reasoningEngineCandidateDeploymentId"])
        self.assertEqual("ontology-active", saved[0]["reasoningEngineV2TypeDbDatabase"])

    def test_stale_reasoning_candidate_is_retired_before_worker_selection(self):
        class FakeRegistry:
            def __init__(self):
                self.set_control_args = None
                self.retire_args = None

            @staticmethod
            def control():
                return SimpleNamespace(
                    active_deployment_id="ontology-v2-production-r88",
                    delivery_deployment_id="ontology-v2-production-r88",
                    candidate_deployment_id="ontology-v2-production-r75",
                    version=12,
                )

            def set_control(self, active, delivery, candidate, expected_version=None):
                self.set_control_args = (active, delivery, candidate, expected_version)
                return SimpleNamespace(
                    active_deployment_id=active,
                    delivery_deployment_id=delivery,
                )

            def retire_unselected(self, engine_version, keep):
                self.retire_args = (engine_version, list(keep))
                return {"retiredDeploymentIds": ["ontology-v2-production-r75"]}

            @staticmethod
            def get(deployment_id):
                return {
                    "deploymentId": deployment_id,
                    "graphStoreBinding": "ontology-production-r88",
                }

        registry = FakeRegistry()
        saved = []
        result = service_manager.retire_stale_reasoning_candidate(
            {},
            registry_factory=lambda _settings: registry,
            settings_saver=lambda values: saved.append(values),
        )

        self.assertEqual("retired-stale-candidate", result["status"])
        self.assertEqual(
            (
                "ontology-v2-production-r88",
                "ontology-v2-production-r88",
                "",
                12,
            ),
            registry.set_control_args,
        )
        self.assertEqual("ontology-v2-production-r88", saved[0]["reasoningEngineV2DeploymentId"])

        inventory = service_manager.typedb_rotation_database_inventory(
            {
                "typedbDatabase": "ontology-maintenance",
                "managedTypeDbDatabases": ["ontology-maintenance"],
            },
            {"typedbBlueGreenSeedCompatibilityDatabasesEnabled": "0"},
            registry_factory=lambda _settings: registry,
        )
        self.assertTrue(inventory["ready"])
        self.assertEqual(
            ["ontology-maintenance", "ontology-production-r88"],
            inventory["databases"],
        )
        self.assertEqual(
            ["ontology-production-r88"],
            inventory["protectedDatabases"],
        )

    def test_newer_reasoning_candidate_is_preserved(self):
        class FakeRegistry:
            @staticmethod
            def control():
                return SimpleNamespace(
                    active_deployment_id="ontology-v2-production-r88",
                    delivery_deployment_id="ontology-v2-production-r88",
                    candidate_deployment_id="ontology-v2-production-r89",
                    version=12,
                )

        result = service_manager.retire_stale_reasoning_candidate(
            {},
            registry_factory=lambda _settings: FakeRegistry(),
            settings_saver=lambda _values: None,
        )

        self.assertEqual("candidate-current", result["status"])
        self.assertEqual([], result["retiredDeploymentIds"])

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

    def test_supervisor_start_defers_graph_workers_while_typedb_recovers(self):
        specs = {
            "web": {"label": "web", "role": "web"},
            "typedb": {"label": "typedb", "role": "typedb"},
            "reasoning-engine-delivery": {"label": "delivery", "role": "delivery"},
        }
        with patch.object(service_manager, "worker_specs", return_value=specs), patch.object(
            service_manager,
            "start_worker",
            return_value=0,
        ) as start_worker:
            self.assertEqual(0, service_manager.start(supervisor_async=True))

        started = [call.args[0]["label"] for call in start_worker.call_args_list]
        self.assertEqual(["web", "typedb"], started)
        self.assertTrue(start_worker.call_args_list[0].kwargs["wait_for_ready"])
        self.assertFalse(start_worker.call_args_list[1].kwargs["wait_for_ready"])

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

    def test_blue_green_candidate_rejects_rulebox_that_differs_from_delivery_release(self):
        class FakeRegistry:
            def control(self):
                return SimpleNamespace(
                    delivery_deployment_id="v2-r49",
                    active_deployment_id="v2-r49",
                )

            def get(self, deployment_id):
                return {
                    "deploymentId": deployment_id,
                    "status": "active",
                    "graphStoreBinding": "ontology_v2",
                    "health": {
                        "candidateReleaseId": "release-r49@frozen",
                        "ruleboxFingerprint": "frozen-rulebox",
                        "tboxFingerprint": "frozen-tbox",
                    },
                }

        class FakeRepository:
            def rulebox_snapshot(self):
                return {
                    "status": "ok",
                    "rules": [{"id": "rule:new"}],
                    "sourceRulesHash": "new-rulebox",
                }

            def active_tbox_metadata(self):
                return {"status": "ok", "fingerprint": "new-tbox"}

        result = service_manager.validate_typedb_candidate_release_contract(
            {
                "typedbDatabase": "ontology_v2",
                "healthAddress": "127.0.0.1:1730",
                "httpAddress": "127.0.0.1:8001",
            },
            settings_provider=lambda **kwargs: {},
            repository_factory=lambda settings: FakeRepository(),
            registry_factory=lambda settings: FakeRegistry(),
        )

        self.assertFalse(result["ready"])
        self.assertEqual("release-fingerprint-mismatch", result["status"])
        self.assertEqual("new-rulebox", result["candidateRuleboxFingerprint"])

    def test_rotation_validates_experiment_against_saved_artifact_not_source_catalog(self):
        from unittest.mock import Mock
        bundle = {"tbox_release_id": "tbox@frozen-tbox"}
        registry = Mock()
        registry.control.return_value = SimpleNamespace(active_deployment_id="active", delivery_deployment_id="active",
                                                        candidate_deployment_id="evolution-test")
        registry.get.side_effect = lambda ident: ({"status": "shadow", "graphStoreBinding": "experiment",
            "releaseBundle": bundle, "health": {"ruleboxFingerprint": "runtime-rulebox", "tboxFingerprint": "frozen-tbox",
                "ontologyEvolution": {"state": "shadow", "planFingerprint": "plan"}}} if ident == "evolution-test"
            else {"status": "active", "graphStoreBinding": "production"})
        artifact = {"valid": True, "artifactFingerprint": "authored-artifact",
                    "artifact": {"releaseBundle": bundle, "evolutionPlanFingerprint": "plan"}}
        registry.release_artifact.return_value = artifact
        repository = SimpleNamespace(rulebox_snapshot=lambda: {"status": "ok", "rules": [{"rule_id": "experimental"}],
            "sourceRulesHash": "runtime-rulebox"}, active_tbox_metadata=lambda: {"fingerprint": "frozen-tbox"})
        spec = {"typedbDatabase": "experiment", "_typedbSeedContract": {"ready": True,
            "seedMode": "immutable-release-artifact", "artifactFingerprint": "authored-artifact",
            "activeRuleboxFingerprint": "runtime-rulebox", "activeTboxFingerprint": "frozen-tbox", "ruleCount": 1}}
        from digital_twin.modules.reasoning.public import ReasoningEnginePlatformService
        with patch.object(ReasoningEnginePlatformService, "descriptors", side_effect=AssertionError("current source is not the experiment")):
            result = service_manager.validate_typedb_candidate_release_contract(spec, settings_provider=lambda **kw: {},
                registry_factory=lambda _: registry, repository_factory=lambda _: repository)
        self.assertTrue(result["ready"], result)
        artifact["artifact"]["evolutionPlanFingerprint"] = "different-plan"
        self.assertFalse(service_manager.validate_typedb_candidate_release_contract(spec, settings_provider=lambda **kw: {},
            registry_factory=lambda _: registry, repository_factory=lambda _: repository)["ready"])
        artifact["artifact"]["evolutionPlanFingerprint"] = "plan"
        repository.rulebox_snapshot = lambda: {"status": "ok", "rules": [{}], "sourceRulesHash": "tampered"}
        self.assertEqual("registered-candidate-fingerprint-mismatch",
            service_manager.validate_typedb_candidate_release_contract(spec, settings_provider=lambda **kw: {},
                registry_factory=lambda _: registry, repository_factory=lambda _: repository)["status"])
        artifact["valid"] = False
        self.assertFalse(service_manager.validate_typedb_candidate_release_contract(spec, settings_provider=lambda **kw: {},
            registry_factory=lambda _: registry, repository_factory=lambda _: repository)["ready"])

    def test_physical_rotation_failure_does_not_retire_logical_experiment(self):
        from unittest.mock import Mock
        registry = Mock()
        registry.control.return_value = SimpleNamespace(active_deployment_id="active", delivery_deployment_id="active",
                                                        candidate_deployment_id="experiment")
        registry.get.return_value = {"graphStoreBinding": "experiment-db", "health": {"ontologyEvolution": {"state": "shadow"}}}
        saver = Mock()
        result = service_manager.retire_failed_typedb_reasoning_candidate("experiment-db",
            settings_provider=lambda **kw: {}, registry_factory=lambda _: registry, settings_saver=saver)
        self.assertEqual("experiment-preserved", result["status"])
        registry.set_control.assert_not_called()
        registry.retire_unselected.assert_not_called()
        saver.assert_not_called()

    def test_candidate_seed_contract_accepts_fresh_and_unchanged_verified_paths(self):
        from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import default_graph_inference_rules
        from digital_twin.modules.model_registry.domain.ontology_rulebox_governance import rulebox_rules_hash
        from digital_twin.modules.reasoning.domain.ontology_schema import default_tbox_metadata

        source_rulebox_fingerprint = rulebox_rules_hash([
            rule.to_dict()
            for rule in default_graph_inference_rules()
        ])
        runtime_rulebox_fingerprint = "runtime-rulebox-fingerprint"
        static_rulebox_fingerprint = "static-rulebox-fingerprint"
        manifest = {
            "expectedFingerprint": "static-fingerprint",
            "activeFingerprint": "static-fingerprint",
            "expectedTboxFingerprint": default_tbox_metadata()["fingerprint"],
            "activeTboxFingerprint": default_tbox_metadata()["fingerprint"],
            "expectedRuleboxFingerprint": static_rulebox_fingerprint,
            "activeRuleboxFingerprint": static_rulebox_fingerprint,
            "expectedSchemaContractFingerprint": "schema-fingerprint",
            "activeSchemaContractFingerprint": "schema-fingerprint",
        }
        preflight = {
            "ready": True,
            "tboxMatches": True,
            "ruleboxMatches": True,
            "languageRegistryMatches": True,
            "schemaContractMatches": True,
            "staticSeedManifest": manifest,
        }

        class FakeRepository:
            def rulebox_snapshot(self):
                return {
                    "status": "ok",
                    "rules": [{"ruleId": "rule:test"}],
                    "sourceRulesHash": runtime_rulebox_fingerprint,
                    "ruleCount": 1,
                }

        common = {
            "typedbDatabase": "ontology_v2",
            "healthAddress": "127.0.0.1:1730",
            "httpAddress": "127.0.0.1:8001",
        }
        fresh = service_manager.validate_typedb_candidate_seed_contract(
            {
                **common,
                "_typedbSeedAttestation": {
                    "status": "ok",
                    "saved": True,
                    "ruleBoxReplaced": True,
                    "activeRuleBoxHash": runtime_rulebox_fingerprint,
                    "activeRuleBoxRuleCount": 1,
                    "postSeedPreflight": preflight,
                },
            },
            settings_provider=lambda **kwargs: {},
            repository_factory=lambda configured: FakeRepository(),
        )
        unchanged = service_manager.validate_typedb_candidate_seed_contract(
            {
                **common,
                "_typedbSeedAttestation": {
                    "status": "unchanged",
                    "saved": True,
                    "ruleBoxAlreadyCurrent": True,
                    "ruleBoxHashMatched": True,
                    "activeRuleBoxRuleCount": 1,
                    "seedPreflight": preflight,
                },
            },
            settings_provider=lambda **kwargs: {},
            repository_factory=lambda configured: FakeRepository(),
        )

        self.assertTrue(fresh["ready"])
        self.assertTrue(unchanged["ready"])
        self.assertEqual(source_rulebox_fingerprint, fresh["sourceRuleboxFingerprint"])
        self.assertEqual(runtime_rulebox_fingerprint, fresh["activeRuleboxFingerprint"])
        self.assertEqual([], unchanged["failedChecks"])

    def test_blue_green_candidate_stops_before_world_rebuild_on_release_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB ontology graph store",
                "role": "typedb",
                "pid": Path(temp) / "typedb.pid",
                "log": Path(temp) / "typedb.log",
                "dataPath": Path(temp) / "typedb-data",
                "healthAddress": "127.0.0.1:1729",
                "httpAddress": "127.0.0.1:8000",
                "typedbDatabase": "ontology_v2",
                "protectedTypeDbDatabases": ["ontology_v2"],
                "_typedbDatabaseInventory": {
                    "selectedDeploymentIds": ["v2-r49"],
                    "deploymentBindings": {"v2-r49": "ontology_v2"},
                },
            }
            with patch.object(service_manager, "stop_worker", return_value=0), \
                    patch.object(service_manager, "stop_typedb_stage_data_path_processes", return_value=True), \
                    patch.object(service_manager, "launch_typedb_stage_process", return_value=True), \
                    patch.object(service_manager, "ensure_typedb_seeded", return_value=True) as source_seed, \
                    patch.object(service_manager, "restore_typedb_candidate_release_artifact", return_value={
                        "ready": True,
                        "status": "release-artifact-ready",
                        "activeRuleboxFingerprint": "frozen-rulebox",
                        "activeTboxFingerprint": "frozen-tbox",
                    }) as artifact_restore, \
                    patch.object(service_manager, "validate_typedb_candidate_release_contract", return_value={
                        "ready": False,
                        "status": "release-fingerprint-mismatch",
                    }) as release_check, \
                    patch.object(service_manager, "ensure_typedb_shared_world_projection_rebuilt") as rebuild:
                result = service_manager.prepare_typedb_blue_green_candidate(spec)

        self.assertEqual("candidate-release-contract-failed", result["status"])
        release_check.assert_called_once()
        artifact_restore.assert_called_once()
        source_seed.assert_not_called()
        rebuild.assert_not_called()

    def test_blue_green_candidate_reuses_release_verified_seed(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB ontology graph store",
                "role": "typedb",
                "pid": Path(temp) / "typedb.pid",
                "log": Path(temp) / "typedb.log",
                "dataPath": Path(temp) / "typedb-data",
                "healthAddress": "127.0.0.1:1729",
                "httpAddress": "127.0.0.1:8000",
                "typedbDatabase": "ontology_v2",
                "protectedTypeDbDatabases": ["ontology_v2"],
                "_typedbDatabaseInventory": {
                    "selectedDeploymentIds": ["v2-r49"],
                    "deploymentBindings": {"v2-r49": "ontology_v2"},
                },
            }
            candidate = service_manager.typedb_blue_green_stage_spec(spec)
            Path(candidate["dataPath"]).mkdir(parents=True)
            service_manager.write_typedb_candidate_reuse_marker(
                candidate,
                "ontology_v2",
                {
                    "status": "release-artifact-ready",
                    "ready": True,
                    "seedMode": "immutable-release-artifact",
                    "activeRuleboxFingerprint": "frozen-rulebox",
                    "activeTboxFingerprint": "frozen-tbox",
                },
            )
            with patch.object(service_manager, "stop_worker", return_value=0), \
                    patch.object(service_manager, "stop_typedb_stage_data_path_processes", return_value=True), \
                    patch.object(service_manager, "launch_typedb_stage_process", return_value=True), \
                    patch.object(service_manager, "ensure_typedb_seeded", return_value=True) as seed, \
                    patch.object(service_manager, "restore_typedb_candidate_release_artifact") as artifact_restore, \
                    patch.object(service_manager, "validate_typedb_candidate_release_contract", return_value={
                        "ready": True,
                        "status": "ready",
                    }) as release_check, \
                    patch.object(service_manager, "validate_typedb_candidate_inference_runtime", return_value={
                        "ready": True,
                        "mode": "native",
                    }), \
                    patch.object(service_manager, "ensure_typedb_shared_world_projection_rebuilt", return_value=True), \
                    patch.object(service_manager, "ensure_typedb_portfolio_world_projection_rebuilt", return_value=True), \
                    patch.object(service_manager, "typedb_driver_ready", return_value=True):
                result = service_manager.prepare_typedb_blue_green_candidate(spec)
                self.assertEqual(3, release_check.call_count)
                release_check.side_effect = [
                    {"ready": True, "status": "ready"},
                    {"ready": True, "status": "ready"},
                    {"ready": False, "status": "fingerprint-mismatch"},
                ]
                rejected = service_manager.prepare_typedb_blue_green_candidate(spec)

        self.assertEqual("prepared", result["status"])
        self.assertTrue(result["candidateSeedReused"])
        self.assertEqual([], result["missingProtectedDatabases"])
        seed.assert_not_called()
        artifact_restore.assert_not_called()
        self.assertEqual("candidate-post-rebuild-release-contract-failed", rejected["status"])
        self.assertFalse(rejected["candidate"]["_candidateReusable"])

    def test_portfolio_rebuild_composition_freezes_verified_release(self):
        from unittest.mock import MagicMock
        from digital_twin.infrastructure.composition.reasoning_projection import build_ontology_portfolio_rebuild_runner
        guard = {"immutable": True, "ruleboxFingerprint": "frozen-rules", "tboxFingerprint": "frozen-tbox"}
        catalog = {"configured": True, "status": "ok", "rules": [{"rule_id": "frozen"}], "frozenReleaseVerified": True}
        repository = MagicMock()
        with patch.dict(os.environ, {"ORBIT_TYPEDB_REBUILD_RELEASE_GUARD": json.dumps(guard)}), \
                patch("digital_twin.infrastructure.ontology_graph_store.ontology_repository_from_settings", return_value=repository), \
                patch("digital_twin.infrastructure.composition.reasoning_release.prepare_v2_rulebox_release", return_value=(catalog, {})) as verify, \
                patch("digital_twin.infrastructure.ontology_projection.PortfolioOntologyProjectionRecorder") as recorder, \
                patch("digital_twin.infrastructure.operational_store.monitor_store"):
            build_ontology_portfolio_rebuild_runner({"typedbDatabase": "candidate"})
            verify.assert_called_once_with(repository, {"typedbDatabase": "candidate"}, release_guard=guard)
            self.assertIs(catalog, recorder.call_args.kwargs["frozen_rulebox_catalog"])
            verify.side_effect = RuntimeError("fingerprint mismatch")
            recorder.reset_mock()
            with self.assertRaisesRegex(RuntimeError, "fingerprint mismatch"):
                build_ontology_portfolio_rebuild_runner({"typedbDatabase": "candidate"})
            recorder.assert_not_called()

    def test_portfolio_rebuild_subprocess_receives_seed_fingerprints(self):
        spec = {"role": "typedb-stage", "label": "candidate", "log": "unused",
                "_typedbSeedContract": {"activeRuleboxFingerprint": "frozen-rules", "activeTboxFingerprint": "frozen-tbox"}}
        with patch.object(service_manager, "typedb_portfolio_world_projection_rebuild_command", return_value=["rebuild"]), \
                patch.object(service_manager, "typedb_subprocess_environment", return_value={}), \
                patch.object(service_manager, "low_priority_command", return_value=["rebuild"]), \
                patch.object(service_manager, "append_log"), \
                patch.object(service_manager, "append_log_text"), \
                patch.object(service_manager.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="{}", stderr="")) as run:
            self.assertTrue(service_manager.ensure_typedb_portfolio_world_projection_rebuilt(spec))
        guard = json.loads(run.call_args.kwargs["env"]["ORBIT_TYPEDB_REBUILD_RELEASE_GUARD"])
        self.assertEqual({"immutable": True, "ruleboxFingerprint": "frozen-rules", "tboxFingerprint": "frozen-tbox"}, guard)

    def _assert_fresh_blue_green_candidate_restarts_after_schema_seed(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = {
                "label": "TypeDB ontology graph store",
                "role": "typedb",
                "pid": Path(temp) / "typedb.pid",
                "log": Path(temp) / "typedb.log",
                "dataPath": Path(temp) / "typedb-data",
                "healthAddress": "127.0.0.1:1729",
                "httpAddress": "127.0.0.1:8000",
                "typedbDatabase": "ontology_v2",
                "protectedTypeDbDatabases": [],
            }
            candidate = service_manager.typedb_blue_green_stage_spec(spec)
            self.assertEqual("0", candidate["processNice"])
            with patch.object(service_manager, "stop_worker", return_value=0), \
                    patch.object(service_manager, "stop_typedb_stage_data_path_processes", return_value=True), \
                    patch.object(service_manager, "launch_typedb_stage_process", return_value=True), \
                    patch.object(service_manager, "ensure_typedb_seeded", return_value=True), \
                    patch.object(service_manager, "validate_typedb_candidate_seed_contract", return_value={
                        "ready": True,
                        "status": "ready",
                    }), \
                    patch.object(service_manager, "validate_typedb_candidate_release_contract", return_value={
                        "ready": True,
                        "status": "ready",
                    }), \
                    patch.object(service_manager, "write_typedb_candidate_reuse_marker"), \
                    patch.object(service_manager, "restart_typedb_stage_for_schema_retry", return_value=True) as restart, \
                    patch.object(service_manager, "validate_typedb_candidate_inference_runtime", return_value={
                        "ready": True,
                        "mode": "native",
                    }), \
                    patch.object(service_manager, "ensure_typedb_shared_world_projection_rebuilt", return_value=True), \
                    patch.object(service_manager, "ensure_typedb_portfolio_world_projection_rebuilt", return_value=True), \
                    patch.object(service_manager, "typedb_driver_ready", return_value=True):
                result = service_manager.prepare_typedb_blue_green_candidate(spec)

        self.assertEqual("prepared", result["status"])
        self.assertFalse(result["candidateSeedReused"])
        restart.assert_called_once_with(
            result["candidate"],
            "post-seed capability cache warmup",
        )

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
        # Successful credential bootstrap owns and clears the pending marker.
        clear.assert_not_called()

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

    def test_proactive_rotation_starts_before_write_block_threshold(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "typedb-data"
            marker = Path(temp) / "marker.json"
            data_path.mkdir()
            (data_path / "segment").write_bytes(b"x" * (7 * 1024 * 1024))
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=marker):
                result = service_manager.typedb_auto_rotation_needed({
                    "dataPath": data_path,
                    "maxSizeMb": "10",
                    "autoRotationEnabled": "1",
                    "autoRotationPercent": "65",
                    "writeBlockPercent": "75",
                    "autoRotationWalMb": "0",
                    "autoRotationFreeSpaceMb": "0",
                    "blueGreenMinimumHeadroomMb": "1",
                    "blueGreenEstimatedCandidateMaxMb": "1",
                })

        self.assertTrue(result["needed"])
        self.assertEqual(65, result["thresholdPercent"])
        self.assertEqual(75, result["writeBlockPercent"])

    def test_supervisor_dispatches_rotation_to_independent_process(self):
        with tempfile.TemporaryDirectory() as temp:
            pid_path = Path(temp) / "rotation.pid"
            log_path = Path(temp) / "rotation.log"
            process = SimpleNamespace(pid=9876)
            with patch.object(
                service_manager,
                "typedb_auto_rotation_worker_running",
                return_value={"running": False, "pid": 0},
            ), patch.object(
                service_manager,
                "typedb_auto_rotation_worker_pid_path",
                return_value=pid_path,
            ), patch.object(
                service_manager,
                "typedb_auto_rotation_log_path",
                return_value=log_path,
            ), patch.object(
                service_manager,
                "record_typedb_auto_rotation_state",
            ) as record_state, patch.object(
                service_manager.subprocess,
                "Popen",
                return_value=process,
            ) as popen:
                result = service_manager.launch_supervisor_owned_typedb_rotation(
                    "capacity pressure",
                )
                pid_text = pid_path.read_text(encoding="utf-8").strip()

        self.assertTrue(result["started"])
        self.assertEqual(9876, result["pid"])
        self.assertIn("--supervisor-owned", popen.call_args.args[0])
        self.assertEqual("9876", pid_text)
        self.assertEqual("dispatching", record_state.call_args_list[0].kwargs["lastAutoRotationStatus"])

    def test_rotation_failure_counter_resets_only_after_success(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "marker.json"
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=marker):
                first = service_manager.record_typedb_auto_rotation_state(
                    lastAutoRotationStatus="candidate-failed-active-preserved",
                )
                running = service_manager.record_typedb_auto_rotation_state(
                    lastAutoRotationStatus="running",
                )
                second = service_manager.record_typedb_auto_rotation_state(
                    lastAutoRotationStatus="reset-failed",
                )
                successful = service_manager.record_typedb_auto_rotation_state(
                    lastAutoRotationStatus="ok",
                )

        self.assertEqual(1, first["autoRotationConsecutiveFailureCount"])
        self.assertEqual(1, running["autoRotationConsecutiveFailureCount"])
        self.assertEqual(2, second["autoRotationConsecutiveFailureCount"])
        self.assertEqual(0, successful["autoRotationConsecutiveFailureCount"])

    def test_stale_running_rotation_without_live_owner_becomes_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "marker.json"
            lock = Path(temp) / "rotation.lock"
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=marker), \
                    patch.object(service_manager, "typedb_rotation_lock_path", return_value=lock):
                service_manager.write_typedb_retention_marker({
                    "lastAutoRotationAttemptEpoch": 1000,
                    "lastAutoRotationHeartbeatEpoch": 1100,
                    "lastAutoRotationStatus": "running",
                })
                result = service_manager.reconcile_typedb_auto_rotation_state(
                    {"autoRotationRunningTimeoutSeconds": "14400"},
                    now_epoch=1161,
                )

        self.assertEqual("interrupted", result["status"])
        self.assertEqual("interrupted", result["marker"]["lastAutoRotationStatus"])
        self.assertEqual(1, result["marker"]["autoRotationConsecutiveFailureCount"])

    def test_live_rotation_owner_refreshes_expired_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "marker.json"
            lock = Path(temp) / "rotation.lock"
            lock.write_text(json.dumps({"pid": 4321}), encoding="utf-8")
            with patch.object(service_manager, "typedb_retention_marker_path", return_value=marker), \
                    patch.object(service_manager, "typedb_rotation_lock_path", return_value=lock), \
                    patch.object(service_manager, "pid_exists", return_value=True):
                service_manager.write_typedb_retention_marker({
                    "lastAutoRotationAttemptEpoch": 1000,
                    "lastAutoRotationHeartbeatEpoch": 1100,
                    "lastAutoRotationStatus": "running",
                })
                result = service_manager.reconcile_typedb_auto_rotation_state(
                    {"autoRotationRunningTimeoutSeconds": "600"},
                    now_epoch=1800,
                )

        self.assertEqual("heartbeat-refreshed", result["status"])
        self.assertEqual("running", result["marker"]["lastAutoRotationStatus"])
        self.assertEqual(1800, result["marker"]["lastAutoRotationHeartbeatEpoch"])

    def test_expired_rotation_lock_does_not_block_new_owner_even_if_pid_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "rotation.lock"
            lock.write_text(json.dumps({
                "pid": 4321,
                "token": "stale",
                "expiresAtEpoch": 1000,
            }), encoding="utf-8")
            with patch.object(service_manager, "typedb_rotation_lock_path", return_value=lock), \
                    patch.object(service_manager, "pid_exists", return_value=True), \
                    patch.object(service_manager.time, "time", return_value=1100):
                acquired = service_manager.acquire_typedb_maintenance_lock(
                    "replacement",
                    max_age_seconds=60,
                )

        self.assertTrue(acquired["acquired"])
        self.assertEqual("replacement", acquired["operation"])
        self.assertNotEqual("stale", acquired["token"])

    def test_rotation_lock_renewal_preserves_token_and_extends_lease(self):
        with tempfile.TemporaryDirectory() as temp:
            lock_path = Path(temp) / "rotation.lock"
            lock = {
                "acquired": True,
                "pid": os.getpid(),
                "token": "owned",
                "leaseSeconds": 60,
                "expiresAtEpoch": 1060,
            }
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            with patch.object(service_manager, "typedb_rotation_lock_path", return_value=lock_path):
                renewed = service_manager.renew_typedb_maintenance_lock(lock, now_epoch=1050)

        self.assertTrue(renewed["renewed"])
        self.assertEqual("owned", renewed["token"])
        self.assertEqual(1110, renewed["expiresAtEpoch"])

    def test_supervisor_watchdog_resumes_stopped_process_before_restarting(self):
        decision = service_manager.supervisor_recovery_decision(
            pid=123,
            process_exists=True,
            process_state="T",
            heartbeat={"observedAtEpoch": 1000},
            now_epoch=1010,
        )

        self.assertEqual("continue", decision["action"])
        self.assertEqual("supervisor-process-stopped", decision["reason"])

    def test_supervisor_watchdog_replaces_live_process_with_stale_heartbeat(self):
        decision = service_manager.supervisor_recovery_decision(
            pid=123,
            process_exists=True,
            process_state="S",
            heartbeat={"observedAtEpoch": 1000},
            now_epoch=1401,
            stale_after_seconds=300,
        )

        self.assertEqual("replace", decision["action"])
        self.assertEqual(401, decision["heartbeatAgeSeconds"])
        self._assert_watchdog_claim_replaces_pid_reused_by_unrelated_process()
        self._assert_watchdog_claim_preserves_live_orbit_watchdog()

    def _assert_typedb_health_probe_timeouts_allow_loaded_store_recovery(self):
        spec = {
            "runtimeHealthProbeTimeoutMillis": "12000",
            "startupHealthProbeTimeoutMillis": "45000",
        }

        self.assertEqual(
            12000,
            service_manager.typedb_health_probe_timeout_millis(spec),
        )
        self.assertEqual(
            45000,
            service_manager.typedb_health_probe_timeout_millis(spec, startup=True),
        )
        self.assertEqual(
            30000,
            service_manager.typedb_health_probe_timeout_millis(
                {"runtimeHealthProbeTimeoutMillis": "90000"},
            ),
        )
        self.assertEqual(
            60000,
            service_manager.typedb_health_probe_timeout_millis(
                {"startupHealthProbeTimeoutMillis": "90000"},
                startup=True,
            ),
        )

    def _assert_watchdog_claim_replaces_pid_reused_by_unrelated_process(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "watchdog.pid"
            path.write_text("741\n", encoding="utf-8")
            with patch.object(service_manager.os, "getpid", return_value=900), patch.object(
                service_manager,
                "pid_exists",
                return_value=True,
            ), patch.object(
                service_manager,
                "command_for_pid",
                return_value="/System/Library/CoreServices/ReportCrash agent",
            ):
                claimed = service_manager.claim_supervisor_watchdog_pid(path)

            self.assertTrue(claimed)
            self.assertEqual("900", path.read_text(encoding="utf-8").strip())

    def _assert_watchdog_claim_preserves_live_orbit_watchdog(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "watchdog.pid"
            path.write_text("741\n", encoding="utf-8")
            with patch.object(service_manager.os, "getpid", return_value=900), patch.object(
                service_manager,
                "pid_exists",
                return_value=True,
            ), patch.object(
                service_manager,
                "command_for_pid",
                return_value="python python_service/monitor_service.py watchdog",
            ):
                claimed = service_manager.claim_supervisor_watchdog_pid(path)

            self.assertFalse(claimed)
            self.assertEqual("741", path.read_text(encoding="utf-8").strip())

    def test_typedb_rotate_recovers_workers_and_alerts_when_reset_fails(self):
        spec = {"role": "typedb", "dataPath": Path("/tmp/orbit-alpha-typedb-test")}
        with patch.object(service_manager, "worker_specs", return_value={"typedb": spec}), \
                patch.object(service_manager, "typedb_rotation_database_inventory", return_value={
                    "ready": True,
                    "databases": ["ontology-active"],
                    "protectedDatabases": ["ontology-active"],
                }), \
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
        incident.assert_called_once()
        self.assertEqual("typedb", incident.call_args.args[0]["role"])
        self.assertEqual(["ontology-active"], incident.call_args.args[0]["protectedTypeDbDatabases"])
        self.assertEqual({"needed": True, "reason": "size"}, incident.call_args.args[1])
        self.assertEqual("typedb-auto-rotation-failed", incident.call_args.kwargs["alert_kind"])
        self.assertEqual("reset-failed", incident.call_args.kwargs["failure_reason"])
        self.assertEqual("reset-failed", record_state.call_args_list[-1].kwargs["lastAutoRotationStatus"])

    def test_running_typedb_recovery_does_not_block_the_supervisor(self):
        spec = {
            "label": "TypeDB ontology graph store",
            "role": "typedb",
            "pid": Path("/tmp/orbit-alpha-running-typedb.pid"),
            "log": Path("/tmp/orbit-alpha-running-typedb.log"),
            "command": ["typedb", "server"],
            "healthAddress": "127.0.0.1:1729",
            "dataPath": "/tmp/orbit-alpha-typedb-data",
        }
        with patch.object(service_manager, "read_pid", return_value=123), patch.object(
            service_manager,
            "is_running",
            return_value=True,
        ), patch.object(
            service_manager,
            "typedb_service_ready",
            return_value=False,
        ), patch.object(service_manager, "append_log"), patch.object(
            service_manager,
            "wait_for_typedb_ready",
        ) as blocking_wait:
            self.assertEqual(0, service_manager.start_worker(spec, wait_for_ready=False))

        blocking_wait.assert_not_called()

        with tempfile.TemporaryDirectory() as temp, patch.object(
            service_manager,
            "data_dir",
            return_value=Path(temp),
        ), patch.object(
            service_manager,
            "typedb_process_generation",
            return_value="generation-1",
        ):
            saved = service_manager.mark_typedb_startup_finalized(spec, 123)
            self.assertTrue(saved["saved"])
            self.assertTrue(service_manager.typedb_startup_is_finalized(spec, 123))

            with patch.object(
                service_manager,
                "typedb_process_generation",
                return_value="generation-2",
            ):
                self.assertFalse(service_manager.typedb_startup_is_finalized(spec, 123))

            service_manager.clear_typedb_startup_readiness()
            self.assertFalse(service_manager.typedb_startup_readiness_path().exists())

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
            self.assertTrue(marker_payload["graphStoreEpoch"].startswith("rollback:"))

class TypeDBReclaimAdmissionTests(unittest.TestCase):
    def decision(self, root, active_mb=8000, wal_mb=0, size_mb=None):
        active = Path(root) / 'typedb-data'
        active.mkdir(exist_ok=True)
        spec = {'dataPath': active, 'maxSizeMb': 32768, 'autoRotationEnabled': '1',
                'autoRotationPercent': 65, 'writeBlockPercent': 75,
                'autoRotationWalMb': 4096, 'autoRotationFreeSpaceMb': 24576,
                'blueGreenEstimatedCandidateMaxMb': 4096, 'blueGreenRetiredRetentionMinutes': 120}
        with patch.object(service_manager, 'read_typedb_retention_marker', return_value={}), \
                patch.object(service_manager, 'storage_directory_physical_size_bytes', return_value=active_mb * 1024**2):
            return service_manager.typedb_auto_rotation_needed(
                spec, now_epoch=20000,
                size_provider=lambda _: (size_mb if size_mb is not None else active_mb) * 1024**2,
                disk_usage_provider=lambda _: SimpleNamespace(free=20000 * 1024**2),
                inventory_provider=lambda *a, **k: {'typedbWalMb': wal_mb})

    def test_shared_pressure_needs_material_gain_and_no_retained_copy(self):
        with tempfile.TemporaryDirectory() as root:
            for size in (1024, 4400):
                d = self.decision(root, active_mb=size)
                self.assertFalse(d['needed'])
                self.assertEqual('shared-disk-no-reclaim-benefit', d['reason'])
            d = self.decision(root)
            self.assertTrue(d['needed'])
            self.assertEqual(3904, d['estimatedReclaimMb'])
            self.assertTrue(d['reclaimRequiresRetiredCleanup'])
            (Path(root) / 'typedb-data-retired-19000').mkdir()
            d = self.decision(root)
            self.assertFalse(d['needed'])
            self.assertEqual('shared-disk-awaiting-retired-cleanup', d['reason'])
            for kwargs in ({'size_mb': 24000}, {'wal_mb': 5000}, {'size_mb': 33000}):
                with self.subTest(kwargs=kwargs):
                    self.assertTrue(self.decision(root, **kwargs)['needed'])

    def test_cleanup_precedes_both_measurements_but_waits_for_safe_runtime(self):
        for ready, rotating, maintenance in [(True, False, False), (False, False, False), (True, True, False), (True, False, True)]:
            calls = []
            with self.subTest(ready=ready, rotating=rotating, maintenance=maintenance), \
                    patch.object(service_manager, 'typedb_auto_rotation_worker_running', return_value={'running': rotating, 'pid': 123 if rotating else 0}), \
                    patch.object(service_manager, 'supervisor_maintenance_active', return_value=maintenance), \
                    patch.object(service_manager, 'prune_retired_typedb_data_paths', side_effect=lambda _: calls.append('prune')), \
                    patch.object(service_manager, 'typedb_reset_needed', side_effect=lambda *a, **k: calls.append('reset') or {}), \
                    patch.object(service_manager, 'typedb_auto_rotation_needed', side_effect=lambda _: calls.append('automatic') or {}):
                service_manager.typedb_capacity_maintenance_decisions({}, graph_ready=ready)
            expected = ['prune'] if ready and not rotating and not maintenance else []
            self.assertEqual(expected + ['reset', 'automatic'], calls)

    def test_pruning_preserves_active_recent_failed_staged_and_unknown_paths(self):
        from digital_twin.infrastructure.typedb_retired_storage import prune_retired_stores
        with tempfile.TemporaryDirectory() as root:
            paths = {}
            for name in ['typedb-data', 'typedb-data-retired-1000', 'typedb-data-retired-19900',
                         'typedb-data-retired-1001', 'typedb-data-retired-unknown',
                         'typedb-data-failed-1000', 'typedb-data-candidate']:
                p = Path(root) / name
                p.mkdir()
                (p / 'data').write_bytes(b'x' * 8192)
                os.utime(p, (1000, 1000))
                paths[name] = p
            os.utime(paths['typedb-data-retired-1001'], (19900, 19900))
            alias = Path(root) / 'typedb-data-retired-1002'
            alias.symlink_to(paths['typedb-data'], target_is_directory=True)
            result = prune_retired_stores(paths['typedb-data'], 120, 20000)
            self.assertEqual([str(paths['typedb-data-retired-1000'])], result['removedPaths'])
            for name, p in paths.items():
                self.assertEqual(name != 'typedb-data-retired-1000', p.exists(), name)
            self.assertTrue(alias.is_symlink())
            self.assertTrue(result['spaceReclaimed'])
            self.assertGreater(result['removedAllocatedBytes'], 0)
            self.assertIn('diskFreeDeltaBytes', result)

    def test_failed_removal_is_reported_and_still_blocks_shared_rotation(self):
        from digital_twin.infrastructure import typedb_retired_storage as storage
        with tempfile.TemporaryDirectory() as root:
            active = Path(root) / 'typedb-data'
            active.mkdir()
            old = Path(root) / 'typedb-data-retired-1000'
            old.mkdir()
            (old / 'data').write_bytes(b'x')
            os.utime(old, (1000, 1000))
            with patch.object(storage.shutil, 'rmtree', side_effect=PermissionError('denied')):
                result = storage.prune_retired_stores(active, 120, 20000)
            self.assertTrue(old.exists())
            self.assertFalse(result['spaceReclaimed'])
            self.assertEqual(1, result['retiredStoreCount'])
            self.assertEqual('PermissionError', result['errors'][0]['errorType'])
            self.assertFalse(self.decision(root)['needed'])

    def test_pending_cutover_keeps_rollback_directory(self):
        with patch.object(service_manager, 'read_typedb_retention_marker', return_value={'blueGreenCutoverPending': True}), \
                patch.object(service_manager, 'prune_retired_stores') as prune:
            self.assertEqual([], service_manager.prune_retired_typedb_data_paths({}))
            prune.assert_not_called()

    def test_expired_cleanup_relief_cancels_shared_disk_rotation(self):
        from digital_twin.infrastructure import typedb_retired_storage as storage
        with tempfile.TemporaryDirectory() as root:
            active = Path(root) / 'typedb-data'
            active.mkdir()
            (active / 'data').write_bytes(b'x')
            old = Path(root) / 'typedb-data-retired-1000'
            old.mkdir()
            (old / 'data').write_bytes(b'x' * 8192)
            os.utime(old, (1000, 1000))
            spec = {'dataPath': active, 'maxSizeMb': 32768, 'autoRotationEnabled': '1',
                    'autoRotationWalMb': 0, 'autoRotationFreeSpaceMb': 24576,
                    'blueGreenRetiredRetentionMinutes': 120}
            def usage(_):
                return SimpleNamespace(free=(20000 if old.exists() else 26000) * 1024**2)
            with patch.object(service_manager, 'read_typedb_retention_marker', return_value={}), \
                    patch.object(service_manager, 'typedb_auto_rotation_worker_running', return_value={'running': False}), \
                    patch.object(service_manager, 'supervisor_maintenance_active', return_value=False), \
                    patch.object(service_manager, 'record_typedb_auto_rotation_state') as record, \
                    patch.object(service_manager, 'append_log'), \
                    patch.object(service_manager, 'typedb_reset_needed', return_value={}), \
                    patch.object(storage.shutil, 'disk_usage', side_effect=usage):
                before = service_manager.typedb_auto_rotation_needed(spec)
                _, after = service_manager.typedb_capacity_maintenance_decisions(spec, graph_ready=True)
            self.assertTrue(before['diskPressureReached'])
            self.assertEqual('below-threshold', after['reason'])
            self.assertFalse(after['needed'])
            self.assertFalse(old.exists())
            self.assertTrue((active / 'data').exists())
            proof = record.call_args.kwargs['lastRetiredCleanup']
            self.assertTrue(proof['spaceReclaimed'])
            self.assertEqual(6000 * 1024**2, proof['diskFreeDeltaBytes'])

    def test_seed_disabled_pending_flag_requires_a_completed_swap(self):
        marker = {'blueGreenCutoverPending': True, 'blueGreenCutoverAt': '2026-10-01T05:04:10Z',
                  'lastAutoRotationFinishedAt': '2026-10-01T05:04:39Z', 'lastAutoRotationStatus': 'ok',
                  'lastAutoRotationResult': {'status': 'swapped', 'restartStatus': 'ok'}}
        for status, finish, allowed in [('ok', '2026-10-01T05:04:39Z', True),
                                         ('running', '2026-10-01T05:04:39Z', False),
                                         ('ok', '2026-10-01T04:00:00Z', False)]:
            with self.subTest(status=status, finish=finish), \
                    patch.object(service_manager, 'read_typedb_retention_marker', return_value={
                        **marker, 'lastAutoRotationStatus': status, 'lastAutoRotationFinishedAt': finish}), \
                    patch.object(service_manager, 'prune_retired_stores', return_value={'removedPaths': [], 'errors': []}) as prune:
                service_manager.prune_retired_typedb_data_paths({})
                self.assertEqual(allowed, prune.called)

    def test_shared_disk_inventory_failure_defers_rotation(self):
        with tempfile.TemporaryDirectory() as root, \
                patch.object(service_manager, 'retired_stores', side_effect=PermissionError('denied')):
            result = self.decision(root)
        self.assertFalse(result['needed'])
        self.assertEqual('shared-disk-retired-inventory-unavailable', result['reason'])

    def test_missing_active_store_never_deletes_rollback_copy(self):
        from digital_twin.infrastructure.typedb_retired_storage import prune_retired_stores
        with tempfile.TemporaryDirectory() as root:
            old = Path(root) / 'typedb-data-retired-1000'
            old.mkdir()
            os.utime(old, (1000, 1000))
            result = prune_retired_stores(Path(root) / 'typedb-data', 0, 20000)
            self.assertEqual([], result['removedPaths'])
            self.assertTrue(old.exists())


if __name__ == "__main__":
    unittest.main()
