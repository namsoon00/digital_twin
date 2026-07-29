"""Bounded source-input contract for the live TypeDB ABox projection.

The monitor snapshot is the durable source record and may legitimately retain
article bodies, provider responses, and historical market data.  The live
ABox is not that archive: TypeDB rules consume structured facts, verified
claims, and concise research analysis.  Passing the archive through every
reasoning generation made a small quote update repeatedly serialize megabytes
of unrelated provider payloads.

This module creates a copied, deterministic projection view.  It never
decides whether an investment signal is material and it never mutates the
source payload.  Full source documents remain available to the research and
notification read models through the original monitor snapshot and stores.
"""

from __future__ import annotations

import json
from typing import Dict, Iterable, Mapping, Set

from .security_lines import security_lines_for_symbol


ONTOLOGY_PROJECTION_INPUT_VERSION = "ontology-projection-input-v1"

SYMBOL_SIGNAL_GROUPS = {
    "secFilings",
    "equityQuotes",
    "yfinanceData",
    "newsHeadlines",
    "dartDisclosures",
    "earningsReports",
    "companyOverviews",
    "researchEvidence",
}

RESEARCH_ITEM_LIMIT = 12
TEXT_LIMIT = 1200


def _symbols(values: Iterable[object]) -> Set[str]:
    return {
        str(value or "").upper().strip()
        for value in values or []
        if str(value or "").strip()
    }


def projection_signal_symbols(
    target_symbols: Iterable[object] = None,
    settings: Mapping[str, object] = None,
) -> Set[str]:
    """Return target symbols plus direct security-line dependencies.

    A Korean ordinary share and its ADR can share price, valuation, and
    cross-listing facts.  Include those direct dependencies without widening a
    target-scoped update back to every portfolio symbol.
    """

    selected = _symbols(target_symbols)
    if not selected:
        return set()
    settings = dict(settings or {}) if isinstance(settings, Mapping) else {}
    related = set(selected)
    for symbol in list(selected):
        for line in security_lines_for_symbol(symbol, settings):
            related.update(_symbols([
                getattr(line, "symbol", ""),
                getattr(line, "local_symbol", ""),
                getattr(line, "underlying_symbol", ""),
            ]))
    return related


def _text(value: object, limit: int = TEXT_LIMIT) -> str:
    text = " ".join(str(value or "").split())
    if limit > 3 and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _bounded_value(
    value: object,
    *,
    text_limit: int = TEXT_LIMIT,
    list_limit: int = 12,
    map_limit: int = 40,
    depth: int = 3,
) -> object:
    """Copy provider values without retaining unbounded raw documents."""

    if isinstance(value, str):
        return _text(value, text_limit)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth <= 0:
        return _text(value, text_limit)
    if isinstance(value, Mapping):
        rows = {}
        for key in sorted(value, key=lambda item: str(item))[:max(1, int(map_limit or 1))]:
            rows[str(key)] = _bounded_value(
                value.get(key),
                text_limit=text_limit,
                list_limit=list_limit,
                map_limit=map_limit,
                depth=depth - 1,
            )
        return rows
    if isinstance(value, (list, tuple, set)):
        return [
            _bounded_value(
                item,
                text_limit=text_limit,
                list_limit=list_limit,
                map_limit=map_limit,
                depth=depth - 1,
            )
            for item in list(value)[:max(1, int(list_limit or 1))]
        ]
    return _text(value, text_limit)


def _selected(
    payload: object,
    keys: Iterable[str],
    *,
    text_limit: int = TEXT_LIMIT,
    list_limit: int = 12,
    map_limit: int = 40,
    depth: int = 3,
) -> Dict[str, object]:
    source = payload if isinstance(payload, Mapping) else {}
    return {
        key: _bounded_value(
            source.get(key),
            text_limit=text_limit,
            list_limit=list_limit,
            map_limit=map_limit,
            depth=depth,
        )
        for key in keys
        if key in source
    }


