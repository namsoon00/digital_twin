import signal
import time
from typing import Dict, Iterable, List, Tuple

from ..domain.accounts import AccountConfig
from ..domain.events import market_data_collected_event, ontology_reasoning_requested_event
from ..domain.fact_changes import market_fact_change
from ..domain.market_data import normalize_position, number, technical_indicators_from_candles
from ..domain.materiality import market_change_materiality
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


def position_payload(position: Position, base: Dict[str, object], collection_purpose: str = "account-focus") -> Dict[str, object]:
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
        "collectionPurpose": collection_purpose,
        "collectionTarget": position.source or "holding",
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

    def select_accounts(self) -> List[AccountConfig]:
        accounts = self.account_repository.load()
        selected = []
        for account in accounts:
            if account.enabled and account.provider == "toss" and account.client_id and account.client_secret:
                selected.append(account)
        return selected

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

    def focus_positions(self, provider: MarketDataProvider):
        token = ""
        if hasattr(provider, "fetch_focus_targets"):
            mode, status, token, positions, watchlist = provider.fetch_focus_targets()
        else:
            mode, status, positions, _cash, _currency, watchlist = provider.fetch_positions()
        if str(mode or "").lower() != "live":
            return mode, status, token, []
        seen = set()
        focused = []
        for position in list(positions or []) + list(watchlist or []):
            if not position or position.is_cash() or not position.symbol:
                continue
            symbol = position.symbol.upper()
            if symbol in seen:
                continue
            seen.add(symbol)
            focused.append(position)
        return mode, status, token, focused

    def merge_focus_market_data(
        self,
        provider: MarketDataProvider,
        token: str,
        focused_by_account: List[Dict[str, object]],
    ) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
        symbol_order: List[str] = []
        for entry in focused_by_account:
            for position in entry.get("positions") or []:
                symbol = str(position.symbol or "").upper()
                if symbol and symbol not in symbol_order:
                    symbol_order.append(symbol)
        if not symbol_order:
            return focused_by_account, {"symbols": [], "priceCount": 0, "candleCount": 0}
        if not token:
            token = provider.fetch_access_token()
        try:
            prices, token = provider.fetch_prices(token, symbol_order)
        except Exception:
            prices = {}
        indicators, token = self.collect_candles(provider, token, symbol_order)
        merged_entries: List[Dict[str, object]] = []
        for entry in focused_by_account:
            merged_positions = []
            for position in entry.get("positions") or []:
                symbol = str(position.symbol or "").upper()
                cached = self.quote_cache.load("toss", MARKET_DATA_ACCOUNT_ID, symbol)
                merged_positions.append(provider.merge_market_data(
                    position,
                    prices.get(symbol) or {},
                    indicators.get(symbol) or {},
                    cached,
                    quote_live=bool(prices.get(symbol)),
                    indicators_live=bool(indicators.get(symbol)),
                ))
            next_entry = dict(entry)
            next_entry["positions"] = merged_positions
            merged_entries.append(next_entry)
        return merged_entries, {
            "symbols": symbol_order,
            "priceCount": len(prices),
            "candleCount": len(indicators),
        }

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
        accounts = self.select_accounts()
        if not accounts:
            return {
                "status": "missingCredentials",
                "message": "Toss credentials가 설정된 계정이 없습니다.",
                "universeRefresh": universe_refresh,
                "savedCount": 0,
            }
        markets = self.markets()
        focused_by_account: List[Dict[str, object]] = []
        unavailable_accounts: List[Dict[str, str]] = []
        merge_provider = None
        merge_token = ""
        for account in accounts:
            provider = self.provider_factory(account, self.quote_cache)
            mode, account_status, token, focused_positions = self.focus_positions(provider)
            if str(mode or "").lower() != "live":
                unavailable_accounts.append({
                    "accountId": account.account_id,
                    "mode": str(mode or ""),
                    "status": str(account_status or ""),
                })
                continue
            if merge_provider is None:
                merge_provider = provider
                merge_token = token
            focused_by_account.append({
                "account": account,
                "mode": mode,
                "status": account_status,
                "positions": focused_positions,
            })
        if not focused_by_account:
            return {
                "status": "accountDataUnavailable",
                "message": "실데이터를 읽은 계정이 없습니다.",
                "markets": markets,
                "collectionScope": "account-focus",
                "accountCount": len(accounts),
                "liveAccountCount": 0,
                "unavailableAccounts": unavailable_accounts,
                "universeRefresh": universe_refresh,
                "savedCount": 0,
                "cache": self.quote_cache.summary("toss", MARKET_DATA_ACCOUNT_ID),
            }
        focused_by_account, merge_summary = self.merge_focus_market_data(merge_provider, merge_token, focused_by_account)
        if not merge_summary.get("symbols"):
            return {
                "status": "fresh",
                "markets": markets,
                "collectionScope": "account-focus",
                "accountCount": len(accounts),
                "liveAccountCount": len(focused_by_account),
                "unavailableAccounts": unavailable_accounts,
                "universeRefresh": universe_refresh,
                "savedCount": 0,
                "cache": self.quote_cache.summary("toss", MARKET_DATA_ACCOUNT_ID),
            }
        symbols = list(merge_summary.get("symbols") or [])
        saved = 0
        account_saved = 0
        changed = 0
        changed_symbols: List[str] = []
        changed_fields_by_symbol: Dict[str, List[str]] = {}
        material_symbols: List[str] = []
        materiality_assessments: Dict[str, Dict[str, object]] = {}
        global_saved_symbols = set()
        account_symbol_counts: Dict[str, int] = {}
        for entry in focused_by_account:
            account = entry["account"]
            account_count = 0
            for position in entry.get("positions") or []:
                symbol = str(position.symbol or "").upper()
                if not symbol:
                    continue
                payload = position_payload(position, position.to_dict(), "account-focus")
                if not number(payload.get("currentPrice")) and not any(number(payload.get(key)) for key in ["ma20", "ma60", "volume"]):
                    continue
                self.quote_cache.save("toss", account.account_id, symbol, payload)
                account_saved += 1
                account_count += 1
                if symbol in global_saved_symbols:
                    continue
                cached = self.quote_cache.load("toss", MARKET_DATA_ACCOUNT_ID, symbol)
                change = market_fact_change(cached, payload)
                self.quote_cache.save("toss", MARKET_DATA_ACCOUNT_ID, symbol, payload)
                global_saved_symbols.add(symbol)
                saved += 1
                if change.get("changed"):
                    changed += 1
                    changed_symbols.append(symbol)
                    changed_fields_by_symbol[symbol] = list(change.get("fields") or [])
                    assessment = market_change_materiality(symbol, cached, payload, change, self.settings)
                    materiality_assessments[symbol] = assessment.to_dict()
                    if assessment.passed:
                        material_symbols.append(symbol)
            account_symbol_counts[account.account_id] = account_count
        result = {
            "status": "ok",
            "provider": "toss",
            "markets": markets,
            "collectionScope": "account-focus",
            "accountCount": len(accounts),
            "liveAccountCount": len(focused_by_account),
            "unavailableAccounts": unavailable_accounts,
            "accountSymbolCounts": account_symbol_counts,
            "symbols": symbols,
            "selectedCount": len(symbols),
            "accountSelectedCount": sum(account_symbol_counts.values()),
            "priceCount": int(merge_summary.get("priceCount") or 0),
            "candleCount": int(merge_summary.get("candleCount") or 0),
            "savedCount": saved,
            "accountSavedCount": account_saved,
            "changedCount": changed,
            "changedSymbols": changed_symbols,
            "changedFieldsBySymbol": changed_fields_by_symbol,
            "materialChangedCount": len(material_symbols),
            "materialChangedSymbols": material_symbols,
            "materialityAssessments": materiality_assessments,
            "dataQuality": "actual",
            "universeRefresh": universe_refresh,
            "cache": self.quote_cache.summary("toss", MARKET_DATA_ACCOUNT_ID),
        }
        ontology_symbols = changed_symbols
        if self.event_publisher and saved:
            event = market_data_collected_event(result)
            if hasattr(self.event_publisher, "publish"):
                self.event_publisher.publish(event)
                if ontology_symbols:
                    self.event_publisher.publish(ontology_reasoning_requested_event(
                        event,
                        "market-data-update",
                        ontology_symbols,
                        changed_count=len(ontology_symbols),
                        observed_count=saved,
                        fact_types=["MarketQuote", "TechnicalIndicator"],
                        reason="시장 데이터 변경을 TypeDB ABox에 반영하고 RuleBox 추론을 갱신합니다. 알림은 중요 변경 게이트를 별도로 통과해야 합니다.",
                        materiality_assessments=[materiality_assessments[symbol] for symbol in changed_symbols if symbol in materiality_assessments],
                    ))
            else:
                self.event_publisher.handle(event)
                if ontology_symbols:
                    self.event_publisher.handle(ontology_reasoning_requested_event(
                        event,
                        "market-data-update",
                        ontology_symbols,
                        changed_count=len(ontology_symbols),
                        observed_count=saved,
                        fact_types=["MarketQuote", "TechnicalIndicator"],
                        reason="시장 데이터 변경을 TypeDB ABox에 반영하고 RuleBox 추론을 갱신합니다. 알림은 중요 변경 게이트를 별도로 통과해야 합니다.",
                        materiality_assessments=[materiality_assessments[symbol] for symbol in changed_symbols if symbol in materiality_assessments],
                    ))
        return result

    def status(self) -> Dict[str, object]:
        return {
            "enabled": self.enabled(),
            "markets": self.markets(),
            "priceBatchSize": self.price_batch_size(),
            "candleBatchSize": self.candle_batch_size(),
            "maxAgeMinutes": self.max_age_minutes(),
            "collectionScope": "account-focus",
            "cache": self.quote_cache.summary("toss", MARKET_DATA_ACCOUNT_ID),
            "symbolUniverse": self.symbol_service.summary(),
        }


class MarketDataCollectionScheduler:
    def __init__(self, runner: MarketDataCollectionRunner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(3 * 60, int(interval_seconds or 180))
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
