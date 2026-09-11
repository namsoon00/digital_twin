"""Match queries for TypeQL, without database execution."""

from typing import Dict, Iterable, List, Tuple

from digital_twin.modules.reasoning.domain.ontology_contracts import entity_id
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.typeql.condition_queries import (
    typedb_condition_pattern,
    typedb_entity_match_type,
)
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string, typedb_value_match
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
    normalized_condition_role,
    typedb_native_rule_id,
    typedb_source_kind_uses_symbol_scope,
)
from digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses import (
    typedb_active_abox_member_clause,
    typedb_active_worldview_manifest_clause,
    typedb_scoped_manifest_member_clause,
)


def typedb_native_match_query(
    rule: Dict[str, object],
    target_symbols: Iterable[str] = None,
    any_helper_names: List[str] = None,
    scoped_manifest_only: bool = False,
    manifest_id_variable: str = "",
    bind_active_manifest: bool = True,
    include_source_manifest_membership: bool = True,
    include_required_conditions: bool = True,
    include_negative_conditions: bool = True,
    include_any_conditions: bool = True,
    world_id: str = "",
    world_id_variable: str = "",
    active_source_storage_ids: Iterable[str] = None,
    active_relation_storage_ids_by_type: Dict[str, Iterable[str]] = None,
    active_relation_storage_ids_by_condition: Dict[str, Iterable[str]] = None,
    compact_result_rows: bool = False,
) -> Dict[str, object]:
    """Compile one RuleBox rule into a bounded TypeQL read pipeline.

    ``any`` conditions used to be expanded into every possible combination.
    That made one six-condition, two-of-six rule generate fifteen nested
    function branches and caused TypeDB's planner to hold CPU after the client
    had already timed out.  Each RuleBox condition already has a durable,
    unique ontology entity, so the pipeline now matches one branch at a time
    and counts distinct condition tokens per source.  This preserves the
    exact ``at least N different conditions`` semantics without combinatorial
    function bodies.
    """
    rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "")
    source_kind = str(rule.get("source_kind") or rule.get("sourceKind") or "stock")
    conditions = [item for item in (rule.get("conditions") or []) if isinstance(item, dict)]
    indexed_source_storage_ids = sorted({
        str(item or "").strip()
        for item in active_source_storage_ids or []
        if str(item or "").strip()
    })
    indexed_relation_storage_ids_by_type = {
        str(relation_type or "").upper().strip(): sorted({
            str(storage_id or "").strip()
            for storage_id in storage_ids or []
            if str(storage_id or "").strip()
        })
        for relation_type, storage_ids in dict(active_relation_storage_ids_by_type or {}).items()
        if str(relation_type or "").strip()
    }
    indexed_relation_storage_ids_by_condition = {
        str(condition_id or "").strip(): sorted({
            str(storage_id or "").strip()
            for storage_id in storage_ids or []
            if str(storage_id or "").strip()
        })
        for condition_id, storage_ids in dict(active_relation_storage_ids_by_condition or {}).items()
        if str(condition_id or "").strip()
    }
    indexed_evidence = bool(indexed_source_storage_ids)
    scoped_manifest_variable = str(manifest_id_variable or "$activeManifestId") if scoped_manifest_only and not indexed_evidence else ""
    if indexed_evidence:
        clauses = []
    elif scoped_manifest_only:
        clauses = []
        if bind_active_manifest:
            clauses.append(typedb_active_worldview_manifest_clause(
                "$activeManifestPointer",
                scoped_manifest_variable,
                world_id,
                world_id_variable,
            ))
        if include_source_manifest_membership:
            clauses.append(typedb_scoped_manifest_member_clause(
                "$source",
                "source",
                scoped_manifest_variable,
                world_id,
                world_id_variable,
            ))
    else:
        clauses = [typedb_active_abox_member_clause(
            "$source",
            "source",
            world_id,
            world_id_variable,
        )]
    clauses.append(
        "$source isa " + typedb_entity_match_type(source_kind)
        + ", has ontology-id $sourceId, has ontology-label $sourceLabel, has ontology-kind "
        + typedb_string(source_kind)
        + ";"
    )
    if indexed_evidence:
        clauses.append(typedb_value_match(
            "$source",
            "ontology-storage-id",
            indexed_source_storage_ids,
            "==",
            "activeSourceStorage",
        ))
    symbols = clean_symbols_from_payload(list(target_symbols or []))
    if symbols and typedb_source_kind_uses_symbol_scope(source_kind):
        clauses.append(typedb_value_match("$source", "ontology-symbol", symbols, "==", "sourceSymbol"))
    columns = ["sourceId", "sourceLabel"]
    evidence_columns: List[str] = []
    condition_evidence_columns: Dict[str, str] = {}
    any_conditions: List[Tuple[int, Dict[str, object]]] = []
    any_min_count = max(1, int(number_or_none(rule.get("any_condition_min_count") or rule.get("anyConditionMinCount")) or 1))
    for index, condition in enumerate(conditions):
        condition_id = str(condition.get("condition_id") or condition.get("conditionId") or "condition-" + str(index))
        role = normalized_condition_role(condition)
        if role in {"any", "optional"} and not include_any_conditions:
            continue
        if role == "not" and not include_negative_conditions:
            continue
        if role not in {"any", "optional", "not"} and not include_required_conditions:
            continue
        relation_type = str(condition.get("relation_type") or condition.get("relationType") or "").upper().strip()
        active_relation_storage_ids = (
            indexed_relation_storage_ids_by_condition.get(
                condition_id,
                indexed_relation_storage_ids_by_type.get(relation_type, []),
            )
            if indexed_evidence and str(condition.get("kind") or "") == "relation"
            else []
        )
        if (
            indexed_evidence
            and str(condition.get("kind") or "") == "relation"
            and not active_relation_storage_ids
            and role not in {"any", "optional", "not"}
        ):
            return {
                "ruleId": rule_id,
                "query": "",
                "columns": columns,
                "reason": "Active Manifest evidence index has no relation storage identities for " + relation_type + ".",
            }
        if (
            indexed_evidence
            and str(condition.get("kind") or "") == "relation"
            and not active_relation_storage_ids
            and role in {"any", "optional"}
        ):
            # An absent alternative cannot satisfy an N-of-M group. Leave it
            # out of the TypeDB branch set rather than falling back to an
            # unbounded active-scope scan for this one missing relation type.
            continue
        if (
            indexed_evidence
            and str(condition.get("kind") or "") == "relation"
            and not active_relation_storage_ids
            and role == "not"
        ):
            # The verified Manifest index is complete for the requested
            # sources. With no active relation of this type, the negative
            # predicate already holds and must not fall back to a broad
            # active-scope absence scan.
            continue
        pattern = typedb_condition_pattern(
            condition,
            index,
            manifest_id_variable=scoped_manifest_variable,
            world_id=world_id,
            world_id_variable=world_id_variable,
            active_relation_storage_ids=active_relation_storage_ids,
        )
        if pattern.get("reason"):
            return {"ruleId": rule_id, "query": "", "columns": columns, "reason": str(pattern.get("reason") or "")}
        pattern_clauses = [str(item) for item in pattern.get("clauses") or [] if str(item or "").strip()]
        if not pattern_clauses:
            continue
        if role in {"any", "optional"}:
            any_conditions.append((index, condition))
            continue
        if role == "not":
            if include_negative_conditions:
                clauses.append("not { " + " ".join(pattern_clauses) + " };")
            continue
        if not include_required_conditions:
            continue
        clauses.extend(pattern_clauses)
        for column in pattern.get("columns") or []:
            columns.append(str(column))
        for column in pattern.get("evidenceColumns") or []:
            evidence_columns.append(str(column))
        if pattern.get("relationIdColumn"):
            condition_evidence_columns[condition_id] = str(pattern.get("relationIdColumn"))
    query = "match " + " ".join(clauses)
    result_rows_compacted = False
    if any_conditions and include_any_conditions:
        # An N-of-M group is evidence-based, not condition-row-based. Two
        # aliases of one raw observation must contribute one confirmation.
        # N=1 is a pure existence predicate and must not pay for RuleBox token
        # joins or aggregation. Higher cardinalities use one durable condition
        # entity per evidence group as the TypeDB count token.
        any_group_representatives: Dict[str, str] = {}
        for condition_index, condition in any_conditions:
            condition_id = str(condition.get("condition_id") or condition.get("conditionId") or "condition-" + str(condition_index))
            evidence_group = str(
                condition.get("evidence_group_key")
                or condition.get("evidenceGroupKey")
                or condition_id
            ).strip() or condition_id
            any_group_representatives.setdefault(evidence_group, condition_id)
        if any_min_count > len(any_group_representatives):
            return {
                "ruleId": rule_id,
                "query": "",
                "columns": columns,
                "reason": "any condition minimum exceeds independent evidence groups",
            }
        branches: List[str] = []
        for branch_index, (_condition_index, condition) in enumerate(any_conditions):
            pattern = typedb_condition_pattern(
                condition,
                branch_index,
                relation_prefix="anyCountRel" + str(branch_index) + "_",
                target_prefix="anyCountTarget" + str(branch_index) + "_",
                variable_scope="anyCount" + str(branch_index) + "_",
                manifest_id_variable=scoped_manifest_variable,
                world_id=world_id,
                world_id_variable=world_id_variable,
                active_relation_storage_ids=(
                    indexed_relation_storage_ids_by_condition.get(
                        str(condition.get("condition_id") or condition.get("conditionId") or "condition-" + str(_condition_index)),
                        indexed_relation_storage_ids_by_type.get(
                            str(condition.get("relation_type") or condition.get("relationType") or "").upper().strip(),
                            [],
                        ),
                    )
                    if indexed_evidence and str(condition.get("kind") or "") == "relation"
                    else []
                ),
            )
            if pattern.get("reason"):
                return {"ruleId": rule_id, "query": "", "columns": columns, "reason": str(pattern.get("reason") or "")}
            branch_clauses = [str(item) for item in pattern.get("clauses") or [] if str(item or "").strip()]
            if branch_clauses:
                condition_id = str(condition.get("condition_id") or condition.get("conditionId") or "condition-" + str(branch_index))
                evidence_group = str(
                    condition.get("evidence_group_key")
                    or condition.get("evidenceGroupKey")
                    or condition_id
                ).strip() or condition_id
                if any_min_count > 1:
                    token_condition_id = any_group_representatives[evidence_group]
                    token_id = entity_id("rule-condition", rule_id + ":" + token_condition_id)
                    branch_clauses.append(
                        "$anyConditionToken isa ontology-node, has ontology-box \"RuleBox\", has ontology-id "
                        + typedb_string(token_id)
                        + ";"
                    )
                branches.append("{ " + " ".join(branch_clauses) + " }")
        if not branches:
            return {"ruleId": rule_id, "query": "", "columns": columns, "reason": "any conditions produced no TypeQL branches"}
        if any_min_count == 1:
            query += (
                " match " + " or ".join(branches) + ";"
                + " $source has ontology-id $sourceId, has ontology-label $sourceLabel;"
            )
        else:
            # `count($anyConditionToken)` counts distinct independent evidence
            # groups, represented by durable RuleBox condition entities, not
            # relation rows or duplicate aliases of the same raw observation.
            query += (
                " match " + " or ".join(branches) + ";"
                + " reduce $anyConditionCount = count($anyConditionToken) groupby $source;"
                + " match $anyConditionCount >= " + str(any_min_count) + ";"
                + " $source has ontology-id $sourceId, has ontology-label $sourceLabel;"
            )
        # The reduce stage intentionally drops relation variables. Detailed
        # evidence is collected only by the opt-in condition-detail path.
        evidence_columns = []
        condition_evidence_columns = {}
        result_rows_compacted = True
    elif compact_result_rows:
        # The calling pipeline only needs one boolean result per source. A
        # raw TypeQL match otherwise returns every intermediate relation and
        # target binding, even though the Python merge immediately collapses
        # them by ``ruleId|sourceId``. Grouping inside TypeDB preserves the
        # existential rule semantics and prevents that discarded result set
        # from dominating gRPC decode time.
        query += " reduce $nativeMatchCount = count groupby $sourceId, $sourceLabel;"
        columns = ["sourceId", "sourceLabel"]
        evidence_columns = []
        condition_evidence_columns = {}
        result_rows_compacted = True
    return {
        "ruleId": rule_id,
        "nativeRuleId": typedb_native_rule_id(rule_id),
        "query": query,
        "columns": columns,
        "evidenceColumns": evidence_columns,
        "conditionEvidenceColumns": condition_evidence_columns,
        "resultRowsCompacted": result_rows_compacted,
    }
