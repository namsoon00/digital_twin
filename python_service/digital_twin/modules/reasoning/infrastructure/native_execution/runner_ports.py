"""Explicit capabilities for native_execution/runner; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Dict


class NativeExecutionRunnerStore(Protocol):
    _inference_write_lease_enabled: Any

    def _run_rulebox_unlocked(self, payload: Dict[str, object] = None) -> Dict[str, object]: ...

    def acquire_scoped_abox_write_lease(
        self, manifest_id: str = "", world_id: str = "", lease_seconds: int = 0
    ) -> Dict[str, object]: ...

    address: Any

    def native_rule_execution_enabled(self) -> bool: ...

    def release_scoped_abox_write_lease(self, lease: Dict[str, object]) -> Dict[str, object]: ...

    def scoped_abox_write_lease_status(self, world_id: str = "") -> Dict[str, object]: ...


@dataclass(frozen=True)
class NativeExecutionRunnerRuntime:
    NullTypeDBOntologyGraphRepository: Callable[..., Any]
