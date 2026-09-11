"""Capabilities for catalog; no runtime construction."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Set, Tuple


class CatalogPort(Protocol):
    _frozen_compiled_rule_catalogs: Any

    _frozen_rulebox_catalog: Any

    _frozen_rulebox_readiness: Any

    _frozen_world_rule_partition: Any

    _rulebox_impact_rules: Any

    def copy_compiled_rule_catalog(
        self, catalog: Dict[str, object]
    ) -> Dict[str, object]: ...

    def copy_world_rule_partition(
        self, partition: Dict[str, object]
    ) -> Dict[str, object]: ...

    def migrate_typedb_rule_catalog(
        self, snapshot: Dict[str, object], bootstrap_rules: List[Dict[str, object]]
    ) -> Dict[str, object]: ...

    repository: Any

    def rulebox_rules_for_impact(self) -> List[Dict[str, object]]: ...


@dataclass(frozen=True)
class EnsureRuleboxReadyBindings:
    bootstrap_rule_catalog: Callable[..., Any]
    rulebox_catalog_requires_bootstrap_repair: Callable[..., Any]
    rulebox_input_relation_types: Callable[..., Any]
    rulebox_rules_missing_decision_stage: Callable[..., Any]
