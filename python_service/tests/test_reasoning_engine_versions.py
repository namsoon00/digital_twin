import unittest
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from digital_twin.application.reasoning_engine_platform import ReasoningEnginePlatformService
from digital_twin.domain.reasoning_engine_versions import (
    EngineControlState,
    EngineReleaseBundle,
    ReasoningEngineDescriptor,
    engine_transition_allowed,
    promotion_blockers,
    reasoning_release_identity,
)
from digital_twin.infrastructure.mysql_versioned_runtime import (
    MySQLReasoningEngineJobStore,
    merge_reasoning_deployment_health,
)


def descriptor(status="candidate"):
    return ReasoningEngineDescriptor(
        engine_family="ontology-investment-brain",
        engine_version="v2",
        deployment_id="ontology-v2-shadow",
        status=status,
        graph_store_binding="typedb-v9",
        time_series_backend_id="questdb-shadow",
        release_bundle=EngineReleaseBundle("tbox-v1", "rulebox-v1", "prompt-v1", "features-v1"),
    )


class ReasoningEngineVersionTests(unittest.TestCase):
    def test_reasoning_health_merge_preserves_write_once_lifecycle_markers(self):
        result = merge_reasoning_deployment_health(
            {
                "status": "ready",
                "validationStartedAt": "2026-08-26T07:52:00Z",
                "graphStoreProvisioning": {"mode": "reuse-existing"},
            },
            {"status": "degraded", "lastError": "temporary"},
        )

        self.assertEqual("degraded", result["status"])
        self.assertEqual("temporary", result["lastError"])
        self.assertEqual("2026-08-26T07:52:00Z", result["validationStartedAt"])
        self.assertEqual(
            {"mode": "reuse-existing"},
            result["graphStoreProvisioning"],
        )

    def test_validation_window_counts_only_jobs_claimed_after_the_marker(self):
        class Connection:
            def __init__(self):
                self.queries = []

            def execute(self, sql, params=()):
                self.queries.append((sql, tuple(params)))
                return SimpleNamespace(fetchall=lambda: [])

        connection = Connection()

        class Store(MySQLReasoningEngineJobStore):
            @contextmanager
            def connect(self):
                yield connection

        result = object.__new__(Store).summary(
            "ontology-v2-candidate",
            completed_since="2026-08-26T08:27:29Z",
        )

        cohort_queries = [
            sql for sql, _params in connection.queries
            if "validation_cohort_id" not in sql and "claimed_at >= %s" in sql
        ]
        self.assertTrue(cohort_queries)
        self.assertTrue(all("completed_at >= %s" not in sql for sql in cohort_queries))
        self.assertEqual(0, result["sampleCount"])

    def test_v2_release_preflight_reuses_exact_immutable_release_without_migration(self):
        from digital_twin.domain.ontology_rulebox_governance import rulebox_rules_hash
        from digital_twin.domain.ontology_schema import default_tbox_metadata
        from digital_twin.infrastructure.ontology_projection import (
            PortfolioOntologyProjectionRecorder,
            bootstrap_rule_catalog,
        )
        from digital_twin.infrastructure.service_factory import prepare_v2_rulebox_release

        rules = deepcopy(bootstrap_rule_catalog()["rules"])
        rules[0]["knowledge_basis"]["ownershipContractVersion"] = "frozen-old-contract"
        frozen_hash = rulebox_rules_hash(rules)
        expected_tbox = default_tbox_metadata()

        class Repository:
            store_key = "typedb"

            def __init__(self):
                self.calls = []

            def rulebox_snapshot(self):
                self.calls.append("snapshot")
                return {
                    "configured": True,
                    "status": "ok",
                    "rules": deepcopy(rules),
                    "ruleCount": len(rules),
                    "ruleboxRulesHash": frozen_hash,
                }

            def save_rulebox(self, _payload):
                self.calls.append("save")
                raise AssertionError("an immutable release must never be migrated")

            def active_tbox_metadata(self):
                self.calls.append("tbox")
                return {**expected_tbox, "status": "ok", "source": "test"}

        repository = Repository()

        snapshot, readiness = prepare_v2_rulebox_release(
            repository,
            {},
            release_guard={
                "immutable": True,
                "ruleboxFingerprint": frozen_hash,
                "tboxFingerprint": expected_tbox["fingerprint"],
                "tboxVersion": expected_tbox["version"],
            },
        )
        recorder = PortfolioOntologyProjectionRecorder(
            Repository(),
            frozen_rulebox_catalog=snapshot,
            frozen_tbox_metadata=readiness["tboxReleasePreflight"],
        )

        self.assertEqual(["snapshot", "tbox"], repository.calls)
        self.assertTrue(snapshot["frozenReleaseVerified"])
        self.assertEqual("immutable-release-reused", readiness["ruleCatalogMigration"]["status"])
        self.assertEqual("ready", recorder.ensure_rulebox_ready()["status"])

    def test_v2_release_preflight_bootstraps_an_isolated_empty_database(self):
        from digital_twin.domain.ontology_schema import default_tbox_metadata
        from digital_twin.infrastructure.ontology_projection import bootstrap_rule_catalog
        from digital_twin.infrastructure.service_factory import prepare_v2_rulebox_release

        rules = deepcopy(bootstrap_rule_catalog()["rules"])
        expected_tbox = default_tbox_metadata()

        class Repository:
            def __init__(self):
                self.seeded = False
                self.calls = []

            def rulebox_snapshot(self):
                self.calls.append("snapshot")
                if not self.seeded:
                    raise RuntimeError("[INF2] Type label 'ontology-node' not found.")
                return {
                    "configured": True,
                    "status": "ok",
                    "rules": deepcopy(rules),
                    "ruleCount": len(rules),
                }

            def seed_ontology(self, payload):
                self.calls.append("seed")
                self.seeded = True
                self.assert_seed_payload = deepcopy(payload)
                return {
                    "configured": True,
                    "saved": True,
                    "seeded": True,
                    "status": "ok",
                    "ruleCount": len(rules),
                }

            def active_tbox_metadata(self):
                self.calls.append("tbox")
                return {**expected_tbox, "status": "ok", "source": "test"}

        repository = Repository()

        snapshot, readiness = prepare_v2_rulebox_release(repository, {})

        self.assertEqual("ready", readiness["status"])
        self.assertEqual("ok", readiness["releaseBootstrap"]["status"])
        self.assertTrue(repository.assert_seed_payload["replaceRuleBox"])
        self.assertEqual(len(rules), snapshot["ruleCount"])
        self.assertEqual(
            ["snapshot", "seed", "snapshot", "snapshot", "tbox"],
            repository.calls,
        )

    def test_v2_release_preflight_rejects_immutable_fingerprint_mismatch_without_write(self):
        from digital_twin.infrastructure.ontology_projection import bootstrap_rule_catalog
        from digital_twin.infrastructure.service_factory import prepare_v2_rulebox_release

        rules = deepcopy(bootstrap_rule_catalog()["rules"])

        class Repository:
            def __init__(self):
                self.calls = []

            def rulebox_snapshot(self):
                self.calls.append("snapshot")
                return {
                    "configured": True,
                    "status": "ok",
                    "rules": deepcopy(rules),
                    "ruleCount": len(rules),
                    "ruleboxRulesHash": "actual-release-hash",
                }

            def save_rulebox(self, _payload):
                self.calls.append("save")
                raise AssertionError("a mismatched immutable release must not be rewritten")

            def active_tbox_metadata(self):
                self.calls.append("tbox")
                raise AssertionError("RuleBox mismatch must fail before TBox access")

        repository = Repository()

        with self.assertRaisesRegex(RuntimeError, "immutable V2 RuleBox release fingerprint"):
            prepare_v2_rulebox_release(
                repository,
                {},
                release_guard={
                    "immutable": True,
                    "ruleboxFingerprint": "expected-release-hash",
                    "tboxFingerprint": "expected-tbox-hash",
                },
            )

        self.assertEqual(["snapshot"], repository.calls)

    def test_v2_release_preflight_rejects_unavailable_model_signal_release(self):
        from digital_twin.infrastructure.service_factory import (
            v2_model_signal_release_contract,
        )

        contract = v2_model_signal_release_contract(
            {
                "rules": [
                    {
                        "enabled": True,
                        "conditions": [
                            {
                                "relation_type": "HAS_MODEL_SIGNAL",
                                "target_property_filters": {
                                    "releaseId": "event-statistics-production-v3"
                                },
                            }
                        ],
                    }
                ]
            },
            {
                "statisticalPriceSignalReleaseId": "price-path-statistics-production-v2",
                "statisticalFlowSignalReleaseId": "flow-statistics-production-v2",
            },
        )

        self.assertEqual("mismatch", contract["status"])
        self.assertEqual(
            ["event-statistics-production-v3"], contract["missingReleaseIds"]
        )

    def test_v2_watch_waits_for_release_database_without_process_exit(self):
        from digital_twin.infrastructure.cli import watch_v2_reasoning_engine

        watched = []
        attempts = []
        runners = []

        class Runner:
            def __init__(self):
                self.shutdown_count = 0

            def watch(self):
                watched.append(True)

            def shutdown(self):
                self.shutdown_count += 1
                return {"status": "unchanged"}

        def factory(settings, worker_id=""):
            attempts.append((settings, worker_id))
            if len(attempts) == 1:
                raise RuntimeError("release database is rebuilding")
            runner = Runner()
            runners.append(runner)
            return runner

        sleeps = []
        result = watch_v2_reasoning_engine(
            factory,
            {"reasoningEngineV2IndependentEnabled": "1"},
            worker_id="v2-test",
            retry_seconds=7,
            sleep=sleeps.append,
        )

        self.assertEqual(0, result)
        self.assertEqual([7.0], sleeps)
        self.assertEqual([True], watched)
        self.assertEqual(2, len(attempts))
        self.assertEqual(1, runners[0].shutdown_count)

    def test_v2_watch_reconnects_after_mysql_connection_loss(self):
        from digital_twin.infrastructure.cli import watch_v2_reasoning_engine

        attempts = []
        runners = []

        class Runner:
            def __init__(self, fails):
                self.fails = fails
                self.shutdown_count = 0

            def watch(self):
                if self.fails:
                    raise RuntimeError(2013, "Lost connection during idempotent ingress")

            def shutdown(self):
                self.shutdown_count += 1

        def factory(_settings, worker_id=""):
            attempts.append(worker_id)
            runner = Runner(fails=len(attempts) == 1)
            runners.append(runner)
            return runner

        sleeps = []
        result = watch_v2_reasoning_engine(
            factory,
            {"reasoningEngineV2IndependentEnabled": "1"},
            worker_id="v2-reconnect-test",
            retry_seconds=3,
            sleep=sleeps.append,
        )

        self.assertEqual(0, result)
        self.assertEqual([3.0], sleeps)
        self.assertEqual(2, len(attempts))
        self.assertEqual([1, 1], [runner.shutdown_count for runner in runners])

    def test_initialize_never_rewrites_an_existing_release_bundle(self):
        class Registry:
            def __init__(self):
                self.rows = {
                    "v1": {
                        "deploymentId": "v1",
                        "engineVersion": "v1",
                        "status": "candidate",
                        "releaseBundle": {"runtime_revision": "v1-frozen"},
                        "capabilities": {},
                    },
                    "v2-r20": {
                        "deploymentId": "v2-r20",
                        "engineVersion": "v2",
                        "status": "active",
                        "releaseBundle": {"runtime_revision": "r20-frozen"},
                        "capabilities": {
                            "productionDelivery": True,
                            "shadowComparison": False,
                        },
                    },
                }
                self.upserted = []
                self.control_value = EngineControlState("v2-r20", "v2-r20", "v1", 4)

            def upsert(self, item):
                self.upserted.append(item.deployment_id)
                self.rows[item.deployment_id] = item.to_dict()

            def get(self, deployment_id):
                return self.rows.get(deployment_id, {})

            def list(self):
                return list(self.rows.values())

            def control(self):
                return self.control_value

            def update_capabilities(self, deployment_id, capabilities):
                self.rows[deployment_id]["capabilities"] = dict(capabilities)

        registry = Registry()
        ReasoningEnginePlatformService(
            registry,
            {
                "reasoningEngineV1DeploymentId": "v1",
                "reasoningEngineV2DeploymentId": "v2-r20",
                "_runtimeIdentity": {"revision": "new-runtime"},
            },
        ).initialize()

        self.assertEqual([], registry.upserted)
        self.assertEqual(
            "r20-frozen",
            registry.rows["v2-r20"]["releaseBundle"]["runtime_revision"],
        )

    def test_initialize_preserves_registered_active_v2_during_rolling_release(self):
        class Registry:
            def __init__(self):
                self.rows = {
                    "ontology-v2-production-r13": {
                        "deploymentId": "ontology-v2-production-r13",
                        "engineVersion": "v2",
                        "status": "active",
                    },
                }
                self.control_value = EngineControlState(
                    active_deployment_id="ontology-v2-production-r13",
                    delivery_deployment_id="ontology-v2-production-r13",
                    candidate_deployment_id="ontology-v2-production-r14",
                    version=8,
                )

            def upsert(self, item):
                self.rows.setdefault(item.deployment_id, item.to_dict())

            def get(self, deployment_id):
                return self.rows.get(deployment_id, {})

            def list(self):
                return list(self.rows.values())

            def control(self):
                return self.control_value

            def set_control(self, *args, **kwargs):
                raise AssertionError("valid rolling control must not be reset")

        registry = Registry()
        platform = ReasoningEnginePlatformService(registry, {
            "reasoningEngineV2DeploymentId": "ontology-v2-production-r14",
            "reasoningEngineCandidateDeploymentId": "ontology-v2-production-r14",
        })

        state = platform.initialize()

        self.assertEqual("ontology-v2-production-r13", state["control"]["active_deployment_id"])
        self.assertEqual("ontology-v2-production-r14", state["control"]["candidate_deployment_id"])

    def test_register_v2_release_rejects_active_graph_database_reuse(self):
        class Registry:
            def __init__(self):
                self.rows = {
                    "v2-active": {
                        "deploymentId": "v2-active",
                        "engineVersion": "v2",
                        "status": "active",
                        "graphStoreBinding": "typedb-production",
                    },
                }
                self.control_value = EngineControlState("v2-active", "v2-active", "", 4)

            def get(self, deployment_id):
                return self.rows.get(deployment_id, {})

            def control(self):
                return self.control_value

        platform = ReasoningEnginePlatformService(Registry(), {})

        result = platform.register_v2_release(
            "v2-candidate",
            "release-candidate",
            graph_database="typedb-production",
        )

        self.assertEqual("blocked", result["status"])
        self.assertEqual(["candidate-graph-store-must-be-isolated"], result["blockers"])

    def test_register_v2_release_derives_isolated_database_when_unspecified(self):
        class Registry:
            def __init__(self):
                self.rows = {
                    "v2-active": {
                        "deploymentId": "v2-active",
                        "engineVersion": "v2",
                        "status": "active",
                        "graphStoreBinding": "typedb-production",
                    },
                }
                self.control_value = EngineControlState("v2-active", "v2-active", "", 4)
                self.release_artifacts = {}

            def get(self, deployment_id):
                return self.rows.get(deployment_id, {})

            def control(self):
                return self.control_value

            def upsert(self, descriptor):
                self.rows[descriptor.deployment_id] = descriptor.to_dict()

            def set_control(self, active, delivery, candidate, expected_version=None):
                del expected_version
                self.control_value = EngineControlState(active, delivery, candidate, 5)
                return self.control_value

            def update_capabilities(self, deployment_id, capabilities):
                self.rows[deployment_id]["capabilities"] = dict(capabilities)

            def save_release_artifact(self, deployment_id, artifact):
                self.release_artifacts[deployment_id] = dict(artifact)
                return {
                    "status": "saved",
                    "artifactFingerprint": "artifact-fingerprint",
                    "ruleboxFingerprint": str(artifact.get("ruleboxFingerprint") or ""),
                    "tboxFingerprint": str(artifact.get("tboxFingerprint") or ""),
                }

        platform = ReasoningEnginePlatformService(
            Registry(),
            {"reasoningEngineShadowTypeDbDatabase": "typedb-production"},
        )

        result = platform.register_v2_release("v2-candidate", "release-candidate")

        self.assertEqual("registered", result["status"])
        self.assertNotEqual("typedb-production", result["deployment"]["graphStoreBinding"])
        self.assertTrue(
            result["deployment"]["graphStoreBinding"].startswith("orbit_alpha_ontology_candidate_")
        )
        self.assertEqual("saved", result["releaseSeedArtifact"]["status"])
        artifact = platform.registry.release_artifacts["v2-candidate"]
        self.assertEqual("ontology-release-seed-artifact-v2", artifact["version"])
        self.assertEqual(
            "release-candidate",
            artifact["releaseBundle"]["release_id"],
        )
        self.assertTrue(artifact["rules"])
        self.assertTrue(artifact["graph"]["entities"])
        from digital_twin.infrastructure.graph_store_lifecycle import (
            ontology_seed_graph_from_artifact,
        )
        restored_graph = ontology_seed_graph_from_artifact(artifact)
        self.assertEqual(
            len(artifact["graph"]["entities"]),
            len(restored_graph.entities),
        )
        self.assertEqual(
            len(artifact["graph"]["relations"]),
            len(restored_graph.relations),
        )

    def test_candidate_database_name_does_not_reuse_fixed_shadow_storage(self):
        class Registry:
            @staticmethod
            def list():
                return []

        platform = ReasoningEnginePlatformService(
            Registry(),
            {"reasoningEngineShadowTypeDbDatabase": "stale-fixed-shadow"},
        )

        database = platform.isolated_candidate_graph_database(
            "v2-candidate",
            "release-candidate",
            [],
        )

        self.assertNotEqual("stale-fixed-shadow", database)
        self.assertTrue(database.startswith("orbit_alpha_ontology_candidate_"))

        class WarmRegistry:
            @staticmethod
            def list():
                return [
                    {
                        "deploymentId": "v2-retired-ready",
                        "status": "retired",
                        "graphStoreBinding": "typedb-warm-standby",
                        "health": {
                            "runtimeOntologyRelease": {
                                "status": "ready",
                                "warmed": True,
                                "ruleCount": 119,
                            },
                        },
                    },
                ]

        warm_platform = ReasoningEnginePlatformService(WarmRegistry())

        selection = warm_platform.candidate_graph_database_selection(
            "v2-candidate",
            "release-candidate",
            ["typedb-production"],
        )

        self.assertNotEqual("typedb-warm-standby", selection["database"])
        self.assertEqual("create-isolated", selection["mode"])

        warm_reuse_platform = ReasoningEnginePlatformService(
            WarmRegistry(),
            {"reasoningEngineReuseRetiredCandidateStoreEnabled": "1"},
        )
        selection = warm_reuse_platform.candidate_graph_database_selection(
            "v2-candidate",
            "release-candidate",
            ["typedb-production"],
        )

        self.assertEqual("typedb-warm-standby", selection["database"])
        self.assertEqual("reuse-existing", selection["mode"])
        self.assertEqual("v2-retired-ready", selection["sourceDeploymentId"])

        class PrunedWarmRegistry:
            @staticmethod
            def list():
                return [
                    {
                        "deploymentId": "v2-retired-pruned",
                        "status": "retired",
                        "graphStoreBinding": "typedb-pruned-standby",
                        "health": {
                            "runtimeOntologyRelease": {
                                "status": "ready",
                                "warmed": True,
                                "ruleCount": 119,
                            },
                            "graphStorePruned": {
                                "status": "quarantined",
                            },
                        },
                    },
                ]

        pruned_platform = ReasoningEnginePlatformService(PrunedWarmRegistry())
        selection = pruned_platform.candidate_graph_database_selection(
            "v2-candidate",
            "release-candidate",
            ["typedb-production"],
        )

        self.assertNotEqual("typedb-pruned-standby", selection["database"])
        self.assertEqual("create-isolated", selection["mode"])

        class FailedLatestRegistry:
            @staticmethod
            def list():
                return [
                    {
                        "deploymentId": "v2-retired-ready",
                        "status": "retired",
                        "graphStoreBinding": "typedb-stale-standby",
                        "health": {
                            "runtimeOntologyRelease": {
                                "status": "ready",
                                "warmed": True,
                                "ruleCount": 119,
                            },
                        },
                    },
                    {
                        "deploymentId": "v2-retired-failed",
                        "status": "retired",
                        "graphStoreBinding": "typedb-stale-standby",
                        "health": {},
                    },
                ]

        failed_latest_platform = ReasoningEnginePlatformService(FailedLatestRegistry())

        selection = failed_latest_platform.candidate_graph_database_selection(
            "v2-candidate",
            "release-candidate",
            ["typedb-production"],
        )

        self.assertNotEqual("typedb-stale-standby", selection["database"])
        self.assertEqual("create-isolated", selection["mode"])

    def test_current_status_keeps_historical_resolved_failures_without_degrading(self):
        deployment = {
            "deploymentId": "v2-active",
            "engineVersion": "v2",
            "status": "active",
            "health": {"status": "ready"},
            "releaseBundle": {"release_id": "release-v2-active"},
        }

        class Registry:
            @staticmethod
            def get(_deployment_id):
                return dict(deployment)

        class Jobs:
            @staticmethod
            def summary(_deployment_id, lookback=200, **_kwargs):
                del lookback
                return {
                    "deploymentId": "v2-active",
                    "pendingCount": 0,
                    "failureCount": 61,
                    "unresolvedFailureCount": 0,
                    "resolvedFailureCount": 61,
                    "recentFailureCount24h": 1,
                }

        platform = ReasoningEnginePlatformService(
            Registry(),
            independent_job_store=Jobs(),
        )
        state = {
            "control": {
                "active_deployment_id": "v2-active",
                "delivery_deployment_id": "v2-active",
                "candidate_deployment_id": "",
            },
            "deployments": [deployment],
            "independentQueue": Jobs.summary("v2-active"),
        }

        current = platform.current_status(state)

        self.assertEqual("ready", current["status"])
        self.assertEqual([], current["reasons"])
        self.assertEqual(61, current["queue"]["failureCount"])
        self.assertEqual(0, current["queue"]["unresolvedFailureCount"])
        self.assertEqual("clear", current["historicalDebt"]["status"])

    def test_current_status_separates_old_unresolved_failure_from_current_health(self):
        deployment = {
            "deploymentId": "v2-active",
            "engineVersion": "v2",
            "status": "active",
            "health": {"status": "ready"},
            "releaseBundle": {"release_id": "release-v2-active"},
        }

        class Registry:
            @staticmethod
            def get(_deployment_id):
                return dict(deployment)

        queue = {
            "deploymentId": "v2-active",
            "pendingCount": 0,
            "failureCount": 1,
            "unresolvedFailureCount": 1,
            "recentFailureCount24h": 0,
            "latestUnresolvedFailureAt": "2026-01-01T00:00:00Z",
            "unresolvedFailureReasonCounts": {"typedbReadError": 1},
        }
        platform = ReasoningEnginePlatformService(
            Registry(),
            independent_job_store=object(),
        )
        state = {
            "control": {
                "active_deployment_id": "v2-active",
                "delivery_deployment_id": "v2-active",
                "candidate_deployment_id": "",
            },
            "deployments": [deployment],
            "independentQueue": queue,
        }

        current = platform.current_status(state)

        self.assertEqual("ready", current["status"])
        self.assertEqual([], current["reasons"])
        self.assertEqual("attention", current["historicalDebt"]["status"])
        self.assertEqual(1, current["historicalDebt"]["unresolvedFailureCount"])

    def test_current_status_degrades_for_recent_reasoning_failure(self):
        deployment = {
            "deploymentId": "v2-active",
            "engineVersion": "v2",
            "status": "active",
            "health": {"status": "ready"},
            "releaseBundle": {"release_id": "release-v2-active"},
        }

        class Registry:
            @staticmethod
            def get(_deployment_id):
                return dict(deployment)

        queue = {
            "deploymentId": "v2-active",
            "pendingCount": 0,
            "failureCount": 1,
            "unresolvedFailureCount": 1,
            "recentFailureCount24h": 1,
        }
        platform = ReasoningEnginePlatformService(
            Registry(),
            independent_job_store=object(),
        )
        state = {
            "control": {
                "active_deployment_id": "v2-active",
                "delivery_deployment_id": "v2-active",
                "candidate_deployment_id": "",
            },
            "deployments": [deployment],
            "independentQueue": queue,
        }

        current = platform.current_status(state)

        self.assertEqual("degraded", current["status"])
        self.assertIn("reasoning-failures-present", current["reasons"])
        self.assertEqual("current", current["historicalDebt"]["status"])

    def test_current_status_reads_completion_from_active_not_candidate(self):
        deployment = {
            "deploymentId": "v2-active",
            "engineVersion": "v2",
            "status": "active",
            "releaseBundle": {"release_id": "release-v2-active"},
        }

        class Registry:
            @staticmethod
            def get(_deployment_id):
                return dict(deployment)

        class Jobs:
            completion_calls = []

            @staticmethod
            def summary(deployment_id, **_kwargs):
                return {"deploymentId": deployment_id, "pendingCount": 0}

            @classmethod
            def market_observation_completion_summary(cls, deployment_id, limit=20):
                cls.completion_calls.append((deployment_id, limit))
                return {
                    "deploymentId": deployment_id,
                    "status": "healthy",
                    "receiptCount": 3,
                }

        platform = ReasoningEnginePlatformService(
            Registry(),
            independent_job_store=Jobs(),
        )
        state = {
            "control": {
                "active_deployment_id": "v2-active",
                "delivery_deployment_id": "v2-active",
                "candidate_deployment_id": "v2-candidate",
            },
            "deployments": [deployment],
            "independentQueue": {"deploymentId": "v2-active", "pendingCount": 0},
            "marketObservationReasoningCompletion": {
                "deploymentId": "v2-candidate",
                "status": "healthy",
                "receiptCount": 0,
            },
        }

        current = platform.current_status(state)

        self.assertEqual(
            {"deploymentId": "v2-active", "status": "healthy", "receiptCount": 3},
            current["marketObservationReasoningCompletion"],
        )
        self.assertEqual([("v2-active", 20)], Jobs.completion_calls)

    def test_history_gate_requires_coverage_freshness_and_zero_delivery(self):
        class Registry:
            def get(self, deployment_id):
                return {
                    "deploymentId": deployment_id,
                    "status": "shadow",
                    "health": {"status": "ready"},
                }

        class Comparisons:
            def summary(self, deployment_id, limit=200, **kwargs):
                del deployment_id, limit, kwargs
                return {
                    "sampleCount": 20,
                    "distinctSymbolCount": 5,
                    "nonEmptyNativeInferenceSampleCount": 20,
                    "nonEmptyDecisionSampleCount": 20,
                    "distinctMatchedRuleCount": 5,
                    "marketClassCount": 2,
                    "minimumFactParityPct": 100.0,
                    "minimumRuleSlotCoveragePct": 100.0,
                    "unexplainedDecisionDifferenceCount": 0,
                    "shadowDeliveryCount": 0,
                    "statusCounts": {"equivalent": 20},
                    "baselineP95DurationMs": 100,
                    "candidateP95DurationMs": 120,
                    "queueWaitP95Ms": 20,
                    "latestComparisonAt": "2099-01-01T00:00:00Z",
                }

        platform = ReasoningEnginePlatformService(
            Registry(),
            {
                "reasoningEnginePromotionMinimumComparisons": "20",
                "reasoningEnginePromotionMinimumSymbols": "5",
                "reasoningEnginePromotionMinimumNativeInferenceSamples": "20",
                "reasoningEnginePromotionMinimumDecisionSamples": "20",
                "reasoningEnginePromotionMinimumMatchedRules": "5",
                "reasoningEnginePromotionMinimumMarketClasses": "2",
            },
            comparison_store=Comparisons(),
        )

        readiness = platform.promotion_readiness("ontology-v2-shadow")

        self.assertTrue(readiness["ready"])
        self.assertEqual([], readiness["blockers"])

    def test_independent_v2_gate_uses_current_queue_age_for_historical_wait_recovery(self):
        class Registry:
            def get(self, deployment_id):
                return {
                    "deploymentId": deployment_id,
                    "status": "shadow",
                    "health": {"status": "ready", "independentExecution": True},
                    "releaseBundle": {},
                }

        class Jobs:
            def __init__(self, pending_count=0, oldest_pending_age_seconds=0):
                self.pending_count = pending_count
                self.oldest_pending_age_seconds = oldest_pending_age_seconds

            def summary(self, deployment_id, lookback=200):
                del deployment_id, lookback
                return {
                    "sampleCount": 8,
                    "successfulRunCount": 8,
                    "traceCompleteRunCount": 8,
                    "candidateEventRunCount": 3,
                    "distinctSymbolCount": 4,
                    "failureCount": 0,
                    "shadowDeliveryAuthorizedRunCount": 0,
                    "durationP95Ms": 1200,
                    "queueWaitP95Ms": 180000,
                    "latestCompletedAt": "2099-01-01T00:00:00Z",
                    "pendingCount": self.pending_count,
                    "oldestPendingAgeSeconds": self.oldest_pending_age_seconds,
                }

        platform = ReasoningEnginePlatformService(
            Registry(),
            {"reasoningEngineV2IndependentEnabled": "1"},
            independent_job_store=Jobs(),
        )

        blocked = platform.promotion_readiness("ontology-v2-shadow")
        approved = platform.promotion_readiness(
            "ontology-v2-shadow",
            allow_recovered_queue_wait=True,
        )

        self.assertIn("candidate-queue-wait-slo-breached", blocked["blockers"])
        self.assertTrue(approved["ready"])
        self.assertTrue(approved["recoveredQueueWaitOverrideApplied"])
        self.assertIn(
            "historical-queue-wait-slo-breached-but-current-queue-within-slo",
            approved["warnings"],
        )

        live_healthy_backlog = ReasoningEnginePlatformService(
            Registry(),
            {"reasoningEngineV2IndependentEnabled": "1"},
            independent_job_store=Jobs(
                pending_count=1,
                oldest_pending_age_seconds=30,
            ),
        ).promotion_readiness(
            "ontology-v2-shadow",
            allow_recovered_queue_wait=True,
        )
        self.assertTrue(live_healthy_backlog["ready"])
        self.assertTrue(live_healthy_backlog["recoveredQueueWaitOverrideApplied"])

        stale_backlog = ReasoningEnginePlatformService(
            Registry(),
            {"reasoningEngineV2IndependentEnabled": "1"},
            independent_job_store=Jobs(
                pending_count=1,
                oldest_pending_age_seconds=61,
            ),
        ).promotion_readiness(
            "ontology-v2-shadow",
            allow_recovered_queue_wait=True,
        )
        self.assertIn("candidate-queue-wait-slo-breached", stale_backlog["blockers"])

    def test_independent_v2_gate_uses_decision_synthesis_not_notification_novelty(self):
        class Registry:
            def get(self, deployment_id):
                return {
                    "deploymentId": deployment_id,
                    "status": "shadow",
                    "health": {"status": "ready", "independentExecution": True},
                    "releaseBundle": {},
                }

        class Jobs:
            def summary(self, deployment_id, lookback=200):
                del deployment_id, lookback
                return {
                    "sampleCount": 8,
                    "successfulRunCount": 8,
                    "traceCompleteRunCount": 8,
                    "decisionSynthesisRunCount": 3,
                    "candidateEventRunCount": 0,
                    "distinctSymbolCount": 4,
                    "failureCount": 0,
                    "shadowDeliveryAuthorizedRunCount": 0,
                    "durationP95Ms": 1200,
                    "queueWaitP95Ms": 0,
                    "endToEndP95Ms": 1200,
                    "latestCompletedAt": "2099-01-01T00:00:00Z",
                    "pendingCount": 0,
                    "oldestPendingAgeSeconds": 0,
                }

        readiness = ReasoningEnginePlatformService(
            Registry(),
            {"reasoningEngineV2IndependentEnabled": "1"},
            independent_job_store=Jobs(),
        ).promotion_readiness("ontology-v2-shadow")

        self.assertTrue(readiness["ready"])
        self.assertEqual(3, readiness["independentExecution"]["decisionSynthesisRunCount"])
        self.assertEqual(0, readiness["independentExecution"]["candidateEventRunCount"])

    def test_current_status_exposes_only_active_v2_and_unique_run_metrics(self):
        platform = ReasoningEnginePlatformService(object(), {
            "ontologyReasoningQueueCriticalAgeMinutes": "5",
        })
        state = {
            "control": {
                "active_deployment_id": "v2-r24",
                "delivery_deployment_id": "v2-r24",
                "candidate_deployment_id": "v2-r23",
            },
            "deployments": [
                {
                    "deploymentId": "v1-retired",
                    "engineVersion": "v1",
                    "status": "retired",
                    "health": {"lastResult": {"status": "critical"}},
                },
                {
                    "deploymentId": "v2-r24",
                    "engineVersion": "v2",
                    "status": "active",
                    "releaseBundle": {"release_id": "release-r24"},
                    "health": {
                        "graphWriter": {
                            "singleWriter": True,
                            "role": "delivery",
                            "processId": 1234,
                            "acquired": True,
                        },
                        "schemaFunctionReadiness": {
                            "status": "provisioning",
                            "functionsReady": False,
                            "directTypeqlFallbackReady": True,
                        },
                        "runtimeOntologyRelease": {
                            "status": "ready",
                            "catalogSource": "frozen-v2-release",
                            "ruleCount": 118,
                            "sharedRuleCount": 104,
                            "overlayRuleCount": 116,
                            "tboxSource": "frozen-v2-release",
                            "warmed": True,
                        },
                        "lastResult": {
                            "status": "ok",
                            "request_id": "request-1",
                            "duration_ms": 32000,
                            "trace_complete": True,
                            "symbols": ["005930"],
                        },
                    },
                },
                {
                    "deploymentId": "v2-r23",
                    "engineVersion": "v2",
                    "status": "candidate",
                },
            ],
            "independentQueue": {
                "deploymentId": "v2-r24",
                "counts": {"completed": 24},
                "uniqueCompletedRunCount": 16,
                "successfulRunCount": 16,
                "traceCompleteRunCount": 16,
                "pendingCount": 0,
            },
        }

        result = platform.current_status(state)

        self.assertEqual("ready", result["status"])
        self.assertEqual("v2-r24", result["activeDeployment"]["deploymentId"])
        self.assertEqual(16, result["queue"]["uniqueCompletedRunCount"])
        self.assertEqual({"completed": 24}, result["queue"]["jobRowCounts"])
        self.assertNotIn("deployments", result)
        self.assertEqual([], result["reasons"])
        self.assertEqual(
            "typedb-direct-typeql",
            result["activeDeployment"]["ruleExecutionReadiness"]["mode"],
        )
        self.assertEqual(
            {
                "status": "ready",
                "catalogSource": "frozen-v2-release",
                "ruleCount": 118,
                "sharedRuleCount": 104,
                "overlayRuleCount": 116,
                "tboxSource": "frozen-v2-release",
                "warmed": True,
            },
            result["activeDeployment"]["runtimeOntologyRelease"],
        )
        self.assertEqual("single-process", result["writerTopology"]["mode"])
        self.assertEqual(1234, result["writerTopology"]["owner"]["processId"])


if __name__ == "__main__":
    unittest.main()
