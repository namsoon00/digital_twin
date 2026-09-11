"""Select semantic changes, physical rebinds and exact active-image reuse."""

from typing import Dict, Iterable, List, Mapping

from digital_twin.modules.reasoning.domain.ontology_current_state import CURRENT_STATE_ABOX_PERSISTENCE_MODE
from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
from digital_twin.modules.reasoning.domain.ontology_change_impact import scope_symbol
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import clean_symbols_from_payload
from digital_twin.infrastructure.graph_store_payloads import number_or_none


def scoped_abox_semantic_changed_scope_ids(logical_scope_plan: Iterable[Dict[str, object]], active_metadata: Dict[str, object], current_state_mode: bool, migration_mode: str='', current_state_persistence_mode: str=CURRENT_STATE_ABOX_PERSISTENCE_MODE) -> List[str]:
    """Return scopes whose logical content, rather than storage binding, changed."""

    scope_plan = [dict(item or {}) for item in logical_scope_plan or []]
    active = dict(active_metadata or {})
    active_fingerprints = dict(active.get("scopeFingerprints") or {})
    active_generations = dict(active.get("scopeGenerationIds") or {})
    scoped_active = (
        str(active.get("scopedAboxManifestVersion") or "")
        == SCOPED_ABOX_MANIFEST_VERSION
    )
    active_current_state = str(
        active.get("persistenceMode") or active.get("physicalStateMode") or ""
    ) == str(current_state_persistence_mode or CURRENT_STATE_ABOX_PERSISTENCE_MODE)
    full_current_state_migration = bool(
        current_state_mode
        and not active_current_state
        and str(migration_mode or "").strip().lower() == "full"
    )
    changed = {
        str(item.get("scopeId") or "")
        for item in scope_plan
        if str(item.get("scopeId") or "") and (
            not scoped_active
            or full_current_state_migration
            or str(
                active_fingerprints.get(str(item.get("scopeId") or "")) or ""
            ) != str(item.get("fingerprint") or "")
            or (
                not current_state_mode
                and str(
                    active_generations.get(str(item.get("scopeId") or "")) or ""
                ) != str(item.get("generationId") or "")
            )
        )
    }
    return [
        str(item.get("scopeId") or "")
        for item in scope_plan
        if str(item.get("scopeId") or "") in changed
    ]


def scoped_abox_changed_scope_ids(logical_scope_plan: Iterable[Dict[str, object]], active_metadata: Dict[str, object], current_state_mode: bool, migration_mode: str='', current_state_persistence_mode: str=CURRENT_STATE_ABOX_PERSISTENCE_MODE, relation_rebind_root_scope_ids: Iterable[str]=None) -> List[str]:
    """Select semantic writes plus physical relation endpoint rebinds."""

    scope_plan = [dict(item or {}) for item in logical_scope_plan or []]
    changed = set(scoped_abox_semantic_changed_scope_ids(
        scope_plan,
        active_metadata,
        current_state_mode=current_state_mode,
        migration_mode=migration_mode,
        current_state_persistence_mode=current_state_persistence_mode,
    ))

    requested_rebind_roots = {
        str(value or "").strip()
        for value in relation_rebind_root_scope_ids or []
        if str(value or "").strip()
    }
    rebind_roots = (
        changed.intersection(requested_rebind_roots)
        if relation_rebind_root_scope_ids is not None
        else set(changed)
    )
    # A TypeDB relation is bound to the physical storage identities of
    # both endpoints. Copy-on-write gives a changed node scope a new
    # physical identity even when an incident relation's own semantic
    # payload is unchanged. Rewrite every dependent link scope as well,
    # otherwise the active Manifest points at a new node while its reused
    # relations still point at the retired node generation.
    while rebind_roots:
        dependent = {
            str(item.get("scopeId") or "")
            for item in scope_plan
            if str(item.get("scopeId") or "") not in changed
            and rebind_roots.intersection({
                str(value or "").strip()
                for value in item.get("dependencyScopeIds") or []
                if str(value or "").strip()
            })
        }
        if not dependent:
            break
        changed.update(dependent)
        # Preserve transitive physical dependencies (including a
        # relation that plays a role in another relation). The caller's
        # explicit root set prevents endpoint companion nodes from
        # becoming unrelated fan-out roots.
        rebind_roots = dependent

    return [
        str(item.get("scopeId") or "")
        for item in scope_plan
        if str(item.get("scopeId") or "") in changed
    ]


def scoped_abox_rebind_only_relation_scope_ids(logical_scope_plan: Iterable[Dict[str, object]], semantic_changed_scope_ids: Iterable[str], physical_changed_scope_ids: Iterable[str]) -> List[str]:
    """Return relation scopes rewritten only because an endpoint moved.

    Copy-on-write changes a node's physical storage identity. Incident
    relations must then be written into a new physical generation even
    when their assertion did not change. Keeping this set separate from
    semantic changes prevents the current in-memory graph from replacing
    an active relation that belongs to another authoritative event.
    """

    semantic_changed = {
        str(value or "").strip()
        for value in semantic_changed_scope_ids or []
        if str(value or "").strip()
    }
    physical_changed = {
        str(value or "").strip()
        for value in physical_changed_scope_ids or []
        if str(value or "").strip()
    }
    return sorted({
        str(item.get("scopeId") or "").strip()
        for item in logical_scope_plan or []
        if str(item.get("scopeId") or "").strip() in physical_changed
        and str(item.get("scopeId") or "").strip() not in semantic_changed
        and int(number_or_none(item.get("relationCount")) or 0) > 0
    })


