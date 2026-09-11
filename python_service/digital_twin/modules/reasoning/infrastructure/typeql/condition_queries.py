"""Condition queries for TypeQL, without database execution."""

import re
from typing import Dict, Iterable, List

from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string, typedb_value_match
from digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses import (
    typedb_active_abox_member_clause,
    typedb_active_worldview_manifest_clause,
    typedb_scoped_manifest_member_clause,
)
from digital_twin.modules.reasoning.infrastructure.typeql.storage_schema import (
    typedb_entity_storage_type,
    typedb_relation_attribute,
    typedb_relation_storage_type,
    typedb_subject_attribute,
    typedb_target_attribute,
)


def typedb_filter_operator(filter_key: str, expected: object, default_operator: str = "==") -> str:
    if isinstance(expected, dict) and expected.get("operator"):
        return str(expected.get("operator") or default_operator)
    if str(filter_key or "").startswith("min"):
        return ">="
    if str(filter_key or "").startswith("max"):
        return "<="
    return default_operator


def typedb_entity_match_type(kind: object) -> str:
    """Use the stored semantic subtype to keep TypeDB read plans bounded."""
    return typedb_entity_storage_type({}, kind, fallback="ontology-node")


def typedb_relation_match_type(relation_type: object) -> str:
    """Use the stored semantic relation subtype when the TBox defines it."""
    return typedb_relation_storage_type(relation_type, fallback="ontology-assertion")


def typedb_condition_pattern(
    condition: Dict[str, object],
    index: int,
    source_var: str = "$source",
    relation_prefix: str = "rel",
    target_prefix: str = "target",
    variable_scope: str = "",
    manifest_id_variable: str = "",
    world_id: str = "",
    world_id_variable: str = "",
    active_relation_storage_ids: Iterable[str] = None,
    source_storage_indexed: bool = False,
) -> Dict[str, object]:
    condition_id = str(condition.get("condition_id") or condition.get("conditionId") or "condition-" + str(index))
    kind = str(condition.get("kind") or "")
    clauses: List[str] = []
    columns: List[str] = []
    evidence_columns: List[str] = []
    safe_scope = re.sub(r"[^A-Za-z0-9_]", "", str(variable_scope or ""))

    def value_variable(prefix: str, clause_index: int) -> str:
        if safe_scope:
            return prefix + safe_scope + str(clause_index)
        return prefix + str(index) + str(clause_index)

    if kind == "subject_property":
        attr = typedb_subject_attribute(str(condition.get("field") or ""))
        if not attr:
            return {"conditionId": condition_id, "clauses": [], "columns": [], "reason": "unsupported subject field"}
        clause = typedb_value_match(
            source_var,
            attr,
            condition.get("value"),
            str(condition.get("operator") or "=="),
            value_variable("subjectValue", 0),
        )
        if clause:
            clauses.append(clause)
        return {
            "conditionId": condition_id,
            "kind": kind,
            "clauses": clauses,
            "columns": columns,
            "evidenceColumns": evidence_columns,
        }
    if kind == "relation":
        relation_var = "$" + relation_prefix + str(index)
        target_var = "$" + target_prefix + str(index)
        relation_id_var = value_variable("relationId", 0)
        rel_type = str(condition.get("relation_type") or condition.get("relationType") or "").upper()
        direction = str(condition.get("direction") or "out")
        target_kind = str(condition.get("target_kind") or condition.get("targetKind") or "")
        if not rel_type:
            return {"conditionId": condition_id, "clauses": [], "columns": [], "reason": "missing relation type"}
        target_type = typedb_entity_match_type(target_kind)
        relation_type = typedb_relation_match_type(rel_type)
        if direction == "in":
            clauses.append(
                target_var + " isa " + target_type + "; "
                + relation_var + " isa " + relation_type + ", links (source: " + target_var + ", target: " + source_var + "), "
                + "has ontology-id $" + relation_id_var + ", has ontology-relation-type " + typedb_string(rel_type) + ";"
            )
        else:
            clauses.append(
                target_var + " isa " + target_type + "; "
                + relation_var + " isa " + relation_type + ", links (source: " + source_var + ", target: " + target_var + "), "
                + "has ontology-id $" + relation_id_var + ", has ontology-relation-type " + typedb_string(rel_type) + ";"
            )
        indexed_relation_storage_ids = sorted({
            str(item or "").strip()
            for item in active_relation_storage_ids or []
            if str(item or "").strip()
        })
        if indexed_relation_storage_ids:
            # The active Manifest evidence index contains the exact immutable
            # assertion rows for this source symbol. Anchoring the assertion
            # by storage identity also fixes its role-player target, so no
            # historical scope-pointer traversal is needed for this predicate.
            clauses.append(typedb_value_match(
                relation_var,
                "ontology-storage-id",
                indexed_relation_storage_ids,
                "==",
                value_variable("activeRelationStorage", len(clauses)),
            ))
        elif manifest_id_variable:
            clauses.append(typedb_scoped_manifest_member_clause(
                target_var,
                target_prefix + str(index),
                manifest_id_variable,
                world_id,
                world_id_variable,
            ))
            clauses.append(typedb_scoped_manifest_member_clause(
                relation_var,
                relation_prefix + str(index),
                manifest_id_variable,
                world_id,
                world_id_variable,
            ))
        elif not source_storage_indexed:
            clauses.append(typedb_active_abox_member_clause(
                target_var,
                target_prefix + str(index),
                world_id,
                world_id_variable,
            ))
            clauses.append(typedb_active_abox_member_clause(
                relation_var,
                relation_prefix + str(index),
                world_id,
                world_id_variable,
            ))
        if target_kind:
            clauses.append(target_var + " has ontology-kind " + typedb_string(target_kind) + ";")
        for filter_key, expected in dict(condition.get("target_property_filters") or condition.get("targetPropertyFilters") or {}).items():
            attr = typedb_target_attribute(str(filter_key))
            if attr:
                op = typedb_filter_operator(str(filter_key), expected)
                clause = typedb_value_match(target_var, attr, expected, op, value_variable("targetValue", len(clauses)))
                if clause:
                    clauses.append(clause)
        for filter_key, expected in dict(condition.get("relation_property_filters") or condition.get("relationPropertyFilters") or {}).items():
            attr = typedb_relation_attribute(str(filter_key))
            if attr:
                op = typedb_filter_operator(str(filter_key), expected)
                clause = typedb_value_match(relation_var, attr, expected, op, value_variable("relationValue", len(clauses)))
                if clause:
                    clauses.append(clause)
        columns.append(relation_id_var)
        evidence_columns.append(relation_id_var)
        return {
            "conditionId": condition_id,
            "kind": kind,
            "clauses": clauses,
            "columns": columns,
            "evidenceColumns": evidence_columns,
            "relationIdColumn": relation_id_var,
        }
    return {"conditionId": condition_id, "clauses": [], "columns": [], "reason": "unsupported condition kind"}


