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
FINANCIAL_REPORT_CONTRACT_VERSION = "financial-report-observation-v1"
FINANCIAL_PERIOD_SELECTION_VERSION = "financial-period-selection-v1"
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


def _source_reference_rows(values):
    fields = (
        "contractVersion", "datasetId", "providerId", "subjectKey", "revisionId",
        "providerRevision", "payloadHash", "sourceSchemaVersion", "sourceAsOf",
        "fetchedAt", "availability", "freshnessState",
    )
    result = []
    seen = set()
    for value in values or []:
        if not isinstance(value, Mapping):
            continue
        row = {field: value.get(field) for field in fields if value.get(field) not in (None, "")}
        identity = (str(row.get("datasetId") or ""), str(row.get("revisionId") or ""))
        if not identity[0] or identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return sorted(result, key=lambda row: (str(row.get("datasetId") or ""), str(row.get("revisionId") or "")))


def bind_financial_report_contract(row: Mapping, source_references=()):
    """Bind a normalized financial period to exact immutable source revisions.

    This contract describes the report observation only. It does not make a
    valuation or investment claim, and an absent provider timestamp remains
    absent rather than being replaced with the collection clock.
    """
    result = dict(row or {})
    metric_sources = [
        dict(value)
        for value in (result.get("metricProvenance") or {}).values()
        if isinstance(value, Mapping)
    ]
    provider = str(result.get("provider") or next((item.get("provider") for item in metric_sources if item.get("provider")), ""))
    provider_key = provider.lower()
    dataset_ids = (
        {"opendart.company_facts"} if "dart" in provider_key else
        {"sec.company_facts"} if "sec" in provider_key else
        {"yfinance.fundamental"} if "yfinance" in provider_key else set()
    )
    references = _source_reference_rows([
        item for item in source_references or []
        if not dataset_ids or str(item.get("datasetId") or "") in dataset_ids
    ])
    starts = [reporting_period_end(item.get("periodStart") or item.get("start")) for item in metric_sources]
    starts = [item for item in starts if item]
    end = reporting_period_end(result.get("periodEnd") or result.get("period"))
    filing_ids = sorted({
        str(item.get("receiptNo") or item.get("accessionNumber") or "").strip()
        for item in metric_sources
        if str(item.get("receiptNo") or item.get("accessionNumber") or "").strip()
    })
    published = sorted({
        str(item.get("publishedAt") or item.get("filed") or "").strip()
        for item in metric_sources
        if str(item.get("publishedAt") or item.get("filed") or "").strip()
    })
    currencies = sorted({str(item.get("currency") or "").strip() for item in metric_sources if str(item.get("currency") or "").strip()})
    scopes = sorted({str(item.get("scope") or "").strip() for item in metric_sources if str(item.get("scope") or "").strip()})
    duration_bases = sorted({str(item.get("durationBasis") or "").strip() for item in metric_sources if str(item.get("durationBasis") or "").strip()})
    fiscal_years = sorted({str(item.get("fiscalYear") or "").strip() for item in metric_sources if str(item.get("fiscalYear") or "").strip()})
    accounting_standards = sorted({str(item.get("accountingStandard") or "").strip() for item in metric_sources if str(item.get("accountingStandard") or "").strip()})
    material = {
        "contractVersion": FINANCIAL_REPORT_CONTRACT_VERSION,
        "provider": provider,
        "periodStart": min(starts).isoformat() if starts else "",
        "periodEnd": end.isoformat() if end else "",
        "frequency": str(result.get("frequency") or ""),
        "durationBases": duration_bases,
        "scope": scopes,
        "currencies": currencies,
        "fiscalYears": fiscal_years,
        "accountingStandards": accounting_standards,
        "publishedAt": published[-1] if published else "",
        "filingIds": filing_ids,
        "sourceReferences": references,
        "revisionState": "immutable-source-bound" if references else "provider-row-only",
        "correctionState": "not-indicated",
    }
    fingerprint_input = {
        **material,
        "reportedValues": {
            key: value for key, value in result.items()
            if key not in {"metricProvenance", "comparisonEvidence", "qualityIssues", "derivedMetricEvidence", "reportContract"}
        },
    }
    material["observationId"] = hashlib.sha256(
        json.dumps(fingerprint_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:24]
    result["reportContract"] = material
    return result


def financial_report_contract_assessment(row: Mapping, expected_frequency: object = ""):
    """Validate whether a cached report row may participate in current facts.

    A version marker by itself is not evidence that a legacy row was rebuilt
    from a real report.  Current-state merging therefore requires the report
    contract emitted at the source-normalization boundary and checks its
    period, frequency and duration semantics.  The original row remains in
    its immutable source/cache history when this assessment excludes it.
    """

    if not isinstance(row, Mapping):
        return {"eligible": False, "reason": "invalid-financial-period-row"}
    frequency = str(expected_frequency or row.get("frequency") or "").strip().lower()
    row_frequency = str(row.get("frequency") or "").strip().lower()
    if row.get("financialReportingVersion") != FINANCIAL_REPORTING_VERSION:
        return {"eligible": False, "reason": "unverified-financial-reporting-version"}
    report = row.get("reportContract") if isinstance(row.get("reportContract"), Mapping) else {}
    if not report:
        return {"eligible": False, "reason": "missing-financial-report-contract"}
    if report.get("contractVersion") != FINANCIAL_REPORT_CONTRACT_VERSION:
        return {"eligible": False, "reason": "unsupported-financial-report-contract"}
    references = [
        item for item in report.get("sourceReferences") or []
        if isinstance(item, Mapping)
        and str(item.get("datasetId") or "").strip()
        and str(item.get("revisionId") or "").strip()
    ]
    if report.get("revisionState") != "immutable-source-bound" or not references:
        return {"eligible": False, "reason": "missing-financial-report-source-revision"}
    if not str(report.get("observationId") or "").strip():
        return {"eligible": False, "reason": "missing-financial-report-observation-id"}
    contract_frequency = str(report.get("frequency") or "").strip().lower()
    if not frequency or row_frequency != frequency or contract_frequency != frequency:
        return {"eligible": False, "reason": "financial-report-frequency-mismatch"}
    period = reporting_period_end(row.get("periodEnd") or row.get("period"))
    contract_period = reporting_period_end(report.get("periodEnd"))
    if not period or not contract_period:
        return {"eligible": False, "reason": "invalid-financial-report-period"}
    if period != contract_period:
        return {"eligible": False, "reason": "financial-report-period-mismatch"}
    if not str(report.get("provider") or row.get("provider") or "").strip():
        return {"eligible": False, "reason": "missing-financial-report-provider"}

    duration_bases = {
        str(value or "").strip().lower()
        for value in report.get("durationBases") or []
        if str(value or "").strip()
    }
    duration_fields = {
        "revenue", "grossProfit", "operatingIncome", "netIncome", "netIncomeCommon",
        "basicEPS", "dilutedEPS", "weightedAverageSharesBasic", "weightedAverageSharesDiluted",
        "operatingCashFlow", "capitalExpenditure", "freeCashFlow",
    }
    has_duration_value = any(row.get(field) is not None for field in duration_fields)
    supported = {
        "annual": {"annual"},
        "interim": {"quarterly", "year-to-date"},
        "quarterly": {"quarterly"},
    }.get(frequency, set())
    observed_durations = duration_bases - {"instant"}
    if has_duration_value and (not observed_durations or not observed_durations.issubset(supported)):
        return {"eligible": False, "reason": "financial-report-duration-mismatch"}
    return {
        "eligible": True,
        "reason": "",
        "period": period.isoformat(),
        "frequency": frequency,
        "observationId": str(report.get("observationId") or ""),
        "revisionState": str(report.get("revisionState") or "unknown"),
    }


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
    report_contract = current.get("reportContract") if isinstance(current.get("reportContract"), Mapping) else {}
    if report_contract:
        material["report"] = dict(report_contract)
    ratios = current.get("derivedMetricEvidence") or {}
    material["ratios"] = [dict(ratios[key]) for key in ("freeCashFlowMarginPct", "cashConversionPct", "operatingMarginPct") if key in ratios]
    material["earningsQuality"] = {
        "status": "not-assessed",
        "normalizedEarningsAvailable": False,
        "reason": "일회성 손익·보조금·환율 효과를 분리한 원문 근거가 없어 지속 가능한 이익으로 단정하지 않습니다.",
        "cashFlowComparisonAvailable": any(item.get("metric") == "freeCashFlow" and item.get("status") == "verified-comparable" for item in comparisons),
        "cashConversionAvailable": "cashConversionPct" in ratios,
    }
    material["fingerprint"] = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:24]
    material["decisionFingerprint"] = _financial_decision_fingerprint(material)
    material["eventSemantics"] = "reporting-period-evidence-not-new-filing"
    return material


