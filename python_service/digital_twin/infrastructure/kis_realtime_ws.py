import json
import socket
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from digital_twin.modules.accounts.domain.accounts import split_symbols
from digital_twin.modules.market_data.domain.market_data import known_stock, number
from digital_twin.modules.portfolio.domain.portfolio import utc_now_iso
from .external_signal_utils import guarded_external_call
from .kis_market_signals import KIS_CACHE_ACCOUNT_ID, KIS_CACHE_PROVIDER, clean_symbol
from .kis_realtime_validation import (
    KIS_REALTIME_STAGE_FIELDS, KIS_REALTIME_VALIDATION_VERSION,
    has_unvalidated_websocket_stage, valid_kis_wire_row,
)
from .operational_store import market_quote_cache
from .settings import runtime_settings


KIS_REALTIME_DEFAULT_WS_URL = "ws://ops.koreainvestment.com:21000"
KIS_REALTIME_DEMO_WS_URL = "ws://ops.koreainvestment.com:31000"
KIS_TR_CCN_PRICE = "H0STCNT0"
KIS_TR_ORDERBOOK = "H0STASP0"
KIS_REALTIME_API_GUARD_STATE: Dict[str, object] = {}

CCNL_COLUMNS = [
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "STCK_PRPR", "PRDY_VRSS_SIGN",
    "PRDY_VRSS", "PRDY_CTRT", "WGHN_AVRG_STCK_PRC", "STCK_OPRC",
    "STCK_HGPR", "STCK_LWPR", "ASKP1", "BIDP1", "CNTG_VOL", "ACML_VOL",
    "ACML_TR_PBMN", "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU",
    "CTTR", "SELN_CNTG_SMTN", "SHNU_CNTG_SMTN", "CCLD_DVSN", "SHNU_RATE",
    "PRDY_VOL_VRSS_ACML_VOL_RATE", "OPRC_HOUR", "OPRC_VRSS_PRPR_SIGN",
    "OPRC_VRSS_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN", "HGPR_VRSS_PRPR",
    "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_PRPR", "BSOP_DATE",
    "NEW_MKOP_CLS_CODE", "TRHT_YN", "ASKP_RSQN1", "BIDP_RSQN1",
    "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "VOL_TNRT",
    "PRDY_SMNS_HOUR_ACML_VOL", "PRDY_SMNS_HOUR_ACML_VOL_RATE",
    "HOUR_CLS_CODE", "MRKT_TRTM_CLS_CODE", "VI_STND_PRC",
]

ORDERBOOK_COLUMNS = [
    "MKSC_SHRN_ISCD", "BSOP_HOUR", "HOUR_CLS_CODE",
    "ASKP1", "ASKP2", "ASKP3", "ASKP4", "ASKP5",
    "ASKP6", "ASKP7", "ASKP8", "ASKP9", "ASKP10",
    "BIDP1", "BIDP2", "BIDP3", "BIDP4", "BIDP5",
    "BIDP6", "BIDP7", "BIDP8", "BIDP9", "BIDP10",
    "ASKP_RSQN1", "ASKP_RSQN2", "ASKP_RSQN3", "ASKP_RSQN4", "ASKP_RSQN5",
    "ASKP_RSQN6", "ASKP_RSQN7", "ASKP_RSQN8", "ASKP_RSQN9", "ASKP_RSQN10",
    "BIDP_RSQN1", "BIDP_RSQN2", "BIDP_RSQN3", "BIDP_RSQN4", "BIDP_RSQN5",
    "BIDP_RSQN6", "BIDP_RSQN7", "BIDP_RSQN8", "BIDP_RSQN9", "BIDP_RSQN10",
    "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "OVTM_TOTAL_ASKP_RSQN", "OVTM_TOTAL_BIDP_RSQN",
    "ANTC_CNPR", "ANTC_CNQN", "ANTC_VOL", "ANTC_CNTG_VRSS", "ANTC_CNTG_VRSS_SIGN",
    "ANTC_CNTG_PRDY_CTRT", "ACML_VOL", "TOTAL_ASKP_RSQN_ICDC", "TOTAL_BIDP_RSQN_ICDC",
    "OVTM_TOTAL_ASKP_ICDC", "OVTM_TOTAL_BIDP_ICDC", "STCK_DEAL_CLS_CODE",
]

