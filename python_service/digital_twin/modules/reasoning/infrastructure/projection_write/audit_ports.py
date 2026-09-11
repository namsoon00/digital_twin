"""Capabilities for audit; no runtime construction."""

from __future__ import annotations
from digital_twin.modules.reasoning.domain.ontology_projection_audit import OntologyProjectionRun
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class AuditPort(Protocol):
    def active_graph_store_key(self, result: Dict[str, object] = None) -> str: ...

    def attach_inference_reuse_proof(
        self, projection_run: OntologyProjectionRun, result: Dict[str, object]
    ) -> None: ...

    def execution_namespace(self) -> Dict[str, str]: ...

    def finalize_current_state_transition(
        self, completed_run: OntologyProjectionRun, result: Dict[str, object]
    ) -> Dict[str, object]: ...

    projection_run_store: Any

    settings: Any
