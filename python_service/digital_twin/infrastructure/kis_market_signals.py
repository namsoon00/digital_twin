import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from ..domain.data_freshness import combine_quality
from ..domain.market_data import known_stock, number, pct_distance
from ..domain.portfolio import Position, utc_now_iso
from .external_signal_utils import ExternalCircuitOpen, root_api_error
from .operational_store import market_quote_cache
from .settings import runtime_settings


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
MICROSTRUCTURE_SIGNAL_KEYS = [
    "tradeStrength",
    "buyVolume",
    "sellVolume",
    "orderbookBidVolume",
    "orderbookAskVolume",
    "bidAskImbalance",
    *INVESTOR_SIGNAL_KEYS,
]
INVESTOR_RAW_KEYS = [
    "frgn_shnu_vol",
    "frgn_seln_vol",
    "frgn_ntby_qty",
    "frgn_ntby_tr_pbmn",
    "orgn_shnu_vol",
    "orgn_seln_vol",
    "orgn_ntby_qty",
    "orgn_ntby_tr_pbmn",
    "prsn_shnu_vol",
    "prsn_seln_vol",
    "prsn_ntby_qty",
    "prsn_ntby_tr_pbmn",
]
INVESTOR_DELAYED_LABEL = "KIS 투자자 수급 지연 가능"
INVESTOR_DELAYED_REASON = "KIS 투자자별 수급이 캐시·반복값·노후값으로 판정되어 실시간 근거로 쓰지 않습니다."
INVESTOR_REST_REFERENCE_REASON = "KIS 투자자별 수급은 REST 조회값이며 원천의 초단위 실시간 기준시각이 제공되지 않아 강한 판단 근거로 쓰지 않습니다."

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


def has_raw_value(payload: Dict[str, object], keys: Iterable[str]) -> bool:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip() != "":
            return True
    return False


def kst_business_date_iso(value: object, time_value: object = "") -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 8:
        return ""
    date = digits[:8]
    time_digits = "".join(ch for ch in str(time_value or "").strip() if ch.isdigit())
    if len(time_digits) >= 6:
        hhmmss = time_digits[:6]
    else:
        hhmmss = "000000"
    return (
        date[:4]
        + "-"
        + date[4:6]
        + "-"
        + date[6:8]
        + "T"
        + hhmmss[:2]
        + ":"
        + hhmmss[2:4]
        + ":"
        + hhmmss[4:6]
        + "+09:00"
    )


def stage_source_as_of(stage: str, raw_payload, fetched_at: str) -> Tuple[str, str]:
    if stage != "investor":
        return fetched_at, "provider-timestamp"
    rows = raw_payload if isinstance(raw_payload, list) else [raw_payload] if isinstance(raw_payload, dict) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_as_of = kst_business_date_iso(
            row.get("stck_bsop_date") or row.get("bsop_date"),
            row.get("stck_bsop_hour") or row.get("bsop_hour"),
        )
        if source_as_of:
            if source_as_of.endswith("00:00:00+09:00"):
                return source_as_of, "business-date-only"
            return source_as_of, "provider-timestamp"
    return fetched_at, "queried-at-fallback"


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


