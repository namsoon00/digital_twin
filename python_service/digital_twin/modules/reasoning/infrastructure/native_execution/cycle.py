"""native_execution: cycle through explicit injected capabilities."""

from digital_twin.modules.reasoning.domain.ontology_change_impact import compact_inference_impact_plan
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_runtime_operations import native_rule_timing_profile
from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
from digital_twin.modules.reasoning.domain.world_partitioned_reasoning import WORLD_PARTITIONED_REASONING_VERSION, compile_world_partitioned_rules
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.infrastructure.graph_store_rulebox import rulebox_rules_from_payload
from digital_twin.modules.reasoning.infrastructure.abox_persistence.world_calls import (
    typedb_call_for_world,
)
from digital_twin.modules.reasoning.infrastructure.backend_constants import (
    TYPEDB_NATIVE_BLOCKED_MODE,
    TYPEDB_NATIVE_MATERIALIZATION_SOURCE,
    TYPEDB_NATIVE_REASONING_MODE,
)
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import typedb_bool
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    typedb_native_rule_evidence_read_allows_active_membership_recovery,
    typedb_native_rule_evidence_read_index_for_execution,
    typedb_native_rule_planner_topology_for_execution,
    typedb_projection_preflight_graph_for_execution,
)
from digital_twin.modules.reasoning.infrastructure.typeql.planning import (
    typedb_native_rule_execution_selection,
    typedb_reasoning_subject_source_kinds,
)
from digital_twin.modules.reasoning.infrastructure.typeql.profiles import (
    typedb_native_reasoning_profile,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
    symbol_from_subject,
    typedb_rule_is_enabled,
    typedb_source_kind_uses_symbol_scope,
)
from typing import Dict
import copy
import time
from .cycle_ports import NativeExecutionCycleStore, NativeExecutionCycleRuntime


