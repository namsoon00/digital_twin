from copy import deepcopy
from typing import Dict

from ...domain.portfolio import utc_now_iso
from ...application.external_data.contracts import setting_enabled
from ..external_signal_provider_yfinance import YFINANCE_MODULE_PROFILES
from .adapters.base import source_as_of, source_revision


class LegacyExternalSignalImporter:
    """One-time bounded migration from the aggregate cache into current facts."""

    def __init__(self, cache, store, registry, settings: Dict[str, object]):
        self.cache = cache
        self.store = store
        self.registry = registry
        self.settings = dict(settings or {})
        self._checked = False

    def import_if_empty(self) -> Dict[str, object]:
        if self._checked:
            return {"status": "skipped", "reason": "migration already checked", "savedCount": 0}
        self._checked = True
        summary = self.store.summary()
        fact_count = int((summary.get("facts") or {}).get("count") or 0)
        if fact_count > 0:
            compacted = self.compact_legacy_cache(fact_count)
            return {
                "status": "skipped",
                "reason": "current facts already exist",
                "savedCount": 0,
                "legacyCacheCompacted": compacted,
            }
        payload = self.cache.load() if self.cache else {}
        entries = payload.get("entries") if isinstance(payload.get("entries"), dict) else {}
        candidates = [
            entry for entry in entries.values()
            if isinstance(entry, dict)
            and isinstance(entry.get("signals"), dict)
            and str(entry.get("cacheScope") or "") in {"account-snapshot", "market-monitor"}
        ]
        if not candidates and isinstance(payload.get("signals"), dict):
            candidates = [payload]
        if not candidates:
            return {"status": "skipped", "reason": "legacy cache missing", "savedCount": 0}
        latest = max(candidates, key=lambda item: str(item.get("fetchedAt") or ""))
        signals = dict(latest.get("signals") or {})
        fetched_at = str(signals.get("fetchedAt") or latest.get("fetchedAt") or "")
        saved = 0

        crypto = signals.get("cryptoMarkets") if isinstance(signals.get("cryptoMarkets"), dict) else {}
        if crypto:
            saved += self.seed(
                "coingecko.market",
                "global",
                {"cryptoMarkets": crypto},
                fetched_at,
                source_as_of(crypto, signals.get("cryptoSourceAsOf") or signals.get("cryptoFetchedAt")),
            )
        macro = signals.get("macro") if isinstance(signals.get("macro"), dict) else {}
        if macro:
            saved += self.seed("fred.macro", "global", {"macro": macro}, fetched_at, source_as_of(macro, fetched_at))

        yfinance = signals.get("yfinanceData") if isinstance(signals.get("yfinanceData"), dict) else {}
        for symbol, raw_payload in yfinance.items():
            if not isinstance(raw_payload, dict):
                continue
            modules = [str(item or "") for item in raw_payload.get("modulesCollected") or []]
            for profile in ["price", "options", "news", "analyst", "fundamental"]:
                profile_modules = [item for item in modules if YFINANCE_MODULE_PROFILES.get(item, "fundamental") == profile]
                if not profile_modules:
                    continue
                profile_payload = {
                    key: deepcopy(value)
                    for key, value in raw_payload.items()
                    if key in {
                        "provider", "symbol", "querySymbol", "collectedAt", "historyPeriod", "historyInterval",
                        "freshness", "moduleFreshness", "dataQualityNotes", "errors",
                    } or key in profile_modules
                }
                profile_payload["profilesCollected"] = [profile]
                profile_payload["modulesCollected"] = profile_modules
                fragment = {"yfinanceData": {str(symbol): profile_payload}}
                for key in ["equityQuotes", "companyOverviews", "earningsReports"]:
                    group = signals.get(key) if isinstance(signals.get(key), dict) else {}
                    if symbol in group:
                        fragment[key] = {str(symbol): deepcopy(group[symbol])}
                saved += self.seed(
                    "yfinance." + profile,
                    str(symbol),
                    fragment,
                    str(raw_payload.get("collectedAt") or fetched_at),
                    source_as_of(profile_payload, raw_payload.get("collectedAt") or fetched_at),
                )

        yfinance_symbols = set(yfinance)
        quotes = signals.get("equityQuotes") if isinstance(signals.get("equityQuotes"), dict) else {}
        for symbol, quote in quotes.items():
            if symbol in yfinance_symbols or not isinstance(quote, dict):
                continue
            saved += self.seed(
                "alpha.quote",
                str(symbol),
                {"equityQuotes": {str(symbol): deepcopy(quote)}},
                fetched_at,
                source_as_of(quote, fetched_at),
            )

        sec = signals.get("secFilings") if isinstance(signals.get("secFilings"), dict) else {}
        for symbol, raw_row in sec.items():
            if not isinstance(raw_row, dict):
                continue
            submission = {key: deepcopy(value) for key, value in raw_row.items() if key != "facts"}
            facts = {key: deepcopy(value) for key, value in raw_row.items() if key in {"provider", "symbol", "cik", "companyName", "facts"}}
            saved += self.seed("sec.submissions", str(symbol), {"secFilings": {str(symbol): submission}}, fetched_at, source_as_of(submission, fetched_at))
            if facts.get("facts"):
                saved += self.seed("sec.company_facts", str(symbol), {"secFilings": {str(symbol): facts}}, fetched_at, source_as_of(facts, fetched_at))

        dart = signals.get("dartDisclosures") if isinstance(signals.get("dartDisclosures"), dict) else {}
        fundamental_keys = {"company", "financialStatements", "financialStatementBasis", "executives"}
        for symbol, raw_row in dart.items():
            if not isinstance(raw_row, dict):
                continue
            disclosure = {key: deepcopy(value) for key, value in raw_row.items() if key not in fundamental_keys}
            facts = {key: deepcopy(value) for key, value in raw_row.items() if key in fundamental_keys or key in {"provider", "corpCode", "corpName", "receiptNo", "receiptDate"}}
            saved += self.seed("opendart.disclosures", str(symbol), {"dartDisclosures": {str(symbol): disclosure}}, fetched_at, source_as_of(disclosure, fetched_at))
            if any(key in facts for key in fundamental_keys):
                saved += self.seed("opendart.company_facts", str(symbol), {"dartDisclosures": {str(symbol): facts}}, fetched_at, source_as_of(facts, fetched_at))
        compacted = self.compact_legacy_cache(saved) if saved else False
        return {
            "status": "ok",
            "savedCount": saved,
            "sourceFetchedAt": fetched_at,
            "legacyCacheCompacted": compacted,
        }

    def compact_legacy_cache(self, fact_count: int) -> bool:
        if not self.cache or not setting_enabled(self.settings, "externalDataCompactLegacyCacheEnabled", True):
            return False
        self.cache.replace({
            "schemaVersion": 6,
            "migratedTo": "external_fact_current",
            "migratedAt": utc_now_iso(),
            "factCount": max(0, int(fact_count or 0)),
            "entries": {},
        })
        return True

    def seed(
        self,
        dataset_id: str,
        subject_key: str,
        fragment: Dict[str, object],
        fetched_at: str,
        source_time: str,
    ) -> int:
        try:
            descriptor = self.registry.adapter(dataset_id).descriptor
        except KeyError:
            return 0
        saved = self.store.seed_fact(
            dataset_id,
            subject_key,
            descriptor.provider_id,
            source_revision(fragment),
            source_time,
            fetched_at,
            descriptor.resolved_freshness_seconds(self.settings),
            fragment,
        )
        return 1 if saved else 0
