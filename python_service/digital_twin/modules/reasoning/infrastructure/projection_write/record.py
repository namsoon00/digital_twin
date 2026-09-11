"""Synchronous projection coordinator with explicit phase and audit handoffs."""

from __future__ import annotations
from .record_ports import RecordPort, RecordSnapshotBindings
from digital_twin.domain.ontology_performance_contract import (
    ontology_performance_assessment,
)
from digital_twin.domain.portfolio import AccountSnapshot
from typing import Callable, Dict, List
import time
import traceback


from .stage_results import CompletedProjection
from .prepare_attempt import prepare_attempt
from .assemble_source import assemble_source
from .select_source import select_source
from .patch_manifest import patch_manifest
from .validate_manifest import validate_manifest
from .plan_inference import plan_inference
from .reuse_inference import reuse_inference
from .create_audit import create_audit
from .begin_publication import begin_publication
from .publish_candidate import publish_candidate
from .schedule_followups import schedule_followups

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
    stage_result = prepare_attempt(
        _store=_store,
        emit_progress=emit_progress,
        projection_run=projection_run,
        projection_started=projection_started,
        reasoning_context=reasoning_context,
        runtime_stages=runtime_stages,
        snapshot=snapshot,
        target_symbols=target_symbols,
    )
    if isinstance(stage_result, CompletedProjection):
        return stage_result.result
    compact_reasoning_context = stage_result.compact_reasoning_context
    fresh_candidate_rebuild = stage_result.fresh_candidate_rebuild
    knowledge_world_context = stage_result.knowledge_world_context
    market_world_context = stage_result.market_world_context
    pending_activation_recovery = stage_result.pending_activation_recovery
    portfolio_world_context = stage_result.portfolio_world_context
    shared_premise_proof = stage_result.shared_premise_proof
    try:
        stage_result = assemble_source(
            _store=_store,
            compact_reasoning_context=compact_reasoning_context,
            emit_progress=emit_progress,
            market_world_context=market_world_context,
            portfolio_world_context=portfolio_world_context,
            projection_run=projection_run,
            runtime_stages=runtime_stages,
            shared_premise_proof=shared_premise_proof,
            snapshot=snapshot,
            target_symbols=target_symbols,
        )
        if isinstance(stage_result, CompletedProjection):
            return stage_result.result
        graph = stage_result.graph
        graph_input = stage_result.graph_input
        material_fingerprint = stage_result.material_fingerprint
        material_snapshot_id = stage_result.material_snapshot_id
        observation_followup_targets = stage_result.observation_followup_targets
        persistence_graph = stage_result.persistence_graph
        planner_topology = stage_result.planner_topology
        projection_graph = stage_result.projection_graph
        rulebox_bootstrap = stage_result.rulebox_bootstrap
        scoped_identity = stage_result.scoped_identity
        stage_result = select_source(
            _store=_store,
            compact_reasoning_context=compact_reasoning_context,
            emit_progress=emit_progress,
            fresh_candidate_rebuild=fresh_candidate_rebuild,
            graph=graph,
            graph_input=graph_input,
            market_world_context=market_world_context,
            material_fingerprint=material_fingerprint,
            material_snapshot_id=material_snapshot_id,
            persistence_graph=persistence_graph,
            planner_topology=planner_topology,
            portfolio_world_context=portfolio_world_context,
            projection_graph=projection_graph,
            rulebox_bootstrap=rulebox_bootstrap,
            runtime_stages=runtime_stages,
            scoped_identity=scoped_identity,
            shared_premise_proof=shared_premise_proof,
            snapshot=snapshot,
            target_symbols=target_symbols,
        )
        if isinstance(stage_result, CompletedProjection):
            return stage_result.result
        active_abox = stage_result.active_abox
        active_abox_complete = stage_result.active_abox_complete
        active_abox_is_scoped_manifest = stage_result.active_abox_is_scoped_manifest
        evidence_index_upgrade = stage_result.evidence_index_upgrade
        graph = stage_result.graph
        material_fingerprint = stage_result.material_fingerprint
        material_snapshot_id = stage_result.material_snapshot_id
        persistence_graph = stage_result.persistence_graph
        planner_topology = stage_result.planner_topology
        scoped_identity = stage_result.scoped_identity
        source_scope_plan = stage_result.source_scope_plan
        target_scoped_patch = stage_result.target_scoped_patch
        stage_result = patch_manifest(
            _store=_store,
            _bindings=_bindings,
            active_abox=active_abox,
            compact_reasoning_context=compact_reasoning_context,
            emit_progress=emit_progress,
            graph=graph,
            graph_input=graph_input,
            market_world_context=market_world_context,
            material_fingerprint=material_fingerprint,
            material_snapshot_id=material_snapshot_id,
            observation_followup_targets=observation_followup_targets,
            persistence_graph=persistence_graph,
            planner_topology=planner_topology,
            portfolio_world_context=portfolio_world_context,
            projection_run=projection_run,
            rulebox_bootstrap=rulebox_bootstrap,
            runtime_stages=runtime_stages,
            scoped_identity=scoped_identity,
            shared_premise_proof=shared_premise_proof,
            snapshot=snapshot,
            target_scoped_patch=target_scoped_patch,
            target_symbols=target_symbols,
        )
        if isinstance(stage_result, CompletedProjection):
            return stage_result.result
        graph = stage_result.graph
        material_fingerprint = stage_result.material_fingerprint
        material_snapshot_id = stage_result.material_snapshot_id
        persistence_graph = stage_result.persistence_graph
        scoped_identity = stage_result.scoped_identity
        target_scoped_patch = stage_result.target_scoped_patch
        stage_result = validate_manifest(
            _store=_store,
            active_abox=active_abox,
            active_abox_complete=active_abox_complete,
            active_abox_is_scoped_manifest=active_abox_is_scoped_manifest,
            emit_progress=emit_progress,
            evidence_index_upgrade=evidence_index_upgrade,
            graph_input=graph_input,
            material_fingerprint=material_fingerprint,
            material_snapshot_id=material_snapshot_id,
            persistence_graph=persistence_graph,
            portfolio_world_context=portfolio_world_context,
            projection_run=projection_run,
            runtime_stages=runtime_stages,
            snapshot=snapshot,
            target_scoped_patch=target_scoped_patch,
        )
        if isinstance(stage_result, CompletedProjection):
            return stage_result.result
        active_abox = stage_result.active_abox
        current_state_migration_mode = stage_result.current_state_migration_mode
        desired_persistence_mode = stage_result.desired_persistence_mode
        evidence_index_upgrade = stage_result.evidence_index_upgrade
        physical_state_migration_required = (
            stage_result.physical_state_migration_required
        )
        validation = stage_result.validation
        stage_result = plan_inference(
            _store=_store,
            active_abox=active_abox,
            compact_reasoning_context=compact_reasoning_context,
            current_state_migration_mode=current_state_migration_mode,
            desired_persistence_mode=desired_persistence_mode,
            emit_progress=emit_progress,
            graph_input=graph_input,
            market_world_context=market_world_context,
            material_snapshot_id=material_snapshot_id,
            persistence_graph=persistence_graph,
            physical_state_migration_required=physical_state_migration_required,
            portfolio_world_context=portfolio_world_context,
            runtime_stages=runtime_stages,
            scoped_identity=scoped_identity,
            snapshot=snapshot,
            source_scope_plan=source_scope_plan,
            target_scoped_patch=target_scoped_patch,
            target_symbols=target_symbols,
        )
        if isinstance(stage_result, CompletedProjection):
            return stage_result.result
        compact_impact_plan = stage_result.compact_impact_plan
        comparison_scope = stage_result.comparison_scope
        inference_symbols = stage_result.inference_symbols
        persisted_comparison_scope = stage_result.persisted_comparison_scope
        projection_scope = stage_result.projection_scope
        world_impact_route = stage_result.world_impact_route
        # Identical facts must still be persisted once when upgrading from
        # the legacy complete-generation pointer. Otherwise a quiet market
        # could leave the old full-rewrite ABox active indefinitely.
        stage_result = reuse_inference(
            _store=_store,
            active_abox=active_abox,
            active_abox_complete=active_abox_complete,
            active_abox_is_scoped_manifest=active_abox_is_scoped_manifest,
            compact_impact_plan=compact_impact_plan,
            compact_reasoning_context=compact_reasoning_context,
            comparison_scope=comparison_scope,
            evidence_index_upgrade=evidence_index_upgrade,
            inference_symbols=inference_symbols,
            market_world_context=market_world_context,
            material_fingerprint=material_fingerprint,
            material_snapshot_id=material_snapshot_id,
            pending_activation_recovery=pending_activation_recovery,
            persisted_comparison_scope=persisted_comparison_scope,
            persistence_graph=persistence_graph,
            physical_state_migration_required=physical_state_migration_required,
            portfolio_world_context=portfolio_world_context,
            projection_scope=projection_scope,
            rulebox_bootstrap=rulebox_bootstrap,
            runtime_stages=runtime_stages,
            scoped_identity=scoped_identity,
            snapshot=snapshot,
            validation=validation,
        )
        if isinstance(stage_result, CompletedProjection):
            return stage_result.result
        stage_result = create_audit(
            _store=_store,
            compact_reasoning_context=compact_reasoning_context,
            inference_symbols=inference_symbols,
            material_fingerprint=material_fingerprint,
            material_snapshot_id=material_snapshot_id,
            persistence_graph=persistence_graph,
            rulebox_bootstrap=rulebox_bootstrap,
            runtime_stages=runtime_stages,
            snapshot=snapshot,
            validation=validation,
        )
        if isinstance(stage_result, CompletedProjection):
            return stage_result.result
        projection_run = stage_result.projection_run
        stage_result = begin_publication(
            _store=_store,
            active_abox=active_abox,
            desired_persistence_mode=desired_persistence_mode,
            inference_symbols=inference_symbols,
            material_fingerprint=material_fingerprint,
            material_snapshot_id=material_snapshot_id,
            projection_run=projection_run,
            runtime_stages=runtime_stages,
            scoped_identity=scoped_identity,
            snapshot=snapshot,
            target_scoped_patch=target_scoped_patch,
        )
        if isinstance(stage_result, CompletedProjection):
            return stage_result.result
        current_state_recovery = stage_result.current_state_recovery
        current_state_transition = stage_result.current_state_transition
        # Target subjects do not change the material ABox identity. They
        # are persisted only in the activation journal so a restart can
        # verify that the eventual native InferenceBox covered the exact
        # requested incremental scope before predecessor cleanup.
        stage_result = publish_candidate(
            _store=_store,
            compact_impact_plan=compact_impact_plan,
            compact_reasoning_context=compact_reasoning_context,
            comparison_scope=comparison_scope,
            current_state_recovery=current_state_recovery,
            current_state_transition=current_state_transition,
            emit_progress=emit_progress,
            graph_input=graph_input,
            inference_symbols=inference_symbols,
            material_fingerprint=material_fingerprint,
            material_snapshot_id=material_snapshot_id,
            pending_activation_recovery=pending_activation_recovery,
            persisted_comparison_scope=persisted_comparison_scope,
            persistence_graph=persistence_graph,
            portfolio_world_context=portfolio_world_context,
            projection_run=projection_run,
            projection_scope=projection_scope,
            rulebox_bootstrap=rulebox_bootstrap,
            runtime_stages=runtime_stages,
            scoped_identity=scoped_identity,
            snapshot=snapshot,
            validation=validation,
        )
        if isinstance(stage_result, CompletedProjection):
            return stage_result.result
        result = stage_result.result
        stage_result = schedule_followups(
            _store=_store,
            _bindings=_bindings,
            graph=graph,
            knowledge_world_context=knowledge_world_context,
            market_world_context=market_world_context,
            portfolio_world_context=portfolio_world_context,
            result=result,
            runtime_stages=runtime_stages,
            world_impact_route=world_impact_route,
        )
        if isinstance(stage_result, CompletedProjection):
            return stage_result.result
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
