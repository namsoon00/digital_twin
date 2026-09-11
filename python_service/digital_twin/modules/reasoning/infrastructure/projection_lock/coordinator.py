"""projection_lock: coordinator through explicit injected capabilities."""

from digital_twin.modules.reasoning.infrastructure.backend_constants import (
    TYPEDB_PROJECTION_COORDINATOR_VERSION,
    TYPEDB_PROJECTION_COORDINATOR_WORLD_ID,
)
from typing import Dict
from .coordinator_ports import ProjectionLockCoordinatorStore


def projection_coordinator_lease_status(
    _store: ProjectionLockCoordinatorStore,
) -> Dict[str, object]:
    """Expose the database-wide projection owner without exposing ABox facts."""
    result = _store.scoped_abox_write_lease_status(TYPEDB_PROJECTION_COORDINATOR_WORLD_ID)
    return {
        **dict(result or {}),
        "coordinator": "typedb-projection",
        "coordinatorVersion": TYPEDB_PROJECTION_COORDINATOR_VERSION,
        "coordinatorWorldId": TYPEDB_PROJECTION_COORDINATOR_WORLD_ID,
    }


def recover_dead_projection_coordinator_lease(
    _store: ProjectionLockCoordinatorStore,
) -> Dict[str, object]:
    """Recover a dead or proven-orphaned local projection coordinator."""
    with _store._projection_coordinator_registry_lock:
        return _store.recover_dead_local_scoped_abox_write_lease(
            TYPEDB_PROJECTION_COORDINATOR_WORLD_ID,
            recover_untracked_current_process=True,
        )


def projection_coordinator_write_enforced(_store: ProjectionLockCoordinatorStore) -> bool:
    """Whether public repository mutations must take the global writer lease."""
    return bool(getattr(_store, "_projection_coordinator_write_enforced", False))


def active_projection_coordinator_lease(
    _store: ProjectionLockCoordinatorStore,
) -> Dict[str, object]:
    leases = list(getattr(_store._projection_coordinator_local, "leases", []) or [])
    if not leases:
        return {}
    return dict(leases[-1] or {})


def projection_coordinator_token_is_active(
    _store: ProjectionLockCoordinatorStore, token: str
) -> bool:
    clean_token = str(token or "").strip()
    if not clean_token:
        return False
    with _store._projection_coordinator_registry_lock:
        return clean_token in _store._active_projection_coordinator_tokens


def track_projection_coordinator_lease(
    _store: ProjectionLockCoordinatorStore, lease: Dict[str, object]
) -> None:
    if not bool((lease or {}).get("acquired")):
        return
    with _store._projection_coordinator_registry_lock:
        leases = list(getattr(_store._projection_coordinator_local, "leases", []) or [])
        leases.append(dict(lease or {}))
        _store._projection_coordinator_local.leases = leases
        token = str((lease or {}).get("leaseToken") or "").strip()
        if token:
            _store._active_projection_coordinator_tokens.add(token)


def forget_projection_coordinator_lease(
    _store: ProjectionLockCoordinatorStore, lease: Dict[str, object]
) -> None:
    with _store._projection_coordinator_registry_lock:
        leases = list(getattr(_store._projection_coordinator_local, "leases", []) or [])
        target_token = str((lease or {}).get("leaseToken") or "")
        if target_token:
            _store._active_projection_coordinator_tokens.discard(target_token)
        if not leases:
            return
        target_owner = str((lease or {}).get("leaseOwner") or "")
        target_status = str((lease or {}).get("status") or "")
        for index in range(len(leases) - 1, -1, -1):
            candidate = dict(leases[index] or {})
            if target_token and str(candidate.get("leaseToken") or "") == target_token:
                leases.pop(index)
                _store._projection_coordinator_local.leases = leases
                return
            if (
                not target_token
                and target_owner
                and str(candidate.get("leaseOwner") or "") == target_owner
            ):
                leases.pop(index)
                _store._projection_coordinator_local.leases = leases
                return
            if not target_token and not target_owner and target_status == "disabled":
                leases.pop(index)
                _store._projection_coordinator_local.leases = leases
                return


def projection_coordinator_write_scope(
    _store: ProjectionLockCoordinatorStore, owner: str, world_id: str = ""
):
    """Reuse an explicit outer scope or release the lease acquired here.

    Public top-level acquisition deliberately does not adopt a thread-local
    lease.  Only this context manager may do so, which prevents a leaked
    lease from being mistaken for a legitimate nested write on the next
    worker job.
    """
    depth = int(getattr(_store._projection_coordinator_local, "explicit_scope_depth", 0) or 0)
    _store._projection_coordinator_local.explicit_scope_depth = depth + 1
    lease: Dict[str, object] = {}
    try:
        lease = _store.acquire_projection_coordinator_lease(owner, world_id=world_id)
        adopted = bool((lease or {}).get("adopted"))
        try:
            yield lease
        finally:
            if bool((lease or {}).get("acquired")) and not adopted:
                _store.release_projection_coordinator_lease(lease)
    finally:
        _store._projection_coordinator_local.explicit_scope_depth = depth


