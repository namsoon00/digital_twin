import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from ..domain.market_data import known_stock, number, pct_distance
from ..domain.portfolio import Position, utc_now_iso
from .settings import runtime_settings
from .sqlite_monitoring import SQLiteMarketQuoteCache


KST = timezone(timedelta(hours=9))
KIS_CACHE_PROVIDER = "kis"
KIS_CACHE_ACCOUNT_ID = "__market_signals__"
PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
CCNL_PATH = "/uapi/domestic-stock/v1/quotations/inquire-ccnl"
INVESTOR_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor"
ORDERBOOK_PATH = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
DETAIL_SIGNAL_KEYS = [
    "tradeStrength",
    "buyVolume",
    "sellVolume",
    "foreignBuyVolume",
    "foreignSellVolume",
    "foreignNetVolume",
    "foreignNetAmount",
    "institutionBuyVolume",
    "institutionSellVolume",
    "institutionNetVolume",
    "institutionNetAmount",
    "individualBuyVolume",
    "individualSellVolume",
    "individualNetVolume",
    "individualNetAmount",
    "orderbookBidVolume",
    "orderbookAskVolume",
    "bidAskImbalance",
]
INVESTOR_SIGNAL_KEYS = [
    "foreignBuyVolume",
    "foreignSellVolume",
    "foreignNetVolume",
    "foreignNetAmount",
    "institutionBuyVolume",
    "institutionSellVolume",
    "institutionNetVolume",
    "institutionNetAmount",
    "individualBuyVolume",
    "individualSellVolume",
    "individualNetVolume",
    "individualNetAmount",
]

JsonFetcher = Callable[[str, str, Dict[str, str], Optional[Dict[str, object]], Optional[Dict[str, str]], int], Dict[str, object]]


