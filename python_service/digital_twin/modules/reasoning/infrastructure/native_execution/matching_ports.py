"""Explicit capabilities for native_execution/matching; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.model_registry.contracts import GraphInferenceRule
from typing import Dict, Iterable, List, Tuple


class NativeExecutionMatchingStore(Protocol):
    def active_abox_rule_context(
        self, symbols: Iterable[str], world_id: str = ""
    ) -> Dict[str, object]: ...

    def active_abox_uses_scoped_manifest(self, world_id: str = "") -> bool: ...

    def close_driver(self, driver) -> None: ...

    def close_native_rule_read_driver(self, driver) -> None: ...

    def condition_detail_queries_enabled(self) -> bool: ...

    database: Any

    def driver_imports(self) -> Tuple[object, object]: ...

    def ensure_database(self, driver) -> None: ...

    def execute_typedb_model_signal_bridge_batches(
        self,
        batches: Iterable[Dict[str, object]],
        *,
        world_id: str,
        imported,
        transaction_type,
        deadline: float,
        evidence_read_index: Dict[str, object] = None
    ) -> Dict[str, object]: ...

    def execute_typedb_native_rule_entry(
        self,
        planned: Dict[str, object],
        clean_symbols: Iterable[str],
        world_id: str,
        scoped_manifest_only: bool,
        imported,
        transaction_type,
        deadline: float,
        execution_mode: str,
        evidence_read_index: Dict[str, object] = None,
        shared_read_driver=None,
    ) -> Dict[str, object]: ...

    def hydrate_native_rule_evidence_field_index(
        self,
        evidence_read_index: Dict[str, object] = None,
        target_symbols: Iterable[str] = None,
        relation_types: Iterable[str] = None,
    ) -> Dict[str, object]: ...

    def match_typedb_native_rules_by_subject(
        self,
        rules: Iterable[GraphInferenceRule],
        target_symbols: Iterable[str],
        *,
        world_id: str,
        planner_topology: Dict[str, object] = None,
        preflight_graph: PortfolioOntology = None,
        preflight_incoming_relations_complete: bool = False,
        evidence_read_index: Dict[str, object] = None
    ) -> Dict[str, object]: ...

    def merge_native_match_rows(
        self,
        rule: GraphInferenceRule,
        query_plan: Dict[str, object],
        rows: Iterable[Dict[str, object]],
        match_index: Dict[str, Dict[str, object]],
        matches: List[Dict[str, object]],
        world_id: str = "",
    ) -> None: ...

    def native_rule_adaptive_target_sharding_enabled(self) -> bool: ...

    def native_rule_any_condition_parallelism(self) -> int: ...

    def native_rule_execution_budget_seconds(self) -> float: ...

    def native_rule_indexed_any_condition_query_timeout_seconds(self) -> float: ...

    def native_rule_query_timeout_seconds(self) -> float: ...

    def native_rule_subject_fanout_enabled(self) -> bool: ...

    def native_rule_target_work_sharding_enabled(self) -> bool: ...

    def open_driver(self, imported, request_timeout_seconds: float = None): ...

    def open_native_rule_read_driver(self, imported, request_timeout_seconds: float = None): ...

    def query_metrics_snapshot(self) -> Dict[str, object]: ...

    def read_rows_in_transaction(
        self,
        tx,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]: ...

    def read_transaction_options(self, timeout_seconds: float = None): ...

    def recover_timed_out_native_rule_entry(
        self,
        primary_result: Dict[str, object],
        planned: Dict[str, object],
        clean_symbols: Iterable[str],
        world_id: str,
        scoped_manifest_only: bool,
        imported,
        transaction_type,
        deadline: float,
        execution_mode: str,
        evidence_read_index: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def verify_typedb_native_any_conditions(
        self,
        driver,
        transaction_type,
        rule: GraphInferenceRule,
        source_id: str,
        timeout_seconds: float,
        scoped_manifest_only: bool,
        tx=None,
        world_id: str = "",
        evidence_read_index: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def with_typedb_retries(self, operation, retry_if=None): ...


@dataclass(frozen=True)
class NativeExecutionMatchingRuntime:
    typedb_error_code: Callable[..., Any]
    typedb_native_rule_execution_incomplete_diagnostic: Callable[..., Any]