def scoped_abox_active_reuse_scope_ids(physical_scope_plan: Iterable[Dict[str, object]], active_generations: Mapping[str, object], physical_changed_scope_ids: Iterable[str], deferred_scope_ids: Iterable[str], rebind_only_relation_scope_ids: Iterable[str]) -> Dict[str, object]:
    """Select exact active rows needed to reconcile candidate relations.

    A target graph can contain a relation to an unchanged endpoint that
    sits outside the target subject or fact family. That endpoint remains
    in the active Manifest, but it is not necessarily listed as a deferred
    incoming scope. Read every unchanged dependency of a changed relation
    explicitly so candidate storage IDs can be resolved without expanding
    the source graph. Missing logical endpoint IDs still fail closed in
    ``scoped_abox_candidate_persistence_rows``.
    """

    active_by_scope = {
        str(scope_id or "").strip(): str(generation_id or "").strip()
        for scope_id, generation_id in dict(active_generations or {}).items()
        if str(scope_id or "").strip() and str(generation_id or "").strip()
    }
    changed = {
        str(value or "").strip()
        for value in physical_changed_scope_ids or []
        if str(value or "").strip()
    }
    rebind_only = {
        str(value or "").strip()
        for value in rebind_only_relation_scope_ids or []
        if str(value or "").strip()
    }
    requested = {
        str(value or "").strip()
        for value in deferred_scope_ids or []
        if str(value or "").strip() in active_by_scope
    }.union({
        scope_id
        for scope_id in rebind_only
        if scope_id in active_by_scope
    })
    relation_endpoint_scopes = set()
    relation_candidates = changed.union(rebind_only)
    for raw in physical_scope_plan or []:
        item = dict(raw or {})
        scope_id = str(item.get("scopeId") or "").strip()
        scope_type = str(item.get("scopeType") or "").strip().lower()
        if (
            scope_id not in relation_candidates
            or (scope_type != "link" and not scope_id.startswith("link:"))
        ):
            continue
        for raw_dependency in item.get("dependencyScopeIds") or []:
            dependency_scope_id = str(raw_dependency or "").strip()
            if (
                dependency_scope_id
                and dependency_scope_id not in changed
                and dependency_scope_id in active_by_scope
            ):
                requested.add(dependency_scope_id)
                relation_endpoint_scopes.add(dependency_scope_id)
    return {
        "scopeIds": sorted(requested),
        "relationEndpointScopeIds": sorted(relation_endpoint_scopes),
    }


def scoped_abox_native_index_reuse_scope_ids(target_patch: Mapping[str, object], active_generations: Mapping[str, object], physical_changed_scope_ids: Iterable[str], candidate_scope_plan: Iterable[Mapping[str, object]]=None) -> List[str]:
    """Read the unchanged target image needed by the physical rule index.

    A fact-slice update can remove the only decision-eligible event in a
    selected scope without rewriting the stock anchor. The candidate then
    has no incoming subject row even though its merged planner topology
    still owns the stock. Reuse every unchanged active scope for the
    replacement symbol while constructing the control-plane evidence
    index; persistence still writes only physically changed scopes.
    """

    patch = dict(target_patch or {})
    replacement_symbols = set(clean_symbols_from_payload(
        patch.get("replacementSymbols") or []
    ))
    if not replacement_symbols:
        return []
    active_scope_ids = {
        str(scope_id or "").strip()
        for scope_id, generation_id in dict(active_generations or {}).items()
        if str(scope_id or "").strip()
        and str(generation_id or "").strip()
    }
    changed = {
        str(scope_id or "").strip()
        for scope_id in physical_changed_scope_ids or []
        if str(scope_id or "").strip()
    }
    retired = {
        str(scope_id or "").strip()
        for scope_id in patch.get("retiredScopeIds") or []
        if str(scope_id or "").strip()
    }
    explicit_reused = {
        str(scope_id or "").strip()
        for scope_id in patch.get("reusedActiveScopeIds") or []
        if str(scope_id or "").strip()
    }
    candidate_scope_ids = {
        str(dict(item or {}).get("scopeId") or "").strip()
        for item in candidate_scope_plan or []
        if str(dict(item or {}).get("scopeId") or "").strip()
    }
    # Compact target patches retain only reuse counts. The complete merged
    # scope plan is the authoritative list of active generations that the
    # candidate will keep, so use it when the optional verbose reuse list is
    # absent. This also excludes retired scopes without trusting a missing
    # patch detail.
    reused = explicit_reused or active_scope_ids.intersection(candidate_scope_ids)

    def belongs_to_replacement_symbol(scope_id: str) -> bool:
        owned_symbol = str(scope_symbol(scope_id) or "").upper().strip()
        if owned_symbol in replacement_symbols:
            return True
        normalized_scope = scope_id.upper()
        return any(
            ("SYMBOL:" + symbol + ":") in normalized_scope
            for symbol in replacement_symbols
        )

    return sorted(
        scope_id
        for scope_id in reused
        if scope_id in active_scope_ids
        and scope_id not in changed
        and scope_id not in retired
        and belongs_to_replacement_symbol(scope_id)
    )
