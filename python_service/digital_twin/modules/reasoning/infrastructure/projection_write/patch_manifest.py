"""Plan and repair the scoped candidate without publishing active state."""

from __future__ import annotations
from typing import Optional, Union
from .record_ports import RecordSnapshotBindings
from digital_twin.modules.reasoning.domain.ontology_scopes import (
    apply_scoped_abox_repair_epochs,
    merge_target_scoped_abox_manifest,
)
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from typing import Callable, Dict, List
import time


from .stage_results import CompletedProjection, PatchManifestResult
from .patch_manifest_ports import PatchManifestPort
from digital_twin.modules.reasoning.domain.ontology_worlds import OntologyWorld
from digital_twin.modules.reasoning.domain.ontology_projection_audit import OntologyProjectionRun
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology


from .patch_manifest_source import repair_manifest_source
from .patch_manifest_identity import merge_manifest_identity
from .patch_manifest_diagnostics import (
    applied_patch_diagnostics,
    blocked_patch_result,
    full_manifest_fallback,
)
from .patch_manifest_results import (
    RepairSourceInput,
    ManifestIdentityInput,
    AppliedPatchInput,
    FailedPatchInput,
)


def patch_manifest(
    _store: PatchManifestPort,
    _bindings: RecordSnapshotBindings,
    active_abox: Dict[str, object],
    compact_reasoning_context: Dict[str, object],
    emit_progress: Callable[..., None],
    graph: PortfolioOntology,
    graph_input: Dict[str, object],
    market_world_context: OntologyWorld,
    material_fingerprint: str,
    material_snapshot_id: str,
    observation_followup_targets: List[str],
    persistence_graph: PortfolioOntology,
    planner_topology: Dict[str, object],
    portfolio_world_context: OntologyWorld,
    projection_run: Optional[OntologyProjectionRun],
    rulebox_bootstrap: Dict[str, object],
    runtime_stages: Dict[str, int],
    scoped_identity: Dict[str, object],
    shared_premise_proof: Dict[str, object],
    snapshot: AccountSnapshot,
    target_scoped_patch: Dict[str, object],
    target_symbols: List[str],
) -> Union[PatchManifestResult, CompletedProjection]:
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
                not str(applied_target_patch.get("status") or "").startswith("blocked-")
                or applied_target_patch.get("requiresCompleteSource") is True
            )
        ):
            repaired = repair_manifest_source(
                RepairSourceInput(
                    snapshot=snapshot,
                    rulebox_bootstrap=rulebox_bootstrap,
                    portfolio_world_context=portfolio_world_context,
                    market_world_context=market_world_context,
                    target_symbols=target_symbols,
                    shared_premise_proof=shared_premise_proof,
                    compact_reasoning_context=compact_reasoning_context,
                    observation_followup_targets=observation_followup_targets,
                    active_abox=active_abox,
                    applied_target_patch=applied_target_patch,
                ),
                build_projection_graph=_store.build_projection_graph,
                target_scoped_patch_targets=_store.target_scoped_patch_targets,
                emit_progress=emit_progress,
                clock=time.perf_counter,
                graph_input=graph_input,
                runtime_stages=runtime_stages,
            )
            graph = repaired.graph
            persistence_graph = repaired.persistence_graph
            planner_topology = repaired.planner_topology
            material_fingerprint = repaired.material_fingerprint
            material_snapshot_id = repaired.material_snapshot_id
            scoped_identity = repaired.scoped_identity
            target_scoped_patch = repaired.target_scoped_patch
            scope_repair = repaired.scope_repair
            applied_target_patch = repaired.applied_target_patch
            repair_input_fallback = repaired.repair_input_fallback
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
            identity = merge_manifest_identity(
                ManifestIdentityInput(
                    active_abox=active_abox,
                    planner_topology=planner_topology,
                    applied_target_patch=applied_target_patch,
                    persistence_graph=persistence_graph,
                    snapshot=snapshot,
                    portfolio_world_context=portfolio_world_context,
                    material_snapshot_id=material_snapshot_id,
                ),
            )
            material_fingerprint = identity.material_fingerprint
            scoped_identity = identity.scoped_identity
            material_snapshot_id = identity.material_snapshot_id
            target_scoped_patch = applied_patch_diagnostics(
                AppliedPatchInput(
                    applied_target_patch=applied_target_patch,
                    target_scoped_patch=target_scoped_patch,
                    persistence_graph=persistence_graph,
                    scope_repair=scope_repair,
                    repair_input_fallback=repair_input_fallback,
                    identity=identity,
                ),
                compact_target_scope_selection_trace=_bindings.compact_target_scope_selection_trace,
                scope_integrity_audit_interval_minutes=_store.scope_integrity_audit_interval_minutes,
            )
            persistence_graph.worldview["targetScopedManifestPatch"] = dict(target_scoped_patch)
        elif str(graph_input.get("mode") or "") == "target-scoped":
            result = blocked_patch_result(
                FailedPatchInput(
                    applied_target_patch,
                    target_scoped_patch,
                    repair_input_fallback,
                    graph_input,
                ),
                active_graph_store_key=_store.active_graph_store_key,
            )
            _store.store_projection_result(snapshot, result, projection_run)
            return CompletedProjection(result)
        else:
            target_scoped_patch = full_manifest_fallback(
                applied_target_patch,
                target_scoped_patch,
            )

    return PatchManifestResult(
        graph=graph,
        material_fingerprint=material_fingerprint,
        material_snapshot_id=material_snapshot_id,
        persistence_graph=persistence_graph,
        scoped_identity=scoped_identity,
        target_scoped_patch=target_scoped_patch,
    )
