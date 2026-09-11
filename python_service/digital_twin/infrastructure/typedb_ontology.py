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


def rulebox_runtime_metadata(rules_payload: List[Dict[str, object]]) -> Dict[str, object]:
    rules_payload = [item for item in (rules_payload or []) if isinstance(item, dict)]
    active_rules_payload = [item for item in rules_payload if typedb_rule_is_enabled(item)]
    rules_hash = rulebox_rules_hash(rules_payload)
    execution_profiles = [rule_execution_profile(item) for item in active_rules_payload]
    all_execution_profiles = [rule_execution_profile(item) for item in rules_payload]
    stage_counts = {
        stage: len([
            item
            for item in execution_profiles
            if str(item.get("executionStage") or "") == stage
        ])
        for stage in ["critical", "core", "supporting"]
    }
    dependency_index = rule_dependency_reverse_index(rules_payload)
    return {
        "ruleboxRulesHash": rules_hash,
        "ruleboxShortHash": rules_hash[:12],
        "ruleboxRuleCount": len(rules_payload),
        "ruleboxActiveRuleCount": len(active_rules_payload),
        "ruleboxDisabledRuleCount": len(rules_payload) - len(active_rules_payload),
        "ruleboxConditionCount": sum(len(item.get("conditions") or []) for item in rules_payload),
        "ruleboxDerivationCount": sum(len(item.get("derivations") or []) for item in rules_payload),
        "ruleboxEngineVersion": GRAPH_REASONER_VERSION,
        "ruleExecutionPolicyVersion": RULE_EXECUTION_POLICY_VERSION,
        "ruleExecutionStageCounts": stage_counts,
        "ruleExecutionStageCountsAll": {
            stage: len([
                item
                for item in all_execution_profiles
                if str(item.get("executionStage") or "") == stage
            ])
            for stage in ["critical", "core", "supporting"]
        },
        "ruleDependencyIndexVersion": str(dependency_index.get("version") or ""),
        "ruleDependencyIndexFingerprint": str(dependency_index.get("fingerprint") or ""),
        "ruleDependencyIndexRuleCount": int(dependency_index.get("ruleCount") or 0),
    }


def rulebox_structural_fingerprint(rules_payload: List[Dict[str, object]]) -> Dict[str, Tuple[int, int]]:
    fingerprint: Dict[str, Tuple[int, int]] = {}
    for rule in rules_payload or []:
        if not isinstance(rule, dict):
            continue
        rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "").strip()
        if not rule_id:
            continue
        fingerprint[rule_id] = (
            len(rule.get("conditions") or []),
            len(rule.get("derivations") or []),
        )
    return fingerprint


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


