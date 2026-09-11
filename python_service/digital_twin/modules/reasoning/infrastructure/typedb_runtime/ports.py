"""Explicit capabilities for connection state, schema I/O, clocks and shared readiness.

The composition facade retains state identity while the lifecycle moves behind
these narrow ports. No application workflows or driver package are imported.
"""

from dataclasses import dataclass
from typing import Any, Callable, ContextManager, Dict, Iterable, List, Protocol, Tuple
from digital_twin.domain.ontology_contracts import PortfolioOntology
from .constants import DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE


@dataclass(frozen=True)
class TypeDBRuntime:
    timeout: Callable[[float, str], ContextManager[None]]
    monotonic: Callable[[], float]
    perf_counter: Callable[[], float]
    sleep: Callable[[float], None]
    error_code: Callable[[object], str]


@dataclass(frozen=True)
class SchemaReadinessCache:
    entries: Dict[Tuple[str, str, bool, str], float]
    lock: ContextManager[object]
    ttl_seconds: float


class ConnectionPort(Protocol):
    _database_created_in_process: bool

    _persistent_driver: Any

    _persistent_driver_enabled: bool

    _persistent_driver_lock: ContextManager[object]

    _query_timeout_seconds: float

    _schema_operation_timeout_seconds: float

    _write_operation_timeout_seconds: float

    address: str

    def close_driver(self, driver) -> None:
        ...

    def create_driver(self, imported, request_timeout_seconds: float=None) -> Any:
        ...

    database: str

    def driver_request_timeout_seconds(self) -> float:
        ...

    def invalidate_persistent_driver(self) -> None:
        ...

    def invalidate_process_base_schema_readiness(self) -> None:
        ...

    def native_rule_dedicated_read_driver_enabled(self) -> bool:
        ...

    def open_driver(self, imported, request_timeout_seconds: float=None) -> Any:
        ...

    password: str

    retry_count: int

    timeout_seconds: float

    tls_enabled: bool

    user: str


class TransactionPort(Protocol):
    database: str

    def query_timeout_seconds(self) -> float:
        ...

    def schema_operation_timeout_seconds(self) -> float:
        ...

    def schema_transaction_options(self, timeout_seconds: float=None) -> Any:
        ...

    def write_operation_timeout_seconds(self) -> float:
        ...


class SchemaCachePort(Protocol):
    address: str

    database: str

    def process_base_schema_cache_key(self, schema_fingerprint: str) -> Tuple[str, str, bool, str]:
        ...

    tls_enabled: bool


class SchemaInspectionPort(Protocol):
    _base_schema_type_names: set

    def base_schema_contract_metadata(self) -> Dict[str, str]:
        ...

    database: str

    def read_seed_static_manifest(self) -> Dict[str, object]:
        ...

    def schema_query(self) -> str:
        ...

    def typedb_schema_text(self, driver) -> str:
        ...


class SchemaMigrationPort(Protocol):
    database: str

    def schema_operation_timeout_seconds(self) -> float:
        ...


class SchemaBootstrapPort(Protocol):
    _last_base_schema_sync: Dict[str, object]

    def base_schema_bootstrap_plan(self, existing_schema_text: str='', batch_size: int=DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE) -> List[Dict[str, object]]:
        ...

    database: str

    password: str

    def schema_operation_timeout_seconds(self) -> float:
        ...

    def schema_transaction(self, driver, transaction_type, timeout_seconds: float=None) -> Any:
        ...

    def typedb_http_json_request(self, path: str, payload: Dict[str, object], timeout_seconds: float, token: str='') -> Dict[str, object]:
        ...

    user: str


class HttpPort(Protocol):
    http_address: str


class SchemaLifecyclePort(Protocol):
    _base_schema_ready_fingerprint: str

    _database_created_in_process: bool

    _fresh_candidate_rebuild: bool

    _fresh_schema_bootstrap_batch_size: int

    _fresh_schema_bootstrap_timeout_seconds: float

    _last_base_schema_sync: Dict[str, object]

    address: str

    def base_schema_contract_metadata(self) -> Dict[str, str]:
        ...

    def base_schema_contract_state(self) -> Dict[str, object]:
        ...

    def base_schema_type_names(self) -> set:
        ...

    def close_driver(self, driver) -> None:
        ...

    def driver_imports(self) -> Tuple[object, object]:
        ...

    def driver_missing_result(self, error: Exception, graph: PortfolioOntology) -> Dict[str, object]:
        ...

    def ensure_database(self, driver) -> None:
        ...

    def ensure_schema(self, driver, imported) -> None:
        ...

    http_address: str

    def mark_process_base_schema_ready(self, schema_fingerprint: str) -> None:
        ...

    def migrate_ontology_content_fingerprint_schema(self, driver, imported, schema_text: str) -> None:
        ...

    def migrate_ontology_scope_schema(self, driver, imported) -> None:
        ...

    def migrate_ontology_semantic_schema(self, driver, imported, schema_text: str) -> None:
        ...

    def migrate_ontology_storage_identity(self, driver, imported, schema_text: str) -> None:
        ...

    def migrate_ontology_world_schema(self, driver, imported) -> None:
        ...

    def migrate_promoted_schema(self, driver, imported, schema_text: str) -> None:
        ...

    def ontology_content_fingerprint_schema_migration_required(self, schema_text: str) -> bool:
        ...

    def ontology_scope_schema_migration_required(self, schema_text: str) -> bool:
        ...

    def ontology_semantic_schema_migration_required(self, schema_text: str) -> bool:
        ...

    def ontology_storage_identity_migration_required(self, schema_text: str) -> bool:
        ...

    def ontology_world_schema_migration_required(self, schema_text: str) -> bool:
        ...

    def open_driver(self, imported, request_timeout_seconds: float=None) -> Any:
        ...

    def process_base_schema_is_ready(self, schema_fingerprint: str) -> bool:
        ...

    def promoted_schema_migration_required(self, schema_text: str) -> bool:
        ...

    def schema_query(self) -> str:
        ...

    def synchronize_base_schema_batches(self, driver, imported, schema_text: str='', batch_size: int=DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE, operation_timeout_seconds: float=None) -> Dict[str, object]:
        ...

    def synchronize_base_schema_batches_http(self, schema_text: str='', batch_size: int=DEFAULT_TYPEDB_BASE_SCHEMA_BOOTSTRAP_BATCH_SIZE, operation_timeout_seconds: float=None) -> Dict[str, object]:
        ...

    def typedb_schema_text(self, driver) -> str:
        ...

    def with_typedb_retries(self, operation, retry_if=None) -> Any:
        ...
