import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List

from ..domain.market_data import number
from ..domain.portfolio import Position, utc_now_iso
from .settings import runtime_settings
from .sqlite_monitoring import SQLiteExternalSignalCache


JsonFetcher = Callable[[str, Dict[str, str]], object]


def default_json_fetcher(url: str, headers: Dict[str, str] = None) -> Dict[str, object]:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=12) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def parse_iso(value: str):
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def symbol_list(raw: str) -> List[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def symbol_assignments(raw: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    normalized = str(raw or "").replace(";", "\n")
    for line in normalized.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        separator = "=" if "=" in stripped else ":" if ":" in stripped else "," if "," in stripped else ""
        if not separator:
            continue
        key, raw_value = stripped.split(separator, 1)
        key = key.strip().upper()
        value = raw_value.strip()
        if not key or not key.replace("_", "").isalnum() or not value:
            continue
        values[key] = value
    return values


def percent_text(value: object) -> float:
    return number(str(value or "").replace("%", ""))


def api_error_text(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        reason = str(error.reason or "").strip()
        return "HTTP " + str(error.code) + (" " + reason if reason else "")
    if isinstance(error, urllib.error.URLError):
        return "URL error " + str(error.reason or error)[:120]
    return str(error or type(error).__name__)[:120]


def retryable_api_error(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return int(error.code or 0) in {408, 409, 425, 429, 500, 502, 503, 504}
    return isinstance(error, (urllib.error.URLError, TimeoutError, OSError))


class ExternalCircuitOpen(RuntimeError):
    pass


class ExternalRateLimited(RuntimeError):
    pass


class ExternalApiGuard:
    def __init__(
        self,
        state: Dict[str, object],
        sleep: Callable[[float], None] = None,
        now: Callable[[], datetime] = None,
    ):
        self.state = state
        self.sleep = sleep or time.sleep
        self.now = now or (lambda: datetime.now(timezone.utc))

    def entry(self, key: str) -> Dict[str, object]:
        raw = self.state.get(key)
        if isinstance(raw, dict):
            return raw
        entry: Dict[str, object] = {}
        self.state[key] = entry
        return entry

    def call(
        self,
        key: str,
        label: str,
        fetch: Callable[[], object],
        attempts: int,
        rate_limit_seconds: int,
        failure_threshold: int,
        cooldown_minutes: int,
        retry_delay_seconds: float = 0.25,
    ):
        entry = self.entry(key)
        now = self.now()
        opened_until = parse_iso(str(entry.get("openedUntil") or ""))
        if opened_until and opened_until > now:
            raise ExternalCircuitOpen("circuit open until " + opened_until.isoformat().replace("+00:00", "Z"))
        last_request_at = parse_iso(str(entry.get("lastRequestAt") or ""))
        if rate_limit_seconds and last_request_at and now - last_request_at < timedelta(seconds=rate_limit_seconds):
            raise ExternalRateLimited("local rate limit active")

        last_error: Exception = RuntimeError("unknown error")
        max_attempts = max(1, int(attempts or 1))
        for attempt in range(max_attempts):
            try:
                result = fetch()
                entry["lastRequestAt"] = now.isoformat().replace("+00:00", "Z")
                entry["failures"] = 0
                entry["lastError"] = ""
                entry["openedUntil"] = ""
                return result
            except Exception as error:  # noqa: BLE001 - external adapters normalize vendor failures.
                last_error = error
                if attempt + 1 >= max_attempts or not retryable_api_error(error):
                    break
                self.sleep(retry_delay_seconds * (attempt + 1))

        failures = int(number(entry.get("failures")) or 0) + 1
        entry["lastRequestAt"] = now.isoformat().replace("+00:00", "Z")
        entry["failures"] = failures
        entry["lastError"] = api_error_text(last_error)
        if failures >= max(1, int(failure_threshold or 1)):
            entry["openedUntil"] = (now + timedelta(minutes=max(1, int(cooldown_minutes or 1)))).isoformat().replace("+00:00", "Z")
        raise RuntimeError(label + " 실패 · " + api_error_text(last_error))


class ExternalSignalProvider:
    def __init__(
        self,
        settings: Dict[str, str] = None,
        cache: SQLiteExternalSignalCache = None,
        fetch_json: JsonFetcher = None,
        sleep: Callable[[float], None] = None,
    ):
        self.settings = settings or runtime_settings()
        self.cache = cache or SQLiteExternalSignalCache()
        self.fetch_json = fetch_json or default_json_fetcher
        self.sleep = sleep or time.sleep
        self.provider_state: Dict[str, object] = {}

    def signals_for_positions(self, positions: Iterable[Position]) -> Dict[str, object]:
        position_list = list(positions)
        cache_key = self.cache_key_for_positions(position_list)
        cached = self.cache.load()
        self.provider_state = self.provider_state_from(cached)
        entry = self.cache_entry(cached, cache_key)
        if self.is_cache_fresh(entry):
            signals = entry.get("signals")
            if isinstance(signals, dict):
                return signals

        signals = self.fetch_signals(position_list)
        self.cache.replace(self.next_cache_payload(cached, cache_key, signals))
        return signals

    def cache_entry(self, payload: Dict[str, object], cache_key: str) -> Dict[str, object]:
        entries = payload.get("entries") if isinstance(payload.get("entries"), dict) else {}
        entry = entries.get(cache_key) if isinstance(entries.get(cache_key), dict) else {}
        if entry:
            return entry
        if payload.get("cacheKey") == cache_key:
            return payload
        return {}

    def next_cache_payload(self, cached: Dict[str, object], cache_key: str, signals: Dict[str, object]) -> Dict[str, object]:
        entries = cached.get("entries") if isinstance(cached.get("entries"), dict) else {}
        entries = dict(entries)
        entries[cache_key] = {
            "fetchedAt": signals.get("fetchedAt") or utc_now_iso(),
            "signals": signals,
        }
        ordered = sorted(
            entries.items(),
            key=lambda item: str(item[1].get("fetchedAt") if isinstance(item[1], dict) else ""),
            reverse=True,
        )[:20]
        return {"schemaVersion": 1, "entries": dict(ordered), "providerState": dict(self.provider_state)}

    def provider_state_from(self, payload: Dict[str, object]) -> Dict[str, object]:
        state = payload.get("providerState") if isinstance(payload.get("providerState"), dict) else {}
        return {str(key): dict(value) for key, value in state.items() if isinstance(value, dict)}

    def cache_key_for_positions(self, positions: List[Position]) -> str:
        payload = {
            "alphaSymbols": self.alpha_symbols(positions),
            "cryptoIds": symbol_list(self.settings.get("externalCryptoIds") or "bitcoin,ethereum"),
            "fredSeries": symbol_list(self.settings.get("externalFredSeries") or "DGS10,DGS2,DFF"),
            "dartSymbols": self.dart_symbols(positions),
            "alphaMax": str(self.settings.get("externalAlphaMaxSymbols") or "3"),
            "dartLookbackDays": str(self.settings.get("externalDartLookbackDays") or "14"),
            "dartMappings": symbol_assignments(self.settings.get("externalDartCorpCodes") or ""),
            "settingsUpdatedAt": str(self.settings.get("updatedAt") or ""),
            "configured": {
                "alpha": bool(str(self.settings.get("alphaVantageApiKey") or "").strip()),
                "coingecko": bool(str(self.settings.get("coingeckoApiKey") or "").strip()),
                "fred": bool(str(self.settings.get("fredApiKey") or "").strip()),
                "opendart": bool(str(self.settings.get("opendartApiKey") or "").strip()),
            },
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def is_cache_fresh(self, payload: Dict[str, object]) -> bool:
        fetched_at = parse_iso(str(payload.get("fetchedAt") or ""))
        if not fetched_at:
            return False
        minutes = int(number(self.settings.get("externalApiFetchIntervalMinutes")) or 60)
        minutes = max(10, minutes)
        return datetime.now(timezone.utc) - fetched_at < timedelta(minutes=minutes)

    def fetch_signals(self, positions: List[Position]) -> Dict[str, object]:
        signals = {
            "fetchedAt": utc_now_iso(),
            "equityQuotes": {},
            "cryptoMarkets": {},
            "macro": {},
            "dartDisclosures": {},
            "statuses": [],
        }
        self.add_alpha_vantage(signals, positions)
        self.add_coingecko(signals)
        self.add_fred(signals)
        self.add_opendart(signals, positions)
        return signals

    def int_setting(self, key: str, fallback: int, minimum: int = 0) -> int:
        raw = self.settings.get(key)
        value = fallback if str(raw or "").strip() == "" else int(number(raw))
        return max(minimum, value)

    def guarded_call(self, source: str, target: str, fetch: Callable[[], object]):
        guard = ExternalApiGuard(self.provider_state, sleep=self.sleep)
        return guard.call(
            source.lower().replace(" ", "-") + ":" + target,
            source + " " + target,
            fetch,
            attempts=self.int_setting("externalApiRetryAttempts", 2, 1),
            rate_limit_seconds=self.int_setting("externalApiRateLimitSeconds", 60, 0),
            failure_threshold=self.int_setting("externalApiCircuitFailures", 2, 1),
            cooldown_minutes=self.int_setting("externalApiCircuitCooldownMinutes", 30, 1),
        )

    def limited_targets(self, signals: Dict[str, object], source: str, values: List[str], limit_key: str, fallback: int) -> List[str]:
        limit = self.int_setting(limit_key, fallback, 1)
        if len(values) > limit:
            self.status(signals, source, True, "bulk cap " + str(limit) + "/" + str(len(values)))
        return values[:limit]

    def status(self, signals: Dict[str, object], source: str, ok: bool, message: str) -> None:
        signals.setdefault("statuses", []).append({
            "source": source,
            "ok": bool(ok),
            "message": str(message or ""),
        })

    def status_for_error(self, signals: Dict[str, object], source: str, message: str, error: Exception) -> None:
        self.status(signals, source, isinstance(error, ExternalRateLimited), message + str(error)[:120])

    def add_alpha_vantage(self, signals: Dict[str, object], positions: List[Position]) -> None:
        api_key = str(self.settings.get("alphaVantageApiKey") or "").strip()
        if not api_key:
            return
        for symbol in self.alpha_symbols(positions):
            try:
                def fetch_quote():
                    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode({
                        "function": "GLOBAL_QUOTE",
                        "symbol": symbol,
                        "apikey": api_key,
                    })
                    payload = self.fetch_json(url, {"Accept": "application/json"})
                    if payload.get("Information") or payload.get("Note"):
                        raise RuntimeError(str(payload.get("Information") or payload.get("Note")))
                    quote = payload.get("Global Quote") if isinstance(payload.get("Global Quote"), dict) else {}
                    if not quote:
                        raise RuntimeError("empty GLOBAL_QUOTE")
                    return {
                        "provider": "Alpha Vantage",
                        "price": number(quote.get("05. price")),
                        "change": number(quote.get("09. change")),
                        "changePercent": percent_text(quote.get("10. change percent")),
                        "volume": number(quote.get("06. volume")),
                        "latestTradingDay": str(quote.get("07. latest trading day") or ""),
                    }

                signals["equityQuotes"][symbol] = self.guarded_call("Alpha Vantage", "GLOBAL_QUOTE:" + symbol, fetch_quote)
            except Exception as error:  # noqa: BLE001 - provider failure should not stop Toss monitoring.
                self.status_for_error(signals, "Alpha Vantage", symbol + " ", error)

    def alpha_symbols(self, positions: List[Position]) -> List[str]:
        max_symbols = int(number(self.settings.get("externalAlphaMaxSymbols")) or 3)
        symbols = []
        seen = set()
        for position in positions:
            if position.is_cash():
                continue
            symbol = str(position.symbol or "").upper()
            if not symbol or symbol in seen:
                continue
            if position.market.upper() == "US" or position.currency.upper() == "USD":
                seen.add(symbol)
                symbols.append(symbol)
        return symbols[:max(1, max_symbols)]

    def add_coingecko(self, signals: Dict[str, object]) -> None:
        ids = self.limited_targets(
            signals,
            "CoinGecko",
            symbol_list(self.settings.get("externalCryptoIds") or "bitcoin,ethereum"),
            "externalCryptoMaxIds",
            50,
        )
        if not ids:
            return
        headers = {"Accept": "application/json", "User-Agent": "DigitalTwin/1.0"}
        api_key = str(self.settings.get("coingeckoApiKey") or "").strip()
        if api_key:
            headers["x-cg-demo-api-key"] = api_key
        try:
            def fetch_markets():
                url = "https://api.coingecko.com/api/v3/coins/markets?" + urllib.parse.urlencode({
                    "vs_currency": "usd",
                    "ids": ",".join(ids),
                    "order": "market_cap_desc",
                    "per_page": str(len(ids)),
                    "page": "1",
                    "sparkline": "false",
                    "price_change_percentage": "1h,24h,7d",
                    "locale": "en",
                })
                payload = self.fetch_json(url, headers)
                if not isinstance(payload, list):
                    raise RuntimeError("coins/markets JSON is not a list")
                return payload

            payload = self.guarded_call("CoinGecko", "coins/markets", fetch_markets)
            for item in payload:
                if not isinstance(item, dict):
                    continue
                coin_id = str(item.get("id") or "").lower()
                if not coin_id:
                    continue
                signals["cryptoMarkets"][coin_id] = {
                    "provider": "CoinGecko",
                    "symbol": str(item.get("symbol") or "").upper(),
                    "name": str(item.get("name") or coin_id),
                    "price": number(item.get("current_price")),
                    "marketCap": number(item.get("market_cap")),
                    "volume24h": number(item.get("total_volume")),
                    "change1h": number(item.get("price_change_percentage_1h_in_currency")),
                    "change24h": number(item.get("price_change_percentage_24h_in_currency") or item.get("price_change_percentage_24h")),
                    "change7d": number(item.get("price_change_percentage_7d_in_currency")),
                }
        except Exception as error:  # noqa: BLE001
            self.status_for_error(signals, "CoinGecko", "", error)

    def add_fred(self, signals: Dict[str, object]) -> None:
        api_key = str(self.settings.get("fredApiKey") or "").strip()
        if not api_key:
            return
        series_ids = self.limited_targets(
            signals,
            "FRED",
            [item.upper() for item in symbol_list(self.settings.get("externalFredSeries") or "DGS10,DGS2,DFF")],
            "externalFredMaxSeries",
            5,
        )
        macro = signals.setdefault("macro", {})
        macro["series"] = {}
        for series_id in series_ids:
            try:
                def fetch_series():
                    url = "https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode({
                        "series_id": series_id,
                        "api_key": api_key,
                        "file_type": "json",
                        "limit": "2",
                        "sort_order": "desc",
                    })
                    payload = self.fetch_json(url, {"Accept": "application/json"})
                    observations = payload.get("observations") if isinstance(payload.get("observations"), list) else []
                    latest = next((item for item in observations if number(item.get("value"))), None)
                    if not latest:
                        raise RuntimeError("empty observations")
                    return {
                        "provider": "FRED",
                        "date": str(latest.get("date") or ""),
                        "value": number(latest.get("value")),
                    }

                macro["series"][series_id] = self.guarded_call("FRED", "series:" + series_id, fetch_series)
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "FRED", series_id + " ", error)
        series = macro.get("series") or {}
        if "DGS10" in series and "DGS2" in series:
            macro["yieldSpread10y2y"] = number(series["DGS10"].get("value")) - number(series["DGS2"].get("value"))

    def add_opendart(self, signals: Dict[str, object], positions: List[Position]) -> None:
        api_key = str(self.settings.get("opendartApiKey") or "").strip()
        if not api_key:
            return
        corp_codes = symbol_assignments(self.settings.get("externalDartCorpCodes") or "")
        if not corp_codes:
            return
        now = datetime.now(timezone.utc)
        lookback_days = int(number(self.settings.get("externalDartLookbackDays")) or 14)
        bgn_de = (now - timedelta(days=max(1, lookback_days))).strftime("%Y%m%d")
        end_de = now.strftime("%Y%m%d")
        positions_by_symbol = {str(position.symbol or "").upper(): position for position in positions}
        for symbol in self.limited_targets(signals, "OpenDART", self.dart_symbols(positions), "externalDartMaxSymbols", 5):
            raw_corp_code = str(corp_codes.get(symbol) or "").strip()
            if not raw_corp_code:
                continue
            corp_code = raw_corp_code.zfill(8)
            position = positions_by_symbol.get(symbol)
            try:
                def fetch_disclosure():
                    url = "https://opendart.fss.or.kr/api/list.json?" + urllib.parse.urlencode({
                        "crtfc_key": api_key,
                        "corp_code": corp_code,
                        "bgn_de": bgn_de,
                        "end_de": end_de,
                        "page_no": "1",
                        "page_count": "5",
                    })
                    payload = self.fetch_json(url, {"Accept": "application/json"})
                    status = str(payload.get("status") or "")
                    if status and status != "000":
                        raise RuntimeError(str(payload.get("message") or status))
                    items = payload.get("list") if isinstance(payload.get("list"), list) else []
                    latest = items[0] if items else {}
                    if not latest:
                        return {}
                    return {
                        "provider": "OpenDART",
                        "corpCode": corp_code,
                        "corpName": str(latest.get("corp_name") or (position.name if position else symbol)),
                        "reportName": str(latest.get("report_nm") or ""),
                        "receiptNo": str(latest.get("rcept_no") or ""),
                        "receiptDate": str(latest.get("rcept_dt") or ""),
                        "count": len(items),
                    }

                disclosure = self.guarded_call("OpenDART", "list:" + symbol, fetch_disclosure)
                if disclosure:
                    signals["dartDisclosures"][symbol] = disclosure
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "OpenDART", symbol + " ", error)

    def dart_symbols(self, positions: List[Position]) -> List[str]:
        symbols = []
        seen = set()
        for position in positions:
            symbol = str(position.symbol or "").upper()
            if not symbol or symbol in seen or not symbol.isdigit():
                continue
            seen.add(symbol)
            symbols.append(symbol)
        return symbols