def slim_typeql_node_schema(schema: str) -> str:
    """Replace the universal capability fan-out with bounded-context roots."""
    pattern = re.compile(
        r"entity ontology-node @abstract,\s*(?P<body>.*?)"
        r"\s*plays ontology-assertion:target;\s*\n\s*"
        r"entity ontology-entity, sub ontology-node;\s*\n"
        r"entity ontology-evidence, sub ontology-node;\s*\n"
        r"entity ontology-belief, sub ontology-node;\s*\n"
        r"entity ontology-opinion, sub ontology-node;\s*\n"
        r"entity ontology-reasoning-card, sub ontology-node;",
        re.DOTALL,
    )
    match = pattern.search(str(schema or ""))
    if match is None:
        raise ValueError("The TypeDB ontology-node schema block is unavailable.")
    all_owned = set(re.findall(r"owns\s+(ontology-[a-z0-9-]+)", match.group("body")))
    common = sorted(all_owned & TYPEDB_COMMON_NODE_ATTRIBUTES)
    fallback_only = sorted(all_owned - TYPEDB_COMMON_NODE_ATTRIBUTES)

    def ownership(attributes: Iterable[str], unique_storage: bool = False) -> str:
        rows = []
        for attribute in attributes:
            annotation = " @unique" if unique_storage and attribute == "ontology-storage-id" else ""
            rows.append("    owns " + attribute + annotation)
        return ",\n".join(rows)

    root = (
        "entity ontology-node @abstract,\n"
        + ownership(common, unique_storage=True)
        + ",\n    plays ontology-assertion:source,\n"
        + "    plays ontology-assertion:target;"
    )
    fallback_ownership = ownership(fallback_only)
    fallbacks = []
    for node_type in (
        "ontology-entity",
        "ontology-evidence",
        "ontology-belief",
        "ontology-opinion",
        "ontology-reasoning-card",
    ):
        clause = "entity " + node_type + ", sub ontology-node"
        if fallback_ownership:
            clause += ",\n" + fallback_ownership
        fallbacks.append(clause + ";")
    return schema[:match.start()] + root + "\n" + "\n".join(fallbacks) + schema[match.end():]


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

    @coordinated_typedb_projection_write(
        "graph-save",
        typedb_projection_world_from_graph,
    )
    def save_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        if not self.address:
            return NullTypeDBOntologyGraphRepository().save_graph(graph)
        imported = self.driver_imports()
        if imported[0] is None:
            return self.driver_missing_result(imported[1], graph)
        boxes = node_boxes(graph)
        if "ABox" in boxes and self.is_scoped_abox_graph(graph):
            return self.save_scoped_abox_graph(graph, boxes)
        abox_projection_verification: Dict[str, object] = {}
        abox_persistence_timing: Dict[str, object] = {}
        try:
            def operation():
                nonlocal abox_projection_verification, abox_persistence_timing
                with typedb_operation_timeout(self.write_operation_timeout_seconds(), "TypeDB graph save"):
                    driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    expected_entity_count = 0
                    expected_relation_count = 0
                    if "ABox" in boxes:
                        abox_started_at = time.monotonic()
                        abox_persistence_timing = {"startedAt": utc_now()}
                        node_rows, relation_rows = self.graph_persistence_rows(graph)
                        expected_entity_count = len(node_rows)
                        expected_relation_count = len(relation_rows)
                        candidate_graph = self.abox_candidate_graph(graph)
                        snapshot_id = self.abox_snapshot_id_from_graph(candidate_graph)
                        abox_persistence_timing["candidateAboxSnapshotId"] = snapshot_id
                        if not snapshot_id:
                            abox_projection_verification = {
                                "status": "skipped",
                                "reason": "ABox material identity is unavailable.",
                            }
                        else:
                            active_before = self.active_abox_metadata()
                            active_snapshot_id = str(active_before.get("aboxSnapshotId") or "").strip()
                            if active_snapshot_id != snapshot_id:
                                cleanup_started_at = time.monotonic()
                                try:
                                    incremental_cleanup = self.drain_inactive_abox_generations_incrementally(
                                        driver,
                                        imported,
                                        active_snapshot_id,
                                        excluded_snapshot_ids=[snapshot_id],
                                    )
                                except Exception as error:  # noqa: BLE001 - maintenance cannot block a new live generation.
                                    incremental_cleanup = {
                                        "status": "deferred",
                                        "reason": str(error)[:180],
                                        "activeAboxSnapshotId": active_snapshot_id,
                                    }
                                abox_persistence_timing["incrementalCleanupMs"] = round(
                                    (time.monotonic() - cleanup_started_at) * 1000,
                                    1,
                                )
                                abox_persistence_timing["incrementalCleanup"] = incremental_cleanup
                                # A candidate shares the physical ABox box with the
                                # active generation, but storage IDs include the
                                # snapshot. Clear only an interrupted retry of this
                                # exact candidate; never touch the live generation.
                                clear_started_at = time.monotonic()
                                self.delete_box_snapshot_rows_in_batches(
                                    driver,
                                    imported,
                                    "ABox",
                                    snapshot_id,
                                )
                                abox_persistence_timing["candidateRetryClearMs"] = round(
                                    (time.monotonic() - clear_started_at) * 1000,
                                    1,
                                )
                                candidate_write_started_at = time.monotonic()
                                self.write_graph(driver, imported, candidate_graph, delete_boxes=[])
                                abox_persistence_timing["candidateWriteMs"] = round(
                                    (time.monotonic() - candidate_write_started_at) * 1000,
                                    1,
                                )
                                marker_graph = self.abox_projection_marker_graph(
                                    candidate_graph,
                                    expected_entity_count,
                                    expected_relation_count,
                                )
                                if not marker_graph.entities:
                                    raise RuntimeError("ABox completion marker is unavailable.")
                                marker_write_started_at = time.monotonic()
                                self.write_graph(driver, imported, marker_graph, delete_boxes=[])
                                abox_persistence_timing["markerWriteMs"] = round(
                                    (time.monotonic() - marker_write_started_at) * 1000,
                                    1,
                                )
                            verification_started_at = time.monotonic()
                            candidate_verification = self.verify_abox_projection(
                                candidate_graph,
                                expected_entity_count,
                                expected_relation_count,
                            )
                            abox_persistence_timing["candidateVerificationMs"] = round(
                                (time.monotonic() - verification_started_at) * 1000,
                                1,
                            )
                            if candidate_verification.get("status") != "ok":
                                raise RuntimeError(
                                    "ABox candidate verification failed: "
                                    + json.dumps(candidate_verification, ensure_ascii=False, sort_keys=True)
                                )
                            if active_snapshot_id != snapshot_id:
                                pointer_graph = self.abox_active_pointer_graph(
                                    candidate_graph,
                                    previous_snapshot_id=active_snapshot_id,
                                )
                                pointer_write_started_at = time.monotonic()
                                self.write_graph(
                                    driver,
                                    imported,
                                    pointer_graph,
                                    delete_boxes=["ABoxControl"],
                                )
                                abox_persistence_timing["pointerWriteMs"] = round(
                                    (time.monotonic() - pointer_write_started_at) * 1000,
                                    1,
                                )
                                # Keep the prior active generation until the
                                # new ABox has produced an aligned native
                                # InferenceBox. The projection recorder either
                                # finalizes this retention after success or
                                # restores this pointer after a rule failure.
                            abox_projection_verification = {
                                **self.verify_abox_projection(
                                    candidate_graph,
                                    expected_entity_count,
                                    expected_relation_count,
                                ),
                                "activePointer": self.active_abox_metadata(),
                                "activation": {
                                    "status": "unchanged" if active_snapshot_id == snapshot_id else "activated",
                                    "snapshotId": snapshot_id,
                                    "previousSnapshotId": active_snapshot_id,
                                    "atomic": True,
                                    "finalizationRequired": bool(
                                        active_snapshot_id and active_snapshot_id != snapshot_id
                                    ),
                                },
                            }
                            abox_persistence_timing["totalMs"] = round(
                                (time.monotonic() - abox_started_at) * 1000,
                                1,
                            )
                            abox_projection_verification["timing"] = dict(abox_persistence_timing)
                            if abox_projection_verification.get("status") != "ok":
                                raise RuntimeError(
                                    "ABox activation verification failed: "
                                    + json.dumps(abox_projection_verification, ensure_ascii=False, sort_keys=True)
                                )
                    non_abox_boxes = [box for box in boxes if box != "ABox"]
                    if non_abox_boxes:
                        self.write_graph(
                            driver,
                            imported,
                            self.graph_for_boxes(graph, non_abox_boxes),
                            delete_boxes=non_abox_boxes,
                        )
                finally:
                    self.close_driver(driver)
            self.with_typedb_retries(operation)
        except Exception as error:  # noqa: BLE001 - graph-store persistence must not block monitoring.
            # Candidate writes never replace the active pointer until their
            # own marker and row counts verify. Preserve both the active ABox
            # and a failed candidate for diagnosis; a retry clears only that
            # candidate snapshot before writing it again.
            cleanup = {
                "status": "preserved-active-generation",
                "activeAboxSnapshotId": str(self.active_abox_metadata().get("aboxSnapshotId") or ""),
            } if "ABox" in boxes else {}
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "graphStore": "typedb",
                "reason": str(error)[:240],
                "partialWriteCleanup": cleanup,
                "entityCount": len(graph.entities),
                "relationCount": len(graph.relations),
                "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
                "aboxPersistenceVerification": abox_projection_verification,
                "aboxPersistenceTiming": abox_persistence_timing,
            }
        self._last_graph = copy.deepcopy(graph)
        box_entity_counts = graph_box_entity_counts(graph)
        box_relation_counts = graph_box_relation_counts(graph)
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "schemaPrepared": True,
            "address": self.address,
            "database": self.database,
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "tboxEntityCount": box_entity_counts.get("TBox", 0),
            "aboxEntityCount": box_entity_counts.get("ABox", 0),
            "ruleBoxEntityCount": box_entity_counts.get("RuleBox", 0),
            "languageGovernanceEntityCount": box_entity_counts.get("LanguageGovernance", 0),
            "inferenceBoxEntityCount": box_entity_counts.get("InferenceBox", 0),
            "tboxRelationCount": box_relation_counts.get("TBox", 0),
            "aboxRelationCount": box_relation_counts.get("ABox", 0),
            "ruleBoxRelationCount": box_relation_counts.get("RuleBox", 0),
            "languageGovernanceRelationCount": box_relation_counts.get("LanguageGovernance", 0),
            "inferenceBoxRelationCount": box_relation_counts.get("InferenceBox", 0),
            "evidenceCount": len(graph.evidence),
            "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
            "aboxPersistenceVerification": abox_projection_verification,
            "aboxPersistenceTiming": abox_persistence_timing,
        }

    def driver_missing_result(self, error: Exception, graph: PortfolioOntology) -> Dict[str, object]:
        return {
            "configured": True,
            "saved": False,
            "status": "driver-missing",
            "graphStore": "typedb",
            "reason": "typedb-driver Python package is not installed: " + str(error)[:160],
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
        }

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
        """Return whether this world still has no durable candidate Manifest.

        The blue-green control plane sets the fresh-candidate flag while a
        database is provisioned. A long-lived worker can process many later
        target patches, so the flag must stop bypassing active metadata after
        the first successful Manifest write.
        """
        if not self._fresh_candidate_rebuild:
            return False
        try:
            active = dict(self.active_abox_metadata(str(world_id or "")) or {})
        except Exception:
            return True
        return not bool(
            str(active.get("status") or "") == "ok"
            and str(
                active.get("worldviewManifestId")
                or active.get("aboxSnapshotId")
                or ""
            ).strip()
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
        configured_settings = runtime_settings() if settings is None else settings
        raw = dict(configured_settings or {}).get("typedbABoxDeleteBatchSize")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 1000
        # ABox replacement is a bounded operational cleanup, not a per-row
        # workflow. Very small batches turn a few thousand facts into dozens
        # of TypeDB commits and can starve the live reasoning worker before
        # it reaches the first insert batch.
        return max(100, min(5000, int(parsed)))

    def abox_incremental_cleanup_batch_size(self, settings: Dict[str, object] = None) -> int:
        """Keep one live cleanup slice below the TypeDB writer saturation point."""
        configured_settings = runtime_settings() if settings is None else settings
        raw = dict(configured_settings or {}).get("typedbABoxIncrementalCleanupBatchSize")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 50
        # Full deletion remains available to explicit repair commands. Runtime
        # projection drains only a small slice, so a historic generation can
        # never monopolize the writer before the next market inference runs.
        return max(10, min(500, int(parsed)))

    def abox_incremental_cleanup_max_batches_per_save(self, settings: Dict[str, object] = None) -> int:
        configured_settings = runtime_settings() if settings is None else settings
        raw = dict(configured_settings or {}).get("typedbABoxIncrementalCleanupMaxBatchesPerSave")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 1
        return max(0, min(4, int(parsed)))

    def abox_inactive_generation_keep_count(self, settings: Dict[str, object] = None) -> int:
        raw = (settings or runtime_settings()).get("typedbABoxInactiveGenerationKeepCount")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 0
        # MySQL keeps the source snapshot and activation audit. TypeDB retains
        # only active facts, not a rollback or time-series history.
        return max(0, min(5, int(parsed)))

    def abox_inactive_generation_max_prune_per_save(self, settings: Dict[str, object] = None) -> int:
        raw = (settings or runtime_settings()).get("typedbABoxInactiveGenerationMaxPrunePerSave")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 2
        # Deletes are deliberately bounded so a live activation cannot spend
        # minutes reclaiming a historic backlog under TypeDB's writer lock.
        return max(0, min(10, int(parsed)))

    def deferred_maintenance_abox_max_manifests(self, settings: Dict[str, object] = None) -> int:
        """Allow idle maintenance to drain faster than a live ABox save.

        The realtime activation path intentionally removes at most a couple of
        manifests. Once the queue is idle, a larger bounded slice prevents a
        sustained market session from leaving hundreds of immutable manifests
        behind indefinitely.
        """
        raw = (settings or runtime_settings()).get("typedbDeferredMaintenanceMaxManifests")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 10
        return max(1, min(10, int(parsed)))

    def deferred_maintenance_abox_max_delete_batches(self, settings: Dict[str, object] = None) -> int:
        """Bound physical TypeDB deletes independently from Manifest count.

        One immutable Manifest can own several scope generations and each
        generation can require many TypeDB delete transactions. This budget
        is the real latency guard for a low-priority retention pass.
        """
        raw = (settings or runtime_settings()).get("ontologyAboxMaintenanceMaxDeleteBatchesPerRun")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 2
        return max(1, min(50, int(parsed)))

    def deferred_maintenance_abox_delete_batch_size(self, settings: Dict[str, object] = None) -> int:
        """Use short deletes for deferred retention, independent of ABox replacement."""
        raw = (settings or runtime_settings()).get("ontologyAboxMaintenanceDeleteBatchSize")
        parsed = number_or_none(raw)
        if parsed is None:
            return self.abox_incremental_cleanup_batch_size(settings)
        return max(10, min(500, int(parsed)))

    def abox_write_transaction_query_count(self, settings: Dict[str, object] = None) -> int:
        raw = (settings or runtime_settings()).get("typedbABoxWriteTransactionQueryCount")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 16
        # A single ABox refresh can produce dozens of insert queries. Keeping
        # fifty of them in one transaction made the TypeDB writer hold its lock
        # for several minutes under live market load, which starved the next
        # reasoning and notification cycle. Sixteen keeps commit overhead
        # bounded without bringing back the long writer lock; larger explicit
        # settings are capped at twenty-four for the same reason.
        return max(1, min(24, int(parsed)))

    def abox_node_batch_size(self, settings: Dict[str, object] = None) -> int:
        """Keep one native TypeQL insert plan below the transport idle edge."""

        raw = dict(settings or runtime_settings()).get("typedbABoxNodeBatchSize")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 10
        # Independent inserts in one TypeQL query still share one planner
        # graph. Ten keeps large shared-world replays responsive while the
        # transaction grouping above amortises commit overhead.
        return max(1, min(10, int(parsed)))

    def abox_relation_batch_size(self, settings: Dict[str, object] = None) -> int:
        configured_settings = runtime_settings() if settings is None else settings
        raw = dict(configured_settings or {}).get("typedbABoxRelationBatchSize")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 1
        # A live replay on TypeDB 3.12 showed the planner spending minutes in
        # a beam-search plan for even a small group of independent endpoint
        # matches. A relation write is therefore one edge per TypeQL query.
        # Queries remain grouped into short write transactions, so this avoids
        # the planner cross product without one commit per edge.
        return max(1, min(1, int(parsed)))

    def graph_write_transaction_query_count(self, settings: Dict[str, object] = None) -> int:
        raw = (settings or runtime_settings()).get("typedbGraphWriteTransactionQueryCount")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 8
        # TBox and RuleBox seeding can also contain
        # thousands of queries. Seed in short commits so startup does not hold
        # the TypeDB writer for minutes before the live ABox worker can run.
        # A subsequent seed deletes and rebuilds those boxes, so retrying a
        # partial seed is deterministic.
        return max(1, min(50, int(parsed)))

    def static_node_insert_batch_size(self, settings: Dict[str, object] = None) -> int:
        """Keep immutable ontology seed inserts planner-safe on a live ABox.

        TypeDB 3 plans independent node inserts together.  A RuleBox component
        contains a large JSON contract and many promoted attributes, so a
        conventional 100-node batch can consume minutes of CPU before any
        rows commit when a multi-gigabyte ABox is present.  Static seed writes
        are rare and correctness-critical, therefore their safe default is
        one node per TypeQL query.  Operators can raise the bounded setting
        after benchmarking their own TypeDB deployment.
        """
        raw = dict(settings or runtime_settings()).get("typedbStaticNodeBatchSize")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 1
        return max(1, min(8, int(parsed)))

    def static_write_transaction_query_count(self, settings: Dict[str, object] = None) -> int:
        """Bound commits for static seed writes without combining TypeQL plans."""
        raw = dict(settings or runtime_settings()).get("typedbStaticWriteTransactionQueryCount")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 16
        return max(1, min(32, int(parsed)))

    def inferencebox_write_transaction_query_count(self, settings: Dict[str, object] = None) -> int:
        configured_settings = runtime_settings() if settings is None else settings
        raw = dict(configured_settings or {}).get("typedbInferenceBoxWriteTransactionQueryCount")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 24
        # One candidate normally fits in one transaction. The cap keeps a
        # pathological trace set bounded while avoiding separate commits for
        # candidate cleanup, rows, and the candidate marker.
        return max(1, min(50, int(parsed)))

    def inferencebox_relation_batch_size(self, settings: Dict[str, object] = None) -> int:
        raw = (settings or runtime_settings()).get("typedbInferenceBoxRelationBatchSize")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 1
        # Inference traces are denser than ABox facts, so grouped endpoint
        # matches are particularly costly to compile. Keep the same safe
        # single-edge plan used by the live ABox writer.
        return max(1, min(1, int(parsed)))

    def inferencebox_given_relation_writes_enabled(
        self,
        settings: Dict[str, object] = None,
    ) -> bool:
        values = dict(runtime_settings() if settings is None else settings or {})
        raw = values.get("typedbInferenceBoxGivenRelationWritesEnabled")
        if raw is None:
            return True
        return str(raw).strip().lower() not in {
            "0", "false", "no", "off", "disabled",
        }

    def inferencebox_given_relation_batch_size(
        self,
        settings: Dict[str, object] = None,
    ) -> int:
        values = dict(runtime_settings() if settings is None else settings or {})
        configured = number_or_none(
            values.get("typedbInferenceBoxGivenRelationBatchSize")
        )
        if configured is None:
            configured = 50
        return max(1, min(250, int(configured)))

    def box_instance_exists(self, driver, imported, box: str, type_label: str) -> bool:
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        query = (
            "match $item isa " + str(type_label) + ", has ontology-box " + typedb_string(box) + "; limit 1;"
        )
        with driver.transaction(self.database, TransactionType.READ) as tx:
            return bool(self.read_rows_in_transaction(tx, query, [], label="typedb.box-exists"))

    def box_delete_batch_query(self, box: str, type_label: str, batch_size: int) -> str:
        variable = "$r" if str(type_label) == "ontology-assertion" else "$n"
        return (
            "match " + variable + " isa " + str(type_label) + ", has ontology-box " + typedb_string(box)
            + "; limit " + str(max(1, int(batch_size or 1))) + "; delete " + variable + ";"
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
        rows = self.read_rows(
            'match $n isa ontology-node, has ontology-box "ABox", has ontology-snapshot-id $snapshotId;',
            ["snapshotId"],
            label="typedb.abox-candidate-cleanup-audit",
        )
        return sorted({str(row.get("snapshotId") or "").strip() for row in rows if str(row.get("snapshotId") or "").strip()})

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
        """Return a persistence-safe graph slice for the requested ontology boxes.

        Static boxes are normally independent, except for a small number of
        declaration edges such as ``TBox RuleBox -> RuleBox RuleRegistry``.
        A targeted RuleBox refresh must retain those edges without re-inserting
        the already durable TBox endpoint.  External endpoints are therefore
        kept only as in-memory lookup rows and are excluded from node writes.
        """
        allowed = {str(item or "").strip() for item in boxes or [] if str(item or "").strip()}
        if not allowed:
            return PortfolioOntology(str(graph.portfolio_id or "typedb-empty"))
        clone = copy.deepcopy(graph)
        source_entities = list(clone.entities)
        source_entity_ids = {str(item.entity_id or "") for item in source_entities if str(item.entity_id or "")}
        selected_entities = [
            item
            for item in source_entities
            if str((item.properties or {}).get("ontologyBox") or "ABox") in allowed
        ]
        selected_entity_ids = {str(item.entity_id or "") for item in selected_entities}
        selected_relations = [
            item
            for item in clone.relations
            if str((item.properties or {}).get("ontologyBox") or "ABox") in allowed
            and str(item.source or "") in source_entity_ids
            and str(item.target or "") in source_entity_ids
            and (
                retain_cross_box_relations
                or (
                    str(item.source or "") in selected_entity_ids
                    and str(item.target or "") in selected_entity_ids
                )
            )
        ]
        if retain_cross_box_relations:
            endpoint_ids = {
                str(endpoint or "")
                for relation in selected_relations
                for endpoint in [relation.source, relation.target]
                if str(endpoint or "")
            }
            external_endpoint_ids = endpoint_ids - selected_entity_ids
            selected_entities.extend(
                item
                for item in source_entities
                if str(item.entity_id or "") in external_endpoint_ids
            )
            for item in selected_entities:
                if str(item.entity_id or "") in external_endpoint_ids:
                    item.properties = dict(item.properties or {})
                    item.properties["_typedbExternalEndpointRef"] = True
        clone.entities = selected_entities
        clone.relations = selected_relations
        clone.evidence = [
            item
            for item in clone.evidence
            if str((item.value or {}).get("ontologyBox") or "ABox") in allowed
        ]
        return clone

    def graph_with_static_seed_generation(
        self,
        graph: PortfolioOntology,
        boxes: Iterable[str],
        generation_id,
    ) -> PortfolioOntology:
        """Attach immutable static generation IDs to selected persisted boxes.

        ``generation_id`` accepts either one shared value or a per-box map.
        The latter is required for targeted static updates: a changed RuleBox
        must still link to the active TBox generation without rewriting that
        TBox.  Cross-box endpoint references receive their owning box's
        storage identity but remain excluded from node writes.
        """
        selected_boxes = {str(item or "").strip() for item in boxes or [] if str(item or "").strip()}
        if isinstance(generation_id, dict):
            generation_by_box = {
                str(box or "").strip(): str(value or "").strip()
                for box, value in generation_id.items()
                if str(box or "").strip() and str(value or "").strip()
            }
        else:
            clean_generation = str(generation_id or "").strip()
            generation_by_box = {
                box: clean_generation
                for box in selected_boxes
                if clean_generation
            }
        if not generation_by_box or not selected_boxes:
            return graph
        clone = copy.deepcopy(graph)
        for item in clone.entities:
            properties = dict(item.properties or {})
            box = str(properties.get("ontologyBox") or "ABox")
            generation = generation_by_box.get(box)
            if generation:
                properties["snapshotId"] = generation
                properties["staticSeedGeneration"] = generation
                item.properties = properties
        for item in clone.relations:
            properties = dict(item.properties or {})
            box = str(properties.get("ontologyBox") or "ABox")
            generation = generation_by_box.get(box)
            if generation:
                properties["snapshotId"] = generation
                properties["staticSeedGeneration"] = generation
                item.properties = properties
        return clone

    def abox_candidate_graph(self, graph: PortfolioOntology) -> PortfolioOntology:
        """Return one immutable ABox generation ready for pointer activation.

        ABox records stay in their normal box. Their storage identity already
        includes ``snapshotId``, so a verified candidate can coexist with the
        currently active generation without rewriting thousands of records.
        """
        return self.graph_for_boxes(graph, ["ABox"])

    @staticmethod
    def abox_snapshot_id_from_graph(graph: PortfolioOntology) -> str:
        worldview = dict(getattr(graph, "worldview", {}) or {})
        snapshot_id = str(worldview.get("aboxSnapshotId") or worldview.get("snapshotId") or "").strip()
        if snapshot_id:
            return snapshot_id
        for item in list(getattr(graph, "entities", []) or []):
            properties = dict(getattr(item, "properties", {}) or {})
            snapshot_id = str(properties.get("aboxSnapshotId") or properties.get("snapshotId") or "").strip()
            if snapshot_id:
                return snapshot_id
        return ""

    def abox_active_pointer_graph(
        self,
        graph: PortfolioOntology,
        previous_snapshot_id: str = "",
        pending_activation: bool = True,
    ) -> PortfolioOntology:
        worldview = dict(getattr(graph, "worldview", {}) or {})
        snapshot_id = self.abox_snapshot_id_from_graph(graph)
        fingerprint = str(worldview.get("materialFingerprint") or "").strip()
        if not snapshot_id:
            return PortfolioOntology(str(graph.portfolio_id or "typedb-abox-control"))
        as_of = str(worldview.get("asOf") or worldview.get("generatedAt") or utc_now())
        target_symbols = clean_symbols_from_payload(
            worldview.get("inferenceTargetSymbols") or worldview.get("targetSymbols") or []
        )
        world_id = str(worldview.get("worldId") or "").strip()
        world_context = {
            "worldId": world_id,
            "worldType": str(worldview.get("worldType") or ""),
            "tenantId": str(worldview.get("tenantId") or ""),
            "accountId": str(worldview.get("accountId") or graph.portfolio_id or ""),
        }
        world_suffix = (":world:" + hashlib.sha256(world_id.encode("utf-8")).hexdigest()[:16]) if world_id else ""
        pointer = OntologyEntity(
            entity_id="abox-active-pointer" + world_suffix,
            label="Active ABox generation",
            kind="abox-active-pointer",
            properties={
                "ontologyBox": "ABoxControl",
                **world_context,
                "tboxClass": "ABoxActivePointer",
                "snapshotId": snapshot_id,
                "aboxSnapshotId": snapshot_id,
                "materialFingerprint": fingerprint,
                "projectionRunId": str(worldview.get("projectionRunId") or ""),
                "asOf": as_of,
            },
        )
        entities = [pointer]
        # Store the activation hand-off in the same atomic ABoxControl write as
        # the pointer. This is cleared only after a native InferenceBox is
        # aligned, or after an explicit rollback to the retained predecessor.
        if pending_activation and str(previous_snapshot_id or "").strip() != snapshot_id:
            entities.append(OntologyEntity(
                entity_id="abox-activation-pending" + world_suffix,
                label="ABox activation pending native inference",
                kind="abox-activation-pending",
                properties={
                    "ontologyBox": "ABoxControl",
                    **world_context,
                    "tboxClass": "ABoxActivationPending",
                    "snapshotId": snapshot_id,
                    "aboxSnapshotId": snapshot_id,
                    "candidateAboxSnapshotId": snapshot_id,
                    "previousAboxSnapshotId": str(previous_snapshot_id or "").strip(),
                    "materialFingerprint": fingerprint,
                    "projectionRunId": str(worldview.get("projectionRunId") or ""),
                    "asOf": as_of,
                    "targetSymbols": target_symbols,
                    "activationStatus": "pending-native-inference",
                },
            ))
        return PortfolioOntology(str(graph.portfolio_id or "typedb-abox-control"), entities=entities)

    def activate_abox_generation(self, snapshot_id: str, world_id: str = "") -> Dict[str, object]:
        """Point the active ABox control record at a verified generation.

        This is used to restore the last aligned ABox when a newly activated
        generation cannot complete TypeDB native inference. It only accepts a
        generation with a complete ABox marker, so it cannot promote a partial
        write left behind by an interrupted worker.
        """
        clean_snapshot_id = str(snapshot_id or "").strip()
        if not clean_snapshot_id:
            return {
                "configured": bool(self.address),
                "status": "skipped",
                "graphStore": "typedb",
                "reason": "ABox snapshot id is empty.",
            }
        if self.scoped_manifest_metadata(clean_snapshot_id, world_id):
            return typedb_call_for_world(
                self.activate_scoped_abox_manifest,
                clean_snapshot_id,
                world_id=world_id,
            )
        marker = next((
            item
            for item in self.abox_projection_marker_rows(world_id)
            if str(item.get("aboxSnapshotId") or item.get("snapshotId") or "").strip() == clean_snapshot_id
        ), None)
        metadata = self.abox_metadata_from_marker(marker or {}) if marker else {}
        if str(metadata.get("status") or "") != "ok":
            return {
                "configured": bool(self.address),
                "status": "error",
                "graphStore": "typedb",
                "aboxSnapshotId": clean_snapshot_id,
                "reason": str(metadata.get("reason") or "ABox generation is not complete."),
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return self.driver_missing_result(imported[1], PortfolioOntology("typedb-abox-control"))
        pointer_graph = self.abox_active_pointer_graph(PortfolioOntology(
            "typedb-abox-control",
            worldview={
                "aboxSnapshotId": clean_snapshot_id,
                "materialFingerprint": str(metadata.get("materialFingerprint") or ""),
                "projectionRunId": str(metadata.get("projectionRunId") or ""),
                "asOf": str(metadata.get("asOf") or utc_now()),
                "worldId": str(world_id or metadata.get("worldId") or ""),
            },
        ), pending_activation=False)
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.delete_world_abox_control_rows(driver, imported, world_id)
                    self.write_graph(driver, imported, pointer_graph, delete_boxes=[])
                finally:
                    self.close_driver(driver)

            self.with_typedb_retries(operation)
            active = self.active_abox_metadata(world_id)
            if str(active.get("status") or "") != "ok" or str(active.get("aboxSnapshotId") or "") != clean_snapshot_id:
                return {
                    "configured": True,
                    "status": "error",
                    "graphStore": "typedb",
                    "aboxSnapshotId": clean_snapshot_id,
                    "reason": "ABox control pointer verification failed after activation.",
                    "activeAbox": active,
                }
            return {
                "configured": True,
                "status": "ok",
                "graphStore": "typedb",
                "aboxSnapshotId": clean_snapshot_id,
                "activeAbox": active,
            }
        except Exception as error:  # noqa: BLE001 - caller preserves the diagnostic failure state.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "aboxSnapshotId": clean_snapshot_id,
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
            }

    def finalize_abox_generation(self, active_snapshot_id: str, previous_snapshot_id: str = "", world_id: str = "") -> Dict[str, object]:
        """Complete an ABox activation after aligned native inference.

        Clearing the durable activation journal is a correctness boundary;
        deleting the prior generation is storage maintenance. Keeping those
        operations separate prevents one expensive TypeDB delete from making a
        valid realtime inference appear incomplete or retriggering alerts.
        """
        active_id = str(active_snapshot_id or "").strip()
        previous_id = str(previous_snapshot_id or "").strip()
        active_metadata = self.active_abox_metadata(world_id)
        if str(active_metadata.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION:
            return self.finalize_scoped_abox_manifest(active_id, previous_id, world_id)
        if not active_id:
            return {
                "configured": bool(self.address),
                "status": "error",
                "graphStore": "typedb",
                "activeAboxSnapshotId": active_id,
                "previousAboxSnapshotId": previous_id,
                "reason": "Active ABox snapshot id is empty.",
            }
        active = self.active_abox_metadata(world_id)
        if str(active.get("status") or "") != "ok" or str(active.get("aboxSnapshotId") or "") != active_id:
            return {
                "configured": bool(self.address),
                "status": "error",
                "graphStore": "typedb",
                "activeAboxSnapshotId": active_id,
                "previousAboxSnapshotId": previous_id,
                "reason": "Active ABox changed before retained-generation cleanup.",
            }
        control = typedb_call_for_world(
            self.activate_abox_generation,
            active_id,
            world_id=world_id,
        )
        cleared = str(control.get("status") or "") == "ok"
        cleanup_deferred = bool(previous_id and previous_id != active_id)
        return {
            "configured": True,
            "status": "ok" if cleared else "error",
            "graphStore": "typedb",
            "activeAboxSnapshotId": active_id,
            "previousAboxSnapshotId": previous_id,
            "clearedPendingActivation": cleared,
            "cleanupDeferred": cleanup_deferred,
            "cleanup": {
                "status": "deferred" if cleanup_deferred else "not-required",
                "previousAboxSnapshotId": previous_id,
                "reason": (
                    "Inactive ABox cleanup will run in bounded maintenance slices."
                    if cleanup_deferred
                    else "No prior ABox generation requires cleanup."
                ),
            },
            "control": control,
            "reason": "" if cleared else str(control.get("reason") or "ABox activation journal clear failed."),
        }

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
        self,
        driver,
        imported,
        graph: PortfolioOntology,
        delete_boxes: Iterable[str] = None,
    ) -> None:
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        boxes = node_boxes(graph) if delete_boxes is None else list(delete_boxes or [])
        static_replacement_boxes = {"TBox", "RuleBox", "RuleBoxGovernance", "LanguageGovernance"}
        static_boxes = sorted(static_replacement_boxes.intersection(boxes))
        # A broad static delete scans the whole ontology-node/assertion space
        # on a large durable ABox.  Delete each static box in bounded TypeQL
        # batches before inserting its replacement instead.  This keeps a
        # RuleBox-only policy change from monopolising the TypeDB writer.
        if static_boxes:
            self.delete_box_rows_in_batches(driver, imported, static_boxes)
        delete_queries = self.delete_queries(
            box for box in boxes
            if box not in static_replacement_boxes
        )
        graph_boxes = node_boxes(graph)
        static_graph_write = bool(static_replacement_boxes.intersection(graph_boxes))
        insert_queries = (
            self.static_graph_insert_queries(graph)
            if static_graph_write
            else self.graph_insert_queries(graph)
        )
        if not delete_queries and not insert_queries:
            return
        transaction_query_count = (
            self.abox_write_transaction_query_count()
            if "ABox" in graph_boxes
            else (
                self.static_write_transaction_query_count()
                if static_graph_write
                else self.graph_write_transaction_query_count()
            )
        )
        # Large static replacements span multiple batches. Commit their deletes
        # first so a later insert batch cannot collide with an old @unique
        # storage ID. Small ABoxControl pointer swaps remain one transaction.
        phases = [delete_queries, insert_queries] if static_boxes else [delete_queries + insert_queries]
        for queries in phases:
            for offset in range(0, len(queries), transaction_query_count):
                query_batch = queries[offset: offset + transaction_query_count]

                def write_batch():
                    with typedb_operation_timeout(self.write_operation_timeout_seconds(), "TypeDB graph write batch"):
                        with driver.transaction(
                            self.database,
                            TransactionType.WRITE,
                            options=self.write_transaction_options(),
                        ) as tx:
                            for query in query_batch:
                                tx.query(query).resolve()
                            tx.commit()

                self.with_typedb_retries(write_batch)

    def clear_inferencebox(self, world_id: str = "") -> Dict[str, object]:
        if not self.address:
            return {
                "configured": False,
                "status": "disabled",
                "graphStore": "typedb",
                "reason": "TypeDB ontology storage is not configured.",
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return {
                "configured": True,
                "status": "driver-missing",
                "graphStore": "typedb",
                "reason": "typedb-driver Python package is not installed: " + str(imported[1])[:160],
            }
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    with driver.transaction(self.database, TransactionType.WRITE) as tx:
                        world_clause = (
                            ", has ontology-world-id " + typedb_string(world_id)
                            if str(world_id or "").strip()
                            else ""
                        )
                        delete_queries = (
                            [
                                'match $r isa ontology-assertion, has ontology-box "InferenceBox"'
                                + world_clause + "; delete $r;",
                                'match $n isa ontology-node, has ontology-box "InferenceBox"'
                                + world_clause + "; delete $n;",
                            ]
                            if world_clause
                            else self.delete_queries(["InferenceBox"])
                        )
                        for query in delete_queries:
                            tx.query(query).resolve()
                        tx.commit()
                finally:
                    self.close_driver(driver)
            self.with_typedb_retries(operation)
            return {
                "configured": True,
                "status": "ok",
                "graphStore": "typedb",
                "worldId": str(world_id or ""),
                "clearedBox": "InferenceBox",
            }
        except Exception as error:  # noqa: BLE001 - caller reports clear failure as inference boundary status.
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
            }

    def schema_query(self) -> str:
        schema = """
define
attribute ontology-id, value string;
attribute ontology-storage-id, value string;
attribute ontology-content-fingerprint, value string;
attribute ontology-label, value string;
attribute ontology-kind, value string;
attribute ontology-box, value string;
attribute ontology-symbol, value string;
attribute ontology-rule-id, value string;
attribute ontology-account-id, value string;
attribute ontology-tenant-id, value string;
attribute ontology-world-id, value string;
attribute ontology-world-type, value string;
attribute ontology-snapshot-id, value string;
attribute ontology-scope-id, value string;
attribute ontology-scope-type, value string;
attribute ontology-manifest-id, value string;
attribute ontology-tbox-class, value string;
attribute ontology-semantic-type, value string;
attribute ontology-relation-type, value string;
attribute ontology-updated-at, value string;
attribute ontology-json, value string;
attribute ontology-weight, value double;
attribute ontology-source-value, value string;
attribute ontology-field, value string;
attribute ontology-level-type, value string;
attribute ontology-data-scope, value string;
attribute ontology-domain-scope, value string;
attribute ontology-relation-scope, value string;
attribute ontology-group, value string;
attribute ontology-polarity, value string;
attribute ontology-evidence-role, value string;
attribute ontology-review-level, value string;
attribute ontology-data-state, value string;
attribute ontology-change-state, value string;
attribute ontology-conflict-state, value string;
attribute ontology-validation-state, value string;
attribute ontology-transition-type, value string;
attribute ontology-signal-group, value string;
attribute ontology-event-type, value string;
attribute ontology-materiality-passed, value string;
attribute ontology-materiality-state, value string;
attribute ontology-relevance-state, value string;
attribute ontology-source-trust-state, value string;
attribute ontology-value-number, value double;
attribute ontology-profit-loss-rate, value double;
attribute ontology-allow-add-on-strength, value string;
attribute ontology-trim-on-trend-break, value string;
attribute ontology-avoid-averaging-down, value string;
attribute ontology-impact-polarity, value string;
attribute ontology-needs-review, value string;
attribute ontology-read-scope, value string;
attribute ontology-pe-ratio, value double;
attribute ontology-beta, value double;
attribute ontology-delta, value double;
attribute ontology-delta-pct, value double;
attribute ontology-delta-bp, value double;
attribute ontology-previous-value, value double;
attribute ontology-delta-1d-bp, value double;
attribute ontology-delta-5d-bp, value double;
attribute ontology-delta-20d-bp, value double;
attribute ontology-change-24h, value double;
attribute ontology-change-7d, value double;
attribute ontology-surprise-percentage, value double;
attribute ontology-current-price, value double;
attribute ontology-average-price, value double;
attribute ontology-market-value, value double;
attribute ontology-quantity, value double;
attribute ontology-sellable-quantity, value double;
attribute ontology-position-weight-pct, value double;
attribute ontology-position-account-weight-pct, value double;
attribute ontology-exposure-ratio, value double;
attribute ontology-position-count, value double;
attribute ontology-change-rate, value double;
attribute ontology-price-change-rate, value double;
attribute ontology-ma5, value double;
attribute ontology-ma20, value double;
attribute ontology-ma60, value double;
attribute ontology-ma5-distance, value double;
attribute ontology-ma20-distance, value double;
attribute ontology-ma60-distance, value double;
attribute ontology-ma20-slope, value double;
attribute ontology-ma60-slope, value double;
attribute ontology-trend-curve, value double;
attribute ontology-volume, value double;
attribute ontology-volume-ratio, value double;
attribute ontology-raw-volume-ratio, value double;
attribute ontology-time-adjusted-volume-ratio, value double;
attribute ontology-expected-volume-ratio-now, value double;
attribute ontology-trade-strength, value double;
attribute ontology-trading-value, value double;
attribute ontology-reported-trading-value, value double;
attribute ontology-estimated-trading-value, value double;
attribute ontology-trading-value-mismatch-pct, value double;
attribute ontology-trading-value-quality, value string;
attribute ontology-trading-value-basis, value string;
attribute ontology-bid-ask-imbalance, value double;
attribute ontology-foreign-net-volume, value double;
attribute ontology-foreign-net-amount, value double;
attribute ontology-institution-net-volume, value double;
attribute ontology-institution-net-amount, value double;
attribute ontology-individual-net-volume, value double;
attribute ontology-individual-net-amount, value double;
attribute ontology-smart-money-net-volume, value double;
attribute ontology-adr-ratio, value double;
attribute ontology-adr-price-usd, value double;
attribute ontology-adr-volume, value double;
attribute ontology-usd-krw-rate, value double;
attribute ontology-local-price-krw, value double;
attribute ontology-local-equivalent-krw, value double;
attribute ontology-leverage-factor, value double;
attribute ontology-price, value double;
attribute ontology-fair-value, value double;
attribute ontology-fair-value-price, value double;
attribute ontology-fair-value-low, value double;
attribute ontology-fair-value-base, value double;
attribute ontology-fair-value-high, value double;
attribute ontology-margin-of-safety-pct, value double;
attribute ontology-conservative-margin-of-safety-pct, value double;
attribute ontology-optimistic-margin-of-safety-pct, value double;
attribute ontology-expensive-premium-pct, value double;
attribute ontology-minimum-margin-of-safety-pct, value double;
attribute ontology-valuation-decision-eligible, value double;
attribute ontology-valuation-model-count, value double;
attribute ontology-valuation-consensus-price, value double;
attribute ontology-valuation-disagreement-pct, value double;
attribute ontology-expected-eps, value double;
attribute ontology-reported-eps, value double;
attribute ontology-estimated-eps, value double;
attribute ontology-target-per, value double;
attribute ontology-forward-pe, value double;
attribute ontology-peg-ratio, value double;
attribute ontology-dividend-yield, value double;
attribute ontology-peer-per, value double;
attribute ontology-historical-median-per, value double;
attribute ontology-lookback-days, value double;
attribute ontology-required-sample-count, value double;
attribute ontology-sample-count, value double;
attribute ontology-coverage-ratio, value double;
attribute ontology-elapsed-hours, value double;
attribute ontology-start-price, value double;
attribute ontology-price-change-pct, value double;
attribute ontology-relative-return-pct, value double;
attribute ontology-proxy-change-rate, value double;
attribute ontology-peak-price, value double;
attribute ontology-trough-price, value double;
attribute ontology-peak-return-pct, value double;
attribute ontology-trough-return-pct, value double;
attribute ontology-drawdown-from-peak-pct, value double;
attribute ontology-rebound-from-trough-pct, value double;
attribute ontology-prior-price-change-pct, value double;
attribute ontology-recent-price-change-pct, value double;
attribute ontology-price-velocity-change-pct, value double;
attribute ontology-consecutive-decline-count, value double;
attribute ontology-consecutive-advance-count, value double;
attribute ontology-direction-change-count, value double;
attribute ontology-valid-observation-count, value double;
attribute ontology-invalid-observation-count, value double;
attribute ontology-stale-observation-count, value double;
attribute ontology-valid-observation-ratio, value double;
attribute ontology-profit-loss-rate-start, value double;
attribute ontology-profit-loss-rate-end, value double;
attribute ontology-profit-loss-rate-change-pct, value double;
attribute ontology-ma20-distance-start, value double;
attribute ontology-ma20-distance-end, value double;
attribute ontology-ma20-distance-change, value double;
attribute ontology-ma20-distance-peak, value double;
attribute ontology-ma20-distance-trough, value double;
attribute ontology-ma20-reclaim-count, value double;
attribute ontology-ma20-break-count, value double;
attribute ontology-ma20-observation-count, value double;
attribute ontology-ma60-distance-start, value double;
attribute ontology-ma60-distance-end, value double;
attribute ontology-volume-ratio-end, value double;
attribute ontology-trade-strength-end, value double;
attribute ontology-bid-ask-imbalance-end, value double;
attribute ontology-smart-money-net-latest, value double;
attribute ontology-smart-money-net-change, value double;
attribute ontology-smart-money-net-cumulative, value double;
attribute ontology-smart-money-net-amount-cumulative, value double;
attribute ontology-smart-money-trading-value-ratio-pct, value double;
attribute ontology-smart-money-positive-session-ratio, value double;
attribute ontology-smart-money-negative-session-ratio, value double;
attribute ontology-smart-money-flow-persistence-ratio, value double;
attribute ontology-smart-money-flow-acceleration, value double;
attribute ontology-smart-money-observation-count, value double;
attribute ontology-smart-money-distinct-observation-count, value double;
attribute ontology-smart-money-distinct-session-count, value double;
attribute ontology-individual-net-latest, value double;
attribute ontology-event-count, value double;
attribute ontology-risk-event-count, value double;
attribute ontology-support-event-count, value double;
attribute ontology-investment-strategy-profile, value string;
attribute ontology-investment-strategy-profile-label, value string;
attribute ontology-position-role, value string;
attribute ontology-target-position-role, value string;
attribute ontology-position-intent, value string;
attribute ontology-position-intent-label, value string;
attribute ontology-position-intent-description, value string;
attribute ontology-instrument-archetype, value string;
attribute ontology-instrument-archetype-label, value string;
attribute ontology-factor, value string;
attribute ontology-sensitivity-level, value string;
attribute ontology-rate-series-id, value string;
attribute ontology-observation-date, value string;
attribute ontology-previous-observation-date, value string;
attribute ontology-source-as-of, value string;
attribute ontology-change-basis, value string;
attribute ontology-crypto-symbol, value string;
attribute ontology-fx-pair, value string;
attribute ontology-action-policy, value string;
attribute ontology-security-line-role, value string;
attribute ontology-local-symbol, value string;
attribute ontology-company-name, value string;
attribute ontology-market, value string;
attribute ontology-currency, value string;
attribute ontology-exchange, value string;
attribute ontology-adr-symbol, value string;
attribute ontology-etf-symbol, value string;
attribute ontology-underlying-symbol, value string;
attribute ontology-conversion-start-date, value string;
attribute ontology-listing-date, value string;
attribute ontology-source-url, value string;
attribute ontology-valuation-method, value string;
attribute ontology-formula, value string;
attribute ontology-eps-period, value string;
attribute ontology-multiple-period, value string;
attribute ontology-valuation-as-of, value string;
attribute ontology-valuation-freshness-status, value string;
attribute ontology-valuation-data-state-label, value string;
attribute ontology-valuation-source-type, value string;
attribute ontology-valuation-currency, value string;
attribute ontology-valuation-consensus-status, value string;
attribute ontology-per-valuation-status, value string;
attribute ontology-per-valuation-reason, value string;
attribute ontology-preferred-valuation-metric, value string;
attribute ontology-fundamental-data-source-priority, value string;
attribute ontology-window-key, value string;
attribute ontology-has-sufficient-history, value string;
attribute ontology-latest-observation-quality, value string;
attribute ontology-sequence-role, value string;
attribute ontology-observation-quality, value string;
attribute ontology-observed-at, value string;
attribute ontology-provider, value string;
attribute ontology-price-path-pattern, value string;
attribute ontology-flow-pattern, value string;
attribute ontology-event-cluster-type, value string;
attribute ontology-trend-episode-type, value string;
attribute ontology-language-registry-version, value string;
attribute ontology-language-term-id, value string;
attribute ontology-language-term-category, value string;
attribute ontology-language-term-status, value string;
attribute ontology-language-term-version, value string;
attribute ontology-language-preferred-label, value string;
attribute ontology-language-delivery-level, value string;
attribute ontology-language-delivery-level-label, value string;
attribute ontology-language-rendered-label, value string;
attribute ontology-smart-money-direction, value string;
attribute ontology-smart-money-flow-direction, value string;
attribute ontology-smart-money-flow-basis, value string;
attribute ontology-investor-flow-psychology, value string;
attribute ontology-investor-flow-evidence-role, value string;
attribute ontology-investor-flow-data-state, value string;
attribute ontology-investor-flow-review-level, value string;
attribute ontology-investor-flow-measurement-type, value string;
attribute ontology-investor-flow-is-estimate, value string;
attribute ontology-investor-flow-source-as-of, value string;
attribute ontology-investor-flow-provider-update-slot, value string;
attribute ontology-investor-flow-freshness-status, value string;
attribute ontology-trend-risk-state, value string;
attribute ontology-trend-review-level, value string;
attribute ontology-trend-evidence-role, value string;
attribute ontology-trend-data-state, value string;
attribute ontology-liquidity-state, value string;
attribute ontology-liquidity-review-level, value string;
attribute ontology-liquidity-data-state, value string;
attribute ontology-source-data-state, value string;
attribute ontology-external-signal-data-state, value string;
attribute ontology-valuation-data-state, value string;
attribute ontology-valuation-input-state, value string;
attribute ontology-valuation-reliability-state, value string;

entity ontology-node @abstract,
    owns ontology-id,
    owns ontology-storage-id @unique,
    owns ontology-content-fingerprint,
    owns ontology-label,
    owns ontology-kind,
    owns ontology-box,
    owns ontology-symbol,
    owns ontology-rule-id,
    owns ontology-account-id,
    owns ontology-tenant-id,
    owns ontology-world-id,
    owns ontology-world-type,
    owns ontology-snapshot-id,
    owns ontology-scope-id,
    owns ontology-scope-type,
    owns ontology-manifest-id,
    owns ontology-tbox-class,
    owns ontology-semantic-type,
    owns ontology-updated-at,
    owns ontology-json,
    owns ontology-source-value,
    owns ontology-field,
    owns ontology-level-type,
    owns ontology-data-scope,
    owns ontology-domain-scope,
    owns ontology-relation-type,
    owns ontology-relation-scope,
    owns ontology-group,
    owns ontology-polarity,
    owns ontology-evidence-role,
    owns ontology-review-level,
    owns ontology-data-state,
    owns ontology-change-state,
    owns ontology-conflict-state,
    owns ontology-validation-state,
    owns ontology-event-type,
    owns ontology-materiality-passed,
    owns ontology-materiality-state,
    owns ontology-relevance-state,
    owns ontology-source-trust-state,
    owns ontology-value-number,
    owns ontology-profit-loss-rate,
    owns ontology-allow-add-on-strength,
    owns ontology-trim-on-trend-break,
    owns ontology-avoid-averaging-down,
    owns ontology-impact-polarity,
    owns ontology-needs-review,
    owns ontology-read-scope,
    owns ontology-pe-ratio,
    owns ontology-beta,
    owns ontology-delta,
    owns ontology-delta-pct,
    owns ontology-delta-bp,
    owns ontology-previous-value,
    owns ontology-delta-1d-bp,
    owns ontology-delta-5d-bp,
    owns ontology-delta-20d-bp,
    owns ontology-change-24h,
    owns ontology-change-7d,
    owns ontology-surprise-percentage,
    owns ontology-current-price,
    owns ontology-average-price,
    owns ontology-market-value,
    owns ontology-quantity,
    owns ontology-sellable-quantity,
    owns ontology-position-weight-pct,
    owns ontology-position-account-weight-pct,
    owns ontology-exposure-ratio,
    owns ontology-position-count,
    owns ontology-change-rate,
    owns ontology-price-change-rate,
    owns ontology-ma5,
    owns ontology-ma20,
    owns ontology-ma60,
    owns ontology-ma5-distance,
    owns ontology-ma20-distance,
    owns ontology-ma60-distance,
    owns ontology-ma20-slope,
    owns ontology-ma60-slope,
    owns ontology-trend-curve,
    owns ontology-volume,
    owns ontology-volume-ratio,
    owns ontology-raw-volume-ratio,
    owns ontology-time-adjusted-volume-ratio,
    owns ontology-expected-volume-ratio-now,
    owns ontology-trade-strength,
    owns ontology-trading-value,
    owns ontology-reported-trading-value,
    owns ontology-estimated-trading-value,
    owns ontology-trading-value-mismatch-pct,
    owns ontology-trading-value-quality,
    owns ontology-trading-value-basis,
    owns ontology-bid-ask-imbalance,
    owns ontology-foreign-net-volume,
    owns ontology-foreign-net-amount,
    owns ontology-institution-net-volume,
    owns ontology-institution-net-amount,
    owns ontology-individual-net-volume,
    owns ontology-individual-net-amount,
    owns ontology-smart-money-net-volume,
    owns ontology-adr-ratio,
    owns ontology-adr-price-usd,
    owns ontology-adr-volume,
    owns ontology-usd-krw-rate,
    owns ontology-local-price-krw,
    owns ontology-local-equivalent-krw,
    owns ontology-leverage-factor,
    owns ontology-price,
    owns ontology-fair-value,
    owns ontology-fair-value-price,
    owns ontology-fair-value-low,
    owns ontology-fair-value-base,
    owns ontology-fair-value-high,
    owns ontology-margin-of-safety-pct,
    owns ontology-conservative-margin-of-safety-pct,
    owns ontology-optimistic-margin-of-safety-pct,
    owns ontology-expensive-premium-pct,
    owns ontology-minimum-margin-of-safety-pct,
    owns ontology-valuation-decision-eligible,
    owns ontology-valuation-model-count,
    owns ontology-valuation-consensus-price,
    owns ontology-valuation-disagreement-pct,
    owns ontology-expected-eps,
    owns ontology-reported-eps,
    owns ontology-estimated-eps,
    owns ontology-target-per,
    owns ontology-forward-pe,
    owns ontology-peg-ratio,
    owns ontology-dividend-yield,
    owns ontology-peer-per,
    owns ontology-historical-median-per,
    owns ontology-lookback-days,
    owns ontology-required-sample-count,
    owns ontology-sample-count,
    owns ontology-coverage-ratio,
    owns ontology-elapsed-hours,
    owns ontology-start-price,
    owns ontology-price-change-pct,
    owns ontology-relative-return-pct,
    owns ontology-proxy-change-rate,
    owns ontology-peak-price,
    owns ontology-trough-price,
    owns ontology-peak-return-pct,
    owns ontology-trough-return-pct,
    owns ontology-drawdown-from-peak-pct,
    owns ontology-rebound-from-trough-pct,
    owns ontology-prior-price-change-pct,
    owns ontology-recent-price-change-pct,
    owns ontology-price-velocity-change-pct,
    owns ontology-consecutive-decline-count,
    owns ontology-consecutive-advance-count,
    owns ontology-direction-change-count,
    owns ontology-valid-observation-count,
    owns ontology-invalid-observation-count,
    owns ontology-stale-observation-count,
    owns ontology-valid-observation-ratio,
    owns ontology-profit-loss-rate-start,
    owns ontology-profit-loss-rate-end,
    owns ontology-profit-loss-rate-change-pct,
    owns ontology-ma20-distance-start,
    owns ontology-ma20-distance-end,
    owns ontology-ma20-distance-change,
    owns ontology-ma20-distance-peak,
    owns ontology-ma20-distance-trough,
    owns ontology-ma20-reclaim-count,
    owns ontology-ma20-break-count,
    owns ontology-ma20-observation-count,
    owns ontology-ma60-distance-start,
    owns ontology-ma60-distance-end,
    owns ontology-volume-ratio-end,
    owns ontology-trade-strength-end,
    owns ontology-bid-ask-imbalance-end,
    owns ontology-smart-money-net-latest,
    owns ontology-smart-money-net-change,
    owns ontology-smart-money-net-cumulative,
    owns ontology-smart-money-net-amount-cumulative,
    owns ontology-smart-money-trading-value-ratio-pct,
    owns ontology-smart-money-positive-session-ratio,
    owns ontology-smart-money-negative-session-ratio,
    owns ontology-smart-money-flow-persistence-ratio,
    owns ontology-smart-money-flow-acceleration,
    owns ontology-smart-money-observation-count,
    owns ontology-smart-money-distinct-observation-count,
    owns ontology-smart-money-distinct-session-count,
    owns ontology-individual-net-latest,
    owns ontology-event-count,
    owns ontology-risk-event-count,
    owns ontology-support-event-count,
    owns ontology-investment-strategy-profile,
    owns ontology-investment-strategy-profile-label,
    owns ontology-position-role,
    owns ontology-target-position-role,
    owns ontology-position-intent,
    owns ontology-position-intent-label,
    owns ontology-position-intent-description,
    owns ontology-instrument-archetype,
    owns ontology-instrument-archetype-label,
    owns ontology-factor,
    owns ontology-sensitivity-level,
    owns ontology-rate-series-id,
    owns ontology-observation-date,
    owns ontology-previous-observation-date,
    owns ontology-source-as-of,
    owns ontology-change-basis,
    owns ontology-crypto-symbol,
    owns ontology-fx-pair,
    owns ontology-action-policy,
    owns ontology-security-line-role,
    owns ontology-local-symbol,
    owns ontology-company-name,
    owns ontology-market,
    owns ontology-currency,
    owns ontology-exchange,
    owns ontology-adr-symbol,
    owns ontology-etf-symbol,
    owns ontology-underlying-symbol,
    owns ontology-conversion-start-date,
    owns ontology-listing-date,
    owns ontology-source-url,
    owns ontology-valuation-method,
    owns ontology-formula,
    owns ontology-eps-period,
    owns ontology-multiple-period,
    owns ontology-valuation-as-of,
    owns ontology-valuation-freshness-status,
    owns ontology-valuation-data-state-label,
    owns ontology-valuation-source-type,
    owns ontology-valuation-currency,
    owns ontology-valuation-consensus-status,
    owns ontology-per-valuation-status,
    owns ontology-per-valuation-reason,
    owns ontology-preferred-valuation-metric,
    owns ontology-fundamental-data-source-priority,
    owns ontology-window-key,
    owns ontology-has-sufficient-history,
    owns ontology-latest-observation-quality,
    owns ontology-sequence-role,
    owns ontology-observation-quality,
    owns ontology-observed-at,
    owns ontology-provider,
    owns ontology-price-path-pattern,
    owns ontology-flow-pattern,
    owns ontology-event-cluster-type,
    owns ontology-trend-episode-type,
    owns ontology-language-registry-version,
    owns ontology-language-term-id,
    owns ontology-language-term-category,
    owns ontology-language-term-status,
    owns ontology-language-term-version,
    owns ontology-language-preferred-label,
    owns ontology-language-delivery-level,
    owns ontology-language-delivery-level-label,
    owns ontology-language-rendered-label,
    owns ontology-smart-money-direction,
    owns ontology-smart-money-flow-direction,
    owns ontology-smart-money-flow-basis,
    owns ontology-investor-flow-psychology,
    owns ontology-investor-flow-evidence-role,
    owns ontology-investor-flow-data-state,
    owns ontology-investor-flow-review-level,
    owns ontology-investor-flow-measurement-type,
    owns ontology-investor-flow-is-estimate,
    owns ontology-investor-flow-source-as-of,
    owns ontology-investor-flow-provider-update-slot,
    owns ontology-investor-flow-freshness-status,
    owns ontology-trend-risk-state,
    owns ontology-trend-review-level,
    owns ontology-trend-evidence-role,
    owns ontology-trend-data-state,
    owns ontology-liquidity-state,
    owns ontology-liquidity-review-level,
    owns ontology-liquidity-data-state,
    owns ontology-source-data-state,
    owns ontology-external-signal-data-state,
    owns ontology-valuation-data-state,
    owns ontology-valuation-input-state,
    owns ontology-valuation-reliability-state,
    plays ontology-assertion:source,
    plays ontology-assertion:target;

entity ontology-entity, sub ontology-node;
entity ontology-evidence, sub ontology-node;
entity ontology-belief, sub ontology-node;
entity ontology-opinion, sub ontology-node;
entity ontology-reasoning-card, sub ontology-node;

relation ontology-assertion,
    relates source,
    relates target,
    owns ontology-id,
    owns ontology-storage-id @unique,
    owns ontology-content-fingerprint,
    owns ontology-relation-type,
    owns ontology-box,
    owns ontology-symbol,
    owns ontology-rule-id,
    owns ontology-account-id,
    owns ontology-tenant-id,
    owns ontology-world-id,
    owns ontology-world-type,
    owns ontology-snapshot-id,
    owns ontology-scope-id,
    owns ontology-scope-type,
    owns ontology-manifest-id,
    owns ontology-tbox-class,
    owns ontology-semantic-type,
    owns ontology-updated-at,
    owns ontology-json,
    owns ontology-weight,
    owns ontology-field,
    owns ontology-polarity,
    owns ontology-evidence-role,
    owns ontology-review-level,
    owns ontology-data-state,
    owns ontology-change-state,
    owns ontology-conflict-state,
    owns ontology-validation-state,
    owns ontology-transition-type,
    owns ontology-signal-group,
    owns ontology-materiality-passed,
    owns ontology-materiality-state,
    owns ontology-relevance-state,
    owns ontology-source-trust-state,
    owns ontology-delta,
    owns ontology-delta-pct,
    owns ontology-exposure-ratio,
    owns ontology-position-count;
""".strip()
        promoted_types = {
            **{attribute: "double" for attribute in TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES.values()},
            **{attribute: "string" for attribute in TYPEDB_PROMOTED_TEXT_ATTRIBUTES.values()},
        }
        missing_promoted_types = {
            attribute: value_type
            for attribute, value_type in promoted_types.items()
            if "attribute " + attribute + ", value " not in schema
        }
        if missing_promoted_types:
            declarations = "\n".join(
                "attribute " + attribute + ", value " + value_type + ";"
                for attribute, value_type in sorted(missing_promoted_types.items())
            )
            ownership = "\n".join(
                "    owns " + attribute + ","
                for attribute in sorted(missing_promoted_types)
            )
            schema = schema.replace("define\n", "define\n" + declarations + "\n", 1)
            schema = schema.replace(
                "    plays ontology-assertion:source,",
                ownership + "\n    plays ontology-assertion:source,",
                1,
            )
        schema = slim_typeql_node_schema(schema)
        capability_contract = typedb_rule_schema_capability_contract()
        semantic_schema = semantic_typeql_schema(
            context_attribute_ownership=capability_contract.get("contextAttributes") or {},
            physical_class_names=capability_contract.get("physicalClassNames") or [],
            physical_relation_names=capability_contract.get("physicalRelationNames") or [],
        )
        return schema + "\n\n" + semantic_schema.replace("define\n", "", 1).strip()

    def delete_queries(self, boxes: Iterable[str]) -> List[str]:
        queries = []
        for box in sorted(set(str(item or "").strip() for item in boxes if str(item or "").strip())):
            queries.append(
                "match $r isa ontology-assertion, has ontology-box " + typedb_string(box) + "; delete $r;"
            )
            queries.append(
                "match $n isa ontology-node, has ontology-box " + typedb_string(box) + "; delete $n;"
            )
        return queries

    def insert_queries(self, graph: PortfolioOntology) -> List[str]:
        queries: List[str] = []
        updated_at = utc_now()
        node_rows, relation_rows = self.graph_persistence_rows(graph)
        for row in node_rows:
            queries.append(self.node_insert_query(row, updated_at))
        for row in relation_rows:
            queries.append(self.relation_insert_query(row, updated_at))
        return [item for item in queries if item]

    def graph_persistence_rows(self, graph: PortfolioOntology) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
        node_rows = self.node_rows(graph)
        endpoint_rows = self.node_rows(graph, include_external_relation_endpoints=True)
        node_rows_by_id = {
            str(row.get("id") or ""): row
            for row in endpoint_rows
            if str(row.get("id") or "")
        }
        node_ids = set(node_rows_by_id)
        relation_rows = [
            {
                **row,
                "sourceStorageId": ontology_storage_id(
                    node_rows_by_id[str(row.get("source") or "")],
                    row.get("source"),
                    "node",
                ),
                "targetStorageId": ontology_storage_id(
                    node_rows_by_id[str(row.get("target") or "")],
                    row.get("target"),
                    "node",
                ),
            }
            for row in self.rows_for_relations(graph) + self.support_relation_rows(graph)
            if str(row.get("source") or "") in node_ids
            and str(row.get("target") or "") in node_ids
        ]
        return node_rows, relation_rows

    def abox_projection_marker_graph(
        self,
        graph: PortfolioOntology,
        expected_entity_count: int,
        expected_relation_count: int,
        box: str = "ABox",
    ) -> PortfolioOntology:
        worldview = dict(getattr(graph, "worldview", {}) or {})
        snapshot_id = str(worldview.get("aboxSnapshotId") or worldview.get("snapshotId") or "").strip()
        fingerprint = str(worldview.get("materialFingerprint") or "").strip()
        if not snapshot_id or not fingerprint:
            return PortfolioOntology(str(graph.portfolio_id or "typedb-abox-marker"))
        as_of = str(worldview.get("asOf") or worldview.get("generatedAt") or utc_now())
        marker = OntologyEntity(
            entity_id="abox-projection-marker:" + snapshot_id,
            label="ABox projection completion",
            kind="abox-projection-marker",
            properties={
                "ontologyBox": str(box or "ABox"),
                "tboxClass": "ABoxProjectionMarker",
                "snapshotId": snapshot_id,
                "aboxSnapshotId": snapshot_id,
                "materialFingerprint": fingerprint,
                "projectionRunId": str(worldview.get("projectionRunId") or ""),
                "asOf": as_of,
                "expectedAboxEntityCount": int(expected_entity_count),
                "expectedAboxRelationCount": int(expected_relation_count),
                "projectionStatus": "complete",
            },
        )
        return PortfolioOntology(str(graph.portfolio_id or "typedb-abox-marker"), entities=[marker])

    def verify_abox_projection(
        self,
        graph: PortfolioOntology,
        expected_entity_count: int,
        expected_relation_count: int,
        box: str = "ABox",
    ) -> Dict[str, object]:
        worldview = dict(getattr(graph, "worldview", {}) or {})
        snapshot_id = str(worldview.get("aboxSnapshotId") or worldview.get("snapshotId") or "").strip()
        if not snapshot_id:
            return {"status": "skipped", "reason": "ABox material identity is unavailable."}
        actual = self.box_snapshot_row_counts(str(box or "ABox"), snapshot_id)
        complete = (
            actual["entityCount"] == int(expected_entity_count) + 1
            and actual["relationCount"] == int(expected_relation_count)
        )
        return {
            "status": "ok" if complete else "incomplete",
            "ontologyBox": str(box or "ABox"),
            "aboxSnapshotId": snapshot_id,
            "expectedEntityCount": int(expected_entity_count),
            "expectedRelationCount": int(expected_relation_count),
            "actualEntityCount": actual["entityCount"] - 1 if actual["entityCount"] else 0,
            "actualRelationCount": actual["relationCount"],
            "completionMarkerCount": 1 if actual["entityCount"] else 0,
        }

    def graph_insert_queries(self, graph: PortfolioOntology) -> List[str]:
        updated_at = utc_now()
        node_rows, relation_rows = self.graph_persistence_rows(graph)
        settings = runtime_settings()
        node_batch_size = self.abox_node_batch_size(settings)
        relation_batch_size = self.abox_relation_batch_size(settings)
        max_query_bytes = self.write_query_max_bytes(settings)
        return [
            *self.batched_node_insert_queries(node_rows, updated_at, node_batch_size, max_query_bytes),
            *self.batched_relation_insert_queries(relation_rows, updated_at, relation_batch_size, max_query_bytes),
        ]

    def static_graph_insert_queries(self, graph: PortfolioOntology) -> List[str]:
        """Build static TBox/RuleBox writes without exponential node batches.

        Relation queries remain deliberately one edge each: grouping unrelated
        endpoint matches creates a TypeDB planner cross product.  They are
        still committed in short transactions by ``write_graph``.
        """
        updated_at = utc_now()
        node_rows, relation_rows = self.graph_persistence_rows(graph)
        settings = runtime_settings()
        return [
            *self.batched_node_insert_queries(
                node_rows,
                updated_at,
                self.static_node_insert_batch_size(settings),
                self.write_query_max_bytes(settings),
            ),
            *self.batched_relation_insert_queries(
                relation_rows,
                updated_at,
                1,
                self.write_query_max_bytes(settings),
            ),
        ]

    def write_query_max_bytes(self, settings: Dict[str, object] = None) -> int:
        configured_settings = runtime_settings() if settings is None else settings
        raw = dict(configured_settings or {}).get("typedbWriteMaxQueryBytes")
        parsed = number_or_none(raw)
        if parsed is None:
            parsed = 192000
        return max(4096, min(256000, int(parsed)))

    @staticmethod
    def query_byte_size(query: str) -> int:
        return len(str(query or "").encode("utf-8"))

    @staticmethod
    def external_relation_endpoint_ids(graph: PortfolioOntology) -> set:
        return {
            str(item.entity_id or "")
            for item in getattr(graph, "entities", []) or []
            if str(item.entity_id or "")
            and bool(dict(getattr(item, "properties", {}) or {}).get("_typedbExternalEndpointRef"))
        }

    def node_rows(
        self,
        graph: PortfolioOntology,
        include_external_relation_endpoints: bool = False,
    ) -> List[Dict[str, object]]:
        rows = []
        rows.extend({**row, "nodeType": "ontology-entity"} for row in self.rows_for_entities(graph))
        rows.extend(self.evidence_node_rows(graph))
        rows.extend(self.belief_node_rows(graph))
        rows.extend(self.opinion_node_rows(graph))
        rows.extend(self.reasoning_card_node_rows(graph))
        external_ids = self.external_relation_endpoint_ids(graph)
        return [
            row
            for row in rows
            if str(row.get("id") or "")
            and (
                include_external_relation_endpoints
                or str(row.get("id") or "") not in external_ids
            )
        ]

    def evidence_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                **row,
                "nodeType": "ontology-evidence",
                "label": row.get("summary") or row.get("id"),
                "kind": "evidence:" + str(row.get("kind") or "evidence"),
                "symbol": "",
                "ruleId": "",
                "tboxClass": "Evidence",
                "propertiesJson": row.get("valueJson") or "{}",
            }
            for row in self.rows_for_evidence(graph)
        ]

    def belief_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                **row,
                "nodeType": "ontology-belief",
                "label": row.get("label") or row.get("id"),
                "kind": "belief",
                "symbol": symbol_from_subject(row.get("subject")),
                "ruleId": rule_id_from_value(row.get("id")),
                "tboxClass": "Belief",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            }
            for row in self.rows_for_beliefs(graph)
        ]

    def opinion_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                **row,
                "nodeType": "ontology-opinion",
                "label": str(row.get("symbol") or row.get("id")),
                "kind": "opinion",
                "ruleId": "",
                "tboxClass": "InvestmentOpinion",
                "propertiesJson": row.get("payloadJson") or "{}",
            }
            for row in self.rows_for_opinions(graph)
        ]

    def reasoning_card_node_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                **row,
                "nodeType": "ontology-reasoning-card",
                "label": row.get("companyName") or row.get("symbol") or row.get("id"),
                "kind": "reasoning-card",
                "ruleId": "",
                "tboxClass": "ReasoningCard",
                "propertiesJson": row.get("payloadJson") or "{}",
            }
            for row in self.rows_for_reasoning_cards(graph)
        ]

    def support_relation_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        support_scope_plan = dict((getattr(graph, "worldview", {}) or {}).get("supportRelationScopes") or {})

        def scoped_owner(relation_type: str, source: object, target: object) -> Dict[str, object]:
            metadata = support_scope_plan.get(support_relation_key(relation_type, source, target))
            if not isinstance(metadata, dict):
                return {}
            scope_id = str(metadata.get("scopeId") or "").strip()
            generation_id = str(
                metadata.get("scopeGenerationId")
                or metadata.get("snapshotId")
                or metadata.get("aboxSnapshotId")
                or ""
            ).strip()
            if not scope_id or not generation_id:
                return {}
            return {
                "scopeId": scope_id,
                "scopeType": str(metadata.get("scopeType") or scope_id.split(":", 1)[0] or "link"),
                "manifestId": str(metadata.get("manifestId") or ""),
                "scopeGenerationId": generation_id,
                "snapshotId": generation_id,
                "aboxSnapshotId": generation_id,
            }

        rows: List[Dict[str, object]] = []
        for row in self.rows_for_evidence(graph):
            original_source = row.get("subject")
            original_target = row.get("id")
            owner = scoped_owner("HAS_EVIDENCE", original_source, original_target)
            metadata = support_scope_plan.get(
                support_relation_key("HAS_EVIDENCE", original_source, original_target)
            )
            metadata = dict(metadata or {}) if isinstance(metadata, dict) else {}
            source = metadata.get("source") or original_source
            target = metadata.get("target") or original_target
            rows.append({
                "source": source,
                "target": target,
                "type": "HAS_EVIDENCE",
                "weight": 1.0,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "tenantId": row.get("tenantId") or "",
                "worldId": row.get("worldId") or "",
                "worldType": row.get("worldType") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "scopeId": row.get("scopeId") or "",
                "scopeType": row.get("scopeType") or "",
                "manifestId": row.get("manifestId") or "",
                "scopeGenerationId": row.get("scopeGenerationId") or row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": "",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
                **owner,
            })
        for row in self.rows_for_beliefs(graph):
            rows.append({
                "source": row.get("subject"),
                "target": row.get("id"),
                "type": "HAS_BELIEF",
                "weight": 1.0,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "tenantId": row.get("tenantId") or "",
                "worldId": row.get("worldId") or "",
                "worldType": row.get("worldType") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "scopeId": row.get("scopeId") or "",
                "scopeType": row.get("scopeType") or "",
                "manifestId": row.get("manifestId") or "",
                "scopeGenerationId": row.get("scopeGenerationId") or row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": row.get("ruleId") or rule_id_from_value(row.get("id")),
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            })
        for row in self.rows_for_opinions(graph):
            rows.append({
                "source": "stock:" + str(row.get("symbol") or "").upper(),
                "target": row.get("id"),
                "type": "HAS_OPINION",
                "weight": 1.0,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "tenantId": row.get("tenantId") or "",
                "worldId": row.get("worldId") or "",
                "worldType": row.get("worldType") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": "",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            })
        for row in self.rows_for_reasoning_cards(graph):
            rows.append({
                "source": "stock:" + str(row.get("symbol") or "").upper(),
                "target": row.get("id"),
                "type": "HAS_REASONING_CARD",
                "weight": 1.0,
                "ontologyBox": row.get("ontologyBox") or "ABox",
                "accountId": row.get("accountId") or "",
                "tenantId": row.get("tenantId") or "",
                "worldId": row.get("worldId") or "",
                "worldType": row.get("worldType") or "",
                "snapshotId": row.get("snapshotId") or row.get("aboxSnapshotId") or "",
                "ruleId": "",
                "propertiesJson": json.dumps(row, ensure_ascii=False, sort_keys=True),
            })
        return [row for row in rows if row.get("source") and row.get("target")]

    def node_insert_query(self, row: Dict[str, object], updated_at: str) -> str:
        return "insert " + self.node_insert_clause(row, updated_at, "$n") + ";"

    def node_insert_clause(self, row: Dict[str, object], updated_at: str, variable: str) -> str:
        properties = json_object(row.get("propertiesJson"))
        semantic_properties = {
            "tboxClass": row.get("tboxClass"),
            "tboxClasses": row.get("tboxClasses") or [],
        }
        node_type = typedb_entity_storage_type(
            semantic_properties,
            row.get("kind"),
            fallback=str(row.get("nodeType") or "ontology-entity"),
        )
        allowed_attributes = typedb_node_allowed_attributes(
            semantic_properties,
            row.get("kind"),
        )

        def node_has(attribute: str, value: object, numeric: bool = False) -> str:
            if allowed_attributes is not None and attribute not in allowed_attributes:
                return ""
            return typeql_has(attribute, value, numeric=numeric)

        def node_has_bool(attribute: str, value: object) -> str:
            if allowed_attributes is not None and attribute not in allowed_attributes:
                return ""
            return typeql_has_bool_string(attribute, value)

        node_id = str(row.get("id") or "")
        return (
            str(variable or "$n") + " isa " + node_type
            + ", has ontology-id " + typedb_string(node_id)
            + ", has ontology-storage-id " + typedb_string(ontology_storage_id(row, node_id, "node"))
            + node_has(
                "ontology-content-fingerprint",
                row.get("contentFingerprint")
                or ontology_row_content_fingerprint(row, "node"),
            )
            + node_has("ontology-label", row.get("label"))
            + node_has("ontology-kind", row.get("kind"))
            + node_has("ontology-box", row.get("ontologyBox") or "ABox")
            + node_has("ontology-symbol", row.get("symbol"))
            + node_has("ontology-rule-id", row.get("ruleId"))
            + node_has("ontology-account-id", row.get("accountId"))
            + node_has("ontology-tenant-id", row.get("tenantId"))
            + node_has("ontology-world-id", row.get("worldId"))
            + node_has("ontology-world-type", row.get("worldType"))
            + node_has("ontology-snapshot-id", row.get("snapshotId") or row.get("aboxSnapshotId"))
            + node_has("ontology-scope-id", row.get("scopeId"))
            + node_has("ontology-scope-type", row.get("scopeType"))
            + node_has("ontology-manifest-id", row.get("manifestId"))
            + node_has("ontology-tbox-class", row.get("tboxClass"))
            + node_has("ontology-semantic-type", node_type)
            + node_has("ontology-relation-type", row.get("relationTypeName"))
            + node_has("ontology-updated-at", updated_at)
            + node_has("ontology-json", row.get("propertiesJson"))
            + node_has("ontology-source-value", row.get("sourceValue"))
            + node_has("ontology-field", row.get("field"))
            + node_has("ontology-level-type", row.get("levelType"))
            + node_has("ontology-data-scope", row.get("dataScope"))
            + node_has("ontology-domain-scope", row.get("domainScope"))
            + node_has("ontology-relation-scope", row.get("relationScope"))
            + node_has("ontology-group", row.get("group"))
            + node_has("ontology-polarity", row.get("polarity"))
            + node_has("ontology-evidence-role", row.get("evidenceRole"))
            + node_has("ontology-review-level", row.get("reviewLevel"))
            + node_has("ontology-data-state", row.get("dataState"))
            + node_has("ontology-change-state", row.get("changeState"))
            + node_has("ontology-conflict-state", row.get("conflictState"))
            + node_has("ontology-validation-state", row.get("validationState"))
            + node_has("ontology-event-type", row.get("eventType"))
            + node_has_bool("ontology-materiality-passed", row.get("materialityPassed"))
            + node_has("ontology-value-number", row.get("valueNumber"), numeric=True)
            + node_has("ontology-profit-loss-rate", row.get("profitLossRate"), numeric=True)
            + node_has_bool("ontology-allow-add-on-strength", row.get("allowAddOnStrength"))
            + node_has_bool("ontology-trim-on-trend-break", row.get("trimOnTrendBreak"))
            + node_has_bool("ontology-avoid-averaging-down", row.get("avoidAveragingDown"))
            + node_has("ontology-impact-polarity", row.get("impactPolarity"))
            + node_has_bool("ontology-needs-review", row.get("needsReview"))
            + node_has("ontology-read-scope", row.get("readScope"))
            + node_has("ontology-pe-ratio", row.get("peRatio"), numeric=True)
            + node_has("ontology-beta", row.get("beta"), numeric=True)
            + "".join(
                node_has(attribute, promoted_node_value(row, properties, field), numeric=True)
                for field, attribute in TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES.items()
            )
            + "".join(
                node_has(attribute, promoted_node_text_value(row, properties, field))
                for field, attribute in TYPEDB_PROMOTED_TEXT_ATTRIBUTES.items()
            )
        )

    def relation_insert_query(self, row: Dict[str, object], updated_at: str) -> str:
        return (
            "match "
            + self.relation_match_clause(row, "$source", "$target")
            + "insert "
            + self.relation_insert_clause(row, updated_at, "$r", "$source", "$target")
            + ";"
        )

    def relation_match_clause(self, row: Dict[str, object], source_variable: str, target_variable: str) -> str:
        source_storage_id = str(row.get("sourceStorageId") or "").strip()
        target_storage_id = str(row.get("targetStorageId") or "").strip()
        if source_storage_id and target_storage_id:
            return (
                str(source_variable or "$source") + " isa ontology-node, has ontology-storage-id "
                + typedb_string(source_storage_id) + "; "
                + str(target_variable or "$target") + " isa ontology-node, has ontology-storage-id "
                + typedb_string(target_storage_id) + "; "
            )
        snapshot_id = row.get("snapshotId") or row.get("aboxSnapshotId")
        ontology_box = str(row.get("ontologyBox") or "ABox").strip() or "ABox"
        # A live ABox generation can contain the same public ontology ID as
        # its predecessor while activation is still pending. Match its
        # endpoints by the generation-scoped unique storage identity instead
        # of scanning all nodes with the public ID and snapshot attribute.
        # Static TBox/RuleBox relations may cross boxes, so they retain the
        # public-ID lookup below.
        if ontology_box == "ABox" and str(snapshot_id or "").strip():
            return (
                str(source_variable or "$source") + " isa ontology-node, has ontology-storage-id "
                + typedb_string(ontology_storage_id(row, row.get("source"), "node")) + "; "
                + str(target_variable or "$target") + " isa ontology-node, has ontology-storage-id "
                + typedb_string(ontology_storage_id(row, row.get("target"), "node")) + "; "
            )
        snapshot_match = typeql_has("ontology-snapshot-id", snapshot_id)
        return (
            str(source_variable or "$source") + " isa ontology-node, has ontology-id " + typedb_string(row.get("source"))
            + snapshot_match + "; "
            + str(target_variable or "$target") + " isa ontology-node, has ontology-id " + typedb_string(row.get("target"))
            + snapshot_match + "; "
        )

    def relation_insert_clause(
        self,
        row: Dict[str, object],
        updated_at: str,
        relation_variable: str,
        source_variable: str,
        target_variable: str,
    ) -> str:
        relation_id = relation_row_id(row)
        relation_type = typedb_relation_storage_type(row.get("type"))
        return (
            str(relation_variable or "$r")
            + " isa " + relation_type + ", links (source: "
            + str(source_variable or "$source")
            + ", target: "
            + str(target_variable or "$target")
            + ")"
            + ", has ontology-id " + typedb_string(relation_id)
            + ", has ontology-storage-id " + typedb_string(ontology_storage_id(row, relation_id, "relation"))
            + typeql_has(
                "ontology-content-fingerprint",
                row.get("contentFingerprint")
                or ontology_row_content_fingerprint(row, "relation"),
            )
            + typeql_has("ontology-relation-type", row.get("type"))
            + typeql_has("ontology-box", row.get("ontologyBox") or "ABox")
            + typeql_has("ontology-symbol", row.get("symbol"))
            + typeql_has("ontology-rule-id", row.get("ruleId"))
            + typeql_has("ontology-account-id", row.get("accountId"))
            + typeql_has("ontology-tenant-id", row.get("tenantId"))
            + typeql_has("ontology-world-id", row.get("worldId"))
            + typeql_has("ontology-world-type", row.get("worldType"))
            + typeql_has("ontology-snapshot-id", row.get("snapshotId") or row.get("aboxSnapshotId"))
            + typeql_has("ontology-scope-id", row.get("scopeId"))
            + typeql_has("ontology-scope-type", row.get("scopeType"))
            + typeql_has("ontology-manifest-id", row.get("manifestId"))
            + typeql_has("ontology-tbox-class", row.get("tboxClass"))
            + typeql_has("ontology-semantic-type", relation_type)
            + typeql_has("ontology-updated-at", updated_at)
            + typeql_has("ontology-json", row.get("propertiesJson"))
            + typeql_has("ontology-weight", row.get("weight"), numeric=True)
            + typeql_has("ontology-field", row.get("field"))
            + typeql_has("ontology-polarity", row.get("polarity"))
            + typeql_has("ontology-evidence-role", row.get("evidenceRole"))
            + typeql_has("ontology-review-level", row.get("reviewLevel"))
            + typeql_has("ontology-data-state", row.get("dataState"))
            + typeql_has("ontology-change-state", row.get("changeState"))
            + typeql_has("ontology-conflict-state", row.get("conflictState"))
            + typeql_has("ontology-validation-state", row.get("validationState"))
            + typeql_has("ontology-transition-type", row.get("transitionType"))
            + typeql_has("ontology-signal-group", row.get("signalGroup"))
            + typeql_has_bool_string("ontology-materiality-passed", row.get("materialityPassed"))
            + typeql_has("ontology-materiality-state", row.get("materialityState"))
            + typeql_has("ontology-relevance-state", row.get("relevanceState"))
            + typeql_has("ontology-source-trust-state", row.get("sourceTrustState"))
        )

    def batched_node_insert_queries(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        batch_size: int = 40,
        max_query_bytes: int = 0,
    ) -> List[str]:
        items = [row for row in rows or [] if str((row or {}).get("id") or "").strip()]
        maximum_count = max(1, int(batch_size or 40))
        maximum_bytes = max(0, int(max_query_bytes or 0))
        queries: List[str] = []
        clauses: List[str] = []
        query_bytes = self.query_byte_size("insert ")
        for row in items:
            clause = self.node_insert_clause(row, updated_at, "$n" + str(len(clauses))) + ";"
            clause_bytes = self.query_byte_size(clause)
            candidate_bytes = query_bytes + clause_bytes + (1 if clauses else 0)
            if clauses and (len(clauses) >= maximum_count or (maximum_bytes and candidate_bytes > maximum_bytes)):
                queries.append("insert " + " ".join(clauses))
                clauses = []
                query_bytes = self.query_byte_size("insert ")
                clause = self.node_insert_clause(row, updated_at, "$n0") + ";"
                clause_bytes = self.query_byte_size(clause)
            clauses.append(clause)
            query_bytes += clause_bytes + (1 if len(clauses) > 1 else 0)
        if clauses:
            queries.append("insert " + " ".join(clauses))
        return queries

    def node_batch_insert_query(self, rows: Iterable[Dict[str, object]], updated_at: str) -> str:
        inserts = [
            self.node_insert_clause(row, updated_at, "$n" + str(index)) + ";"
            for index, row in enumerate(rows or [])
        ]
        return "insert " + " ".join(inserts)

    def batched_relation_insert_queries(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        batch_size: int = 25,
        max_query_bytes: int = 0,
    ) -> List[str]:
        items = [
            row for row in rows or []
            if str((row or {}).get("source") or "").strip() and str((row or {}).get("target") or "").strip()
        ]
        maximum_count = max(1, int(batch_size or 25))
        maximum_bytes = max(0, int(max_query_bytes or 0))
        queries: List[str] = []
        matches: List[str] = []
        inserts: List[str] = []
        query_bytes = self.query_byte_size("match ") + self.query_byte_size(" insert ")
        for row in items:
            index = len(matches)
            source_var = "$source" + str(index)
            target_var = "$target" + str(index)
            relation_var = "$r" + str(index)
            match = self.relation_match_clause(row, source_var, target_var)
            insert = self.relation_insert_clause(row, updated_at, relation_var, source_var, target_var) + ";"
            candidate_bytes = query_bytes + self.query_byte_size(match) + self.query_byte_size(insert) + (2 if matches else 0)
            if matches and (len(matches) >= maximum_count or (maximum_bytes and candidate_bytes > maximum_bytes)):
                queries.append("match " + " ".join(matches) + " insert " + " ".join(inserts))
                matches = []
                inserts = []
                query_bytes = self.query_byte_size("match ") + self.query_byte_size(" insert ")
                source_var = "$source0"
                target_var = "$target0"
                relation_var = "$r0"
                match = self.relation_match_clause(row, source_var, target_var)
                insert = self.relation_insert_clause(row, updated_at, relation_var, source_var, target_var) + ";"
            matches.append(match)
            inserts.append(insert)
            query_bytes += self.query_byte_size(match) + self.query_byte_size(insert) + (2 if len(matches) > 1 else 0)
        if matches:
            queries.append("match " + " ".join(matches) + " insert " + " ".join(inserts))
        return queries

    def relation_batch_insert_query(self, rows: Iterable[Dict[str, object]], updated_at: str) -> str:
        matches = []
        inserts = []
        for index, row in enumerate(rows or []):
            source_var = "$source" + str(index)
            target_var = "$target" + str(index)
            relation_var = "$r" + str(index)
            matches.append(self.relation_match_clause(row, source_var, target_var))
            inserts.append(self.relation_insert_clause(row, updated_at, relation_var, source_var, target_var) + ";")
        return "match " + " ".join(matches) + " insert " + " ".join(inserts)

    @staticmethod
    def _given_relation_value(value: object, value_type: str) -> object:
        if value_type == "double":
            return float(value)
        return str(value)

    @staticmethod
    def _given_relation_has_value(value: object) -> bool:
        return value is not None and str(value).strip() != ""

    def given_relation_writes_enabled(self, settings: Dict[str, object] = None) -> bool:
        values = dict(runtime_settings() if settings is None else settings or {})
        raw = values.get("typedbABoxGivenRelationWritesEnabled")
        if raw is None:
            # TypeDB 3.12 accepts ``given`` input rows. The legacy query path
            # remains an automatic per-batch fallback for mixed deployments.
            return True
        return str(raw).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def given_relation_batch_size(self, settings: Dict[str, object] = None) -> int:
        values = dict(runtime_settings() if settings is None else settings or {})
        configured = number_or_none(values.get("typedbABoxGivenRelationBatchSize"))
        if configured is None:
            configured = 50
        # The old multi-edge query created independent endpoint matches in a
        # single TypeQL plan. ``given`` keeps one stable plan and streams row
        # values, but a bounded size still limits transaction validation work.
        return max(1, min(250, int(configured)))

    def given_relation_row_values(self, row: Dict[str, object]) -> List[tuple]:
        """Return the stable typed inputs for a relation ``given`` query."""
        relation_id = relation_row_id(row)
        values = [
            ("source-storage-id", "ontology-storage-id", "string", str(
                row.get("sourceStorageId") or ontology_storage_id(row, row.get("source"), "node")
            )),
            ("target-storage-id", "ontology-storage-id", "string", str(
                row.get("targetStorageId") or ontology_storage_id(row, row.get("target"), "node")
            )),
            ("relation-id", "ontology-id", "string", relation_id),
            ("relation-storage-id", "ontology-storage-id", "string", ontology_storage_id(row, relation_id, "relation")),
            (
                "content-fingerprint",
                "ontology-content-fingerprint",
                "string",
                row.get("contentFingerprint")
                or ontology_row_content_fingerprint(row, "relation"),
            ),
            ("relation-type", "ontology-relation-type", "string", row.get("type")),
            ("ontology-box", "ontology-box", "string", row.get("ontologyBox") or "ABox"),
            ("ontology-symbol", "ontology-symbol", "string", row.get("symbol")),
            ("ontology-rule-id", "ontology-rule-id", "string", row.get("ruleId")),
            ("ontology-account-id", "ontology-account-id", "string", row.get("accountId")),
            ("ontology-tenant-id", "ontology-tenant-id", "string", row.get("tenantId")),
            ("ontology-world-id", "ontology-world-id", "string", row.get("worldId")),
            ("ontology-world-type", "ontology-world-type", "string", row.get("worldType")),
            ("ontology-snapshot-id", "ontology-snapshot-id", "string", row.get("snapshotId") or row.get("aboxSnapshotId")),
            ("ontology-scope-id", "ontology-scope-id", "string", row.get("scopeId")),
            ("ontology-scope-type", "ontology-scope-type", "string", row.get("scopeType")),
            ("ontology-manifest-id", "ontology-manifest-id", "string", row.get("manifestId")),
            ("ontology-tbox-class", "ontology-tbox-class", "string", row.get("tboxClass")),
            ("ontology-json", "ontology-json", "string", row.get("propertiesJson")),
            ("ontology-weight", "ontology-weight", "double", row.get("weight")),
            ("ontology-field", "ontology-field", "string", row.get("field")),
            ("ontology-polarity", "ontology-polarity", "string", row.get("polarity")),
            ("ontology-evidence-role", "ontology-evidence-role", "string", row.get("evidenceRole")),
            ("ontology-review-level", "ontology-review-level", "string", row.get("reviewLevel")),
            ("ontology-data-state", "ontology-data-state", "string", row.get("dataState")),
            ("ontology-change-state", "ontology-change-state", "string", row.get("changeState")),
            ("ontology-conflict-state", "ontology-conflict-state", "string", row.get("conflictState")),
            ("ontology-validation-state", "ontology-validation-state", "string", row.get("validationState")),
            ("ontology-transition-type", "ontology-transition-type", "string", row.get("transitionType")),
            ("ontology-signal-group", "ontology-signal-group", "string", row.get("signalGroup")),
            ("ontology-materiality-passed", "ontology-materiality-passed", "string", row.get("materialityPassed")),
            ("ontology-materiality-state", "ontology-materiality-state", "string", row.get("materialityState")),
            ("ontology-relevance-state", "ontology-relevance-state", "string", row.get("relevanceState")),
            ("ontology-source-trust-state", "ontology-source-trust-state", "string", row.get("sourceTrustState")),
        ]
        return [item for item in values if self._given_relation_has_value(item[3])]

    def given_relation_insert_plans(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        settings: Dict[str, object] = None,
    ) -> List[Dict[str, object]]:
        """Build TypeDB 3.12 ``given`` relation inserts grouped by query shape.

        Rows with different optional attributes require different TypeQL
        shapes. Grouping by relation type and attribute presence keeps each
        query plan stable and avoids the old cross-product of independent
        endpoint matches.
        """
        items = [
            dict(row)
            for row in rows or []
            if str((row or {}).get("source") or "").strip()
            and str((row or {}).get("target") or "").strip()
        ]
        if not items:
            return []
        if not self.given_relation_writes_enabled(settings):
            return [{"query": query, "rows": [], "givenRows": []} for query in self.batched_relation_insert_queries(
                items,
                updated_at,
                self.abox_relation_batch_size(settings),
                self.write_query_max_bytes(settings),
            )]

        grouped: Dict[tuple, List[tuple]] = {}
        for row in items:
            fields = self.given_relation_row_values(row)
            relation_type = typedb_relation_storage_type(row.get("type"))
            signature = tuple((name, attribute, value_type) for name, attribute, value_type, _value in fields)
            grouped.setdefault((relation_type, signature), []).append((row, fields))

        plans: List[Dict[str, object]] = []
        maximum = self.given_relation_batch_size(settings)
        for (relation_type, signature), grouped_rows in grouped.items():
            declarations = ", ".join("$" + name + ": " + value_type for name, _attribute, value_type in signature)
            relation_attributes = "".join(
                ", has " + attribute + " == $" + name
                for name, attribute, _value_type in signature
                if name not in {"source-storage-id", "target-storage-id"}
            )
            query = (
                "given " + declarations + "; "
                "match $source isa ontology-node, has ontology-storage-id == $source-storage-id; "
                "$target isa ontology-node, has ontology-storage-id == $target-storage-id; "
                "insert $r isa " + relation_type + ", links (source: $source, target: $target)"
                + relation_attributes
                + ", has ontology-semantic-type " + typedb_string(relation_type)
                + ", has ontology-updated-at " + typedb_string(updated_at)
                + ";"
            )
            for offset in range(0, len(grouped_rows), maximum):
                chunk = grouped_rows[offset: offset + maximum]
                plans.append({
                    "query": query,
                    "rows": [row for row, _fields in chunk],
                    "givenRows": [
                        {
                            name: self._given_relation_value(value, value_type)
                            for name, _attribute, value_type, value in fields
                        }
                        for _row, fields in chunk
                    ],
                    "relationType": relation_type,
                    "rowCount": len(chunk),
                })
        return plans

    def inferencebox_insert_queries(
        self,
        node_rows: Iterable[Dict[str, object]],
        relation_rows: Iterable[Dict[str, object]],
        updated_at: str,
    ) -> List[str]:
        settings = runtime_settings()
        node_batch_size = int(number_or_none(settings.get("typedbInferenceBoxNodeBatchSize")) or 25)
        relation_batch_size = self.inferencebox_relation_batch_size(settings)
        max_query_bytes = self.write_query_max_bytes(settings)
        return [
            *self.batched_node_insert_queries(node_rows, updated_at, node_batch_size, max_query_bytes),
            *self.batched_relation_insert_queries(relation_rows, updated_at, relation_batch_size, max_query_bytes),
        ]

    def inferencebox_given_relation_insert_plans(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        settings: Dict[str, object] = None,
    ) -> List[Dict[str, object]]:
        values = dict(runtime_settings() if settings is None else settings or {})
        values["typedbABoxGivenRelationWritesEnabled"] = (
            "1" if self.inferencebox_given_relation_writes_enabled(values) else "0"
        )
        values["typedbABoxGivenRelationBatchSize"] = str(
            self.inferencebox_given_relation_batch_size(values)
        )
        values["typedbABoxRelationBatchSize"] = str(
            self.inferencebox_relation_batch_size(values)
        )
        return self.given_relation_insert_plans(rows, updated_at, settings=values)

    @staticmethod
    def seed_static_manifest_entity_id() -> str:
        return "ontology-seed-manifest:typedb-static-v1"

    def base_schema_contract_metadata(self) -> Dict[str, str]:
        """Return the immutable TypeDB schema contract required by this build.

        A current static graph does not prove that every promoted TypeQL
        attribute required by the active RuleBox exists. Keep a compact,
        content-addressed schema contract beside the static manifest so a
        costly schema inspection happens only after the actual definition
        changes.
        """
        schema = self.schema_query()
        return {
            "schemaContractVersion": "typedb-base-schema-contract-v2:" + SEMANTIC_STORAGE_CONTRACT_VERSION,
            "schemaContractFingerprint": "typedb-base-schema:"
            + hashlib.sha256(schema.encode("utf-8")).hexdigest()[:24],
        }

    def seed_static_manifest_metadata(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
        tbox_metadata: Dict[str, object] = None,
    ) -> Dict[str, object]:
        """Return the durable identity of the immutable ontology seed.

        The manifest deliberately excludes mutable ABox/InferenceBox data. It
        lets startup compare a small, keyed record instead of reducing every
        row in a multi-gigabyte TypeDB graph merely to prove static boxes have
        not changed.
        """
        expected_entities = graph_box_entity_counts(graph)
        expected_relations = graph_box_relation_counts(graph)
        expected_boxes = self.seed_static_box_names()
        counts = {
            box: {
                "entityCount": int(expected_entities.get(box, 0)),
                "relationCount": int(expected_relations.get(box, 0)),
            }
            for box in expected_boxes
        }
        expected_rulebox = rulebox_runtime_metadata(rules_payload)
        expected_tbox = normalize_tbox_metadata(
            dict(tbox_metadata or default_tbox_metadata())
        )
        language_registry = next(
            (
                item
                for item in graph.entities
                if str(item.kind or "") == "language-registry-version"
            ),
            None,
        )
        metadata = {
            "manifestVersion": "typedb-static-seed-manifest-v1",
            "engineVersion": GRAPH_REASONER_VERSION,
            "tboxVersion": str(expected_tbox.get("version") or ""),
            "tboxFingerprint": str(expected_tbox.get("fingerprint") or ""),
            "ruleboxRulesHash": str(expected_rulebox.get("ruleboxRulesHash") or ""),
            "ruleboxRuleCount": int(expected_rulebox.get("ruleboxRuleCount") or 0),
            "ruleboxConditionCount": int(expected_rulebox.get("ruleboxConditionCount") or 0),
            "ruleboxDerivationCount": int(expected_rulebox.get("ruleboxDerivationCount") or 0),
            "languageRegistryVersion": str(
                (language_registry.properties if language_registry else {}).get("registryVersion") or ""
            ),
            "boxCounts": counts,
        }
        canonical = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = "typedb-static-seed:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        tbox_fingerprint = str(expected_tbox.get("fingerprint") or "")
        rulebox_fingerprint = hashlib.sha256(
            (tbox_fingerprint + ":" + str(expected_rulebox.get("ruleboxRulesHash") or "")).encode("utf-8")
        ).hexdigest()[:24]
        language_fingerprint = hashlib.sha256(
            (tbox_fingerprint + ":" + str(metadata.get("languageRegistryVersion") or "")).encode("utf-8")
        ).hexdigest()[:24]
        schema_contract = self.base_schema_contract_metadata()
        return {
            **metadata,
            "staticSeedFingerprint": fingerprint,
            # Keep storage-schema evolution independent from the immutable
            # TBox/RuleBox graph fingerprint. A new promoted attribute should
            # trigger one schema sync, not a static graph rewrite.
            **schema_contract,
            # Every immutable static box has its own stable physical
            # generation.  RuleBox-only updates can therefore keep linking to
            # the active TBox endpoint, while a TBox change advances all
            # dependent static generations together.
            "tboxSnapshotId": "static-tbox:" + tbox_fingerprint,
            "ruleboxSnapshotId": "static-rulebox:" + rulebox_fingerprint,
            "languageSnapshotId": "static-language:" + language_fingerprint,
        }

    @staticmethod
    def static_seed_generation_ids(metadata: Dict[str, object] = None) -> Dict[str, str]:
        """Resolve active immutable static generations from one manifest."""
        values = dict(metadata or {})
        mappings = {
            "TBox": str(values.get("tboxSnapshotId") or "").strip(),
            "RuleBox": str(values.get("ruleboxSnapshotId") or "").strip(),
            "LanguageGovernance": str(values.get("languageSnapshotId") or "").strip(),
        }
        # Manifests written during the first RuleBox-only rollout carried only
        # the RuleBox ID.  Preserve their read behavior until the next full
        # static seed publishes the richer generation map.
        return {box: generation for box, generation in mappings.items() if generation}

    def seed_static_manifest_graph(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
        tbox_metadata: Dict[str, object] = None,
    ) -> PortfolioOntology:
        metadata = self.seed_static_manifest_metadata(
            graph,
            rules_payload,
            tbox_metadata=tbox_metadata,
        )
        return PortfolioOntology(
            "typedb-static-seed-manifest",
            entities=[OntologyEntity(
                self.seed_static_manifest_entity_id(),
                "TypeDB static ontology seed manifest",
                "ontology-seed-manifest",
                {
                    "ontologyBox": "TBox",
                    "tboxClass": "OntologySeedManifest",
                    **metadata,
                },
            )],
        )

    def seed_static_manifest_storage_id(self) -> str:
        return ontology_storage_id(
            {"ontologyBox": "TBox"},
            self.seed_static_manifest_entity_id(),
            "node",
        )

    def read_seed_static_manifest(self) -> Dict[str, object]:
        """Read the static seed manifest through its unique storage identity."""
        query = (
            "match $n isa ontology-node, has ontology-storage-id "
            + typedb_string(self.seed_static_manifest_storage_id())
            + ", has ontology-json $json; limit 1;"
        )
        try:
            rows = self.read_rows(query, ["json"], label="typedb.static-seed-manifest")
        except Exception as error:  # noqa: BLE001 - caller treats an unreadable manifest as stale.
            return {
                "status": "error",
                "reason": str(error)[:180],
                "metadata": {},
            }
        metadata = json_object((rows[0] if rows else {}).get("json"))
        if not metadata:
            return {"status": "missing", "metadata": {}}
        if str(metadata.get("manifestVersion") or "") != "typedb-static-seed-manifest-v1":
            return {"status": "invalid", "metadata": metadata}
        return {"status": "ok", "metadata": metadata}

    def seed_static_sentinels(
        self,
        graph: PortfolioOntology,
        generation_ids=None,
    ) -> List[Dict[str, str]]:
        """Return stable static records that must exist beside a valid manifest."""
        if isinstance(generation_ids, dict):
            resolved_generation_ids = self.static_seed_generation_ids(generation_ids)
            if not resolved_generation_ids:
                resolved_generation_ids = {
                    str(box or "").strip(): str(value or "").strip()
                    for box, value in generation_ids.items()
                    if str(box or "").strip() and str(value or "").strip()
                }
        else:
            rulebox_snapshot_id = str(generation_ids or "").strip()
            resolved_generation_ids = {"RuleBox": rulebox_snapshot_id} if rulebox_snapshot_id else {}
        static_graph = self.graph_with_static_seed_generation(
            graph,
            self.seed_static_box_names(),
            resolved_generation_ids,
        )
        node_rows, relation_rows = self.graph_persistence_rows(static_graph)
        candidates: List[Tuple[str, Dict[str, object]]] = []
        for row in node_rows:
            node_id = str(row.get("id") or "")
            if node_id == "ontology-box:TBox":
                candidates.append(("tbox", row))
            elif str(row.get("kind") or "") == "rule-registry":
                candidates.append(("rulebox", row))
            elif str(row.get("kind") or "") == "language-registry-version":
                candidates.append(("language", row))
        for row in relation_rows:
            if (
                str(row.get("type") or "") == "DEFINES_RULE"
                and str(row.get("source") or "") == "ontology-box:RuleBox"
            ):
                candidates.append(("rulebox-declaration", row))
                break
        sentinels = []
        seen = set()
        for name, row in candidates:
            if name in seen:
                continue
            seen.add(name)
            owner_kind = "relation" if "source" in row and "target" in row else "node"
            canonical_id = relation_row_id(row) if owner_kind == "relation" else row.get("id")
            sentinels.append({
                "name": name,
                "type": "ontology-assertion" if owner_kind == "relation" else "ontology-node",
                "storageId": ontology_storage_id(row, canonical_id, owner_kind),
            })
        return sentinels

    def seed_static_sentinels_present(
        self,
        graph: PortfolioOntology,
        generation_ids=None,
    ) -> Dict[str, object]:
        missing = []
        try:
            for sentinel in self.seed_static_sentinels(graph, generation_ids):
                rows = self.read_rows(
                    "match $item isa " + sentinel["type"]
                    + ", has ontology-storage-id " + typedb_string(sentinel["storageId"])
                    + "; limit 1;",
                    [],
                    label="typedb.static-seed-sentinel",
                )
                if not rows:
                    missing.append(sentinel["name"])
        except Exception as error:  # noqa: BLE001 - a probe failure is not a valid static seed.
            return {"status": "error", "missing": missing, "reason": str(error)[:180]}
        return {
            "status": "ok" if not missing else "missing",
            "missing": missing,
        }

    def seed_static_node_properties(
        self,
        graph: PortfolioOntology,
        entity_id_value: str,
    ) -> Dict[str, object]:
        node_row = next(
            (
                row
                for row in self.node_rows(graph)
                if str(row.get("id") or "") == str(entity_id_value or "")
            ),
            None,
        )
        if not isinstance(node_row, dict):
            return {}
        query = (
            "match $n isa ontology-node, has ontology-storage-id "
            + typedb_string(ontology_storage_id(node_row, node_row.get("id"), "node"))
            + ", has ontology-json $json; limit 1;"
        )
        rows = self.read_rows(query, ["json"], label="typedb.static-seed-node")
        return json_object((rows[0] if rows else {}).get("json"))

    def legacy_static_seed_preflight(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
        expected: Dict[str, object],
    ) -> Dict[str, object]:
        """Upgrade a legacy seed without an ABox-wide or RuleBox-wide scan.

        There is no historical keyed RuleBox fingerprint to trust.  Instead of
        reading every old rule component, verify the TBox and language anchors
        through their unique storage identities and deterministically replace
        the RuleBox.  The replacement produces the first trustworthy manifest.
        """
        expected_counts = dict(expected.get("boxCounts") or {})
        try:
            tbox_properties = self.seed_static_node_properties(graph, "ontology-box:TBox")
            expected_tbox = default_tbox_metadata()
            tbox_matches = (
                str(tbox_properties.get("tboxFingerprint") or tbox_properties.get("fingerprint") or "")
                == str(expected_tbox.get("fingerprint") or "")
                and str(tbox_properties.get("tboxVersion") or tbox_properties.get("version") or "")
                == str(expected_tbox.get("version") or "")
            )
            language_entity = next(
                (
                    item
                    for item in graph.entities
                    if str(item.kind or "") == "language-registry-version"
                ),
                None,
            )
            language_properties = self.seed_static_node_properties(
                graph,
                str(language_entity.entity_id if language_entity else ""),
            ) if language_entity else {}
            language_registry_matches = (
                language_entity is None
                or str(language_properties.get("registryVersion") or "")
                == str(expected.get("languageRegistryVersion") or "")
            )
        except Exception as error:  # noqa: BLE001 - a legacy seed cannot be trusted after a failed probe.
            return {
                "ready": False,
                "status": "legacy-probe-error",
                "reason": str(error)[:180],
                "preflightMode": "legacy-static-seed-probe",
                "expectedBoxCounts": expected_counts,
                "actualBoxCounts": {},
                "tboxMatches": False,
                "ruleboxMatches": False,
                "languageRegistryMatches": False,
                "schemaContractMatches": False,
            }
        return {
            "ready": False,
            "status": "legacy-manifest-bootstrap-repair",
            "preflightMode": "legacy-static-seed-anchor",
            "expectedBoxCounts": expected_counts,
            "actualBoxCounts": {
                box: dict(expected_counts.get(box) or {})
                for box in self.seed_static_box_names()
                if (
                    (box == "TBox" and tbox_matches)
                    or (box == "LanguageGovernance" and language_registry_matches)
                )
            },
            "tboxMatches": tbox_matches,
            "ruleboxMatches": False,
            "languageRegistryMatches": language_registry_matches,
            "schemaContractMatches": False,
            "staticSeedManifest": {
                "status": "missing",
                "expectedFingerprint": expected.get("staticSeedFingerprint"),
                "bootstrapAction": "replace-rulebox",
            },
        }

    def seed_graph_preflight(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
    ) -> Dict[str, object]:
        """Check immutable boxes without scanning the live ABox.

        TypeDB count reductions filtered by ``ontology-box`` can still plan
        over every persisted assertion. A keyed static manifest plus exact
        sentinel probes gives the startup path a bounded, fail-closed check.
        Legacy stores without a manifest validate their keyed TBox/language
        anchors, replace RuleBox deterministically, and receive the manifest
        only after that bounded repair completes.
        """
        expected = self.seed_static_manifest_metadata(graph, rules_payload)
        expected_counts = dict(expected.get("boxCounts") or {})
        manifest = self.read_seed_static_manifest()
        stored = dict(manifest.get("metadata") or {})
        if str(manifest.get("status") or "") != "ok":
            if str(manifest.get("status") or "") == "missing":
                return self.legacy_static_seed_preflight(graph, rules_payload, expected)
            return {
                "ready": False,
                "status": "manifest-" + str(manifest.get("status") or "unavailable"),
                "reason": str(manifest.get("reason") or "Static seed manifest is absent."),
                "preflightMode": "static-seed-manifest",
                "expectedBoxCounts": expected_counts,
                "actualBoxCounts": dict(stored.get("boxCounts") or {}),
                "tboxMatches": False,
                "ruleboxMatches": False,
                "languageRegistryMatches": False,
                "schemaContractMatches": False,
                "staticSeedManifest": {
                    "status": str(manifest.get("status") or "unavailable"),
                    "expectedFingerprint": expected.get("staticSeedFingerprint"),
                },
            }
        actual_counts = dict(stored.get("boxCounts") or {})
        tbox_matches = (
            str(stored.get("tboxVersion") or "") == str(expected.get("tboxVersion") or "")
            and str(stored.get("tboxFingerprint") or "") == str(expected.get("tboxFingerprint") or "")
            and actual_counts.get("TBox") == expected_counts.get("TBox")
        )
        rulebox_matches = (
            str(stored.get("ruleboxRulesHash") or "") == str(expected.get("ruleboxRulesHash") or "")
            and int(number_or_none(stored.get("ruleboxRuleCount")) or 0)
            == int(number_or_none(expected.get("ruleboxRuleCount")) or 0)
            and int(number_or_none(stored.get("ruleboxConditionCount")) or 0)
            == int(number_or_none(expected.get("ruleboxConditionCount")) or 0)
            and int(number_or_none(stored.get("ruleboxDerivationCount")) or 0)
            == int(number_or_none(expected.get("ruleboxDerivationCount")) or 0)
            and actual_counts.get("RuleBox") == expected_counts.get("RuleBox")
        )
        language_registry_matches = (
            str(stored.get("languageRegistryVersion") or "")
            == str(expected.get("languageRegistryVersion") or "")
            and actual_counts.get("LanguageGovernance") == expected_counts.get("LanguageGovernance")
        )
        fingerprint_matches = (
            str(stored.get("staticSeedFingerprint") or "")
            == str(expected.get("staticSeedFingerprint") or "")
        )
        schema_contract_matches = (
            str(stored.get("schemaContractVersion") or "")
            == str(expected.get("schemaContractVersion") or "")
            and str(stored.get("schemaContractFingerprint") or "")
            == str(expected.get("schemaContractFingerprint") or "")
        )
        sentinels = self.seed_static_sentinels_present(
            graph,
            self.static_seed_generation_ids(stored),
        )
        ready = bool(
            fingerprint_matches
            and tbox_matches
            and rulebox_matches
            and language_registry_matches
            and str(sentinels.get("status") or "") == "ok"
        )
        return {
            "ready": ready,
            "status": "current" if ready else "stale",
            "reason": str(sentinels.get("reason") or ""),
            "preflightMode": "static-seed-manifest",
            "expectedBoxCounts": expected_counts,
            "actualBoxCounts": actual_counts,
            "tboxMatches": tbox_matches,
            "ruleboxMatches": rulebox_matches,
            "languageRegistryMatches": language_registry_matches,
            "schemaContractMatches": schema_contract_matches,
            "staticSeedManifest": {
                "status": "ok",
                "expectedFingerprint": expected.get("staticSeedFingerprint"),
                "activeFingerprint": stored.get("staticSeedFingerprint"),
                "fingerprintMatches": fingerprint_matches,
                "expectedTboxFingerprint": expected.get("tboxFingerprint"),
                "activeTboxFingerprint": stored.get("tboxFingerprint"),
                "tboxMatches": tbox_matches,
                "expectedRuleboxFingerprint": expected.get("ruleboxRulesHash"),
                "activeRuleboxFingerprint": stored.get("ruleboxRulesHash"),
                "ruleboxMatches": rulebox_matches,
                "sentinelStatus": sentinels.get("status"),
                "missingSentinels": list(sentinels.get("missing") or []),
                "expectedSchemaContractFingerprint": expected.get("schemaContractFingerprint"),
                "activeSchemaContractFingerprint": stored.get("schemaContractFingerprint"),
                "schemaContractMatches": schema_contract_matches,
            },
        }

    def seed_relation_repair_eligible(self, preflight: Dict[str, object]) -> bool:
        """Return whether a stale seed can be repaired without replacing nodes.

        A TypeDB process can be interrupted after static nodes are committed but
        before every relation batch is written. Replacing all boxes in that
        state is slow and competes with live ABox projection. A relation-only
        repair is safe when every expected static node is already present and
        no box has more relations than the immutable seed expects.
        """
        if not isinstance(preflight, dict) or preflight.get("status") != "stale":
            return False
        if str(preflight.get("preflightMode") or "") in {
            "static-seed-manifest",
            "legacy-static-seed-anchor",
        }:
            # The manifest is intentionally a bounded startup proof, not a
            # full relation inventory. A failed sentinel must trigger a
            # deterministic static replacement rather than an unbounded scan.
            return False
        expected = preflight.get("expectedBoxCounts") if isinstance(preflight.get("expectedBoxCounts"), dict) else {}
        actual = preflight.get("actualBoxCounts") if isinstance(preflight.get("actualBoxCounts"), dict) else {}
        if not expected or not actual:
            return False
        for box, expected_counts in expected.items():
            if not isinstance(expected_counts, dict):
                return False
            actual_counts = actual.get(box) if isinstance(actual.get(box), dict) else {}
            expected_entities = int(number_or_none(expected_counts.get("entityCount")) or 0)
            expected_relations = int(number_or_none(expected_counts.get("relationCount")) or 0)
            if int(number_or_none(actual_counts.get("entityCount")) or 0) != expected_entities:
                return False
            if int(number_or_none(actual_counts.get("relationCount")) or 0) > expected_relations:
                return False
        return True

    def missing_seed_relation_rows(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        """Return immutable static relation rows absent from the graph store."""
        _node_rows, relation_rows = self.graph_persistence_rows(graph)
        expected_boxes = {
            str(row.get("ontologyBox") or "ABox")
            for row in relation_rows
            if str(row.get("ontologyBox") or "ABox") != "ABox"
        }
        stored_ids = set()
        for box in sorted(expected_boxes):
            rows = self.read_rows(
                "match $r isa ontology-assertion, has ontology-box "
                + typedb_string(box)
                + ", has ontology-id $id;",
                ["id"],
                label="typedb.seed-relation-repair-audit",
            )
            stored_ids.update(str(row.get("id") or "") for row in rows)
        return [
            row
            for row in relation_rows
            if str(row.get("ontologyBox") or "ABox") in expected_boxes
            and relation_row_id(row) not in stored_ids
        ]

    def repair_seed_relations(self, graph: PortfolioOntology) -> Dict[str, object]:
        """Insert only missing static relations after an interrupted seed."""
        if not self.address:
            return {"configured": False, "saved": False, "status": "disabled", "missingRelationCount": 0}
        imported = self.driver_imports()
        if imported[0] is None:
            return self.driver_missing_result(imported[1], graph)
        try:
            missing_rows = self.missing_seed_relation_rows(graph)
            if not missing_rows:
                return {
                    "configured": True,
                    "saved": True,
                    "status": "unchanged",
                    "graphStore": "typedb",
                    "missingRelationCount": 0,
                    "insertedRelationCount": 0,
                }
            settings = runtime_settings()
            relation_batch_size = self.abox_relation_batch_size(settings)
            queries = self.batched_relation_insert_queries(
                missing_rows,
                utc_now(),
                relation_batch_size,
                self.write_query_max_bytes(settings),
            )
            _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]

            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    transaction_query_count = self.graph_write_transaction_query_count(settings)
                    for offset in range(0, len(queries), transaction_query_count):
                        query_batch = queries[offset: offset + transaction_query_count]
                        with typedb_operation_timeout(self.write_operation_timeout_seconds(), "TypeDB seed relation repair"):
                            with driver.transaction(
                                self.database,
                                TransactionType.WRITE,
                                options=self.write_transaction_options(),
                            ) as tx:
                                for query in query_batch:
                                    tx.query(query).resolve()
                                tx.commit()
                finally:
                    self.close_driver(driver)

            self.with_typedb_retries(operation)
            return {
                "configured": True,
                "saved": True,
                "status": "ok",
                "graphStore": "typedb",
                "missingRelationCount": len(missing_rows),
                "insertedRelationCount": len(missing_rows),
                "queryCount": len(queries),
            }
        except Exception as error:  # noqa: BLE001 - caller can fall back to a full deterministic seed.
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "graphStore": "typedb",
                "reason": str(error)[:220],
            }

    @staticmethod
    def seed_static_box_names() -> List[str]:
        return ["TBox", "RuleBox", "LanguageGovernance"]

    def seed_static_boxes_requiring_refresh(self, preflight: Dict[str, object]) -> List[str]:
        """Identify the smallest safe static seed replacement.

        A RuleBox policy edit must not rewrite the TBox or language registry.
        Conversely, a changed TBox can change the meaning of both, so it is
        deliberately promoted to a complete static refresh.  Incomplete
        preflight metadata is treated conservatively as a full static repair.
        """
        static_boxes = self.seed_static_box_names()
        if not isinstance(preflight, dict):
            return static_boxes
        expected = preflight.get("expectedBoxCounts")
        actual = preflight.get("actualBoxCounts")
        required_flags = {"tboxMatches", "ruleboxMatches", "languageRegistryMatches"}
        if (
            not isinstance(expected, dict)
            or not isinstance(actual, dict)
            or not required_flags.issubset(set(preflight))
        ):
            return static_boxes

        def counts_match(box: str) -> bool:
            expected_counts = expected.get(box)
            actual_counts = actual.get(box)
            if not isinstance(expected_counts, dict) or not isinstance(actual_counts, dict):
                return False
            return (
                int(number_or_none(expected_counts.get("entityCount")) or 0)
                == int(number_or_none(actual_counts.get("entityCount")) or 0)
                and int(number_or_none(expected_counts.get("relationCount")) or 0)
                == int(number_or_none(actual_counts.get("relationCount")) or 0)
            )

        tbox_stale = not bool(preflight.get("tboxMatches")) or not counts_match("TBox")
        if tbox_stale:
            return static_boxes
        stale = []
        if not bool(preflight.get("ruleboxMatches")) or not counts_match("RuleBox"):
            stale.append("RuleBox")
        if not bool(preflight.get("languageRegistryMatches")) or not counts_match("LanguageGovernance"):
            stale.append("LanguageGovernance")
        # A stale signal without a diagnosable box is never assumed harmless.
        return stale or static_boxes

    @staticmethod
    def static_seed_schema_prepared(preflight: Dict[str, object]) -> bool:
        """Return whether bounded graph probes already proved the base schema.

        A current manifest is sufficient only when it was written against the
        exact base-schema contract of this build. New, legacy, or upgraded
        databases intentionally return ``False`` and run the bounded schema
        upgrade path before the manifest is republished.
        """
        mode = str((preflight or {}).get("preflightMode") or "")
        status = str((preflight or {}).get("status") or "")
        return (
            mode == "static-seed-manifest"
            and status in {"current", "stale"}
            and bool((preflight or {}).get("schemaContractMatches"))
        )

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
        """Refresh only selected immutable seed boxes through one graph write.

        This bypasses ``save_graph`` because a targeted RuleBox slice carries
        a read-only TBox endpoint for its cross-box declaration relation.  The
        endpoint is used to match the existing node, never inserted or deleted.
        Every static box is append-only. The manifest pointer activates the
        completed TBox/RuleBox/language generation after this write succeeds,
        so a TBox evolution never scans or deletes the live ABox.
        """
        selected_boxes = [
            box
            for box in self.seed_static_box_names()
            if box in {str(item or "").strip() for item in boxes or []}
        ]
        if not selected_boxes:
            return {
                "configured": bool(self.address),
                "saved": True,
                "status": "unchanged",
                "graphStore": "typedb",
                "refreshedBoxes": [],
            }
        if not self.address:
            return {
                "configured": False,
                "saved": False,
                "status": "disabled",
                "graphStore": "typedb",
                "refreshedBoxes": selected_boxes,
                "reason": "TypeDB ontology storage is not configured.",
            }
        imported = self.driver_imports()
        if imported[0] is None:
            result = self.driver_missing_result(imported[1], graph)
            result["refreshedBoxes"] = selected_boxes
            return result
        slice_graph = self.graph_for_boxes(
            graph,
            selected_boxes,
            retain_cross_box_relations=True,
        )
        metadata = self.seed_static_manifest_metadata(
            graph,
            list(rules_payload or rulebox_rules_to_payload(self._last_rules or default_graph_inference_rules())),
            tbox_metadata=tbox_metadata,
        )
        generation_ids = self.static_seed_generation_ids(metadata)
        slice_graph = self.graph_with_static_seed_generation(
            slice_graph,
            self.seed_static_box_names(),
            generation_ids,
        )
        rulebox_generation = str(generation_ids.get("RuleBox") or "")
        # Do not use ``ontology-box`` deletes for static evolution. TypeDB can
        # plan such deletes against the entire durable graph even though the
        # requested box is tiny relative to the ABox. Immutable rows plus the
        # manifest pointer provide atomic read selection without that scan.
        delete_boxes: List[str] = []
        node_rows, relation_rows = self.graph_persistence_rows(slice_graph)
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    if not schema_prepared:
                        self.ensure_schema(driver, imported)
                    self.write_graph(
                        driver,
                        imported,
                        slice_graph,
                        delete_boxes=delete_boxes,
                    )
                finally:
                    self.close_driver(driver)

            self.with_typedb_retries(operation)
        except Exception as error:  # noqa: BLE001 - startup must surface a failed static contract.
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "graphStore": "typedb",
                "refreshedBoxes": selected_boxes,
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:240],
                "entityCount": len(node_rows),
                "relationCount": len(relation_rows),
                "ruleboxSnapshotId": rulebox_generation,
            }
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "refreshedBoxes": selected_boxes,
            "entityCount": len(node_rows),
            "relationCount": len(relation_rows),
            "crossBoxEndpointReferenceCount": len(self.external_relation_endpoint_ids(slice_graph)),
            "ruleboxSnapshotId": rulebox_generation,
            "staticGenerationIds": generation_ids,
            "staticWriteMode": "append-only-static-generation",
        }

    def save_seed_static_manifest(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
        schema_prepared: bool = False,
        tbox_metadata: Dict[str, object] = None,
    ) -> Dict[str, object]:
        """Atomically publish the static seed identity after a successful refresh."""
        manifest_graph = self.seed_static_manifest_graph(
            graph,
            rules_payload,
            tbox_metadata=tbox_metadata,
        )
        metadata = self.seed_static_manifest_metadata(
            graph,
            rules_payload,
            tbox_metadata=tbox_metadata,
        )
        if not self.address:
            return {
                "configured": False,
                "saved": False,
                "status": "disabled",
                "graphStore": "typedb",
                "reason": "TypeDB ontology storage is not configured.",
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return self.driver_missing_result(imported[1], manifest_graph)
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        delete_query = (
            "match $n isa ontology-node, has ontology-storage-id "
            + typedb_string(self.seed_static_manifest_storage_id())
            + "; delete $n;"
        )
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    if not schema_prepared:
                        self.ensure_schema(driver, imported)
                    with typedb_operation_timeout(self.write_operation_timeout_seconds(), "TypeDB static seed manifest delete"):
                        with driver.transaction(
                            self.database,
                            TransactionType.WRITE,
                            options=self.write_transaction_options(),
                        ) as tx:
                            tx.query(delete_query).resolve()
                            tx.commit()
                    self.write_graph(driver, imported, manifest_graph, delete_boxes=[])
                finally:
                    self.close_driver(driver)

            self.with_typedb_retries(operation)
        except Exception as error:  # noqa: BLE001 - no manifest means the next startup repairs static boxes.
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:240],
            }
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "staticSeedFingerprint": metadata.get("staticSeedFingerprint"),
        }

    @coordinated_typedb_projection_write(
        "ontology-release-artifact-seed",
        typedb_projection_world_from_payload,
        bootstrap_schema=True,
    )
    def seed_release_artifact(self, payload: Dict[str, object]) -> Dict[str, object]:
        """Restore an immutable release from its durable static graph artifact."""

        artifact = dict(payload or {})
        if (
            str(artifact.get("version") or "") != "ontology-release-seed-artifact-v2"
            or str(artifact.get("semanticStorageContractVersion") or "")
            != SEMANTIC_STORAGE_CONTRACT_VERSION
            or not dict(artifact.get("releaseBundle") or {})
        ):
            return {
                "configured": True,
                "saved": False,
                "status": "unsupported-release-artifact-contract",
                "artifactVersion": str(artifact.get("version") or ""),
                "semanticStorageContractVersion": str(
                    artifact.get("semanticStorageContractVersion") or ""
                ),
            }
        authored_rules_payload = [
            dict(item)
            for item in list(artifact.get("rules") or [])
            if isinstance(item, dict)
        ]
        try:
            rules = rulebox_rules_from_payload(
                {"rules": authored_rules_payload},
                strict_governance=True,
            )
        except ValueError as error:
            return {
                "configured": True,
                "saved": False,
                "status": "invalid-release-artifact",
                "reason": str(error)[:240],
            }
        expected_rulebox_fingerprint = str(artifact.get("ruleboxFingerprint") or "").strip()
        # The artifact payload is immutable release data. Newer readers may
        # normalize an older governed rule into a different in-memory shape,
        # so hashing a parse/serialize round trip falsely marks a valid frozen
        # release as corrupt. Validate and publish the authored rows exactly;
        # keep parsed rules only as the executable in-process representation.
        actual_rulebox_fingerprint = rulebox_rules_hash(authored_rules_payload)
        tbox_metadata = normalize_tbox_metadata(dict(artifact.get("tboxMetadata") or {}))
        expected_tbox_fingerprint = str(artifact.get("tboxFingerprint") or "").strip()
        if (
            not expected_rulebox_fingerprint
            or expected_rulebox_fingerprint != actual_rulebox_fingerprint
            or not expected_tbox_fingerprint
            or expected_tbox_fingerprint != str(tbox_metadata.get("fingerprint") or "")
        ):
            return {
                "configured": True,
                "saved": False,
                "status": "release-artifact-fingerprint-mismatch",
                "expectedRuleboxFingerprint": expected_rulebox_fingerprint,
                "actualRuleboxFingerprint": actual_rulebox_fingerprint,
                "expectedTboxFingerprint": expected_tbox_fingerprint,
                "actualTboxFingerprint": str(tbox_metadata.get("fingerprint") or ""),
            }
        graph = ontology_seed_graph_from_artifact(artifact)
        box_counts = graph_box_entity_counts(graph)
        missing_boxes = [
            box for box in self.seed_static_box_names()
            if int(box_counts.get(box) or 0) <= 0
        ]
        if missing_boxes:
            return {
                "configured": True,
                "saved": False,
                "status": "release-artifact-static-box-missing",
                "missingBoxes": missing_boxes,
            }
        schema_sync = self.sync_base_schema_contract()
        if not bool(schema_sync.get("saved")):
            return {
                "configured": True,
                "saved": False,
                "status": "release-artifact-schema-sync-failed",
                "schemaSync": schema_sync,
                "reason": str(schema_sync.get("reason") or "")[:240],
            }
        self._last_rules = list(rules)
        static_write = self.save_static_seed_boxes(
            graph,
            self.seed_static_box_names(),
            rules_payload=authored_rules_payload,
            schema_prepared=True,
            tbox_metadata=tbox_metadata,
        )
        if not bool(static_write.get("saved")):
            return {
                "configured": True,
                "saved": False,
                "status": "release-artifact-static-write-failed",
                "staticWrite": static_write,
            }
        manifest = self.save_seed_static_manifest(
            graph,
            authored_rules_payload,
            schema_prepared=True,
            tbox_metadata=tbox_metadata,
        )
        if not bool(manifest.get("saved")):
            return {
                "configured": True,
                "saved": False,
                "status": "release-artifact-manifest-write-failed",
                "staticWrite": static_write,
                "manifest": manifest,
            }
        self.clear_rulebox_snapshot_cache()
        restored_rulebox = dict(self.rulebox_snapshot() or {})
        restored_tbox = dict(self.active_tbox_metadata() or {})
        restored_runtime_rulebox_fingerprint = str(
            restored_rulebox.get("sourceRulesHash")
            or restored_rulebox.get("ruleboxRulesHash")
            or restored_rulebox.get("rulesHash")
            or ""
        ).strip()
        restored_tbox_fingerprint = str(restored_tbox.get("fingerprint") or "").strip()
        restored_manifest = dict(self.read_seed_static_manifest() or {})
        restored_manifest_metadata = dict(restored_manifest.get("metadata") or {})
        restored_artifact_rulebox_fingerprint = str(
            restored_manifest_metadata.get("ruleboxRulesHash") or ""
        ).strip()
        ready = bool(
            str(restored_rulebox.get("status") or "") == "ok"
            and restored_runtime_rulebox_fingerprint
            and str(restored_manifest.get("status") or "") == "ok"
            and restored_artifact_rulebox_fingerprint == expected_rulebox_fingerprint
            and str(restored_tbox.get("status") or "") == "ok"
            and restored_tbox_fingerprint == expected_tbox_fingerprint
        )
        return {
            "configured": True,
            "saved": ready,
            "status": "restored" if ready else "release-artifact-readback-mismatch",
            "ruleCount": len(authored_rules_payload),
            # TypeDB normalizes governed rule rows on readback. Keep the exact
            # authored artifact hash separate from the executable readback
            # hash used by the deployment release identity.
            "ruleboxFingerprint": restored_runtime_rulebox_fingerprint,
            "runtimeRuleboxFingerprint": restored_runtime_rulebox_fingerprint,
            "artifactRuleboxFingerprint": restored_artifact_rulebox_fingerprint,
            "expectedArtifactRuleboxFingerprint": expected_rulebox_fingerprint,
            "tboxFingerprint": restored_tbox_fingerprint,
            "schemaSync": schema_sync,
            "staticWrite": static_write,
            "manifest": manifest,
            "manifestReadback": {
                "status": str(restored_manifest.get("status") or ""),
                "ruleboxFingerprint": restored_artifact_rulebox_fingerprint,
                "tboxFingerprint": str(
                    restored_manifest_metadata.get("tboxFingerprint") or ""
                ),
            },
        }

    @coordinated_typedb_projection_write(
        "ontology-seed",
        typedb_projection_world_from_payload,
        bootstrap_schema=True,
    )
    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = payload or {}
        try:
            rules = rulebox_rules_from_payload(payload, strict_governance=True) if (payload.get("rules") is not None or payload.get("rulesJson")) else default_graph_inference_rules()
        except ValueError as error:
            return {"configured": True, "saved": False, "seeded": False, "status": "invalid-rulebox", "graphStore": "typedb", "reason": str(error)}
        rules = list(rules)
        rules_payload = rulebox_rules_to_payload(rules)
        self._last_rules = rules
        scoped_write_lease_recovery = {}
        if typedb_bool(payload.get("recoverScopedABoxWriteLease")):
            scoped_write_lease_recovery = self.recover_scoped_abox_write_lease_after_server_start()

        def complete_seed(result: Dict[str, object]) -> Dict[str, object]:
            completed = dict(result or {})
            if scoped_write_lease_recovery:
                completed["scopedABoxWriteLeaseRecovery"] = dict(scoped_write_lease_recovery)
            return completed

        seed_graph = ontology_seed_graph(
            rules,
            language_registry=investment_language_registry(runtime_settings()),
        )
        preflight = self.seed_graph_preflight(seed_graph, rules_payload)
        schema_prepared = self.static_seed_schema_prepared(preflight)
        if preflight.get("ready") and not typedb_bool(payload.get("forceReseed")):
            schema_contract_sync = {}
            # A manifest can prove static rows are current while an older
            # TypeDB schema lacks a newly promoted attribute used by the
            # RuleBox. Upgrade only that schema contract before returning the
            # normal static no-op; do not rewrite the static graph or ABox.
            if (
                str(preflight.get("preflightMode") or "") == "static-seed-manifest"
                and not bool(preflight.get("schemaContractMatches"))
            ):
                schema_contract_sync = self.sync_base_schema_contract()
                if not schema_contract_sync.get("saved"):
                    return complete_seed({
                        "configured": True,
                        "saved": False,
                        "seeded": False,
                        "status": "schema-contract-sync-failed",
                        "graphStore": "typedb",
                        "engineVersion": GRAPH_REASONER_VERSION,
                        "ruleCount": len(rules),
                        "seedSkipped": True,
                        "seedPreflight": preflight,
                        "schemaContractSync": schema_contract_sync,
                        "reason": str(schema_contract_sync.get("reason") or "TypeDB schema contract sync failed."),
                    })
                manifest_result = self.save_seed_static_manifest(
                    seed_graph,
                    rules_payload,
                    schema_prepared=True,
                )
                schema_contract_sync["manifest"] = manifest_result
                if not manifest_result.get("saved"):
                    return complete_seed({
                        "configured": True,
                        "saved": False,
                        "seeded": False,
                        "status": "schema-contract-manifest-write-failed",
                        "graphStore": "typedb",
                        "engineVersion": GRAPH_REASONER_VERSION,
                        "ruleCount": len(rules),
                        "seedSkipped": True,
                        "seedPreflight": preflight,
                        "schemaContractSync": schema_contract_sync,
                        "reason": str(manifest_result.get("reason") or "TypeDB schema contract manifest write failed."),
                    })
                preflight = self.seed_graph_preflight(seed_graph, rules_payload)
                if not (preflight.get("ready") and preflight.get("schemaContractMatches")):
                    return complete_seed({
                        "configured": True,
                        "saved": False,
                        "seeded": False,
                        "status": "schema-contract-verification-failed",
                        "graphStore": "typedb",
                        "engineVersion": GRAPH_REASONER_VERSION,
                        "ruleCount": len(rules),
                        "seedSkipped": True,
                        "seedPreflight": preflight,
                        "schemaContractSync": schema_contract_sync,
                        "reason": "The TypeDB static manifest did not confirm the active schema contract.",
                    })
            manifest_bootstrap = {}
            if preflight.get("manifestBootstrapRequired"):
                manifest_bootstrap = self.save_seed_static_manifest(
                    seed_graph,
                    rules_payload,
                    schema_prepared=schema_prepared,
                )
                if not manifest_bootstrap.get("saved"):
                    return complete_seed({
                        "configured": True,
                        "saved": False,
                        "seeded": False,
                        "status": "static-seed-manifest-write-failed",
                        "graphStore": "typedb",
                        "engineVersion": GRAPH_REASONER_VERSION,
                        "ruleCount": len(rules),
                        "seedSkipped": True,
                        "seedPreflight": preflight,
                        "staticSeedManifest": manifest_bootstrap,
                        "reason": str(manifest_bootstrap.get("reason") or "Static seed manifest write failed."),
                    })
            return complete_seed({
                "configured": True,
                "saved": True,
                "seeded": True,
                "status": "unchanged",
                "graphStore": "typedb",
                "engineVersion": GRAPH_REASONER_VERSION,
                "ruleCount": len(rules),
                "seedSkipped": True,
                "seedPreflight": preflight,
                "ruleBoxReplaceRequested": typedb_bool(payload.get("replaceRuleBox")),
                "ruleBoxAlreadyCurrent": True,
                "ruleBoxHashMatched": True,
                "activeRuleBoxRuleCount": len(rules),
                "expectedRuleBoxRuleCount": len(rules),
                "activeRuleBoxShortHash": rulebox_runtime_metadata(rules_payload)["ruleboxShortHash"],
                "expectedRuleBoxShortHash": rulebox_runtime_metadata(rules_payload)["ruleboxShortHash"],
                "staticSeedManifest": manifest_bootstrap,
                "manifestBootstrapped": bool(manifest_bootstrap.get("saved")),
                "schemaContractSync": schema_contract_sync,
            })
        relation_repair = {}
        if not typedb_bool(payload.get("forceReseed")) and self.seed_relation_repair_eligible(preflight):
            relation_repair = self.repair_seed_relations(seed_graph)
            if relation_repair.get("saved"):
                repaired_preflight = self.seed_graph_preflight(seed_graph, rules_payload)
                if repaired_preflight.get("ready"):
                    return complete_seed({
                        "configured": True,
                        "saved": True,
                        "seeded": True,
                        "status": "repaired",
                        "graphStore": "typedb",
                        "engineVersion": GRAPH_REASONER_VERSION,
                        "ruleCount": len(rules),
                        "seedSkipped": False,
                        "seedPreflight": repaired_preflight,
                        "staticRelationRepair": relation_repair,
                        "ruleBoxReplaceRequested": typedb_bool(payload.get("replaceRuleBox")),
                        "ruleBoxAlreadyCurrent": True,
                        "ruleBoxHashMatched": True,
                        "activeRuleBoxRuleCount": len(rules),
                        "expectedRuleBoxRuleCount": len(rules),
                        "activeRuleBoxShortHash": rulebox_runtime_metadata(rules_payload)["ruleboxShortHash"],
                        "expectedRuleBoxShortHash": rulebox_runtime_metadata(rules_payload)["ruleboxShortHash"],
        })
        refresh_boxes = self.seed_static_boxes_requiring_refresh(preflight)
        result = self.save_static_seed_boxes(
            seed_graph,
            refresh_boxes,
            rules_payload=rules_payload,
            schema_prepared=schema_prepared,
        )
        result.update({
            "configured": True,
            "seeded": bool(result.get("saved")),
            "engineVersion": GRAPH_REASONER_VERSION,
            "ruleCount": len(rules),
            "graphStore": "typedb",
            "seedSkipped": False,
            "seedPreflight": preflight,
            "staticBoxRefresh": {
                "mode": "targeted-box-replacement",
                "requestedBoxes": refresh_boxes,
                "refreshedBoxes": list(result.get("refreshedBoxes") or refresh_boxes),
            },
        })
        if relation_repair:
            result["staticRelationRepair"] = relation_repair
        if result.get("saved"):
            manifest_result = self.save_seed_static_manifest(
                seed_graph,
                rules_payload,
                schema_prepared=True,
            )
            result["staticSeedManifest"] = manifest_result
            if not manifest_result.get("saved"):
                result.update({
                    "saved": False,
                    "seeded": False,
                    "status": "static-seed-manifest-write-failed",
                    "reason": str(manifest_result.get("reason") or "Static seed manifest write failed."),
                })
        if result.get("saved"):
            self.clear_rulebox_snapshot_cache()
            post_seed_preflight = self.seed_graph_preflight(seed_graph, rules_payload)
            result["postSeedPreflight"] = post_seed_preflight
            result["staticBoxRefresh"]["verified"] = bool(post_seed_preflight.get("ready"))
            if not post_seed_preflight.get("ready"):
                result.update({
                    "saved": False,
                    "seeded": False,
                    "status": "static-seed-verification-failed",
                    "reason": "Targeted static seed replacement did not pass the post-write completeness check.",
                })
        if typedb_bool(payload.get("replaceRuleBox")) and result.get("saved"):
            expected_rulebox = rulebox_runtime_metadata(rules_payload)
            # ``seed_graph`` already replaced RuleBox in the same graph write.
            # Read it back for verification instead of performing a second full
            # RuleBox replacement during every service startup.
            self.clear_rulebox_snapshot_cache()
            rulebox_result = self.rulebox_snapshot()
            expected_structure = rulebox_structural_fingerprint(rules_payload)
            active_rules_payload = rulebox_result.get("rules") if isinstance(rulebox_result.get("rules"), list) else []
            active_structure = rulebox_structural_fingerprint(active_rules_payload)
            active_rule_count = int(number_or_none(rulebox_result.get("ruleCount") or rulebox_result.get("ruleboxRuleCount")) or 0)
            active_rule_hash = str(rulebox_result.get("ruleboxRulesHash") or "")
            hash_matched = active_rule_hash == expected_rulebox["ruleboxRulesHash"]
            replace_verified = (
                bool(rulebox_result.get("saved"))
                and str(rulebox_result.get("status") or "") == "ok"
                and active_rule_count == len(rules_payload)
                and active_structure == expected_structure
            )
            result.update({
                "ruleBoxReplaceRequested": True,
                "ruleBoxReplaced": replace_verified,
                "ruleBoxHashMatched": hash_matched,
                "activeRuleBoxRuleCount": active_rule_count,
                "expectedRuleBoxRuleCount": len(rules_payload),
                "activeRuleBoxHash": active_rule_hash,
                "expectedRuleBoxHash": expected_rulebox["ruleboxRulesHash"],
                "activeRuleBoxShortHash": str(rulebox_result.get("ruleboxShortHash") or active_rule_hash[:12]),
                "expectedRuleBoxShortHash": expected_rulebox["ruleboxShortHash"],
                "ruleBoxReplaceResult": {
                    "saved": bool(rulebox_result.get("saved")),
                    "status": rulebox_result.get("status") or "",
                    "reason": rulebox_result.get("reason") or "",
                    "ruleCount": active_rule_count,
                    "conditionCount": int(number_or_none(rulebox_result.get("conditionCount") or rulebox_result.get("ruleboxConditionCount")) or 0),
                    "derivationCount": int(number_or_none(rulebox_result.get("derivationCount") or rulebox_result.get("ruleboxDerivationCount")) or 0),
                    "ruleboxRulesHash": active_rule_hash,
                    "ruleboxShortHash": str(rulebox_result.get("ruleboxShortHash") or active_rule_hash[:12]),
                },
            })
            if replace_verified and typedb_bool(payload.get("clearInference")):
                result["clearInferenceResult"] = self.clear_inferencebox()
            if not replace_verified:
                result.update({
                    "saved": False,
                    "seeded": False,
                    "status": rulebox_result.get("status") or "rulebox-replace-failed",
                    "reason": (
                        "RuleBox replace requested but active RuleBox did not match the seeded rules. "
                        + str(rulebox_result.get("reason") or "")
                    ).strip(),
                })
        return complete_seed(result)

    def rulebox_snapshot(self) -> Dict[str, object]:
        if not self.address:
            return NullTypeDBOntologyGraphRepository().rulebox_snapshot()
        cache_age = time.time() - float(self._rulebox_snapshot_cache_at or 0)
        if self._rulebox_snapshot_cache_result and cache_age <= self.rulebox_snapshot_cache_seconds():
            cached = copy.deepcopy(self._rulebox_snapshot_cache_result)
            cached["cached"] = True
            cached["ruleBoxSnapshotCached"] = True
            return cached
        manifest = self.read_seed_static_manifest()
        rulebox_snapshot_id = str(
            (manifest.get("metadata") or {}).get("ruleboxSnapshotId") or ""
        ).strip() if str(manifest.get("status") or "") == "ok" else ""
        full_cache_age = time.time() - float(
            self._rulebox_snapshot_cache_full_load_at or 0
        )
        maximum_full_cache_age = max(
            300.0,
            min(1800.0, self.rulebox_snapshot_cache_seconds() * 10.0),
        )
        cached_snapshot_id = str(
            (self._rulebox_snapshot_cache_result or {}).get("ruleboxSnapshotId") or ""
        ).strip()
        if (
            self._rulebox_snapshot_cache_result
            and rulebox_snapshot_id
            and cached_snapshot_id == rulebox_snapshot_id
            and full_cache_age <= maximum_full_cache_age
        ):
            # RuleBox rows are immutable under a content-addressed static
            # manifest. A lightweight manifest read is enough to prove that
            # the executable policy is unchanged; periodically force a full
            # governance refresh so cross-process version history remains
            # visible as well.
            self._rulebox_snapshot_cache_at = time.time()
            cached = copy.deepcopy(self._rulebox_snapshot_cache_result)
            cached["cached"] = True
            cached["ruleBoxSnapshotCached"] = True
            cached["ruleBoxManifestRevalidated"] = True
            return cached
        try:
            if rulebox_snapshot_id:
                entities = [
                    *self.read_entity_rows(["RuleBox"], snapshot_id=rulebox_snapshot_id),
                    *self.read_entity_rows(["RuleBoxGovernance"]),
                ]
                relations = [
                    *self.read_relation_rows(["RuleBox"], snapshot_id=rulebox_snapshot_id),
                    *self.read_relation_rows(["RuleBoxGovernance"]),
                ]
            else:
                entities = self.read_entity_rows(["RuleBox", "RuleBoxGovernance"])
                relations = self.read_relation_rows(["RuleBox", "RuleBoxGovernance"])
        except Exception as error:  # noqa: BLE001 - admin read model must fail closed.
            rules = rulebox_rules_to_payload(self._last_rules or default_graph_inference_rules())
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "source": "typedb-typeql",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
                "engineVersion": GRAPH_REASONER_VERSION,
                "rules": [],
                "ruleCount": 0,
                "conditionCount": 0,
                "derivationCount": 0,
                "relationTypes": [],
                "defaultsFallbackUsed": False,
                "bootstrapAvailable": True,
                "bootstrapRuleCount": len(rules),
                "bootstrapRules": rules,
                "versions": [],
                "versionCount": 0,
                "changeCandidates": rulebox_governance_candidates([], []),
            }
        rowsets = {
            "rules": [row for row in entities if entity_node_kind(row) == "rule" and row.get("ontologyBox") == "RuleBox"],
            "conditions": [row for row in entities if entity_node_kind(row) == "rule-condition" and row.get("ontologyBox") == "RuleBox"],
            "derivations": [row for row in entities if entity_node_kind(row) == "relation-template" and row.get("ontologyBox") == "RuleBox"],
            "relationTypes": relation_type_rows_from_derivations(entities, relations),
            "versions": [row for row in entities if entity_node_kind(row) == "rulebox-version" and row.get("ontologyBox") == "RuleBoxGovernance"],
            "candidates": [row for row in entities if entity_node_kind(row) == "rule-change-candidate" and row.get("ontologyBox") == "RuleBoxGovernance"],
        }
        snapshot = rulebox_snapshot_from_rows(rowsets, "typedb-typeql")
        snapshot.update({
            "graphStore": "typedb",
            "source": "typedb-typeql",
            "ruleboxSnapshotId": rulebox_snapshot_id,
        })
        snapshot.update(rulebox_runtime_metadata(snapshot.get("rules") if isinstance(snapshot.get("rules"), list) else []))
        if snapshot.get("status") == "ok":
            try:
                self._last_rules = rulebox_rules_from_payload({"rules": snapshot.get("rules") or []})
            except ValueError:
                pass
        snapshot["nativeReasoningProfile"] = typedb_native_reasoning_profile(snapshot.get("rules") or [])
        snapshot["ruleBoxSnapshotCached"] = False
        self._rulebox_snapshot_cache_at = time.time()
        self._rulebox_snapshot_cache_full_load_at = self._rulebox_snapshot_cache_at
        self._rulebox_snapshot_cache_result = copy.deepcopy(snapshot)
        return snapshot

    @coordinated_typedb_projection_write(
        "rulebox-save",
        typedb_projection_world_from_payload,
    )
    def save_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        try:
            rules = rulebox_rules_from_payload(payload or {}, strict_governance=True)
        except ValueError as error:
            return {"configured": True, "saved": False, "status": "invalid-rulebox", "graphStore": "typedb", "reason": str(error)}
        source = dict(payload or {}) if isinstance(payload, dict) else {}
        version = rulebox_version_payload(
            rules,
            utc_now(),
            str(source.get("changeReason") or ""),
            str(source.get("author") or "local-admin"),
            str(source.get("status") or "saved"),
        )
        baseline_result = {}
        # The first governed save must retain the previous active RuleBox as a
        # restoration point.  Later writes already have immutable versions.
        try:
            previous_snapshot = self.rulebox_snapshot()
            previous_rows = previous_snapshot.get("rules") if isinstance(previous_snapshot.get("rules"), list) else []
            previous_versions = previous_snapshot.get("versions") if isinstance(previous_snapshot.get("versions"), list) else []
            if previous_rows and not previous_versions:
                previous_rules = rulebox_rules_from_payload({"rules": previous_rows})
                baseline = rulebox_version_payload(
                    previous_rules,
                    utc_now(),
                    "정책 변경 전 자동 기준선",
                    "system-baseline",
                    "baseline",
                )
                baseline_result = self.append_rulebox_version(baseline)
        except Exception as error:  # noqa: BLE001 - a failed audit append must not hide a valid active RuleBox.
            baseline_result = {
                "saved": False,
                "status": "error",
                "reason": str(error)[:220],
            }
        self._last_rules = list(rules)
        self.clear_rulebox_snapshot_cache()
        # RuleBox is an immutable static generation. Route an admin edit
        # through the same seed/manifest boundary used at startup so a policy
        # save never broad-deletes the durable graph before its replacement is
        # available for TypeDB-native inference.
        save_result = self.seed_ontology({
            "rules": rulebox_rules_to_payload(self._last_rules),
            "replaceRuleBox": True,
            "clearInference": False,
        })
        version_result = {}
        if bool(save_result.get("saved")):
            version_result = self.append_rulebox_version(version)
        self.clear_rulebox_snapshot_cache()
        snapshot = self.rulebox_snapshot()
        snapshot.update({
            "saved": bool(save_result.get("saved")),
            "status": save_result.get("status") or snapshot.get("status"),
            "reason": save_result.get("reason") or snapshot.get("reason") or "",
            "saveResult": save_result,
            "savedVersion": {
                key: version.get(key)
                for key in [
                    "id", "versionLabel", "rulesHash", "shortHash", "ruleCount", "conditionCount",
                    "derivationCount", "createdAt", "changeReason", "author", "status",
                ]
            } if bool(version_result.get("saved")) else {},
            "versionAudit": version_result or {
                "saved": False,
                "status": "skipped",
                "reason": "RuleBox 저장이 완료되지 않아 버전 기록을 만들지 않았습니다.",
            },
            "preSaveBaselineAudit": baseline_result,
        })
        return snapshot

    @coordinated_typedb_projection_write("rulebox-version-append")
    def append_rulebox_version(self, version: Dict[str, object]) -> Dict[str, object]:
        """Append immutable RuleBox governance history without replacing it.

        A normal static graph save replaces every row in the boxes contained
        in the graph.  Version history must survive a later RuleBox edit, so
        this write deliberately has no delete phase.
        """
        if not self.address:
            return {
                "configured": False,
                "saved": False,
                "status": "disabled",
                "graphStore": "typedb",
                "reason": "TypeDB ontology storage is not configured.",
            }
        imported = self.driver_imports()
        if imported[0] is None:
            return self.driver_missing_result(imported[1], PortfolioOntology("typedb-rulebox-governance"))
        graph = PortfolioOntology("typedb-rulebox-governance")
        add_rulebox_version_concept(graph, version)
        if not graph.entities:
            return {
                "configured": True,
                "saved": False,
                "status": "invalid-version",
                "graphStore": "typedb",
                "reason": "RuleBox version payload is missing its ID.",
            }
        try:
            def operation():
                driver = self.open_driver(imported)
                try:
                    self.ensure_database(driver)
                    self.ensure_schema(driver, imported)
                    self.write_graph(driver, imported, graph, delete_boxes=[])
                finally:
                    self.close_driver(driver)
            self.with_typedb_retries(operation)
        except Exception as error:  # noqa: BLE001 - preserve a saved RuleBox even if its audit append failed.
            return {
                "configured": True,
                "saved": False,
                "status": "error",
                "graphStore": "typedb",
                "reasonCode": typedb_error_code(error),
                "reason": str(error)[:220],
                "versionId": str(version.get("id") or ""),
            }
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "versionId": str(version.get("id") or ""),
        }

    def restore_rulebox_version(
        self,
        version_id: str,
        change_reason: str = "",
        author: str = "",
    ) -> Dict[str, object]:
        target = str(version_id or "").strip()
        if not target:
            return {
                "configured": bool(self.address),
                "saved": False,
                "status": "invalid-version",
                "graphStore": "typedb",
                "reason": "RuleBox version ID is required.",
            }
        snapshot = self.rulebox_snapshot()
        version = next(
            (item for item in snapshot.get("versions") or [] if str(item.get("id") or "").strip() == target),
            None,
        )
        if not isinstance(version, dict):
            return {
                "configured": bool(self.address),
                "saved": False,
                "status": "version-not-found",
                "graphStore": "typedb",
                "reason": "RuleBox version was not found: " + target,
            }
        try:
            rules = json.loads(str(version.get("rulesJson") or "[]"))
        except json.JSONDecodeError as error:
            return {
                "configured": bool(self.address),
                "saved": False,
                "status": "invalid-version",
                "graphStore": "typedb",
                "reason": "Stored RuleBox version is not valid JSON: " + str(error),
            }
        result = self.save_rulebox({
            "rules": rules,
            "changeReason": str(change_reason or "").strip() or ("RuleBox 버전 복원: " + target),
            "author": str(author or "local-admin").strip() or "local-admin",
            "status": "restored",
            "source": "rulebox-version-restore",
        })
        result["restoredVersionId"] = target
        return result

    def ensure_rulebox_version_baseline(self, author: str = "") -> Dict[str, object]:
        """Record the active RuleBox once without changing its executable rows."""
        snapshot = self.rulebox_snapshot()
        if str(snapshot.get("status") or "") != "ok":
            return {
                "configured": bool(self.address),
                "saved": False,
                "status": str(snapshot.get("status") or "unavailable"),
                "graphStore": "typedb",
                "reason": str(snapshot.get("reason") or "현재 RuleBox를 읽지 못했습니다."),
            }
        existing = snapshot.get("versions") if isinstance(snapshot.get("versions"), list) else []
        if existing:
            return {
                "configured": True,
                "saved": False,
                "status": "unchanged",
                "graphStore": "typedb",
                "versionCount": len(existing),
                "reason": "기존 RuleBox 버전이 이미 있습니다.",
            }
        try:
            rules = rulebox_rules_from_payload({"rules": snapshot.get("rules") or []})
        except ValueError as error:
            return {
                "configured": True,
                "saved": False,
                "status": "invalid-rulebox",
                "graphStore": "typedb",
                "reason": str(error),
            }
        version = rulebox_version_payload(
            rules,
            utc_now(),
            "기존 활성 RuleBox 기준선 기록",
            str(author or "system-baseline").strip() or "system-baseline",
            "baseline",
        )
        result = self.append_rulebox_version(version)
        self.clear_rulebox_snapshot_cache()
        result["baselineVersion"] = {
            key: version.get(key)
            for key in ["id", "versionLabel", "shortHash", "rulesHash", "createdAt", "changeReason", "author", "status"]
        }
        return result

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

    def save_rule_change_candidates(self, candidates: List[Dict[str, object]], context: Dict[str, object] = None) -> Dict[str, object]:
        if not self._last_rules:
            try:
                snapshot = self.rulebox_snapshot()
                self._last_rules = rulebox_rules_from_payload({"rules": snapshot.get("rules") or []})
            except ValueError:
                self._last_rules = []
        normalized = [
            normalize_rule_change_candidate(candidate, existing_rule_ids=[rule.rule_id for rule in self._last_rules])
            for candidate in (candidates or [])
            if isinstance(candidate, dict)
        ]
        normalized = [item for item in normalized if item]
        if not normalized:
            return {"configured": bool(self.address), "status": "no-candidates", "graphStore": "typedb", "candidateCount": 0, "savedCount": 0}
        graph = PortfolioOntology("typedb-rule-change-candidates")
        for item in normalized:
            from ..domain.ontology_contracts import OntologyEntity

            graph.entities.append(OntologyEntity(
                "rule-change-candidate:" + str(item.get("id") or item.get("title") or len(graph.entities)),
                str(item.get("title") or "Rule change candidate"),
                "rule-change-candidate",
                {
                    "ontologyBox": "RuleBoxGovernance",
                    "boundedContext": "reasoning-insight",
                    "tboxClass": "RuleChangeCandidate",
                    "properties": item,
                },
            ))
        save_result = self.save_graph(graph)
        return {
            "configured": bool(self.address),
            "status": save_result.get("status"),
            "graphStore": "typedb",
            "candidateCount": len(normalized),
            "savedCount": len(normalized) if save_result.get("saved") else 0,
            "saveResult": save_result,
        }


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
