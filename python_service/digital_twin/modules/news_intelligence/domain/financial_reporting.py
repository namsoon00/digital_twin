"""Reporting dates and comparable, source-bound financial observations."""

from __future__ import annotations

import calendar
import hashlib
import json
import math
from datetime import date, datetime
import re
from typing import Mapping


FINANCIAL_REPORTING_VERSION = "financial-reporting-v2"
GROWTH_FIELDS = {
    "revenueGrowthPct": "revenue",
    "operatingIncomeGrowthPct": "operatingIncome",
    "netIncomeGrowthPct": "netIncome",
    "freeCashFlowGrowthPct": "freeCashFlow",
    "sharesOutstandingGrowthPct": "sharesOutstanding",
}
RATIO_FIELDS = {
    "grossMarginPct": ("grossProfit", "revenue"),
    "operatingMarginPct": ("operatingIncome", "revenue"),
    "netMarginPct": ("netIncome", "revenue"),
    "cashConversionPct": ("operatingCashFlow", "netIncome"),
    "freeCashFlowMarginPct": ("freeCashFlow", "revenue"),
    "debtToEquityPct": ("totalDebt", "equity"),
    "liabilitiesToAssetsPct": ("totalLiabilities", "totalAssets"),
}


def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def compatible_metric_sources(left, right):
    return all(not left.get(key) or not right.get(key) or left[key] == right[key]
               for key in ("provider", "currency", "scope", "durationBasis"))


def reporting_period_end(value: object):
    text = str(value or "").strip()
    # Parse timestamps before ranges; time and UTC-offset digits are not dates.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        pass
    dates = re.findall(r"(?<!\d)((?:19|20)\d{2})[./-]?(\d{2})[./-]?(\d{2})(?!\d)", text)
    for parts in reversed(dates):
        try:
            return date(*(int(part) for part in parts))
        except ValueError:
            continue
    if re.fullmatch(r"(?:19|20)\d{2}", text):
        return date(int(text), 12, 31)
    return None


def financial_period_sort_key(value: object):
    parsed = reporting_period_end(value)
    return (parsed.toordinal() if parsed else 0, str(value or ""))


def dart_reporting_date(year: object, code: object, *, prior: int = 0, balance: bool = False):
    try:
        year = int(year) - prior
        month = 12 if balance and prior else {"11011": 12, "11012": 6, "11013": 3, "11014": 9}[str(code)]
        return date(year, month, calendar.monthrange(year, month)[1]).isoformat()
    except (ValueError, TypeError, KeyError):
        return ""


def financial_comparison(current: Mapping, previous: Mapping, field: str):
    left, right = _finite(current.get(field)), _finite(previous.get(field))
    source = dict((current.get("metricProvenance") or {}).get(field) or {})
    prior_source = dict((previous.get("metricProvenance") or {}).get(field) or {})
    reason = ""
    if left is None or right in (None, 0):
        reason = "missing-comparison-value"
    elif not source.get("provider") or not prior_source.get("provider") or not current.get("frequency"):
        reason = "missing-comparison-lineage"
    for key in ("currency", "scope", "durationBasis", "shareCountBasis"):
        a, b = source.get(key), prior_source.get(key)
        if a and b and a != b:
            reason = "incomparable-" + key
    if source.get("provider") and prior_source.get("provider") and source["provider"] != prior_source["provider"]:
        reason = "different-source"
    start, end = reporting_period_end(previous.get("period")), reporting_period_end(current.get("period"))
    if not start or not end or start >= end:
        reason = "invalid-period-order"
    elif current.get("comparisonBasis") == "year-over-year" or current.get("frequency") == "annual":
        if not 350 <= (end - start).days <= 380:
            reason = "non-adjacent-comparison-period"
    elif current.get("frequency") == "quarterly" and not 75 <= (end - start).days <= 106:
        reason = "non-adjacent-comparison-period"
    change = None if reason else round((float(left) - float(right)) / abs(float(right)) * 100.0, 4)
    # This is a source-quality check, not an investment threshold. Large
    # secondary-vendor share-count discontinuities need official corroboration.
    if field == "sharesOutstanding" and change is not None and abs(change) >= 25 and not source.get("official"):
        reason = "share-count-discontinuity-unverified"
    return {
        "field": field, "currentPeriod": current.get("period"), "previousPeriod": previous.get("period"),
        "currentValue": left, "previousValue": right, "changePct": change,
        "basis": current.get("comparisonBasis") or ("year-over-year" if current.get("frequency") == "annual" else "previous-period"),
        "source": source, "previousSource": prior_source,
        "status": "verified-comparable" if not reason else "excluded", "reason": reason,
    }


def _ratio_evidence(row, ratio, numerator, denominator):
    sources = row.get("metricProvenance") or {}
    left_source, right_source = sources.get(numerator, {}), sources.get(denominator, {})
    left, right = _finite(row.get(numerator)), _finite(row.get(denominator))
    if (left is None or right in (None, 0) or not left_source.get("provider") or not right_source.get("provider")
            or not compatible_metric_sources(left_source, right_source)):
        return None
    return {"metric": ratio, "value": round(left / right * 100.0, 4), "period": row.get("period"),
            "provider": left_source["provider"], "official": bool(left_source.get("official") and right_source.get("official")),
            "currency": left_source.get("currency") or "", "scope": left_source.get("scope") or "",
            "durationBasis": left_source.get("durationBasis") or "", "status": "verified-comparable",
            "sourceUrl": left_source.get("sourceUrl") or "", "formula": numerator + " / " + denominator + " * 100",
            "numerator": {"metric": numerator, "value": left}, "denominator": {"metric": denominator, "value": right}}