def acquire_projection_coordinator_lease(
    _store: ProjectionLockCoordinatorStore, owner: str, world_id: str = ""
) -> Dict[str, object]:
    with _store._projection_coordinator_registry_lock:
        return _store._acquire_projection_coordinator_lease(owner, world_id=world_id)


def _acquire_projection_coordinator_lease(
    _store: ProjectionLockCoordinatorStore,
    owner: str,
    world_id: str = "",
    allow_adopt: bool = False,
) -> Dict[str, object]:
    """Serialize physical TypeDB writes across portfolio and shared worlds.

    Per-world ABox leases protect semantic generation ownership. This
    outer lease protects the TypeDB database's single writer so a Market
    World merge cannot contend with a PortfolioWorld activation halfway
    through its native InferenceBox lifecycle.
    """
    allow_adopt = bool(
        allow_adopt
        or int(getattr(_store._projection_coordinator_local, "explicit_scope_depth", 0) or 0) > 0
    )
    active = _store.active_projection_coordinator_lease()
    if bool(active.get("acquired")):
        if allow_adopt:
            return {
                **active,
                "status": "adopted",
                "adopted": True,
                "requestedWorldId": str(world_id or ""),
            }
        return {
            **active,
            "acquired": False,
            "status": "self-owned-coordinator-not-released",
            "requestedWorldId": str(world_id or ""),
            "recommendedRetryAfterSeconds": _store.typedb_projection_coordinator_retry_seconds(),
            "reason": (
                "The previous top-level TypeDB projection coordinator lease "
                "is still owned by this worker and must be released before "
                "another graph write starts."
            ),
        }
    if not _store.typedb_projection_coordinator_enabled():
        response = {
            "acquired": True,
            "status": "disabled",
            "coordinator": "typedb-projection",
            "coordinatorVersion": TYPEDB_PROJECTION_COORDINATOR_VERSION,
            "requestedWorldId": str(world_id or ""),
        }
        _store.track_projection_coordinator_lease(response)
        return response
    try:
        lease = _store.acquire_scoped_abox_write_lease(
            "projection-coordinator:" + str(owner or "unknown")[:160],
            world_id=TYPEDB_PROJECTION_COORDINATOR_WORLD_ID,
            lease_seconds=_store.typedb_projection_coordinator_lease_seconds(),
        )
    except (
        Exception
    ) as error:  # noqa: BLE001 - callers keep the prior active generation on a failed claim.
        return {
            "acquired": False,
            "status": "error",
            "coordinator": "typedb-projection",
            "coordinatorVersion": TYPEDB_PROJECTION_COORDINATOR_VERSION,
            "requestedWorldId": str(world_id or ""),
            "recommendedRetryAfterSeconds": _store.typedb_projection_coordinator_retry_seconds(),
            "reason": str(error)[:180],
        }
    response = dict(lease or {})
    response.update(
        {
            "coordinator": "typedb-projection",
            "coordinatorVersion": TYPEDB_PROJECTION_COORDINATOR_VERSION,
            "coordinatorWorldId": TYPEDB_PROJECTION_COORDINATOR_WORLD_ID,
            "requestedWorldId": str(world_id or ""),
        }
    )
    if not response.get("acquired"):
        response["recommendedRetryAfterSeconds"] = (
            _store.typedb_projection_coordinator_retry_seconds()
        )
    else:
        _store.track_projection_coordinator_lease(response)
    return response


def release_projection_coordinator_lease(
    _store: ProjectionLockCoordinatorStore, lease: Dict[str, object]
) -> Dict[str, object]:
    with _store._projection_coordinator_registry_lock:
        return _store._release_projection_coordinator_lease(lease)


def _release_projection_coordinator_lease(
    _store: ProjectionLockCoordinatorStore, lease: Dict[str, object]
) -> Dict[str, object]:
    if bool((lease or {}).get("adopted")):
        return {"status": "adopted-by-caller"}
    if str((lease or {}).get("status") or "") == "disabled":
        _store.forget_projection_coordinator_lease(lease)
        return {"status": "disabled"}
    result = dict(_store.release_scoped_abox_write_lease(lease) or {})
    if str(result.get("status") or "") in {"released", "not-owner", "missing"}:
        _store.forget_projection_coordinator_lease(lease)
    return result
