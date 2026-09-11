"""Reuse implementation; facade-independent dependencies."""

from __future__ import annotations
from .reuse_ports import (
    ReusePort,
    ExecutionNamespaceBindings,
    CompactSharedInferenceReuseBindings,
)
from digital_twin.domain.ontology_performance_contract import (
    ontology_performance_assessment,
)
from digital_twin.domain.ontology_projection_audit import (
    INFERENCE_REUSE_PROOF_VERSION,
    OntologyProjectionRun,
    inference_reuse_scope_plan,
    inference_reuse_scope_plan_fingerprint,
)
from digital_twin.domain.ontology_runtime_operations import native_replay_validation
from digital_twin.domain.ontology_scopes import target_scope_manifest_fingerprint
from digital_twin.domain.portfolio import AccountSnapshot
from digital_twin.domain.world_partitioned_reasoning import (
    WORLD_PARTITIONED_REASONING_VERSION,
    shared_premise_matches,
)
from digital_twin.infrastructure.runtime_identity import runtime_identity
from typing import Dict, List
import hashlib
import time


def execution_namespace(
    _store: ReusePort, *, _bindings: ExecutionNamespaceBindings
) -> Dict[str, str]:
    """Return the compatibility boundary for reusable native rule slots.

    A deployment commit can change queueing, presentation, or collection
    code without changing one TypeDB rule result. Rule slots are already
    guarded by the exact RuleBox hash and TBox fingerprint, so binding the
    namespace to the whole release fingerprint forced an unnecessary full
    catalogue bootstrap after every deploy. The native engine contract is
    the remaining executable-code boundary and must be bumped whenever
    TypeQL generation/evaluation semantics change.
    """
    from digital_twin.infrastructure.typedb_ontology import (
        TYPEDB_NATIVE_RULE_ENGINE_VERSION,
    )

    deployment_id = str(
        _store.settings.get("_reasoningEngineDeploymentId")
        or _store.settings.get("reasoningEngineActiveDeploymentId")
        or "ontology-v1-active"
    ).strip()
    graph_database = str(
        _store.settings.get("typedbDatabase") or "orbit_alpha_ontology"
    ).strip()
    release_fingerprint = str(
        _store.settings.get("_reasoningEngineReleaseFingerprint")
        or runtime_identity().get("revision")
        or "local-runtime"
    ).strip()
    validation_cohort_id = str(
        _store.settings.get("_reasoningEngineValidationCohortId") or ""
    ).strip()
    native_rule_engine_version = str(
        _store.settings.get("_reasoningEngineNativeRuleEngineVersion")
        or _store.settings.get("typedbNativeRuleEngineVersion")
        or TYPEDB_NATIVE_RULE_ENGINE_VERSION
    ).strip()
    material = "|".join(
        [
            _bindings.RULE_EVALUATION_NAMESPACE_VERSION,
            deployment_id,
            graph_database,
            native_rule_engine_version,
        ]
    )
    return {
        "executionNamespaceId": "projection-namespace:"
        + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32],
        "engineDeploymentId": deployment_id,
        "graphDatabase": graph_database,
        "releaseFingerprint": release_fingerprint,
        "validationCohortId": validation_cohort_id,
        "nativeRuleEngineVersion": native_rule_engine_version,
        "namespaceVersion": _bindings.RULE_EVALUATION_NAMESPACE_VERSION,
    }


def compact_shared_inference_reuse(
    _store: ReusePort,
    active_abox: Dict[str, object],
    selection_context: Dict[str, object],
    symbols: List[str],
    world_id: str,
    *,
    _bindings: CompactSharedInferenceReuseBindings,
) -> tuple:
    """Reuse shared inference only through the compact result-slot proof.

    The detailed InferenceBox can be large and belongs to a physical graph
    database shared by successive engine releases.  Reading it merely to
    discover that its execution namespace is incompatible made the first
    request after a release substantially slower than a fresh native pass.
    Result slots already bind reuse to the RuleBox, TBox, deployment and
    native-engine namespace, so a missing slot proof must fall through to
    new inference without expanding the previous generation.
    """
    recovery_metadata = {}
    try:
        recovery_metadata = _store.repository_world_call(
            "inferencebox_recovery_metadata",
            world_id=world_id,
        )
    except Exception:
        recovery_metadata = {}
    existing = _bindings.shared_inference_from_result_slot_proof(
        world_id=world_id,
        active_abox=active_abox,
        recovery_metadata=recovery_metadata,
        selection_context=selection_context,
        symbols=symbols,
    )
    if existing:
        return existing, "typedb-result-slot-generation"
    return {
        "status": "skipped-missing-compact-result-slot-proof",
        "reason": (
            "The prior SharedPremiseWorld generation has no compatible "
            "compact result-slot proof. Native TypeDB inference will run "
            "without expanding the detailed predecessor InferenceBox."
        ),
    }, "compact-result-slot-proof-unavailable"


