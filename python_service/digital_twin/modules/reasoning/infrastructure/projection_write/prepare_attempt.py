"""Resolve source worlds, eligibility and pending activation recovery."""

from __future__ import annotations
from typing import Optional, Union
from digital_twin.domain.ontology_projection_audit import (
    compact_reasoning_request_context,
)
from digital_twin.domain.ontology_projection_status import (
    TYPEDB_REASONING_WORKER_DEFERRED,
)
from digital_twin.domain.ontology_worlds import (
    knowledge_world,
    market_world,
    world_from_snapshot,
    world_metadata,
)
from digital_twin.domain.portfolio import AccountSnapshot
from typing import Callable, Dict, List
import time


from .stage_results import CompletedProjection, PrepareAttemptResult
from .prepare_attempt_ports import PrepareAttemptPort
from digital_twin.domain.ontology_projection_audit import OntologyProjectionRun


def prepare_attempt(
    _store: PrepareAttemptPort,
    emit_progress: Callable[..., None],
    projection_run: Optional[OntologyProjectionRun],
    projection_started: float,
    reasoning_context: Dict[str, object],
    runtime_stages: Dict[str, int],
    snapshot: AccountSnapshot,
    target_symbols: List[str],
) -> Union[PrepareAttemptResult, CompletedProjection]:
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
        return CompletedProjection({})
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
        return CompletedProjection(result)
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
            return CompletedProjection(result)
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
        return CompletedProjection(result)
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
        return CompletedProjection(result)
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
        return CompletedProjection(result)
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
        return CompletedProjection(result)
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

    return PrepareAttemptResult(
        compact_reasoning_context=compact_reasoning_context,
        fresh_candidate_rebuild=fresh_candidate_rebuild,
        knowledge_world_context=knowledge_world_context,
        market_world_context=market_world_context,
        pending_activation_recovery=pending_activation_recovery,
        portfolio_world_context=portfolio_world_context,
        shared_premise_proof=shared_premise_proof,
    )
