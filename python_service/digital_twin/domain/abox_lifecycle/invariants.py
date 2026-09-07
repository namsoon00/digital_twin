"""Pure invariant checks for an incremental Manifest patch plan."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Set, Tuple

from .contracts import (
    ABoxChangeSet,
    ManifestPatchPlan,
    PatchPlanValidation,
    PatchPlanViolation,
    RelationPatchDirective,
    ScopePlanEntry,
)


def _entries(values: Iterable[object]) -> Dict[str, ScopePlanEntry]:
    rows: Dict[str, ScopePlanEntry] = {}
    for value in values or []:
        if not isinstance(value, Mapping):
            continue
        entry = ScopePlanEntry.from_mapping(value)
        if entry.scope_id:
            rows[entry.scope_id] = entry
    return rows


def relation_patch_directives(
    plan: ManifestPatchPlan,
    incoming_scope_plan: Iterable[object],
    active_scope_plan: Iterable[object],
) -> Tuple[RelationPatchDirective, ...]:
    incoming = _entries(incoming_scope_plan)
    active = _entries(active_scope_plan)
    selected = set(plan.selected_scope_ids)
    retired = set(plan.retired_scope_ids)
    final_ids = (set(active) - retired) | selected
    directives = []
    for scope_id in sorted(set(incoming) | set(active)):
        entry = incoming.get(scope_id) or active.get(scope_id)
        if not entry or not entry.is_relation_scope:
            continue
        dependencies = set(entry.dependency_scope_ids)
        active_entry = active.get(scope_id)
        if scope_id in retired:
            disposition = "retire"
        elif scope_id in selected:
            disposition = "replace"
        elif scope_id in final_ids and dependencies.intersection(selected | retired):
            disposition = "rebind-active"
        elif scope_id in final_ids:
            disposition = "reuse-active"
        else:
            continue
        directives.append(RelationPatchDirective(
            scope_id=scope_id,
            disposition=disposition,
            relation_lifecycle=(
                entry.relation_lifecycle
                if entry
                else (active_entry.relation_lifecycle if active_entry else "source-owned")
            ),
            dependency_scope_ids=tuple(sorted(dependencies)),
            changed_dependency_scope_ids=tuple(sorted(
                dependencies.intersection(selected | retired)
            )),
        ))
    return tuple(directives)


def validate_manifest_patch_plan(
    change_set: ABoxChangeSet,
    plan: ManifestPatchPlan,
    incoming_scope_plan: Iterable[object],
    active_scope_plan: Iterable[object],
) -> PatchPlanValidation:
    """Reject an internally inconsistent patch before a TypeDB transaction."""

    incoming = _entries(incoming_scope_plan)
    active = _entries(active_scope_plan)
    if not plan.applied:
        return PatchPlanValidation(status="not-applicable")

    violations = []

    def add(code: str, scope_id: str = "", dependency_id: str = "", detail: str = "") -> None:
        violations.append(PatchPlanViolation(
            code=code,
            scope_id=scope_id,
            dependency_scope_id=dependency_id,
            detail=detail,
        ))

    selected = set(plan.selected_scope_ids)
    reused = set(plan.reused_scope_ids)
    retired = set(plan.retired_scope_ids)
    replacement_roots = set(plan.replacement_root_scope_ids)

    if plan.source_graph_complete != change_set.source_graph_complete:
        add(
            "source-completeness-mismatch",
            detail=(
                "change-set=" + change_set.source_completeness.value
                + ", plan=" + ("complete" if plan.source_graph_complete else "partial")
            ),
        )
    for scope_id in sorted(selected - set(incoming)):
        add("selected-scope-missing-from-incoming", scope_id)
    for scope_id in sorted(reused - set(active)):
        add("reused-scope-missing-from-active", scope_id)
    for scope_id in sorted(retired - set(active)):
        add("retired-scope-missing-from-active", scope_id)
    for scope_id in sorted(selected.intersection(retired)):
        add("scope-selected-and-retired", scope_id)
    for scope_id in sorted(reused.intersection(retired)):
        add("scope-reused-and-retired", scope_id)
    for scope_id in sorted(replacement_roots - selected):
        add("replacement-root-not-selected", scope_id)
    for scope_id in plan.missing_endpoint_scope_ids:
        add("applied-plan-has-missing-endpoint", dependency_id=scope_id)

    final_entries = dict(active)
    for scope_id in retired:
        final_entries.pop(scope_id, None)
    for scope_id in selected:
        if scope_id in incoming:
            final_entries[scope_id] = incoming[scope_id]
    final_ids = set(final_entries)
    for scope_id, entry in sorted(final_entries.items()):
        if not entry.is_relation_scope or entry.relation_count <= 0:
            continue
        for dependency_id in sorted(set(entry.dependency_scope_ids) - final_ids):
            add(
                "relation-dependency-missing-from-final-manifest",
                scope_id,
                dependency_id,
            )

    # A complete source owns removal of its derived companions. Rebinding an
    # older assertion after its source fact changed can reference an entity
    # that the new endpoint generation no longer contains. This invariant is
    # generic; relation kinds declare lifecycle metadata when the scope plan
    # is built, and the planner no longer needs provider-specific knowledge.
    if change_set.source_graph_complete:
        changed_dependencies: Set[str] = selected | retired
        for scope_id in sorted(set(active).intersection(incoming)):
            before = active[scope_id]
            after = incoming[scope_id]
            if (
                after.relation_lifecycle != "derived-companion"
                or not after.is_relation_scope
                or before.base_fingerprint == after.base_fingerprint
                or not set(before.dependency_scope_ids + after.dependency_scope_ids).intersection(
                    changed_dependencies
                )
                or scope_id in selected
            ):
                continue
            add(
                "changed-derived-relation-not-replaced",
                scope_id,
                detail="A complete source changed a derived relation assertion but reused its active generation.",
            )

    return PatchPlanValidation(
        status="valid" if not violations else "invalid",
        violations=tuple(violations),
    )
