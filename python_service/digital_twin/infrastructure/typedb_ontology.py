from digital_twin.modules.reasoning.infrastructure.graph_writes import graph_save as _graph_writes_graph_save
from digital_twin.modules.reasoning.infrastructure.graph_writes import graph_write as _graph_writes_graph_write
from digital_twin.modules.reasoning.infrastructure.graph_writes import legacy_activation as _graph_writes_legacy_activation
from digital_twin.modules.reasoning.infrastructure.graph_writes import node_rows as _graph_writes_node_rows
from digital_twin.modules.reasoning.infrastructure.graph_writes import row_queries as _graph_writes_row_queries
from digital_twin.modules.reasoning.infrastructure.graph_writes import rulebox_commands as _graph_writes_rulebox_commands
from digital_twin.modules.reasoning.infrastructure.graph_writes import rulebox_history as _graph_writes_rulebox_history
from digital_twin.modules.reasoning.infrastructure.graph_writes import rulebox_read as _graph_writes_rulebox_read
from digital_twin.modules.reasoning.infrastructure.graph_writes import write_policy as _graph_writes_write_policy
from digital_twin.modules.reasoning.infrastructure.graph_writes.graph_save_ports import SaveGraphBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.graph_write_ports import ClearInferenceboxBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.graph_write_ports import GraphInsertQueriesBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.graph_write_ports import InsertQueriesBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.graph_write_ports import StaticGraphInsertQueriesBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.graph_write_ports import WriteGraphBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.legacy_activation_ports import AboxActivePointerGraphBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.legacy_activation_ports import AboxProjectionMarkerGraphBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.legacy_activation_ports import ActivateAboxGenerationBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.node_rows_ports import BeliefNodeRowsBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.node_rows_ports import SupportRelationRowsBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.row_queries_ports import InferenceboxGivenRelationInsertPlansBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.row_queries_ports import InferenceboxInsertQueriesBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.row_queries_ports import NodeInsertClauseBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.row_queries_ports import RelationInsertClauseBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.row_queries_ports import RelationMatchClauseBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.rulebox_commands_ports import EnsureRuleboxVersionBaselineBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.rulebox_commands_ports import SaveRuleboxBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.rulebox_history_ports import AppendRuleboxVersionBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.rulebox_read_ports import RuleboxSnapshotBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import AboxDeleteBatchSizeBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import AboxInactiveGenerationKeepCountBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import AboxInactiveGenerationMaxPrunePerSaveBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import AboxIncrementalCleanupBatchSizeBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import AboxIncrementalCleanupMaxBatchesPerSaveBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import AboxNodeBatchSizeBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import AboxRelationBatchSizeBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import AboxWriteTransactionQueryCountBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import DeferredMaintenanceAboxDeleteBatchSizeBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import DeferredMaintenanceAboxMaxDeleteBatchesBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import DeferredMaintenanceAboxMaxManifestsBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import GivenRelationBatchSizeBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import GivenRelationWritesEnabledBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import GraphWriteTransactionQueryCountBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import InferenceboxGivenRelationBatchSizeBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import InferenceboxGivenRelationWritesEnabledBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import InferenceboxRelationBatchSizeBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import InferenceboxWriteTransactionQueryCountBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import StaticNodeInsertBatchSizeBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import StaticWriteTransactionQueryCountBindings
from digital_twin.modules.reasoning.infrastructure.graph_writes.write_policy_ports import WriteQueryMaxBytesBindings

import copy
import hashlib
import json
import math
import os
import re
import signal
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from functools import wraps
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

from ..domain.abox_lifecycle import MANIFEST_PATCH_BOUNDARY_VERSION
from ..domain.ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from ..domain.ontology_current_state import (
    CURRENT_STATE_ABOX_PERSISTENCE_MODE,
    copy_on_write_generation_id,
    is_current_state_persistence_mode,
    next_current_state_slot,
)
from ..domain.model_signal_interpretation import (
    MODEL_SIGNAL_BRIDGE_VERSION,
    is_model_signal_interpretation_rule,
    model_signal_bridge_conditions,
    model_signal_bridge_definition_key,
    model_signal_bridge_rule_payload,
    model_signal_conditions,
    model_signal_interpretation_contract_id,
)
from ..domain.ontology_semantics import (
    SEMANTIC_STORAGE_CONTRACT_VERSION,
    entity_semantic_type,
    primary_tbox_class,
    relation_semantic_type,
    semantic_class_types,
    semantic_relation_types,
    semantic_storage_type_names,
    semantic_typeql_schema,
    typedb_context_type,
)
from ..domain.ontology_tbox import tbox_class_def
from ..domain.investment_ubiquitous_language import investment_language_registry
from ..domain.ontology_inference_materializer import (
    evidence_relation_index,
    materialize_rule_inference,
    ontology_property_value_matches,
)
from ..domain.ontology_rulebox_catalog import default_graph_inference_rules
from ..domain.ontology_rulebox_contracts import GRAPH_REASONER_VERSION, GraphInferenceRule
from ..domain.ontology_rule_execution_policy import (
    RULE_EXECUTION_POLICY_VERSION,
    rule_execution_profile,
)
from ..domain.ontology_execution_units import (
    rules_allow_subject_fanout,
)
from ..domain.ontology_rulebox_governance import (
    normalize_rule_change_candidate,
    rulebox_governance_candidates,
    rulebox_rules_hash,
    rulebox_version_payload,
)
from ..domain.ontology_change_impact import compact_inference_impact_plan, scope_family, scope_symbol
from ..domain.ontology_rule_manifest import rule_dependency_reverse_index
from ..domain.ontology_native_rule_planning import normalize_native_rule_planner_topology
from ..domain.ontology_projection_fingerprint import (
    material_graph_fingerprint,
    stable_value,
)
from ..domain.ontology_runtime_operations import native_rule_timing_profile
from ..domain.ontology_schema import default_tbox_metadata, normalize_tbox_metadata
from ..domain.ontology_subject_fanout import evaluate_subject_fanout_comparison
from ..domain.world_partitioned_reasoning import (
    WORLD_PARTITIONED_REASONING_VERSION,
    compile_world_partitioned_rules,
)
from ..domain.hypothesis_calibration import hypothesis_calibration_snapshot_from_abox_rows
from ..domain.ontology_scopes import (
    SCOPED_ABOX_MANIFEST_VERSION,
    SCOPED_ABOX_PERSISTENCE_MODE,
    SCOPE_NODE_INVENTORY_VERSION,
    support_relation_key,
)
from ..domain.ontology_worlds import (
    KNOWLEDGE_WORLD_TYPE,
    MARKET_WORLD_TYPE,
    SHARED_PREMISE_WORLD_TYPE,
    world_type_from_id,
)
from .graph_store_inferencebox import (
    inferencebox_entity_payload,
    inferencebox_relation_payload,
    inferencebox_snapshot_from_rows,
    inferencebox_trace_payload,
)
from .graph_store_lifecycle import (
    active_tbox_metadata_from_rows,
    active_tbox_metadata_unavailable,
    graph_box_entity_counts,
    graph_box_relation_counts,
    ontology_seed_graph_from_artifact,
    ontology_seed_graph,
)
from .graph_store_payloads import (
    GraphStoreOntologyRowMapperMixin,
    PROMOTED_NUMERIC_ENTITY_FIELDS,
    PROMOTED_TEXT_ENTITY_FIELDS,
    condition_relation_filter_values,
    list_of_strings,
    number_or_none,
)
from .graph_store_rulebox import (
    add_rulebox_version_concept,
    rulebox_graph_from_rules,
    rulebox_rules_from_payload,
    rulebox_snapshot_from_rows,
    rulebox_rules_to_payload,
)
from .settings import runtime_settings, utc_now

from digital_twin.modules.reasoning.infrastructure.typeql.any_queries import typedb_native_any_group_check_query
from digital_twin.modules.reasoning.infrastructure.typeql.condition_queries import (
    typedb_condition_pattern,
    typedb_entity_match_type,
    typedb_filter_operator,
    typedb_native_condition_check_query,
    typedb_relation_match_type,
)
from digital_twin.modules.reasoning.infrastructure.typeql.constants import (
    NATIVE_RULE_EVIDENCE_READ_INDEX_BATCH_SIZE,
    NATIVE_RULE_EVIDENCE_READ_INDEX_VERSION,
    NATIVE_RULE_INDEXED_QUERY_MAX_STORAGE_IDS,
    TYPEDB_COMMON_NODE_ATTRIBUTES,
    TYPEDB_FUNCTION_OPERATORS,
    TYPEDB_FUNCTION_RELATION_FILTERS,
    TYPEDB_FUNCTION_SUBJECT_FIELDS,
    TYPEDB_FUNCTION_TARGET_FILTERS,
    TYPEDB_NATIVE_REASONING_LAYER,
    TYPEDB_NATIVE_REASONING_PROFILE_VERSION,
    TYPEDB_NATIVE_RULE_ENGINE_VERSION,
    TYPEDB_NUMERIC_ATTRIBUTES,
    TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES,
    TYPEDB_PROMOTED_TEXT_ATTRIBUTES,
    TYPEDB_STRING_ATTRIBUTES,
)
from digital_twin.modules.reasoning.infrastructure.typeql.indexed_queries import (
    typedb_native_indexed_evidence_match_query,
    typedb_native_rule_runtime_query_plan,
)
from digital_twin.modules.reasoning.infrastructure.typeql.literals import (
    typedb_expected_value,
    typedb_literal,
    typedb_literal_for_attribute,
    typedb_number,
    typedb_number_literal,
    typedb_string,
    typedb_value_match,
)
from digital_twin.modules.reasoning.infrastructure.typeql.match_queries import typedb_native_match_query
from digital_twin.modules.reasoning.infrastructure.typeql.model_signal_queries import (
    typedb_dispatch_model_signal_bridge_rows,
    typedb_model_signal_bridge_batch_plan,
    typedb_model_signal_bridge_batch_plan_summary,
    typedb_model_signal_bridge_batch_query,
)
from digital_twin.modules.reasoning.infrastructure.typeql.planning import (
    typedb_native_rule_adaptive_target_parallelism_by_rule_id,
    typedb_native_rule_execution_plan,
    typedb_native_rule_execution_plan_summary,
    typedb_native_rule_execution_selection,
    typedb_native_rule_query_complexity,
    typedb_native_rule_target_work_plan,
    typedb_reasoning_subject_source_kinds,
    typedb_rule_execution_failure_partition,
    typedb_rule_execution_profile_fields,
)
from digital_twin.modules.reasoning.infrastructure.typeql.preflight import (
    typedb_native_rule_any_relation_requirement,
    typedb_native_rule_manifest_evidence_preflight,
    typedb_native_rule_required_conditions_preflight,
    typedb_native_rule_required_relation_types,
    typedb_native_rule_subject_properties_preflight,
    typedb_preflight_filter_key_and_operator,
    typedb_preflight_filters_match,
    typedb_preflight_properties,
    typedb_preflight_relation_condition_matches,
    typedb_preflight_scalar_equal,
    typedb_preflight_value_matches,
    typedb_rule_condition_value,
)
from digital_twin.modules.reasoning.infrastructure.typeql.profiles import (
    condition_blocker,
    filter_blockers,
    typedb_function_blueprint,
    typedb_native_condition_profile,
    typedb_native_reasoning_profile,
    typedb_native_rule_profile,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
    normalized_condition_role,
    symbol_from_subject,
    typedb_native_rule_id,
    typedb_planned_candidate_symbols,
    typedb_rule_condition_payloads,
    typedb_rule_is_enabled,
    typedb_source_kind_uses_symbol_scope,
)
from digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses import (
    typedb_active_abox_member_clause,
    typedb_active_abox_pointer_clause,
    typedb_active_abox_snapshot_clause,
    typedb_active_scoped_abox_member_clause,
    typedb_active_worldview_manifest_clause,
    typedb_scoped_manifest_member_clause,
    typedb_world_id_attribute_variable,
    typedb_world_id_constraint,
    typedb_world_id_value_match,
)
from digital_twin.modules.reasoning.infrastructure.typeql.storage_schema import (
    typedb_entity_storage_type,
    typedb_relation_attribute,
    typedb_relation_storage_type,
    typedb_rule_schema_capability_contract,
    typedb_subject_attribute,
    typedb_target_attribute,
)

from digital_twin.modules.reasoning.infrastructure.inference_publication import (
    lifecycle as _inference_lifecycle,
    markers as _inference_markers,
    validation as _inference_validation,
    writer as _inference_writer,
)
from digital_twin.modules.reasoning.infrastructure.inference_publication.markers import inference_generation_delete_queries
from digital_twin.modules.reasoning.infrastructure.inference_publication.ports import PublicationRuntime
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import json_object, typedb_bool


from digital_twin.modules.reasoning.infrastructure.abox_persistence import (
    controls as _abox_controls,
    lifecycle as _abox_lifecycle,
    writer as _abox_writer,
)
from digital_twin.modules.reasoning.infrastructure.abox_persistence.ports import ABoxRuntime
from digital_twin.modules.reasoning.infrastructure.abox_persistence.world_calls import typedb_call_for_world, typedb_world_kwargs
from digital_twin.modules.reasoning.infrastructure.typedb_runtime import (
    bootstrap as _typedb_bootstrap,
    connection as _typedb_connection,
    http as _typedb_http,
    inspection as _typedb_inspection,
    lifecycle as _typedb_lifecycle,
    migrations as _typedb_migrations,
    readiness as _typedb_readiness,
    schema_plan as _typedb_schema_plan,
    transactions as _typedb_transactions,
)
from digital_twin.modules.reasoning.infrastructure.typedb_runtime.ports import SchemaReadinessCache, TypeDBRuntime
from digital_twin.modules.reasoning.infrastructure.typedb_runtime.constants import (
    DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE,
    DEFAULT_TYPEDB_FRESH_SCHEMA_BOOTSTRAP_BATCH_SIZE,
    DEFAULT_TYPEDB_FRESH_SCHEMA_BOOTSTRAP_TIMEOUT_SECONDS,
)


from digital_twin.modules.reasoning.infrastructure.abox_candidates import (
    recovery as _abox_candidate_recovery,
    retry as _abox_candidate_retry,
    row_image as _abox_candidate_row_image,
    rows as _abox_candidate_rows,
    scope_plan as _abox_candidate_scope_plan,
    selection as _abox_candidate_selection,
    validation as _abox_candidate_validation,
)
from digital_twin.modules.reasoning.infrastructure.abox_candidates.identity import (
    CURRENT_STATE_STORAGE_ONLY_KEYS,
    ontology_row_content_fingerprint,
    ontology_storage_id,
    relation_row_id,
)


from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    native_rule_manifest_index_required,
)
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    typedb_native_rule_planner_topology_for_execution,
)
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    native_rule_evidence_read_index_from_rows,
)
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    native_rule_evidence_read_index_from_components,
)
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    merge_native_rule_evidence_read_index,
)
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    normalize_native_rule_evidence_read_index,
)
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    typedb_native_rule_evidence_read_index_for_execution,
)
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    typedb_native_rule_evidence_read_allows_active_membership_recovery,
)
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    native_rule_matched_evidence_storage_plan,
)
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    typedb_projection_preflight_graph_for_execution,
)
from digital_twin.modules.reasoning.infrastructure.manifest import graphs as _manifest_graphs
from digital_twin.modules.reasoning.infrastructure.manifest import (
    graphs_ports as _manifest_graphs_ports,
)
from digital_twin.modules.reasoning.infrastructure.manifest import counts as _manifest_counts
from digital_twin.modules.reasoning.infrastructure.manifest import (
    counts_ports as _manifest_counts_ports,
)
from digital_twin.modules.reasoning.infrastructure.manifest import indexes as _manifest_indexes
from digital_twin.modules.reasoning.infrastructure.manifest import (
    indexes_ports as _manifest_indexes_ports,
)
from digital_twin.modules.reasoning.infrastructure.manifest import repair as _manifest_repair
from digital_twin.modules.reasoning.infrastructure.manifest import (
    repair_ports as _manifest_repair_ports,
)
from digital_twin.modules.reasoning.infrastructure.manifest import (
    observation as _manifest_observation,
)
from digital_twin.modules.reasoning.infrastructure.manifest import (
    observation_ports as _manifest_observation_ports,
)
from digital_twin.modules.reasoning.infrastructure.manifest import save as _manifest_save
from digital_twin.modules.reasoning.infrastructure.manifest import (
    save_ports as _manifest_save_ports,
)
from digital_twin.modules.reasoning.infrastructure.manifest import (
    read_index as _manifest_read_index,
)
from digital_twin.modules.reasoning.infrastructure.manifest import (
    read_index_ports as _manifest_read_index_ports,
)

from digital_twin.modules.reasoning.infrastructure.projection_lock import (
    policy as _projection_lock_policy,
)
from digital_twin.modules.reasoning.infrastructure.projection_lock import (
    policy_ports as _projection_lock_policy_ports,
)
from digital_twin.modules.reasoning.infrastructure.projection_lock import (
    lease as _projection_lock_lease,
)
from digital_twin.modules.reasoning.infrastructure.projection_lock import (
    lease_ports as _projection_lock_lease_ports,
)
from digital_twin.modules.reasoning.infrastructure.projection_lock import (
    coordinator as _projection_lock_coordinator,
)
from digital_twin.modules.reasoning.infrastructure.projection_lock import (
    coordinator_ports as _projection_lock_coordinator_ports,
)
from digital_twin.modules.reasoning.infrastructure.projection_lock import (
    recovery as _projection_lock_recovery,
)
from digital_twin.modules.reasoning.infrastructure.projection_lock import (
    recovery_ports as _projection_lock_recovery_ports,
)
from digital_twin.modules.reasoning.infrastructure.graph_reads import (
    inventory as _graph_reads_inventory,
)
from digital_twin.modules.reasoning.infrastructure.graph_reads import (
    inventory_ports as _graph_reads_inventory_ports,
)
from digital_twin.modules.reasoning.infrastructure.graph_reads import (
    execution as _graph_reads_execution,
)
from digital_twin.modules.reasoning.infrastructure.graph_reads import (
    execution_ports as _graph_reads_execution_ports,
)
from digital_twin.modules.reasoning.infrastructure.graph_reads import rows as _graph_reads_rows
from digital_twin.modules.reasoning.infrastructure.graph_reads import (
    rows_ports as _graph_reads_rows_ports,
)
from digital_twin.modules.reasoning.infrastructure.graph_reads import (
    metadata as _graph_reads_metadata,
)
from digital_twin.modules.reasoning.infrastructure.graph_reads import (
    metadata_ports as _graph_reads_metadata_ports,
)
from digital_twin.modules.reasoning.infrastructure.graph_reads import (
    metrics as _graph_reads_metrics,
)
from digital_twin.modules.reasoning.infrastructure.graph_reads import (
    metrics_ports as _graph_reads_metrics_ports,
)
from digital_twin.modules.reasoning.infrastructure.graph_maintenance import (
    current_state as _graph_maintenance_current_state,
)
from digital_twin.modules.reasoning.infrastructure.graph_maintenance import (
    current_state_ports as _graph_maintenance_current_state_ports,
)
from digital_twin.modules.reasoning.infrastructure.graph_maintenance import (
    orphans as _graph_maintenance_orphans,
)
from digital_twin.modules.reasoning.infrastructure.graph_maintenance import (
    orphans_ports as _graph_maintenance_orphans_ports,
)
from digital_twin.modules.reasoning.infrastructure.graph_maintenance import (
    manifests as _graph_maintenance_manifests,
)
from digital_twin.modules.reasoning.infrastructure.graph_maintenance import (
    manifests_ports as _graph_maintenance_manifests_ports,
)
from digital_twin.modules.reasoning.infrastructure.graph_maintenance import (
    runner as _graph_maintenance_runner,
)
from digital_twin.modules.reasoning.infrastructure.graph_maintenance import (
    runner_ports as _graph_maintenance_runner_ports,
)
from digital_twin.modules.reasoning.infrastructure.graph_maintenance import (
    generations as _graph_maintenance_generations,
)
from digital_twin.modules.reasoning.infrastructure.graph_maintenance import (
    generations_ports as _graph_maintenance_generations_ports,
)

from digital_twin.modules.reasoning.infrastructure.graph_reads.state import QueryMetricState
from digital_twin.modules.reasoning.infrastructure.projection_lock.state import ProjectionLeaseState


from digital_twin.modules.reasoning.infrastructure.native_execution import (
    entry as _native_execution_entry,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    entry_ports as _native_execution_entry_ports,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    bridge as _native_execution_bridge,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    bridge_ports as _native_execution_bridge_ports,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    retry as _native_execution_retry,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    retry_ports as _native_execution_retry_ports,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    fanout as _native_execution_fanout,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    fanout_ports as _native_execution_fanout_ports,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    matching as _native_execution_matching,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    matching_ports as _native_execution_matching_ports,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    profile as _native_execution_profile,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    profile_ports as _native_execution_profile_ports,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    context as _native_execution_context,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    context_ports as _native_execution_context_ports,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    staged as _native_execution_staged,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    staged_ports as _native_execution_staged_ports,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    runner as _native_execution_runner,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    runner_ports as _native_execution_runner_ports,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    cycle as _native_execution_cycle,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    cycle_ports as _native_execution_cycle_ports,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    validation as _native_execution_validation,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    validation_ports as _native_execution_validation_ports,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    evidence as _native_execution_evidence,
)
from digital_twin.modules.reasoning.infrastructure.native_execution import (
    evidence_ports as _native_execution_evidence_ports,
)
from digital_twin.modules.reasoning.infrastructure.graph_reads import (
    inference as _graph_reads_inference,
)
from digital_twin.modules.reasoning.infrastructure.graph_reads import (
    inference_ports as _graph_reads_inference_ports,
)

from digital_twin.modules.reasoning.infrastructure.static_seed import (
    bootstrap as _static_seed_bootstrap,
    graphs as _static_seed_graphs,
    identity as _static_seed_identity,
    persistence as _static_seed_persistence,
    preflight as _static_seed_preflight,
    reads as _static_seed_reads,
    repair as _static_seed_repair,
    restore as _static_seed_restore,
    schema as _static_seed_schema,
)
from digital_twin.modules.reasoning.infrastructure.static_seed.bootstrap_ports import BootstrapBindings
from digital_twin.modules.reasoning.infrastructure.static_seed.identity import (
    rulebox_runtime_metadata,
    rulebox_structural_fingerprint,
)
from digital_twin.modules.reasoning.infrastructure.static_seed.persistence_ports import PersistenceBindings
from digital_twin.modules.reasoning.infrastructure.static_seed.repair_ports import RepairBindings
from digital_twin.modules.reasoning.infrastructure.static_seed.schema import slim_typeql_node_schema

from digital_twin.modules.reasoning.infrastructure.backend_constants import (
    NATIVE_RULE_EVIDENCE_READ_INDEX_TYPED_VERSION,
    NATIVE_RULE_EVIDENCE_READ_INDEX_LEGACY_VERSION,
    TYPEDB_NATIVE_REASONING_MODE,
    TYPEDB_NATIVE_BLOCKED_MODE,
    TYPEDB_NATIVE_REQUIRED_MODE,
    TYPEDB_NATIVE_MATERIALIZATION_SOURCE,
    SCOPED_ABOX_WRITE_LEASE_ID,
    SCOPED_ABOX_WRITE_LEASE_BOX,
    SCOPED_ABOX_WRITE_LEASE_VERSION,
    TYPEDB_PROJECTION_COORDINATOR_WORLD_ID,
    TYPEDB_PROJECTION_COORDINATOR_VERSION,
    DEFAULT_TYPEDB_NATIVE_RULE_QUERY_TIMEOUT_SECONDS,
    DEFAULT_TYPEDB_NATIVE_RULE_INDEXED_ANY_CONDITION_QUERY_TIMEOUT_SECONDS,
    DEFAULT_TYPEDB_NATIVE_RULE_EXECUTION_BUDGET_SECONDS,
    DEFAULT_TYPEDB_NATIVE_RULE_PARALLELISM,
    DEFAULT_TYPEDB_NATIVE_RULE_TARGET_PARALLELISM,
)


class TypeDBOperationTimeout(TimeoutError):
    pass


