"""Capabilities for record; no runtime construction."""

from __future__ import annotations
from digital_twin.modules.reasoning.domain.ontology_projection_audit import OntologyProjectionRun
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from typing import Any, Dict, Protocol


class PrepareAttemptPort(Protocol):

    def active_graph_store_key(self, result: Dict[str, object] = None) -> str: ...

    def active_projection_audit_run(self, world_id: str = ""): ...

    def has_projectable_data(self, snapshot: AccountSnapshot) -> bool: ...

    def interrupted_projection_recovery_required(self, world_id: str) -> bool: ...

    def reconcile_interrupted_projection_audit(
        self, world_id: str = ""
    ) -> Dict[str, object]: ...

    def recover_pending_abox_activation(
        self, world_id: str = "", max_staged_target_symbols: int = 0
    ) -> Dict[str, object]: ...

    repository: Any

    def resume_staged_pending_abox_activation(
        self, snapshot: AccountSnapshot, world_id: str, recovery: Dict[str, object]
    ) -> Dict[str, object]: ...

    def scheduler_target_symbol_limit(
        self, reasoning_context: Dict[str, object] = None
    ) -> int: ...

    settings: Any

    def store_projection_result(
        self,
        snapshot: AccountSnapshot,
        result: Dict[str, object],
        projection_run: OntologyProjectionRun = None,
    ) -> None: ...

    def typedb_projection_deferred(self) -> bool: ...