def stage_coverage(
    stage: str,
    raw_payload,
    normalized: Dict[str, object],
    keys: Iterable[str],
    fetched_at: str = "",
    source_as_of: str = "",
    session: Dict[str, object] = None,
    real_time: bool = False,
    transport: str = "rest",
    source_as_of_confidence: str = "",
    ai_usable_as_strong_evidence: Optional[bool] = None,
) -> Dict[str, object]:
    session = session or {}
    fields = sorted([
        key
        for key in keys
        if key in normalized and normalized.get(key) not in (None, "")
    ])
    non_zero_fields = sorted([key for key in fields if number(normalized.get(key))])
    if fields:
        status = "available"
    elif raw_payload is None:
        status = "missing"
    else:
        status = "empty"
    payload = {
        "stage": stage,
        "status": status,
        "fields": fields,
        "nonZeroFields": non_zero_fields,
    }
    if fetched_at:
        payload["fetchedAt"] = fetched_at
    if source_as_of:
        payload["sourceAsOf"] = source_as_of
    payload["transport"] = str(transport or "rest")
    if source_as_of_confidence:
        payload["sourceAsOfConfidence"] = str(source_as_of_confidence)
    if ai_usable_as_strong_evidence is not None:
        payload["aiUsableAsStrongEvidence"] = bool(ai_usable_as_strong_evidence)
    if payload["status"] == "available":
        if stage == "investor" and ai_usable_as_strong_evidence is False:
            payload["freshnessStatus"] = "reference-only"
        elif real_time:
            payload["freshnessStatus"] = "realtime"
        else:
            payload["freshnessStatus"] = "near-live"
    if session:
        payload["marketSession"] = str(session.get("key") or "")
        payload["marketSessionLabel"] = str(session.get("label") or "")
    if stage == "investor" and status == "available":
        payload["realTime"] = bool(real_time)
        payload["cadence"] = "live-poll" if real_time else "rest-reference"
        if not real_time:
            payload["latencyStatus"] = "delayed-or-batched"
            payload["latencyLabel"] = INVESTOR_DELAYED_LABEL
            payload["latencyReason"] = INVESTOR_REST_REFERENCE_REASON
            payload["aiUsableAsStrongEvidence"] = False
            payload["freshnessStatus"] = "reference-only"
    return payload


def unavailable_stage_coverage(stage: str, session: Dict[str, object]) -> Dict[str, object]:
    return {
        "stage": stage,
        "status": "unavailable",
        "fields": [],
        "nonZeroFields": [],
        "transport": "rest",
        "freshnessStatus": "unavailable",
        "aiUsableAsStrongEvidence": False,
        "reason": str(session.get("reason") or "정규장 시작 전이라 장중 수급 신호를 사용하지 않습니다."),
        "marketSession": str(session.get("key") or ""),
        "marketSessionLabel": str(session.get("label") or ""),
    }


def coverage_has_fields(coverage: Dict[str, object], stage: str, keys: Iterable[str]) -> bool:
    item = coverage.get(stage) if isinstance(coverage, dict) else {}
    if isinstance(item, dict):
        status = str(item.get("status") or "").strip()
        if status and status != "available":
            return False
    fields = item.get("fields") if isinstance(item, dict) else []
    return any(key in set(str(field) for field in fields or []) for key in keys)


def fresh_websocket_stage(payload: Dict[str, object], stage: str, max_age_seconds: int) -> bool:
    coverage = payload.get("marketSignalCoverage") if isinstance(payload.get("marketSignalCoverage"), dict) else {}
    item = coverage.get(stage) if isinstance(coverage, dict) and isinstance(coverage.get(stage), dict) else {}
    if not item or str(item.get("status") or "") != "available":
        return False
    if str(item.get("cadence") or "") != "websocket" and str(item.get("transport") or "") != "websocket":
        return False
    fetched_at = parse_iso(item.get("fetchedAt") or item.get("sourceAsOf") or payload.get("updatedAt"))
    if not fetched_at:
        return False
    return datetime.now(timezone.utc) - fetched_at <= timedelta(seconds=max(1, int(max_age_seconds or 1)))


def merge_fresh_websocket_stages(
    signal: Dict[str, object],
    cached: Dict[str, object],
    max_age_seconds: int,
) -> Dict[str, object]:
    if not signal or not cached:
        return signal
    merged = dict(signal)
    coverage = dict(merged.get("marketSignalCoverage") or {}) if isinstance(merged.get("marketSignalCoverage"), dict) else {}
    cached_coverage = cached.get("marketSignalCoverage") if isinstance(cached.get("marketSignalCoverage"), dict) else {}
    stage_keys = {
        "ccnl": ["currentPrice", "changeRate", "volume", "volumeRatio", "tradingValue", "tradeStrength", "buyVolume", "sellVolume"],
        "orderbook": ["orderbookBidVolume", "orderbookAskVolume", "bidAskImbalance"],
    }
    used = []
    for stage, keys in stage_keys.items():
        if not fresh_websocket_stage(cached, stage, max_age_seconds):
            continue
        for key in keys:
            value = cached.get(key)
            if value not in (None, ""):
                merged[key] = value
        if isinstance(cached_coverage.get(stage), dict):
            coverage[stage] = dict(cached_coverage.get(stage) or {})
        used.append(stage)
    if used:
        merged["marketSignalCoverage"] = coverage
        merged["quoteSource"] = append_source(merged.get("quoteSource"), "KIS WebSocket")
        merged["quoteStatus"] = "KIS 실시간 체결·호가 반영"
        merged["quoteMessage"] = append_message(
            merged.get("quoteMessage"),
            "신선한 KIS WebSocket 체결·호가를 REST 투자자 수급 참고값과 병합했습니다.",
        )
    return merged


