"""Explicit capabilities for native_execution/context; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.domain.ontology_rulebox_contracts import GraphInferenceRule
from typing import Dict, Iterable, List


class NativeExecutionContextStore(Protocol):
    def condition_detail_queries_enabled(self) -> bool: ...

    def read_rows(
        self,
        query: str,
        columns: Iterable[str],
        label: str = "typedb.read",
        timeout_seconds: float = None,
    ) -> List[Dict[str, object]]: ...

    def typedb_rule_condition_context(
        self,
        rule: GraphInferenceRule,
        source_id: str,
        query_plan: Dict[str, object] = None,
        row: Dict[str, object] = None,
        world_id: str = "",
    ) -> Dict[str, object]: ...


@dataclass(frozen=True)
class NativeExecutionContextRuntime:
    typedb_native_matched_conditions: Callable[..., Any]
    typedb_static_rule_condition_context: Callable[..., Any]
