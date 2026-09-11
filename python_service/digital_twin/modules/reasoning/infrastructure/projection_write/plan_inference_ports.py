"""Capabilities for record; no runtime construction."""

from __future__ import annotations
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from typing import Any, Dict, List, Protocol


class PlanInferencePort(Protocol):

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

    def scheduler_target_symbol_limit(
        self, reasoning_context: Dict[str, object] = None
    ) -> int: ...

    settings: Any
