"""Capabilities for record; no runtime construction."""

from __future__ import annotations
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_projection_audit import OntologyProjectionRun
from digital_twin.domain.portfolio import AccountSnapshot
from typing import Any, Dict, List, Protocol


class PublishCandidatePort(Protocol):

    def acquire_projection_coordinator_lease(
        self, owner: str, world_id: str
    ) -> Dict[str, object]: ...

    def advance_current_state_transition(
        self,
        projection_run: OntologyProjectionRun,
        stage: str,
        status: str = "running",
        inference_generation_id: str = "",
        detail: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def attach_abox_persistence_runtime_stages(
        self, runtime_stages: Dict[str, int], result: Dict[str, object]
    ) -> None: ...

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

    def native_preflight_projection_graph(
        self, graph: PortfolioOntology, persistence: Dict[str, object]
    ) -> PortfolioOntology: ...

    def projection_coordinator_summary(
        self, lease: Dict[str, object]
    ) -> Dict[str, object]: ...

    def release_projection_coordinator_lease(
        self, lease: Dict[str, object]
    ) -> Dict[str, object]: ...

    repository: Any

    def store_projection_result(
        self,
        snapshot: AccountSnapshot,
        result: Dict[str, object],
        projection_run: OntologyProjectionRun = None,
    ) -> None: ...
