"""Capabilities for selection; no runtime construction."""

from __future__ import annotations
from digital_twin.domain.portfolio import AccountSnapshot
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class SelectionPort(Protocol):
    def audited_prior_rule_selection_context(
        self,
        snapshot: AccountSnapshot,
        inference_symbols: List[str],
        candidate_scope_plan: List[Dict[str, object]] = None,
        rulebox_rules_hash: str = "",
        tbox_fingerprint: str = "",
        world_id: str = "",
        requested_fact_families: List[str] = None,
        requested_fact_families_by_symbol: Dict[str, List[str]] = None,
    ) -> Dict[str, object]: ...

    def execution_namespace(self) -> Dict[str, str]: ...

    projection_run_store: Any

    def rulebox_rules_for_impact(self) -> List[Dict[str, object]]: ...

    settings: Any
