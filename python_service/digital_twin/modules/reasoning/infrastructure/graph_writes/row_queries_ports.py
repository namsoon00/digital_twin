"""Capabilities for row queries; no runtime construction."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class RowQueriesPort(Protocol):
    def _given_relation_has_value(self, value: object) -> bool: ...

    def _given_relation_value(self, value: object, value_type: str) -> object: ...

    def abox_relation_batch_size(self, settings: Dict[str, object] = None) -> int: ...

    def batched_node_insert_queries(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        batch_size: int = 40,
        max_query_bytes: int = 0,
    ) -> List[str]: ...

    def batched_relation_insert_queries(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        batch_size: int = 25,
        max_query_bytes: int = 0,
    ) -> List[str]: ...

    def given_relation_batch_size(self, settings: Dict[str, object] = None) -> int: ...

    def given_relation_insert_plans(
        self,
        rows: Iterable[Dict[str, object]],
        updated_at: str,
        settings: Dict[str, object] = None,
    ) -> List[Dict[str, object]]: ...

    def given_relation_row_values(self, row: Dict[str, object]) -> List[tuple]: ...

    def given_relation_writes_enabled(
        self, settings: Dict[str, object] = None
    ) -> bool: ...

    def inferencebox_given_relation_batch_size(
        self, settings: Dict[str, object] = None
    ) -> int: ...

    def inferencebox_given_relation_writes_enabled(
        self, settings: Dict[str, object] = None
    ) -> bool: ...

    def inferencebox_relation_batch_size(
        self, settings: Dict[str, object] = None
    ) -> int: ...

    def node_insert_clause(
        self, row: Dict[str, object], updated_at: str, variable: str
    ) -> str: ...

    def query_byte_size(self, query: str) -> int: ...

    def relation_insert_clause(
        self,
        row: Dict[str, object],
        updated_at: str,
        relation_variable: str,
        source_variable: str,
        target_variable: str,
    ) -> str: ...

    def relation_match_clause(
        self, row: Dict[str, object], source_variable: str, target_variable: str
    ) -> str: ...

    def write_query_max_bytes(self, settings: Dict[str, object] = None) -> int: ...


@dataclass(frozen=True)
class NodeInsertClauseBindings:
    promoted_node_text_value: Callable[..., Any]
    promoted_node_value: Callable[..., Any]
    typedb_node_allowed_attributes: Callable[..., Any]
    typeql_has: Callable[..., Any]
    typeql_has_bool_string: Callable[..., Any]


@dataclass(frozen=True)
class RelationMatchClauseBindings:
    typeql_has: Callable[..., Any]


@dataclass(frozen=True)
class RelationInsertClauseBindings:
    typeql_has: Callable[..., Any]
    typeql_has_bool_string: Callable[..., Any]


@dataclass(frozen=True)
class InferenceboxInsertQueriesBindings:
    runtime_settings: Callable[..., Any]


@dataclass(frozen=True)
class InferenceboxGivenRelationInsertPlansBindings:
    runtime_settings: Callable[..., Any]
