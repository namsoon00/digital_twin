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

from copy import deepcopy
import json
from typing import Dict, Iterable, Mapping, Set

from .company_knowledge import company_knowledge_by_symbol, merge_company_knowledge_rows
from .security_lines import security_lines_for_symbol


ONTOLOGY_PROJECTION_INPUT_VERSION = "ontology-projection-input-v2"
ONTOLOGY_REASONING_SNAPSHOT_INPUT_VERSION = "ontology-reasoning-snapshot-input-v2"

SYMBOL_SIGNAL_GROUPS = {
    "secFilings",
    "equityQuotes",
    "yfinanceData",
    "newsHeadlines",
    "dartDisclosures",
    "earningsReports",
    "companyOverviews",
    "researchEvidence",
    "companyKnowledge",
}

RESEARCH_ITEM_LIMIT = 12
TEXT_LIMIT = 1200
TEMPORAL_RESEARCH_ITEM_LIMIT = 3
TEMPORAL_RESEARCH_TEXT_LIMIT = 420


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
            "contrastSignals", "decisionInlineEligible", "decisionInlineReasonKo",
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
            "evidenceRole", "officialDocumentState", "documentVerified", "analysisReady",
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
        "officialDocumentState", "documentVerified", "analysisReady",
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
    for key in ["evidenceGovernance", "qualityGate", "newsEligibility", "promptEvidenceAdmission"]:
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
            "officialDocumentState", "documentVerified", "analysisReady",
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


def _compact_temporal_research_evidence_item(value: object) -> Dict[str, object]:
    """Keep only the facts needed to compare an article across snapshots.

    A temporal window needs to know whether a direct event existed and its
    direction. It does not need the article summary, claim ledger, or AI
    analysis again for every historical monitor row; those remain on the
    source snapshot and are still available for the current decision.
    """

    source = value if isinstance(value, Mapping) else {}
    result = _selected(
        source,
        [
            "evidenceId", "evidence_id", "symbol", "kind", "source", "title", "url",
            "polarity", "observedAt", "observed_at", "publishedAt", "published_at",
            "eventType", "relationScope", "sourceKind", "sourceOrigin", "sourcePlatform",
            "sourcePublisher", "materialityState", "sourceTrustState", "validationState",
            "lifecycleState", "lifecycleChangedAt", "stockImpact", "stockImpactLabel",
            "stockImpactPolarity", "stockImpactScore", "articleReadStatus",
        ],
        text_limit=TEMPORAL_RESEARCH_TEXT_LIMIT,
        list_limit=4,
        depth=1,
    )
    nested = source.get("payload") or source.get("rawPayload") or source.get("raw_payload")
    payload_source = nested if isinstance(nested, Mapping) else source
    payload = _selected(
        payload_source,
        [
            "relationScope", "eventType", "sourceTrustState", "materialityState",
            "validationState", "dataState", "sourceKind", "sourceOrigin", "sourcePlatform",
            "sourcePublisher", "stockImpact", "stockImpactLabel", "stockImpactPolarity",
            "stockImpactScore", "articleReadStatus", "bodyQualityState", "bodyQualityPassed",
        ],
        text_limit=240,
        list_limit=4,
        depth=1,
    )
    # Older stored evidence can carry these fields only on its envelope.
    for key in [
        "relationScope", "eventType", "sourceTrustState", "materialityState",
        "validationState", "dataState", "sourceKind", "sourceOrigin", "sourcePlatform",
        "sourcePublisher", "stockImpact", "stockImpactLabel", "stockImpactPolarity",
        "stockImpactScore", "articleReadStatus", "bodyQualityState", "bodyQualityPassed",
    ]:
        if key not in payload and key in source:
            payload[key] = _bounded_value(source.get(key), text_limit=240, list_limit=4, depth=1)
    if payload:
        result["payload"] = payload
    return result


