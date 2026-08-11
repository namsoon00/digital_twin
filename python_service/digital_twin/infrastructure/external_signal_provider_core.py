import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List

from ..domain.company_knowledge import (
    COMPANY_KNOWLEDGE_CACHE_VERSION,
    company_knowledge_by_symbol,
    merge_company_knowledge_rows,
)
from ..domain.crypto_market_signals import (
    combine_crypto_market_snapshots,
    crypto_market_positions,
    crypto_market_snapshot,
    merge_crypto_market_snapshot,
)
from ..domain.external_signal_quality import attach_external_signal_quality
from ..domain.investment_evidence_governance import claim_policy, governed_evidence
from ..domain.investment_research import NewsCollectionTarget, research_evidence_from_external_signals
from ..domain.market_data import number
from ..domain.portfolio import Position, utc_now_iso
from .external_signal_utils import (
    DISABLED_SETTING_VALUES,
    ExternalApiGuard,
    ExternalRateLimited,
    default_bytes_fetcher,
    default_json_fetcher,
    default_text_fetcher,
    parse_iso,
    sanitize_sensitive_text,
    symbol_assignments,
    symbol_list,
)


class ExternalSignalCoreMixin:
    def provider_timeout_seconds(
        self,
        setting_key: str,
        fallback: float,
        minimum: float = 1.0,
        maximum: float = 30.0,
    ) -> float:
        """Resolve a bounded timeout for one slower external provider."""
        configured = number(self.settings.get(setting_key))
        timeout = float(configured) if configured and configured > 0 else float(fallback)
        return max(float(minimum), min(float(maximum), timeout))

    def fetch_json_with_timeout(
        self,
        url: str,
        headers: Dict[str, str] = None,
        setting_key: str = "",
        fallback_timeout: float = 3.0,
        minimum_timeout: float = 1.0,
        maximum_timeout: float = 30.0,
    ) -> Dict[str, object]:
        """Use a provider timeout without changing injected fetcher APIs."""
        if getattr(self, "_uses_default_json_fetcher", False):
            timeout = self.provider_timeout_seconds(
                setting_key,
                fallback_timeout,
                minimum_timeout,
                maximum_timeout,
            )
            return default_json_fetcher(url, headers, timeout=timeout)
        return self.fetch_json(url, headers)

    def default_fetch_json(self, url: str, headers: Dict[str, str] = None) -> Dict[str, object]:
        timeout = number(self.settings.get("externalApiTimeoutSeconds")) or 3.0
        return default_json_fetcher(url, headers, timeout=timeout)

    def default_fetch_text(self, url: str, headers: Dict[str, str] = None) -> str:
        timeout = number(self.settings.get("externalApiTimeoutSeconds")) or 3.0
        return default_text_fetcher(url, headers, timeout=timeout)

    def default_fetch_bytes(self, url: str, headers: Dict[str, str] = None) -> bytes:
        timeout = number(self.settings.get("externalApiTimeoutSeconds")) or 3.0
        return default_bytes_fetcher(url, headers, timeout=timeout)

    def signals_for_positions(
        self,
        positions: Iterable[Position],
        cache_scope: str = "general",
    ) -> Dict[str, object]:
        position_list = list(positions)
        normalized_cache_scope = str(cache_scope or "general").strip().lower() or "general"
        subject_count = len({
            str(getattr(position, "symbol", "") or "").upper().strip()
            for position in position_list
            if str(getattr(position, "symbol", "") or "").strip()
        })
        cache_key = self.cache_key_for_positions(position_list)
        cached = self.cache.load()
        cache_only = self.external_signal_cache_only()
        shared_company_knowledge = self.load_shared_company_knowledge(
            cached,
            persist_backfill=not cache_only,
        )
        self.provider_state = self.provider_state_from(cached)
        crypto_snapshot, crypto_cache_state = self.load_crypto_market_snapshot(cached)
        self._crypto_market_snapshot = crypto_snapshot
        self._crypto_market_cache_state = crypto_cache_state
        entry = self.cache_entry(cached, cache_key)
        cache_fresh = self.is_cache_fresh(entry)
        if cache_fresh or cache_only:
            signals = entry.get("signals") if isinstance(entry, dict) else None
            if isinstance(signals, dict):
                cache_needs_store = cache_fresh and self.should_promote_cache_scope(entry, normalized_cache_scope)
                # Cache-only callers are the reasoning path. They must never
                # refresh a vendor inline. The metadata-only legacy promotion
                # above preserves a pre-existing cache entry without changing
                # any vendor payload.
                signals = deepcopy(signals)
                self.attach_shared_company_knowledge(signals, shared_company_knowledge, position_list)
                crypto_changed = self.merge_cached_crypto_market_snapshot(
                    signals,
                    crypto_snapshot,
                    crypto_cache_state,
                )
                if crypto_changed and cache_fresh and not cache_only:
                    cache_needs_store = True
                if cache_fresh and not cache_only and self.crypto_refresh_due(signals):
                    self.refresh_cached_coingecko(signals)
                    # The general cache timestamp remains untouched. Only the
                    # CoinGecko payload and provider state are refreshed.
                    cache_needs_store = True
                if cache_needs_store:
                    self.cache.replace(self.next_cache_payload(
                        cached,
                        cache_key,
                        signals,
                        cache_scope=normalized_cache_scope,
                        subject_count=subject_count,
                    ))
                if cache_only:
                    self.status(
                        signals,
                        "External signal cache",
                        True,
                        (
                            "fresh cache reused by reasoning worker"
                            if cache_fresh
                            else "stale cache reused; collection worker refresh is pending"
                        ),
                        deferred=not cache_fresh,
                        dataUsable=cache_fresh,
                        cacheOnly=True,
                    )
                self.refresh_broker_fx_rates(signals, position_list)
                signals = attach_external_signal_quality(signals, positions=position_list, settings=self.settings)
                self.attach_stored_research_evidence(position_list, signals)
                if not cache_only:
                    self.record_research_evidence(position_list, signals)
                return signals
            if cache_only:
                signals = self.empty_cache_only_signals()
                self.merge_cached_crypto_market_snapshot(
                    signals,
                    crypto_snapshot,
                    crypto_cache_state,
                )
                self.attach_stored_research_evidence(position_list, signals)
                return attach_external_signal_quality(signals, positions=position_list, settings=self.settings)

        signals = self.fetch_signals(position_list)
        refreshed_company_knowledge = self.company_knowledge_from_signals(signals)
        shared_company_knowledge = self.merge_company_knowledge_maps(
            shared_company_knowledge,
            refreshed_company_knowledge,
        )
        self.persist_shared_company_knowledge(shared_company_knowledge)
        self.attach_shared_company_knowledge(signals, shared_company_knowledge, position_list)
        self.refresh_broker_fx_rates(signals, position_list)
        self.record_research_evidence(position_list, signals)
        self.attach_stored_research_evidence(position_list, signals)
        self.cache.replace(self.next_cache_payload(
            cached,
            cache_key,
            signals,
            cache_scope=normalized_cache_scope,
            subject_count=subject_count,
        ))
        return signals

    @staticmethod
    def merge_company_knowledge_maps(*groups: Dict[str, object]) -> Dict[str, Dict[str, object]]:
        result: Dict[str, Dict[str, object]] = {}
        for group in groups:
            if not isinstance(group, dict):
                continue
            for raw_symbol, row in group.items():
                symbol = str(raw_symbol or "").upper().strip()
                if not symbol or not isinstance(row, dict):
                    continue
                result[symbol] = merge_company_knowledge_rows(result.get(symbol, {}), row)
        return result

    @staticmethod
    def company_knowledge_from_signals(signals: Dict[str, object]) -> Dict[str, Dict[str, object]]:
        source = dict(signals or {}) if isinstance(signals, dict) else {}
        symbols = {
            str(symbol or "").upper().strip()
            for group in ("companyOverviews", "yfinanceData", "secFilings", "dartDisclosures", "companyKnowledge")
            for symbol in ((source.get(group) or {}).keys() if isinstance(source.get(group), dict) else [])
            if str(symbol or "").strip()
        }
        generated = company_knowledge_by_symbol(source, symbols)
        existing = source.get("companyKnowledge") if isinstance(source.get("companyKnowledge"), dict) else {}
        return ExternalSignalCoreMixin.merge_company_knowledge_maps(existing, generated)

    def load_shared_company_knowledge(
        self,
        cached_external_signals: Dict[str, object],
        *,
        persist_backfill: bool = False,
    ) -> Dict[str, Dict[str, object]]:
        dedicated = {}
        if getattr(self, "company_knowledge_cache", None):
            try:
                payload = self.company_knowledge_cache.load()
                dedicated = payload.get("symbols") if isinstance(payload, dict) and isinstance(payload.get("symbols"), dict) else {}
            except Exception:
                dedicated = {}
        if dedicated:
            normalized = self.merge_company_knowledge_maps(dedicated)
            if persist_backfill and normalized != dedicated:
                self.persist_shared_company_knowledge(normalized)
            return normalized
        aggregate_groups = []
        payload = cached_external_signals if isinstance(cached_external_signals, dict) else {}
        direct = payload.get("signals") if isinstance(payload.get("signals"), dict) else {}
        if direct:
            aggregate_groups.append(self.company_knowledge_from_signals(direct))
        entries = payload.get("entries") if isinstance(payload.get("entries"), dict) else {}
        for entry in entries.values():
            signals = entry.get("signals") if isinstance(entry, dict) and isinstance(entry.get("signals"), dict) else {}
            if signals:
                aggregate_groups.append(self.company_knowledge_from_signals(signals))
        merged = self.merge_company_knowledge_maps(dedicated, *aggregate_groups)
        if persist_backfill and merged and merged != dedicated:
            self.persist_shared_company_knowledge(merged)
        return merged

    def persist_shared_company_knowledge(self, symbols: Dict[str, object]) -> None:
        if not getattr(self, "company_knowledge_cache", None) or not symbols:
            return
        try:
            self.company_knowledge_cache.replace({
                "schemaVersion": COMPANY_KNOWLEDGE_CACHE_VERSION,
                "updatedAt": utc_now_iso(),
                "symbols": symbols,
            })
        except Exception:
            # The existing external cache remains a complete backfill source.
            pass

    @staticmethod
    def attach_shared_company_knowledge(
        signals: Dict[str, object],
        shared: Dict[str, object],
        positions: Iterable[Position],
    ) -> None:
        if not isinstance(signals, dict):
            return
        selected = {
            str(getattr(position, "symbol", "") or "").upper().strip()
            for position in positions or []
            if str(getattr(position, "symbol", "") or "").strip()
        }
        current = signals.get("companyKnowledge") if isinstance(signals.get("companyKnowledge"), dict) else {}
        attached = {}
        for symbol in sorted(selected):
            merged = merge_company_knowledge_rows(
                shared.get(symbol) if isinstance(shared, dict) else {},
                current.get(symbol) if isinstance(current, dict) else {},
            )
            if merged:
                attached[symbol] = merged
        if attached:
            signals["companyKnowledge"] = attached

    def external_signal_cache_only(self) -> bool:
        value = str(self.settings.get("_externalSignalsCacheOnly") or "").strip().lower()
        return value in {"1", "true", "yes", "on"}

    @staticmethod
    def should_promote_cache_scope(entry: Dict[str, object], requested_scope: str) -> bool:
        """Keep legacy aggregate entries from being evicted before refresh."""
        pinned_scopes = {"account-snapshot", "market-monitor"}
        target = str(requested_scope or "").strip().lower()
        current = str((entry or {}).get("cacheScope") or "").strip().lower()
        return target in pinned_scopes and current not in pinned_scopes

    def empty_cache_only_signals(self) -> Dict[str, object]:
        signals = {
            "fetchedAt": "",
            "cryptoFetchedAt": "",
            "cryptoLastAttemptAt": "",
            "equityQuotes": {},
            "cryptoMarkets": {},
            "macro": {},
            "fxRates": {},
            "secFilings": {},
            "dartDisclosures": {},
            "newsHeadlines": {},
            "companyOverviews": {},
            "companyKnowledge": {},
            "earningsReports": {},
            "yfinanceData": {},
            "researchEvidence": {},
            "statuses": [],
        }
        self.status(
            signals,
            "External signal cache",
            True,
            "no cached external signals; collection worker has not supplied a usable snapshot",
            deferred=True,
            dataUsable=False,
            cacheOnly=True,
        )
        return signals

    def aggregate_crypto_market_snapshot(self, cached: Dict[str, object]) -> Dict[str, object]:
        """Recover the newest CoinGecko facts from any legacy aggregate key."""

        payload = cached if isinstance(cached, dict) else {}
        candidates = []
        direct_signals = payload.get("signals") if isinstance(payload.get("signals"), dict) else {}
        if direct_signals:
            candidates.append(crypto_market_snapshot(direct_signals))
        entries = payload.get("entries") if isinstance(payload.get("entries"), dict) else {}
        for entry in entries.values():
            signals = entry.get("signals") if isinstance(entry, dict) and isinstance(entry.get("signals"), dict) else {}
            if signals:
                candidates.append(crypto_market_snapshot(signals))
        return combine_crypto_market_snapshots(*[item for item in candidates if item])

    def load_crypto_market_snapshot(self, cached: Dict[str, object]) -> tuple:
        if not self.external_api_enabled("externalCoinGeckoEnabled"):
            return {}, "disabled"
        self._crypto_cache_error = ""
        dedicated = {}
        if self.crypto_cache:
            try:
                dedicated = self.crypto_cache.load()
            except Exception as error:  # noqa: BLE001 - aggregate migration remains usable.
                self._crypto_cache_error = self.safe_error_message(error)
        aggregate = self.aggregate_crypto_market_snapshot(cached)
        combined = combine_crypto_market_snapshots(aggregate, dedicated)
        if not combined:
            return {}, "missing"
        cache_state = "dedicated" if dedicated else "aggregate-fallback"
        normalized_dedicated = combine_crypto_market_snapshots(dedicated)
        dedicated_needs_normalization = bool(dedicated and dedicated != normalized_dedicated)
        if self.crypto_cache and (combined != normalized_dedicated or dedicated_needs_normalization):
            try:
                self.crypto_cache.replace(combined)
                cache_state = (
                    "aggregate-migrated"
                    if not dedicated
                    else "dedicated-normalized"
                    if dedicated_needs_normalization and combined == normalized_dedicated
                    else "dedicated-reconciled"
                )
            except Exception as error:  # noqa: BLE001 - in-memory facts remain usable for this cycle.
                self._crypto_cache_error = self.safe_error_message(error)
        return combined, cache_state

    def merge_cached_crypto_market_snapshot(
        self,
        signals: Dict[str, object],
        snapshot: Dict[str, object],
        cache_state: str,
    ) -> bool:
        changed = merge_crypto_market_snapshot(signals, snapshot, cache_state=cache_state) if snapshot else False
        cache_error = str(getattr(self, "_crypto_cache_error", "") or "")
        if cache_error:
            self.status(
                signals,
                "Crypto market cache",
                False,
                "CoinGecko 전용 캐시 조회/저장 실패 · " + cache_error,
                operationalAlert=True,
            )
        return changed

    def persist_crypto_market_snapshot(self, signals: Dict[str, object]) -> bool:
        snapshot = crypto_market_snapshot(signals)
        if not snapshot or not self.crypto_cache:
            return False
        try:
            self.crypto_cache.replace(snapshot)
            self._crypto_market_snapshot = snapshot
            self._crypto_market_cache_state = "dedicated"
            merge_crypto_market_snapshot(signals, snapshot, cache_state="dedicated")
        except Exception as error:  # noqa: BLE001 - vendor result remains usable in the current cycle.
            self.status(
                signals,
                "Crypto market cache",
                False,
                "CoinGecko 전용 캐시 저장 실패 · " + self.safe_error_message(error),
                operationalAlert=True,
            )
            return False
        try:
            history = self.persist_crypto_market_history(snapshot)
            if history:
                signals["cryptoHistory"] = history
        except Exception as error:  # noqa: BLE001 - current vendor facts remain usable.
            self.status(
                signals,
                "Crypto market history",
                False,
                "CoinGecko 시계열 저장 실패 · " + self.safe_error_message(error),
                dataUsable=True,
                operationalAlert=True,
            )
        return True

    def persist_crypto_market_history(self, snapshot: Dict[str, object]) -> Dict[str, object]:
        store = getattr(self, "crypto_time_series_store", None)
        if store is None and bool(getattr(self, "_default_crypto_time_series_store", False)):
            factory = getattr(self, "_crypto_time_series_store_factory", None)
            if callable(factory):
                store = factory(self.settings)
                self.crypto_time_series_store = store
        if store is None or not hasattr(store, "record_positions"):
            return {}
        fetched_at = str(snapshot.get("fetchedAt") or "").strip()
        markets = snapshot.get("markets") if isinstance(snapshot.get("markets"), dict) else {}
        if not fetched_at or not markets:
            return {}
        positions = crypto_market_positions({
            "cryptoMarkets": markets,
            "cryptoFetchedAt": fetched_at,
            "cryptoSourceAsOf": str(snapshot.get("sourceAsOf") or ""),
            "cryptoFreshness": {
                "status": "fresh",
                "fetchedAt": fetched_at,
            },
        })
        result = store.record_positions(
            "__market_data__",
            positions,
            fetched_at,
            provider="CoinGecko coins/markets",
            replace=False,
        )
        return {
            "provider": "CoinGecko",
            "dataset": "coins/markets",
            "observedAt": fetched_at,
            "savedCount": int((result or {}).get("savedCount") or 0),
            "symbolCount": int((result or {}).get("symbolCount") or 0),
        }

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
        targets: Dict[str, NewsCollectionTarget] = {}
        for position in positions or []:
            symbol = str(getattr(position, "symbol", "") or "").upper().strip()
            if not symbol:
                continue
            targets[symbol] = NewsCollectionTarget(
                symbol=symbol,
                name=str(getattr(position, "name", "") or symbol),
                market=str(getattr(position, "market", "") or ""),
                currency=str(getattr(position, "currency", "") or ""),
            )
            for item in research_evidence_from_external_signals(symbol, signals):
                evidence_by_id[item.evidence_id] = item
        if not evidence_by_id:
            return
        try:
            governed_by_id = {}
            max_age = self.int_setting("newsEvidenceMaxAgeMinutes", 360, 5)
            for symbol, target in targets.items():
                fresh = [item for item in evidence_by_id.values() if str(item.symbol or "").upper() == symbol]
                if not fresh:
                    continue
                try:
                    cached = self.evidence_store.latest(symbol=symbol, limit=100)
                except Exception:  # noqa: BLE001 - new evidence remains conservatively governed without history.
                    cached = []
                corpus = list(fresh) + list(cached)
                governed_evidence(
                    corpus,
                    target,
                    max_age,
                    str(self.settings.get("investmentBrainResearchMinimumSourceTrustState") or "standard"),
                    policy=claim_policy(self.settings),
                )
                governed_by_id.update({str(item.evidence_id or ""): item for item in corpus if str(item.evidence_id or "")})
            self.evidence_store.upsert_many(governed_by_id.values() or evidence_by_id.values())
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

    def next_cache_payload(
        self,
        cached: Dict[str, object],
        cache_key: str,
        signals: Dict[str, object],
        cache_scope: str = "general",
        subject_count: int = 0,
    ) -> Dict[str, object]:
        entries = cached.get("entries") if isinstance(cached.get("entries"), dict) else {}
        entries = dict(entries)
        entries[cache_key] = {
            "fetchedAt": signals.get("fetchedAt") or utc_now_iso(),
            "signals": signals,
            "cacheScope": str(cache_scope or "general").strip().lower() or "general",
            "subjectCount": max(0, int(subject_count or 0)),
        }
        try:
            max_entries = int(float(str(self.settings.get("externalSignalCacheMaxEntries") or "6")))
        except (TypeError, ValueError):
            max_entries = 6
        max_entries = max(2, min(20, max_entries))
        ordered = sorted(
            entries.items(),
            key=lambda item: str(item[1].get("fetchedAt") if isinstance(item[1], dict) else ""),
            reverse=True,
        )
        # Account snapshots are the only cache entries used by the realtime
        # monitor. Keep the newest two even when per-symbol research refreshes
        # create more cache entries than the normal cap.
        pinned_scopes = {"account-snapshot", "market-monitor"}
        all_pinned = [
            item for item in ordered
            if isinstance(item[1], dict)
            and str(item[1].get("cacheScope") or "").strip().lower() in pinned_scopes
        ]
        pinned = all_pinned[:min(2, max_entries)]
        pinned_keys = {key for key, _entry in all_pinned}
        remaining = [item for item in ordered if item[0] not in pinned_keys]
        selected = pinned + remaining[:max(0, max_entries - len(pinned))]
        return {"schemaVersion": 1, "entries": dict(selected), "providerState": dict(self.provider_state)}

    def provider_state_from(self, payload: Dict[str, object]) -> Dict[str, object]:
        state = payload.get("providerState") if isinstance(payload.get("providerState"), dict) else {}
        rows = {}
        for key, value in state.items():
            if not isinstance(value, dict):
                continue
            row = dict(value)
            if row.get("lastError"):
                row["lastError"] = sanitize_sensitive_text(row.get("lastError"))[:120]
            rows[str(key)] = row
        return rows

    def cache_key_for_positions(self, positions: List[Position]) -> str:
        payload = {
            "alphaSymbols": self.alpha_symbols(positions),
            "securityLineMappings": str(self.settings.get("securityLineMappings") or ""),
            "alphaRelatedSymbolsEnabled": str(self.settings.get("externalAlphaRelatedSymbolsEnabled") or "1"),
            "alphaRelatedMaxSymbols": str(self.settings.get("externalAlphaRelatedMaxSymbols") or "8"),
            "cryptoIds": symbol_list(self.settings.get("externalCryptoIds") or "bitcoin,ethereum") if self.external_api_enabled("externalCoinGeckoEnabled") else [],
            "cryptoFetchIntervalMinutes": str(self.crypto_fetch_interval_minutes()),
            "fredSeries": symbol_list(self.settings.get("externalFredSeries") or "DGS10,DGS2,DFF") if self.external_api_enabled("externalFredEnabled") else [],
            "fredTimeoutSeconds": str(self.settings.get("externalFredTimeoutSeconds") or "8"),
            "fxRates": str(self.settings.get("fxRates") or ""),
            "secSymbols": self.sec_symbols(positions),
            "dartSymbols": self.dart_symbols(positions),
            "newsSymbols": self.news_symbols(positions),
            "alphaMax": str(self.settings.get("externalAlphaMaxSymbols") or "3"),
            "secMax": str(self.settings.get("externalSecMaxSymbols") or "3"),
            "secDocumentText": str(self.settings.get("externalSecDocumentTextEnabled") or "1"),
            "secDocumentTextMaxChars": str(self.settings.get("externalSecDocumentTextMaxChars") or "6000"),
            "secDocumentAccess": "contact-configured" if self.sec_document_access_configured() else "metadata-only",
            "newsProvider": self.news_provider(),
            "newsMax": str(self.settings.get("externalNewsMaxSymbols") or "3"),
            "newsLookbackHours": str(self.settings.get("externalNewsLookbackHours") or "48"),
            "yfinanceSymbols": self.yfinance_target_symbols(positions),
            "yfinanceMax": str(self.settings.get("externalYFinanceMaxSymbols") or "8"),
            "yfinanceHistoryPeriod": str(self.settings.get("externalYFinanceHistoryPeriod") or "1y"),
            "yfinanceHistoryInterval": str(self.settings.get("externalYFinanceHistoryInterval") or "1d"),
            "yfinanceFinancialPeriods": str(self.settings.get("externalYFinanceFinancialPeriods") or "4"),
            "yfinanceOptionExpirations": str(self.settings.get("externalYFinanceOptionExpirations") or "2"),
            "yfinancePriceMaxAge": str(self.settings.get("externalYFinancePriceMaxAgeMinutes") or "30"),
            "yfinanceOptionsMaxAge": str(self.settings.get("externalYFinanceOptionsMaxAgeMinutes") or "30"),
            "yfinanceNewsMaxAge": str(self.settings.get("externalYFinanceNewsMaxAgeMinutes") or "1440"),
            "yfinanceAnalystMaxAge": str(self.settings.get("externalYFinanceAnalystMaxAgeMinutes") or "10080"),
            "yfinanceFundamentalMaxAge": str(self.settings.get("externalYFinanceFundamentalMaxAgeMinutes") or "129600"),
            "fxRateSourceVersion": "broker-account-alpha-daily-v1",
            "fxRateFetchIntervalHours": str(self.settings.get("externalFxRateFetchIntervalHours") or "24"),
            "alphaRateLimitSeconds": str(self.settings.get("externalAlphaRateLimitSeconds") or "15"),
            "alphaDailyRequestBudget": str(self.settings.get("externalAlphaDailyRequestBudget") or "20"),
            "alphaQuotaCooldownMinutes": str(self.settings.get("externalAlphaQuotaCooldownMinutes") or "1440"),
            "secMappings": symbol_assignments(self.settings.get("externalSecCompanyCiks") or ""),
            "dartLookbackDays": str(self.settings.get("externalDartLookbackDays") or "14"),
            "dartDocumentText": str(self.settings.get("externalDartDocumentTextEnabled") or "1"),
            "dartDocumentTextMaxChars": str(self.settings.get("externalDartDocumentTextMaxChars") or "6000"),
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
                "yfinance": self.yfinance_enabled(),
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
                "yfinance": self.yfinance_enabled(),
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

    def crypto_fetch_interval_minutes(self) -> int:
        value = int(number(self.settings.get("externalCoinGeckoFetchIntervalMinutes")) or 10)
        return max(1, min(120, value))

    def crypto_refresh_due(self, signals: Dict[str, object]) -> bool:
        if not self.external_api_enabled("externalCoinGeckoEnabled"):
            return False
        payload = signals if isinstance(signals, dict) else {}
        stamp = str(payload.get("cryptoLastAttemptAt") or payload.get("cryptoFetchedAt") or "")
        if not stamp:
            markets = payload.get("cryptoMarkets") if isinstance(payload.get("cryptoMarkets"), dict) else {}
            stamps = [
                str(item.get("fetchedAt") or item.get("lastUpdated") or "")
                for item in markets.values()
                if isinstance(item, dict)
            ]
            stamp = max(stamps) if stamps else ""
        fetched_at = parse_iso(stamp)
        if not fetched_at:
            return True
        return datetime.now(timezone.utc) - fetched_at >= timedelta(minutes=self.crypto_fetch_interval_minutes())

    def refresh_cached_coingecko(self, signals: Dict[str, object]) -> bool:
        return bool(self.add_coingecko(signals))

    def fetch_signals(self, positions: List[Position]) -> Dict[str, object]:
        signals = {
            "fetchedAt": utc_now_iso(),
            "cryptoFetchedAt": "",
            "cryptoLastAttemptAt": "",
            "equityQuotes": {},
            "cryptoMarkets": {},
            "macro": {},
            "fxRates": {},
            "secFilings": {},
            "dartDisclosures": {},
            "newsHeadlines": {},
            "companyOverviews": {},
            "earningsReports": {},
            "yfinanceData": {},
            "researchEvidence": {},
            "statuses": [],
        }
        self.merge_cached_crypto_market_snapshot(
            signals,
            getattr(self, "_crypto_market_snapshot", {}),
            str(getattr(self, "_crypto_market_cache_state", "missing") or "missing"),
        )
        self.add_fx_rates(signals, positions)
        self.add_alpha_vantage(signals, positions)
        self.add_alpha_fundamentals(signals, positions)
        self.add_yfinance(signals, positions)
        self.add_sec_edgar(signals, positions)
        if self.crypto_refresh_due(signals):
            self.add_coingecko(signals)
        self.add_fred(signals)
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
        shared_key = "alpha-vantage:provider" if self.is_alpha_vantage_source(source) else ""
        return guard.call(
            source.lower().replace(" ", "-") + ":" + target,
            source + " " + target,
            fetch,
            attempts=self.int_setting("externalApiRetryAttempts", 2, 1),
            rate_limit_seconds=self.int_setting("externalApiRateLimitSeconds", 60, 0),
            failure_threshold=self.int_setting("externalApiCircuitFailures", 2, 1),
            cooldown_minutes=self.int_setting("externalApiCircuitCooldownMinutes", 30, 1),
            shared_rate_limit_key=shared_key,
            shared_rate_limit_seconds=self.int_setting("externalAlphaRateLimitSeconds", 15, 0) if shared_key else 0,
            shared_rate_limit_label="Alpha Vantage provider" if shared_key else "",
            shared_daily_request_budget=self.int_setting("externalAlphaDailyRequestBudget", 20, 0) if shared_key else 0,
            shared_quota_cooldown_minutes=self.int_setting("externalAlphaQuotaCooldownMinutes", 1440, 1) if shared_key else 0,
        )

    def is_alpha_vantage_source(self, source: str) -> bool:
        normalized = str(source or "").strip().lower().replace("_", "-").replace(" ", "-")
        return normalized.startswith("alpha-vantage")

    def limited_targets(self, signals: Dict[str, object], source: str, values: List[str], limit_key: str, fallback: int) -> List[str]:
        limit = self.int_setting(limit_key, fallback, 1)
        if len(values) > limit:
            self.status(signals, source, True, "bulk cap " + str(limit) + "/" + str(len(values)))
        return values[:limit]

    def status(self, signals: Dict[str, object], source: str, ok: bool, message: str, **metadata) -> None:
        row = {
            "source": source,
            "ok": bool(ok),
            "message": str(message or ""),
        }
        row.update({key: value for key, value in metadata.items() if value is not None})
        signals.setdefault("statuses", []).append(row)

    def status_for_error(self, signals: Dict[str, object], source: str, message: str, error: Exception) -> None:
        if isinstance(error, ExternalRateLimited):
            self.status(
                signals,
                source,
                True,
                message + self.safe_error_message(error),
                dataUsable=False,
                deferred=True,
                operationalAlert=False,
            )
            return
        self.status(signals, source, False, message + self.safe_error_message(error))

    def safe_error_message(self, error: Exception) -> str:
        return sanitize_sensitive_text(error)[:120]
