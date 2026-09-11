"""Capabilities for record; no runtime construction."""

from __future__ import annotations
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from typing import Callable, Dict, List, Protocol


class SelectSourcePort(Protocol):

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    def build_projection_graph(
        self,
        snapshot: AccountSnapshot,
        rule_catalog: Dict[str, object],
        portfolio_world_context,
        market_world_context=None,
        target_symbols: List[str] = None,
        target_scoped_input: bool = False,
        progress_callback: Callable[..., None] = None,
        shared_premise_proof: Dict[str, object] = None,
        reasoning_context: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def target_scoped_patch_targets(
        self,
        snapshot: AccountSnapshot,
        active_metadata: Dict[str, object],
        scoped_identity: Dict[str, object],
        requested_symbols: List[str] = None,
        reasoning_context: Dict[str, object] = None,
    ) -> Dict[str, object]: ...
