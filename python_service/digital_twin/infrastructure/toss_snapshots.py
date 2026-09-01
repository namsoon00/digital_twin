import json
import hashlib
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from threading import Lock
from typing import Dict, List, Optional, Tuple

from ..domain.accounts import AccountConfig
from ..domain.data_freshness import combine_quality, freshness_record, int_setting, parse_datetime
from ..domain.instrument_profiles import market_signal_symbols
from ..domain.investor_flow_psychology import INVESTOR_PARTY_FIELDS, investor_flow_observed_fields
from ..domain.market_data import (
    derived_price_change_facts,
    known_stock,
    normalize_position,
    number,
    pct_distance,
    technical_indicators_from_candles,
)
from ..domain.market_hours import evaluate_market_hours
from ..domain.market_time_series import market_session_date
from ..domain.message_types import INVESTMENT_INSIGHT
from ..domain.position_identity import position_with_symbol_identity
from ..domain.portfolio import AccountSnapshot, Position, utc_now_iso
from ..domain.portfolio_calculations import (
    LIVE_MARKET_FX_SOURCE_TYPE,
    apply_position_base_currency_values,
    fx_rates_with_external_signals,
    portfolio_summary,
    runtime_fx_currencies_from_external_signals,
)
from ..domain.portfolio_valuation import BROKER_NET_BASIS, MARK_TO_MARKET_BASIS, normalized_valuation_basis
from ..domain.strategy import decisions_for_positions
from ..domain.volume_time_adjustment import trading_value_snapshot
from .external_signals import ExternalSignalProvider
from .external_signal_utils import guarded_external_call, root_api_error
from .kis_market_signals import KISMarketSignalProvider
from .operational_store import market_quote_cache, symbol_universe_store
from .settings import currency_rates, runtime_settings


MARKET_DATA_ACCOUNT_ID = "__market_data__"
TOSS_TOKEN_CACHE: Dict[str, Dict[str, object]] = {}
TOSS_TOKEN_CACHE_LOCK = Lock()


def market_proxy_quote_context(
    settings: Dict[str, str],
    quote_cache,
    limit: int = 80,
    external_signals: Dict[str, object] = None,
) -> Dict[str, Dict[str, object]]:
    symbols = market_signal_symbols(settings or {})[:max(1, int(limit or 80))]
    if quote_cache is None:
        rows: Dict[str, Dict[str, object]] = {}
    else:
        rows = {}
        for symbol in symbols:
            try:
                payload = quote_cache.load("toss", MARKET_DATA_ACCOUNT_ID, symbol)
            except Exception:
                payload = {}
            if not isinstance(payload, dict) or not payload:
                continue
            if not any(number(payload.get(key)) for key in ["currentPrice", "ma20", "ma60", "volume", "changeRate"]):
                continue
            rows[symbol] = {
                key: payload.get(key)
                for key in [
                    "symbol",
                    "name",
                    "market",
                    "currency",
                    "assetType",
                    "sector",
                    "currentPrice",
                    "changeRate",
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
                    "quoteSource",
                    "quoteStatus",
                    "dataQuality",
                    "updatedAt",
                    "sourceAsOf",
                    "sourceFetchedAt",
                    "sourceTimestampState",
                    "freshnessStatus",
                    "freshnessReason",
                    "freshnessAgeMinutes",
                    "freshnessMaxAgeMinutes",
                    "latencyStatus",
                    "latencyReason",
                    "marketSession",
                    "marketSessionLabel",
                    "transport",
                    "realTime",
                    "indicatorAsOf",
                    "indicatorFetchedAt",
                    "collectionPurpose",
                    "collectionTarget",
                ]
                if payload.get(key) not in (None, "")
            }
    rows.update(crypto_market_proxy_quote_context(symbols, external_signals or {}))
    for payload in rows.values():
        source_as_of = payload.get("sourceAsOf") or payload.get("updatedAt") or ""
        source_fetched_at = payload.get("sourceFetchedAt") or payload.get("updatedAt") or ""
        freshness = freshness_record(
            payload.get("quoteSource") or "market-proxy-quote",
            INVESTMENT_INSIGHT,
            settings=settings,
            source_fetched_at=source_fetched_at,
            source_as_of=source_as_of,
            data_quality=payload.get("dataQuality") or "",
            max_age_minutes=int_setting(settings or {}, "dataFreshnessExternalMaxAgeMinutes", 10),
            require_source_as_of=True,
        )
        market_closed_reference = str(payload.get("marketSession") or "").strip().lower() in {
            "closed",
            "closed_exception",
        }
        payload.update({
            "sourceAsOf": freshness.get("sourceAsOf") or "",
            "sourceFetchedAt": freshness.get("sourceFetchedAt") or "",
            "freshnessStatus": "last-close" if market_closed_reference else (freshness.get("status") or "unknown"),
            "freshnessReason": (
                payload.get("freshnessReason")
                if market_closed_reference
                else (freshness.get("reason") or "")
            ),
            "freshnessAgeMinutes": freshness.get("ageMinutes"),
            "maxAgeMinutes": freshness.get("maxAgeMinutes"),
            "sourceTimestampPresent": bool(freshness.get("sourceTimestampPresent")),
            "judgementEvidenceUsable": (
                not market_closed_reference
                and str(freshness.get("status") or "") == "fresh"
            ),
        })
    return rows


