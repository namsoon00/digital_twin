"""Attach world and source identity only after a complete input assembly."""

from __future__ import annotations

from digital_twin.domain.ontology_native_rule_planning import (
    native_rule_planner_manifest_fingerprint,
)
from digital_twin.domain.ontology_native_rule_planning import (
    native_rule_planner_topology,
)
from digital_twin.domain.ontology_projection_fingerprint import (
    apply_material_graph_identity,
)
from digital_twin.domain.ontology_projection_fingerprint import (
    material_graph_fingerprint,
)
from digital_twin.domain.ontology_scopes import apply_scoped_abox_identity
from digital_twin.domain.ontology_worlds import market_world
from digital_twin.domain.ontology_worlds import world_metadata
from digital_twin.domain.portfolio import AccountSnapshot
from digital_twin.domain.world_partitioned_reasoning import account_overlay_graph
from typing import Callable
from typing import Dict
from typing import List
import time
from digital_twin.modules.reasoning.application.projection_input.ports import (
    ProjectionIdentityInputs,
)


def build_projection_graph(
    _inputs: ProjectionIdentityInputs,
    snapshot: AccountSnapshot,
    rule_catalog: Dict[str, object],
    portfolio_world_context,
    market_world_context=None,
    target_symbols: List[str] = None,
    target_scoped_input: bool = False,
    progress_callback: Callable[..., None] = None,
    shared_premise_proof: Dict[str, object] = None,
    reasoning_context: Dict[str, object] = None,
) -> Dict[str, object]:
    """Assemble one immutable projection graph and its scoped identity."""
    graph_build_started = time.perf_counter()
    partition = {}
    assembly_catalog = rule_catalog
    if _inputs.world_partitioned_reasoning_enabled():
        partition = _inputs.world_rule_partition(rule_catalog)
        if str(partition.get("status") or "") != "ready":
            raise RuntimeError(
                "RuleBox world partition is invalid; PortfolioWorld projection was blocked."
            )
        assembly_catalog = _inputs.catalog_for_rules(
            rule_catalog,
            partition.get("overlayRules") or [],
        )
    graph, persistence_graph, graph_assembly = _inputs.build_graph_assembly(
        snapshot,
        assembly_catalog,
        target_symbols=target_symbols,
        target_scoped_input=target_scoped_input,
        progress_callback=progress_callback,
        reasoning_context=reasoning_context,
    )
    if _inputs.world_partitioned_reasoning_enabled():
        proof = dict(shared_premise_proof or {})
        if not bool(proof.get("ready")):
            raise RuntimeError(
                "SharedPremiseWorld premises are not ready; PortfolioWorld projection was blocked."
            )
        shared_generation_id = str(proof.get("inferenceGenerationId") or "").strip()
        shared_source_abox_id = str(proof.get("sourceAboxSnapshotId") or "").strip()
        generation_vector = (
            dict(proof.get("generationVector") or {})
            if isinstance(proof.get("generationVector"), dict)
            else {}
        )
        if not shared_generation_id or not shared_source_abox_id:
            raise RuntimeError(
                "SharedPremiseWorld generation identity is incomplete; PortfolioWorld projection was blocked."
            )
        if generation_vector and (
            str(generation_vector.get("inferenceGenerationId") or "").strip()
            != shared_generation_id
            or str(generation_vector.get("sourceAboxSnapshotId") or "").strip()
            != shared_source_abox_id
        ):
            raise RuntimeError(
                "SharedPremiseWorld generation vector is incoherent; PortfolioWorld projection was blocked."
            )
        persistence_graph = account_overlay_graph(
            persistence_graph,
            partition.get("overlayRules") or [],
            proof.get("premisesBySymbol") or {},
            shared_generation_id=shared_generation_id,
            source_abox_snapshot_id=shared_source_abox_id,
            premise_proofs_by_symbol=proof.get("symbols") or {},
        )
    runtime_stages = dict(graph_assembly.get("runtimeStages") or {})
    runtime_stages["graphAssemblyCacheHit"] = (
        1 if str(graph_assembly.get("status") or "") == "hit" else 0
    )
    if graph_assembly.get("ageMs") is not None:
        runtime_stages["graphAssemblyCacheAgeMs"] = int(
            graph_assembly.get("ageMs") or 0
        )
    planner_topology = native_rule_planner_topology(persistence_graph)
    persistence_graph.worldview["nativeRulePlannerTopology"] = planner_topology
    resolved_market_world = market_world_context or market_world(
        portfolio_world_context.market_id,
        _inputs.settings.get("ontologySharedMarketTenantId") or "shared",
    )
    world_metadata_payload = {
        **world_metadata(portfolio_world_context),
        "marketWorldId": resolved_market_world.world_id,
        "marketContextMode": (
            "shared-premise-account-overlay"
            if _inputs.world_partitioned_reasoning_enabled()
            else (
                "incremental-current-state-one-pass"
                if _inputs.incremental_current_state_reasoning_enabled()
                else "shared-market-world-with-portfolio-rule-mirror"
            )
        ),
    }
    if _inputs.incremental_current_state_reasoning_enabled():
        world_metadata_payload.update(
            {
                "reasoningExecutionMode": "incremental-current-state-one-pass-v1",
                "sharedPremiseCriticalPath": False,
                "factSliceProjection": True,
            }
        )
    if _inputs.world_partitioned_reasoning_enabled():
        world_metadata_payload.update(
            {
                "sharedPremiseWorldId": str(
                    (shared_premise_proof or {}).get("worldId") or ""
                ),
                "sharedPremiseInferenceGenerationId": str(
                    (shared_premise_proof or {}).get("inferenceGenerationId") or ""
                ),
                "sharedPremiseGenerationVector": (
                    dict((shared_premise_proof or {}).get("generationVector") or {})
                    if isinstance(
                        (shared_premise_proof or {}).get("generationVector"),
                        dict,
                    )
                    else {}
                ),
            }
        )
    graph.worldview.update(world_metadata_payload)
    persistence_graph.worldview.update(world_metadata_payload)
    material_fingerprint = native_rule_planner_manifest_fingerprint(
        material_graph_fingerprint(persistence_graph),
        planner_topology,
    )
    material_snapshot_id = apply_material_graph_identity(
        persistence_graph,
        snapshot.account_id,
        material_fingerprint,
        world_id=portfolio_world_context.world_id,
    )
    scoped_identity_started = time.perf_counter()
    scoped_identity = apply_scoped_abox_identity(
        persistence_graph,
        snapshot.account_id,
        world_id=portfolio_world_context.world_id,
        tenant_id=portfolio_world_context.tenant_id,
        world_type=portfolio_world_context.world_type,
    )
    runtime_stages["scopedAboxIdentityMs"] = int(
        (time.perf_counter() - scoped_identity_started) * 1000
    )
    material_snapshot_id = str(
        scoped_identity.get("manifestId") or material_snapshot_id
    )
    runtime_stages["graphBuildMs"] = int(
        (time.perf_counter() - graph_build_started) * 1000
    )
    return {
        "graph": graph,
        "persistenceGraph": persistence_graph,
        "assembly": graph_assembly,
        "plannerTopology": planner_topology,
        "materialFingerprint": material_fingerprint,
        "materialSnapshotId": material_snapshot_id,
        "scopedIdentity": scoped_identity,
        "runtimeStages": runtime_stages,
    }
