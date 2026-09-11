"""Capabilities for current state; no runtime construction."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class CurrentStatePort(Protocol):
    def incremental_current_state_reasoning_enabled(self) -> bool: ...

    settings: Any
