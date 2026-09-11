"""projection_lock: recovery through explicit injected capabilities."""

from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import json_object
from typing import Dict
import os
import socket
from .recovery_ports import ProjectionLockRecoveryStore


def recover_dead_local_scoped_abox_write_lease(_store: ProjectionLockRecoveryStore, world_id: str='', recover_untracked_current_process: bool=False) -> Dict[str, object]:
    """Release a held lease only when its local owner process is gone.

    This covers a project worker restart without requiring a TypeDB server
    restart.  Legacy rows without owner host/PID and rows owned by another
    host intentionally remain until normal expiry, so an operator cannot
    accidentally steal an active cross-process writer.
    """
    if not str(getattr(_store, "address", "") or "").strip():
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "reason": "TypeDB ontology storage is not configured.",
        }
    try:
        existing = _store.scoped_abox_write_lease_status(world_id)
    except Exception as error:  # noqa: BLE001 - recovery must never block the worker startup.
        return {
            "configured": True,
            "status": "unavailable",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "reason": str(error)[:180],
        }
    if str(existing.get("status") or "") != "held":
        return {
            "configured": True,
            "status": "skipped",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "reason": "No held scoped ABox write lease requires local recovery.",
        }
    payload = json_object(existing.get("propertiesJson"))
    lease_host = str(existing.get("leaseHost") or payload.get("leaseHost") or "").strip()
    lease_process_id = existing.get("leaseProcessId")
    if lease_process_id in (None, ""):
        lease_process_id = payload.get("leaseProcessId")
    if not lease_host or lease_process_id in (None, ""):
        return {
            "configured": True,
            "status": "legacy-owner-unknown",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "leaseOwner": str(existing.get("leaseOwner") or ""),
            "reason": "Held lease has no local owner metadata and will expire normally.",
        }
    try:
        local_process_id = int(lease_process_id)
    except (TypeError, ValueError):
        return {
            "configured": True,
            "status": "invalid-owner",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "leaseOwner": str(existing.get("leaseOwner") or ""),
            "reason": "Held lease has an invalid local process identifier and will expire normally.",
        }
    if local_process_id <= 0:
        return {
            "configured": True,
            "status": "invalid-owner",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "leaseOwner": str(existing.get("leaseOwner") or ""),
            "reason": "Held lease has no valid local process identifier and will expire normally.",
        }
    if lease_host != socket.gethostname():
        return {
            "configured": True,
            "status": "foreign-owner",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "leaseOwner": str(existing.get("leaseOwner") or ""),
            "leaseHost": lease_host,
            "reason": "Held lease belongs to another host and cannot be reclaimed locally.",
        }
    lease_token = str(existing.get("leaseToken") or payload.get("leaseToken") or "").strip()
    current_process_orphan = bool(
        recover_untracked_current_process
        and local_process_id == os.getpid()
        and lease_token
        and not _store.projection_coordinator_token_is_active(lease_token)
    )
    if _store.local_process_alive(local_process_id) and not current_process_orphan:
        return {
            "configured": True,
            "status": "active-owner",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "leaseOwner": str(existing.get("leaseOwner") or ""),
            "leaseHost": lease_host,
            "leaseProcessId": local_process_id,
            "reason": "Held lease owner process is still alive.",
        }
    owner = str(existing.get("leaseOwner") or "")
    properties_json = str(existing.get("propertiesJson") or "")
    if not owner or not properties_json:
        return {
            "configured": True,
            "status": "invalid",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "reason": "Dead local lease has no exact ownership payload.",
        }
    try:
        release = _store.release_scoped_abox_write_lease({
            "acquired": True,
            "owner": owner,
            "leaseOwner": owner,
            "propertiesJson": properties_json,
            "worldId": str(world_id or ""),
            "storageId": _store.scoped_abox_write_lease_storage_id(world_id),
        })
    except Exception as error:  # noqa: BLE001 - normal expiry remains the final fallback.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "leaseOwner": owner,
            "reason": str(error)[:180],
        }
    return {
        "configured": True,
        "status": "cleared" if str((release or {}).get("status") or "") == "released" else "error",
        "graphStore": "typedb",
        "worldId": str(world_id or ""),
        "previousLeaseOwner": owner,
        "previousLeaseHost": lease_host,
        "previousLeaseProcessId": local_process_id,
        "release": dict(release or {}),
    }


def recover_all_dead_local_scoped_abox_write_leases(_store: ProjectionLockRecoveryStore) -> Dict[str, object]:
    """Recover every proven-dead local writer, including account worlds."""
    if not str(getattr(_store, "address", "") or "").strip():
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "worlds": [],
        }
    try:
        world_ids = _store.scoped_abox_write_lease_world_ids()
    except Exception as error:  # noqa: BLE001 - startup must not fail only because the inventory is unavailable.
        return {
            "configured": True,
            "status": "unavailable",
            "graphStore": "typedb",
            "reason": str(error)[:180],
            "worlds": [],
        }
    # Preserve recovery for the legacy unscoped lease even when there are
    # no rows to inventory, then handle every validated account world.
    world_ids = list(dict.fromkeys(["", *world_ids]))
    worlds = [_store.recover_dead_local_scoped_abox_write_lease(item) for item in world_ids]
    statuses = [str(item.get("status") or "") for item in worlds]
    cleared_worlds = [str(item.get("worldId") or "") for item in worlds if str(item.get("status") or "") == "cleared"]
    errors = [item for item in worlds if str(item.get("status") or "") in {"error", "unavailable"}]
    if errors and cleared_worlds:
        status = "partial"
    elif errors:
        status = "error"
    elif cleared_worlds:
        status = "cleared"
    else:
        status = "skipped"
    return {
        "configured": True,
        "status": status,
        "graphStore": "typedb",
        "worldCount": len(worlds),
        "clearedCount": len(cleared_worlds),
        "clearedWorldIds": cleared_worlds,
        "worlds": worlds,
        "statuses": statuses,
    }


