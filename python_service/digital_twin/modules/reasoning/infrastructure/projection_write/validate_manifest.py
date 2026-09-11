"""Validate candidate facts and physical mode before graph publication."""

from __future__ import annotations
from typing import Optional, Union
from digital_twin.modules.reasoning.domain.ontology_current_state import CURRENT_STATE_ABOX_PERSISTENCE_MODE
from digital_twin.modules.reasoning.domain.ontology_projection_fingerprint import active_material_fingerprint
from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_PERSISTENCE_MODE
from digital_twin.modules.reasoning.domain.ontology_validator import validate_ontology
from digital_twin.modules.reasoning.domain.ontology_worlds import world_metadata
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from typing import Callable, Dict
import time


from .stage_results import CompletedProjection, ValidateManifestResult
from .validate_manifest_ports import ValidateManifestPort
from digital_twin.modules.reasoning.domain.ontology_worlds import OntologyWorld
from digital_twin.modules.reasoning.domain.ontology_projection_audit import OntologyProjectionRun
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology


def validate_manifest(
    _store: ValidateManifestPort,
    active_abox: Dict[str, object],
    active_abox_complete: bool,
    active_abox_is_scoped_manifest: bool,
    emit_progress: Callable[..., None],
    evidence_index_upgrade: Dict[str, object],
    graph_input: Dict[str, object],
    material_fingerprint: str,
    material_snapshot_id: str,
    persistence_graph: PortfolioOntology,
    portfolio_world_context: OntologyWorld,
    projection_run: Optional[OntologyProjectionRun],
    runtime_stages: Dict[str, int],
    snapshot: AccountSnapshot,
    target_scoped_patch: Dict[str, object],
) -> Union[ValidateManifestResult, CompletedProjection]:
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
        return CompletedProjection(result)
    # Preserve the exact incremental path or safe fallback in the
    # manifest, so operational diagnostics do not infer it later.
    persistence_graph.worldview["targetScopedManifestPatch"] = dict(target_scoped_patch)
    persistence_graph.worldview["factSlotProjection"] = {
        "status": str(target_scoped_patch.get("factSlotStatus") or "not-applied"),
        "selectedScopeCount": int(
            target_scoped_patch.get("factSlotSelectedScopeCount") or 0
        ),
        "deferredScopeCount": int(
            target_scoped_patch.get("factSlotDeferredScopeCount") or 0
        ),
        "slotFamilies": list(target_scoped_patch.get("factSlotFamilies") or [])[:20],
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
            target_scoped_patch.get("factSlotUnclassifiedChangedFieldsBySymbol") or {}
        ),
        "fallbackReason": str(target_scoped_patch.get("factSlotFallbackReason") or ""),
    }
    if str(target_scoped_patch.get("status") or "") == "applied":
        full_reconcile_at = str(
            active_abox.get("lastFullScopeReconcileAt") or active_abox.get("asOf") or ""
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
                        + str(evidence_index_upgrade.get("reason") or upgrade_status)[
                            :180
                        ]
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
                return CompletedProjection(result)

    return ValidateManifestResult(
        active_abox=active_abox,
        current_state_migration_mode=current_state_migration_mode,
        desired_persistence_mode=desired_persistence_mode,
        evidence_index_upgrade=evidence_index_upgrade,
        physical_state_migration_required=physical_state_migration_required,
        validation=validation,
    )