def reused_shared_premise_result(
    _store: ReusePort,
    *,
    inference: Dict[str, object],
    active_abox: Dict[str, object],
    selection_context: Dict[str, object],
    shared_world,
    shared_rule_ids: List[str],
    overlay_rule_ids: List[str],
    shared_rulebox_hash: str,
    shared_tbox_fingerprint: str,
    requested_symbols: List[str],
    evaluated_symbols: List[str],
    not_evaluated_symbols: List[str],
    catalog: Dict[str, object],
    preflight: Dict[str, object],
    runtime_stages: Dict[str, int],
    started_at: float,
    reuse_mode: str,
) -> Dict[str, object]:
    """Return one exact prior SharedPremiseWorld generation.

    The caller may use this before graph assembly only after the durable
    result-slot ledger, active ABox pointer, and published InferenceBox
    generation have all aligned. No Python investment rule is evaluated
    here; matched states are rehydrated from the TypeDB-authored ledger.
    """

    if not (
        _store.inference_result_is_reusable(
            inference,
            active_abox,
            evaluated_symbols,
        )
        and str(inference.get("ruleExecutionPhase") or "") == "shared-premise"
        and str(inference.get("worldPartitionedReasoningVersion") or "")
        == WORLD_PARTITIONED_REASONING_VERSION
    ):
        return {}
    premises = shared_premise_matches(inference)
    generation_id = str(inference.get("inferenceGenerationId") or "")
    source_abox_snapshot_id = str(inference.get("sourceAboxSnapshotId") or "")
    target_scope_proof = target_scope_manifest_fingerprint(
        list((active_abox or {}).get("scopePlan") or []),
        evaluated_symbols,
    )
    symbol_rows = {}
    for symbol in evaluated_symbols:
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
    full_rule_count = int(
        selection_context.get("expectedRuleCount") or len(shared_rule_ids)
    )
    return {
        "contractVersion": WORLD_PARTITIONED_REASONING_VERSION,
        "status": "ready",
        "ready": True,
        "worldId": shared_world.world_id,
        "projectionStatus": "reused-pre-projection-generation",
        "executionStatus": "reused-shared-premise-generation",
        "premisesBySymbol": premises,
        "sharedRuleIds": list(shared_rule_ids),
        "overlayRuleIds": list(overlay_rule_ids),
        "inferenceGenerationId": generation_id,
        "sourceAboxSnapshotId": source_abox_snapshot_id,
        "relations": list(inference.get("relations") or [])[:480],
        "traces": list(inference.get("traces") or [])[:480],
        "symbols": symbol_rows,
        "requestedSymbols": list(requested_symbols),
        "evaluatedSymbols": list(evaluated_symbols),
        "notEvaluatedSymbols": list(not_evaluated_symbols),
        "targetCoverageComplete": not not_evaluated_symbols,
        "dynamicInferencePreflight": dict(preflight or {}),
        "inferenceImpactPlan": {
            "version": str((preflight or {}).get("version") or ""),
            "impactScope": "REUSED_SHARED_GENERATION",
            "candidateRuleIds": list((preflight or {}).get("candidateRuleIds") or []),
            "candidateRuleCount": int((preflight or {}).get("candidateRuleCount") or 0),
            "contextRetention": {
                "aboxReadMode": "complete-active-world",
                "unchangedFactsRetained": True,
                "priorValidInferencesRetained": True,
            },
        },
        "ruleSelectionProof": {
            "reusable": True,
            "proofSource": str(selection_context.get("proofSource") or ""),
            "reason": ",".join((preflight or {}).get("reasonCodes") or []),
            "selectionRequested": False,
            "selectionApplied": False,
            "generationReused": True,
            "reuseMode": reuse_mode,
            "candidateRuleCount": 0,
            "executedRuleCount": 0,
            "deferredRuleCount": full_rule_count,
            "fullRuleCount": full_rule_count,
        },
        "resultSlotWrite": {
            "status": "reused-existing-result-slots",
            "saved": True,
            "reused": True,
            "worldId": shared_world.world_id,
            "symbolCount": len(evaluated_symbols),
            "catalogRuleCount": full_rule_count,
            "slotCount": len(evaluated_symbols) * full_rule_count,
        },
        "existingInferenceReuseMode": reuse_mode,
        "generationVector": {
            "worldId": shared_world.world_id,
            "sourceAboxSnapshotId": source_abox_snapshot_id,
            "inferenceGenerationId": generation_id,
            "ruleboxRulesHash": shared_rulebox_hash,
            "tboxFingerprint": shared_tbox_fingerprint,
            "scopePlanFingerprint": str(target_scope_proof.get("fingerprint") or ""),
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
        "modelSignalBridgeExecution": {},
        "activationLifecycle": {},
        "assembly": {
            "inputMode": "preflight-reuse",
            "targetSymbols": list(evaluated_symbols),
        },
    }


def existing_inference_result(
    _store: ReusePort,
    snapshot: AccountSnapshot,
    target_symbols: List[str] = None,
    world_id: str = "",
) -> Dict[str, object]:
    if not hasattr(_store.repository, "inferencebox_snapshot"):
        return {}
    inference_symbols = _store.inference_symbols(snapshot, target_symbols)
    try:
        inferencebox = _store.repository_world_call(
            "inferencebox_snapshot",
            symbols=inference_symbols,
            limit=_store.inference_snapshot_limit(),
            world_id=world_id,
        )
    except (
        Exception
    ) as error:  # noqa: BLE001 - unchanged ABox remains valid even if readback fails.
        inferencebox = {"status": "error", "reason": str(error)[:180]}
    return dict(inferencebox or {}) if isinstance(inferencebox, dict) else {}


def inference_result_is_reusable(
    _store: ReusePort,
    inferencebox: Dict[str, object],
    active_abox: Dict[str, object],
    required_symbols: List[str] = None,
) -> bool:
    inference_status = str((inferencebox or {}).get("status") or "").strip().lower()
    if inference_status not in {"ok", "empty"}:
        return False
    native_output_used = bool((inferencebox or {}).get("nativeTypeDbReasoningUsed"))
    native_evaluation_completed = bool(
        (inferencebox or {}).get("nativeTypeDbReasoningCompleted")
        or (inferencebox or {}).get("typedbNativeRuleEvaluationCompleted")
        or native_output_used
    )
    # A verified no-match is a complete current-generation result, not a
    # missing InferenceBox. It must be allowed to finalize the matching
    # ABox so the worker can continue to the next subject.
    if not native_evaluation_completed:
        return False
    if inference_status == "empty" and native_output_used:
        return False
    if inference_status == "ok" and not native_output_used:
        return False
    if (inferencebox or {}).get("generationAligned") is False:
        return False
    source_abox_id = str((inferencebox or {}).get("sourceAboxSnapshotId") or "").strip()
    active_abox_id = str((active_abox or {}).get("aboxSnapshotId") or "").strip()
    if not (source_abox_id and active_abox_id and source_abox_id == active_abox_id):
        return False
    expected = {
        str(symbol or "").upper().strip()
        for symbol in list(required_symbols or [])
        if str(symbol or "").strip()
    }
    actual = {
        str(symbol or "").upper().strip()
        for symbol in list((inferencebox or {}).get("targetSymbols") or [])
        if str(symbol or "").strip()
    }
    return not expected or expected.issubset(actual)


def attach_inference_reuse_proof(
    _store: ReusePort, projection_run: OntologyProjectionRun, result: Dict[str, object]
) -> None:
    """Record a TypeDB-complete target result for later rule scheduling.

    The proof retains only scope fingerprints and TypeDB-reported rule
    identities. It does not carry ABox values or derive an investment
    outcome outside TypeDB.
    """
    context = dict(projection_run.context_payload or {})
    topology = (
        context.get("scopeTopology")
        if isinstance(context.get("scopeTopology"), dict)
        else {}
    )
    scope_plan = inference_reuse_scope_plan(
        topology.get("inferenceReuseScopePlan") or []
    )
    scope_plan_fingerprint = (
        inference_reuse_scope_plan_fingerprint(scope_plan) if scope_plan else ""
    )
    inference = (
        result.get("inferenceBox")
        if isinstance(result.get("inferenceBox"), dict)
        else {}
    )
    execution = (
        result.get("ruleboxExecution")
        if isinstance(result.get("ruleboxExecution"), dict)
        else {}
    )
    matched_rule_ids = _store.matched_rule_ids_from_inference_payload(execution)
    for rule_id in _store.matched_rule_ids_from_inference_payload(inference):
        if rule_id not in matched_rule_ids:
            matched_rule_ids.append(rule_id)
    native_evaluation_complete = bool(
        inference.get("nativeTypeDbReasoningCompleted")
        or inference.get("typedbNativeRuleEvaluationCompleted")
        or execution.get("nativeInferenceEvaluationComplete")
    )
    target_symbols = [
        str(symbol or "").upper().strip()
        for symbol in list(
            inference.get("targetSymbols") or projection_run.source_symbols or []
        )
        if str(symbol or "").strip()
    ]
    source_abox_snapshot_id = str(inference.get("sourceAboxSnapshotId") or "").strip()
    expected_abox_snapshot_id = str(projection_run.abox_snapshot_id or "").strip()
    selection_applied = bool(execution.get("nativeRuleSelectionApplied"))
    inherited_coverage = bool(
        selection_applied
        and isinstance(result.get("priorInferenceReuse"), dict)
        and result["priorInferenceReuse"].get("reusable")
    )
    coverage_complete = bool(not selection_applied or inherited_coverage)
    matched_count = int(execution.get("typedbNativeRuleMatchedCount") or 0)
    match_ids_complete = not matched_count or bool(matched_rule_ids)
    verified = bool(
        str(result.get("status") or "") == "ok"
        and native_evaluation_complete
        and bool(inference.get("generationAligned"))
        and bool(scope_plan)
        and scope_plan_fingerprint
        == str(topology.get("inferenceReuseScopePlanFingerprint") or "")
        and bool(projection_run.rulebox_rules_hash)
        and bool(projection_run.tbox_fingerprint)
        and bool(projection_run.execution_namespace_id)
        and bool(projection_run.engine_deployment_id)
        and bool(projection_run.graph_database)
        and bool(projection_run.release_fingerprint)
        and bool(target_symbols)
        and source_abox_snapshot_id == expected_abox_snapshot_id
        and coverage_complete
        and match_ids_complete
    )
    if verified:
        reason = ""
    elif not coverage_complete:
        reason = "Previous native coverage was unavailable for a dependency-selected inference result."
    elif matched_count and not matched_rule_ids:
        reason = "TypeDB reported matches without persisted matched rule identities."
    else:
        reason = (
            "Current TypeDB inference did not produce a complete reusable target proof."
        )
    result["inferenceReuseProof"] = {
        "version": INFERENCE_REUSE_PROOF_VERSION,
        "status": "verified" if verified else "incomplete",
        "reason": reason,
        "coverageComplete": coverage_complete,
        "executionNamespaceId": str(projection_run.execution_namespace_id or ""),
        "engineDeploymentId": str(projection_run.engine_deployment_id or ""),
        "graphDatabase": str(projection_run.graph_database or ""),
        "releaseFingerprint": str(projection_run.release_fingerprint or ""),
        "sourceAboxSnapshotId": source_abox_snapshot_id,
        "inferenceGenerationId": str(inference.get("inferenceGenerationId") or ""),
        "targetSymbols": target_symbols,
        "matchedRuleIds": matched_rule_ids[:160],
        "matchedRuleCount": len(matched_rule_ids),
        "ruleboxRulesHash": str(projection_run.rulebox_rules_hash or ""),
        "tboxFingerprint": str(projection_run.tbox_fingerprint or ""),
        "scopePlanFingerprint": scope_plan_fingerprint,
        "scopePlanCount": len(scope_plan),
        "selectionApplied": selection_applied,
        "inheritedCoverage": inherited_coverage,
    }
    result["nativeReplayValidation"] = native_replay_validation(result)
