"""Explicit capabilities for native_execution/retry; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Dict, Iterable


class NativeExecutionRetryStore(Protocol):
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

    def native_rule_entry_has_interrupted_transaction_failure(
        self, result: Dict[str, object]
    ) -> bool: ...

    def native_rule_entry_has_timeout_failure(self, result: Dict[str, object]) -> bool: ...


@dataclass(frozen=True)
class NativeExecutionRetryRuntime:
    typedb_error_code: Callable[..., Any]