def investor_stage_values_reliable(coverage: Dict[str, object]) -> bool:
    item = coverage.get("investor") if isinstance(coverage, dict) else {}
    if not isinstance(item, dict) or not item:
        return True
    status = str(item.get("status") or "").strip()
    latency_status = str(item.get("latencyStatus") or "").strip()
    if status in {"stale", "unknown", "unavailable", "missing", "empty"}:
        return False
    if item.get("aiUsableAsStrongEvidence") is False:
        return False
    if int(number(item.get("unchangedCount")) or 0) > 0:
        return False
    if item.get("realTime") is False or latency_status or str(item.get("cadence") or "") == "stale-repeat":
        return False
    return True


def remove_unreliable_investor_values(signal: Dict[str, object], strip_values: bool = False) -> Dict[str, object]:
    coverage = signal.get("marketSignalCoverage") if isinstance(signal.get("marketSignalCoverage"), dict) else {}
    investor = coverage.get("investor") if isinstance(coverage.get("investor"), dict) else {}
    if not investor or investor_stage_values_reliable(coverage):
        return signal
    if strip_values:
        for key in INVESTOR_SIGNAL_KEYS:
            signal.pop(key, None)
    reason = str(
        investor.get("latencyReason")
        or investor.get("staleReason")
        or investor.get("reason")
        or INVESTOR_DELAYED_REASON
    )
    signal["quoteMessage"] = append_message(
        signal.get("quoteMessage"),
        "KIS 투자자별 수급은 최신 실시간 값으로 확인되지 않아 수치 근거에서 제외했습니다. " + reason,
    )
    return signal


def comparable_stage_values(payload: Dict[str, object], keys: Iterable[str]) -> Dict[str, float]:
    return {
        key: number(payload.get(key))
        for key in keys
        if payload.get(key) not in (None, "") and number(payload.get(key)) is not None
    }


class KISAPIError(RuntimeError):
    def __init__(self, stage: str, error: Exception):
        self.stage = str(stage or "")
        self.original_error = root_api_error(error)
        self.http_status = int(getattr(error, "code", 0) or 0)
        if not self.http_status:
            self.http_status = int(getattr(self.original_error, "code", 0) or 0)
        self.message = "KIS " + self.stage + " 단계 실패 · " + http_error_text(self.original_error)
        super().__init__(self.message)


