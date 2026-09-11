"""manifest: graphs through explicit injected capabilities."""

from digital_twin.domain.ontology_contracts import OntologyEntity, PortfolioOntology
from digital_twin.domain.ontology_scopes import (
    SCOPED_ABOX_MANIFEST_VERSION,
    SCOPED_ABOX_PERSISTENCE_MODE,
)
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    native_rule_evidence_read_index_from_rows,
    native_rule_manifest_index_required,
    normalize_native_rule_evidence_read_index,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
)
from typing import Dict, Iterable, List
import hashlib
from .graphs_ports import ManifestGraphsStore, ManifestGraphsRuntime


def scoped_manifest_marker_graph(
    _store: ManifestGraphsStore,
    graph: PortfolioOntology,
    scope_plan: List[Dict[str, object]],
    changed_scope_ids: Iterable[str],
    *,
    _bindings: ManifestGraphsRuntime
) -> PortfolioOntology:
    worldview = dict(getattr(graph, "worldview", {}) or {})
    manifest_id = str(
        worldview.get("worldviewManifestId") or worldview.get("aboxSnapshotId") or ""
    ).strip()
    if not manifest_id:
        return PortfolioOntology(str(graph.portfolio_id or "typedb-scoped-manifest"))
    world_context = {
        "worldId": str(worldview.get("worldId") or ""),
        "worldType": str(worldview.get("worldType") or ""),
        "tenantId": str(worldview.get("tenantId") or ""),
        "accountId": str(worldview.get("accountId") or graph.portfolio_id or ""),
    }
    rule_index_required = native_rule_manifest_index_required(worldview)
    evidence_read_index = {}
    if rule_index_required:
        # Target-scoped graphs contain only the current mailbox subject.
        # Their index is prepared against the retained active Manifest
        # before this marker is written. A full graph can derive it here.
        all_node_rows, all_relation_rows = _store.graph_persistence_rows(graph)
        local_evidence_read_index = native_rule_evidence_read_index_from_rows(
            all_node_rows,
            all_relation_rows,
        )
        planner_topology = dict(worldview.get("nativeRulePlannerTopology") or {})
        prepared_index = dict(worldview.get("nativeRuleEvidenceReadIndex") or {})
        prepared = normalize_native_rule_evidence_read_index(
            prepared_index,
            planner_topology=planner_topology,
        )
        local = normalize_native_rule_evidence_read_index(
            local_evidence_read_index,
            planner_topology=planner_topology,
        )
        if str(prepared.get("status") or "") == "ok":
            evidence_read_index = prepared_index
        elif str(local.get("status") or "") == "ok":
            evidence_read_index = local_evidence_read_index
        # Never persist a self-consistent partial index against a merged
        # inference Manifest. Runtime recovery must verify membership.
    marker_scope_id = "manifest:" + manifest_id
    marker = OntologyEntity(
        entity_id="worldview-manifest-marker:" + manifest_id,
        label="Worldview Manifest " + manifest_id,
        kind="worldview-manifest-marker",
        properties={
            "ontologyBox": "ABox",
            **world_context,
            "tboxClass": "WorldviewManifest",
            "snapshotId": manifest_id,
            "aboxSnapshotId": manifest_id,
            "worldviewManifestId": manifest_id,
            "aboxScopeId": marker_scope_id,
            "aboxScopeType": "manifest",
            "scopeGenerationId": manifest_id,
            "materialFingerprint": str(worldview.get("materialFingerprint") or ""),
            "projectionRunId": str(worldview.get("projectionRunId") or ""),
            "asOf": str(
                worldview.get("asOf") or worldview.get("generatedAt") or _bindings.utc_now()
            ),
            "lastFullScopeReconcileAt": str(worldview.get("lastFullScopeReconcileAt") or ""),
            "scopePlan": list(scope_plan),
            "scopeGenerationIds": dict(worldview.get("scopeGenerationIds") or {}),
            "logicalScopeGenerationIds": dict(worldview.get("logicalScopeGenerationIds") or {}),
            "scopeFingerprints": dict(worldview.get("scopeFingerprints") or {}),
            "scopeTopologyVersion": str(worldview.get("scopeTopologyVersion") or ""),
            "scopeFamilyCounts": dict(worldview.get("scopeFamilyCounts") or {}),
            "marketScopeObservedAt": dict(worldview.get("marketScopeObservedAt") or {}),
            "marketScopeObservedAtVersion": str(
                worldview.get("marketScopeObservedAtVersion") or ""
            ),
            "marketWorldProjectionMode": str(worldview.get("marketWorldProjectionMode") or ""),
            "sharedWorldProjection": str(worldview.get("sharedWorldProjection") or ""),
            "sharedWorldProjectionContractVersion": str(
                worldview.get("sharedWorldProjectionContractVersion") or ""
            ),
            "sharedWorldFullRebuild": bool(worldview.get("sharedWorldFullRebuild")),
            "accountOverlayProjectionContractVersion": str(
                worldview.get("accountOverlayProjectionContractVersion") or ""
            ),
            "worldPartitionedReasoningVersion": str(
                worldview.get("worldPartitionedReasoningVersion") or ""
            ),
            "marketContextMode": str(worldview.get("marketContextMode") or ""),
            "marketReadMirrorRemoved": bool(worldview.get("marketReadMirrorRemoved")),
            "sharedPremiseWorldId": str(worldview.get("sharedPremiseWorldId") or ""),
            "sharedPremiseInferenceGenerationId": str(
                worldview.get("sharedPremiseInferenceGenerationId")
                or worldview.get("sharedInferenceGenerationId")
                or ""
            ),
            "sharedPremiseSourceAboxSnapshotId": str(
                worldview.get("sharedSourceAboxSnapshotId") or ""
            ),
            "scopeDelta": dict(worldview.get("scopeDelta") or {}),
            "inferenceImpactPlan": dict(worldview.get("inferenceImpactPlan") or {}),
            "nativeRulePlannerTopology": dict(worldview.get("nativeRulePlannerTopology") or {}),
            "nativeRuleEvidenceReadIndex": evidence_read_index,
            "nativeRuleEvidenceReadIndexRequired": rule_index_required,
            "nativeRuleEvidenceReadIndexStatus": (
                str((worldview.get("nativeRuleEvidenceReadIndexMerge") or {}).get("status") or "")
                if rule_index_required
                else "not-required-source-world"
            ),
            "nativeRulePlannerTopologyMerge": dict(
                worldview.get("nativeRulePlannerTopologyMerge") or {}
            ),
            "nativeRuleEvidenceReadIndexMerge": dict(
                worldview.get("nativeRuleEvidenceReadIndexMerge") or {}
            ),
            "factSlotProjection": dict(worldview.get("factSlotProjection") or {}),
            "changedScopeIds": sorted(
                {str(item or "") for item in changed_scope_ids if str(item or "")}
            ),
            "projectionStatus": "complete",
            "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
            "persistenceMode": str(
                worldview.get("persistenceMode") or SCOPED_ABOX_PERSISTENCE_MODE
            ),
            "physicalStateMode": str(
                worldview.get("physicalStateMode")
                or worldview.get("persistenceMode")
                or SCOPED_ABOX_PERSISTENCE_MODE
            ),
        },
    )
    return PortfolioOntology(str(graph.portfolio_id or "typedb-scoped-manifest"), entities=[marker])


