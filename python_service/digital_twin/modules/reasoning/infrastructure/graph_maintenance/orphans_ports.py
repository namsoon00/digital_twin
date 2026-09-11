"""Explicit capabilities for graph_maintenance/orphans; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from typing import Dict
from typing import Tuple


class GraphMaintenanceOrphansStore(Protocol):
    address: Any

    def cleanup_orphan_scoped_abox_candidates(self, driver, imported, max_generation_count: int=0, world_id: str='') -> Dict[str, object]:
        ...

    def close_driver(self, driver) -> None:
        ...

    def delete_box_snapshot_rows_in_batches(self, driver, imported, box: str, snapshot_id: str, batch_size: int=None, max_batches: int=None, deadline_monotonic: float=None) -> Dict[str, object]:
        ...

    def driver_imports(self) -> Tuple[object, object]:
        ...

    def ensure_database(self, driver) -> None:
        ...

    def ensure_schema(self, driver, imported) -> None:
        ...

    def open_driver(self, imported, request_timeout_seconds: float=None):
        ...

    def scoped_abox_orphan_candidate_inventory(self, world_id: str='') -> Dict[str, object]:
        ...

    def scoped_abox_orphan_cleanup_max_generations(self, settings: Dict[str, object]=None) -> int:
        ...

    def with_typedb_retries(self, operation, retry_if=None):
        ...


@dataclass(frozen=True)
class GraphMaintenanceOrphansRuntime:
    runtime_settings: Callable[..., Any]
    typedb_error_code: Callable[..., Any]
