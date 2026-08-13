"""Prepare bounded internal observations for the notification AI judge."""

from __future__ import annotations

import time
from collections import OrderedDict
from copy import deepcopy
from typing import Dict, List

from ..domain.ai_inference_queue import notification_ai_subject
from ..domain.investment_brain import canonical_investment_timestamp
from ..domain.market_data import number
from ..domain.market_time_series import market_session_date
from ..domain.message_types import INVESTMENT_INSIGHT
from ..domain.portfolio_ontology_temporal_concepts import (
    dedupe_temporal_rows,
    parse_temporal_windows,
    temporal_window_values,
    trim_to_recent_sessions,
    window_rows,
)


AI_INTERNAL_DATA_VERSION = "notification-ai-internal-data-v1"
TEMPORAL_AI_KEYS = (
    "windowKey", "windowType", "lookbackDays", "lookbackMinutes",
    "sampleCount", "validObservationCount", "requiredSampleCount",
    "coveredSessionCount", "requiredSessionCount", "hasSufficientHistory",
    "coverageRatio", "latestObservationQuality", "observationSource",
    "observationGranularity", "firstObservedAt", "lastObservedAt",
    "startPrice", "currentPrice", "priceChangePct", "peakPrice", "troughPrice",
    "drawdownFromPeakPct", "reboundFromTroughPct", "priorPriceChangePct",
    "recentPriceChangePct", "priceVelocityChangePct", "consecutiveDeclineCount",
    "consecutiveAdvanceCount", "directionChangeCount", "ma20DistanceStart",
    "ma20DistanceEnd", "ma20DistanceChange", "ma20ReclaimCount", "ma20BreakCount",
    "ma60DistanceStart", "ma60DistanceEnd", "volumeRatioEnd", "tradeStrengthEnd",
    "bidAskImbalanceEnd", "smartMoneyObservationCount",
    "smartMoneyDistinctObservationCount", "smartMoneyDataState", "smartMoneyNetLatest",
    "smartMoneyNetChange",
)


def compact_temporal_window(values: Dict[str, object]) -> Dict[str, object]:
    return {
        key: values.get(key)
        for key in TEMPORAL_AI_KEYS
        if values.get(key) not in (None, "", [], {})
    }


def current_temporal_observation(
    context: Dict[str, object],
    relation: Dict[str, object],
    subject: Dict[str, object],
    as_of: str,
) -> Dict[str, object]:
    """Build the same current observation that the ABox projection receives."""

    facts = relation.get("facts") if isinstance(relation.get("facts"), dict) else {}
    current_price = number(facts.get("currentPrice"))
    if current_price <= 0 or not as_of:
        return {}
    market = str(subject.get("market") or facts.get("market") or context.get("market") or "")
    currency = str(facts.get("currency") or context.get("currency") or ("KRW" if market.upper() in {"KR", "KOSPI", "KOSDAQ"} else ""))
    row = {
        "generatedAt": as_of,
        "bucketAt": as_of,
        "sourceAsOf": str(facts.get("priceSourceAsOf") or facts.get("sourceAsOf") or as_of),
        "marketSessionDate": market_session_date(as_of, market, currency),
        "symbol": str(subject.get("symbol") or facts.get("symbol") or ""),
        "name": str(subject.get("name") or facts.get("name") or ""),
        "market": market,
        "currency": currency,
        "currentPrice": current_price,
        "dataQuality": str(facts.get("dataQuality") or context.get("dataQuality") or "unknown"),
        "observationSource": "notification-inference-generation",
        "observationGranularity": "snapshot",
    }
    for key in (
        "volume", "volumeRatio", "tradeStrength", "bidAskImbalance",
        "foreignNetVolume", "institutionNetVolume", "individualNetVolume",
        "ma5", "ma20", "ma60", "ma20Slope", "ma60Slope",
        "ma20Distance", "ma60Distance",
    ):
        if facts.get(key) not in (None, ""):
            row[key] = facts.get(key)
    return row


