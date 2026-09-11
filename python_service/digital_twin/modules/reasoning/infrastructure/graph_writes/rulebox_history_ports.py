"""Capabilities for rulebox history; no runtime construction."""

from __future__ import annotations
from digital_twin.domain.ontology_contracts import PortfolioOntology
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class RuleboxHistoryPort(Protocol):
    _last_rules: Any

    address: Any

    def close_driver(self, driver) -> None: ...

    def driver_imports(self) -> Tuple[object, object]: ...

    def driver_missing_result(
        self, error: Exception, graph: PortfolioOntology
    ) -> Dict[str, object]: ...

    def ensure_database(self, driver) -> None: ...

    def ensure_schema(self, driver, imported) -> None: ...

    def open_driver(self, imported, request_timeout_seconds: float = None): ...

    def rulebox_snapshot(self) -> Dict[str, object]: ...

    def save_graph(self, graph: PortfolioOntology) -> Dict[str, object]: ...

    def with_typedb_retries(self, operation, retry_if=None): ...

    def write_graph(
        self,
        driver,
        imported,
        graph: PortfolioOntology,
        delete_boxes: Iterable[str] = None,
    ) -> None: ...


@dataclass(frozen=True)
class AppendRuleboxVersionBindings:
    typedb_error_code: Callable[..., Any]
