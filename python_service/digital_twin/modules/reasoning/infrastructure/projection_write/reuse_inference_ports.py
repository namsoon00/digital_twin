"""Capabilities for record; no runtime construction."""

from __future__ import annotations
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_projection_audit import OntologyProjectionRun
from digital_twin.domain.portfolio import AccountSnapshot
from typing import Dict, List, Protocol


class ReuseInferencePort(Protocol):

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

    def existing_inference_result(
        self,
        snapshot: AccountSnapshot,
        target_symbols: List[str] = None,
        world_id: str = "",
    ) -> Dict[str, object]: ...

    def inference_result_is_reusable(
        self,
        inferencebox: Dict[str, object],
        active_abox: Dict[str, object],
        required_symbols: List[str] = None,
    ) -> bool: ...

    def native_preflight_projection_graph(
        self, graph: PortfolioOntology, persistence: Dict[str, object]
    ) -> PortfolioOntology: ...

    def store_projection_result(
        self,
        snapshot: AccountSnapshot,
        result: Dict[str, object],
        projection_run: OntologyProjectionRun = None,
    ) -> None: ...
