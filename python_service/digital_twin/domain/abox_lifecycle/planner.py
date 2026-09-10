"""Contract finalization for target-scoped ABox patch selection."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping

from .contracts import (
    MANIFEST_PATCH_BOUNDARY_VERSION,
    ABoxChangeSet,
    ManifestPatchPlan,
)
from .invariants import relation_patch_directives, validate_manifest_patch_plan


def finalize_manifest_patch_plan(
    selection: Mapping[str, object],
    change_set: ABoxChangeSet,
    incoming_scope_plan: Iterable[object],
    active_scope_plan: Iterable[object],
) -> Dict[str, object]:
    """Attach one typed, validated contract to a legacy selector result."""

    raw = dict(selection or {})
    plan = ManifestPatchPlan.from_mapping(raw)
    validation = validate_manifest_patch_plan(
        change_set,
        plan,
        incoming_scope_plan,
        active_scope_plan,
    )
    directives = relation_patch_directives(
        plan,
        incoming_scope_plan,
        active_scope_plan,
    )
    contract = {
        "version": MANIFEST_PATCH_BOUNDARY_VERSION,
        "changeSet": change_set.to_dict(),
        "plan": plan.to_dict(),
        "validation": validation.to_dict(),
        "relationDirectives": [item.to_dict() for item in directives],
    }
    if validation.valid or not plan.applied:
        raw["manifestPatchContract"] = contract
        return raw
    violations = validation.to_dict()["violations"]
    # Incomplete input may omit shared endpoints still owned by other subjects.
    # Reassemble the same source once; never waive the invariant or commit it.
    repairable = not change_set.source_graph_complete and bool(violations) and all(
        item["code"] in {
            "relation-endpoint-missing-from-final-scope",
            "relation-endpoint-scope-missing-from-final-manifest",
        }
        for item in violations
    )
    return {
        **raw,
        "status": "blocked-invalid-manifest-patch-plan",
        "applied": False,
        "fallbackReason": "manifest-patch-invariant-violation",
        "manifestPatchContract": contract,
        "patchPlanViolations": violations,
        "requiresCompleteSource": repairable,
    }
