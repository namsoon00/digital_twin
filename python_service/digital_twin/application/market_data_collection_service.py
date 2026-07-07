import signal
import time
from typing import Dict, Iterable, List

from ..domain.accounts import AccountConfig
from ..domain.events import market_data_collected_event
from ..domain.market_data import normalize_position, number, technical_indicators_from_candles
from ..domain.portfolio import Position, utc_now_iso
from ..domain.repositories import AccountRepository, MarketDataProvider, MarketDataProviderFactory, MarketQuoteRepository
from ..domain.symbol_universe import SUPPORTED_MARKETS, normalize_market


MARKET_DATA_ACCOUNT_ID = "__market_data__"


def truthy(value: str, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "no", "off", "disabled"}


def int_setting(settings: Dict[str, str], key: str, fallback: int, lower: int = 0, upper: int = 100000) -> int:
    try:
        parsed = int(float(str(settings.get(key) or "").strip()))
    except ValueError:
        parsed = fallback
    return max(lower, min(upper, parsed))


def configured_markets(raw_value: str = "") -> List[str]:
    markets = []
    for raw in str(raw_value or "").split(","):
        market = normalize_market(raw)
        if market in SUPPORTED_MARKETS and market not in markets:
            markets.append(market)
    return markets or list(SUPPORTED_MARKETS)


def position_payload(position: Position, base: Dict[str, object]) -> Dict[str, object]:
    return {
        "symbol": position.symbol,
        "name": position.name or str(base.get("name") or position.symbol),
        "market": position.market or str(base.get("market") or ""),
        "exchange": str(base.get("exchange") or position.market or ""),
        "currency": position.currency or str(base.get("currency") or ""),
        "sector": position.sector or str(base.get("sector") or ""),
        "assetType": str(base.get("assetType") or "STOCK"),
        "currentPrice": position.current_price,
        "changeRate": position.change_rate,
        "quoteSource": position.quote_source,
        "quoteStatus": position.quote_status,
        "quoteMessage": position.quote_message,
        "dataQuality": position.data_quality or "actual",
        "updatedAt": position.updated_at or utc_now_iso(),
        "collectionSource": "market-data-collector",
        "collectionPurpose": "recommendation-universe",
        "tradingValue": position.trading_value,
        "volume": position.volume,
        "volumeRatio": position.volume_ratio,
        "ma5": position.ma5,
        "ma20": position.ma20,
        "ma60": position.ma60,
        "ma120": position.ma120,
        "ma200": position.ma200,
        "ma20Slope": position.ma20_slope,
        "ma60Slope": position.ma60_slope,
        "ma20Distance": position.ma20_distance,
        "ma60Distance": position.ma60_distance,
    }


