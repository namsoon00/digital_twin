"""Read-only immutable storage identity checks and endpoint diagnostics."""

from typing import Dict, Iterable, List, Mapping, Tuple

from .identity import ontology_storage_id, relation_row_id
from .ports import CandidateValidationStore


def scoped_abox_storage_identity(row: Dict[str, object], owner_kind: str) -> Dict[str, str]:
    """Return the immutable TypeDB identity expected for one scoped row.

    A scoped generation can be referenced by more than one Worldview
    Manifest.  The manifest itself is intentionally not part of the
    physical storage identity, so a failed candidate and a later retry can
    safely share the same immutable fact instead of attempting a duplicate
    insert.
    """
    values = dict(row or {})
    kind = str(owner_kind or "node").strip() or "node"
    canonical_id = (
        relation_row_id(values)
        if kind == "relation"
        else str(values.get("id") or "").strip()
    )
    return {
        "storageId": ontology_storage_id(values, canonical_id, kind),
        "id": canonical_id,
        "ontologyBox": str(values.get("ontologyBox") or "ABox").strip() or "ABox",
        "snapshotId": str(values.get("snapshotId") or values.get("aboxSnapshotId") or "").strip(),
    }


def scoped_abox_storage_rows_unique(rows: Iterable[Dict[str, object]], owner_kind: str) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Deduplicate equal physical writes and reject incompatible aliases."""
    unique_rows: List[Dict[str, object]] = []
    identities: Dict[str, Dict[str, str]] = {}
    conflicts: List[Dict[str, object]] = []
    for raw in rows or []:
        row = dict(raw or {})
        identity = scoped_abox_storage_identity(row, owner_kind)
        storage_id = str(identity.get("storageId") or "")
        previous = identities.get(storage_id)
        if previous is None:
            identities[storage_id] = identity
            unique_rows.append(row)
            continue
        if previous != identity:
            conflicts.append({
                "ownerKind": owner_kind,
                "storageId": storage_id,
                "expected": previous,
                "actual": identity,
            })
    return unique_rows, conflicts


def scoped_abox_storage_reuse_plan(store: CandidateValidationStore, node_rows: Iterable[Dict[str, object]], relation_rows: Iterable[Dict[str, object]], assume_missing_storage: bool=False) -> Dict[str, object]:
    """Split a candidate into missing writes and reusable immutable rows."""
    unique_nodes, node_conflicts = store.scoped_abox_storage_rows_unique(node_rows, "node")
    unique_relations, relation_conflicts = store.scoped_abox_storage_rows_unique(relation_rows, "relation")
    node_identities = [store.scoped_abox_storage_identity(row, "node") for row in unique_nodes]
    relation_identities = [store.scoped_abox_storage_identity(row, "relation") for row in unique_relations]
    existing = (
        {"nodes": {}, "relations": {}}
        if assume_missing_storage
        else store.scoped_abox_storage_rows_by_id(
            [item["storageId"] for item in node_identities],
            [item["storageId"] for item in relation_identities],
        )
    )
    existing_nodes = dict(existing.get("nodes") or {})
    existing_relations = dict(existing.get("relations") or {})

    def partition(rows, identities, actual_by_storage_id, owner_kind):
        missing = []
        reused = []
        conflicts = []
        for row, identity in zip(rows, identities):
            storage_id = str(identity.get("storageId") or "")
            actual = dict(actual_by_storage_id.get(storage_id) or {})
            if not actual:
                missing.append(row)
                continue
            comparable = {
                key: str(actual.get(key) or "").strip()
                for key in ["id", "ontologyBox", "snapshotId"]
            }
            expected = {
                key: str(identity.get(key) or "").strip()
                for key in ["id", "ontologyBox", "snapshotId"]
            }
            if comparable != expected:
                conflicts.append({
                    "ownerKind": owner_kind,
                    "storageId": storage_id,
                    "expected": expected,
                    "actual": comparable,
                })
                continue
            reused.append(row)
        return missing, reused, conflicts

    missing_nodes, reused_nodes, existing_node_conflicts = partition(
        unique_nodes,
        node_identities,
        existing_nodes,
        "node",
    )
    missing_relations, reused_relations, existing_relation_conflicts = partition(
        unique_relations,
        relation_identities,
        existing_relations,
        "relation",
    )
    conflicts = [
        *node_conflicts,
        *relation_conflicts,
        *existing_node_conflicts,
        *existing_relation_conflicts,
    ]
    return {
        "status": "conflict" if conflicts else "ok",
        "nodeRows": unique_nodes,
        "relationRows": unique_relations,
        "nodeRowsToInsert": missing_nodes,
        "relationRowsToInsert": missing_relations,
        "reusedNodeRows": reused_nodes,
        "reusedRelationRows": reused_relations,
        "conflicts": conflicts,
        "storageLookupMode": (
            "fresh-candidate-known-empty"
            if assume_missing_storage
            else "immutable-storage-identity-read"
        ),
    }


def scoped_abox_storage_identity_counts(store: CandidateValidationStore, node_rows: Iterable[Dict[str, object]], relation_rows: Iterable[Dict[str, object]]) -> Dict[str, object]:
    """Verify a staged candidate from its exact physical row identities."""
    plan = store.scoped_abox_storage_reuse_plan(node_rows, relation_rows)
    expected_counts: Dict[str, Dict[str, int]] = {}
    actual_counts: Dict[str, Dict[str, int]] = {}
    for key, rows in [("entityCount", plan.get("nodeRows") or []), ("relationCount", plan.get("relationRows") or [])]:
        for row in rows:
            scope_id = str((row or {}).get("scopeId") or "").strip()
            if not scope_id:
                continue
            expected_counts.setdefault(scope_id, {"entityCount": 0, "relationCount": 0})[key] += 1
    for key, rows in [("entityCount", plan.get("reusedNodeRows") or []), ("relationCount", plan.get("reusedRelationRows") or [])]:
        for row in rows:
            scope_id = str((row or {}).get("scopeId") or "").strip()
            if not scope_id:
                continue
            actual_counts.setdefault(scope_id, {"entityCount": 0, "relationCount": 0})[key] += 1
    missing = [
        *[
            store.scoped_abox_storage_identity(row, "node").get("storageId")
            for row in plan.get("nodeRowsToInsert") or []
        ],
        *[
            store.scoped_abox_storage_identity(row, "relation").get("storageId")
            for row in plan.get("relationRowsToInsert") or []
        ],
    ]
    return {
        "status": "ok" if not plan.get("conflicts") and not missing else "incomplete",
        "expectedCountsByScope": expected_counts,
        "actualCountsByScope": actual_counts,
        "missingStorageIds": [str(value or "") for value in missing if str(value or "")],
        "conflicts": list(plan.get("conflicts") or []),
    }


def missing_relation_endpoint_storage_ids(relation_rows: Iterable[Dict[str, object]], node_inventory: Mapping[str, object]) -> List[str]:
    """Return physical relation endpoints absent from the staged ABox.

    TypeDB accepts a ``match ... insert`` whose match resolves no rows
    without raising a write error. A copy-on-write relation would then be
    reported as submitted even though no assertion was created. Verify
    endpoint identities after node commits and before any relation batch
    so an incomplete candidate cannot produce partial relation scopes.
    """

    expected = {
        str(value or "").strip()
        for row in relation_rows or []
        for value in (
            (row or {}).get("sourceStorageId"),
            (row or {}).get("targetStorageId"),
        )
        if str(value or "").strip()
    }
    available = {
        str(value or "").strip()
        for value in dict(node_inventory or {})
        if str(value or "").strip()
    }
    return sorted(expected - available)


def missing_relation_endpoint_diagnostics(relation_rows: Iterable[Dict[str, object]], missing_storage_ids: Iterable[str]) -> List[Dict[str, str]]:
    """Describe a missing physical endpoint without another graph read."""

    missing = {
        str(value or "").strip()
        for value in missing_storage_ids or []
        if str(value or "").strip()
    }
    rows: List[Dict[str, str]] = []
    seen = set()
    for raw in relation_rows or []:
        relation = dict(raw or {})
        for side, node_key, storage_key in [
            ("source", "source", "sourceStorageId"),
            ("target", "target", "targetStorageId"),
        ]:
            storage_id = str(relation.get(storage_key) or "").strip()
            if storage_id not in missing:
                continue
            key = (
                storage_id,
                str(relation.get("scopeId") or ""),
                str(relation.get("type") or ""),
                side,
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "storageId": storage_id,
                "nodeId": str(relation.get(node_key) or ""),
                "side": side,
                "relationType": str(relation.get("type") or ""),
                "relationScopeId": str(relation.get("scopeId") or ""),
                "relationGenerationId": str(
                    relation.get("snapshotId")
                    or relation.get("aboxSnapshotId")
                    or ""
                ),
            })
    return rows
