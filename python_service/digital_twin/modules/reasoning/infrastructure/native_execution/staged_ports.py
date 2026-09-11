"""Explicit capabilities for native_execution/staged; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Dict, Iterable


class NativeExecutionStagedStore(Protocol):
    def _run_rulebox_unlocked(self, payload: Dict[str, object] = None) -> Dict[str, object]: ...

    def acquire_scoped_abox_write_lease(
        self, manifest_id: str = "", world_id: str = "", lease_seconds: int = 0
    ) -> Dict[str, object]: ...

    def activate_abox_generation(
        self, snapshot_id: str, world_id: str = ""
    ) -> Dict[str, object]: ...

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    address: Any

    def finalize_abox_generation(
        self, active_snapshot_id: str, previous_snapshot_id: str = "", world_id: str = ""
    ) -> Dict[str, object]: ...

    def inferencebox_matches_pending_abox_activation(
        self,
        inferencebox: Dict[str, object],
        candidate_snapshot_id: str,
        target_symbols: Iterable[str] = None,
    ) -> bool: ...

    def prepare_pending_abox_activation_for_inference(
        self, world_id: str = ""
    ) -> Dict[str, object]: ...

    def release_scoped_abox_write_lease(self, lease: Dict[str, object]) -> Dict[str, object]: ...


@dataclass(frozen=True)
class NativeExecutionStagedRuntime:
    NullTypeDBOntologyGraphRepository: Callable[..., Any]
    typedb_projection_coordinator_summary: Callable[..., Any]