def current_financial_state(financials: Mapping):
    """Merge only same-period facts; each metric keeps its own time basis."""
    candidates = []
    for frequency in ("annual", "interim", "quarterly"):
        rows = [dict(row) for row in financials.get(frequency) or [] if isinstance(row, Mapping)]
        if rows:
            candidates.append(max(rows, key=lambda row: financial_period_sort_key(row.get("period"))))
    candidates = [row for row in candidates if reporting_period_end(row.get("period"))]
    if not candidates:
        return {}
    newest = max(reporting_period_end(row["period"]) for row in candidates)
    candidates = [row for row in candidates if reporting_period_end(row["period"]) == newest]
    candidates.sort(key=lambda r: (bool(r.get("officialSource")), {"annual": 1, "interim": 2, "quarterly": 3}.get(r.get("frequency"), 0)))
    result, provenance, comparisons, paired_ratios = {}, {}, {}, {}
    for row in candidates:
        for ratio, (numerator, denominator) in RATIO_FIELDS.items():
            evidence = _ratio_evidence(row, ratio, numerator, denominator)
            if evidence:
                paired_ratios[ratio] = evidence
        row_sources = row.get("metricProvenance") or {}
        for field, value in row.items():
            if value is None or field in {"metricProvenance", "comparisonEvidence", "qualityIssues", "derivedMetricEvidence"} or field in RATIO_FIELDS:
                continue
            if field in GROWTH_FIELDS:
                comparison = (row.get("comparisonEvidence") or {}).get(field) or {}
                if comparison.get("status") != "verified-comparable":
                    continue
            if field in row_sources:
                existing = provenance.get(field) or {}
                incoming = row_sources[field]
                if existing.get("durationBasis") and incoming.get("durationBasis") and existing["durationBasis"] != incoming["durationBasis"]:
                    continue
                provenance[field] = dict(incoming)
            result[field] = value
        for growth, comparison in (row.get("comparisonEvidence") or {}).items():
            field = GROWTH_FIELDS.get(growth)
            if field and comparison.get("currentValue") == result.get(field):
                comparisons[growth] = comparison
    # Derived numbers must still refer to their source observation after merge.
    for growth, field in GROWTH_FIELDS.items():
        comparison = comparisons.get(growth) or {}
        if (not comparison or comparison.get("currentValue") != result.get(field)
                or comparison.get("source") != provenance.get(field, {})):
            result.pop(growth, None)
            comparisons.pop(growth, None)
        elif comparison.get("status") != "verified-comparable":
            result.pop(growth, None)
    ratios = {}
    for ratio, (numerator, denominator) in RATIO_FIELDS.items():
        # An official numerator must not erase an independently valid, same-
        # period vendor ratio. Keep its paired inputs rather than mixing sources.
        evidence = _ratio_evidence({**result, "metricProvenance": provenance}, ratio, numerator, denominator) or paired_ratios.get(ratio)
        if evidence:
            result[ratio] = evidence["value"]
            ratios[ratio] = evidence
            provenance[ratio] = {key: evidence[key] for key in ("provider", "official", "period", "scope", "durationBasis", "sourceUrl")}
    result.update({"metricProvenance": provenance, "comparisonEvidence": comparisons,
                   "derivedMetricEvidence": ratios,
                   "qualityIssues": [issue for row in candidates for issue in row.get("qualityIssues", [])],
                   "financialReportingVersion": (FINANCIAL_REPORTING_VERSION if all(
                       row.get("financialReportingVersion") == FINANCIAL_REPORTING_VERSION for row in candidates
                   ) else "legacy-unverified")})
    return result


def compact_financial_evidence(company: Mapping):
    """Small evidence packet that survives prompt and customer-view compression."""
    if not isinstance(company, Mapping):
        return {}
    if isinstance(company.get("financialEvidence"), Mapping):
        return dict(company["financialEvidence"])
    current = company.get("currentFinancialState") or current_financial_state(company.get("latestFinancials") or company.get("financials") or {})
    if not current:
        return {}
    comparisons = []
    for growth, field in GROWTH_FIELDS.items():
        item = (current.get("comparisonEvidence") or {}).get(growth) or {}
        if not item:
            continue
        source = item.get("source") or {}
        comparisons.append({
            "metric": field, "growthMetric": growth, "currentPeriod": item.get("currentPeriod"),
            "previousPeriod": item.get("previousPeriod"), "currentValue": item.get("currentValue"),
            "previousValue": item.get("previousValue"), "changePct": item.get("changePct"),
            "comparisonBasis": item.get("basis"), "status": item.get("status"), "reason": item.get("reason"),
            "provider": source.get("provider") or current.get("provider") or "",
            "sourceUrl": source.get("sourceUrl") or "", "receiptNo": source.get("receiptNo") or "",
            "currency": source.get("currency") or "", "scope": source.get("scope") or "",
            "durationBasis": source.get("durationBasis") or "", "shareCountBasis": source.get("shareCountBasis") or "",
        })
    material = {"version": current.get("financialReportingVersion") or "legacy-unverified",
                "period": current.get("period"), "comparisons": comparisons,
                "issues": list(current.get("qualityIssues") or [])[:6]}
    ratios = current.get("derivedMetricEvidence") or {}
    material["ratios"] = [dict(ratios[key]) for key in ("freeCashFlowMarginPct", "cashConversionPct", "operatingMarginPct") if key in ratios]
    material["fingerprint"] = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:24]
    material["eventSemantics"] = "reporting-period-evidence-not-new-filing"
    return material
