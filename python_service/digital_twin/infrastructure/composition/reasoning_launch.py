"""Explicit V2 reasoning launch composition phase."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from digital_twin.modules.reasoning.domain.reasoning_engine_versions import ReasoningEngineDescriptor
    from digital_twin.modules.reasoning.public import ReasoningEnginePlatformService


@dataclass(frozen=True)
class ReasoningLaunch:
    configured: Dict[str, Any]
    platform: ReasoningEnginePlatformService
    descriptor: ReasoningEngineDescriptor
    deployment_health: Dict[str, Any]
    candidate_settings: Dict[str, Any]
    store_settings: Dict[str, Any]


def prepare_v2_launch(settings, deployment_id) -> ReasoningLaunch:
    from digital_twin.infrastructure.settings import runtime_settings

    configured = dict(settings or runtime_settings())
    from digital_twin.infrastructure.reasoning_engine_factory import build_reasoning_engine_platform

    platform = build_reasoning_engine_platform(configured)
    platform.initialize()
    deployment_id = str(
        deployment_id or configured.get("reasoningEngineV2DeploymentId") or "ontology-v2-shadow"
    ).strip()
    descriptor = platform.deployment_descriptor(deployment_id)
    if str(descriptor.engine_version or "").lower() != "v2":
        raise RuntimeError("The V2 reasoning deployment descriptor is unavailable")

    deployment_row = dict(platform.registry.get(descriptor.deployment_id) or {})
    deployment_health = dict(deployment_row.get("health") or {})
    candidate_settings = dict(configured)
    candidate_settings["typedbDatabase"] = platform.graph_database_for(descriptor.deployment_id)
    candidate_settings["timeSeriesActiveBackendId"] = descriptor.time_series_backend_id
    candidate_settings["typedbNativeRuleExecutionEnabled"] = "1"
    candidate_settings["ontologyReasoningTypeDbNativeRuleExecutionEnabled"] = "1"
    candidate_settings["ontologySharedMarketWorldAsyncProjectionEnabled"] = "0"
    candidate_settings["ontologyIncrementalCurrentStateReasoningEnabled"] = "1"
    candidate_settings["ontologyCurrentStateAboxStorageEnabled"] = "1"
    if str(
        candidate_settings.get("ontologyTemporalObservationAnchorProjectionEnabled") or "auto"
    ).strip().lower() in {"", "auto"}:
        # QuestDB owns historical points. Freeze V2's auto mode to compact
        # temporal summaries before the projection settings are serialized.
        candidate_settings["ontologyTemporalObservationAnchorProjectionEnabled"] = "0"
    candidate_settings["ontologyWorldPartitionedReasoningEnabled"] = "0"
    candidate_settings["ontologyInferenceDetailOutboxEnabled"] = "1"
    candidate_settings["ontologyAsyncQualityRecordEnabled"] = "0"
    provisioning_contract = dict(deployment_health.get("graphStoreProvisioning") or {})
    reuses_existing_graph_store = (
        str(provisioning_contract.get("mode") or "").strip().lower() == "reuse-existing"
        and str(provisioning_contract.get("database") or "").strip()
        == str(candidate_settings.get("typedbDatabase") or "").strip()
    )
    if (
        str(descriptor.status or "").strip().lower() == "provisioning"
        and not reuses_existing_graph_store
    ):
        candidate_settings["typedbFreshCandidateRebuild"] = "1"
    candidate_settings["_reasoningEngineDeploymentId"] = descriptor.deployment_id
    candidate_settings["_reasoningEngineVersion"] = descriptor.engine_version
    candidate_settings["_reasoningTimeSeriesBackendId"] = descriptor.time_series_backend_id
    candidate_settings["_reasoningFeatureSetVersion"] = (
        descriptor.release_bundle.feature_set_version
    )
    store_settings = dict(configured)
    store_settings["_skipOperationalHistoryRetention"] = "1"
    store_settings["_skipOperationalSchemaBootstrap"] = "1"
    return ReasoningLaunch(
        configured, platform, descriptor, deployment_health, candidate_settings, store_settings
    )
