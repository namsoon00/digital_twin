"""Bind planner topology and identity to the merged, not incoming, Manifest."""

from digital_twin.modules.reasoning.domain.ontology_native_rule_planning import (
    merge_native_rule_planner_topology,
    native_rule_planner_manifest_fingerprint,
)
from digital_twin.modules.reasoning.domain.ontology_scopes import apply_scoped_manifest_plan
from .patch_manifest_results import ManifestIdentityInput, ManifestIdentityResult


def merge_manifest_identity(request: ManifestIdentityInput) -> ManifestIdentityResult:
    active_abox = request.active_abox
    planner_topology = request.planner_topology
    applied_target_patch = request.applied_target_patch
    persistence_graph = request.persistence_graph
    snapshot = request.snapshot
    portfolio_world_context = request.portfolio_world_context
    material_snapshot_id = request.material_snapshot_id
    # The source graph can contain newer observations for
    # deferred symbols. The persisted identity must describe
    # the merged active manifest, not facts intentionally held
    # for their own target cycle.
    incoming_planner_topology = dict(planner_topology or {})
    semantic_noop_patch = bool(
        not applied_target_patch.get("selectedIncomingScopeIds")
        and not applied_target_patch.get("retiredScopeIds")
    )
    replacement_symbols = list(
        applied_target_patch.get("replacementSymbols")
        if "replacementSymbols" in applied_target_patch
        else applied_target_patch.get("targetSymbols") or []
    )
    active_planner_topology = dict(active_abox.get("nativeRulePlannerTopology") or {})
    if semantic_noop_patch and str(active_planner_topology.get("status") or "") == "ok":
        topology_merge = {
            "status": "ok",
            "reason": "No semantic scope changed; the verified active planner topology is reusable.",
            "topology": active_planner_topology,
            "replacedSymbols": [],
            "retainedSymbols": list(active_planner_topology.get("symbols") or []),
            "activeSymbolCount": int(active_planner_topology.get("symbolCount") or 0),
            "incomingSymbolCount": int(incoming_planner_topology.get("symbolCount") or 0),
            "mergedSymbolCount": int(active_planner_topology.get("symbolCount") or 0),
            "semanticNoopReuse": True,
        }
    else:
        topology_merge = merge_native_rule_planner_topology(
            active_planner_topology,
            incoming_planner_topology,
            replacement_symbols,
        )
    merged_topology_available = str(topology_merge.get("status") or "") == "ok"
    planner_topology = dict(
        topology_merge.get("topology") if merged_topology_available else incoming_planner_topology
    )
    if merged_topology_available:
        persistence_graph.worldview["nativeRulePlannerTopologyIncoming"] = incoming_planner_topology
    else:
        # Older markers may predate the structural index. Keep
        # this target correct through active-membership
        # fallback, then establish the complete merged index on
        # the next eligible scoped or full projection.
        persistence_graph.worldview.pop("nativeRulePlannerTopologyIncoming", None)
    persistence_graph.worldview["nativeRulePlannerTopology"] = planner_topology
    persistence_graph.worldview["nativeRulePlannerTopologyMerge"] = {
        key: topology_merge.get(key)
        for key in [
            "status",
            "reason",
            "replacedSymbols",
            "retainedSymbols",
            "activeSymbolCount",
            "incomingSymbolCount",
            "mergedSymbolCount",
            "semanticNoopReuse",
        ]
    }
    material_fingerprint = native_rule_planner_manifest_fingerprint(
        applied_target_patch.get("scopeManifestFingerprint"),
        planner_topology,
    )
    scoped_identity = apply_scoped_manifest_plan(
        persistence_graph,
        applied_target_patch.get("scopePlan") or [],
        account_id=snapshot.account_id,
        world_id=portfolio_world_context.world_id,
        material_fingerprint=material_fingerprint,
    )
    material_snapshot_id = str(scoped_identity.get("manifestId") or material_snapshot_id)
    return ManifestIdentityResult(
        material_fingerprint,
        scoped_identity,
        material_snapshot_id,
        replacement_symbols,
        semantic_noop_patch,
    )