def _compact_temporal_research_group(
    value: object,
    allowed_symbols: Set[str],
) -> Dict[str, object]:
    """Bound historical article facts independently from live research input."""

    source = value if isinstance(value, Mapping) else {}
    result = {}
    for raw_symbol in sorted(source, key=lambda item: str(item)):
        symbol = str(raw_symbol or "").upper().strip()
        if not symbol or (allowed_symbols and symbol not in allowed_symbols):
            continue
        rows = source.get(raw_symbol) if isinstance(source.get(raw_symbol), list) else []
        compact_rows = [
            _compact_temporal_research_evidence_item(row)
            for row in rows[:TEMPORAL_RESEARCH_ITEM_LIMIT]
            if isinstance(row, Mapping) and str(row.get("title") or "").strip()
        ]
        if compact_rows:
            result[symbol] = compact_rows
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
            "priceToBook", "trailingEps", "returnOnEquity", "returnOnAssets",
            "enterpriseToEbitda", "totalRevenue", "grossProfits", "operatingIncome",
            "netIncomeToCommon", "freeCashflow", "operatingCashflow", "totalCash",
            "totalDebt", "sharesOutstanding", "floatShares", "sharesShort",
            "heldPercentInstitutions", "heldPercentInsiders", "website",
            "mostRecentQuarter", "companyOfficers",
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
            "pbr", "bps", "bookValue", "trailingEPS", "epsTrailingTwelveMonths",
            "returnOnEquity", "returnOnAssets", "enterpriseToEbitda", "freeCashFlow",
            "operatingCashFlow", "totalDebt", "totalCash", "sharesOutstanding", "ceoName",
            "dividendYield", "analystTargetPrice", "analystRatingStrongBuy", "analystRatingBuy",
            "analystRatingHold", "analystRatingSell", "analystRatingStrongSell",
        ],
        text_limit=300,
        list_limit=8,
        depth=2,
    )


def _compact_earnings_report(value: object) -> Dict[str, object]:
    source = value if isinstance(value, Mapping) else {}
    result = _selected(source, [
        "symbol", "provider", "fetchedAt", "retrievedAt", "effectiveAt", "validFrom", "validUntil",
        "eventLifecycleState", "eventFreshnessClass", "eventDecisionEligible", "eventDecisionReason",
        "eventTimeContractVersion", "eventAgeMinutes", "eventMaxAgeMinutes",
    ], text_limit=240, depth=2)
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
            "retrievedAt", "effectiveAt", "validFrom", "validUntil", "eventLifecycleState",
            "eventFreshnessClass", "eventDecisionEligible", "eventDecisionReason",
            "eventTimeContractVersion", "eventAgeMinutes", "eventMaxAgeMinutes",
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


def _compact_company_knowledge(value: object) -> Dict[str, object]:
    """Preserve the canonical company facts without provider archives.

    CompanyKnowledge is already a normalized read model.  Applying the generic
    depth-three limiter converted nested financial period rows into strings,
    which made a cached statement disappear from change detection.  Keep a
    deliberately small number of structured periods here; the ABox builder
    applies its stricter current-state limits afterwards.
    """

    source = value if isinstance(value, Mapping) else {}
    result = _selected(
        source,
        ["schemaVersion", "symbol", "companyName", "factRevision", "materialRevision"],
        text_limit=240,
        depth=1,
    )
    for section in ("profile", "valuation", "ownership", "capital", "coverage"):
        compact = _bounded_value(
            source.get(section),
            text_limit=320,
            list_limit=8,
            map_limit=50,
            depth=3,
        )
        if compact:
            result[section] = compact

    financials = source.get("financials") if isinstance(source.get("financials"), Mapping) else {}
    compact_financials = {}
    for frequency in ("annual", "interim", "quarterly"):
        periods = financials.get(frequency) if isinstance(financials.get(frequency), list) else []
        compact_periods = [
            _bounded_value(
                period,
                text_limit=240,
                list_limit=6,
                map_limit=50,
                depth=3,
            )
            for period in periods[:4]
            if isinstance(period, Mapping)
        ]
        if compact_periods:
            compact_financials[frequency] = compact_periods
    if compact_financials:
        result["financials"] = compact_financials

    governance = source.get("governance") if isinstance(source.get("governance"), Mapping) else {}
    executives = governance.get("executives") if isinstance(governance.get("executives"), list) else []
    compact_executives = [
        _bounded_value(
            item,
            text_limit=320,
            list_limit=4,
            map_limit=20,
            depth=3,
        )
        for item in executives[:8]
        if isinstance(item, Mapping)
    ]
    if compact_executives or governance.get("executiveCount") not in (None, ""):
        result["governance"] = {
            "executiveCount": min(len(compact_executives), 8),
            "executives": compact_executives,
        }

    provenance = source.get("provenance") if isinstance(source.get("provenance"), list) else []
    compact_provenance = [
        _selected(item, ["provider", "asOf", "scope"], text_limit=240, depth=1)
        for item in provenance[:6]
        if isinstance(item, Mapping)
    ]
    if compact_provenance:
        result["provenance"] = compact_provenance
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
        elif group == "companyKnowledge":
            compact = _compact_company_knowledge(item)
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