class KISMarketSignalProvider:
    def __init__(
        self,
        settings: Dict[str, str] = None,
        quote_cache=None,
        fetch_json: JsonFetcher = None,
        sleep: Callable[[float], None] = None,
        now_provider: Callable[[], datetime] = None,
    ):
        self.settings = settings or runtime_settings()
        self.quote_cache = quote_cache if quote_cache is not None else market_quote_cache(self.settings)
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
        return self.int_setting("kisMarketSignalCacheMinutes", 3, 1, 1440)

    def bool_setting(self, key: str, fallback: bool = False) -> bool:
        value = self.settings.get(key)
        if value in (None, ""):
            return fallback
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def prefer_live_during_market_hours(self) -> bool:
        return self.bool_setting("kisMarketSignalPreferLiveDuringMarketHours", True)

    def investor_realtime_enabled(self) -> bool:
        return self.bool_setting("kisInvestorRealtimeEnabled", True)

    def live_refresh_seconds(self) -> int:
        return self.int_setting("kisMarketSignalLiveRefreshSeconds", 60, 0, 3600)

    def unchanged_stale_count(self) -> int:
        return self.int_setting("kisMarketSignalUnchangedStaleCount", 3, 1, 20)

    def now(self) -> datetime:
        value = self.now_provider()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def is_kr_regular_market_hours(self) -> bool:
        return bool(self.kr_market_session().get("regular"))

    def kr_market_session(self) -> Dict[str, object]:
        now = self.now().astimezone(KST)
        if now.weekday() >= 5:
            return {
                "key": "closed",
                "label": "휴장",
                "regular": False,
                "microstructureAvailable": False,
                "reason": "국장 휴장일이라 장중 수급 신호를 사용하지 않습니다.",
            }
        minutes = now.hour * 60 + now.minute
        if minutes < 9 * 60:
            return {
                "key": "pre_open",
                "label": "장 시작 전",
                "regular": False,
                "microstructureAvailable": False,
                "reason": "정규장 시작 전이라 외국인·기관·개인 수급과 체결강도는 오늘 장중 데이터로 쓰지 않습니다.",
            }
        if minutes <= 15 * 60 + 30:
            return {
                "key": "regular",
                "label": "정규장",
                "regular": True,
                "microstructureAvailable": True,
                "reason": "정규장 장중 데이터",
            }
        return {
            "key": "post_close",
            "label": "장 마감 후",
            "regular": False,
            "microstructureAvailable": True,
            "reason": "장 마감 후 확정 또는 최신 수급 데이터",
        }

    def market_microstructure_available(self) -> bool:
        return bool(self.kr_market_session().get("microstructureAvailable"))

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
            self.diagnostics["circuitOpenUntil"] = self.circuit_open_until.isoformat().replace("+00:00", "Z")

    def record_circuit_skip(self, stage: str, symbol: str) -> None:
        failures = self.diagnostics.get("failures")
        if not isinstance(failures, list):
            failures = []
            self.diagnostics["failures"] = failures
        failures.append({
            "stage": str(stage or ""),
            "symbol": str(symbol or ""),
            "message": http_error_text(self.circuit_open_error()),
        })
        del failures[:-8]
        self.diagnostics["circuitOpen"] = True
        if self.circuit_open_until:
            self.diagnostics["circuitOpenUntil"] = self.circuit_open_until.isoformat().replace("+00:00", "Z")

    def circuit_open(self) -> bool:
        return bool(self.circuit_open_until and self.circuit_open_until > datetime.now(timezone.utc))

    def circuit_open_error(self) -> ExternalCircuitOpen:
        opened_until = self.circuit_open_until.isoformat().replace("+00:00", "Z") if self.circuit_open_until else ""
        return ExternalCircuitOpen("circuit open until " + opened_until if opened_until else "circuit open")

    def request(
        self,
        stage: str,
        method: str,
        path: str,
        headers: Dict[str, str],
        body: Dict[str, object] = None,
        query: Dict[str, str] = None,
    ) -> Dict[str, object]:
        if self.circuit_open():
            raise KISAPIError(stage, self.circuit_open_error())
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
        coverage = payload.get("marketSignalCoverage")
        if isinstance(coverage, dict):
            has_detail = (
                coverage_has_fields(coverage, "ccnl", ["tradeStrength", "buyVolume", "sellVolume"])
                or coverage_has_fields(coverage, "orderbook", ["orderbookBidVolume", "orderbookAskVolume", "bidAskImbalance"])
            )
            has_investor = coverage_has_fields(coverage, "investor", INVESTOR_SIGNAL_KEYS)
            return has_detail and has_investor
        has_detail = any(key in payload and payload.get(key) not in (None, "") for key in DETAIL_SIGNAL_KEYS)
        has_investor = any(key in payload and payload.get(key) not in (None, "") for key in INVESTOR_SIGNAL_KEYS)
        return has_detail and has_investor

    def should_use_cached_signal(self, payload: Dict[str, object]) -> bool:
        if not self.is_cache_fresh(payload) or not self.is_signal_complete(payload):
            return False
        if not (self.configured() and self.prefer_live_during_market_hours() and self.is_kr_regular_market_hours()):
            return True
        if self.investor_realtime_enabled():
            return False
        age_seconds = self.cache_age_seconds(payload)
        return age_seconds is not None and age_seconds <= self.live_refresh_seconds()

    def signal_for_current_session(self, payload: Dict[str, object]) -> Dict[str, object]:
        signal = dict(payload or {})
        session = self.kr_market_session()
        signal["marketSession"] = str(session.get("key") or "")
        signal["marketSessionLabel"] = str(session.get("label") or "")
        if session.get("microstructureAvailable"):
            return signal
        for key in MICROSTRUCTURE_SIGNAL_KEYS:
            signal.pop(key, None)
        coverage = signal.get("marketSignalCoverage") if isinstance(signal.get("marketSignalCoverage"), dict) else {}
        coverage = dict(coverage or {})
        for stage in ["ccnl", "investor", "orderbook"]:
            coverage[stage] = unavailable_stage_coverage(stage, session)
        signal["marketSignalCoverage"] = coverage
        signal["quoteStatus"] = "KIS 현재가 반영"
        signal["quoteMessage"] = str(session.get("reason") or "정규장 시작 전이라 장중 수급 신호를 제외했습니다.")
        return signal

    def mark_unchanged_stage_health(self, signal: Dict[str, object], previous: Dict[str, object] = None) -> Dict[str, object]:
        if not signal or not previous or not self.is_kr_regular_market_hours():
            return signal
        coverage = signal.get("marketSignalCoverage") if isinstance(signal.get("marketSignalCoverage"), dict) else {}
        previous_coverage = previous.get("marketSignalCoverage") if isinstance(previous.get("marketSignalCoverage"), dict) else {}
        if not isinstance(coverage, dict):
            return signal
        coverage = dict(coverage)
        stage_keys = {
            "price": ["currentPrice", "volume", "tradingValue"],
            "ccnl": ["tradeStrength", "buyVolume", "sellVolume"],
            "investor": INVESTOR_SIGNAL_KEYS,
            "orderbook": ["orderbookBidVolume", "orderbookAskVolume", "bidAskImbalance"],
        }
        stale_threshold = self.unchanged_stale_count()
        for stage, keys in stage_keys.items():
            item = coverage.get(stage) if isinstance(coverage.get(stage), dict) else {}
            if str(item.get("status") or "") != "available":
                continue
            current_values = comparable_stage_values(signal, keys)
            previous_values = comparable_stage_values(previous, keys)
            if not current_values or not previous_values:
                continue
            comparable_keys = set(current_values).intersection(previous_values)
            if not comparable_keys:
                continue
            unchanged = all(abs(float(current_values[key]) - float(previous_values[key])) < 0.000001 for key in comparable_keys)
            next_item = dict(item)
            if unchanged:
                previous_item = previous_coverage.get(stage) if isinstance(previous_coverage, dict) and isinstance(previous_coverage.get(stage), dict) else {}
                unchanged_count = int(previous_item.get("unchangedCount") or 0) + 1
                next_item["unchangedCount"] = unchanged_count
                next_item["unchangedFields"] = sorted(comparable_keys)
                next_item["reason"] = "장중 이전 조회와 같은 값 " + str(unchanged_count) + "회 연속"
                if stage == "investor":
                    next_item["aiUsableAsStrongEvidence"] = False
                    next_item["freshnessStatus"] = "reference-repeat"
                    next_item["latencyStatus"] = "unchanged-repeat"
                    next_item["latencyLabel"] = INVESTOR_DELAYED_LABEL
                    next_item["latencyReason"] = "KIS 투자자별 수급이 이전 조회와 같아 실시간 변화 신호로 쓰지 않습니다."
                if stage != "price" and unchanged_count >= stale_threshold:
                    next_item["status"] = "stale"
                    if stage == "investor":
                        next_item["realTime"] = False
                        next_item["cadence"] = "stale-repeat"
                        next_item["latencyStatus"] = "stale"
                        next_item["latencyLabel"] = INVESTOR_DELAYED_LABEL
                        next_item["freshnessStatus"] = "stale-repeat"
                    next_item["staleReason"] = "장중 같은 " + stage + " 값이 " + str(unchanged_count) + "회 연속 반복되어 지연 가능성이 있습니다."
            else:
                next_item["unchangedCount"] = 0
            coverage[stage] = next_item
        signal["marketSignalCoverage"] = coverage
        included = []
        if coverage_has_fields(coverage, "price", ["currentPrice"]):
            included.append("현재가")
        if coverage_has_fields(coverage, "ccnl", ["tradeStrength"]):
            included.append("체결강도")
        if coverage_has_fields(coverage, "ccnl", ["buyVolume", "sellVolume"]):
            included.append("방향별 체결량")
        if coverage_has_fields(coverage, "investor", INVESTOR_SIGNAL_KEYS) and investor_stage_values_reliable(coverage):
            included.append("투자자별 수급")
        if coverage_has_fields(coverage, "orderbook", ["orderbookBidVolume", "orderbookAskVolume"]):
            included.append("호가 잔량")
        if included:
            included_text = ", ".join(included)
            signal["quoteStatus"] = "KIS " + included_text + " 반영"
            signal["quoteMessage"] = "KIS " + included_text + "을 모델링 데이터에 반영했습니다."
        return remove_unreliable_investor_values(signal)

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
            self.record_circuit_skip(stage, symbol)
            return None
        try:
            payload = self.request(stage, "GET", path, self.auth_headers(tr_id), query=self.query(symbol))
            for key in ["output", "output1", "output2"]:
                if key in payload:
                    return payload.get(key)
            return None
        except KISAPIError as error:
            self.record_failure(stage, symbol, error)
            return None

    def fetch_symbol_signal(self, symbol: str) -> Dict[str, object]:
        self.fetch_access_token()
        session = self.kr_market_session()
        microstructure_available = bool(session.get("microstructureAvailable"))
        price = self.fetch_stage(symbol, "price", PRICE_PATH, "FHKST01010100")
        ccnl = self.fetch_stage(symbol, "ccnl", CCNL_PATH, "FHKST01010300") if microstructure_available else None
        investor = self.fetch_stage(symbol, "investor", INVESTOR_PATH, "FHKST01010900") if microstructure_available else None
        orderbook = self.fetch_stage(symbol, "orderbook", ORDERBOOK_PATH, "FHKST01010200") if microstructure_available else None

        signal: Dict[str, object] = {}
        coverage: Dict[str, object] = {}
        fetched_at = utc_now_iso()
        if isinstance(price, dict):
            normalized_price = normalize_price(symbol, price)
            if not microstructure_available:
                normalized_price.pop("foreignNetVolume", None)
            merge_if_present(signal, normalized_price)
            price_as_of, price_as_of_confidence = stage_source_as_of("price", price, fetched_at)
            coverage["price"] = stage_coverage(
                "price",
                price,
                normalized_price,
                ["currentPrice", "changeRate", "volume", "volumeRatio", "tradingValue", "foreignNetVolume"],
                fetched_at=fetched_at,
                source_as_of=price_as_of,
                source_as_of_confidence=price_as_of_confidence,
                session=session,
                transport="rest",
                ai_usable_as_strong_evidence=True,
            )
        else:
            coverage["price"] = stage_coverage("price", price, {}, ["currentPrice"], fetched_at=fetched_at, session=session)
        if isinstance(ccnl, list):
            normalized_ccnl = normalize_ccnl(ccnl)
            merge_if_present(signal, normalized_ccnl)
            ccnl_as_of, ccnl_as_of_confidence = stage_source_as_of("ccnl", ccnl, fetched_at)
            coverage["ccnl"] = stage_coverage("ccnl", ccnl, normalized_ccnl, ["tradeStrength", "buyVolume", "sellVolume"], fetched_at=fetched_at, source_as_of=ccnl_as_of, source_as_of_confidence=ccnl_as_of_confidence, session=session, transport="rest", ai_usable_as_strong_evidence=True)
        else:
            coverage["ccnl"] = unavailable_stage_coverage("ccnl", session) if not microstructure_available else stage_coverage("ccnl", ccnl, {}, ["tradeStrength", "buyVolume", "sellVolume"], fetched_at=fetched_at, session=session)
        if isinstance(investor, list):
            normalized_investor = normalize_investor(investor)
            merge_if_present(signal, normalized_investor)
            investor_as_of, investor_as_of_confidence = stage_source_as_of("investor", investor, fetched_at)
            coverage["investor"] = stage_coverage("investor", investor, normalized_investor, INVESTOR_SIGNAL_KEYS, fetched_at=fetched_at, source_as_of=investor_as_of, source_as_of_confidence=investor_as_of_confidence, session=session, real_time=False, transport="rest", ai_usable_as_strong_evidence=False)
        elif isinstance(investor, dict):
            normalized_investor = normalize_investor([investor])
            merge_if_present(signal, normalized_investor)
            investor_as_of, investor_as_of_confidence = stage_source_as_of("investor", investor, fetched_at)
            coverage["investor"] = stage_coverage("investor", investor, normalized_investor, INVESTOR_SIGNAL_KEYS, fetched_at=fetched_at, source_as_of=investor_as_of, source_as_of_confidence=investor_as_of_confidence, session=session, real_time=False, transport="rest", ai_usable_as_strong_evidence=False)
        else:
            coverage["investor"] = unavailable_stage_coverage("investor", session) if not microstructure_available else stage_coverage("investor", investor, {}, INVESTOR_SIGNAL_KEYS, fetched_at=fetched_at, session=session)
        if isinstance(orderbook, dict):
            normalized_orderbook = normalize_orderbook(orderbook)
            merge_if_present(signal, normalized_orderbook)
            orderbook_as_of, orderbook_as_of_confidence = stage_source_as_of("orderbook", orderbook, fetched_at)
            coverage["orderbook"] = stage_coverage(
                "orderbook",
                orderbook,
                normalized_orderbook,
                ["orderbookBidVolume", "orderbookAskVolume", "bidAskImbalance"],
                fetched_at=fetched_at,
                source_as_of=orderbook_as_of,
                source_as_of_confidence=orderbook_as_of_confidence,
                session=session,
                transport="rest",
                ai_usable_as_strong_evidence=True,
            )
        else:
            coverage["orderbook"] = unavailable_stage_coverage("orderbook", session) if not microstructure_available else stage_coverage("orderbook", orderbook, {}, ["orderbookBidVolume", "orderbookAskVolume", "bidAskImbalance"], fetched_at=fetched_at, session=session)
        if not signal:
            raise RuntimeError("KIS 수급 응답 없음")

        info = known_stock(symbol)
        signal.setdefault("symbol", symbol)
        signal.setdefault("name", info["name"])
        signal.setdefault("market", "KR")
        signal.setdefault("currency", "KRW")
        signal["quoteSource"] = "KIS Open API"
        signal["marketSignalCoverage"] = coverage
        signal["marketSession"] = str(session.get("key") or "")
        signal["marketSessionLabel"] = str(session.get("label") or "")
        signal["dataQuality"] = "actual"
        signal["updatedAt"] = fetched_at
        signal = merge_fresh_websocket_stages(signal, self.cached_signal(symbol), max(10, self.live_refresh_seconds() * 2))
        coverage = signal.get("marketSignalCoverage") if isinstance(signal.get("marketSignalCoverage"), dict) else coverage
        signal = remove_unreliable_investor_values(signal)
        coverage = signal.get("marketSignalCoverage") if isinstance(signal.get("marketSignalCoverage"), dict) else coverage
        included = []
        if coverage_has_fields(coverage, "price", ["currentPrice"]):
            included.append("현재가")
        if coverage_has_fields(coverage, "ccnl", ["tradeStrength"]):
            included.append("체결강도")
        if coverage_has_fields(coverage, "ccnl", ["buyVolume", "sellVolume"]):
            included.append("방향별 체결량")
        if coverage_has_fields(coverage, "investor", INVESTOR_SIGNAL_KEYS) and investor_stage_values_reliable(coverage):
            included.append("투자자별 수급")
        if coverage_has_fields(coverage, "orderbook", ["orderbookBidVolume", "orderbookAskVolume"]):
            included.append("호가 잔량")
        included_text = ", ".join(included) if included else "응답 데이터"
        if "KIS WebSocket" in str(signal.get("quoteSource") or ""):
            signal["quoteStatus"] = "KIS 실시간 체결·호가 반영"
            signal["quoteMessage"] = append_message(
                signal.get("quoteMessage"),
                "KIS " + included_text + "을 모델링 데이터에 반영했습니다.",
            )
        else:
            signal["quoteStatus"] = "KIS " + included_text + " 반영"
            signal["quoteMessage"] = "KIS " + included_text + "을 모델링 데이터에 반영했습니다."
        if not microstructure_available:
            signal["quoteMessage"] = str(session.get("reason") or "") or signal["quoteMessage"]
        signal = remove_unreliable_investor_values(signal)
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
                cached = self.signal_for_current_session(cached)
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
                        cached = self.signal_for_current_session(cached)
                        cached["dataQuality"] = "cached"
                        signals[symbol] = cached
                        self.diagnostics["cached"] = int(self.diagnostics.get("cached") or 0) + 1
                    else:
                        self.diagnostics["skipped"] = int(self.diagnostics.get("skipped") or 0) + 1
            return signals

        for symbol, cached in stale_symbols:
            if self.circuit_open():
                self.record_circuit_skip("circuit", symbol)
                if cached:
                    cached = self.signal_for_current_session(cached)
                    cached["dataQuality"] = "cached"
                    signals[symbol] = cached
                    self.diagnostics["cached"] = int(self.diagnostics.get("cached") or 0) + 1
                else:
                    self.diagnostics["skipped"] = int(self.diagnostics.get("skipped") or 0) + 1
                continue
            try:
                signal = self.fetch_symbol_signal(symbol)
                signal = self.mark_unchanged_stage_health(signal, cached)
                signal = self.signal_for_current_session(signal)
                signals[symbol] = signal
                self.save_signal(symbol, signal)
                self.diagnostics["live"] = int(self.diagnostics.get("live") or 0) + 1
            except Exception as error:  # noqa: BLE001 - use cached signal if live KIS fails.
                self.record_failure("symbol", symbol, error)
                if cached:
                    cached = self.signal_for_current_session(cached)
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
        market_signal_coverage = signal.get("marketSignalCoverage") if isinstance(signal.get("marketSignalCoverage"), dict) else position.market_signal_coverage
        investor_values_reliable = investor_stage_values_reliable(market_signal_coverage)

        merged_price = current_price if current_price is not None else position.current_price
        market_value = position.market_value
        if not market_value and position.quantity and merged_price:
            market_value = position.quantity * merged_price

        data_quality = combine_quality(position.data_quality, signal.get("dataQuality"))
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
            market_signal_coverage=dict(market_signal_coverage or {}) if isinstance(market_signal_coverage, dict) else {},
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
            foreign_buy_volume=foreign_buy_volume if investor_values_reliable and foreign_buy_volume is not None else (position.foreign_buy_volume if investor_values_reliable else 0.0),
            foreign_sell_volume=foreign_sell_volume if investor_values_reliable and foreign_sell_volume is not None else (position.foreign_sell_volume if investor_values_reliable else 0.0),
            foreign_net_volume=foreign_net_volume if investor_values_reliable and foreign_net_volume is not None else (position.foreign_net_volume if investor_values_reliable else 0.0),
            foreign_net_amount=foreign_net_amount if investor_values_reliable and foreign_net_amount is not None else (position.foreign_net_amount if investor_values_reliable else 0.0),
            institution_buy_volume=institution_buy_volume if investor_values_reliable and institution_buy_volume is not None else (position.institution_buy_volume if investor_values_reliable else 0.0),
            institution_sell_volume=institution_sell_volume if investor_values_reliable and institution_sell_volume is not None else (position.institution_sell_volume if investor_values_reliable else 0.0),
            institution_net_volume=institution_net_volume if investor_values_reliable and institution_net_volume is not None else (position.institution_net_volume if investor_values_reliable else 0.0),
            institution_net_amount=institution_net_amount if investor_values_reliable and institution_net_amount is not None else (position.institution_net_amount if investor_values_reliable else 0.0),
            individual_buy_volume=individual_buy_volume if investor_values_reliable and individual_buy_volume is not None else (position.individual_buy_volume if investor_values_reliable else 0.0),
            individual_sell_volume=individual_sell_volume if investor_values_reliable and individual_sell_volume is not None else (position.individual_sell_volume if investor_values_reliable else 0.0),
            individual_net_volume=individual_net_volume if investor_values_reliable and individual_net_volume is not None else (position.individual_net_volume if investor_values_reliable else 0.0),
            individual_net_amount=individual_net_amount if investor_values_reliable and individual_net_amount is not None else (position.individual_net_amount if investor_values_reliable else 0.0),
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
    latest = {}
    for item in items:
        if isinstance(item, dict) and has_raw_value(item, INVESTOR_RAW_KEYS):
            latest = item
            break
    if not latest:
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
