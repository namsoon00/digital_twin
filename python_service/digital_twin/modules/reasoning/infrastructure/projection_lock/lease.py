"""projection_lock: lease through explicit injected capabilities."""

from digital_twin.domain.ontology_contracts import OntologyEntity, PortfolioOntology
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.backend_constants import (
    SCOPED_ABOX_WRITE_LEASE_BOX,
    SCOPED_ABOX_WRITE_LEASE_ID,
    SCOPED_ABOX_WRITE_LEASE_VERSION,
    TYPEDB_PROJECTION_COORDINATOR_WORLD_ID,
)
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import json_object
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string
from typing import Dict, List, Tuple
import hashlib
import os
import socket
import time
import uuid
from .lease_ports import ProjectionLockLeaseStore, ProjectionLockLeaseRuntime


def scoped_abox_write_lease_rows(
    _store: ProjectionLockLeaseStore, world_id: str = ""
) -> List[Dict[str, object]]:
    """Read the durable lease without treating it as an ontology fact."""
    query = (
        "match $n isa ontology-node, "
        "has ontology-id "
        + typedb_string(
            SCOPED_ABOX_WRITE_LEASE_ID
            + (
                ":world:" + hashlib.sha256(str(world_id).encode("utf-8")).hexdigest()[:16]
                if str(world_id or "").strip()
                else ""
            )
        )
        + ", "
        "has ontology-box " + typedb_string(SCOPED_ABOX_WRITE_LEASE_BOX) + ", "
        "has ontology-storage-id "
        + typedb_string(_store.scoped_abox_write_lease_storage_id(world_id))
        + ", "
        "has ontology-updated-at $updatedAt, has ontology-json $json;"
    )
    return _store.read_rows(
        query,
        ["updatedAt", "json"],
        label="typedb.scoped-abox-write-lease",
    )


def scoped_abox_write_lease_world_ids(_store: ProjectionLockLeaseStore) -> List[str]:
    """List worlds with a durable scoped ABox lease.

    Leases are stored outside the world generations, so a worker restart
    cannot infer their worlds from the active ABox alone. Only accept rows
    whose deterministic id and storage id agree with the embedded world
    id; recovery must never inspect or delete another control record.
    """
    query = (
        "match $n isa ontology-node, "
        "has ontology-id $id, "
        "has ontology-box " + typedb_string(SCOPED_ABOX_WRITE_LEASE_BOX) + ", "
        "has ontology-storage-id $storageId, "
        "has ontology-updated-at $updatedAt, has ontology-json $json;"
    )
    rows = _store.read_rows(
        query,
        ["id", "storageId", "updatedAt", "json"],
        label="typedb.scoped-abox-write-lease-worlds",
    )
    worlds = set()
    prefix = SCOPED_ABOX_WRITE_LEASE_ID + ":world:"
    for row in rows:
        lease_id = str(row.get("id") or "")
        if lease_id != SCOPED_ABOX_WRITE_LEASE_ID and not lease_id.startswith(prefix):
            continue
        payload = json_object(row.get("json"))
        world_id = str(payload.get("worldId") or "")
        expected_id = SCOPED_ABOX_WRITE_LEASE_ID + (
            ":world:" + hashlib.sha256(world_id.encode("utf-8")).hexdigest()[:16]
            if world_id.strip()
            else ""
        )
        if lease_id != expected_id:
            continue
        expected_storage_id = _store.scoped_abox_write_lease_storage_id(world_id)
        if str(row.get("storageId") or "") != expected_storage_id:
            continue
        worlds.add(world_id)
    return sorted(worlds)


def scoped_abox_write_lease_status(
    _store: ProjectionLockLeaseStore, world_id: str = ""
) -> Dict[str, object]:
    rows = list(_store.scoped_abox_write_lease_rows(world_id) or [])
    if not rows:
        return {
            "status": "empty",
            "leaseId": SCOPED_ABOX_WRITE_LEASE_ID,
            "leaseBox": SCOPED_ABOX_WRITE_LEASE_BOX,
            "worldId": str(world_id or ""),
        }
    row = sorted(rows, key=lambda item: str(item.get("updatedAt") or ""), reverse=True)[0]
    payload = json_object(row.get("json"))
    expires_at = float(number_or_none(payload.get("leaseExpiresAtEpoch")) or 0)
    owner = str(payload.get("leaseOwner") or "")
    lease_host = str(payload.get("leaseHost") or "")
    lease_process_id = number_or_none(payload.get("leaseProcessId"))
    status = "held" if expires_at > time.time() else "expired"
    return {
        "status": status,
        "leaseId": SCOPED_ABOX_WRITE_LEASE_ID,
        "leaseBox": SCOPED_ABOX_WRITE_LEASE_BOX,
        "worldId": str(world_id or ""),
        "leaseOwner": owner,
        "leaseToken": str(payload.get("leaseToken") or ""),
        "leaseHost": lease_host,
        "leaseProcessId": int(lease_process_id) if lease_process_id is not None else None,
        "leaseAcquiredAtEpoch": float(number_or_none(payload.get("leaseAcquiredAtEpoch")) or 0),
        "leaseExpiresAtEpoch": expires_at,
        "leaseRemainingSeconds": max(0, int(expires_at - time.time())),
        "updatedAt": str(row.get("updatedAt") or ""),
        "propertiesJson": str(row.get("json") or "{}"),
    }