def scoped_manifest_pointer_graph(
    _store: ManifestGraphsStore,
    graph: PortfolioOntology,
    scope_plan: List[Dict[str, object]],
    previous_metadata: Dict[str, object] = None,
    pending_activation: bool = True,
    inference_target_symbols: Iterable[str] = None,
    scope_ids: Iterable[str] = None,
    *,
    _bindings: ManifestGraphsRuntime
) -> PortfolioOntology:
    worldview = dict(getattr(graph, "worldview", {}) or {})
    manifest_id = str(
        worldview.get("worldviewManifestId") or worldview.get("aboxSnapshotId") or ""
    ).strip()
    if not manifest_id:
        return PortfolioOntology(str(graph.portfolio_id or "typedb-scoped-control"))
    previous = dict(previous_metadata or {})
    previous_manifest_id = str(
        previous.get("worldviewManifestId") or previous.get("aboxSnapshotId") or ""
    ).strip()
    world_context = {
        "worldId": str(worldview.get("worldId") or previous.get("worldId") or ""),
        "worldType": str(worldview.get("worldType") or previous.get("worldType") or ""),
        "tenantId": str(worldview.get("tenantId") or previous.get("tenantId") or ""),
        "accountId": str(
            worldview.get("accountId") or previous.get("accountId") or graph.portfolio_id or ""
        ),
    }
    world_id = str(world_context.get("worldId") or "")
    world_suffix = (
        (":world:" + hashlib.sha256(world_id.encode("utf-8")).hexdigest()[:16]) if world_id else ""
    )
    # A Manifest marker intentionally excludes execution targets because
    # they do not affect its material identity. The activation journal is
    # the durable hand-off for those targets, so a pointer rebuild must
    # carry the already-verified values explicitly instead of trying to
    # recover them from the immutable marker later.
    target_symbols = (
        clean_symbols_from_payload(inference_target_symbols)
        if inference_target_symbols is not None
        else clean_symbols_from_payload(
            worldview.get("inferenceTargetSymbols") or worldview.get("targetSymbols") or []
        )
    )
    # Both control records are routing journals, not a second copy of the
    # immutable Manifest. The active pointer selects the generation, and
    # the pending journal carries only the recovery identity and target
    # coverage. Detailed scope/evidence data stays on the Manifest marker.
    control_identity_common = {
        "ontologyBox": "ABoxControl",
        **world_context,
        "worldviewManifestId": manifest_id,
        "materialFingerprint": str(worldview.get("materialFingerprint") or ""),
        "projectionRunId": str(worldview.get("projectionRunId") or ""),
        "asOf": str(worldview.get("asOf") or worldview.get("generatedAt") or _bindings.utc_now()),
        "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
    }
    entities = [
        OntologyEntity(
            entity_id="worldview-manifest-active-pointer" + world_suffix,
            label="Active Worldview Manifest",
            kind="worldview-manifest-active-pointer",
            properties={
                **control_identity_common,
                "tboxClass": "WorldviewManifestActivePointer",
                "snapshotId": manifest_id,
                "aboxSnapshotId": manifest_id,
            },
        )
    ]
    selected_scope_ids = {
        str(value or "").strip() for value in scope_ids or [] if str(value or "").strip()
    }
    include_all_scope_pointers = scope_ids is None
    for item in scope_plan:
        scope_id = str(item.get("scopeId") or "").strip()
        generation_id = str(item.get("generationId") or "").strip()
        if (
            not scope_id
            or not generation_id
            or (not include_all_scope_pointers and scope_id not in selected_scope_ids)
        ):
            continue
        digest = hashlib.sha256((world_id + "|" + scope_id).encode("utf-8")).hexdigest()[:16]
        entities.append(
            OntologyEntity(
                entity_id="abox-scope-active-pointer:" + digest,
                label="Active ABox scope " + scope_id,
                kind="abox-scope-active-pointer",
                properties={
                    "ontologyBox": "ABoxControl",
                    **world_context,
                    "tboxClass": "ABoxScopeActivePointer",
                    "snapshotId": generation_id,
                    "aboxSnapshotId": generation_id,
                    "worldviewManifestId": manifest_id,
                    "aboxScopeId": scope_id,
                    "aboxScopeType": str(item.get("scopeType") or scope_id.split(":", 1)[0]),
                    "scopeGenerationId": generation_id,
                    "scopeFingerprint": str(item.get("fingerprint") or ""),
                },
            )
        )
    if pending_activation and previous_manifest_id != manifest_id:
        entities.append(
            OntologyEntity(
                entity_id="abox-activation-pending" + world_suffix,
                label="Worldview Manifest activation pending native inference",
                kind="abox-activation-pending",
                properties={
                    **control_identity_common,
                    "tboxClass": "ABoxActivationPending",
                    "snapshotId": manifest_id,
                    "aboxSnapshotId": manifest_id,
                    "candidateAboxSnapshotId": manifest_id,
                    "candidateWorldviewManifestId": manifest_id,
                    "previousAboxSnapshotId": previous_manifest_id,
                    "previousWorldviewManifestId": previous_manifest_id,
                    "targetSymbols": target_symbols,
                    "activationStatus": "pending-native-inference",
                },
            )
        )
    return PortfolioOntology(str(graph.portfolio_id or "typedb-scoped-control"), entities=entities)


