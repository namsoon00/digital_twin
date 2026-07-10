import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List

from ..domain.external_signal_quality import attach_external_signal_quality
from ..domain.investment_research import research_evidence_from_external_signals
from ..domain.market_data import number
from ..domain.portfolio import Position, utc_now_iso
from .settings import runtime_settings
from .sqlite_monitoring import SQLiteExternalSignalCache, SQLiteResearchEvidenceStore


JsonFetcher = Callable[[str, Dict[str, str]], object]
DISABLED_SETTING_VALUES = {"0", "false", "no", "off", "disabled"}


def default_json_fetcher(url: str, headers: Dict[str, str] = None, timeout: float = 12.0) -> Dict[str, object]:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=max(0.5, float(timeout or 12.0))) as response:
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
        evidence_store: SQLiteResearchEvidenceStore = None,
        fetch_json: JsonFetcher = None,
        sleep: Callable[[float], None] = None,
    ):
        self.settings = settings or runtime_settings()
        self.cache = cache or SQLiteExternalSignalCache()
        self.evidence_store = evidence_store or SQLiteResearchEvidenceStore()
        self.fetch_json = fetch_json or self.default_fetch_json
        self.sleep = sleep or time.sleep
        self.provider_state: Dict[str, object] = {}

    def default_fetch_json(self, url: str, headers: Dict[str, str] = None) -> Dict[str, object]:
        timeout = number(self.settings.get("externalApiTimeoutSeconds")) or 3.0
        return default_json_fetcher(url, headers, timeout=timeout)

    def signals_for_positions(self, positions: Iterable[Position]) -> Dict[str, object]:
        position_list = list(positions)
        cache_key = self.cache_key_for_positions(position_list)
        cached = self.cache.load()
        self.provider_state = self.provider_state_from(cached)
        entry = self.cache_entry(cached, cache_key)
        if self.is_cache_fresh(entry):
            signals = entry.get("signals")
            if isinstance(signals, dict):
                signals = attach_external_signal_quality(signals, positions=position_list, settings=self.settings)
                self.attach_stored_research_evidence(position_list, signals)
                self.record_research_evidence(position_list, signals)
                return signals

        signals = self.fetch_signals(position_list)
        self.record_research_evidence(position_list, signals)
        self.attach_stored_research_evidence(position_list, signals)
        self.cache.replace(self.next_cache_payload(cached, cache_key, signals))
        return signals

    def attach_stored_research_evidence(self, positions: Iterable[Position], signals: Dict[str, object]) -> None:
        if not self.evidence_store or not isinstance(signals, dict):
            return
        per_symbol: Dict[str, object] = {}
        seen = set()
        limit = self.int_setting("externalResearchEvidenceMaxItems", 8, 1)
        for position in positions or []:
            symbol = str(getattr(position, "symbol", "") or "").upper().strip()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            try:
                items = [
                    item.to_dict()
                    for item in self.evidence_store.latest(symbol=symbol, limit=limit)
                    if item.kind in {"news", "disclosure", "filing", "financial-fact", "market-move"}
                ]
            except Exception as error:  # noqa: BLE001 - stored evidence must not block realtime monitoring.
                signals.setdefault("statuses", []).append({
                    "source": "ResearchEvidence",
                    "ok": False,
                    "message": "research_evidence 조회 실패 · " + str(error)[:120],
                })
                continue
            if items:
                per_symbol[symbol] = items
        if per_symbol:
            signals["researchEvidence"] = per_symbol

    def record_research_evidence(self, positions: Iterable[Position], signals: Dict[str, object]) -> None:
        if not self.evidence_store or not isinstance(signals, dict):
            return
        evidence_by_id = {}
        for position in positions or []:
            symbol = str(getattr(position, "symbol", "") or "").upper().strip()
            if not symbol:
                continue
            for item in research_evidence_from_external_signals(symbol, signals):
                evidence_by_id[item.evidence_id] = item
        if not evidence_by_id:
            return
        try:
            self.evidence_store.upsert_many(evidence_by_id.values())
        except Exception as error:  # noqa: BLE001 - evidence history must not break market monitoring.
            signals.setdefault("statuses", []).append({
                "source": "ResearchEvidence",
                "ok": False,
                "message": "research_evidence 저장 실패 · " + str(error)[:120],
            })

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
            "cryptoIds": symbol_list(self.settings.get("externalCryptoIds") or "bitcoin,ethereum") if self.external_api_enabled("externalCoinGeckoEnabled") else [],
            "fredSeries": symbol_list(self.settings.get("externalFredSeries") or "DGS10,DGS2,DFF") if self.external_api_enabled("externalFredEnabled") else [],
            "fxRates": str(self.settings.get("fxRates") or ""),
            "secSymbols": self.sec_symbols(positions),
            "dartSymbols": self.dart_symbols(positions),
            "newsSymbols": self.news_symbols(positions),
            "alphaMax": str(self.settings.get("externalAlphaMaxSymbols") or "3"),
            "secMax": str(self.settings.get("externalSecMaxSymbols") or "3"),
            "newsProvider": self.news_provider(),
            "newsMax": str(self.settings.get("externalNewsMaxSymbols") or "3"),
            "newsLookbackHours": str(self.settings.get("externalNewsLookbackHours") or "48"),
            "fxRateSourceVersion": "alpha-vantage-currency-exchange-v1",
            "secMappings": symbol_assignments(self.settings.get("externalSecCompanyCiks") or ""),
            "dartLookbackDays": str(self.settings.get("externalDartLookbackDays") or "14"),
            "dartMappings": symbol_assignments(self.settings.get("externalDartCorpCodes") or ""),
            "alphaFundamentals": self.alpha_fundamentals_enabled(),
            "alphaFundamentalsMax": str(self.settings.get("externalAlphaFundamentalsMaxSymbols") or "1"),
            "settingsUpdatedAt": str(self.settings.get("updatedAt") or ""),
            "enabled": {
                "alpha": self.external_api_enabled("externalAlphaEnabled"),
                "alphaFundamentals": self.alpha_fundamentals_enabled(),
                "coingecko": self.external_api_enabled("externalCoinGeckoEnabled"),
                "fred": self.external_api_enabled("externalFredEnabled"),
                "opendart": self.external_api_enabled("externalDartEnabled"),
                "sec": self.sec_enabled(),
                "news": self.external_api_enabled("externalNewsEnabled"),
                "fx": self.external_api_enabled("externalFxRateEnabled"),
            },
            "configured": {
                "alpha": self.external_api_enabled("externalAlphaEnabled") and bool(str(self.settings.get("alphaVantageApiKey") or "").strip()),
                "alphaFundamentals": self.alpha_fundamentals_enabled(),
                "coingecko": self.external_api_enabled("externalCoinGeckoEnabled"),
                "fred": self.external_api_enabled("externalFredEnabled") and bool(str(self.settings.get("fredApiKey") or "").strip()),
                "opendart": self.external_api_enabled("externalDartEnabled") and bool(str(self.settings.get("opendartApiKey") or "").strip()),
                "sec": self.sec_enabled(),
                "news": self.external_api_enabled("externalNewsEnabled"),
                "fx": self.fx_live_rate_enabled(),
            },
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def is_cache_fresh(self, payload: Dict[str, object]) -> bool:
        fetched_at = parse_iso(str(payload.get("fetchedAt") or ""))
        if not fetched_at:
            return False
        minutes = self.cache_ttl_minutes()
        return datetime.now(timezone.utc) - fetched_at < timedelta(minutes=minutes)

    def cache_ttl_minutes(self) -> int:
        interval = int(number(self.settings.get("externalApiFetchIntervalMinutes")) or 30)
        interval = max(10, interval)
        freshness = int(number(self.settings.get("externalSignalCacheMaxAgeMinutes")) or number(self.settings.get("dataFreshnessExternalMaxAgeMinutes")) or interval)
        freshness = max(1, freshness)
        return min(interval, freshness)

    def fetch_signals(self, positions: List[Position]) -> Dict[str, object]:
        signals = {
            "fetchedAt": utc_now_iso(),
            "equityQuotes": {},
            "cryptoMarkets": {},
            "macro": {},
            "fxRates": {},
            "secFilings": {},
            "dartDisclosures": {},
            "newsHeadlines": {},
            "companyOverviews": {},
            "earningsReports": {},
            "researchEvidence": {},
            "statuses": [],
        }
        self.add_alpha_vantage(signals, positions)
        self.add_alpha_fundamentals(signals, positions)
        self.add_sec_edgar(signals, positions)
        self.add_coingecko(signals)
        self.add_fred(signals)
        self.add_fx_rates(signals)
        self.add_opendart(signals, positions)
        self.add_news_headlines(signals, positions)
        return attach_external_signal_quality(signals, positions=positions, settings=self.settings)

    def int_setting(self, key: str, fallback: int, minimum: int = 0) -> int:
        raw = self.settings.get(key)
        value = fallback if str(raw or "").strip() == "" else int(number(raw))
        return max(minimum, value)

    def external_api_enabled(self, key: str) -> bool:
        return str(self.settings.get(key) or "1").strip().lower() not in DISABLED_SETTING_VALUES

    def fx_live_rate_enabled(self) -> bool:
        return (
            self.external_api_enabled("externalFxRateEnabled")
            and self.external_api_enabled("externalAlphaEnabled")
            and bool(str(self.settings.get("alphaVantageApiKey") or "").strip())
        )

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
        if not self.external_api_enabled("externalAlphaEnabled"):
            return
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
        if not self.external_api_enabled("externalAlphaEnabled"):
            return []
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

    def alpha_fundamentals_enabled(self) -> bool:
        raw_enabled = str(self.settings.get("externalAlphaFundamentalsEnabled") or "0").strip().lower()
        return (
            self.external_api_enabled("externalAlphaEnabled")
            and raw_enabled not in DISABLED_SETTING_VALUES
            and bool(str(self.settings.get("alphaVantageApiKey") or "").strip())
        )

    def alpha_fundamental_symbols(self, positions: List[Position]) -> List[str]:
        if not self.alpha_fundamentals_enabled():
            return []
        max_symbols = int(number(self.settings.get("externalAlphaFundamentalsMaxSymbols")) or 1)
        return self.alpha_symbols(positions)[:max(1, max_symbols)]

    def add_alpha_fundamentals(self, signals: Dict[str, object], positions: List[Position]) -> None:
        if not self.alpha_fundamentals_enabled():
            return
        api_key = str(self.settings.get("alphaVantageApiKey") or "").strip()
        for symbol in self.alpha_fundamental_symbols(positions):
            try:
                def fetch_overview():
                    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode({
                        "function": "OVERVIEW",
                        "symbol": symbol,
                        "apikey": api_key,
                    })
                    payload = self.fetch_json(url, {"Accept": "application/json"})
                    if payload.get("Information") or payload.get("Note") or payload.get("Error Message"):
                        raise RuntimeError(str(payload.get("Information") or payload.get("Note") or payload.get("Error Message")))
                    if not payload.get("Symbol") and not payload.get("Name"):
                        raise RuntimeError("empty OVERVIEW")
                    return self.alpha_company_overview(symbol, payload)

                signals.setdefault("companyOverviews", {})[symbol] = self.guarded_call("Alpha Vantage", "OVERVIEW:" + symbol, fetch_overview)
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "Alpha Vantage", "OVERVIEW:" + symbol + " ", error)

            try:
                def fetch_earnings():
                    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode({
                        "function": "EARNINGS",
                        "symbol": symbol,
                        "apikey": api_key,
                    })
                    payload = self.fetch_json(url, {"Accept": "application/json"})
                    if payload.get("Information") or payload.get("Note") or payload.get("Error Message"):
                        raise RuntimeError(str(payload.get("Information") or payload.get("Note") or payload.get("Error Message")))
                    quarterly = payload.get("quarterlyEarnings") if isinstance(payload.get("quarterlyEarnings"), list) else []
                    latest = quarterly[0] if quarterly and isinstance(quarterly[0], dict) else {}
                    if not latest:
                        raise RuntimeError("empty EARNINGS")
                    return {
                        "provider": "Alpha Vantage",
                        "symbol": symbol,
                        "latestQuarter": {
                            "fiscalDateEnding": str(latest.get("fiscalDateEnding") or ""),
                            "reportedDate": str(latest.get("reportedDate") or ""),
                            "reportedEPS": number(latest.get("reportedEPS")),
                            "estimatedEPS": number(latest.get("estimatedEPS")),
                            "surprise": number(latest.get("surprise")),
                            "surprisePercentage": number(latest.get("surprisePercentage")),
                        },
                    }

                signals.setdefault("earningsReports", {})[symbol] = self.guarded_call("Alpha Vantage", "EARNINGS:" + symbol, fetch_earnings)
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "Alpha Vantage", "EARNINGS:" + symbol + " ", error)

    def alpha_company_overview(self, symbol: str, payload: Dict[str, object]) -> Dict[str, object]:
        return {
            "provider": "Alpha Vantage",
            "symbol": str(payload.get("Symbol") or symbol).upper(),
            "name": str(payload.get("Name") or symbol),
            "assetType": str(payload.get("AssetType") or ""),
            "exchange": str(payload.get("Exchange") or ""),
            "currency": str(payload.get("Currency") or ""),
            "country": str(payload.get("Country") or ""),
            "sector": str(payload.get("Sector") or ""),
            "industry": str(payload.get("Industry") or ""),
            "latestQuarter": str(payload.get("LatestQuarter") or ""),
            "marketCapitalization": number(payload.get("MarketCapitalization")),
            "revenueTTM": number(payload.get("RevenueTTM")),
            "grossProfitTTM": number(payload.get("GrossProfitTTM")),
            "ebitda": number(payload.get("EBITDA")),
            "profitMargin": number(payload.get("ProfitMargin")),
            "operatingMarginTTM": number(payload.get("OperatingMarginTTM")),
            "peRatio": number(payload.get("PERatio")),
            "pegRatio": number(payload.get("PEGRatio")),
            "forwardPE": number(payload.get("ForwardPE")),
            "beta": number(payload.get("Beta")),
            "dividendYield": number(payload.get("DividendYield")),
            "analystTargetPrice": number(payload.get("AnalystTargetPrice")),
            "analystRatingStrongBuy": number(payload.get("AnalystRatingStrongBuy")),
            "analystRatingBuy": number(payload.get("AnalystRatingBuy")),
            "analystRatingHold": number(payload.get("AnalystRatingHold")),
            "analystRatingSell": number(payload.get("AnalystRatingSell")),
            "analystRatingStrongSell": number(payload.get("AnalystRatingStrongSell")),
        }

    def sec_enabled(self) -> bool:
        return self.external_api_enabled("externalSecEnabled")

    def sec_user_agent(self) -> str:
        return str(self.settings.get("externalSecUserAgent") or "DigitalTwin/1.0 local-contact").strip() or "DigitalTwin/1.0 local-contact"

    def sec_headers(self) -> Dict[str, str]:
        return {"Accept": "application/json", "User-Agent": self.sec_user_agent()}

    def sec_symbol_key(self, symbol: str) -> str:
        return str(symbol or "").upper().replace(".", "-").strip()

    def sec_symbols(self, positions: List[Position]) -> List[str]:
        if not self.sec_enabled():
            return []
        max_symbols = int(number(self.settings.get("externalSecMaxSymbols")) or 3)
        symbols = []
        seen = set()
        for position in positions:
            if position.is_cash():
                continue
            symbol = self.sec_symbol_key(position.symbol)
            if not symbol or symbol in seen or symbol.isdigit():
                continue
            if position.market.upper() == "US" or position.currency.upper() == "USD":
                seen.add(symbol)
                symbols.append(symbol)
        return symbols[:max(1, max_symbols)]

    def normalize_cik(self, value: object) -> str:
        digits = "".join(ch for ch in str(value or "") if ch.isdigit())
        return digits.zfill(10) if digits else ""

    def add_sec_edgar(self, signals: Dict[str, object], positions: List[Position]) -> None:
        symbols = self.limited_targets(signals, "SEC EDGAR", self.sec_symbols(positions), "externalSecMaxSymbols", 3)
        if not symbols:
            return
        mappings = {
            self.sec_symbol_key(symbol): self.normalize_cik(cik)
            for symbol, cik in symbol_assignments(self.settings.get("externalSecCompanyCiks") or "").items()
            if self.normalize_cik(cik)
        }
        ticker_map: Dict[str, str] = {}
        if any(symbol not in mappings for symbol in symbols):
            try:
                def fetch_tickers():
                    return self.sec_ticker_lookup_payload(self.fetch_json("https://www.sec.gov/files/company_tickers.json", self.sec_headers()))

                ticker_map = self.guarded_call("SEC EDGAR", "company_tickers", fetch_tickers)
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "SEC EDGAR", "company_tickers ", error)

        for symbol in symbols:
            cik = mappings.get(symbol) or ticker_map.get(symbol) or ""
            if not cik:
                self.status(signals, "SEC EDGAR", True, symbol + " CIK mapping 없음")
                continue
            try:
                def fetch_submissions():
                    return self.fetch_json("https://data.sec.gov/submissions/CIK" + cik + ".json", self.sec_headers())

                submissions = self.guarded_call("SEC EDGAR", "submissions:" + symbol, fetch_submissions)
                filing = self.latest_sec_filing(submissions, cik)

                def fetch_facts():
                    return self.fetch_json("https://data.sec.gov/api/xbrl/companyfacts/CIK" + cik + ".json", self.sec_headers())

                facts = self.guarded_call("SEC EDGAR", "companyfacts:" + symbol, fetch_facts)
                signals["secFilings"][symbol] = {
                    "provider": "SEC EDGAR",
                    "symbol": symbol,
                    "cik": cik,
                    "companyName": str(submissions.get("name") or facts.get("entityName") or symbol),
                    "latestFiling": filing,
                    "facts": self.sec_company_facts_summary(facts),
                }
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "SEC EDGAR", symbol + " ", error)

    def sec_ticker_lookup_payload(self, payload: object) -> Dict[str, str]:
        if not isinstance(payload, dict):
            return {}
        values = payload.values()
        return {
            self.sec_symbol_key(item.get("ticker")): self.normalize_cik(item.get("cik_str"))
            for item in values
            if isinstance(item, dict) and item.get("ticker") and self.normalize_cik(item.get("cik_str"))
        }

    def latest_sec_filing(self, payload: Dict[str, object], cik: str) -> Dict[str, object]:
        recent = payload.get("filings", {}).get("recent", {}) if isinstance(payload.get("filings"), dict) else {}
        forms = recent.get("form") if isinstance(recent.get("form"), list) else []
        preferred_forms = {"10-K", "10-Q", "8-K", "20-F", "40-F", "6-K"}
        selected_index = next((index for index, form in enumerate(forms) if str(form or "").upper() in preferred_forms), None)
        if selected_index is None and forms:
            selected_index = 0
        if selected_index is None:
            return {}

        def recent_value(key: str) -> str:
            values = recent.get(key) if isinstance(recent.get(key), list) else []
            return str(values[selected_index] or "") if selected_index < len(values) else ""

        accession = recent_value("accessionNumber")
        primary_document = recent_value("primaryDocument")
        cik_path = str(int(cik)) if cik and cik.isdigit() else cik.lstrip("0")
        accession_path = accession.replace("-", "")
        filing_url = (
            "https://www.sec.gov/Archives/edgar/data/" + cik_path + "/" + accession_path + "/" + primary_document
            if cik_path and accession_path and primary_document
            else ""
        )
        return {
            "form": str(forms[selected_index] or ""),
            "filingDate": recent_value("filingDate"),
            "reportDate": recent_value("reportDate"),
            "accessionNumber": accession,
            "primaryDocument": primary_document,
            "url": filing_url,
        }

    def sec_company_facts_summary(self, payload: Dict[str, object]) -> Dict[str, object]:
        facts = payload.get("facts", {}).get("us-gaap", {}) if isinstance(payload.get("facts"), dict) else {}
        if not isinstance(facts, dict):
            facts = {}
        return {
            "entityName": str(payload.get("entityName") or ""),
            "revenue": self.latest_sec_fact(facts, [
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues",
                "SalesRevenueNet",
            ]),
            "netIncome": self.latest_sec_fact(facts, ["NetIncomeLoss", "ProfitLoss"]),
            "assets": self.latest_sec_fact(facts, ["Assets"]),
            "liabilities": self.latest_sec_fact(facts, ["Liabilities"]),
            "equity": self.latest_sec_fact(facts, ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
        }

    def latest_sec_fact(self, facts: Dict[str, object], tags: List[str]) -> Dict[str, object]:
        financial_forms = {"10-K", "10-Q", "20-F", "40-F"}
        for tag in tags:
            concept = facts.get(tag)
            units = concept.get("units") if isinstance(concept, dict) else {}
            values = units.get("USD") if isinstance(units, dict) else []
            if not isinstance(values, list):
                continue
            candidates = [
                item for item in values
                if isinstance(item, dict)
                and str(item.get("form") or "").upper() in financial_forms
                and item.get("val") not in (None, "")
            ]
            if not candidates:
                continue
            latest = sorted(
                candidates,
                key=lambda item: (str(item.get("filed") or ""), str(item.get("end") or "")),
                reverse=True,
            )[0]
            return {
                "tag": tag,
                "value": number(latest.get("val")),
                "end": str(latest.get("end") or ""),
                "filed": str(latest.get("filed") or ""),
                "fy": str(latest.get("fy") or ""),
                "fp": str(latest.get("fp") or ""),
                "form": str(latest.get("form") or ""),
            }
        return {}

    def add_coingecko(self, signals: Dict[str, object]) -> None:
        if not self.external_api_enabled("externalCoinGeckoEnabled"):
            return
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
                    "lastUpdated": str(item.get("last_updated") or ""),
                }
        except Exception as error:  # noqa: BLE001
            self.status_for_error(signals, "CoinGecko", "", error)

    def add_fred(self, signals: Dict[str, object]) -> None:
        if not self.external_api_enabled("externalFredEnabled"):
            return
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

    def add_fx_rates(self, signals: Dict[str, object]) -> None:
        assignments = symbol_assignments(self.settings.get("fxRates") or "")
        rates: Dict[str, object] = {}
        fetched_at = str(signals.get("fetchedAt") or utc_now_iso())
        live_rates = self.live_fx_rates(signals, sorted(assignments.keys()))
        for currency, raw_rate in sorted(assignments.items()):
            base = str(currency or "").upper().strip()
            if not base or base == "KRW":
                continue
            live = live_rates.get(base) if isinstance(live_rates.get(base), dict) else {}
            rate = number(live.get("rate")) or number(raw_rate)
            if rate <= 0:
                continue
            pair = base + "KRW"
            rates[pair] = {
                "provider": str(live.get("provider") or "RuntimeSettings"),
                "base": base,
                "quote": "KRW",
                "rate": rate,
                "value": rate,
                "lastUpdated": str(live.get("lastUpdated") or ""),
                "fetchedAt": fetched_at,
            }
        if rates:
            signals["fxRates"] = rates

    def live_fx_rates(self, signals: Dict[str, object], currencies: List[str]) -> Dict[str, Dict[str, object]]:
        if not self.fx_live_rate_enabled():
            return {}
        api_key = str(self.settings.get("alphaVantageApiKey") or "").strip()
        rows: Dict[str, Dict[str, object]] = {}
        for currency in currencies:
            base = str(currency or "").upper().strip()
            if not base or base == "KRW":
                continue
            try:
                def fetch_rate():
                    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode({
                        "function": "CURRENCY_EXCHANGE_RATE",
                        "from_currency": base,
                        "to_currency": "KRW",
                        "apikey": api_key,
                    })
                    payload = self.fetch_json(url, {"Accept": "application/json"})
                    data = payload.get("Realtime Currency Exchange Rate") if isinstance(payload.get("Realtime Currency Exchange Rate"), dict) else payload
                    rate = number(
                        data.get("5. Exchange Rate")
                        or data.get("exchangeRate")
                        or data.get("rate")
                        or data.get("value")
                    ) if isinstance(data, dict) else 0.0
                    if not rate:
                        reason = ""
                        if isinstance(payload, dict):
                            reason = str(payload.get("Note") or payload.get("Information") or payload.get("Error Message") or "")
                        raise RuntimeError(reason or "empty FX rate")
                    return {
                        "provider": "Alpha Vantage",
                        "rate": rate,
                        "lastUpdated": str(
                            data.get("6. Last Refreshed")
                            or data.get("lastRefreshed")
                            or data.get("lastUpdated")
                            or ""
                        ) if isinstance(data, dict) else "",
                    }

                rows[base] = self.guarded_call("Alpha Vantage", "fx:" + base + "KRW", fetch_rate)
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, "Alpha Vantage", "fx:" + base + "KRW", error)
        return rows

    def add_opendart(self, signals: Dict[str, object], positions: List[Position]) -> None:
        if not self.external_api_enabled("externalDartEnabled"):
            return
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
        if not self.external_api_enabled("externalDartEnabled"):
            return []
        symbols = []
        seen = set()
        for position in positions:
            symbol = str(position.symbol or "").upper()
            if not symbol or symbol in seen or not symbol.isdigit():
                continue
            seen.add(symbol)
            symbols.append(symbol)
        return symbols

    def add_news_headlines(self, signals: Dict[str, object], positions: List[Position]) -> None:
        if not self.external_api_enabled("externalNewsEnabled"):
            return
        positions_by_symbol = {str(position.symbol or "").upper(): position for position in positions if not position.is_cash()}
        for symbol in self.limited_targets(signals, "News", self.news_symbols(positions), "externalNewsMaxSymbols", 3):
            position = positions_by_symbol.get(symbol)
            if not position:
                continue
            source = "News"
            try:
                provider = self.news_provider_for_position(position)
                source = "Alpha Vantage News" if provider == "alpha_vantage" else "GDELT News"
                target = "NEWS_SENTIMENT:" + symbol if provider == "alpha_vantage" else "doc:" + symbol

                def fetch_news():
                    return self.fetch_alpha_vantage_news(symbol, position) if provider == "alpha_vantage" else self.fetch_gdelt_news(symbol, position)

                news = self.guarded_call(source, target, fetch_news)
                if news:
                    signals.setdefault("newsHeadlines", {})[symbol] = news
            except Exception as error:  # noqa: BLE001
                self.status_for_error(signals, source, symbol + " ", error)

    def fetch_gdelt_news(self, symbol: str, position: Position) -> Dict[str, object]:
        query = self.gdelt_news_query(position)
        lookback_hours = self.int_setting("externalNewsLookbackHours", 48, 1)
        url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode({
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": "5",
            "sort": "HybridRel",
            "timespan": str(lookback_hours) + "h",
        })
        payload = self.fetch_json(url, {"Accept": "application/json", "User-Agent": "DigitalTwin/1.0"})
        articles = payload.get("articles") if isinstance(payload, dict) and isinstance(payload.get("articles"), list) else []
        items = []
        seen = set()
        for article in articles[:10]:
            if not isinstance(article, dict):
                continue
            title = str(article.get("title") or "").strip()
            url_value = str(article.get("url") or "").strip()
            dedupe_key = url_value or title
            if not title or not url_value or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            items.append({
                "title": title,
                "url": url_value,
                "domain": str(article.get("domain") or "").strip(),
                "sourceCountry": str(article.get("sourceCountry") or "").strip(),
                "language": str(article.get("language") or "").strip(),
                "seenDate": str(article.get("seendate") or "").strip(),
            })
            if len(items) >= 5:
                break
        return {
            "provider": "GDELT",
            "symbol": symbol,
            "name": str(position.name or symbol),
            "query": query,
            "lookbackHours": lookback_hours,
            "items": items,
            "count": len(items),
            "fetchedAt": utc_now_iso(),
        }

    def fetch_alpha_vantage_news(self, symbol: str, position: Position) -> Dict[str, object]:
        api_key = str(self.settings.get("alphaVantageApiKey") or "").strip()
        if not api_key:
            raise RuntimeError("Alpha Vantage API key 없음")
        lookback_hours = self.int_setting("externalNewsLookbackHours", 48, 1)
        time_from = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).strftime("%Y%m%dT%H%M")
        url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode({
            "function": "NEWS_SENTIMENT",
            "tickers": symbol,
            "apikey": api_key,
            "limit": "5",
            "sort": "LATEST",
            "time_from": time_from,
        })
        payload = self.fetch_json(url, {"Accept": "application/json"})
        if isinstance(payload, dict) and (payload.get("Information") or payload.get("Note")):
            raise RuntimeError(str(payload.get("Information") or payload.get("Note")))
        feed = payload.get("feed") if isinstance(payload, dict) and isinstance(payload.get("feed"), list) else []
        items = []
        seen = set()
        for article in feed[:10]:
            if not isinstance(article, dict):
                continue
            title = str(article.get("title") or "").strip()
            url_value = str(article.get("url") or "").strip()
            dedupe_key = url_value or title
            if not title or not url_value or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            ticker_sentiment = self.alpha_news_ticker_sentiment(article, symbol)
            items.append({
                "title": title,
                "url": url_value,
                "domain": str(article.get("source") or "").strip(),
                "summary": str(article.get("summary") or "").strip(),
                "seenDate": str(article.get("time_published") or "").strip(),
                "overallSentimentScore": number(article.get("overall_sentiment_score")),
                "overallSentimentLabel": str(article.get("overall_sentiment_label") or "").strip(),
                "relevanceScore": number(ticker_sentiment.get("relevance_score")) if ticker_sentiment else 0.0,
                "tickerSentimentScore": number(ticker_sentiment.get("ticker_sentiment_score")) if ticker_sentiment else 0.0,
                "tickerSentimentLabel": str(ticker_sentiment.get("ticker_sentiment_label") or "").strip() if ticker_sentiment else "",
            })
            if len(items) >= 5:
                break
        return {
            "provider": "Alpha Vantage",
            "symbol": symbol,
            "name": str(position.name or symbol),
            "query": "NEWS_SENTIMENT:" + symbol,
            "lookbackHours": lookback_hours,
            "items": items,
            "count": len(items),
            "fetchedAt": utc_now_iso(),
        }

    def alpha_news_ticker_sentiment(self, article: Dict[str, object], symbol: str) -> Dict[str, object]:
        ticker_sentiments = article.get("ticker_sentiment") if isinstance(article.get("ticker_sentiment"), list) else []
        normalized = str(symbol or "").upper()
        for item in ticker_sentiments:
            if isinstance(item, dict) and str(item.get("ticker") or "").upper() == normalized:
                return item
        return {}

    def news_provider(self) -> str:
        raw = str(self.settings.get("externalNewsProvider") or "auto").strip().lower().replace("-", "_")
        if raw in {"alpha", "alphavantage", "alpha_vantage", "alpha vantage"}:
            return "alpha_vantage"
        if raw == "gdelt":
            return "gdelt"
        return "auto"

    def news_provider_for_position(self, position: Position) -> str:
        provider = self.news_provider()
        if provider in {"gdelt", "alpha_vantage"}:
            return provider
        symbol = str(position.symbol or "").upper()
        market = str(position.market or "").upper()
        currency = str(position.currency or "").upper()
        has_alpha_key = bool(str(self.settings.get("alphaVantageApiKey") or "").strip())
        if has_alpha_key and not symbol.isdigit() and (market == "US" or currency == "USD"):
            return "alpha_vantage"
        return "gdelt"

    def add_gdelt_news(self, signals: Dict[str, object], positions: List[Position]) -> None:
        self.add_news_headlines(signals, positions)

    def news_symbols(self, positions: List[Position]) -> List[str]:
        if not self.external_api_enabled("externalNewsEnabled"):
            return []
        symbols = []
        seen = set()
        for position in positions:
            if position.is_cash():
                continue
            symbol = str(position.symbol or "").upper()
            name = str(position.name or "").strip()
            if not symbol or symbol in seen or not name:
                continue
            seen.add(symbol)
            symbols.append(symbol)
        return symbols

    def gdelt_news_query(self, position: Position) -> str:
        terms = []
        for raw in [position.name, position.symbol]:
            text = str(raw or "").replace('"', " ").strip()
            if text and text not in terms:
                terms.append(text)
        quoted = ['"' + term + '"' for term in terms[:2]]
        return "(" + " OR ".join(quoted) + ")" if quoted else '"' + str(position.symbol or "").upper() + '"'
