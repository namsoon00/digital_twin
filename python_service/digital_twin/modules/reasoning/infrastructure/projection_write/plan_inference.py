"""Route changed facts into bounded inference and comparison scopes."""

from __future__ import annotations
from typing import Optional, Union
from digital_twin.modules.reasoning.domain.ontology_change_impact import compact_inference_impact_plan
from digital_twin.modules.reasoning.domain.ontology_scopes import target_scope_manifest_fingerprint
from digital_twin.modules.reasoning.domain.ontology_world_routing import route_world_impact
from digital_twin.modules.reasoning.domain.ontology_worlds import shared_premise_world
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from typing import Callable, Dict, List
import time


from .stage_results import CompletedProjection, PlanInferenceResult
from .plan_inference_ports import PlanInferencePort
from digital_twin.modules.reasoning.domain.ontology_worlds import OntologyWorld
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology


def plan_inference(
    _store: PlanInferencePort,
    active_abox: Dict[str, object],
    compact_reasoning_context: Dict[str, object],
    current_state_migration_mode: str,
    desired_persistence_mode: str,
    emit_progress: Callable[..., None],
    graph_input: Dict[str, object],
    market_world_context: OntologyWorld,
    material_snapshot_id: str,
    persistence_graph: PortfolioOntology,
    physical_state_migration_required: bool,
    portfolio_world_context: OntologyWorld,
    runtime_stages: Dict[str, int],
    scoped_identity: Dict[str, object],
    snapshot: AccountSnapshot,
    source_scope_plan: List[Dict[str, object]],
    target_scoped_patch: Dict[str, object],
    target_symbols: List[str],
) -> Union[PlanInferenceResult, CompletedProjection]:
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
            for key, value in dict(world_impact_route.get("partitions") or {}).items()
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

    return PlanInferenceResult(
        compact_impact_plan=compact_impact_plan,
        comparison_scope=comparison_scope,
        inference_symbols=inference_symbols,
        persisted_comparison_scope=persisted_comparison_scope,
        projection_scope=projection_scope,
        world_impact_route=world_impact_route,
    )
