"""Capabilities for current state; no runtime construction."""

from __future__ import annotations
from digital_twin.domain.ontology_projection_audit import OntologyProjectionRun
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class CurrentStatePort(Protocol):
    def advance_current_state_transition(
        self,
        projection_run: OntologyProjectionRun,
        stage: str,
        status: str = "running",
        inference_generation_id: str = "",
        detail: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    projection_run_store: Any
