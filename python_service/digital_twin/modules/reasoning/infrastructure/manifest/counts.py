"""manifest: counts through explicit injected capabilities."""

from digital_twin.modules.reasoning.domain.ontology_change_impact import scope_family, scope_symbol
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.abox_candidates.identity import (
    ontology_row_content_fingerprint,
    ontology_storage_id,
    relation_row_id,
)
from typing import Dict, Iterable
from .counts_ports import ManifestCountsStore


def scoped_abox_counts_by_scope(
    node_rows: Iterable[Dict[str, object]], relation_rows: Iterable[Dict[str, object]]
) -> Dict[str, Dict[str, int]]:
    """Count already-deduplicated physical rows by owning scope."""
    counts: Dict[str, Dict[str, int]] = {}
    for count_key, rows in (
        ("entityCount", node_rows or []),
        ("relationCount", relation_rows or []),
    ):
        for row in rows:
            scope_id = str((row or {}).get("scopeId") or "").strip()
            if not scope_id:
                continue
            counts.setdefault(scope_id, {"entityCount": 0, "relationCount": 0})[count_key] += 1
    return counts


def scoped_abox_relation_breakdown(
    relation_rows: Iterable[Dict[str, object]], bucket_limit: int = 24
) -> Dict[str, object]:
    """Return bounded physical-write telemetry for scoped ABox relations.

    The payload is operational audit data, never RuleBox input. It makes
    a large write attributable to a relation type, factual family,
    instrument, and owning scope without retaining every relation row.
    """
    limit = max(1, int(bucket_limit or 24))
    counters: Dict[str, Dict[str, int]] = {
        "relationType": {},
        "scopeFamily": {},
        "symbol": {},
        "scope": {},
    }
    relation_count = 0

    def increment(counter: Dict[str, int], key: object) -> None:
        value = str(key or "").strip() or "shared"
        counter[value] = counter.get(value, 0) + 1

    for raw in relation_rows or []:
        row = dict(raw or {})
        scope_id = str(row.get("scopeId") or "").strip()
        symbol = scope_symbol(scope_id) or str(row.get("symbol") or "").strip().upper()
        relation_count += 1
        increment(counters["relationType"], row.get("type") or "unknown")
        increment(counters["scopeFamily"], scope_family(scope_id))
        increment(counters["symbol"], symbol)
        increment(counters["scope"], scope_id)

    def compact(counter: Dict[str, int]) -> Dict[str, object]:
        ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
        selected = ordered[:limit]
        return {
            "distinctCount": len(ordered),
            "items": [{"key": key, "count": count} for key, count in selected],
            "remainingCount": sum(count for _key, count in ordered[limit:]),
        }

    return {
        "version": "scoped-abox-relation-breakdown-v1",
        "relationCount": relation_count,
        "byRelationType": compact(counters["relationType"]),
        "byScopeFamily": compact(counters["scopeFamily"]),
        "bySymbol": compact(counters["symbol"]),
        "byScope": compact(counters["scope"]),
    }


def scoped_abox_relation_persistence_summary(write_plan: Dict[str, object]) -> Dict[str, object]:
    """Expose requested, inserted, and reused relation writes for audit."""
    values = dict(write_plan or {})
    expected = dict(values.get("expectedCountsByScope") or {})
    inserted = dict(values.get("insertedCountsByScope") or {})
    reused = dict(values.get("reusedCountsByScope") or {})

    def counts(source: Dict[str, object], scope_id: str) -> Dict[str, int]:
        row = dict(source.get(scope_id) or {})
        return {
            "entityCount": max(0, int(row.get("entityCount") or 0)),
            "relationCount": max(0, int(row.get("relationCount") or 0)),
        }

    scope_rows = []
    for scope_id in sorted(set(expected) | set(inserted) | set(reused)):
        requested_counts = counts(expected, scope_id)
        inserted_counts = counts(inserted, scope_id)
        reused_counts = counts(reused, scope_id)
        scope_rows.append(
            {
                "scopeId": scope_id,
                "scopeFamily": scope_family(scope_id),
                "symbol": scope_symbol(scope_id),
                "requested": requested_counts,
                "inserted": inserted_counts,
                "reused": reused_counts,
            }
        )
    scope_rows.sort(
        key=lambda item: (
            -int(item["inserted"]["entityCount"] + item["inserted"]["relationCount"]),
            -int(item["requested"]["entityCount"] + item["requested"]["relationCount"]),
            str(item["scopeId"]),
        )
    )
    return {
        "version": "scoped-abox-relation-persistence-v2",
        "requested": dict(values.get("requestedRelationBreakdown") or {}),
        "inserted": dict(values.get("insertedRelationBreakdown") or {}),
        "reused": dict(values.get("reusedRelationBreakdown") or {}),
        "scopeCount": len(scope_rows),
        "scopes": scope_rows[:40],
        "remainingScopeCount": max(0, len(scope_rows) - 40),
    }


