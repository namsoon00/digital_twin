"""Shared premises implementation; facade-independent dependencies."""

from __future__ import annotations
from .shared_premises_ports import SharedPremisesPort, PrepareSharedPremisesBindings
from copy import deepcopy
from digital_twin.domain.ontology_change_impact import (
    build_dynamic_inference_preflight,
    build_inference_impact_plan,
    compact_inference_impact_plan,
)
from digital_twin.domain.ontology_performance_contract import (
    ontology_performance_assessment,
)
from digital_twin.domain.ontology_projection_fingerprint import (
    active_material_fingerprint,
)
from digital_twin.domain.ontology_schema import tbox_fingerprint
from digital_twin.domain.ontology_scopes import target_scope_manifest_fingerprint
from digital_twin.domain.ontology_worlds import (
    shared_premise_world,
    world_from_snapshot,
    world_metadata,
)
from digital_twin.domain.portfolio import AccountSnapshot
from digital_twin.domain.world_partitioned_reasoning import (
    WORLD_PARTITIONED_REASONING_VERSION,
    partitioned_phase_impact_plan,
    shared_premise_matches,
    shared_premise_world_graph,
)
from typing import Callable, Dict, List
import time


def prepare_shared_premises(
    _store: SharedPremisesPort,
    snapshot: AccountSnapshot,
    target_symbols: List[str] = None,
    reasoning_context: Dict[str, object] = None,
    progress_callback: Callable[[str, Dict[str, object]], None] = None,
    *,
    _bindings: PrepareSharedPremisesBindings,
) -> Dict[str, object]:
    """Establish an exact SharedPremiseWorld generation for PortfolioWorld.

    A durable full-catalog result-slot proof allows this boundary to run
    only rules affected by the current fact revision plus prior matches.
    Missing or incoherent proof fails closed to one complete native pass.
    """

    if not _store.world_partitioned_reasoning_enabled():
        return {"status": "disabled", "ready": False}

    started_at = time.perf_counter()
    runtime_stages: Dict[str, int] = {}

    def progress(stage: str, **payload) -> None:
        if callable(progress_callback):
            try:
                progress_callback("shared_premise." + stage, dict(payload or {}))
            except Exception:
                return

    progress("rule_catalog.start")
    stage_started = time.perf_counter()
    catalog = _store.ensure_rulebox_ready()
    runtime_stages["ruleCatalogMs"] = int((time.perf_counter() - stage_started) * 1000)
    if str(catalog.get("status") or "") not in {"ready", "seeded"}:
        return {
            "status": "rule-catalog-not-ready",
            "ready": False,
            "retryable": True,
            "reason": str(catalog.get("reason") or "TypeDB RuleBox is unavailable.")[
                :220
            ],
        }
    stage_started = time.perf_counter()
    partition = _store.world_rule_partition(catalog)
    runtime_stages["worldRulePartitionMs"] = int(
        (time.perf_counter() - stage_started) * 1000
    )
    if str(partition.get("status") or "") != "ready":
        return {
            "status": "invalid-world-rule-partition",
            "ready": False,
            "retryable": False,
            "failures": list(partition.get("failures") or [])[:40],
            "reason": "RuleBox contains conditions without an auditable world owner.",
        }
    shared_rules = list(partition.get("sharedRules") or [])
    shared_rule_ids = list(partition.get("sharedRuleIds") or [])
    shared_rule_catalog = _store.catalog_for_rules(catalog, shared_rules)
    shared_rulebox_hash = str(shared_rule_catalog.get("compiledRuleboxRulesHash") or "")
    symbols = _store.inference_symbols(snapshot, target_symbols)
    requested_symbols = sorted(
        {
            str(symbol or "").upper().strip()
            for symbol in target_symbols or symbols
            if str(symbol or "").strip()
        }
    )
    not_evaluated_symbols = sorted(set(requested_symbols) - set(symbols))
    if requested_symbols and not symbols:
        return {
            "status": "shared-premise-target-not-in-source-snapshot",
            "ready": False,
            "retryable": False,
            "requestedSymbols": requested_symbols,
            "evaluatedSymbols": [],
            "notEvaluatedSymbols": requested_symbols,
            "targetCoverageComplete": False,
            "reason": (
                "The immutable source snapshot does not contain any requested "
                "SharedPremiseWorld subject."
            ),
        }
    portfolio_context = world_from_snapshot(snapshot, _store.settings)
    shared_world = shared_premise_world(
        portfolio_context.market_id,
        _store.settings.get("ontologySharedMarketTenantId") or "shared",
    )
    if _store._frozen_tbox_metadata is not None:
        active_tbox = dict(_store._frozen_tbox_metadata)
        runtime_stages["preflightTboxReadMs"] = 0
    else:
        stage_started = time.perf_counter()
        active_tbox = _store.active_tbox_context()
        runtime_stages["preflightTboxReadMs"] = int(
            (time.perf_counter() - stage_started) * 1000
        )
    shared_tbox_fingerprint = str(
        (active_tbox or {}).get("fingerprint") or tbox_fingerprint()
    )
    stage_started = time.perf_counter()
    try:
        prior_active_abox = _store.repository_world_call(
            "active_abox_metadata",
            world_id=shared_world.world_id,
        )
    except Exception:
        prior_active_abox = {}
    runtime_stages["priorAboxMetadataMs"] = int(
        (time.perf_counter() - stage_started) * 1000
    )
    namespace = _store.execution_namespace()
    selection_context = {
        "reusable": False,
        "proofSource": "",
        "matchedRuleIds": [],
        "ruleStatesBySymbol": {},
        "revisionVectorsBySymbol": {},
        "reason": "shared-premise-result-slot-store-unavailable",
    }
    slot_reader = (
        getattr(
            _store.projection_run_store,
            "active_rule_result_slot_context",
            None,
        )
        if _store.projection_run_store
        else None
    )
    stage_started = time.perf_counter()
    if callable(slot_reader):
        try:
            selection_context = dict(
                slot_reader(
                    world_id=shared_world.world_id,
                    account_id="",
                    symbols=symbols,
                    rulebox_rules_hash=shared_rulebox_hash,
                    tbox_fingerprint=shared_tbox_fingerprint,
                    expected_rule_count=len(shared_rule_ids),
                    execution_namespace_id=str(
                        namespace.get("executionNamespaceId") or ""
                    ),
                    engine_deployment_id=str(namespace.get("engineDeploymentId") or ""),
                    graph_database=str(namespace.get("graphDatabase") or ""),
                    catalog_rule_ids=shared_rule_ids,
                )
                or {}
            )
        except Exception as error:
            selection_context = {
                "reusable": False,
                "proofSource": "typedb-rule-result-slots",
                "matchedRuleIds": [],
                "ruleStatesBySymbol": {},
                "revisionVectorsBySymbol": {},
                "reason": "shared-premise-result-slot-read-failed",
                "detail": str(error)[:180],
            }
    runtime_stages["resultSlotReadMs"] = int(
        (time.perf_counter() - stage_started) * 1000
    )
    stage_started = time.perf_counter()
    dynamic_preflight = build_dynamic_inference_preflight(
        rules=shared_rules,
        target_symbols=symbols,
        requested_fact_families=(
            (reasoning_context or {}).get("requestedScopeFamilies") or []
        ),
        requested_fact_families_by_symbol=(
            (reasoning_context or {}).get("requestedScopeFamiliesBySymbol") or {}
        ),
        requested_dependency_keys=(
            (reasoning_context or {}).get("requestedDependencyKeys") or []
        ),
        requested_dependency_keys_by_symbol=(
            (reasoning_context or {}).get("requestedDependencyKeysBySymbol") or {}
        ),
        event_fact_boundary_authoritative=bool(
            (reasoning_context or {}).get("eventFactBoundaryAuthoritative")
        ),
        event_dependency_boundary_authoritative=bool(
            (reasoning_context or {}).get("eventDependencyBoundaryAuthoritative")
        ),
        revision_vectors_by_symbol=(
            (reasoning_context or {}).get("revisionVectorsBySymbol") or {}
        ),
        prior_revision_vectors_by_symbol=(
            selection_context.get("revisionVectorsBySymbol") or {}
        ),
        prior_result_slots_reusable=bool(
            selection_context.get("reusable")
            and selection_context.get("coverageComplete", True)
            and selection_context.get("fullGenerationReusable", True)
        ),
    )
    runtime_stages["dynamicPreflightMs"] = int(
        (time.perf_counter() - stage_started) * 1000
    )
    evaluation_plan = _bindings.shared_premise_evaluation_plan(
        selection_context,
        dynamic_preflight,
        len(shared_rule_ids),
    )
    dynamic_preflight = {
        **dynamic_preflight,
        "evaluationPlan": evaluation_plan,
    }
    progress(
        "preflight.done",
        route=str(dynamic_preflight.get("route") or ""),
        candidateRuleCount=int(evaluation_plan["plannedRuleCount"]),
        directChangeCandidateRuleCount=int(
            evaluation_plan["directChangeCandidateRuleCount"]
        ),
        evaluationMode=str(evaluation_plan["mode"]),
        resultSlotCoverageComplete=bool(evaluation_plan["resultSlotCoverageComplete"]),
        coldTargetSymbolCount=int(evaluation_plan["coldTargetSymbolCount"]),
        coldTargetSymbols=list(evaluation_plan["coldTargetSymbols"]),
        runtimeMs=runtime_stages["dynamicPreflightMs"],
    )
    if bool(dynamic_preflight.get("sharedReuseEligible")):
        stage_started = time.perf_counter()
        reused_inference, reuse_mode = _store.compact_shared_inference_reuse(
            prior_active_abox,
            selection_context,
            symbols,
            shared_world.world_id,
        )
        runtime_stages["preflightReuseReadMs"] = int(
            (time.perf_counter() - stage_started) * 1000
        )
        reused_result = _store.reused_shared_premise_result(
            inference=reused_inference,
            active_abox=prior_active_abox,
            selection_context=selection_context,
            shared_world=shared_world,
            shared_rule_ids=shared_rule_ids,
            overlay_rule_ids=list(partition.get("overlayRuleIds") or []),
            shared_rulebox_hash=shared_rulebox_hash,
            shared_tbox_fingerprint=shared_tbox_fingerprint,
            requested_symbols=requested_symbols,
            evaluated_symbols=symbols,
            not_evaluated_symbols=not_evaluated_symbols,
            catalog=catalog,
            preflight=dynamic_preflight,
            runtime_stages=runtime_stages,
            started_at=started_at,
            reuse_mode=reuse_mode,
        )
        if reused_result:
            progress(
                "done",
                matchedPremiseCount=sum(
                    len(values)
                    for values in reused_result.get("premisesBySymbol", {}).values()
                ),
                totalMs=runtime_stages.get("totalMs", 0),
                preflightReused=True,
            )
            return reused_result
        dynamic_preflight = {
            **dynamic_preflight,
            "route": "FULL_SAFE",
            "sharedReuseEligible": False,
            "sharedWorkRequired": True,
            "reuseFallbackReason": str(
                reused_inference.get("reason")
                or reused_inference.get("status")
                or "active-shared-generation-not-aligned"
            )[:180],
            "reasonCodes": [
                *list(dynamic_preflight.get("reasonCodes") or []),
                "preflight-reuse-proof-not-aligned",
            ],
        }
    progress("graph.start", sharedRuleCount=int(partition.get("sharedRuleCount") or 0))
    stage_started = time.perf_counter()
    graph, _persistence_graph, assembly = _store.build_graph_assembly(
        snapshot,
        shared_rule_catalog,
        target_symbols=target_symbols,
        target_scoped_input=bool(target_symbols),
    )
    runtime_stages["graphAssemblyMs"] = int(
        (time.perf_counter() - stage_started) * 1000
    )
    graph.worldview.update(
        {
            **world_metadata(portfolio_context),
            "sharedPremiseWorldId": shared_world.world_id,
            "asOf": str(snapshot.generated_at or ""),
        }
    )
    update = shared_premise_world_graph(
        graph,
        shared_rules,
        shared_world,
    )
    # Shared-world projection assigns its own worldview metadata. Rebind
    # the immutable deployment TBox after that transformation as well, so
    # the persisted generation and rule-result slots keep the same release
    # identity that preflight used for selection.
    update.worldview["activeTBox"] = deepcopy(active_tbox)
    if target_symbols:
        update.worldview["targetScopedManifestPatch"] = {
            "status": "applied",
            "mode": "shared-premise-target-scoped-input",
            "targetSymbols": symbols,
        }
    progress("projection.start", worldId=shared_world.world_id)
    stage_started = time.perf_counter()
    projection = _store.project_shared_world_update(
        update, shared_world, projection_kind="premise"
    )
    runtime_stages["projectionMs"] = int((time.perf_counter() - stage_started) * 1000)
    projection_status = str(projection.get("status") or "")
    if projection_status not in {
        "ok",
        "staged-scoped-manifest",
        "unchanged-material-facts",
        "already-projected-material",
    }:
        return {
            "status": "shared-premise-projection-failed",
            "ready": False,
            "retryable": bool(projection.get("retryable", True)),
            "recommendedRetryAfterSeconds": int(
                projection.get("recommendedRetryAfterSeconds") or 10
            ),
            "projection": projection,
            "reasonCode": str(
                projection.get("reasonCode") or "shared-premise-projection-failed"
            )[:96],
            "failureStage": str(
                projection.get("failureStage") or "shared-premise-projection"
            )[:96],
            "reason": str(projection.get("reason") or projection_status)[:220],
        }
    stage_started = time.perf_counter()
    if projection_status == "staged-scoped-manifest":
        staged_save = (
            dict(projection.get("save") or {})
            if isinstance(projection.get("save"), dict)
            else {}
        )
        active_abox = {
            "status": "ok",
            "worldId": shared_world.world_id,
            "aboxSnapshotId": str(
                projection.get("worldviewManifestId")
                or staged_save.get("aboxSnapshotId")
                or (update.worldview or {}).get("worldviewManifestId")
                or ""
            ),
            "worldviewManifestId": str(
                projection.get("worldviewManifestId")
                or staged_save.get("worldviewManifestId")
                or (update.worldview or {}).get("worldviewManifestId")
                or ""
            ),
            "scopePlan": list(
                staged_save.get("scopePlan")
                or (update.worldview or {}).get("scopePlan")
                or []
            ),
            "materialFingerprint": str(
                projection.get("materialFingerprint")
                or (update.worldview or {}).get("materialFingerprint")
                or ""
            ),
            "candidateState": "staged-native-inference",
        }
    else:
        try:
            active_abox = _store.repository_world_call(
                "active_abox_metadata",
                world_id=shared_world.world_id,
            )
        except Exception as error:
            return {
                "status": "shared-market-metadata-failed",
                "ready": False,
                "retryable": True,
                "reason": str(error)[:220],
            }
    runtime_stages["activeAboxMetadataMs"] = int(
        (time.perf_counter() - stage_started) * 1000
    )
    projected_shared_tbox_fingerprint = str(
        ((update.worldview or {}).get("activeTBox") or {}).get("fingerprint")
        or tbox_fingerprint()
    )
    if projected_shared_tbox_fingerprint != shared_tbox_fingerprint:
        selection_context = {
            "reusable": False,
            "proofSource": "typedb-rule-result-slots",
            "matchedRuleIds": [],
            "ruleStatesBySymbol": {},
            "revisionVectorsBySymbol": {},
            "reason": "preflight-projected-tbox-fingerprint-mismatch",
        }
        dynamic_preflight = {
            **dynamic_preflight,
            "route": "FULL_SAFE",
            "sharedReuseEligible": False,
            "sharedWorkRequired": True,
            "reasonCodes": [
                *list(dynamic_preflight.get("reasonCodes") or []),
                "projected-tbox-fingerprint-changed",
            ],
        }
    shared_tbox_fingerprint = projected_shared_tbox_fingerprint
    stage_started = time.perf_counter()
    impact_prior_abox = dict(prior_active_abox or {})
    if projection_status == "staged-scoped-manifest":
        staged_save = (
            dict(projection.get("save") or {})
            if isinstance(projection.get("save"), dict)
            else {}
        )
        staged_verification = (
            dict(staged_save.get("aboxPersistenceVerification") or {})
            if isinstance(staged_save.get("aboxPersistenceVerification"), dict)
            else {}
        )
        verified_predecessor = (
            dict(staged_verification.get("activePointer") or {})
            if isinstance(staged_verification.get("activePointer"), dict)
            else {}
        )
        if str(verified_predecessor.get("status") or "") == "ok":
            impact_prior_abox = verified_predecessor
    source_impact_plan = build_inference_impact_plan(
        list((impact_prior_abox or {}).get("scopePlan") or []),
        list(
            (active_abox or {}).get("scopePlan")
            or (update.worldview or {}).get("scopePlan")
            or []
        ),
        _store.snapshot_symbols(snapshot),
        explicit_target_symbols=symbols,
        rules=_store.rulebox_rules_for_impact(),
        requested_fact_families=(reasoning_context or {}).get("requestedScopeFamilies")
        or [],
        requested_fact_families_by_symbol=(reasoning_context or {}).get(
            "requestedScopeFamiliesBySymbol"
        )
        or {},
        requested_dependency_keys=(reasoning_context or {}).get(
            "requestedDependencyKeys"
        )
        or [],
        requested_dependency_keys_by_symbol=(reasoning_context or {}).get(
            "requestedDependencyKeysBySymbol"
        )
        or {},
        dependency_boundary_authoritative=bool(
            (reasoning_context or {}).get("eventDependencyBoundaryAuthoritative")
        ),
    )
    shared_impact_plan = compact_inference_impact_plan(
        partitioned_phase_impact_plan(
            source_impact_plan,
            partition,
            "shared-premise",
        )
    )
    missing_slot_rule_ids = {
        str(rule_id or "").strip()
        for rule_id in selection_context.get("candidateRuleIds") or []
        if str(rule_id or "").strip()
    }
    if bool(selection_context.get("reusable")) and missing_slot_rule_ids:
        enabled_ids = [
            str(rule_id or "").strip()
            for rule_id in shared_rule_ids
            if str(rule_id or "").strip()
        ]
        candidates = {
            str(rule_id or "").strip()
            for rule_id in shared_impact_plan.get("candidateRuleIds") or []
            if str(rule_id or "").strip()
        } | missing_slot_rule_ids
        candidates.intersection_update(enabled_ids)
        shared_impact_plan.update(
            {
                "candidateRuleIds": [
                    rule_id for rule_id in enabled_ids if rule_id in candidates
                ],
                "deferredRuleIds": [
                    rule_id for rule_id in enabled_ids if rule_id not in candidates
                ],
                "candidateRuleCount": len(candidates),
                "enabledRuleCount": len(enabled_ids),
                "nativeRuleSelectionEligible": True,
                "nativeRuleSelectionEligibilityReason": (
                    "partial-result-slot-catalog-reconciliation"
                ),
                "partialResultSlotReconciliation": {
                    "enabled": True,
                    "missingRuleCount": len(missing_slot_rule_ids),
                    "incompleteSymbols": list(
                        selection_context.get("incompleteSymbols") or []
                    ),
                },
            }
        )
    runtime_stages["impactPlanningMs"] = int(
        (time.perf_counter() - stage_started) * 1000
    )
    target_scope_proof = target_scope_manifest_fingerprint(
        list(
            (active_abox or {}).get("scopePlan")
            or (update.worldview or {}).get("scopePlan")
            or []
        ),
        symbols,
    )
    scope_plan_fingerprint = str(target_scope_proof.get("fingerprint") or "")
    selection_enabled = bool(
        shared_impact_plan.get("nativeRuleSelectionEligible")
        and selection_context.get("reusable")
    )

    existing = {}
    existing_reuse_mode = ""
    runtime_stages["existingInferenceMetadataMs"] = 0
    runtime_stages["existingInferenceReadMs"] = 0
    if projection_status == "staged-scoped-manifest":
        existing = {
            "status": "skipped-new-shared-premise-candidate",
            "reason": (
                "A predecessor InferenceBox cannot prove reuse for a newly "
                "staged ABox Manifest."
            ),
        }
    else:
        stage_started = time.perf_counter()
        existing, existing_reuse_mode = _store.compact_shared_inference_reuse(
            active_abox,
            selection_context,
            symbols,
            shared_world.world_id,
        )
        runtime_stages["existingInferenceMetadataMs"] = int(
            (time.perf_counter() - stage_started) * 1000
        )
    existing_reusable = bool(
        projection_status != "staged-scoped-manifest"
        and _store.inference_result_is_reusable(existing, active_abox, symbols)
        and str(existing.get("ruleExecutionPhase") or "") == "shared-premise"
        and str(existing.get("worldPartitionedReasoningVersion") or "")
        == WORLD_PARTITIONED_REASONING_VERSION
    )
    execution = {}
    if existing_reusable:
        inference = dict(existing)
        execution_status = "reused-shared-premise-generation"
        execution = {**dict(existing), "status": "ok"}
    else:
        progress("inference.start", targetSymbolCount=len(symbols))
        stage_started = time.perf_counter()
        rulebox_payload = {
            "worldId": shared_world.world_id,
            "worldType": str(shared_world.world_type or "MarketWorld"),
            "tenantId": str(shared_world.tenant_id or "shared"),
            "accountId": "",
            "symbols": symbols,
            "ruleExecutionPhase": "shared-premise",
            "worldPartitionedReasoningVersion": WORLD_PARTITIONED_REASONING_VERSION,
            "inferenceImpactPlan": shared_impact_plan,
            "typedbNativeRuleSelectionEnabled": "1" if selection_enabled else "0",
            "priorInferenceReusable": selection_enabled,
            "priorMatchedRuleIds": (
                list(selection_context.get("matchedRuleIds") or [])
                if selection_enabled
                else []
            ),
            "priorInferenceProofSource": str(
                selection_context.get("proofSource") or ""
            ),
            "pruneOldGenerations": False,
            "inferenceSnapshotLimit": _store.inference_snapshot_limit(),
            "nativeRulePlannerTopology": dict(
                (update.worldview or {}).get("nativeRulePlannerTopology") or {}
            ),
            "_nativePreflightProjectionGraph": update,
            "_nativePreflightProjectionManifestId": str(
                (active_abox or {}).get("aboxSnapshotId")
                or (update.worldview or {}).get("worldviewManifestId")
                or ""
            ),
            "expectedAboxSnapshotId": str(
                (active_abox or {}).get("aboxSnapshotId") or ""
            ),
        }
        staged_runner = getattr(
            _store.repository,
            "run_rulebox_for_staged_abox",
            None,
        )
        if callable(staged_runner):
            execution = staged_runner(rulebox_payload)
        elif projection_status == "staged-scoped-manifest":
            execution = {
                "status": "staged-abox-runner-unavailable",
                "retryable": True,
                "recommendedRetryAfterSeconds": 10,
                "reason": (
                    "The graph adapter cannot atomically activate, infer, and "
                    "finalize a staged SharedPremiseWorld candidate."
                ),
            }
        else:
            rulebox_payload.pop("expectedAboxSnapshotId", None)
            execution = _store.repository.run_rulebox(rulebox_payload)
        runtime_stages["nativeInferenceMs"] = int(
            (time.perf_counter() - stage_started) * 1000
        )
        execution_status = str((execution or {}).get("status") or "error")
        inference = dict((execution or {}).get("inferenceBox") or {})
    source_abox_snapshot_id = str(inference.get("sourceAboxSnapshotId") or "").strip()
    active_abox_snapshot_id = str(
        (active_abox or {}).get("aboxSnapshotId") or ""
    ).strip()
    complete = bool(
        execution_status in {"ok", "reused-shared-premise-generation"}
        and (
            inference.get("nativeTypeDbReasoningCompleted")
            or inference.get("typedbNativeRuleEvaluationCompleted")
        )
        and inference.get("generationAligned") is not False
        and source_abox_snapshot_id
        and active_abox_snapshot_id
        and source_abox_snapshot_id == active_abox_snapshot_id
        and str(inference.get("ruleExecutionPhase") or "") == "shared-premise"
        and str(inference.get("worldPartitionedReasoningVersion") or "")
        == WORLD_PARTITIONED_REASONING_VERSION
    )
    if not complete:
        return {
            "status": "shared-premise-inference-incomplete",
            "ready": False,
            "retryable": True,
            "recommendedRetryAfterSeconds": 10,
            "projection": projection,
            "executionStatus": execution_status,
            "inferenceStatus": str(inference.get("status") or ""),
            "generationVector": {
                "activeAboxSnapshotId": active_abox_snapshot_id,
                "sourceAboxSnapshotId": source_abox_snapshot_id,
                "inferenceGenerationId": str(
                    inference.get("inferenceGenerationId") or ""
                ),
                "ruleExecutionPhase": str(inference.get("ruleExecutionPhase") or ""),
                "worldPartitionedReasoningVersion": str(
                    inference.get("worldPartitionedReasoningVersion") or ""
                ),
            },
            "activationLifecycle": _bindings.compact_staged_abox_activation_lifecycle(
                execution
            ),
            "requestedSymbols": requested_symbols,
            "evaluatedSymbols": symbols,
            "notEvaluatedSymbols": not_evaluated_symbols,
            "targetCoverageComplete": not not_evaluated_symbols,
            "reason": "SharedPremiseWorld TypeDB generation did not complete.",
        }
    result_slot_write = {
        "status": "result-slot-store-unavailable",
        "saved": False,
    }
    slot_writer = (
        getattr(
            _store.projection_run_store,
            "record_rule_result_slots",
            None,
        )
        if _store.projection_run_store
        else None
    )
    stage_started = time.perf_counter()
    if existing_reusable and bool(existing.get("resultSlotProofReused")):
        result_slot_write = {
            "status": "reused-existing-result-slots",
            "saved": True,
            "reused": True,
            "worldId": shared_world.world_id,
            "symbolCount": len(symbols),
            "catalogRuleCount": len(shared_rule_ids),
            "slotCount": len(symbols) * len(shared_rule_ids),
        }
    elif callable(slot_writer):
        try:
            result_slot_write = dict(
                slot_writer(
                    world_id=shared_world.world_id,
                    account_id="",
                    symbols=symbols,
                    catalog_rule_ids=shared_rule_ids,
                    rulebox_rules_hash=shared_rulebox_hash,
                    tbox_fingerprint=shared_tbox_fingerprint,
                    scope_plan_fingerprint=scope_plan_fingerprint,
                    source_abox_snapshot_id=str(
                        inference.get("sourceAboxSnapshotId") or ""
                    ),
                    source_snapshot_fingerprint=str(
                        projection.get("materialFingerprint")
                        or active_material_fingerprint(active_abox)
                        or ""
                    ),
                    execution=execution,
                    inference=inference,
                    execution_namespace_id=str(
                        namespace.get("executionNamespaceId") or ""
                    ),
                    engine_deployment_id=str(namespace.get("engineDeploymentId") or ""),
                    graph_database=str(namespace.get("graphDatabase") or ""),
                    release_fingerprint=str(namespace.get("releaseFingerprint") or ""),
                    validation_cohort_id=str(namespace.get("validationCohortId") or ""),
                    prior_rule_states_by_symbol=(
                        dict(selection_context.get("ruleStatesBySymbol") or {})
                        if bool(execution.get("nativeRuleSelectionApplied"))
                        else {}
                    ),
                    revision_vectors_by_symbol=dict(
                        (reasoning_context or {}).get("revisionVectorsBySymbol") or {}
                    ),
                    source_run_id="shared-premise:"
                    + str(inference.get("inferenceGenerationId") or ""),
                    tbox_version=str(
                        ((update.worldview or {}).get("activeTBox") or {}).get(
                            "version"
                        )
                        or ""
                    ),
                )
                or {}
            )
        except Exception as error:
            result_slot_write = {
                "status": "result-slot-write-failed",
                "saved": False,
                "reason": str(error)[:180],
            }
    runtime_stages["resultSlotWriteMs"] = int(
        (time.perf_counter() - stage_started) * 1000
    )
    premises = shared_premise_matches(inference)
    symbol_rows = {}
    generation_id = str(inference.get("inferenceGenerationId") or "")
    for symbol in symbols:
        symbol_rows[symbol] = {
            "snapshotId": generation_id,
            "relations": [
                dict(row)
                for row in inference.get("relations") or []
                if isinstance(row, dict)
                and str(row.get("symbol") or "").upper().strip() == symbol
            ],
            "traces": [
                dict(row)
                for row in inference.get("traces") or []
                if isinstance(row, dict)
                and str(row.get("symbol") or "").upper().strip() == symbol
            ],
        }
    runtime_stages["totalMs"] = int((time.perf_counter() - started_at) * 1000)
    performance_assessment = ontology_performance_assessment(
        runtime_stages,
        (
            _store.settings.get("ontologyPerformanceBudgetsMs")
            if isinstance(_store.settings.get("ontologyPerformanceBudgetsMs"), dict)
            else None
        ),
    )
    actual_evaluation_mode = (
        "incremental-dependency-selection"
        if bool(execution.get("nativeRuleSelectionApplied"))
        else (
            "full-catalog-cold-target-warmup"
            if str(evaluation_plan.get("mode") or "").startswith("full-catalog")
            else "full-catalog-safe-fallback"
        )
    )
    progress(
        "done",
        matchedPremiseCount=sum(len(values) for values in premises.values()),
        totalMs=runtime_stages["totalMs"],
        evaluationMode=actual_evaluation_mode,
        executedRuleCount=int(execution.get("nativeRuleSelectionExecutedCount") or 0),
        fullRuleCount=int(execution.get("nativeRuleSelectionFullRuleCount") or 0),
        selectionApplied=bool(execution.get("nativeRuleSelectionApplied")),
    )
    model_signal_bridge_execution = (
        dict(execution.get("modelSignalBridgeExecution") or {})
        if isinstance(execution.get("modelSignalBridgeExecution"), dict)
        else (
            dict(
                (execution.get("nativeMatchResult") or {}).get(
                    "modelSignalBridgeExecution"
                )
                or {}
            )
            if isinstance(execution.get("nativeMatchResult"), dict)
            else {}
        )
    )
    return {
        "contractVersion": WORLD_PARTITIONED_REASONING_VERSION,
        "status": "ready",
        "ready": True,
        "worldId": shared_world.world_id,
        "projectionStatus": projection_status,
        "executionStatus": execution_status,
        "premisesBySymbol": premises,
        "sharedRuleIds": shared_rule_ids,
        "overlayRuleIds": list(partition.get("overlayRuleIds") or []),
        "inferenceGenerationId": generation_id,
        "sourceAboxSnapshotId": str(inference.get("sourceAboxSnapshotId") or ""),
        "relations": list(inference.get("relations") or [])[:480],
        "traces": list(inference.get("traces") or [])[:480],
        "symbols": symbol_rows,
        "requestedSymbols": requested_symbols,
        "evaluatedSymbols": symbols,
        "notEvaluatedSymbols": not_evaluated_symbols,
        "targetCoverageComplete": not not_evaluated_symbols,
        "dynamicInferencePreflight": dynamic_preflight,
        "inferenceImpactPlan": shared_impact_plan,
        "ruleSelectionProof": {
            "reusable": bool(selection_context.get("reusable")),
            "proofSource": str(selection_context.get("proofSource") or ""),
            "reason": str(
                selection_context.get("reason")
                or selection_context.get("fallbackReason")
                or ""
            ),
            "selectionRequested": selection_enabled,
            "selectionApplied": bool(execution.get("nativeRuleSelectionApplied")),
            "plannedEvaluationMode": str(evaluation_plan.get("mode") or ""),
            "actualEvaluationMode": actual_evaluation_mode,
            "plannedRuleCount": int(evaluation_plan.get("plannedRuleCount") or 0),
            "directChangeCandidateRuleCount": int(
                evaluation_plan.get("directChangeCandidateRuleCount") or 0
            ),
            "generationReused": existing_reusable,
            "reuseMode": existing_reuse_mode,
            "candidateRuleCount": int(
                execution.get("nativeRuleSelectionCandidateCount") or 0
            ),
            "executedRuleCount": int(
                execution.get("nativeRuleSelectionExecutedCount") or 0
            ),
            "deferredRuleCount": int(
                execution.get("nativeRuleSelectionDeferredCount") or 0
            ),
            "fullRuleCount": int(
                execution.get("nativeRuleSelectionFullRuleCount") or 0
            ),
            "coverageComplete": bool(selection_context.get("coverageComplete", True)),
            "partialCatalogProof": bool(selection_context.get("partialCatalogProof")),
            "incompleteSymbols": list(selection_context.get("incompleteSymbols") or []),
            "coldTargetSymbols": list(selection_context.get("coldTargetSymbols") or []),
        },
        "resultSlotWrite": result_slot_write,
        "existingInferenceReuseMode": existing_reuse_mode,
        "generationVector": {
            "worldId": shared_world.world_id,
            "sourceAboxSnapshotId": str(inference.get("sourceAboxSnapshotId") or ""),
            "inferenceGenerationId": generation_id,
            "ruleboxRulesHash": shared_rulebox_hash,
            "tboxFingerprint": shared_tbox_fingerprint,
            "scopePlanFingerprint": scope_plan_fingerprint,
            "targetScopeCount": int(target_scope_proof.get("scopeCount") or 0),
        },
        "runtimeStages": runtime_stages,
        "performanceAssessment": performance_assessment,
        "releaseCatalog": {
            "source": str(
                catalog.get("runtimeCatalogSource")
                or catalog.get("ruleCatalogStore")
                or "typedb-runtime"
            ),
            "ruleCount": int(catalog.get("ruleCount") or 0),
            "ruleboxRulesHash": str(catalog.get("ruleboxRulesHash") or ""),
            "ruleboxReused": bool(catalog.get("releaseCatalogReused")),
            "tboxSource": (
                "frozen-v2-release"
                if _store._frozen_tbox_metadata is not None
                else "typedb-runtime"
            ),
        },
        "modelSignalBridgeExecution": model_signal_bridge_execution,
        "activationLifecycle": _bindings.compact_staged_abox_activation_lifecycle(
            execution
        ),
        "assembly": {
            "inputMode": str(assembly.get("inputMode") or ""),
            "targetSymbols": list(assembly.get("targetSymbols") or []),
        },
    }