def typedb_native_condition_check_query(
    condition: Dict[str, object],
    source_id: str,
    index: int,
    scoped_manifest_only: bool = False,
    world_id: str = "",
) -> Dict[str, object]:
    """Build one bounded native condition probe for a resolved source.

    The active scoped Manifest is bound once for the probe. This is used only
    after a base direct query has matched, so N-of-M `any` conditions do
    not expand the query or make unrelated sources part of the
    TypeQL search space.
    """
    manifest_id_variable = "$activeManifestId" if scoped_manifest_only else ""
    pattern = typedb_condition_pattern(
        condition,
        index,
        relation_prefix="checkRel",
        target_prefix="checkTarget",
        manifest_id_variable=manifest_id_variable,
        world_id=world_id,
    )
    if pattern.get("reason"):
        return {
            "conditionId": str(condition.get("condition_id") or condition.get("conditionId") or ""),
            "query": "",
            "columns": [],
            "reason": str(pattern.get("reason") or ""),
        }
    clauses = (
        [
            typedb_active_worldview_manifest_clause("$activeManifestPointer", manifest_id_variable, world_id),
            typedb_scoped_manifest_member_clause("$source", "source", manifest_id_variable, world_id),
        ]
        if scoped_manifest_only
        else [typedb_active_abox_member_clause("$source", "source", world_id)]
    )
    clauses.extend([
        "$source isa ontology-node, has ontology-id " + typedb_string(source_id) + ";",
        *[str(item) for item in pattern.get("clauses") or [] if str(item or "").strip()],
    ])
    columns = list(pattern.get("columns") or []) or ["sourceId"]
    if "sourceId" in columns:
        clauses.insert(1, "$source has ontology-id $sourceId;")
    return {
        "conditionId": str(condition.get("condition_id") or condition.get("conditionId") or ""),
        "query": "match " + " ".join(clauses),
        "columns": columns,
        "evidenceColumns": list(pattern.get("evidenceColumns") or []),
        "relationIdColumn": str(pattern.get("relationIdColumn") or ""),
    }