def _compact_ai_analysis(value: object) -> Dict[str, object]:
    source = value if isinstance(value, Mapping) else {}
    summary = _selected(
        source.get("summary"),
        ["oneLineKo", "briefKo", "keyTakeaways", "whyItMatters", "watchPoints"],
        text_limit=700,
        list_limit=6,
        depth=2,
    )
    result = _selected(
        source,
        [
            "model", "status", "version", "promptVersion", "dataState",
            "eventType", "readScope", "sourceTextHash", "relationScope",
            "impactPolarity", "impactLabelKo", "impactReasonKo", "relevanceState",
            "sourceLanguage", "needsReview", "validationState", "materialityState",
            "sourceTrustState", "translationStatus", "translatedTitleKo",
            "rationaleKo", "actionBoundaryKo", "validationReasonKo",
            "reasoningLimitations", "keyNumbers", "riskSignals", "supportSignals",
            "contrastSignals",
        ],
        text_limit=900,
        list_limit=8,
        depth=2,
    )
    if summary:
        result["summary"] = summary
    return result


def _compact_claim_ledger(value: object) -> Dict[str, object]:
    source = value if isinstance(value, Mapping) else {}
    result = _selected(
        source,
        ["version", "status", "sourceTextHash", "claimCount", "checkedAt"],
        text_limit=240,
        list_limit=8,
        depth=2,
    )
    claims = source.get("claims") if isinstance(source.get("claims"), list) else []
    compact_claims = []
    for item in claims[:8]:
        if not isinstance(item, Mapping):
            continue
        compact_claims.append(_selected(
            item,
            [
                "claimId", "statement", "excerpt", "state", "verificationStatus",
                "entityResolutionStatus", "investmentJudgmentEligible", "sourceTrustState",
                "sourceOrigin", "independentSourceCount", "officialEvidenceIds",
                "corroboratingEvidenceIds", "conflictingEvidenceIds", "supersededByEvidenceId",
                "supersedesClaimIds", "reasons", "excerptIndex", "excerptStart", "excerptEnd",
            ],
            text_limit=700,
            list_limit=8,
            depth=2,
        ))
    if compact_claims:
        result["claims"] = compact_claims
    return result


def _compact_article_facts(value: object) -> Dict[str, object]:
    return _selected(
        value,
        [
            "bodyQualityPassed", "bodyQualityState", "articleReadStatus", "readScope",
            "sourceTextHash", "wordCount", "language", "publishedAt", "observedAt",
            "fetchedAt", "sourceUrl", "title",
        ],
        text_limit=500,
        list_limit=8,
        depth=2,
    )


