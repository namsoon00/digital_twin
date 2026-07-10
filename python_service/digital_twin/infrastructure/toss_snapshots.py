import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from ..domain.accounts import AccountConfig
from ..domain.data_freshness import combine_quality
from ..domain.market_data import known_stock, normalize_position, number, pct_distance, technical_indicators_from_candles
from ..domain.portfolio import AccountSnapshot, Position, utc_now_iso
from ..domain.portfolio_calculations import fx_rates_with_external_signals, portfolio_summary
from ..domain.strategy import decisions_for_positions
from .external_signals import ExternalSignalProvider
from .kis_market_signals import KISMarketSignalProvider
from .settings import currency_rates, runtime_settings
from .sqlite_monitoring import SQLiteMarketQuoteCache


MARKET_DATA_ACCOUNT_ID = "__market_data__"


class TossAPIError(RuntimeError):
    def __init__(self, stage: str, error: Exception):
        self.stage = str(stage or "")
        self.original_error = error
        self.http_status = int(getattr(error, "code", 0) or 0)
        super().__init__("Toss " + self.stage + " 단계 실패 · " + http_error_text(error))


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
) -> Dict[str, object]:
    last_error: Exception = RuntimeError("unknown error")
    for attempt in range(max(1, attempts)):
        try:
            return http_json(method, url, headers, body=body, timeout=timeout)
        except Exception as error:  # noqa: BLE001 - adapter normalizes vendor failures.
            last_error = error
            if attempt + 1 >= max(1, attempts) or not retryable_http_error(error):
                break
            time.sleep(0.35 * (attempt + 1))
    raise TossAPIError(stage, last_error)


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


