"""Explicit compiler exports; no driver, settings or runtime construction."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {
    "NATIVE_RULE_EVIDENCE_READ_INDEX_BATCH_SIZE": (
        "digital_twin.modules.reasoning.infrastructure.typeql.constants", "NATIVE_RULE_EVIDENCE_READ_INDEX_BATCH_SIZE",
    ),
    "NATIVE_RULE_EVIDENCE_READ_INDEX_VERSION": (
        "digital_twin.modules.reasoning.infrastructure.typeql.constants", "NATIVE_RULE_EVIDENCE_READ_INDEX_VERSION",
    ),
    "NATIVE_RULE_INDEXED_QUERY_MAX_STORAGE_IDS": (
        "digital_twin.modules.reasoning.infrastructure.typeql.constants", "NATIVE_RULE_INDEXED_QUERY_MAX_STORAGE_IDS",
    ),
    "TYPEDB_COMMON_NODE_ATTRIBUTES": (
        "digital_twin.modules.reasoning.infrastructure.typeql.constants", "TYPEDB_COMMON_NODE_ATTRIBUTES",
    ),
    "TYPEDB_FUNCTION_OPERATORS": (
        "digital_twin.modules.reasoning.infrastructure.typeql.constants", "TYPEDB_FUNCTION_OPERATORS",
    ),
    "TYPEDB_FUNCTION_RELATION_FILTERS": (
        "digital_twin.modules.reasoning.infrastructure.typeql.constants", "TYPEDB_FUNCTION_RELATION_FILTERS",
    ),
    "TYPEDB_FUNCTION_SUBJECT_FIELDS": (
        "digital_twin.modules.reasoning.infrastructure.typeql.constants", "TYPEDB_FUNCTION_SUBJECT_FIELDS",
    ),
    "TYPEDB_FUNCTION_TARGET_FILTERS": (
        "digital_twin.modules.reasoning.infrastructure.typeql.constants", "TYPEDB_FUNCTION_TARGET_FILTERS",
    ),
    "TYPEDB_NATIVE_REASONING_LAYER": (
        "digital_twin.modules.reasoning.infrastructure.typeql.constants", "TYPEDB_NATIVE_REASONING_LAYER",
    ),
    "TYPEDB_NATIVE_REASONING_PROFILE_VERSION": (
        "digital_twin.modules.reasoning.infrastructure.typeql.constants", "TYPEDB_NATIVE_REASONING_PROFILE_VERSION",
    ),
    "TYPEDB_NATIVE_RULE_ENGINE_VERSION": (
        "digital_twin.modules.reasoning.infrastructure.typeql.constants", "TYPEDB_NATIVE_RULE_ENGINE_VERSION",
    ),
    "TYPEDB_NUMERIC_ATTRIBUTES": (
        "digital_twin.modules.reasoning.infrastructure.typeql.constants", "TYPEDB_NUMERIC_ATTRIBUTES",
    ),
    "TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES": (
        "digital_twin.modules.reasoning.infrastructure.typeql.constants", "TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES",
    ),
    "TYPEDB_PROMOTED_TEXT_ATTRIBUTES": (
        "digital_twin.modules.reasoning.infrastructure.typeql.constants", "TYPEDB_PROMOTED_TEXT_ATTRIBUTES",
    ),
    "TYPEDB_STRING_ATTRIBUTES": (
        "digital_twin.modules.reasoning.infrastructure.typeql.constants", "TYPEDB_STRING_ATTRIBUTES",
    ),
    "clean_symbols_from_payload": (
        "digital_twin.modules.reasoning.infrastructure.typeql.rule_shape", "clean_symbols_from_payload",
    ),
    "condition_blocker": (
        "digital_twin.modules.reasoning.infrastructure.typeql.profiles", "condition_blocker",
    ),
    "filter_blockers": (
        "digital_twin.modules.reasoning.infrastructure.typeql.profiles", "filter_blockers",
    ),
    "normalized_condition_role": (
        "digital_twin.modules.reasoning.infrastructure.typeql.rule_shape", "normalized_condition_role",
    ),
    "symbol_from_subject": (
        "digital_twin.modules.reasoning.infrastructure.typeql.rule_shape", "symbol_from_subject",
    ),
    "typedb_active_abox_member_clause": (
        "digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses", "typedb_active_abox_member_clause",
    ),
    "typedb_active_abox_pointer_clause": (
        "digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses", "typedb_active_abox_pointer_clause",
    ),
    "typedb_active_abox_snapshot_clause": (
        "digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses", "typedb_active_abox_snapshot_clause",
    ),
    "typedb_active_scoped_abox_member_clause": (
        "digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses", "typedb_active_scoped_abox_member_clause",
    ),
    "typedb_active_worldview_manifest_clause": (
        "digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses", "typedb_active_worldview_manifest_clause",
    ),
    "typedb_condition_pattern": (
        "digital_twin.modules.reasoning.infrastructure.typeql.condition_queries", "typedb_condition_pattern",
    ),
    "typedb_dispatch_model_signal_bridge_rows": (
        "digital_twin.modules.reasoning.infrastructure.typeql.model_signal_queries", "typedb_dispatch_model_signal_bridge_rows",
    ),
    "typedb_entity_match_type": (
        "digital_twin.modules.reasoning.infrastructure.typeql.condition_queries", "typedb_entity_match_type",
    ),
    "typedb_entity_storage_type": (
        "digital_twin.modules.reasoning.infrastructure.typeql.storage_schema", "typedb_entity_storage_type",
    ),
    "typedb_expected_value": (
        "digital_twin.modules.reasoning.infrastructure.typeql.literals", "typedb_expected_value",
    ),
    "typedb_filter_operator": (
        "digital_twin.modules.reasoning.infrastructure.typeql.condition_queries", "typedb_filter_operator",
    ),
    "typedb_function_blueprint": (
        "digital_twin.modules.reasoning.infrastructure.typeql.profiles", "typedb_function_blueprint",
    ),
    "typedb_literal": (
        "digital_twin.modules.reasoning.infrastructure.typeql.literals", "typedb_literal",
    ),
    "typedb_literal_for_attribute": (
        "digital_twin.modules.reasoning.infrastructure.typeql.literals", "typedb_literal_for_attribute",
    ),
    "typedb_model_signal_bridge_batch_plan": (
        "digital_twin.modules.reasoning.infrastructure.typeql.model_signal_queries", "typedb_model_signal_bridge_batch_plan",
    ),
    "typedb_model_signal_bridge_batch_plan_summary": (
        "digital_twin.modules.reasoning.infrastructure.typeql.model_signal_queries", "typedb_model_signal_bridge_batch_plan_summary",
    ),
    "typedb_model_signal_bridge_batch_query": (
        "digital_twin.modules.reasoning.infrastructure.typeql.model_signal_queries", "typedb_model_signal_bridge_batch_query",
    ),
    "typedb_native_any_group_check_query": (
        "digital_twin.modules.reasoning.infrastructure.typeql.any_queries", "typedb_native_any_group_check_query",
    ),
    "typedb_native_condition_check_query": (
        "digital_twin.modules.reasoning.infrastructure.typeql.condition_queries", "typedb_native_condition_check_query",
    ),
    "typedb_native_condition_profile": (
        "digital_twin.modules.reasoning.infrastructure.typeql.profiles", "typedb_native_condition_profile",
    ),
    "typedb_native_indexed_evidence_match_query": (
        "digital_twin.modules.reasoning.infrastructure.typeql.indexed_queries", "typedb_native_indexed_evidence_match_query",
    ),
    "typedb_native_match_query": (
        "digital_twin.modules.reasoning.infrastructure.typeql.match_queries", "typedb_native_match_query",
    ),
    "typedb_native_reasoning_profile": (
        "digital_twin.modules.reasoning.infrastructure.typeql.profiles", "typedb_native_reasoning_profile",
    ),
    "typedb_native_rule_adaptive_target_parallelism_by_rule_id": (
        "digital_twin.modules.reasoning.infrastructure.typeql.planning", "typedb_native_rule_adaptive_target_parallelism_by_rule_id",
    ),
    "typedb_native_rule_any_relation_requirement": (
        "digital_twin.modules.reasoning.infrastructure.typeql.preflight", "typedb_native_rule_any_relation_requirement",
    ),
    "typedb_native_rule_execution_plan": (
        "digital_twin.modules.reasoning.infrastructure.typeql.planning", "typedb_native_rule_execution_plan",
    ),
    "typedb_native_rule_execution_plan_summary": (
        "digital_twin.modules.reasoning.infrastructure.typeql.planning", "typedb_native_rule_execution_plan_summary",
    ),
    "typedb_native_rule_execution_selection": (
        "digital_twin.modules.reasoning.infrastructure.typeql.planning", "typedb_native_rule_execution_selection",
    ),
    "typedb_native_rule_id": (
        "digital_twin.modules.reasoning.infrastructure.typeql.rule_shape", "typedb_native_rule_id",
    ),
    "typedb_native_rule_manifest_evidence_preflight": (
        "digital_twin.modules.reasoning.infrastructure.typeql.preflight", "typedb_native_rule_manifest_evidence_preflight",
    ),
    "typedb_native_rule_profile": (
        "digital_twin.modules.reasoning.infrastructure.typeql.profiles", "typedb_native_rule_profile",
    ),
    "typedb_native_rule_query_complexity": (
        "digital_twin.modules.reasoning.infrastructure.typeql.planning", "typedb_native_rule_query_complexity",
    ),
    "typedb_native_rule_required_conditions_preflight": (
        "digital_twin.modules.reasoning.infrastructure.typeql.preflight", "typedb_native_rule_required_conditions_preflight",
    ),
    "typedb_native_rule_required_relation_types": (
        "digital_twin.modules.reasoning.infrastructure.typeql.preflight", "typedb_native_rule_required_relation_types",
    ),
    "typedb_native_rule_runtime_query_plan": (
        "digital_twin.modules.reasoning.infrastructure.typeql.indexed_queries", "typedb_native_rule_runtime_query_plan",
    ),
    "typedb_native_rule_subject_properties_preflight": (
        "digital_twin.modules.reasoning.infrastructure.typeql.preflight", "typedb_native_rule_subject_properties_preflight",
    ),
    "typedb_native_rule_target_work_plan": (
        "digital_twin.modules.reasoning.infrastructure.typeql.planning", "typedb_native_rule_target_work_plan",
    ),
    "typedb_number": (
        "digital_twin.modules.reasoning.infrastructure.typeql.literals", "typedb_number",
    ),
    "typedb_number_literal": (
        "digital_twin.modules.reasoning.infrastructure.typeql.literals", "typedb_number_literal",
    ),
    "typedb_planned_candidate_symbols": (
        "digital_twin.modules.reasoning.infrastructure.typeql.rule_shape", "typedb_planned_candidate_symbols",
    ),
    "typedb_preflight_filter_key_and_operator": (
        "digital_twin.modules.reasoning.infrastructure.typeql.preflight", "typedb_preflight_filter_key_and_operator",
    ),
    "typedb_preflight_filters_match": (
        "digital_twin.modules.reasoning.infrastructure.typeql.preflight", "typedb_preflight_filters_match",
    ),
    "typedb_preflight_properties": (
        "digital_twin.modules.reasoning.infrastructure.typeql.preflight", "typedb_preflight_properties",
    ),
    "typedb_preflight_relation_condition_matches": (
        "digital_twin.modules.reasoning.infrastructure.typeql.preflight", "typedb_preflight_relation_condition_matches",
    ),
    "typedb_preflight_scalar_equal": (
        "digital_twin.modules.reasoning.infrastructure.typeql.preflight", "typedb_preflight_scalar_equal",
    ),
    "typedb_preflight_value_matches": (
        "digital_twin.modules.reasoning.infrastructure.typeql.preflight", "typedb_preflight_value_matches",
    ),
    "typedb_reasoning_subject_source_kinds": (
        "digital_twin.modules.reasoning.infrastructure.typeql.planning", "typedb_reasoning_subject_source_kinds",
    ),
    "typedb_relation_attribute": (
        "digital_twin.modules.reasoning.infrastructure.typeql.storage_schema", "typedb_relation_attribute",
    ),
    "typedb_relation_match_type": (
        "digital_twin.modules.reasoning.infrastructure.typeql.condition_queries", "typedb_relation_match_type",
    ),
    "typedb_relation_storage_type": (
        "digital_twin.modules.reasoning.infrastructure.typeql.storage_schema", "typedb_relation_storage_type",
    ),
    "typedb_rule_condition_payloads": (
        "digital_twin.modules.reasoning.infrastructure.typeql.rule_shape", "typedb_rule_condition_payloads",
    ),
    "typedb_rule_condition_value": (
        "digital_twin.modules.reasoning.infrastructure.typeql.preflight", "typedb_rule_condition_value",
    ),
    "typedb_rule_execution_failure_partition": (
        "digital_twin.modules.reasoning.infrastructure.typeql.planning", "typedb_rule_execution_failure_partition",
    ),
    "typedb_rule_execution_profile_fields": (
        "digital_twin.modules.reasoning.infrastructure.typeql.planning", "typedb_rule_execution_profile_fields",
    ),
    "typedb_rule_is_enabled": (
        "digital_twin.modules.reasoning.infrastructure.typeql.rule_shape", "typedb_rule_is_enabled",
    ),
    "typedb_rule_schema_capability_contract": (
        "digital_twin.modules.reasoning.infrastructure.typeql.storage_schema", "typedb_rule_schema_capability_contract",
    ),
    "typedb_scoped_manifest_member_clause": (
        "digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses", "typedb_scoped_manifest_member_clause",
    ),
    "typedb_source_kind_uses_symbol_scope": (
        "digital_twin.modules.reasoning.infrastructure.typeql.rule_shape", "typedb_source_kind_uses_symbol_scope",
    ),
    "typedb_string": (
        "digital_twin.modules.reasoning.infrastructure.typeql.literals", "typedb_string",
    ),
    "typedb_subject_attribute": (
        "digital_twin.modules.reasoning.infrastructure.typeql.storage_schema", "typedb_subject_attribute",
    ),
    "typedb_target_attribute": (
        "digital_twin.modules.reasoning.infrastructure.typeql.storage_schema", "typedb_target_attribute",
    ),
    "typedb_value_match": (
        "digital_twin.modules.reasoning.infrastructure.typeql.literals", "typedb_value_match",
    ),
    "typedb_world_id_attribute_variable": (
        "digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses", "typedb_world_id_attribute_variable",
    ),
    "typedb_world_id_constraint": (
        "digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses", "typedb_world_id_constraint",
    ),
    "typedb_world_id_value_match": (
        "digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses", "typedb_world_id_value_match",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
