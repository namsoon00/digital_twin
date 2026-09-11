"""Capabilities used only by static-seed restore."""

from typing import Dict, Iterable, List, Protocol
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology


class RestoreStore(Protocol):
    _last_rules: List[object]

    def active_tbox_metadata(self) -> Dict[str, object]: ...

    def clear_rulebox_snapshot_cache(self) -> None: ...

    def read_seed_static_manifest(self) -> Dict[str, object]: ...

    def rulebox_snapshot(self) -> Dict[str, object]: ...

    def save_seed_static_manifest(
        self,
        graph: PortfolioOntology,
        rules_payload: List[Dict[str, object]],
        schema_prepared: bool = False,
        tbox_metadata: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def save_static_seed_boxes(
        self,
        graph: PortfolioOntology,
        boxes: Iterable[str],
        rules_payload: List[Dict[str, object]] = None,
        schema_prepared: bool = False,
        tbox_metadata: Dict[str, object] = None,
    ) -> Dict[str, object]: ...

    def seed_static_box_names(self) -> List[str]: ...

    def sync_base_schema_contract(self) -> Dict[str, object]: ...
