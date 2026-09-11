"""Capabilities for record; no runtime construction."""

from __future__ import annotations
from digital_twin.domain.ontology_projection_audit import OntologyProjectionRun
from digital_twin.domain.portfolio import AccountSnapshot
from typing import Callable, Dict, List, Protocol


class AssembleSourcePort(Protocol):

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

    def ensure_rulebox_ready(self) -> Dict[str, object]: ...

    def store_projection_result(
        self,
        snapshot: AccountSnapshot,
        result: Dict[str, object],
        projection_run: OntologyProjectionRun = None,
    ) -> None: ...
