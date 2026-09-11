"""Capabilities for record; no runtime construction."""

from __future__ import annotations
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_projection_audit import OntologyProjectionRun
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from typing import Dict, List, Protocol


class CreateAuditPort(Protocol):

    def active_graph_store_key(self, result: Dict[str, object] = None) -> str: ...

    def begin_projection_audit_run(
        self,
        snapshot: AccountSnapshot,
        graph: PortfolioOntology,
        material_fingerprint: str,
        abox_snapshot_id: str,
        inference_symbols: List[str],
        rulebox_metadata: Dict[str, object],
        reasoning_context: Dict[str, object] = None,
    ): ...

    def store_projection_result(
        self,
        snapshot: AccountSnapshot,
        result: Dict[str, object],
        projection_run: OntologyProjectionRun = None,
    ) -> None: ...