@contextmanager
def typedb_operation_timeout(seconds: float, label: str):
    seconds = float(seconds or 0)
    if seconds <= 0 or threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGALRM"):
        yield
        return

    def timeout_handler(_signum, _frame):
        raise TypeDBOperationTimeout(label + " timed out after " + str(round(seconds, 1)) + "s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer and previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def typedb_projection_coordinator_summary(lease: Dict[str, object] = None) -> Dict[str, object]:
    """Expose coordinator state without returning the durable lease payload."""
    allowed = {
        "acquired",
        "status",
        "coordinator",
        "coordinatorVersion",
        "requestedWorldId",
        "leaseOwner",
        "leaseExpiresAtEpoch",
        "leaseRemainingSeconds",
        "recommendedRetryAfterSeconds",
        "reason",
    }
    return {
        key: value
        for key, value in dict(lease or {}).items()
        if key in allowed and value not in (None, "", [], {})
    }


def typedb_projection_world_from_graph(args, kwargs) -> str:
    graph = kwargs.get("graph") if isinstance(kwargs, dict) else None
    if graph is None and args:
        graph = args[0]
    worldview = getattr(graph, "worldview", {}) if graph is not None else {}
    return str((worldview or {}).get("worldId") or "").strip()


def typedb_projection_world_from_payload(args, kwargs) -> str:
    payload = kwargs.get("payload") if isinstance(kwargs, dict) else None
    if payload is None and args:
        payload = args[0]
    values = dict(payload or {}) if isinstance(payload, dict) else {}
    return str(values.get("worldId") or values.get("ontologyWorldId") or "").strip()


def typedb_projection_world_from_recovery(args, kwargs) -> str:
    if isinstance(kwargs, dict) and str(kwargs.get("world_id") or "").strip():
        return str(kwargs.get("world_id") or "").strip()
    return str(args[0] if args else "").strip()


def typedb_projection_world_from_manifest_index(args, kwargs) -> str:
    if isinstance(kwargs, dict) and str(kwargs.get("world_id") or "").strip():
        return str(kwargs.get("world_id") or "").strip()
    graph = kwargs.get("graph") if isinstance(kwargs, dict) else None
    if graph is None and args:
        graph = args[0]
    worldview = getattr(graph, "worldview", {}) if graph is not None else {}
    return str((worldview or {}).get("worldId") or "").strip()


def coordinated_typedb_projection_write(
    owner_prefix: str,
    world_resolver=None,
    bootstrap_schema: bool = False,
):
    """Apply the production TypeDB writer boundary to every mutating entrypoint.

    The coordinator is intentionally enabled only by the production repository
    factory. Bare adapters used by deterministic unit tests preserve their
    lightweight behavior, while every runtime adapter serializes physical
    TypeDB writes across logical ontology worlds.
    """
    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            enabled = getattr(self, "projection_coordinator_write_enforced", None)
            scope = getattr(self, "projection_coordinator_write_scope", None)
            if not callable(enabled) or not enabled() or not callable(scope):
                return method(self, *args, **kwargs)
            schema_bootstrap = {}
            if bootstrap_schema:
                synchronize_schema = getattr(self, "sync_base_schema_contract", None)
                if not callable(synchronize_schema):
                    return {
                        "configured": bool(getattr(self, "address", "")),
                        "saved": False,
                        "status": "schema-bootstrap-unavailable",
                        "graphStore": "typedb",
                        "preservedActiveGeneration": True,
                        "reason": "TypeDB static seed requires a base schema synchronizer.",
                    }
                try:
                    schema_bootstrap = dict(synchronize_schema() or {})
                except Exception as error:  # noqa: BLE001 - preserve the active graph when bootstrap cannot run.
                    schema_bootstrap = {
                        "configured": bool(getattr(self, "address", "")),
                        "saved": False,
                        "status": "error",
                        "reason": str(error)[:220],
                    }
                if not bool(schema_bootstrap.get("saved")):
                    return {
                        "configured": bool(getattr(self, "address", "")),
                        "saved": False,
                        "status": "schema-bootstrap-failed",
                        "graphStore": "typedb",
                        "preservedActiveGeneration": True,
                        "retryable": True,
                        "reason": str(
                            schema_bootstrap.get("reason")
                            or "TypeDB static seed could not prepare the base schema."
                        )[:220],
                        "schemaBootstrap": schema_bootstrap,
                    }
            world_id = ""
            if callable(world_resolver):
                try:
                    world_id = str(world_resolver(args, kwargs) or "").strip()
                except Exception:
                    world_id = ""
            with scope(owner_prefix, world_id) as lease:
                if not bool((lease or {}).get("acquired")):
                    return {
                        "configured": bool(getattr(self, "address", "")),
                        "saved": False,
                        "status": "deferred-projection-coordinator",
                        "graphStore": "typedb",
                        "preservedActiveGeneration": True,
                        "retryable": True,
                        "recommendedRetryAfterSeconds": int(
                            (lease or {}).get("recommendedRetryAfterSeconds") or 10
                        ),
                        "reason": str(
                            (lease or {}).get("reason")
                            or "다른 TypeDB 투영 또는 규칙 실행이 데이터베이스 쓰기 경계를 사용 중입니다."
                        )[:220],
                        "projectionCoordinator": typedb_projection_coordinator_summary(lease),
                    }
                result = method(self, *args, **kwargs)
                if isinstance(result, dict):
                    if schema_bootstrap:
                        result.setdefault("schemaBootstrap", schema_bootstrap)
                    result.setdefault(
                        "projectionCoordinator",
                        typedb_projection_coordinator_summary(lease),
                    )
                return result
        return wrapped
    return decorate




def typedb_error_code(error: object) -> str:
    text = str(error or "").lower()
    if "scoped abox candidate verification failed" in text:
        return "typedbCandidateVerificationError"
    if any(term in text for term in ["unable to connect", "connection refused", "connect failed", "unavailable"]):
        return "typedbConnectionError"
    # TypeDB can surface a cancelled bounded read as TSV13 without including
    # the word "timeout". Treat that interruption as a timeout so the native
    # rule executor can use its bounded recovery path instead of misreporting
    # a transient query-shape issue as an unknown read error.
    if (
        "[tsv13]" in text and "execution interrupted" in text
    ) or any(term in text for term in ["timeout", "timed out", "deadline"]):
        return "typedbTimeout"
    if any(term in text for term in ["schema"]):
        return "typedbSchemaError"
    return "typedbReadError"


def typedb_native_rule_execution_incomplete_diagnostic(
    query_failures: Iterable[Dict[str, object]] = None,
    skipped_rules: Iterable[Dict[str, object]] = None,
    execution_budget_exhausted: bool = False,
) -> Dict[str, object]:
    """Describe the first blocking native-rule failure without hiding it.

    ``skipped_rules`` also contains normal not-applicable planner results, so
    only an explicit execution failure is surfaced as the blocker. This is
    operational diagnostics, not a secondary rule evaluator: TypeDB remains
    the source of the actual match result.
    """
    failures = [
        dict(item)
        for item in query_failures or []
        if isinstance(item, dict) and str(item.get("status") or "").strip()
    ]
    if not failures:
        normal_statuses = {"not-applicable", "not-applicable-preflight", "planned"}
        failures = [
            dict(item)
            for item in skipped_rules or []
            if isinstance(item, dict)
            and str(item.get("status") or "").strip()
            and str(item.get("status") or "").strip() not in normal_statuses
        ]
    blocking = dict(failures[0] or {}) if failures else {}
    status = str(blocking.get("status") or "").strip()
    rule_id = str(blocking.get("ruleId") or "").strip()
    detail = str(blocking.get("reason") or "").strip()
    reason = "TypeDB native rule execution did not complete for every applicable rule."
    reason_code = "typedbNativeRuleExecutionPartial"
    if status == "query-timeout":
        reason_code = "typedbNativeRuleQueryTimeout"
    elif status == "deferred-by-runtime-budget" or execution_budget_exhausted:
        reason_code = "typedbNativeRuleExecutionBudgetExceeded"
    elif status:
        reason_code = "typedbNativeRuleQueryFailure"
    if rule_id or status or detail:
        context = " / ".join(item for item in [rule_id, status, detail] if item)
        reason += " Blocking rule: " + context[:360] + "."
    return {
        "reasonCode": reason_code,
        "reason": reason,
        "blockingRule": {
            key: blocking.get(key)
            for key in ["ruleId", "status", "reason", "candidateSymbols"]
            if blocking.get(key) not in (None, "", [])
        },
    }


def inference_target_coverage_payload(
    requested_symbols: Iterable[str] = None,
    evaluated_symbols: Iterable[str] = None,
) -> Dict[str, object]:
    """Describe whether an InferenceBox generation covers a requested read.

    A generation may be intentionally scoped to one changed symbol.  An empty
    result for another requested symbol is therefore not a verified no-match.
    This is an operational completeness contract, not a Python investment
    rule: TypeDB still owns the actual rule result for every evaluated symbol.
    """
    requested = clean_symbols_from_payload(requested_symbols)
    evaluated = clean_symbols_from_payload(evaluated_symbols)
    missing = sorted(set(requested) - set(evaluated))
    if not requested:
        status = "not-requested"
        complete = True
        reason = "No target symbols were requested for this InferenceBox read."
    elif not evaluated:
        status = "unknown"
        complete = None
        reason = "The active InferenceBox generation does not expose evaluated target symbols."
    elif missing:
        status = "partial"
        complete = False
        reason = "The active InferenceBox generation has not evaluated every requested symbol."
    else:
        status = "complete"
        complete = True
        reason = "The active InferenceBox generation covers every requested symbol."
    return {
        "targetCoverageStatus": status,
        "targetCoverageComplete": complete,
        "requestedSymbols": requested,
        "evaluatedSymbols": evaluated,
        "notEvaluatedSymbols": missing,
        "targetCoverageReason": reason,
    }


def apply_inference_target_coverage(
    snapshot: Dict[str, object],
    requested_symbols: Iterable[str] = None,
) -> Dict[str, object]:
    """Fail closed when a scoped generation omits a requested symbol."""
    payload = snapshot
    coverage = inference_target_coverage_payload(
        requested_symbols=requested_symbols,
        evaluated_symbols=payload.get("targetSymbols"),
    )
    payload.update(coverage)
    if (
        coverage["targetCoverageStatus"] != "partial"
        or str(payload.get("status") or "").strip().lower() not in {"ok", "empty"}
    ):
        return payload

    requested = ", ".join(coverage["requestedSymbols"])
    evaluated = ", ".join(coverage["evaluatedSymbols"])
    missing = ", ".join(coverage["notEvaluatedSymbols"])
    payload.update({
        "status": "not-evaluated",
        "reason": (
            "현재 활성 InferenceBox 세대는 " + evaluated
            + "만 TypeDB 네이티브 규칙으로 계산했습니다. 요청한 " + missing
            + "는 아직 이 세대에서 계산되지 않아 '결과 없음'으로 해석하지 않습니다."
        ),
        "nativeInferenceNoMatch": False,
        "entities": [],
        "relations": [],
        "traces": [],
        "entityCount": 0,
        "relationCount": 0,
        "traceCount": 0,
        "nativeEntityCount": 0,
        "nativeRelationCount": 0,
        "nativeTraceCount": 0,
        "nativeTypeDbReasoningUsed": False,
        "typedbNativeRuleReasoningUsed": False,
        "targetCoverageRequestedLabel": requested,
    })
    return payload


def materialization_preview_diff_payload(
    baseline_inferencebox: Dict[str, object],
    matched_count: int,
    candidate_rule_count: int,
    native_query_used: bool = False,
) -> Dict[str, object]:
    baseline = baseline_inferencebox if isinstance(baseline_inferencebox, dict) else {}
    baseline_relations = int(number_or_none(baseline.get("relationCount")) or 0)
    baseline_traces = int(number_or_none(baseline.get("traceCount")) or 0)
    matched = int(number_or_none(matched_count) or 0)
    return {
        "baselineRelationCount": baseline_relations,
        "baselineTraceCount": baseline_traces,
        "candidateMatchedCount": matched,
        "candidateRuleCount": int(number_or_none(candidate_rule_count) or 0),
        "matchedMinusBaselineRelations": matched - baseline_relations,
        "validationOnly": True,
        "mutatedOperationalRuleBox": False,
        "wroteInferenceBox": False,
        "nativeQueryUsed": bool(native_query_used),
    }


def inference_generation_id() -> str:
    return "inference-generation:" + uuid.uuid4().hex[:16]


def generated_inference_id(original_id: str, generation_id: str) -> str:
    original = str(original_id or "unknown")
    digest = hashlib.sha256((str(generation_id or "") + "|" + original).encode("utf-8")).hexdigest()[:12]
    return original + ":gen:" + digest






def node_boxes(graph: PortfolioOntology) -> List[str]:
    boxes = {
        str((item.properties or {}).get("ontologyBox") or "ABox")
        for item in graph.entities
        if str((item.properties or {}).get("ontologyBox") or "ABox")
    }
    boxes.update(
        str((item.value or {}).get("ontologyBox") or ("InferenceBox" if item.kind == "inference-trace" else "ABox"))
        for item in graph.evidence
        if str((item.value or {}).get("ontologyBox") or ("InferenceBox" if item.kind == "inference-trace" else "ABox"))
    )
    boxes.update(
        "InferenceBox" if str(item.belief_id or "").startswith("belief:inference:") else "ABox"
        for item in graph.beliefs
    )
    boxes.update(
        str((item.properties or {}).get("ontologyBox") or "ABox")
        for item in graph.relations
        if str((item.properties or {}).get("ontologyBox") or "ABox")
    )
    if graph.opinions or getattr(graph, "reasoning_cards", None):
        boxes.add("ABox")
    return sorted(boxes)


def typeql_has(attribute: str, value: object, numeric: bool = False) -> str:
    if value in (None, "", [], {}):
        return ""
    if numeric:
        literal = typedb_number_literal(value)
        if not literal:
            return ""
        return ", has " + attribute + " " + literal
    return ", has " + attribute + " " + typedb_string(value)


def typeql_has_bool_string(attribute: str, value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        normalized = "true" if value else "false"
    else:
        normalized = str(value).strip().lower()
        if normalized not in {"true", "false", "1", "0", "yes", "no", "y", "n", "on", "off"}:
            return ""
        normalized = "true" if normalized in {"true", "1", "yes", "y", "on"} else "false"
    return ", has " + attribute + " " + typedb_string(normalized)


def promoted_node_value(row: Dict[str, object], properties: Dict[str, object], field: str):
    value = row.get(field)
    return properties.get(field) if value in (None, "") else value


def promoted_node_text_value(row: Dict[str, object], properties: Dict[str, object], field: str) -> str:
    value = promoted_node_value(row, properties, field)
    if isinstance(value, list):
        return ", ".join(list_of_strings(value))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value or "")


def list_of_strings(value: object) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if value in (None, ""):
        return []
    return [str(value)]


def typeql_limit_clause(limit: int) -> str:
    safe_limit = int(limit or 0)
    return " limit " + str(safe_limit) + ";" if safe_limit > 0 else ""


def typedb_abox_inference_generation_id(metadata: Dict[str, object]) -> str:
    """Return the single ABox identity that an InferenceBox must reference.

    Scoped ABox rows deliberately use a distinct immutable generation ID per
    scope.  Those IDs cannot be compared as though they were one portfolio
    generation.  The Worldview Manifest is the atomic active-world identity;
    legacy ABox storage continues to use its snapshot ID until migrated.
    """
    payload = dict(metadata or {})
    if str(payload.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION:
        return str(payload.get("worldviewManifestId") or payload.get("aboxSnapshotId") or "").strip()
    return str(payload.get("aboxSnapshotId") or payload.get("worldviewManifestId") or "").strip()


def typedb_concept_value(concept: object):
    if concept is None:
        return None
    get_value = getattr(concept, "get_value", None)
    if callable(get_value):
        return get_value()
    # Aggregate TypeQL results are Value concepts. They expose typed getters
    # rather than get_value(), so inspect them before falling back to the type
    # label (for example, "integer" instead of the actual count).
    for getter_name in [
        "get_boolean",
        "get_integer",
        "get_double",
        "get_decimal",
        "get_string",
        "get_datetime_tz",
        "get_datetime",
        "get_date",
        "get_duration",
    ]:
        getter = getattr(concept, getter_name, None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                continue
    value = getattr(concept, "value", None)
    if callable(value):
        return value()
    if value is not None:
        return value
    get_label = getattr(concept, "get_label", None)
    if callable(get_label):
        return str(get_label())
    return concept


def typedb_row_value(row: object, name: str):
    if row is None:
        return None
    getter = getattr(row, "get", None)
    if callable(getter):
        try:
            return typedb_concept_value(getter(name))
        except Exception:
            return None
    if isinstance(row, dict):
        return typedb_concept_value(row.get(name))
    return None


def merge_flat_properties(row: Dict[str, object], props: Dict[str, object]) -> Dict[str, object]:
    merged = dict(props or {})
    nested = merged.get("properties") if isinstance(merged.get("properties"), dict) else {}
    if nested:
        merged.update(nested)
    for key, value in row.items():
        if value not in (None, "", [], {}):
            merged.setdefault(key, value)
    return merged




def typedb_node_allowed_attributes(properties: Dict[str, object], kind: object = "") -> Optional[Set[str]]:
    """Return direct attributes for a semantic class; ``None`` means fallback."""
    class_name = primary_tbox_class(properties) or ""
    definition = tbox_class_def(class_name) if class_name else None
    if definition is None:
        # Generic compatibility rows are rare and do not have semantic
        # descendants.  Their physical fallback types retain every declared
        # attribute so old operational payloads remain writable.
        return None
    contract = typedb_rule_schema_capability_contract()
    context_attributes = dict(contract.get("contextAttributes") or {})
    return TYPEDB_COMMON_NODE_ATTRIBUTES | set(
        context_attributes.get(definition.bounded_context) or []
    )




class ScopedABoxManifestMixin:
    """Shared immutable scoped-ABox persistence contract.

    The concrete TypeDB repository supplies TypeQL I/O and row mapping.  The
    disabled repository inherits the conservative fallback methods below, and
    the concrete repository inherits the scoped persistence implementation.
    Keeping the lifecycle in one mixin prevents the two adapters from slowly
    diverging as the ABox contract evolves.
    """
    store_key = "typedb"
    store_label = "TypeDB"

    def active_tbox_metadata(self) -> Dict[str, object]:
        metadata = default_tbox_metadata()
        metadata.update({
            "configured": False,
            "status": "code-fallback",
            "source": "code",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
        })
        return metadata

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "aboxSnapshotId": "",
            "materialFingerprint": "",
        }

    def inferencebox_recovery_metadata(self, world_id: str = "") -> Dict[str, object]:
        """Return the small durable marker used by operational recovery.

        A projection-circuit health probe must not read every historical
        InferenceBox row merely to decide whether a new projection may start.
        The full snapshot remains the query contract for investment features;
        this lightweight marker is only a diagnostic for retry scheduling.
        """
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "reason": "TypeDB ontology storage is not configured.",
        }

    def inferencebox_commit_proof(
        self,
        inference_generation_id: str,
        source_abox_snapshot_id: str,
        target_symbols: List[str] = None,
        world_id: str = "",
    ) -> Dict[str, object]:
        """Return the minimum durable proof required before alert delivery.

        A full InferenceBox expansion is intentionally an asynchronous audit
        concern.  The realtime path still needs to prove that the just-written
        native generation is the active one for the active ABox, but that can
        be done with the small active generation marker and ABox pointer.
        Disabled adapters fail closed so callers retain their legacy full
        readback behaviour.
        """
        return {
            "configured": False,
            "status": "disabled",
            "verified": False,
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "inferenceGenerationId": str(inference_generation_id or ""),
            "sourceAboxSnapshotId": str(source_abox_snapshot_id or ""),
            "targetSymbols": clean_symbols_from_payload(target_symbols or []),
            "reason": "TypeDB ontology storage is not configured.",
        }

    def list_ontology_worlds(self) -> List[Dict[str, object]]:
        return []

    def active_abox_uses_scoped_manifest(self, world_id: str = "") -> bool:
        return _graph_reads_inventory.active_abox_uses_scoped_manifest(
            self,
            world_id,
        )

    def active_abox_members_clause(self, members: Iterable[Tuple[str, str]], world_id: str = "") -> str:
        return _graph_reads_inventory.active_abox_members_clause(
            self,
            members,
            world_id,
        )

    def scoped_abox_manifest_inventory(self, world_id: str = "") -> Dict[str, object]:
        return _graph_reads_inventory.scoped_abox_manifest_inventory(
            self,
            world_id,
        )

    def scoped_abox_integrity_audit(
        self,
        world_id: str = "",
        cursor: int = 0,
        limit: int = 20,
        scope_ids: Iterable[str] = None,
    ) -> Dict[str, object]:
        return _graph_reads_inventory.scoped_abox_integrity_audit(
            self,
            world_id,
            cursor,
            limit,
            scope_ids,
        )

    def scoped_abox_storage_diagnostics(self, world_id: str = "") -> Dict[str, object]:
        return _graph_reads_inventory.scoped_abox_storage_diagnostics(
            self,
            world_id,
        )

    def scoped_abox_write_lease_seconds(self, settings: Dict[str, object] = None) -> int:
        return _projection_lock_policy.scoped_abox_write_lease_seconds(
            self,
            settings,
            _bindings=_projection_lock_policy_ports.ProjectionLockPolicyRuntime(
                runtime_settings=runtime_settings
            ),
        )

    def typedb_projection_coordinator_enabled(self, settings: Dict[str, object] = None) -> bool:
        return _projection_lock_policy.typedb_projection_coordinator_enabled(
            self,
            settings,
            _bindings=_projection_lock_policy_ports.ProjectionLockPolicyRuntime(
                runtime_settings=runtime_settings
            ),
        )

    def typedb_projection_coordinator_lease_seconds(self, settings: Dict[str, object] = None) -> int:
        return _projection_lock_policy.typedb_projection_coordinator_lease_seconds(
            self,
            settings,
            _bindings=_projection_lock_policy_ports.ProjectionLockPolicyRuntime(
                runtime_settings=runtime_settings
            ),
        )

    def typedb_projection_coordinator_retry_seconds(self, settings: Dict[str, object] = None) -> int:
        return _projection_lock_policy.typedb_projection_coordinator_retry_seconds(
            self,
            settings,
            _bindings=_projection_lock_policy_ports.ProjectionLockPolicyRuntime(
                runtime_settings=runtime_settings
            ),
        )

    def scoped_abox_orphan_cleanup_max_generations(self, settings: Dict[str, object] = None) -> int:
        return _graph_maintenance_orphans.scoped_abox_orphan_cleanup_max_generations(
            self,
            settings,
            _bindings=_graph_maintenance_orphans_ports.GraphMaintenanceOrphansRuntime(
                runtime_settings=runtime_settings, typedb_error_code=typedb_error_code
            ),
        )

    @staticmethod
    def scoped_abox_write_lease_storage_id(world_id: str = "") -> str:
        return _projection_lock_policy.scoped_abox_write_lease_storage_id(
            world_id,
        )

    def scoped_abox_write_lease_rows(self, world_id: str = "") -> List[Dict[str, object]]:
        return _projection_lock_lease.scoped_abox_write_lease_rows(
            self,
            world_id,
        )

    def scoped_abox_write_lease_world_ids(self) -> List[str]:
        return _projection_lock_lease.scoped_abox_write_lease_world_ids(
            self,
        )

    def scoped_abox_write_lease_status(self, world_id: str = "") -> Dict[str, object]:
        return _projection_lock_lease.scoped_abox_write_lease_status(
            self,
            world_id,
        )

    def scoped_abox_write_lease_graph(
        self,
        owner: str,
        manifest_id: str = "",
        lease_seconds: int = 0,
        world_id: str = "",
    ) -> Tuple[PortfolioOntology, Dict[str, object]]:
        return _projection_lock_lease.scoped_abox_write_lease_graph(
            self,
            owner,
            manifest_id,
            lease_seconds,
            world_id,
        )

    def delete_scoped_abox_write_lease(
        self,
        driver,
        imported,
        lease: Dict[str, object],
    ) -> Dict[str, object]:
        return _projection_lock_lease.delete_scoped_abox_write_lease(
            self,
            driver,
            imported,
            lease,
            _bindings=_projection_lock_lease_ports.ProjectionLockLeaseRuntime(
                typedb_operation_timeout=typedb_operation_timeout
            ),
        )

    def acquire_scoped_abox_write_lease(
        self,
        manifest_id: str = "",
        world_id: str = "",
        lease_seconds: int = 0,
    ) -> Dict[str, object]:
        return _projection_lock_lease.acquire_scoped_abox_write_lease(
            self,
            manifest_id,
            world_id,
            lease_seconds,
        )

    def projection_coordinator_lease_status(self) -> Dict[str, object]:
        return _projection_lock_coordinator.projection_coordinator_lease_status(
            self,
        )

    def recover_dead_projection_coordinator_lease(self) -> Dict[str, object]:
        return _projection_lock_coordinator.recover_dead_projection_coordinator_lease(
            self,
        )

    def projection_coordinator_write_enforced(self) -> bool:
        return _projection_lock_coordinator.projection_coordinator_write_enforced(
            self,
        )

    def active_projection_coordinator_lease(self) -> Dict[str, object]:
        return _projection_lock_coordinator.active_projection_coordinator_lease(
            self,
        )

    def projection_coordinator_token_is_active(self, token: str) -> bool:
        return _projection_lock_coordinator.projection_coordinator_token_is_active(
            self,
            token,
        )

    def track_projection_coordinator_lease(self, lease: Dict[str, object]) -> None:
        return _projection_lock_coordinator.track_projection_coordinator_lease(
            self,
            lease,
        )

    def forget_projection_coordinator_lease(self, lease: Dict[str, object]) -> None:
        return _projection_lock_coordinator.forget_projection_coordinator_lease(
            self,
            lease,
        )

    @contextmanager
    def projection_coordinator_write_scope(self, owner: str, world_id: str = ""):
        yield from _projection_lock_coordinator.projection_coordinator_write_scope(
            self,
            owner,
            world_id,
        )

    def acquire_projection_coordinator_lease(
        self,
        owner: str,
        world_id: str = "",
    ) -> Dict[str, object]:
        return _projection_lock_coordinator.acquire_projection_coordinator_lease(
            self,
            owner,
            world_id,
        )

    def _acquire_projection_coordinator_lease(
        self,
        owner: str,
        world_id: str = "",
        allow_adopt: bool = False,
    ) -> Dict[str, object]:
        return _projection_lock_coordinator._acquire_projection_coordinator_lease(
            self,
            owner,
            world_id,
            allow_adopt,
        )

    def release_projection_coordinator_lease(self, lease: Dict[str, object]) -> Dict[str, object]:
        return _projection_lock_coordinator.release_projection_coordinator_lease(
            self,
            lease,
        )

    def _release_projection_coordinator_lease(self, lease: Dict[str, object]) -> Dict[str, object]:
        return _projection_lock_coordinator._release_projection_coordinator_lease(
            self,
            lease,
        )

    def release_scoped_abox_write_lease(self, lease: Dict[str, object]) -> Dict[str, object]:
        return _projection_lock_lease.release_scoped_abox_write_lease(
            self,
            lease,
        )

    @staticmethod
    def local_process_alive(process_id: object) -> bool:
        return _projection_lock_policy.local_process_alive(
            process_id,
        )

    def recover_dead_local_scoped_abox_write_lease(
        self,
        world_id: str = "",
        recover_untracked_current_process: bool = False,
    ) -> Dict[str, object]:
        return _projection_lock_recovery.recover_dead_local_scoped_abox_write_lease(
            self,
            world_id,
            recover_untracked_current_process,
        )

    def recover_all_dead_local_scoped_abox_write_leases(self) -> Dict[str, object]:
        return _projection_lock_recovery.recover_all_dead_local_scoped_abox_write_leases(
            self,
        )

    def recover_scoped_abox_write_lease_after_server_start_for_world(self, world_id: str = "") -> Dict[str, object]:
        return (
            _projection_lock_recovery.recover_scoped_abox_write_lease_after_server_start_for_world(
                self,
                world_id,
            )
        )

    def recover_all_scoped_abox_write_leases_after_server_start(self) -> Dict[str, object]:
        return _projection_lock_recovery.recover_all_scoped_abox_write_leases_after_server_start(
            self,
        )

    def recover_scoped_abox_write_lease_after_server_start(self) -> Dict[str, object]:
        return _projection_lock_recovery.recover_scoped_abox_write_lease_after_server_start(
            self,
        )

    def recover_scoped_abox_write_lease_after_managed_shutdown(self) -> Dict[str, object]:
        return _projection_lock_recovery.recover_scoped_abox_write_lease_after_managed_shutdown(
            self,
        )

    def recover_pending_abox_activation(
        self,
        world_id: str = "",
        max_staged_target_symbols: int = 0,
    ) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
        }

    def discard_abox_generation(self, snapshot_id: str) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "aboxSnapshotId": str(snapshot_id or ""),
            "reason": "TypeDB ontology storage is not configured.",
        }

    def prune_inactive_abox_generations(
        self,
        _driver=None,
        _imported=None,
        active_snapshot_id: str = "",
        keep_inactive_count: int = 0,
        max_generations: int = 1,
    ) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "activeAboxSnapshotId": str(active_snapshot_id or ""),
            "retainedInactiveSnapshotIds": [],
            "removedCandidateSnapshotIds": [],
            "deletedBatchCount": 0,
        }

    is_scoped_abox_graph = staticmethod(_abox_candidate_scope_plan.is_scoped_abox_graph)

    is_current_state_scoped_abox_graph = staticmethod(_abox_candidate_scope_plan.is_current_state_scoped_abox_graph)

    scoped_abox_plan = staticmethod(_abox_candidate_scope_plan.scoped_abox_plan)

    current_state_physical_scope_plan = staticmethod(_abox_candidate_scope_plan.current_state_physical_scope_plan)

    scoped_abox_semantic_changed_scope_ids = staticmethod(_abox_candidate_selection.scoped_abox_semantic_changed_scope_ids)

    scoped_abox_changed_scope_ids = staticmethod(_abox_candidate_selection.scoped_abox_changed_scope_ids)

    scoped_abox_rebind_only_relation_scope_ids = staticmethod(_abox_candidate_selection.scoped_abox_rebind_only_relation_scope_ids)

    current_state_physical_graph = staticmethod(_abox_candidate_row_image.current_state_physical_graph)

    def delete_current_state_slot_rows(
        self,
        driver,
        imported,
        physical_generation_ids: Iterable[str],
    ) -> Dict[str, object]:
        return _graph_maintenance_current_state.delete_current_state_slot_rows(
            self,
            driver,
            imported,
            physical_generation_ids,
            _bindings=_graph_maintenance_current_state_ports.GraphMaintenanceCurrentStateRuntime(
                runtime_settings=runtime_settings, typedb_operation_timeout=typedb_operation_timeout
            ),
        )

    def current_state_slot_inventory(
        self,
        driver,
        imported,
        physical_generation_ids: Iterable[str],
    ) -> Dict[str, Dict[str, Dict[str, object]]]:
        return _graph_reads_inventory.current_state_slot_inventory(
            self,
            driver,
            imported,
            physical_generation_ids,
        )

    @staticmethod
    def current_state_inventory_batch_size(settings: Dict[str, object] = None) -> int:
        configured = runtime_settings() if settings is None else dict(settings or {})
        parsed = number_or_none(
            configured.get("typedbABoxCurrentStateInventoryBatchSize")
        )
        return max(16, min(64, int(parsed or 32)))

    def current_state_storage_inventory(
        self,
        driver,
        imported,
        node_storage_ids: Iterable[str] = None,
        relation_storage_ids: Iterable[str] = None,
    ) -> Dict[str, Dict[str, Dict[str, object]]]:
        return _graph_reads_inventory.current_state_storage_inventory(
            self,
            driver,
            imported,
            node_storage_ids,
            relation_storage_ids,
        )

    @staticmethod
    def current_state_delta_plan(
        node_rows: Iterable[Dict[str, object]],
        relation_rows: Iterable[Dict[str, object]],
        inventory: Dict[str, Dict[str, Dict[str, object]]],
    ) -> Dict[str, object]:
        return _manifest_counts.current_state_delta_plan(
            node_rows,
            relation_rows,
            inventory,
        )

    def delete_current_state_storage_ids(
        self,
        driver,
        imported,
        node_storage_ids: Iterable[str],
        relation_storage_ids: Iterable[str],
    ) -> Dict[str, object]:
        return _graph_maintenance_current_state.delete_current_state_storage_ids(
            self,
            driver,
            imported,
            node_storage_ids,
            relation_storage_ids,
            _bindings=_graph_maintenance_current_state_ports.GraphMaintenanceCurrentStateRuntime(
                runtime_settings=runtime_settings, typedb_operation_timeout=typedb_operation_timeout
            ),
        )

    def scoped_abox_persistence_rows(
        self,
        graph: PortfolioOntology,
        scope_ids: Iterable[str],
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
        return _abox_candidate_row_image.scoped_abox_persistence_rows(
            self, graph, scope_ids,
        )

    def read_active_scoped_abox_rows(
        self,
        active_metadata: Dict[str, object],
        scope_ids: Iterable[str],
        world_id: str = "",
    ) -> Dict[str, object]:
        return _graph_reads_inventory.read_active_scoped_abox_rows(
            self,
            active_metadata,
            scope_ids,
            world_id,
            _bindings=_graph_reads_inventory_ports.GraphReadsInventoryRuntime(
                endpoint_node_row=endpoint_node_row
            ),
        )

    scoped_abox_active_reuse_scope_ids = staticmethod(_abox_candidate_selection.scoped_abox_active_reuse_scope_ids)

    scoped_abox_native_index_reuse_scope_ids = staticmethod(_abox_candidate_selection.scoped_abox_native_index_reuse_scope_ids)

    scoped_abox_candidate_persistence_rows = staticmethod(_abox_candidate_rows.scoped_abox_candidate_persistence_rows)

    def scoped_abox_manifest_generation_references(self, world_id: str = "") -> Dict[str, object]:
        return _graph_reads_inventory.scoped_abox_manifest_generation_references(
            self,
            world_id,
        )

    def scoped_abox_orphan_candidate_inventory(self, world_id: str = "") -> Dict[str, object]:
        return _graph_reads_inventory.scoped_abox_orphan_candidate_inventory(
            self,
            world_id,
        )

    def cleanup_orphan_scoped_abox_candidates(
        self,
        driver,
        imported,
        max_generation_count: int = 0,
        world_id: str = "",
    ) -> Dict[str, object]:
        return _graph_maintenance_orphans.cleanup_orphan_scoped_abox_candidates(
            self,
            driver,
            imported,
            max_generation_count,
            world_id,
        )

    def prune_orphan_scoped_abox_candidates(
        self,
        world_id: str = "",
        max_generation_count: int = 0,
    ) -> Dict[str, object]:
        return _graph_maintenance_orphans.prune_orphan_scoped_abox_candidates(
            self,
            world_id,
            max_generation_count,
            _bindings=_graph_maintenance_orphans_ports.GraphMaintenanceOrphansRuntime(
                runtime_settings=runtime_settings, typedb_error_code=typedb_error_code
            ),
        )

    def scoped_abox_scope_row_counts(self, scope_id: str, generation_id: str) -> Dict[str, int]:
        return _graph_reads_inventory.scoped_abox_scope_row_counts(
            self,
            scope_id,
            generation_id,
        )

    def scoped_abox_scope_row_counts_batch(
        self,
        scope_rows: Iterable[Dict[str, object]],
        manifest_id: str = "",
        world_id: str = "",
    ) -> Dict[str, Dict[str, int]]:
        return _graph_reads_inventory.scoped_abox_scope_row_counts_batch(
            self,
            scope_rows,
            manifest_id,
            world_id,
        )

    scoped_abox_storage_identity = staticmethod(_abox_candidate_validation.scoped_abox_storage_identity)

    def scoped_abox_storage_rows_by_id(
        self,
        node_storage_ids: Iterable[str],
        relation_storage_ids: Iterable[str],
    ) -> Dict[str, Dict[str, Dict[str, object]]]:
        return _graph_reads_inventory.scoped_abox_storage_rows_by_id(
            self,
            node_storage_ids,
            relation_storage_ids,
        )

    scoped_abox_storage_rows_unique = staticmethod(_abox_candidate_validation.scoped_abox_storage_rows_unique)

    @staticmethod
    def scoped_abox_counts_by_scope(
        node_rows: Iterable[Dict[str, object]],
        relation_rows: Iterable[Dict[str, object]],
    ) -> Dict[str, Dict[str, int]]:
        return _manifest_counts.scoped_abox_counts_by_scope(
            node_rows,
            relation_rows,
        )

    @staticmethod
    def scoped_abox_relation_breakdown(
        relation_rows: Iterable[Dict[str, object]],
        bucket_limit: int = 24,
    ) -> Dict[str, object]:
        return _manifest_counts.scoped_abox_relation_breakdown(
            relation_rows,
            bucket_limit,
        )

    @staticmethod
    def scoped_abox_relation_persistence_summary(write_plan: Dict[str, object]) -> Dict[str, object]:
        return _manifest_counts.scoped_abox_relation_persistence_summary(
            write_plan,
        )

    @staticmethod
    def merged_scoped_abox_counts(
        *count_sets: Dict[str, Dict[str, int]],
    ) -> Dict[str, Dict[str, int]]:
        return _manifest_counts.merged_scoped_abox_counts(
            *count_sets,
        )

    def scoped_abox_storage_reuse_plan(
        self,
        node_rows: Iterable[Dict[str, object]],
        relation_rows: Iterable[Dict[str, object]],
        assume_missing_storage: bool = False,
    ) -> Dict[str, object]:
        return _abox_candidate_validation.scoped_abox_storage_reuse_plan(
            self, node_rows, relation_rows, assume_missing_storage,
        )

    def scoped_abox_storage_identity_counts(
        self,
        node_rows: Iterable[Dict[str, object]],
        relation_rows: Iterable[Dict[str, object]],
    ) -> Dict[str, object]:
        return _abox_candidate_validation.scoped_abox_storage_identity_counts(
            self, node_rows, relation_rows,
        )

    missing_relation_endpoint_storage_ids = staticmethod(_abox_candidate_validation.missing_relation_endpoint_storage_ids)

    missing_relation_endpoint_diagnostics = staticmethod(_abox_candidate_validation.missing_relation_endpoint_diagnostics)

    def _abox_persistence_runtime(self) -> ABoxRuntime:
        return ABoxRuntime(
            settings=runtime_settings,
            now=utc_now,
            timeout=typedb_operation_timeout,
            error_code=typedb_error_code,
        )

    def write_persistence_rows(
        self,
        driver,
        imported,
        node_rows: Iterable[Dict[str, object]],
        relation_rows: Iterable[Dict[str, object]],
        telemetry: Dict[str, object] = None,
        assume_missing_storage: bool = False,
    ) -> Dict[str, object]:
        return _abox_writer.write_persistence_rows(
            self, driver, imported, node_rows, relation_rows, telemetry,
            assume_missing_storage, runtime=self._abox_persistence_runtime(),
        )

    def scoped_manifest_marker_graph(
        self,
        graph: PortfolioOntology,
        scope_plan: List[Dict[str, object]],
        changed_scope_ids: Iterable[str],
    ) -> PortfolioOntology:
        return _manifest_graphs.scoped_manifest_marker_graph(
            self,
            graph,
            scope_plan,
            changed_scope_ids,
            _bindings=_manifest_graphs_ports.ManifestGraphsRuntime(utc_now=utc_now),
        )

    def prepare_scoped_manifest_native_rule_indexes(
        self,
        graph: PortfolioOntology,
        active_metadata: Dict[str, object] = None,
        persistence_rows: Tuple[
            Iterable[Dict[str, object]],
            Iterable[Dict[str, object]],
        ] = None,
    ) -> Dict[str, object]:
        return _manifest_indexes.prepare_scoped_manifest_native_rule_indexes(
            self,
            graph,
            active_metadata,
            persistence_rows,
        )

    def replace_scoped_manifest_marker_graph(
        self,
        marker_graph: PortfolioOntology,
    ) -> Dict[str, object]:
        return _manifest_repair.replace_scoped_manifest_marker_graph(
            self,
            marker_graph,
            _bindings=_manifest_repair_ports.ManifestRepairRuntime(
                typedb_error_code=typedb_error_code,
                typedb_operation_timeout=typedb_operation_timeout,
                typedb_projection_coordinator_summary=typedb_projection_coordinator_summary,
                utc_now=utc_now,
            ),
        )

    def refresh_market_world_observation_metadata(
        self,
        manifest_id: str,
        scope_plan: List[Dict[str, object]],
        market_scope_observed_at: Dict[str, object],
        world_id: str = "",
        adopted_write_lease: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _manifest_observation.refresh_market_world_observation_metadata(
            self,
            manifest_id,
            scope_plan,
            market_scope_observed_at,
            world_id,
            adopted_write_lease,
        )

    def repair_active_manifest_native_rule_evidence_index(
        self,
        active_metadata: Dict[str, object] = None,
        world_id: str = "",
        expected_manifest_id: str = "",
        stable_write_lease_held: bool = False,
    ) -> Dict[str, object]:
        return _manifest_repair.repair_active_manifest_native_rule_evidence_index(
            self,
            active_metadata,
            world_id,
            expected_manifest_id,
            stable_write_lease_held,
            _bindings=_manifest_repair_ports.ManifestRepairRuntime(
                typedb_error_code=typedb_error_code,
                typedb_operation_timeout=typedb_operation_timeout,
                typedb_projection_coordinator_summary=typedb_projection_coordinator_summary,
                utc_now=utc_now,
            ),
        )

    @coordinated_typedb_projection_write(
        "manifest-evidence-index",
        typedb_projection_world_from_manifest_index,
    )
    def ensure_scoped_manifest_evidence_read_index(
        self,
        graph: PortfolioOntology,
        active_metadata: Dict[str, object] = None,
        world_id: str = "",
    ) -> Dict[str, object]:
        return _manifest_repair.ensure_scoped_manifest_evidence_read_index(
            self,
            graph,
            active_metadata,
            world_id,
        )

    def scoped_manifest_pointer_graph(
        self,
        graph: PortfolioOntology,
        scope_plan: List[Dict[str, object]],
        previous_metadata: Dict[str, object] = None,
        pending_activation: bool = True,
        inference_target_symbols: Iterable[str] = None,
        scope_ids: Iterable[str] = None,
    ) -> PortfolioOntology:
        return _manifest_graphs.scoped_manifest_pointer_graph(
            self,
            graph,
            scope_plan,
            previous_metadata,
            pending_activation,
            inference_target_symbols,
            scope_ids,
            _bindings=_manifest_graphs_ports.ManifestGraphsRuntime(utc_now=utc_now),
        )

    def scoped_manifest_pending_graph(
        self,
        graph: PortfolioOntology,
        scope_plan: List[Dict[str, object]],
        previous_metadata: Dict[str, object] = None,
        inference_target_symbols: Iterable[str] = None,
    ) -> PortfolioOntology:
        return _manifest_graphs.scoped_manifest_pending_graph(
            self,
            graph,
            scope_plan,
            previous_metadata,
            inference_target_symbols,
        )

    @coordinated_typedb_projection_write(
        "scoped-abox-save",
        typedb_projection_world_from_graph,
    )
    def save_scoped_abox_graph(
        self,
        graph: PortfolioOntology,
        boxes: Iterable[str] = None,
        adopted_write_lease: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _manifest_save.save_scoped_abox_graph(
            self,
            graph,
            boxes,
            adopted_write_lease,
            _bindings=_manifest_save_ports.ManifestSaveRuntime(
                typedb_error_code=typedb_error_code, utc_now=utc_now
            ),
        )

    def scoped_manifest_metadata(self, manifest_id: str, world_id: str = "") -> Dict[str, object]:
        return _graph_reads_inventory.scoped_manifest_metadata(
            self,
            manifest_id,
            world_id,
        )

    def scoped_manifest_control_graph(
        self,
        metadata: Dict[str, object],
        previous_metadata: Dict[str, object] = None,
        pending_activation: bool = False,
        inference_target_symbols: Iterable[str] = None,
        scope_ids: Iterable[str] = None,
    ) -> PortfolioOntology:
        return _manifest_graphs.scoped_manifest_control_graph(
            self,
            metadata,
            previous_metadata,
            pending_activation,
            inference_target_symbols,
            scope_ids,
            _bindings=_manifest_graphs_ports.ManifestGraphsRuntime(utc_now=utc_now),
        )

    @staticmethod
    def scoped_manifest_control_delta(
        metadata: Dict[str, object],
        previous_metadata: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _manifest_graphs.scoped_manifest_control_delta(
            metadata,
            previous_metadata,
        )

    def activate_scoped_abox_manifest(
        self,
        manifest_id: str,
        previous_metadata: Dict[str, object] = None,
        pending_activation: bool = False,
        inference_target_symbols: Iterable[str] = None,
        world_id: str = "",
    ) -> Dict[str, object]:
        return _abox_lifecycle.activate_scoped_abox_manifest(
            self,
            manifest_id,
            previous_metadata,
            pending_activation,
            inference_target_symbols,
            world_id,
            runtime=self._abox_persistence_runtime(),
        )

    def prepare_pending_abox_activation_for_inference(
        self, world_id: str = ""
    ) -> Dict[str, object]:
        return _abox_lifecycle.prepare_pending_abox_activation_for_inference(self, world_id)

    def finalize_scoped_abox_manifest(
        self,
        active_manifest_id: str,
        previous_manifest_id: str = "",
        world_id: str = "",
    ) -> Dict[str, object]:
        return _abox_lifecycle.finalize_scoped_abox_manifest(
            self,
            active_manifest_id,
            previous_manifest_id,
            world_id,
        )

    def discard_scoped_abox_manifest_in_driver(
        self,
        driver,
        imported,
        manifest_id: str,
        protected_generation_ids: Iterable[str] = None,
        world_id: str = "",
        max_delete_batches: int = None,
        delete_batch_size: int = None,
    ) -> Dict[str, object]:
        return _graph_maintenance_manifests.discard_scoped_abox_manifest_in_driver(
            self,
            driver,
            imported,
            manifest_id,
            protected_generation_ids,
            world_id,
            max_delete_batches,
            delete_batch_size,
        )

    def discard_scoped_abox_manifest(
        self, manifest_id: str, world_id: str = ""
    ) -> Dict[str, object]:
        return _graph_maintenance_manifests.discard_scoped_abox_manifest(
            self,
            manifest_id,
            world_id,
            _bindings=_graph_maintenance_manifests_ports.GraphMaintenanceManifestsRuntime(
                typedb_error_code=typedb_error_code,
                typedb_operation_timeout=typedb_operation_timeout,
            ),
        )

    def delete_worldview_manifest_markers_batch(
        self,
        driver,
        imported,
        manifest_ids: Iterable[str],
        world_id: str = "",
    ) -> Dict[str, object]:
        return _graph_maintenance_manifests.delete_worldview_manifest_markers_batch(
            self,
            driver,
            imported,
            manifest_ids,
            world_id,
            _bindings=_graph_maintenance_manifests_ports.GraphMaintenanceManifestsRuntime(
                typedb_error_code=typedb_error_code,
                typedb_operation_timeout=typedb_operation_timeout,
            ),
        )

    def prune_inactive_scoped_abox_manifests_in_driver(
        self,
        driver,
        imported,
        active_manifest_id: str = "",
        keep_inactive_count: int = None,
        max_manifests: int = None,
        max_delete_batches: int = None,
        delete_batch_size: int = None,
        world_id: str = "",
        max_duration_seconds: int = None,
    ) -> Dict[str, object]:
        return _graph_maintenance_manifests.prune_inactive_scoped_abox_manifests_in_driver(
            self,
            driver,
            imported,
            active_manifest_id,
            keep_inactive_count,
            max_manifests,
            max_delete_batches,
            delete_batch_size,
            world_id,
            max_duration_seconds,
        )

    def prune_inactive_scoped_abox_manifests(
        self,
        world_id: str = "",
        keep_inactive_count: int = None,
        max_manifests: int = None,
        max_delete_batches: int = None,
        delete_batch_size: int = None,
        max_duration_seconds: int = None,
    ) -> Dict[str, object]:
        return _graph_maintenance_manifests.prune_inactive_scoped_abox_manifests(
            self,
            world_id,
            keep_inactive_count,
            max_manifests,
            max_delete_batches,
            delete_batch_size,
            max_duration_seconds,
            _bindings=_graph_maintenance_manifests_ports.GraphMaintenanceManifestsRuntime(
                typedb_error_code=typedb_error_code,
                typedb_operation_timeout=typedb_operation_timeout,
            ),
        )

    @coordinated_typedb_projection_write(
        "deferred-maintenance",
        typedb_projection_world_from_payload,
    )
    def run_deferred_maintenance(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        return _graph_maintenance_runner.run_deferred_maintenance(
            self,
            payload,
            _bindings=_graph_maintenance_runner_ports.GraphMaintenanceRunnerRuntime(
                typedb_error_code=typedb_error_code
            ),
        )

    def save_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
        }

    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        graph = ontology_seed_graph(
            language_registry=investment_language_registry(runtime_settings())
        )
        result = self.save_graph(graph)
        result.update(
            {
                "seeded": False,
                "engineVersion": GRAPH_REASONER_VERSION,
                "ruleCount": len(default_graph_inference_rules()),
            }
        )
        return result

    def rulebox_snapshot(self) -> Dict[str, object]:
        rules = rulebox_rules_to_payload(default_graph_inference_rules())
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "source": "typedb-defaults",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "engineVersion": GRAPH_REASONER_VERSION,
            "rules": rules,
            "ruleCount": len(rules),
            "conditionCount": sum(len(item.get("conditions") or []) for item in rules),
            "derivationCount": sum(len(item.get("derivations") or []) for item in rules),
            "relationTypes": sorted(
                {
                    str(derivation.get("relation_type") or derivation.get("relationType") or "")
                    for rule in rules
                    for derivation in (rule.get("derivations") or [])
                    if isinstance(derivation, dict)
                }
            ),
            "defaultsFallbackUsed": True,
            "versions": [],
            "versionCount": 0,
            "changeCandidates": rulebox_governance_candidates(rules, []),
            "nativeReasoningProfile": typedb_native_reasoning_profile(rules),
        }

    def save_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        snapshot = self.rulebox_snapshot()
        snapshot.update({"saved": False, "status": "disabled"})
        return snapshot

    def restore_rulebox_version(
        self,
        version_id: str,
        change_reason: str = "",
        author: str = "",
    ) -> Dict[str, object]:
        snapshot = self.rulebox_snapshot()
        snapshot.update(
            {
                "saved": False,
                "status": "disabled",
                "reason": "TypeDB ontology storage is not configured.",
                "restoredVersionId": str(version_id or ""),
            }
        )
        return snapshot

    def ensure_rulebox_version_baseline(self, author: str = "") -> Dict[str, object]:
        snapshot = self.rulebox_snapshot()
        snapshot.update(
            {
                "saved": False,
                "status": "disabled",
                "reason": "TypeDB ontology storage is not configured.",
            }
        )
        return snapshot

    def run_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "statementCount": 0,
        }

    def profile_native_rule_reads(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        """Return a disabled read-only proof result for the null adapter."""
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "readOnly": True,
            "mutatedOperationalState": False,
            "writeMethodsInvoked": [],
            "samples": [],
            "reason": "TypeDB ontology storage is not configured.",
        }

    def validate_rulebox_materialization(
        self, payload: Dict[str, object] = None
    ) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "validationOnly": True,
            "mutatedOperationalRuleBox": False,
            "wroteInferenceBox": False,
            "candidateRuleCount": 0,
            "baselineInferenceBox": self.inferencebox_snapshot(),
            "diff": materialization_preview_diff_payload({}, 0, 0, False),
        }

    def inferencebox_snapshot(
        self,
        symbols: List[str] = None,
        limit: int = 80,
        reset_metrics: bool = True,
        world_id: str = "",
        inference_generation_id: str = "",
        source_abox_snapshot_id: str = "",
    ) -> Dict[str, object]:
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "source": "typedbInferenceBox",
            "graphStore": "typedb",
            "reasoningMode": "disabled",
            "reason": "TypeDB ontology storage is not configured.",
            "symbols": list(symbols or []),
            "inferenceGenerationId": str(inference_generation_id or ""),
            "sourceAboxSnapshotId": str(source_abox_snapshot_id or ""),
            "entities": [],
            "relations": [],
            "traces": [],
            "entityCount": 0,
            "relationCount": 0,
            "traceCount": 0,
            "nativeEntityCount": 0,
            "nativeRelationCount": 0,
            "nativeTraceCount": 0,
            "nativeTypeDbReasoningUsed": False,
            "typedbBootstrapReasoningUsed": False,
            "hypothesisCalibration": {
                "status": "unavailable",
                "source": "typedb-abox-hypothesis-calibration",
                "reason": "TypeDB ontology storage is not configured.",
                "calibrations": [],
                "calibrationCount": 0,
                "generationAligned": False,
                "automaticDeployment": False,
                "decisionEligibility": "historical-review-only",
            },
        }

    def save_rule_change_candidates(
        self, candidates: List[Dict[str, object]], context: Dict[str, object] = None
    ) -> Dict[str, object]:
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "candidateCount": len(list(candidates or [])),
            "savedCount": 0,
        }