def kis_http_json(
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Optional[Dict[str, object]] = None,
    query: Optional[Dict[str, str]] = None,
    timeout: int = 12,
) -> Dict[str, object]:
    target = url
    if query:
        target += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(target, data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def http_error_text(error: Exception) -> str:
    if isinstance(error, KISAPIError):
        return error.message
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


def parse_iso(value: object):
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def clean_symbol(symbol: object) -> str:
    text = str(symbol or "").upper().strip()
    if text.startswith("A") and text[1:].isdigit() and len(text[1:]) == 6:
        return text[1:]
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    return text


def is_kr_equity_symbol(position: Position) -> bool:
    symbol = clean_symbol(position.symbol)
    if not (symbol.isdigit() and len(symbol) == 6):
        return False
    market = str(position.market or "").upper()
    currency = str(position.currency or "").upper()
    return not market or market in {"KR", "KOSPI", "KOSDAQ", "KONEX"} or currency == "KRW"


def optional_number(payload: Dict[str, object], keys: Iterable[str]):
    for key in keys:
        if key in payload and payload.get(key) not in (None, ""):
            return number(payload.get(key))
    return None


def merge_if_present(target: Dict[str, object], source: Dict[str, object]) -> Dict[str, object]:
    for key, value in source.items():
        if value not in (None, ""):
            target[key] = value
    return target


def append_source(existing: str, addition: str) -> str:
    base = str(existing or "").strip()
    extra = str(addition or "").strip()
    if not extra:
        return base
    if not base:
        return extra
    if extra in base:
        return base
    return base + " + " + extra


def append_message(existing: str, addition: str) -> str:
    base = str(existing or "").strip()
    extra = str(addition or "").strip()
    if not extra:
        return base
    if not base:
        return extra
    if extra in base:
        return base
    return base + " " + extra


class KISAPIError(RuntimeError):
    def __init__(self, stage: str, error: Exception):
        self.stage = str(stage or "")
        self.original_error = error
        self.http_status = int(getattr(error, "code", 0) or 0)
        self.message = "KIS " + self.stage + " 단계 실패 · " + http_error_text(error)
        super().__init__(self.message)


class KISMarketSignalProvider:
    def __init__(
        self,
        settings: Dict[str, str] = None,
        quote_cache: SQLiteMarketQuoteCache = None,
        fetch_json: JsonFetcher = None,
        sleep: Callable[[float], None] = None,
        now_provider: Callable[[], datetime] = None,
    ):
        self.settings = settings or runtime_settings()
        self.quote_cache = quote_cache if quote_cache is not None else SQLiteMarketQuoteCache()
        self.fetch_json = fetch_json or kis_http_json
        self.sleep = sleep or time.sleep
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.base_url = str(self.settings.get("kisBaseUrl") or "").rstrip("/")
        self.app_key = str(self.settings.get("kisAppKey") or "").strip()
        self.app_secret = str(self.settings.get("kisAppSecret") or "").strip()
        self.token = ""
        self.last_request_monotonic = 0.0
        self.consecutive_failures = 0
        self.circuit_open_until: Optional[datetime] = None
        self.diagnostics: Dict[str, object] = {
            "enabled": self.enabled(),
            "configured": self.configured(),
            "symbols": [],
            "live": 0,
            "cached": 0,
            "skipped": 0,
            "failures": [],
            "circuitOpen": False,
        }

    def diagnostics_payload(self) -> Dict[str, object]:
        return {"kis": dict(self.diagnostics)}

    def enabled(self) -> bool:
        value = str(self.settings.get("kisMarketSignalsEnabled") or "1").strip().lower()
        return value not in {"0", "false", "no", "off", "disabled"}

    def configured(self) -> bool:
        return bool(self.base_url and self.app_key and self.app_secret)

    def int_setting(self, key: str, fallback: int, minimum: int = 0, maximum: int = 100000) -> int:
        try:
            parsed = int(float(str(self.settings.get(key) or "").strip()))
        except ValueError:
            parsed = fallback
        return max(minimum, min(maximum, parsed))

    def float_setting(self, key: str, fallback: float, minimum: float = 0.0, maximum: float = 60.0) -> float:
        try:
            parsed = float(str(self.settings.get(key) or "").strip())
        except ValueError:
            parsed = fallback
        return max(minimum, min(maximum, parsed))

    def cache_minutes(self) -> int:
        return self.int_setting("kisMarketSignalCacheMinutes", 10, 1, 1440)

    def bool_setting(self, key: str, fallback: bool = False) -> bool:
        value = self.settings.get(key)
        if value in (None, ""):
            return fallback
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def prefer_live_during_market_hours(self) -> bool:
        return self.bool_setting("kisMarketSignalPreferLiveDuringMarketHours", True)

    def live_refresh_seconds(self) -> int:
        return self.int_setting("kisMarketSignalLiveRefreshSeconds", 60, 0, 3600)

    def now(self) -> datetime:
        value = self.now_provider()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def is_kr_regular_market_hours(self) -> bool:
        now = self.now().astimezone(KST)
        if now.weekday() >= 5:
            return False
        minutes = now.hour * 60 + now.minute
        return 9 * 60 <= minutes <= 15 * 60 + 30

    def max_symbols(self) -> int:
        return self.int_setting("kisMarketSignalMaxSymbols", 20, 1, 200)

    def request_gap_seconds(self) -> float:
        return self.float_setting("kisMarketSignalGapSeconds", 0.35, 0.0, 10.0)

    def retry_attempts(self) -> int:
        return self.int_setting("externalApiRetryAttempts", 2, 1, 5)

    def circuit_failure_threshold(self) -> int:
        return self.int_setting("externalApiCircuitFailures", 2, 1, 20)

    def circuit_cooldown_minutes(self) -> int:
        return self.int_setting("externalApiCircuitCooldownMinutes", 30, 1, 1440)

    def throttle(self) -> None:
        gap = self.request_gap_seconds()
        if not gap or not self.last_request_monotonic:
            return
        elapsed = time.monotonic() - self.last_request_monotonic
        if elapsed < gap:
            self.sleep(gap - elapsed)

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def record_failure(self, stage: str, symbol: str, error: Exception) -> None:
        failures = self.diagnostics.get("failures")
        if not isinstance(failures, list):
            failures = []
            self.diagnostics["failures"] = failures
        failures.append({
            "stage": str(stage or ""),
            "symbol": str(symbol or ""),
            "message": http_error_text(error),
        })
        del failures[:-8]
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.circuit_failure_threshold():
            self.circuit_open_until = datetime.now(timezone.utc) + timedelta(minutes=self.circuit_cooldown_minutes())
            self.diagnostics["circuitOpen"] = True

    def circuit_open(self) -> bool:
        return bool(self.circuit_open_until and self.circuit_open_until > datetime.now(timezone.utc))

    def request(
        self,
        stage: str,
        method: str,
        path: str,
        headers: Dict[str, str],
        body: Dict[str, object] = None,
        query: Dict[str, str] = None,
    ) -> Dict[str, object]:
        last_error: Exception = RuntimeError("unknown error")
        for attempt in range(self.retry_attempts()):
            try:
                self.throttle()
                try:
                    payload = self.fetch_json(method, self.base_url + path, headers, body, query, 12)
                finally:
                    self.last_request_monotonic = time.monotonic()
                if not isinstance(payload, dict):
                    raise RuntimeError("KIS 응답 형식 오류")
                if str(payload.get("rt_cd") or "0") not in {"0", ""}:
                    raise RuntimeError(str(payload.get("msg_cd") or "") + " " + str(payload.get("msg1") or "").strip())
                self.record_success()
                return payload
            except Exception as error:  # noqa: BLE001 - adapter normalizes vendor failures.
                last_error = error
                if attempt + 1 >= self.retry_attempts() or not retryable_http_error(error):
                    break
                self.sleep(0.45 * (attempt + 1))
        raise KISAPIError(stage, last_error)

    def token_headers(self) -> Dict[str, str]:
        return {"content-type": "application/json; charset=utf-8"}

    def auth_headers(self, tr_id: str) -> Dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": "Bearer " + self.token,
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "custtype": "P",
            "tr_id": tr_id,
        }

    def fetch_access_token(self) -> str:
        if self.token:
            return self.token
        payload = self.request(
            "token",
            "POST",
            "/oauth2/tokenP",
            self.token_headers(),
            body={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
        )
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("KIS access_token이 없습니다.")
        self.token = token
        return token

    def cached_signal(self, symbol: str) -> Dict[str, object]:
        try:
            return self.quote_cache.load(KIS_CACHE_PROVIDER, KIS_CACHE_ACCOUNT_ID, symbol)
        except Exception:
            return {}

    def is_cache_fresh(self, payload: Dict[str, object]) -> bool:
        if not payload:
            return False
        updated_at = parse_iso(payload.get("updatedAt"))
        if not updated_at:
            return False
        return self.now() - updated_at < timedelta(minutes=self.cache_minutes())

    def cache_age_seconds(self, payload: Dict[str, object]) -> Optional[float]:
        updated_at = parse_iso(payload.get("updatedAt") if payload else "")
        if not updated_at:
            return None
        return max(0.0, (self.now() - updated_at).total_seconds())

    def is_signal_complete(self, payload: Dict[str, object]) -> bool:
        if not payload:
            return False
        has_detail = any(key in payload and payload.get(key) not in (None, "") for key in DETAIL_SIGNAL_KEYS)
        has_investor = any(key in payload and payload.get(key) not in (None, "") for key in INVESTOR_SIGNAL_KEYS)
        return has_detail and has_investor

    def should_use_cached_signal(self, payload: Dict[str, object]) -> bool:
        if not self.is_cache_fresh(payload) or not self.is_signal_complete(payload):
            return False
        if not (self.configured() and self.prefer_live_during_market_hours() and self.is_kr_regular_market_hours()):
            return True
        age_seconds = self.cache_age_seconds(payload)
        return age_seconds is not None and age_seconds <= self.live_refresh_seconds()

    def save_signal(self, symbol: str, payload: Dict[str, object]) -> None:
        try:
            self.quote_cache.save(KIS_CACHE_PROVIDER, KIS_CACHE_ACCOUNT_ID, symbol, payload)
        except Exception:
            return

    def query(self, symbol: str) -> Dict[str, str]:
        return {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
        }

    def fetch_stage(self, symbol: str, stage: str, path: str, tr_id: str):
        if self.circuit_open():
            return None
        try:
            payload = self.request(stage, "GET", path, self.auth_headers(tr_id), query=self.query(symbol))
            return payload.get("output") or payload.get("output1") or payload.get("output2")
        except KISAPIError as error:
            self.record_failure(stage, symbol, error)
            return None

    def fetch_symbol_signal(self, symbol: str) -> Dict[str, object]:
        self.fetch_access_token()
        price = self.fetch_stage(symbol, "price", PRICE_PATH, "FHKST01010100")
        ccnl = self.fetch_stage(symbol, "ccnl", CCNL_PATH, "FHKST01010300")
        investor = self.fetch_stage(symbol, "investor", INVESTOR_PATH, "FHKST01010900")
        orderbook = self.fetch_stage(symbol, "orderbook", ORDERBOOK_PATH, "FHKST01010200")

        signal: Dict[str, object] = {}
        if isinstance(price, dict):
            merge_if_present(signal, normalize_price(symbol, price))
        if isinstance(ccnl, list):
            merge_if_present(signal, normalize_ccnl(ccnl))
        if isinstance(investor, list):
            merge_if_present(signal, normalize_investor(investor))
        elif isinstance(investor, dict):
            merge_if_present(signal, normalize_investor([investor]))
        if isinstance(orderbook, dict):
            merge_if_present(signal, normalize_orderbook(orderbook))
        if not signal:
            raise RuntimeError("KIS 수급 응답 없음")

        info = known_stock(symbol)
        signal.setdefault("symbol", symbol)
        signal.setdefault("name", info["name"])
        signal.setdefault("market", "KR")
        signal.setdefault("currency", "KRW")
        signal["quoteSource"] = "KIS Open API"
        included = []
        if signal.get("currentPrice"):
            included.append("현재가")
        if signal.get("tradeStrength"):
            included.append("체결강도")
        if signal.get("buyVolume") is not None or signal.get("sellVolume") is not None:
            included.append("방향별 체결량")
        if any(signal.get(key) is not None for key in [
            "foreignBuyVolume",
            "foreignSellVolume",
            "foreignNetVolume",
            "foreignNetAmount",
            "institutionBuyVolume",
            "institutionSellVolume",
            "individualBuyVolume",
            "individualSellVolume",
            "institutionNetVolume",
            "individualNetVolume",
            "institutionNetAmount",
            "individualNetAmount",
        ]):
            included.append("투자자별 수급")
        if signal.get("orderbookBidVolume") is not None or signal.get("orderbookAskVolume") is not None:
            included.append("호가 잔량")
        included_text = ", ".join(included) if included else "응답 데이터"
        signal["quoteStatus"] = "KIS " + included_text + " 반영"
        signal["quoteMessage"] = "KIS " + included_text + "을 모델링 데이터에 반영했습니다."
        signal["dataQuality"] = "actual"
        signal["updatedAt"] = utc_now_iso()
        return signal

    def symbols_for_positions(self, positions: Iterable[Position]) -> List[str]:
        symbols: List[str] = []
        for position in positions:
            if position.is_cash() or not is_kr_equity_symbol(position):
                continue
            symbol = clean_symbol(position.symbol)
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        return symbols[:self.max_symbols()]

    def signals_for_positions(self, positions: Iterable[Position]) -> Dict[str, Dict[str, object]]:
        position_list = list(positions)
        symbols = self.symbols_for_positions(position_list)
        self.diagnostics["symbols"] = symbols
        if not self.enabled() or not symbols:
            self.diagnostics["skipped"] = len(symbols)
            return {}

        signals: Dict[str, Dict[str, object]] = {}
        stale_symbols: List[Tuple[str, Dict[str, object]]] = []
        for symbol in symbols:
            cached = self.cached_signal(symbol)
            if self.should_use_cached_signal(cached):
                cached = dict(cached)
                cached["dataQuality"] = cached.get("dataQuality") or "cached"
                signals[symbol] = cached
                self.diagnostics["cached"] = int(self.diagnostics.get("cached") or 0) + 1
            else:
                if self.is_cache_fresh(cached) and cached:
                    if self.is_signal_complete(cached) and self.configured() and self.prefer_live_during_market_hours() and self.is_kr_regular_market_hours():
                        self.diagnostics["livePreferred"] = int(self.diagnostics.get("livePreferred") or 0) + 1
                    self.diagnostics["partialCached"] = int(self.diagnostics.get("partialCached") or 0) + 1
                stale_symbols.append((symbol, cached))

        if not stale_symbols or not self.configured():
            if stale_symbols and not self.configured():
                for symbol, cached in stale_symbols:
                    if cached:
                        cached = dict(cached)
                        cached["dataQuality"] = "cached"
                        signals[symbol] = cached
                        self.diagnostics["cached"] = int(self.diagnostics.get("cached") or 0) + 1
                    else:
                        self.diagnostics["skipped"] = int(self.diagnostics.get("skipped") or 0) + 1
            return signals

        for symbol, cached in stale_symbols:
            if self.circuit_open():
                if cached:
                    cached = dict(cached)
                    cached["dataQuality"] = "cached"
                    signals[symbol] = cached
                    self.diagnostics["cached"] = int(self.diagnostics.get("cached") or 0) + 1
                else:
                    self.diagnostics["skipped"] = int(self.diagnostics.get("skipped") or 0) + 1
                continue
            try:
                signal = self.fetch_symbol_signal(symbol)
                signals[symbol] = signal
                self.save_signal(symbol, signal)
                self.diagnostics["live"] = int(self.diagnostics.get("live") or 0) + 1
            except Exception as error:  # noqa: BLE001 - use cached signal if live KIS fails.
                self.record_failure("symbol", symbol, error)
                if cached:
                    cached = dict(cached)
                    cached["dataQuality"] = "cached"
                    signals[symbol] = cached
                    self.diagnostics["cached"] = int(self.diagnostics.get("cached") or 0) + 1
                else:
                    self.diagnostics["skipped"] = int(self.diagnostics.get("skipped") or 0) + 1
        return signals

    def enrich_collections(self, positions: List[Position], watchlist: List[Position]) -> Tuple[List[Position], List[Position]]:
        all_positions = list(positions or []) + list(watchlist or [])
        signals = self.signals_for_positions(all_positions)
        if not signals:
            return positions, watchlist
        return (
            [self.merge_position(item, signals.get(clean_symbol(item.symbol))) for item in positions],
            [self.merge_position(item, signals.get(clean_symbol(item.symbol))) for item in watchlist],
        )

    def merge_position(self, position: Position, signal: Dict[str, object] = None) -> Position:
        if not signal:
            return position

        current_price = optional_number(signal, ["currentPrice", "lastPrice", "price"])
        change_rate = optional_number(signal, ["changeRate", "priceChangeRate", "changePercent"])
        volume = optional_number(signal, ["volume", "tradingVolume"])
        volume_ratio = optional_number(signal, ["volumeRatio", "relativeVolume"])
        trading_value = optional_number(signal, ["tradingValue", "tradeValue", "tradingAmount"])
        trade_strength = optional_number(signal, ["tradeStrength", "executionStrength"])
        buy_volume = optional_number(signal, ["buyVolume"])
        sell_volume = optional_number(signal, ["sellVolume"])
        orderbook_bid_volume = optional_number(signal, ["orderbookBidVolume"])
        orderbook_ask_volume = optional_number(signal, ["orderbookAskVolume"])
        bid_ask_imbalance = optional_number(signal, ["bidAskImbalance"])
        foreign_buy_volume = optional_number(signal, ["foreignBuyVolume"])
        foreign_sell_volume = optional_number(signal, ["foreignSellVolume"])
        foreign_net_volume = optional_number(signal, ["foreignNetVolume", "foreignNet"])
        foreign_net_amount = optional_number(signal, ["foreignNetAmount"])
        institution_buy_volume = optional_number(signal, ["institutionBuyVolume"])
        institution_sell_volume = optional_number(signal, ["institutionSellVolume"])
        institution_net_volume = optional_number(signal, ["institutionNetVolume", "institutionNet"])
        institution_net_amount = optional_number(signal, ["institutionNetAmount"])
        individual_buy_volume = optional_number(signal, ["individualBuyVolume"])
        individual_sell_volume = optional_number(signal, ["individualSellVolume"])
        individual_net_volume = optional_number(signal, ["individualNetVolume", "individualNet"])
        individual_net_amount = optional_number(signal, ["individualNetAmount"])

        merged_price = current_price if current_price is not None else position.current_price
        market_value = position.market_value
        if not market_value and position.quantity and merged_price:
            market_value = position.quantity * merged_price

        data_quality = str(signal.get("dataQuality") or "")
        if position.data_quality == "actual" and data_quality == "cached":
            data_quality = position.data_quality
        ma20_distance = pct_distance(merged_price, position.ma20) if merged_price and position.ma20 else position.ma20_distance
        ma60_distance = pct_distance(merged_price, position.ma60) if merged_price and position.ma60 else position.ma60_distance

        return replace(
            position,
            symbol=clean_symbol(position.symbol) or position.symbol,
            name=str(signal.get("name") or position.name),
            market=str(signal.get("market") or position.market or "KR"),
            currency=str(signal.get("currency") or position.currency or "KRW"),
            current_price=merged_price,
            change_rate=change_rate if change_rate is not None else position.change_rate,
            quote_source=append_source(position.quote_source, str(signal.get("quoteSource") or "")),
            quote_status=str(signal.get("quoteStatus") or position.quote_status),
            quote_message=append_message(position.quote_message, str(signal.get("quoteMessage") or "")),
            data_quality=data_quality or position.data_quality,
            updated_at=str(signal.get("updatedAt") or position.updated_at),
            market_value=market_value,
            trade_strength=trade_strength if trade_strength is not None else position.trade_strength,
            trading_value=trading_value if trading_value is not None else position.trading_value,
            volume=volume if volume is not None else position.volume,
            volume_ratio=volume_ratio if volume_ratio is not None else position.volume_ratio,
            buy_volume=buy_volume if buy_volume is not None else position.buy_volume,
            sell_volume=sell_volume if sell_volume is not None else position.sell_volume,
            orderbook_bid_volume=orderbook_bid_volume if orderbook_bid_volume is not None else position.orderbook_bid_volume,
            orderbook_ask_volume=orderbook_ask_volume if orderbook_ask_volume is not None else position.orderbook_ask_volume,
            bid_ask_imbalance=bid_ask_imbalance if bid_ask_imbalance is not None else position.bid_ask_imbalance,
            foreign_buy_volume=foreign_buy_volume if foreign_buy_volume is not None else position.foreign_buy_volume,
            foreign_sell_volume=foreign_sell_volume if foreign_sell_volume is not None else position.foreign_sell_volume,
            foreign_net_volume=foreign_net_volume if foreign_net_volume is not None else position.foreign_net_volume,
            foreign_net_amount=foreign_net_amount if foreign_net_amount is not None else position.foreign_net_amount,
            institution_buy_volume=institution_buy_volume if institution_buy_volume is not None else position.institution_buy_volume,
            institution_sell_volume=institution_sell_volume if institution_sell_volume is not None else position.institution_sell_volume,
            institution_net_volume=institution_net_volume if institution_net_volume is not None else position.institution_net_volume,
            institution_net_amount=institution_net_amount if institution_net_amount is not None else position.institution_net_amount,
            individual_buy_volume=individual_buy_volume if individual_buy_volume is not None else position.individual_buy_volume,
            individual_sell_volume=individual_sell_volume if individual_sell_volume is not None else position.individual_sell_volume,
            individual_net_volume=individual_net_volume if individual_net_volume is not None else position.individual_net_volume,
            individual_net_amount=individual_net_amount if individual_net_amount is not None else position.individual_net_amount,
            ma20_distance=ma20_distance,
            ma60_distance=ma60_distance,
        )


def normalize_price(symbol: str, payload: Dict[str, object]) -> Dict[str, object]:
    info = known_stock(symbol)
    current_price = number(payload.get("stck_prpr"))
    volume = number(payload.get("acml_vol"))
    trading_value = number(payload.get("acml_tr_pbmn"))
    if not trading_value and current_price and volume:
        trading_value = current_price * volume
    volume_ratio_raw = optional_number(payload, ["prdy_vrss_vol_rate"])
    return {
        "symbol": symbol,
        "name": str(payload.get("hts_kor_isnm") or info["name"]),
        "market": "KR",
        "currency": "KRW",
        "currentPrice": current_price,
        "changeRate": optional_number(payload, ["prdy_ctrt"]),
        "volume": volume,
        "volumeRatio": volume_ratio_raw / 100 if volume_ratio_raw is not None else None,
        "tradingValue": trading_value,
        "foreignNetVolume": optional_number(payload, ["frgn_ntby_qty"]),
    }


def normalize_ccnl(items: List[Dict[str, object]]) -> Dict[str, object]:
    if not items:
        return {}
    latest = items[0] if isinstance(items[0], dict) else {}
    signal: Dict[str, object] = {
        "tradeStrength": optional_number(latest, ["tday_rltv"]),
        "currentPrice": optional_number(latest, ["stck_prpr"]),
        "changeRate": optional_number(latest, ["prdy_ctrt"]),
    }
    aggregate_buy = optional_number(latest, [
        "total_shnu_qty",
        "shnu_cntg_smtn",
        "shnu_cntg_qty",
        "tday_shnu_vol",
        "tday_buy_vol",
    ])
    aggregate_sell = optional_number(latest, [
        "total_seln_qty",
        "seln_cntg_smtn",
        "seln_cntg_qty",
        "tday_seln_vol",
        "tday_sell_vol",
    ])
    if aggregate_buy is not None and aggregate_sell is not None:
        signal["buyVolume"] = aggregate_buy
        signal["sellVolume"] = aggregate_sell
        return signal
    buy_volume = 0.0
    sell_volume = 0.0
    has_buy = False
    has_sell = False
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("cntg_vol") or "").strip()
        value = abs(number(raw))
        if not value:
            continue
        if raw.startswith("-"):
            has_sell = True
            sell_volume += value
        else:
            has_buy = True
            buy_volume += value
    if has_buy and has_sell:
        signal["buyVolume"] = buy_volume
        signal["sellVolume"] = sell_volume
    return signal


