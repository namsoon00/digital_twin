"""Capabilities for rulebox commands; no runtime construction."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class RuleboxCommandsPort(Protocol):
    _last_rules: Any

    address: Any

    def append_rulebox_version(
        self, version: Dict[str, object]
    ) -> Dict[str, object]: ...

    def clear_rulebox_snapshot_cache(self) -> None: ...

    def rulebox_snapshot(self) -> Dict[str, object]: ...

    def save_rulebox(self, payload: Dict[str, object] = None) -> Dict[str, object]: ...

    def seed_ontology(self, payload: Dict[str, object] = None) -> Dict[str, object]: ...


@dataclass(frozen=True)
class SaveRuleboxBindings:
    utc_now: Callable[..., Any]


@dataclass(frozen=True)
class EnsureRuleboxVersionBaselineBindings:
    utc_now: Callable[..., Any]
