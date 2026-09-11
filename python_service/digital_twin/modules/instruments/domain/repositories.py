"""Repository capabilities owned by instruments."""

from typing import Dict, Iterable, List, Optional, Protocol
from digital_twin.modules.instruments.contracts import ListedSymbol


class SymbolUniverseRepository(Protocol):
    def upsert_many(self, symbols: Iterable[ListedSymbol]) -> int:
        ...

    def counts_by_market(self) -> Dict[str, int]:
        ...

    def latest_seen_by_market(self) -> Dict[str, str]:
        ...

    def search(self, query: str = "", market: str = "", limit: int = 80, offset: int = 0) -> List[ListedSymbol]:
        ...

    def search_count(self, query: str = "", market: str = "") -> int:
        ...

    def get(self, symbol: str, market: str = "") -> Optional[ListedSymbol]:
        ...

    def mark_source(self, market: str, source: str, source_url: str, status: str, count: int = 0, error: str = "") -> None:
        ...

    def refresh_market(self, market: str, source: str, source_url: str, symbols: Iterable[ListedSymbol]) -> int:
        ...

    def source_states(self) -> List[Dict[str, object]]:
        ...


class SymbolSourceGateway(Protocol):
    def fetch_market_symbols(self, market: str) -> List[ListedSymbol]:
        ...

    def source_descriptor(self, market: str) -> Dict[str, str]:
        ...
