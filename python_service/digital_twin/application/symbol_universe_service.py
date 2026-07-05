from typing import Dict, Iterable, List

from ..domain.market_data import known_stock
from ..domain.repositories import MarketQuoteRepository, SymbolSourceGateway, SymbolUniverseRepository
from ..domain.symbol_universe import ListedSymbol, SUPPORTED_MARKETS, is_stale, stale_after_hours


DEFAULT_SYMBOL_SEEDS = ["005930", "000660", "TSLA", "AAPL", "NVDA", "MSFT", "AMD", "MSTR"]
MARKET_DATA_ACCOUNT_ID = "__market_data__"


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
    def __init__(
        self,
        store: SymbolUniverseRepository,
        source_gateway: SymbolSourceGateway,
        settings: Dict[str, str] = None,
        quote_cache: MarketQuoteRepository = None,
    ):
        self.store = store
        self.source_gateway = source_gateway
        self.settings = dict(settings or {})
        self.quote_cache = quote_cache

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
            descriptor = self.source_gateway.source_descriptor(market)
            markets.append({
                "market": market,
                "count": counts.get(market, 0),
                "lastSeenAt": last_seen,
                "stale": is_stale(last_seen, max_age),
                "source": descriptor["source"],
                "sourceUrl": descriptor["sourceUrl"],
            })
        payload = {
            "markets": markets,
            "sources": self.store.source_states(),
            "maxAgeHours": max_age,
            "total": sum(counts.values()),
        }
        if self.quote_cache:
            payload["marketData"] = self.quote_cache.summary("toss", MARKET_DATA_ACCOUNT_ID)
        return payload

    def attach_market_data(self, items: List[Dict[str, object]]) -> List[Dict[str, object]]:
        if not self.quote_cache or not items:
            return items
        quotes = self.quote_cache.load_many("toss", MARKET_DATA_ACCOUNT_ID, [item.get("symbol") for item in items])
        merged = []
        for item in items:
            symbol = str(item.get("symbol") or "").upper()
            quote = quotes.get(symbol) or {}
            next_item = dict(item)
            if quote:
                for key in [
                    "currentPrice",
                    "changeRate",
                    "quoteSource",
                    "quoteStatus",
                    "quoteMessage",
                    "dataQuality",
                    "volume",
                    "volumeRatio",
                    "tradingValue",
                    "ma5",
                    "ma20",
                    "ma60",
                    "ma120",
                    "ma200",
                    "ma20Slope",
                    "ma60Slope",
                    "ma20Distance",
                    "ma60Distance",
                ]:
                    if quote.get(key) not in (None, ""):
                        next_item[key] = quote.get(key)
                next_item["marketDataUpdatedAt"] = quote.get("updatedAt") or ""
            merged.append(next_item)
        return merged

    def search(self, query: str = "", market: str = "", limit: int = 80, offset: int = 0) -> Dict[str, object]:
        self.ensure_seed()
        max_age = self.max_age_hours()
        limit_value = max(1, min(500, int(limit or 80)))
        offset_value = max(0, int(offset or 0))
        result_total = self.store.search_count(query=query, market=market)
        items = self.store.search(query=query, market=market, limit=limit_value, offset=offset_value)
        payload_items = [item.to_dict(max_age) for item in items]
        return {
            "items": self.attach_market_data(payload_items),
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
            descriptor = self.source_gateway.source_descriptor(market)
            try:
                items = self.source_gateway.fetch_market_symbols(market)
                if hasattr(self.store, "refresh_market"):
                    count = self.store.refresh_market(market, descriptor["source"], descriptor["sourceUrl"], items)
                else:
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
