"""Select bounded manifest input or the existing complete-source fallback."""

from __future__ import annotations
from typing import Optional, Union
from copy import deepcopy
from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from typing import Callable, Dict, List
import time


from .stage_results import CompletedProjection, SelectSourceResult
from .select_source_ports import SelectSourcePort
from digital_twin.modules.reasoning.domain.ontology_worlds import OntologyWorld
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology


def select_source(
    _store: SelectSourcePort,
    compact_reasoning_context: Dict[str, object],
    emit_progress: Callable[..., None],
    fresh_candidate_rebuild: bool,
    graph: PortfolioOntology,
    graph_input: Dict[str, object],
    market_world_context: OntologyWorld,
    material_fingerprint: str,
    material_snapshot_id: str,
    persistence_graph: PortfolioOntology,
    planner_topology: Dict[str, object],
    portfolio_world_context: OntologyWorld,
    projection_graph: Dict[str, object],
    rulebox_bootstrap: Dict[str, object],
    runtime_stages: Dict[str, int],
    scoped_identity: Dict[str, object],
    shared_premise_proof: Dict[str, object],
    snapshot: AccountSnapshot,
    target_symbols: List[str],
) -> Union[SelectSourceResult, CompletedProjection]:
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

    return SelectSourceResult(
        active_abox=active_abox,
        active_abox_complete=active_abox_complete,
        active_abox_is_scoped_manifest=active_abox_is_scoped_manifest,
        evidence_index_upgrade=evidence_index_upgrade,
        graph=graph,
        material_fingerprint=material_fingerprint,
        material_snapshot_id=material_snapshot_id,
        persistence_graph=persistence_graph,
        planner_topology=planner_topology,
        scoped_identity=scoped_identity,
        source_scope_plan=source_scope_plan,
        target_scoped_patch=target_scoped_patch,
    )