def _run_rulebox_unlocked(
    _store: NativeExecutionCycleStore,
    payload: Dict[str, object] = None,
    *,
    _bindings: NativeExecutionCycleRuntime
) -> Dict[str, object]:
    if not _store.address:
        return _bindings.NullTypeDBOntologyGraphRepository().run_rulebox(payload)
    # A rule run owns its diagnostic window.  Nested reads made while
    # preserving a previous InferenceBox deliberately pass
    # ``reset_metrics=False`` to their own snapshot method.
    _store.reset_query_metrics()
    payload = dict(payload) if isinstance(payload, dict) else {}
    stable_abox_write_lease_held = typedb_bool(payload.pop("_nativeInferenceWriteLeaseHeld", False))
    projection_preflight_graph = payload.pop("_nativePreflightProjectionGraph", None)
    projection_preflight_manifest_id = str(
        payload.pop("_nativePreflightProjectionManifestId", "") or ""
    ).strip()
    world_id = str(payload.get("worldId") or payload.get("ontologyWorldId") or "").strip()
    if "typedbNativeRuleExecutionEnabled" in payload:
        native_execution_enabled = typedb_bool(payload.get("typedbNativeRuleExecutionEnabled"))
    else:
        native_execution_enabled = _store.native_rule_execution_enabled()
    if not native_execution_enabled:
        return {
            "configured": True,
            "status": "skipped",
            "graphStore": "typedb",
            "source": "typedbNativeRule",
            "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
            "reason": "TypeDB native rule execution is disabled for this runtime path.",
            "statementCount": 0,
            "relationTypes": [],
            "nativeTypeDbReasoningUsed": False,
            "typedbDirectTypeqlUsed": False,
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "pythonCompatibilityReasonerUsed": False,
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
        }
    target_symbols = clean_symbols_from_payload(
        payload.get("symbols") or payload.get("targetSymbols") or payload.get("changedSymbols")
    )
    reasoning_subject_kinds = sorted(
        {
            str(value or "").upper().strip()
            for value in payload.get("reasoningSubjectKinds") or []
            if str(value or "").strip()
        }
    )
    reasoning_subject_ids = sorted(
        {
            str(value or "").strip()
            for value in payload.get("reasoningSubjectIds") or []
            if str(value or "").strip()
        }
    )
    allowed_source_kinds = typedb_reasoning_subject_source_kinds(reasoning_subject_kinds)
    adaptive_target_sharding_profile = (
        dict(payload.get("nativeRuleAdaptiveTargetShardingProfile") or {})
        if isinstance(payload.get("nativeRuleAdaptiveTargetShardingProfile"), dict)
        else {}
    )
    force_clear_requested = typedb_bool(payload.get("forceClearInference"))
    if "forceClearInference" not in payload:
        force_clear_requested = typedb_bool(payload.get("clearInference"))
    destructive_clear_allowed = typedb_bool(payload.get("allowDestructiveInferenceClear"))
    prune_requested = (
        typedb_bool(payload.get("pruneOldGenerations"))
        if "pruneOldGenerations" in payload
        else True
    )
    keep_generation_count = max(
        1,
        int(
            number_or_none(payload.get("keepGenerationCount"))
            or _store.inference_generation_keep_count
        ),
    )
    requested_generation_id = str(payload.get("generationId") or "").strip()
    generation_id = requested_generation_id or _bindings.inference_generation_id()
    fresh_inference_generation = not bool(requested_generation_id)
    generation_at = _bindings.utc_now()
    clear_requested = force_clear_requested and destructive_clear_allowed
    clear_result = {}
    if force_clear_requested and not clear_requested:
        clear_result = {
            "configured": True,
            "status": "skipped",
            "graphStore": "typedb",
            "reason": "InferenceBox is generation-scoped; destructive clear is skipped unless allowDestructiveInferenceClear is true.",
            "preservedPreviousInference": True,
        }
    try:
        abox_available = typedb_call_for_world(
            _store.has_box_rows,
            "ABox",
            world_id=world_id,
        )
        abox_metadata = (
            typedb_call_for_world(
                _store.active_abox_metadata,
                world_id=world_id,
            )
            if abox_available
            else {}
        )
    except Exception as error:  # noqa: BLE001 - report TypeDB read failures through diagnostics.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "source": "typedbNativeRule",
            "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": "TypeDB ABox 조회 실패: " + str(error)[:180],
            "statementCount": 0,
            "relationTypes": [],
            "nativeTypeDbReasoningUsed": False,
            "typedbNativeFunctionReasoningUsed": False,
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "clearResult": clear_result,
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
        }
    if not abox_available:
        return {
            "configured": True,
            "status": "missing-abox",
            "graphStore": "typedb",
            "source": "typedbNativeRule",
            "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
            "reason": "TypeDB에 실행 가능한 ABox 그래프가 없습니다.",
            "statementCount": 0,
            "nativeTypeDbReasoningUsed": False,
            "typedbNativeFunctionReasoningUsed": False,
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "clearResult": clear_result,
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
        }
    if str(abox_metadata.get("status") or "") != "ok":
        return {
            "configured": True,
            "status": "incomplete-abox",
            "graphStore": "typedb",
            "source": "typedbNativeRule",
            "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
            "reason": "TypeDB ABox 저장이 아직 완료되지 않아 투자 추론을 보류했습니다. "
            + str(
                abox_metadata.get("reason") or "완료 표식 또는 저장 건수를 다시 확인해야 합니다."
            )[:180],
            "statementCount": 0,
            "relationTypes": [],
            "nativeTypeDbReasoningUsed": False,
            "typedbNativeFunctionReasoningUsed": False,
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "clearResult": clear_result,
            "aboxMetadata": abox_metadata,
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
        }
    scoped_active_abox = (
        str(abox_metadata.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION
    )
    evidence_read_index = (
        typedb_native_rule_evidence_read_index_for_execution(
            abox_metadata,
            target_symbols=target_symbols,
        )
        if scoped_active_abox
        else {
            "status": "legacy",
            "source": "legacy-active-membership",
            "reason": "Active ABox has not yet migrated to a scoped Manifest evidence index.",
            "index": {},
        }
    )
    evidence_index_repair: Dict[str, object] = {}
    if scoped_active_abox and str(evidence_read_index.get("status") or "") != "verified":
        # Evidence-index reconstruction scans physical ABox membership and
        # rewrites control metadata. It belongs to projection maintenance,
        # not the latency-sensitive native inference request. A successor
        # projection repairs the active marker before staging new facts.
        evidence_index_repair = {
            "status": "deferred-to-projection-maintenance",
            "totalDurationMs": 0,
            "manifestId": str(
                abox_metadata.get("worldviewManifestId")
                or abox_metadata.get("aboxSnapshotId")
                or ""
            ),
        }
        return {
            "configured": True,
            "status": "deferred-manifest-evidence-index-repair",
            "graphStore": "typedb",
            "source": "typedbNativeRule",
            "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
            "reason": (
                "The active ABox evidence index requires control-plane repair; "
                "native inference did not perform a synchronous full-membership scan."
            ),
            "reasonCode": "manifest-evidence-index-repair-required",
            "recommendedRetryAfterSeconds": 30,
            "statementCount": 0,
            "relationTypes": [],
            "nativeTypeDbReasoningUsed": False,
            "typedbNativeFunctionReasoningUsed": False,
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "preservedActiveGeneration": True,
            "aboxMetadata": abox_metadata,
            "nativeRuleEvidenceReadIndexStatus": str(evidence_read_index.get("status") or ""),
            "nativeRuleEvidenceReadIndexReason": str(evidence_read_index.get("reason") or "")[:220],
            "nativeRuleEvidenceReadIndexRepairStatus": str(
                evidence_index_repair.get("status") or ""
            ),
            "nativeRuleEvidenceReadIndexRepairDurationMs": 0,
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
        }
    planner_topology = typedb_native_rule_planner_topology_for_execution(
        abox_metadata,
        (
            payload.get("nativeRulePlannerTopology")
            if isinstance(payload.get("nativeRulePlannerTopology"), dict)
            else {}
        ),
        target_symbols=target_symbols,
    )
    snapshot = _store.rulebox_snapshot()
    rules = snapshot.get("rules") if isinstance(snapshot.get("rules"), list) else []
    rulebox_metadata = _bindings.rulebox_runtime_metadata(rules)
    requested_impact_plan = payload.get("inferenceImpactPlan")
    if isinstance(requested_impact_plan, dict) and requested_impact_plan:
        compact_impact_plan = compact_inference_impact_plan(requested_impact_plan)
        rulebox_metadata.update(
            {
                "inferenceImpactPlan": compact_impact_plan,
                "impactPlanVersion": str(compact_impact_plan.get("version") or ""),
                "ruleExecutionScope": str(
                    compact_impact_plan.get("ruleExecutionScope") or "complete-native-evaluation"
                ),
                "nativeRuleSelectionApplied": bool(
                    compact_impact_plan.get("nativeRuleSelectionApplied")
                ),
                "ruleRoutingComplete": bool(compact_impact_plan.get("ruleRoutingComplete")),
            }
        )
    native_profile = typedb_native_reasoning_profile(rules)
    rulebox_metadata.update(_bindings.typedb_native_profile_metadata(native_profile))
    if str(snapshot.get("status") or "") != "ok" or not rules:
        return {
            "configured": True,
            "status": "rulebox-not-ready",
            "graphStore": "typedb",
            "source": "typedbNativeRule",
            "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
            "reason": str(snapshot.get("reason") or "TypeDB RuleBox rules are not available."),
            "statementCount": 0,
            "relationTypes": [],
            "nativeTypeDbReasoningUsed": False,
            "typedbNativeFunctionReasoningUsed": False,
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "clearResult": clear_result,
            "nativeReasoningProfile": native_profile,
            "ruleboxMetadata": rulebox_metadata,
            **rulebox_metadata,
        }
    native_stage_timings: Dict[str, int] = {}
    try:
        parsed_rules = [
            rule
            for rule in rulebox_rules_from_payload({"rules": rules})
            if typedb_rule_is_enabled(rule)
        ]
        full_parsed_rule_count = len(parsed_rules)
        rule_execution_phase = (
            str(payload.get("ruleExecutionPhase") or payload.get("worldRulePhase") or "")
            .strip()
            .lower()
        )
        world_partition = {}
        if rule_execution_phase in {"shared-premise", "account-overlay"}:
            world_partition = compile_world_partitioned_rules(parsed_rules)
            if str(world_partition.get("status") or "") != "ready":
                return {
                    "configured": True,
                    "status": "invalid-world-rule-partition",
                    "graphStore": "typedb",
                    "source": "typedbNativeRule",
                    "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                    "reason": "RuleBox world ownership is incomplete; no partial investment inference was produced.",
                    "worldPartitionedReasoningVersion": WORLD_PARTITIONED_REASONING_VERSION,
                    "ruleExecutionPhase": rule_execution_phase,
                    "worldPartitionFailures": list(world_partition.get("failures") or [])[:40],
                    "nativeTypeDbReasoningUsed": False,
                    "pythonCompatibilityReasonerUsed": False,
                }
            parsed_rules = list(
                (
                    world_partition.get("sharedRules")
                    if rule_execution_phase == "shared-premise"
                    else world_partition.get("overlayRules")
                )
                or []
            )
            rulebox_metadata.update(
                {
                    "worldPartitionedReasoningVersion": WORLD_PARTITIONED_REASONING_VERSION,
                    "ruleExecutionPhase": rule_execution_phase,
                    "sourceRuleCount": int(world_partition.get("sourceRuleCount") or 0),
                    "sharedPremiseRuleCount": int(world_partition.get("sharedRuleCount") or 0),
                    "accountOverlayRuleCount": int(world_partition.get("overlayRuleCount") or 0),
                    "mixedRuleCount": int(world_partition.get("mixedRuleCount") or 0),
                    "marketReadMirrorRemoved": rule_execution_phase == "account-overlay",
                }
            )
        if allowed_source_kinds:
            parsed_rules = [
                rule
                for rule in parsed_rules
                if str(getattr(rule, "source_kind", "") or "").strip().lower()
                in allowed_source_kinds
            ]
        impact_plan = compact_inference_impact_plan(requested_impact_plan or {})
        selection_requested = (
            typedb_bool(payload.get("typedbNativeRuleSelectionEnabled"))
            if "typedbNativeRuleSelectionEnabled" in payload
            else bool(impact_plan.get("nativeRuleSelectionEligible"))
        )
        rule_selection = typedb_native_rule_execution_selection(
            parsed_rules,
            candidate_rule_ids=impact_plan.get("candidateRuleIds") or [],
            prior_matched_rule_ids=payload.get("priorMatchedRuleIds") or [],
            eligible=selection_requested and bool(impact_plan.get("nativeRuleSelectionEligible")),
            prior_inference_reusable=typedb_bool(payload.get("priorInferenceReusable")),
            global_impact=bool(impact_plan.get("globalImpact")),
            bounded_global_context=bool(impact_plan.get("boundedGlobalContext")),
        )
        execution_rules = list(rule_selection.get("selectedRules") or parsed_rules)
        rule_target_symbols = (
            target_symbols
            if any(
                typedb_source_kind_uses_symbol_scope(getattr(rule, "source_kind", ""))
                for rule in execution_rules
            )
            else []
        )
        runtime_rulebox_metadata = dict(rulebox_metadata)
        runtime_rulebox_metadata.update(
            {
                "worldId": world_id,
                "worldType": str(abox_metadata.get("worldType") or payload.get("worldType") or ""),
                "tenantId": str(abox_metadata.get("tenantId") or payload.get("tenantId") or ""),
                "accountId": str(abox_metadata.get("accountId") or payload.get("accountId") or ""),
                "freshInferenceGeneration": fresh_inference_generation,
                "sourceAboxValidatedUnderWriteLease": stable_abox_write_lease_held,
                "targetSymbols": target_symbols,
                "ruleTargetSymbols": rule_target_symbols,
                "reasoningSubjectKinds": reasoning_subject_kinds,
                "reasoningSubjectIds": reasoning_subject_ids,
                "reasoningSubjectFilterApplied": bool(allowed_source_kinds),
                "reasoningSubjectAllowedSourceKinds": sorted(allowed_source_kinds),
                "reasoningSubjectFullRuleCount": full_parsed_rule_count,
                "reasoningSubjectSelectedRuleCount": len(parsed_rules),
                "incrementalScope": (
                    "portfolio-subject"
                    if allowed_source_kinds == {"portfolio"}
                    else "symbols" if target_symbols else "all-symbols"
                ),
                "ruleExecutionScope": (
                    str(
                        impact_plan.get("ruleExecutionScope")
                        or "subject-dependency-selected-native-evaluation"
                    )
                    if bool(rule_selection.get("selectionApplied"))
                    else "complete-native-evaluation"
                ),
                "nativeRuleSelectionCoverageMode": str(
                    rule_selection.get("coverageMode") or "complete-catalog"
                ),
                "nativeRuleSelectionApplied": bool(rule_selection.get("selectionApplied")),
                "nativeRuleSelectionFallbackReason": str(
                    rule_selection.get("fallbackReason") or ""
                ),
                "nativeRuleRoutingComplete": bool(impact_plan.get("ruleRoutingComplete")),
                "nativeRuleSelectionCandidateCount": len(
                    rule_selection.get("candidateRuleIds") or []
                ),
                "nativeRuleTriggerCandidateCount": len(impact_plan.get("triggerRuleIds") or []),
                "nativeRuleInvalidationCandidateCount": len(
                    impact_plan.get("invalidationRuleIds") or []
                ),
                "nativeRuleSelectionPriorMatchedCount": len(
                    rule_selection.get("priorMatchedRuleIds") or []
                ),
                "nativeRuleSelectionExecutedCount": len(
                    rule_selection.get("selectedRuleIds") or []
                ),
                "nativeRuleSelectionDeferredCount": len(
                    rule_selection.get("deferredRuleIds") or []
                ),
                "nativeRuleSelectionFullRuleCount": int(rule_selection.get("fullRuleCount") or 0),
                "nativeRuleSelectionFullRuleIds": list(rule_selection.get("selectedRuleIds") or [])
                + list(rule_selection.get("deferredRuleIds") or []),
                "nativeRuleSelectionExecutedRuleIds": list(
                    rule_selection.get("selectedRuleIds") or []
                )[:200],
                "nativeRuleSelectionDeferredRuleIds": list(
                    rule_selection.get("deferredRuleIds") or []
                )[:200],
                "nativeRulePlannerTopologyStatus": str(planner_topology.get("status") or ""),
                "nativeRulePlannerTopologySource": str(planner_topology.get("source") or ""),
                "nativeRulePlannerTopologyFingerprint": str(
                    planner_topology.get("fingerprint") or ""
                ),
                "nativeRulePlannerTopologyReason": str(planner_topology.get("reason") or "")[:220],
                "nativeRulePlannerSubjectPropertyIndexAvailable": bool(
                    planner_topology.get("subjectPropertyIndexAvailable")
                ),
                "nativeRulePlannerRelationEvidenceIndexAvailable": bool(
                    planner_topology.get("relationEvidenceIndexAvailable")
                ),
                "nativeRuleEvidenceReadIndexStatus": str(evidence_read_index.get("status") or ""),
                "nativeRuleEvidenceReadIndexSource": str(evidence_read_index.get("source") or ""),
                "nativeRuleEvidenceReadIndexFingerprint": str(
                    evidence_read_index.get("fingerprint") or ""
                ),
                "nativeRuleEvidenceReadIndexReason": str(evidence_read_index.get("reason") or "")[
                    :220
                ],
                "nativeRuleEvidenceReadIndexRepairStatus": str(
                    evidence_index_repair.get("status") or "not-required"
                ),
                "nativeRuleEvidenceReadIndexRepairDurationMs": int(
                    evidence_index_repair.get("totalDurationMs") or 0
                ),
                "pythonCompatibilityReasonerUsed": False,
                "typedbNativeStageTimings": dict(native_stage_timings),
            }
        )
        # Read a bounded, exact ABox slice before invoking schema
        # functions when the active Manifest can prove the physical rows
        # involved. This is only a negative preflight: it removes a rule
        # when one of its required facts is provably impossible. TypeDB
        # still evaluates every surviving rule and remains the sole
        # investment-rule evaluator.
        native_preflight = {
            "status": "not-available",
            "mode": "none",
            "reason": "Active Manifest has no verified exact evidence index.",
            "sourceCount": 0,
            "entityCount": 0,
            "relationCount": 0,
        }
        preflight_graph = None
        preflight_incoming_relations_complete = False
        preflight_started = time.perf_counter()
        if (
            target_symbols
            and str(evidence_read_index.get("status") or "") == "verified"
            and str(planner_topology.get("status") or "") == "verified"
        ):
            source_ids_by_symbol = dict(planner_topology.get("sourceIdsBySymbol") or {})
            preflight_source_ids = sorted(
                {
                    str(source_id or "").strip()
                    for symbol in target_symbols
                    for source_id in source_ids_by_symbol.get(str(symbol or "").upper().strip(), [])
                    or []
                    if str(source_id or "").strip()
                }
            )
            native_preflight["sourceCount"] = len(preflight_source_ids)
            projection_preflight = typedb_projection_preflight_graph_for_execution(
                projection_preflight_graph,
                projection_preflight_manifest_id,
                abox_metadata,
                planner_topology,
                target_symbols=target_symbols,
                world_id=world_id,
            )
            projection_preflight_status = str(projection_preflight.get("status") or "")
            if projection_preflight_status in {"ok", "partial"}:
                preflight_graph = projection_preflight.get("graph")
                preflight_incoming_relations_complete = projection_preflight_status == "ok"
                native_preflight.update(
                    {key: value for key, value in projection_preflight.items() if key != "graph"}
                )
            elif preflight_source_ids and _store.native_rule_durable_preflight_fallback_enabled():
                try:
                    preflight_graph_candidate = typedb_call_for_world(
                        _store.load_graph_for_native_matches,
                        {
                            "matches": [
                                {
                                    "sourceId": source_id,
                                    "sourceLabel": symbol_from_subject(source_id),
                                }
                                for source_id in preflight_source_ids
                            ]
                        },
                        execution_rules,
                        include_all_rule_relation_types=True,
                        include_incoming_relations=True,
                        evidence_read_index=evidence_read_index,
                        world_id=world_id,
                    )
                    preflight_read = dict(
                        getattr(preflight_graph_candidate, "worldview", {}).get(
                            "nativeEvidenceRead"
                        )
                        or {}
                    )
                    loaded_source_ids = {
                        str(entity.entity_id or "").strip()
                        for entity in getattr(preflight_graph_candidate, "entities", []) or []
                        if str(entity.entity_id or "").strip() in preflight_source_ids
                    }
                    native_preflight.update(
                        {
                            "status": str(preflight_read.get("status") or "incomplete"),
                            "mode": str(preflight_read.get("mode") or "manifest-storage-index"),
                            "reason": str(preflight_read.get("reason") or "")[:220],
                            "entityCount": len(
                                getattr(preflight_graph_candidate, "entities", []) or []
                            ),
                            "relationCount": len(
                                getattr(preflight_graph_candidate, "relations", []) or []
                            ),
                            "loadedSourceCount": len(loaded_source_ids),
                        }
                    )
                    if native_preflight["status"] == "ok" and set(preflight_source_ids).issubset(
                        loaded_source_ids
                    ):
                        preflight_graph = preflight_graph_candidate
                        preflight_incoming_relations_complete = True
                    elif native_preflight["status"] == "ok":
                        native_preflight.update(
                            {
                                "status": "incomplete",
                                "reason": "Manifest-indexed preflight did not return every target stock.",
                            }
                        )
                except (
                    Exception
                ) as error:  # noqa: BLE001 - an optimization must never weaken native correctness.
                    native_preflight.update(
                        {
                            "status": "error",
                            "mode": "manifest-storage-index",
                            "reason": "Manifest-indexed preflight lookup failed: "
                            + str(error)[:180],
                        }
                    )
            elif not preflight_source_ids:
                native_preflight.update(
                    {
                        "status": "incomplete",
                        "mode": "manifest-storage-index",
                        "reason": "Manifest planner topology has no target stock source IDs.",
                    }
                )
            else:
                native_preflight.update(
                    {
                        "status": "skipped",
                        "mode": "planner-topology-only",
                        "reason": (
                            "Durable TypeDB preflight reread is disabled; TypeDB evaluates "
                            "the surviving native rules without a second ABox graph read."
                        ),
                    }
                )
        native_stage_timings["preflightReadMs"] = int(
            (time.perf_counter() - preflight_started) * 1000
        )
        runtime_rulebox_metadata.update(
            {
                "nativeRulePreflightStatus": str(native_preflight.get("status") or ""),
                "nativeRulePreflightMode": str(native_preflight.get("mode") or ""),
                "nativeRulePreflightReason": str(native_preflight.get("reason") or "")[:220],
                "nativeRulePreflightSourceCount": int(
                    number_or_none(native_preflight.get("sourceCount")) or 0
                ),
                "nativeRulePreflightLoadedSourceCount": int(
                    number_or_none(native_preflight.get("loadedSourceCount")) or 0
                ),
                "nativeRulePreflightEntityCount": int(
                    number_or_none(native_preflight.get("entityCount")) or 0
                ),
                "nativeRulePreflightRelationCount": int(
                    number_or_none(native_preflight.get("relationCount")) or 0
                ),
                "typedbNativeStageTimings": dict(native_stage_timings),
            }
        )
        # Direct TypeQL is the sole production execution strategy. Rule
        # selection and bounded query execution happen in this lifecycle;
        # there is no separate rule preparation or schema-write stage.
        indexed_rule_count = 0
        execution_mode = "typedb-native-direct-typeql"
        if bool(rule_selection.get("selectionApplied")):
            execution_mode += "-dependency-selected"
        elif target_symbols:
            execution_mode += "-filtered"
        runtime_rulebox_metadata.update(
            {
                "typedbNativeIndexedRuleCandidateCount": indexed_rule_count,
                "typedbNativeIndexedRuleCandidateIds": [],
                "typedbRuleExecutionStrategy": "direct-typeql",
                "typedbNativeExecutionMode": execution_mode,
                "typedbNativeStageTimings": dict(native_stage_timings),
            }
        )
        native_query_started = time.perf_counter()
        native_rule_parallelism = (
            _store.native_rule_parallelism() if stable_abox_write_lease_held else 1
        )
        native_rule_target_parallelism = (
            _store.native_rule_target_parallelism() if stable_abox_write_lease_held else 1
        )
        native_match_result = typedb_call_for_world(
            _store.match_typedb_native_rules,
            execution_rules,
            target_symbols=target_symbols,
            world_id=world_id,
            planner_topology=(
                dict(planner_topology.get("topology") or {})
                if str(planner_topology.get("status") or "") == "verified"
                else None
            ),
            preflight_graph=preflight_graph,
            preflight_incoming_relations_complete=preflight_incoming_relations_complete,
            native_rule_parallelism=native_rule_parallelism,
            native_rule_target_parallelism=native_rule_target_parallelism,
            adaptive_target_sharding_profile=adaptive_target_sharding_profile,
            stable_abox_write_lease_held=stable_abox_write_lease_held,
            evidence_read_index=evidence_read_index,
        )
        native_stage_timings["nativeRuleQueriesMs"] = int(
            (time.perf_counter() - native_query_started) * 1000
        )
        native_query_used = str(native_match_result.get("status") or "") == "ok"
        native_rule_timing = native_rule_timing_profile(native_match_result)
        native_rule_timing["wallClockMs"] = native_stage_timings["nativeRuleQueriesMs"]
        evidence_field_index = dict(native_match_result.get("evidenceFieldIndex") or {})
        materialization_evidence_read_index = dict(
            native_match_result.pop(
                "_materializationEvidenceReadIndex",
                evidence_read_index,
            )
            or {}
        )
        native_execution_plan = dict(native_match_result.get("executionPlan") or {})
        model_signal_bridge_execution = dict(
            native_match_result.get("modelSignalBridgeExecution") or {}
        )
        runtime_rulebox_metadata.update(
            {
                "typedbNativeRuleQueryStatus": str(native_match_result.get("status") or ""),
                "typedbNativeRuleQueryUsed": bool(native_match_result.get("nativeQueryUsed")),
                "typedbDirectTypeqlQueryUsed": bool(native_match_result.get("nativeQueryUsed")),
                "typedbNativeIndexedRuleQueryUsed": bool(
                    native_match_result.get("indexedEvidenceQueryUsed")
                ),
                "typedbNativeEvidenceFieldIndexStatus": str(
                    evidence_field_index.get("status") or ""
                ),
                "typedbNativeEvidenceFieldIndexChunkCount": int(
                    number_or_none(evidence_field_index.get("chunkCount")) or 0
                ),
                "typedbNativeEvidenceFieldIndexStorageIdentityCount": int(
                    number_or_none(evidence_field_index.get("storageIdentityCount")) or 0
                ),
                "typedbNativeEvidenceFieldIndexFieldRowCount": int(
                    number_or_none(evidence_field_index.get("fieldRowCount")) or 0
                ),
                "typedbNativeEvidenceFieldIndexRelationTypes": list(
                    evidence_field_index.get("relationTypes") or []
                )[:40],
                "typedbNativeRuleMatchedCount": int(
                    number_or_none(native_match_result.get("matchedCount")) or 0
                ),
                "typedbNativeRuleMatchedRuleIds": sorted(
                    {
                        str(item.get("ruleId") or "").strip()
                        for item in native_match_result.get("matches") or []
                        if isinstance(item, dict) and str(item.get("ruleId") or "").strip()
                    }
                )[:160],
                "typedbNativeRuleExecutedCount": int(
                    number_or_none(native_match_result.get("executedRuleCount")) or 0
                ),
                "typedbNativeRuleExecutedWorkCount": int(
                    number_or_none(native_match_result.get("executedRuleWorkCount")) or 0
                ),
                "typedbNativeRuleSkippedCount": int(
                    number_or_none(native_match_result.get("skippedRuleCount")) or 0
                ),
                "typedbModelSignalLogicalPolicyCount": int(
                    number_or_none(
                        model_signal_bridge_execution.get("logicalModelSignalPolicyCount")
                    )
                    or 0
                ),
                "typedbModelSignalBatchedSimplePolicyCount": int(
                    number_or_none(model_signal_bridge_execution.get("batchedSimplePolicyCount"))
                    or 0
                ),
                "typedbModelSignalConstrainedPolicyCount": int(
                    number_or_none(model_signal_bridge_execution.get("constrainedPolicyCount")) or 0
                ),
                "typedbModelSignalBridgeReadCount": int(
                    number_or_none(model_signal_bridge_execution.get("modelSignalBridgeReadCount"))
                    or 0
                ),
                "typedbModelSignalEliminatedPolicyQueryCount": int(
                    number_or_none(
                        model_signal_bridge_execution.get("eliminatedModelSignalPolicyQueryCount")
                    )
                    or 0
                ),
                "typedbModelSignalIndexedEvidenceReadCount": int(
                    number_or_none(model_signal_bridge_execution.get("indexedEvidenceReadCount"))
                    or 0
                ),
                "typedbModelSignalIgnoredContractIds": list(
                    model_signal_bridge_execution.get("ignoredContractIds") or []
                )[:20],
                "typedbNativeManifestEvidencePreflightEnabled": bool(
                    native_execution_plan.get("manifestEvidencePreflightEnabled")
                ),
                "typedbNativeRelationEvidencePreflightEnabled": bool(
                    native_execution_plan.get("relationEvidencePreflightEnabled")
                ),
                "typedbNativeManifestEvidencePreflightPrunedSymbolCount": int(
                    number_or_none(
                        native_execution_plan.get("manifestEvidencePreflightPrunedSymbolCount")
                    )
                    or 0
                ),
                "nativeInferenceEvaluationComplete": bool(
                    native_match_result.get("nativeInferenceEvaluationComplete", True)
                ),
                "coreNativeInferenceEvaluationComplete": bool(
                    native_match_result.get("coreNativeInferenceEvaluationComplete", True)
                ),
                "nativeCoverageStatus": str(
                    native_match_result.get("nativeCoverageStatus") or "complete"
                ),
                "supportingRuleFailureCount": int(
                    number_or_none(native_match_result.get("supportingRuleFailureCount")) or 0
                ),
                "supportingRuleFailures": list(
                    native_match_result.get("supportingRuleFailures") or []
                ),
                "typedbNativeRuleParallelism": int(
                    number_or_none(native_match_result.get("nativeRuleParallelism")) or 1
                ),
                "typedbNativeRuleParallelUsed": bool(
                    native_match_result.get("parallelRuleExecution")
                ),
                "typedbNativeRuleSubjectRuleParallelism": int(
                    number_or_none(native_match_result.get("subjectRuleParallelism")) or 1
                ),
                "typedbNativeRuleTotalReadParallelismCap": int(
                    number_or_none(native_match_result.get("totalReadParallelismCap")) or 1
                ),
                "typedbNativeRuleEffectiveTotalReadParallelism": int(
                    number_or_none(native_match_result.get("effectiveTotalReadParallelism")) or 1
                ),
                "typedbNativeRuleTargetParallelism": int(
                    number_or_none(native_match_result.get("nativeRuleTargetParallelism")) or 1
                ),
                "typedbNativeRuleTargetWorkShardingUsed": bool(
                    native_match_result.get("targetWorkShardingUsed")
                ),
                "typedbNativeRuleTargetWorkShardingEnabled": bool(
                    native_match_result.get("targetWorkShardingEnabled")
                ),
                "typedbNativeRuleTargetWorkShardingSuppressed": bool(
                    native_match_result.get("targetWorkShardingSuppressed")
                ),
                "typedbNativeRuleTargetWorkShardCount": int(
                    number_or_none(native_match_result.get("targetWorkShardCount")) or 0
                ),
                "typedbNativeRuleWorkItemCount": int(
                    number_or_none(native_match_result.get("targetWorkItemCount")) or 0
                ),
                "typedbNativeRuleAdaptiveTargetShardingEnabled": bool(
                    native_match_result.get("targetWorkAdaptiveShardingEnabled")
                ),
                "typedbNativeRuleAdaptiveTargetShardingProfileStatus": str(
                    native_match_result.get("targetWorkAdaptiveShardingProfileStatus") or ""
                ),
                "typedbNativeRuleAdaptiveTargetShardingUsed": bool(
                    native_match_result.get("targetWorkAdaptiveShardingUsed")
                ),
                "typedbNativeRuleAdaptiveTargetShardedRuleCount": int(
                    number_or_none(native_match_result.get("targetWorkAdaptiveShardedRuleCount"))
                    or 0
                ),
                "typedbNativeRuleAdaptiveTargetShardedRuleIds": list(
                    native_match_result.get("targetWorkAdaptiveShardedRuleIds") or []
                )[:20],
                "typedbNativeRuleTimeoutFallbackUsed": bool(
                    native_match_result.get("timeoutFallbackUsed")
                ),
                "typedbNativeRuleTimeoutFallbackRuleCount": int(
                    number_or_none(native_match_result.get("timeoutFallbackRuleCount")) or 0
                ),
                "typedbNativeRuleTimeoutFallbackShardCount": int(
                    number_or_none(native_match_result.get("timeoutFallbackShardCount")) or 0
                ),
                # All target shards are merged before this one generation is
                # written, so no partial target result can become active.
                "typedbNativeRuleCommitMode": "single-inferencebox-generation",
                "typedbNativeRuleTimingProfile": native_rule_timing,
                "pythonCompatibilityReasonerUsed": False,
                "typedbNativeStageTimings": dict(native_stage_timings),
            }
        )
        if not native_query_used:
            runtime_rulebox_metadata["typedbNativeRuleQueryReason"] = str(
                native_match_result.get("reason") or ""
            )
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "source": "typedbNativeRule",
                "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                "reasonCode": str(
                    native_match_result.get("reasonCode") or "typedbDirectTypeqlQueryError"
                ),
                "reason": "TypeDB 직접 TypeQL 규칙 실행 실패: "
                + str(native_match_result.get("reason") or "")[:180],
                "statementCount": 0,
                "relationTypes": [],
                "nativeTypeDbReasoningUsed": False,
                "typedbDirectTypeqlUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "pythonCompatibilityReasonerUsed": False,
                "clearResult": clear_result,
                "nativeReasoningProfile": native_profile,
                "nativeMatchResult": native_match_result,
                "ruleboxMetadata": runtime_rulebox_metadata,
                "typedbQueryMetrics": _store.query_metrics_snapshot(),
                **runtime_rulebox_metadata,
            }
        native_matches = [
            item
            for item in native_match_result.get("matches") or []
            if isinstance(item, dict) and str(item.get("sourceId") or "").strip()
        ]
        if (
            scoped_active_abox
            and native_matches
            and not typedb_native_rule_evidence_read_allows_active_membership_recovery(
                materialization_evidence_read_index
            )
        ):
            return {
                "configured": True,
                "status": "evidence-read-index-unavailable",
                "graphStore": "typedb",
                "source": "typedbNativeRule",
                "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                "reasonCode": "typedbEvidenceReadIndexUnavailable",
                "reason": (
                    "활성 ABox Manifest에 현재 근거 조회용 인덱스가 없어 TypeDB 규칙 결과를 안전하게 "
                    "설명 그래프로 만들지 않았습니다. 다음 ABox 재투영에서 인덱스를 생성한 뒤 다시 실행합니다. "
                    + str(evidence_read_index.get("reason") or "")[:120]
                ),
                "statementCount": 0,
                "relationTypes": [],
                "nativeTypeDbReasoningUsed": False,
                "typedbDirectTypeqlUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "preservedPreviousInference": True,
                "requiresAboxReprojection": True,
                "clearResult": clear_result,
                "nativeReasoningProfile": native_profile,
                "nativeMatchResult": native_match_result,
                "ruleboxMetadata": runtime_rulebox_metadata,
                "typedbQueryMetrics": _store.query_metrics_snapshot(),
                **runtime_rulebox_metadata,
            }
        graph_load_kwargs = (
            {"evidence_read_index": materialization_evidence_read_index}
            if scoped_active_abox
            else {}
        )
        matched_graph_read_started = time.perf_counter()
        graph = None
        matched_graph_source = "typedb-durable-evidence-read"
        matched_graph_reuse = {
            "status": "not-attempted",
            "reason": "A complete verified preflight graph was not available.",
        }
        if isinstance(preflight_graph, PortfolioOntology):
            preflight_mode = str(native_preflight.get("mode") or "")
            if preflight_mode.startswith("projection-verified-in-memory"):
                # A routed projection can be partial relative to every
                # requested subject and still be complete for the rules
                # TypeDB actually matched. The exact active-Manifest
                # storage-id proof below is the authoritative gate and
                # falls back to the durable read when any row is absent.
                matched_graph_reuse = _store.projection_graph_for_native_matches(
                    preflight_graph,
                    native_match_result,
                    execution_rules,
                    evidence_read_index=materialization_evidence_read_index,
                )
                if str(matched_graph_reuse.get("status") or "") == "ok":
                    graph = matched_graph_reuse.get("graph")
                    matched_graph_source = "projection-verified-in-memory"
            elif preflight_incoming_relations_complete:
                preflight_evidence_read = dict(
                    (preflight_graph.worldview or {}).get("nativeEvidenceRead") or {}
                )
                if str(preflight_evidence_read.get("status") or "") == "ok":
                    graph = copy.deepcopy(preflight_graph)
                    matched_graph_source = "durable-preflight-reuse"
                    matched_graph_reuse = {
                        "status": "ok",
                        "reason": "The complete durable preflight graph already contains all selected rule relation types.",
                    }
        if not isinstance(graph, PortfolioOntology):
            graph = typedb_call_for_world(
                _store.load_graph_for_native_matches,
                native_match_result,
                execution_rules,
                world_id=world_id,
                **graph_load_kwargs,
            )
        native_stage_timings["matchedGraphReadMs"] = int(
            (time.perf_counter() - matched_graph_read_started) * 1000
        )
        runtime_rulebox_metadata.update(
            {
                "matchedGraphSource": matched_graph_source,
                "matchedGraphReuseStatus": str(matched_graph_reuse.get("status") or ""),
                "matchedGraphReuseReason": str(matched_graph_reuse.get("reason") or "")[:220],
            }
        )
        graph.worldview.update(
            {
                "worldId": world_id,
                "worldType": str(abox_metadata.get("worldType") or payload.get("worldType") or ""),
                "tenantId": str(abox_metadata.get("tenantId") or payload.get("tenantId") or ""),
                "accountId": str(abox_metadata.get("accountId") or payload.get("accountId") or ""),
            }
        )
        before_entities = len(graph.entities)
        before_relations = len(graph.relations)
        matched_source_ids = {
            str(item.get("sourceId") or "").strip()
            for item in native_match_result.get("matches") or []
            if isinstance(item, dict) and str(item.get("sourceId") or "").strip()
        }
        source_entities = [
            item
            for item in graph.entities
            if str(item.entity_id or "").strip() in matched_source_ids
        ]
        native_evidence_read = dict(graph.worldview.get("nativeEvidenceRead") or {})
        if not native_evidence_read and not scoped_active_abox:
            # Compatibility graph-store doubles and the pre-scoped ABox
            # reader have no Manifest index contract. Production legacy
            # reads still use ``load_graph_for_native_matches`` above,
            # which records its own diagnostics.
            native_evidence_read = {
                "status": "ok",
                "mode": "legacy-compatibility",
                "reason": "",
                "loadedSourceCount": len(source_entities),
                "loadedRelationCount": len(graph.relations),
            }
        runtime_rulebox_metadata.update(
            {
                "nativeEvidenceReadStatus": str(native_evidence_read.get("status") or ""),
                "nativeEvidenceReadMode": str(native_evidence_read.get("mode") or ""),
                "nativeEvidenceReadLoadedSourceCount": int(
                    number_or_none(native_evidence_read.get("loadedSourceCount")) or 0
                ),
                "nativeEvidenceReadLoadedRelationCount": int(
                    number_or_none(native_evidence_read.get("loadedRelationCount")) or 0
                ),
                "nativeEvidenceReadCandidateRelationCount": int(
                    number_or_none(native_evidence_read.get("candidateRelationStorageCount")) or 0
                ),
                "nativeEvidenceReadSelectedRelationCount": int(
                    number_or_none(native_evidence_read.get("selectedEvidenceStorageCount")) or 0
                ),
                "nativeEvidenceReadNarrowingPct": float(
                    number_or_none(native_evidence_read.get("evidenceNarrowingPct")) or 0.0
                ),
                "nativeEvidenceReadFallbackConditionCount": int(
                    number_or_none(native_evidence_read.get("evidenceFallbackConditionCount")) or 0
                ),
                "nativeEvidenceReadReason": str(native_evidence_read.get("reason") or "")[:220],
            }
        )
        if native_matches and str(native_evidence_read.get("status") or "") != "ok":
            return {
                "configured": True,
                "status": "evidence-read-failed",
                "graphStore": "typedb",
                "source": "typedbNativeRule",
                "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                "reasonCode": "typedbEvidenceReadFailed",
                "reason": "TypeDB 규칙 결과의 근거 사실을 완전하게 읽지 못해 새 투자 판단을 차단했습니다. "
                + str(native_evidence_read.get("reason") or "")[:180],
                "statementCount": 0,
                "relationTypes": [],
                "nativeTypeDbReasoningUsed": False,
                "typedbDirectTypeqlUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "preservedPreviousInference": True,
                "clearResult": clear_result,
                "nativeReasoningProfile": native_profile,
                "nativeMatchResult": native_match_result,
                "ruleboxMetadata": runtime_rulebox_metadata,
                "typedbQueryMetrics": _store.query_metrics_snapshot(),
                **runtime_rulebox_metadata,
            }
        active_abox_generation_id = _bindings.typedb_abox_inference_generation_id(abox_metadata)
        if scoped_active_abox:
            # Individual scopes intentionally have different immutable
            # generation IDs and may be reused by a later Manifest. The
            # JSON provenance on a reused fact can therefore contain the
            # Manifest that first wrote it. The exact storage identities
            # in the verified active Manifest evidence index, not the
            # historical JSON field, prove the one live Worldview source
            # identity used by this materialization.
            source_entity_ids = {
                str(item.entity_id or "").strip()
                for item in source_entities
                if str(item.entity_id or "").strip()
            }
            stored_source_manifest_ids = sorted(
                {
                    str((item.properties or {}).get("worldviewManifestId") or "").strip()
                    for item in source_entities
                    if str((item.properties or {}).get("worldviewManifestId") or "").strip()
                }
            )
            missing_source_generation = bool(matched_source_ids) and (
                source_entity_ids != matched_source_ids
                or any(
                    bool((item.properties or {}).get("queryFallback")) for item in source_entities
                )
            )
            source_generation_valid = (
                bool(active_abox_generation_id) and not missing_source_generation
            )
            source_abox_snapshot_ids = (
                [active_abox_generation_id] if source_generation_valid else []
            )
            runtime_rulebox_metadata["sourceAboxManifestId"] = active_abox_generation_id
            runtime_rulebox_metadata["sourceAboxStoredManifestIds"] = stored_source_manifest_ids
            runtime_rulebox_metadata["sourceAboxMembershipValidation"] = (
                "manifest-storage-index"
                if str(evidence_read_index.get("status") or "") == "verified"
                else "active-scope-pointer"
            )
        else:
            source_abox_snapshot_ids = sorted(
                {
                    str(
                        (item.properties or {}).get("aboxSnapshotId")
                        or (item.properties or {}).get("snapshotId")
                        or ""
                    ).strip()
                    for item in source_entities
                    if str(
                        (item.properties or {}).get("aboxSnapshotId")
                        or (item.properties or {}).get("snapshotId")
                        or ""
                    ).strip()
                }
            )
            missing_source_generation = bool(matched_source_ids) and (
                len(source_entities) != len(matched_source_ids)
                or any(
                    not str(
                        (item.properties or {}).get("aboxSnapshotId")
                        or (item.properties or {}).get("snapshotId")
                        or ""
                    ).strip()
                    for item in source_entities
                )
            )
            source_generation_valid = (
                len(source_abox_snapshot_ids) == 1
                and not missing_source_generation
                and (
                    not active_abox_generation_id
                    or source_abox_snapshot_ids[0] == active_abox_generation_id
                )
            )
        # A successful native evaluation can legitimately match no
        # source subject. In that case the active ABox pointer itself is
        # the provenance proof: there is no matched row whose historical
        # generation needs validating. Treating this as an invalid source
        # forces an unnecessary rollback and leaves the entire reasoning
        # worker stuck on an older InferenceBox generation.
        if not matched_source_ids and active_abox_generation_id:
            source_abox_snapshot_ids = [active_abox_generation_id]
            source_generation_valid = True
        if source_generation_valid:
            runtime_rulebox_metadata["sourceAboxSnapshotId"] = (
                active_abox_generation_id or source_abox_snapshot_ids[0]
            )
        runtime_rulebox_metadata["sourceAboxSnapshotCount"] = len(source_abox_snapshot_ids)
        runtime_rulebox_metadata["sourceAboxGenerationMode"] = (
            "worldview-manifest" if scoped_active_abox else "snapshot"
        )
        # Keep the proof used by both successful and blocked native
        # materialization paths in the durable execution metadata. Scoped
        # facts may retain the manifest that first created them, so the
        # active scope pointer is the authoritative membership check.
        runtime_rulebox_metadata["sourceAboxGenerationValid"] = source_generation_valid
        runtime_rulebox_metadata["sourceAboxSnapshotIds"] = list(source_abox_snapshot_ids)
        inference_graph_started = time.perf_counter()
        _bindings.materialize_typedb_native_matches(graph, execution_rules, native_match_result)
        native_match_found = bool(graph.relations)
        # A no-match result is still a complete TypeDB evaluation. Persist
        # an empty generation marker with the active ABox provenance so a
        # new factual generation can be finalized without reusing stale
        # relations from an older market snapshot.
        runtime_rulebox_metadata["nativeInferenceEvaluationComplete"] = bool(
            native_match_result.get("nativeInferenceEvaluationComplete", True)
        )
        runtime_rulebox_metadata["coreNativeInferenceEvaluationComplete"] = bool(
            native_match_result.get("coreNativeInferenceEvaluationComplete", True)
        )
        runtime_rulebox_metadata["nativeCoverageStatus"] = str(
            native_match_result.get("nativeCoverageStatus") or "complete"
        )
        runtime_rulebox_metadata["nativeInferenceOutcome"] = (
            "matched" if native_match_found else "no-match"
        )
        runtime_rulebox_metadata["nativeInferenceNoMatch"] = not native_match_found
        inference_graph = _bindings.typedb_inferencebox_graph(
            graph,
            generation_id=generation_id,
            generation_at=generation_at,
            rulebox_metadata=runtime_rulebox_metadata,
        )
        native_stage_timings["inferenceGraphBuildMs"] = int(
            (time.perf_counter() - inference_graph_started) * 1000
        )
        inferencebox_limit = max(
            80, min(500, int(number_or_none(payload.get("inferenceSnapshotLimit")) or 500))
        )
        invalid_abox_generation = bool(inference_graph.relations) and not source_generation_valid
        if invalid_abox_generation:
            previous_inferencebox = _store.inferencebox_snapshot(
                symbols=rule_target_symbols,
                limit=inferencebox_limit,
                reset_metrics=False,
                world_id=world_id,
            )
            return {
                "configured": True,
                "status": "invalid-abox-generation",
                "graphStore": "typedb",
                "source": "typedbNativeRule",
                "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                "reason": (
                    "원본 ABox 세대를 하나로 확인할 수 없어 새 InferenceBox를 활성화하지 않았습니다."
                    if invalid_abox_generation
                    else "TypeDB native rules could not verify the source ABox generation."
                ),
                "statementCount": 0,
                "entityCount": 0,
                "relationCount": 0,
                "traceCount": 0,
                "relationTypes": [],
                "nativeTypeDbReasoningUsed": False,
                "typedbNativeRuleReasoningUsed": False,
                "typedbNativeFunctionReasoningUsed": False,
                "typedbBootstrapReasoningUsed": False,
                "pythonBootstrapDisabled": True,
                "preservedPreviousInference": True,
                "activatedGeneration": False,
                "sourceAboxSnapshotIds": source_abox_snapshot_ids,
                "sourceAboxGenerationMode": runtime_rulebox_metadata["sourceAboxGenerationMode"],
                "sourceAboxGenerationValid": source_generation_valid,
                "inferenceGenerationId": generation_id,
                "inferenceGenerationAt": generation_at,
                "targetSymbols": target_symbols,
                "saveResult": {"saved": False, "status": "skipped-preserve-previous"},
                "clearResult": clear_result,
                "inferenceBox": previous_inferencebox,
                "nativeReasoningProfile": native_profile,
                "nativeMatchResult": {
                    key: native_match_result.get(key)
                    for key in [
                        "status",
                        "reason",
                        "reasonCode",
                        "nativeQueryUsed",
                        "indexedEvidenceQueryUsed",
                        "executedRuleCount",
                        "skippedRuleCount",
                        "matchedCount",
                        "executedRules",
                        "skippedRules",
                        "nativeExecutionMode",
                        "readTransactionCount",
                        "readQueryCount",
                        "executionPlan",
                        "blockingRule",
                        "typedbQueryMetrics",
                        "modelSignalBridgeExecution",
                    ]
                    if key in native_match_result
                },
                "ruleboxMetadata": runtime_rulebox_metadata,
                "typedbQueryMetrics": _store.query_metrics_snapshot(),
                **runtime_rulebox_metadata,
            }
        if clear_requested and native_match_found:
            clear_result = _store.clear_inferencebox(world_id=world_id)
            if str(clear_result.get("status") or "") != "ok":
                return {
                    "configured": True,
                    "status": "error",
                    "graphStore": "typedb",
                    "source": "typedbNativeRule",
                    "reasoningMode": TYPEDB_NATIVE_BLOCKED_MODE,
                    "reasonCode": str(clear_result.get("reasonCode") or "typedbClearError"),
                    "reason": "TypeDB InferenceBox 초기화 실패: "
                    + str(clear_result.get("reason") or clear_result.get("status") or ""),
                    "statementCount": 0,
                    "relationTypes": [],
                    "nativeTypeDbReasoningUsed": False,
                    "typedbNativeFunctionReasoningUsed": False,
                    "typedbBootstrapReasoningUsed": False,
                    "pythonBootstrapDisabled": True,
                    "clearResult": clear_result,
                    "nativeReasoningProfile": native_profile,
                    "nativeMatchResult": native_match_result,
                    "ruleboxMetadata": runtime_rulebox_metadata,
                    **runtime_rulebox_metadata,
                }
        elif clear_requested:
            clear_result = {
                "configured": True,
                "status": "skipped",
                "graphStore": "typedb",
                "reason": "A complete no-match generation is activated by pointer swap; destructive InferenceBox clear is skipped.",
                "preservedPreviousInference": True,
            }
        inference_write_started = time.perf_counter()
        save_result = _store.write_inferencebox_graph(inference_graph)
        native_stage_timings["inferenceBoxWriteMs"] = int(
            (time.perf_counter() - inference_write_started) * 1000
        )
        inference_write_timing = (
            dict(save_result.get("writeTiming") or {})
            if isinstance(save_result.get("writeTiming"), dict)
            else {}
        )
        for source_key, target_key in {
            "candidateDeleteMs": "inferenceBoxCandidateDeleteMs",
            "candidateNodeWriteMs": "inferenceBoxNodeWriteMs",
            "candidateRelationWriteMs": "inferenceBoxRelationWriteMs",
            "candidateMarkerMs": "inferenceBoxCandidateMarkerMs",
            "candidateValidationMs": "inferenceBoxCandidateValidationMs",
            "activationMs": "inferenceBoxActivationMs",
            "totalQueryMs": "inferenceBoxQueryMs",
        }.items():
            value = number_or_none(inference_write_timing.get(source_key))
            if value is not None:
                native_stage_timings[target_key] = int(max(0, value))
        runtime_rulebox_metadata["inferenceBoxCandidateDeleteSkipped"] = bool(
            inference_write_timing.get("candidateDeleteSkipped")
        )
        runtime_rulebox_metadata["typedbNativeStageTimings"] = dict(native_stage_timings)
    except (
        Exception
    ) as error:  # noqa: BLE001 - expose materialization failures to monitoring diagnostics.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "source": "typedbNativeRule",
            "reasoningMode": TYPEDB_NATIVE_REASONING_MODE,
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": "TypeDB native rule materialization failed: " + str(error)[:180],
            "statementCount": 0,
            "relationTypes": [],
            "nativeTypeDbReasoningUsed": False,
            "typedbNativeFunctionReasoningUsed": False,
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "clearResult": clear_result,
            "nativeReasoningProfile": native_profile,
            "ruleboxMetadata": rulebox_metadata,
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
            **rulebox_metadata,
        }
    relation_types = sorted(
        {
            str(item.relation_type or "")
            for item in inference_graph.relations
            if str(item.relation_type or "").strip()
        }
    )
    materialized_entity_count = (
        len(inference_graph.entities) + len(inference_graph.evidence) + len(inference_graph.beliefs)
    )
    materialized_relation_count = len(inference_graph.relations)
    has_materialized_relations = materialized_relation_count > 0
    saved_ok = bool(save_result.get("saved"))
    native_evaluation_completed = bool(saved_ok)
    native_inference_outcome = "matched" if has_materialized_relations else "no-match"
    prune_result = (
        typedb_call_for_world(
            _store.prune_inferencebox_generations,
            generation_id,
            keep_count=keep_generation_count,
            world_id=world_id,
        )
        if saved_ok and prune_requested
        else {}
    )
    inferencebox_payload = _store.inferencebox_snapshot_from_graph(
        inference_graph,
        rule_target_symbols,
        inferencebox_limit,
    )
    # The materialization graph contains only direct RuleBox premises.
    # Historical calibration is active ABox state and needs its own bounded
    # membership read so it reaches hypothesis comparison and the AI input.
    inferencebox_payload["hypothesisCalibration"] = (
        _store.hypothesis_calibration_snapshot_for_native_result(
            graph,
            target_symbols,
            str(runtime_rulebox_metadata.get("sourceAboxSnapshotId") or ""),
            source_generation_valid,
            scoped_active_abox,
            limit=min(40, inferencebox_limit),
            world_id=world_id,
        )
    )
    return {
        "configured": True,
        "status": (
            ("ok" if has_materialized_relations else "empty")
            if saved_ok
            else str(save_result.get("status") or "error")
        ),
        "graphStore": "typedb",
        "source": "typedbNativeRule",
        "reasoningMode": TYPEDB_NATIVE_REASONING_MODE,
        "reason": (
            (
                ""
                if has_materialized_relations
                else "TypeDB native rules completed successfully, but no current ABox fact matched an enabled RuleBox rule."
            )
            if saved_ok
            else str(save_result.get("reason") or "")
        ),
        "statementCount": materialized_entity_count + materialized_relation_count,
        "entityCount": materialized_entity_count,
        "relationCount": materialized_relation_count,
        "traceCount": len(
            [item for item in inference_graph.entities if item.kind == "inference-trace"]
        ),
        "relationTypes": relation_types,
        "nativeTypeDbReasoningUsed": saved_ok and has_materialized_relations,
        "typedbNativeRuleReasoningUsed": saved_ok and has_materialized_relations,
        "nativeTypeDbReasoningCompleted": native_evaluation_completed,
        "typedbNativeRuleEvaluationCompleted": native_evaluation_completed,
        "nativeInferenceOutcome": native_inference_outcome if saved_ok else "failed",
        "nativeInferenceNoMatch": bool(saved_ok and not has_materialized_relations),
        "typedbNativeRuleQueryUsed": bool(native_match_result.get("nativeQueryUsed")),
        "typedbDirectTypeqlQueryUsed": bool(native_match_result.get("nativeQueryUsed")),
        "typedbNativeIndexedRuleQueryUsed": bool(
            native_match_result.get("indexedEvidenceQueryUsed")
        ),
        "typedbNativeRuleQueryStatus": str(native_match_result.get("status") or ""),
        "typedbNativeRuleMatchedCount": int(
            number_or_none(native_match_result.get("matchedCount")) or 0
        ),
        "typedbNativeRuleExecutedCount": int(
            number_or_none(native_match_result.get("executedRuleCount")) or 0
        ),
        "typedbNativeRuleExecutedWorkCount": int(
            number_or_none(native_match_result.get("executedRuleWorkCount")) or 0
        ),
        "typedbNativeRuleSkippedCount": int(
            number_or_none(native_match_result.get("skippedRuleCount")) or 0
        ),
        "modelSignalBridgeExecution": dict(
            native_match_result.get("modelSignalBridgeExecution") or {}
        ),
        "typedbNativeManifestEvidencePreflightEnabled": bool(
            dict(native_match_result.get("executionPlan") or {}).get(
                "manifestEvidencePreflightEnabled"
            )
        ),
        "typedbNativeRelationEvidencePreflightEnabled": bool(
            dict(native_match_result.get("executionPlan") or {}).get(
                "relationEvidencePreflightEnabled"
            )
        ),
        "typedbNativeManifestEvidencePreflightPrunedSymbolCount": int(
            number_or_none(
                dict(native_match_result.get("executionPlan") or {}).get(
                    "manifestEvidencePreflightPrunedSymbolCount"
                )
            )
            or 0
        ),
        "typedbNativeRuleSubjectRuleParallelism": int(
            number_or_none(native_match_result.get("subjectRuleParallelism")) or 1
        ),
        "typedbNativeRuleTotalReadParallelismCap": int(
            number_or_none(native_match_result.get("totalReadParallelismCap")) or 1
        ),
        "typedbNativeRuleEffectiveTotalReadParallelism": int(
            number_or_none(native_match_result.get("effectiveTotalReadParallelism")) or 1
        ),
        "typedbNativeRuleTargetParallelism": int(
            number_or_none(native_match_result.get("nativeRuleTargetParallelism")) or 1
        ),
        "typedbNativeRuleSubjectFanoutUsed": bool(native_match_result.get("subjectFanoutUsed")),
        "typedbNativeRuleSubjectFanoutParallelism": int(
            number_or_none(native_match_result.get("subjectFanoutParallelism")) or 1
        ),
        "typedbNativeRuleSubjectFanoutDurationMs": int(
            number_or_none(native_match_result.get("subjectFanoutDurationMs")) or 0
        ),
        "typedbNativeRuleSubjectFanoutFailureCount": int(
            number_or_none(native_match_result.get("subjectFanoutFailureCount")) or 0
        ),
        "typedbNativeRuleSubjectFanoutSubjects": list(
            native_match_result.get("subjectFanoutSubjects") or []
        )[:8],
        "typedbNativeRuleTargetWorkShardingUsed": bool(
            native_match_result.get("targetWorkShardingUsed")
        ),
        "typedbNativeRuleTargetWorkShardingEnabled": bool(
            native_match_result.get("targetWorkShardingEnabled")
        ),
        "typedbNativeRuleTargetWorkShardingSuppressed": bool(
            native_match_result.get("targetWorkShardingSuppressed")
        ),
        "typedbNativeRuleTargetWorkShardCount": int(
            number_or_none(native_match_result.get("targetWorkShardCount")) or 0
        ),
        "typedbNativeRuleWorkItemCount": int(
            number_or_none(native_match_result.get("targetWorkItemCount")) or 0
        ),
        "typedbNativeRuleAdaptiveTargetShardingEnabled": bool(
            native_match_result.get("targetWorkAdaptiveShardingEnabled")
        ),
        "typedbNativeRuleAdaptiveTargetShardingProfileStatus": str(
            native_match_result.get("targetWorkAdaptiveShardingProfileStatus") or ""
        ),
        "typedbNativeRuleAdaptiveTargetShardingUsed": bool(
            native_match_result.get("targetWorkAdaptiveShardingUsed")
        ),
        "typedbNativeRuleAdaptiveTargetShardedRuleCount": int(
            number_or_none(native_match_result.get("targetWorkAdaptiveShardedRuleCount")) or 0
        ),
        "typedbNativeRuleAdaptiveTargetShardedRuleIds": list(
            native_match_result.get("targetWorkAdaptiveShardedRuleIds") or []
        )[:20],
        "typedbNativeRuleTimeoutFallbackUsed": bool(native_match_result.get("timeoutFallbackUsed")),
        "typedbNativeRuleTimeoutFallbackRuleCount": int(
            number_or_none(native_match_result.get("timeoutFallbackRuleCount")) or 0
        ),
        "typedbNativeRuleTimeoutFallbackShardCount": int(
            number_or_none(native_match_result.get("timeoutFallbackShardCount")) or 0
        ),
        "typedbNativeRuleCommitMode": "single-inferencebox-generation",
        "pythonCompatibilityReasonerUsed": False,
        "typedbNativeFunctionReasoningUsed": False,
        "typeDbFunctionReasoningUsed": False,
        "typedbNativeReasoningReady": native_profile.get("status") in {"ready", "partial"},
        "typedbBootstrapReasoningUsed": False,
        "pythonBootstrapDisabled": True,
        "materializationSource": TYPEDB_NATIVE_MATERIALIZATION_SOURCE,
        "inferenceGenerationId": generation_id,
        "inferenceGenerationAt": generation_at,
        "targetSymbols": target_symbols,
        "ruleTargetSymbols": rule_target_symbols,
        "reasoningSubjectKinds": reasoning_subject_kinds,
        "reasoningSubjectIds": reasoning_subject_ids,
        "incrementalScope": (
            "portfolio-subject"
            if allowed_source_kinds == {"portfolio"}
            else "symbols" if target_symbols else "all-symbols"
        ),
        "readAboxEntityCount": before_entities,
        "readAboxRelationCount": before_relations,
        "clearResult": clear_result,
        "pruneResult": prune_result,
        "saveResult": save_result,
        "nativeMatchResult": {
            key: native_match_result.get(key)
            for key in [
                "status",
                "reason",
                "reasonCode",
                "nativeQueryUsed",
                "indexedEvidenceQueryUsed",
                "executedRuleCount",
                "skippedRuleCount",
                "matchedCount",
                "executedRules",
                "skippedRules",
                "nativeExecutionMode",
                "readTransactionCount",
                "readQueryCount",
                "executionPlan",
                "blockingRule",
                "typedbQueryMetrics",
                "timeoutFallbackUsed",
                "timeoutFallbackRuleCount",
                "timeoutFallbackShardCount",
                "subjectFanoutUsed",
                "subjectFanoutParallelism",
                "subjectFanoutDurationMs",
                "subjectFanoutFailureCount",
                "subjectFanoutSubjects",
                "subjectRuleParallelism",
                "totalReadParallelismCap",
                "effectiveTotalReadParallelism",
                "modelSignalBridgeExecution",
            ]
            if key in native_match_result
        }
        | {
            # The normalized execution trace and result-slot writer need
            # exact subject identity. Keep only the bounded identity rows;
            # condition evidence remains in TypeDB and the InferenceBox.
            "matches": [
                {
                    key: item.get(key)
                    for key in [
                        "ruleId",
                        "sourceId",
                        "sourceLabel",
                        "sourceKind",
                        "worldId",
                        "sourceSymbol",
                        "subjectId",
                        "subjectSymbol",
                    ]
                    if item.get(key) not in (None, "")
                }
                for item in native_match_result.get("matches") or []
                if isinstance(item, dict)
                and str(item.get("ruleId") or "").strip()
                and str(item.get("sourceId") or item.get("subjectId") or "").strip()
            ],
        },
        "typedbQueryMetrics": _store.query_metrics_snapshot(),
        "inferenceBox": inferencebox_payload,
        "nativeReasoningProfile": native_profile,
        "ruleboxMetadata": runtime_rulebox_metadata,
        **runtime_rulebox_metadata,
    }
