"""Capabilities for rulebox read; no runtime construction."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class RuleboxReadPort(Protocol):
    _last_rules: Any

    _rulebox_snapshot_cache_at: Any

    _rulebox_snapshot_cache_full_load_at: Any

    _rulebox_snapshot_cache_result: Any

    address: Any

    def read_entity_rows(
        self,
        boxes: Iterable[str] = None,
        limit: int = 0,
        world_id: str = "",
        snapshot_id: str = "",
    ) -> List[Dict[str, object]]: ...

    def read_relation_rows(
        self,
        boxes: Iterable[str] = None,
        limit: int = 0,
        world_id: str = "",
        snapshot_id: str = "",
    ) -> List[Dict[str, object]]: ...

    def read_seed_static_manifest(self) -> Dict[str, object]: ...

    def rulebox_snapshot_cache_seconds(self) -> float: ...


@dataclass(frozen=True)
class RuleboxSnapshotBindings:
    NullTypeDBOntologyGraphRepository: Callable[..., Any]
    entity_node_kind: Callable[..., Any]
    relation_type_rows_from_derivations: Callable[..., Any]
    typedb_error_code: Callable[..., Any]
