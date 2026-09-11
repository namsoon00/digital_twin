"""Publish and infer under the existing write lease and recovery contract."""

from __future__ import annotations
from typing import Optional, Union
from digital_twin.modules.reasoning.domain.ontology_worlds import world_metadata
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from typing import Callable, Dict, List
import time


from .stage_results import CompletedProjection, PublishCandidateResult
from .publish_candidate_ports import PublishCandidatePort
from digital_twin.modules.reasoning.domain.ontology_worlds import OntologyWorld
from digital_twin.modules.reasoning.domain.ontology_validator import OntologyValidationReport
from digital_twin.modules.reasoning.domain.ontology_projection_audit import OntologyProjectionRun
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology


def publish_candidate(
    _store: PublishCandidatePort,
    compact_impact_plan: Dict[str, object],
    compact_reasoning_context: Dict[str, object],
    comparison_scope: Dict[str, object],
    current_state_recovery: Dict[str, object],
    current_state_transition: Dict[str, object],
    emit_progress: Callable[..., None],
    graph_input: Dict[str, object],
    inference_symbols: List[str],
    material_fingerprint: str,
    material_snapshot_id: str,
    pending_activation_recovery: Dict[str, object],
    persisted_comparison_scope: Dict[str, object],
    persistence_graph: PortfolioOntology,
    portfolio_world_context: OntologyWorld,
    projection_run: Optional[OntologyProjectionRun],
    projection_scope: Dict[str, object],
    rulebox_bootstrap: Dict[str, object],
    runtime_stages: Dict[str, int],
    scoped_identity: Dict[str, object],
    snapshot: AccountSnapshot,
    validation: OntologyValidationReport,
) -> Union[PublishCandidateResult, CompletedProjection]:
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
        return CompletedProjection(result)
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
                    result.get("scopePlan") or scoped_identity.get("scopePlan") or []
                ),
                rulebox_rules_hash=str(rulebox_bootstrap.get("ruleboxRulesHash") or ""),
                tbox_fingerprint=str(
                    ((persistence_graph.worldview or {}).get("activeTBox") or {}).get(
                        "fingerprint"
                    )
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
                    (result.get("runtimeStages") or {}).get("nativeInferenceMs") or 0
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
                                inference_payload.get("nativeTypeDbReasoningCompleted")
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

    return PublishCandidateResult(
        result=result,
    )
