"""Assemble the exact source graph and material identity for this attempt."""

from __future__ import annotations
from typing import Optional, Union
from digital_twin.domain.portfolio import AccountSnapshot
from typing import Callable, Dict, List
import time


from .stage_results import CompletedProjection, AssembleSourceResult
from .assemble_source_ports import AssembleSourcePort
from digital_twin.domain.ontology_worlds import OntologyWorld
from digital_twin.domain.ontology_projection_audit import OntologyProjectionRun


def assemble_source(
    _store: AssembleSourcePort,
    compact_reasoning_context: Dict[str, object],
    emit_progress: Callable[..., None],
    market_world_context: OntologyWorld,
    portfolio_world_context: OntologyWorld,
    projection_run: Optional[OntologyProjectionRun],
    runtime_stages: Dict[str, int],
    shared_premise_proof: Dict[str, object],
    snapshot: AccountSnapshot,
    target_symbols: List[str],
) -> Union[AssembleSourceResult, CompletedProjection]:
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
        return CompletedProjection(result)
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
            for symbol in compact_reasoning_context.get("observationFollowupSymbols")
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
        persistence_graph.worldview["targetScopeRetentionMode"] = "observation-followup"
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

    return AssembleSourceResult(
        graph=graph,
        graph_input=graph_input,
        material_fingerprint=material_fingerprint,
        material_snapshot_id=material_snapshot_id,
        observation_followup_targets=observation_followup_targets,
        persistence_graph=persistence_graph,
        planner_topology=planner_topology,
        projection_graph=projection_graph,
        rulebox_bootstrap=rulebox_bootstrap,
        scoped_identity=scoped_identity,
    )
