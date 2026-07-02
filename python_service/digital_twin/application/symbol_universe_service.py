from typing import Dict, Iterable, List

from ..domain.analytics import known_stock
from ..domain.symbol_universe import ListedSymbol, SUPPORTED_MARKETS, is_stale, stale_after_hours
from ..infrastructure.settings import runtime_settings
from ..infrastructure.sqlite_operational import SQLiteSymbolUniverseStore
from ..infrastructure.symbol_sources import fetch_market_symbols, source_descriptor


DEFAULT_SYMBOL_SEEDS = ["005930", "000660", "TSLA", "AAPL", "NVDA", "MSFT", "AMD", "MSTR"]


def seed_symbol(symbol: str) -> ListedSymbol:
    info = known_stock(symbol)
    market = info.get("market") or ("NASDAQ" if symbol.isalpha() else "KOSPI")
    if market == "US":
        market = "NASDAQ"
    if market == "KR":
        market = "KOSPI"
    return ListedSymbol.create(
        symbol=info["symbol"],
        name=info["name"],
        market=market,
        exchange=market,
        currency=info.get("currency") or "",
        sector=info.get("sector") or "",
        asset_type="STOCK",
        source="Exit Lens seed",
        source_url="local-default",
    )


class SymbolUniverseService:
    def __init__(self, store: SQLiteSymbolUniverseStore = None, settings: Dict[str, str] = None):
        self.store = store or SQLiteSymbolUniverseStore()
        self.settings = settings or runtime_settings()

    def max_age_hours(self) -> int:
        return stale_after_hours(self.settings.get("symbolUniverseMaxAgeHours"), 24)

    def ensure_seed(self) -> None:
        counts = self.store.counts_by_market()
        if counts:
            return
        self.store.upsert_many([seed_symbol(symbol) for symbol in DEFAULT_SYMBOL_SEEDS])

    def summary(self) -> Dict[str, object]:
        self.ensure_seed()
        max_age = self.max_age_hours()
        counts = self.store.counts_by_market()
        latest = self.store.latest_seen_by_market()
        markets = []
        for market in SUPPORTED_MARKETS:
            last_seen = latest.get(market, "")
            descriptor = source_descriptor(market)
            markets.append({
                "market": market,
                "count": counts.get(market, 0),
                "lastSeenAt": last_seen,
                "stale": is_stale(last_seen, max_age),
                "source": descriptor["source"],
                "sourceUrl": descriptor["sourceUrl"],
            })
        return {
            "markets": markets,
            "sources": self.store.source_states(),
            "maxAgeHours": max_age,
            "total": sum(counts.values()),
        }

    def search(self, query: str = "", market: str = "", limit: int = 80, offset: int = 0) -> Dict[str, object]:
        self.ensure_seed()
        max_age = self.max_age_hours()
        limit_value = max(1, min(500, int(limit or 80)))
        offset_value = max(0, int(offset or 0))
        result_total = self.store.search_count(query=query, market=market)
        items = self.store.search(query=query, market=market, limit=limit_value, offset=offset_value)
        return {
            "items": [item.to_dict(max_age) for item in items],
            "summary": self.summary(),
            "resultTotal": result_total,
            "limit": limit_value,
            "offset": offset_value,
            "hasMore": offset_value + len(items) < result_total,
        }

    def refresh(self, markets: Iterable[str] = None) -> Dict[str, object]:
        selected = [str(market or "").upper() for market in (markets or SUPPORTED_MARKETS)]
        selected = [market for market in selected if market in SUPPORTED_MARKETS]
        if not selected:
            selected = list(SUPPORTED_MARKETS)
        results: List[Dict[str, object]] = []
        for market in selected:
            descriptor = source_descriptor(market)
            try:
                items = fetch_market_symbols(market)
                count = self.store.upsert_many(items)
                self.store.mark_source(market, descriptor["source"], descriptor["sourceUrl"], "ok", count=count)
                results.append({"market": market, "status": "ok", "count": count, **descriptor})
            except Exception as error:  # noqa: BLE001 - one source failure must not discard cached symbols.
                self.store.mark_source(market, descriptor["source"], descriptor["sourceUrl"], "error", error=str(error))
                results.append({"market": market, "status": "error", "count": 0, "error": str(error), **descriptor})
        return {"results": results, "summary": self.summary()}

    def enrich(self, symbol: str) -> Dict[str, object]:
        self.ensure_seed()
        item = self.store.get(symbol)
        if item:
            return item.to_dict(self.max_age_hours())
        return seed_symbol(symbol).to_dict(self.max_age_hours())
