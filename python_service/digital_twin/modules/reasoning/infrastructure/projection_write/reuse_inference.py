"""Reuse a verified generation only when exact material contracts agree."""

from __future__ import annotations
from typing import Optional, Union
from digital_twin.modules.reasoning.domain.ontology_projection_fingerprint import active_material_fingerprint
from digital_twin.modules.reasoning.domain.ontology_worlds import world_metadata
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from typing import Dict, List


from .stage_results import CompletedProjection, ReuseInferenceResult
from .reuse_inference_ports import ReuseInferencePort
from digital_twin.modules.reasoning.domain.ontology_worlds import OntologyWorld
from digital_twin.modules.reasoning.domain.ontology_validator import OntologyValidationReport
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology


def reuse_inference(
    _store: ReuseInferencePort,
    active_abox: Dict[str, object],
    active_abox_complete: bool,
    active_abox_is_scoped_manifest: bool,
    compact_impact_plan: Dict[str, object],
    compact_reasoning_context: Dict[str, object],
    comparison_scope: Dict[str, object],
    evidence_index_upgrade: Dict[str, object],
    inference_symbols: List[str],
    market_world_context: OntologyWorld,
    material_fingerprint: str,
    material_snapshot_id: str,
    pending_activation_recovery: Dict[str, object],
    persisted_comparison_scope: Dict[str, object],
    persistence_graph: PortfolioOntology,
    physical_state_migration_required: bool,
    portfolio_world_context: OntologyWorld,
    projection_scope: Dict[str, object],
    rulebox_bootstrap: Dict[str, object],
    runtime_stages: Dict[str, int],
    scoped_identity: Dict[str, object],
    snapshot: AccountSnapshot,
    validation: OntologyValidationReport,
) -> Union[ReuseInferenceResult, CompletedProjection]:
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
                rulebox_rules_hash=str(rulebox_bootstrap.get("ruleboxRulesHash") or ""),
                tbox_fingerprint=str(
                    ((persistence_graph.worldview or {}).get("activeTBox") or {}).get(
                        "fingerprint"
                    )
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
        return CompletedProjection(result)

    return ReuseInferenceResult()
