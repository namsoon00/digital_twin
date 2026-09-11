"""native_execution: matching through explicit injected capabilities."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from digital_twin.modules.model_registry.contracts import is_model_signal_interpretation_rule
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_execution_units import rules_allow_subject_fanout
from digital_twin.modules.reasoning.domain.ontology_native_rule_planning import normalize_native_rule_planner_topology
from digital_twin.modules.model_registry.contracts import GraphInferenceRule
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.typeql.constants import (
    TYPEDB_NATIVE_RULE_ENGINE_VERSION,
)
from digital_twin.modules.reasoning.infrastructure.typeql.indexed_queries import (
    typedb_native_rule_runtime_query_plan,
)
from digital_twin.modules.reasoning.infrastructure.typeql.model_signal_queries import (
    typedb_model_signal_bridge_batch_plan,
    typedb_model_signal_bridge_batch_plan_summary,
)
from digital_twin.modules.reasoning.infrastructure.typeql.planning import (
    typedb_native_rule_adaptive_target_parallelism_by_rule_id,
    typedb_native_rule_execution_plan,
    typedb_native_rule_execution_plan_summary,
    typedb_native_rule_target_work_plan,
    typedb_rule_execution_failure_partition,
    typedb_rule_execution_profile_fields,
)
from digital_twin.modules.reasoning.infrastructure.typeql.profiles import typedb_native_rule_profile
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
    normalized_condition_role,
    typedb_native_rule_id,
    typedb_planned_candidate_symbols,
    typedb_rule_condition_payloads,
    typedb_rule_is_enabled,
)
from typing import Dict, Iterable, List
import time
from .matching_ports import NativeExecutionMatchingStore, NativeExecutionMatchingRuntime


def match_typedb_native_rules(
    _store: NativeExecutionMatchingStore,
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
    *,
    _bindings: NativeExecutionMatchingRuntime
) -> Dict[str, object]:
    rules = [rule for rule in rules or [] if typedb_rule_is_enabled(rule)]
    clean_symbols = clean_symbols_from_payload(list(target_symbols or []))
    if (
        stable_abox_write_lease_held
        and _store.native_rule_subject_fanout_enabled()
        and len(clean_symbols) > 1
        and rules_allow_subject_fanout(rules)
    ):
        return _store.match_typedb_native_rules_by_subject(
            rules,
            clean_symbols,
            world_id=world_id,
            planner_topology=planner_topology,
            preflight_graph=preflight_graph,
            preflight_incoming_relations_complete=preflight_incoming_relations_complete,
            evidence_read_index=evidence_read_index,
        )
    execution_mode = "typedb-scoped-typeql"
    matches: List[Dict[str, object]] = []
    match_index: Dict[str, Dict[str, object]] = {}
    executed_rules = []
    skipped_rules = []
    read_call_count = 0
    read_transaction_count = 0
    execution_plan: Dict[str, object] = {}
    query_failures = []
    execution_budget_exhausted = False
    execution_incomplete = False
    isolated_entry_execution = False
    parallel_rule_execution = False
    effective_parallelism = 1
    any_condition_parallelism_cap = 1
    any_condition_rule_count = 0
    native_rule_execution_phases: Dict[str, object] = {}
    indexed_evidence_query_used = False
    evidence_index_hydration: Dict[str, object] = {}
    timeout_fallback_rule_count = 0
    timeout_fallback_shard_count = 0
    adaptive_target_sharding_profile = (
        dict(adaptive_target_sharding_profile or {})
        if isinstance(adaptive_target_sharding_profile, dict)
        else {}
    )
    adaptive_target_parallelism_by_rule_id: Dict[str, int] = {}
    model_signal_batch_plan: Dict[str, object] = {
        "status": "not-planned",
        "logicalModelSignalPolicyCount": 0,
        "batchedSimplePolicyCount": 0,
        "constrainedPolicyCount": 0,
        "modelSignalBridgeReadCount": 0,
        "eliminatedModelSignalPolicyQueryCount": 0,
        "plannedModelSignalQueryCount": 0,
        "batches": [],
        "regularEntries": [],
    }
    bridge_batch_result: Dict[str, object] = {}
    model_signal_ignored_contract_ids: List[str] = []
    adaptive_target_sharding_profile_status = str(
        adaptive_target_sharding_profile.get("status") or "not-requested"
    )
    requested_target_parallelism = max(
        1,
        min(8, int(number_or_none(native_rule_target_parallelism) or 1)),
    )
    target_work_plan: Dict[str, object] = {
        "requestedTargetParallelism": requested_target_parallelism,
        "effectiveTargetParallelism": 1,
        "targetSymbols": list(clean_symbols),
        "targetSymbolCount": len(clean_symbols),
        "targetWorkShardingUsed": False,
        "targetWorkShardCount": 1 if clean_symbols else 0,
        "targetWorkItemCount": 0,
        "targetWorkOriginalEntryCount": 0,
        "targetWorkShardedRuleCount": 0,
        "targetWorkAdaptiveShardingUsed": False,
        "targetWorkAdaptiveShardedRuleCount": 0,
        "targetWorkAdaptiveShardedRuleIds": [],
        "workItems": [],
    }
    try:
        relation_types_by_symbol: Dict[str, Iterable[str]] = {}
        subject_properties_by_symbol: Dict[str, Dict[str, object]] = {}
        relation_evidence_by_symbol: Dict[str, List[Dict[str, object]]] = {}
        relation_evidence_complete_by_symbol: Dict[str, bool] = {}
        rule_context: Dict[str, object] = {}
        structural_execution_plan: Dict[str, object] = {}
        if clean_symbols:
            topology = (
                normalize_native_rule_planner_topology(
                    planner_topology,
                    target_symbols=clean_symbols,
                )
                if planner_topology
                else {}
            )
            if str(topology.get("status") or "") == "ok":
                relation_types_by_symbol = dict(topology.get("relationTypesBySymbol") or {})
                source_ids_by_symbol = dict(topology.get("sourceIdsBySymbol") or {})
                subject_properties_by_symbol = dict(topology.get("subjectPropertiesBySymbol") or {})
                relation_evidence_by_symbol = dict(topology.get("relationEvidenceBySymbol") or {})
                relation_evidence_complete_by_symbol = dict(
                    topology.get("relationEvidenceCompleteBySymbol") or {}
                )
                source_count = sum(
                    1
                    for symbol in clean_symbols
                    for source_id in source_ids_by_symbol.get(symbol, []) or []
                    if str(source_id or "").strip()
                )
                rule_context = {
                    "status": "ok",
                    "symbols": clean_symbols,
                    "source": "persisted-projection-graph-topology",
                    "plannerTopologyFingerprint": str(topology.get("fingerprint") or ""),
                    "relationTypesBySymbol": relation_types_by_symbol,
                    "sourceIdsBySymbol": source_ids_by_symbol,
                    "subjectPropertyIndexAvailable": bool(
                        topology.get("subjectPropertyIndexAvailable")
                    ),
                    "relationEvidenceIndexAvailable": bool(
                        topology.get("relationEvidenceIndexAvailable")
                    ),
                    "preflightStatus": "persisted-projection-topology",
                    "preflightSourceCount": source_count,
                }
            else:
                try:
                    rule_context = _store.active_abox_rule_context(clean_symbols, world_id)
                    if str(rule_context.get("status") or "") != "ok":
                        raise RuntimeError("TypeDB active ABox rule context is unavailable.")
                    relation_types_by_symbol = dict(rule_context.get("relationTypesBySymbol") or {})
                    source_ids_by_symbol = dict(rule_context.get("sourceIdsBySymbol") or {})
                    source_count = sum(
                        1
                        for symbol in clean_symbols
                        for source_id in source_ids_by_symbol.get(symbol, []) or []
                        if str(source_id or "").strip()
                    )
                    # The fallback preserves compatibility for a manifest
                    # written before structural planner topology existed.
                    # TypeDB functions remain the sole rule evaluator.
                    rule_context.update(
                        {
                            "preflightStatus": "typedb-active-abox-topology-fallback",
                            "preflightSourceCount": source_count,
                        }
                    )
                except (
                    Exception
                ) as error:  # noqa: BLE001 - planner topology is an optimization, never a reason to stall all rule functions.
                    rule_context = {
                        "status": "degraded",
                        "symbols": clean_symbols,
                        "reason": str(error)[:220],
                        "relationTypesBySymbol": {},
                        "preflightStatus": "degraded",
                    }
        if clean_symbols and relation_types_by_symbol:
            structural_execution_plan = typedb_native_rule_execution_plan(
                rules,
                clean_symbols,
                relation_types_by_symbol,
                subject_properties_by_symbol=subject_properties_by_symbol,
                relation_evidence_by_symbol=relation_evidence_by_symbol,
                relation_evidence_complete_by_symbol=relation_evidence_complete_by_symbol,
            )
            rule_context.update(
                {
                    "structuralCandidateRuleCount": int(
                        structural_execution_plan.get("candidateRuleCount") or 0
                    ),
                    "structuralSelectedRuleCount": int(
                        structural_execution_plan.get("selectedRuleCount") or 0
                    ),
                    "structuralPrunedRuleCount": int(
                        structural_execution_plan.get("skippedRuleCount") or 0
                    ),
                }
            )
        if clean_symbols and str(dict(evidence_read_index or {}).get("status") or "") == "verified":
            hydration_rules = [
                item.get("rule")
                for item in structural_execution_plan.get("selectedEntries") or []
                if isinstance(item, dict) and item.get("rule")
            ] or list(rules)
            indexed_relation_types = sorted(
                {
                    str(condition.get("relation_type") or condition.get("relationType") or "")
                    .upper()
                    .strip()
                    for rule in hydration_rules
                    for condition in typedb_rule_condition_payloads(rule)
                    if str(condition.get("kind") or "") == "relation"
                    and normalized_condition_role(condition) != "not"
                    and str(
                        condition.get("relation_type") or condition.get("relationType") or ""
                    ).strip()
                }
            )
            if indexed_relation_types:
                evidence_index_hydration = _store.hydrate_native_rule_evidence_field_index(
                    evidence_read_index,
                    clean_symbols,
                    indexed_relation_types,
                )
                evidence_read_index = dict(
                    evidence_index_hydration.get("evidence") or evidence_read_index or {}
                )
                read_call_count += int(evidence_index_hydration.get("readQueryCount") or 0)
                read_transaction_count += int(
                    evidence_index_hydration.get("readTransactionCount") or 0
                )
                rule_context["evidenceFieldIndexStatus"] = str(
                    evidence_index_hydration.get("status") or ""
                )
                rule_context["evidenceFieldIndexRowCount"] = int(
                    evidence_index_hydration.get("fieldRowCount") or 0
                )
                rule_context["evidenceFieldHydratedRelationTypeCount"] = len(indexed_relation_types)
        execution_plan = typedb_native_rule_execution_plan(
            rules,
            clean_symbols,
            relation_types_by_symbol,
            # A selected symbol is one inference unit.  Limiting individual
            # rule calls here meant that a stable priority list could defer
            # the same applicable rules forever and materialize a partial
            # judgement.  Symbol scheduling happens before this method;
            # TypeDB must evaluate every applicable rule for that symbol.
            0,
            # A preflight graph is accepted only when the caller read it
            # through the active Manifest's exact storage index. It can
            # prove an impossible required condition and skip that TypeDB
            # function, but it never accepts a match or makes a decision.
            preflight_graph=preflight_graph,
            preflight_incoming_relations_complete=preflight_incoming_relations_complete,
            subject_properties_by_symbol=subject_properties_by_symbol,
            relation_evidence_by_symbol=relation_evidence_by_symbol,
            relation_evidence_complete_by_symbol=relation_evidence_complete_by_symbol,
        )
        for item in execution_plan.get("skippedEntries") or []:
            skipped_rules.append(
                {
                    "ruleId": str(item.get("ruleId") or ""),
                    "status": str(item.get("status") or "skipped"),
                    "reason": str(item.get("reason") or "")[:220],
                    "requiredRelationTypes": list(item.get("requiredRelationTypes") or []),
                    **typedb_rule_execution_profile_fields(item),
                }
            )
        # Reject non-native RuleBox entries before opening TypeDB.  This
        # makes a mixed or incomplete RuleBox fail closed without issuing
        # a partial query against the live investment world.
        selected_entries = []
        for planned in execution_plan.get("selectedEntries") or []:
            rule = planned.get("rule")
            if not rule:
                continue
            rule_payload = rule.to_dict() if hasattr(rule, "to_dict") else dict(rule or {})
            profile = typedb_native_rule_profile(rule_payload)
            if profile.get("status") != "ready":
                execution_incomplete = True
                skipped_rules.append(
                    {
                        "ruleId": str(rule.rule_id or ""),
                        "status": str(profile.get("status") or "partial"),
                        "reason": "Rule has JSON-bound or unsupported conditions for direct TypeQL execution.",
                        **typedb_rule_execution_profile_fields(planned),
                    }
                )
                continue
            selected_entries.append(planned)
        # The ABox pointer is stable only while the projection-owned write
        # lease is held. The normal path keeps every selected target in
        # each rule's single TypeDB query; bounded rule parallelism is the
        # one concurrency dimension. Target splitting remains an explicit
        # capacity-test option and still requires the same write lease.
        target_work_sharding_enabled = _store.native_rule_target_work_sharding_enabled()
        adaptive_target_sharding_enabled = _store.native_rule_adaptive_target_sharding_enabled()
        if stable_abox_write_lease_held and adaptive_target_sharding_enabled:
            adaptive_target_parallelism_by_rule_id = (
                typedb_native_rule_adaptive_target_parallelism_by_rule_id(
                    adaptive_target_sharding_profile
                )
            )
        target_work_parallelism = (
            requested_target_parallelism
            if stable_abox_write_lease_held and target_work_sharding_enabled
            else 1
        )
        target_work_plan = typedb_native_rule_target_work_plan(
            selected_entries,
            target_parallelism=target_work_parallelism,
            adaptive_target_parallelism_by_rule_id=adaptive_target_parallelism_by_rule_id,
        )
        target_work_plan["targetWorkShardingEnabled"] = target_work_sharding_enabled
        target_work_plan["targetWorkAdaptiveShardingEnabled"] = adaptive_target_sharding_enabled
        target_work_plan["targetWorkAdaptiveShardingProfileStatus"] = (
            adaptive_target_sharding_profile_status
        )
        target_work_plan["targetWorkAdaptiveRequestedRuleIds"] = sorted(
            adaptive_target_parallelism_by_rule_id.keys()
        )[:20]
        target_work_plan["targetWorkShardingSuppressed"] = bool(
            stable_abox_write_lease_held
            and requested_target_parallelism > 1
            and not target_work_sharding_enabled
            and len(clean_symbols) > 1
        )
        selected_entries = list(target_work_plan.get("workItems") or [])
        model_signal_batch_plan = typedb_model_signal_bridge_batch_plan(
            selected_entries,
            clean_symbols,
        )
        selected_entries = list(model_signal_batch_plan.get("regularEntries") or [])
        imported = _store.driver_imports()
        if imported[0] is None:
            raise RuntimeError(
                "typedb-driver Python package is not installed: " + str(imported[1])[:160]
            )
        _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
        # Direct TypeQL binds the active Manifest for every raw predicate,
        # including N-of-M follow-up checks, so inactive ABox generations
        # cannot enter a scoped recovery read.
        requires_direct_any_probe = any(
            any(
                normalized_condition_role(
                    condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {})
                )
                in {"any", "optional"}
                for condition in (getattr(item.get("rule"), "conditions", []) or [])
            )
            for item in selected_entries
            if item.get("rule")
        )
        scoped_manifest_only = False
        try:
            scoped_manifest_only = _store.active_abox_uses_scoped_manifest(world_id)
        except Exception:
            scoped_manifest_only = False
        requested_parallelism = max(
            1,
            min(8, int(number_or_none(native_rule_parallelism) or 1)),
        )
        any_condition_rule_count = sum(
            1
            for item in selected_entries
            if any(
                normalized_condition_role(
                    condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {})
                )
                in {"any", "optional"}
                for condition in (getattr(item.get("rule"), "conditions", []) or [])
            )
        )
        any_condition_parallelism_cap = (
            min(
                requested_parallelism,
                _store.native_rule_any_condition_parallelism(),
            )
            if requires_direct_any_probe
            else requested_parallelism
        )
        entry_has_any_conditions = {
            index: any(
                normalized_condition_role(
                    condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {})
                )
                in {"any", "optional"}
                for condition in (getattr(planned.get("rule"), "conditions", []) or [])
            )
            for index, planned in enumerate(selected_entries)
        }
        adaptive_target_entries = [
            (index, planned)
            for index, planned in enumerate(selected_entries)
            if bool(planned.get("targetWorkAdaptiveShardingUsed"))
        ]
        adaptive_target_indexes = {index for index, _planned in adaptive_target_entries}
        direct_entries = [
            (index, planned)
            for index, planned in enumerate(selected_entries)
            if index not in adaptive_target_indexes and not entry_has_any_conditions.get(index)
        ]
        any_condition_entries = [
            (index, planned)
            for index, planned in enumerate(selected_entries)
            if index not in adaptive_target_indexes and entry_has_any_conditions.get(index)
        ]
        execution_batches = []
        for execution_stage in ["critical", "core", "supporting"]:
            stage_direct_entries = [
                item
                for item in direct_entries
                if str(item[1].get("executionStage") or "core") == execution_stage
            ]
            stage_any_entries = [
                item
                for item in any_condition_entries
                if str(item[1].get("executionStage") or "core") == execution_stage
            ]
            stage_adaptive_entries = [
                item
                for item in adaptive_target_entries
                if str(item[1].get("executionStage") or "core") == execution_stage
            ]
            if stage_direct_entries:
                execution_batches.append(
                    {
                        "name": execution_stage + ":direct-typeql",
                        "executionStage": execution_stage,
                        "entries": stage_direct_entries,
                        "parallelism": min(requested_parallelism, len(stage_direct_entries)),
                    }
                )
            if stage_any_entries:
                # N-of-M verification is contention-sensitive, but a
                # critical any-rule must still run before cheaper core or
                # supporting rules.
                execution_batches.append(
                    {
                        "name": execution_stage + ":any-condition",
                        "executionStage": execution_stage,
                        "entries": stage_any_entries,
                        "parallelism": min(any_condition_parallelism_cap, len(stage_any_entries)),
                    }
                )
            if stage_adaptive_entries:
                execution_batches.append(
                    {
                        "name": execution_stage + ":adaptive-target-shards",
                        "executionStage": execution_stage,
                        "entries": stage_adaptive_entries,
                        "parallelism": 1,
                    }
                )
        native_rule_execution_phases = {
            "directTypeqlRuleCount": len(direct_entries),
            "directTypeqlParallelism": (
                min(requested_parallelism, len(direct_entries)) if direct_entries else 0
            ),
            "anyConditionRuleCount": len(any_condition_entries),
            "anyConditionParallelism": (
                min(any_condition_parallelism_cap, len(any_condition_entries))
                if any_condition_entries
                else 0
            ),
            "adaptiveTargetShardWorkItemCount": len(adaptive_target_entries),
            "adaptiveTargetShardedRuleCount": int(
                target_work_plan.get("targetWorkAdaptiveShardedRuleCount") or 0
            ),
            "adaptiveTargetShardParallelism": 1 if adaptive_target_entries else 0,
            "executionStageWorkCounts": {
                stage: len(
                    [
                        item
                        for item in selected_entries
                        if str(item.get("executionStage") or "core") == stage
                    ]
                )
                for stage in ["critical", "core", "supporting"]
            },
        }
        isolated_entry_execution = bool(
            stable_abox_write_lease_held
            and (execution_batches or model_signal_batch_plan.get("batches"))
        )
        parallel_rule_execution = bool(
            isolated_entry_execution
            and any(int(batch.get("parallelism") or 1) > 1 for batch in execution_batches)
        )
        effective_parallelism = (
            max(int(batch.get("parallelism") or 1) for batch in execution_batches)
            if isolated_entry_execution and execution_batches
            else 1
        )
        if isolated_entry_execution:
            # ABox writes are serialized by the durable lease passed by the
            # projection recorder. Independent direct TypeQL rules may use
            # separate bounded read transactions without observing a world
            # pointer transition between rule evaluations.
            if parallel_rule_execution:
                execution_mode += "-parallel"
            elif adaptive_target_entries:
                execution_mode += "-adaptive-target-shards"

        native_execution_deadline = time.monotonic() + _store.native_rule_execution_budget_seconds()
        bridge_batch_result = _store.execute_typedb_model_signal_bridge_batches(
            model_signal_batch_plan.get("batches") or [],
            world_id=world_id,
            imported=imported,
            transaction_type=TransactionType,
            deadline=native_execution_deadline,
            evidence_read_index=evidence_read_index,
        )
        read_transaction_count += int(bridge_batch_result.get("readTransactionCount") or 0)
        read_call_count += int(bridge_batch_result.get("readQueryCount") or 0)
        bridge_executed_rules = [
            dict(item)
            for item in bridge_batch_result.get("executedRules") or []
            if isinstance(item, dict)
        ]
        executed_rules.extend(bridge_executed_rules)
        bridge_failures = [
            dict(item)
            for item in bridge_batch_result.get("failures") or []
            if isinstance(item, dict)
        ]
        if bridge_failures:
            skipped_rules.extend(bridge_failures)
            query_failures.extend(bridge_failures)
            execution_incomplete = True
            execution_budget_exhausted = execution_budget_exhausted or any(
                str(item.get("status") or "") == "deferred-by-runtime-budget"
                for item in bridge_failures
            )
        for dispatched in bridge_batch_result.get("dispatchedMatches") or []:
            if not isinstance(dispatched, dict):
                continue
            rule = dispatched.get("rule")
            if not rule:
                continue
            _store.merge_native_match_rows(
                rule,
                dict(dispatched.get("queryPlan") or {}),
                [dict(dispatched.get("row") or {})],
                match_index,
                matches,
                world_id,
            )
        model_signal_ignored_contract_ids = list(
            bridge_batch_result.get("ignoredContractIds") or []
        )
        if bridge_executed_rules:
            execution_mode = "typedb-shared-model-signal-bridge-batch"

        def operation():
            nonlocal read_call_count, read_transaction_count, execution_budget_exhausted, execution_incomplete, execution_mode
            nonlocal indexed_evidence_query_used
            if not selected_entries:
                return
            # The durable projection may need a long write timeout, but a
            # native read must be bounded independently. Reusing the
            # write-oriented driver deadline here made a 30-second
            # native-rule budget wait for up to five minutes in the
            # synchronous TypeDB driver.
            transaction_timeout = max(
                _store.native_rule_query_timeout_seconds(),
                min(120.0, _store.native_rule_execution_budget_seconds() + 2.0),
            )
            driver = _store.open_driver(
                imported,
                request_timeout_seconds=transaction_timeout,
            )
            try:
                _store.ensure_database(driver)
                deadline = native_execution_deadline
                # All applicable rules observe one stable ABox read view.
                # Previously every rule, and then every N-of-M check, opened
                # an independent transaction. That multiplied driver setup
                # and query planning work while allowing a live world switch
                # between rules. Per-query alarm limits still bound an
                # expensive predicate; a failed query aborts this shared
                # transaction and yields a fail-closed partial result.
                read_transaction_count += 1
                with driver.transaction(
                    _store.database,
                    TransactionType.READ,
                    _store.read_transaction_options(transaction_timeout),
                ) as tx:
                    for planned in selected_entries:
                        rule = planned.get("rule")
                        if not rule:
                            continue
                        rule_started = time.perf_counter()
                        rule_query_count_started = read_call_count
                        rule_query_duration_ms = 0.0
                        remaining_seconds = deadline - time.monotonic()
                        if remaining_seconds <= 0:
                            execution_budget_exhausted = True
                            execution_incomplete = True
                            skipped_rules.append(
                                {
                                    "ruleId": str(rule.rule_id or ""),
                                    "status": "deferred-by-runtime-budget",
                                    "reason": "TypeDB native-rule realtime execution budget is exhausted.",
                                    "elapsedMs": int((time.perf_counter() - rule_started) * 1000),
                                    **typedb_rule_execution_profile_fields(planned),
                                }
                            )
                            continue
                        rule_payload = (
                            rule.to_dict() if hasattr(rule, "to_dict") else dict(rule or {})
                        )
                        has_any_conditions = any(
                            normalized_condition_role(
                                condition.to_dict()
                                if hasattr(condition, "to_dict")
                                else dict(condition or {})
                            )
                            in {"any", "optional"}
                            for condition in (rule.conditions or [])
                        )
                        candidate_symbols = typedb_planned_candidate_symbols(
                            planned,
                            clean_symbols,
                        )
                        query_plan = typedb_native_rule_runtime_query_plan(
                            rule_payload,
                            candidate_symbols,
                            scoped_manifest_only=scoped_manifest_only,
                            world_id=world_id,
                            evidence_read_index=evidence_read_index,
                            compact_result_rows=not _store.condition_detail_queries_enabled(),
                        )
                        uses_indexed_evidence_query = bool(query_plan.get("indexedEvidenceQuery"))
                        indexed_evidence_query_used = (
                            indexed_evidence_query_used or uses_indexed_evidence_query
                        )
                        if uses_indexed_evidence_query:
                            execution_mode = "typedb-manifest-evidence-index"
                        if not query_plan.get("query"):
                            execution_incomplete = True
                            skipped_rules.append(
                                {
                                    "ruleId": str(rule.rule_id or ""),
                                    "status": "blocked",
                                    "reason": "Direct TypeQL rule query could not be built.",
                                    "elapsedMs": int((time.perf_counter() - rule_started) * 1000),
                                    **typedb_rule_execution_profile_fields(planned),
                                }
                            )
                            continue
                        query_timeout = min(
                            _store.native_rule_query_timeout_seconds(), remaining_seconds
                        )
                        try:
                            query_started = time.perf_counter()
                            try:
                                rows = _store.read_rows_in_transaction(
                                    tx,
                                    str(query_plan.get("query")),
                                    query_plan.get("columns") or ["sourceId"],
                                    label="nativeRule:" + str(rule.rule_id or ""),
                                    timeout_seconds=query_timeout,
                                )
                            finally:
                                rule_query_duration_ms += (
                                    time.perf_counter() - query_started
                                ) * 1000
                            read_call_count += 1
                        except (
                            Exception
                        ) as error:  # noqa: BLE001 - a timed-out shared read cannot safely continue.
                            failure = {
                                "ruleId": str(rule.rule_id or ""),
                                "status": (
                                    "query-timeout"
                                    if _bindings.typedb_error_code(error) == "typedbTimeout"
                                    else "query-error"
                                ),
                                "reason": str(error)[:220],
                                "candidateSymbols": candidate_symbols,
                                "elapsedMs": int((time.perf_counter() - rule_started) * 1000),
                                "queryDurationMs": int(rule_query_duration_ms),
                                **typedb_rule_execution_profile_fields(planned),
                            }
                            skipped_rules.append(failure)
                            query_failures.append(failure)
                            execution_incomplete = True
                            break
                        any_condition_query_count = 0
                        any_condition_failure = False
                        if (
                            rows
                            and has_any_conditions
                            and not bool(query_plan.get("anyConditionsVerified"))
                        ):
                            verified_rows = []
                            for row in rows:
                                remaining_seconds = deadline - time.monotonic()
                                if remaining_seconds <= 0:
                                    failure = {
                                        "ruleId": str(rule.rule_id or ""),
                                        "status": "deferred-by-runtime-budget",
                                        "reason": "TypeDB native-rule runtime budget was exhausted while verifying any conditions.",
                                        "candidateSymbols": candidate_symbols,
                                        "elapsedMs": int(
                                            (time.perf_counter() - rule_started) * 1000
                                        ),
                                        "queryDurationMs": int(rule_query_duration_ms),
                                        **typedb_rule_execution_profile_fields(planned),
                                    }
                                    skipped_rules.append(failure)
                                    query_failures.append(failure)
                                    execution_budget_exhausted = True
                                    execution_incomplete = True
                                    any_condition_failure = True
                                    break
                                verification_started = time.perf_counter()
                                try:
                                    verification = _store.verify_typedb_native_any_conditions(
                                        driver,
                                        TransactionType,
                                        rule,
                                        str(row.get("sourceId") or ""),
                                        remaining_seconds,
                                        scoped_manifest_only,
                                        tx=tx,
                                        world_id=world_id,
                                        evidence_read_index=evidence_read_index,
                                    )
                                finally:
                                    rule_query_duration_ms += (
                                        time.perf_counter() - verification_started
                                    ) * 1000
                                read_transaction_count += int(
                                    verification.get("readTransactionCount") or 0
                                )
                                read_call_count += int(verification.get("readQueryCount") or 0)
                                any_condition_query_count += int(
                                    verification.get("readQueryCount") or 0
                                )
                                verification_status = str(verification.get("status") or "error")
                                if verification_status == "matched":
                                    row["_matchedAnyConditionIds"] = list(
                                        verification.get("matchedConditionIds") or []
                                    )
                                    row["_anyConditionsVerified"] = bool(
                                        verification.get("typeDbCardinalityVerified")
                                    )
                                    verified_rows.append(row)
                                    continue
                                if verification_status == "not-matched":
                                    continue
                                failure = {
                                    "ruleId": str(rule.rule_id or ""),
                                    "status": "any-condition-" + verification_status,
                                    "reason": str(
                                        verification.get("reason")
                                        or "TypeDB any-condition verification did not complete."
                                    )[:220],
                                    "candidateSymbols": candidate_symbols,
                                    "elapsedMs": int((time.perf_counter() - rule_started) * 1000),
                                    "queryDurationMs": int(rule_query_duration_ms),
                                    **typedb_rule_execution_profile_fields(planned),
                                }
                                skipped_rules.append(failure)
                                query_failures.append(failure)
                                execution_incomplete = True
                                any_condition_failure = True
                                break
                            if any_condition_failure:
                                # The failed group query can invalidate the
                                # shared read transaction. Do not evaluate
                                # the remaining rules against an uncertain
                                # snapshot.
                                break
                            rows = verified_rows
                        elif rows and has_any_conditions:
                            for row in rows:
                                row["_matchedAnyConditionIds"] = []
                                row["_anyConditionsVerified"] = True
                        executed_rules.append(
                            {
                                "ruleId": rule.rule_id,
                                "nativeRuleId": typedb_native_rule_id(rule.rule_id),
                                "typeqlExecutionMode": "direct-typeql",
                                "queryMode": str(
                                    query_plan.get("queryMode")
                                    or "typedb-scoped-typeql-any-verified"
                                ),
                                "indexedEvidenceQueryUsed": uses_indexed_evidence_query,
                                "modelSignalInterpretationPolicy": is_model_signal_interpretation_rule(
                                    rule_payload
                                ),
                                "modelSignalInterpretationPolicyId": (
                                    "model-signal-interpretation:" + str(rule.rule_id or "")
                                    if is_model_signal_interpretation_rule(rule_payload)
                                    else ""
                                ),
                                "sharedModelSignalBridge": bool(
                                    query_plan.get("sharedModelSignalBridge")
                                ),
                                "bridgeSourceScope": str(query_plan.get("bridgeSourceScope") or ""),
                                "rowCount": len(rows),
                                "candidateSymbols": candidate_symbols,
                                "queryComplexity": int(planned.get("queryComplexity") or 0),
                                "queryCount": read_call_count - rule_query_count_started,
                                "anyConditionQueryCount": any_condition_query_count,
                                "elapsedMs": int((time.perf_counter() - rule_started) * 1000),
                                "queryDurationMs": int(rule_query_duration_ms),
                                **typedb_rule_execution_profile_fields(planned),
                            }
                        )
                        _store.merge_native_match_rows(
                            rule, query_plan, rows, match_index, matches, world_id
                        )
            finally:
                _store.close_driver(driver)

        if isolated_entry_execution:
            deadline = native_execution_deadline
            completed_entries: Dict[int, Dict[str, object]] = {}
            read_driver_pool = []

            def capture(
                index: int, planned: Dict[str, object], future_result=None, error=None
            ) -> None:
                rule = planned.get("rule")
                if error is None:
                    completed_entries[index] = future_result
                    return
                completed_entries[index] = {
                    "status": "partial",
                    "readTransactionCount": 0,
                    "readQueryCount": 0,
                    "failure": {
                        "ruleId": str(getattr(rule, "rule_id", "") or ""),
                        "status": "query-error",
                        "reason": str(error)[:220],
                        "candidateSymbols": typedb_planned_candidate_symbols(
                            planned,
                            clean_symbols,
                        ),
                        **typedb_rule_execution_profile_fields(planned),
                    },
                }

            def execute_lane(lane_entries, read_driver):
                lane_results = []
                for index, planned in lane_entries:
                    try:
                        lane_results.append(
                            (
                                index,
                                planned,
                                _store.execute_typedb_native_rule_entry(
                                    planned,
                                    clean_symbols,
                                    world_id,
                                    scoped_manifest_only,
                                    imported,
                                    TransactionType,
                                    deadline,
                                    execution_mode,
                                    evidence_read_index,
                                    read_driver,
                                ),
                                None,
                            )
                        )
                    except (
                        Exception
                    ) as error:  # noqa: BLE001 - one failed lane entry blocks activation.
                        lane_results.append((index, planned, None, error))
                return lane_results

            try:
                # Allocate one bounded driver per worker lane and keep that
                # lane as its exclusive owner. Rules still use independent
                # read transactions, while connection handshakes are paid
                # once per subject run instead of once per rule.
                imported_driver_types = imported[0]
                typedb_driver_api = (
                    imported_driver_types[0]
                    if isinstance(imported_driver_types, (tuple, list)) and imported_driver_types
                    else imported_driver_types
                )
                typedb_driver_factory = getattr(typedb_driver_api, "driver", None)
                if callable(typedb_driver_factory):
                    for _lane_index in range(effective_parallelism):
                        read_driver = _store.open_native_rule_read_driver(
                            imported,
                            request_timeout_seconds=min(
                                _store.native_rule_execution_budget_seconds(),
                                max(
                                    _store.native_rule_query_timeout_seconds(),
                                    _store.native_rule_indexed_any_condition_query_timeout_seconds(),
                                ),
                            ),
                        )
                        _store.ensure_database(read_driver)
                        read_driver_pool.append(read_driver)
                for batch in execution_batches:
                    batch_entries = list(batch.get("entries") or [])
                    batch_parallelism = min(
                        len(batch_entries),
                        max(1, int(batch.get("parallelism") or 1)),
                    )
                    if batch_parallelism == 1:
                        lane_driver = read_driver_pool[0] if read_driver_pool else None
                        for index, planned, future_result, error in execute_lane(
                            batch_entries,
                            lane_driver,
                        ):
                            capture(index, planned, future_result=future_result, error=error)
                        continue
                    lanes = [[] for _lane_index in range(batch_parallelism)]
                    for entry_index, entry in enumerate(batch_entries):
                        lanes[entry_index % batch_parallelism].append(entry)
                    with ThreadPoolExecutor(max_workers=batch_parallelism) as executor:
                        futures = {
                            executor.submit(
                                execute_lane,
                                lane_entries,
                                (
                                    read_driver_pool[lane_index]
                                    if lane_index < len(read_driver_pool)
                                    else None
                                ),
                            ): lane_index
                            for lane_index, lane_entries in enumerate(lanes)
                            if lane_entries
                        }
                        for future in as_completed(futures):
                            try:
                                lane_results = future.result()
                            except (
                                Exception
                            ) as error:  # noqa: BLE001 - executor failures must block the complete generation.
                                lane_index = futures[future]
                                for index, planned in lanes[lane_index]:
                                    capture(index, planned, error=error)
                                continue
                            for index, planned, future_result, error in lane_results:
                                capture(index, planned, future_result=future_result, error=error)
            finally:
                closed_driver_ids = set()
                for read_driver in read_driver_pool:
                    driver_id = id(read_driver)
                    if driver_id in closed_driver_ids:
                        continue
                    closed_driver_ids.add(driver_id)
                    _store.close_native_rule_read_driver(read_driver)
            for index, planned in enumerate(selected_entries):
                completed = dict(completed_entries.get(index) or {})
                if str(completed.get("status") or "partial") != "ok":
                    # Initial independent rule calls may run in parallel,
                    # but a timeout recovery runs here after that phase has
                    # drained. This gives the failed rule smaller target
                    # sets without adding concurrent TypeDB pressure.
                    completed = _store.recover_timed_out_native_rule_entry(
                        completed,
                        planned,
                        clean_symbols,
                        world_id,
                        scoped_manifest_only,
                        imported,
                        TransactionType,
                        deadline,
                        execution_mode,
                        evidence_read_index,
                    )
                read_transaction_count += int(completed.get("readTransactionCount") or 0)
                read_call_count += int(completed.get("readQueryCount") or 0)
                if str(completed.get("status") or "partial") != "ok":
                    failure = dict(completed.get("failure") or {})
                    failure.setdefault(
                        "ruleId", str(getattr(planned.get("rule"), "rule_id", "") or "")
                    )
                    failure.setdefault("status", "query-error")
                    failure.setdefault("reason", "TypeDB native rule did not complete.")
                    for key, value in typedb_rule_execution_profile_fields(planned).items():
                        failure.setdefault(key, value)
                    skipped_rules.append(failure)
                    query_failures.append(failure)
                    execution_incomplete = True
                    if str(failure.get("status") or "") == "deferred-by-runtime-budget":
                        execution_budget_exhausted = True
                    continue
                rule = completed.get("rule")
                query_plan = completed.get("queryPlan") or {}
                rows = list(completed.get("rows") or [])
                executed = dict(completed.get("executed") or {})
                if not rule or not executed:
                    failure = {
                        "ruleId": str(getattr(planned.get("rule"), "rule_id", "") or ""),
                        "status": "query-error",
                        "reason": "TypeDB native rule returned an incomplete parallel result.",
                        **typedb_rule_execution_profile_fields(planned),
                    }
                    skipped_rules.append(failure)
                    query_failures.append(failure)
                    execution_incomplete = True
                    continue
                executed.update(
                    {
                        key: value
                        for key, value in typedb_rule_execution_profile_fields(planned).items()
                        if key not in executed
                    }
                )
                executed_rules.append(executed)
                if bool(executed.get("timeoutFallbackUsed")):
                    timeout_fallback_rule_count += 1
                    timeout_fallback_shard_count += int(
                        executed.get("timeoutFallbackShardCount") or 0
                    )
                indexed_evidence_query_used = indexed_evidence_query_used or bool(
                    executed.get("indexedEvidenceQueryUsed")
                )
                # Merge on the coordinator thread to retain deterministic
                # ordering and keep condition-detail reads out of workers.
                _store.merge_native_match_rows(
                    rule, query_plan, rows, match_index, matches, world_id
                )
        else:
            _store.with_typedb_retries(operation)
        incomplete_failure_candidates = list(query_failures)
        if not incomplete_failure_candidates:
            incomplete_failure_candidates = [
                item
                for item in skipped_rules
                if isinstance(item, dict)
                and str(item.get("status") or "")
                not in {"", "not-applicable", "not-applicable-preflight", "planned"}
            ]
        failure_partition = typedb_rule_execution_failure_partition(incomplete_failure_candidates)
        supporting_coverage_gap = bool(
            (query_failures or execution_budget_exhausted or execution_incomplete)
            and failure_partition["supporting"]
            and not failure_partition["blocking"]
        )
        if (
            query_failures or execution_budget_exhausted or execution_incomplete
        ) and not supporting_coverage_gap:
            incomplete_diagnostic = _bindings.typedb_native_rule_execution_incomplete_diagnostic(
                query_failures,
                skipped_rules,
                execution_budget_exhausted=execution_budget_exhausted,
            )
            return {
                "status": "partial",
                "graphStore": "typedb",
                "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
                "nativeQueryUsed": False,
                "indexedEvidenceQueryUsed": indexed_evidence_query_used,
                "nativeExecutionMode": execution_mode,
                "nativeRuleParallelism": effective_parallelism,
                "nativeRuleTargetParallelism": int(
                    target_work_plan.get("effectiveTargetParallelism") or 1
                ),
                "targetWorkShardingUsed": bool(target_work_plan.get("targetWorkShardingUsed")),
                "targetWorkShardingEnabled": bool(
                    target_work_plan.get("targetWorkShardingEnabled")
                ),
                "targetWorkShardingSuppressed": bool(
                    target_work_plan.get("targetWorkShardingSuppressed")
                ),
                "targetWorkShardCount": int(target_work_plan.get("targetWorkShardCount") or 0),
                "targetWorkItemCount": int(target_work_plan.get("targetWorkItemCount") or 0),
                "targetWorkOriginalEntryCount": int(
                    target_work_plan.get("targetWorkOriginalEntryCount") or 0
                ),
                "targetWorkShardedRuleCount": int(
                    target_work_plan.get("targetWorkShardedRuleCount") or 0
                ),
                "targetWorkAdaptiveShardingEnabled": bool(
                    target_work_plan.get("targetWorkAdaptiveShardingEnabled")
                ),
                "targetWorkAdaptiveShardingProfileStatus": str(
                    target_work_plan.get("targetWorkAdaptiveShardingProfileStatus") or ""
                ),
                "targetWorkAdaptiveShardingUsed": bool(
                    target_work_plan.get("targetWorkAdaptiveShardingUsed")
                ),
                "targetWorkAdaptiveShardedRuleCount": int(
                    target_work_plan.get("targetWorkAdaptiveShardedRuleCount") or 0
                ),
                "targetWorkAdaptiveShardedRuleIds": list(
                    target_work_plan.get("targetWorkAdaptiveShardedRuleIds") or []
                )[:20],
                "timeoutFallbackUsed": timeout_fallback_rule_count > 0,
                "timeoutFallbackRuleCount": timeout_fallback_rule_count,
                "timeoutFallbackShardCount": timeout_fallback_shard_count,
                "nativeRuleAnyConditionParallelismCap": any_condition_parallelism_cap,
                "nativeRuleAnyConditionRuleCount": any_condition_rule_count,
                "nativeRuleExecutionPhases": native_rule_execution_phases,
                "parallelRuleExecution": parallel_rule_execution,
                "matchedCount": len(matches),
                "nativeInferenceEvaluationComplete": False,
                "coreNativeInferenceEvaluationComplete": False,
                "nativeCoverageStatus": "blocking-rule-failure",
                "blockingRuleFailureCount": len(failure_partition["blocking"]),
                "supportingRuleFailureCount": len(failure_partition["supporting"]),
                "executedRuleCount": len(
                    {
                        str(item.get("ruleId") or "").strip()
                        for item in executed_rules
                        if str(item.get("ruleId") or "").strip()
                    }
                ),
                "executedRuleWorkCount": len(executed_rules),
                "skippedRuleCount": len(
                    {
                        str(item.get("ruleId") or "").strip()
                        for item in skipped_rules
                        if str(item.get("ruleId") or "").strip()
                    }
                ),
                "skippedRuleWorkCount": len(skipped_rules),
                "matches": matches,
                "reasonCode": str(
                    incomplete_diagnostic.get("reasonCode") or "typedbNativeRuleExecutionPartial"
                ),
                "reason": str(
                    incomplete_diagnostic.get("reason")
                    or "TypeDB native rule execution did not complete for every applicable rule."
                ),
                "blockingRule": dict(incomplete_diagnostic.get("blockingRule") or {}),
                "readTransactionCount": read_transaction_count,
                "readQueryCount": read_call_count,
                "executedRules": list(executed_rules),
                "skippedRules": list(skipped_rules),
                "modelSignalBridgeExecution": typedb_model_signal_bridge_batch_plan_summary(
                    model_signal_batch_plan,
                    ignored_contract_ids=model_signal_ignored_contract_ids,
                    execution=bridge_batch_result,
                ),
                "executionPlan": typedb_native_rule_execution_plan_summary(execution_plan),
                "ruleContext": rule_context,
                "evidenceFieldIndex": evidence_index_hydration,
                "typedbQueryMetrics": _store.query_metrics_snapshot(),
            }
        return {
            "status": "ok",
            "graphStore": "typedb",
            "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
            "nativeQueryUsed": True,
            "indexedEvidenceQueryUsed": indexed_evidence_query_used,
            "nativeExecutionMode": execution_mode,
            "nativeRuleParallelism": effective_parallelism,
            "nativeRuleTargetParallelism": int(
                target_work_plan.get("effectiveTargetParallelism") or 1
            ),
            "targetWorkShardingUsed": bool(target_work_plan.get("targetWorkShardingUsed")),
            "targetWorkShardingEnabled": bool(target_work_plan.get("targetWorkShardingEnabled")),
            "targetWorkShardingSuppressed": bool(
                target_work_plan.get("targetWorkShardingSuppressed")
            ),
            "targetWorkShardCount": int(target_work_plan.get("targetWorkShardCount") or 0),
            "targetWorkItemCount": int(target_work_plan.get("targetWorkItemCount") or 0),
            "targetWorkOriginalEntryCount": int(
                target_work_plan.get("targetWorkOriginalEntryCount") or 0
            ),
            "targetWorkShardedRuleCount": int(
                target_work_plan.get("targetWorkShardedRuleCount") or 0
            ),
            "targetWorkAdaptiveShardingEnabled": bool(
                target_work_plan.get("targetWorkAdaptiveShardingEnabled")
            ),
            "targetWorkAdaptiveShardingProfileStatus": str(
                target_work_plan.get("targetWorkAdaptiveShardingProfileStatus") or ""
            ),
            "targetWorkAdaptiveShardingUsed": bool(
                target_work_plan.get("targetWorkAdaptiveShardingUsed")
            ),
            "targetWorkAdaptiveShardedRuleCount": int(
                target_work_plan.get("targetWorkAdaptiveShardedRuleCount") or 0
            ),
            "targetWorkAdaptiveShardedRuleIds": list(
                target_work_plan.get("targetWorkAdaptiveShardedRuleIds") or []
            )[:20],
            "timeoutFallbackUsed": timeout_fallback_rule_count > 0,
            "timeoutFallbackRuleCount": timeout_fallback_rule_count,
            "timeoutFallbackShardCount": timeout_fallback_shard_count,
            "nativeRuleAnyConditionParallelismCap": any_condition_parallelism_cap,
            "nativeRuleAnyConditionRuleCount": any_condition_rule_count,
            "nativeRuleExecutionPhases": native_rule_execution_phases,
            "parallelRuleExecution": parallel_rule_execution,
            "nativeInferenceEvaluationComplete": not supporting_coverage_gap,
            "coreNativeInferenceEvaluationComplete": True,
            "nativeCoverageStatus": (
                "core-complete-supporting-partial" if supporting_coverage_gap else "complete"
            ),
            "supportingRuleFailureCount": len(failure_partition["supporting"]),
            "supportingRuleFailures": list(failure_partition["supporting"]),
            "executedRuleCount": len(
                {
                    str(item.get("ruleId") or "").strip()
                    for item in executed_rules
                    if str(item.get("ruleId") or "").strip()
                }
            ),
            "executedRuleWorkCount": len(executed_rules),
            "skippedRuleCount": len(
                {
                    str(item.get("ruleId") or "").strip()
                    for item in skipped_rules
                    if str(item.get("ruleId") or "").strip()
                }
            ),
            "skippedRuleWorkCount": len(skipped_rules),
            "matchedCount": len(matches),
            "readTransactionCount": read_transaction_count,
            "readQueryCount": read_call_count,
            "readTransactionCount": read_transaction_count,
            "conditionDetailQueryCount": (
                0 if not _store.condition_detail_queries_enabled() else None
            ),
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
            "matches": matches,
            "executedRules": list(executed_rules),
            "skippedRules": list(skipped_rules),
            "modelSignalBridgeExecution": typedb_model_signal_bridge_batch_plan_summary(
                model_signal_batch_plan,
                ignored_contract_ids=model_signal_ignored_contract_ids,
                execution=bridge_batch_result,
            ),
            "executionPlan": typedb_native_rule_execution_plan_summary(execution_plan),
            "ruleContext": rule_context,
            "evidenceFieldIndex": {
                key: value
                for key, value in dict(evidence_index_hydration or {}).items()
                if key != "evidence"
            },
            # Internal hand-off only: the TypeDB evaluator may have added
            # a legacy field index needed by exact evidence grounding.
            # The lifecycle caller removes this before diagnostics or API
            # payloads are persisted.
            "_materializationEvidenceReadIndex": evidence_read_index,
        }
    except (
        Exception
    ) as error:  # noqa: BLE001 - run_rulebox reports and can use compatibility fallback.
        return {
            "status": "error",
            "graphStore": "typedb",
            "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
            "nativeQueryUsed": False,
            "nativeExecutionMode": execution_mode,
            "nativeRuleParallelism": effective_parallelism,
            "nativeRuleTargetParallelism": int(
                target_work_plan.get("effectiveTargetParallelism") or 1
            ),
            "targetWorkShardingUsed": bool(target_work_plan.get("targetWorkShardingUsed")),
            "targetWorkShardingEnabled": bool(target_work_plan.get("targetWorkShardingEnabled")),
            "targetWorkShardingSuppressed": bool(
                target_work_plan.get("targetWorkShardingSuppressed")
            ),
            "targetWorkShardCount": int(target_work_plan.get("targetWorkShardCount") or 0),
            "targetWorkItemCount": int(target_work_plan.get("targetWorkItemCount") or 0),
            "targetWorkOriginalEntryCount": int(
                target_work_plan.get("targetWorkOriginalEntryCount") or 0
            ),
            "targetWorkShardedRuleCount": int(
                target_work_plan.get("targetWorkShardedRuleCount") or 0
            ),
            "targetWorkAdaptiveShardingEnabled": bool(
                target_work_plan.get("targetWorkAdaptiveShardingEnabled")
            ),
            "targetWorkAdaptiveShardingProfileStatus": str(
                target_work_plan.get("targetWorkAdaptiveShardingProfileStatus") or ""
            ),
            "targetWorkAdaptiveShardingUsed": bool(
                target_work_plan.get("targetWorkAdaptiveShardingUsed")
            ),
            "targetWorkAdaptiveShardedRuleCount": int(
                target_work_plan.get("targetWorkAdaptiveShardedRuleCount") or 0
            ),
            "targetWorkAdaptiveShardedRuleIds": list(
                target_work_plan.get("targetWorkAdaptiveShardedRuleIds") or []
            )[:20],
            "timeoutFallbackUsed": timeout_fallback_rule_count > 0,
            "timeoutFallbackRuleCount": timeout_fallback_rule_count,
            "timeoutFallbackShardCount": timeout_fallback_shard_count,
            "nativeRuleAnyConditionParallelismCap": any_condition_parallelism_cap,
            "nativeRuleAnyConditionRuleCount": any_condition_rule_count,
            "nativeRuleExecutionPhases": native_rule_execution_phases,
            "parallelRuleExecution": parallel_rule_execution,
            "matchedCount": 0,
            "matches": [],
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": str(error)[:220],
            "executedRules": list(executed_rules),
            "skippedRules": list(skipped_rules),
            "readQueryCount": read_call_count,
            "modelSignalBridgeExecution": typedb_model_signal_bridge_batch_plan_summary(
                model_signal_batch_plan,
                ignored_contract_ids=model_signal_ignored_contract_ids,
                execution=bridge_batch_result,
            ),
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
            "executionPlan": typedb_native_rule_execution_plan_summary(execution_plan),
        }