def crypto_market_proxy_quote_context(symbols: List[str], external_signals: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    allowed = {str(symbol or "").upper().strip() for symbol in symbols or [] if str(symbol or "").strip()}
    rows: Dict[str, Dict[str, object]] = {}
    markets = external_signals.get("cryptoMarkets") if isinstance(external_signals.get("cryptoMarkets"), dict) else {}
    for coin_id, payload in markets.items():
        if not isinstance(payload, dict):
            continue
        symbol = str(payload.get("symbol") or "").upper().strip()
        if not symbol:
            symbol = {"bitcoin": "BTC", "ethereum": "ETH"}.get(str(coin_id or "").lower().strip(), "")
        if not symbol or symbol not in allowed:
            continue
        price = number(payload.get("price") or payload.get("currentPrice"))
        if not price:
            continue
        meta = known_stock(symbol)
        rows[symbol] = {
            "symbol": symbol,
            "name": payload.get("name") or meta.get("name") or symbol,
            "market": "CRYPTO",
            "currency": "USD",
            "assetType": "CRYPTO",
            "sector": meta.get("sector") or "디지털자산",
            "currentPrice": price,
            "changeRate": number(payload.get("change24h")),
            "volume": number(payload.get("volume24h")),
            "tradingValue": number(payload.get("volume24h")),
            "quoteSource": "CoinGecko coins/markets",
            "quoteStatus": "ok",
            "dataQuality": "actual",
            "updatedAt": payload.get("lastUpdated") or external_signals.get("fetchedAt") or "",
            "sourceAsOf": payload.get("lastUpdated") or external_signals.get("fetchedAt") or "",
            "sourceFetchedAt": external_signals.get("fetchedAt") or payload.get("lastUpdated") or "",
            "collectionPurpose": "market-signal",
            "collectionTarget": "market-proxy",
        }
    return rows


class TossAPIError(RuntimeError):
    def __init__(self, stage: str, error: Exception):
        self.stage = str(stage or "")
        self.original_error = root_api_error(error)
        self.http_status = int(getattr(error, "code", 0) or 0)
        if not self.http_status:
            self.http_status = int(getattr(self.original_error, "code", 0) or 0)
        super().__init__("Toss " + self.stage + " 단계 실패 · " + http_error_text(self.original_error))


def http_json(method: str, url: str, headers: Dict[str, str], body: bytes = None, timeout: int = 12) -> Dict[str, object]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def http_error_text(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        reason = str(error.reason or "").strip()
        return "HTTP " + str(error.code) + (" " + reason if reason else "")
    if isinstance(error, urllib.error.URLError):
        return "URL error " + str(error.reason or error)[:120]
    return str(error or type(error).__name__)[:120]


def retryable_http_error(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return int(error.code or 0) in {408, 409, 425, 429, 500, 502, 503, 504}
    return isinstance(error, (urllib.error.URLError, TimeoutError, OSError))


def toss_json(
    stage: str,
    method: str,
    url: str,
    headers: Dict[str, str],
    body: bytes = None,
    timeout: int = 12,
    attempts: int = 2,
    settings: Dict[str, str] = None,
    guard_state: Dict[str, object] = None,
) -> Dict[str, object]:
    try:
        return guarded_external_call(
            settings or runtime_settings(),
            "Toss",
            stage,
            lambda: http_json(method, url, headers, body=body, timeout=timeout),
            state=guard_state,
            sleep=time.sleep,
            attempts=max(1, attempts),
            rate_limit_seconds=0,
            retry_delay_seconds=0.35,
        )
    except Exception as error:  # noqa: BLE001 - adapter normalizes vendor failures.
        raise TossAPIError(stage, error) from error


def form_body(payload: Dict[str, str]) -> bytes:
    return urllib.parse.urlencode(payload).encode("utf-8")


def normalize_accounts(payload: Dict[str, object]) -> List[Dict[str, object]]:
    data = payload.get("data") or payload.get("result") or payload
    accounts = data.get("accounts") if isinstance(data, dict) else data
    if isinstance(accounts, list):
        return [item for item in accounts if isinstance(item, dict)]
    return []


def account_identifiers(account: Dict[str, object]) -> List[str]:
    keys = [
        "accountSeq",
        "account_seq",
        "accountId",
        "account_id",
        "id",
        "accountNo",
        "accountNumber",
        "account_number",
    ]
    values = []
    for key in keys:
        value = str(account.get(key) or "").strip()
        if value:
            values.append(value)
    return values


def select_account(accounts: List[Dict[str, object]], configured_seq: str = "") -> Dict[str, object]:
    requested = str(configured_seq or "").strip()
    if requested:
        for account in accounts:
            if requested in account_identifiers(account):
                return account
    return accounts[0] if accounts else {}


def account_cash_amount(account: Dict[str, object]) -> float:
    keys = [
        "orderableAmount",
        "orderable_amount",
        "orderAvailableAmount",
        "availableOrderAmount",
        "availableAmount",
        "available_amount",
        "availableCash",
        "cashAvailable",
        "cashBalance",
        "cash_balance",
        "withdrawableAmount",
        "withdrawable_amount",
        "depositAmount",
        "deposit",
        "balance",
        "cash",
        "주문가능금액",
        "주문가능",
        "출금가능금액",
        "현금",
    ]
    for key in keys:
        if key in account and account.get(key) not in (None, ""):
            amount = number(account.get(key))
            if amount:
                return amount
    balances = account.get("balances") or account.get("cashBalances") or account.get("cash_balances")
    if isinstance(balances, list):
        for item in balances:
            if isinstance(item, dict):
                amount = account_cash_amount(item)
                if amount:
                    return amount
    return 0.0


def currency_rates_from_external_signals(
    settings: Dict[str, str] = None,
    external_signals: Dict[str, object] = None,
) -> Dict[str, float]:
    return fx_rates_with_external_signals(currency_rates(settings or runtime_settings()), external_signals)


def normalize_holdings(payload: Dict[str, object]) -> List[Dict[str, object]]:
    data = payload.get("data") or payload.get("result") or payload
    overview = data.get("overview") or data.get("holdings") or data if isinstance(data, dict) else data
    items = overview.get("items") or overview.get("holdings") or overview.get("positions") if isinstance(overview, dict) else overview
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def normalize_holdings_overview(payload: Dict[str, object]) -> Dict[str, object]:
    data = payload.get("data") or payload.get("result") or payload
    if not isinstance(data, dict):
        return {}
    overview = data.get("overview") if isinstance(data.get("overview"), dict) else data
    return {
        key: value
        for key, value in overview.items()
        if key not in {"items", "holdings", "positions"}
    }


def normalize_candles(payload: Dict[str, object]) -> List[Dict[str, object]]:
    data = payload.get("data") or payload.get("result") or payload
    candles = data.get("candles") if isinstance(data, dict) else data
    if isinstance(candles, list):
        return [item for item in candles if isinstance(item, dict)]
    return []


def normalize_price_items(payload: Dict[str, object]) -> List[Dict[str, object]]:
    data = payload.get("data") or payload.get("result") or payload
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ["prices", "items", "quotes", "result"]:
        items = data.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def first_present(item: Dict[str, object], keys: List[str]):
    for key in keys:
        if key in item and item.get(key) not in (None, ""):
            return item.get(key)
    return None


def optional_rate(item: Dict[str, object]):
    value = first_present(item, ["changeRate", "priceChangeRate", "changePercent", "changePct", "rate"])
    return number(value) if value not in (None, "") else None


def price_symbol(item: Dict[str, object]) -> str:
    return str(item.get("symbol") or item.get("stockCode") or item.get("code") or "").upper().strip()


def normalize_price_payload(item: Dict[str, object], now=None) -> Dict[str, object]:
    symbol = price_symbol(item)
    info = known_stock(symbol)
    market = str(item.get("marketCountry") or item.get("market") or info["market"])
    currency = str(item.get("currency") or info["currency"])
    price = number(first_present(item, ["lastPrice", "currentPrice", "price", "closePrice"]))
    volume = number(first_present(item, ["volume", "tradingVolume", "accumulatedVolume", "accTradeVolume"]))
    trading_value = number(first_present(item, ["tradingValue", "tradeValue", "tradingAmount", "accumulatedTradeAmount", "accTradeAmount"]))
    if not trading_value and volume and price:
        trading_value = volume * price
    timestamp = str(
        item.get("timestamp")
        or item.get("sourceAsOf")
        or item.get("tradeDateTime")
        or item.get("tradeDate")
        or item.get("latestTradingDay")
        or item.get("updatedAt")
        or item.get("time")
        or ""
    )
    fetched_at = utc_now_iso()
    market_session = evaluate_market_hours(
        INVESTMENT_INSIGHT,
        {"symbol": symbol, "market": market, "currency": currency},
        True,
        [market],
        now=now,
    )
    closed_market_reference = market_session.status in {"closed", "closed_exception"}
    freshness = freshness_record(
        "Toss /api/v1/prices",
        INVESTMENT_INSIGHT,
        source_fetched_at=fetched_at,
        source_as_of=timestamp,
        data_quality="actual",
        now=now,
        require_source_as_of=True,
    )
    data_quality = "reference" if closed_market_reference else "actual"
    freshness_status = "last-close" if closed_market_reference else (freshness.get("status") or "unknown")
    freshness_reason = (
        market_session.reason
        if closed_market_reference
        else (freshness.get("reason") or "")
    )
    source_timestamp_state = (
        "provider-last-close"
        if closed_market_reference and timestamp
        else "provider" if timestamp else "missing"
    )
    quote_status = "토스 prices 마지막 종가 기준" if closed_market_reference else "토스 prices 반영"
    quote_message = (
        "현재가는 토스 prices의 장 마감 기준값이며 실시간 체결 근거로 사용하지 않습니다."
        if closed_market_reference
        else "현재가는 토스 prices, 이동평균은 토스 candles 기준입니다."
    )
    latency_status = "last-close" if closed_market_reference else ("provider-observation" if timestamp else "timestamp-missing")
    latency_reason = (
        market_session.reason
        if closed_market_reference
        else "토스 가격 원천이 제공한 기준시각을 사용합니다."
        if timestamp
        else "토스 가격 원천 기준시각이 제공되지 않았습니다."
    )
    return {
        "symbol": symbol or info["symbol"],
        "name": str(item.get("name") or item.get("stockName") or info["name"]),
        "market": market,
        "currency": currency,
        "currentPrice": price,
        "lastPrice": price,
        "changeRate": optional_rate(item),
        "volume": volume,
        "tradingValue": trading_value,
        "quoteSource": "Toss /api/v1/prices",
        "quoteStatus": quote_status,
        "quoteMessage": quote_message,
        "dataQuality": data_quality,
        "provider": "Toss Open API",
        "updatedAt": timestamp or fetched_at,
        "sourceAsOf": timestamp,
        "sourceFetchedAt": fetched_at,
        "sourceTimestampState": source_timestamp_state,
        "freshnessStatus": freshness_status,
        "freshnessReason": freshness_reason,
        "freshnessAgeMinutes": freshness.get("ageMinutes"),
        "freshnessMaxAgeMinutes": freshness.get("maxAgeMinutes"),
        "latencyStatus": latency_status,
        "latencyReason": latency_reason,
        "marketSession": market_session.status,
        "marketSessionLabel": market_session.label,
        "transport": "rest",
        "realTime": False,
    }


def demo_positions() -> List[Position]:
    return [
        normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "quantity": "12",
            "sellableQuantity": "12",
            "averagePrice": 65000,
            "currentPrice": 72000,
            "marketValue": 864000,
            "profitLoss": 84000,
            "profitLossRate": 10.8,
            "tradeStrength": 118,
            "tradingValue": 912000000,
            "volume": 12666,
            "volumeRatio": 1.8,
            "buyVolume": 620000,
            "sellVolume": 480000,
            "foreignBuyVolume": 420000,
            "foreignSellVolume": 275000,
            "institutionBuyVolume": 310000,
            "institutionSellVolume": 228000,
            "ma5": 70500,
            "ma20": 69000,
            "ma60": 66000,
            "ma120": 64000,
            "ma200": 62000,
            "ma20Slope": 0.4,
            "ma60Slope": 0.2,
            "ma20Distance": 4.3,
            "ma60Distance": 9.1,
            "sector": "반도체",
        }),
        normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "quantity": "2",
            "sellableQuantity": "2",
            "averagePrice": 210,
            "currentPrice": 243.1,
            "marketValue": 486.2,
            "profitLoss": 66.2,
            "profitLossRate": 15.8,
            "tradeStrength": 106,
            "tradingValue": 142000000,
            "volume": 584000,
            "volumeRatio": 1.4,
            "buyVolume": 320000,
            "sellVolume": 410000,
            "foreignBuyVolume": 840000,
            "foreignSellVolume": 910000,
            "institutionBuyVolume": 420000,
            "institutionSellVolume": 455000,
            "ma5": 239,
            "ma20": 232,
            "ma60": 218,
            "ma120": 205,
            "ma200": 198,
            "ma20Slope": 0.3,
            "ma60Slope": 0.2,
            "ma20Distance": 4.8,
            "ma60Distance": 11.5,
            "sector": "AI/플랫폼",
        }),
        normalize_position({
            "symbol": "CASH",
            "name": "대기 현금",
            "market": "CASH",
            "currency": "KRW",
            "marketValue": 1250000,
            "sector": "현금",
        }),
    ]