class NotificationAIDecisionContextEnricher:
    """Load one subject's exact time-series windows before immutable queue capture."""

    def __init__(
        self,
        time_series_store=None,
        settings: Dict[str, object] = None,
        investment_domain_store=None,
    ):
        self.time_series_store = time_series_store
        self.settings = dict(settings or {})
        self.investment_domain_store = investment_domain_store
        self._cache = OrderedDict()

    def cache_max_entries(self) -> int:
        try:
            value = int(float(str(self.settings.get("notificationAiInternalDataCacheMaxEntries") or 256)))
        except (TypeError, ValueError):
            value = 256
        return max(1, min(2048, value))

    def enabled(self) -> bool:
        return str(self.settings.get("notificationAiInternalDataEnabled", "1")).strip().lower() not in {
            "0", "false", "no", "off", "disabled",
        }

    def __call__(self, job) -> None:
        if getattr(job, "message_type", "") != INVESTMENT_INSIGHT or not self.enabled():
            return
        context = dict(getattr(job, "context", {}) or {})
        if context.get("notificationAiInternalData"):
            return
        subject = notification_ai_subject(context)
        symbol = str(subject.get("symbol") or "").upper().strip()
        relation = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
        raw_as_of = relation.get("inferenceGenerationAt") or context.get("referenceDate") or ""
        as_of = canonical_investment_timestamp(raw_as_of)
        started = time.monotonic()
        audit = {
            "status": "unavailable",
            "source": "market-time-series",
            "symbol": symbol,
            "asOf": as_of,
            "requestedWindowCount": 0,
            "loadedWindowCount": 0,
            "loadMs": 0,
            "cacheHit": False,
        }
        windows: List[Dict[str, object]] = []
        cache_key = (str(getattr(job, "account_id", "") or context.get("accountId") or ""), symbol, as_of)
        try:
            if not symbol:
                audit["reason"] = "subject-symbol-missing"
            elif not self.time_series_store or not hasattr(self.time_series_store, "load_temporal_windows"):
                audit["reason"] = "time-series-store-unavailable"
            elif cache_key in self._cache:
                cached = deepcopy(self._cache.pop(cache_key))
                self._cache[cache_key] = cached
                windows = list(cached.get("temporalWindows") or [])
                audit.update(dict(cached.get("audit") or {}))
                audit["cacheHit"] = True
                audit["status"] = "ready" if windows else "partial"
            else:
                definitions = parse_temporal_windows(self.settings.get("temporalWindowPeriods"))
                audit["requestedWindowCount"] = len(definitions)
                loaded = self.time_series_store.load_temporal_windows(
                    str(getattr(job, "account_id", "") or context.get("accountId") or ""),
                    [symbol],
                    definitions,
                    as_of=as_of,
                )
                rows_by_window = (loaded or {}).get(symbol) or {}
                current_row = current_temporal_observation(context, relation, subject, as_of)
                for definition in definitions:
                    rows = list(rows_by_window.get(definition.key) or [])
                    if current_row:
                        rows = dedupe_temporal_rows([*rows, current_row], symbol)
                    if not rows:
                        continue
                    if definition.is_intraday:
                        rows = window_rows(rows, definition, None)
                    else:
                        rows = trim_to_recent_sessions(rows, definition.required_sessions)
                    values = compact_temporal_window(temporal_window_values(rows, definition))
                    if values:
                        windows.append(values)
                audit["loadedWindowCount"] = len(windows)
                audit["status"] = "ready" if windows else "partial"
                audit["windowKeys"] = [str(item.get("windowKey") or "") for item in windows]
                audit["includesCurrentObservation"] = bool(current_row)
                audit["inferenceGenerationId"] = str(relation.get("inferenceGenerationId") or "")
                if not windows:
                    audit["reason"] = "no-observations-at-reference-time"
        except Exception as error:  # noqa: BLE001 - AI can continue with graph context.
            audit["status"] = "error"
            audit["reason"] = str(error)[:180]
        audit["loadMs"] = int((time.monotonic() - started) * 1000)
        if audit.get("status") in {"ready", "partial"} and not audit.get("cacheHit"):
            self._cache[cache_key] = deepcopy({"temporalWindows": windows, "audit": audit})
            while len(self._cache) > self.cache_max_entries():
                self._cache.popitem(last=False)
        context["notificationAiInternalData"] = {
            "schemaVersion": AI_INTERNAL_DATA_VERSION,
            "temporalWindows": windows,
            "audit": audit,
        }
        account_id = str(getattr(job, "account_id", "") or context.get("accountId") or "")
        if self.investment_domain_store and account_id:
            try:
                lifecycle = self.investment_domain_store.latest_portfolio_lifecycle("portfolio:" + account_id)
                context["portfolioLifecycle"] = {
                    "status": lifecycle.get("status"),
                    "portfolioId": lifecycle.get("portfolioId"),
                    "mandate": lifecycle.get("mandate") or {},
                    "snapshotCheckpoint": lifecycle.get("snapshotCheckpoint") or {},
                    "reconciliation": lifecycle.get("reconciliation") or {},
                    "exposureSnapshot": lifecycle.get("exposureSnapshot") or {},
                    "portfolioRiskSnapshot": lifecycle.get("portfolioRiskSnapshot") or {},
                    "rebalanceProposal": lifecycle.get("rebalanceProposal") or {},
                    "portfolioDecisionCycle": lifecycle.get("portfolioDecisionCycle") or {},
                    "portfolioState": lifecycle.get("portfolioState") or {},
                    "recentActivityEpisodes": list(lifecycle.get("recentActivityEpisodes") or [])[:8],
                    "decisionActionObservations": list(lifecycle.get("decisionActionObservations") or [])[:8],
                }
                audit["portfolioLifecycleStatus"] = lifecycle.get("status") or "unavailable"
            except Exception as error:  # noqa: BLE001 - graph facts remain the primary AI input.
                audit["portfolioLifecycleStatus"] = "error"
                audit["portfolioLifecycleReason"] = str(error)[:160]
        job.context = context
