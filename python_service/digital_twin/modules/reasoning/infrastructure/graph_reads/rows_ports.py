"""Explicit capabilities for graph_reads/rows; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.domain.ontology_contracts import PortfolioOntology
from typing import Dict
from typing import Iterable
from typing import List
from typing import Tuple


class GraphReadsRowsStore(Protocol):
    def active_abox_members_clause(self, members: Iterable[Tuple[str, str]], world_id: str='') -> str:
        ...

    def active_abox_metadata(self, world_id: str='') -> Dict[str, object]:
        ...

    def active_abox_relation_types_by_symbol(self, symbols: Iterable[str]=None, timeout_seconds: float=None, world_id: str='', active_abox_metadata: Dict[str, object]=None) -> Dict[str, object]:
        ...

    def entity_rows_from_typeql(self, rows: Iterable[Dict[str, object]], box: str) -> List[Dict[str, object]]:
        ...

    def hypothesis_calibration_snapshot(self, symbols: Iterable[str]=None, limit: int=40, world_id: str='', source_abox_snapshot_id: str='', generation_aligned: bool=False) -> Dict[str, object]:
        ...

    def native_rule_query_timeout_seconds(self) -> float:
        ...

    def read_active_hypothesis_calibration_rows(self, symbols: Iterable[str]=None, limit: int=40, world_id: str='') -> List[Dict[str, object]]:
        ...

    def read_rows(self, query: str, columns: Iterable[str], label: str='typedb.read', timeout_seconds: float=None) -> List[Dict[str, object]]:
        ...

    def read_seed_static_manifest(self) -> Dict[str, object]:
        ...

    def relation_rows_from_typeql(self, rows: Iterable[Dict[str, object]], box: str) -> List[Dict[str, object]]:
        ...

    def rows_for_entities(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        ...

    def seed_static_box_names(self) -> List[str]:
        ...

    def static_seed_generation_ids(self, metadata: Dict[str, object]=None) -> Dict[str, str]:
        ...


@dataclass(frozen=True)
class GraphReadsRowsRuntime:
    endpoint_node_row: Callable[..., Any]
    list_of_strings: Callable[..., Any]
    merge_flat_properties: Callable[..., Any]
    normalized_boxes: Callable[..., Any]
    typeql_limit_clause: Callable[..., Any]
