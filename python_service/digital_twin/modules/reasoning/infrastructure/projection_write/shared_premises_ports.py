"""Capabilities for shared premises; no runtime construction."""

from __future__ import annotations
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.portfolio import AccountSnapshot
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class SharedPremisesPort(Protocol):
    _frozen_tbox_metadata: Any

    def active_tbox_context(self) -> Dict[str, object]: ...

    def build_graph_assembly(
        self,
        snapshot: AccountSnapshot,
        rule_catalog: Dict[str, object],
        target_symbols: List[str] = None,
        target_scoped_input: bool = False,
        progress_callback: Callable[..., None] = None,
        reasoning_context: Dict[str, object] = None,
    ) -> tuple: ...

    def catalog_for_rules(
        self, rule_catalog: Dict[str, object], rules
    ) -> Dict[str, object]: ...

    def compact_shared_inference_reuse(
        self,
        active_abox: Dict[str, object],
        selection_context: Dict[str, object],
        symbols: List[str],
        world_id: str,
    ) -> tuple: ...

    def ensure_rulebox_ready(self) -> Dict[str, object]: ...

    def execution_namespace(self) -> Dict[str, str]: ...

    def inference_result_is_reusable(
        self,
        inferencebox: Dict[str, object],
        active_abox: Dict[str, object],
        required_symbols: List[str] = None,
    ) -> bool: ...

    def inference_snapshot_limit(self) -> int: ...

    def inference_symbols(
        self, snapshot: AccountSnapshot, target_symbols: List[str] = None
    ) -> List[str]: ...

    def project_shared_world_update(
        self, update: PortfolioOntology, shared_world, projection_kind: str = "market"
    ) -> Dict[str, object]: ...

    projection_run_store: Any

    repository: Any

    def repository_world_call(
        self, method_name: str, *args, world_id: str = "", **kwargs
    ): ...

    def reused_shared_premise_result(
        self,
        *,
        inference: Dict[str, object],
        active_abox: Dict[str, object],
        selection_context: Dict[str, object],
        shared_world,
        shared_rule_ids: List[str],
        overlay_rule_ids: List[str],
        shared_rulebox_hash: str,
        shared_tbox_fingerprint: str,
        requested_symbols: List[str],
        evaluated_symbols: List[str],
        not_evaluated_symbols: List[str],
        catalog: Dict[str, object],
        preflight: Dict[str, object],
        runtime_stages: Dict[str, int],
        started_at: float,
        reuse_mode: str,
    ) -> Dict[str, object]: ...

    def rulebox_rules_for_impact(self) -> List[Dict[str, object]]: ...

    settings: Any

    def snapshot_symbols(self, snapshot: AccountSnapshot) -> List[str]: ...

    def world_partitioned_reasoning_enabled(self) -> bool: ...

    def world_rule_partition(
        self, rule_catalog: Dict[str, object]
    ) -> Dict[str, object]: ...


@dataclass(frozen=True)
class PrepareSharedPremisesBindings:
    compact_staged_abox_activation_lifecycle: Callable[..., Any]
    shared_premise_evaluation_plan: Callable[..., Any]
