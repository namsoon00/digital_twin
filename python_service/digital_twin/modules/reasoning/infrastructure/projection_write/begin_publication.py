"""Recover or begin the current-state publication journal."""

from __future__ import annotations
from typing import Optional, Union
from digital_twin.modules.reasoning.domain.ontology_current_state import CURRENT_STATE_ABOX_PERSISTENCE_MODE
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from typing import Dict, List
import time


from .stage_results import CompletedProjection, BeginPublicationResult
from .begin_publication_ports import BeginPublicationPort
from digital_twin.modules.reasoning.domain.ontology_projection_audit import OntologyProjectionRun


def begin_publication(
    _store: BeginPublicationPort,
    active_abox: Dict[str, object],
    desired_persistence_mode: str,
    inference_symbols: List[str],
    material_fingerprint: str,
    material_snapshot_id: str,
    projection_run: Optional[OntologyProjectionRun],
    runtime_stages: Dict[str, int],
    scoped_identity: Dict[str, object],
    snapshot: AccountSnapshot,
    target_scoped_patch: Dict[str, object],
) -> Union[BeginPublicationResult, CompletedProjection]:
    current_state_transition = {}
    current_state_recovery = {}
    begin_current_state_transition = (
        getattr(
            _store.projection_run_store,
            "begin_current_state_transition",
            None,
        )
        if _store.projection_run_store
        else None
    )
    if (
        projection_run
        and desired_persistence_mode == CURRENT_STATE_ABOX_PERSISTENCE_MODE
        and callable(begin_current_state_transition)
    ):
        recover_committed = getattr(
            _store.projection_run_store,
            "recover_committed_current_state_transitions",
            None,
        )
        if callable(recover_committed):
            current_state_recovery_started = time.perf_counter()
            try:
                current_state_recovery = dict(
                    recover_committed(
                        world_id=projection_run.world_id,
                        limit=10,
                    )
                    or {}
                )
            except TypeError:
                current_state_recovery = dict(
                    recover_committed(projection_run.world_id, 10) or {}
                )
            except (
                Exception
            ) as error:  # noqa: BLE001 - a new audited transition can continue.
                current_state_recovery = {
                    "status": "error",
                    "reason": str(error)[:180],
                }
            runtime_stages["currentStateRecoveryMs"] = int(
                (time.perf_counter() - current_state_recovery_started) * 1000
            )
        else:
            current_state_recovery = {"status": "unsupported"}
            runtime_stages["currentStateRecoveryMs"] = 0
        current_state_transition_started = time.perf_counter()
        try:
            current_state_transition = dict(
                begin_current_state_transition(
                    projection_run,
                    str(
                        active_abox.get("worldviewManifestId")
                        or active_abox.get("aboxSnapshotId")
                        or ""
                    ),
                    material_snapshot_id,
                    material_fingerprint,
                    desired_persistence_mode,
                    detail={
                        "targetSymbols": list(inference_symbols),
                        "scopeCount": len(scoped_identity.get("scopePlan") or []),
                        "targetScopedPatch": dict(target_scoped_patch or {}),
                    },
                )
                or {}
            )
        except Exception as error:  # noqa: BLE001 - no unaudited physical mutation.
            current_state_transition = {
                "status": "error",
                "reason": str(error)[:180],
            }
        runtime_stages["currentStateTransitionAuditMs"] = int(
            (time.perf_counter() - current_state_transition_started) * 1000
        )
        if str(current_state_transition.get("status") or "") != "ok":
            result = {
                "saved": False,
                "status": "current-state-transition-audit-failed",
                "reason": str(
                    current_state_transition.get("reason")
                    or "Current-state transition checkpoint could not be stored."
                )[:220],
                "graphStore": _store.active_graph_store_key(),
                "materialFingerprint": material_fingerprint,
                "aboxSnapshotId": material_snapshot_id,
                "preservedActiveGeneration": True,
                "currentStateTransition": current_state_transition,
            }
            _store.store_projection_result(snapshot, result, projection_run)
            return CompletedProjection(result)

    return BeginPublicationResult(
        current_state_recovery=current_state_recovery,
        current_state_transition=current_state_transition,
    )
