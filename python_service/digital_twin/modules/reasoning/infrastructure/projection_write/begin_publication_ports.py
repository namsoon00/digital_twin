"""Capabilities for record; no runtime construction."""

from __future__ import annotations
from digital_twin.domain.ontology_projection_audit import OntologyProjectionRun
from digital_twin.domain.portfolio import AccountSnapshot
from typing import Any, Dict, Protocol


class BeginPublicationPort(Protocol):

    def active_graph_store_key(self, result: Dict[str, object] = None) -> str: ...

    projection_run_store: Any

    def store_projection_result(
        self,
        snapshot: AccountSnapshot,
        result: Dict[str, object],
        projection_run: OntologyProjectionRun = None,
    ) -> None: ...
