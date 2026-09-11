"""Capabilities for record; no runtime construction."""

from __future__ import annotations
from digital_twin.modules.reasoning.domain.ontology_projection_audit import OntologyProjectionRun
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from typing import Any, Dict, Protocol


class ValidateManifestPort(Protocol):

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    def active_graph_store_key(self, result: Dict[str, object] = None) -> str: ...

    def current_state_abox_storage_enabled(self) -> bool: ...

    repository: Any

    def repository_world_call(
        self, method_name: str, *args, world_id: str = "", **kwargs
    ): ...

    def store_projection_result(
        self,
        snapshot: AccountSnapshot,
        result: Dict[str, object],
        projection_run: OntologyProjectionRun = None,
    ) -> None: ...