def merged_scoped_abox_counts(*count_sets: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, int]]:
    """Combine physical and reused rows without changing their ownership."""
    merged: Dict[str, Dict[str, int]] = {}
    for count_set in count_sets:
        for scope_id, raw in dict(count_set or {}).items():
            clean_scope_id = str(scope_id or "").strip()
            if not clean_scope_id:
                continue
            target = merged.setdefault(clean_scope_id, {"entityCount": 0, "relationCount": 0})
            target["entityCount"] += int(number_or_none((raw or {}).get("entityCount")) or 0)
            target["relationCount"] += int(number_or_none((raw or {}).get("relationCount")) or 0)
    return merged


def current_state_delta_plan(
    node_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
    inventory: Dict[str, Dict[str, Dict[str, object]]],
) -> Dict[str, object]:
    """Compare a desired slot image with the retained inactive image."""

    desired_nodes: Dict[str, Dict[str, object]] = {}
    for raw in node_rows or []:
        row = dict(raw or {})
        storage_id = ontology_storage_id(row, row.get("id"), "node")
        row["contentFingerprint"] = ontology_row_content_fingerprint(
            row,
            "node",
        )
        desired_nodes[storage_id] = row
    desired_relations: Dict[str, Dict[str, object]] = {}
    for raw in relation_rows or []:
        row = dict(raw or {})
        storage_id = ontology_storage_id(
            row,
            relation_row_id(row),
            "relation",
        )
        row["contentFingerprint"] = ontology_row_content_fingerprint(
            row,
            "relation",
        )
        desired_relations[storage_id] = row
    existing_nodes = dict((inventory or {}).get("nodes") or {})
    existing_relations = dict((inventory or {}).get("relations") or {})
    changed_node_ids = {
        storage_id
        for storage_id, row in desired_nodes.items()
        if str((existing_nodes.get(storage_id) or {}).get("contentFingerprint") or "")
        != str(row.get("contentFingerprint") or "")
    }
    removed_node_ids = set(existing_nodes) - set(desired_nodes)
    changed_or_removed_node_ids = changed_node_ids | removed_node_ids
    changed_node_scopes = {
        str(
            (desired_nodes.get(storage_id) or existing_nodes.get(storage_id) or {}).get("scopeId")
            or ""
        )
        for storage_id in changed_or_removed_node_ids
    }
    changed_node_scopes.discard("")

    def relation_touches_changed_node(row: Dict[str, object]) -> bool:
        source_storage_id = str(
            row.get("sourceStorageId") or ontology_storage_id(row, row.get("source"), "node")
        ).strip()
        target_storage_id = str(
            row.get("targetStorageId") or ontology_storage_id(row, row.get("target"), "node")
        ).strip()
        endpoint_ids = {value for value in [source_storage_id, target_storage_id] if value}
        if endpoint_ids:
            return bool(endpoint_ids.intersection(changed_or_removed_node_ids))
        # Legacy rows without endpoint identities cannot prove adjacency.
        # Retain scope-wide invalidation only for those compatibility rows.
        return str(row.get("scopeId") or "") in changed_node_scopes

    changed_relation_ids = {
        storage_id
        for storage_id, row in desired_relations.items()
        if str((existing_relations.get(storage_id) or {}).get("contentFingerprint") or "")
        != str(row.get("contentFingerprint") or "")
        or relation_touches_changed_node(row)
    }
    removed_relation_ids = {
        storage_id
        for storage_id, row in existing_relations.items()
        if storage_id not in desired_relations
    }
    node_rows_to_insert = [desired_nodes[storage_id] for storage_id in sorted(changed_node_ids)]
    relation_rows_to_insert = [
        desired_relations[storage_id] for storage_id in sorted(changed_relation_ids)
    ]
    return {
        "nodeRows": list(desired_nodes.values()),
        "relationRows": list(desired_relations.values()),
        "nodeRowsToInsert": node_rows_to_insert,
        "relationRowsToInsert": relation_rows_to_insert,
        "nodeStorageIdsToDelete": sorted(changed_node_ids | removed_node_ids),
        "relationStorageIdsToDelete": sorted(changed_relation_ids | removed_relation_ids),
        "reusedNodeRows": [
            row for storage_id, row in desired_nodes.items() if storage_id not in changed_node_ids
        ],
        "reusedRelationRows": [
            row
            for storage_id, row in desired_relations.items()
            if storage_id not in changed_relation_ids
        ],
        "changedNodeScopeIds": sorted(changed_node_scopes),
    }
