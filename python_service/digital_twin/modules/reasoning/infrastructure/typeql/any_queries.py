"""Any queries for TypeQL, without database execution."""

from typing import Dict, Iterable, List, Tuple

from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.typeql.condition_queries import (
    typedb_condition_pattern,
    typedb_entity_match_type,
    typedb_filter_operator,
    typedb_relation_match_type,
)
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string, typedb_value_match
from digital_twin.modules.reasoning.infrastructure.typeql.match_queries import typedb_native_match_query
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    normalized_condition_role,
    symbol_from_subject,
    typedb_native_rule_id,
)
from digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses import (
    typedb_active_abox_member_clause,
    typedb_active_worldview_manifest_clause,
    typedb_scoped_manifest_member_clause,
)
from digital_twin.modules.reasoning.infrastructure.typeql.storage_schema import (
    typedb_relation_attribute,
    typedb_target_attribute,
)


def typedb_native_any_group_check_query(
    rule: Dict[str, object],
    source_id: str,
    scoped_manifest_only: bool = False,
    world_id: str = "",
    active_source_storage_id: str = "",
    active_relation_storage_ids: Iterable[str] = None,
    active_relation_storage_ids_by_type: Dict[str, Iterable[str]] = None,
) -> Dict[str, object]:
    """Build a source-bounded TypeDB N-of-M condition check.

    This is a direct TypeQL query:
    TypeDB evaluates the cardinality with `reduce count`, while schema compile
    remains small and predictable.  The caller invokes it only after the base
    rule query produced this exact source id.
    """
    clean_source_id = str(source_id or "").strip()
    source_kind = str(rule.get("source_kind") or rule.get("sourceKind") or "stock")
    source_type = typedb_entity_match_type(source_kind)
    if not clean_source_id:
        return {
            "ruleId": str(rule.get("rule_id") or rule.get("ruleId") or ""),
            "query": "",
            "columns": [],
            "reason": "Native any-condition check needs a source id.",
        }
    conditions = [
        (index, condition)
        for index, condition in enumerate(rule.get("conditions") or [])
        if isinstance(condition, dict)
        and normalized_condition_role(condition) in {"any", "optional"}
    ]
    any_min_count = max(
        1,
        int(number_or_none(rule.get("any_condition_min_count") or rule.get("anyConditionMinCount")) or 1),
    )
    evidence_groups = {
        str(condition.get("evidence_group_key") or condition.get("evidenceGroupKey") or condition.get("condition_id") or condition.get("conditionId") or "condition-" + str(index)).strip()
        for index, condition in conditions
    }
    if any_min_count > len(evidence_groups):
        return {
            "ruleId": str(rule.get("rule_id") or rule.get("ruleId") or ""),
            "query": "",
            "columns": [],
            "reason": "any condition minimum exceeds independent evidence groups",
        }

    def relation_shape(condition: Dict[str, object]) -> Tuple[str, str, str]:
        return (
            str(condition.get("relation_type") or condition.get("relationType") or "").upper(),
            str(condition.get("direction") or "out"),
            str(condition.get("target_kind") or condition.get("targetKind") or ""),
        )

    clean_source_storage_id = str(active_source_storage_id or "").strip()
    indexed_relation_storage_ids_by_type = {
        str(relation_type or "").upper().strip(): sorted({
            str(storage_id or "").strip()
            for storage_id in storage_ids or []
            if str(storage_id or "").strip()
        })
        for relation_type, storage_ids in dict(active_relation_storage_ids_by_type or {}).items()
        if str(relation_type or "").strip()
    }
    any_relation_types = {
        str(condition.get("relation_type") or condition.get("relationType") or "").upper().strip()
        for _condition_index, condition in conditions
        if str(condition.get("kind") or "") == "relation"
        and str(condition.get("relation_type") or condition.get("relationType") or "").strip()
    }
    if not indexed_relation_storage_ids_by_type:
        flattened_relation_storage_ids = sorted({
            str(storage_id or "").strip()
            for storage_id in active_relation_storage_ids or []
            if str(storage_id or "").strip()
        })
        if flattened_relation_storage_ids:
            # v1 callers have one verified source-local relation set but no
            # type partition. The rule's TypeDB relation-type predicate still
            # keeps every branch exact.
            indexed_relation_storage_ids_by_type = {
                relation_type: list(flattened_relation_storage_ids)
                for relation_type in any_relation_types
            }

    # For N-of-M groups, a source-bounded generic query still expands the
    # active scoped-Manifest membership graph. Use the verified source-local
    # physical rows when available. The direct query retains the same
    # TypeDB `reduce count` evaluation and RuleBox condition tokens; Python
    # supplies identities only and never decides the cardinality outcome.
    if clean_source_storage_id and any_min_count > 1:
        source_symbol = symbol_from_subject(clean_source_id)
        indexed_plan = typedb_native_match_query(
            rule,
            [source_symbol] if source_symbol else [],
            scoped_manifest_only=False,
            include_required_conditions=False,
            include_negative_conditions=False,
            include_any_conditions=True,
            world_id=world_id,
            active_source_storage_ids=[clean_source_storage_id],
            active_relation_storage_ids_by_type=indexed_relation_storage_ids_by_type,
        )
        if indexed_plan.get("query"):
            return {
                **indexed_plan,
                "columns": ["sourceId", "sourceLabel"],
                "anyConditionCheckMode": "distinct-condition-count-manifest-indexed",
            }

    # An N-of-M group with N=1 is a pure existence test. The former generic
    # path joined RuleBox condition-token entities and ran a `reduce count`
    # even though there is no cardinality to calculate. Keep the decision in
    # TypeDB, but ask it for one bounded matching branch instead.
    if any_min_count == 1:
        clean_relation_storage_ids = sorted({
            str(item or "").strip()
            for item in active_relation_storage_ids or []
            if str(item or "").strip()
        })
        shared_relation_conditions = all(
            str(condition.get("kind") or "") == "relation"
            for _condition_index, condition in conditions
        )
        mixed_condition_kinds = {
            str(condition.get("kind") or "")
            for _condition_index, condition in conditions
            if str(condition.get("kind") or "")
        }
        if clean_source_storage_id and len(mixed_condition_kinds) > 1:
            indexed_branches: List[str] = []
            for branch_index, (condition_index, condition) in enumerate(conditions):
                relation_type = str(
                    condition.get("relation_type") or condition.get("relationType") or ""
                ).upper().strip()
                pattern = typedb_condition_pattern(
                    condition,
                    condition_index,
                    relation_prefix="anyIndexedMixedRel" + str(branch_index) + "_",
                    target_prefix="anyIndexedMixedTarget" + str(branch_index) + "_",
                    variable_scope="anyIndexedMixed" + str(branch_index) + "_",
                    active_relation_storage_ids=indexed_relation_storage_ids_by_type.get(
                        relation_type,
                        [],
                    ),
                    # The exact immutable source row is already bound below.
                    # A relation linked to that physical row belongs to the
                    # same ABox generation even when a legacy evidence index
                    # has no relation storage IDs by type.
                    source_storage_indexed=True,
                )
                if pattern.get("reason"):
                    return {
                        "ruleId": str(rule.get("rule_id") or rule.get("ruleId") or ""),
                        "query": "",
                        "columns": [],
                        "reason": str(pattern.get("reason") or ""),
                    }
                branch_clauses = [
                    str(item)
                    for item in pattern.get("clauses") or []
                    if str(item or "").strip()
                ]
                if branch_clauses:
                    indexed_branches.append("{ " + " ".join(branch_clauses) + " }")
            if indexed_branches:
                return {
                    "ruleId": str(rule.get("rule_id") or rule.get("ruleId") or ""),
                    "nativeRuleId": typedb_native_rule_id(
                        str(rule.get("rule_id") or rule.get("ruleId") or "")
                    ),
                    "query": (
                        "match $source isa " + source_type + ", has ontology-id "
                        + typedb_string(clean_source_id)
                        + ", has ontology-storage-id " + typedb_string(clean_source_storage_id) + ";"
                        + " match " + " or ".join(indexed_branches) + ";"
                        + " $source has ontology-id $sourceId, has ontology-label $sourceLabel;"
                    ),
                    "columns": ["sourceId", "sourceLabel"],
                    "anyConditionCheckMode": "exists-any-manifest-indexed-mixed",
                }
        if (
            clean_source_storage_id
            and shared_relation_conditions
        ):
            first_condition = dict(conditions[0][1])
            common_shape = relation_shape(first_condition)
            if common_shape[0] and all(
                relation_shape(condition) == common_shape
                for _condition_index, condition in conditions
            ):
                relation_type, direction, target_kind = common_shape
                relation_match_type = typedb_relation_match_type(relation_type)
                target_match_type = typedb_entity_match_type(target_kind)
                source_var = "$source"
                target_var = "$anyIndexedTarget"
                relation_var = "$anyIndexedRelation"
                if direction == "in":
                    link_clause = (
                        target_var + " isa " + target_match_type + "; "
                        + relation_var + " isa " + relation_match_type + ", links (source: " + target_var
                        + ", target: " + source_var + "), has ontology-relation-type "
                        + typedb_string(relation_type) + ";"
                    )
                else:
                    link_clause = (
                        target_var + " isa " + target_match_type + "; "
                        + relation_var + " isa " + relation_match_type + ", links (source: " + source_var
                        + ", target: " + target_var + "), has ontology-relation-type "
                        + typedb_string(relation_type) + ";"
                    )
                structural_clauses = [
                    source_var + " isa " + source_type + ", has ontology-id " + typedb_string(clean_source_id)
                    + ", has ontology-storage-id " + typedb_string(clean_source_storage_id) + ";",
                    link_clause,
                ]
                if clean_relation_storage_ids:
                    structural_clauses.append(typedb_value_match(
                        relation_var,
                        "ontology-storage-id",
                        clean_relation_storage_ids,
                        "==",
                        "anyIndexedRelationStorage",
                    ))
                if target_kind:
                    structural_clauses.append(
                        target_var + " has ontology-kind " + typedb_string(target_kind) + ";"
                    )
                filter_branches: List[List[str]] = []
                for condition_index, condition in conditions:
                    filters: List[str] = []
                    for filter_index, (filter_key, expected) in enumerate(dict(
                        condition.get("target_property_filters") or condition.get("targetPropertyFilters") or {}
                    ).items()):
                        attribute = typedb_target_attribute(str(filter_key))
                        if attribute:
                            clause = typedb_value_match(
                                target_var,
                                attribute,
                                expected,
                                typedb_filter_operator(str(filter_key), expected),
                                "anyIndexedTargetValue" + str(condition_index) + "_" + str(filter_index),
                            )
                            if clause:
                                filters.append(clause)
                    for filter_index, (filter_key, expected) in enumerate(dict(
                        condition.get("relation_property_filters") or condition.get("relationPropertyFilters") or {}
                    ).items()):
                        attribute = typedb_relation_attribute(str(filter_key))
                        if attribute:
                            clause = typedb_value_match(
                                relation_var,
                                attribute,
                                expected,
                                typedb_filter_operator(str(filter_key), expected),
                                "anyIndexedRelationValue" + str(condition_index) + "_" + str(filter_index),
                            )
                            if clause:
                                filters.append(clause)
                    filter_branches.append(filters)
                filter_query = ""
                if not any(not branch for branch in filter_branches):
                    filter_query = " match " + " or ".join(
                        "{ " + " ".join(branch) + " }"
                        for branch in filter_branches
                    ) + ";"
                # The Manifest index is verified from the active TypeDB ABox
                # before this query is built. Physical storage IDs therefore
                # constrain both source and relation to the one active world,
                # avoiding an expensive historical scope-pointer join.
                return {
                    "ruleId": str(rule.get("rule_id") or rule.get("ruleId") or ""),
                    "nativeRuleId": typedb_native_rule_id(str(rule.get("rule_id") or rule.get("ruleId") or "")),
                    "query": (
                        "match " + " ".join(structural_clauses)
                        + filter_query
                        + " " + source_var + " has ontology-id $sourceId, has ontology-label $sourceLabel;"
                    ),
                    "columns": ["sourceId", "sourceLabel"],
                    "anyConditionCheckMode": (
                        "exists-any-manifest-indexed-relation"
                        if clean_relation_storage_ids
                        else "exists-any-manifest-indexed-source"
                    ),
                }
            # Some N=1 groups use the same active evidence set but different
            # relation shapes.  Valuation checks are a representative case:
            # one branch tests PE and another tests beta.  Constraining every
            # branch to the verified physical rows in the active Manifest
            # avoids repeating the historical scope-pointer traversal while
            # preserving the complete alternative decision inside TypeDB.
            indexed_branches: List[str] = []
            for branch_index, (_condition_index, condition) in enumerate(conditions):
                relation_type, direction, target_kind = relation_shape(condition)
                if not relation_type:
                    indexed_branches = []
                    break
                relation_match_type = typedb_relation_match_type(relation_type)
                target_match_type = typedb_entity_match_type(target_kind)
                target_var = "$anyIndexedBranchTarget" + str(branch_index)
                relation_var = "$anyIndexedBranchRelation" + str(branch_index)
                if direction == "in":
                    link_clause = (
                        target_var + " isa " + target_match_type + "; "
                        + relation_var + " isa " + relation_match_type + ", links (source: " + target_var
                        + ", target: $source), has ontology-relation-type "
                        + typedb_string(relation_type) + ";"
                    )
                else:
                    link_clause = (
                        target_var + " isa " + target_match_type + "; "
                        + relation_var + " isa " + relation_match_type + ", links (source: $source, target: "
                        + target_var + "), has ontology-relation-type "
                        + typedb_string(relation_type) + ";"
                    )
                branch_clauses = [
                    link_clause,
                ]
                if clean_relation_storage_ids:
                    branch_clauses.append(typedb_value_match(
                        relation_var,
                        "ontology-storage-id",
                        clean_relation_storage_ids,
                        "==",
                        "anyIndexedBranchRelationStorage" + str(branch_index),
                    ))
                if target_kind:
                    branch_clauses.append(
                        target_var + " has ontology-kind " + typedb_string(target_kind) + ";"
                    )
                for filter_index, (filter_key, expected) in enumerate(dict(
                    condition.get("target_property_filters") or condition.get("targetPropertyFilters") or {}
                ).items()):
                    attribute = typedb_target_attribute(str(filter_key))
                    if attribute:
                        clause = typedb_value_match(
                            target_var,
                            attribute,
                            expected,
                            typedb_filter_operator(str(filter_key), expected),
                            "anyIndexedBranchTargetValue" + str(branch_index) + "_" + str(filter_index),
                        )
                        if clause:
                            branch_clauses.append(clause)
                for filter_index, (filter_key, expected) in enumerate(dict(
                    condition.get("relation_property_filters") or condition.get("relationPropertyFilters") or {}
                ).items()):
                    attribute = typedb_relation_attribute(str(filter_key))
                    if attribute:
                        clause = typedb_value_match(
                            relation_var,
                            attribute,
                            expected,
                            typedb_filter_operator(str(filter_key), expected),
                            "anyIndexedBranchRelationValue" + str(branch_index) + "_" + str(filter_index),
                        )
                        if clause:
                            branch_clauses.append(clause)
                indexed_branches.append("{ " + " ".join(branch_clauses) + " }")
            if indexed_branches:
                return {
                    "ruleId": str(rule.get("rule_id") or rule.get("ruleId") or ""),
                    "nativeRuleId": typedb_native_rule_id(str(rule.get("rule_id") or rule.get("ruleId") or "")),
                    "query": (
                        "match $source isa " + source_type + ", has ontology-id " + typedb_string(clean_source_id)
                        + ", has ontology-storage-id " + typedb_string(clean_source_storage_id) + ";"
                        + " match " + " or ".join(indexed_branches) + ";"
                        + " $source has ontology-id $sourceId, has ontology-label $sourceLabel;"
                    ),
                    "columns": ["sourceId", "sourceLabel"],
                    "anyConditionCheckMode": "exists-any-manifest-indexed-relation-branches",
                }
        scoped_manifest_variable = "$activeManifestId" if scoped_manifest_only else ""
        clauses: List[str] = []
        if scoped_manifest_only:
            clauses.append(typedb_active_worldview_manifest_clause(
                "$activeManifestPointer",
                scoped_manifest_variable,
                world_id,
            ))
            clauses.append(typedb_scoped_manifest_member_clause(
                "$source",
                "source",
                scoped_manifest_variable,
                world_id,
            ))
        else:
            clauses.append(typedb_active_abox_member_clause("$source", "source", world_id))
        clauses.append(
            "$source isa " + source_type + ", has ontology-id " + typedb_string(clean_source_id) + ";"
        )
        # A common RuleBox shape is one relation type with several alternative
        # target thresholds, such as execution metrics.  Repeating the same
        # source-to-target link and active-scope membership join in every `or`
        # branch gives TypeDB a much larger search plan than the rule needs.
        # Keep the complete truth test in TypeDB, but share that structural
        # join once and leave only the threshold predicates in the branches.
        if shared_relation_conditions:
            first_condition = dict(conditions[0][1])
            common_shape = relation_shape(first_condition)
            if common_shape[0] and all(
                relation_shape(condition) == common_shape
                for _condition_index, condition in conditions
            ):
                shared_base_condition = dict(first_condition)
                shared_base_condition.pop("target_property_filters", None)
                shared_base_condition.pop("targetPropertyFilters", None)
                shared_base_condition.pop("relation_property_filters", None)
                shared_base_condition.pop("relationPropertyFilters", None)
                shared_base_pattern = typedb_condition_pattern(
                    shared_base_condition,
                    0,
                    relation_prefix="anyExistsSharedRel_",
                    target_prefix="anyExistsSharedTarget_",
                    variable_scope="anyExistsShared_",
                    manifest_id_variable=scoped_manifest_variable,
                    world_id=world_id,
                )
                shared_base_clauses = [
                    str(item)
                    for item in shared_base_pattern.get("clauses") or []
                    if str(item or "").strip()
                ]
                shared_filter_branches: List[List[str]] = []
                if shared_base_clauses and not shared_base_pattern.get("reason"):
                    for _condition_index, condition in conditions:
                        full_pattern = typedb_condition_pattern(
                            condition,
                            0,
                            relation_prefix="anyExistsSharedRel_",
                            target_prefix="anyExistsSharedTarget_",
                            variable_scope="anyExistsShared_",
                            manifest_id_variable=scoped_manifest_variable,
                            world_id=world_id,
                        )
                        full_clauses = [
                            str(item)
                            for item in full_pattern.get("clauses") or []
                            if str(item or "").strip()
                        ]
                        if (
                            full_pattern.get("reason")
                            or full_clauses[:len(shared_base_clauses)] != shared_base_clauses
                        ):
                            shared_filter_branches = []
                            break
                        shared_filter_branches.append(full_clauses[len(shared_base_clauses):])
                if shared_filter_branches:
                    # An unfiltered branch is already proved by the shared
                    # relation join, so no disjunction is needed.
                    filter_query = ""
                    if not any(not branch for branch in shared_filter_branches):
                        filter_query = " match " + " or ".join(
                            "{ " + " ".join(branch) + " }"
                            for branch in shared_filter_branches
                        ) + ";"
                    return {
                        "ruleId": str(rule.get("rule_id") or rule.get("ruleId") or ""),
                        "nativeRuleId": typedb_native_rule_id(str(rule.get("rule_id") or rule.get("ruleId") or "")),
                        "query": (
                            "match " + " ".join(clauses + shared_base_clauses)
                            + filter_query
                            + " $source has ontology-id $sourceId, has ontology-label $sourceLabel;"
                        ),
                        "columns": ["sourceId", "sourceLabel"],
                        "anyConditionCheckMode": "exists-any-shared-relation",
                    }
        branches: List[str] = []
        for branch_index, (condition_index, condition) in enumerate(conditions):
            pattern = typedb_condition_pattern(
                condition,
                condition_index,
                relation_prefix="anyExistsRel" + str(branch_index) + "_",
                target_prefix="anyExistsTarget" + str(branch_index) + "_",
                variable_scope="anyExists" + str(branch_index) + "_",
                manifest_id_variable=scoped_manifest_variable,
                world_id=world_id,
            )
            if pattern.get("reason"):
                return {
                    "ruleId": str(rule.get("rule_id") or rule.get("ruleId") or ""),
                    "query": "",
                    "columns": [],
                    "reason": str(pattern.get("reason") or ""),
                }
            branch_clauses = [
                str(item)
                for item in pattern.get("clauses") or []
                if str(item or "").strip()
            ]
            if branch_clauses:
                branches.append("{ " + " ".join(branch_clauses) + " }")
        if not branches:
            return {
                "ruleId": str(rule.get("rule_id") or rule.get("ruleId") or ""),
                "query": "",
                "columns": [],
                "reason": "any conditions produced no TypeQL branches",
            }
        return {
            "ruleId": str(rule.get("rule_id") or rule.get("ruleId") or ""),
            "nativeRuleId": typedb_native_rule_id(str(rule.get("rule_id") or rule.get("ruleId") or "")),
            "query": (
                "match " + " ".join(clauses)
                + " match " + " or ".join(branches) + ";"
                + " $source has ontology-id $sourceId, has ontology-label $sourceLabel;"
            ),
            "columns": ["sourceId", "sourceLabel"],
            "anyConditionCheckMode": "exists-any",
        }

    plan = typedb_native_match_query(
        rule,
        [],
        scoped_manifest_only=scoped_manifest_only,
        # The base direct query already proved required and negative clauses for
        # this exact source in the same read transaction. Repeating its joins
        # here turns a bounded N-of-M check into a full second rule query.
        include_required_conditions=False,
        include_negative_conditions=False,
        include_any_conditions=True,
        world_id=world_id,
    )
    query = str(plan.get("query") or "").strip()
    if not query.startswith("match "):
        return plan
    source_clause = "$source has ontology-id " + typedb_string(clean_source_id) + "; "
    return {
        **plan,
        "query": "match " + source_clause + query[len("match "):],
        "columns": ["sourceId", "sourceLabel"],
        "anyConditionCheckMode": "distinct-condition-count",
    }