def scoped_abox_write_lease_graph(
    _store: ProjectionLockLeaseStore,
    owner: str,
    manifest_id: str = "",
    lease_seconds: int = 0,
    world_id: str = "",
) -> Tuple[PortfolioOntology, Dict[str, object]]:
    acquired_at = time.time()
    lease_settings = (
        {"typedbScopedABoxLeaseSeconds": lease_seconds} if int(lease_seconds or 0) > 0 else None
    )
    expires_at = acquired_at + _store.scoped_abox_write_lease_seconds(lease_settings)
    properties = {
        "ontologyBox": SCOPED_ABOX_WRITE_LEASE_BOX,
        "tboxClass": "ScopedABoxWriteLease",
        "leaseVersion": SCOPED_ABOX_WRITE_LEASE_VERSION,
        "leaseOwner": str(owner or ""),
        "leaseToken": uuid.uuid4().hex,
        "leaseManifestId": str(manifest_id or ""),
        "worldId": str(world_id or ""),
        # A durable lease can outlive a force-stopped local worker.  These
        # fields let the replacement worker reclaim only a proven-dead
        # local owner; they are not used to steal a live or remote lease.
        "leaseHost": socket.gethostname(),
        "leaseProcessId": os.getpid(),
        "leaseAcquiredAtEpoch": acquired_at,
        "leaseExpiresAtEpoch": expires_at,
    }
    graph = PortfolioOntology(
        "typedb-scoped-abox-lease",
        entities=[
            OntologyEntity(
                entity_id=SCOPED_ABOX_WRITE_LEASE_ID
                + (
                    ":world:" + hashlib.sha256(str(world_id).encode("utf-8")).hexdigest()[:16]
                    if str(world_id or "").strip()
                    else ""
                ),
                label="Scoped ABox write lease",
                kind="scoped-abox-write-lease",
                properties=properties,
            )
        ],
    )
    row = _store.node_rows(graph)[0]
    return graph, {
        "owner": str(owner or ""),
        "leaseToken": str(properties.get("leaseToken") or ""),
        "manifestId": str(manifest_id or ""),
        "worldId": str(world_id or ""),
        "storageId": _store.scoped_abox_write_lease_storage_id(world_id),
        "expiresAtEpoch": expires_at,
        "propertiesJson": str(row.get("propertiesJson") or "{}"),
    }


def delete_scoped_abox_write_lease(
    _store: ProjectionLockLeaseStore,
    driver,
    imported,
    lease: Dict[str, object],
    *,
    _bindings: ProjectionLockLeaseRuntime
) -> Dict[str, object]:
    """Delete only the exact owner record, never a successor's lease."""
    owner = str((lease or {}).get("owner") or "")
    properties_json = str((lease or {}).get("propertiesJson") or "")
    if not owner or not properties_json:
        return {"status": "skipped", "reason": "Lease ownership payload is incomplete."}
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    storage_id = str(
        (lease or {}).get("storageId")
        or _store.scoped_abox_write_lease_storage_id(str((lease or {}).get("worldId") or ""))
    )
    query = (
        "match $n isa ontology-node, has ontology-storage-id "
        + typedb_string(storage_id)
        + ", has ontology-json "
        + typedb_string(properties_json)
        + "; delete $n;"
    )

    def operation():
        with _bindings.typedb_operation_timeout(
            _store.write_operation_timeout_seconds(), "TypeDB scoped ABox lease release"
        ):
            with driver.transaction(
                _store.database,
                TransactionType.WRITE,
                options=_store.write_transaction_options(),
            ) as tx:
                tx.query(query).resolve()
                tx.commit()

    _store.with_typedb_retries(operation)
    return {"status": "released", "leaseOwner": owner}


