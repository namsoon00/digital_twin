import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List

from ..domain.external_signal_quality import attach_external_signal_quality
from ..domain.investment_research import research_evidence_from_external_signals
from ..domain.market_data import number
from ..domain.portfolio import Position, utc_now_iso
from .external_signal_utils import (
    DISABLED_SETTING_VALUES,
    ExternalApiGuard,
    ExternalRateLimited,
    default_json_fetcher,
    parse_iso,
    symbol_assignments,
    symbol_list,
)


class ExternalSignalCoreMixin:
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