def recover_scoped_abox_write_lease_after_server_start_for_world(_store: ProjectionLockRecoveryStore, world_id: str='') -> Dict[str, object]:
    """Clear a lease after TypeDB itself has restarted.

    A scoped ABox writer holds a durable lease across bounded TypeDB write
    transactions. A fresh TypeDB server cannot still have a writer from the
    previous server process, so the service manager can reclaim this row
    before any dependent workers start. A normal live seed must never pass
    this recovery path.
    """
    if not str(getattr(_store, "address", "") or "").strip():
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "reason": "TypeDB ontology storage is not configured.",
        }
    try:
        existing = _store.scoped_abox_write_lease_status(world_id)
    except Exception as error:  # noqa: BLE001 - a fresh database may not have schema rows yet.
        return {
            "configured": True,
            "status": "unavailable",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "reason": str(error)[:180],
        }
    if str(existing.get("status") or "") == "empty":
        return {
            "configured": True,
            "status": "empty",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
        }
    owner = str(existing.get("leaseOwner") or "")
    properties_json = str(existing.get("propertiesJson") or "")
    if not owner or not properties_json:
        return {
            "configured": True,
            "status": "invalid",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "reason": "Scoped ABox write lease has no exact ownership payload.",
        }
    imported = _store.driver_imports()
    if imported[0] is None:
        return {
            "configured": True,
            "status": "driver-missing",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "reason": str(imported[1])[:180],
        }

    def operation():
        driver = _store.open_driver(imported)
        try:
            _store.ensure_database(driver)
            # ``scoped_abox_write_lease_status`` above already read this
            # exact durable control row.  Re-reading the complete schema
            # before deleting it can dominate TypeDB server startup on a
            # large graph, while it adds no safety: a missing schema would
            # have made the keyed lease probe unavailable.  New databases
            # never reach this write path.
            return _store.delete_scoped_abox_write_lease(driver, imported, {
                "owner": owner,
                "propertiesJson": properties_json,
                "worldId": str(world_id or ""),
                "storageId": _store.scoped_abox_write_lease_storage_id(world_id),
            })
        finally:
            _store.close_driver(driver)

    try:
        deleted = _store.with_typedb_retries(operation)
    except Exception as error:  # noqa: BLE001 - seed can continue and surface the recovery state.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "previousLeaseOwner": owner,
            "reason": str(error)[:180],
        }
    return {
        "configured": True,
        "status": "cleared" if str((deleted or {}).get("status") or "") == "released" else str((deleted or {}).get("status") or "error"),
        "graphStore": "typedb",
        "worldId": str(world_id or ""),
        "previousLeaseOwner": owner,
        "previousLeaseExpiresAtEpoch": float(number_or_none(existing.get("leaseExpiresAtEpoch")) or 0),
        "release": dict(deleted or {}),
    }


def recover_all_scoped_abox_write_leases_after_server_start(_store: ProjectionLockRecoveryStore) -> Dict[str, object]:
    """Clear every validated lease after a fresh TypeDB server startup."""
    if not str(getattr(_store, "address", "") or "").strip():
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
            "worlds": [],
        }
    try:
        world_ids = _store.scoped_abox_write_lease_world_ids()
    except Exception as error:  # noqa: BLE001 - seed must surface the unavailable inventory without hiding it.
        return {
            "configured": True,
            "status": "unavailable",
            "graphStore": "typedb",
            "reason": str(error)[:180],
            "worlds": [],
        }
    world_ids = list(dict.fromkeys(["", *world_ids]))
    worlds = [_store.recover_scoped_abox_write_lease_after_server_start_for_world(item) for item in world_ids]
    statuses = [str(item.get("status") or "") for item in worlds]
    cleared_worlds = [str(item.get("worldId") or "") for item in worlds if str(item.get("status") or "") == "cleared"]
    errors = [item for item in worlds if str(item.get("status") or "") in {"error", "unavailable", "driver-missing", "invalid"}]
    if errors and cleared_worlds:
        status = "partial"
    elif errors:
        status = "error"
    elif cleared_worlds:
        status = "cleared"
    elif all(item == "empty" for item in statuses):
        status = "empty"
    else:
        status = "skipped"
    return {
        "configured": True,
        "status": status,
        "graphStore": "typedb",
        "worldCount": len(worlds),
        "clearedCount": len(cleared_worlds),
        "clearedWorldIds": cleared_worlds,
        "worlds": worlds,
        "statuses": statuses,
    }


def recover_scoped_abox_write_lease_after_server_start(_store: ProjectionLockRecoveryStore) -> Dict[str, object]:
    """Clear all leases after TypeDB itself has restarted."""
    return _store.recover_all_scoped_abox_write_leases_after_server_start()


def recover_scoped_abox_write_lease_after_managed_shutdown(_store: ProjectionLockRecoveryStore) -> Dict[str, object]:
    """Recover only a proven-dead local writer after worker restart.

    A project manager restart does not prove that an independently started
    CLI process is absent. Reuse the local owner identity check instead of
    treating a worker restart like a TypeDB server restart.
    """
    return _store.recover_all_dead_local_scoped_abox_write_leases()
