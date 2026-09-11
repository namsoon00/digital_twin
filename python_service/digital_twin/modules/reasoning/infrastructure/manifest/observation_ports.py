"""Explicit capabilities for manifest/observation; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol
from digital_twin.domain.ontology_contracts import PortfolioOntology
from typing import Dict, List


class ManifestObservationStore(Protocol):
    def acquire_scoped_abox_write_lease(
        self, manifest_id: str = "", world_id: str = "", lease_seconds: int = 0
    ) -> Dict[str, object]: ...

    def active_abox_metadata(self, world_id: str = "") -> Dict[str, object]: ...

    address: Any

    def release_scoped_abox_write_lease(self, lease: Dict[str, object]) -> Dict[str, object]: ...

    def replace_scoped_manifest_marker_graph(
        self, marker_graph: PortfolioOntology
    ) -> Dict[str, object]: ...

    def scoped_abox_write_lease_status(self, world_id: str = "") -> Dict[str, object]: ...

    def worldview_manifest_marker_rows(
        self, world_id: str = "", manifest_id: str = "", limit: int = 0
    ) -> List[Dict[str, object]]: ...
