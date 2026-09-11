"""Current state implementation; facade-independent dependencies."""

from __future__ import annotations
from .current_state_ports import CurrentStatePort
from digital_twin.modules.reasoning.domain.ontology_projection_audit import OntologyProjectionRun
from typing import Dict


def interrupted_projection_recovery_required(
    _store: CurrentStatePort, world_id: str
) -> bool:
    """Use MySQL's recovery index before opening historical TypeDB reads."""

    if not _store.projection_run_store:
        return False
    candidates = getattr(
        _store.projection_run_store,
        "interrupted_projection_recovery_candidates",
        None,
    )
    if not callable(candidates):
        # Compatibility stores used by explicit recovery tests retain the
        # old behavior. Production MySQL always provides the index.
        return True
    try:
        return bool(candidates(world_id=world_id, limit=1))
    except TypeError:
        return bool(candidates(world_id, 1))
    except Exception:
        # An unreadable recovery index must not make TypeDB history part
        # of every normal request. The dedicated recovery worker retries.
        return False


def advance_current_state_transition(
    _store: CurrentStatePort,
    projection_run: OntologyProjectionRun,
    stage: str,
    status: str = "running",
    inference_generation_id: str = "",
    detail: Dict[str, object] = None,
) -> Dict[str, object]:
    if not projection_run or not _store.projection_run_store:
        return {"status": "disabled"}
    advance = getattr(
        _store.projection_run_store,
        "advance_current_state_transition",
        None,
    )
    if not callable(advance):
        return {"status": "unsupported"}
    try:
        return dict(
            advance(
                projection_run.run_id,
                stage,
                status=status,
                inference_generation_id=inference_generation_id,
                detail=detail or {},
            )
            or {}
        )
    except Exception as error:  # noqa: BLE001 - projection audit remains authoritative.
        return {"status": "error", "reason": str(error)[:180]}


def finalize_current_state_transition(
    _store: CurrentStatePort,
    completed_run: OntologyProjectionRun,
    result: Dict[str, object],
) -> Dict[str, object]:
    """Close a committed current-state transition or retain a failed audit."""

    transition = result.get("currentStateTransition")
    if not isinstance(transition, dict) or str(transition.get("status") or "") != "ok":
        return {"status": "not-applicable"}
    inference_payload = (
        dict(result.get("inferenceBox") or {})
        if isinstance(result.get("inferenceBox"), dict)
        else {}
    )
    inference_generation_id = str(
        inference_payload.get("inferenceGenerationId")
        or completed_run.inference_generation_id
        or ""
    )
    native_completed = bool(
        inference_payload.get("nativeTypeDbReasoningCompleted")
        or inference_payload.get("typedbNativeRuleEvaluationCompleted")
    )
    if bool(result.get("saved")) and native_completed:
        synthesis = _store.advance_current_state_transition(
            completed_run,
            "synthesis-persisted",
            inference_generation_id=inference_generation_id,
            detail={
                "projectionStatus": completed_run.status,
                "activeAboxSnapshotId": completed_run.active_abox_snapshot_id,
            },
        )
        result["currentStateSynthesisCheckpoint"] = synthesis
        if str(synthesis.get("status") or "") != "ok":
            return {
                "status": "pending-recovery",
                "stage": "synthesis-persisted",
                "checkpoint": synthesis,
            }
        completion = _store.advance_current_state_transition(
            completed_run,
            "completed",
            status="completed",
            inference_generation_id=inference_generation_id,
            detail={
                "projectionStatus": completed_run.status,
                "activeAboxSnapshotId": completed_run.active_abox_snapshot_id,
            },
        )
        result["currentStateCompletionCheckpoint"] = completion
        return {
            "status": (
                "completed" if bool(completion.get("completed")) else "pending-recovery"
            ),
            "stage": str(completion.get("resumeStage") or "completed"),
            "checkpoint": completion,
        }

    failed_stage = "source-bound"
    if isinstance(result.get("currentStateInferenceCheckpoint"), dict):
        failed_stage = "inferred"
    elif isinstance(result.get("currentStatePatchCheckpoint"), dict):
        failed_stage = "patch-applied"
    failure = _store.advance_current_state_transition(
        completed_run,
        failed_stage,
        status="failed",
        inference_generation_id=inference_generation_id,
        detail={
            "projectionStatus": completed_run.status,
            "reason": str(
                result.get("reason")
                or "Native inference did not complete after the ABox patch committed."
            )[:500],
            "activeAboxSnapshotId": completed_run.active_abox_snapshot_id,
        },
    )
    result["currentStateFailureCheckpoint"] = failure
    return {
        "status": "failed",
        "stage": failed_stage,
        "checkpoint": failure,
    }