def _financial_decision_fingerprint(packet: Mapping) -> str:
    """Hash financial meaning while excluding collection and audit lineage."""
    if not isinstance(packet, Mapping):
        return ""
    payload = {
        key: packet.get(key)
        for key in ("period", "comparisons", "issues", "ratios", "earningsQuality")
        if key in packet
    }
    if not any(payload.get(key) for key in ("comparisons", "issues", "ratios", "earningsQuality")):
        return ""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()[:24]


def financial_evidence_use(packet: Mapping, previous: Mapping = None):
    """Describe evidence continuity, not investment merit or filing publication."""
    if not packet:
        return {}
    previous = previous or {}
    period = reporting_period_end(packet.get("period"))
    prior_period = reporting_period_end(previous.get("period"))
    fingerprint = _financial_decision_fingerprint(packet) or packet.get("fingerprint")
    prior_fingerprint = _financial_decision_fingerprint(previous) or previous.get("fingerprint")
    if fingerprint and prior_fingerprint and fingerprint == prior_fingerprint:
        state = "reused"
    elif not prior_fingerprint:
        state = "first-observed"
    elif period and prior_period and period > prior_period:
        state = "new-period"
    else:
        state = "revised"
    return {
        "state": state,
        "reportingPeriod": period.isoformat() if period else str(packet.get("period") or "")[:10],
        "previousReportingPeriod": prior_period.isoformat() if prior_period else str(previous.get("period") or "")[:10],
        "filingPublication": "not-established-by-financial-packet",
        "priceAttribution": "co-observation-not-proven-causation",
    }