def scoped_manifest_pending_graph(
    _store: ManifestGraphsStore,
    graph: PortfolioOntology,
    scope_plan: List[Dict[str, object]],
    previous_metadata: Dict[str, object] = None,
    inference_target_symbols: Iterable[str] = None,
) -> PortfolioOntology:
    """Persist a verified candidate journal without moving the live world.

    Candidate scope generations and their complete Manifest marker can be
    safely written before native inference.  The small pending control
    record makes that hand-off durable while leaving the prior active
    Worldview Manifest readable until the reasoning worker explicitly
    prepares the candidate.
    """
    control = _store.scoped_manifest_pointer_graph(
        graph,
        scope_plan,
        previous_metadata=previous_metadata,
        pending_activation=True,
        inference_target_symbols=inference_target_symbols,
    )
    pending_entities = [
        entity for entity in control.entities if str(entity.kind or "") == "abox-activation-pending"
    ]
    for entity in pending_entities:
        entity.properties["activationStatus"] = "staged-native-inference"
    return PortfolioOntology(
        str(graph.portfolio_id or "typedb-scoped-control"),
        entities=pending_entities,
    )


def scoped_manifest_control_graph(
    _store: ManifestGraphsStore,
    metadata: Dict[str, object],
    previous_metadata: Dict[str, object] = None,
    pending_activation: bool = False,
    inference_target_symbols: Iterable[str] = None,
    scope_ids: Iterable[str] = None,
    *,
    _bindings: ManifestGraphsRuntime
) -> PortfolioOntology:
    """Build the active Manifest pointer and an optional scope-pointer delta.

    The main Manifest pointer is always replaced. ``scope_ids`` lets an
    activation replace only generations that changed from the already
    verified active Manifest; all other scope pointers remain valid.
    """
    payload = dict(metadata or {})
    manifest_id = str(
        payload.get("worldviewManifestId") or payload.get("aboxSnapshotId") or ""
    ).strip()
    if not manifest_id:
        return PortfolioOntology("typedb-scoped-control")
    worldview = {
        "worldviewManifestId": manifest_id,
        "aboxSnapshotId": manifest_id,
        "snapshotId": manifest_id,
        "materialFingerprint": str(payload.get("materialFingerprint") or ""),
        "projectionRunId": str(payload.get("projectionRunId") or ""),
        "asOf": str(payload.get("asOf") or _bindings.utc_now()),
        "scopePlan": list(payload.get("scopePlan") or []),
        "scopeGenerationIds": dict(payload.get("scopeGenerationIds") or {}),
        "scopeFingerprints": dict(payload.get("scopeFingerprints") or {}),
        "worldId": str(payload.get("worldId") or ""),
        "worldType": str(payload.get("worldType") or ""),
        "tenantId": str(payload.get("tenantId") or ""),
        "accountId": str(payload.get("accountId") or ""),
        "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
        "persistenceMode": SCOPED_ABOX_PERSISTENCE_MODE,
    }
    graph = PortfolioOntology("typedb-scoped-control", worldview=worldview)
    return _store.scoped_manifest_pointer_graph(
        graph,
        list(worldview["scopePlan"]),
        previous_metadata=previous_metadata,
        pending_activation=pending_activation,
        inference_target_symbols=inference_target_symbols,
        scope_ids=scope_ids,
    )


