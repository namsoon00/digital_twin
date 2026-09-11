"""Explicit capabilities for graph_reads/metadata; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Dict, Iterable, List


class GraphReadsMetadataStore(Protocol):
    _active_scoped_abox_metadata_cache: Any

    _active_scoped_abox_metadata_cache_lock: Any

    def abox_metadata_from_marker(self, marker: Dict[str, object]) -> Dict[str, object]: ...

    def abox_pending_activation_rows(self, world_id: str = "") -> List[Dict[str, object]]: ...

    def abox_projection_marker_rows(
        self, world_id: str = "", snapshot_id: str = "", limit: int = 0
    ) -> List[Dict[str, object]]: ...

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    def active_abox_pointer_rows(
        self, world_id: str = "", limit: int = 0
    ) -> List[Dict[str, object]]: ...

    def active_inference_generation_marker_rows(
        self, world_id: str = "", limit: int = 1
    ) -> List[Dict[str, object]]: ...

    def active_worldview_manifest_pointer_identity_rows(
        self, world_id: str = "", limit: int = 0
    ) -> List[Dict[str, object]]: ...

    def active_worldview_manifest_pointer_rows(
        self, world_id: str = "", limit: int = 0
    ) -> List[Dict[str, object]]: ...

    address: Any

    def base_schema_contract_metadata(self) -> Dict[str, str]: ...

    def base_schema_contract_state(self) -> Dict[str, object]: ...

    def box_snapshot_row_counts(
        self, box: str, snapshot_id: str, world_id: str = ""
    ) -> Dict[str, int]: ...

    def entity_rows_from_typeql(
        self, rows: Iterable[Dict[str, object]], box: str
    ) -> List[Dict[str, object]]: ...

    def inferencebox_recovery_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    def read_entity_rows(
        self, boxes: Iterable[str] = None, limit: int = 0, world_id: str = "", snapshot_id: str = ""
    ) -> List[Dict[str, object]]: ...

    def read_relation_rows(
        self, boxes: Iterable[str] = None, limit: int = 0, world_id: str = "", snapshot_id: str = ""
    ) -> List[Dict[str, object]]: ...

    def read_rows(
        self,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]: ...

    def read_seed_static_manifest(self) -> Dict[str, object]: ...

    def scoped_abox_metadata_from_manifest_marker(
        self, marker: Dict[str, object]
    ) -> Dict[str, object]: ...

    def worldview_manifest_marker_identity_rows(
        self, world_id: str = "", manifest_id: str = "", limit: int = 0
    ) -> List[Dict[str, object]]: ...

    def worldview_manifest_marker_rows(
        self, world_id: str = "", manifest_id: str = "", limit: int = 0
    ) -> List[Dict[str, object]]: ...


@dataclass(frozen=True)
class GraphReadsMetadataRuntime:
    NullTypeDBOntologyGraphRepository: Callable[..., Any]
    inference_generation_records: Callable[..., Any]
    inference_marker_is_active: Callable[..., Any]
    inference_rulebox_metadata: Callable[..., Any]
    native_inference_decision_eligible: Callable[..., Any]
    typedb_error_code: Callable[..., Any]
    typeql_limit_clause: Callable[..., Any]
