from typing import Mapping


VERIFIED_MONITOR_SNAPSHOT_QUEUED = "queued-verified-monitor-snapshot"
TYPEDB_REASONING_WORKER_DEFERRED = "deferred-to-reasoning-worker"

REASONING_WORKER_PENDING_PROJECTION_STATUSES = frozenset({
    VERIFIED_MONITOR_SNAPSHOT_QUEUED,
    TYPEDB_REASONING_WORKER_DEFERRED,
})


def projection_waits_for_reasoning_worker(projection: object) -> bool:
    if not isinstance(projection, Mapping):
        return False
    status = str(projection.get("status") or "").strip().lower()
    return status in REASONING_WORKER_PENDING_PROJECTION_STATUSES