class MarketDataCollectionRunner:
    def __init__(
        self,
        account_repository: AccountRepository,
        symbol_service,
        quote_cache: MarketQuoteRepository,
        settings: Dict[str, str],
        provider_factory: MarketDataProviderFactory,
        event_publisher=None,
        sleep_fn=time.sleep,
    ):
        self.account_repository = account_repository
        self.symbol_service = symbol_service
        self.quote_cache = quote_cache
        self.settings = dict(settings or {})
        self.provider_factory = provider_factory
        self.event_publisher = event_publisher
        self.sleep_fn = sleep_fn

    def enabled(self) -> bool:
        return truthy(self.settings.get("marketDataCollectionEnabled"), True)

    def markets(self) -> List[str]:
        return configured_markets(self.settings.get("marketDataCollectionMarkets"))

    def price_batch_size(self) -> int:
        return int_setting(self.settings, "marketDataPriceBatchSize", 200, 1, 200)

    def candle_batch_size(self) -> int:
        return int_setting(self.settings, "marketDataCandleBatchSize", 25, 0, self.price_batch_size())

    def max_age_minutes(self) -> int:
        return int_setting(self.settings, "marketDataMaxAgeMinutes", 240, 1, 1440 * 30)

    def refresh_universe_enabled(self) -> bool:
        return truthy(self.settings.get("marketDataRefreshUniverse"), True)

    def select_account(self) -> AccountConfig:
        accounts = self.account_repository.load()
        for account in accounts:
            if account.enabled and account.provider == "toss" and account.client_id and account.client_secret:
                return account
        return None

    def refresh_symbol_universe_if_needed(self) -> Dict[str, object]:
        if not self.refresh_universe_enabled():
            return {"status": "disabled"}
        try:
            summary = self.symbol_service.summary()
            markets = [
                item.get("market")
                for item in summary.get("markets") or []
                if item.get("market") in self.markets() and (item.get("stale") or not item.get("count"))
            ]
            if not markets:
                return {"status": "fresh", "summary": summary}
            return {"status": "refreshed", **self.symbol_service.refresh(markets)}
        except Exception as error:  # noqa: BLE001 - market-data collection can continue with cached symbol universe.
            return {"status": "error", "error": str(error)}

    def base_position(self, item: Dict[str, object]) -> Position:
        return normalize_position({
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "market": item.get("market"),
            "currency": item.get("currency"),
            "sector": item.get("sector"),
        })

    def collect_candles(self, provider: MarketDataProvider, token: str, symbols: Iterable[str]):
        result: Dict[str, Dict[str, object]] = {}
        for index, symbol in enumerate(symbols):
            try:
                if index:
                    self.sleep_fn(0.22)
                candles, token = provider.fetch_daily_candles(token, symbol)
                indicators = technical_indicators_from_candles(candles)
                if indicators:
                    result[symbol] = indicators
            except Exception:
                continue
        return result, token

    def run_once(self, force: bool = False) -> Dict[str, object]:
        if not self.enabled() and not force:
            return {"status": "disabled", "savedCount": 0}
        universe_refresh = self.refresh_symbol_universe_if_needed()
        account = self.select_account()
        if not account:
            return {
                "status": "missingCredentials",
                "message": "Toss credentials가 설정된 계정이 없습니다.",
                "universeRefresh": universe_refresh,
                "savedCount": 0,
            }
        markets = self.markets()
        selected = self.quote_cache.stale_universe_symbols(
            "toss",
            MARKET_DATA_ACCOUNT_ID,
            markets=markets,
            limit=self.price_batch_size(),
            max_age_minutes=0 if force else self.max_age_minutes(),
        )
        if not selected:
            return {
                "status": "fresh",
                "markets": markets,
                "universeRefresh": universe_refresh,
                "savedCount": 0,
                "cache": self.quote_cache.summary("toss", MARKET_DATA_ACCOUNT_ID),
            }
        provider = self.provider_factory(account, self.quote_cache)
        token = provider.fetch_access_token()
        symbols = [str(item.get("symbol") or "").upper() for item in selected if item.get("symbol")]
        prices, token = provider.fetch_prices(token, symbols)
        candle_symbols = symbols[:self.candle_batch_size()]
        indicators, token = self.collect_candles(provider, token, candle_symbols)
        saved = 0
        for item in selected:
            symbol = str(item.get("symbol") or "").upper()
            if not symbol:
                continue
            quote = prices.get(symbol) or {}
            indicator = indicators.get(symbol) or {}
            if not quote and not indicator:
                continue
            position = provider.merge_market_data(
                self.base_position(item),
                quote,
                indicator,
                self.quote_cache.load("toss", MARKET_DATA_ACCOUNT_ID, symbol),
                quote_live=bool(quote),
                indicators_live=bool(indicator),
            )
            payload = position_payload(position, item)
            if not number(payload.get("currentPrice")) and not any(number(payload.get(key)) for key in ["ma20", "ma60", "volume"]):
                continue
            self.quote_cache.save("toss", MARKET_DATA_ACCOUNT_ID, symbol, payload)
            saved += 1
        result = {
            "status": "ok",
            "provider": "toss",
            "markets": markets,
            "selectedCount": len(selected),
            "priceCount": len(prices),
            "candleCount": len(indicators),
            "savedCount": saved,
            "dataQuality": "actual",
            "universeRefresh": universe_refresh,
            "cache": self.quote_cache.summary("toss", MARKET_DATA_ACCOUNT_ID),
        }
        if self.event_publisher and saved:
            event = market_data_collected_event(result)
            if hasattr(self.event_publisher, "publish"):
                self.event_publisher.publish(event)
            else:
                self.event_publisher.handle(event)
        return result

    def status(self) -> Dict[str, object]:
        return {
            "enabled": self.enabled(),
            "markets": self.markets(),
            "priceBatchSize": self.price_batch_size(),
            "candleBatchSize": self.candle_batch_size(),
            "maxAgeMinutes": self.max_age_minutes(),
            "cache": self.quote_cache.summary("toss", MARKET_DATA_ACCOUNT_ID),
            "symbolUniverse": self.symbol_service.summary(),
        }


class MarketDataCollectionScheduler:
    def __init__(self, runner: MarketDataCollectionRunner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(10 * 60, int(interval_seconds or 3600))
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        print("Python market data collector started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                result = self.runner.run_once()
                print("Market data collection " + str(result.get("status")) + " saved=" + str(result.get("savedCount", 0)))
            except Exception as error:  # noqa: BLE001 - long-running collector must continue after provider failures.
                print("Python market data collector error: " + str(error))
            elapsed = time.monotonic() - started
            sleep_seconds = max(1.0, self.interval_seconds - elapsed)
            end_at = time.monotonic() + sleep_seconds
            while self.running and time.monotonic() < end_at:
                time.sleep(min(1.0, end_at - time.monotonic()))
