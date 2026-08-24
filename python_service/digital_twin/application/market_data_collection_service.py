import time
from typing import Callable, Dict, Iterable, List, Tuple

from ..domain.accounts import AccountConfig
from ..domain.data_freshness import age_minutes
from ..domain.events import market_data_collected_event
from ..domain.fact_changes import market_fact_change
from ..domain.instrument_profiles import market_signal_symbols
from ..domain.market_data import normalize_position, number, technical_indicators_from_candles
from ..domain.position_identity import position_with_symbol_identity
from ..domain.materiality import market_change_materiality
from ..domain.portfolio import Position, utc_now_iso
from ..domain.repositories import AccountRepository, MarketDataProvider, MarketDataProviderFactory, MarketQuoteRepository
from ..domain.symbol_universe import SUPPORTED_MARKETS, normalize_market


MARKET_DATA_ACCOUNT_ID = "__market_data__"


def collection_quote_quality(positions: Iterable[Position]) -> Dict[str, object]:
    """Summarize quote validity without confusing a successful poll with live data."""
    quality_counts: Dict[str, int] = {}
    session_counts: Dict[str, int] = {}
    for position in positions or []:
        quality = str(getattr(position, "data_quality", "") or "unknown").strip().lower() or "unknown"
        session = str(getattr(position, "market_session", "") or "unknown").strip().lower() or "unknown"
        quality_counts[quality] = quality_counts.get(quality, 0) + 1
        session_counts[session] = session_counts.get(session, 0) + 1
    values = set(quality_counts)
    if not values:
        data_quality = "unknown"
    elif len(values) == 1:
        data_quality = next(iter(values))
    else:
        data_quality = "mixed"
    return {
        "dataQuality": data_quality,
        "quoteQualityCounts": dict(sorted(quality_counts.items())),
        "marketSessionCounts": dict(sorted(session_counts.items())),
    }


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
        "previousClose": position.previous_close,
        "return1d": position.return_1d,
        "return3d": position.return_3d,
        "return5d": position.return_5d,
        "priceChangeSource": position.price_change_source,
        "priceChangeBasis": position.price_change_basis,
        "priceHistoryAdjustment": position.price_history_adjustment,
        "priceChangeUsable": position.price_change_usable,
        "quoteSource": position.quote_source,
        "quoteStatus": position.quote_status,
        "quoteMessage": position.quote_message,
        "dataQuality": position.data_quality or "actual",
        "updatedAt": position.updated_at or utc_now_iso(),
        # ``updatedAt`` is our collection clock.  These fields retain the
        # vendor observation clock and its validity state for ABox/AI use.
        "sourceAsOf": position.source_as_of or str(base.get("sourceAsOf") or ""),
        "sourceFetchedAt": position.source_fetched_at or str(base.get("sourceFetchedAt") or base.get("fetchedAt") or ""),
        "sourceTimestampState": position.source_timestamp_state or str(base.get("sourceTimestampState") or ""),
        "freshnessStatus": position.freshness_status or str(base.get("freshnessStatus") or ""),
        "freshnessReason": position.freshness_reason or str(base.get("freshnessReason") or ""),
        "freshnessAgeMinutes": (
            position.freshness_age_minutes
            if position.freshness_age_minutes is not None
            else base.get("freshnessAgeMinutes")
        ),
        "freshnessMaxAgeMinutes": (
            position.freshness_max_age_minutes
            if position.freshness_max_age_minutes is not None
            else base.get("freshnessMaxAgeMinutes") or base.get("maxAgeMinutes")
        ),
        "latencyStatus": position.latency_status or str(base.get("latencyStatus") or ""),
        "latencyReason": position.latency_reason or str(base.get("latencyReason") or ""),
        "marketSession": position.market_session or str(base.get("marketSession") or ""),
        "marketSessionLabel": position.market_session_label or str(base.get("marketSessionLabel") or ""),
        "transport": position.source_transport or str(base.get("transport") or ""),
        "realTime": bool(position.real_time or base.get("realTime")),
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
        time_series_store=None,
        health_service=None,
        decision_episode_store=None,
        external_signal_refresher: Callable[[Iterable[Position]], Dict[str, object]] = None,
    ):
        self.account_repository = account_repository
        self.symbol_service = symbol_service
        self.quote_cache = quote_cache
        self.settings = dict(settings or {})
        self.provider_factory = provider_factory
        self.event_publisher = event_publisher
        self.sleep_fn = sleep_fn
        self.time_series_store = time_series_store
        self.health_service = health_service
        self.decision_episode_store = decision_episode_store
        self.external_signal_refresher = external_signal_refresher

    def attach_pipeline_health(self, result: Dict[str, object]) -> Dict[str, object]:
        if not self.health_service or not hasattr(self.health_service, "record_market_data_collection"):
            return result
        try:
            health, event = self.health_service.record_market_data_collection(result)
            result["pipelineHealth"] = health.to_dict() if hasattr(health, "to_dict") else dict(health or {})
            if event and self.event_publisher:
                if hasattr(self.event_publisher, "publish"):
                    self.event_publisher.publish(event)
                else:
                    self.event_publisher.handle(event)
        except Exception as error:  # noqa: BLE001 - health telemetry must not block collection.
            result["pipelineHealth"] = {"state": "unknown", "reason": str(error)[:180]}
        return result

    def publish_collected_event(self, result: Dict[str, object]) -> bool:
        """Publish market facts without starting a parallel TypeDB turn.

        The market collector is the fast source-of-truth writer.  Its facts
        are picked up by the next committed monitor snapshot, which is the
        only replayable boundary for the asynchronous TypeDB worker.  Keeping
        this source event separate prevents a fast polling loop from filling
        the reasoning mailbox with raw ticks that the snapshot will supersede.
        """

        if not self.event_publisher or not int(result.get("savedCount") or 0):
            return False
        event = market_data_collected_event(result)
        if hasattr(self.event_publisher, "publish"):
            self.event_publisher.publish(event)
        else:
            self.event_publisher.handle(event)
        return True

    def enabled(self) -> bool:
        return truthy(self.settings.get("marketDataCollectionEnabled"), True)

    def markets(self) -> List[str]:
        return configured_markets(self.settings.get("marketDataCollectionMarkets"))

    def price_batch_size(self) -> int:
        return int_setting(self.settings, "marketDataPriceBatchSize", 200, 1, 200)

    def candle_batch_size(self) -> int:
        return int_setting(self.settings, "marketDataCandleBatchSize", 25, 0, self.price_batch_size())

    def market_signal_collection_enabled(self) -> bool:
        return truthy(self.settings.get("marketSignalDataCollectionEnabled"), True)

    def market_signal_batch_size(self) -> int:
        return int_setting(self.settings, "marketSignalDataBatchSize", 12, 0, self.price_batch_size())

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
            try:
                return {"status": "disabled", "summary": self.symbol_service.summary()}
            except Exception as error:  # noqa: BLE001 - disabled refresh must not block account-focus collection.
                return {"status": "disabled", "error": str(error)}
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

    def market_signal_targets(self, excluded_symbols: Iterable[str] = None) -> List[Tuple[Position, Dict[str, object]]]:
        batch_size = self.market_signal_batch_size()
        if not self.market_signal_collection_enabled() or batch_size <= 0:
            return []
        excluded = {str(symbol or "").upper() for symbol in (excluded_symbols or []) if str(symbol or "").strip()}
        signal_symbols = market_signal_symbols(self.settings)
        if not signal_symbols:
            return []
        selected_markets = set(self.markets())
        fallback_rows: Dict[str, Dict[str, object]] = {}
        if not hasattr(self.symbol_service, "enrich"):
            selection_limit = max(self.price_batch_size(), batch_size * 5)
            try:
                fallback_rows = {
                    str(row.get("symbol") or "").upper(): dict(row)
                    for row in self.quote_cache.stale_universe_symbols(
                        "toss",
                        MARKET_DATA_ACCOUNT_ID,
                        self.markets(),
                        limit=selection_limit,
                        max_age_minutes=self.max_age_minutes(),
                    )
                }
            except Exception:
                fallback_rows = {}
        targets: List[Tuple[Position, Dict[str, object]]] = []
        for symbol in signal_symbols:
            if not symbol or symbol in excluded:
                continue
            try:
                cached = self.quote_cache.load("toss", MARKET_DATA_ACCOUNT_ID, symbol)
            except Exception:
                cached = {}
            cached_age = age_minutes(str(cached.get("updatedAt") or cached.get("updated_at") or "")) if isinstance(cached, dict) else None
            if cached and cached_age is not None and cached_age <= self.max_age_minutes():
                continue
            row = {}
            if hasattr(self.symbol_service, "enrich"):
                try:
                    row = self.symbol_service.enrich(symbol)
                except Exception:
                    row = {}
            if not row:
                row = fallback_rows.get(symbol) or {}
            if not row:
                continue
            if normalize_market(str(row.get("market") or "")) not in selected_markets:
                continue
            position = self.base_position(row)
            position.source = "market-signal"
            targets.append((position, dict(row)))
            if len(targets) >= batch_size:
                break
        return targets

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
            identity = {}
            if hasattr(self.symbol_service, "enrich"):
                try:
                    identity = self.symbol_service.enrich(symbol) or {}
                except Exception:
                    identity = {}
            focused.append(position_with_symbol_identity(position, identity))
        return mode, status, token, focused

    def outcome_observation_targets(
        self,
        account_entries: Iterable[Dict[str, object]],
        excluded_symbols: Iterable[str] = None,
    ) -> List[Tuple[Position, Dict[str, object]]]:
        """Collect quotes for due decision outcomes without turning them into alerts.

        A sold or removed watchlist symbol still needs a quote at the decision
        horizon. These are bounded by the outcome store and are deliberately
        tagged as background collection so only the feedback loop consumes
        them.
        """
        if not self.decision_episode_store or not hasattr(self.decision_episode_store, "pending_outcome_targets"):
            return []
        excluded = {str(symbol or "").upper().strip() for symbol in excluded_symbols or [] if str(symbol or "").strip()}
        selected_markets = set(self.markets())
        result: List[Tuple[Position, Dict[str, object]]] = []
        seen = set(excluded)
        limit = max(1, min(self.price_batch_size(), int_setting(self.settings, "investmentBrainOutcomeEpisodeBatchSize", 200, 10, 1000)))
        for entry in account_entries or []:
            account = entry.get("account")
            account_id = str(getattr(account, "account_id", "") or "")
            if not account_id:
                continue
            try:
                pending = self.decision_episode_store.pending_outcome_targets(account_id, utc_now_iso(), limit=limit)
            except Exception:
                continue
            for target in pending:
                symbol = str(target.get("symbol") or "").upper().strip()
                if symbol and symbol not in seen:
                    cached = self.quote_cache.load("toss", MARKET_DATA_ACCOUNT_ID, symbol)
                    enriched = {}
                    if hasattr(self.symbol_service, "enrich"):
                        try:
                            enriched = self.symbol_service.enrich(symbol) or {}
                        except Exception:
                            enriched = {}
                    base = {
                        **dict(cached or {}),
                        **dict(target or {}),
                        **dict(enriched or {}),
                        "symbol": symbol,
                        "name": str((enriched or {}).get("name") or target.get("subjectName") or (cached or {}).get("name") or symbol),
                        "market": str((enriched or {}).get("market") or target.get("market") or (cached or {}).get("market") or ""),
                        "currency": str((enriched or {}).get("currency") or target.get("currency") or (cached or {}).get("currency") or ""),
                    }
                    if normalize_market(str(base.get("market") or "")) in selected_markets:
                        position = self.base_position(base)
                        position.source = "decision-outcome"
                        result.append((position, base))
                        seen.add(symbol)
                benchmark_symbol = str(target.get("benchmarkSymbol") or "").upper().strip()
                if benchmark_symbol and benchmark_symbol not in seen and len(result) < limit:
                    benchmark_identity = {}
                    if hasattr(self.symbol_service, "enrich"):
                        try:
                            benchmark_identity = self.symbol_service.enrich(benchmark_symbol) or {}
                        except Exception:
                            benchmark_identity = {}
                    benchmark_base = {
                        **dict(self.quote_cache.load("toss", MARKET_DATA_ACCOUNT_ID, benchmark_symbol) or {}),
                        **dict(benchmark_identity or {}),
                        "symbol": benchmark_symbol,
                        "name": str((benchmark_identity or {}).get("name") or benchmark_symbol),
                        "market": str((benchmark_identity or {}).get("market") or target.get("market") or ""),
                        "currency": str((benchmark_identity or {}).get("currency") or target.get("currency") or ""),
                        "outcomeBenchmarkFor": symbol,
                    }
                    if normalize_market(str(benchmark_base.get("market") or "")) in selected_markets:
                        benchmark_position = self.base_position(benchmark_base)
                        benchmark_position.source = "decision-outcome-benchmark"
                        result.append((benchmark_position, benchmark_base))
                        seen.add(benchmark_symbol)
                if len(result) >= limit:
                    return result
        return result

    def merge_focus_market_data(
        self,
        provider: MarketDataProvider,
        token: str,
        focused_by_account: List[Dict[str, object]],
        market_signal_targets: List[Tuple[Position, Dict[str, object]]] = None,
    ) -> Tuple[List[Dict[str, object]], List[Tuple[Position, Dict[str, object]]], Dict[str, object]]:
        market_signal_targets = list(market_signal_targets or [])
        symbol_order: List[str] = []
        for entry in focused_by_account:
            for position in entry.get("positions") or []:
                symbol = str(position.symbol or "").upper()
                if symbol and symbol not in symbol_order:
                    symbol_order.append(symbol)
        for position, _base in market_signal_targets:
            symbol = str(position.symbol or "").upper()
            if symbol and symbol not in symbol_order:
                symbol_order.append(symbol)
        if not symbol_order:
            return focused_by_account, [], {"symbols": [], "priceCount": 0, "candleCount": 0}
        if not token:
            token = provider.fetch_access_token()
        try:
            prices, token = provider.fetch_prices(token, symbol_order)
        except Exception:
            prices = {}
        indicators, candles_by_symbol, token = self.collect_candles(provider, token, symbol_order)
        time_series = self.record_daily_history(
            candles_by_symbol,
            focused_by_account,
            market_signal_targets,
        )
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
        merged_market_signals: List[Tuple[Position, Dict[str, object]]] = []
        for position, base in market_signal_targets:
            symbol = str(position.symbol or "").upper()
            cached = self.quote_cache.load("toss", MARKET_DATA_ACCOUNT_ID, symbol)
            merged_market_signals.append((
                provider.merge_market_data(
                    position,
                    prices.get(symbol) or {},
                    indicators.get(symbol) or {},
                    cached,
                    quote_live=bool(prices.get(symbol)),
                    indicators_live=bool(indicators.get(symbol)),
                ),
                base,
            ))
        return merged_entries, merged_market_signals, {
            "symbols": symbol_order,
            "priceCount": len(prices),
            "candleCount": len(indicators),
            "dailyHistorySavedCount": int(time_series.get("savedCount") or 0),
            "dailyHistorySymbolCount": int(time_series.get("symbolCount") or 0),
            "timeSeries": time_series,
        }

    def collect_candles(self, provider: MarketDataProvider, token: str, symbols: Iterable[str]):
        result: Dict[str, Dict[str, object]] = {}
        candles_by_symbol: Dict[str, List[Dict[str, object]]] = {}
        for index, symbol in enumerate(symbols):
            try:
                if index:
                    self.sleep_fn(0.22)
                candles, token = provider.fetch_daily_candles(token, symbol)
                if candles:
                    candles_by_symbol[str(symbol or "").upper()] = list(candles)
                indicators = technical_indicators_from_candles(
                    candles,
                    adjustment_status="provider-adjusted",
                )
                if indicators:
                    result[symbol] = indicators
            except Exception:
                continue
        return result, candles_by_symbol, token

    def record_daily_history(
        self,
        candles_by_symbol: Dict[str, List[Dict[str, object]]],
        focused_by_account: List[Dict[str, object]],
        market_signal_targets: List[Tuple[Position, Dict[str, object]]],
    ) -> Dict[str, object]:
        if not self.time_series_store or not candles_by_symbol:
            return {"enabled": bool(self.time_series_store), "savedCount": 0, "symbolCount": 0}
        metadata: Dict[str, Dict[str, object]] = {}
        for entry in focused_by_account or []:
            for position in entry.get("positions") or []:
                symbol = str(position.symbol or "").upper().strip()
                if symbol:
                    metadata[symbol] = {
                        "name": position.name,
                        "market": position.market,
                        "currency": position.currency,
                    }
        for position, base in market_signal_targets or []:
            symbol = str(position.symbol or "").upper().strip()
            if symbol:
                metadata.setdefault(symbol, {
                    "name": position.name or str(base.get("name") or symbol),
                    "market": position.market or str(base.get("market") or ""),
                    "currency": position.currency or str(base.get("currency") or ""),
                })
        try:
            return self.time_series_store.record_daily_candles(candles_by_symbol, metadata)
        except Exception as error:  # noqa: BLE001 - quote cache collection must survive history persistence failure.
            return {
                "enabled": True,
                "savedCount": 0,
                "symbolCount": 0,
                "status": "error",
                "reason": str(error)[:180],
            }

    def record_outcome_time_series(self, targets: Iterable[Tuple[Position, Dict[str, object]]]) -> Dict[str, object]:
        if not self.time_series_store or not hasattr(self.time_series_store, "record_positions"):
            return {"enabled": bool(self.time_series_store), "savedCount": 0, "symbolCount": 0}
        positions = [
            position for position, _base in targets or []
            if str(getattr(position, "source", "") or "") == "decision-outcome"
        ]
        if not positions:
            return {"enabled": True, "savedCount": 0, "symbolCount": 0}
        try:
            return self.time_series_store.record_positions(
                MARKET_DATA_ACCOUNT_ID,
                positions,
                utc_now_iso(),
                provider="market-data-collector:decision-outcome",
            )
        except Exception as error:  # noqa: BLE001 - quote cache collection must survive feedback history failure.
            return {
                "enabled": True,
                "savedCount": 0,
                "symbolCount": 0,
                "status": "error",
                "reason": str(error)[:180],
            }

    def refresh_external_signal_cache(self, positions: Iterable[Position]) -> Dict[str, object]:
        """Refresh slower vendor data outside the realtime alert path."""
        if not self.external_signal_refresher:
            return {"status": "disabled", "reason": "external signal refresher is not configured"}
        if not truthy(self.settings.get("externalSignalCollectionEnabled"), True):
            return {"status": "disabled", "reason": "external signal collection is disabled"}
        selected = [
            position for position in positions or []
            if position and not position.is_cash() and str(position.symbol or "").strip()
        ]
        if not selected:
            return {"status": "skipped", "reason": "no account symbols"}
        try:
            signals = self.external_signal_refresher(selected) or {}
        except Exception as error:  # noqa: BLE001 - slow external data must not invalidate a quote collection.
            return {"status": "error", "reason": str(error)[:180], "symbolCount": len(selected)}
        statuses = signals.get("statuses") if isinstance(signals.get("statuses"), list) else []
        failures = [row for row in statuses if isinstance(row, dict) and row.get("ok") is False]
        deferred = [row for row in statuses if isinstance(row, dict) and row.get("deferred")]
        return {
            "status": "ok",
            "symbolCount": len(selected),
            "fetchedAt": str(signals.get("fetchedAt") or ""),
            "providerStatusCount": len(statuses),
            "providerFailureCount": len(failures),
            "providerDeferredCount": len(deferred),
        }

    def run_once(self, force: bool = False) -> Dict[str, object]:
        if not self.enabled() and not force:
            return self.attach_pipeline_health({"status": "disabled", "savedCount": 0})
        universe_refresh = self.refresh_symbol_universe_if_needed()
        accounts = self.select_accounts()
        if not accounts:
            return self.attach_pipeline_health({
                "status": "missingCredentials",
                "message": "Toss credentials가 설정된 계정이 없습니다.",
                "universeRefresh": universe_refresh,
                "savedCount": 0,
            })
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
            return self.attach_pipeline_health({
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
            })
        focused_symbols = [
            str(position.symbol or "").upper()
            for entry in focused_by_account
            for position in (entry.get("positions") or [])
            if str(position.symbol or "").strip()
        ]
        outcome_targets = self.outcome_observation_targets(focused_by_account, focused_symbols)
        auxiliary_exclusions = focused_symbols + [str(position.symbol or "").upper() for position, _base in outcome_targets]
        market_signal_targets = self.market_signal_targets(auxiliary_exclusions)
        auxiliary_targets = list(market_signal_targets) + list(outcome_targets)
        focused_by_account, auxiliary_targets, merge_summary = self.merge_focus_market_data(
            merge_provider,
            merge_token,
            focused_by_account,
            auxiliary_targets,
        )
        quote_quality = collection_quote_quality(
            [
                position
                for entry in focused_by_account
                for position in (entry.get("positions") or [])
            ] + [position for position, _base in auxiliary_targets]
        )
        outcome_time_series = self.record_outcome_time_series(auxiliary_targets)
        if not merge_summary.get("symbols"):
            return self.attach_pipeline_health({
                "status": "fresh",
                "markets": markets,
                "collectionScope": "account-focus",
                "accountCount": len(accounts),
                "liveAccountCount": len(focused_by_account),
                "unavailableAccounts": unavailable_accounts,
                "universeRefresh": universe_refresh,
                "savedCount": 0,
                "cache": self.quote_cache.summary("toss", MARKET_DATA_ACCOUNT_ID),
            })
        symbols = list(merge_summary.get("symbols") or [])
        saved = 0
        account_saved = 0
        changed = 0
        changed_symbols: List[str] = []
        changed_fields_by_symbol: Dict[str, List[str]] = {}
        fact_revisions_by_symbol: Dict[str, str] = {}
        material_symbols: List[str] = []
        bootstrap_symbols: List[str] = []
        materiality_assessments: Dict[str, Dict[str, object]] = {}
        global_saved_symbols = set()
        account_symbol_counts: Dict[str, int] = {}
        market_signal_saved = 0
        market_signal_symbols: List[str] = []
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
                    fact_revisions_by_symbol[symbol] = str(change.get("revisionId") or "")
                    if str(change.get("reason") or "") == "new-market-fact":
                        bootstrap_symbols.append(symbol)
                    assessment = market_change_materiality(symbol, cached, payload, change, self.settings)
                    materiality_assessments[symbol] = assessment.to_dict()
                    if assessment.passed:
                        material_symbols.append(symbol)
            account_symbol_counts[account.account_id] = account_count
        outcome_saved = 0
        outcome_symbols: List[str] = []
        for position, base in auxiliary_targets:
            symbol = str(position.symbol or "").upper()
            if not symbol or symbol in global_saved_symbols:
                continue
            is_outcome_target = str(getattr(position, "source", "") or "") == "decision-outcome"
            payload = position_payload(position, base, "decision-outcome" if is_outcome_target else "market-signal")
            payload["collectionTarget"] = "decision-outcome" if is_outcome_target else "market-proxy"
            if not number(payload.get("currentPrice")) and not any(number(payload.get(key)) for key in ["ma20", "ma60", "volume"]):
                continue
            cached = self.quote_cache.load("toss", MARKET_DATA_ACCOUNT_ID, symbol)
            change = market_fact_change(cached, payload)
            self.quote_cache.save("toss", MARKET_DATA_ACCOUNT_ID, symbol, payload)
            global_saved_symbols.add(symbol)
            if is_outcome_target:
                outcome_saved += 1
                outcome_symbols.append(symbol)
            else:
                market_signal_saved += 1
                market_signal_symbols.append(symbol)
            saved += 1
            if change.get("changed"):
                changed += 1
                changed_symbols.append(symbol)
                changed_fields_by_symbol[symbol] = list(change.get("fields") or [])
                fact_revisions_by_symbol[symbol] = str(change.get("revisionId") or "")
                if str(change.get("reason") or "") == "new-market-fact":
                    bootstrap_symbols.append(symbol)
                assessment = market_change_materiality(symbol, cached, payload, change, self.settings)
                materiality_assessments[symbol] = assessment.to_dict()
                if assessment.passed:
                    material_symbols.append(symbol)
        material_symbol_set = set(material_symbols)
        bootstrap_symbol_set = set(bootstrap_symbols)
        result = {
            "status": "ok",
            "provider": "toss",
            "markets": markets,
            "collectionScope": (
                "account-focus+market-signals+decision-outcomes" if outcome_targets
                else ("account-focus+market-signals" if auxiliary_targets else "account-focus")
            ),
            "accountCount": len(accounts),
            "liveAccountCount": len(focused_by_account),
            "unavailableAccounts": unavailable_accounts,
            "accountSymbolCounts": account_symbol_counts,
            "symbols": symbols,
            "selectedCount": len(symbols),
            "accountSelectedCount": sum(account_symbol_counts.values()),
            "marketSignalSelectedCount": len([item for item in auxiliary_targets if str(getattr(item[0], "source", "") or "") != "decision-outcome"]),
            "marketSignalSavedCount": market_signal_saved,
            "marketSignalSymbols": market_signal_symbols,
            "decisionOutcomeSelectedCount": len(outcome_targets),
            "decisionOutcomeSavedCount": outcome_saved,
            "decisionOutcomeSymbols": outcome_symbols,
            "decisionOutcomeTimeSeriesSavedCount": int(outcome_time_series.get("savedCount") or 0),
            "priceCount": int(merge_summary.get("priceCount") or 0),
            "candleCount": int(merge_summary.get("candleCount") or 0),
            "dailyHistorySavedCount": int(merge_summary.get("dailyHistorySavedCount") or 0),
            "dailyHistorySymbolCount": int(merge_summary.get("dailyHistorySymbolCount") or 0),
            "timeSeries": dict(merge_summary.get("timeSeries") or {}),
            "savedCount": saved,
            "accountSavedCount": account_saved,
            "changedCount": changed,
            "changedSymbols": changed_symbols,
            "changedFieldsBySymbol": changed_fields_by_symbol,
            "factRevisionsBySymbol": fact_revisions_by_symbol,
            "materialChangedCount": len(material_symbols),
            "materialChangedSymbols": material_symbols,
            "bootstrapReasoningSymbols": bootstrap_symbols,
            "immaterialChangedSymbolCount": len([symbol for symbol in changed_symbols if symbol not in material_symbol_set and symbol not in bootstrap_symbol_set]),
            "materialityAssessments": materiality_assessments,
            "dataQuality": quote_quality["dataQuality"],
            "quoteQualityCounts": quote_quality["quoteQualityCounts"],
            "marketSessionCounts": quote_quality["marketSessionCounts"],
            "universeRefresh": universe_refresh,
            "cache": self.quote_cache.summary("toss", MARKET_DATA_ACCOUNT_ID),
        }
        self.attach_pipeline_health(result)
        # Market proxy symbols keep macro and sector context fresh, but they are
        # not investment subjects by themselves. Only account holdings and
        # watchlist names may enqueue an investment-reasoning cycle; otherwise
        # proxy refreshes can make a live holding wait behind background ticks.
        #
        # ``market_change_materiality`` remains useful operational provenance
        # (priority, diagnostics and later policy review), but it must not
        # decide whether a real market fact reaches the investment ABox. A
        # small price move can satisfy a TypeDB rule only in the context of an
        # existing hypothesis, position or macro fact. The next committed
        # monitor snapshot is the sole replayable TypeDB boundary, so the
        # fast collector never opens a second raw-market queue branch.
        focus_symbols = {str(symbol or "").upper().strip() for symbol in focused_symbols if str(symbol or "").strip()}
        fact_revision_symbol_set = set(changed_symbols)
        ontology_symbols = [symbol for symbol in changed_symbols if symbol in focus_symbols]
        result["investmentReasoningSymbols"] = ontology_symbols
        result["factRevisionReasoningSymbols"] = ontology_symbols
        result["investmentReasoningScheduling"] = "verified-monitor-snapshot-barrier"
        result["reasoningDispatch"] = "next-verified-monitor-snapshot"
        result["backgroundMaterialSymbolCount"] = len([symbol for symbol in material_symbols if symbol not in focus_symbols])
        result["backgroundBootstrapSymbolCount"] = len([symbol for symbol in bootstrap_symbols if symbol not in focus_symbols])
        result["backgroundFactRevisionSymbolCount"] = len([
            symbol for symbol in fact_revision_symbol_set if symbol not in focus_symbols
        ])
        self.publish_collected_event(result)
        # Quote changes have already entered the event stream. A slow
        # fundamentals/news refresh is deliberately best-effort and cannot
        # delay the monitor that turns those quote changes into alerts.
        result["externalSignalRefresh"] = self.refresh_external_signal_cache(
            [
                position
                for entry in focused_by_account
                for position in (entry.get("positions") or [])
            ]
        )
        return result

    def status(self) -> Dict[str, object]:
        result = {
            "enabled": self.enabled(),
            "markets": self.markets(),
            "priceBatchSize": self.price_batch_size(),
            "candleBatchSize": self.candle_batch_size(),
            "marketSignalCollectionEnabled": self.market_signal_collection_enabled(),
            "marketSignalBatchSize": self.market_signal_batch_size(),
            "maxAgeMinutes": self.max_age_minutes(),
            "collectionScope": "account-focus+market-signals+decision-outcomes",
            "cache": self.quote_cache.summary("toss", MARKET_DATA_ACCOUNT_ID),
            "symbolUniverse": self.symbol_service.summary(),
        }
        if self.time_series_store:
            try:
                result["timeSeries"] = self.time_series_store.summary()
            except Exception as error:  # noqa: BLE001 - collection status should still report the cache state.
                result["timeSeries"] = {"enabled": True, "status": "error", "reason": str(error)[:180]}
        return result
