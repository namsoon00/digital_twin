"""Explicit capabilities for manifest/save; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.domain.ontology_contracts import PortfolioOntology
from typing import Dict, Iterable, List, Tuple
from digital_twin.domain.ontology_current_state import CURRENT_STATE_ABOX_PERSISTENCE_MODE
from typing import Mapping


class ManifestSaveStore(Protocol):
    _fresh_candidate_rebuild: Any

    def acquire_scoped_abox_write_lease(
        self, manifest_id: str = "", world_id: str = "", lease_seconds: int = 0
    ) -> Dict[str, object]: ...

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    def close_driver(self, driver) -> None: ...

    def current_state_delta_plan(
        self,
        node_rows: Iterable[Dict[str, object]],
        relation_rows: Iterable[Dict[str, object]],
        inventory: Dict[str, Dict[str, Dict[str, object]]],
    ) -> Dict[str, object]: ...

    def current_state_physical_graph(
        self, graph: PortfolioOntology, physical_scope_plan: Iterable[Dict[str, object]]
    ) -> PortfolioOntology: ...

    def current_state_physical_scope_plan(
        self,
        logical_scope_plan: Iterable[Dict[str, object]],
        active_metadata: Dict[str, object],
        changed_scope_ids: Iterable[str],
        world_id: str,
        persistence_mode: str = CURRENT_STATE_ABOX_PERSISTENCE_MODE,
        transition_id: str = "",
    ) -> List[Dict[str, object]]: ...

    def current_state_slot_inventory(
        self, driver, imported, physical_generation_ids: Iterable[str]
    ) -> Dict[str, Dict[str, Dict[str, object]]]: ...

    def current_state_storage_inventory(
        self,
        driver,
        imported,
        node_storage_ids: Iterable[str] = None,
        relation_storage_ids: Iterable[str] = None,
    ) -> Dict[str, Dict[str, Dict[str, object]]]: ...

    def delete_box_manifest_rows_in_batches(
        self,
        driver,
        imported,
        box: str,
        manifest_id: str,
        batch_size: int = None,
        max_batches: int = None,
        world_id: str = "",
    ) -> Dict[str, object]: ...

    def delete_current_state_storage_ids(
        self, driver, imported, node_storage_ids: Iterable[str], relation_storage_ids: Iterable[str]
    ) -> Dict[str, object]: ...

    def driver_imports(self) -> Tuple[object, object]: ...

    def driver_missing_result(
        self, error: Exception, graph: PortfolioOntology
    ) -> Dict[str, object]: ...

    def ensure_database(self, driver) -> None: ...

    def ensure_schema(self, driver, imported) -> None: ...

    def fresh_candidate_world_bootstrap_required(self, world_id: str = "") -> bool: ...

    def graph_persistence_rows(
        self, graph: PortfolioOntology
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]: ...

    def is_current_state_scoped_abox_graph(self, graph: PortfolioOntology) -> bool: ...

    def merged_scoped_abox_counts(
        self, *count_sets: Dict[str, Dict[str, int]]
    ) -> Dict[str, Dict[str, int]]: ...

    def open_driver(self, imported, request_timeout_seconds: float = None): ...

    def pending_abox_activation(self, world_id: str = "") -> Dict[str, object]: ...

    def prepare_scoped_manifest_native_rule_indexes(
        self,
        graph: PortfolioOntology,
        active_metadata: Dict[str, object] = None,
        persistence_rows: Tuple[Iterable[Dict[str, object]], Iterable[Dict[str, object]]] = None,
    ) -> Dict[str, object]: ...

    def read_active_scoped_abox_rows(
        self, active_metadata: Dict[str, object], scope_ids: Iterable[str], world_id: str = ""
    ) -> Dict[str, object]: ...

    def release_scoped_abox_write_lease(self, lease: Dict[str, object]) -> Dict[str, object]: ...

    def repair_active_manifest_native_rule_evidence_index(
        self,
        active_metadata: Dict[str, object] = None,
        world_id: str = "",
        expected_manifest_id: str = "",
        stable_write_lease_held: bool = False,
    ) -> Dict[str, object]: ...

    def scoped_abox_active_reuse_scope_ids(
        self,
        physical_scope_plan: Iterable[Dict[str, object]],
        active_generations: Mapping[str, object],
        physical_changed_scope_ids: Iterable[str],
        deferred_scope_ids: Iterable[str],
        rebind_only_relation_scope_ids: Iterable[str],
    ) -> Dict[str, object]: ...

    def scoped_abox_candidate_persistence_rows(
        self,
        current_node_rows: Iterable[Dict[str, object]],
        current_relation_rows: Iterable[Dict[str, object]],
        active_scope_rows: Dict[str, object],
        physical_scope_plan: Iterable[Dict[str, object]],
        semantic_changed_scope_ids: Iterable[str],
        physical_changed_scope_ids: Iterable[str],
        deferred_scope_ids: Iterable[str],
        candidate_manifest_id: str,
    ) -> Dict[str, object]: ...

    def scoped_abox_changed_scope_ids(
        self,
        logical_scope_plan: Iterable[Dict[str, object]],
        active_metadata: Dict[str, object],
        current_state_mode: bool,
        migration_mode: str = "",
        current_state_persistence_mode: str = CURRENT_STATE_ABOX_PERSISTENCE_MODE,
        relation_rebind_root_scope_ids: Iterable[str] = None,
    ) -> List[str]: ...

    def scoped_abox_counts_by_scope(
        self, node_rows: Iterable[Dict[str, object]], relation_rows: Iterable[Dict[str, object]]
    ) -> Dict[str, Dict[str, int]]: ...

    def scoped_abox_native_index_reuse_scope_ids(
        self,
        target_patch: Mapping[str, object],
        active_generations: Mapping[str, object],
        physical_changed_scope_ids: Iterable[str],
        candidate_scope_plan: Iterable[Mapping[str, object]] = None,
    ) -> List[str]: ...

    def scoped_abox_persistence_rows(
        self, graph: PortfolioOntology, scope_ids: Iterable[str]
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]: ...

    def scoped_abox_plan(self, graph: PortfolioOntology) -> List[Dict[str, object]]: ...

    def scoped_abox_rebind_only_relation_scope_ids(
        self,
        logical_scope_plan: Iterable[Dict[str, object]],
        semantic_changed_scope_ids: Iterable[str],
        physical_changed_scope_ids: Iterable[str],
    ) -> List[str]: ...

    def scoped_abox_relation_breakdown(
        self, relation_rows: Iterable[Dict[str, object]], bucket_limit: int = 24
    ) -> Dict[str, object]: ...

    def scoped_abox_relation_persistence_summary(
        self, write_plan: Dict[str, object]
    ) -> Dict[str, object]: ...

    def scoped_abox_scope_row_counts_batch(
        self, scope_rows: Iterable[Dict[str, object]], manifest_id: str = "", world_id: str = ""
    ) -> Dict[str, Dict[str, int]]: ...

    def scoped_abox_semantic_changed_scope_ids(
        self,
        logical_scope_plan: Iterable[Dict[str, object]],
        active_metadata: Dict[str, object],
        current_state_mode: bool,
        migration_mode: str = "",
        current_state_persistence_mode: str = CURRENT_STATE_ABOX_PERSISTENCE_MODE,
    ) -> List[str]: ...

    def scoped_abox_write_lease_status(self, world_id: str = "") -> Dict[str, object]: ...

    def scoped_manifest_marker_graph(
        self,
        graph: PortfolioOntology,
        scope_plan: List[Dict[str, object]],
        changed_scope_ids: Iterable[str],
    ) -> PortfolioOntology: ...

    def scoped_manifest_pending_graph(
        self,
        graph: PortfolioOntology,
        scope_plan: List[Dict[str, object]],
        previous_metadata: Dict[str, object] = None,
        inference_target_symbols: Iterable[str] = None,
    ) -> PortfolioOntology: ...

    def with_scoped_abox_candidate_verification_retry(
        self, operation, timing: Dict[str, object] = None, verification: Dict[str, object] = None
    ): ...

    def write_graph(
        self, driver, imported, graph: PortfolioOntology, delete_boxes: Iterable[str] = None
    ) -> None: ...

    def write_persistence_rows(
        self,
        driver,
        imported,
        node_rows: Iterable[Dict[str, object]],
        relation_rows: Iterable[Dict[str, object]],
        telemetry: Dict[str, object] = None,
        assume_missing_storage: bool = False,
    ) -> Dict[str, object]: ...


@dataclass(frozen=True)
class ManifestSaveRuntime:
    typedb_error_code: Callable[..., Any]
    utc_now: Callable[..., Any]
