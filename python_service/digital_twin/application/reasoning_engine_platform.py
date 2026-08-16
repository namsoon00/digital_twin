"""Versioned reasoning-engine release registration and guarded switching."""

import inspect
from datetime import datetime, timezone
from typing import Dict, Iterable, Mapping

from ..domain.reasoning_engine_versions import (
    EngineReleaseBundle,
    ReasoningEngineDescriptor,
    promotion_blockers,
    reasoning_release_identity,
)
from ..domain.time_series_storage import TEMPORAL_FEATURE_SET_VERSION


class ReasoningEnginePlatformService:
    def __init__(
        self,
        registry,
        settings=None,
        comparison_store=None,
        shadow_queue=None,
        independent_job_store=None,
    ):
        self.registry = registry
        self.settings = dict(settings or {})
        self.comparison_store = comparison_store
        self.shadow_queue = shadow_queue
        self.independent_job_store = independent_job_store

    def independent_v2_enabled(self) -> bool:
        return str(
            self.settings.get("reasoningEngineV2IndependentEnabled") or "1"
        ).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def int_setting(self, key: str, fallback: int, lower: int = 0, upper: int = 100000) -> int:
        try:
            value = int(float(str(self.settings.get(key) or fallback)))
        except (TypeError, ValueError):
            value = fallback
        return max(lower, min(upper, value))

    def float_setting(self, key: str, fallback: float, lower: float = 0.0, upper: float = 100000.0) -> float:
        try:
            value = float(str(self.settings.get(key) or fallback))
        except (TypeError, ValueError):
            value = fallback
        return max(lower, min(upper, value))

    def independent_queue_summary(
        self,
        deployment_id: str,
        release: Mapping[str, object],
    ) -> Dict[str, object]:
        lookback = self.int_setting("reasoningEnginePromotionComparisonLookback", 200, 1, 2000)
        parameters = inspect.signature(self.independent_job_store.summary).parameters
        if "release_fingerprint" not in parameters:
            return self.independent_job_store.summary(deployment_id, lookback=lookback)
        return self.independent_job_store.summary(
            deployment_id,
            lookback=lookback,
            release_fingerprint=str(release.get("releaseFingerprint") or ""),
            validation_cohort_id=str(release.get("validationCohortId") or ""),
        )

    def descriptors(self):
        from ..domain.ontology_rulebox_release_manifest import RULEBOX_RELEASE_MANIFEST_VERSION
        from ..domain.ontology_schema import ONTOLOGY_TBOX_VERSION
        from ..infrastructure.typedb_ontology import TYPEDB_NATIVE_RULE_ENGINE_VERSION

        active_backend = str(self.settings.get("timeSeriesActiveBackendId") or "mysql-primary")
        v1_graph_database = str(
            self.settings.get("reasoningEngineV1TypeDbDatabase")
            or self.settings.get("typedbDatabase")
            or "orbit_alpha_ontology"
        )
        v2_graph_database = str(
            self.settings.get("reasoningEngineV2TypeDbDatabase")
            or self.settings.get("reasoningEngineShadowTypeDbDatabase")
            or "orbit_alpha_ontology_shadow_v2"
        )
        runtime = dict(self.settings.get("_runtimeIdentity") or {})
        common_bundle_values = dict(
            tbox_release_id=ONTOLOGY_TBOX_VERSION,
            rulebox_release_id=RULEBOX_RELEASE_MANIFEST_VERSION,
            prompt_release_id="investment-notification-prompt-registry-current",
            feature_set_version=TEMPORAL_FEATURE_SET_VERSION,
            source_contract_versions=(
                "typedb-semantic-storage-v2",
                TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                "time-series-storage-contract-v1",
            ),
            runtime_revision=str(runtime.get("revision") or "unknown"),
        )
        return [
            ReasoningEngineDescriptor(
                engine_family="ontology-investment-brain",
                engine_version="v1",
                deployment_id=str(self.settings.get("reasoningEngineV1DeploymentId") or "ontology-v1-active"),
                status="active",
                graph_store_binding=v1_graph_database,
                time_series_backend_id=active_backend,
                release_bundle=EngineReleaseBundle(
                    **common_bundle_values,
                    release_id=str(self.settings.get("reasoningEngineActiveReleaseId") or "ontology-v1-release-r2"),
                ),
                capabilities={
                    "typedbNativeInference": True,
                    "productionDelivery": True,
                    "shadowComparison": False,
                    "versionedFeatureSnapshot": True,
                },
            ),
            ReasoningEngineDescriptor(
                engine_family="ontology-investment-brain",
                engine_version="v2",
                deployment_id=str(self.settings.get("reasoningEngineV2DeploymentId") or "ontology-v2-shadow"),
                status="provisioning",
                graph_store_binding=v2_graph_database,
                time_series_backend_id=str(self.settings.get("timeSeriesShadowBackendId") or "questdb-shadow"),
                release_bundle=EngineReleaseBundle(
                    **common_bundle_values,
                    release_id=str(self.settings.get("reasoningEngineCandidateReleaseId") or "ontology-v2-release-r2"),
                ),
                capabilities={
                    "typedbNativeInference": True,
                    "productionDelivery": False,
                    "shadowComparison": True,
                    "versionedFeatureSnapshot": True,
                    "independentExecution": self.independent_v2_enabled(),
                    "directSourceEvents": self.independent_v2_enabled(),
                    "monitorRunnerDependency": not self.independent_v2_enabled(),
                    "aiDecisionHandoff": True,
                },
            ),
        ]

    def graph_database_for(self, deployment_id: str) -> str:
        """Return the immutable TypeDB database binding for one deployment."""
        row = self.registry.get(str(deployment_id or ""))
        if not row:
            raise ValueError("Unknown reasoning engine deployment: " + str(deployment_id or ""))
        database = str(row.get("graphStoreBinding") or "").strip()
        if not database:
            raise RuntimeError(
                "Reasoning engine deployment has no TypeDB database binding: "
                + str(deployment_id or "")
            )
        return database

    def engine_version_for(self, deployment_id: str) -> str:
        row = self.registry.get(str(deployment_id or ""))
        if not row:
            raise ValueError("Unknown reasoning engine deployment: " + str(deployment_id or ""))
        return str(row.get("engineVersion") or "").strip().lower()

    def release_identity(self, deployment_id: str) -> Dict[str, str]:
        row = self.registry.get(deployment_id)
        health = dict(row.get("health") or {})
        bundle = dict(row.get("releaseBundle") or {})
        bundle_release_id = str(bundle.get("release_id") or bundle.get("releaseId") or deployment_id)
        bundle_runtime_revision = str(
            bundle.get("runtime_revision") or bundle.get("runtimeRevision") or "unknown"
        )
        health_matches_bundle = bool(
            str(health.get("candidateReleaseId") or "") == bundle_release_id
            and str(health.get("candidateRuntimeRevision") or "") == bundle_runtime_revision
        )
        if health_matches_bundle and str(
            health.get("candidateReleaseFingerprint") or health.get("releaseFingerprint") or ""
        ):
            return {
                "releaseId": str(
                    health.get("candidateReleaseId")
                    or bundle_release_id
                    or deployment_id
                ),
                "runtimeRevision": str(
                    health.get("candidateRuntimeRevision")
                    or bundle_runtime_revision
                    or "unknown"
                ),
                "ruleboxFingerprint": str(health.get("ruleboxFingerprint") or ""),
                "releaseFingerprint": str(
                    health.get("candidateReleaseFingerprint")
                    or health.get("releaseFingerprint")
                    or ""
                ),
                "validationCohortId": str(health.get("validationCohortId") or ""),
            }
        return reasoning_release_identity(row, health.get("ruleboxFingerprint") or "")

    def initialize(self) -> Dict[str, object]:
        descriptors = self.descriptors()
        for descriptor in descriptors:
            self.registry.upsert(descriptor)
        control = self.registry.control()
        known = {descriptor.deployment_id for descriptor in descriptors}
        active = control.active_deployment_id
        delivery = control.delivery_deployment_id
        candidate = control.candidate_deployment_id
        if active not in known or delivery not in known:
            active = str(self.settings.get("reasoningEngineActiveDeploymentId") or "ontology-v1-active")
            delivery = str(self.settings.get("reasoningEngineDeliveryDeploymentId") or active)
            candidate = str(self.settings.get("reasoningEngineCandidateDeploymentId") or "ontology-v2-shadow")
            if active not in known:
                active = str(self.settings.get("reasoningEngineV1DeploymentId") or "ontology-v1-active")
            if delivery not in known:
                delivery = active
            if candidate not in known or candidate in {active, delivery}:
                configured_v2 = str(self.settings.get("reasoningEngineV2DeploymentId") or "ontology-v2-shadow")
                candidate = configured_v2 if configured_v2 not in {active, delivery} else ""
            control = self.registry.set_control(active, delivery, candidate)
        response = {
            "control": control.to_dict(),
            "deployments": self.registry.list(),
        }
        candidate_id = str(control.candidate_deployment_id or "")
        candidate_release = self.release_identity(candidate_id) if candidate_id else {}
        if self.shadow_queue is not None:
            response["shadowQueue"] = self.shadow_queue.summary(
                candidate_id,
                str(candidate_release.get("releaseId") or ""),
                str(candidate_release.get("runtimeRevision") or ""),
            )
        if self.comparison_store is not None and candidate_id:
            response["comparisonSummary"] = self.comparison_store.summary(
                candidate_id,
                limit=self.int_setting("reasoningEnginePromotionComparisonLookback", 200, 1, 2000),
                candidate_release_fingerprint=str(candidate_release.get("releaseFingerprint") or ""),
                validation_cohort_id=str(candidate_release.get("validationCohortId") or ""),
            )
        if self.independent_job_store is not None and candidate_id:
            response["independentQueue"] = self.independent_queue_summary(
                candidate_id,
                candidate_release,
            )
        if candidate_id:
            response["promotionReadiness"] = self.promotion_readiness(candidate_id)
        return response

    @staticmethod
    def timestamp(value: object):
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def promotion_readiness(self, deployment_id: str) -> Dict[str, object]:
        if self.independent_v2_enabled() and self.independent_job_store is not None:
            return self.independent_promotion_readiness(deployment_id)
        row = self.registry.get(deployment_id)
        release = self.release_identity(deployment_id)
        summary = (
            self.comparison_store.summary(
                deployment_id,
                limit=self.int_setting("reasoningEnginePromotionComparisonLookback", 200, 1, 2000),
                candidate_release_fingerprint=str(release.get("releaseFingerprint") or ""),
                validation_cohort_id=str(release.get("validationCohortId") or ""),
            )
            if self.comparison_store is not None
            else {}
        )
        blockers = []
        if str(row.get("status") or "") not in {"shadow", "candidate"}:
            blockers.append("engine-not-shadow-or-candidate")
        health = dict(row.get("health") or {})
        if str(health.get("status") or "").lower() not in {"ready", "healthy"}:
            blockers.append("engine-unhealthy")
        if int(summary.get("sampleCount") or 0) < self.int_setting(
            "reasoningEnginePromotionMinimumComparisons", 100, 1, 10000
        ):
            blockers.append("insufficient-comparison-samples")
        if int(summary.get("distinctSymbolCount") or 0) < self.int_setting(
            "reasoningEnginePromotionMinimumSymbols", 10, 1, 10000
        ):
            blockers.append("insufficient-symbol-coverage")
        if int(summary.get("nonEmptyNativeInferenceSampleCount") or 0) < self.int_setting(
            "reasoningEnginePromotionMinimumNativeInferenceSamples", 30, 1, 10000
        ):
            blockers.append("insufficient-native-inference-coverage")
        if int(summary.get("nonEmptyDecisionSampleCount") or 0) < self.int_setting(
            "reasoningEnginePromotionMinimumDecisionSamples", 30, 1, 10000
        ):
            blockers.append("insufficient-decision-coverage")
        if int(summary.get("distinctMatchedRuleCount") or 0) < self.int_setting(
            "reasoningEnginePromotionMinimumMatchedRules", 5, 1, 10000
        ):
            blockers.append("insufficient-matched-rule-coverage")
        if int(summary.get("marketClassCount") or 0) < self.int_setting(
            "reasoningEnginePromotionMinimumMarketClasses", 2, 1, 10
        ):
            blockers.append("insufficient-market-coverage")
        if float(summary.get("minimumFactParityPct") or 0.0) < 100.0:
            blockers.append("fact-parity-incomplete")
        if float(summary.get("minimumRuleSlotCoveragePct") or 0.0) < 100.0:
            blockers.append("rule-slot-coverage-incomplete")
        if int(summary.get("unexplainedDecisionDifferenceCount") or 0) > 0:
            blockers.append("unexplained-decision-differences")
        if int(summary.get("shadowDeliveryCount") or 0) > 0:
            blockers.append("shadow-delivery-detected")
        execution_failures = int((summary.get("statusCounts") or {}).get("candidate-failed") or 0)
        if execution_failures > self.int_setting(
            "reasoningEnginePromotionMaximumExecutionFailures", 0, 0, 10000
        ):
            blockers.append("candidate-execution-failures")
        baseline_p95 = int(summary.get("baselineP95DurationMs") or 0)
        candidate_p95 = int(summary.get("candidateP95DurationMs") or 0)
        latency_ratio = round(candidate_p95 / baseline_p95, 3) if baseline_p95 > 0 else 0.0
        max_latency_ratio = self.float_setting(
            "reasoningEnginePromotionMaximumLatencyRatio", 3.0, 1.0, 100.0
        )
        if baseline_p95 > 0 and latency_ratio > max_latency_ratio:
            blockers.append("candidate-latency-regression")
        maximum_candidate_p95 = self.int_setting(
            "reasoningEnginePromotionMaximumCandidateP95Ms", 90000, 1000, 3600000
        )
        if candidate_p95 <= 0 or candidate_p95 > maximum_candidate_p95:
            blockers.append("candidate-absolute-latency-slo-breached")
        queue_wait_p95 = int(summary.get("queueWaitP95Ms") or 0)
        maximum_queue_wait_p95 = self.int_setting(
            "reasoningEnginePromotionMaximumQueueWaitP95Ms", 60000, 1000, 3600000
        )
        if queue_wait_p95 > maximum_queue_wait_p95:
            blockers.append("candidate-queue-wait-slo-breached")
        latest = self.timestamp(summary.get("latestComparisonAt"))
        maximum_age = self.int_setting(
            "reasoningEnginePromotionMaximumComparisonAgeSeconds", 3600, 60, 7 * 24 * 60 * 60
        )
        age_seconds = (
            max(0, int((datetime.now(timezone.utc) - latest).total_seconds()))
            if latest
            else None
        )
        if age_seconds is None or age_seconds > maximum_age:
            blockers.append("comparison-window-stale")
        return {
            "ready": not blockers,
            "deploymentId": str(deployment_id or ""),
            "blockers": list(dict.fromkeys(blockers)),
            "comparison": summary,
            "health": health,
            "latencyRatio": latency_ratio,
            "maximumLatencyRatio": max_latency_ratio,
            "release": release,
            "maximumCandidateP95Ms": maximum_candidate_p95,
            "maximumQueueWaitP95Ms": maximum_queue_wait_p95,
            "latestComparisonAgeSeconds": age_seconds,
            "maximumComparisonAgeSeconds": maximum_age,
        }

    def independent_promotion_readiness(self, deployment_id: str) -> Dict[str, object]:
        row = self.registry.get(deployment_id)
        health = dict(row.get("health") or {})
        release = self.release_identity(deployment_id)
        summary = self.independent_queue_summary(
            deployment_id,
            release,
        )
        blockers = []
        if str(row.get("status") or "") not in {"shadow", "candidate"}:
            blockers.append("engine-not-shadow-or-candidate")
        if str(health.get("status") or "").lower() not in {"ready", "healthy"}:
            blockers.append("engine-unhealthy")

        minimum_runs = self.int_setting(
            "reasoningEngineV2PromotionMinimumRuns", 5, 1, 10000
        )
        successful_runs = int(summary.get("successfulRunCount") or 0)
        if successful_runs < minimum_runs:
            blockers.append("insufficient-independent-runs")
        if int(summary.get("distinctSymbolCount") or 0) < self.int_setting(
            "reasoningEngineV2PromotionMinimumSymbols", 3, 1, 10000
        ):
            blockers.append("insufficient-symbol-coverage")
        if int(summary.get("candidateEventRunCount") or 0) < self.int_setting(
            "reasoningEngineV2PromotionMinimumCandidateRuns", 2, 1, 10000
        ):
            blockers.append("insufficient-decision-candidate-coverage")
        if int(summary.get("traceCompleteRunCount") or 0) < successful_runs:
            blockers.append("inference-trace-incomplete")
        if int(summary.get("failureCount") or 0) > self.int_setting(
            "reasoningEngineV2PromotionMaximumFailures", 0, 0, 10000
        ):
            blockers.append("independent-execution-failures")
        if int(summary.get("shadowDeliveryAuthorizedRunCount") or 0) > 0:
            blockers.append("shadow-delivery-detected")

        candidate_p95 = int(summary.get("durationP95Ms") or 0)
        maximum_candidate_p95 = self.int_setting(
            "reasoningEnginePromotionMaximumCandidateP95Ms", 90000, 1000, 3600000
        )
        if candidate_p95 <= 0 or candidate_p95 > maximum_candidate_p95:
            blockers.append("candidate-absolute-latency-slo-breached")
        queue_wait_p95 = int(summary.get("queueWaitP95Ms") or 0)
        maximum_queue_wait_p95 = self.int_setting(
            "reasoningEnginePromotionMaximumQueueWaitP95Ms", 60000, 1000, 3600000
        )
        if queue_wait_p95 > maximum_queue_wait_p95:
            blockers.append("candidate-queue-wait-slo-breached")

        latest = self.timestamp(summary.get("latestCompletedAt"))
        maximum_age = self.int_setting(
            "reasoningEnginePromotionMaximumComparisonAgeSeconds", 3600, 60, 7 * 24 * 60 * 60
        )
        age_seconds = (
            max(0, int((datetime.now(timezone.utc) - latest).total_seconds()))
            if latest
            else None
        )
        if age_seconds is None or age_seconds > maximum_age:
            blockers.append("independent-run-window-stale")
        if int(summary.get("oldestPendingAgeSeconds") or 0) > max(
            1, maximum_queue_wait_p95 // 1000
        ):
            blockers.append("independent-queue-stale")
        return {
            "ready": not blockers,
            "mode": "independent-v2",
            "deploymentId": str(deployment_id or ""),
            "blockers": list(dict.fromkeys(blockers)),
            "independentExecution": summary,
            "health": health,
            "release": release,
            "minimumSuccessfulRuns": minimum_runs,
            "maximumCandidateP95Ms": maximum_candidate_p95,
            "maximumQueueWaitP95Ms": maximum_queue_wait_p95,
            "latestRunAgeSeconds": age_seconds,
            "maximumRunAgeSeconds": maximum_age,
        }

    def mark_candidate(self, deployment_id: str) -> Dict[str, object]:
        readiness = self.promotion_readiness(deployment_id)
        if not readiness.get("ready"):
            return {
                "status": "blocked",
                "deploymentId": str(deployment_id or ""),
                "blockers": readiness.get("blockers") or [],
                "promotionReadiness": readiness,
            }
        row = self.registry.get(deployment_id)
        if str(row.get("status") or "") == "shadow":
            row = self.registry.transition(deployment_id, "candidate")
        return {"status": "candidate", "deployment": row, "promotionReadiness": readiness}

    def promote_from_history(self, deployment_id: str) -> Dict[str, object]:
        readiness = self.promotion_readiness(deployment_id)
        if not readiness.get("ready"):
            return {
                "status": "blocked",
                "deploymentId": str(deployment_id or ""),
                "blockers": readiness.get("blockers") or [],
                "promotionReadiness": readiness,
            }
        row = self.registry.get(deployment_id)
        if str(row.get("status") or "") != "candidate":
            return {
                "status": "blocked",
                "deploymentId": str(deployment_id or ""),
                "blockers": ["engine-not-candidate"],
                "promotionReadiness": readiness,
            }
        if readiness.get("mode") == "independent-v2":
            return self.activate(deployment_id, readiness)
        summary = dict(readiness.get("comparison") or {})
        summary.update({
            "factParityPct": summary.get("minimumFactParityPct"),
            "ruleSlotCoveragePct": summary.get("minimumRuleSlotCoveragePct"),
        })
        return self.promote(deployment_id, readiness.get("health") or {}, summary)

    def activate(self, deployment_id: str, readiness: Mapping[str, object]) -> Dict[str, object]:
        control = self.registry.control()
        previous = control.active_deployment_id
        if previous and previous != deployment_id:
            self.registry.transition(previous, "candidate")
        self.registry.transition(deployment_id, "active")
        next_control = self.registry.set_control(
            deployment_id,
            deployment_id,
            previous,
            expected_version=control.version,
        )
        return {
            "status": "promoted",
            "control": next_control.to_dict(),
            "promotionReadiness": dict(readiness or {}),
        }

    def promote(self, deployment_id: str, health: Mapping[str, object], comparison: Mapping[str, object]):
        row = self.registry.get(deployment_id)
        descriptor = next(
            (item for item in self.descriptors() if item.deployment_id == str(deployment_id or "")),
            None,
        )
        if not descriptor:
            raise ValueError("Unknown reasoning engine deployment: " + str(deployment_id or ""))
        descriptor = ReasoningEngineDescriptor(
            engine_family=descriptor.engine_family,
            engine_version=descriptor.engine_version,
            deployment_id=descriptor.deployment_id,
            status=str(row.get("status") or descriptor.status),
            graph_store_binding=descriptor.graph_store_binding,
            time_series_backend_id=descriptor.time_series_backend_id,
            release_bundle=descriptor.release_bundle,
            capabilities=descriptor.capabilities,
        )
        blockers = promotion_blockers(descriptor, health, comparison)
        if blockers:
            return {"status": "blocked", "deploymentId": deployment_id, "blockers": list(blockers)}
        control = self.registry.control()
        previous = control.active_deployment_id
        if previous and previous != deployment_id:
            self.registry.transition(previous, "candidate")
        self.registry.transition(deployment_id, "active")
        next_control = self.registry.set_control(
            deployment_id,
            deployment_id,
            previous,
            expected_version=control.version,
        )
        return {"status": "promoted", "control": next_control.to_dict()}

    def rollback(self) -> Dict[str, object]:
        control = self.registry.control()
        fallback = control.candidate_deployment_id
        if not fallback:
            raise ValueError("No rollback deployment is registered")
        fallback_row = self.registry.get(fallback)
        if str(fallback_row.get("status") or "") not in {"candidate", "active"}:
            return {
                "status": "blocked",
                "deploymentId": fallback,
                "blockers": ["rollback-deployment-not-previously-active"],
            }
        if control.active_deployment_id and control.active_deployment_id != fallback:
            self.registry.transition(control.active_deployment_id, "candidate")
        self.registry.transition(fallback, "active")
        next_control = self.registry.set_control(
            fallback,
            fallback,
            control.active_deployment_id,
            expected_version=control.version,
        )
        return {"status": "rolled-back", "control": next_control.to_dict()}