def normalize_price_payload(item: Dict[str, object]) -> Dict[str, object]:
    symbol = price_symbol(item)
    info = known_stock(symbol)
    price = number(first_present(item, ["lastPrice", "currentPrice", "price", "closePrice"]))
    volume = number(first_present(item, ["volume", "tradingVolume", "accumulatedVolume", "accTradeVolume"]))
    trading_value = number(first_present(item, ["tradingValue", "tradeValue", "tradingAmount", "accumulatedTradeAmount", "accTradeAmount"]))
    if not trading_value and volume and price:
        trading_value = volume * price
    timestamp = str(item.get("timestamp") or item.get("updatedAt") or item.get("time") or "")
    return {
        "symbol": symbol or info["symbol"],
        "name": str(item.get("name") or item.get("stockName") or info["name"]),
        "market": str(item.get("marketCountry") or item.get("market") or info["market"]),
        "currency": str(item.get("currency") or info["currency"]),
        "currentPrice": price,
        "lastPrice": price,
        "changeRate": optional_rate(item),
        "volume": volume,
        "tradingValue": trading_value,
        "quoteSource": "Toss /api/v1/prices",
        "quoteStatus": "토스 prices 반영",
        "quoteMessage": "현재가는 토스 prices, 이동평균은 토스 candles 기준입니다.",
        "dataQuality": "actual",
        "provider": "Toss Open API",
        "updatedAt": timestamp or utc_now_iso(),
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
    def __init__(self, account: AccountConfig, quote_cache: Optional[SQLiteMarketQuoteCache] = None):
        self.account = account
        self.base_url = account.base_url.rstrip("/")
        self.quote_cache = quote_cache if quote_cache is not None else SQLiteMarketQuoteCache()
        self.stage_failures: Dict[str, Dict[str, object]] = {}
        self.auth_refreshes = 0

    def diagnostics_payload(self) -> Dict[str, object]:
        return {
            "toss": {
                "stageFailures": {
                    stage: dict(payload)
                    for stage, payload in self.stage_failures.items()
                },
                "authRefreshes": self.auth_refreshes,
            }
        }

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
            payload = toss_json(stage, method, url, self.auth_headers(token, extra_headers), body=body)
            return payload, token
        except TossAPIError as error:
            if error.http_status != 401:
                self.record_stage_failure(stage, error)
                raise
            self.record_stage_failure(stage, error)
            refreshed = self.fetch_access_token()
            self.auth_refreshes += 1
            try:
                payload = toss_json(stage, method, url, self.auth_headers(refreshed, extra_headers), body=body)
                self.record_stage_failure(stage, error, recovered=True)
                return payload, refreshed
            except TossAPIError as retry_error:
                self.record_stage_failure(stage, retry_error)
                raise

    def fetch_access_token(self) -> str:
        if not self.account.client_id or not self.account.client_secret:
            raise RuntimeError("토스 credentials 미설정")
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
            )
        except TossAPIError as error:
            self.record_stage_failure("token", error)
            raise
        token = str(token_payload.get("access_token") or "")
        if not token:
            raise RuntimeError("토스 access_token이 없습니다.")
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
            account_cash = account_cash_amount(selected)
            account_currency = str(selected.get("currency") or "KRW")
            if not account_seq:
                return "live", "계좌 식별값 없음", [], account_cash, account_currency, []
            buying_power, token = self.fetch_buying_power(token, account_seq)
            if buying_power:
                account_cash = buying_power
                account_currency = "KRW"
            holdings_payload, token = self.token_request(
                "holdings",
                "GET",
                self.base_url + "/api/v1/holdings",
                token,
                {"X-Tossinvest-Account": account_seq},
            )
            positions = [normalize_position(item) for item in normalize_holdings(holdings_payload)]
            position_prices, token = self.safe_fetch_prices(token, [position.symbol for position in positions if position.symbol and not position.is_cash()])
            positions, token = self.enrich_positions_with_candles(token, positions, position_prices)
            watchlist, token = self.fetch_watchlist_quotes(token, positions)
            return "live", "토스 계좌 동기화", positions, account_cash, account_currency, watchlist
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError, OSError) as error:
            return "demo", "토스 조회 실패 · " + str(error), demo_positions(), 1250000.0, "KRW", []

    def fetch_buying_power(self, token: str, account_seq: str) -> Tuple[float, str]:
        total = 0.0
        rates = currency_rates()
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
                continue
            data = payload.get("data") or payload.get("result") or payload
            amount = number(data.get("cashBuyingPower") if isinstance(data, dict) else 0)
            total += amount * rates.get(currency, 1.0)
        return total, token

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
        try:
            payload = self.quote_cache.load("toss", self.account.account_id, clean_symbol)
            if payload:
                return payload
        except Exception:
            pass
        try:
            return self.quote_cache.load("toss", MARKET_DATA_ACCOUNT_ID, clean_symbol)
        except Exception:
            return {}

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
            "quoteSource": position.quote_source,
            "quoteStatus": position.quote_status,
            "quoteMessage": position.quote_message,
            "dataQuality": position.data_quality or "actual",
            "marketSignalCoverage": dict(position.market_signal_coverage or {}),
            "updatedAt": position.updated_at or utc_now_iso(),
            "tradingValue": position.trading_value,
            "volume": position.volume,
            "volumeRatio": position.volume_ratio,
            "tradeStrength": position.trade_strength,
            "buyVolume": position.buy_volume,
            "sellVolume": position.sell_volume,
            "foreignBuyVolume": position.foreign_buy_volume,
            "foreignSellVolume": position.foreign_sell_volume,
            "foreignNetVolume": position.foreign_net_volume,
            "foreignNetAmount": position.foreign_net_amount,
            "institutionBuyVolume": position.institution_buy_volume,
            "institutionSellVolume": position.institution_sell_volume,
            "institutionNetVolume": position.institution_net_volume,
            "institutionNetAmount": position.institution_net_amount,
            "individualBuyVolume": position.individual_buy_volume,
            "individualSellVolume": position.individual_sell_volume,
            "individualNetVolume": position.individual_net_volume,
            "individualNetAmount": position.individual_net_amount,
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
        used_cached_price = not live_price and not position.current_price and bool(cached_price)
        current_price = live_price or position.current_price or cached_price
        indicator_source = indicators if indicators else cached
        volume = (
            position.volume
            or number(first_present(quote, ["volume", "tradingVolume", "accumulatedVolume"]))
            or number(indicator_source.get("volume"))
            or number(cached.get("volume"))
        )
        volume_ratio = position.volume_ratio or number(indicator_source.get("volumeRatio")) or number(cached.get("volumeRatio"))
        trading_value = (
            position.trading_value
            or number(first_present(quote, ["tradingValue", "tradeValue", "tradingAmount"]))
            or number(cached.get("tradingValue"))
        )
        if not trading_value and volume and current_price:
            trading_value = volume * current_price
        quote_message = "현재가는 토스 prices, 이동평균은 토스 candles 기준입니다."
        quote_status = "토스 prices 반영" if live_price else ""
        quote_source = str(quote.get("quoteSource") or "")
        data_quality = "actual" if live_price and indicators_live else position.data_quality
        updated_at = str(quote.get("updatedAt") or "")
        if used_cached_price:
            quote_status = "마지막 저장 시세"
            quote_message = "토스 호출 제한 또는 오류로 마지막 저장 시세를 표시합니다."
            quote_source = str(cached.get("quoteSource") or "Toss Open API cache")
            data_quality = "cached"
            updated_at = str(cached.get("updatedAt") or "")
        elif live_price and not indicators_live and cached:
            quote_message = "현재가는 토스 prices, 이동평균은 마지막 저장 candles 기준입니다."
            data_quality = combine_quality("actual", str(cached.get("dataQuality") or "cached"))
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
        market_value = position.market_value or (position.quantity * current_price if position.quantity and current_price else 0.0)
        ma20 = number(indicator_source.get("ma20")) or position.ma20
        ma60 = number(indicator_source.get("ma60")) or position.ma60
        ma20_distance = pct_distance(current_price, ma20) if current_price and ma20 else number(indicator_source.get("ma20Distance")) or position.ma20_distance
        ma60_distance = pct_distance(current_price, ma60) if current_price and ma60 else number(indicator_source.get("ma60Distance")) or position.ma60_distance
        return replace(
            position,
            current_price=current_price,
            change_rate=quote.get("changeRate") if quote.get("changeRate") is not None else position.change_rate,
            quote_source=quote_source or position.quote_source or str(cached.get("quoteSource") or ""),
            quote_status=quote_status or position.quote_status or str(cached.get("quoteStatus") or ""),
            quote_message=quote_message or position.quote_message or str(cached.get("quoteMessage") or ""),
            data_quality=data_quality or position.data_quality or str(cached.get("dataQuality") or ""),
            updated_at=updated_at or position.updated_at or str(cached.get("updatedAt") or ""),
            currency=str(quote.get("currency") or position.currency or cached.get("currency") or ""),
            market=str(quote.get("market") or position.market or cached.get("market") or ""),
            market_value=market_value,
            trading_value=trading_value,
            volume=volume,
            volume_ratio=volume_ratio,
            ma5=number(indicator_source.get("ma5")) or position.ma5,
            ma20=ma20,
            ma60=ma60,
            ma120=number(indicator_source.get("ma120")) or position.ma120,
            ma200=number(indicator_source.get("ma200")) or position.ma200,
            ma20_slope=number(indicator_source.get("ma20Slope")) or position.ma20_slope,
            ma60_slope=number(indicator_source.get("ma60Slope")) or position.ma60_slope,
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
                indicators = technical_indicators_from_candles(candles)
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
            for symbol in self.account.watchlist_symbols[:30]
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
                indicators = technical_indicators_from_candles(candles)
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
    provider = TossProvider(account)
    mode, status, positions, cash, currency, watchlist = provider.fetch_positions()
    kis_provider = KISMarketSignalProvider()
    positions, watchlist = kis_provider.enrich_collections(positions, watchlist)
    external_signals = ExternalSignalProvider(settings=settings).signals_for_positions(positions + watchlist)
    portfolio = portfolio_summary(positions, cash, currency, currency_rates_from_external_signals(settings, external_signals))
    decisions = decisions_for_positions(
        positions,
        portfolio,
        external_signals=external_signals,
        require_inference_context=True,
    )
    metadata = provider.diagnostics_payload()
    metadata.update(kis_provider.diagnostics_payload())
    return AccountSnapshot(
        account_id=account.account_id,
        account_label=account.label,
        provider=account.provider,
        mode=mode,
        status=status,
        generated_at=utc_now_iso(),
        portfolio=portfolio,
        positions=positions,
        decisions=decisions,
        external_signals=external_signals,
        watchlist=watchlist,
        metadata=metadata,
    )