# The live 2026-09-14 feed appends one trade field and four orderbook fields.
# Their meaning isn't used here; the verified prefix stays unchanged. Never
# stride a batched live message with the older prefix width.
KIS_RECORD_WIDTHS = {KIS_TR_CCN_PRICE: {46, 47}, KIS_TR_ORDERBOOK: {59, 63}}


def bool_setting(settings: Dict[str, str], key: str, fallback: bool = True) -> bool:
    value = settings.get(key)
    if value in (None, ""):
        return fallback
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def int_setting(settings: Dict[str, str], key: str, fallback: int, minimum: int = 0, maximum: int = 100000) -> int:
    try:
        parsed = int(float(str(settings.get(key) or "").strip()))
    except ValueError:
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def append_source(existing: object, addition: str) -> str:
    base = str(existing or "").strip()
    extra = str(addition or "").strip()
    if not extra:
        return base
    if not base:
        return extra
    if extra in base:
        return base
    return base + " + " + extra


def websocket_url_from_settings(settings: Dict[str, str]) -> str:
    configured = str(settings.get("kisWebSocketUrl") or "").strip()
    if configured:
        return configured
    env = str(settings.get("kisEnv") or "").strip().lower()
    if env in {"vps", "paper", "demo", "mock"}:
        return KIS_REALTIME_DEMO_WS_URL
    return KIS_REALTIME_DEFAULT_WS_URL


def parse_kis_realtime_text(text: str) -> Optional[Tuple[str, List[Dict[str, object]]]]:
    raw = str(text or "")
    if not raw.startswith("0|"):
        return None
    parts = raw.split("|", 3)
    if len(parts) < 4:
        return None
    tr_id = parts[1]
    try:
        item_count = int(parts[2])
    except ValueError:
        return None
    columns = CCNL_COLUMNS if tr_id == KIS_TR_CCN_PRICE else ORDERBOOK_COLUMNS if tr_id == KIS_TR_ORDERBOOK else []
    if not columns:
        return None
    values = parts[3].split("^")
    if item_count <= 0 or len(values) % item_count:
        return None
    width = len(values) // item_count
    if width not in KIS_RECORD_WIDTHS[tr_id]:
        return None
    rows = []
    for index in range(item_count):
        chunk = values[index * width:(index + 1) * width]
        row = dict(zip((column.lower() for column in columns), chunk))
        if not valid_kis_wire_row(row, "ccnl" if tr_id == KIS_TR_CCN_PRICE else "orderbook"):
            return None
        row["_wire_field_count"] = width
        rows.append(row)
    return tr_id, rows


def normalize_ws_ccnl(row: Dict[str, object]) -> Dict[str, object]:
    symbol = clean_symbol(row.get("mksc_shrn_iscd"))
    info = known_stock(symbol)
    current_price = number(row.get("stck_prpr"))
    volume = number(row.get("acml_vol"))
    trading_value = number(row.get("acml_tr_pbmn"))
    if not trading_value and current_price and volume:
        trading_value = current_price * volume
    return {
        "symbol": symbol,
        "name": info["name"],
        "market": "KR",
        "currency": "KRW",
        "wireFieldCount": row.get("_wire_field_count"),
        "currentPrice": current_price,
        "changeRate": number(row.get("prdy_ctrt")),
        "volume": volume,
        "volumeRatio": (number(row.get("prdy_vol_vrss_acml_vol_rate")) or 0) / 100 if row.get("prdy_vol_vrss_acml_vol_rate") not in (None, "") else None,
        "tradingValue": trading_value,
        "tradeStrength": number(row.get("cttr")),
        "buyVolume": number(row.get("shnu_cntg_smtn")),
        "sellVolume": number(row.get("seln_cntg_smtn")),
        "orderbookBidVolume": number(row.get("total_bidp_rsqn")),
        "orderbookAskVolume": number(row.get("total_askp_rsqn")),
    }


def normalize_ws_orderbook(row: Dict[str, object]) -> Dict[str, object]:
    symbol = clean_symbol(row.get("mksc_shrn_iscd"))
    info = known_stock(symbol)
    bid_volume = number(row.get("total_bidp_rsqn"))
    ask_volume = number(row.get("total_askp_rsqn"))
    base = number(bid_volume) + number(ask_volume)
    imbalance = ((number(bid_volume) - number(ask_volume)) / base) * 100 if base else None
    return {
        "symbol": symbol,
        "name": info["name"],
        "market": "KR",
        "currency": "KRW",
        "wireFieldCount": row.get("_wire_field_count"),
        "volume": number(row.get("acml_vol")),
        "orderbookBidVolume": bid_volume,
        "orderbookAskVolume": ask_volume,
        "bidAskImbalance": imbalance,
    }