class TossProvider:
    def __init__(self, account: AccountConfig, quote_cache=None, settings: Dict[str, str] = None, token_cache=None, now_fn=None):
        self.account = account
        self.base_url = account.base_url.rstrip("/")
        self.quote_cache = quote_cache if quote_cache is not None else market_quote_cache(runtime_settings())
        self.settings = dict(settings or runtime_settings())
        self.token_cache = token_cache if token_cache is not None else TOSS_TOKEN_CACHE
        self.now_fn = now_fn or time.time
        self.api_guard_state: Dict[str, object] = {}
        self.stage_failures: Dict[str, Dict[str, object]] = {}
        self.auth_refreshes = 0
        self.token_cache_hits = 0
        self.token_expires_at = ""
        self.account_source_fingerprint = ""
        self.cash_balances: Dict[str, Dict[str, object]] = {}
        self.cash_balance_failures: List[str] = []
        self.holdings_overview: Dict[str, object] = {}
        self.exchange_rates: Dict[str, Dict[str, object]] = {}
        self.exchange_rate_failures: List[str] = []

    def cash_balances_complete(self) -> bool:
        return not self.cash_balance_failures and {"KRW", "USD"}.issubset(self.cash_balances)

    def diagnostics_payload(self) -> Dict[str, object]:
        return {
            "toss": {
                "stageFailures": {
                    stage: dict(payload)
                    for stage, payload in self.stage_failures.items()
                },
                "authRefreshes": self.auth_refreshes,
                "tokenCacheHits": self.token_cache_hits,
                "tokenExpiresAt": self.token_expires_at,
                "cashBalances": {
                    currency: dict(payload)
                    for currency, payload in self.cash_balances.items()
                },
                "cashBalanceFailures": list(self.cash_balance_failures),
                "holdingsOverview": dict(self.holdings_overview),
                "exchangeRates": {
                    pair: dict(payload)
                    for pair, payload in self.exchange_rates.items()
                },
                "exchangeRateFailures": list(self.exchange_rate_failures),
            }
        }

    def token_cache_key(self) -> str:
        source = self.base_url + "\n" + str(self.account.client_id or "")
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    def token_refresh_skew_seconds(self) -> int:
        return int_setting(self.settings, "tossTokenRefreshSkewSeconds", 60, 5, 600)

    def token_cache_entry(self) -> Dict[str, object]:
        cached = self.token_cache.get(self.token_cache_key()) if isinstance(self.token_cache, dict) else {}
        return dict(cached or {}) if isinstance(cached, dict) else {}

    def cached_access_token(self, force_refresh: bool = False, stale_token: str = "") -> str:
        if force_refresh and not stale_token:
            return ""
        cached = self.token_cache_entry()
        token = str(cached.get("token") or "")
        refresh_at = number(cached.get("refreshAt"))
        if not token or not refresh_at or self.now_fn() >= refresh_at:
            return ""
        if force_refresh and token == stale_token:
            return ""
        self.token_cache_hits += 1
        self.token_expires_at = str(cached.get("expiresAt") or "")
        return token

    def token_expiry(self, payload: Dict[str, object], now: float) -> Tuple[float, float]:
        raw_expiry = ""
        for key in ["expiresAt", "expires_at", "expiredAt", "expired_at"]:
            if payload.get(key) not in (None, ""):
                raw_expiry = str(payload.get(key))
                break
        parsed_expiry = parse_datetime(raw_expiry)
        expires_at = parsed_expiry.timestamp() if parsed_expiry else 0.0
        if not expires_at:
            expires_in = number(payload.get("expires_in") or payload.get("expiresIn"))
            if expires_in > 0:
                expires_at = now + expires_in
        if not expires_at or expires_at <= now:
            return 0.0, 0.0
        lifetime_seconds = max(1.0, expires_at - now)
        skew_seconds = min(float(self.token_refresh_skew_seconds()), max(5.0, lifetime_seconds * 0.1))
        return expires_at, max(now, expires_at - skew_seconds)

    def record_stage_failure(self, stage: str, error: Exception, recovered: bool = False) -> None:
        normalized = str(stage or "unknown")
        entry = self.stage_failures.setdefault(normalized, {
            "count": 0,
            "lastError": "",
            "recovered": 0,
        })
        entry["lastError"] = http_error_text(error.original_error if isinstance(error, TossAPIError) else error)
        if recovered:
            entry["recovered"] = int(entry.get("recovered") or 0) + 1
            return
        entry["count"] = int(entry.get("count") or 0) + 1

    def auth_headers(self, token: str, extra: Dict[str, str] = None) -> Dict[str, str]:
        headers = {"Authorization": "Bearer " + token}
        headers.update(extra or {})
        return headers

    def token_request(
        self,
        stage: str,
        method: str,
        url: str,
        token: str,
        extra_headers: Dict[str, str] = None,
        body: bytes = None,
    ) -> Tuple[Dict[str, object], str]:
        try:
            payload = toss_json(stage, method, url, self.auth_headers(token, extra_headers), body=body, guard_state=self.api_guard_state)
            return payload, token
        except TossAPIError as error:
            if error.http_status != 401:
                self.record_stage_failure(stage, error)
                raise
            self.record_stage_failure(stage, error)
            refreshed = self.fetch_access_token(force_refresh=True, stale_token=token)
            self.auth_refreshes += 1
            try:
                payload = toss_json(stage, method, url, self.auth_headers(refreshed, extra_headers), body=body, guard_state=self.api_guard_state)
                self.record_stage_failure(stage, error, recovered=True)
                return payload, refreshed
            except TossAPIError as retry_error:
                self.record_stage_failure(stage, retry_error)
                raise

    def fetch_access_token(self, force_refresh: bool = False, stale_token: str = "") -> str:
        if not self.account.client_id or not self.account.client_secret:
            raise RuntimeError("토스 credentials 미설정")
        with TOSS_TOKEN_CACHE_LOCK:
            cached = self.cached_access_token(force_refresh=force_refresh, stale_token=stale_token)
            if cached:
                return cached
            try:
                token_payload = toss_json(
                    "token",
                    "POST",
                    self.base_url + "/oauth2/token",
                    {"Content-Type": "application/x-www-form-urlencoded"},
                    form_body({
                        "grant_type": "client_credentials",
                        "client_id": self.account.client_id,
                        "client_secret": self.account.client_secret,
                    }),
                    guard_state=self.api_guard_state,
                )
            except TossAPIError as error:
                self.record_stage_failure("token", error)
                raise
            token = str(token_payload.get("access_token") or "")
            if not token:
                raise RuntimeError("토스 access_token이 없습니다.")
            now = self.now_fn()
            expires_at, refresh_at = self.token_expiry(token_payload, now)
            if expires_at and isinstance(self.token_cache, dict):
                self.token_cache[self.token_cache_key()] = {
                    "token": token,
                    "expiresAt": expires_at,
                    "refreshAt": refresh_at,
                }
                self.token_expires_at = str(expires_at)
            return token

    def fetch_positions(self) -> Tuple[str, str, List[Position], float, str, List[Position]]:
        if not self.account.client_id or not self.account.client_secret:
            return "demo", "토스 credentials 미설정", demo_positions(), 1250000.0, "KRW", []
        try:
            token = self.fetch_access_token()
            accounts_payload, token = self.token_request("accounts", "GET", self.base_url + "/api/v1/accounts", token)
            accounts = normalize_accounts(accounts_payload)
            selected = select_account(accounts, self.account.account_seq)
            account_seq = self.account.account_seq or str(selected.get("accountSeq") or selected.get("id") or "")
            if account_seq:
                self.account_source_fingerprint = hashlib.sha256(
                    "|".join([
                        str(self.account.provider or "").strip().lower(),
                        self.base_url.strip().lower(),
                        account_seq,
                    ]).encode("utf-8")
                ).hexdigest()
            account_cash = account_cash_amount(selected)
            account_currency = str(selected.get("currency") or "KRW")
            if not account_seq:
                return "live", "계좌 식별값 없음", [], account_cash, account_currency, []
            buying_power, token = self.fetch_buying_power(token, account_seq)
            if buying_power and self.cash_balances_complete():
                account_cash = buying_power
                account_currency = "KRW"
            holdings_payload, token = self.token_request(
                "holdings",
                "GET",
                self.base_url + "/api/v1/holdings",
                token,
                {"X-Tossinvest-Account": account_seq},
            )
            holdings_fetched_at = utc_now_iso()
            self.holdings_overview = normalize_holdings_overview(holdings_payload)
            positions = [
                replace(normalize_position(item), broker_source_as_of=holdings_fetched_at)
                for item in normalize_holdings(holdings_payload)
            ]
            token = self.fetch_exchange_rates(
                token,
                sorted({
                    str(position.currency or "").upper().strip()
                    for position in positions
                    if str(position.currency or "").upper().strip() not in {"", "KRW"}
                }),
            )
            position_prices, token = self.safe_fetch_prices(token, [position.symbol for position in positions if position.symbol and not position.is_cash()])
            positions, token = self.enrich_positions_with_candles(token, positions, position_prices)
            watchlist, token = self.fetch_watchlist_quotes(token, positions)
            return "live", "토스 계좌 동기화", positions, account_cash, account_currency, watchlist
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError, OSError) as error:
            return "demo", "토스 조회 실패 · " + str(error), demo_positions(), 1250000.0, "KRW", []

    def fetch_focus_targets(self) -> Tuple[str, str, str, List[Position], List[Position]]:
        if not self.account.client_id or not self.account.client_secret:
            return "demo", "토스 credentials 미설정", "", [], []
        try:
            token = self.fetch_access_token()
            accounts_payload, token = self.token_request("accounts", "GET", self.base_url + "/api/v1/accounts", token)
            accounts = normalize_accounts(accounts_payload)
            selected = select_account(accounts, self.account.account_seq)
            account_seq = self.account.account_seq or str(selected.get("accountSeq") or selected.get("id") or "")
            if not account_seq:
                return "live", "계좌 식별값 없음", token, [], []
            holdings_payload, token = self.token_request(
                "holdings",
                "GET",
                self.base_url + "/api/v1/holdings",
                token,
                {"X-Tossinvest-Account": account_seq},
            )
            holdings_fetched_at = utc_now_iso()
            positions = [
                replace(normalize_position(item), broker_source_as_of=holdings_fetched_at)
                for item in normalize_holdings(holdings_payload)
            ]
            holding_symbols = {position.symbol.upper() for position in positions if position.symbol}
            watchlist: List[Position] = []
            seen_watchlist = set()
            for raw_symbol in self.account.watchlist_symbols:
                normalized = str(raw_symbol or "").upper().strip()
                if not normalized or normalized in holding_symbols or normalized in seen_watchlist:
                    continue
                seen_watchlist.add(normalized)
                info = known_stock(normalized)
                watchlist.append(replace(normalize_position({
                    "symbol": info.get("symbol") or normalized,
                    "name": info.get("name") or normalized,
                    "market": info.get("market") or "",
                    "currency": info.get("currency") or "",
                    "sector": info.get("sector") or "",
                }), source="watchlist"))
            return "live", "토스 계좌 동기화", token, positions, watchlist
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError, OSError) as error:
            return "demo", "토스 조회 실패 · " + str(error), "", [], []

    def fetch_buying_power(self, token: str, account_seq: str) -> Tuple[float, str]:
        total = 0.0
        rates = currency_rates(self.settings)
        self.cash_balances = {}
        self.cash_balance_failures = []
        for currency in ["KRW", "USD"]:
            try:
                query = urllib.parse.urlencode({"currency": currency})
                payload, token = self.token_request(
                    "buying-power",
                    "GET",
                    self.base_url + "/api/v1/buying-power?" + query,
                    token,
                    {"X-Tossinvest-Account": account_seq},
                )
            except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError):
                self.cash_balance_failures.append(currency)
                continue
            data = payload.get("data") or payload.get("result") or payload
            amount = number(data.get("cashBuyingPower") if isinstance(data, dict) else 0)
            self.cash_balances[currency] = {
                "currency": currency,
                "amount": amount,
                "source": "Toss /api/v1/buying-power",
                "sourceAsOf": utc_now_iso(),
            }
            total += amount * rates.get(currency, 1.0)
        return total, token

    def fetch_exchange_rates(self, token: str, currencies: List[str]) -> str:
        self.exchange_rates = {}
        self.exchange_rate_failures = []
        for currency in currencies or []:
            base = str(currency or "").upper().strip()
            if not base or base == "KRW":
                continue
            query = urllib.parse.urlencode({
                "baseCurrency": base,
                "quoteCurrency": "KRW",
            })
            try:
                payload, token = self.token_request(
                    "exchange-rate",
                    "GET",
                    self.base_url + "/api/v1/exchange-rate?" + query,
                    token,
                )
                data = payload.get("data") or payload.get("result") or payload
                rate = number(data.get("rate") if isinstance(data, dict) else 0)
                if rate <= 0:
                    raise ValueError("empty exchange rate")
                pair = base + "KRW"
                fetched_at = utc_now_iso()
                self.exchange_rates[pair] = {
                    "provider": "Toss Securities",
                    "base": base,
                    "quote": "KRW",
                    "rate": rate,
                    "value": rate,
                    "midRate": number(data.get("midRate")),
                    "basisPoint": number(data.get("basisPoint")),
                    "rateChangeType": str(data.get("rateChangeType") or ""),
                    "validFrom": str(data.get("validFrom") or ""),
                    "validUntil": str(data.get("validUntil") or ""),
                    "lastUpdated": str(data.get("validFrom") or ""),
                    "fetchedAt": fetched_at,
                    "sourceType": LIVE_MARKET_FX_SOURCE_TYPE,
                    "evidenceStrength": "broker_reference",
                    "valuationRate": rate,
                    "valuationProvider": "Toss Securities",
                    "valuationSourceType": LIVE_MARKET_FX_SOURCE_TYPE,
                }
            except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError, OSError):
                self.exchange_rate_failures.append(base)
        return token

    def attach_exchange_rates(self, signals: Dict[str, object]) -> Dict[str, object]:
        if not isinstance(signals, dict) or not self.exchange_rates:
            return signals
        rates = signals.setdefault("fxRates", {})
        if not isinstance(rates, dict):
            rates = {}
            signals["fxRates"] = rates
        for pair, payload in self.exchange_rates.items():
            existing = rates.get(pair) if isinstance(rates.get(pair), dict) else {}
            row = dict(existing)
            if existing:
                row.setdefault("fallbackRate", number(existing.get("rate") or existing.get("value")))
                row.setdefault("fallbackProvider", str(existing.get("provider") or ""))
            row.update(payload)
            rates[pair] = row
        return signals

    def safe_fetch_prices(self, token: str, symbols: List[str]) -> Tuple[Dict[str, Dict[str, object]], str]:
        try:
            return self.fetch_prices(token, symbols)
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError, OSError):
            return {}, token

    def fetch_prices(self, token: str, symbols: List[str]) -> Tuple[Dict[str, Dict[str, object]], str]:
        unique = []
        for symbol in symbols:
            normalized = str(symbol or "").upper().strip()
            if normalized and normalized not in unique:
                unique.append(normalized)
        quotes: Dict[str, Dict[str, object]] = {}
        for index in range(0, len(unique), 200):
            chunk = unique[index:index + 200]
            if not chunk:
                continue
            query = urllib.parse.urlencode({"symbols": ",".join(chunk)})
            payload, token = self.token_request(
                "prices",
                "GET",
                self.base_url + "/api/v1/prices?" + query,
                token,
            )
            for item in normalize_price_items(payload):
                normalized = normalize_price_payload(item)
                symbol = str(normalized.get("symbol") or "").upper()
                if symbol:
                    quotes[symbol] = normalized
        return quotes, token

    def fetch_daily_candles(self, token: str, symbol: str) -> Tuple[List[Dict[str, object]], str]:
        query = urllib.parse.urlencode({
            "symbol": symbol,
            "interval": "1d",
            "count": "200",
            "adjusted": "true",
        })
        payload, token = self.token_request(
            "candles",
            "GET",
            self.base_url + "/api/v1/candles?" + query,
            token,
        )
        return normalize_candles(payload), token

    def cached_quote(self, symbol: str) -> Dict[str, object]:
        clean_symbol = str(symbol or "").upper().strip()
        if not clean_symbol:
            return {}
        candidates = []
        for account_id, scope in [
            (self.account.account_id, "account"),
            (MARKET_DATA_ACCOUNT_ID, "market-data"),
        ]:
            try:
                payload = self.quote_cache.load("toss", account_id, clean_symbol)
            except Exception:
                payload = {}
            if not isinstance(payload, dict) or not payload:
                continue
            candidate = dict(payload)
            candidate["cacheScope"] = scope
            timestamp = parse_datetime(candidate.get("sourceAsOf") or candidate.get("sourceFetchedAt") or candidate.get("fetchedAt") or candidate.get("updatedAt"))
            candidates.append((timestamp.timestamp() if timestamp else float("-inf"), candidate))
        return max(candidates, key=lambda item: item[0])[1] if candidates else {}

    def save_quote_cache(self, position: Position) -> None:
        if not position.symbol:
            return
        if not any([
            position.current_price,
            position.volume,
            position.ma5,
            position.ma20,
            position.ma60,
            position.ma120,
            position.ma200,
        ]):
            return
        payload = {
            "symbol": position.symbol,
            "name": position.name,
            "market": position.market,
            "currency": position.currency,
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
            "marketSignalCoverage": dict(position.market_signal_coverage or {}),
            "updatedAt": position.updated_at or utc_now_iso(),
            "sourceAsOf": position.source_as_of,
            "sourceFetchedAt": position.source_fetched_at,
            "sourceTimestampState": position.source_timestamp_state,
            "freshnessStatus": position.freshness_status,
            "freshnessReason": position.freshness_reason,
            "freshnessAgeMinutes": position.freshness_age_minutes,
            "freshnessMaxAgeMinutes": position.freshness_max_age_minutes,
            "latencyStatus": position.latency_status,
            "latencyReason": position.latency_reason,
            "marketSession": position.market_session,
            "marketSessionLabel": position.market_session_label,
            "transport": position.source_transport,
            "realTime": bool(position.real_time),
            "indicatorAsOf": position.indicator_as_of,
            "indicatorFetchedAt": position.indicator_fetched_at,
            "tradingValue": position.trading_value,
            "volume": position.volume,
            "volumeRatio": position.volume_ratio,
            "tradeStrength": position.trade_strength,
            "buyVolume": position.buy_volume,
            "sellVolume": position.sell_volume,
            "ma5": position.ma5,
            "ma20": position.ma20,
            "ma60": position.ma60,
            "ma120": position.ma120,
            "ma200": position.ma200,
            "ma20Slope": position.ma20_slope,
            "ma60Slope": position.ma60_slope,
            "ma20Distance": position.ma20_distance,
            "ma60Distance": position.ma60_distance,
            "sector": position.sector,
        }
        observed = investor_flow_observed_fields(position)
        for fields in INVESTOR_PARTY_FIELDS.values():
            for public_key, attr_key in [
                (fields["buy"], fields["buy_attr"]),
                (fields["sell"], fields["sell_attr"]),
                (fields["net"], fields["net_attr"]),
                (fields["amount"], fields["amount_attr"]),
            ]:
                if public_key in observed:
                    payload[public_key] = getattr(position, attr_key, 0.0)
        try:
            self.quote_cache.save("toss", self.account.account_id, position.symbol, payload)
        except Exception:
            return

    def merge_market_data(
        self,
        position: Position,
        quote: Dict[str, object],
        indicators: Dict[str, object],
        cached: Dict[str, object],
        quote_live: bool,
        indicators_live: bool,
    ) -> Position:
        quote = quote or {}
        indicators = indicators or {}
        cached = cached or {}
        cached_price = number(first_present(cached, ["currentPrice", "lastPrice", "price", "closePrice"]))
        live_price = number(first_present(quote, ["currentPrice", "lastPrice", "price", "closePrice"]))
        cached_at = parse_datetime(cached.get("sourceAsOf") or cached.get("sourceFetchedAt") or cached.get("fetchedAt") or cached.get("updatedAt"))
        position_at = parse_datetime(position.source_as_of or position.source_fetched_at or position.updated_at)
        cached_is_fresher = bool(
            cached_price
            and (
                (cached_at and (not position_at or cached_at > position_at))
                or not position.current_price
            )
        )
        used_cached_price = not live_price and cached_is_fresher
        current_price = live_price or (cached_price if used_cached_price else position.current_price) or cached_price
        indicator_source = indicators if indicators else cached
        volume = (
            number(first_present(quote, ["volume", "tradingVolume", "accumulatedVolume"]))
            or (number(cached.get("volume")) if used_cached_price else 0)
            or position.volume
            or number(indicator_source.get("volume"))
            or number(cached.get("volume"))
        )
        volume_ratio = (
            number(indicator_source.get("volumeRatio"))
            or (number(cached.get("volumeRatio")) if used_cached_price else 0)
            or position.volume_ratio
            or number(cached.get("volumeRatio"))
        )
        raw_trading_value = (
            number(first_present(quote, ["tradingValue", "tradeValue", "tradingAmount"]))
            or (number(cached.get("tradingValue")) if used_cached_price else 0)
            or position.trading_value
            or number(cached.get("tradingValue"))
        )
        trading_value = number(trading_value_snapshot(current_price, volume, raw_trading_value).get("tradingValue"))
        quote_message = str(quote.get("quoteMessage") or "현재가는 토스 prices, 이동평균은 토스 candles 기준입니다.")
        quote_status = str(quote.get("quoteStatus") or ("토스 prices 반영" if live_price else ""))
        quote_source = str(quote.get("quoteSource") or "")
        # A successful REST response outside the exchange session is still a
        # last-close reference. Daily candle availability must not promote
        # that quote into an actual intraday observation.
        quote_quality = str(quote.get("dataQuality") or "").strip()
        data_quality = quote_quality or ("actual" if live_price and indicators_live else position.data_quality)
        updated_at = str(quote.get("updatedAt") or "")
        source_as_of = str(quote.get("sourceAsOf") or "")
        source_fetched_at = str(quote.get("sourceFetchedAt") or "")
        source_timestamp_state = str(quote.get("sourceTimestampState") or "")
        freshness_status = str(quote.get("freshnessStatus") or "")
        freshness_reason = str(quote.get("freshnessReason") or "")
        freshness_age_minutes = quote.get("freshnessAgeMinutes")
        freshness_max_age_minutes = quote.get("freshnessMaxAgeMinutes") or quote.get("maxAgeMinutes")
        latency_status = str(quote.get("latencyStatus") or "")
        latency_reason = str(quote.get("latencyReason") or "")
        market_session = str(quote.get("marketSession") or "")
        market_session_label = str(quote.get("marketSessionLabel") or "")
        source_transport = str(quote.get("transport") or "")
        real_time = bool(quote.get("realTime"))
        indicator_as_of = str(indicator_source.get("sourceAsOf") or indicator_source.get("latestCandleAt") or "")
        indicator_fetched_at = str(indicator_source.get("sourceFetchedAt") or "")
        if used_cached_price:
            quote_status = "마지막 저장 시세"
            quote_message = "토스 호출 제한 또는 오류로 마지막 저장 시세를 표시합니다."
            quote_source = str(cached.get("quoteSource") or "Toss Open API cache")
            data_quality = "cached"
            updated_at = str(cached.get("updatedAt") or "")
            source_as_of = str(cached.get("sourceAsOf") or "")
            source_fetched_at = str(cached.get("sourceFetchedAt") or cached.get("fetchedAt") or "")
            source_timestamp_state = str(cached.get("sourceTimestampState") or "cached")
            freshness_status = str(cached.get("freshnessStatus") or "cached")
            freshness_reason = str(cached.get("freshnessReason") or "마지막 저장 시세를 사용합니다.")
            freshness_age_minutes = cached.get("freshnessAgeMinutes")
            freshness_max_age_minutes = cached.get("freshnessMaxAgeMinutes") or cached.get("maxAgeMinutes")
            latency_status = str(cached.get("latencyStatus") or "cached")
            latency_reason = str(cached.get("latencyReason") or "원천 호출 실패로 마지막 저장 시세를 사용합니다.")
            market_session = str(cached.get("marketSession") or "")
            market_session_label = str(cached.get("marketSessionLabel") or "")
            source_transport = str(cached.get("transport") or "cache")
            real_time = False
            indicator_as_of = str(cached.get("indicatorAsOf") or indicator_as_of)
            indicator_fetched_at = str(cached.get("indicatorFetchedAt") or indicator_fetched_at)
        elif live_price and not indicators_live and cached:
            quote_message = "현재가는 토스 prices, 이동평균은 마지막 저장 candles 기준입니다."
            if quote_quality not in {"reference", "stale", "cached", "unavailable"}:
                data_quality = combine_quality(quote_quality or "actual", str(cached.get("dataQuality") or "cached"))
        elif not live_price and indicators_live and not position.current_price:
            quote_status = "토스 candles 지표 반영"
            quote_message = "토스 prices 현재가 없이 candles 지표만 반영했습니다."
            quote_source = "Toss /api/v1/candles"
            data_quality = combine_quality(position.data_quality, "actual")
        elif not live_price and position.current_price:
            quote_status = position.quote_status or "토스 잔고 시세"
            quote_message = position.quote_message or "잔고 응답의 현재가를 표시합니다."
            quote_source = position.quote_source or "Toss holdings"
            updated_at = position.updated_at
            source_as_of = position.source_as_of
            source_fetched_at = position.source_fetched_at
            source_timestamp_state = position.source_timestamp_state
            freshness_status = position.freshness_status
            freshness_reason = position.freshness_reason
            freshness_age_minutes = position.freshness_age_minutes
            freshness_max_age_minutes = position.freshness_max_age_minutes
            latency_status = position.latency_status
            latency_reason = position.latency_reason
            market_session = position.market_session
            market_session_label = position.market_session_label
            source_transport = position.source_transport
            real_time = bool(position.real_time)
            indicator_as_of = position.indicator_as_of or indicator_as_of
            indicator_fetched_at = position.indicator_fetched_at or indicator_fetched_at
        selected_updated_at = updated_at
        if not live_price and not used_cached_price:
            selected_updated_at = position.updated_at or str(cached.get("updatedAt") or "")
        estimated_market_value = position.quantity * current_price if position.quantity and current_price else 0.0
        market_value = position.market_value or estimated_market_value
        market_value_repriced = False
        if (live_price or used_cached_price) and estimated_market_value:
            market_value_repriced = abs(estimated_market_value - number(position.market_value)) > max(
                1.0,
                abs(estimated_market_value) * 0.0001,
            )
            market_value = estimated_market_value
        market_value_krw = position.market_value_krw
        profit_loss = position.profit_loss
        profit_loss_krw = position.profit_loss_krw
        profit_loss_rate = position.profit_loss_rate
        if market_value_repriced:
            if str(position.currency or quote.get("currency") or "").upper() == "KRW":
                market_value_krw = market_value
            else:
                market_value_krw = 0.0
            if position.average_price and position.quantity:
                cost_basis = position.average_price * position.quantity
                profit_loss = market_value - cost_basis
                profit_loss_rate = ((current_price - position.average_price) / position.average_price) * 100
                profit_loss_krw = profit_loss if str(position.currency or quote.get("currency") or "").upper() == "KRW" else 0.0
        ma5 = number(indicator_source.get("ma5")) or position.ma5
        ma20 = number(indicator_source.get("ma20")) or position.ma20
        ma60 = number(indicator_source.get("ma60")) or position.ma60
        ma5_distance = pct_distance(current_price, ma5) if current_price and ma5 else number(indicator_source.get("ma5Distance")) or position.ma5_distance
        ma20_distance = pct_distance(current_price, ma20) if current_price and ma20 else number(indicator_source.get("ma20Distance")) or position.ma20_distance
        ma60_distance = pct_distance(current_price, ma60) if current_price and ma60 else number(indicator_source.get("ma60Distance")) or position.ma60_distance
        quote_change_rate = quote.get("changeRate")
        selected_change_rate = (
            quote_change_rate
            if quote_change_rate is not None
            else position.change_rate
        )
        candle_session = market_session_date(
            indicator_source.get("latestCandleAt") or indicator_source.get("sourceAsOf"),
            quote.get("market") or position.market or cached.get("market"),
            quote.get("currency") or position.currency or cached.get("currency"),
        )
        quote_session = market_session_date(
            source_as_of or selected_updated_at,
            quote.get("market") or position.market or cached.get("market"),
            quote.get("currency") or position.currency or cached.get("currency"),
        )
        same_session = (
            candle_session == quote_session
            if candle_session and quote_session
            else None
        )
        derived_change = derived_price_change_facts(
            current_price,
            indicator_source,
            same_session,
        )
        if selected_change_rate is None and derived_change:
            selected_change_rate = derived_change.get("changeRate")
        explicit_change = quote_change_rate is not None
        previous_close = (
            number(quote.get("previousClose"))
            or number(cached.get("previousClose"))
            or position.previous_close
            or number(derived_change.get("previousClose"))
        )
        return_1d = (
            selected_change_rate
            if selected_change_rate is not None
            else derived_change.get("return1d")
        )
        return replace(
            position,
            current_price=current_price,
            change_rate=selected_change_rate,
            previous_close=previous_close,
            return_1d=return_1d,
            return_3d=(
                derived_change.get("return3d")
                if derived_change.get("return3d") is not None
                else position.return_3d
            ),
            return_5d=(
                derived_change.get("return5d")
                if derived_change.get("return5d") is not None
                else position.return_5d
            ),
            price_change_source=(
                "provider-quote"
                if explicit_change
                else str(derived_change.get("priceChangeSource") or position.price_change_source or cached.get("priceChangeSource") or "")
            ),
            price_change_basis=(
                "provider-reported-change-rate"
                if explicit_change
                else str(derived_change.get("priceChangeBasis") or position.price_change_basis or cached.get("priceChangeBasis") or "")
            ),
            price_history_adjustment=str(
                derived_change.get("priceHistoryAdjustment")
                or indicator_source.get("priceHistoryAdjustment")
                or position.price_history_adjustment
                or cached.get("priceHistoryAdjustment")
                or ""
            ),
            price_change_usable=bool(
                explicit_change
                or derived_change.get("priceChangeUsable")
                or position.price_change_usable
                or cached.get("priceChangeUsable")
            ),
            quote_source=quote_source or position.quote_source or str(cached.get("quoteSource") or ""),
            quote_status=quote_status or position.quote_status or str(cached.get("quoteStatus") or ""),
            quote_message=quote_message or position.quote_message or str(cached.get("quoteMessage") or ""),
            data_quality=data_quality or position.data_quality or str(cached.get("dataQuality") or ""),
            updated_at=selected_updated_at,
            source_as_of=source_as_of,
            source_fetched_at=source_fetched_at,
            source_timestamp_state=source_timestamp_state,
            freshness_status=freshness_status,
            freshness_reason=freshness_reason,
            freshness_age_minutes=freshness_age_minutes,
            freshness_max_age_minutes=freshness_max_age_minutes,
            latency_status=latency_status,
            latency_reason=latency_reason,
            market_session=market_session,
            market_session_label=market_session_label,
            source_transport=source_transport,
            real_time=real_time,
            indicator_as_of=indicator_as_of,
            indicator_fetched_at=indicator_fetched_at,
            currency=str(quote.get("currency") or position.currency or cached.get("currency") or ""),
            market=str(quote.get("market") or position.market or cached.get("market") or ""),
            market_value=market_value,
            market_value_krw=market_value_krw,
            mark_to_market_value=market_value,
            mark_to_market_value_krw=market_value_krw,
            profit_loss=profit_loss,
            profit_loss_krw=profit_loss_krw,
            profit_loss_rate=profit_loss_rate,
            trading_value=trading_value,
            volume=volume,
            volume_ratio=volume_ratio,
            ma5=ma5,
            ma20=ma20,
            ma60=ma60,
            ma120=number(indicator_source.get("ma120")) or position.ma120,
            ma200=number(indicator_source.get("ma200")) or position.ma200,
            ma20_slope=number(indicator_source.get("ma20Slope")) or position.ma20_slope,
            ma60_slope=number(indicator_source.get("ma60Slope")) or position.ma60_slope,
            ma5_distance=ma5_distance,
            ma20_distance=ma20_distance,
            ma60_distance=ma60_distance,
        )

    def enrich_positions_with_candles(
        self,
        token: str,
        positions: List[Position],
        price_map: Optional[Dict[str, Dict[str, object]]] = None,
    ) -> Tuple[List[Position], str]:
        enriched: List[Position] = []
        chart_calls = 0
        prices = price_map or {}
        for position in positions:
            if position.is_cash() or not position.symbol:
                enriched.append(position)
                continue
            indicators: Dict[str, object] = {}
            indicators_live = False
            try:
                if chart_calls:
                    time.sleep(0.22)
                candles, token = self.fetch_daily_candles(token, position.symbol)
                chart_calls += 1
                indicators = technical_indicators_from_candles(
                    candles,
                    adjustment_status="provider-adjusted",
                )
                indicators["sourceFetchedAt"] = utc_now_iso()
                indicators_live = bool(indicators)
            except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError, OSError):
                indicators = {}
            symbol = position.symbol.upper()
            merged = self.merge_market_data(
                position,
                prices.get(symbol) or {},
                indicators,
                self.cached_quote(symbol),
                quote_live=bool(prices.get(symbol)),
                indicators_live=indicators_live,
            )
            if prices.get(symbol) or indicators_live:
                self.save_quote_cache(merged)
            enriched.append(merged)
        return enriched, token

    def fetch_watchlist_quotes(self, token: str, positions: List[Position]) -> Tuple[List[Position], str]:
        holding_symbols = {position.symbol.upper() for position in positions if position.symbol}
        watchlist: List[Position] = []
        symbols = [
            str(symbol or "").upper()
            for symbol in self.account.watchlist_symbols
            if str(symbol or "").upper() and str(symbol or "").upper() not in holding_symbols
        ]
        prices, token = self.safe_fetch_prices(token, symbols)
        chart_calls = 0
        for symbol in symbols:
            normalized = str(symbol or "").upper()
            info = known_stock(normalized)
            base = normalize_position({
                "symbol": info.get("symbol") or normalized,
                "name": info.get("name") or normalized,
                "market": info.get("market") or "",
                "currency": info.get("currency") or "",
                "sector": info.get("sector") or "",
            })
            indicators: Dict[str, object] = {}
            indicators_live = False
            try:
                if chart_calls:
                    time.sleep(0.22)
                candles, token = self.fetch_daily_candles(token, normalized)
                chart_calls += 1
                indicators = technical_indicators_from_candles(
                    candles,
                    adjustment_status="provider-adjusted",
                )
                indicators["sourceFetchedAt"] = utc_now_iso()
                indicators_live = bool(indicators)
            except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError, OSError):
                indicators = {}
            quote = prices.get(normalized) or {}
            merged = self.merge_market_data(
                base,
                quote,
                indicators,
                self.cached_quote(normalized),
                quote_live=bool(quote),
                indicators_live=indicators_live,
            )
            merged = replace(merged, source="watchlist")
            if quote or indicators_live:
                self.save_quote_cache(merged)
            watchlist.append(merged)
        return watchlist, token


