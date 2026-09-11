"""Explicit capabilities for manifest/repair; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.domain.ontology_contracts import PortfolioOntology
from typing import Dict, List, Tuple


class ManifestRepairStore(Protocol):
    _active_scoped_abox_metadata_cache: Any

    _active_scoped_abox_metadata_cache_lock: Any

    def acquire_scoped_abox_write_lease(
        self, manifest_id: str = "", world_id: str = "", lease_seconds: int = 0
    ) -> Dict[str, object]: ...

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    address: Any

    def close_driver(self, driver) -> None: ...

    database: Any

    def driver_imports(self) -> Tuple[object, object]: ...

    def ensure_database(self, driver) -> None: ...

    def ensure_schema(self, driver, imported) -> None: ...

    def node_insert_query(self, row: Dict[str, object], updated_at: str) -> str: ...

    def node_rows(
        self, graph: PortfolioOntology, include_external_relation_endpoints: bool = False
    ) -> List[Dict[str, object]]: ...

    def open_driver(self, imported, request_timeout_seconds: float = None): ...

    def rebuild_active_manifest_native_rule_evidence_read_index(
        self, active_metadata: Dict[str, object], world_id: str = ""
    ) -> Dict[str, object]: ...

    def release_scoped_abox_write_lease(self, lease: Dict[str, object]) -> Dict[str, object]: ...

    def repair_active_manifest_native_rule_evidence_index(
        self,
        active_metadata: Dict[str, object] = None,
        world_id: str = "",
        expected_manifest_id: str = "",
        stable_write_lease_held: bool = False,
    ) -> Dict[str, object]: ...

    def replace_scoped_manifest_marker_graph(
        self, marker_graph: PortfolioOntology
    ) -> Dict[str, object]: ...

    def with_typedb_retries(self, operation, retry_if=None): ...

    def worldview_manifest_marker_rows(
        self, world_id: str = "", manifest_id: str = "", limit: int = 0
    ) -> List[Dict[str, object]]: ...

    def write_operation_timeout_seconds(self) -> float: ...

    def write_transaction_options(self): ...


@dataclass(frozen=True)
class ManifestRepairRuntime:
    typedb_error_code: Callable[..., Any]
    typedb_operation_timeout: Callable[..., Any]
    typedb_projection_coordinator_summary: Callable[..., Any]
    utc_now: Callable[..., Any]
