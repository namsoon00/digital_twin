"""Capabilities for coordinator; no runtime construction."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class CoordinatorPort(Protocol):
    def active_graph_store_key(self, result: Dict[str, object] = None) -> str: ...

    repository: Any
