"""Capabilities for record; no runtime construction."""

from __future__ import annotations
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_projection_audit import OntologyProjectionRun
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class RecordPort(Protocol):
    def acquire_projection_coordinator_lease(
        self, owner: str, world_id: str
    ) -> Dict[str, object]: ...

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    def active_graph_store_key(self, result: Dict[str, object] = None) -> str: ...

    def active_projection_audit_run(self, world_id: str = ""): ...

    def advance_current_state_transition(
        self,
        projection_run: OntologyProjectionRun,
        stage: str,
        status: str = "running",
        inference_generation_id: str = "",
        detail: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def async_quality_record_enabled(self) -> bool: ...

    def attach_abox_persistence_runtime_stages(
        self, runtime_stages: Dict[str, int], result: Dict[str, object]
    ) -> None: ...

    def attach_graph_store_inference_result(
        self,
        result: Dict[str, object],
        snapshot: AccountSnapshot,
        target_symbols: List[str] = None,
        inference_impact_plan: Dict[str, object] = None,
        world_id: str = "",
        candidate_scope_plan: List[Dict[str, object]] = None,
        rulebox_rules_hash: str = "",
        tbox_fingerprint: str = "",
        preflight_graph: PortfolioOntology = None,
        preflight_manifest_id: str = "",
    ) -> None: ...

    def begin_projection_audit_run(
        self,
        snapshot: AccountSnapshot,
        graph: PortfolioOntology,
        material_fingerprint: str,
        abox_snapshot_id: str,
        inference_symbols: List[str],
        rulebox_metadata: Dict[str, object],
        reasoning_context: Dict[str, object] = None,
    ): ...

    def bounded_native_inference_symbols(
        self,
        snapshot: AccountSnapshot,
        inferred_symbols: List[str],
        requested_symbols: List[str] = None,
        scheduler_target_symbol_limit: int = 0,
    ) -> List[str]: ...

    def build_projection_graph(
        self,
        snapshot: AccountSnapshot,
        rule_catalog: Dict[str, object],
        portfolio_world_context,
        market_world_context=None,
        target_symbols: List[str] = None,
        target_scoped_input: bool = False,
        progress_callback: Callable[..., None] = None,
        shared_premise_proof: Dict[str, object] = None,
        reasoning_context: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def current_state_abox_storage_enabled(self) -> bool: ...

    def ensure_rulebox_ready(self) -> Dict[str, object]: ...

    def existing_inference_result(
        self,
        snapshot: AccountSnapshot,
        target_symbols: List[str] = None,
        world_id: str = "",
    ) -> Dict[str, object]: ...

    def has_projectable_data(self, snapshot: AccountSnapshot) -> bool: ...

    def inference_impact_plan(
        self,
        snapshot: AccountSnapshot,
        active_abox: Dict[str, object],
        scoped_identity: Dict[str, object],
        target_symbols: List[str] = None,
        reasoning_context: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def inference_result_is_reusable(
        self,
        inferencebox: Dict[str, object],
        active_abox: Dict[str, object],
        required_symbols: List[str] = None,
    ) -> bool: ...

    def inference_symbols(
        self, snapshot: AccountSnapshot, target_symbols: List[str] = None
    ) -> List[str]: ...

    def interrupted_projection_recovery_required(self, world_id: str) -> bool: ...

    def native_preflight_projection_graph(
        self, graph: PortfolioOntology, persistence: Dict[str, object]
    ) -> PortfolioOntology: ...

    def projection_coordinator_summary(
        self, lease: Dict[str, object]
    ) -> Dict[str, object]: ...

    projection_run_store: Any

    quality_store: Any

    def reconcile_interrupted_projection_audit(
        self, world_id: str = ""
    ) -> Dict[str, object]: ...

    def recover_pending_abox_activation(
        self, world_id: str = "", max_staged_target_symbols: int = 0
    ) -> Dict[str, object]: ...

    def release_projection_coordinator_lease(
        self, lease: Dict[str, object]
    ) -> Dict[str, object]: ...

    repository: Any

    def repository_world_call(
        self, method_name: str, *args, world_id: str = "", **kwargs
    ): ...

    def resume_staged_pending_abox_activation(
        self, snapshot: AccountSnapshot, world_id: str, recovery: Dict[str, object]
    ) -> Dict[str, object]: ...

    def schedule_knowledge_world_projection(
        self, portfolio_graph: PortfolioOntology, shared_world, source_world=None
    ) -> Dict[str, object]: ...

    def schedule_market_world_projection(
        self, portfolio_graph: PortfolioOntology, shared_world, source_world=None
    ) -> Dict[str, object]: ...

    def scheduler_target_symbol_limit(
        self, reasoning_context: Dict[str, object] = None
    ) -> int: ...

    def scope_integrity_audit_interval_minutes(self) -> float: ...

    settings: Any

    source: Any

    def store_projection_result(
        self,
        snapshot: AccountSnapshot,
        result: Dict[str, object],
        projection_run: OntologyProjectionRun = None,
    ) -> None: ...

    def target_scoped_patch_targets(
        self,
        snapshot: AccountSnapshot,
        active_metadata: Dict[str, object],
        scoped_identity: Dict[str, object],
        requested_symbols: List[str] = None,
        reasoning_context: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def typedb_projection_deferred(self) -> bool: ...


@dataclass(frozen=True)
class RecordSnapshotBindings:
    SHARED_ONTOLOGY_QUALITY_RECORD_COORDINATOR: Any
    compact_target_scope_selection_trace: Callable[..., Any]
