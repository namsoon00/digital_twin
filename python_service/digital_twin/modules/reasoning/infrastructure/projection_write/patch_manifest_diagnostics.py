"""Manifest diagnostics and integrity contracts; no persistence capability."""

from typing import Callable, Dict
from .patch_manifest_results import AppliedPatchInput, FailedPatchInput


def applied_patch_diagnostics(
    request: AppliedPatchInput,
    *,
    compact_target_scope_selection_trace: Callable[..., Dict[str, object]],
    scope_integrity_audit_interval_minutes: Callable[[], float],
) -> Dict[str, object]:
    applied_target_patch = request.applied_target_patch
    target_scoped_patch = request.target_scoped_patch
    persistence_graph = request.persistence_graph
    scope_repair = request.scope_repair
    repair_input_fallback = request.repair_input_fallback
    replacement_symbols = request.identity.replacement_symbols
    semantic_noop_patch = request.identity.semantic_noop_patch
    scope_selection_trace = compact_target_scope_selection_trace(
        applied_target_patch,
    )
    relation_rebind_root_scope_ids = list(
        scope_selection_trace.get("relationRebindRootScopeIds") or []
    )
    return {
        "status": "applied",
        "mode": "incremental-target-scoped-manifest-patch",
        "targetSymbols": list(applied_target_patch.get("targetSymbols") or []),
        "replacementSymbols": replacement_symbols,
        "replacementRootScopeIds": list(applied_target_patch.get("replacementRootScopeIds") or []),
        "selectedIncomingScopeCount": len(
            applied_target_patch.get("selectedIncomingScopeIds") or []
        ),
        "semanticNoop": semantic_noop_patch,
        "reusedActiveScopeCount": len(applied_target_patch.get("reusedActiveScopeIds") or []),
        "deferredScopeCount": len(applied_target_patch.get("deferredScopeIds") or []),
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
        "retiredScopeIds": list(applied_target_patch.get("retiredScopeIds") or []),
        "scopeTopologyVersion": str(
            (persistence_graph.worldview or {}).get("scopeTopologyVersion") or ""
        ),
        "scopeTopologyMigration": dict(applied_target_patch.get("scopeTopologyMigration") or {}),
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
                for scope_id in applied_target_patch.get("selectedIncomingScopeIds") or []
                if ":bucket:" in str(scope_id) or ":window:" in str(scope_id)
            ]
        ),
        "factSlotStatus": str((applied_target_patch.get("factSlot") or {}).get("status") or ""),
        "factSlotSelectedScopeCount": len(
            (applied_target_patch.get("factSlot") or {}).get("selectedScopeIds") or []
        ),
        "factSlotDeferredScopeCount": len(
            (applied_target_patch.get("factSlot") or {}).get("deferredScopeIds") or []
        ),
        "factSlotFamilies": list(
            (applied_target_patch.get("factSlot") or {}).get("slotFamilies") or []
        )[:20],
        "factSlotFamiliesBySymbol": dict(
            (applied_target_patch.get("factSlot") or {}).get("slotFamiliesBySymbol") or {}
        ),
        "factSlotChangedFieldsBySymbol": dict(
            (applied_target_patch.get("factSlot") or {}).get("changedFieldsBySymbol") or {}
        ),
        "factSlotPreciseFieldRoutingSymbols": list(
            (applied_target_patch.get("factSlot") or {}).get("preciseFieldRoutingSymbols") or []
        )[:20],
        "factSlotUnclassifiedChangedFieldsBySymbol": dict(
            (applied_target_patch.get("factSlot") or {}).get("unclassifiedChangedFieldsBySymbol")
            or {}
        ),
        "factSlotFallbackReason": str(
            (applied_target_patch.get("factSlot") or {}).get("fallbackReason") or ""
        ),
        "scopeSelectionTrace": scope_selection_trace,
        "manifestPatchContract": dict(applied_target_patch.get("manifestPatchContract") or {}),
        "scopeIntegrityAuditIntervalMinutes": scope_integrity_audit_interval_minutes(),
        "scopeIntegrityAuditDue": bool(target_scoped_patch.get("scopeIntegrityAuditDue")),
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


def blocked_patch_result(
    request: FailedPatchInput,
    *,
    active_graph_store_key: Callable[[], str],
) -> Dict[str, object]:
    applied_target_patch = request.applied_target_patch
    target_scoped_patch = request.target_scoped_patch
    repair_input_fallback = request.repair_input_fallback
    graph_input = request.graph_input
    # A local event must never become a whole-world write merely
    # because its incremental merge needs repair. Preserve the
    # active Manifest and surface the exact scope failure. An
    # operator can run the explicit rebuild path for a topology
    # migration; normal workers remain bounded by subject.
    return {
        "saved": False,
        "status": "target-scope-repair-required",
        "reason": "Target-scoped Manifest patch could not be applied safely.",
        "graphStore": active_graph_store_key(),
        "preservedActiveGeneration": True,
        "recommendedRetryAfterSeconds": 60,
        "graphInput": graph_input,
        "targetScopedManifestPatch": {
            "status": str(applied_target_patch.get("status") or "repair-required"),
            "mode": "target-scope-repair-required",
            "targetSymbols": list(target_scoped_patch.get("targetSymbols") or []),
            "incomingScopeCount": int(applied_target_patch.get("incomingScopeCount") or 0),
            "activeScopeCount": int(applied_target_patch.get("activeScopeCount") or 0),
            "missingEndpointScopeIds": list(
                applied_target_patch.get("missingEndpointScopeIds") or []
            )[:50],
            "removedRelevantScopeIds": list(
                applied_target_patch.get("removedRelevantScopeIds") or []
            )[:50],
            "sharedRemovedScopeIds": list(applied_target_patch.get("sharedRemovedScopeIds") or [])[
                :50
            ],
            "retiredScopeIds": list(applied_target_patch.get("retiredScopeIds") or [])[:50],
            "manifestPatchContract": dict(applied_target_patch.get("manifestPatchContract") or {}),
            "patchPlanViolations": list(applied_target_patch.get("patchPlanViolations") or [])[:50],
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


def full_manifest_fallback(
    applied_target_patch: Dict[str, object],
    target_scoped_patch: Dict[str, object],
) -> Dict[str, object]:
    return {
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
        "deferredScopeCount": len(applied_target_patch.get("deferredScopeIds") or []),
        "scopeTopologyMigration": dict(applied_target_patch.get("scopeTopologyMigration") or {}),
    }
