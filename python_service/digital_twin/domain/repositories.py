from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Tuple

from .accounts import AccountConfig
from .ontology import PortfolioOntology
from .portfolio import AccountSnapshot, AlertEvent, Position
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


@dataclass
class MonitoringCycleRecordResult:
    delivered: bool
    queued: int = 0
    reason: str = ""


class MonitoringCycleRecorder(Protocol):
    def record_cycle(
        self,
        account_ids: List[str],
        snapshots: List[AccountSnapshot],
        alert_events: List[AlertEvent],
        dry_run: bool = False,
    ) -> MonitoringCycleRecordResult:
        ...


class OntologyGraphRepository(Protocol):
    def save_graph(self, graph: PortfolioOntology) -> Dict[str, object]:
        ...


class OntologyProjectionRecorder(Protocol):
    def record_snapshot(self, snapshot: AccountSnapshot) -> Dict[str, object]:
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

    def refresh_market(self, market: str, source: str, source_url: str, symbols: Iterable[ListedSymbol]) -> int:
        ...

    def source_states(self) -> List[Dict[str, object]]:
        ...


class SymbolSourceGateway(Protocol):
    def fetch_market_symbols(self, market: str) -> List[ListedSymbol]:
        ...

    def source_descriptor(self, market: str) -> Dict[str, str]:
        ...


class MarketQuoteRepository(Protocol):
    def load(self, provider: str, account_id: str, symbol: str) -> Dict[str, object]:
        ...

    def load_many(self, provider: str, account_id: str, symbols: Iterable[str]) -> Dict[str, Dict[str, object]]:
        ...

    def save(self, provider: str, account_id: str, symbol: str, payload: Dict[str, object]) -> None:
        ...

    def summary(self, provider: str, account_id: str) -> Dict[str, object]:
        ...

    def stale_universe_symbols(
        self,
        provider: str,
        account_id: str,
        markets: Iterable[str],
        limit: int = 200,
        max_age_minutes: int = 240,
    ) -> List[Dict[str, object]]:
        ...


class MarketDataProvider(Protocol):
    def fetch_access_token(self) -> str:
        ...

    def fetch_prices(self, token: str, symbols: Iterable[str]) -> Tuple[Dict[str, Dict[str, object]], str]:
        ...

    def fetch_daily_candles(self, token: str, symbol: str) -> Tuple[List[Dict[str, object]], str]:
        ...

    def merge_market_data(
        self,
        position: Position,
        quote: Dict[str, object],
        indicators: Dict[str, object],
        cached: Dict[str, object],
        quote_live: bool = False,
        indicators_live: bool = False,
    ) -> Position:
        ...


MarketDataProviderFactory = Callable[[AccountConfig, MarketQuoteRepository], MarketDataProvider]
