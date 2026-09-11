"""Explicit capabilities for graph_maintenance/manifests; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.domain.ontology_contracts import PortfolioOntology
from typing import Dict, Iterable, List, Tuple


class GraphMaintenanceManifestsStore(Protocol):
    def abox_inactive_generation_keep_count(self, settings: Dict[str, object] = None) -> int: ...

    def abox_inactive_generation_max_prune_per_save(
        self, settings: Dict[str, object] = None
    ) -> int: ...

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    address: Any

    def close_driver(self, driver) -> None: ...

    database: Any

    def deferred_maintenance_abox_delete_batch_size(
        self, settings: Dict[str, object] = None
    ) -> int: ...

    def deferred_maintenance_abox_max_delete_batches(
        self, settings: Dict[str, object] = None
    ) -> int: ...

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

    def delete_worldview_manifest_markers_batch(
        self, driver, imported, manifest_ids: Iterable[str], world_id: str = ""
    ) -> Dict[str, object]: ...

    def discard_scoped_abox_manifest_in_driver(
        self,
        driver,
        imported,
        manifest_id: str,
        protected_generation_ids: Iterable[str] = None,
        world_id: str = "",
        max_delete_batches: int = None,
        delete_batch_size: int = None,
    ) -> Dict[str, object]: ...

    def driver_imports(self) -> Tuple[object, object]: ...

    def driver_missing_result(
        self, error: Exception, graph: PortfolioOntology
    ) -> Dict[str, object]: ...

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

    def scoped_manifest_metadata(
        self, manifest_id: str, world_id: str = ""
    ) -> Dict[str, object]: ...

    def with_typedb_retries(self, operation, retry_if=None): ...

    def worldview_manifest_marker_identity_rows(
        self, world_id: str = "", manifest_id: str = "", limit: int = 0
    ) -> List[Dict[str, object]]: ...

    def write_operation_timeout_seconds(self) -> float: ...

    def write_transaction_options(self): ...


@dataclass(frozen=True)
class GraphMaintenanceManifestsRuntime:
    typedb_error_code: Callable[..., Any]
    typedb_operation_timeout: Callable[..., Any]
