"""Coordinator implementation; facade-independent dependencies."""

from __future__ import annotations
from .coordinator_ports import CoordinatorPort
from typing import Dict


def acquire_projection_coordinator_lease(
    _store: CoordinatorPort, owner: str, world_id: str
) -> Dict[str, object]:
    """Claim the narrow TypeDB physical-write boundary when available."""
    if _store.active_graph_store_key() != "typedb":
        return {"acquired": True, "status": "not-typedb"}
    acquire = getattr(_store.repository, "acquire_projection_coordinator_lease", None)
    if not callable(acquire):
        # Compatibility adapters retain their existing per-world lease.
        return {"acquired": True, "status": "unsupported"}
    try:
        return dict(acquire(owner, world_id=world_id) or {})
    except (
        Exception
    ) as error:  # noqa: BLE001 - never replace the active generation without this boundary.
        return {
            "acquired": False,
            "status": "error",
            "requestedWorldId": str(world_id or ""),
            "recommendedRetryAfterSeconds": 10,
            "reason": str(error)[:180],
        }


def release_projection_coordinator_lease(
    _store: CoordinatorPort, lease: Dict[str, object]
) -> Dict[str, object]:
    if not bool((lease or {}).get("acquired")):
        return {"status": "not-owner"}
    releaser = getattr(_store.repository, "release_projection_coordinator_lease", None)
    if not callable(releaser):
        return {"status": "unsupported"}
    last_result: Dict[str, object] = {}
    for attempt in range(1, 3):
        try:
            last_result = dict(releaser(lease) or {})
        except (
            Exception
        ) as error:  # noqa: BLE001 - the same owner token is safe to retry.
            last_result = {"status": "error", "reason": str(error)[:180]}
        if str(last_result.get("status") or "") in {
            "released",
            "disabled",
            "not-owner",
            "missing",
            "unsupported",
        }:
            if attempt > 1:
                last_result["releaseAttempts"] = attempt
            return last_result
    return {
        **last_result,
        "status": "error",
        "releaseAttempts": 2,
        "retryable": True,
        "reason": str(
            last_result.get("reason")
            or "TypeDB projection coordinator release did not reach a terminal state."
        )[:180],
    }