def websocket_stage_coverage(stage: str, fields: Iterable[str], fetched_at: str, tr_id: str) -> Dict[str, object]:
    clean_fields = sorted(str(field) for field in fields if str(field or "").strip())
    return {
        "stage": stage,
        "status": "available" if clean_fields else "empty",
        "fields": clean_fields,
        "nonZeroFields": clean_fields,
        "fetchedAt": fetched_at,
        "sourceAsOf": fetched_at,
        # The protocol frame does not always carry an exchange timestamp.
        # Record the receipt clock honestly instead of presenting it as an
        # exchange-issued tick time.
        "sourceTimestampState": "websocket-received",
        "realTime": True,
        "freshnessStatus": "realtime",
        "aiUsableAsStrongEvidence": True,
        "cadence": "websocket",
        "transport": "websocket",
        "provider": "KIS",
        "trId": tr_id,
        "marketSession": "regular",
        "marketSessionLabel": "정규장",
    }


def merge_realtime_signal(previous: Dict[str, object], update: Dict[str, object], stage: str, tr_id: str, fetched_at: str) -> Dict[str, object]:
    merged = {} if has_unvalidated_websocket_stage(previous or {}) else dict(previous or {})
    for key, value in (update or {}).items():
        if value not in (None, ""):
            merged[key] = value
    symbol = clean_symbol(merged.get("symbol") or update.get("symbol"))
    info = known_stock(symbol)
    merged.setdefault("symbol", symbol)
    merged.setdefault("name", info["name"])
    merged.setdefault("market", "KR")
    merged.setdefault("currency", "KRW")
    merged["quoteSource"] = append_source(merged.get("quoteSource"), "KIS WebSocket")
    merged["quoteStatus"] = "KIS 실시간 체결·호가 반영"
    merged["quoteMessage"] = "KIS WebSocket 체결·호가를 실시간 모델링 데이터에 반영했습니다. 투자자별 수급은 KIS REST live-poll 근거와 분리해서 봅니다."
    merged["dataQuality"] = "actual"
    merged["updatedAt"] = fetched_at
    merged["sourceAsOf"] = fetched_at
    merged["sourceFetchedAt"] = fetched_at
    merged["sourceTimestampState"] = "websocket-received"
    merged["freshnessStatus"] = "realtime"
    merged["freshnessReason"] = "KIS WebSocket에서 실제 체결 또는 호가 프레임을 수신했습니다."
    merged["freshnessAgeMinutes"] = 0
    merged["latencyStatus"] = "live-transport"
    merged["latencyReason"] = "WebSocket 수신 시각 기준의 장중 체결·호가 관측값입니다."
    merged["transport"] = "websocket"
    merged["realTime"] = True
    merged["marketSession"] = "regular"
    merged["marketSessionLabel"] = "정규장"
    coverage = dict(merged.get("marketSignalCoverage") or {}) if isinstance(merged.get("marketSignalCoverage"), dict) else {}
    fields = [key for key in KIS_REALTIME_STAGE_FIELDS[stage] if update.get(key) not in (None, "")]
    coverage[stage] = websocket_stage_coverage(stage, fields, fetched_at, tr_id)
    coverage[stage]["validationVersion"] = KIS_REALTIME_VALIDATION_VERSION
    coverage[stage]["wireFieldCount"] = update.get("wireFieldCount")
    coverage[stage]["values"] = {"symbol": symbol, **{key: update[key] for key in fields}}
    merged["marketSignalCoverage"] = coverage
    return merged


class MinimalWebSocket:
    """Compatibility port backed by a complete, timeout-safe RFC 6455 client."""

    def __init__(self, url: str, timeout: int = 10):
        self.url = url
        self.timeout = timeout
        self.socket = None

    def connect(self) -> None:
        from websockets.sync.client import connect

        self.socket = connect(
            self.url, open_timeout=self.timeout, close_timeout=2,
            compression=None, proxy=None, max_size=1024 * 1024,
        )

    def send_text(self, text: str) -> None:
        if not self.socket:
            raise RuntimeError("WebSocket is not connected.")
        self.socket.send(str(text or ""))

    def recv_text(self, timeout: Optional[float] = None) -> str:
        if not self.socket:
            raise RuntimeError("WebSocket is not connected.")
        return self.socket.recv(timeout=timeout, decode=True)

    def close(self) -> None:
        if not self.socket:
            return
        try:
            self.socket.close()
        finally:
            self.socket = None


