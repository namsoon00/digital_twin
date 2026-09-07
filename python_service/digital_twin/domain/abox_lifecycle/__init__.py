"""Typed contracts for incremental ABox lifecycle management."""

from .contracts import (
    ABOX_CHANGE_SET_VERSION,
    MANIFEST_PATCH_PLAN_VERSION,
    MANIFEST_PATCH_BOUNDARY_VERSION,
    ABoxChangeSet,
    ManifestPatchPlan,
    PatchPlanValidation,
    PatchPlanViolation,
    RelationPatchDirective,
    ScopePlanEntry,
    SourceGraphCompleteness,
    relation_lifecycle_for_scope,
)
from .planner import finalize_manifest_patch_plan

__all__ = [
    "ABOX_CHANGE_SET_VERSION",
    "MANIFEST_PATCH_PLAN_VERSION",
    "MANIFEST_PATCH_BOUNDARY_VERSION",
    "ABoxChangeSet",
    "ManifestPatchPlan",
    "PatchPlanValidation",
    "PatchPlanViolation",
    "RelationPatchDirective",
    "ScopePlanEntry",
    "SourceGraphCompleteness",
    "finalize_manifest_patch_plan",
    "relation_lifecycle_for_scope",
]
