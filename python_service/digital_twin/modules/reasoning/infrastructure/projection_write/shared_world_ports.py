"""Capabilities for shared world; no runtime construction."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class SharedWorldPort(Protocol):
    def acquire_projection_coordinator_lease(
        self, owner: str, world_id: str
    ) -> Dict[str, object]: ...

    def active_graph_store_key(self, result: Dict[str, object] = None) -> str: ...

    def projection_coordinator_summary(
        self, lease: Dict[str, object]
    ) -> Dict[str, object]: ...

    def recover_pending_abox_activation(
        self, world_id: str = "", max_staged_target_symbols: int = 0
    ) -> Dict[str, object]: ...

    def release_projection_coordinator_lease(
        self, lease: Dict[str, object]
    ) -> Dict[str, object]: ...

    repository: Any

    def repository_world_call(
        self, method_name: str, *args, world_id: str = "", **kwargs
    ): ...

    def shared_market_world_symbol_limit(self) -> int: ...

    def shared_world_retention_hours(self, projection_kind: str) -> float: ...
