"""Record implementation; facade-independent dependencies."""

from __future__ import annotations
from .record_ports import RecordPort, RecordSnapshotBindings
from copy import deepcopy
from digital_twin.domain.ontology_change_impact import compact_inference_impact_plan
from digital_twin.domain.ontology_current_state import (
    CURRENT_STATE_ABOX_PERSISTENCE_MODE,
)
from digital_twin.domain.ontology_native_rule_planning import (
    merge_native_rule_planner_topology,
    native_rule_planner_manifest_fingerprint,
)
from digital_twin.domain.ontology_performance_contract import (
    ontology_performance_assessment,
)
from digital_twin.domain.ontology_projection_audit import (
    compact_reasoning_request_context,
)
from digital_twin.domain.ontology_projection_fingerprint import (
    active_material_fingerprint,
)
from digital_twin.domain.ontology_projection_status import (
    TYPEDB_REASONING_WORKER_DEFERRED,
)
from digital_twin.domain.ontology_scopes import (
    SCOPED_ABOX_MANIFEST_VERSION,
    SCOPED_ABOX_PERSISTENCE_MODE,
    apply_scoped_abox_repair_epochs,
    apply_scoped_manifest_plan,
    merge_target_scoped_abox_manifest,
    target_scope_manifest_fingerprint,
)
from digital_twin.domain.ontology_validator import validate_ontology
from digital_twin.domain.ontology_world_routing import route_world_impact
from digital_twin.domain.ontology_worlds import (
    knowledge_world,
    market_world,
    shared_premise_world,
    world_from_snapshot,
    world_metadata,
)
from digital_twin.domain.portfolio import AccountSnapshot
from typing import Callable, Dict, List
import time
import traceback


