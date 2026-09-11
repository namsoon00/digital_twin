"""Version-neutral routing contract for ontology graph-store shards."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from typing import Dict, Iterable


ONTOLOGY_STORE_ROUTE_VERSION = "ontology-store-route-v1"


@dataclass(frozen=True)
class OntologyStoreRoute:
    shard_id: str
    shard_index: int
    shard_count: int
    routing_key: str
    symbols: tuple
    account_id: str = ""
    version: str = ONTOLOGY_STORE_ROUTE_VERSION

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["symbols"] = list(self.symbols)
        return payload


def ontology_store_route(
    symbols: Iterable[object],
    account_id: str = "",
    shard_count: int = 1,
) -> OntologyStoreRoute:
    """Route a bounded subject set without depending on TypeDB APIs.

    The same contract can select another TypeDB database or a future graph
    adapter. A multi-symbol request is expected to be split before routing;
    sorting here only guarantees retry determinism.
    """

    selected = tuple(sorted({
        str(value or "").upper().strip()
        for value in symbols or []
        if str(value or "").strip()
    }))
    bounded_count = max(1, min(64, int(shard_count or 1)))
    subject_key = selected[0] if selected else str(account_id or "global").strip()
    digest = hashlib.sha256(subject_key.encode("utf-8")).hexdigest()
    index = int(digest[:16], 16) % bounded_count
    return OntologyStoreRoute(
        shard_id="ontology-shard-" + str(index).zfill(2),
        shard_index=index,
        shard_count=bounded_count,
        routing_key=subject_key,
        symbols=selected,
        account_id=str(account_id or "").strip(),
    )
