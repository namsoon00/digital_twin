"""Plan and repair the scoped candidate without publishing active state."""

from __future__ import annotations
from typing import Optional, Union
from .record_ports import RecordSnapshotBindings
from digital_twin.domain.ontology_native_rule_planning import (
    merge_native_rule_planner_topology,
    native_rule_planner_manifest_fingerprint,
)
from digital_twin.domain.ontology_scopes import (
    apply_scoped_abox_repair_epochs,
    apply_scoped_manifest_plan,
    merge_target_scoped_abox_manifest,
)
from digital_twin.domain.portfolio import AccountSnapshot
from typing import Callable, Dict, List
import time


from .stage_results import CompletedProjection, PatchManifestResult
from .patch_manifest_ports import PatchManifestPort
from digital_twin.domain.ontology_worlds import OntologyWorld
from digital_twin.domain.ontology_projection_audit import OntologyProjectionRun
from digital_twin.domain.ontology_contracts import PortfolioOntology


def patch_manifest(
    _store: PatchManifestPort,
    _bindings: RecordSnapshotBindings,
    active_abox: Dict[str, object],
    compact_reasoning_context: Dict[str, object],
    emit_progress: Callable[..., None],
    graph: PortfolioOntology,
    graph_input: Dict[str, object],
    market_world_context: OntologyWorld,
    material_fingerprint: str,
    material_snapshot_id: str,
    observation_followup_targets: List[str],
    persistence_graph: PortfolioOntology,
    planner_topology: Dict[str, object],
    portfolio_world_context: OntologyWorld,
    projection_run: Optional[OntologyProjectionRun],
    rulebox_bootstrap: Dict[str, object],
    runtime_stages: Dict[str, int],
    scoped_identity: Dict[str, object],
    shared_premise_proof: Dict[str, object],
    snapshot: AccountSnapshot,
    target_scoped_patch: Dict[str, object],
    target_symbols: List[str],
) -> Union[PatchManifestResult, CompletedProjection]:
    if target_scoped_patch.get("eligible"):
        emit_progress(
            "target_manifest_patch.start",
            targetSymbolCount=len(target_scoped_patch.get("targetSymbols") or []),
        )
        target_patch_started = time.perf_counter()
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
            source_graph_complete=str(graph_input.get("mode") or "") == "full",
        )
        repair_input_fallback = {}
        if (
            not applied_target_patch.get("applied")
            and str(graph_input.get("mode") or "") == "target-scoped"
            and (
                not str(applied_target_patch.get("status") or "").startswith("blocked-")
                or applied_target_patch.get("requiresCompleteSource") is True
            )
        ):
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
                missingEndpointScopeIds=list(
                    applied_target_patch.get("missingEndpointScopeIds") or []
                )[:24],
                missingEndpointScopes="|".join(
                    str(value or "")
                    for value in (
                        applied_target_patch.get("missingEndpointScopeIds") or []
                    )[:12]
                ),
            )
            repair_input_started = time.perf_counter()
            first_patch_failure = dict(applied_target_patch or {})
            repair_projection_graph = _store.build_projection_graph(
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
                "observation-followup"
                if observation_followup_targets
                else "incremental-target-patch"
            )
            if observation_followup_targets:
                persistence_graph.worldview["observationFollowupTargets"] = list(
                    observation_followup_targets
                )
            target_scoped_patch = _store.target_scoped_patch_targets(
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
            repair_runtime_stages = dict(
                repair_projection_graph.get("runtimeStages") or {}
            )
            for stage, value in repair_runtime_stages.items():
                runtime_stages[
                    "targetManifestRepairInput" + stage[:1].upper() + stage[1:]
                ] = value
            runtime_stages["targetManifestRepairInputMs"] = int(
                (time.perf_counter() - repair_input_started) * 1000
            )
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
        runtime_stages["targetScopedManifestPatchMs"] = int(
            (time.perf_counter() - target_patch_started) * 1000
        )
        emit_progress(
            "target_manifest_patch.done",
            status=str(applied_target_patch.get("status") or ""),
            applied=bool(applied_target_patch.get("applied")),
            runtimeMs=runtime_stages["targetScopedManifestPatchMs"],
        )
        if applied_target_patch.get("applied"):
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
            active_planner_topology = dict(
                active_abox.get("nativeRulePlannerTopology") or {}
            )
            if (
                semantic_noop_patch
                and str(active_planner_topology.get("status") or "") == "ok"
            ):
                topology_merge = {
                    "status": "ok",
                    "reason": "No semantic scope changed; the verified active planner topology is reusable.",
                    "topology": active_planner_topology,
                    "replacedSymbols": [],
                    "retainedSymbols": list(
                        active_planner_topology.get("symbols") or []
                    ),
                    "activeSymbolCount": int(
                        active_planner_topology.get("symbolCount") or 0
                    ),
                    "incomingSymbolCount": int(
                        incoming_planner_topology.get("symbolCount") or 0
                    ),
                    "mergedSymbolCount": int(
                        active_planner_topology.get("symbolCount") or 0
                    ),
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
                topology_merge.get("topology")
                if merged_topology_available
                else incoming_planner_topology
            )
            if merged_topology_available:
                persistence_graph.worldview["nativeRulePlannerTopologyIncoming"] = (
                    incoming_planner_topology
                )
            else:
                # Older markers may predate the structural index. Keep
                # this target correct through active-membership
                # fallback, then establish the complete merged index on
                # the next eligible scoped or full projection.
                persistence_graph.worldview.pop(
                    "nativeRulePlannerTopologyIncoming", None
                )
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
            material_snapshot_id = str(
                scoped_identity.get("manifestId") or material_snapshot_id
            )
            scope_selection_trace = _bindings.compact_target_scope_selection_trace(
                applied_target_patch,
            )
            relation_rebind_root_scope_ids = list(
                scope_selection_trace.get("relationRebindRootScopeIds") or []
            )
            target_scoped_patch = {
                "status": "applied",
                "mode": "incremental-target-scoped-manifest-patch",
                "targetSymbols": list(applied_target_patch.get("targetSymbols") or []),
                "replacementSymbols": replacement_symbols,
                "replacementRootScopeIds": list(
                    applied_target_patch.get("replacementRootScopeIds") or []
                ),
                "selectedIncomingScopeCount": len(
                    applied_target_patch.get("selectedIncomingScopeIds") or []
                ),
                "semanticNoop": semantic_noop_patch,
                "reusedActiveScopeCount": len(
                    applied_target_patch.get("reusedActiveScopeIds") or []
                ),
                "deferredScopeCount": len(
                    applied_target_patch.get("deferredScopeIds") or []
                ),
                # These exact scope IDs are part of the persistence
                # contract. The TypeDB adapter uses them to keep the
                # active semantic relation image when the current
                # in-memory graph also contains changes from another
                # event family.
                "deferredRelationScopeIds": list(
                    applied_target_patch.get("deferredRelationScopeIds") or []
                ),
                "reusedActiveRelationScopeIds": list(
                    applied_target_patch.get("reusedActiveRelationScopeIds") or []
                ),
                # This is a persistence-integrity contract, not
                # optional trace detail. Without it the repository
                # treats endpoint companions as semantic roots and can
                # rebind relations to nodes not staged by this patch.
                "relationRebindRootScopeIds": relation_rebind_root_scope_ids,
                "relationRebindRootScopeCount": len(relation_rebind_root_scope_ids),
                "retiredScopeIds": list(
                    applied_target_patch.get("retiredScopeIds") or []
                ),
                "scopeTopologyVersion": str(
                    (persistence_graph.worldview or {}).get("scopeTopologyVersion")
                    or ""
                ),
                "scopeTopologyMigration": dict(
                    applied_target_patch.get("scopeTopologyMigration") or {}
                ),
                "boundedScopeCount": len(
                    [
                        item
                        for item in applied_target_patch.get("scopePlan") or []
                        if ":bucket:" in str(item.get("scopeId") or "")
                        or ":window:" in str(item.get("scopeId") or "")
                    ]
                ),
                "selectedBoundedScopeCount": len(
                    [
                        scope_id
                        for scope_id in applied_target_patch.get(
                            "selectedIncomingScopeIds"
                        )
                        or []
                        if ":bucket:" in str(scope_id) or ":window:" in str(scope_id)
                    ]
                ),
                "factSlotStatus": str(
                    (applied_target_patch.get("factSlot") or {}).get("status") or ""
                ),
                "factSlotSelectedScopeCount": len(
                    (applied_target_patch.get("factSlot") or {}).get("selectedScopeIds")
                    or []
                ),
                "factSlotDeferredScopeCount": len(
                    (applied_target_patch.get("factSlot") or {}).get("deferredScopeIds")
                    or []
                ),
                "factSlotFamilies": list(
                    (applied_target_patch.get("factSlot") or {}).get("slotFamilies")
                    or []
                )[:20],
                "factSlotFamiliesBySymbol": dict(
                    (applied_target_patch.get("factSlot") or {}).get(
                        "slotFamiliesBySymbol"
                    )
                    or {}
                ),
                "factSlotChangedFieldsBySymbol": dict(
                    (applied_target_patch.get("factSlot") or {}).get(
                        "changedFieldsBySymbol"
                    )
                    or {}
                ),
                "factSlotPreciseFieldRoutingSymbols": list(
                    (applied_target_patch.get("factSlot") or {}).get(
                        "preciseFieldRoutingSymbols"
                    )
                    or []
                )[:20],
                "factSlotUnclassifiedChangedFieldsBySymbol": dict(
                    (applied_target_patch.get("factSlot") or {}).get(
                        "unclassifiedChangedFieldsBySymbol"
                    )
                    or {}
                ),
                "factSlotFallbackReason": str(
                    (applied_target_patch.get("factSlot") or {}).get("fallbackReason")
                    or ""
                ),
                "scopeSelectionTrace": scope_selection_trace,
                "manifestPatchContract": dict(
                    applied_target_patch.get("manifestPatchContract") or {}
                ),
                "scopeIntegrityAuditIntervalMinutes": _store.scope_integrity_audit_interval_minutes(),
                "scopeIntegrityAuditDue": bool(
                    target_scoped_patch.get("scopeIntegrityAuditDue")
                ),
                "scopeRepair": {
                    key: scope_repair.get(key)
                    for key in [
                        "status",
                        "applied",
                        "requestedScopeIds",
                        "repairedScopeIds",
                        "retainedRepairScopeIds",
                    ]
                    if key in scope_repair
                },
                "repairInputFallback": dict(repair_input_fallback),
                "automaticFullProjectionBlocked": True,
            }
            persistence_graph.worldview["targetScopedManifestPatch"] = dict(
                target_scoped_patch
            )
        elif str(graph_input.get("mode") or "") == "target-scoped":
            # A local event must never become a whole-world write merely
            # because its incremental merge needs repair. Preserve the
            # active Manifest and surface the exact scope failure. An
            # operator can run the explicit rebuild path for a topology
            # migration; normal workers remain bounded by subject.
            result = {
                "saved": False,
                "status": "target-scope-repair-required",
                "reason": "Target-scoped Manifest patch could not be applied safely.",
                "graphStore": _store.active_graph_store_key(),
                "preservedActiveGeneration": True,
                "recommendedRetryAfterSeconds": 60,
                "graphInput": graph_input,
                "targetScopedManifestPatch": {
                    "status": str(
                        applied_target_patch.get("status") or "repair-required"
                    ),
                    "mode": "target-scope-repair-required",
                    "targetSymbols": list(
                        target_scoped_patch.get("targetSymbols") or []
                    ),
                    "incomingScopeCount": int(
                        applied_target_patch.get("incomingScopeCount") or 0
                    ),
                    "activeScopeCount": int(
                        applied_target_patch.get("activeScopeCount") or 0
                    ),
                    "missingEndpointScopeIds": list(
                        applied_target_patch.get("missingEndpointScopeIds") or []
                    )[:50],
                    "removedRelevantScopeIds": list(
                        applied_target_patch.get("removedRelevantScopeIds") or []
                    )[:50],
                    "sharedRemovedScopeIds": list(
                        applied_target_patch.get("sharedRemovedScopeIds") or []
                    )[:50],
                    "retiredScopeIds": list(
                        applied_target_patch.get("retiredScopeIds") or []
                    )[:50],
                    "manifestPatchContract": dict(
                        applied_target_patch.get("manifestPatchContract") or {}
                    ),
                    "patchPlanViolations": list(
                        applied_target_patch.get("patchPlanViolations") or []
                    )[:50],
                    "scopeTopologyMigration": dict(
                        applied_target_patch.get("scopeTopologyMigration") or {}
                    ),
                    "retainedDependencyScopeIds": list(
                        applied_target_patch.get("retainedDependencyScopeIds") or []
                    )[:50],
                    "selectedDependencyScopeIds": list(
                        applied_target_patch.get("selectedDependencyScopeIds") or []
                    )[:50],
                    "factSlot": dict(applied_target_patch.get("factSlot") or {}),
                    "fallbackReason": str(
                        applied_target_patch.get("fallbackReason")
                        or applied_target_patch.get("status")
                        or "target-scoped-manifest-patch-not-applied"
                    ),
                    "repairInputFallback": dict(repair_input_fallback),
                    "automaticFullProjectionBlocked": True,
                },
            }
            _store.store_projection_result(snapshot, result, projection_run)
            return CompletedProjection(result)
        else:
            target_scoped_patch = {
                "status": str(applied_target_patch.get("status") or "skipped"),
                "mode": "full-manifest-fallback",
                "targetSymbols": list(target_scoped_patch.get("targetSymbols") or []),
                "fallbackReason": str(
                    applied_target_patch.get("fallbackReason")
                    or applied_target_patch.get("status")
                    or "target-scoped-manifest-patch-not-applied"
                ),
                "selectedIncomingScopeCount": len(
                    applied_target_patch.get("selectedIncomingScopeIds") or []
                ),
                "deferredScopeCount": len(
                    applied_target_patch.get("deferredScopeIds") or []
                ),
                "scopeTopologyMigration": dict(
                    applied_target_patch.get("scopeTopologyMigration") or {}
                ),
            }

    return PatchManifestResult(
        graph=graph,
        material_fingerprint=material_fingerprint,
        material_snapshot_id=material_snapshot_id,
        persistence_graph=persistence_graph,
        scoped_identity=scoped_identity,
        target_scoped_patch=target_scoped_patch,
    )
