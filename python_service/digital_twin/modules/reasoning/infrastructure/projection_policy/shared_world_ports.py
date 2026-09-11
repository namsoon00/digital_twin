"""Capabilities for shared world; no runtime construction."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class SharedWorldPort(Protocol):
    settings: Any

    def shared_knowledge_world_retention_hours(self) -> float: ...

    def shared_market_world_retention_hours(self) -> float: ...
