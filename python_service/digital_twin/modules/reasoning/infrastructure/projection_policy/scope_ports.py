"""Capabilities for scope; no runtime construction."""

from __future__ import annotations
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class ScopePort(Protocol):
    def active_graph_store_key(self, result: Dict[str, object] = None) -> str: ...

    def incremental_equivalence_audit_sample_pct(self) -> int: ...

    def inference_symbols(
        self, snapshot: AccountSnapshot, target_symbols: List[str] = None
    ) -> List[str]: ...

    def native_inference_symbol_limit(self) -> int: ...

    settings: Any

    def snapshot_symbols(self, snapshot: AccountSnapshot) -> List[str]: ...
