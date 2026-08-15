"""Versioned reasoning-engine release registration and guarded switching."""

from typing import Dict, Iterable, Mapping

from ..domain.reasoning_engine_versions import (
    EngineReleaseBundle,
    ReasoningEngineDescriptor,
    promotion_blockers,
)
from ..domain.time_series_storage import TEMPORAL_FEATURE_SET_VERSION


class ReasoningEnginePlatformService:
    def __init__(self, registry, settings=None):
        self.registry = registry
        self.settings = dict(settings or {})

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
        return {
            "control": control.to_dict(),
            "deployments": self.registry.list(),
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
