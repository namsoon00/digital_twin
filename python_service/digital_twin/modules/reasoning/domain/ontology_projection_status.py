from typing import Mapping


VERIFIED_MONITOR_SNAPSHOT_QUEUED = "queued-verified-monitor-snapshot"
TYPEDB_REASONING_WORKER_DEFERRED = "deferred-to-reasoning-worker"

REASONING_WORKER_PENDING_PROJECTION_STATUSES = frozenset({
    VERIFIED_MONITOR_SNAPSHOT_QUEUED,
    TYPEDB_REASONING_WORKER_DEFERRED,
})

UNCHANGED_MATERIAL_FACTS = "unchanged-material-facts"


def projection_waits_for_reasoning_worker(projection: object) -> bool:
    if not isinstance(projection, Mapping):
        return False
    status = str(projection.get("status") or "").strip().lower()
    return status in REASONING_WORKER_PENDING_PROJECTION_STATUSES


def projection_reuses_unchanged_inference(projection: object) -> bool:
    """Return true only for a verified reuse of the previous InferenceBox.

    Collection timestamps may advance while the material ABox remains the
    same.  Reusing the aligned InferenceBox is enough for audit reads, but it
    must not reopen investment-event, AI, or notification work.
    """

    if not isinstance(projection, Mapping):
        return False
    status = str(projection.get("status") or "").strip().lower()
    inference = projection.get("inferenceBox")
    inference = inference if isinstance(inference, Mapping) else {}
    return bool(
        status == UNCHANGED_MATERIAL_FACTS
        and projection.get("materialChangeDetected") is False
        and inference.get("reusedForUnchangedMaterialFacts")
        and not projection.get("reasoningRetryRequired")
    )
