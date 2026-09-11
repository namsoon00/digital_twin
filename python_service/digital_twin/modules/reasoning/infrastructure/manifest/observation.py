"""manifest: observation through explicit injected capabilities."""

from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import json_object
from typing import Dict, List
from .observation_ports import ManifestObservationStore


def refresh_market_world_observation_metadata(
    _store: ManifestObservationStore,
    manifest_id: str,
    scope_plan: List[Dict[str, object]],
    market_scope_observed_at: Dict[str, object],
    world_id: str = "",
    adopted_write_lease: Dict[str, object] = None,
) -> Dict[str, object]:
    """Refresh MarketWorld source clocks without changing ABox facts.

    A collection heartbeat can prove that an unchanged quote or news feed
    was observed again. It must not manufacture a new scoped generation
    or rerun native inference. The active marker is the only mutable
    control record here; its manifest id and scope generations are
    verified before it is atomically replaced.
    """
    if not getattr(_store, "address", ""):
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
        }
    clean_manifest_id = str(manifest_id or "").strip()
    requested_world_id = str(world_id or "").strip()
    expected_generations = {
        str(item.get("scopeId") or "").strip(): str(item.get("generationId") or "").strip()
        for item in scope_plan or []
        if isinstance(item, dict)
        and str(item.get("scopeId") or "").strip()
        and str(item.get("generationId") or "").strip()
    }
    if not clean_manifest_id or not expected_generations:
        return {
            "configured": True,
            "saved": False,
            "status": "invalid-scoped-manifest",
            "graphStore": "typedb",
            "reason": "MarketWorld observation metadata requires a complete active scoped Manifest.",
        }

    adopted = dict(adopted_write_lease or {})
    adopted_owner = str(adopted.get("leaseOwner") or adopted.get("owner") or "").strip()
    lease_is_adopted = bool(adopted_owner)
    write_lease: Dict[str, object] = {}
    if lease_is_adopted:
        current_lease = _store.scoped_abox_write_lease_status(requested_world_id)
        if (
            str(current_lease.get("status") or "") != "held"
            or str(current_lease.get("leaseOwner") or "") != adopted_owner
        ):
            return {
                "configured": True,
                "saved": False,
                "status": "invalid-scoped-write-lease",
                "graphStore": "typedb",
                "worldId": requested_world_id,
                "worldviewManifestId": clean_manifest_id,
                "preservedActiveGeneration": True,
                "reason": "The MarketWorld observation refresh no longer owns its scoped ABox write lease.",
            }
        write_lease = {**adopted, "acquired": True, "leaseOwner": adopted_owner}
    else:
        write_lease = _store.acquire_scoped_abox_write_lease(
            "market-world-observation:" + clean_manifest_id,
            world_id=requested_world_id,
        )
        if not write_lease.get("acquired"):
            return {
                "configured": True,
                "saved": False,
                "status": "deferred-scoped-write-lease",
                "graphStore": "typedb",
                "worldId": requested_world_id,
                "worldviewManifestId": clean_manifest_id,
                "preservedActiveGeneration": True,
                "reason": "Another scoped ABox writer is active; MarketWorld observation metadata will be retried.",
            }

    def release_write_lease() -> Dict[str, object]:
        if lease_is_adopted:
            return {"status": "adopted-by-caller", "leaseOwner": adopted_owner}
        try:
            return _store.release_scoped_abox_write_lease(write_lease)
        except Exception as error:  # noqa: BLE001 - expiry still protects the next cycle.
            return {"status": "error", "reason": str(error)[:180]}

    try:
        active = dict(_store.active_abox_metadata(requested_world_id) or {})
        active_manifest_id = str(
            active.get("worldviewManifestId") or active.get("aboxSnapshotId") or ""
        ).strip()
        active_generations = {
            str(scope_id or "").strip(): str(generation_id or "").strip()
            for scope_id, generation_id in dict(active.get("scopeGenerationIds") or {}).items()
            if str(scope_id or "").strip() and str(generation_id or "").strip()
        }
        if (
            str(active.get("status") or "") != "ok"
            or active_manifest_id != clean_manifest_id
            or active_generations != expected_generations
        ):
            return {
                "configured": True,
                "saved": False,
                "status": "stale-manifest",
                "graphStore": "typedb",
                "worldId": requested_world_id,
                "worldviewManifestId": clean_manifest_id,
                "activeManifestId": active_manifest_id,
                "preservedActiveGeneration": True,
                "reason": "The active MarketWorld Manifest changed before its observation metadata could be refreshed.",
            }
        markers = _store.worldview_manifest_marker_rows(
            requested_world_id,
            manifest_id=clean_manifest_id,
            limit=1,
        )
        marker = next(
            (
                item
                for item in markers
                if str(
                    item.get("worldviewManifestId")
                    or item.get("aboxSnapshotId")
                    or item.get("snapshotId")
                    or ""
                ).strip()
                == clean_manifest_id
            ),
            {},
        )
        if not marker:
            return {
                "configured": True,
                "saved": False,
                "status": "manifest-marker-missing",
                "graphStore": "typedb",
                "worldId": requested_world_id,
                "worldviewManifestId": clean_manifest_id,
                "preservedActiveGeneration": True,
                "reason": "The active MarketWorld Manifest marker is unavailable for an observation-only refresh.",
            }
        properties = json_object(marker.get("propertiesJson"))
        for key in [
            "ontologyBox",
            "worldId",
            "worldType",
            "tenantId",
            "accountId",
            "tboxClass",
            "snapshotId",
            "aboxSnapshotId",
            "worldviewManifestId",
            "aboxScopeId",
            "aboxScopeType",
            "scopeGenerationId",
            "materialFingerprint",
            "projectionRunId",
            "asOf",
            "scopePlan",
            "scopeGenerationIds",
            "scopeFingerprints",
            "scopeTopologyVersion",
            "scopeFamilyCounts",
            "scopeDelta",
            "inferenceImpactPlan",
            "nativeRulePlannerTopology",
            "nativeRuleEvidenceReadIndex",
            "nativeRuleEvidenceReadIndexRequired",
            "nativeRuleEvidenceReadIndexStatus",
            "changedScopeIds",
            "projectionStatus",
            "scopedAboxManifestVersion",
            "marketWorldProjectionMode",
            "sharedWorldProjection",
            "sharedWorldProjectionContractVersion",
            "sharedWorldFullRebuild",
            "accountOverlayProjectionContractVersion",
            "worldPartitionedReasoningVersion",
            "marketContextMode",
            "marketReadMirrorRemoved",
            "sharedPremiseWorldId",
            "sharedPremiseInferenceGenerationId",
            "sharedPremiseSourceAboxSnapshotId",
        ]:
            if key in marker and key not in properties:
                properties[key] = marker.get(key)
        properties.update(
            {
                "ontologyBox": "ABox",
                "worldId": requested_world_id or str(properties.get("worldId") or ""),
                "worldType": str(active.get("worldType") or properties.get("worldType") or ""),
                "tenantId": str(active.get("tenantId") or properties.get("tenantId") or ""),
                "accountId": str(active.get("accountId") or properties.get("accountId") or ""),
                "tboxClass": "WorldviewManifest",
                "snapshotId": clean_manifest_id,
                "aboxSnapshotId": clean_manifest_id,
                "worldviewManifestId": clean_manifest_id,
                "scopePlan": list(scope_plan),
                "scopeGenerationIds": expected_generations,
                "scopeFingerprints": {
                    str(item.get("scopeId") or "").strip(): str(item.get("fingerprint") or "")
                    for item in scope_plan or []
                    if isinstance(item, dict) and str(item.get("scopeId") or "").strip()
                },
                "scopeFamilyCounts": dict(
                    active.get("scopeFamilyCounts") or properties.get("scopeFamilyCounts") or {}
                ),
                "materialFingerprint": str(
                    active.get("materialFingerprint") or properties.get("materialFingerprint") or ""
                ),
                "marketScopeObservedAt": {
                    str(scope_id): str(stamp)
                    for scope_id, stamp in dict(market_scope_observed_at or {}).items()
                    if str(scope_id or "").strip() and str(stamp or "").strip()
                },
                "marketScopeObservedAtVersion": "source-item-v1",
                "marketWorldProjectionMode": str(
                    properties.get("marketWorldProjectionMode")
                    or "incremental-scoped-manifest-patch"
                ),
                "sharedWorldProjection": str(properties.get("sharedWorldProjection") or "market"),
                "sharedWorldProjectionContractVersion": str(
                    properties.get("sharedWorldProjectionContractVersion") or ""
                ),
                "sharedWorldFullRebuild": bool(properties.get("sharedWorldFullRebuild")),
                "accountOverlayProjectionContractVersion": str(
                    properties.get("accountOverlayProjectionContractVersion") or ""
                ),
                "worldPartitionedReasoningVersion": str(
                    properties.get("worldPartitionedReasoningVersion") or ""
                ),
                "marketContextMode": str(properties.get("marketContextMode") or ""),
                "marketReadMirrorRemoved": bool(properties.get("marketReadMirrorRemoved")),
                "sharedPremiseWorldId": str(properties.get("sharedPremiseWorldId") or ""),
                "sharedPremiseInferenceGenerationId": str(
                    properties.get("sharedPremiseInferenceGenerationId") or ""
                ),
                "sharedPremiseSourceAboxSnapshotId": str(
                    properties.get("sharedPremiseSourceAboxSnapshotId") or ""
                ),
                "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
            }
        )
        marker_graph = PortfolioOntology(
            str(properties.get("accountId") or "typedb-scoped-manifest"),
            entities=[
                OntologyEntity(
                    entity_id=str(
                        marker.get("id") or "worldview-manifest-marker:" + clean_manifest_id
                    ),
                    label=str(marker.get("label") or "Worldview Manifest " + clean_manifest_id),
                    kind="worldview-manifest-marker",
                    properties=properties,
                )
            ],
        )
        replacement = _store.replace_scoped_manifest_marker_graph(marker_graph)
        if not replacement.get("saved"):
            return {
                **dict(replacement or {}),
                "status": str(replacement.get("status") or "error"),
                "worldId": requested_world_id,
                "worldviewManifestId": clean_manifest_id,
                "preservedActiveGeneration": True,
            }
        verified = dict(_store.active_abox_metadata(requested_world_id) or {})
        verified_manifest_id = str(
            verified.get("worldviewManifestId") or verified.get("aboxSnapshotId") or ""
        ).strip()
        if (
            str(verified.get("status") or "") != "ok"
            or verified_manifest_id != clean_manifest_id
            or {
                str(scope_id or "").strip(): str(generation_id or "").strip()
                for scope_id, generation_id in dict(
                    verified.get("scopeGenerationIds") or {}
                ).items()
                if str(scope_id or "").strip() and str(generation_id or "").strip()
            }
            != expected_generations
        ):
            return {
                "configured": True,
                "saved": False,
                "status": "verification-failed",
                "graphStore": "typedb",
                "worldId": requested_world_id,
                "worldviewManifestId": clean_manifest_id,
                "preservedActiveGeneration": True,
                "reason": "MarketWorld observation metadata replacement did not preserve the active scoped Manifest.",
            }
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "worldId": requested_world_id,
            "worldviewManifestId": clean_manifest_id,
            "observationScopeCount": len(properties.get("marketScopeObservedAt") or {}),
            "replacement": replacement,
        }
    finally:
        release_write_lease()
