from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict


SUPPORTED_MARKETS = ("KOSPI", "KOSDAQ", "NASDAQ")

SYMBOL_SEARCH_ALIASES = {
    "팔란티어": ("PLTR", "Palantir", "Palantir Technologies"),
    "palantir": ("PLTR", "Palantir", "Palantir Technologies"),
    "pltr": ("PLTR", "Palantir", "Palantir Technologies"),
    "애플": ("AAPL", "Apple"),
    "apple": ("AAPL", "Apple"),
    "테슬라": ("TSLA", "Tesla"),
    "tesla": ("TSLA", "Tesla"),
    "엔비디아": ("NVDA", "NVIDIA"),
    "nvidia": ("NVDA", "NVIDIA"),
    "마이크로소프트": ("MSFT", "Microsoft"),
    "microsoft": ("MSFT", "Microsoft"),
    "하이닉스": ("000660", "SK하이닉스"),
    "sk하이닉스": ("000660", "SK하이닉스"),
    "삼성전자": ("005930", "삼성전자"),
    "네이버": ("035420", "NAVER"),
    "naver": ("035420", "NAVER"),
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_market(value: str) -> str:
    market = str(value or "").strip().upper()
    aliases = {
        "KR": "KOSPI",
        "KS": "KOSPI",
        "KQ": "KOSDAQ",
        "US": "NASDAQ",
        "NASDAQGS": "NASDAQ",
        "NASDAQGM": "NASDAQ",
        "NASDAQCM": "NASDAQ",
    }
    return aliases.get(market, market)


def normalize_symbol(value: str) -> str:
    return str(value or "").strip().upper()


def compact_search_text(value: str) -> str:
    return "".join(
        char
        for char in str(value or "").strip().lower()
        if char.isalnum()
    )


def symbol_search_terms(query: str) -> list:
    raw = str(query or "").strip()
    compact = compact_search_text(raw)
    alias_ready = len(compact) >= 2 or any("\uac00" <= char <= "\ud7a3" for char in compact)
    terms = []

    def add(value: str) -> None:
        text = str(value or "").strip()
        if text and text not in terms:
            terms.append(text)

    if compact and alias_ready:
        for alias, values in SYMBOL_SEARCH_ALIASES.items():
            alias_compact = compact_search_text(alias)
            matched = alias_compact.startswith(compact)
            if not matched and len(compact) >= 2:
                matched = compact in alias_compact
            if not matched:
                for value in values:
                    value_compact = compact_search_text(value)
                    if value_compact.startswith(compact) or (len(compact) >= 2 and compact in value_compact):
                        matched = True
                        break
            if matched:
                for value in values:
                    add(value)
    add(raw)
    return terms


def symbol_search_symbol_candidates(query: str) -> list:
    candidates = []
    for term in symbol_search_terms(query):
        symbol = normalize_symbol(term)
        if symbol and symbol.replace(".", "").replace("-", "").isalnum() and symbol not in candidates:
            candidates.append(symbol)
    return candidates


def market_currency(market: str) -> str:
    return "KRW" if normalize_market(market) in {"KOSPI", "KOSDAQ"} else "USD"


def stale_after_hours(value: str, fallback: int = 24) -> int:
    try:
        parsed = int(float(str(value or "").strip()))
    except ValueError:
        parsed = fallback
    return max(1, parsed)


def is_stale(timestamp: str, max_age_hours: int = 24) -> bool:
    if not timestamp:
        return True
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return True
    now = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).total_seconds() > max_age_hours * 3600


@dataclass
class ListedSymbol:
    symbol: str
    name: str
    market: str
    exchange: str = ""
    currency: str = ""
    sector: str = ""
    asset_type: str = "STOCK"
    source: str = ""
    source_url: str = ""
    active: bool = True
    fetched_at: str = ""
    first_seen_at: str = ""
    last_seen_at: str = ""

    @classmethod
    def create(
        cls,
        symbol: str,
        name: str,
        market: str,
        exchange: str = "",
        currency: str = "",
        sector: str = "",
        asset_type: str = "STOCK",
        source: str = "",
        source_url: str = "",
        fetched_at: str = "",
    ):
        stamp = fetched_at or utc_now_iso()
        normalized_market = normalize_market(market)
        return cls(
            symbol=normalize_symbol(symbol),
            name=str(name or symbol or "").strip(),
            market=normalized_market,
            exchange=str(exchange or normalized_market).strip().upper(),
            currency=str(currency or market_currency(normalized_market)).strip().upper(),
            sector=str(sector or "").strip(),
            asset_type=str(asset_type or "STOCK").strip().upper(),
            source=str(source or "").strip(),
            source_url=str(source_url or "").strip(),
            active=True,
            fetched_at=stamp,
            first_seen_at=stamp,
            last_seen_at=stamp,
        )

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        return cls(
            symbol=normalize_symbol(payload.get("symbol")),
            name=str(payload.get("name") or payload.get("symbol") or "").strip(),
            market=normalize_market(str(payload.get("market") or "")),
            exchange=str(payload.get("exchange") or payload.get("market") or "").strip().upper(),
            currency=str(payload.get("currency") or "").strip().upper(),
            sector=str(payload.get("sector") or "").strip(),
            asset_type=str(payload.get("assetType") or payload.get("asset_type") or "STOCK").strip().upper(),
            source=str(payload.get("source") or "").strip(),
            source_url=str(payload.get("sourceUrl") or payload.get("source_url") or "").strip(),
            active=payload.get("active") is not False,
            fetched_at=str(payload.get("fetchedAt") or payload.get("fetched_at") or ""),
            first_seen_at=str(payload.get("firstSeenAt") or payload.get("first_seen_at") or ""),
            last_seen_at=str(payload.get("lastSeenAt") or payload.get("last_seen_at") or ""),
        )

    def key(self) -> str:
        return self.market + ":" + self.symbol

    def to_dict(self, max_age_hours: int = 24) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "market": self.market,
            "exchange": self.exchange,
            "currency": self.currency,
            "sector": self.sector,
            "assetType": self.asset_type,
            "source": self.source,
            "sourceUrl": self.source_url,
            "active": self.active,
            "fetchedAt": self.fetched_at,
            "firstSeenAt": self.first_seen_at,
            "lastSeenAt": self.last_seen_at,
            "stale": is_stale(self.last_seen_at or self.fetched_at, max_age_hours),
        }
