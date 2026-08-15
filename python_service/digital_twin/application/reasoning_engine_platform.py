"""Versioned reasoning-engine release registration and guarded switching."""

from datetime import datetime, timezone
from typing import Dict, Iterable, Mapping

from ..domain.reasoning_engine_versions import (
    EngineReleaseBundle,
    ReasoningEngineDescriptor,
    promotion_blockers,
)
from ..domain.time_series_storage import TEMPORAL_FEATURE_SET_VERSION


class ReasoningEnginePlatformService:
    def __init__(self, registry, settings=None, comparison_store=None, shadow_queue=None):
        self.registry = registry
        self.settings = dict(settings or {})
        self.comparison_store = comparison_store
        self.shadow_queue = shadow_queue

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

    def descriptors(self):
        from ..domain.ontology_rulebox_release_manifest import RULEBOX_RELEASE_MANIFEST_VERSION
        from ..domain.ontology_schema import ONTOLOGY_TBOX_VERSION
        from ..infrastructure.typedb_ontology import TYPEDB_NATIVE_RULE_ENGINE_VERSION

        active_backend = str(self.settings.get("timeSeriesActiveBackendId") or "mysql-primary")
        common_bundle = EngineReleaseBundle(
            tbox_release_id=ONTOLOGY_TBOX_VERSION,
            rulebox_release_id=RULEBOX_RELEASE_MANIFEST_VERSION,
            prompt_release_id="investment-notification-prompt-registry-current",
            feature_set_version=TEMPORAL_FEATURE_SET_VERSION,
            source_contract_versions=("typedb-semantic-storage-v2", "time-series-storage-contract-v1"),
        )
        return [
            ReasoningEngineDescriptor(
                engine_family="ontology-investment-brain",
                engine_version="v1",
                deployment_id="ontology-v1-active",
                status="active",
                graph_store_binding=TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                time_series_backend_id=active_backend,
                release_bundle=common_bundle,
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
                deployment_id="ontology-v2-shadow",
                status="provisioning",
                graph_store_binding=TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                time_series_backend_id=str(self.settings.get("timeSeriesShadowBackendId") or "questdb-shadow"),
                release_bundle=common_bundle,
                capabilities={
                    "typedbNativeInference": True,
                    "productionDelivery": False,
                    "shadowComparison": True,
                    "versionedFeatureSnapshot": True,
                },
            ),
        ]

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
                active = "ontology-v1-active"
            if delivery not in known:
                delivery = active
            if candidate not in known or candidate in {active, delivery}:
                candidate = ""
            control = self.registry.set_control(active, delivery, candidate)
        response = {
            "control": control.to_dict(),
            "deployments": self.registry.list(),
        }
        candidate_id = str(control.candidate_deployment_id or "")
        if self.shadow_queue is not None:
            response["shadowQueue"] = self.shadow_queue.summary()
        if self.comparison_store is not None and candidate_id:
            response["comparisonSummary"] = self.comparison_store.summary(
                candidate_id,
                limit=self.int_setting("reasoningEnginePromotionComparisonLookback", 200, 1, 2000),
            )
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
        row = self.registry.get(deployment_id)
        summary = (
            self.comparison_store.summary(
                deployment_id,
                limit=self.int_setting("reasoningEnginePromotionComparisonLookback", 200, 1, 2000),
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
            "reasoningEnginePromotionMinimumComparisons", 20, 1, 10000
        ):
            blockers.append("insufficient-comparison-samples")
        if int(summary.get("distinctSymbolCount") or 0) < self.int_setting(
            "reasoningEnginePromotionMinimumSymbols", 5, 1, 10000
        ):
            blockers.append("insufficient-symbol-coverage")
        if float(summary.get("minimumFactParityPct") or 0.0) < 100.0:
            blockers.append("fact-parity-incomplete")
        if float(summary.get("minimumRuleSlotCoveragePct") or 0.0) < 100.0:
            blockers.append("rule-slot-coverage-incomplete")
        if int(summary.get("unexplainedDecisionDifferenceCount") or 0) > 0:
            blockers.append("unexplained-decision-differences")
        if int(summary.get("shadowDeliveryCount") or 0) > 0:
            blockers.append("shadow-delivery-detected")
        if int((summary.get("statusCounts") or {}).get("candidate-failed") or 0) > 0:
            blockers.append("candidate-execution-failures")
        baseline_p95 = int(summary.get("baselineP95DurationMs") or 0)
        candidate_p95 = int(summary.get("candidateP95DurationMs") or 0)
        latency_ratio = round(candidate_p95 / baseline_p95, 3) if baseline_p95 > 0 else 0.0
        max_latency_ratio = self.float_setting(
            "reasoningEnginePromotionMaximumLatencyRatio", 3.0, 1.0, 100.0
        )
        if baseline_p95 > 0 and latency_ratio > max_latency_ratio:
            blockers.append("candidate-latency-regression")
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
            "latestComparisonAgeSeconds": age_seconds,
            "maximumComparisonAgeSeconds": maximum_age,
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
        summary = dict(readiness.get("comparison") or {})
        summary.update({
            "factParityPct": summary.get("minimumFactParityPct"),
            "ruleSlotCoveragePct": summary.get("minimumRuleSlotCoveragePct"),
        })
        return self.promote(deployment_id, readiness.get("health") or {}, summary)

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
