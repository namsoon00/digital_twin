"""Repository capabilities owned by market data."""

from typing import Callable, Dict, Iterable, List, Protocol, Tuple
from digital_twin.modules.accounts.contracts import AccountConfig
from digital_twin.domain.portfolio import AccountSnapshot, Position


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


class MarketTimeSeriesRepository(Protocol):
    def record_snapshots_with_connection(
        self,
        connection,
        snapshots: Iterable[AccountSnapshot],
    ) -> Dict[str, object]:
        ...

    def record_daily_candles(
        self,
        candles_by_symbol: Dict[str, List[Dict[str, object]]],
        metadata_by_symbol: Dict[str, Dict[str, object]] = None,
        provider: str = "toss-candles",
    ) -> Dict[str, object]:
        ...

    def record_positions(
        self,
        account_id: str,
        positions: Iterable[object],
        observed_at: str,
        provider: str = "",
        replace: bool = True,
    ) -> Dict[str, object]:
        ...

    def load_temporal_windows(
        self,
        account_id: str,
        symbols: Iterable[str],
        definitions: Iterable[object],
        as_of: str = "",
    ) -> Dict[str, Dict[str, List[Dict[str, object]]]]:
        ...

    def load_instrument_series(
        self,
        account_id: str,
        symbol: str,
        granularity: str = "1d",
        limit: int = 260,
        as_of: str = "",
    ) -> List[Dict[str, object]]:
        ...

    def load_outcome_observations(
        self,
        account_id: str,
        targets: Iterable[Dict[str, object]],
        max_delay_minutes: int = 180,
    ) -> Dict[str, Dict[str, object]]:
        ...

    def load_baseline_observations(
        self,
        account_id: str,
        targets: Iterable[Dict[str, object]],
        max_age_minutes: int = 60 * 24 * 7,
    ) -> Dict[str, Dict[str, object]]:
        ...

    def summary(self, account_id: str = "") -> Dict[str, object]:
        ...


class MarketDataProvider(Protocol):
    def fetch_access_token(self) -> str:
        ...
    def fetch_positions(self) -> Tuple[str, str, List[Position], float, str, List[Position]]:
        ...

    def fetch_focus_targets(self) -> Tuple[str, str, str, List[Position], List[Position]]:
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