class KISRealtimeWebSocketClient:
    def __init__(
        self,
        settings: Dict[str, str] = None,
        quote_cache=None,
        http_json: Callable[[str, Dict[str, object], Dict[str, str], int], Dict[str, object]] = None,
        websocket_factory: Callable[[str, int], object] = None,
        now_provider: Callable[[], str] = None,
    ):
        self.settings = settings or runtime_settings()
        self.quote_cache = quote_cache if quote_cache is not None else market_quote_cache(self.settings)
        self.http_json = http_json or self.post_json
        self.websocket_factory = websocket_factory or (lambda url, timeout: MinimalWebSocket(url, timeout))
        self.now_provider = now_provider or utc_now_iso
        self.base_url = str(self.settings.get("kisBaseUrl") or "https://openapi.koreainvestment.com:9443").rstrip("/")
        self.ws_url = websocket_url_from_settings(self.settings)
        self.app_key = str(self.settings.get("kisAppKey") or "").strip()
        self.app_secret = str(self.settings.get("kisAppSecret") or "").strip()
        self.approval_key = ""
        self.rejected_frame_count = 0

    def enabled(self) -> bool:
        return bool_setting(self.settings, "kisRealtimeWebSocketEnabled", True)

    def configured(self) -> bool:
        return bool(self.base_url and self.ws_url and self.app_key and self.app_secret)

    def timeout_seconds(self) -> int:
        return int_setting(self.settings, "kisRealtimeWebSocketTimeoutSeconds", 10, 1, 120)

    def post_json(self, url: str, body: Dict[str, object], headers: Dict[str, str], timeout: int) -> Dict[str, object]:
        request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def fetch_approval_key(self) -> str:
        if self.approval_key:
            return self.approval_key
        payload = guarded_external_call(
            self.settings,
            "KIS WebSocket",
            "oauth2/Approval",
            lambda: self.http_json(
                self.base_url + "/oauth2/Approval",
                {
                    "grant_type": "client_credentials",
                    "appkey": self.app_key,
                    "secretkey": self.app_secret,
                },
                {"content-type": "application/json; charset=utf-8"},
                self.timeout_seconds(),
            ),
            state=KIS_REALTIME_API_GUARD_STATE,
            rate_limit_seconds=0,
        )
        key = str(payload.get("approval_key") or "").strip()
        if not key:
            raise RuntimeError("KIS WebSocket approval_key가 없습니다.")
        self.approval_key = key
        return key

    def subscribe_message(self, tr_id: str, symbol: str, tr_type: str = "1") -> str:
        return json.dumps({
            "header": {
                "approval_key": self.fetch_approval_key(),
                "custtype": "P",
                "tr_type": tr_type,
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": tr_id,
                    "tr_key": clean_symbol(symbol),
                },
            },
        }, ensure_ascii=False, separators=(",", ":"))

    def merge_update(self, symbol: str, update: Dict[str, object], stage: str, tr_id: str) -> Tuple[Dict[str, object], Dict[str, object]]:
        clean = clean_symbol(symbol)
        previous = self.quote_cache.load(KIS_CACHE_PROVIDER, KIS_CACHE_ACCOUNT_ID, clean)
        merged = merge_realtime_signal(previous, update, stage, tr_id, self.now_provider())
        self.quote_cache.save(KIS_CACHE_PROVIDER, KIS_CACHE_ACCOUNT_ID, clean, merged)
        return previous, merged

    def apply_message(self, text: str) -> List[Dict[str, object]]:
        parsed = parse_kis_realtime_text(text)
        if not parsed:
            if str(text or "").startswith(("0|H0STCNT0|", "0|H0STASP0|")):
                self.rejected_frame_count += 1
            return []
        tr_id, rows = parsed
        results = []
        for row in rows:
            if tr_id == KIS_TR_CCN_PRICE:
                update = normalize_ws_ccnl(row)
                stage = "ccnl"
            elif tr_id == KIS_TR_ORDERBOOK:
                update = normalize_ws_orderbook(row)
                stage = "orderbook"
            else:
                continue
            symbol = clean_symbol(update.get("symbol"))
            if not symbol:
                continue
            previous, merged = self.merge_update(symbol, update, stage, tr_id)
            results.append({"symbol": symbol, "stage": stage, "trId": tr_id, "previous": previous, "payload": merged})
        return results

    def collect(self, symbols: Iterable[str], duration_seconds: int, on_update: Callable[[List[Dict[str, object]]], None] = None) -> Dict[str, object]:
        self.rejected_frame_count = 0
        clean_symbols = [clean_symbol(symbol) for symbol in symbols or [] if clean_symbol(symbol)]
        if not self.enabled():
            return {"status": "disabled", "symbols": [], "savedCount": 0}
        if not self.configured():
            return {"status": "missingCredentials", "symbols": clean_symbols, "savedCount": 0}
        if not clean_symbols:
            return {"status": "noSymbols", "symbols": [], "savedCount": 0}
        saved_count = 0
        stage_counts: Dict[str, int] = {}
        subscribed_symbols: List[str] = []
        last_received_at = ""
        last_tick_at = ""
        ws = None
        stage = "connect"
        started = time.monotonic()
        try:
            ws = self.websocket_factory(self.ws_url, self.timeout_seconds())
            ws.connect()
            stage = "subscribe"
            for symbol in clean_symbols:
                ws.send_text(self.subscribe_message(KIS_TR_CCN_PRICE, symbol))
                ws.send_text(self.subscribe_message(KIS_TR_ORDERBOOK, symbol))
                subscribed_symbols.append(symbol)
                time.sleep(0.03)
            stage = "receive"
            while time.monotonic() - started < max(1, int(duration_seconds or 1)):
                try:
                    text = ws.recv_text(timeout=1.0)
                except (TimeoutError, socket.timeout):
                    # Python 3.9 keeps socket.timeout distinct from the
                    # built-in timeout raised by websockets' message buffer.
                    continue
                last_received_at = str(self.now_provider() or "")
                if str(text).startswith("{"):
                    control = json.loads(text)
                    if (control.get("header") or {}).get("tr_id") == "PINGPONG":
                        ws.send_text(text)
                        continue
                updates = self.apply_message(text)
                if not updates:
                    continue
                last_tick_at = last_received_at
                saved_count += len(updates)
                for update in updates:
                    update_stage = str(update.get("stage") or "")
                    stage_counts[update_stage] = stage_counts.get(update_stage, 0) + 1
                if on_update:
                    on_update(updates)
        except Exception as error:  # noqa: BLE001 - a disconnected feed must not terminate the realtime worker.
            status = "partial" if saved_count else "connection-error"
            return {
                "status": status,
                "provider": "kis-websocket",
                "symbols": clean_symbols,
                "selectedCount": len(clean_symbols),
                "subscribedSymbols": subscribed_symbols,
                "subscribedCount": len(subscribed_symbols),
                "savedCount": saved_count,
                "rejectedFrameCount": self.rejected_frame_count,
                "wireValidationVersion": KIS_REALTIME_VALIDATION_VERSION,
                "stageCounts": stage_counts,
                "dataQuality": "partial" if saved_count else "unavailable",
                "freshnessStatus": "realtime" if saved_count else "unavailable",
                "sourceTimestampState": "websocket-received" if saved_count else "no-observation",
                "lastReceivedAt": last_received_at,
                "lastTickAt": last_tick_at,
                "realTime": bool(saved_count),
                "transport": "websocket",
                "errorStage": stage,
                "reason": ("KIS WebSocket " + stage + " 단계 연결이 끊겼습니다: " + str(error))[:360],
                "reconnectRecommended": True,
                "elapsedSeconds": round(time.monotonic() - started, 3),
            }
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
        has_tick = bool(saved_count)
        rejected_only = bool(self.rejected_frame_count and not has_tick)
        return {
            "status": "partial" if has_tick and self.rejected_frame_count else "ok" if has_tick else "invalid-data" if rejected_only else "no-tick",
            "provider": "kis-websocket",
            "symbols": clean_symbols,
            "selectedCount": len(clean_symbols),
            "subscribedSymbols": subscribed_symbols,
            "subscribedCount": len(subscribed_symbols),
            "savedCount": saved_count,
            "rejectedFrameCount": self.rejected_frame_count,
            "wireValidationVersion": KIS_REALTIME_VALIDATION_VERSION,
            "stageCounts": stage_counts,
            "dataQuality": "partial" if has_tick and self.rejected_frame_count else "actual" if has_tick else "invalid" if rejected_only else "reference",
            "reason": "KIS 수신 레코드 형식 또는 값 검증 실패: " + str(self.rejected_frame_count) + "건" if self.rejected_frame_count else "",
            "freshnessStatus": "realtime" if has_tick else "no-tick",
            "sourceTimestampState": "websocket-received" if has_tick else "no-observation",
            "lastReceivedAt": last_received_at,
            "lastTickAt": last_tick_at,
            "realTime": has_tick,
            "transport": "websocket",
            "elapsedSeconds": round(time.monotonic() - started, 3),
        }


