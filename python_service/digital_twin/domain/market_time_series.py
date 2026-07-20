from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from .portfolio import Position


GRANULARITY_SECONDS = {
    "3m": 3 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
}

WINDOW_GRANULARITY_PREFERENCES = {
    "1D": ["3m", "15m", "1h", "1d"],
    "3D": ["15m", "1h", "1d", "3m"],
    "5D": ["1h", "15m", "1d", "3m"],
    "20D": ["1d", "1h", "15m", "3m"],
}


def number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_timestamp(value: object) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_utc(value: object) -> str:
    parsed = parse_timestamp(value)
    if not parsed:
        return ""
    return parsed.isoformat().replace("+00:00", "Z")


def market_timestamp(value: object, market: object = "", currency: object = "") -> str:
    text = str(value or "").strip()
    if len(text) == 10 and text[4:5] == "-" and text[7:8] == "-":
        try:
            local = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=market_timezone(market, currency))
        except ValueError:
            return ""
        return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return iso_utc(value)


def market_session_date(value: object, market: object = "", currency: object = "") -> str:
    parsed = parse_timestamp(value)
    if not parsed:
        return ""
    return parsed.astimezone(market_timezone(market, currency)).date().isoformat()


def market_timezone(market: object, currency: object = "") -> ZoneInfo:
    market_code = str(market or "").upper().strip()
    currency_code = str(currency or "").upper().strip()
    if market_code in {"KR", "KOR", "KOREA", "KOSPI", "KOSDAQ", "KONEX", "KRX", "XKRX"} or currency_code == "KRW":
        return ZoneInfo("Asia/Seoul")
    if market_code in {"US", "USA", "NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "XNYS", "XNAS"} or currency_code == "USD":
        return ZoneInfo("America/New_York")
    return ZoneInfo("UTC")


def bucket_start(value: object, granularity: str, market: object = "", currency: object = "") -> str:
    parsed = parse_timestamp(value)
    if not parsed:
        return ""
    normalized = str(granularity or "3m").strip().lower()
    if normalized == "1d":
        local = parsed.astimezone(market_timezone(market, currency))
        local_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return local_midnight.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    seconds = GRANULARITY_SECONDS.get(normalized, GRANULARITY_SECONDS["3m"])
    epoch = int(parsed.timestamp())
    rounded = epoch - (epoch % seconds)
    return datetime.fromtimestamp(rounded, timezone.utc).isoformat().replace("+00:00", "Z")


def first_value(row: Dict[str, object], keys: Iterable[str]) -> object:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


@dataclass(frozen=True)
class MarketTimeSeriesObservation:
    account_id: str
    symbol: str
    granularity: str
    bucket_at: str
    observed_at: str
    source_as_of: str = ""
    provider: str = ""
    source_role: str = ""
    name: str = ""
    market: str = ""
    currency: str = ""
    sample_count: int = 1
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    current_price: float = 0.0
    change_rate: float = 0.0
    quantity: float = 0.0
    average_price: float = 0.0
    profit_loss_rate: float = 0.0
    volume: float = 0.0
    trading_value: float = 0.0
    volume_ratio: float = 0.0
    trade_strength: float = 0.0
    bid_ask_imbalance: float = 0.0
    foreign_net_volume: float = 0.0
    institution_net_volume: float = 0.0
    individual_net_volume: float = 0.0
    ma5: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    ma20_slope: float = 0.0
    ma60_slope: float = 0.0
    ma20_distance: float = 0.0
    ma60_distance: float = 0.0
    data_quality: str = "actual"

    @classmethod
    def from_position(
        cls,
        account_id: str,
        position: Position,
        observed_at: str,
        provider: str = "",
        granularity: str = "3m",
    ):
        stamp = iso_utc(observed_at or position.updated_at or position.source_as_of)
        price = number(position.current_price)
        return cls(
            account_id=str(account_id or ""),
            symbol=str(position.symbol or "").upper().strip(),
            granularity=granularity,
            bucket_at=bucket_start(stamp, granularity, position.market, position.currency),
            observed_at=stamp,
            source_as_of=iso_utc(position.source_as_of or position.updated_at or stamp),
            provider=str(provider or position.quote_source or ""),
            source_role=str(position.source or ""),
            name=str(position.name or position.symbol or ""),
            market=str(position.market or ""),
            currency=str(position.currency or ""),
            open_price=price,
            high_price=price,
            low_price=price,
            current_price=price,
            change_rate=number(position.change_rate),
            quantity=number(position.quantity),
            average_price=number(position.average_price),
            profit_loss_rate=number(position.profit_loss_rate),
            volume=number(position.volume),
            trading_value=number(position.trading_value),
            volume_ratio=number(position.volume_ratio),
            trade_strength=number(position.trade_strength),
            bid_ask_imbalance=number(position.bid_ask_imbalance),
            foreign_net_volume=number(position.foreign_net_volume),
            institution_net_volume=number(position.institution_net_volume),
            individual_net_volume=number(position.individual_net_volume),
            ma5=number(position.ma5),
            ma20=number(position.ma20),
            ma60=number(position.ma60),
            ma20_slope=number(position.ma20_slope),
            ma60_slope=number(position.ma60_slope),
            ma20_distance=number(position.ma20_distance),
            ma60_distance=number(position.ma60_distance),
            data_quality=str(position.data_quality or "actual"),
        )

    @classmethod
    def from_daily_candle(
        cls,
        account_id: str,
        symbol: str,
        candle: Dict[str, object],
        market: str = "",
        currency: str = "",
        provider: str = "",
        name: str = "",
    ):
        stamp = first_value(candle, ["timestamp", "date", "tradingDate", "tradeDate", "time", "updatedAt"])
        observed_at = market_timestamp(stamp, market, currency)
        close = number(first_value(candle, ["closePrice", "close", "currentPrice", "price", "lastPrice"]))
        open_price = number(first_value(candle, ["openPrice", "open"])) or close
        high_price = number(first_value(candle, ["highPrice", "high"])) or max(open_price, close)
        low_price = number(first_value(candle, ["lowPrice", "low"])) or min(open_price, close)
        return cls(
            account_id=str(account_id or ""),
            symbol=str(symbol or "").upper().strip(),
            granularity="1d",
            bucket_at=bucket_start(observed_at, "1d", market, currency),
            observed_at=observed_at,
            source_as_of=observed_at,
            provider=str(provider or ""),
            source_role="market-history",
            name=str(name or symbol or ""),
            market=str(market or ""),
            currency=str(currency or ""),
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            current_price=close,
            volume=number(first_value(candle, ["volume", "tradingVolume", "accumulatedVolume"])),
            trading_value=number(first_value(candle, ["tradingValue", "tradeValue", "tradingAmount"])),
            data_quality="actual",
        )

    def valid(self) -> bool:
        return bool(self.account_id and self.symbol and self.bucket_at and self.observed_at and self.current_price > 0)

    def to_row(self) -> Dict[str, object]:
        return asdict(self)


def granularity_preferences(window_key: object) -> List[str]:
    normalized = str(window_key or "").upper().strip()
    return list(WINDOW_GRANULARITY_PREFERENCES.get(normalized) or ["1d", "1h", "15m", "3m"])


def required_session_count(lookback_days: object) -> int:
    try:
        value = float(lookback_days or 1)
    except (TypeError, ValueError):
        value = 1.0
    return max(1, int(round(value)))