def _compact_research_payload(item: Mapping[str, object]) -> Dict[str, object]:
    nested = item.get("payload") or item.get("rawPayload") or item.get("raw_payload")
    source = nested if isinstance(nested, Mapping) else {}
    result = _selected(
        source,
        [
            "relevanceScore", "relationScope", "matchedAliases", "mentionedPeers",
            "topicTags", "marketTopics", "sourceReliability", "directMention",
            "eventType", "materialityScore", "materialityPassed", "excludedReason",
            "analysisSummary", "analysisVersion", "articleSummaryKo", "articleReadStatus",
            "articleTextPreview", "bodyQualityState", "bodyQualityPassed",
            "articleDigestVersion", "stockImpact", "stockImpactLabel",
            "stockImpactPolarity", "stockImpactScore", "stockImpactReasonKo",
            "articleAiAnalysisVersion", "analysisConflict", "analysisConflictSource",
            "analysisConflictExistingPolarity", "analysisConflictAiPolarity",
            "analysisConflictReasonKo", "dataQualityRisk", "dataQualityRiskScore",
            "normalizedTitle", "normalizedSummary", "sourceKind", "sourcePlatform",
            "sourceOrigin", "sourcePublisher", "lifecycleState", "lifecycleChangedAt",
            "relevanceState", "materialityState", "sourceTrustState", "validationState",
            "evidenceRole",
        ],
        text_limit=900,
        list_limit=10,
        depth=2,
    )
    for key in [
        "relationScope", "eventType", "articleSummaryKo", "articleReadStatus",
        "stockImpact", "stockImpactLabel", "stockImpactPolarity", "stockImpactScore",
        "stockImpactReasonKo", "analysisSummary", "dataQualityRisk", "sourceKind",
        "sourcePlatform", "sourceOrigin", "sourcePublisher", "lifecycleState",
        "lifecycleChangedAt", "relevanceState", "materialityState", "sourceTrustState",
        "validationState", "evidenceRole", "articleAiAnalysisVersion",
    ]:
        if key not in result and key in item:
            result[key] = _bounded_value(item.get(key), text_limit=900, list_limit=10, depth=2)
    analysis = source.get("aiAnalysis") if isinstance(source.get("aiAnalysis"), Mapping) else item.get("aiAnalysis")
    if isinstance(analysis, Mapping):
        result["aiAnalysis"] = _compact_ai_analysis(analysis)
    article_facts = source.get("articleFacts") if isinstance(source.get("articleFacts"), Mapping) else item.get("articleFacts")
    if isinstance(article_facts, Mapping):
        result["articleFacts"] = _compact_article_facts(article_facts)
    ledger = source.get("claimLedger") if isinstance(source.get("claimLedger"), Mapping) else item.get("claimLedger")
    if isinstance(ledger, Mapping):
        result["claimLedger"] = _compact_claim_ledger(ledger)
    for key in ["evidenceGovernance", "qualityGate"]:
        value = source.get(key) if isinstance(source.get(key), Mapping) else item.get(key)
        if isinstance(value, Mapping):
            result[key] = _bounded_value(value, text_limit=500, list_limit=10, map_limit=30, depth=3)
    for key in ["ontologyRelations", "entityLinks"]:
        value = source.get(key) if isinstance(source.get(key), list) else item.get(key)
        if isinstance(value, list):
            result[key] = _bounded_value(value, text_limit=500, list_limit=8, map_limit=24, depth=3)
    return result


def compact_research_evidence_item(value: object) -> Dict[str, object]:
    source = value if isinstance(value, Mapping) else {}
    result = _selected(
        source,
        [
            "evidenceId", "evidence_id", "symbol", "kind", "source", "title", "url",
            "summary", "polarity", "observedAt", "observed_at", "publishedAt",
            "published_at", "dataState", "eventType", "sourceKind", "sourceOrigin",
            "sourcePlatform", "sourcePublisher", "relevanceState", "relationScope",
            "evidenceRole", "materialityState", "sourceTrustState", "validationState",
            "lifecycleState", "lifecycleChangedAt", "excludedReason", "analysisSummary",
            "articleSummaryKo", "articleReadStatus", "stockImpact", "stockImpactLabel",
            "stockImpactPolarity", "stockImpactReasonKo", "stockImpactScore",
            "dataQualityRisk", "articleAiAnalysisVersion", "analysisConflict",
            "analysisConflictSource", "analysisConflictReasonKo", "analysisConflictAiPolarity",
            "analysisConflictExistingPolarity",
        ],
        text_limit=900,
        list_limit=10,
        depth=2,
    )
    payload = _compact_research_payload(source)
    if payload:
        result["payload"] = payload
    for key, compact in [
        ("aiAnalysis", _compact_ai_analysis(source.get("aiAnalysis"))),
        ("articleFacts", _compact_article_facts(source.get("articleFacts"))),
        ("claimLedger", _compact_claim_ledger(source.get("claimLedger"))),
    ]:
        if compact:
            result[key] = compact
    return result


def _compact_quote(value: object) -> Dict[str, object]:
    return _selected(
        value,
        [
            "symbol", "provider", "price", "currentPrice", "change", "changePercent",
            "volume", "tradingValue", "latestTradingDay", "marketSession", "marketState",
            "currency", "sourceAsOf", "observedAt", "fetchedAt", "freshnessStatus",
            "dataState", "quality", "open", "high", "low", "previousClose",
        ],
        text_limit=180,
        list_limit=8,
        depth=2,
    )