def normalize_investor(items: List[Dict[str, object]]) -> Dict[str, object]:
    if not items:
        return {}
    latest = items[0] if isinstance(items[0], dict) else {}
    foreign_buy = optional_number(latest, ["frgn_shnu_vol"])
    foreign_sell = optional_number(latest, ["frgn_seln_vol"])
    foreign_net = optional_number(latest, ["frgn_ntby_qty"])
    foreign_net_amount = optional_number(latest, ["frgn_ntby_tr_pbmn"])
    institution_buy = optional_number(latest, ["orgn_shnu_vol"])
    institution_sell = optional_number(latest, ["orgn_seln_vol"])
    institution_net = optional_number(latest, ["orgn_ntby_qty"])
    institution_net_amount = optional_number(latest, ["orgn_ntby_tr_pbmn"])
    individual_buy = optional_number(latest, ["prsn_shnu_vol"])
    individual_sell = optional_number(latest, ["prsn_seln_vol"])
    individual_net = optional_number(latest, ["prsn_ntby_qty"])
    individual_net_amount = optional_number(latest, ["prsn_ntby_tr_pbmn"])
    return {
        "foreignBuyVolume": foreign_buy,
        "foreignSellVolume": foreign_sell,
        "foreignNetVolume": foreign_net if foreign_net is not None else (
            foreign_buy - foreign_sell if foreign_buy is not None and foreign_sell is not None else None
        ),
        "foreignNetAmount": foreign_net_amount,
        "institutionBuyVolume": institution_buy,
        "institutionSellVolume": institution_sell,
        "institutionNetVolume": institution_net if institution_net is not None else (
            institution_buy - institution_sell if institution_buy is not None and institution_sell is not None else None
        ),
        "institutionNetAmount": institution_net_amount,
        "individualBuyVolume": individual_buy,
        "individualSellVolume": individual_sell,
        "individualNetVolume": individual_net if individual_net is not None else (
            individual_buy - individual_sell if individual_buy is not None and individual_sell is not None else None
        ),
        "individualNetAmount": individual_net_amount,
    }


def normalize_orderbook(payload: Dict[str, object]) -> Dict[str, object]:
    bid_volume = optional_number(payload, ["total_bidp_rsqn"])
    ask_volume = optional_number(payload, ["total_askp_rsqn"])
    base = number(bid_volume) + number(ask_volume)
    imbalance = ((number(bid_volume) - number(ask_volume)) / base) * 100 if base else None
    return {
        "orderbookBidVolume": bid_volume,
        "orderbookAskVolume": ask_volume,
        "bidAskImbalance": imbalance,
    }
