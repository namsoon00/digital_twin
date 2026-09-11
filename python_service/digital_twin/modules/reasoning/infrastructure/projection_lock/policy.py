"""projection_lock: policy through explicit injected capabilities."""

from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.abox_candidates.identity import (
    ontology_storage_id,
)
from digital_twin.modules.reasoning.infrastructure.backend_constants import (
    SCOPED_ABOX_WRITE_LEASE_BOX,
    SCOPED_ABOX_WRITE_LEASE_ID,
)
from typing import Dict
import hashlib
import os
from .policy_ports import ProjectionLockPolicyStore, ProjectionLockPolicyRuntime


def scoped_abox_write_lease_seconds(
    _store: ProjectionLockPolicyStore,
    settings: Dict[str, object] = None,
    *,
    _bindings: ProjectionLockPolicyRuntime
) -> int:
    """Return a bounded cross-process lease for one scoped ABox writer."""
    raw = (settings or _bindings.runtime_settings()).get("typedbScopedABoxLeaseSeconds")
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 900
    # A first migration writes a full world in bounded batches. The lease
    # must outlast a normal write, while still recovering after a crashed
    # local worker instead of blocking the graph indefinitely.
    return max(120, min(3600, int(parsed)))


def typedb_projection_coordinator_enabled(
    _store: ProjectionLockPolicyStore,
    settings: Dict[str, object] = None,
    *,
    _bindings: ProjectionLockPolicyRuntime
) -> bool:
    """Whether one physical TypeDB writer coordinates logical worlds."""
    value = (
        str(
            (settings or _bindings.runtime_settings()).get(
                "typedbProjectionCoordinatorEnabled",
                "1",
            )
            or ""
        )
        .strip()
        .lower()
    )
    return value not in {"0", "false", "no", "off", "disabled"}


def typedb_projection_coordinator_lease_seconds(
    _store: ProjectionLockPolicyStore,
    settings: Dict[str, object] = None,
    *,
    _bindings: ProjectionLockPolicyRuntime
) -> int:
    raw = (settings or _bindings.runtime_settings()).get("typedbProjectionCoordinatorLeaseSeconds")
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 600
    # A complete scoped write plus native-rule materialization can take a
    # few minutes. Keep recovery materially faster than the per-world
    # 15-minute safety lease without expiring a healthy live cycle.
    return max(300, min(1800, int(parsed)))


def typedb_projection_coordinator_retry_seconds(
    _store: ProjectionLockPolicyStore,
    settings: Dict[str, object] = None,
    *,
    _bindings: ProjectionLockPolicyRuntime
) -> int:
    raw = (settings or _bindings.runtime_settings()).get("typedbProjectionCoordinatorRetrySeconds")
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 10
    return max(5, min(120, int(parsed)))


def scoped_abox_write_lease_storage_id(world_id: str = "") -> str:
    lease_id = SCOPED_ABOX_WRITE_LEASE_ID
    if str(world_id or "").strip():
        lease_id += ":world:" + hashlib.sha256(str(world_id).encode("utf-8")).hexdigest()[:16]
    return ontology_storage_id(
        {"ontologyBox": SCOPED_ABOX_WRITE_LEASE_BOX, "worldId": str(world_id or "")},
        lease_id,
        "node",
    )


def local_process_alive(process_id: object) -> bool:
    """Return whether a locally recorded lease owner still exists."""
    try:
        pid = int(process_id or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A permission error proves that a process exists, even though the
        # local user cannot signal it.
        return True
    except OSError:
        # Treat an unknown OS state as live: recovery must be conservative.
        return True
    return True