def scoped_manifest_control_delta(
    metadata: Dict[str, object], previous_metadata: Dict[str, object] = None
) -> Dict[str, object]:
    """Return the pointer rows an activation must replace.

    A Worldview Manifest changes whenever any scope generation changes,
    but the per-scope controls are independently addressable. Replacing
    only changed generations keeps the native read contract intact without
    turning a one-symbol observation into a full control-plane rewrite.
    """
    next_payload = dict(metadata or {})
    previous_payload = dict(previous_metadata or {})
    next_generations = {
        str(scope_id or "").strip(): str(generation_id or "").strip()
        for scope_id, generation_id in dict(next_payload.get("scopeGenerationIds") or {}).items()
        if str(scope_id or "").strip() and str(generation_id or "").strip()
    }
    previous_generations = {
        str(scope_id or "").strip(): str(generation_id or "").strip()
        for scope_id, generation_id in dict(
            previous_payload.get("scopeGenerationIds") or {}
        ).items()
        if str(scope_id or "").strip() and str(generation_id or "").strip()
    }
    previous_scoped = (
        str(previous_payload.get("status") or "") == "ok"
        and str(previous_payload.get("scopedAboxManifestVersion") or "")
        == SCOPED_ABOX_MANIFEST_VERSION
        and bool(previous_generations)
    )
    same_topology = str(previous_payload.get("scopeTopologyVersion") or "") == str(
        next_payload.get("scopeTopologyVersion") or ""
    )
    replace_all = not previous_scoped or not same_topology
    changed_scope_ids = sorted(
        next_generations
        if replace_all
        else {
            scope_id
            for scope_id, generation_id in next_generations.items()
            if previous_generations.get(scope_id) != generation_id
        }
    )
    removed_scope_ids = sorted(set(previous_generations) - set(next_generations))
    return {
        "mode": (
            "full-scoped-control-rebuild" if replace_all else "incremental-scoped-control-patch"
        ),
        "replaceAllScopePointers": replace_all,
        "changedScopeIds": changed_scope_ids,
        "removedScopeIds": removed_scope_ids,
        "replacedScopeIds": sorted(set(changed_scope_ids) | set(removed_scope_ids)),
        "reusedScopeIds": sorted(set(next_generations) - set(changed_scope_ids)),
        "previousScopeCount": len(previous_generations),
        "nextScopeCount": len(next_generations),
    }
