"""Explicit capabilities for graph_maintenance/generations; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Dict, Iterable, List, Tuple


class GraphMaintenanceGenerationsStore(Protocol):
    def abox_candidate_snapshot_ids(self) -> List[str]: ...

    def abox_delete_batch_size(self, settings: Dict[str, object] = None) -> int: ...

    def abox_inactive_generation_keep_count(self, settings: Dict[str, object] = None) -> int: ...

    def abox_inactive_generation_max_prune_per_save(
        self, settings: Dict[str, object] = None
    ) -> int: ...

    def abox_incremental_cleanup_batch_size(self, settings: Dict[str, object] = None) -> int: ...

    def abox_incremental_cleanup_max_batches_per_save(
        self, settings: Dict[str, object] = None
    ) -> int: ...

    def abox_projection_marker_rows(
        self, world_id: str = "", snapshot_id: str = "", limit: int = 0
    ) -> List[Dict[str, object]]: ...

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    address: Any

    def box_delete_batch_query(self, box: str, type_label: str, batch_size: int) -> str: ...

    def box_instance_exists(self, driver, imported, box: str, type_label: str) -> bool: ...

    def box_manifest_delete_batch_query(
        self, box: str, manifest_id: str, type_label: str, batch_size: int, world_id: str = ""
    ) -> str: ...

    def box_manifest_instance_exists(
        self, driver, imported, box: str, manifest_id: str, type_label: str, world_id: str = ""
    ) -> bool: ...

    def box_snapshot_delete_batch_query(
        self, box: str, snapshot_id: str, type_label: str, batch_size: int
    ) -> str: ...

    def box_snapshot_external_relation_references(
        self, driver, imported, box: str, snapshot_id: str, limit: int = 5
    ) -> List[Dict[str, object]]: ...

    def box_snapshot_instance_exists(
        self, driver, imported, box: str, snapshot_id: str, type_label: str
    ) -> bool: ...

    def close_driver(self, driver) -> None: ...

    database: Any

    def delete_box_rows_in_batches(
        self, driver, imported, boxes: Iterable[str]
    ) -> Dict[str, object]: ...

    def delete_box_snapshot_rows_in_batches(
        self,
        driver,
        imported,
        box: str,
        snapshot_id: str,
        batch_size: int = None,
        max_batches: int = None,
        deadline_monotonic: float = None,
    ) -> Dict[str, object]: ...

    def discard_scoped_abox_manifest(
        self, manifest_id: str, world_id: str = ""
    ) -> Dict[str, object]: ...

    def driver_imports(self) -> Tuple[object, object]: ...

    def ensure_database(self, driver) -> None: ...

    def ensure_schema(self, driver, imported) -> None: ...

    def open_driver(self, imported, request_timeout_seconds: float = None): ...

    def pending_abox_activation(self, world_id: str = "") -> Dict[str, object]: ...

    def prune_inactive_scoped_abox_manifests_in_driver(
        self,
        driver,
        imported,
        active_manifest_id: str = "",
        keep_inactive_count: int = None,
        max_manifests: int = None,
        max_delete_batches: int = None,
        delete_batch_size: int = None,
        world_id: str = "",
        max_duration_seconds: int = None,
    ) -> Dict[str, object]: ...

    def read_rows_in_transaction(
        self,
        tx,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]: ...

    def scoped_manifest_metadata(
        self, manifest_id: str, world_id: str = ""
    ) -> Dict[str, object]: ...

    def with_typedb_retries(self, operation, retry_if=None): ...

    def write_operation_timeout_seconds(self) -> float: ...

    def write_transaction_options(self): ...


@dataclass(frozen=True)
class GraphMaintenanceGenerationsRuntime:
    typedb_error_code: Callable[..., Any]
    typedb_operation_timeout: Callable[..., Any]