def _compact_yfinance(value: object) -> Dict[str, object]:
    source = value if isinstance(value, Mapping) else {}
    result = _selected(
        source,
        ["provider", "querySymbol", "symbol", "isin", "modulesCollected", "freshness", "moduleFreshness", "collectedAt"],
        text_limit=300,
        list_limit=40,
        map_limit=40,
        depth=3,
    )
    quote = _compact_quote(source.get("quote"))
    if quote:
        result["quote"] = quote
    info = _selected(
        source.get("info"),
        [
            "longName", "shortName", "displayName", "quoteType", "currency", "exchange",
            "sector", "industry", "marketCap", "trailingPE", "forwardPE", "beta",
            "dividendYield", "bookValue", "epsTrailingTwelveMonths", "epsForward",
        ],
        text_limit=300,
        list_limit=8,
        depth=2,
    )
    if info:
        result["info"] = info
    for key in ["analystPriceTargets", "calendar"]:
        compact = _bounded_value(source.get(key), text_limit=300, list_limit=8, map_limit=30, depth=3)
        if compact not in ({}, [], None, ""):
            result[key] = compact
    option_chains = source.get("optionChains") if isinstance(source.get("optionChains"), list) else []
    compact_options = []
    for row in option_chains[:2]:
        if not isinstance(row, Mapping):
            continue
        compact_options.append(_selected(row, ["expiration", "summary"], text_limit=300, list_limit=8, depth=3))
    if compact_options:
        result["optionChains"] = compact_options
    options = source.get("options") if isinstance(source.get("options"), list) else []
    if options:
        result["options"] = _bounded_value(options, text_limit=80, list_limit=12, depth=1)
    errors = source.get("errors") if isinstance(source.get("errors"), list) else []
    if errors:
        result["errors"] = _bounded_value(errors, text_limit=240, list_limit=8, depth=1)
    result["statementMetricCounts"] = {
        "incomeStatement": len(source.get("incomeStatement") or []),
        "balanceSheet": len(source.get("balanceSheet") or []),
        "cashFlow": len(source.get("cashFlow") or []),
        "quarterlyIncomeStatement": len(source.get("quarterlyIncomeStatement") or []),
        "quarterlyBalanceSheet": len(source.get("quarterlyBalanceSheet") or []),
        "quarterlyCashFlow": len(source.get("quarterlyCashFlow") or []),
    }
    return result


def _compact_company_overview(value: object) -> Dict[str, object]:
    return _selected(
        value,
        [
            "symbol", "name", "provider", "currency", "sector", "industry", "fetchedAt",
            "latestQuarter", "marketCapitalization", "revenueTTM", "grossProfitTTM", "ebitda",
            "profitMargin", "operatingMarginTTM", "peRatio", "pegRatio", "forwardPE", "beta",
            "dividendYield", "analystTargetPrice", "analystRatingStrongBuy", "analystRatingBuy",
            "analystRatingHold", "analystRatingSell", "analystRatingStrongSell",
        ],
        text_limit=300,
        list_limit=8,
        depth=2,
    )


def _compact_earnings_report(value: object) -> Dict[str, object]:
    source = value if isinstance(value, Mapping) else {}
    result = _selected(source, ["symbol", "provider", "fetchedAt"], text_limit=240, depth=2)
    latest = _selected(
        source.get("latestQuarter"),
        ["fiscalDateEnding", "reportedDate", "reportedEPS", "estimatedEPS", "surprise", "surprisePercentage"],
        text_limit=160,
        depth=2,
    )
    if latest:
        result["latestQuarter"] = latest
    return result


def _compact_financial_facts(value: object) -> Dict[str, object]:
    source = value if isinstance(value, Mapping) else {}
    rows = {}
    for key in ["revenue", "netIncome", "assets", "liabilities", "equity"]:
        compact = _selected(source.get(key), ["value", "unit", "end", "filed", "form", "fy", "fp"], text_limit=120, depth=2)
        if compact:
            rows[key] = compact
    return rows


