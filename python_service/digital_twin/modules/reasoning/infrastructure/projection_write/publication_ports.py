"""Capabilities for publication; no runtime construction."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class PublicationPort(Protocol):
    def acquire_projection_coordinator_lease(
        self, owner: str, world_id: str
    ) -> Dict[str, object]: ...

    def active_graph_store_key(self, result: Dict[str, object] = None) -> str: ...

    def inference_alignment_diagnostics(
        self,
        inferencebox: Dict[str, object],
        expected_snapshot_id: str,
        required_symbols: List[str],
    ) -> Dict[str, object]: ...

    def inference_result_is_reusable(
        self,
        inferencebox: Dict[str, object],
        active_abox: Dict[str, object],
        required_symbols: List[str] = None,
    ) -> bool: ...

    def projection_coordinator_summary(
        self, lease: Dict[str, object]
    ) -> Dict[str, object]: ...

    def release_projection_coordinator_lease(
        self, lease: Dict[str, object]
    ) -> Dict[str, object]: ...

    repository: Any

    def repository_world_call(
        self, method_name: str, *args, world_id: str = "", **kwargs
    ): ...