class NullTypeDBOntologyGraphRepository(ScopedABoxManifestMixin):
    """Disabled TypeDB adapter preserving the graph-store interface."""


class TypeDBOntologyGraphRepository(GraphStoreOntologyRowMapperMixin, ScopedABoxManifestMixin):
    store_key = "typedb"
    store_label = "TypeDB"
    # ``build_monitor_runner`` is intentionally short-lived so account changes
    # are picked up by the reasoning worker.  A fresh repository instance must
    # not therefore read the same immutable base schema every cycle.  Keep the
    # cache process-local: a service restart still verifies the schema before
    # accepting live ABox writes.
    _process_base_schema_ready: Dict[Tuple[str, str, bool, str], float] = {}
    _process_base_schema_ready_lock = threading.Lock()
    _process_base_schema_cache_seconds = 300.0

    def __init__(
        self,
        address: str,
        user: str = "admin",
        password: str = "password",
        database: str = "orbit_alpha_ontology",
        tls_enabled: bool = False,
        timeout_seconds: int = 20,
        retry_count: int = 2,
        inference_generation_keep_count: int = 2,
        query_timeout_seconds: float = None,
        schema_operation_timeout_seconds: float = None,
        write_operation_timeout_seconds: float = None,
        condition_detail_queries_enabled: bool = False,
        query_metrics_enabled: bool = True,
        rulebox_snapshot_cache_seconds: float = 60.0,
        native_rule_execution_enabled: bool = True,
        native_rule_query_timeout_seconds: float = DEFAULT_TYPEDB_NATIVE_RULE_QUERY_TIMEOUT_SECONDS,
        native_rule_dedicated_read_driver_enabled: bool = True,
        native_rule_execution_budget_seconds: float = DEFAULT_TYPEDB_NATIVE_RULE_EXECUTION_BUDGET_SECONDS,
        native_rule_parallelism: int = DEFAULT_TYPEDB_NATIVE_RULE_PARALLELISM,
        native_rule_target_parallelism: int = DEFAULT_TYPEDB_NATIVE_RULE_TARGET_PARALLELISM,
        native_rule_subject_fanout_enabled: bool = False,
        native_rule_subject_parallelism: int = 2,
        native_rule_total_read_parallelism: int = 4,
        native_rule_target_work_sharding_enabled: bool = False,
        native_rule_adaptive_target_sharding_enabled: bool = True,
        native_rule_any_condition_parallelism: int = 1,
        native_rule_durable_preflight_fallback_enabled: bool = False,
        inference_write_lease_enabled: bool = False,
        projection_coordinator_write_enforced: bool = False,
        persistent_driver_enabled: bool = False,
        fresh_candidate_rebuild: bool = False,
        fresh_schema_bootstrap_batch_size: int = DEFAULT_TYPEDB_FRESH_SCHEMA_BOOTSTRAP_BATCH_SIZE,
        fresh_schema_bootstrap_timeout_seconds: float = DEFAULT_TYPEDB_FRESH_SCHEMA_BOOTSTRAP_TIMEOUT_SECONDS,
        http_address: str = "",
    ):
        self.address = str(address or "").strip()
        self.http_address = str(http_address or "").strip().rstrip("/")
        self.user = str(user or "admin").strip() or "admin"
        self.password = str(password or "password")
        self.database = str(database or "orbit_alpha_ontology").strip() or "orbit_alpha_ontology"
        self.tls_enabled = bool(tls_enabled)
        self.timeout_seconds = max(2, int(timeout_seconds or 20))
        self.retry_count = max(0, int(retry_count or 0))
        self.inference_generation_keep_count = max(1, int(inference_generation_keep_count or 2))
        self._query_timeout_seconds = max(
            1.0,
            float(
                query_timeout_seconds
                if query_timeout_seconds is not None
                else float(self.timeout_seconds or 20)
            ),
        )
        self._schema_operation_timeout_seconds = max(
            1.0,
            float(
                schema_operation_timeout_seconds
                if schema_operation_timeout_seconds is not None
                else float(self.timeout_seconds or 20)
            ),
        )
        self._write_operation_timeout_seconds = max(
            1.0,
            float(
                write_operation_timeout_seconds
                if write_operation_timeout_seconds is not None
                else float(self.timeout_seconds or 20)
            ),
        )
        self._condition_detail_queries_enabled = bool(condition_detail_queries_enabled)
        self._query_metrics_enabled = bool(query_metrics_enabled)
        self._rulebox_snapshot_cache_seconds = max(
            1.0, float(rulebox_snapshot_cache_seconds or 60.0)
        )
        self._native_rule_execution_enabled = bool(native_rule_execution_enabled)
        self._native_rule_query_timeout_seconds = max(
            0.5,
            float(
                native_rule_query_timeout_seconds
                or DEFAULT_TYPEDB_NATIVE_RULE_QUERY_TIMEOUT_SECONDS
            ),
        )
        self._native_rule_dedicated_read_driver_enabled = bool(
            native_rule_dedicated_read_driver_enabled
        )
        self._native_rule_execution_budget_seconds = max(
            1.0,
            float(
                native_rule_execution_budget_seconds
                or DEFAULT_TYPEDB_NATIVE_RULE_EXECUTION_BUDGET_SECONDS
            ),
        )
        self._native_rule_parallelism = max(
            1,
            min(
                8,
                int(
                    number_or_none(native_rule_parallelism)
                    or DEFAULT_TYPEDB_NATIVE_RULE_PARALLELISM
                ),
            ),
        )
        self._native_rule_target_parallelism = max(
            1,
            min(
                self._native_rule_parallelism,
                int(
                    number_or_none(native_rule_target_parallelism)
                    or DEFAULT_TYPEDB_NATIVE_RULE_TARGET_PARALLELISM
                ),
            ),
        )
        self._native_rule_subject_fanout_enabled = bool(native_rule_subject_fanout_enabled)
        self._native_rule_subject_parallelism = max(
            1,
            min(2, int(number_or_none(native_rule_subject_parallelism) or 2)),
        )
        self._native_rule_total_read_parallelism = max(
            1,
            min(16, int(number_or_none(native_rule_total_read_parallelism) or 4)),
        )
        # A native rule takes the complete candidate-symbol set in one query.
        # Target splitting multiplies those queries while the existing rule
        # parallelism already supplies bounded concurrency. Keep splitting as
        # an explicit capacity-test opt-in, never the live backlog default.
        self._native_rule_target_work_sharding_enabled = bool(
            native_rule_target_work_sharding_enabled
        )
        # Historical timing can request a small, per-rule target split. It is
        # deliberately independent of the broad capacity-test switch above:
        # only recent timeout-prone rules are eligible and their work runs in
        # a serial recovery phase under the same ABox lease.
        self._native_rule_adaptive_target_sharding_enabled = bool(
            native_rule_adaptive_target_sharding_enabled
        )
        self._native_rule_any_condition_parallelism = max(
            1,
            min(
                self._native_rule_parallelism,
                int(number_or_none(native_rule_any_condition_parallelism) or 1),
            ),
        )
        self._native_rule_durable_preflight_fallback_enabled = bool(
            native_rule_durable_preflight_fallback_enabled
        )
        # The production composition root enables this durable lease. Bare
        # adapters retain the old unlocked behavior for isolated migrations
        # and deterministic unit tests that do not open a real TypeDB driver.
        self._inference_write_lease_enabled = bool(inference_write_lease_enabled)
        self._last_graph = None
        self._last_rules: List[GraphInferenceRule] = []
        self._base_schema_ready_fingerprint = ""
        self._base_schema_type_names: set = set()
        self._last_base_schema_sync: Dict[str, object] = {}
        self._rulebox_snapshot_cache_at = 0.0
        self._rulebox_snapshot_cache_full_load_at = 0.0
        self._rulebox_snapshot_cache_result: Dict[str, object] = {}
        self._query_read_state = QueryMetricState()
        # Nested repository calls in one worker must share the coordinator
        # acquired by their outer projection. A thread-local stack keeps that
        # adoption local to the request and never bypasses the durable TypeDB
        # lease held by another process.
        self._projection_leases = ProjectionLeaseState()
        self._projection_coordinator_write_enforced = bool(projection_coordinator_write_enforced)
        # The TypeDB Python driver performs a server-description handshake
        # when it is created.  Creating and closing it for each tiny control
        # read can cost more than the bounded native inference itself.  The
        # production worker therefore retains one process-local driver while
        # its supervisor still owns a killable process boundary.
        self._persistent_driver_enabled = bool(persistent_driver_enabled)
        # Set only for an isolated provisioning deployment. The control plane
        # has fenced its database from active delivery, so no PortfolioWorld
        # ABox can be reused. Normal live repositories always retain immutable
        # storage identity verification.
        self._fresh_candidate_rebuild = bool(fresh_candidate_rebuild)
        self._fresh_schema_bootstrap_batch_size = max(
            1,
            min(
                2048,
                int(
                    number_or_none(fresh_schema_bootstrap_batch_size)
                    or DEFAULT_TYPEDB_FRESH_SCHEMA_BOOTSTRAP_BATCH_SIZE
                ),
            ),
        )
        self._fresh_schema_bootstrap_timeout_seconds = max(
            1.0,
            min(
                1800.0,
                float(
                    number_or_none(fresh_schema_bootstrap_timeout_seconds)
                    or DEFAULT_TYPEDB_FRESH_SCHEMA_BOOTSTRAP_TIMEOUT_SECONDS
                ),
            ),
        )
        self._database_created_in_process = False
        self._persistent_driver = None
        self._persistent_driver_lock = threading.RLock()
        # A scoped Worldview Manifest contains the complete evidence-read
        # index.  It is intentionally large, but immutable between marker
        # revisions.  The persistent reasoning worker therefore keeps the
        # parsed form until the lightweight pointer/marker identity changes.
        # The cache is repository-local so an isolated worker exit still
        # provides a hard recovery boundary.
        self._active_scoped_abox_metadata_cache: Dict[
            Tuple[str, str, str, str, str], Dict[str, object]
        ] = {}
        self._active_scoped_abox_metadata_cache_lock = threading.RLock()

    @property
    def _query_metrics(self):
        return self._query_read_state.rows

    @_query_metrics.setter
    def _query_metrics(self, rows):
        self._query_read_state.rows = rows

    @property
    def _query_metrics_lock(self):
        return self._query_read_state.lock

    @property
    def _projection_coordinator_local(self):
        return self._projection_leases.local

    @property
    def _projection_coordinator_registry_lock(self):
        return self._projection_leases.registry_lock

    @property
    def _active_projection_coordinator_tokens(self):
        return self._projection_leases.active_tokens

    def with_scoped_abox_candidate_verification_retry(
        self,
        operation,
        timing: Dict[str, object] = None,
        verification: Dict[str, object] = None,
    ):
        return _abox_candidate_retry.with_scoped_abox_candidate_verification_retry(
            self,
            operation,
            timing,
            verification,
            error_code=typedb_error_code,
        )

    @staticmethod
    def _typedb_runtime() -> TypeDBRuntime:
        return TypeDBRuntime(
            timeout=typedb_operation_timeout,
            monotonic=time.monotonic,
            perf_counter=time.perf_counter,
            sleep=time.sleep,
            error_code=typedb_error_code,
        )

    @staticmethod
    def _typedb_readiness_cache() -> SchemaReadinessCache:
        return SchemaReadinessCache(
            entries=TypeDBOntologyGraphRepository._process_base_schema_ready,
            lock=TypeDBOntologyGraphRepository._process_base_schema_ready_lock,
            ttl_seconds=TypeDBOntologyGraphRepository._process_base_schema_cache_seconds,
        )

    def with_typedb_retries(self, operation, retry_if=None):
        return _typedb_connection.with_typedb_retries(
            self, operation, retry_if, runtime=self._typedb_runtime()
        )

    def runtime_timeout_seconds(self, key: str, default_seconds: float) -> float:
        try:
            configured = number_or_none(runtime_settings().get(key))
        except Exception:
            configured = None
        if configured is None:
            configured = default_seconds
        return max(1.0, float(configured or default_seconds))

    def query_timeout_seconds(self) -> float:
        return self._query_timeout_seconds

    def schema_operation_timeout_seconds(self) -> float:
        return self._schema_operation_timeout_seconds

    def write_operation_timeout_seconds(self) -> float:
        return self._write_operation_timeout_seconds

    def condition_detail_queries_enabled(self) -> bool:
        return self._condition_detail_queries_enabled

    def query_metrics_enabled(self) -> bool:
        return self._query_metrics_enabled

    def reset_query_metrics(self) -> None:
        return _graph_reads_metrics.reset_query_metrics(
            self,
        )

    def record_query_metric(
        self, label: str, query: str, row_count: int, duration_ms: float, status: str = "ok"
    ) -> None:
        return _graph_reads_metrics.record_query_metric(
            self,
            label,
            query,
            row_count,
            duration_ms,
            status,
        )

    def query_metrics_snapshot(self) -> Dict[str, object]:
        return _graph_reads_metrics.query_metrics_snapshot(
            self,
        )

    def rulebox_snapshot_cache_seconds(self) -> float:
        return self._rulebox_snapshot_cache_seconds

    def native_rule_execution_enabled(self) -> bool:
        return self._native_rule_execution_enabled

    def native_rule_query_timeout_seconds(self) -> float:
        return self._native_rule_query_timeout_seconds

    def native_rule_dedicated_read_driver_enabled(self) -> bool:
        return self._native_rule_dedicated_read_driver_enabled

    def native_rule_indexed_any_condition_query_timeout_seconds(self) -> float:
        return max(
            0.5,
            min(
                self.native_rule_execution_budget_seconds(),
                max(
                    self.native_rule_query_timeout_seconds(),
                    DEFAULT_TYPEDB_NATIVE_RULE_INDEXED_ANY_CONDITION_QUERY_TIMEOUT_SECONDS,
                ),
            ),
        )

    def native_rule_execution_budget_seconds(self) -> float:
        return self._native_rule_execution_budget_seconds

    def native_rule_parallelism(self) -> int:
        return self._native_rule_parallelism

    def native_rule_target_parallelism(self) -> int:
        return self._native_rule_target_parallelism

    def native_rule_subject_fanout_enabled(self) -> bool:
        return self._native_rule_subject_fanout_enabled

    def native_rule_subject_parallelism(self) -> int:
        return self._native_rule_subject_parallelism

    def native_rule_total_read_parallelism(self) -> int:
        return self._native_rule_total_read_parallelism

    def native_rule_target_work_sharding_enabled(self) -> bool:
        return self._native_rule_target_work_sharding_enabled

    def native_rule_adaptive_target_sharding_enabled(self) -> bool:
        return self._native_rule_adaptive_target_sharding_enabled

    def native_rule_any_condition_parallelism(self) -> int:
        return self._native_rule_any_condition_parallelism

    def native_rule_durable_preflight_fallback_enabled(self) -> bool:
        return self._native_rule_durable_preflight_fallback_enabled

    def clear_rulebox_snapshot_cache(self) -> None:
        self._rulebox_snapshot_cache_at = 0.0
        self._rulebox_snapshot_cache_full_load_at = 0.0
        self._rulebox_snapshot_cache_result = {}

    def active_tbox_metadata(self) -> Dict[str, object]:
        return _graph_reads_metadata.active_tbox_metadata(
            self,
            _bindings=_graph_reads_metadata_ports.GraphReadsMetadataRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                inference_generation_records=inference_generation_records,
                inference_marker_is_active=inference_marker_is_active,
                inference_rulebox_metadata=inference_rulebox_metadata,
                native_inference_decision_eligible=native_inference_decision_eligible,
                typedb_error_code=typedb_error_code,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    @coordinated_typedb_projection_write("graph-save", typedb_projection_world_from_graph)
    def save_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        return _graph_writes_graph_save.save_graph(
            self,
            graph,
            _bindings=SaveGraphBindings(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                node_boxes=node_boxes,
                typedb_operation_timeout=typedb_operation_timeout,
                utc_now=utc_now,
            ),
        )

    def driver_missing_result(
        self, error: Exception, graph: PortfolioOntology
    ) -> Dict[str, object]:
        return _graph_writes_graph_save.driver_missing_result(self, error, graph)

    def driver_imports(self) -> Tuple[object, object]:
        return _typedb_connection.driver_imports()

    def create_driver(self, imported, request_timeout_seconds: float = None):
        return _typedb_connection.create_driver(self, imported, request_timeout_seconds)

    def open_driver(self, imported, request_timeout_seconds: float = None):
        return _typedb_connection.open_driver(self, imported, request_timeout_seconds)

    def driver_request_timeout_seconds(self) -> float:
        return _typedb_connection.driver_request_timeout_seconds(self)

    def open_native_rule_read_driver(self, imported, request_timeout_seconds: float = None):
        return _typedb_connection.open_native_rule_read_driver(self, imported, request_timeout_seconds)

    def close_native_rule_read_driver(self, driver) -> None:
        return _typedb_connection.close_native_rule_read_driver(self, driver)

    def write_transaction_options(self):
        return _typedb_transactions.write_transaction_options(self)

    def read_transaction_options(self, timeout_seconds: float = None):
        return _typedb_transactions.read_transaction_options(self, timeout_seconds)

    def schema_transaction_options(self, timeout_seconds: float = None):
        return _typedb_transactions.schema_transaction_options(self, timeout_seconds)

    def schema_transaction(self, driver, transaction_type, timeout_seconds: float = None):
        return _typedb_transactions.schema_transaction(self, driver, transaction_type, timeout_seconds)

    def close_driver(self, driver) -> None:
        return _typedb_connection.close_driver(self, driver)

    def invalidate_persistent_driver(self) -> None:
        return _typedb_connection.invalidate_persistent_driver(self)

    def ensure_database(self, driver) -> None:
        return _typedb_connection.ensure_database(self, driver)

    def fresh_candidate_world_bootstrap_required(self, world_id: str = "") -> bool:
        return _graph_writes_graph_save.fresh_candidate_world_bootstrap_required(
            self, world_id
        )

    def process_base_schema_cache_key(self, schema_fingerprint: str) -> Tuple[str, str, bool, str]:
        return _typedb_readiness.process_base_schema_cache_key(self, schema_fingerprint)

    def process_base_schema_is_ready(self, schema_fingerprint: str) -> bool:
        return _typedb_readiness.process_base_schema_is_ready(self, schema_fingerprint, runtime=self._typedb_runtime(), cache=self._typedb_readiness_cache())

    def mark_process_base_schema_ready(self, schema_fingerprint: str) -> None:
        return _typedb_readiness.mark_process_base_schema_ready(self, schema_fingerprint, runtime=self._typedb_runtime(), cache=self._typedb_readiness_cache())

    def invalidate_process_base_schema_readiness(self) -> None:
        return _typedb_readiness.invalidate_process_base_schema_readiness(self, cache=self._typedb_readiness_cache())

    def base_schema_type_names(self) -> set:
        return _typedb_inspection.base_schema_type_names(self)

    def typedb_schema_type_names(self, driver) -> set:
        return _typedb_inspection.typedb_schema_type_names(self, driver)

    def typedb_schema_text(self, driver) -> str:
        return _typedb_inspection.typedb_schema_text(self, driver)

    def base_schema_contract_state(self) -> Dict[str, object]:
        return _typedb_inspection.base_schema_contract_state(self)

    ontology_storage_identity_migration_required = staticmethod(_typedb_migrations.ontology_storage_identity_migration_required)

    def migrate_ontology_storage_identity(self, driver, imported, schema_text: str) -> None:
        return _typedb_migrations.migrate_ontology_storage_identity(self, driver, imported, schema_text, runtime=self._typedb_runtime())

    ontology_scope_schema_migration_required = staticmethod(_typedb_migrations.ontology_scope_schema_migration_required)

    def migrate_ontology_scope_schema(self, driver, imported) -> None:
        return _typedb_migrations.migrate_ontology_scope_schema(self, driver, imported, runtime=self._typedb_runtime())

    ontology_content_fingerprint_schema_migration_required = staticmethod(_typedb_migrations.ontology_content_fingerprint_schema_migration_required)

    def migrate_ontology_content_fingerprint_schema(
        self,
        driver,
        imported,
        schema_text: str,
    ) -> None:
        return _typedb_migrations.migrate_ontology_content_fingerprint_schema(self, driver, imported, schema_text, runtime=self._typedb_runtime())

    ontology_world_schema_migration_required = staticmethod(_typedb_migrations.ontology_world_schema_migration_required)

    def migrate_ontology_world_schema(self, driver, imported) -> None:
        return _typedb_migrations.migrate_ontology_world_schema(self, driver, imported, runtime=self._typedb_runtime())

    promoted_schema_migration_required = staticmethod(_typedb_migrations.promoted_schema_migration_required)

    def migrate_promoted_schema(self, driver, imported, schema_text: str) -> None:
        return _typedb_migrations.migrate_promoted_schema(self, driver, imported, schema_text, runtime=self._typedb_runtime())

    ontology_semantic_schema_migration_required = staticmethod(_typedb_migrations.ontology_semantic_schema_migration_required)

    def migrate_ontology_semantic_schema(self, driver, imported, schema_text: str) -> None:
        return _typedb_migrations.migrate_ontology_semantic_schema(self, driver, imported, schema_text, runtime=self._typedb_runtime())

    schema_definition_statements = staticmethod(_typedb_schema_plan.schema_definition_statements)

    schema_definition_identity = staticmethod(_typedb_schema_plan.schema_definition_identity)

    schema_definition_clauses = staticmethod(_typedb_schema_plan.schema_definition_clauses)

    schema_subtype_parent = staticmethod(_typedb_schema_plan.schema_subtype_parent)

    schema_topological_definitions = staticmethod(_typedb_schema_plan.schema_topological_definitions)

    schema_definition_batches = staticmethod(_typedb_schema_plan.schema_definition_batches)

    def base_schema_bootstrap_plan(
        self,
        existing_schema_text: str = "",
        batch_size: int = DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE,
    ) -> List[Dict[str, object]]:
        return _typedb_schema_plan.base_schema_bootstrap_plan(self.schema_query(), existing_schema_text, batch_size)

    def synchronize_base_schema_batches(
        self,
        driver,
        imported,
        schema_text: str = "",
        batch_size: int = DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE,
        operation_timeout_seconds: float = None,
    ) -> Dict[str, object]:
        return _typedb_bootstrap.synchronize_base_schema_batches(self, driver, imported, schema_text, batch_size, operation_timeout_seconds, runtime=self._typedb_runtime())

    def typedb_http_json_request(
        self,
        path: str,
        payload: Dict[str, object],
        timeout_seconds: float,
        token: str = "",
    ) -> Dict[str, object]:
        return _typedb_http.typedb_http_json_request(self, path, payload, timeout_seconds, token)

    def synchronize_base_schema_batches_http(
        self,
        schema_text: str = "",
        batch_size: int = DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE,
        operation_timeout_seconds: float = None,
    ) -> Dict[str, object]:
        return _typedb_bootstrap.synchronize_base_schema_batches_http(self, schema_text, batch_size, operation_timeout_seconds, runtime=self._typedb_runtime())

    def ensure_schema(self, driver, imported) -> None:
        return _typedb_lifecycle.ensure_schema(self, driver, imported, runtime=self._typedb_runtime())

    def read_rows(
        self,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]:
        return _graph_reads_execution.read_rows(
            self,
            query,
            columns,
            label,
            timeout_seconds,
        )

    def read_rows_in_transaction(
        self,
        tx,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]:
        return _graph_reads_execution.read_rows_in_transaction(
            self,
            tx,
            query,
            columns,
            label,
            timeout_seconds,
            _bindings=_graph_reads_execution_ports.GraphReadsExecutionRuntime(
                typedb_operation_timeout=typedb_operation_timeout, typedb_row_value=typedb_row_value
            ),
        )

    def has_box_rows(self, box: str, world_id: str = "") -> bool:
        return _graph_reads_rows.has_box_rows(
            self,
            box,
            world_id,
        )

    def read_entity_rows(
        self,
        boxes: Iterable[str] = None,
        limit: int = 0,
        world_id: str = "",
        snapshot_id: str = "",
    ) -> List[Dict[str, object]]:
        return _graph_reads_rows.read_entity_rows(
            self,
            boxes,
            limit,
            world_id,
            snapshot_id,
            _bindings=_graph_reads_rows_ports.GraphReadsRowsRuntime(
                endpoint_node_row=endpoint_node_row,
                list_of_strings=list_of_strings,
                merge_flat_properties=merge_flat_properties,
                normalized_boxes=normalized_boxes,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def read_active_hypothesis_calibration_rows(
        self,
        symbols: Iterable[str] = None,
        limit: int = 40,
        world_id: str = "",
    ) -> List[Dict[str, object]]:
        return _graph_reads_rows.read_active_hypothesis_calibration_rows(
            self,
            symbols,
            limit,
            world_id,
            _bindings=_graph_reads_rows_ports.GraphReadsRowsRuntime(
                endpoint_node_row=endpoint_node_row,
                list_of_strings=list_of_strings,
                merge_flat_properties=merge_flat_properties,
                normalized_boxes=normalized_boxes,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def hypothesis_calibration_snapshot(
        self,
        symbols: Iterable[str] = None,
        limit: int = 40,
        world_id: str = "",
        source_abox_snapshot_id: str = "",
        generation_aligned: bool = False,
    ) -> Dict[str, object]:
        return _graph_reads_rows.hypothesis_calibration_snapshot(
            self,
            symbols,
            limit,
            world_id,
            source_abox_snapshot_id,
            generation_aligned,
        )

    def hypothesis_calibration_snapshot_for_native_result(
        self,
        matched_graph: PortfolioOntology,
        symbols: Iterable[str],
        source_abox_snapshot_id: str,
        generation_aligned: bool,
        scoped_active_abox: bool,
        limit: int = 40,
        world_id: str = "",
    ) -> Dict[str, object]:
        return _graph_reads_rows.hypothesis_calibration_snapshot_for_native_result(
            self,
            matched_graph,
            symbols,
            source_abox_snapshot_id,
            generation_aligned,
            scoped_active_abox,
            limit,
            world_id,
        )

    def read_entity_rows_by_ids(self, ids: Iterable[str], boxes: Iterable[str] = None, world_id: str = "") -> List[Dict[str, object]]:
        return _graph_reads_rows.read_entity_rows_by_ids(
            self,
            ids,
            boxes,
            world_id,
            _bindings=_graph_reads_rows_ports.GraphReadsRowsRuntime(
                endpoint_node_row=endpoint_node_row,
                list_of_strings=list_of_strings,
                merge_flat_properties=merge_flat_properties,
                normalized_boxes=normalized_boxes,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def read_abox_entity_rows_by_storage_ids(
        self,
        storage_ids: Iterable[str],
        world_id: str = "",
    ) -> List[Dict[str, object]]:
        return _graph_reads_rows.read_abox_entity_rows_by_storage_ids(
            self,
            storage_ids,
            world_id,
        )

    def read_abox_relation_rows_by_storage_ids(
        self,
        storage_ids: Iterable[str],
        relation_types: Iterable[str] = None,
        world_id: str = "",
    ) -> List[Dict[str, object]]:
        return _graph_reads_rows.read_abox_relation_rows_by_storage_ids(
            self,
            storage_ids,
            relation_types,
            world_id,
            _bindings=_graph_reads_rows_ports.GraphReadsRowsRuntime(
                endpoint_node_row=endpoint_node_row,
                list_of_strings=list_of_strings,
                merge_flat_properties=merge_flat_properties,
                normalized_boxes=normalized_boxes,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def read_relation_rows_by_source_ids(
        self,
        source_ids: Iterable[str],
        boxes: Iterable[str] = None,
        relation_types: Iterable[str] = None,
        include_incoming: bool = True,
        world_id: str = "",
    ) -> List[Dict[str, object]]:
        return _graph_reads_rows.read_relation_rows_by_source_ids(
            self,
            source_ids,
            boxes,
            relation_types,
            include_incoming,
            world_id,
            _bindings=_graph_reads_rows_ports.GraphReadsRowsRuntime(
                endpoint_node_row=endpoint_node_row,
                list_of_strings=list_of_strings,
                merge_flat_properties=merge_flat_properties,
                normalized_boxes=normalized_boxes,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def active_abox_relation_types_by_symbol(
        self,
        symbols: Iterable[str] = None,
        timeout_seconds: float = None,
        world_id: str = "",
        active_abox_metadata: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _graph_reads_rows.active_abox_relation_types_by_symbol(
            self,
            symbols,
            timeout_seconds,
            world_id,
            active_abox_metadata,
        )

    def rebuild_active_manifest_native_rule_evidence_read_index(
        self,
        active_metadata: Dict[str, object],
        world_id: str = "",
    ) -> Dict[str, object]:
        return _manifest_read_index.rebuild_active_manifest_native_rule_evidence_read_index(
            self,
            active_metadata,
            world_id,
        )

    def active_abox_rule_context(self, symbols: Iterable[str], world_id: str = "") -> Dict[str, object]:
        return _graph_reads_rows.active_abox_rule_context(
            self,
            symbols,
            world_id,
        )

    def hydrate_native_rule_evidence_field_index(
        self,
        evidence_read_index: Dict[str, object] = None,
        target_symbols: Iterable[str] = None,
        relation_types: Iterable[str] = None,
    ) -> Dict[str, object]:
        return _manifest_read_index.hydrate_native_rule_evidence_field_index(
            self,
            evidence_read_index,
            target_symbols,
            relation_types,
            _bindings=_manifest_read_index_ports.ManifestReadIndexRuntime(
                typedb_error_code=typedb_error_code
            ),
        )

    def active_abox_snapshot_id(self, world_id: str = "") -> str:
        metadata = self.active_abox_metadata(world_id)
        if str(metadata.get("status") or "") != "ok":
            return ""
        return str(metadata.get("aboxSnapshotId") or "")

    def box_snapshot_row_counts(self, box: str, snapshot_id: str, world_id: str = "") -> Dict[str, int]:
        return _graph_reads_metadata.box_snapshot_row_counts(
            self,
            box,
            snapshot_id,
            world_id,
        )

    def box_row_counts(self, box: str, world_id: str = "") -> Dict[str, int]:
        return _graph_reads_metadata.box_row_counts(
            self,
            box,
            world_id,
        )

    def abox_projection_marker_rows(
        self,
        world_id: str = "",
        snapshot_id: str = "",
        limit: int = 0,
    ) -> List[Dict[str, object]]:
        return _graph_reads_metadata.abox_projection_marker_rows(
            self,
            world_id,
            snapshot_id,
            limit,
            _bindings=_graph_reads_metadata_ports.GraphReadsMetadataRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                inference_generation_records=inference_generation_records,
                inference_marker_is_active=inference_marker_is_active,
                inference_rulebox_metadata=inference_rulebox_metadata,
                native_inference_decision_eligible=native_inference_decision_eligible,
                typedb_error_code=typedb_error_code,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def active_worldview_manifest_pointer_rows(self, world_id: str = "", limit: int = 0) -> List[Dict[str, object]]:
        return _graph_reads_metadata.active_worldview_manifest_pointer_rows(
            self,
            world_id,
            limit,
            _bindings=_graph_reads_metadata_ports.GraphReadsMetadataRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                inference_generation_records=inference_generation_records,
                inference_marker_is_active=inference_marker_is_active,
                inference_rulebox_metadata=inference_rulebox_metadata,
                native_inference_decision_eligible=native_inference_decision_eligible,
                typedb_error_code=typedb_error_code,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def active_worldview_manifest_pointer_identity_rows(
        self,
        world_id: str = "",
        limit: int = 0,
    ) -> List[Dict[str, object]]:
        return _graph_reads_metadata.active_worldview_manifest_pointer_identity_rows(
            self,
            world_id,
            limit,
            _bindings=_graph_reads_metadata_ports.GraphReadsMetadataRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                inference_generation_records=inference_generation_records,
                inference_marker_is_active=inference_marker_is_active,
                inference_rulebox_metadata=inference_rulebox_metadata,
                native_inference_decision_eligible=native_inference_decision_eligible,
                typedb_error_code=typedb_error_code,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def worldview_manifest_marker_count(self, world_id: str = "") -> int:
        return _graph_reads_metadata.worldview_manifest_marker_count(
            self,
            world_id,
        )

    def worldview_manifest_marker_rows(
        self,
        world_id: str = "",
        manifest_id: str = "",
        limit: int = 0,
    ) -> List[Dict[str, object]]:
        return _graph_reads_metadata.worldview_manifest_marker_rows(
            self,
            world_id,
            manifest_id,
            limit,
            _bindings=_graph_reads_metadata_ports.GraphReadsMetadataRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                inference_generation_records=inference_generation_records,
                inference_marker_is_active=inference_marker_is_active,
                inference_rulebox_metadata=inference_rulebox_metadata,
                native_inference_decision_eligible=native_inference_decision_eligible,
                typedb_error_code=typedb_error_code,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def worldview_manifest_marker_identity_rows(
        self,
        world_id: str = "",
        manifest_id: str = "",
        limit: int = 0,
    ) -> List[Dict[str, object]]:
        return _graph_reads_metadata.worldview_manifest_marker_identity_rows(
            self,
            world_id,
            manifest_id,
            limit,
            _bindings=_graph_reads_metadata_ports.GraphReadsMetadataRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                inference_generation_records=inference_generation_records,
                inference_marker_is_active=inference_marker_is_active,
                inference_rulebox_metadata=inference_rulebox_metadata,
                native_inference_decision_eligible=native_inference_decision_eligible,
                typedb_error_code=typedb_error_code,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    @staticmethod
    def scoped_abox_metadata_from_manifest_marker(marker: Dict[str, object]) -> Dict[str, object]:
        return _graph_reads_metadata.scoped_abox_metadata_from_manifest_marker(
            marker,
        )

    def active_abox_pointer_rows(self, world_id: str = "", limit: int = 0) -> List[Dict[str, object]]:
        return _graph_reads_metadata.active_abox_pointer_rows(
            self,
            world_id,
            limit,
            _bindings=_graph_reads_metadata_ports.GraphReadsMetadataRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                inference_generation_records=inference_generation_records,
                inference_marker_is_active=inference_marker_is_active,
                inference_rulebox_metadata=inference_rulebox_metadata,
                native_inference_decision_eligible=native_inference_decision_eligible,
                typedb_error_code=typedb_error_code,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def abox_metadata_from_marker(self, marker: Dict[str, object]) -> Dict[str, object]:
        return _graph_reads_metadata.abox_metadata_from_marker(
            self,
            marker,
        )

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]:
        return _graph_reads_metadata.active_abox_metadata(
            self,
            world_id,
        )

    def active_inference_generation_marker_rows(
        self,
        world_id: str = "",
        limit: int = 1,
    ) -> List[Dict[str, object]]:
        return _graph_reads_metadata.active_inference_generation_marker_rows(
            self,
            world_id,
            limit,
            _bindings=_graph_reads_metadata_ports.GraphReadsMetadataRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                inference_generation_records=inference_generation_records,
                inference_marker_is_active=inference_marker_is_active,
                inference_rulebox_metadata=inference_rulebox_metadata,
                native_inference_decision_eligible=native_inference_decision_eligible,
                typedb_error_code=typedb_error_code,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def inferencebox_recovery_metadata(self, world_id: str = "") -> Dict[str, object]:
        return _graph_reads_metadata.inferencebox_recovery_metadata(
            self,
            world_id,
            _bindings=_graph_reads_metadata_ports.GraphReadsMetadataRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                inference_generation_records=inference_generation_records,
                inference_marker_is_active=inference_marker_is_active,
                inference_rulebox_metadata=inference_rulebox_metadata,
                native_inference_decision_eligible=native_inference_decision_eligible,
                typedb_error_code=typedb_error_code,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def inferencebox_commit_proof(
        self,
        inference_generation_id: str,
        source_abox_snapshot_id: str,
        target_symbols: List[str] = None,
        world_id: str = "",
    ) -> Dict[str, object]:
        return _graph_reads_metadata.inferencebox_commit_proof(
            self,
            inference_generation_id,
            source_abox_snapshot_id,
            target_symbols,
            world_id,
        )

    def list_ontology_worlds(self) -> List[Dict[str, object]]:
        return _graph_reads_metadata.list_ontology_worlds(
            self,
        )

    def abox_pending_activation_rows(self, world_id: str = "") -> List[Dict[str, object]]:
        return _graph_reads_metadata.abox_pending_activation_rows(
            self,
            world_id,
        )

    def pending_abox_activation(self, world_id: str = "") -> Dict[str, object]:
        return _graph_reads_metadata.pending_abox_activation(
            self,
            world_id,
        )

    def read_relation_rows(
        self,
        boxes: Iterable[str] = None,
        limit: int = 0,
        world_id: str = "",
        snapshot_id: str = "",
    ) -> List[Dict[str, object]]:
        return _graph_reads_rows.read_relation_rows(
            self,
            boxes,
            limit,
            world_id,
            snapshot_id,
            _bindings=_graph_reads_rows_ports.GraphReadsRowsRuntime(
                endpoint_node_row=endpoint_node_row,
                list_of_strings=list_of_strings,
                merge_flat_properties=merge_flat_properties,
                normalized_boxes=normalized_boxes,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def read_inference_generation_records(self, published_only: bool = True, world_id: str = "") -> List[Dict[str, object]]:
        return _graph_reads_metadata.read_inference_generation_records(
            self,
            published_only,
            world_id,
            _bindings=_graph_reads_metadata_ports.GraphReadsMetadataRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                inference_generation_records=inference_generation_records,
                inference_marker_is_active=inference_marker_is_active,
                inference_rulebox_metadata=inference_rulebox_metadata,
                native_inference_decision_eligible=native_inference_decision_eligible,
                typedb_error_code=typedb_error_code,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def read_inferencebox_entity_rows(
        self,
        generation_id: str = "",
        symbols: Iterable[str] = None,
        limit: int = 0,
        world_id: str = "",
    ) -> List[Dict[str, object]]:
        return _graph_reads_rows.read_inferencebox_entity_rows(
            self,
            generation_id,
            symbols,
            limit,
            world_id,
            _bindings=_graph_reads_rows_ports.GraphReadsRowsRuntime(
                endpoint_node_row=endpoint_node_row,
                list_of_strings=list_of_strings,
                merge_flat_properties=merge_flat_properties,
                normalized_boxes=normalized_boxes,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def read_inferencebox_relation_rows(
        self,
        generation_id: str = "",
        symbols: Iterable[str] = None,
        limit: int = 0,
        world_id: str = "",
    ) -> List[Dict[str, object]]:
        return _graph_reads_rows.read_inferencebox_relation_rows(
            self,
            generation_id,
            symbols,
            limit,
            world_id,
            _bindings=_graph_reads_rows_ports.GraphReadsRowsRuntime(
                endpoint_node_row=endpoint_node_row,
                list_of_strings=list_of_strings,
                merge_flat_properties=merge_flat_properties,
                normalized_boxes=normalized_boxes,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def entity_rows_from_typeql(self, rows: Iterable[Dict[str, object]], box: str) -> List[Dict[str, object]]:
        return [self.entity_row_from_typeql(row, box) for row in rows or [] if str(row.get("id") or "")]

    def entity_row_from_typeql(self, row: Dict[str, object], box: str) -> Dict[str, object]:
        return _graph_reads_rows.entity_row_from_typeql(
            self,
            row,
            box,
            _bindings=_graph_reads_rows_ports.GraphReadsRowsRuntime(
                endpoint_node_row=endpoint_node_row,
                list_of_strings=list_of_strings,
                merge_flat_properties=merge_flat_properties,
                normalized_boxes=normalized_boxes,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def relation_rows_from_typeql(self, rows: Iterable[Dict[str, object]], box: str) -> List[Dict[str, object]]:
        return [self.relation_row_from_typeql(row, box) for row in rows or [] if str(row.get("sourceId") or "") and str(row.get("targetId") or "")]

    def relation_row_from_typeql(self, row: Dict[str, object], box: str) -> Dict[str, object]:
        return _graph_reads_rows.relation_row_from_typeql(
            self,
            row,
            box,
            _bindings=_graph_reads_rows_ports.GraphReadsRowsRuntime(
                endpoint_node_row=endpoint_node_row,
                list_of_strings=list_of_strings,
                merge_flat_properties=merge_flat_properties,
                normalized_boxes=normalized_boxes,
                typeql_limit_clause=typeql_limit_clause,
            ),
        )

    def abox_delete_batch_size(self, settings: Dict[str, object] = None) -> int:
        return _graph_writes_write_policy.abox_delete_batch_size(
            self,
            settings,
            _bindings=AboxDeleteBatchSizeBindings(runtime_settings=runtime_settings),
        )

    def abox_incremental_cleanup_batch_size(
        self, settings: Dict[str, object] = None
    ) -> int:
        return _graph_writes_write_policy.abox_incremental_cleanup_batch_size(
            self,
            settings,
            _bindings=AboxIncrementalCleanupBatchSizeBindings(
                runtime_settings=runtime_settings
            ),
        )

    def abox_incremental_cleanup_max_batches_per_save(
        self, settings: Dict[str, object] = None
    ) -> int:
        return _graph_writes_write_policy.abox_incremental_cleanup_max_batches_per_save(
            self,
            settings,
            _bindings=AboxIncrementalCleanupMaxBatchesPerSaveBindings(
                runtime_settings=runtime_settings
            ),
        )

    def abox_inactive_generation_keep_count(
        self, settings: Dict[str, object] = None
    ) -> int:
        return _graph_writes_write_policy.abox_inactive_generation_keep_count(
            self,
            settings,
            _bindings=AboxInactiveGenerationKeepCountBindings(
                runtime_settings=runtime_settings
            ),
        )

    def abox_inactive_generation_max_prune_per_save(
        self, settings: Dict[str, object] = None
    ) -> int:
        return _graph_writes_write_policy.abox_inactive_generation_max_prune_per_save(
            self,
            settings,
            _bindings=AboxInactiveGenerationMaxPrunePerSaveBindings(
                runtime_settings=runtime_settings
            ),
        )

    def deferred_maintenance_abox_max_manifests(
        self, settings: Dict[str, object] = None
    ) -> int:
        return _graph_writes_write_policy.deferred_maintenance_abox_max_manifests(
            self,
            settings,
            _bindings=DeferredMaintenanceAboxMaxManifestsBindings(
                runtime_settings=runtime_settings
            ),
        )

    def deferred_maintenance_abox_max_delete_batches(
        self, settings: Dict[str, object] = None
    ) -> int:
        return _graph_writes_write_policy.deferred_maintenance_abox_max_delete_batches(
            self,
            settings,
            _bindings=DeferredMaintenanceAboxMaxDeleteBatchesBindings(
                runtime_settings=runtime_settings
            ),
        )

    def deferred_maintenance_abox_delete_batch_size(
        self, settings: Dict[str, object] = None
    ) -> int:
        return _graph_writes_write_policy.deferred_maintenance_abox_delete_batch_size(
            self,
            settings,
            _bindings=DeferredMaintenanceAboxDeleteBatchSizeBindings(
                runtime_settings=runtime_settings
            ),
        )

    def abox_write_transaction_query_count(self, settings: Dict[str, object] = None) -> int:
        return _graph_writes_write_policy.abox_write_transaction_query_count(
            self,
            settings,
            _bindings=AboxWriteTransactionQueryCountBindings(
                runtime_settings=runtime_settings
            ),
        )

    def abox_node_batch_size(self, settings: Dict[str, object] = None) -> int:
        return _graph_writes_write_policy.abox_node_batch_size(
            self,
            settings,
            _bindings=AboxNodeBatchSizeBindings(runtime_settings=runtime_settings),
        )

    def abox_relation_batch_size(self, settings: Dict[str, object] = None) -> int:
        return _graph_writes_write_policy.abox_relation_batch_size(
            self,
            settings,
            _bindings=AboxRelationBatchSizeBindings(runtime_settings=runtime_settings),
        )

    def graph_write_transaction_query_count(
        self, settings: Dict[str, object] = None
    ) -> int:
        return _graph_writes_write_policy.graph_write_transaction_query_count(
            self,
            settings,
            _bindings=GraphWriteTransactionQueryCountBindings(
                runtime_settings=runtime_settings
            ),
        )

    def static_node_insert_batch_size(self, settings: Dict[str, object] = None) -> int:
        return _graph_writes_write_policy.static_node_insert_batch_size(
            self,
            settings,
            _bindings=StaticNodeInsertBatchSizeBindings(runtime_settings=runtime_settings),
        )

    def static_write_transaction_query_count(
        self, settings: Dict[str, object] = None
    ) -> int:
        return _graph_writes_write_policy.static_write_transaction_query_count(
            self,
            settings,
            _bindings=StaticWriteTransactionQueryCountBindings(
                runtime_settings=runtime_settings
            ),
        )

    def inferencebox_write_transaction_query_count(
        self, settings: Dict[str, object] = None
    ) -> int:
        return _graph_writes_write_policy.inferencebox_write_transaction_query_count(
            self,
            settings,
            _bindings=InferenceboxWriteTransactionQueryCountBindings(
                runtime_settings=runtime_settings
            ),
        )

    def inferencebox_relation_batch_size(self, settings: Dict[str, object] = None) -> int:
        return _graph_writes_write_policy.inferencebox_relation_batch_size(
            self,
            settings,
            _bindings=InferenceboxRelationBatchSizeBindings(
                runtime_settings=runtime_settings
            ),
        )

    def inferencebox_given_relation_writes_enabled(
        self, settings: Dict[str, object] = None
    ) -> bool:
        return _graph_writes_write_policy.inferencebox_given_relation_writes_enabled(
            self,
            settings,
            _bindings=InferenceboxGivenRelationWritesEnabledBindings(
                runtime_settings=runtime_settings
            ),
        )

    def inferencebox_given_relation_batch_size(
        self, settings: Dict[str, object] = None
    ) -> int:
        return _graph_writes_write_policy.inferencebox_given_relation_batch_size(
            self,
            settings,
            _bindings=InferenceboxGivenRelationBatchSizeBindings(
                runtime_settings=runtime_settings
            ),
        )

    def box_instance_exists(self, driver, imported, box: str, type_label: str) -> bool:
        return _graph_writes_legacy_activation.box_instance_exists(
            self, driver, imported, box, type_label
        )

    def box_delete_batch_query(self, box: str, type_label: str, batch_size: int) -> str:
        return _graph_writes_legacy_activation.box_delete_batch_query(
            self, box, type_label, batch_size
        )

    def box_snapshot_instance_exists(
        self,
        driver,
        imported,
        box: str,
        snapshot_id: str,
        type_label: str,
    ) -> bool:
        return _graph_maintenance_generations.box_snapshot_instance_exists(
            self,
            driver,
            imported,
            box,
            snapshot_id,
            type_label,
        )

    def box_manifest_instance_exists(
        self,
        driver,
        imported,
        box: str,
        manifest_id: str,
        type_label: str,
        world_id: str = "",
    ) -> bool:
        return _graph_maintenance_generations.box_manifest_instance_exists(
            self,
            driver,
            imported,
            box,
            manifest_id,
            type_label,
            world_id,
        )

    def box_manifest_delete_batch_query(
        self,
        box: str,
        manifest_id: str,
        type_label: str,
        batch_size: int,
        world_id: str = "",
    ) -> str:
        return _graph_maintenance_generations.box_manifest_delete_batch_query(
            self,
            box,
            manifest_id,
            type_label,
            batch_size,
            world_id,
        )

    def delete_box_manifest_rows_in_batches(
        self,
        driver,
        imported,
        box: str,
        manifest_id: str,
        batch_size: int = None,
        max_batches: int = None,
        world_id: str = "",
    ) -> Dict[str, object]:
        return _graph_maintenance_generations.delete_box_manifest_rows_in_batches(
            self,
            driver,
            imported,
            box,
            manifest_id,
            batch_size,
            max_batches,
            world_id,
            _bindings=_graph_maintenance_generations_ports.GraphMaintenanceGenerationsRuntime(
                typedb_error_code=typedb_error_code,
                typedb_operation_timeout=typedb_operation_timeout,
            ),
        )

    def box_snapshot_delete_batch_query(
        self,
        box: str,
        snapshot_id: str,
        type_label: str,
        batch_size: int,
    ) -> str:
        return _graph_maintenance_generations.box_snapshot_delete_batch_query(
            self,
            box,
            snapshot_id,
            type_label,
            batch_size,
        )

    def box_snapshot_external_relation_references(
        self,
        driver,
        imported,
        box: str,
        snapshot_id: str,
        limit: int = 5,
    ) -> List[Dict[str, object]]:
        return _graph_maintenance_generations.box_snapshot_external_relation_references(
            self,
            driver,
            imported,
            box,
            snapshot_id,
            limit,
        )

    def delete_box_snapshot_rows_in_batches(
        self,
        driver,
        imported,
        box: str,
        snapshot_id: str,
        batch_size: int = None,
        max_batches: int = None,
        deadline_monotonic: float = None,
    ) -> Dict[str, object]:
        return _graph_maintenance_generations.delete_box_snapshot_rows_in_batches(
            self,
            driver,
            imported,
            box,
            snapshot_id,
            batch_size,
            max_batches,
            deadline_monotonic,
            _bindings=_graph_maintenance_generations_ports.GraphMaintenanceGenerationsRuntime(
                typedb_error_code=typedb_error_code,
                typedb_operation_timeout=typedb_operation_timeout,
            ),
        )

    def discard_abox_generation(self, snapshot_id: str) -> Dict[str, object]:
        return _graph_maintenance_generations.discard_abox_generation(
            self,
            snapshot_id,
            _bindings=_graph_maintenance_generations_ports.GraphMaintenanceGenerationsRuntime(
                typedb_error_code=typedb_error_code,
                typedb_operation_timeout=typedb_operation_timeout,
            ),
        )

    def delete_box_rows_in_batches(self, driver, imported, boxes: Iterable[str]) -> Dict[str, object]:
        return _graph_maintenance_generations.delete_box_rows_in_batches(
            self,
            driver,
            imported,
            boxes,
            _bindings=_graph_maintenance_generations_ports.GraphMaintenanceGenerationsRuntime(
                typedb_error_code=typedb_error_code,
                typedb_operation_timeout=typedb_operation_timeout,
            ),
        )

    def delete_world_abox_control_rows(self, driver, imported, world_id: str = "") -> Dict[str, object]:
        return _graph_maintenance_generations.delete_world_abox_control_rows(
            self,
            driver,
            imported,
            world_id,
            _bindings=_graph_maintenance_generations_ports.GraphMaintenanceGenerationsRuntime(
                typedb_error_code=typedb_error_code,
                typedb_operation_timeout=typedb_operation_timeout,
            ),
        )

    scoped_abox_control_delete_query = staticmethod(_abox_controls.scoped_abox_control_delete_query)

    def replace_scoped_abox_control_graph(
        self,
        driver,
        imported,
        graph: PortfolioOntology,
        world_id: str = "",
        scope_ids: Iterable[str] = None,
        replace_all_scope_pointers: bool = False,
    ) -> Dict[str, object]:
        return _abox_controls.replace_scoped_abox_control_graph(
            self, driver, imported, graph, world_id, scope_ids,
            replace_all_scope_pointers, runtime=self._abox_persistence_runtime(),
        )

    def clear_scoped_abox_pending_activation(self, world_id: str = "") -> Dict[str, object]:
        return _abox_controls.clear_scoped_abox_pending_activation(
            self, world_id, runtime=self._abox_persistence_runtime(),
        )

    def abox_candidate_snapshot_ids(self) -> List[str]:
        return _graph_writes_legacy_activation.abox_candidate_snapshot_ids(self)

    def cleanup_inactive_abox_candidates(
        self,
        driver,
        imported,
        active_snapshot_id: str = "",
    ) -> Dict[str, object]:
        return _graph_maintenance_generations.cleanup_inactive_abox_candidates(
            self,
            driver,
            imported,
            active_snapshot_id,
        )

    def drain_inactive_abox_generations_incrementally(
        self,
        driver,
        imported,
        active_snapshot_id: str = "",
        excluded_snapshot_ids: Iterable[str] = None,
    ) -> Dict[str, object]:
        return _graph_maintenance_generations.drain_inactive_abox_generations_incrementally(
            self,
            driver,
            imported,
            active_snapshot_id,
            excluded_snapshot_ids,
        )

    def prune_inactive_abox_generations(
        self,
        driver,
        imported,
        active_snapshot_id: str = "",
        keep_inactive_count: int = None,
        max_generations: int = None,
    ) -> Dict[str, object]:
        return _graph_maintenance_generations.prune_inactive_abox_generations(
            self,
            driver,
            imported,
            active_snapshot_id,
            keep_inactive_count,
            max_generations,
        )

    def clear_boxes_in_batches(self, boxes: Iterable[str]) -> Dict[str, object]:
        return _graph_maintenance_generations.clear_boxes_in_batches(
            self,
            boxes,
        )

    def graph_for_boxes(
        self,
        graph: PortfolioOntology,
        boxes: Iterable[str],
        retain_cross_box_relations: bool = False,
    ) -> PortfolioOntology:
        return _static_seed_graphs.graph_for_boxes(
            self, graph, boxes, retain_cross_box_relations
        )

    def graph_with_static_seed_generation(
        self, graph: PortfolioOntology, boxes: Iterable[str], generation_id
    ) -> PortfolioOntology:
        return _static_seed_graphs.graph_with_static_seed_generation(
            self, graph, boxes, generation_id
        )

    def abox_candidate_graph(self, graph: PortfolioOntology) -> PortfolioOntology:
        return _graph_writes_legacy_activation.abox_candidate_graph(self, graph)

    @staticmethod
    def abox_snapshot_id_from_graph(graph: PortfolioOntology) -> str:
        return _graph_writes_legacy_activation.abox_snapshot_id_from_graph(graph)

    def abox_active_pointer_graph(
        self,
        graph: PortfolioOntology,
        previous_snapshot_id: str = "",
        pending_activation: bool = True,
    ) -> PortfolioOntology:
        return _graph_writes_legacy_activation.abox_active_pointer_graph(
            self,
            graph,
            previous_snapshot_id,
            pending_activation,
            _bindings=AboxActivePointerGraphBindings(utc_now=utc_now),
        )

    def activate_abox_generation(
        self, snapshot_id: str, world_id: str = ""
    ) -> Dict[str, object]:
        return _graph_writes_legacy_activation.activate_abox_generation(
            self,
            snapshot_id,
            world_id,
            _bindings=ActivateAboxGenerationBindings(
                typedb_error_code=typedb_error_code, utc_now=utc_now
            ),
        )

    def finalize_abox_generation(
        self, active_snapshot_id: str, previous_snapshot_id: str = "", world_id: str = ""
    ) -> Dict[str, object]:
        return _graph_writes_legacy_activation.finalize_abox_generation(
            self, active_snapshot_id, previous_snapshot_id, world_id
        )

    def inferencebox_matches_pending_abox_activation(
        self,
        inferencebox: Dict[str, object],
        candidate_snapshot_id: str,
        target_symbols: Iterable[str] = None,
    ) -> bool:
        return _abox_candidate_recovery.inferencebox_matches_pending_abox_activation(
            inferencebox, candidate_snapshot_id, target_symbols,
        )

    @coordinated_typedb_projection_write(
        "pending-abox-recovery",
        typedb_projection_world_from_recovery,
    )
    def recover_pending_abox_activation(
        self,
        world_id: str = "",
        max_staged_target_symbols: int = 0,
    ) -> Dict[str, object]:
        return _abox_candidate_recovery.recover_pending_abox_activation(
            self, world_id, max_staged_target_symbols, error_code=typedb_error_code,
        )

    def write_graph(
        self, driver, imported, graph: PortfolioOntology, delete_boxes: Iterable[str] = None
    ) -> None:
        return _graph_writes_graph_write.write_graph(
            self,
            driver,
            imported,
            graph,
            delete_boxes,
            _bindings=WriteGraphBindings(
                node_boxes=node_boxes, typedb_operation_timeout=typedb_operation_timeout
            ),
        )

    def clear_inferencebox(self, world_id: str = "") -> Dict[str, object]:
        return _graph_writes_graph_write.clear_inferencebox(
            self,
            world_id,
            _bindings=ClearInferenceboxBindings(typedb_error_code=typedb_error_code),
        )

    def schema_query(self) -> str:
        return _static_seed_schema.schema_query(self)

    def delete_queries(self, boxes: Iterable[str]) -> List[str]:
        return _graph_writes_graph_write.delete_queries(self, boxes)

    def insert_queries(self, graph: PortfolioOntology) -> List[str]:
        return _graph_writes_graph_write.insert_queries(
            self, graph, _bindings=InsertQueriesBindings(utc_now=utc_now)
        )

    def graph_persistence_rows(
        self, graph: PortfolioOntology
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
        return _graph_writes_graph_write.graph_persistence_rows(self, graph)

    def abox_projection_marker_graph(
        self,
        graph: PortfolioOntology,
        expected_entity_count: int,
        expected_relation_count: int,
        box: str = "ABox",
    ) -> PortfolioOntology:
        return _graph_writes_legacy_activation.abox_projection_marker_graph(
            self,
            graph,
            expected_entity_count,
            expected_relation_count,
            box,
            _bindings=AboxProjectionMarkerGraphBindings(utc_now=utc_now),
        )

    def verify_abox_projection(
        self,
        graph: PortfolioOntology,
        expected_entity_count: int,
        expected_relation_count: int,
        box: str = "ABox",
    ) -> Dict[str, object]:
        return _graph_writes_legacy_activation.verify_abox_projection(
            self, graph, expected_entity_count, expected_relation_count, box
        )

    def graph_insert_queries(self, graph: PortfolioOntology) -> List[str]:
        return _graph_writes_graph_write.graph_insert_queries(
            self,
            graph,
            _bindings=GraphInsertQueriesBindings(
                runtime_settings=runtime_settings, utc_now=utc_now
            ),
        )

    def static_graph_insert_queries(self, graph: PortfolioOntology) -> List[str]:
        return _graph_writes_graph_write.static_graph_insert_queries(
            self,
            graph,
            _bindings=StaticGraphInsertQueriesBindings(
                runtime_settings=runtime_settings, utc_now=utc_now
            ),
        )

    def write_query_max_bytes(self, settings: Dict[str, object] = None) -> int:
        return _graph_writes_write_policy.write_query_max_bytes(
            self,
            settings,
            _bindings=WriteQueryMaxBytesBindings(runtime_settings=runtime_settings),
        )

    @staticmethod
    def query_byte_size(query: str) -> int:
        return len(str(query or "").encode("utf-8"))

    @staticmethod
    def external_relation_endpoint_ids(graph: PortfolioOntology) -> set:
        return _graph_writes_graph_write.external_relation_endpoint_ids(graph)

    def node_rows(
        self, graph: PortfolioOntology, include_external_relation_endpoints: bool = False
    ) -> List[Dict[str, object]]:
        return _graph_writes_node_rows.node_rows(
            self, graph, include_external_relation_endpoints
        )

    def evidence_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return _graph_writes_node_rows.evidence_node_rows(self, graph)

    def belief_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return _graph_writes_node_rows.belief_node_rows(
            self,
            graph,
            _bindings=BeliefNodeRowsBindings(rule_id_from_value=rule_id_from_value),
        )

    def opinion_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return _graph_writes_node_rows.opinion_node_rows(self, graph)

    def reasoning_card_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return _graph_writes_node_rows.reasoning_card_node_rows(self, graph)

    def support_relation_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return _graph_writes_node_rows.support_relation_rows(
            self,
            graph,
            _bindings=SupportRelationRowsBindings(rule_id_from_value=rule_id_from_value),
        )

    def node_insert_query(self, row: Dict[str, object], updated_at: str) -> str:
        return "insert " + self.node_insert_clause(row, updated_at, "$n") + ";"

    def node_insert_clause(
        self, row: Dict[str, object], updated_at: str, variable: str
    ) -> str:
        return _graph_writes_row_queries.node_insert_clause(
            self,
            row,
            updated_at,
            variable,
            _bindings=NodeInsertClauseBindings(
                promoted_node_text_value=promoted_node_text_value,
                promoted_node_value=promoted_node_value,
                typedb_node_allowed_attributes=typedb_node_allowed_attributes,
                typeql_has=typeql_has,
                typeql_has_bool_string=typeql_has_bool_string,
            ),
        )

    def relation_insert_query(self, row: Dict[str, object], updated_at: str) -> str:
        return _graph_writes_row_queries.relation_insert_query(self, row, updated_at)

    def relation_match_clause(
        self, row: Dict[str, object], source_variable: str, target_variable: str
    ) -> str:
        return _graph_writes_row_queries.relation_match_clause(
            self,
            row,
            source_variable,
            target_variable,
            _bindings=RelationMatchClauseBindings(typeql_has=typeql_has),
        )

    def relation_insert_clause(
        self,
        row: Dict[str, object],
        updated_at: str,
        relation_variable: str,
        source_variable: str,
        target_variable: str,
    ) -> str:
        return _graph_writes_row_queries.relation_insert_clause(
            self,
            row,
            updated_at,
            relation_variable,
            source_variable,
            target_variable,
            _bindings=RelationInsertClauseBindings(
                typeql_has=typeql_has, typeql_has_bool_string=typeql_has_bool_string
            ),
        )

    def batched_node_insert_queries(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        batch_size: int = 40,
        max_query_bytes: int = 0,
    ) -> List[str]:
        return _graph_writes_row_queries.batched_node_insert_queries(
            self, rows, updated_at, batch_size, max_query_bytes
        )

    def node_batch_insert_query(
        self, rows: Iterable[Dict[str, object]], updated_at: str
    ) -> str:
        return _graph_writes_row_queries.node_batch_insert_query(self, rows, updated_at)

    def batched_relation_insert_queries(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        batch_size: int = 25,
        max_query_bytes: int = 0,
    ) -> List[str]:
        return _graph_writes_row_queries.batched_relation_insert_queries(
            self, rows, updated_at, batch_size, max_query_bytes
        )

    def relation_batch_insert_query(
        self, rows: Iterable[Dict[str, object]], updated_at: str
    ) -> str:
        return _graph_writes_row_queries.relation_batch_insert_query(self, rows, updated_at)

    @staticmethod
    def _given_relation_value(value: object, value_type: str) -> object:
        return _graph_writes_row_queries._given_relation_value(value, value_type)

    @staticmethod
    def _given_relation_has_value(value: object) -> bool:
        return value is not None and str(value).strip() != ""

    def given_relation_writes_enabled(self, settings: Dict[str, object] = None) -> bool:
        return _graph_writes_write_policy.given_relation_writes_enabled(
            self,
            settings,
            _bindings=GivenRelationWritesEnabledBindings(runtime_settings=runtime_settings),
        )

    def given_relation_batch_size(self, settings: Dict[str, object] = None) -> int:
        return _graph_writes_write_policy.given_relation_batch_size(
            self,
            settings,
            _bindings=GivenRelationBatchSizeBindings(runtime_settings=runtime_settings),
        )

    def given_relation_row_values(self, row: Dict[str, object]) -> List[tuple]:
        return _graph_writes_row_queries.given_relation_row_values(self, row)

    def given_relation_insert_plans(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        settings: Dict[str, object] = None,
    ) -> List[Dict[str, object]]:
        return _graph_writes_row_queries.given_relation_insert_plans(
            self, rows, updated_at, settings
        )

    def inferencebox_insert_queries(
        self,
        node_rows: Iterable[Dict[str, object]],
        relation_rows: Iterable[Dict[str, object]],
        updated_at: str,
    ) -> List[str]:
        return _graph_writes_row_queries.inferencebox_insert_queries(
            self,
            node_rows,
            relation_rows,
            updated_at,
            _bindings=InferenceboxInsertQueriesBindings(runtime_settings=runtime_settings),
        )

    def inferencebox_given_relation_insert_plans(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        settings: Dict[str, object] = None,
    ) -> List[Dict[str, object]]:
        return _graph_writes_row_queries.inferencebox_given_relation_insert_plans(
            self,
            rows,
            updated_at,
            settings,
            _bindings=InferenceboxGivenRelationInsertPlansBindings(
                runtime_settings=runtime_settings
            ),
        )

    @staticmethod
    def seed_static_manifest_entity_id() -> str:
        return _static_seed_identity.seed_static_manifest_entity_id()

    def base_schema_contract_metadata(self) -> Dict[str, str]:
        return _static_seed_schema.base_schema_contract_metadata(self)

    def seed_static_manifest_metadata(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
        tbox_metadata: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _static_seed_identity.seed_static_manifest_metadata(
            self, graph, rules_payload, tbox_metadata
        )

    @staticmethod
    def static_seed_generation_ids(metadata: Dict[str, object] = None) -> Dict[str, str]:
        return _static_seed_identity.static_seed_generation_ids(metadata)

    def seed_static_manifest_graph(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
        tbox_metadata: Dict[str, object] = None,
    ) -> PortfolioOntology:
        return _static_seed_graphs.seed_static_manifest_graph(
            self, graph, rules_payload, tbox_metadata
        )

    def seed_static_manifest_storage_id(self) -> str:
        return _static_seed_identity.seed_static_manifest_storage_id(self)

    def read_seed_static_manifest(self) -> Dict[str, object]:
        return _static_seed_reads.read_seed_static_manifest(self)

    def seed_static_sentinels(
        self, graph: PortfolioOntology, generation_ids=None
    ) -> List[Dict[str, str]]:
        return _static_seed_graphs.seed_static_sentinels(self, graph, generation_ids)

    def seed_static_sentinels_present(
        self, graph: PortfolioOntology, generation_ids=None
    ) -> Dict[str, object]:
        return _static_seed_reads.seed_static_sentinels_present(self, graph, generation_ids)

    def seed_static_node_properties(
        self, graph: PortfolioOntology, entity_id_value: str
    ) -> Dict[str, object]:
        return _static_seed_reads.seed_static_node_properties(self, graph, entity_id_value)

    def legacy_static_seed_preflight(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
        expected: Dict[str, object],
    ) -> Dict[str, object]:
        return _static_seed_preflight.legacy_static_seed_preflight(
            self, graph, rules_payload, expected
        )

    def seed_graph_preflight(
        self, graph: PortfolioOntology, rules_payload: List[Dict[str, object]]
    ) -> Dict[str, object]:
        return _static_seed_preflight.seed_graph_preflight(self, graph, rules_payload)

    def seed_relation_repair_eligible(self, preflight: Dict[str, object]) -> bool:
        return _static_seed_preflight.seed_relation_repair_eligible(self, preflight)

    def missing_seed_relation_rows(
        self, graph: PortfolioOntology
    ) -> List[Dict[str, object]]:
        return _static_seed_reads.missing_seed_relation_rows(self, graph)

    def repair_seed_relations(self, graph: PortfolioOntology) -> Dict[str, object]:
        return _static_seed_repair.repair_seed_relations(
            self,
            graph,
            _bindings=RepairBindings(
                runtime_settings=runtime_settings,
                typedb_operation_timeout=typedb_operation_timeout,
                utc_now=utc_now,
            ),
        )

    @staticmethod
    def seed_static_box_names() -> List[str]:
        return _static_seed_identity.seed_static_box_names()

    def seed_static_boxes_requiring_refresh(
        self, preflight: Dict[str, object]
    ) -> List[str]:
        return _static_seed_preflight.seed_static_boxes_requiring_refresh(self, preflight)

    @staticmethod
    def static_seed_schema_prepared(preflight: Dict[str, object]) -> bool:
        return _static_seed_preflight.static_seed_schema_prepared(preflight)

    def sync_base_schema_contract(self) -> Dict[str, object]:
        return _typedb_lifecycle.sync_base_schema_contract(self, runtime=self._typedb_runtime())

    def save_static_seed_boxes(
        self,
        graph: PortfolioOntology,
        boxes: Iterable[str],
        rules_payload: List[Dict[str, object]] = None,
        schema_prepared: bool = False,
        tbox_metadata: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _static_seed_persistence.save_static_seed_boxes(
            self,
            graph,
            boxes,
            rules_payload,
            schema_prepared,
            tbox_metadata,
            _bindings=PersistenceBindings(
                typedb_error_code=typedb_error_code,
                typedb_operation_timeout=typedb_operation_timeout,
            ),
        )

    def save_seed_static_manifest(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
        schema_prepared: bool = False,
        tbox_metadata: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _static_seed_persistence.save_seed_static_manifest(
            self,
            graph,
            rules_payload,
            schema_prepared,
            tbox_metadata,
            _bindings=PersistenceBindings(
                typedb_error_code=typedb_error_code,
                typedb_operation_timeout=typedb_operation_timeout,
            ),
        )

    @coordinated_typedb_projection_write(
        "ontology-release-artifact-seed",
        typedb_projection_world_from_payload,
        bootstrap_schema=True,
    )
    def seed_release_artifact(self, payload: Dict[str, object]) -> Dict[str, object]:
        return _static_seed_restore.seed_release_artifact(self, payload)

    @coordinated_typedb_projection_write(
        "ontology-seed", typedb_projection_world_from_payload, bootstrap_schema=True
    )
    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        return _static_seed_bootstrap.seed_ontology(
            self, payload, _bindings=BootstrapBindings(runtime_settings=runtime_settings)
        )

    def rulebox_snapshot(self) -> Dict[str, object]:
        return _graph_writes_rulebox_read.rulebox_snapshot(
            self,
            _bindings=RuleboxSnapshotBindings(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                entity_node_kind=entity_node_kind,
                relation_type_rows_from_derivations=relation_type_rows_from_derivations,
                typedb_error_code=typedb_error_code,
            ),
        )

    @coordinated_typedb_projection_write(
        "rulebox-save", typedb_projection_world_from_payload
    )
    def save_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        return _graph_writes_rulebox_commands.save_rulebox(
            self, payload, _bindings=SaveRuleboxBindings(utc_now=utc_now)
        )

    @coordinated_typedb_projection_write("rulebox-version-append")
    def append_rulebox_version(self, version: Dict[str, object]) -> Dict[str, object]:
        return _graph_writes_rulebox_history.append_rulebox_version(
            self,
            version,
            _bindings=AppendRuleboxVersionBindings(typedb_error_code=typedb_error_code),
        )

    def restore_rulebox_version(
        self, version_id: str, change_reason: str = "", author: str = ""
    ) -> Dict[str, object]:
        return _graph_writes_rulebox_commands.restore_rulebox_version(
            self, version_id, change_reason, author
        )

    def ensure_rulebox_version_baseline(self, author: str = "") -> Dict[str, object]:
        return _graph_writes_rulebox_commands.ensure_rulebox_version_baseline(
            self, author, _bindings=EnsureRuleboxVersionBaselineBindings(utc_now=utc_now)
        )

    def verify_typedb_native_any_conditions(
        self,
        driver,
        transaction_type,
        rule: GraphInferenceRule,
        source_id: str,
        timeout_seconds: float,
        scoped_manifest_only: bool,
        tx=None,
        world_id: str = "",
        evidence_read_index: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _native_execution_entry.verify_typedb_native_any_conditions(
            self,
            driver,
            transaction_type,
            rule,
            source_id,
            timeout_seconds,
            scoped_manifest_only,
            tx,
            world_id,
            evidence_read_index,
            _bindings=_native_execution_entry_ports.NativeExecutionEntryRuntime(
                typedb_error_code=typedb_error_code
            ),
        )

    def execute_typedb_native_rule_entry(
        self,
        planned: Dict[str, object],
        clean_symbols: Iterable[str],
        world_id: str,
        scoped_manifest_only: bool,
        imported,
        transaction_type,
        deadline: float,
        execution_mode: str,
        evidence_read_index: Dict[str, object] = None,
        shared_read_driver=None,
    ) -> Dict[str, object]:
        return _native_execution_entry.execute_typedb_native_rule_entry(
            self,
            planned,
            clean_symbols,
            world_id,
            scoped_manifest_only,
            imported,
            transaction_type,
            deadline,
            execution_mode,
            evidence_read_index,
            shared_read_driver,
            _bindings=_native_execution_entry_ports.NativeExecutionEntryRuntime(
                typedb_error_code=typedb_error_code
            ),
        )

    def execute_typedb_model_signal_bridge_batches(
        self,
        batches: Iterable[Dict[str, object]],
        *,
        world_id: str,
        imported,
        transaction_type,
        deadline: float,
        evidence_read_index: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _native_execution_bridge.execute_typedb_model_signal_bridge_batches(
            self,
            batches,
            world_id=world_id,
            imported=imported,
            transaction_type=transaction_type,
            deadline=deadline,
            evidence_read_index=evidence_read_index,
            _bindings=_native_execution_bridge_ports.NativeExecutionBridgeRuntime(
                typedb_error_code=typedb_error_code
            ),
        )

    @staticmethod
    def native_rule_entry_has_timeout_failure(result: Dict[str, object]) -> bool:
        return _native_execution_retry.native_rule_entry_has_timeout_failure(
            result,
            _bindings=_native_execution_retry_ports.NativeExecutionRetryRuntime(
                typedb_error_code=typedb_error_code
            ),
        )

    @staticmethod
    def native_rule_entry_has_interrupted_transaction_failure(result: Dict[str, object]) -> bool:
        return _native_execution_retry.native_rule_entry_has_interrupted_transaction_failure(
            result,
        )

    def recover_timed_out_native_rule_entry(
        self,
        primary_result: Dict[str, object],
        planned: Dict[str, object],
        clean_symbols: Iterable[str],
        world_id: str,
        scoped_manifest_only: bool,
        imported,
        transaction_type,
        deadline: float,
        execution_mode: str,
        evidence_read_index: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _native_execution_retry.recover_timed_out_native_rule_entry(
            self,
            primary_result,
            planned,
            clean_symbols,
            world_id,
            scoped_manifest_only,
            imported,
            transaction_type,
            deadline,
            execution_mode,
            evidence_read_index,
        )

    @staticmethod
    def merge_subject_fanout_matches(results: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
        return _native_execution_fanout.merge_subject_fanout_matches(
            results,
        )

    def match_typedb_native_rules_by_subject(
        self,
        rules: Iterable[GraphInferenceRule],
        target_symbols: Iterable[str],
        *,
        world_id: str,
        planner_topology: Dict[str, object] = None,
        preflight_graph: PortfolioOntology = None,
        preflight_incoming_relations_complete: bool = False,
        evidence_read_index: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _native_execution_fanout.match_typedb_native_rules_by_subject(
            self,
            rules,
            target_symbols,
            world_id=world_id,
            planner_topology=planner_topology,
            preflight_graph=preflight_graph,
            preflight_incoming_relations_complete=preflight_incoming_relations_complete,
            evidence_read_index=evidence_read_index,
        )

    def match_typedb_native_rules(
        self,
        rules: Iterable[GraphInferenceRule],
        target_symbols: Iterable[str] = None,
        world_id: str = "",
        planner_topology: Dict[str, object] = None,
        preflight_graph: PortfolioOntology = None,
        preflight_incoming_relations_complete: bool = False,
        native_rule_parallelism: int = 1,
        native_rule_target_parallelism: int = 1,
        adaptive_target_sharding_profile: Dict[str, object] = None,
        stable_abox_write_lease_held: bool = False,
        evidence_read_index: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _native_execution_matching.match_typedb_native_rules(
            self,
            rules,
            target_symbols,
            world_id,
            planner_topology,
            preflight_graph,
            preflight_incoming_relations_complete,
            native_rule_parallelism,
            native_rule_target_parallelism,
            adaptive_target_sharding_profile,
            stable_abox_write_lease_held,
            evidence_read_index,
            _bindings=_native_execution_matching_ports.NativeExecutionMatchingRuntime(
                typedb_error_code=typedb_error_code,
                typedb_native_rule_execution_incomplete_diagnostic=typedb_native_rule_execution_incomplete_diagnostic,
            ),
        )

    @staticmethod
    def abox_generation_identity(metadata: Dict[str, object]) -> Dict[str, object]:
        return _native_execution_profile.abox_generation_identity(
            metadata,
        )

    @staticmethod
    def compact_native_rule_profile_rows(
        rows: Iterable[Dict[str, object]]
    ) -> List[Dict[str, object]]:
        return _native_execution_profile.compact_native_rule_profile_rows(
            rows,
        )

    def profile_native_rule_reads(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        return _native_execution_profile.profile_native_rule_reads(
            self,
            payload,
            _bindings=_native_execution_profile_ports.NativeExecutionProfileRuntime(
                materialize_typedb_native_matches=materialize_typedb_native_matches,
                typedb_inferencebox_graph=typedb_inferencebox_graph,
            ),
        )

    def merge_native_match_rows(
        self,
        rule: GraphInferenceRule,
        query_plan: Dict[str, object],
        rows: Iterable[Dict[str, object]],
        match_index: Dict[str, Dict[str, object]],
        matches: List[Dict[str, object]],
        world_id: str = "",
    ) -> None:
        return _native_execution_context.merge_native_match_rows(
            self,
            rule,
            query_plan,
            rows,
            match_index,
            matches,
            world_id,
            _bindings=_native_execution_context_ports.NativeExecutionContextRuntime(
                typedb_native_matched_conditions=typedb_native_matched_conditions,
                typedb_static_rule_condition_context=typedb_static_rule_condition_context,
            ),
        )

    def typedb_rule_condition_context(
        self,
        rule: GraphInferenceRule,
        source_id: str,
        query_plan: Dict[str, object] = None,
        row: Dict[str, object] = None,
        world_id: str = "",
    ) -> Dict[str, object]:
        return _native_execution_context.typedb_rule_condition_context(
            self,
            rule,
            source_id,
            query_plan,
            row,
            world_id,
            _bindings=_native_execution_context_ports.NativeExecutionContextRuntime(
                typedb_native_matched_conditions=typedb_native_matched_conditions,
                typedb_static_rule_condition_context=typedb_static_rule_condition_context,
            ),
        )

    def run_rulebox_for_staged_abox(
        self,
        payload: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _native_execution_staged.run_rulebox_for_staged_abox(
            self,
            payload,
            _bindings=_native_execution_staged_ports.NativeExecutionStagedRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                typedb_projection_coordinator_summary=typedb_projection_coordinator_summary,
            ),
        )

    @coordinated_typedb_projection_write(
        "native-rule-run",
        typedb_projection_world_from_payload,
    )
    def run_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        return _native_execution_runner.run_rulebox(
            self,
            payload,
            _bindings=_native_execution_runner_ports.NativeExecutionRunnerRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository
            ),
        )

    def _run_rulebox_unlocked(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        return _native_execution_cycle._run_rulebox_unlocked(
            self,
            payload,
            _bindings=_native_execution_cycle_ports.NativeExecutionCycleRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                inference_generation_id=inference_generation_id,
                materialize_typedb_native_matches=materialize_typedb_native_matches,
                rulebox_runtime_metadata=rulebox_runtime_metadata,
                typedb_abox_inference_generation_id=typedb_abox_inference_generation_id,
                typedb_error_code=typedb_error_code,
                typedb_inferencebox_graph=typedb_inferencebox_graph,
                typedb_native_profile_metadata=typedb_native_profile_metadata,
                utc_now=utc_now,
            ),
        )

    def validate_rulebox_materialization(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        return _native_execution_validation.validate_rulebox_materialization(
            self,
            payload,
            _bindings=_native_execution_validation_ports.NativeExecutionValidationRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                materialization_preview_diff_payload=materialization_preview_diff_payload,
                typedb_error_code=typedb_error_code,
            ),
        )

    def _inference_publication_runtime(self) -> PublicationRuntime:
        return PublicationRuntime(
            settings=runtime_settings,
            now=utc_now,
            timeout=typedb_operation_timeout,
            error_code=typedb_error_code,
        )

    def write_inferencebox_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        return _inference_writer.write_inferencebox_graph(
            self,
            graph,
            runtime=self._inference_publication_runtime(),
        )

    def inference_generation_candidate_summary(
        self,
        generation_id: str,
        world_id: str = "",
    ) -> Dict[str, object]:
        return _inference_validation.inference_generation_candidate_summary(
            self,
            generation_id,
            world_id,
        )

    def validate_inference_generation_candidate(
        self,
        graph: PortfolioOntology,
        generation_id: str,
        expected_entity_count: int,
        expected_relation_count: int,
        world_id: str = "",
    ) -> Dict[str, object]:
        return _inference_validation.validate_inference_generation_candidate(
            self,
            graph,
            generation_id,
            expected_entity_count,
            expected_relation_count,
            world_id,
        )

    def activate_inference_generation(
        self,
        graph: PortfolioOntology,
        node_rows: Iterable[Dict[str, object]],
        relation_rows: Iterable[Dict[str, object]],
        world_id: str = "",
    ) -> Dict[str, object]:
        return _inference_lifecycle.activate_inference_generation(
            self,
            graph,
            node_rows,
            relation_rows,
            world_id,
            runtime=self._inference_publication_runtime(),
        )

    def prune_inferencebox_generations(self, active_generation_id: str, keep_count: int = 2, world_id: str = "") -> Dict[str, object]:
        return _inference_lifecycle.prune_inferencebox_generations(
            self,
            active_generation_id,
            keep_count,
            world_id,
        )

    def inferencebox_snapshot_from_graph(
        self,
        graph: PortfolioOntology,
        symbols: List[str] = None,
        limit: int = 80,
    ) -> Dict[str, object]:
        return _graph_reads_inference.inferencebox_snapshot_from_graph(
            self,
            graph,
            symbols,
            limit,
            _bindings=_graph_reads_inference_ports.GraphReadsInferenceRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                apply_inference_target_coverage=apply_inference_target_coverage,
                inference_generation_records=inference_generation_records,
                inference_rulebox_metadata=inference_rulebox_metadata,
                matched_condition_ids=matched_condition_ids,
                row_inference_generation_id=row_inference_generation_id,
                select_inference_generation_record=select_inference_generation_record,
                typedb_error_code=typedb_error_code,
            ),
        )

    def inferencebox_snapshot(
        self,
        symbols: List[str] = None,
        limit: int = 80,
        reset_metrics: bool = True,
        world_id: str = "",
        inference_generation_id: str = "",
        source_abox_snapshot_id: str = "",
    ) -> Dict[str, object]:
        return _graph_reads_inference.inferencebox_snapshot(
            self,
            symbols,
            limit,
            reset_metrics,
            world_id,
            inference_generation_id,
            source_abox_snapshot_id,
            _bindings=_graph_reads_inference_ports.GraphReadsInferenceRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                apply_inference_target_coverage=apply_inference_target_coverage,
                inference_generation_records=inference_generation_records,
                inference_rulebox_metadata=inference_rulebox_metadata,
                matched_condition_ids=matched_condition_ids,
                row_inference_generation_id=row_inference_generation_id,
                select_inference_generation_record=select_inference_generation_record,
                typedb_error_code=typedb_error_code,
            ),
        )
    def inferencebox_snapshot_from_typedb(
        self,
        clean_symbols: List[str],
        safe_limit: int,
        world_id: str = "",
        inference_generation_id: str = "",
        source_abox_snapshot_id: str = "",
    ) -> Dict[str, object]:
        return _graph_reads_inference.inferencebox_snapshot_from_typedb(
            self,
            clean_symbols,
            safe_limit,
            world_id,
            inference_generation_id,
            source_abox_snapshot_id,
            _bindings=_graph_reads_inference_ports.GraphReadsInferenceRuntime(
                NullTypeDBOntologyGraphRepository=NullTypeDBOntologyGraphRepository,
                apply_inference_target_coverage=apply_inference_target_coverage,
                inference_generation_records=inference_generation_records,
                inference_rulebox_metadata=inference_rulebox_metadata,
                matched_condition_ids=matched_condition_ids,
                row_inference_generation_id=row_inference_generation_id,
                select_inference_generation_record=select_inference_generation_record,
                typedb_error_code=typedb_error_code,
            ),
        )

    def load_graph_from_typedb(self, boxes: Iterable[str] = None, world_id: str = "") -> PortfolioOntology:
        return _graph_reads_inference.load_graph_from_typedb(
            self,
            boxes,
            world_id,
        )

    def projection_graph_for_native_matches(
        self,
        projection_graph: PortfolioOntology,
        native_match_result: Dict[str, object],
        rules: Iterable[GraphInferenceRule] = None,
        evidence_read_index: Dict[str, object] = None,
    ) -> Dict[str, object]:
        return _native_execution_evidence.projection_graph_for_native_matches(
            self,
            projection_graph,
            native_match_result,
            rules,
            evidence_read_index,
        )

    def load_graph_for_native_matches(
        self,
        native_match_result: Dict[str, object],
        rules: Iterable[GraphInferenceRule] = None,
        include_all_rule_relation_types: bool = False,
        include_incoming_relations: bool = True,
        evidence_read_index: Dict[str, object] = None,
        world_id: str = "",
    ) -> PortfolioOntology:
        return _native_execution_evidence.load_graph_for_native_matches(
            self,
            native_match_result,
            rules,
            include_all_rule_relation_types,
            include_incoming_relations,
            evidence_read_index,
            world_id,
        )

    def save_rule_change_candidates(
        self, candidates: List[Dict[str, object]], context: Dict[str, object] = None
    ) -> Dict[str, object]:
        return _graph_writes_rulebox_history.save_rule_change_candidates(
            self, candidates, context
        )


def normalized_boxes(boxes: Iterable[str] = None) -> List[str]:
    values = [str(item or "").strip() for item in (boxes or []) if str(item or "").strip()]
    return sorted(set(values or ["ABox"]))


def endpoint_node_row(row: Dict[str, object], prefix: str, box: str) -> Dict[str, object]:
    properties = json_object(row.get(prefix + "Json"))
    return {
        **properties,
        "id": str(row.get(prefix + "Id") or ""),
        "label": str(row.get(prefix + "Label") or row.get(prefix + "Id") or ""),
        "kind": str(row.get(prefix + "Kind") or properties.get("kind") or "observation"),
        "ontologyBox": str(box or properties.get("ontologyBox") or "ABox"),
        "symbol": str(properties.get("symbol") or ""),
        "tboxClass": str(properties.get("tboxClass") or ""),
        "updatedAt": str(row.get(prefix + "UpdatedAt") or properties.get("updatedAt") or ""),
        "propertiesJson": json.dumps(properties, ensure_ascii=False, sort_keys=True),
    }


def relation_type_rows_from_derivations(
    entity_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
) -> List[Dict[str, object]]:
    values = set()
    for row in entity_rows or []:
        if entity_node_kind(row) == "relation-template":
            values.add(str(row.get("derivationRelationType") or row.get("relationType") or "").upper())
    for row in relation_rows or []:
        if row.get("ontologyBox") == "RuleBox":
            values.add(str(row.get("type") or row.get("relationType") or "").upper())
    return [{"relationType": item} for item in sorted(value for value in values if value)]


def typedb_native_profile_metadata(native_profile: Dict[str, object]) -> Dict[str, object]:
    profile = dict(native_profile or {})
    return {
        "reasoningMode": TYPEDB_NATIVE_REASONING_MODE,
        "materializationSource": TYPEDB_NATIVE_MATERIALIZATION_SOURCE,
        "reasoningLayer": TYPEDB_NATIVE_REASONING_LAYER,
        "typedbNativeRuleEngineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
        "typedbNativeRuleProfileVersion": str(profile.get("version") or TYPEDB_NATIVE_REASONING_PROFILE_VERSION),
        "typedbNativeRuleProfileStatus": str(profile.get("status") or ""),
        "typedbNativeRuleCount": int(number_or_none(profile.get("ruleCount")) or 0),
        "typedbNativeReadyRuleCount": int(number_or_none(profile.get("readyRuleCount")) or 0),
        "typedbNativePartialRuleCount": int(number_or_none(profile.get("partialRuleCount")) or 0),
        "typedbNativeBlockedRuleCount": int(number_or_none(profile.get("blockedRuleCount")) or 0),
        "typedbNativeRuleMaterializationUsed": True,
        "typedbDirectTypeqlMaterializationUsed": True,
        "typeDbNativeRulesPrimary": True,
        "ruleStore": "TypeDB direct TypeQL",
    }


def materialize_typedb_native_matches(
    graph: PortfolioOntology,
    rules: Iterable[GraphInferenceRule],
    native_matches: Dict[str, object],
) -> None:
    entities_by_id = {item.entity_id: item for item in graph.entities}
    rules_by_id = {str(rule.rule_id or ""): rule for rule in (rules or [])}
    # Native TypeDB has already identified the matching facts. Build the
    # ABox relation lookup once for this generation so per-rule explanation
    # grounding does not repeatedly rescan the full graph.
    evidence_index = evidence_relation_index(graph)
    for match in native_matches.get("matches") or []:
        if not isinstance(match, dict):
            continue
        rule = rules_by_id.get(str(match.get("ruleId") or ""))
        subject = entities_by_id.get(str(match.get("sourceId") or ""))
        if not rule or not subject:
            continue
        materialize_rule_inference(graph, rule, subject, {
            "matchedConditions": list(match.get("matchedConditions") or []),
            "evidenceRelationIds": list(match.get("evidenceRelationIds") or []),
            "conditionDetailSource": str(match.get("conditionDetailSource") or "direct-typeql-match"),
            "modelSignalInterpretationPolicy": bool(match.get("modelSignalInterpretationPolicy")),
            "modelSignalInterpretationPolicyId": str(match.get("modelSignalInterpretationPolicyId") or ""),
            "sharedModelSignalBridge": bool(match.get("sharedModelSignalBridge")),
            "modelSignalBridgeVersion": str(match.get("modelSignalBridgeVersion") or ""),
            "bridgeSourceScope": str(match.get("bridgeSourceScope") or ""),
            "typeqlExecutionMode": "direct-typeql",
        }, evidence_index=evidence_index)


def typedb_native_matched_conditions(
    rule: GraphInferenceRule,
    row: Dict[str, object],
    query_plan: Dict[str, object],
) -> List[Dict[str, object]]:
    result = []
    evidence_by_condition = dict(query_plan.get("conditionEvidenceColumns") or {})
    verified_any_condition_ids = {
        str(item or "")
        for item in (row or {}).get("_matchedAnyConditionIds") or []
        if str(item or "")
    }
    any_group_verified = bool((row or {}).get("_anyConditionsVerified"))
    any_group_added = False
    interpretation_policy = bool(query_plan.get("modelSignalInterpretationPolicy"))
    shared_bridge = bool(query_plan.get("sharedModelSignalBridge"))
    bridge_condition_ids = {
        str(item or "")
        for item in query_plan.get("bridgeConditionIds") or []
        if str(item or "")
    }
    residual_condition_ids = {
        str(item or "")
        for item in query_plan.get("residualConditionIds") or []
        if str(item or "")
    }
    for condition in getattr(rule, "conditions", []) or []:
        condition_role = condition.role or "required"
        if (
            condition_role in {"any", "optional"}
            and any_group_verified
        ):
            if not any_group_added:
                result.append({
                    "conditionId": "any-group:" + str(rule.rule_id or ""),
                    "kind": "any-condition-group",
                    "role": "any",
                    "minimumCount": max(1, int(number_or_none(getattr(rule, "any_condition_min_count", 1)) or 1)),
                    "matchedByTypeDB": True,
                    "detailDeferred": not bool(verified_any_condition_ids),
                })
                any_group_added = True
            if condition.condition_id not in verified_any_condition_ids:
                continue
        if (
            condition_role in {"any", "optional"}
            and verified_any_condition_ids
            and condition.condition_id not in verified_any_condition_ids
        ):
            continue
        payload = {
            "conditionId": condition.condition_id,
            "kind": condition.kind,
            "role": condition_role,
            "matchedByTypeDB": True,
        }
        if interpretation_policy:
            payload["matchedByModelSignalInterpretationPolicy"] = True
        if shared_bridge and condition.condition_id in bridge_condition_ids:
            payload["matchedBySharedModelSignalBridge"] = True
        if shared_bridge and condition.condition_id in residual_condition_ids:
            payload["matchedByInterpretationPolicyQuery"] = True
        evidence_column = evidence_by_condition.get(condition.condition_id)
        if evidence_column:
            payload["relationId"] = str(row.get(evidence_column) or "")
        if condition.kind == "subject_property":
            payload.update({"field": condition.field, "operator": condition.operator, "value": condition.value})
        elif condition.kind == "relation":
            payload.update({"relationType": condition.relation_type})
        result.append(payload)
    return result


def typedb_static_rule_condition_context(
    rule: GraphInferenceRule,
    query_plan: Dict[str, object] = None,
    row: Dict[str, object] = None,
) -> Dict[str, object]:
    query_plan = dict(query_plan or {})
    row = dict(row or {})
    matched_conditions = typedb_native_matched_conditions(rule, row, query_plan)
    if not matched_conditions:
        for condition in getattr(rule, "conditions", []) or []:
            role = str(getattr(condition, "role", "") or "required")
            if role in {"optional", "any"}:
                continue
            payload = {
                "conditionId": getattr(condition, "condition_id", ""),
                "kind": getattr(condition, "kind", ""),
                "role": role,
                "matchedByTypeDB": True,
            }
            if role == "not":
                payload["absenceSatisfied"] = True
            if getattr(condition, "kind", "") == "subject_property":
                payload.update({
                    "field": getattr(condition, "field", ""),
                    "operator": getattr(condition, "operator", ""),
                    "value": getattr(condition, "value", None),
                })
            elif getattr(condition, "kind", "") == "relation":
                payload.update({
                    "relationType": getattr(condition, "relation_type", ""),
                })
            matched_conditions.append(payload)
    evidence_relation_ids = [
        str(row.get(column) or "")
        for column in (query_plan.get("evidenceColumns") or [])
        if str(row.get(column) or "").strip()
    ]
    return {
        "matchedConditions": matched_conditions,
        "evidenceRelationIds": sorted(set(evidence_relation_ids)),
        "conditionDetailSource": (
            "typedb-model-signal-interpretation-policy"
            if query_plan.get("modelSignalInterpretationPolicy")
            else "direct-typeql-match"
        ),
    }


def typedb_inferencebox_graph(
    graph: PortfolioOntology,
    generation_id: str = None,
    generation_at: str = None,
    rulebox_metadata: Dict[str, object] = None,
) -> PortfolioOntology:
    generation_id = str(generation_id or inference_generation_id())
    generation_at = str(generation_at or utc_now())
    rulebox_metadata = dict(rulebox_metadata or {})
    reasoning_mode = str(rulebox_metadata.get("reasoningMode") or TYPEDB_NATIVE_REASONING_MODE)
    materialization_source = str(rulebox_metadata.get("materializationSource") or TYPEDB_NATIVE_MATERIALIZATION_SOURCE)
    source_worldview = dict(getattr(graph, "worldview", {}) or {})
    world_context = {
        key: source_worldview.get(key)
        for key in [
            "ontologyWorldVersion",
            "worldId",
            "worldType",
            "tenantId",
            "accountId",
            "marketId",
            "marketContextMode",
        ]
        if source_worldview.get(key) not in (None, "", [], {})
    }
    inference_graph = PortfolioOntology(str(graph.portfolio_id or "typedb-inferencebox"))
    id_map: Dict[str, str] = {}
    for item in graph.entities:
        if str((item.properties or {}).get("ontologyBox") or "") == "InferenceBox":
            id_map[item.entity_id] = generated_inference_id(item.entity_id, generation_id)
    for item in graph.evidence:
        if str((item.value or {}).get("ontologyBox") or ("InferenceBox" if item.kind == "inference-trace" else "")) == "InferenceBox":
            id_map[item.evidence_id] = generated_inference_id(item.evidence_id, generation_id)
    inference_graph.entities = [
        OntologyEntity(
            id_map.get(item.entity_id, item.entity_id),
            item.label,
            item.kind,
            typedb_reasoned_properties(item.properties, generation_id, generation_at, item.entity_id, rulebox_metadata),
        )
        for item in graph.entities
        if str((item.properties or {}).get("ontologyBox") or "") == "InferenceBox"
    ]
    inference_graph.relations = [
        OntologyRelation(
            id_map.get(item.source, item.source),
            id_map.get(item.target, item.target),
            item.relation_type,
            item.weight,
            [id_map.get(value, value) for value in list(item.evidence_ids or [])],
            typedb_reasoned_properties(item.properties, generation_id, generation_at, rulebox_metadata=rulebox_metadata),
        )
        for item in graph.relations
        if str((item.properties or {}).get("ontologyBox") or "") == "InferenceBox"
    ]
    inference_graph.evidence = [
        OntologyEvidence(
            id_map.get(item.evidence_id, item.evidence_id),
            id_map.get(item.subject, item.subject),
            item.kind,
            item.source,
            item.summary,
            typedb_reasoned_properties(item.value, generation_id, generation_at, item.evidence_id, rulebox_metadata),
            item.evidence_role,
            item.data_state,
        )
        for item in graph.evidence
        if str((item.value or {}).get("ontologyBox") or ("InferenceBox" if item.kind == "inference-trace" else "")) == "InferenceBox"
    ]
    inference_graph.beliefs = []
    inference_graph.worldview = {
        **world_context,
        "reasoningMode": reasoning_mode,
        "materializationSource": materialization_source,
        "inferenceGenerationId": generation_id,
        "inferenceGenerationAt": generation_at,
        **rulebox_metadata,
    }
    ensure_inference_reference_entities(inference_graph, graph)
    return dedupe_inferencebox_graph(inference_graph)


def ensure_inference_reference_entities(
    inference_graph: PortfolioOntology,
    source_graph: PortfolioOntology,
) -> None:
    """Materialize external inference endpoints into the same generation.

    Native rule materialization creates paths such as ``stock -> trace`` and
    ``rule -> trace``.  The source stock and rule remain in ABox/RuleBox, but
    a TypeDB assertion in an immutable InferenceBox generation must resolve
    both of its endpoint nodes in that generation.  Persisting a compact
    reference node keeps the inference path self-contained and avoids linking
    a new result to a stale ABox generation.
    """
    worldview = dict(getattr(inference_graph, "worldview", {}) or {})
    generation_id = str(worldview.get("inferenceGenerationId") or "")
    generation_at = str(worldview.get("inferenceGenerationAt") or "")
    source_entities = {
        str(item.entity_id or ""): item
        for item in list(getattr(source_graph, "entities", []) or [])
        if str(item.entity_id or "")
    }
    known_ids = {
        str(item.entity_id or "")
        for item in list(getattr(inference_graph, "entities", []) or [])
        if str(item.entity_id or "")
    }
    known_ids.update(
        str(item.evidence_id or "")
        for item in list(getattr(inference_graph, "evidence", []) or [])
        if str(item.evidence_id or "")
    )
    known_ids.update(
        str(item.belief_id or "")
        for item in list(getattr(inference_graph, "beliefs", []) or [])
        if str(item.belief_id or "")
    )

    endpoints = set()
    native_rule_ids_by_endpoint: Dict[str, List[str]] = {}
    for relation in list(getattr(inference_graph, "relations", []) or []):
        relation_endpoints = [str(relation.source or ""), str(relation.target or "")]
        endpoints.update(relation_endpoints)
        relation_properties = dict(getattr(relation, "properties", {}) or {})
        native_rule_id = str(
            relation_properties.get("nativeRuleId")
            or typedb_native_rule_id(relation_properties.get("ruleId"))
            or ""
        ).strip()
        if native_rule_id:
            for endpoint_id in relation_endpoints:
                if endpoint_id and native_rule_id not in native_rule_ids_by_endpoint.setdefault(endpoint_id, []):
                    native_rule_ids_by_endpoint[endpoint_id].append(native_rule_id)
    for evidence in list(getattr(inference_graph, "evidence", []) or []):
        endpoints.add(str(evidence.subject or ""))
    for belief in list(getattr(inference_graph, "beliefs", []) or []):
        endpoints.add(str(belief.subject or ""))

    for endpoint_id in sorted(value for value in endpoints if value and value not in known_ids):
        source = source_entities.get(endpoint_id)
        source_properties = dict((source.properties if source else {}) or {})
        source_kind = str((source.kind if source else "") or endpoint_id.split(":", 1)[0] or "reference")
        source_box = str(source_properties.get("ontologyBox") or "external")
        symbol = str(source_properties.get("symbol") or symbol_from_subject(endpoint_id) or "").upper()
        label = str((source.label if source else "") or endpoint_id)
        native_rule_ids = sorted(native_rule_ids_by_endpoint.get(endpoint_id, []))
        inference_graph.entities.append(OntologyEntity(
            endpoint_id,
            label,
            "inference-context-reference",
            typedb_reasoned_properties({
                "ontologyBox": "InferenceBox",
                "tboxClass": "InferenceContextReference",
                "tboxClasses": ["InferenceContextReference"],
                "symbol": symbol,
                "referenceEntityId": endpoint_id,
                "referenceEntityKind": source_kind,
                "referenceOntologyBox": source_box,
                "referenceOnly": True,
                "nativeRuleId": native_rule_ids[0] if native_rule_ids else "",
                "nativeRuleIds": native_rule_ids,
            }, generation_id, generation_at, endpoint_id, worldview),
        ))
        known_ids.add(endpoint_id)


def dedupe_inferencebox_graph(graph: PortfolioOntology) -> PortfolioOntology:
    entities_by_id: Dict[str, OntologyEntity] = {}
    for item in graph.entities:
        existing = entities_by_id.get(item.entity_id)
        if existing is None:
            entities_by_id[item.entity_id] = item
            continue
        existing.properties = merge_ontology_properties(existing.properties, item.properties)

    relations_by_id: Dict[str, OntologyRelation] = {}
    for item in graph.relations:
        row_id = relation_row_id({
            "source": item.source,
            "target": item.target,
            "type": item.relation_type,
            "ontologyBox": (item.properties or {}).get("ontologyBox"),
            "snapshotId": (item.properties or {}).get("snapshotId"),
            "aboxSnapshotId": (item.properties or {}).get("aboxSnapshotId"),
            "ruleId": (item.properties or {}).get("ruleId"),
        })
        existing = relations_by_id.get(row_id)
        if existing is None:
            relations_by_id[row_id] = item
            continue
        existing.weight = 1.0
        existing.evidence_ids = list(dict.fromkeys(list(existing.evidence_ids or []) + list(item.evidence_ids or [])))
        existing.properties = merge_ontology_properties(existing.properties, item.properties)

    evidence_by_id: Dict[str, OntologyEvidence] = {}
    for item in graph.evidence:
        existing = evidence_by_id.get(item.evidence_id)
        if existing is None:
            evidence_by_id[item.evidence_id] = item
            continue
        existing.value = merge_ontology_properties(existing.value, item.value)
        if existing.evidence_role == "context" and item.evidence_role != "context":
            existing.evidence_role = item.evidence_role
        if existing.data_state == "sufficient" and item.data_state != "sufficient":
            existing.data_state = item.data_state

    return PortfolioOntology(
        graph.portfolio_id,
        entities=list(entities_by_id.values()),
        relations=list(relations_by_id.values()),
        evidence=list(evidence_by_id.values()),
        beliefs=list(graph.beliefs or []),
        opinions=list(graph.opinions or []),
        reasoning_cards=list(graph.reasoning_cards or []),
        worldview=dict(graph.worldview or {}),
        prompt=graph.prompt,
    )


def merge_ontology_properties(left: Dict[str, object], right: Dict[str, object]) -> Dict[str, object]:
    merged = dict(left or {})
    for key, value in dict(right or {}).items():
        if value in (None, "", [], {}):
            continue
        current = merged.get(key)
        if current in (None, "", [], {}):
            merged[key] = value
        elif isinstance(current, list) and isinstance(value, list):
            merged[key] = list(dict.fromkeys(current + value))
    return merged


def typedb_reasoned_properties(
    properties: Dict[str, object],
    generation_id: str = "",
    generation_at: str = "",
    original_id: str = "",
    rulebox_metadata: Dict[str, object] = None,
) -> Dict[str, object]:
    payload = dict(properties or {})
    for key, value in dict(rulebox_metadata or {}).items():
        if value not in (None, "", [], {}):
            payload.setdefault(key, value)
    source_rule_id = str(payload.get("sourceRuleId") or payload.get("ruleId") or "").strip()
    native_rule_id = str(payload.get("nativeRuleId") or typedb_native_rule_id(source_rule_id)).strip()
    if source_rule_id:
        payload.setdefault("sourceRuleId", source_rule_id)
    if native_rule_id:
        payload.setdefault("nativeRuleId", native_rule_id)
        payload.setdefault("semanticRuleId", native_rule_id)
    payload.setdefault("ontologyBox", "InferenceBox")
    payload.setdefault("box", "InferenceBox")
    payload["nativeTypeDbReasoned"] = True
    payload["typedbNativeRuleReasoned"] = True
    payload["typedbNativeRuleMaterializationUsed"] = True
    payload["typedbDirectTypeqlReasoned"] = True
    payload["typedbDirectTypeqlMaterializationUsed"] = True
    payload["typeDbMaterialized"] = True
    payload["graphInferenceUsed"] = True
    payload["typedbMaterialized"] = True
    payload["reasoningMode"] = str((rulebox_metadata or {}).get("reasoningMode") or TYPEDB_NATIVE_REASONING_MODE)
    payload["materializationSource"] = str((rulebox_metadata or {}).get("materializationSource") or TYPEDB_NATIVE_MATERIALIZATION_SOURCE)
    payload.setdefault("reasoningLayer", str((rulebox_metadata or {}).get("reasoningLayer") or TYPEDB_NATIVE_REASONING_LAYER))
    payload.setdefault("typedbNativeRuleEngineVersion", TYPEDB_NATIVE_RULE_ENGINE_VERSION)
    if generation_id:
        payload["inferenceGenerationId"] = generation_id
        payload["snapshotId"] = generation_id
        payload["aboxSnapshotId"] = generation_id
    if generation_at:
        payload["inferenceGenerationAt"] = generation_at
        payload["asOf"] = generation_at
    if original_id:
        payload["originalId"] = original_id
    return payload


def inference_rulebox_metadata(
    entity_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
) -> Dict[str, object]:
    keys = [
        "ruleboxRulesHash",
        "ruleboxShortHash",
        "ruleboxRuleCount",
        "ruleboxConditionCount",
        "ruleboxDerivationCount",
        "ruleboxEngineVersion",
        "reasoningMode",
        "materializationSource",
        "reasoningLayer",
        "typedbNativeRuleEngineVersion",
        "typedbNativeRuleProfileVersion",
        "typedbNativeRuleProfileStatus",
        "typedbNativeRuleCount",
        "typedbNativeReadyRuleCount",
        "typedbNativePartialRuleCount",
        "typedbNativeBlockedRuleCount",
        "typedbNativeRuleQueryStatus",
        "typedbNativeRuleQueryUsed",
        "typedbDirectTypeqlQueryUsed",
        "typedbNativeIndexedRuleQueryUsed",
        "typedbNativeEvidenceFieldIndexStatus",
        "typedbNativeEvidenceFieldIndexChunkCount",
        "typedbNativeEvidenceFieldIndexStorageIdentityCount",
        "typedbNativeEvidenceFieldIndexFieldRowCount",
        "typedbNativeEvidenceFieldIndexRelationTypes",
        "typedbDirectTypeqlUsed",
        "typedbNativeIndexedRuleCandidateCount",
        "typedbNativeIndexedRuleCandidateIds",
        "typedbNativeRuleMatchedCount",
        "typedbNativeRuleMatchedRuleIds",
        "typedbNativeRuleExecutedCount",
        "typedbNativeRuleSkippedCount",
        "nativeInferenceEvaluationComplete",
        "coreNativeInferenceEvaluationComplete",
        "nativeCoverageStatus",
        "supportingRuleFailureCount",
        "supportingRuleFailures",
        "nativeInferenceOutcome",
        "nativeInferenceNoMatch",
        "pythonCompatibilityReasonerUsed",
        "typeDbNativeRulesPrimary",
        "ruleStore",
        "sourceAboxSnapshotId",
        "sourceAboxSnapshotCount",
        "targetSymbols",
        "ruleTargetSymbols",
        "reasoningSubjectKinds",
        "reasoningSubjectIds",
        "reasoningSubjectFilterApplied",
        "reasoningSubjectAllowedSourceKinds",
        "reasoningSubjectFullRuleCount",
        "reasoningSubjectSelectedRuleCount",
        "incrementalScope",
        "impactPlanVersion",
        "inferenceImpactPlan",
        "ruleExecutionScope",
        "nativeRuleSelectionApplied",
        "nativeRuleSelectionFallbackReason",
        "nativeRuleSelectionCandidateCount",
        "nativeRuleSelectionPriorMatchedCount",
        "nativeRuleSelectionExecutedCount",
        "nativeRuleSelectionDeferredCount",
        "nativeRuleSelectionFullRuleCount",
        "nativeRuleSelectionExecutedRuleIds",
        "nativeRuleSelectionDeferredRuleIds",
        "typedbNativeRuleTimingProfile",
        "typedbNativeStageTimings",
        "matchedGraphSource",
        "matchedGraphReuseStatus",
        "matchedGraphReuseReason",
        "worldPartitionedReasoningVersion",
        "ruleExecutionPhase",
        "sourceRuleCount",
        "sharedPremiseRuleCount",
        "accountOverlayRuleCount",
        "mixedRuleCount",
        "marketReadMirrorRemoved",
    ]
    metadata: Dict[str, object] = {}
    for row in list(entity_rows or []) + list(relation_rows or []):
        if not isinstance(row, dict):
            continue
        props = json_object(row.get("propertiesJson"))
        props.update(json_object(row.get("valueJson")))
        source = {**props, **row}
        for key in keys:
            value = source.get(key)
            if value not in (None, "", [], {}) and key not in metadata:
                metadata[key] = value
        if all(key in metadata for key in keys):
            break
    for key in [
        "ruleboxRuleCount",
        "ruleboxConditionCount",
        "ruleboxDerivationCount",
        "typedbNativeRuleCount",
        "typedbNativeReadyRuleCount",
        "typedbNativePartialRuleCount",
        "typedbNativeBlockedRuleCount",
        "typedbNativeRuleMatchedCount",
        "typedbNativeRuleExecutedCount",
        "typedbNativeRuleSkippedCount",
        "sourceAboxSnapshotCount",
        "nativeRuleSelectionCandidateCount",
        "nativeRuleSelectionPriorMatchedCount",
        "nativeRuleSelectionExecutedCount",
        "nativeRuleSelectionDeferredCount",
        "nativeRuleSelectionFullRuleCount",
        "supportingRuleFailureCount",
        "sourceRuleCount",
        "sharedPremiseRuleCount",
        "accountOverlayRuleCount",
        "mixedRuleCount",
    ]:
        if key in metadata:
            metadata[key] = int(number_or_none(metadata.get(key)) or 0)
    return metadata


def native_inference_decision_eligible(metadata: Dict[str, object]) -> bool:
    """Accept a complete core while preserving support-only coverage gaps."""
    values = dict(metadata or {})
    if typedb_bool(values.get("nativeInferenceEvaluationComplete")):
        return True
    return bool(
        typedb_bool(values.get("coreNativeInferenceEvaluationComplete"))
        and str(values.get("nativeCoverageStatus") or "").strip().lower()
        == "core-complete-supporting-partial"
    )


def row_inference_generation_id(row: Dict[str, object]) -> str:
    if not isinstance(row, dict):
        return ""
    direct = str(row.get("inferenceGenerationId") or row.get("snapshotId") or row.get("aboxSnapshotId") or "").strip()
    if direct:
        return direct
    try:
        props = json.loads(str(row.get("propertiesJson") or "{}"))
    except json.JSONDecodeError:
        props = {}
    return str((props or {}).get("inferenceGenerationId") or (props or {}).get("snapshotId") or "").strip()


def row_inference_generation_at(row: Dict[str, object]) -> str:
    if not isinstance(row, dict):
        return ""
    direct = str(row.get("inferenceGenerationAt") or row.get("asOf") or row.get("updatedAt") or "").strip()
    if direct:
        return direct
    try:
        props = json.loads(str(row.get("propertiesJson") or "{}"))
    except json.JSONDecodeError:
        props = {}
    return str((props or {}).get("inferenceGenerationAt") or (props or {}).get("asOf") or "").strip()


def row_source_abox_snapshot_id(row: Dict[str, object]) -> str:
    """Read source ABox provenance from a mapped TypeDB InferenceBox row."""
    if not isinstance(row, dict):
        return ""
    direct = str(row.get("sourceAboxSnapshotId") or "").strip()
    if direct:
        return direct
    try:
        properties = json.loads(str(row.get("propertiesJson") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        properties = {}
    return str((properties or {}).get("sourceAboxSnapshotId") or "").strip()


def inference_generation_marker_row(
    graph: PortfolioOntology,
    node_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
    publication_status: str = "candidate",
) -> Dict[str, object]:
    return _inference_markers.inference_generation_marker_row(
        graph, node_rows, relation_rows, publication_status, now=utc_now,
    )


def inference_marker_is_active(raw_json: object) -> bool:
    try:
        payload = json.loads(str(raw_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    status = str((payload or {}).get("publicationStatus") or "").strip().lower()
    return status in {"", "active", "published"}


def inference_generation_records(
    entity_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
) -> List[Dict[str, object]]:
    records: Dict[str, Dict[str, object]] = {}
    for row in list(entity_rows or []) + list(relation_rows or []):
        generation_id = row_inference_generation_id(row)
        if not generation_id:
            continue
        record = records.setdefault(generation_id, {
            "generationId": generation_id,
            "latestAt": "",
            "entityCount": 0,
            "relationCount": 0,
            "sourceAboxSnapshotIds": [],
        })
        source_abox_snapshot_id = row_source_abox_snapshot_id(row)
        if source_abox_snapshot_id and source_abox_snapshot_id not in record["sourceAboxSnapshotIds"]:
            record["sourceAboxSnapshotIds"].append(source_abox_snapshot_id)
        latest_at = row_inference_generation_at(row)
        if latest_at > str(record.get("latestAt") or ""):
            record["latestAt"] = latest_at
        if "relationType" in row or "type" in row and row.get("source"):
            record["relationCount"] = int(record.get("relationCount") or 0) + 1
        else:
            record["entityCount"] = int(record.get("entityCount") or 0) + 1
    for record in records.values():
        source_ids = sorted(str(value or "") for value in record.get("sourceAboxSnapshotIds") or [] if str(value or ""))
        record["sourceAboxSnapshotIds"] = source_ids
        record["sourceAboxSnapshotId"] = source_ids[0] if len(source_ids) == 1 else ""
    return sorted(records.values(), key=lambda item: str(item.get("latestAt") or ""), reverse=True)


def select_inference_generation_record(
    records: Iterable[Dict[str, object]],
    active_abox_snapshot_id: str = "",
) -> Dict[str, object]:
    """Select a single generation, preferring verified active-ABox provenance.

    This is only used as a compatibility read path when an old TypeDB runtime
    omitted its active-generation marker. A source ABox match is stronger
    evidence than timestamp ordering and prevents old/new inference rows from
    being interpreted as one result.
    """
    candidates = [dict(record or {}) for record in records or [] if str((record or {}).get("generationId") or "").strip()]
    active = str(active_abox_snapshot_id or "").strip()
    if active:
        aligned = [
            record for record in candidates
            if active in {
                str(value or "").strip()
                for value in record.get("sourceAboxSnapshotIds") or []
            }
            or str(record.get("sourceAboxSnapshotId") or "").strip() == active
        ]
        if not aligned:
            return {}
        candidates = aligned
    if not candidates:
        return {}
    return sorted(
        candidates,
        key=lambda record: (
            str(record.get("latestAt") or ""),
            str(record.get("generationId") or ""),
        ),
        reverse=True,
    )[0]


def active_inference_generation(
    entity_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
) -> Dict[str, object]:
    records = inference_generation_records(entity_rows, relation_rows)
    return records[0] if records else {}


def entity_node_kind(row: Dict[str, object]) -> str:
    return str(row.get("nodeKind") or row.get("kind") or "")


def rule_id_from_value(value: object) -> str:
    raw = str(value or "")
    if ":graph." in raw:
        return raw.split(":", 2)[-1]
    return ""


def matched_condition_ids(row: Dict[str, object]) -> List[str]:
    try:
        properties = json.loads(str(row.get("propertiesJson") or "{}"))
    except json.JSONDecodeError:
        properties = {}
    matches = properties.get("matchedConditions") if isinstance(properties, dict) else []
    return [
        str(item.get("conditionId") or "")
        for item in (matches or [])
        if isinstance(item, dict) and str(item.get("conditionId") or "")
    ]


def typedb_repository_from_settings(settings: Dict[str, str] = None):
    settings = settings or runtime_settings()
    enabled = str(settings.get("ontologyTypeDbEnabled") or "0").strip().lower() not in {"0", "false", "no", "off"}
    address = str(settings.get("typedbAddress") or "").strip()
    if not enabled or not address:
        return NullTypeDBOntologyGraphRepository()
    timeout_seconds = int(settings.get("typedbTimeoutSeconds") or 20)
    query_metrics_value = settings.get("typedbQueryMetricsEnabled")
    native_execution_value = settings.get("ontologyReasoningTypeDbNativeRuleExecutionEnabled")
    if native_execution_value in (None, ""):
        native_execution_value = settings.get("typedbNativeRuleExecutionEnabled")
    return TypeDBOntologyGraphRepository(
        address=address,
        http_address=str(settings.get("typedbHttpAddress") or ""),
        user=str(settings.get("typedbUser") or "admin"),
        password=str(settings.get("typedbPassword") or "password"),
        database=str(settings.get("typedbDatabase") or "orbit_alpha_ontology"),
        tls_enabled=typedb_bool(settings.get("typedbTlsEnabled")),
        timeout_seconds=timeout_seconds,
        retry_count=int(number_or_none(settings.get("typedbRetryCount")) or 2),
        inference_generation_keep_count=int(number_or_none(settings.get("typedbInferenceGenerationKeepCount")) or 1),
        query_timeout_seconds=number_or_none(settings.get("typedbQueryTimeoutSeconds")) or float(timeout_seconds or 20),
        schema_operation_timeout_seconds=number_or_none(settings.get("typedbSchemaOperationTimeoutSeconds")) or float(timeout_seconds or 20),
        write_operation_timeout_seconds=number_or_none(settings.get("typedbWriteOperationTimeoutSeconds")) or float(timeout_seconds or 20),
        condition_detail_queries_enabled=typedb_bool(settings.get("typedbConditionDetailQueriesEnabled")),
        query_metrics_enabled=True if query_metrics_value in (None, "") else typedb_bool(query_metrics_value),
        rulebox_snapshot_cache_seconds=number_or_none(settings.get("typedbRuleBoxSnapshotCacheSeconds")) or 60.0,
        native_rule_execution_enabled=True if native_execution_value in (None, "") else typedb_bool(native_execution_value),
        native_rule_query_timeout_seconds=number_or_none(settings.get("typedbNativeRuleQueryTimeoutSeconds"))
        or DEFAULT_TYPEDB_NATIVE_RULE_QUERY_TIMEOUT_SECONDS,
        native_rule_dedicated_read_driver_enabled=True
        if settings.get("typedbNativeRuleDedicatedReadDriverEnabled") in (None, "")
        else typedb_bool(settings.get("typedbNativeRuleDedicatedReadDriverEnabled")),
        native_rule_execution_budget_seconds=number_or_none(settings.get("typedbNativeRuleExecutionBudgetSeconds"))
        or DEFAULT_TYPEDB_NATIVE_RULE_EXECUTION_BUDGET_SECONDS,
        native_rule_parallelism=int(number_or_none(settings.get("typedbNativeRuleParallelism"))
        or DEFAULT_TYPEDB_NATIVE_RULE_PARALLELISM),
        native_rule_target_parallelism=int(number_or_none(
            settings.get("typedbNativeRuleTargetParallelism")
        ) or DEFAULT_TYPEDB_NATIVE_RULE_TARGET_PARALLELISM),
        native_rule_subject_fanout_enabled=typedb_bool(
            settings.get("typedbNativeRuleSubjectFanoutEnabled")
        ),
        native_rule_subject_parallelism=int(number_or_none(
            settings.get("typedbNativeRuleSubjectParallelism")
        ) or 2),
        native_rule_total_read_parallelism=int(number_or_none(
            settings.get("typedbNativeRuleTotalReadParallelism")
        ) or 4),
        native_rule_target_work_sharding_enabled=typedb_bool(
            settings.get("typedbNativeRuleTargetWorkShardingEnabled")
        ),
        native_rule_adaptive_target_sharding_enabled=True
        if settings.get("typedbNativeRuleAdaptiveTargetShardingEnabled") in (None, "")
        else typedb_bool(settings.get("typedbNativeRuleAdaptiveTargetShardingEnabled")),
        native_rule_any_condition_parallelism=int(number_or_none(
            settings.get("typedbNativeRuleAnyConditionParallelism")
        ) or 1),
        native_rule_durable_preflight_fallback_enabled=typedb_bool(
            settings.get("typedbNativeRuleDurablePreflightFallbackEnabled")
        ),
        inference_write_lease_enabled=True
        if settings.get("typedbInferenceWriteLeaseEnabled") in (None, "")
        else typedb_bool(settings.get("typedbInferenceWriteLeaseEnabled")),
        projection_coordinator_write_enforced=typedb_bool(
            settings.get("typedbProjectionCoordinatorEnabled", "1")
        ),
        persistent_driver_enabled=True
        if settings.get("typedbPersistentDriverEnabled") in (None, "")
        else typedb_bool(settings.get("typedbPersistentDriverEnabled")),
        fresh_candidate_rebuild=typedb_bool(
            settings.get("typedbFreshCandidateRebuild")
        ),
        fresh_schema_bootstrap_batch_size=int(number_or_none(
            settings.get("typedbFreshSchemaBootstrapBatchSize")
        ) or DEFAULT_TYPEDB_FRESH_SCHEMA_BOOTSTRAP_BATCH_SIZE),
        fresh_schema_bootstrap_timeout_seconds=number_or_none(
            settings.get("typedbFreshSchemaBootstrapTimeoutSeconds")
        ) or DEFAULT_TYPEDB_FRESH_SCHEMA_BOOTSTRAP_TIMEOUT_SECONDS,
    )