def _compact_filing(value: object) -> Dict[str, object]:
    source = value if isinstance(value, Mapping) else {}
    result = _selected(
        source,
        [
            "symbol", "provider", "companyName", "entityName", "cik", "reportName", "report_name",
            "receiptNo", "receipt_no", "receiptDate", "receipt_date", "fetchedAt",
            "documentTextQuality", "documentTextPreview", "documentText", "sourceAsOf",
        ],
        text_limit=4000,
        list_limit=8,
        depth=2,
    )
    latest = _selected(
        source.get("latestFiling"),
        [
            "form", "filingDate", "filed", "url", "accessionNumber", "primaryDocument",
            "reportDate", "documentText", "documentTextPreview", "documentTextQuality",
        ],
        text_limit=4000,
        list_limit=8,
        depth=2,
    )
    if latest:
        result["latestFiling"] = latest
    facts = _compact_financial_facts(source.get("facts"))
    if facts:
        result["facts"] = facts
    return result


def _compact_news_headlines(value: object) -> Dict[str, object]:
    source = value if isinstance(value, Mapping) else {}
    result = _selected(
        source,
        ["provider", "count", "sentiment", "sentimentState", "fetchedAt", "observedAt", "publishedAt"],
        text_limit=240,
        list_limit=8,
        depth=2,
    )
    items = source.get("items") if isinstance(source.get("items"), list) else []
    compact_items = [compact_research_evidence_item(item) for item in items[:RESEARCH_ITEM_LIMIT] if isinstance(item, Mapping)]
    if compact_items:
        result["items"] = compact_items
    return result


def _compact_symbol_group(
    group: str,
    value: object,
    allowed_symbols: Set[str],
) -> Dict[str, object]:
    source = value if isinstance(value, Mapping) else {}
    result = {}
    for raw_symbol in sorted(source, key=lambda item: str(item)):
        symbol = str(raw_symbol or "").upper().strip()
        if not symbol or (allowed_symbols and symbol not in allowed_symbols):
            continue
        item = source.get(raw_symbol)
        if group == "equityQuotes":
            compact = _compact_quote(item)
        elif group == "yfinanceData":
            compact = _compact_yfinance(item)
        elif group == "companyOverviews":
            compact = _compact_company_overview(item)
        elif group == "earningsReports":
            compact = _compact_earnings_report(item)
        elif group in {"secFilings", "dartDisclosures"}:
            compact = _compact_filing(item)
        elif group == "newsHeadlines":
            compact = _compact_news_headlines(item)
        elif group == "researchEvidence":
            rows = item if isinstance(item, list) else []
            compact = [
                compact_research_evidence_item(row)
                for row in rows[:RESEARCH_ITEM_LIMIT]
                if isinstance(row, Mapping)
            ]
        else:
            compact = _bounded_value(item, text_limit=500, list_limit=12, map_limit=40, depth=3)
        if compact not in ({}, [], None, ""):
            result[symbol] = compact
    return result


def _compact_macro(value: object) -> Dict[str, object]:
    source = value if isinstance(value, Mapping) else {}
    result = _selected(
        source,
        ["yieldSpread10y2y", "yieldSpread10y2yDeltaBp", "regime", "regimeLabel", "fetchedAt"],
        text_limit=180,
        list_limit=8,
        depth=2,
    )
    series = source.get("series") if isinstance(source.get("series"), Mapping) else {}
    compact_series = {}
    for key in sorted(series, key=lambda item: str(item))[:20]:
        item = series.get(key)
        if isinstance(item, Mapping):
            compact_series[str(key)] = _selected(
                item,
                ["value", "deltaBp", "date", "provider", "sourceAsOf", "fetchedAt", "freshnessStatus"],
                text_limit=160,
                depth=2,
            )
    if compact_series:
        result["series"] = compact_series
    return result


def _compact_fx_rates(value: object) -> Dict[str, object]:
    source = value if isinstance(value, Mapping) else {}
    result = {}
    for key in sorted(source, key=lambda item: str(item))[:20]:
        item = source.get(key)
        if isinstance(item, Mapping):
            result[str(key)] = _selected(
                item,
                [
                    "base", "baseCurrency", "quote", "quoteCurrency", "rate", "value", "deltaPct",
                    "provider", "fetchedAt", "observedAt", "sourceType", "evidenceStrength",
                    "marketRate", "valuationRate", "freshnessStatus",
                ],
                text_limit=180,
                depth=2,
            )
        elif item not in (None, ""):
            result[str(key)] = _bounded_value(item, text_limit=120, depth=1)
    return result