def acquire_scoped_abox_write_lease(
    _store: ProjectionLockLeaseStore,
    manifest_id: str = "",
    world_id: str = "",
    lease_seconds: int = 0,
) -> Dict[str, object]:
    """Serialize multi-transaction scoped writes across local workers.

    A TypeDB write transaction protects only one batch. Without this lease,
    two projections can both clear a shared macro/reference generation and
    leave each other with an incomplete candidate. The lease itself is
    outside ABox/ABoxControl so activation swaps do not affect it.
    """
    owner = "scoped-abox:" + uuid.uuid4().hex
    existing = _store.scoped_abox_write_lease_status(world_id)
    recovery: Dict[str, object] = {}
    if str(existing.get("status") or "") == "held":
        # Normal startup deliberately does not inventory every account
        # world.  Recover a durable lease only when this exact world needs
        # to write, and only if its recorded local process is proven dead.
        # This preserves live and remote ownership while avoiding a global
        # TypeDB control-plane scan on every worker restart.
        recovery = _store.recover_dead_local_scoped_abox_write_lease(
            world_id,
            recover_untracked_current_process=(
                str(world_id or "") == TYPEDB_PROJECTION_COORDINATOR_WORLD_ID
            ),
        )
        if str(recovery.get("status") or "") == "cleared":
            existing = _store.scoped_abox_write_lease_status(world_id)
        else:
            return {
                "acquired": False,
                "status": "held",
                "leaseOwner": str(existing.get("leaseOwner") or ""),
                "leaseExpiresAtEpoch": float(
                    number_or_none(existing.get("leaseExpiresAtEpoch")) or 0
                ),
                "recovery": {
                    key: value for key, value in recovery.items() if key != "propertiesJson"
                },
            }
    if str(existing.get("status") or "") == "held":
        return {
            "acquired": False,
            "status": "held",
            "leaseOwner": str(existing.get("leaseOwner") or ""),
            "leaseExpiresAtEpoch": float(number_or_none(existing.get("leaseExpiresAtEpoch")) or 0),
        }
    imported = _store.driver_imports()
    if imported[0] is None:
        return {
            "acquired": False,
            "status": "driver-missing",
            "reason": str(imported[1])[:180],
        }
    graph, lease = _store.scoped_abox_write_lease_graph(
        owner,
        manifest_id,
        lease_seconds=lease_seconds,
        world_id=world_id,
    )

    def operation():
        driver = _store.open_driver(imported)
        try:
            _store.ensure_database(driver)
            _store.ensure_schema(driver, imported)
            if str(existing.get("status") or "") == "expired":
                _store.delete_scoped_abox_write_lease(
                    driver,
                    imported,
                    {
                        "owner": str(existing.get("leaseOwner") or "expired"),
                        "propertiesJson": str(existing.get("propertiesJson") or ""),
                        "worldId": str(world_id or ""),
                        "storageId": _store.scoped_abox_write_lease_storage_id(world_id),
                    },
                )
            try:
                _store.write_graph(driver, imported, graph, delete_boxes=[])
            except Exception:
                current = _store.scoped_abox_write_lease_status(world_id)
                if str(current.get("status") or "") in {"held", "expired"}:
                    return {
                        "acquired": False,
                        "status": "held",
                        "leaseOwner": str(current.get("leaseOwner") or ""),
                        "leaseExpiresAtEpoch": float(
                            number_or_none(current.get("leaseExpiresAtEpoch")) or 0
                        ),
                    }
                raise
        finally:
            _store.close_driver(driver)
        current = _store.scoped_abox_write_lease_status(world_id)
        if str(current.get("leaseOwner") or "") != owner:
            return {
                "acquired": False,
                "status": "held",
                "leaseOwner": str(current.get("leaseOwner") or ""),
                "leaseExpiresAtEpoch": float(
                    number_or_none(current.get("leaseExpiresAtEpoch")) or 0
                ),
            }
        return {
            "acquired": True,
            "status": "acquired",
            "leaseOwner": owner,
            "leaseToken": str(lease.get("leaseToken") or ""),
            "leaseExpiresAtEpoch": float(lease.get("expiresAtEpoch") or 0),
            "propertiesJson": str(lease.get("propertiesJson") or "{}"),
            "worldId": str(world_id or ""),
            "storageId": str(lease.get("storageId") or ""),
        }

    return _store.with_typedb_retries(operation)


def release_scoped_abox_write_lease(
    _store: ProjectionLockLeaseStore, lease: Dict[str, object]
) -> Dict[str, object]:
    if not (lease or {}).get("acquired"):
        return {"status": "not-owner"}
    imported = _store.driver_imports()
    if imported[0] is None:
        return {"status": "driver-missing", "reason": str(imported[1])[:180]}

    def operation():
        driver = _store.open_driver(imported)
        try:
            _store.ensure_database(driver)
            return _store.delete_scoped_abox_write_lease(
                driver,
                imported,
                {
                    **dict(lease or {}),
                    "owner": str(
                        (lease or {}).get("owner") or (lease or {}).get("leaseOwner") or ""
                    ),
                },
            )
        finally:
            _store.close_driver(driver)

    return _store.with_typedb_retries(operation)
