"""Immutable contracts at the ABox patch-planning boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, Mapping, Tuple


ABOX_CHANGE_SET_VERSION = "abox-change-set-v1"
MANIFEST_PATCH_PLAN_VERSION = "manifest-patch-plan-v1"
PATCH_PLAN_VALIDATION_VERSION = "manifest-patch-validation-v1"
MANIFEST_PATCH_BOUNDARY_VERSION = "abox-manifest-patch-boundary-v1"
SCOPE_NODE_INVENTORY_VERSION = "scope-node-inventory-v1"
RELATION_ENDPOINT_BINDING_VERSION = "relation-endpoint-binding-v1"
DERIVED_COMPANION_RELATION_TYPES = frozenset({
    "HAS_DATA_QUALITY",
    "HAS_EVIDENCE",
})


def _text(value: object) -> str:
    return str(value or "").strip()


def _texts(values: object, uppercase: bool = False) -> Tuple[str, ...]:
    if isinstance(values, str):
        values = [values]
    if isinstance(values, Mapping) or values is None:
        return ()
    try:
        rows = list(values)
    except TypeError:
        return ()
    cleaned = {
        (_text(value).upper() if uppercase else _text(value))
        for value in rows
        if _text(value)
    }
    return tuple(sorted(cleaned))


def _endpoint_bindings(value: object) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    if not isinstance(value, Mapping):
        return ()
    return tuple(sorted(
        (
            _text(scope_id),
            _texts(node_ids),
        )
        for scope_id, node_ids in value.items()
        if _text(scope_id) and _texts(node_ids)
    ))


class SourceGraphCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


def relation_lifecycle_for_scope(
    scope_type: object,
    scope_family: object,
    relation_rows: Iterable[object],
) -> str:
    """Classify relation ownership once when a scope plan is authored."""

    rows = [dict(value) for value in relation_rows or [] if isinstance(value, Mapping)]
    explicit = {
        _text(dict(row.get("properties") or {}).get("relationLifecycle"))
        for row in rows
        if _text(dict(row.get("properties") or {}).get("relationLifecycle"))
    }
    if len(explicit) == 1:
        return next(iter(explicit))
    relation_types = {_text(row.get("type")).upper() for row in rows if _text(row.get("type"))}
    if (
        _text(scope_family).lower() == "quality"
        or (relation_types and relation_types.issubset(DERIVED_COMPANION_RELATION_TYPES))
    ):
        return "derived-companion"
    if _text(scope_type) == "link" or rows:
        return "source-owned"
    return "not-a-relation"


@dataclass(frozen=True)
class ABoxChangeSet:
    """Meaning of one graph input before any persistence plan is selected."""

    target_symbols: Tuple[str, ...]
    source_completeness: SourceGraphCompleteness
    event_boundary_authoritative: bool = False
    requested_fact_families: Tuple[str, ...] = ()
    requested_fact_families_by_symbol: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()
    retention_mode: str = ""
    explicit_removed_scope_ids: Tuple[str, ...] = ()
    version: str = ABOX_CHANGE_SET_VERSION

    @classmethod
    def from_inputs(
        cls,
        target_symbols: Iterable[object],
        fact_slot_plan: Mapping[str, object] = None,
        source_graph_complete: bool = True,
        retention_mode: object = "",
    ) -> "ABoxChangeSet":
        slot = dict(fact_slot_plan or {})
        raw_by_symbol = slot.get("requestedFactFamiliesBySymbol")
        raw_by_symbol = raw_by_symbol if isinstance(raw_by_symbol, Mapping) else {}
        by_symbol = tuple(sorted(
            (
                _text(symbol).upper(),
                _texts(families),
            )
            for symbol, families in raw_by_symbol.items()
            if _text(symbol)
        ))
        return cls(
            target_symbols=_texts(target_symbols, uppercase=True),
            source_completeness=(
                SourceGraphCompleteness.COMPLETE
                if source_graph_complete
                else SourceGraphCompleteness.PARTIAL
            ),
            event_boundary_authoritative=bool(slot.get("eventBoundaryAuthoritative")),
            requested_fact_families=_texts(slot.get("requestedFactFamilies") or []),
            requested_fact_families_by_symbol=by_symbol,
            retention_mode=_text(retention_mode),
            explicit_removed_scope_ids=_texts(
                slot.get("removedScopeIds")
                or slot.get("explicitRemovedScopeIds")
                or []
            ),
        )

    @property
    def source_graph_complete(self) -> bool:
        return self.source_completeness == SourceGraphCompleteness.COMPLETE

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "targetSymbols": list(self.target_symbols),
            "sourceCompleteness": self.source_completeness.value,
            "sourceGraphComplete": self.source_graph_complete,
            "eventBoundaryAuthoritative": self.event_boundary_authoritative,
            "requestedFactFamilies": list(self.requested_fact_families),
            "requestedFactFamiliesBySymbol": {
                symbol: list(families)
                for symbol, families in self.requested_fact_families_by_symbol
            },
            "retentionMode": self.retention_mode,
            "explicitRemovedScopeIds": list(self.explicit_removed_scope_ids),
        }


@dataclass(frozen=True)
class ScopePlanEntry:
    scope_id: str
    scope_type: str
    scope_family: str
    generation_id: str
    base_fingerprint: str
    dependency_scope_ids: Tuple[str, ...]
    entity_count: int = 0
    relation_count: int = 0
    evidence_count: int = 0
    relation_lifecycle: str = "not-a-relation"
    lifecycle_owner_scope_id: str = ""
    deletion_semantics: str = "retain-on-omission"
    node_inventory_version: str = ""
    node_ids: Tuple[str, ...] = ()
    relation_endpoint_binding_version: str = ""
    relation_endpoint_bindings: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ScopePlanEntry":
        row = dict(value or {})
        scope_type = _text(row.get("scopeType"))
        scope_family = _text(row.get("scopeFamily"))
        relation_count = max(0, int(row.get("relationCount") or 0))
        lifecycle = _text(row.get("relationLifecycle"))
        if not lifecycle:
            if scope_type != "link" and relation_count <= 0:
                lifecycle = "not-a-relation"
            elif scope_family.lower() == "quality":
                # Compatibility for manifests created before lifecycle metadata
                # was persisted. New scope plans always declare this value.
                lifecycle = "derived-companion"
            else:
                lifecycle = "source-owned"
        return cls(
            scope_id=_text(row.get("scopeId")),
            scope_type=scope_type,
            scope_family=scope_family,
            generation_id=_text(row.get("generationId")),
            base_fingerprint=_text(row.get("baseFingerprint")),
            dependency_scope_ids=_texts(row.get("dependencyScopeIds") or []),
            entity_count=max(0, int(row.get("entityCount") or 0)),
            relation_count=relation_count,
            evidence_count=max(0, int(row.get("evidenceCount") or 0)),
            relation_lifecycle=lifecycle,
            lifecycle_owner_scope_id=(
                _text(row.get("lifecycleOwnerScopeId"))
                or _text(row.get("scopeId"))
            ),
            deletion_semantics=(
                _text(row.get("deletionSemantics"))
                or (
                    "complete-source-assertion-presence"
                    if lifecycle == "derived-companion"
                    else "retain-on-omission"
                )
            ),
            node_inventory_version=_text(row.get("nodeInventoryVersion")),
            node_ids=_texts(row.get("nodeIds") or []),
            relation_endpoint_binding_version=_text(
                row.get("relationEndpointBindingVersion")
            ),
            relation_endpoint_bindings=_endpoint_bindings(
                row.get("relationEndpointNodeIdsByScope")
            ),
        )

    @property
    def is_relation_scope(self) -> bool:
        return self.scope_type == "link" or self.relation_count > 0


@dataclass(frozen=True)
class ManifestPatchPlan:
    status: str
    applied: bool
    source_graph_complete: bool
    selected_scope_ids: Tuple[str, ...] = ()
    reused_scope_ids: Tuple[str, ...] = ()
    deferred_scope_ids: Tuple[str, ...] = ()
    retired_scope_ids: Tuple[str, ...] = ()
    replacement_root_scope_ids: Tuple[str, ...] = ()
    replacement_symbols: Tuple[str, ...] = ()
    missing_endpoint_scope_ids: Tuple[str, ...] = ()
    fallback_reason: str = ""
    version: str = MANIFEST_PATCH_PLAN_VERSION

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ManifestPatchPlan":
        row = dict(value or {})
        return cls(
            status=_text(row.get("status")),
            applied=bool(row.get("applied")),
            source_graph_complete=bool(row.get("sourceGraphComplete", True)),
            selected_scope_ids=_texts(row.get("selectedIncomingScopeIds") or []),
            reused_scope_ids=_texts(row.get("reusedActiveScopeIds") or []),
            deferred_scope_ids=_texts(row.get("deferredScopeIds") or []),
            retired_scope_ids=_texts(row.get("retiredScopeIds") or []),
            replacement_root_scope_ids=_texts(row.get("replacementRootScopeIds") or []),
            replacement_symbols=_texts(row.get("replacementSymbols") or [], uppercase=True),
            missing_endpoint_scope_ids=_texts(row.get("missingEndpointScopeIds") or []),
            fallback_reason=_text(row.get("fallbackReason")),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "status": self.status,
            "applied": self.applied,
            "sourceGraphComplete": self.source_graph_complete,
            "selectedScopeIds": list(self.selected_scope_ids),
            "reusedScopeIds": list(self.reused_scope_ids),
            "deferredScopeIds": list(self.deferred_scope_ids),
            "retiredScopeIds": list(self.retired_scope_ids),
            "replacementRootScopeIds": list(self.replacement_root_scope_ids),
            "replacementSymbols": list(self.replacement_symbols),
            "missingEndpointScopeIds": list(self.missing_endpoint_scope_ids),
            "fallbackReason": self.fallback_reason,
        }


@dataclass(frozen=True)
class RelationPatchDirective:
    scope_id: str
    disposition: str
    relation_lifecycle: str
    dependency_scope_ids: Tuple[str, ...] = ()
    changed_dependency_scope_ids: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "scopeId": self.scope_id,
            "disposition": self.disposition,
            "relationLifecycle": self.relation_lifecycle,
            "dependencyScopeIds": list(self.dependency_scope_ids),
            "changedDependencyScopeIds": list(self.changed_dependency_scope_ids),
        }


@dataclass(frozen=True)
class PatchPlanViolation:
    code: str
    scope_id: str = ""
    dependency_scope_id: str = ""
    detail: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "scopeId": self.scope_id,
            "dependencyScopeId": self.dependency_scope_id,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PatchPlanValidation:
    status: str
    violations: Tuple[PatchPlanViolation, ...] = ()
    version: str = PATCH_PLAN_VALIDATION_VERSION

    @property
    def valid(self) -> bool:
        return not self.violations

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "status": self.status,
            "valid": self.valid,
            "violationCount": len(self.violations),
            "violations": [item.to_dict() for item in self.violations],
        }