def _compact_crypto(value: object) -> Dict[str, object]:
    source = value if isinstance(value, Mapping) else {}
    result = {}
    for key in sorted(source, key=lambda item: str(item))[:12]:
        item = source.get(key)
        if not isinstance(item, Mapping):
            continue
        result[str(key)] = _selected(
            item,
            [
                "symbol", "name", "provider", "price", "change1h", "change24h", "change7d",
                "volume24h", "marketCap", "lastUpdated", "fetchedAt", "freshnessStatus",
            ],
            text_limit=180,
            depth=2,
        )
    return result


def compact_external_signals_for_ontology(
    external_signals: Mapping[str, object] = None,
    *,
    target_symbols: Iterable[object] = None,
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Return the bounded external-signal view consumed by the ABox builder.

    ``target_symbols`` narrows only symbol-keyed provider groups.  Global
    macro/freshness facts remain because they are genuine shared dependencies
    of the selected subject.  The returned dictionary contains no mutable
    values from the source payload.
    """

    source = external_signals if isinstance(external_signals, Mapping) else {}
    allowed_symbols = projection_signal_symbols(target_symbols, settings)
    result: Dict[str, object] = {}
    for key in ["macro", "fxRates", "cryptoMarkets"]:
        if key not in source:
            continue
        if key == "macro":
            compact = _compact_macro(source.get(key))
        elif key == "fxRates":
            compact = _compact_fx_rates(source.get(key))
        else:
            compact = _compact_crypto(source.get(key))
        if compact:
            result[key] = compact
    for key in ["quality", "freshness", "provenance", "statuses"]:
        if key in source and source.get(key) not in (None, ""):
            result[key] = _bounded_value(
                source.get(key),
                text_limit=500,
                list_limit=20,
                map_limit=60,
                depth=4,
            )
    if source.get("fetchedAt") not in (None, ""):
        result["fetchedAt"] = _text(source.get("fetchedAt"), 120)
    for group in sorted(SYMBOL_SIGNAL_GROUPS):
        compact = _compact_symbol_group(group, source.get(group), allowed_symbols)
        if compact:
            result[group] = compact
    return result


def temporal_research_signals_for_symbol(
    external_signals: Mapping[str, object] = None,
    symbol: object = "",
) -> Dict[str, object]:
    """Return only the historical research facts used by temporal windows."""

    compact = compact_external_signals_for_ontology(
        external_signals,
        target_symbols=[symbol],
    )
    return {
        key: value
        for key, value in compact.items()
        if key in {"researchEvidence", "newsHeadlines", "dartDisclosures", "secFilings", "equityQuotes"}
    }


def projection_input_summary(
    source_external_signals: Mapping[str, object] = None,
    projected_external_signals: Mapping[str, object] = None,
    *,
    target_symbols: Iterable[object] = None,
) -> Dict[str, object]:
    """Expose bounded runtime telemetry without persisting source documents."""

    def size(value: object) -> int:
        try:
            return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        except (TypeError, ValueError):
            return 0

    source = source_external_signals if isinstance(source_external_signals, Mapping) else {}
    projected = projected_external_signals if isinstance(projected_external_signals, Mapping) else {}
    source_bytes = size(source)
    projected_bytes = size(projected)
    reduced = max(0, source_bytes - projected_bytes)
    return {
        "version": ONTOLOGY_PROJECTION_INPUT_VERSION,
        "sourceExternalSignalBytes": source_bytes,
        "projectedExternalSignalBytes": projected_bytes,
        "reducedExternalSignalBytes": reduced,
        "reductionPct": round((reduced / source_bytes) * 100, 2) if source_bytes else 0.0,
        "targetSymbols": sorted(_symbols(target_symbols)),
        "includedSignalGroups": sorted(projected.keys()),
    }
