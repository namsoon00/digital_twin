from typing import Dict, Iterable, List, Optional, Protocol

from .accounts import AccountConfig
from .portfolio import AccountSnapshot, AlertEvent
from .symbol_universe import ListedSymbol


class AccountRepository(Protocol):
    def load(self) -> List[AccountConfig]:
        ...

    def load_all(self) -> List[AccountConfig]:
        ...

    def load_saved(self) -> List[AccountConfig]:
        ...

    def upsert(self, account: AccountConfig) -> None:
        ...

    def remove(self, account_id: str) -> bool:
        ...


class SnapshotProvider(Protocol):
    def build_snapshot(self, account: AccountConfig) -> AccountSnapshot:
        ...


class MonitorStateRepository(Protocol):
    @property
    def previous(self) -> Dict[str, object]:
        ...

    @property
    def sent(self) -> Dict[str, object]:
        ...

    def save_snapshot(self, snapshot: AccountSnapshot) -> None:
        ...

    def mark_sent(self, events: Iterable[AlertEvent]) -> None:
        ...

    def write(self) -> None:
        ...


class SnapshotMonitor(Protocol):
    def events_for_snapshot(self, snapshot: AccountSnapshot, previous: Dict[str, object]) -> List[AlertEvent]:
        ...

    def apply_cadence(self, events: List[AlertEvent], store: MonitorStateRepository, force: bool = False) -> List[AlertEvent]:
        ...


class NotificationGateway(Protocol):
    def send_events(self, events: List[AlertEvent], dry_run: bool = False, accounts=None):
        ...


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

    def source_states(self) -> List[Dict[str, object]]:
        ...


class SymbolSourceGateway(Protocol):
    def fetch_market_symbols(self, market: str) -> List[ListedSymbol]:
        ...

    def source_descriptor(self, market: str) -> Dict[str, str]:
        ...
