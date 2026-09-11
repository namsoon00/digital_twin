"""Explicit capabilities for projection_lock/coordinator; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from typing import Dict


class ProjectionLockCoordinatorStore(Protocol):
    def _acquire_projection_coordinator_lease(self, owner: str, world_id: str='', allow_adopt: bool=False) -> Dict[str, object]:
        ...

    _active_projection_coordinator_tokens: Any

    _projection_coordinator_local: Any

    _projection_coordinator_registry_lock: Any

    _projection_coordinator_write_enforced: Any

    def _release_projection_coordinator_lease(self, lease: Dict[str, object]) -> Dict[str, object]:
        ...

    def acquire_projection_coordinator_lease(self, owner: str, world_id: str='') -> Dict[str, object]:
        ...

    def acquire_scoped_abox_write_lease(self, manifest_id: str='', world_id: str='', lease_seconds: int=0) -> Dict[str, object]:
        ...

    def active_projection_coordinator_lease(self) -> Dict[str, object]:
        ...

    def forget_projection_coordinator_lease(self, lease: Dict[str, object]) -> None:
        ...

    def recover_dead_local_scoped_abox_write_lease(self, world_id: str='', recover_untracked_current_process: bool=False) -> Dict[str, object]:
        ...

    def release_projection_coordinator_lease(self, lease: Dict[str, object]) -> Dict[str, object]:
        ...

    def release_scoped_abox_write_lease(self, lease: Dict[str, object]) -> Dict[str, object]:
        ...

    def scoped_abox_write_lease_status(self, world_id: str='') -> Dict[str, object]:
        ...

    def track_projection_coordinator_lease(self, lease: Dict[str, object]) -> None:
        ...

    def typedb_projection_coordinator_enabled(self, settings: Dict[str, object]=None) -> bool:
        ...

    def typedb_projection_coordinator_lease_seconds(self, settings: Dict[str, object]=None) -> int:
        ...

    def typedb_projection_coordinator_retry_seconds(self, settings: Dict[str, object]=None) -> int:
        ...
