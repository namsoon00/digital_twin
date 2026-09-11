"""Reassemble the same source once; selection remains target bounded.

Only source assembly and target selection are capabilities here. This helper
cannot publish a Manifest, write an audit or acquire a graph driver.
"""

from typing import Callable, Dict
from digital_twin.modules.reasoning.domain.ontology_scopes import (
    apply_scoped_abox_repair_epochs,
    merge_target_scoped_abox_manifest,
)
from .patch_manifest_results import RepairSourceInput, RepairSourceResult


def repair_manifest_source(
    request: RepairSourceInput,
    *,
    build_projection_graph: Callable[..., Dict[str, object]],
    target_scoped_patch_targets: Callable[..., Dict[str, object]],
    emit_progress: Callable[..., None],
    clock: Callable[[], float],
    graph_input: Dict[str, object],
    runtime_stages: Dict[str, int],
) -> RepairSourceResult:
    snapshot = request.snapshot
    rulebox_bootstrap = request.rulebox_bootstrap
    portfolio_world_context = request.portfolio_world_context
    market_world_context = request.market_world_context
    target_symbols = request.target_symbols
    shared_premise_proof = request.shared_premise_proof
    compact_reasoning_context = request.compact_reasoning_context
    observation_followup_targets = request.observation_followup_targets
    active_abox = request.active_abox
    applied_target_patch = request.applied_target_patch
    # A scoped source can legitimately omit a shared endpoint
    # that is retained by the active Manifest. Reassemble the
    # complete source in memory once, then persist only the
    # originally requested target patch. This repairs the
    # source boundary without turning local work into a full
    # TypeDB world rewrite.
    emit_progress(
        "target_manifest_repair_input.start",
        status=str(applied_target_patch.get("status") or "repair-required"),
        fallbackReason=str(applied_target_patch.get("fallbackReason") or ""),
        missingEndpointScopeIds=list(applied_target_patch.get("missingEndpointScopeIds") or [])[
            :24
        ],
        missingEndpointScopes="|".join(
            str(value or "")
            for value in (applied_target_patch.get("missingEndpointScopeIds") or [])[:12]
        ),
    )
    repair_input_started = clock()
    first_patch_failure = dict(applied_target_patch or {})
    repair_projection_graph = build_projection_graph(
        snapshot,
        rulebox_bootstrap,
        portfolio_world_context,
        market_world_context=market_world_context,
        target_symbols=target_symbols,
        target_scoped_input=False,
        shared_premise_proof=shared_premise_proof,
        reasoning_context=compact_reasoning_context,
    )
    graph = repair_projection_graph["graph"]
    persistence_graph = repair_projection_graph["persistenceGraph"]
    graph_assembly = repair_projection_graph["assembly"]
    planner_topology = repair_projection_graph["plannerTopology"]
    material_fingerprint = repair_projection_graph["materialFingerprint"]
    material_snapshot_id = repair_projection_graph["materialSnapshotId"]
    scoped_identity = repair_projection_graph["scopedIdentity"]
    persistence_graph.worldview["targetScopeRetentionMode"] = (
        "observation-followup" if observation_followup_targets else "incremental-target-patch"
    )
    if observation_followup_targets:
        persistence_graph.worldview["observationFollowupTargets"] = list(
            observation_followup_targets
        )
    target_scoped_patch = target_scoped_patch_targets(
        snapshot,
        active_abox,
        scoped_identity,
        target_symbols,
        reasoning_context=compact_reasoning_context,
    )
    scope_repair = apply_scoped_abox_repair_epochs(
        persistence_graph,
        active_abox,
        compact_reasoning_context.get("scopeRepairRequestsBySymbol") or {},
    )
    applied_target_patch = merge_target_scoped_abox_manifest(
        persistence_graph,
        active_abox,
        target_scoped_patch.get("targetSymbols") or [],
        fact_slot_plan=target_scoped_patch.get("factSlotPlan") or {},
        source_graph_complete=True,
    )
    repair_runtime_stages = dict(repair_projection_graph.get("runtimeStages") or {})
    for stage, value in repair_runtime_stages.items():
        runtime_stages["targetManifestRepairInput" + stage[:1].upper() + stage[1:]] = value
    runtime_stages["targetManifestRepairInputMs"] = int((clock() - repair_input_started) * 1000)
    repair_input_fallback = {
        "attempted": True,
        "mode": "complete-source-assembly-target-persist",
        "firstStatus": str(first_patch_failure.get("status") or ""),
        "firstMissingEndpointScopeIds": list(
            first_patch_failure.get("missingEndpointScopeIds") or []
        )[:50],
        "finalStatus": str(applied_target_patch.get("status") or ""),
        "applied": bool(applied_target_patch.get("applied")),
        "runtimeMs": runtime_stages["targetManifestRepairInputMs"],
        "automaticFullProjectionBlocked": True,
    }
    graph_input.update(
        {
            "repairInputFallback": True,
            "repairInputMode": "complete-source-assembly-target-persist",
            "repairInputStatus": str(applied_target_patch.get("status") or ""),
        }
    )
    emit_progress(
        "target_manifest_repair_input.done",
        status=str(applied_target_patch.get("status") or ""),
        applied=bool(applied_target_patch.get("applied")),
        runtimeMs=runtime_stages["targetManifestRepairInputMs"],
    )
    return RepairSourceResult(
        graph=graph,
        persistence_graph=persistence_graph,
        planner_topology=planner_topology,
        material_fingerprint=material_fingerprint,
        material_snapshot_id=material_snapshot_id,
        scoped_identity=scoped_identity,
        target_scoped_patch=target_scoped_patch,
        scope_repair=scope_repair,
        applied_target_patch=applied_target_patch,
        repair_input_fallback=repair_input_fallback,
    )
