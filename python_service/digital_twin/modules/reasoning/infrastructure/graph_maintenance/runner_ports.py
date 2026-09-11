"""Explicit capabilities for graph_maintenance/runner; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from typing import Dict
from typing import List


class GraphMaintenanceRunnerStore(Protocol):
    def abox_candidate_snapshot_ids(self) -> List[str]:
        ...

    def acquire_scoped_abox_write_lease(self, manifest_id: str='', world_id: str='', lease_seconds: int=0) -> Dict[str, object]:
        ...

    def active_abox_metadata(self, world_id: str='') -> Dict[str, object]:
        ...

    address: Any

    def deferred_maintenance_abox_delete_batch_size(self, settings: Dict[str, object]=None) -> int:
        ...

    def deferred_maintenance_abox_max_delete_batches(self, settings: Dict[str, object]=None) -> int:
        ...

    def deferred_maintenance_abox_max_manifests(self, settings: Dict[str, object]=None) -> int:
        ...

    def discard_abox_generation(self, snapshot_id: str) -> Dict[str, object]:
        ...

    inference_generation_keep_count: Any

    def list_ontology_worlds(self) -> List[Dict[str, object]]:
        ...

    def pending_abox_activation(self, world_id: str='') -> Dict[str, object]:
        ...

    def prune_inactive_scoped_abox_manifests(self, world_id: str='', keep_inactive_count: int=None, max_manifests: int=None, max_delete_batches: int=None, delete_batch_size: int=None, max_duration_seconds: int=None) -> Dict[str, object]:
        ...

    def prune_inferencebox_generations(self, active_generation_id: str, keep_count: int=2, world_id: str='') -> Dict[str, object]:
        ...

    def prune_orphan_scoped_abox_candidates(self, world_id: str='', max_generation_count: int=0) -> Dict[str, object]:
        ...

    def read_inference_generation_records(self, published_only: bool=True, world_id: str='') -> List[Dict[str, object]]:
        ...

    def release_scoped_abox_write_lease(self, lease: Dict[str, object]) -> Dict[str, object]:
        ...

    def run_deferred_maintenance(self, payload: Dict[str, object]=None) -> Dict[str, object]:
        ...


@dataclass(frozen=True)
class GraphMaintenanceRunnerRuntime:
    typedb_error_code: Callable[..., Any]
