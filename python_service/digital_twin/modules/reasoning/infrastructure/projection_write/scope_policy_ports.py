"""Capabilities for scope policy; no runtime construction."""

from __future__ import annotations
from digital_twin.domain.portfolio import AccountSnapshot
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class ScopePolicyPort(Protocol):
    def active_graph_store_key(self, result: Dict[str, object] = None) -> str: ...

    def bounded_native_inference_symbols(
        self,
        snapshot: AccountSnapshot,
        inferred_symbols: List[str],
        requested_symbols: List[str] = None,
        scheduler_target_symbol_limit: int = 0,
    ) -> List[str]: ...

    def inference_impact_plan(
        self,
        snapshot: AccountSnapshot,
        active_abox: Dict[str, object],
        scoped_identity: Dict[str, object],
        target_symbols: List[str] = None,
        reasoning_context: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def inference_symbols(
        self, snapshot: AccountSnapshot, target_symbols: List[str] = None
    ) -> List[str]: ...

    def reasoning_queue_pressure(
        self, reasoning_context: Dict[str, object] = None
    ) -> Dict[str, object]: ...

    repository: Any

    def rulebox_rules_for_impact(self) -> List[Dict[str, object]]: ...

    def scheduler_target_symbol_limit(
        self, reasoning_context: Dict[str, object] = None
    ) -> int: ...

    def scope_integrity_audit_age_minutes(self, active_metadata: Dict[str, object]): ...

    def scope_integrity_audit_interval_minutes(self) -> float: ...

    settings: Any

    def snapshot_symbols(self, snapshot: AccountSnapshot) -> List[str]: ...

    def world_partitioned_reasoning_enabled(self) -> bool: ...