def _compact_global_external_signals_for_ontology(
    external_signals: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Copy only the shared, non-symbol-scoped ABox input facts.

    The live reasoning cache stores these once per account generation.  It
    deliberately leaves quote, filing, research and other symbol payloads to
    the per-symbol rows so a one-symbol TypeDB turn does not have to decode a
    whole portfolio's provider archive first.
    """

    source = external_signals if isinstance(external_signals, Mapping) else {}
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
    for key in ["quality", "freshness", "cryptoFreshness", "provenance", "statuses"]:
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
    if source.get("cryptoFetchedAt") not in (None, ""):
        result["cryptoFetchedAt"] = _text(source.get("cryptoFetchedAt"), 120)
    return result


def compact_symbol_external_signals_for_ontology(
    external_signals: Mapping[str, object] = None,
    *,
    target_symbols: Iterable[object] = None,
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Return bounded symbol-scoped facts without duplicating global facts."""

    source = external_signals if isinstance(external_signals, Mapping) else {}
    allowed_symbols = projection_signal_symbols(target_symbols, settings)
    result: Dict[str, object] = {}
    for group in sorted(SYMBOL_SIGNAL_GROUPS):
        if group == "companyKnowledge":
            continue
        compact = _compact_symbol_group(group, source.get(group), allowed_symbols)
        if compact:
            result[group] = compact
    company_symbols = set(allowed_symbols)
    if not company_symbols:
        for group in ("companyOverviews", "yfinanceData", "secFilings", "dartDisclosures", "companyKnowledge"):
            rows = source.get(group)
            if isinstance(rows, Mapping):
                company_symbols.update(_symbols(rows.keys()))
    company_knowledge = company_knowledge_by_symbol(source, company_symbols)
    existing_company_knowledge = _compact_symbol_group(
        "companyKnowledge",
        source.get("companyKnowledge"),
        allowed_symbols,
    )
    for symbol, existing in existing_company_knowledge.items():
        candidate = company_knowledge.get(symbol) if isinstance(company_knowledge.get(symbol), Mapping) else {}
        company_knowledge[symbol] = merge_company_knowledge_rows(existing, candidate)
    if company_knowledge:
        result["companyKnowledge"] = company_knowledge
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

    return {
        **_compact_global_external_signals_for_ontology(external_signals),
        **compact_symbol_external_signals_for_ontology(
            external_signals,
            target_symbols=target_symbols,
            settings=settings,
        ),
    }


def compact_monitor_runtime_metadata_for_ontology(value: object) -> Dict[str, object]:
    """Keep the bounded runtime facts required by live ABox replay.

    These fields were previously read by deserialising the whole monitor
    record.  They are not a decision result: account context, proxy quotes and
    observation baselines are source facts that the existing projection and
    monitor paths already consume.  Prior ontology output stays out of the
    live ABox cache except for the small operational missing-inference marker
    used to avoid duplicate system diagnostics.
    """

    source = value if isinstance(value, Mapping) else {}
    result: Dict[str, object] = {}
    for key in [
        "accountContext",
        "connectionFailureStreak",
        "lastConnectionFailure",
        "marketObservationBaselines",
        "marketProxyQuotes",
        "marketSignalProxyQuotes",
    ]:
        if key in source and source.get(key) not in (None, ""):
            result[key] = _bounded_value(
                source.get(key),
                text_limit=900,
                list_limit=40,
                map_limit=100,
                depth=4,
            )
    ontology = source.get("ontology") if isinstance(source.get("ontology"), Mapping) else {}
    inference_missing = ontology.get("inferenceMissingState") if isinstance(ontology, Mapping) else None
    if isinstance(inference_missing, Mapping):
        result["ontology"] = {
            "inferenceMissingState": _bounded_value(
                inference_missing,
                text_limit=500,
                list_limit=20,
                map_limit=40,
                depth=3,
            ),
        }
    return result


def reasoning_snapshot_symbols(state: Mapping[str, object] = None) -> Set[str]:
    """Return all source subjects that need their own cached input row."""

    source = state if isinstance(state, Mapping) else {}
    symbols = set()
    for key in ("positions", "watchlist"):
        values = source.get(key)
        if isinstance(values, Mapping):
            symbols.update(_symbols(values.keys()))
        elif isinstance(values, (list, tuple, set)):
            for item in values:
                if isinstance(item, Mapping):
                    symbols.update(_symbols([item.get("symbol")]))
    signals = source.get("externalSignals") if isinstance(source.get("externalSignals"), Mapping) else {}
    for group in SYMBOL_SIGNAL_GROUPS:
        values = signals.get(group) if isinstance(signals, Mapping) else None
        if isinstance(values, Mapping):
            symbols.update(_symbols(values.keys()))
    return symbols


def compact_monitor_state_for_reasoning_base(
    state: Mapping[str, object] = None,
    *,
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Build the once-per-account portion of a persisted reasoning input.

    The normal monitor writes this in the same transaction as the verified
    snapshot and mailbox ingress.  Thus a queued fact revision is never read
    from an uncommitted or unrelated source cache.
    """

    source = state if isinstance(state, Mapping) else {}
    result: Dict[str, object] = {
        "version": ONTOLOGY_REASONING_SNAPSHOT_INPUT_VERSION,
    }
    for key in (
        "accountId",
        "accountLabel",
        "provider",
        "mode",
        "status",
        "generatedAt",
        "portfolio",
        "positions",
        "watchlist",
    ):
        value = source.get(key)
        if value not in (None, ""):
            result[key] = deepcopy(value)
    metadata = compact_monitor_runtime_metadata_for_ontology(source.get("metadata"))
    if metadata:
        result["metadata"] = metadata
    external_signals = _compact_global_external_signals_for_ontology(source.get("externalSignals"))
    if external_signals:
        result["externalSignals"] = external_signals
    return result


def compact_monitor_state_for_reasoning_symbol(
    state: Mapping[str, object] = None,
    symbol: object = "",
    *,
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Build the target-scoped portion of a persisted reasoning input."""

    clean_symbol = str(symbol or "").upper().strip()
    source = state if isinstance(state, Mapping) else {}
    result: Dict[str, object] = {
        "version": ONTOLOGY_REASONING_SNAPSHOT_INPUT_VERSION,
        "symbol": clean_symbol,
    }
    external_signals = compact_symbol_external_signals_for_ontology(
        source.get("externalSignals"),
        target_symbols=[clean_symbol] if clean_symbol else None,
        settings=settings,
    )
    if external_signals:
        result["externalSignals"] = external_signals
    return result


def frozen_monitor_state_for_reasoning(
    state: Mapping[str, object] = None,
    *,
    target_symbols: Iterable[object] = None,
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Freeze one bounded, replayable source packet for shadow inference.

    The packet contains observations only.  It preserves the exact prior-state
    and temporal-history inputs already selected by the active engine so an
    asynchronous candidate cannot accidentally read a newer monitor row.
    """

    source = state if isinstance(state, Mapping) else {}
    result = compact_monitor_state_for_reasoning_base(source, settings=settings)
    symbol_signals = compact_symbol_external_signals_for_ontology(
        source.get("externalSignals"),
        target_symbols=target_symbols,
        settings=settings,
    )
    if symbol_signals:
        result.setdefault("externalSignals", {}).update(symbol_signals)
    source_metadata = source.get("metadata") if isinstance(source.get("metadata"), Mapping) else {}
    metadata = result.setdefault("metadata", {})
    for key in ("previousMonitorState", "monitorStateHistory"):
        value = source_metadata.get(key)
        if value not in (None, "", [], {}):
            metadata[key] = deepcopy(value)
    metadata["reasoningSnapshotReplay"] = {
        "status": "ready",
        "mode": "immutable-shadow-input",
        "immutableInput": True,
        "snapshotGeneratedAt": str(source.get("generatedAt") or ""),
    }
    return result


def compact_monitor_state_for_ontology(
    state: Mapping[str, object] = None,
    *,
    target_symbols: Iterable[object] = None,
    settings: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Create the small historical state used by temporal ABox concepts.

    Full monitor snapshots deliberately retain provider archives so research
    and notification detail can be reconstructed later.  Those archives do
    not belong in every historical row read by live reasoning: a 96-row
    history of article bodies can turn a single quote update into hundreds of
    megabytes of JSON decoding before TypeDB receives one fact.

    This is a factual projection only.  It preserves the position, watchlist,
    portfolio, and bounded research facts needed by temporal concepts and
    omits derived decisions and provider document archives.
    """

    source = state if isinstance(state, Mapping) else {}
    result: Dict[str, object] = {}
    for key in (
        "accountId",
        "accountLabel",
        "provider",
        "mode",
        "status",
        "generatedAt",
        "portfolio",
        "positions",
        "watchlist",
    ):
        value = source.get(key)
        if value not in (None, ""):
            result[key] = deepcopy(value)
    signals = source.get("externalSignals")
    if isinstance(signals, Mapping):
        compact_signals = compact_external_signals_for_ontology(
            signals,
            target_symbols=target_symbols,
            settings=settings,
        )
        # Historical windows only count and compare events. Keep the full
        # compact research payload for the live snapshot, but store a much
        # smaller evidence envelope for every prior monitor generation.
        temporal_research = _compact_temporal_research_group(
            signals.get("researchEvidence"),
            projection_signal_symbols(target_symbols, settings),
        )
        if temporal_research:
            compact_signals["researchEvidence"] = temporal_research
        else:
            compact_signals.pop("researchEvidence", None)
        result["externalSignals"] = compact_signals
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