class KISRealtimeSymbolSelector:
    def __init__(self, account_repository, monitor_store, quote_cache, settings: Dict[str, str]):
        self.account_repository = account_repository
        self.monitor_store = monitor_store
        self.quote_cache = quote_cache
        self.settings = dict(settings or {})

    def max_symbols(self) -> int:
        return int_setting(self.settings, "kisRealtimeWebSocketMaxSymbols", int_setting(self.settings, "kisMarketSignalMaxSymbols", 20, 1, 200), 1, 200)

    def configured_symbols(self) -> List[str]:
        raw = self.settings.get("kisRealtimeWebSocketSymbols") or self.settings.get("watchlistSymbols") or ""
        return [clean_symbol(item) for item in split_symbols(raw) if clean_symbol(item)]

    def account_symbols(self) -> List[str]:
        symbols: List[str] = []
        try:
            accounts = self.account_repository.load()
        except Exception:
            accounts = []
        for account in accounts:
            for symbol in getattr(account, "watchlist_symbols", []) or []:
                clean = clean_symbol(symbol)
                if clean:
                    symbols.append(clean)
        return symbols

    def monitor_symbols(self) -> List[str]:
        previous = getattr(self.monitor_store, "previous", {}) or {}
        symbols: List[str] = []
        for state in previous.values():
            if not isinstance(state, dict):
                continue
            for container in ["positions", "watchlist"]:
                items = state.get(container)
                if isinstance(items, dict):
                    iterable = items.values()
                elif isinstance(items, list):
                    iterable = items
                else:
                    iterable = []
                for item in iterable:
                    if not isinstance(item, dict):
                        continue
                    market = str(item.get("market") or "").upper()
                    currency = str(item.get("currency") or "").upper()
                    clean = clean_symbol(item.get("symbol"))
                    if clean and clean.isdigit() and len(clean) == 6 and (not market or market in {"KR", "KOSPI", "KOSDAQ", "KONEX"} or currency == "KRW"):
                        symbols.append(clean)
        return symbols

    def cache_symbols(self) -> List[str]:
        try:
            rows = self.quote_cache.stale_universe_symbols("toss", "__market_data__", ["KR", "KOSPI", "KOSDAQ", "KONEX"], limit=self.max_symbols(), max_age_minutes=1440)
        except Exception:
            rows = []
        return [clean_symbol(row.get("symbol")) for row in rows if clean_symbol(row.get("symbol"))]

    def bounded_symbols(self, candidates: Iterable[str]) -> List[str]:
        ordered: List[str] = []
        seen = set()
        for symbol in candidates:
            clean = clean_symbol(symbol)
            if not clean or not (clean.isdigit() and len(clean) == 6) or clean in seen:
                continue
            seen.add(clean)
            ordered.append(clean)
            if len(ordered) >= self.max_symbols():
                break
        return ordered

    def reasoning_symbols(self) -> List[str]:
        """Korean names that may affect a user's investment alert.

        Cache-fill symbols are useful for keeping market data warm, but they
        must not fill the TypeDB investment-reasoning queue. Configured
        transport symbols are intentionally excluded by default: they are
        useful for a warm quote cache, but are not necessarily a user's
        holding or watchlist name.
        """
        candidates = self.monitor_symbols() + self.account_symbols()
        if bool_setting(self.settings, "kisRealtimeWebSocketIncludeConfiguredInReasoning", False):
            candidates += self.configured_symbols()
        return self.bounded_symbols(candidates)

    def symbols(self) -> List[str]:
        return self.bounded_symbols(
            self.configured_symbols() + self.monitor_symbols() + self.account_symbols() + self.cache_symbols()
        )