def record_snapshot(
    _store: RecordPort,
    snapshot: AccountSnapshot,
    target_symbols: List[str] = None,
    reasoning_context: Dict[str, object] = None,
    progress_callback: Callable[[str, Dict[str, object]], None] = None,
    *,
    _bindings: RecordSnapshotBindings,
) -> Dict[str, object]:
    projection_started = time.perf_counter()
    runtime_stages: Dict[str, int] = {}
    projection_run = None
    pending_activation_recovery: Dict[str, object] = {}
    current_stage = "start"

    def emit_progress(stage: str, **details) -> None:
        nonlocal current_stage
        current_stage = str(stage or "unknown")
        if not callable(progress_callback):
            return
        payload = dict(details or {})
        payload.setdefault("accountId", str(snapshot.account_id or ""))
        payload.setdefault(
            "elapsedMs", int((time.perf_counter() - projection_started) * 1000)
        )
        try:
            progress_callback("ontology_projection." + str(stage or "unknown"), payload)
        except Exception:
            return

    emit_progress(
        "start",
        targetSymbolCount=len(target_symbols or []),
        source=str(_store.source or "monitoring"),
    )
    compact_reasoning_context = compact_reasoning_request_context(
        reasoning_context,
        target_symbols=target_symbols,
    )
    shared_premise_proof = (
        dict((reasoning_context or {}).get("sharedPremiseProof") or {})
        if isinstance(reasoning_context, dict)
        else {}
    )
    fresh_candidate_configured = str(
        _store.settings.get("typedbFreshCandidateRebuild") or "0"
    ).strip().lower() in {"1", "true", "yes", "on", "enabled"}
    portfolio_world_context = world_from_snapshot(snapshot, _store.settings)
    candidate_bootstrap_check = getattr(
        _store.repository,
        "fresh_candidate_world_bootstrap_required",
        None,
    )
    fresh_candidate_rebuild = bool(fresh_candidate_configured)
    if fresh_candidate_rebuild and callable(candidate_bootstrap_check):
        fresh_candidate_rebuild = bool(
            candidate_bootstrap_check(portfolio_world_context.world_id)
        )
    market_world_context = market_world(
        portfolio_world_context.market_id,
        _store.settings.get("ontologySharedMarketTenantId") or "shared",
    )
    knowledge_world_context = knowledge_world(
        portfolio_world_context.market_id,
        _store.settings.get("ontologySharedMarketTenantId") or "shared",
    )
    if not _store.repository:
        emit_progress("skipped", status="repository-unavailable")
        return {}
    if not _store.has_projectable_data(snapshot):
        result = {
            "saved": False,
            "status": "rejected-non-live-snapshot",
            "reason": "운영 ABox는 정상 live 계좌의 실제 보유·관심종목 스냅샷으로만 갱신합니다.",
            "snapshotMode": str(snapshot.mode or ""),
            "snapshotStatus": str(snapshot.status or ""),
            "preservedActiveGeneration": True,
            "ontologyWorld": world_metadata(portfolio_world_context),
        }
        _store.store_projection_result(snapshot, result)
        emit_progress("rejected", status=result["status"])
        return result
    if target_symbols:
        target_input = snapshot.projection_observation_input(target_symbols)
        if str(target_input.get("mode") or "") == "empty":
            result = {
                "saved": False,
                "status": "skipped-inactive-target-symbols",
                "reason": "추론 요청 종목이 현재 보유·관심종목 스냅샷에 없어 전체 계좌 재추론을 건너뛰었습니다.",
                "targetSymbols": list(target_input.get("targetSymbols") or []),
                "availableSymbols": list(target_input.get("availableSymbols") or []),
                "preservedActiveGeneration": True,
                "ontologyWorld": world_metadata(portfolio_world_context),
            }
            _store.store_projection_result(snapshot, result)
            emit_progress("skipped", status=result["status"])
            return result
    if _store.typedb_projection_deferred():
        result = {
            "saved": False,
            "status": TYPEDB_REASONING_WORKER_DEFERRED,
            "reason": "TypeDB ABox와 InferenceBox는 전용 온톨로지 추론 워커가 같은 주기에서 생성합니다.",
            "preservedActiveGeneration": True,
            "singleWriter": True,
            "ontologyWorld": world_metadata(portfolio_world_context),
        }
        _store.store_projection_result(snapshot, result)
        emit_progress("deferred", status=result["status"])
        return result
    emit_progress("pending_activation_recovery.start")
    pending_recovery_started = time.perf_counter()
    pending_activation_recovery = (
        {
            "configured": True,
            "status": "skipped-fresh-candidate",
            "graphStore": "typedb",
            "reason": "The isolated blue-green candidate has no prior PortfolioWorld activation.",
        }
        if fresh_candidate_rebuild
        else _store.recover_pending_abox_activation(
            portfolio_world_context.world_id,
            max_staged_target_symbols=_store.scheduler_target_symbol_limit(
                compact_reasoning_context
            ),
        )
    )
    runtime_stages["pendingAboxActivationRecoveryMs"] = int(
        (time.perf_counter() - pending_recovery_started) * 1000
    )
    recovery_status = str(pending_activation_recovery.get("status") or "skipped")
    emit_progress(
        "pending_activation_recovery.done",
        status=recovery_status,
        runtimeMs=runtime_stages["pendingAboxActivationRecoveryMs"],
    )
    # Recovery takes the same database-wide writer coordinator as an ABox
    # swap.  A held coordinator is normal back-pressure: keep the
    # existing generation and let the reasoning mailbox retry after the
    # current writer finishes.  Collapsing it into a generic recovery
    # failure incorrectly opened the projection circuit and left the
    # pending activation (and its retired Manifest cleanup) stranded.
    recovery_is_retryable = recovery_status.startswith("deferred-") or bool(
        pending_activation_recovery.get("retryable")
    )
    if recovery_is_retryable:
        try:
            recovery_retry_after = max(
                1,
                int(
                    float(
                        pending_activation_recovery.get("recommendedRetryAfterSeconds")
                        or pending_activation_recovery.get("retryAfterSeconds")
                        or 10
                    )
                ),
            )
        except (TypeError, ValueError):
            recovery_retry_after = 10
        result = {
            "saved": False,
            "status": (
                recovery_status
                if recovery_status.startswith("deferred-")
                else "deferred-pending-abox-activation-recovery"
            ),
            "reason": str(
                pending_activation_recovery.get("reason")
                or "TypeDB ABox activation recovery is waiting for a safe retry."
            )[:220],
            "graphStore": _store.active_graph_store_key(),
            "retryable": True,
            "recommendedRetryAfterSeconds": recovery_retry_after,
            "preservedActiveGeneration": True,
            "pendingAboxActivationRecovery": pending_activation_recovery,
        }
        if isinstance(pending_activation_recovery.get("projectionCoordinator"), dict):
            result["projectionCoordinator"] = dict(
                pending_activation_recovery.get("projectionCoordinator") or {}
            )
        _store.store_projection_result(snapshot, result)
        emit_progress("deferred", status=result["status"])
        return result
    if recovery_status not in {
        "skipped",
        "skipped-fresh-candidate",
        "disabled",
        "finalized",
        "finalized-empty-target",
        "restored",
        "cleared-stale",
        "discarded-staged-batch",
        "retry-required",
        "staged",
    }:
        result = {
            "saved": False,
            "status": "pending-abox-activation-recovery-failed",
            "reason": str(
                pending_activation_recovery.get("reason")
                or "TypeDB ABox activation recovery must complete before a new investment inference cycle."
            )[:220],
            "graphStore": _store.active_graph_store_key(),
            "preservedActiveGeneration": True,
            "pendingAboxActivationRecovery": pending_activation_recovery,
        }
        _store.store_projection_result(snapshot, result)
        emit_progress("blocked", status=result["status"])
        return result
    if recovery_status in {"staged", "retry-required"}:
        resume_started = time.perf_counter()
        result = _store.resume_staged_pending_abox_activation(
            snapshot,
            portfolio_world_context.world_id,
            pending_activation_recovery,
        )
        runtime_stages["pendingAboxActivationResumeMs"] = int(
            (time.perf_counter() - resume_started) * 1000
        )
        result["pendingAboxActivationRecovery"] = pending_activation_recovery
        runtime_stages["totalMs"] = int(
            (time.perf_counter() - projection_started) * 1000
        )
        result.setdefault("runtimeStages", runtime_stages)
        resumed_projection_run = _store.active_projection_audit_run(
            portfolio_world_context.world_id,
        )
        if resumed_projection_run is not None:
            result["resumedProjectionAudit"] = {
                "status": "completed-with-recovered-activation",
                "runId": resumed_projection_run.run_id,
            }
        _store.store_projection_result(
            snapshot,
            result,
            resumed_projection_run or projection_run,
        )
        emit_progress("completed", status=str(result.get("status") or ""))
        return result
    # A staged or targetless legacy activation has no bounded InferenceBox
    # proof to reconcile yet. The latter is finalized as control-only
    # repair, then this cycle stages the current manifest. Avoid reading
    # historical InferenceBox rows before that bounded retry begins.
    if not fresh_candidate_rebuild and recovery_status not in {
        "retry-required",
        "staged",
        "finalized-empty-target",
    }:
        audit_recovery_required = _store.interrupted_projection_recovery_required(
            portfolio_world_context.world_id
        )
        runtime_stages["interruptedProjectionAuditRecoveryCandidate"] = (
            1 if audit_recovery_required else 0
        )
        if audit_recovery_required:
            audit_recovery_started = time.perf_counter()
            _store.reconcile_interrupted_projection_audit(
                portfolio_world_context.world_id
            )
            runtime_stages["interruptedProjectionAuditRecoveryMs"] = int(
                (time.perf_counter() - audit_recovery_started) * 1000
            )
        else:
            runtime_stages["interruptedProjectionAuditRecoveryMs"] = 0
    try:
        emit_progress("rule_catalog.start")
        rulebox_bootstrap_started = time.perf_counter()
        rulebox_bootstrap = _store.ensure_rulebox_ready()
        runtime_stages["ruleboxBootstrapMs"] = int(
            (time.perf_counter() - rulebox_bootstrap_started) * 1000
        )
        emit_progress(
            "rule_catalog.done",
            status=str(rulebox_bootstrap.get("status") or ""),
            runtimeMs=runtime_stages["ruleboxBootstrapMs"],
        )
        if str(rulebox_bootstrap.get("status") or "") not in {"ready", "seeded"}:
            result = {
                "saved": False,
                "status": "typedb-rule-catalog-not-ready",
                "reason": str(
                    rulebox_bootstrap.get("reason")
                    or "TypeDB 추론 규칙을 사용할 수 없습니다."
                ),
                "preservedActiveGeneration": True,
                "ruleCatalog": rulebox_bootstrap,
            }
            _store.store_projection_result(snapshot, result, projection_run)
            emit_progress("blocked", status=result["status"])
            return result
        emit_progress("graph_assembly.start")
        projection_graph = _store.build_projection_graph(
            snapshot,
            rulebox_bootstrap,
            portfolio_world_context,
            market_world_context=market_world_context,
            target_symbols=target_symbols,
            target_scoped_input=bool(target_symbols),
            progress_callback=emit_progress,
            shared_premise_proof=shared_premise_proof,
            reasoning_context=compact_reasoning_context,
        )
        graph = projection_graph["graph"]
        persistence_graph = projection_graph["persistenceGraph"]
        graph_assembly = projection_graph["assembly"]
        planner_topology = projection_graph["plannerTopology"]
        material_fingerprint = projection_graph["materialFingerprint"]
        material_snapshot_id = projection_graph["materialSnapshotId"]
        scoped_identity = projection_graph["scopedIdentity"]
        runtime_stages.update(dict(projection_graph.get("runtimeStages") or {}))
        if str(graph_assembly.get("inputMode") or "") == "target-scoped":
            # A target-scoped graph is an incremental patch by contract.
            # Missing scopes therefore mean "reuse the active generation".
            # Deletion requires an explicit scoped source fact or operator
            # rebuild; no timer may expand this request to the whole world.
            # This avoids turning one-symbol mailbox work into a complete
            # manifest rewrite while preserving explicit deletion checks
            # on the full projection path.
            persistence_graph.worldview["targetScopeRetentionMode"] = (
                "incremental-target-patch"
            )
        observation_followup_targets = sorted(
            {
                str(symbol or "").upper().strip()
                for symbol in compact_reasoning_context.get(
                    "observationFollowupSymbols"
                )
                or []
                if str(symbol or "").strip()
            }.intersection(
                {
                    str(symbol or "").upper().strip()
                    for symbol in target_symbols or []
                    if str(symbol or "").strip()
                }
            )
        )
        if observation_followup_targets:
            # A target-scoped source intentionally omits unrelated and
            # temporarily absent target facts. For a raw quote follow-up
            # that omission means "retain the last verified context", not
            # "delete the scope". This keeps the TypeDB operation bounded
            # to the notified quote scopes while TypeDB still evaluates
            # its rules over the merged active ABox.
            persistence_graph.worldview["targetScopeRetentionMode"] = (
                "observation-followup"
            )
            persistence_graph.worldview["observationFollowupTargets"] = (
                observation_followup_targets
            )
        emit_progress(
            "graph_assembly.done",
            cacheLayer=str((graph_assembly or {}).get("cacheLayer") or "none"),
            cacheStatus=str((graph_assembly or {}).get("status") or ""),
            runtimeMs=int(runtime_stages.get("graphBuildMs") or 0),
        )
        graph_input = {
            "mode": str(graph_assembly.get("inputMode") or "full"),
            "targetSymbols": list(graph_assembly.get("targetSymbols") or []),
            "requestedTargetSymbols": sorted(
                {
                    str(symbol or "").upper().strip()
                    for symbol in target_symbols or []
                    if str(symbol or "").strip()
                }
            ),
            "sourcePositionCount": int(graph_assembly.get("sourcePositionCount") or 0),
            "referencePositionCount": int(
                graph_assembly.get("referencePositionCount") or 0
            ),
            "externalSignalProjection": dict(
                graph_assembly.get("externalSignalProjection") or {}
            ),
            "fallback": False,
            "fallbackReason": "",
        }
        runtime_stages["targetScopedInputUsed"] = (
            1 if graph_input["mode"] == "target-scoped" else 0
        )
        emit_progress("active_abox_read.start")
        active_abox_started = time.perf_counter()
        active_abox = (
            {}
            if fresh_candidate_rebuild
            else _store.active_abox_metadata(portfolio_world_context.world_id)
        )
        runtime_stages["activeAboxReadMs"] = int(
            (time.perf_counter() - active_abox_started) * 1000
        )
        emit_progress(
            "active_abox_read.done",
            status=str(active_abox.get("status") or ""),
            runtimeMs=runtime_stages["activeAboxReadMs"],
        )
        evidence_index_upgrade = {}
        active_abox_complete = str(active_abox.get("status") or "ok") == "ok"
        active_abox_is_scoped_manifest = (
            str(active_abox.get("scopedAboxManifestVersion") or "")
            == SCOPED_ABOX_MANIFEST_VERSION
        )
        emit_progress(
            "target_scope_plan.start",
            requestedTargetSymbolCount=len(target_symbols or []),
        )
        target_scope_plan_started = time.perf_counter()
        target_scoped_patch = _store.target_scoped_patch_targets(
            snapshot,
            active_abox,
            scoped_identity,
            target_symbols,
            reasoning_context=compact_reasoning_context,
        )
        runtime_stages["targetScopedPatchPlanningMs"] = int(
            (time.perf_counter() - target_scope_plan_started) * 1000
        )
        emit_progress(
            "target_scope_plan.done",
            status=str(target_scoped_patch.get("status") or ""),
            eligible=bool(target_scoped_patch.get("eligible")),
            runtimeMs=runtime_stages["targetScopedPatchPlanningMs"],
        )
        # A first projection, a scheduled complete reconciliation, or a
        # shared-scope shape that cannot be retained must keep the full
        # source graph. The target input is merely a bounded assembly
        # optimization; it must never turn an unsafe patch into a partial
        # ABox replacement.
        if str(graph_input.get("mode") or "") == "target-scoped" and not bool(
            target_scoped_patch.get("eligible")
        ):
            emit_progress(
                "full_input_fallback.start",
                reason=str(
                    target_scoped_patch.get("fallbackReason")
                    or target_scoped_patch.get("status")
                    or "target-scoped-input-not-eligible"
                ),
            )
            full_input_fallback_started = time.perf_counter()
            target_attempt_stages = dict(projection_graph.get("runtimeStages") or {})
            full_projection_graph = _store.build_projection_graph(
                snapshot,
                rulebox_bootstrap,
                portfolio_world_context,
                market_world_context=market_world_context,
                target_symbols=target_symbols,
                target_scoped_input=False,
                shared_premise_proof=shared_premise_proof,
                reasoning_context=compact_reasoning_context,
            )
            graph = full_projection_graph["graph"]
            persistence_graph = full_projection_graph["persistenceGraph"]
            graph_assembly = full_projection_graph["assembly"]
            planner_topology = full_projection_graph["plannerTopology"]
            material_fingerprint = full_projection_graph["materialFingerprint"]
            material_snapshot_id = full_projection_graph["materialSnapshotId"]
            scoped_identity = full_projection_graph["scopedIdentity"]
            full_runtime_stages = dict(full_projection_graph.get("runtimeStages") or {})
            for stage, value in full_runtime_stages.items():
                if stage == "graphBuildMs":
                    continue
                runtime_stages["fullInput" + stage[:1].upper() + stage[1:]] = value
            runtime_stages["targetScopedInputUsed"] = 1
            runtime_stages["targetScopedInputAttemptGraphBuildMs"] = int(
                target_attempt_stages.get("graphBuildMs") or 0
            )
            runtime_stages["targetScopedInputFallbackGraphBuildMs"] = int(
                full_runtime_stages.get("graphBuildMs") or 0
            )
            runtime_stages["targetScopedInputFallback"] = 1
            runtime_stages["graphBuildMs"] = (
                runtime_stages["targetScopedInputAttemptGraphBuildMs"]
                + runtime_stages["targetScopedInputFallbackGraphBuildMs"]
            )
            runtime_stages["targetScopedInputFallbackTotalMs"] = int(
                (time.perf_counter() - full_input_fallback_started) * 1000
            )
            emit_progress(
                "full_input_fallback.done",
                runtimeMs=runtime_stages["targetScopedInputFallbackTotalMs"],
            )
            graph_input.update(
                {
                    "mode": "full",
                    "targetSymbols": list(graph_assembly.get("targetSymbols") or []),
                    "sourcePositionCount": int(
                        graph_assembly.get("sourcePositionCount") or 0
                    ),
                    "referencePositionCount": int(
                        graph_assembly.get("referencePositionCount") or 0
                    ),
                    "externalSignalProjection": dict(
                        graph_assembly.get("externalSignalProjection") or {}
                    ),
                    "fallback": True,
                    "fallbackReason": str(
                        target_scoped_patch.get("fallbackReason")
                        or target_scoped_patch.get("status")
                        or "target-scoped-input-not-eligible"
                    ),
                }
            )
            target_scoped_patch = _store.target_scoped_patch_targets(
                snapshot,
                active_abox,
                scoped_identity,
                target_symbols,
                reasoning_context=compact_reasoning_context,
            )
        # Preserve the semantic scope produced from this immutable source
        # before it is merged with each deployment's older active
        # generations. Shadow parity is about equal inputs; the merged
        # store scope remains a separate diagnostic below.
        source_scope_plan = deepcopy(scoped_identity.get("scopePlan") or [])
        if target_scoped_patch.get("eligible"):
            emit_progress(
                "target_manifest_patch.start",
                targetSymbolCount=len(target_scoped_patch.get("targetSymbols") or []),
            )
            target_patch_started = time.perf_counter()
            scope_repair = apply_scoped_abox_repair_epochs(
                persistence_graph,
                active_abox,
                compact_reasoning_context.get("scopeRepairRequestsBySymbol") or {},
            )
            applied_target_patch = merge_target_scoped_abox_manifest(
                persistence_graph,
                active_abox,
                target_scoped_patch.get("targetSymbols") or [],
                fact_slot_plan=target_scoped_patch.get("factSlotPlan") or {},
                source_graph_complete=str(graph_input.get("mode") or "") == "full",
            )
            repair_input_fallback = {}
            if (
                not applied_target_patch.get("applied")
                and str(graph_input.get("mode") or "") == "target-scoped"
                and (
                    not str(applied_target_patch.get("status") or "").startswith(
                        "blocked-"
                    )
                    or applied_target_patch.get("requiresCompleteSource") is True
                )
            ):
                # A scoped source can legitimately omit a shared endpoint
                # that is retained by the active Manifest. Reassemble the
                # complete source in memory once, then persist only the
                # originally requested target patch. This repairs the
                # source boundary without turning local work into a full
                # TypeDB world rewrite.
                emit_progress(
                    "target_manifest_repair_input.start",
                    status=str(applied_target_patch.get("status") or "repair-required"),
                    fallbackReason=str(
                        applied_target_patch.get("fallbackReason") or ""
                    ),
                    missingEndpointScopeIds=list(
                        applied_target_patch.get("missingEndpointScopeIds") or []
                    )[:24],
                    missingEndpointScopes="|".join(
                        str(value or "")
                        for value in (
                            applied_target_patch.get("missingEndpointScopeIds") or []
                        )[:12]
                    ),
                )
                repair_input_started = time.perf_counter()
                first_patch_failure = dict(applied_target_patch or {})
                repair_projection_graph = _store.build_projection_graph(
                    snapshot,
                    rulebox_bootstrap,
                    portfolio_world_context,
                    market_world_context=market_world_context,
                    target_symbols=target_symbols,
                    target_scoped_input=False,
                    shared_premise_proof=shared_premise_proof,
                    reasoning_context=compact_reasoning_context,
                )
                graph = repair_projection_graph["graph"]
                persistence_graph = repair_projection_graph["persistenceGraph"]
                graph_assembly = repair_projection_graph["assembly"]
                planner_topology = repair_projection_graph["plannerTopology"]
                material_fingerprint = repair_projection_graph["materialFingerprint"]
                material_snapshot_id = repair_projection_graph["materialSnapshotId"]
                scoped_identity = repair_projection_graph["scopedIdentity"]
                persistence_graph.worldview["targetScopeRetentionMode"] = (
                    "observation-followup"
                    if observation_followup_targets
                    else "incremental-target-patch"
                )
                if observation_followup_targets:
                    persistence_graph.worldview["observationFollowupTargets"] = list(
                        observation_followup_targets
                    )
                target_scoped_patch = _store.target_scoped_patch_targets(
                    snapshot,
                    active_abox,
                    scoped_identity,
                    target_symbols,
                    reasoning_context=compact_reasoning_context,
                )
                scope_repair = apply_scoped_abox_repair_epochs(
                    persistence_graph,
                    active_abox,
                    compact_reasoning_context.get("scopeRepairRequestsBySymbol") or {},
                )
                applied_target_patch = merge_target_scoped_abox_manifest(
                    persistence_graph,
                    active_abox,
                    target_scoped_patch.get("targetSymbols") or [],
                    fact_slot_plan=target_scoped_patch.get("factSlotPlan") or {},
                    source_graph_complete=True,
                )
                repair_runtime_stages = dict(
                    repair_projection_graph.get("runtimeStages") or {}
                )
                for stage, value in repair_runtime_stages.items():
                    runtime_stages[
                        "targetManifestRepairInput" + stage[:1].upper() + stage[1:]
                    ] = value
                runtime_stages["targetManifestRepairInputMs"] = int(
                    (time.perf_counter() - repair_input_started) * 1000
                )
                repair_input_fallback = {
                    "attempted": True,
                    "mode": "complete-source-assembly-target-persist",
                    "firstStatus": str(first_patch_failure.get("status") or ""),
                    "firstMissingEndpointScopeIds": list(
                        first_patch_failure.get("missingEndpointScopeIds") or []
                    )[:50],
                    "finalStatus": str(applied_target_patch.get("status") or ""),
                    "applied": bool(applied_target_patch.get("applied")),
                    "runtimeMs": runtime_stages["targetManifestRepairInputMs"],
                    "automaticFullProjectionBlocked": True,
                }
                graph_input.update(
                    {
                        "repairInputFallback": True,
                        "repairInputMode": "complete-source-assembly-target-persist",
                        "repairInputStatus": str(
                            applied_target_patch.get("status") or ""
                        ),
                    }
                )
                emit_progress(
                    "target_manifest_repair_input.done",
                    status=str(applied_target_patch.get("status") or ""),
                    applied=bool(applied_target_patch.get("applied")),
                    runtimeMs=runtime_stages["targetManifestRepairInputMs"],
                )
            runtime_stages["targetScopedManifestPatchMs"] = int(
                (time.perf_counter() - target_patch_started) * 1000
            )
            emit_progress(
                "target_manifest_patch.done",
                status=str(applied_target_patch.get("status") or ""),
                applied=bool(applied_target_patch.get("applied")),
                runtimeMs=runtime_stages["targetScopedManifestPatchMs"],
            )
            if applied_target_patch.get("applied"):
                # The source graph can contain newer observations for
                # deferred symbols. The persisted identity must describe
                # the merged active manifest, not facts intentionally held
                # for their own target cycle.
                incoming_planner_topology = dict(planner_topology or {})
                semantic_noop_patch = bool(
                    not applied_target_patch.get("selectedIncomingScopeIds")
                    and not applied_target_patch.get("retiredScopeIds")
                )
                replacement_symbols = list(
                    applied_target_patch.get("replacementSymbols")
                    if "replacementSymbols" in applied_target_patch
                    else applied_target_patch.get("targetSymbols") or []
                )
                active_planner_topology = dict(
                    active_abox.get("nativeRulePlannerTopology") or {}
                )
                if (
                    semantic_noop_patch
                    and str(active_planner_topology.get("status") or "") == "ok"
                ):
                    topology_merge = {
                        "status": "ok",
                        "reason": "No semantic scope changed; the verified active planner topology is reusable.",
                        "topology": active_planner_topology,
                        "replacedSymbols": [],
                        "retainedSymbols": list(
                            active_planner_topology.get("symbols") or []
                        ),
                        "activeSymbolCount": int(
                            active_planner_topology.get("symbolCount") or 0
                        ),
                        "incomingSymbolCount": int(
                            incoming_planner_topology.get("symbolCount") or 0
                        ),
                        "mergedSymbolCount": int(
                            active_planner_topology.get("symbolCount") or 0
                        ),
                        "semanticNoopReuse": True,
                    }
                else:
                    topology_merge = merge_native_rule_planner_topology(
                        active_planner_topology,
                        incoming_planner_topology,
                        replacement_symbols,
                    )
                merged_topology_available = (
                    str(topology_merge.get("status") or "") == "ok"
                )
                planner_topology = dict(
                    topology_merge.get("topology")
                    if merged_topology_available
                    else incoming_planner_topology
                )
                if merged_topology_available:
                    persistence_graph.worldview["nativeRulePlannerTopologyIncoming"] = (
                        incoming_planner_topology
                    )
                else:
                    # Older markers may predate the structural index. Keep
                    # this target correct through active-membership
                    # fallback, then establish the complete merged index on
                    # the next eligible scoped or full projection.
                    persistence_graph.worldview.pop(
                        "nativeRulePlannerTopologyIncoming", None
                    )
                persistence_graph.worldview["nativeRulePlannerTopology"] = (
                    planner_topology
                )
                persistence_graph.worldview["nativeRulePlannerTopologyMerge"] = {
                    key: topology_merge.get(key)
                    for key in [
                        "status",
                        "reason",
                        "replacedSymbols",
                        "retainedSymbols",
                        "activeSymbolCount",
                        "incomingSymbolCount",
                        "mergedSymbolCount",
                        "semanticNoopReuse",
                    ]
                }
                material_fingerprint = native_rule_planner_manifest_fingerprint(
                    applied_target_patch.get("scopeManifestFingerprint"),
                    planner_topology,
                )
                scoped_identity = apply_scoped_manifest_plan(
                    persistence_graph,
                    applied_target_patch.get("scopePlan") or [],
                    account_id=snapshot.account_id,
                    world_id=portfolio_world_context.world_id,
                    material_fingerprint=material_fingerprint,
                )
                material_snapshot_id = str(
                    scoped_identity.get("manifestId") or material_snapshot_id
                )
                scope_selection_trace = _bindings.compact_target_scope_selection_trace(
                    applied_target_patch,
                )
                relation_rebind_root_scope_ids = list(
                    scope_selection_trace.get("relationRebindRootScopeIds") or []
                )
                target_scoped_patch = {
                    "status": "applied",
                    "mode": "incremental-target-scoped-manifest-patch",
                    "targetSymbols": list(
                        applied_target_patch.get("targetSymbols") or []
                    ),
                    "replacementSymbols": replacement_symbols,
                    "replacementRootScopeIds": list(
                        applied_target_patch.get("replacementRootScopeIds") or []
                    ),
                    "selectedIncomingScopeCount": len(
                        applied_target_patch.get("selectedIncomingScopeIds") or []
                    ),
                    "semanticNoop": semantic_noop_patch,
                    "reusedActiveScopeCount": len(
                        applied_target_patch.get("reusedActiveScopeIds") or []
                    ),
                    "deferredScopeCount": len(
                        applied_target_patch.get("deferredScopeIds") or []
                    ),
                    # These exact scope IDs are part of the persistence
                    # contract. The TypeDB adapter uses them to keep the
                    # active semantic relation image when the current
                    # in-memory graph also contains changes from another
                    # event family.
                    "deferredRelationScopeIds": list(
                        applied_target_patch.get("deferredRelationScopeIds") or []
                    ),
                    "reusedActiveRelationScopeIds": list(
                        applied_target_patch.get("reusedActiveRelationScopeIds") or []
                    ),
                    # This is a persistence-integrity contract, not
                    # optional trace detail. Without it the repository
                    # treats endpoint companions as semantic roots and can
                    # rebind relations to nodes not staged by this patch.
                    "relationRebindRootScopeIds": relation_rebind_root_scope_ids,
                    "relationRebindRootScopeCount": len(relation_rebind_root_scope_ids),
                    "retiredScopeIds": list(
                        applied_target_patch.get("retiredScopeIds") or []
                    ),
                    "scopeTopologyVersion": str(
                        (persistence_graph.worldview or {}).get("scopeTopologyVersion")
                        or ""
                    ),
                    "scopeTopologyMigration": dict(
                        applied_target_patch.get("scopeTopologyMigration") or {}
                    ),
                    "boundedScopeCount": len(
                        [
                            item
                            for item in applied_target_patch.get("scopePlan") or []
                            if ":bucket:" in str(item.get("scopeId") or "")
                            or ":window:" in str(item.get("scopeId") or "")
                        ]
                    ),
                    "selectedBoundedScopeCount": len(
                        [
                            scope_id
                            for scope_id in applied_target_patch.get(
                                "selectedIncomingScopeIds"
                            )
                            or []
                            if ":bucket:" in str(scope_id)
                            or ":window:" in str(scope_id)
                        ]
                    ),
                    "factSlotStatus": str(
                        (applied_target_patch.get("factSlot") or {}).get("status") or ""
                    ),
                    "factSlotSelectedScopeCount": len(
                        (applied_target_patch.get("factSlot") or {}).get(
                            "selectedScopeIds"
                        )
                        or []
                    ),
                    "factSlotDeferredScopeCount": len(
                        (applied_target_patch.get("factSlot") or {}).get(
                            "deferredScopeIds"
                        )
                        or []
                    ),
                    "factSlotFamilies": list(
                        (applied_target_patch.get("factSlot") or {}).get("slotFamilies")
                        or []
                    )[:20],
                    "factSlotFamiliesBySymbol": dict(
                        (applied_target_patch.get("factSlot") or {}).get(
                            "slotFamiliesBySymbol"
                        )
                        or {}
                    ),
                    "factSlotChangedFieldsBySymbol": dict(
                        (applied_target_patch.get("factSlot") or {}).get(
                            "changedFieldsBySymbol"
                        )
                        or {}
                    ),
                    "factSlotPreciseFieldRoutingSymbols": list(
                        (applied_target_patch.get("factSlot") or {}).get(
                            "preciseFieldRoutingSymbols"
                        )
                        or []
                    )[:20],
                    "factSlotUnclassifiedChangedFieldsBySymbol": dict(
                        (applied_target_patch.get("factSlot") or {}).get(
                            "unclassifiedChangedFieldsBySymbol"
                        )
                        or {}
                    ),
                    "factSlotFallbackReason": str(
                        (applied_target_patch.get("factSlot") or {}).get(
                            "fallbackReason"
                        )
                        or ""
                    ),
                    "scopeSelectionTrace": scope_selection_trace,
                    "manifestPatchContract": dict(
                        applied_target_patch.get("manifestPatchContract") or {}
                    ),
                    "scopeIntegrityAuditIntervalMinutes": _store.scope_integrity_audit_interval_minutes(),
                    "scopeIntegrityAuditDue": bool(
                        target_scoped_patch.get("scopeIntegrityAuditDue")
                    ),
                    "scopeRepair": {
                        key: scope_repair.get(key)
                        for key in [
                            "status",
                            "applied",
                            "requestedScopeIds",
                            "repairedScopeIds",
                            "retainedRepairScopeIds",
                        ]
                        if key in scope_repair
                    },
                    "repairInputFallback": dict(repair_input_fallback),
                    "automaticFullProjectionBlocked": True,
                }
                persistence_graph.worldview["targetScopedManifestPatch"] = dict(
                    target_scoped_patch
                )
            elif str(graph_input.get("mode") or "") == "target-scoped":
                # A local event must never become a whole-world write merely
                # because its incremental merge needs repair. Preserve the
                # active Manifest and surface the exact scope failure. An
                # operator can run the explicit rebuild path for a topology
                # migration; normal workers remain bounded by subject.
                result = {
                    "saved": False,
                    "status": "target-scope-repair-required",
                    "reason": "Target-scoped Manifest patch could not be applied safely.",
                    "graphStore": _store.active_graph_store_key(),
                    "preservedActiveGeneration": True,
                    "recommendedRetryAfterSeconds": 60,
                    "graphInput": graph_input,
                    "targetScopedManifestPatch": {
                        "status": str(
                            applied_target_patch.get("status") or "repair-required"
                        ),
                        "mode": "target-scope-repair-required",
                        "targetSymbols": list(
                            target_scoped_patch.get("targetSymbols") or []
                        ),
                        "incomingScopeCount": int(
                            applied_target_patch.get("incomingScopeCount") or 0
                        ),
                        "activeScopeCount": int(
                            applied_target_patch.get("activeScopeCount") or 0
                        ),
                        "missingEndpointScopeIds": list(
                            applied_target_patch.get("missingEndpointScopeIds") or []
                        )[:50],
                        "removedRelevantScopeIds": list(
                            applied_target_patch.get("removedRelevantScopeIds") or []
                        )[:50],
                        "sharedRemovedScopeIds": list(
                            applied_target_patch.get("sharedRemovedScopeIds") or []
                        )[:50],
                        "retiredScopeIds": list(
                            applied_target_patch.get("retiredScopeIds") or []
                        )[:50],
                        "manifestPatchContract": dict(
                            applied_target_patch.get("manifestPatchContract") or {}
                        ),
                        "patchPlanViolations": list(
                            applied_target_patch.get("patchPlanViolations") or []
                        )[:50],
                        "scopeTopologyMigration": dict(
                            applied_target_patch.get("scopeTopologyMigration") or {}
                        ),
                        "retainedDependencyScopeIds": list(
                            applied_target_patch.get("retainedDependencyScopeIds") or []
                        )[:50],
                        "selectedDependencyScopeIds": list(
                            applied_target_patch.get("selectedDependencyScopeIds") or []
                        )[:50],
                        "factSlot": dict(applied_target_patch.get("factSlot") or {}),
                        "fallbackReason": str(
                            applied_target_patch.get("fallbackReason")
                            or applied_target_patch.get("status")
                            or "target-scoped-manifest-patch-not-applied"
                        ),
                        "repairInputFallback": dict(repair_input_fallback),
                        "automaticFullProjectionBlocked": True,
                    },
                }
                _store.store_projection_result(snapshot, result, projection_run)
                return result
            else:
                target_scoped_patch = {
                    "status": str(applied_target_patch.get("status") or "skipped"),
                    "mode": "full-manifest-fallback",
                    "targetSymbols": list(
                        target_scoped_patch.get("targetSymbols") or []
                    ),
                    "fallbackReason": str(
                        applied_target_patch.get("fallbackReason")
                        or applied_target_patch.get("status")
                        or "target-scoped-manifest-patch-not-applied"
                    ),
                    "selectedIncomingScopeCount": len(
                        applied_target_patch.get("selectedIncomingScopeIds") or []
                    ),
                    "deferredScopeCount": len(
                        applied_target_patch.get("deferredScopeIds") or []
                    ),
                    "scopeTopologyMigration": dict(
                        applied_target_patch.get("scopeTopologyMigration") or {}
                    ),
                }
        active_persistence_mode = str(
            active_abox.get("persistenceMode")
            or active_abox.get("physicalStateMode")
            or SCOPED_ABOX_PERSISTENCE_MODE
        )
        current_state_cycle_eligible = _store.current_state_abox_storage_enabled()
        desired_persistence_mode = (
            CURRENT_STATE_ABOX_PERSISTENCE_MODE
            if current_state_cycle_eligible
            else SCOPED_ABOX_PERSISTENCE_MODE
        )
        current_state_migration_mode = (
            "steady-state"
            if active_persistence_mode == CURRENT_STATE_ABOX_PERSISTENCE_MODE
            else (
                "full"
                if str(graph_input.get("mode") or "full") == "full"
                else "progressive"
            )
        )
        physical_state_migration_required = bool(
            active_abox_is_scoped_manifest
            and active_persistence_mode != desired_persistence_mode
        )
        persistence_graph.worldview["persistenceMode"] = desired_persistence_mode
        persistence_graph.worldview["physicalStateMode"] = desired_persistence_mode
        persistence_graph.worldview["currentStateMigrationMode"] = (
            current_state_migration_mode
        )
        emit_progress("abox_validation.start")
        validation_started = time.perf_counter()
        validation = validate_ontology(persistence_graph)
        runtime_stages["aboxValidationMs"] = int(
            (time.perf_counter() - validation_started) * 1000
        )
        emit_progress(
            "abox_validation.done",
            status=validation.status,
            errorCount=validation.error_count,
            runtimeMs=runtime_stages["aboxValidationMs"],
        )
        if validation.error_count:
            result = {
                "saved": False,
                "status": "invalid-abox",
                "reason": "ABox validation failed before graph-store persistence.",
                "graphStore": _store.active_graph_store_key(),
                "aboxValidation": validation.to_dict(),
                "graphInput": graph_input,
            }
            _store.store_projection_result(snapshot, result, projection_run)
            return result
        # Preserve the exact incremental path or safe fallback in the
        # manifest, so operational diagnostics do not infer it later.
        persistence_graph.worldview["targetScopedManifestPatch"] = dict(
            target_scoped_patch
        )
        persistence_graph.worldview["factSlotProjection"] = {
            "status": str(target_scoped_patch.get("factSlotStatus") or "not-applied"),
            "selectedScopeCount": int(
                target_scoped_patch.get("factSlotSelectedScopeCount") or 0
            ),
            "deferredScopeCount": int(
                target_scoped_patch.get("factSlotDeferredScopeCount") or 0
            ),
            "slotFamilies": list(target_scoped_patch.get("factSlotFamilies") or [])[
                :20
            ],
            "slotFamiliesBySymbol": dict(
                target_scoped_patch.get("factSlotFamiliesBySymbol") or {}
            ),
            "changedFieldsBySymbol": dict(
                target_scoped_patch.get("factSlotChangedFieldsBySymbol") or {}
            ),
            "preciseFieldRoutingSymbols": list(
                target_scoped_patch.get("factSlotPreciseFieldRoutingSymbols") or []
            )[:20],
            "unclassifiedChangedFieldsBySymbol": dict(
                target_scoped_patch.get("factSlotUnclassifiedChangedFieldsBySymbol")
                or {}
            ),
            "fallbackReason": str(
                target_scoped_patch.get("factSlotFallbackReason") or ""
            ),
        }
        if str(target_scoped_patch.get("status") or "") == "applied":
            full_reconcile_at = str(
                active_abox.get("lastFullScopeReconcileAt")
                or active_abox.get("asOf")
                or ""
            ).strip()
        else:
            full_reconcile_at = str(
                getattr(snapshot, "generated_at", "")
                or persistence_graph.worldview.get("asOf")
                or ""
            ).strip()
        if full_reconcile_at:
            persistence_graph.worldview["lastFullScopeReconcileAt"] = full_reconcile_at
        # A rolling deployment can encounter an already active immutable
        # ABox that predates the exact physical evidence-read index. The
        # index is marker metadata derived from this same verified graph;
        # it does not alter market facts or native rule semantics.
        if (
            active_abox_complete
            and active_abox_is_scoped_manifest
            and active_material_fingerprint(active_abox) == material_fingerprint
            and not physical_state_migration_required
        ):
            upgrader = getattr(
                _store.repository, "ensure_scoped_manifest_evidence_read_index", None
            )
            if callable(upgrader):
                index_upgrade_started = time.perf_counter()
                try:
                    evidence_index_upgrade = _store.repository_world_call(
                        "ensure_scoped_manifest_evidence_read_index",
                        persistence_graph,
                        active_metadata=active_abox,
                        world_id=portfolio_world_context.world_id,
                    )
                except (
                    Exception
                ) as error:  # noqa: BLE001 - do not run a new judgement without exact current evidence.
                    evidence_index_upgrade = {
                        "configured": True,
                        "saved": False,
                        "status": "error",
                        "reason": str(error)[:180],
                    }
                runtime_stages["manifestEvidenceIndexUpgradeMs"] = int(
                    (time.perf_counter() - index_upgrade_started) * 1000
                )
                upgrade_status = str(evidence_index_upgrade.get("status") or "")
                if upgrade_status in {"ok", "unchanged"}:
                    active_abox = _store.active_abox_metadata(
                        portfolio_world_context.world_id
                    )
                else:
                    result = {
                        "saved": False,
                        "status": "manifest-evidence-index-upgrade-pending",
                        "reason": (
                            "현재 ABox의 근거 조회 인덱스를 안전하게 보강하지 못해 새 투자 판단을 보류했습니다. "
                            + str(
                                evidence_index_upgrade.get("reason") or upgrade_status
                            )[:180]
                        ),
                        "graphStore": _store.active_graph_store_key(),
                        "materialFingerprint": material_fingerprint,
                        "aboxSnapshotId": str(
                            active_abox.get("aboxSnapshotId") or material_snapshot_id
                        ),
                        "preservedActiveGeneration": True,
                        "materialChangeDetected": False,
                        "aboxValidation": validation.to_dict(),
                        "manifestEvidenceIndexUpgrade": evidence_index_upgrade,
                        "runtimeStages": runtime_stages,
                        "ontologyWorld": world_metadata(portfolio_world_context),
                    }
                    _store.store_projection_result(snapshot, result)
                    return result
        emit_progress("impact_planning.start")
        impact_planning_started = time.perf_counter()
        inference_impact_plan = _store.inference_impact_plan(
            snapshot,
            active_abox,
            scoped_identity,
            target_symbols,
            reasoning_context=compact_reasoning_context,
        )
        compact_impact_plan = compact_inference_impact_plan(inference_impact_plan)
        world_impact_route = route_world_impact(
            {
                **compact_impact_plan,
                "portfolioWorldId": portfolio_world_context.world_id,
                "sharedPremiseWorldId": shared_premise_world(
                    portfolio_world_context.market_id,
                    _store.settings.get("ontologySharedMarketTenantId") or "shared",
                ).world_id,
            },
            initial_projection=not bool(active_abox.get("aboxSnapshotId")),
        )
        explicit_inference_symbols = (
            _store.inference_symbols(snapshot, target_symbols) if target_symbols else []
        )
        inference_symbols = explicit_inference_symbols or _store.inference_symbols(
            snapshot,
            inference_impact_plan.get("inferenceTargetSymbols") or target_symbols,
        )
        scheduler_target_limit = _store.scheduler_target_symbol_limit(
            compact_reasoning_context
        )
        inference_symbols = _store.bounded_native_inference_symbols(
            snapshot,
            inference_symbols,
            target_symbols,
            scheduler_target_symbol_limit=scheduler_target_limit,
        )
        runtime_stages["impactPlanningMs"] = int(
            (time.perf_counter() - impact_planning_started) * 1000
        )
        emit_progress(
            "impact_planning.done",
            targetSymbolCount=len(inference_symbols or []),
            runtimeMs=runtime_stages["impactPlanningMs"],
            worldPartitions={
                key: value
                for key, value in dict(
                    world_impact_route.get("partitions") or {}
                ).items()
                if key != "version"
            },
            durableHandoffWorkItemCount=len(
                (world_impact_route.get("durableHandoff") or {}).get("workItems", [])
            ),
        )
        persistence_graph.worldview["scopeDelta"] = dict(
            compact_impact_plan.get("scopeDelta") or {}
        )
        persistence_graph.worldview["inferenceImpactPlan"] = compact_impact_plan
        persistence_graph.worldview["worldImpactRoute"] = world_impact_route
        projection_scope = {
            "triggerMode": "scope-change-impact-native",
            "targetSymbols": list(inference_symbols),
            "schedulerTargetSymbolLimit": scheduler_target_limit,
            "explicitTargetSymbols": list(
                compact_impact_plan.get("explicitTargetSymbols") or []
            ),
            "persistenceMode": desired_persistence_mode,
            "physicalStateMigrationRequired": physical_state_migration_required,
            "currentStateMigrationMode": current_state_migration_mode,
            "atomicActivation": True,
            "manifestId": material_snapshot_id,
            "worldId": portfolio_world_context.world_id,
            "marketWorldId": market_world_context.world_id,
            "scopeCount": len(scoped_identity.get("scopePlan") or []),
            "scopeFamilyCounts": dict(scoped_identity.get("scopeFamilyCounts") or {}),
            "scopeTopologyVersion": str(
                persistence_graph.worldview.get("scopeTopologyVersion") or ""
            ),
            "targetScopedManifestPatch": dict(target_scoped_patch or {}),
            "graphInput": dict(graph_input),
            "inferenceImpactPlan": compact_impact_plan,
            "worldImpactRoute": world_impact_route,
            "reasoningContext": compact_reasoning_context,
            "reason": (
                "변경된 사실군과 ABox 의존 관계에서 재평가 대상을 계산하고, 변경 범위만 새 세대로 기록한 뒤 "
                "대상별 TypeDB 네이티브 규칙을 완전 평가합니다."
            ),
        }
        comparison_scope = target_scope_manifest_fingerprint(
            source_scope_plan,
            inference_symbols,
        )
        persisted_comparison_scope = target_scope_manifest_fingerprint(
            scoped_identity.get("scopePlan") or [],
            inference_symbols,
        )
        # Identical facts must still be persisted once when upgrading from
        # the legacy complete-generation pointer. Otherwise a quiet market
        # could leave the old full-rewrite ABox active indefinitely.
        if (
            active_abox_complete
            and active_abox_is_scoped_manifest
            and active_material_fingerprint(active_abox) == material_fingerprint
            and not physical_state_migration_required
        ):
            inferencebox = _store.existing_inference_result(
                snapshot,
                inference_symbols,
                world_id=portfolio_world_context.world_id,
            )
            result = {
                "saved": False,
                "status": (
                    "unchanged-material-facts"
                    if _store.inference_result_is_reusable(
                        inferencebox,
                        active_abox,
                        inference_symbols,
                    )
                    else "unchanged-material-facts-reasoning-retry"
                ),
                "reason": (
                    "가격·손익·수급·뉴스·신선도 등 추론 입력이 직전 ABox와 같습니다."
                    if _store.inference_result_is_reusable(
                        inferencebox,
                        active_abox,
                        inference_symbols,
                    )
                    else "ABox 입력은 같지만 정상적으로 정렬된 InferenceBox가 없어 추론을 다시 실행합니다."
                ),
                "graphStore": _store.active_graph_store_key(),
                "materialFingerprint": material_fingerprint,
                "aboxSnapshotId": str(
                    active_abox.get("aboxSnapshotId") or material_snapshot_id
                ),
                "preservedActiveGeneration": True,
                "materialChangeDetected": False,
                "aboxValidation": validation.to_dict(),
                "projectionScope": projection_scope,
                "comparisonScope": comparison_scope,
                "persistedComparisonScope": persisted_comparison_scope,
                "inferenceImpactPlan": compact_impact_plan,
                "reasoningContext": compact_reasoning_context,
                "runtimeStages": runtime_stages,
                "ontologyWorld": world_metadata(portfolio_world_context),
                "marketWorld": {
                    **world_metadata(market_world_context),
                    "status": "unchanged-source-not-reprojected",
                },
            }
            if rulebox_bootstrap:
                result["ruleboxBootstrap"] = rulebox_bootstrap
            if evidence_index_upgrade:
                result["manifestEvidenceIndexUpgrade"] = evidence_index_upgrade
            if pending_activation_recovery:
                result["pendingAboxActivationRecovery"] = pending_activation_recovery
            if _store.inference_result_is_reusable(
                inferencebox,
                active_abox,
                inference_symbols,
            ):
                inferencebox["reusedForUnchangedMaterialFacts"] = True
                result["inferenceBox"] = inferencebox
            else:
                result["reasoningRetryRequired"] = True
                result["previousInferenceStatus"] = str(
                    inferencebox.get("status") or "missing"
                )
                _store.attach_graph_store_inference_result(
                    result,
                    snapshot,
                    inference_symbols,
                    compact_impact_plan,
                    world_id=portfolio_world_context.world_id,
                    candidate_scope_plan=active_abox.get("scopePlan")
                    or scoped_identity.get("scopePlan")
                    or [],
                    rulebox_rules_hash=str(
                        rulebox_bootstrap.get("ruleboxRulesHash") or ""
                    ),
                    tbox_fingerprint=str(
                        (
                            (persistence_graph.worldview or {}).get("activeTBox") or {}
                        ).get("fingerprint")
                        or ""
                    ),
                    preflight_graph=_store.native_preflight_projection_graph(
                        persistence_graph,
                        active_abox,
                    ),
                    preflight_manifest_id=str(
                        (persistence_graph.worldview or {}).get("worldviewManifestId")
                        or material_snapshot_id
                    ),
                )
            _store.store_projection_result(snapshot, result)
            return result
        projection_audit_started = time.perf_counter()
        projection_run, audit_error = _store.begin_projection_audit_run(
            snapshot,
            persistence_graph,
            material_fingerprint,
            material_snapshot_id,
            inference_symbols=inference_symbols,
            rulebox_metadata=rulebox_bootstrap,
            reasoning_context=compact_reasoning_context,
        )
        runtime_stages["projectionAuditCreateMs"] = int(
            (time.perf_counter() - projection_audit_started) * 1000
        )
        if audit_error:
            result = {
                "saved": False,
                "status": "source-audit-failed",
                "reason": "MySQL source audit must succeed before the active ABox can change: "
                + audit_error,
                "graphStore": _store.active_graph_store_key(),
                "materialFingerprint": material_fingerprint,
                "aboxSnapshotId": material_snapshot_id,
                "materialChangeDetected": True,
                "preservedActiveGeneration": True,
                "aboxValidation": validation.to_dict(),
            }
            _store.store_projection_result(snapshot, result)
            return result
        current_state_transition = {}
        current_state_recovery = {}
        begin_current_state_transition = (
            getattr(
                _store.projection_run_store,
                "begin_current_state_transition",
                None,
            )
            if _store.projection_run_store
            else None
        )
        if (
            projection_run
            and desired_persistence_mode == CURRENT_STATE_ABOX_PERSISTENCE_MODE
            and callable(begin_current_state_transition)
        ):
            recover_committed = getattr(
                _store.projection_run_store,
                "recover_committed_current_state_transitions",
                None,
            )
            if callable(recover_committed):
                current_state_recovery_started = time.perf_counter()
                try:
                    current_state_recovery = dict(
                        recover_committed(
                            world_id=projection_run.world_id,
                            limit=10,
                        )
                        or {}
                    )
                except TypeError:
                    current_state_recovery = dict(
                        recover_committed(projection_run.world_id, 10) or {}
                    )
                except (
                    Exception
                ) as error:  # noqa: BLE001 - a new audited transition can continue.
                    current_state_recovery = {
                        "status": "error",
                        "reason": str(error)[:180],
                    }
                runtime_stages["currentStateRecoveryMs"] = int(
                    (time.perf_counter() - current_state_recovery_started) * 1000
                )
            else:
                current_state_recovery = {"status": "unsupported"}
                runtime_stages["currentStateRecoveryMs"] = 0
            current_state_transition_started = time.perf_counter()
            try:
                current_state_transition = dict(
                    begin_current_state_transition(
                        projection_run,
                        str(
                            active_abox.get("worldviewManifestId")
                            or active_abox.get("aboxSnapshotId")
                            or ""
                        ),
                        material_snapshot_id,
                        material_fingerprint,
                        desired_persistence_mode,
                        detail={
                            "targetSymbols": list(inference_symbols),
                            "scopeCount": len(scoped_identity.get("scopePlan") or []),
                            "targetScopedPatch": dict(target_scoped_patch or {}),
                        },
                    )
                    or {}
                )
            except Exception as error:  # noqa: BLE001 - no unaudited physical mutation.
                current_state_transition = {
                    "status": "error",
                    "reason": str(error)[:180],
                }
            runtime_stages["currentStateTransitionAuditMs"] = int(
                (time.perf_counter() - current_state_transition_started) * 1000
            )
            if str(current_state_transition.get("status") or "") != "ok":
                result = {
                    "saved": False,
                    "status": "current-state-transition-audit-failed",
                    "reason": str(
                        current_state_transition.get("reason")
                        or "Current-state transition checkpoint could not be stored."
                    )[:220],
                    "graphStore": _store.active_graph_store_key(),
                    "materialFingerprint": material_fingerprint,
                    "aboxSnapshotId": material_snapshot_id,
                    "preservedActiveGeneration": True,
                    "currentStateTransition": current_state_transition,
                }
                _store.store_projection_result(snapshot, result, projection_run)
                return result
        # Target subjects do not change the material ABox identity. They
        # are persisted only in the activation journal so a restart can
        # verify that the eventual native InferenceBox covered the exact
        # requested incremental scope before predecessor cleanup.
        persistence_graph.worldview["inferenceTargetSymbols"] = list(inference_symbols)
        coordinator_acquire_started = time.perf_counter()
        coordinator_lease = _store.acquire_projection_coordinator_lease(
            "portfolio:" + material_snapshot_id,
            portfolio_world_context.world_id,
        )
        runtime_stages["projectionCoordinatorAcquireMs"] = int(
            (time.perf_counter() - coordinator_acquire_started) * 1000
        )
        if not bool(coordinator_lease.get("acquired")):
            result = {
                "saved": False,
                "status": "deferred-projection-coordinator",
                "reason": str(
                    coordinator_lease.get("reason")
                    or "다른 World 투영이 TypeDB 데이터베이스 쓰기 경계를 사용 중입니다."
                )[:220],
                "retryable": True,
                "recommendedRetryAfterSeconds": int(
                    coordinator_lease.get("recommendedRetryAfterSeconds") or 10
                ),
                "preservedActiveGeneration": True,
                "materialFingerprint": material_fingerprint,
                "aboxSnapshotId": material_snapshot_id,
                "projectionScope": projection_scope,
                "inferenceImpactPlan": compact_impact_plan,
                "reasoningContext": compact_reasoning_context,
                "aboxValidation": validation.to_dict(),
                "runtimeStages": runtime_stages,
                "ontologyWorld": world_metadata(portfolio_world_context),
                "projectionCoordinator": _store.projection_coordinator_summary(
                    coordinator_lease
                ),
            }
            _store.store_projection_result(snapshot, result, projection_run)
            return result
        result: Dict[str, object] = {}
        coordinator_release = {}
        try:
            runtime_stages["persistencePreflightMs"] = int(
                runtime_stages.get("projectionAuditCreateMs", 0)
                + runtime_stages.get("currentStateRecoveryMs", 0)
                + runtime_stages.get("currentStateTransitionAuditMs", 0)
                + runtime_stages.get("projectionCoordinatorAcquireMs", 0)
            )
            emit_progress(
                "abox_persistence.start",
                targetSymbolCount=len(inference_symbols or []),
                inputMode=str(graph_input.get("mode") or "full"),
                preflightRuntimeMs=runtime_stages["persistencePreflightMs"],
            )
            abox_persistence_started = time.perf_counter()
            result = _store.repository.save_graph(persistence_graph)
            runtime_stages["aboxPersistenceMs"] = int(
                (time.perf_counter() - abox_persistence_started) * 1000
            )
            if not isinstance(result, dict):
                result = {
                    "saved": False,
                    "status": "error",
                    "reason": "ontology repository returned non-dict result",
                }
            if projection_run:
                result["projectionRunId"] = projection_run.run_id
            if current_state_transition:
                result["currentStateTransition"] = current_state_transition
                result["currentStateRecovery"] = current_state_recovery
            _store.attach_abox_persistence_runtime_stages(runtime_stages, result)
            result["projectionMode"] = "abox-facts-only-typedb-native-rules"
            result["materialFingerprint"] = material_fingerprint
            result["aboxSnapshotId"] = material_snapshot_id
            result["nativeRulePlannerTopology"] = dict(
                persistence_graph.worldview.get("nativeRulePlannerTopology") or {}
            )
            result["materialChangeDetected"] = True
            result["projectionScope"] = projection_scope
            result["comparisonScope"] = comparison_scope
            result["persistedComparisonScope"] = persisted_comparison_scope
            result["graphInput"] = dict(graph_input)
            result["inferenceImpactPlan"] = compact_impact_plan
            result["reasoningContext"] = compact_reasoning_context
            result["aboxValidation"] = validation.to_dict()
            result["runtimeStages"] = runtime_stages
            result["ontologyWorld"] = world_metadata(portfolio_world_context)
            result["_projectionCoordinatorLease"] = coordinator_lease
            if rulebox_bootstrap:
                result["ruleboxBootstrap"] = rulebox_bootstrap
            if pending_activation_recovery:
                result["pendingAboxActivationRecovery"] = pending_activation_recovery
            save_status = str(result.get("status") or "")
            emit_progress(
                "abox_persistence.done",
                status=save_status,
                saved=bool(result.get("saved")),
                runtimeMs=runtime_stages["aboxPersistenceMs"],
                failedScopes=list(result.get("failedScopes") or [])[:12],
                rebindOnlyRelationScopeIds=list(
                    result.get("rebindOnlyRelationScopeIds") or []
                )[:24],
                currentFallbackRelationScopeIds=list(
                    result.get("currentFallbackRelationScopeIds") or []
                )[:24],
                candidateSemanticReconciliationFailure=dict(
                    result.get("candidateSemanticReconciliationFailure") or {}
                ),
                reason=str(result.get("reason") or "")[:220],
            )
            # Candidate ABox writes have committed at this point and the
            # pending activation journal protects this world.  Do not
            # hold the database-wide writer coordinator while TypeDB
            # prepares read-side native rule candidates.  The inference
            # materialization claims its own short coordinator scope.
            # This lets an unrelated world stage its next bounded patch
            # instead of waiting behind a whole account inference cycle.
            if bool(coordinator_lease.get("acquired")):
                early_coordinator_release = _store.release_projection_coordinator_lease(
                    coordinator_lease
                )
                result["projectionCoordinatorPersistenceRelease"] = (
                    early_coordinator_release
                )
                result.pop("_projectionCoordinatorLease", None)
                coordinator_lease = {
                    **coordinator_lease,
                    "acquired": False,
                    "status": "released-after-abox-persistence",
                }
            if result.get("saved") or save_status == "staged-scoped-manifest":
                if projection_run and current_state_transition:
                    result["currentStatePatchCheckpoint"] = (
                        _store.advance_current_state_transition(
                            projection_run,
                            "patch-applied",
                            detail={
                                "saved": bool(result.get("saved")),
                                "changedScopeIds": list(
                                    result.get("changedScopeIds") or []
                                ),
                                "entityCount": int(result.get("entityCount") or 0),
                                "relationCount": int(result.get("relationCount") or 0),
                            },
                        )
                    )
                pending = (
                    result.get("pendingAboxActivation")
                    if isinstance(result.get("pendingAboxActivation"), dict)
                    else {}
                )
                emit_progress(
                    "native_inference.start",
                    targetSymbolCount=len(
                        pending.get("targetSymbols") or inference_symbols or []
                    ),
                )
                _store.attach_graph_store_inference_result(
                    result,
                    snapshot,
                    pending.get("targetSymbols") or inference_symbols,
                    compact_impact_plan,
                    world_id=portfolio_world_context.world_id,
                    candidate_scope_plan=(
                        result.get("scopePlan")
                        or scoped_identity.get("scopePlan")
                        or []
                    ),
                    rulebox_rules_hash=str(
                        rulebox_bootstrap.get("ruleboxRulesHash") or ""
                    ),
                    tbox_fingerprint=str(
                        (
                            (persistence_graph.worldview or {}).get("activeTBox") or {}
                        ).get("fingerprint")
                        or ""
                    ),
                    preflight_graph=_store.native_preflight_projection_graph(
                        persistence_graph,
                        result,
                    ),
                    preflight_manifest_id=str(
                        (persistence_graph.worldview or {}).get("worldviewManifestId")
                        or material_snapshot_id
                    ),
                )
                emit_progress(
                    "native_inference.done",
                    status=str(
                        ((result.get("inferenceBox") or {}).get("status"))
                        if isinstance(result.get("inferenceBox"), dict)
                        else result.get("status") or ""
                    ),
                    runtimeMs=int(
                        (result.get("runtimeStages") or {}).get("nativeInferenceMs")
                        or 0
                    ),
                )
                inference_payload = (
                    dict(result.get("inferenceBox") or {})
                    if isinstance(result.get("inferenceBox"), dict)
                    else {}
                )
                if projection_run and current_state_transition:
                    result["currentStateInferenceCheckpoint"] = (
                        _store.advance_current_state_transition(
                            projection_run,
                            "inferred",
                            inference_generation_id=str(
                                inference_payload.get("inferenceGenerationId") or ""
                            ),
                            detail={
                                "inferenceStatus": str(
                                    inference_payload.get("status") or ""
                                ),
                                "nativeCompleted": bool(
                                    inference_payload.get(
                                        "nativeTypeDbReasoningCompleted"
                                    )
                                    or inference_payload.get(
                                        "typedbNativeRuleEvaluationCompleted"
                                    )
                                ),
                            },
                        )
                    )
            elif save_status == "deferred-pending-scoped-manifest":
                # This input did not stage the pending candidate. Running
                # native rules with its graph would compare a new Manifest
                # to another writer's journal and create a false rollback.
                result["retryable"] = True
                result["recommendedRetryAfterSeconds"] = int(
                    result.get("recommendedRetryAfterSeconds") or 10
                )
                result["pendingManifestOwner"] = "another-projection"
        finally:
            coordinator_release = _store.release_projection_coordinator_lease(
                coordinator_lease
            )
            if isinstance(result, dict):
                result.pop("_projectionCoordinatorLease", None)
                result["projectionCoordinator"] = _store.projection_coordinator_summary(
                    coordinator_lease
                )
                result["projectionCoordinatorRelease"] = coordinator_release
        market_projection_started = time.perf_counter()
        result["worldImpactRoute"] = world_impact_route
        if bool(result.get("saved")) and bool(
            world_impact_route.get("market", {}).get("required")
        ):
            # MarketWorld is an account-independent derived mirror.  It
            # is intentionally scheduled only after the portfolio ABox
            # and its decision-critical TypeDB inference are verified.
            # This keeps a slow shared write out of the alert path while
            # never letting an unverified account projection publish
            # facts to the shared world.
            result["marketWorld"] = _store.schedule_market_world_projection(
                graph,
                market_world_context,
                source_world=portfolio_world_context,
            )
        elif bool(result.get("saved")):
            result["marketWorld"] = {
                **world_metadata(market_world_context),
                "status": "skipped-world-impact-route",
                "preservedActiveGeneration": True,
                "reason": str(world_impact_route.get("market", {}).get("reason") or ""),
            }
        if bool(result.get("saved")) and bool(
            world_impact_route.get("knowledge", {}).get("required")
        ):
            result["knowledgeWorld"] = _store.schedule_knowledge_world_projection(
                graph,
                knowledge_world_context,
                source_world=portfolio_world_context,
            )
        elif bool(result.get("saved")):
            result["knowledgeWorld"] = {
                **world_metadata(knowledge_world_context),
                "status": "skipped-world-impact-route",
                "preservedActiveGeneration": True,
                "reason": str(
                    world_impact_route.get("knowledge", {}).get("reason") or ""
                ),
            }
        else:
            result["marketWorld"] = {
                **world_metadata(market_world_context),
                "status": "deferred-portfolio-inference-not-verified",
                "preservedActiveGeneration": True,
                "reason": "계좌 ABox 또는 TypeDB 추론이 확정되지 않아 공용 시장 읽기 모델 갱신을 건너뛰었습니다.",
            }
            result["knowledgeWorld"] = {
                **world_metadata(knowledge_world_context),
                "status": "deferred-portfolio-inference-not-verified",
                "preservedActiveGeneration": True,
                "reason": "계좌 ABox 또는 TypeDB 추론이 확정되지 않아 공용 지식 세계 갱신을 건너뛰었습니다.",
            }
        runtime_stages["marketWorldQueueMs"] = int(
            (time.perf_counter() - market_projection_started) * 1000
        )
        if _store.quality_store:
            quality_started = time.perf_counter()
            if _store.async_quality_record_enabled():
                result["qualityRecord"] = (
                    _bindings.SHARED_ONTOLOGY_QUALITY_RECORD_COORDINATOR.enqueue(
                        _store.quality_store,
                        graph,
                        _store.source,
                    )
                )
                runtime_stages["qualityRecordQueueMs"] = int(
                    (time.perf_counter() - quality_started) * 1000
                )
            else:
                sample = _store.quality_store.record_graph(graph, source=_store.source)
                runtime_stages["qualityRecordMs"] = int(
                    (time.perf_counter() - quality_started) * 1000
                )
                result["qualitySampleId"] = getattr(sample, "sample_id", "")
                result["qualityState"] = getattr(
                    sample, "overall_state", ""
                ) or getattr(sample, "overall_score", "")
    except (
        Exception
    ) as error:  # noqa: BLE001 - ontology projection must not block realtime monitoring.
        result = {
            "saved": False,
            "status": "error",
            "reason": str(error)[:180],
            "errorType": type(error).__name__,
            "failureStage": current_stage,
            "errorTrace": traceback.format_exc()[-2000:],
        }
        emit_progress("error", status="error", reason=str(error)[:180])
    runtime_stages["totalMs"] = int((time.perf_counter() - projection_started) * 1000)
    result.setdefault("runtimeStages", runtime_stages)
    result.setdefault(
        "performanceAssessment",
        ontology_performance_assessment(
            runtime_stages,
            (
                _store.settings.get("ontologyPerformanceBudgetsMs")
                if isinstance(_store.settings.get("ontologyPerformanceBudgetsMs"), dict)
                else None
            ),
        ),
    )
    _store.store_projection_result(snapshot, result, projection_run)
    emit_progress(
        "completed",
        status=str(result.get("status") or ""),
        saved=bool(result.get("saved")),
        runtimeMs=runtime_stages["totalMs"],
    )
    return result
