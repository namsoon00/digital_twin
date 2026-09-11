"""Explicit capabilities for graph_reads/inventory; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Dict, Iterable, List


class GraphReadsInventoryStore(Protocol):
    def abox_inactive_generation_keep_count(self, settings: Dict[str, object] = None) -> int: ...

    def abox_inactive_generation_max_prune_per_save(
        self, settings: Dict[str, object] = None
    ) -> int: ...

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    def active_abox_uses_scoped_manifest(self, world_id: str = "") -> bool: ...

    def active_worldview_manifest_pointer_identity_rows(
        self, world_id: str = "", limit: int = 0
    ) -> List[Dict[str, object]]: ...

    address: Any

    def box_row_counts(self, box: str, world_id: str = "") -> Dict[str, int]: ...

    def current_state_inventory_batch_size(self, settings: Dict[str, object] = None) -> int: ...

    database: Any

    def entity_row_from_typeql(self, row: Dict[str, object], box: str) -> Dict[str, object]: ...

    def read_rows(
        self,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]: ...

    def read_rows_in_transaction(
        self,
        tx,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]: ...

    def read_transaction_options(self, timeout_seconds: float = None): ...

    def relation_row_from_typeql(self, row: Dict[str, object], box: str) -> Dict[str, object]: ...

    def scoped_abox_counts_by_scope(
        self, node_rows: Iterable[Dict[str, object]], relation_rows: Iterable[Dict[str, object]]
    ) -> Dict[str, Dict[str, int]]: ...

    def scoped_abox_manifest_generation_references(
        self, world_id: str = ""
    ) -> Dict[str, object]: ...

    def scoped_abox_metadata_from_manifest_marker(
        self, marker: Dict[str, object]
    ) -> Dict[str, object]: ...

    def scoped_abox_scope_row_counts_batch(
        self, scope_rows: Iterable[Dict[str, object]], manifest_id: str = "", world_id: str = ""
    ) -> Dict[str, Dict[str, int]]: ...

    def scoped_abox_write_lease_status(self, world_id: str = "") -> Dict[str, object]: ...

    def worldview_manifest_marker_count(self, world_id: str = "") -> int: ...

    def worldview_manifest_marker_identity_rows(
        self, world_id: str = "", manifest_id: str = "", limit: int = 0
    ) -> List[Dict[str, object]]: ...

    def worldview_manifest_marker_rows(
        self, world_id: str = "", manifest_id: str = "", limit: int = 0
    ) -> List[Dict[str, object]]: ...


@dataclass(frozen=True)
class GraphReadsInventoryRuntime:
    endpoint_node_row: Callable[..., Any]
