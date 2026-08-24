import unittest
from copy import deepcopy
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
    def test_v2_release_preflight_migrates_rulebox_before_freezing_snapshot(self):
        from digital_twin.infrastructure.ontology_projection import bootstrap_rule_catalog
        from digital_twin.infrastructure.service_factory import prepare_v2_rulebox_release

        rules = deepcopy(bootstrap_rule_catalog()["rules"])
        rules[0]["knowledge_basis"]["ownershipContractVersion"] = "stale-contract"

        class Repository:
            def __init__(self):
                self.rules = rules
                self.calls = []

            def rulebox_snapshot(self):
                self.calls.append("snapshot")
                return {
                    "configured": True,
                    "status": "ok",
                    "rules": deepcopy(self.rules),
                    "ruleCount": len(self.rules),
                }

            def save_rulebox(self, payload):
                self.calls.append("save")
                self.rules = deepcopy(payload["rules"])
                return {"saved": True, "status": "ok", "ruleCount": len(self.rules)}

            def active_tbox_metadata(self):
                from digital_twin.domain.ontology_schema import default_tbox_metadata

                return {**default_tbox_metadata(), "status": "ok", "source": "test"}

        repository = Repository()

        snapshot, readiness = prepare_v2_rulebox_release(repository, {})

        self.assertEqual("ready", readiness["status"])
        self.assertEqual("matched", readiness["tboxReleasePreflight"]["status"])
        self.assertEqual("matched", readiness["modelSignalReleasePreflight"]["status"])
        self.assertEqual("migrated", readiness["ruleCatalogMigration"]["status"])
        self.assertEqual(["snapshot", "save", "snapshot", "snapshot"], repository.calls)
        self.assertEqual(
            "ontology-rule-ownership-v1",
            snapshot["rules"][0]["knowledge_basis"]["ownershipContractVersion"],
        )

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

    def test_deployments_bind_to_separate_graph_databases(self):
        class Registry:
            rows = {}

            def upsert(self, item):
                self.rows[item.deployment_id] = item.to_dict()

            def get(self, deployment_id):
                return self.rows.get(deployment_id, {})

        registry = Registry()
        platform = ReasoningEnginePlatformService(
            registry,
            {
                "reasoningEngineV1TypeDbDatabase": "ontology-v1-blue",
                "reasoningEngineV2TypeDbDatabase": "ontology-v2-green",
            },
        )
        for item in platform.descriptors():
            registry.upsert(item)

        self.assertEqual("ontology-v1-blue", platform.graph_database_for("ontology-v1-active"))
        self.assertEqual("ontology-v2-green", platform.graph_database_for("ontology-v2-shadow"))
        self.assertEqual("v2", platform.engine_version_for("ontology-v2-shadow"))

    def test_active_v2_uses_the_active_time_series_backend(self):
        class Registry:
            def control(self):
                return EngineControlState(
                    active_deployment_id="ontology-v2-production-r15",
                    delivery_deployment_id="ontology-v2-production-r15",
                    candidate_deployment_id="ontology-v2-production-r14",
                    version=8,
                )

        platform = ReasoningEnginePlatformService(Registry(), {
            "reasoningEngineV2DeploymentId": "ontology-v2-production-r15",
            "timeSeriesActiveBackendId": "questdb-shadow",
            "timeSeriesShadowBackendId": "mysql-primary",
        })

        current = next(item for item in platform.descriptors() if item.engine_version == "v2")

        self.assertEqual("questdb-shadow", current.time_series_backend_id)
        self.assertTrue(current.capabilities["productionDelivery"])
        self.assertFalse(current.capabilities["shadowComparison"])

    def test_candidate_v2_keeps_the_shadow_time_series_backend(self):
        class Registry:
            def control(self):
                return EngineControlState(
                    active_deployment_id="ontology-v2-production-r15",
                    delivery_deployment_id="ontology-v2-production-r15",
                    candidate_deployment_id="ontology-v2-production-r16",
                    version=9,
                )

        platform = ReasoningEnginePlatformService(Registry(), {
            "reasoningEngineV2DeploymentId": "ontology-v2-production-r16",
            "timeSeriesActiveBackendId": "questdb-shadow",
            "timeSeriesShadowBackendId": "mysql-primary",
        })

        candidate = next(item for item in platform.descriptors() if item.engine_version == "v2")

        self.assertEqual("mysql-primary", candidate.time_series_backend_id)
        self.assertFalse(candidate.capabilities["productionDelivery"])
        self.assertTrue(candidate.capabilities["shadowComparison"])

    def test_registered_descriptor_keeps_its_backend_after_control_promotion(self):
        class Registry:
            def __init__(self):
                self.row = descriptor().to_dict()
                self.row["deploymentId"] = "v2-r22"
                self.row["timeSeriesBackendId"] = "mysql-primary"
                self.row["status"] = "active"

            def get(self, deployment_id):
                return self.row if deployment_id == "v2-r22" else {}

            def control(self):
                return EngineControlState("v2-r22", "v2-r22", "v2-r20", 48)

        platform = ReasoningEnginePlatformService(
            Registry(),
            {
                "reasoningEngineV2DeploymentId": "v2-r22",
                "timeSeriesActiveBackendId": "questdb-shadow",
                "timeSeriesShadowBackendId": "mysql-primary",
            },
        )

        frozen = platform.deployment_descriptor("v2-r22")

        self.assertEqual("mysql-primary", frozen.time_series_backend_id)
        self.assertEqual(
            descriptor().release_bundle.runtime_revision,
            frozen.release_bundle.runtime_revision,
        )

    def test_initialize_repairs_persisted_capabilities_from_control_authority(self):
        class Registry:
            def __init__(self):
                self.rows = {
                    "ontology-v2-production-r17": {
                        "deploymentId": "ontology-v2-production-r17",
                        "engineVersion": "v2",
                        "status": "candidate",
                        "capabilities": {
                            "productionDelivery": True,
                            "shadowComparison": False,
                        },
                    },
                    "ontology-v2-production-r20": {
                        "deploymentId": "ontology-v2-production-r20",
                        "engineVersion": "v2",
                        "status": "active",
                        "capabilities": {
                            "productionDelivery": False,
                            "shadowComparison": True,
                        },
                    },
                }
                self.control_value = EngineControlState(
                    "ontology-v2-production-r20",
                    "ontology-v2-production-r20",
                    "ontology-v2-production-r17",
                    45,
                )

            def upsert(self, item):
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
        state = ReasoningEnginePlatformService(
            registry,
            {
                "reasoningEngineV2DeploymentId": "ontology-v2-production-r20",
                "reasoningEngineV1DeploymentId": "ontology-v1-active",
            },
        ).initialize()

        self.assertTrue(
            registry.rows["ontology-v2-production-r20"]["capabilities"]["productionDelivery"]
        )
        self.assertFalse(
            registry.rows["ontology-v2-production-r20"]["capabilities"]["shadowComparison"]
        )
        self.assertFalse(
            registry.rows["ontology-v2-production-r17"]["capabilities"]["productionDelivery"]
        )
        self.assertTrue(
            registry.rows["ontology-v2-production-r17"]["capabilities"]["shadowComparison"]
        )
        self.assertEqual(
            ["ontology-v2-production-r20", "ontology-v2-production-r17"],
            state["controlCapabilitySync"]["updatedDeploymentIds"],
        )

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

    def test_activate_synchronizes_new_active_and_rollback_candidate_capabilities(self):
        class Registry:
            def __init__(self):
                self.rows = {
                    "v2-r20": {
                        "deploymentId": "v2-r20",
                        "status": "active",
                        "capabilities": {
                            "productionDelivery": True,
                            "shadowComparison": False,
                        },
                    },
                    "v2-r21": {
                        "deploymentId": "v2-r21",
                        "status": "candidate",
                        "capabilities": {
                            "productionDelivery": False,
                            "shadowComparison": True,
                        },
                    },
                }
                self.control_value = EngineControlState("v2-r20", "v2-r20", "v2-r21", 7)

            def get(self, deployment_id):
                return self.rows.get(deployment_id, {})

            def control(self):
                return self.control_value

            def transition(self, deployment_id, status):
                self.rows[deployment_id]["status"] = status
                return self.rows[deployment_id]

            def set_control(self, active, delivery, candidate, expected_version=None):
                self.assert_expected_version = expected_version
                self.control_value = EngineControlState(active, delivery, candidate, 8)
                return self.control_value

            def update_capabilities(self, deployment_id, capabilities):
                self.rows[deployment_id]["capabilities"] = dict(capabilities)

        registry = Registry()
        result = ReasoningEnginePlatformService(registry, {}).activate(
            "v2-r21",
            {"ready": True},
        )

        self.assertEqual("v2-r21", result["control"]["active_deployment_id"])
        self.assertEqual(7, registry.assert_expected_version)
        self.assertTrue(registry.rows["v2-r21"]["capabilities"]["productionDelivery"])
        self.assertFalse(registry.rows["v2-r21"]["capabilities"]["shadowComparison"])
        self.assertFalse(registry.rows["v2-r20"]["capabilities"]["productionDelivery"])
        self.assertTrue(registry.rows["v2-r20"]["capabilities"]["shadowComparison"])
        self.assertEqual(
            ["v2-r21", "v2-r20"],
            result["controlCapabilitySync"]["updatedDeploymentIds"],
        )

    def test_initialize_repoints_an_obsolete_candidate_to_the_configured_v2_release(self):
        class Registry:
            def __init__(self):
                self.rows = {}
                self.control_value = EngineControlState(
                    active_deployment_id="ontology-v1-active",
                    delivery_deployment_id="ontology-v1-active",
                    candidate_deployment_id="ontology-v2-shadow-r2",
                    version=3,
                )

            def upsert(self, item):
                self.rows[item.deployment_id] = item.to_dict()

            def get(self, deployment_id):
                return self.rows.get(deployment_id, {})

            def list(self):
                return list(self.rows.values())

            def control(self):
                return self.control_value

            def set_control(self, active, delivery, candidate):
                self.control_value = EngineControlState(
                    active_deployment_id=active,
                    delivery_deployment_id=delivery,
                    candidate_deployment_id=candidate,
                    version=4,
                )
                return self.control_value

        registry = Registry()
        platform = ReasoningEnginePlatformService(registry, {
            "reasoningEngineV2DeploymentId": "ontology-v2-shadow-r3",
            "reasoningEngineCandidateDeploymentId": "ontology-v2-shadow-r3",
        })

        state = platform.initialize()

        self.assertEqual(
            "ontology-v2-shadow-r3",
            state["control"]["candidate_deployment_id"],
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

    def test_register_v2_release_keeps_active_delivery_and_moves_candidate(self):
        class Registry:
            def __init__(self):
                self.rows = {
                    "ontology-v2-production-r13": {
                        "deploymentId": "ontology-v2-production-r13",
                        "status": "active",
                    }
                }
                self.control_value = EngineControlState(
                    active_deployment_id="ontology-v2-production-r13",
                    delivery_deployment_id="ontology-v2-production-r13",
                    candidate_deployment_id="ontology-v1-active",
                    version=11,
                )

            def upsert(self, item):
                self.rows[item.deployment_id] = item.to_dict()

            def get(self, deployment_id):
                return self.rows.get(deployment_id, {})

            def control(self):
                return self.control_value

            def set_control(self, active, delivery, candidate, expected_version=None):
                self.assert_expected_version = expected_version
                self.control_value = EngineControlState(active, delivery, candidate, 12)
                return self.control_value

            def update_capabilities(self, deployment_id, capabilities):
                self.rows[deployment_id]["capabilities"] = dict(capabilities)

        registry = Registry()
        platform = ReasoningEnginePlatformService(registry, {})

        result = platform.register_v2_release(
            "ontology-v2-production-r14",
            "ontology-v2-release-r14",
            graph_database="orbit-alpha-v2",
        )

        self.assertEqual("registered", result["status"])
        self.assertEqual("ontology-v2-production-r13", result["control"]["active_deployment_id"])
        self.assertEqual("ontology-v2-production-r13", result["control"]["delivery_deployment_id"])
        self.assertEqual("ontology-v2-production-r14", result["control"]["candidate_deployment_id"])
        self.assertEqual(11, registry.assert_expected_version)
        self.assertEqual(
            "ontology-v2-release-r14",
            result["deployment"]["releaseBundle"]["release_id"],
        )
        self.assertEqual("orbit-alpha-v2", result["deployment"]["graphStoreBinding"])
        self.assertFalse(result["deployment"]["capabilities"]["productionDelivery"])
        self.assertTrue(result["deployment"]["capabilities"]["shadowComparison"])

    def test_active_v2_status_reads_the_configured_worker_queue_not_rollback_queue(self):
        class Registry:
            def __init__(self):
                self.rows = {
                    "v2-r14": {"deploymentId": "v2-r14", "engineVersion": "v2", "status": "candidate"},
                    "v2-r15": {"deploymentId": "v2-r15", "engineVersion": "v2", "status": "active", "health": {"status": "ready"}},
                }

            def upsert(self, item):
                self.rows.setdefault(item.deployment_id, item.to_dict())

            def get(self, deployment_id):
                return self.rows.get(deployment_id, {})

            def list(self):
                return list(self.rows.values())

            def control(self):
                return EngineControlState("v2-r15", "v2-r15", "v2-r14", 4)

        class Jobs:
            def summary(self, deployment_id, lookback=200, **kwargs):
                del lookback, kwargs
                return {"deploymentId": deployment_id, "pendingCount": 0}

        platform = ReasoningEnginePlatformService(
            Registry(),
            {
                "reasoningEngineV2DeploymentId": "v2-r15",
                "reasoningEngineV1DeploymentId": "v1",
            },
            independent_job_store=Jobs(),
        )

        state = platform.initialize()

        self.assertEqual("v2-r15", state["independentDeploymentId"])
        self.assertEqual("v2-r15", state["independentQueue"]["deploymentId"])
        self.assertEqual("active-v2", state["promotionReadiness"]["mode"])

    def test_cli_promotion_switches_control_and_active_graph_database_together(self):
        from digital_twin.infrastructure.cli import reasoning_engine_platform_command

        class Platform:
            comparison_store = None

            def initialize(self):
                return {
                    "control": {"active_deployment_id": "ontology-v1-active"},
                }

            def engine_version_for(self, deployment_id):
                return "v2" if deployment_id == "ontology-v2-shadow" else "v1"

            def graph_database_for(self, deployment_id):
                self.last_database_deployment = deployment_id
                return "ontology-v2-green"

            def promote_from_history(self, deployment_id):
                return {
                    "status": "promoted",
                    "control": {
                        "active_deployment_id": deployment_id,
                        "delivery_deployment_id": deployment_id,
                        "candidate_deployment_id": "ontology-v1-active",
                    },
                }

        platform = Platform()
        saved = {}
        args = SimpleNamespace(
            reasoning_engine_action="promote",
            deployment_id="ontology-v2-shadow",
        )
        with patch(
            "digital_twin.infrastructure.reasoning_engine_factory.build_reasoning_engine_platform",
            return_value=platform,
        ), patch(
            "digital_twin.infrastructure.cli.runtime_settings",
            return_value={"typedbDatabase": "ontology-v1-blue"},
        ), patch(
            "digital_twin.infrastructure.cli.save_runtime_settings",
            side_effect=lambda values: saved.update(values),
        ):
            status = reasoning_engine_platform_command(args)

        self.assertEqual(0, status)
        self.assertEqual("v2", saved["reasoningEngineActiveVersion"])
        self.assertEqual("ontology-v2-green", saved["typedbDatabase"])
        self.assertEqual("ontology-v1-blue", saved["reasoningEngineV1TypeDbDatabase"])

    def test_release_identity_changes_with_runtime_or_rulebox(self):
        first = reasoning_release_identity(descriptor(), "rules-a")
        second = reasoning_release_identity(descriptor(), "rules-a")
        changed_rules = reasoning_release_identity(descriptor(), "rules-b")
        changed_runtime_descriptor = ReasoningEngineDescriptor(
            **{
                **descriptor().__dict__,
                "release_bundle": EngineReleaseBundle(
                    "tbox-v1", "rulebox-v1", "prompt-v1", "features-v1",
                    runtime_revision="revision-b",
                ),
            }
        )
        changed_runtime = reasoning_release_identity(changed_runtime_descriptor, "rules-a")

        self.assertEqual(first, second)
        self.assertNotEqual(first["releaseFingerprint"], changed_rules["releaseFingerprint"])
        self.assertNotEqual(first["releaseFingerprint"], changed_runtime["releaseFingerprint"])
        self.assertNotEqual(first["releaseId"], changed_runtime["releaseId"])
        self.assertEqual(first["baseReleaseId"], changed_runtime["baseReleaseId"])
        self.assertTrue(first["validationCohortId"].startswith("reasoning-cohort:"))

    def test_shadow_must_become_candidate_before_active(self):
        self.assertTrue(engine_transition_allowed("shadow", "candidate"))
        self.assertFalse(engine_transition_allowed("shadow", "active"))

    def test_promotion_requires_full_parity_and_zero_shadow_delivery(self):
        ready = {
            "factParityPct": 100,
            "ruleSlotCoveragePct": 100,
            "unexplainedDecisionDifferenceCount": 0,
            "shadowDeliveryCount": 0,
        }
        self.assertEqual((), promotion_blockers(descriptor(), {"status": "ready"}, ready))

        unsafe = dict(ready, factParityPct=99.5, shadowDeliveryCount=1)
        self.assertEqual(
            ("fact-parity-incomplete", "shadow-delivery-detected"),
            promotion_blockers(descriptor(), {"status": "ready"}, unsafe),
        )

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

    def test_independent_v2_gate_uses_its_own_runs_instead_of_v1_parity(self):
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
                    "candidateEventRunCount": 3,
                    "distinctSymbolCount": 4,
                    "failureCount": 0,
                    "shadowDeliveryAuthorizedRunCount": 0,
                    "durationP95Ms": 1200,
                    "queueWaitP95Ms": 50,
                    "latestCompletedAt": "2099-01-01T00:00:00Z",
                    "oldestPendingAgeSeconds": 0,
                }

        platform = ReasoningEnginePlatformService(
            Registry(),
            {
                "reasoningEngineV2IndependentEnabled": "1",
                "reasoningEngineV2PromotionMinimumRuns": "5",
                "reasoningEngineV2PromotionMinimumSymbols": "3",
            },
            comparison_store=None,
            independent_job_store=Jobs(),
        )

        readiness = platform.promotion_readiness("ontology-v2-shadow")

        self.assertTrue(readiness["ready"])
        self.assertEqual("independent-v2", readiness["mode"])
        self.assertEqual([], readiness["blockers"])
        self.assertEqual(1250, readiness["endToEndP95Ms"])

    def test_independent_v2_gate_blocks_slow_end_to_end_delivery(self):
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
                    "candidateEventRunCount": 3,
                    "distinctSymbolCount": 4,
                    "failureCount": 0,
                    "shadowDeliveryAuthorizedRunCount": 0,
                    "durationP95Ms": 50000,
                    "queueWaitP95Ms": 50000,
                    "endToEndP95Ms": 100000,
                    "latestCompletedAt": "2099-01-01T00:00:00Z",
                    "pendingCount": 0,
                    "oldestPendingAgeSeconds": 0,
                }

        readiness = ReasoningEnginePlatformService(
            Registry(),
            {
                "reasoningEngineV2IndependentEnabled": "1",
                "reasoningEnginePromotionMaximumEndToEndP95Ms": "90000",
            },
            independent_job_store=Jobs(),
        ).promotion_readiness("ontology-v2-shadow")

        self.assertIn("candidate-end-to-end-latency-slo-breached", readiness["blockers"])

    def test_independent_v2_gate_can_approve_only_a_drained_historical_queue_wait(self):
        class Registry:
            def get(self, deployment_id):
                return {
                    "deploymentId": deployment_id,
                    "status": "shadow",
                    "health": {"status": "ready", "independentExecution": True},
                    "releaseBundle": {},
                }

        class Jobs:
            def __init__(self, pending_count=0):
                self.pending_count = pending_count

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
                    "oldestPendingAgeSeconds": 0,
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
            "historical-queue-wait-slo-breached-but-current-queue-drained",
            approved["warnings"],
        )

        active_backlog = ReasoningEnginePlatformService(
            Registry(),
            {"reasoningEngineV2IndependentEnabled": "1"},
            independent_job_store=Jobs(pending_count=1),
        ).promotion_readiness(
            "ontology-v2-shadow",
            allow_recovered_queue_wait=True,
        )
        self.assertIn("candidate-queue-wait-slo-breached", active_backlog["blockers"])

    def test_mark_candidate_advances_a_healthy_provisioning_release(self):
        class Registry:
            def __init__(self):
                self.status = "provisioning"

            def get(self, deployment_id):
                return {
                    "deploymentId": deployment_id,
                    "status": self.status,
                    "health": {"status": "ready", "independentExecution": True},
                    "releaseBundle": {},
                }

            def transition(self, deployment_id, status):
                del deployment_id
                self.status = status
                return self.get("ontology-v2-shadow")

        class Jobs:
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
                    "queueWaitP95Ms": 50,
                    "latestCompletedAt": "2099-01-01T00:00:00Z",
                    "pendingCount": 0,
                    "oldestPendingAgeSeconds": 0,
                }

        registry = Registry()
        platform = ReasoningEnginePlatformService(
            registry,
            {"reasoningEngineV2IndependentEnabled": "1"},
            independent_job_store=Jobs(),
        )

        result = platform.mark_candidate("ontology-v2-shadow")

        self.assertEqual("candidate", result["status"])
        self.assertEqual("candidate", registry.status)

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
                        "schemaFunctionReadiness": {
                            "status": "provisioning",
                            "functionsReady": False,
                            "directTypeqlFallbackReady": True,
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

        self.assertEqual("degraded", result["status"])
        self.assertEqual("v2-r24", result["activeDeployment"]["deploymentId"])
        self.assertEqual(16, result["queue"]["uniqueCompletedRunCount"])
        self.assertEqual({"completed": 24}, result["queue"]["jobRowCounts"])
        self.assertNotIn("deployments", result)
        self.assertIn(
            "schema-functions-not-ready-direct-typeql-fallback",
            result["reasons"],
        )


if __name__ == "__main__":
    unittest.main()