def build_snapshot(account: AccountConfig, external_settings: Optional[Dict[str, str]] = None) -> AccountSnapshot:
    settings = external_settings or runtime_settings()
    provider = TossProvider(account, settings=settings)
    mode, status, positions, cash, currency, watchlist = provider.fetch_positions()
    positions, watchlist = enrich_snapshot_position_identities(positions, watchlist)
    kis_provider = KISMarketSignalProvider()
    positions, watchlist = kis_provider.enrich_collections(positions, watchlist)
    external_signals = ExternalSignalProvider(settings=settings).signals_for_positions(
        positions + watchlist,
        cache_scope="account-snapshot",
    )
    external_signals = provider.attach_exchange_rates(external_signals)
    external_signals = kis_provider.attach_fundamentals_to_external_signals(external_signals, positions + watchlist)
    account_context = account.ontology_account_context()
    fx_rates = currency_rates_from_external_signals(settings, external_signals)
    runtime_fx_currencies = runtime_fx_currencies_from_external_signals(external_signals)
    if provider.cash_balances_complete():
        cash = sum(
            number(item.get("amount")) * fx_rates.get(str(currency).upper(), 1.0)
            for currency, item in provider.cash_balances.items()
        )
        currency = "KRW"
    valuation_basis = normalized_valuation_basis(
        settings.get("portfolioValuationBasis"),
        BROKER_NET_BASIS,
    )
    generated_at = utc_now_iso()
    positions = apply_position_base_currency_values(
        positions,
        fx_rates,
        runtime_fx_currencies,
        external_signals=external_signals,
        valuation_basis=valuation_basis,
    )
    watchlist = apply_position_base_currency_values(
        watchlist,
        fx_rates,
        runtime_fx_currencies,
        external_signals=external_signals,
        valuation_basis=MARK_TO_MARKET_BASIS,
    )
    portfolio = portfolio_summary(
        positions,
        cash,
        currency,
        fx_rates,
        runtime_fx_currencies,
        valuation_basis=valuation_basis,
        account_id=account.account_id,
        observed_at=generated_at,
        external_signals=external_signals,
    )
    decisions = decisions_for_positions(
        positions,
        portfolio,
        external_signals=external_signals,
        runtime_context={"account": account_context},
        require_inference_context=True,
    )
    metadata = provider.diagnostics_payload()
    metadata["cashBalanceComponents"] = {
        currency: {
            "amount": item.get("amount"),
            "currency": currency,
            "source": item.get("source") or "Toss /api/v1/buying-power",
        }
        for currency, item in sorted(provider.cash_balances.items())
        if isinstance(item, dict) and item.get("amount") not in (None, "")
    }
    metadata.update(kis_provider.diagnostics_payload())
    metadata["accountSourceFingerprint"] = provider.account_source_fingerprint or hashlib.sha256(
        "|".join([
            str(account.provider or "").strip().lower(),
            str(account.base_url or "").strip().lower(),
            str(account.account_seq or account.account_id or "").strip(),
        ]).encode("utf-8")
    ).hexdigest()
    metadata["accountContext"] = account_context
    metadata["marketProxyQuotes"] = market_proxy_quote_context(settings, provider.quote_cache, external_signals=external_signals)
    metadata["valuation"] = {
        key: value
        for key, value in portfolio.valuation.items()
        if key != "positions"
    }
    complete_account_balance = mode == "live" and status == "토스 계좌 동기화"
    metadata["accountSnapshotCompleteness"] = {
        "holdings": "complete" if complete_account_balance else "incomplete",
        "cash": "complete" if complete_account_balance and provider.cash_balances_complete() else "incomplete",
        "source": "toss-account-provider-response",
        "cashCurrencies": sorted(provider.cash_balances),
        "cashFailedCurrencies": list(provider.cash_balance_failures),
    }
    return AccountSnapshot(
        account_id=account.account_id,
        account_label=account.label,
        provider=account.provider,
        mode=mode,
        status=status,
        generated_at=generated_at,
        portfolio=portfolio,
        positions=positions,
        decisions=decisions,
        external_signals=external_signals,
        watchlist=watchlist,
        metadata=metadata,
    )


def enrich_snapshot_position_identities(
    positions: List[Position],
    watchlist: List[Position],
) -> Tuple[List[Position], List[Position]]:
    """Resolve code-only provider names from the locally refreshed symbol universe."""
    try:
        store = symbol_universe_store()
    except Exception:
        return positions, watchlist
    identities: Dict[str, Dict[str, object]] = {}
    for position in list(positions or []) + list(watchlist or []):
        symbol = str(getattr(position, "symbol", "") or "").strip().upper()
        if not symbol or symbol in identities:
            continue
        try:
            item = store.get(symbol)
            identities[symbol] = item.to_dict(24) if item else {}
        except Exception:
            identities[symbol] = {}
    return (
        [position_with_symbol_identity(position, identities.get(str(position.symbol or "").upper())) for position in positions or []],
        [position_with_symbol_identity(position, identities.get(str(position.symbol or "").upper())) for position in watchlist or []],
    )
