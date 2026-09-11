"""Capabilities for recovery; no runtime construction."""

from __future__ import annotations
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_projection_audit import OntologyProjectionRun
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class RecoveryPort(Protocol):
    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    def active_graph_store_key(self, result: Dict[str, object] = None) -> str: ...

    def attach_graph_store_inference_result(
        self,
        result: Dict[str, object],
        snapshot: AccountSnapshot,
        target_symbols: List[str] = None,
        inference_impact_plan: Dict[str, object] = None,
        world_id: str = "",
        candidate_scope_plan: List[Dict[str, object]] = None,
        rulebox_rules_hash: str = "",
        tbox_fingerprint: str = "",
        preflight_graph: PortfolioOntology = None,
        preflight_manifest_id: str = "",
    ) -> None: ...

    def attach_inference_reuse_proof(
        self, projection_run: OntologyProjectionRun, result: Dict[str, object]
    ) -> None: ...

    def inference_result_is_reusable(
        self,
        inferencebox: Dict[str, object],
        active_abox: Dict[str, object],
        required_symbols: List[str] = None,
    ) -> bool: ...

    def inference_snapshot_limit(self) -> int: ...

    def matched_rule_ids_from_inference_payload(
        self, payload: Dict[str, object]
    ) -> List[str]: ...

    projection_run_store: Any

    repository: Any

    def repository_world_call(
        self, method_name: str, *args, world_id: str = "", **kwargs
    ): ...
