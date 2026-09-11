"""Detail outbox implementation; facade-independent dependencies."""

from __future__ import annotations
from .detail_outbox_ports import DetailOutboxPort
from digital_twin.domain.portfolio import AccountSnapshot
from typing import Dict, List


def async_quality_record_enabled(_store: DetailOutboxPort) -> bool:
    value = _store.settings.get("ontologyAsyncQualityRecordEnabled")
    if value is None:
        # Existing focused tests rely on a synchronously visible sample;
        # runtime_settings() explicitly opts into the non-blocking path.
        return False
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def inference_detail_outbox_enabled(_store: DetailOutboxPort) -> bool:
    """Whether full InferenceBox readback may leave the alert path.

    The TypeDB native writer still returns an in-memory materialization and
    the realtime path still verifies its active generation pointer.  This
    setting only controls the expensive second TypeDB expansion used for
    detailed audit/diagnostic storage.
    """
    if not _store.inference_detail_outbox:
        return False
    value = _store.settings.get("ontologyInferenceDetailOutboxEnabled")
    if value is None:
        return True
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def inference_detail_outbox_summary(payload: Dict[str, object]) -> Dict[str, object]:
    """Keep a compact operational queue receipt in the projection audit."""
    values = dict(payload or {}) if isinstance(payload, dict) else {}
    return {
        "status": str(values.get("status") or ""),
        "saved": bool(values.get("saved")),
        "eventuallyConsistent": bool(values.get("eventuallyConsistent")),
        "jobId": str(values.get("jobId") or ""),
        "inferenceGenerationId": str(values.get("inferenceGenerationId") or ""),
        "sourceAboxSnapshotId": str(values.get("sourceAboxSnapshotId") or ""),
        "reason": str(values.get("reason") or "")[:220],
    }


def enqueue_inference_detail_readback(
    _store: DetailOutboxPort,
    result: Dict[str, object],
    snapshot: AccountSnapshot,
    inference_symbols: List[str],
    world_id: str = "",
) -> Dict[str, object]:
    """Durably request detailed TypeDB rows after a verified publication.

    Only immutable identifiers and the bounded target list go into MySQL.
    The detailed facts stay in TypeDB until the low-priority worker reads
    the exact active generation while the live reasoning queue is empty.
    """
    inferencebox = (
        result.get("inferenceBox")
        if isinstance(result.get("inferenceBox"), dict)
        else {}
    )
    if not _store.inference_detail_outbox_enabled():
        return {
            "status": "disabled",
            "saved": False,
            "eventuallyConsistent": False,
            "reason": "Inference detail durable outbox is not configured.",
        }
    generation_id = str(inferencebox.get("inferenceGenerationId") or "").strip()
    source_abox_id = str(inferencebox.get("sourceAboxSnapshotId") or "").strip()
    clean_world_id = str(world_id or inferencebox.get("worldId") or "").strip()
    targets = sorted(
        {
            str(symbol or "").upper().strip()
            for symbol in (inferencebox.get("targetSymbols") or inference_symbols or [])
            if str(symbol or "").strip()
        }
    )
    if not (generation_id and source_abox_id and clean_world_id):
        return {
            "status": "deferred-incomplete-inference-detail-identity",
            "saved": False,
            "eventuallyConsistent": False,
            "inferenceGenerationId": generation_id,
            "sourceAboxSnapshotId": source_abox_id,
            "reason": "Verified InferenceBox identity is incomplete; detailed readback was not queued.",
        }
    try:
        queued = _store.inference_detail_outbox.enqueue(
            world_id=clean_world_id,
            account_id=str(snapshot.account_id or ""),
            inference_generation_id=generation_id,
            source_abox_snapshot_id=source_abox_id,
            target_symbols=targets,
            projection_run_id=str(result.get("projectionRunId") or ""),
            detail_limit=_store.inference_snapshot_limit(),
        )
    except (
        Exception
    ) as error:  # noqa: BLE001 - detailed audit must not reopen a verified realtime judgement.
        queued = {
            "status": "error",
            "saved": False,
            "eventuallyConsistent": False,
            "inferenceGenerationId": generation_id,
            "sourceAboxSnapshotId": source_abox_id,
            "reason": str(error)[:220],
        }
    return (
        dict(queued or {})
        if isinstance(queued, dict)
        else {
            "status": "error",
            "saved": False,
            "eventuallyConsistent": False,
            "reason": "Inference detail outbox returned a non-dict receipt.",
        }
    )
