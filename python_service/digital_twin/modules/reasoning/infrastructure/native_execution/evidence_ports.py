"""Explicit capabilities for native_execution/evidence; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.domain.ontology_contracts import PortfolioOntology
from typing import Dict, Iterable, List, Tuple


class NativeExecutionEvidenceStore(Protocol):
    def graph_persistence_rows(
        self, graph: PortfolioOntology
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]: ...

    def read_abox_entity_rows_by_storage_ids(
        self, storage_ids: Iterable[str], world_id: str = ""
    ) -> List[Dict[str, object]]: ...

    def read_abox_relation_rows_by_storage_ids(
        self, storage_ids: Iterable[str], relation_types: Iterable[str] = None, world_id: str = ""
    ) -> List[Dict[str, object]]: ...

    def read_entity_rows_by_ids(
        self, ids: Iterable[str], boxes: Iterable[str] = None, world_id: str = ""
    ) -> List[Dict[str, object]]: ...

    def read_relation_rows_by_source_ids(
        self,
        source_ids: Iterable[str],
        boxes: Iterable[str] = None,
        relation_types: Iterable[str] = None,
        include_incoming: bool = True,
        world_id: str = "",
    ) -> List[Dict[str, object]]: ...
