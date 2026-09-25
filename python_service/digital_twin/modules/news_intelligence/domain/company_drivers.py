"""Verified company drivers and explicit macro exposure links.

Drivers describe how a company fact can enter a valuation model.  A macro
observation is never promoted into a company impact unless the company has an
explicit exposure record from a source or a versioned scenario assumption.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Dict, Mapping

from digital_twin.modules.news_intelligence.domain.financial_reporting import financial_report_contract_assessment


COMPANY_DRIVER_MAP_VERSION = "company-driver-map-v1"
MAX_PRIMARY_DRIVERS = 3


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _finite(value: object):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _latest_verified_annual(company: Mapping[str, object]) -> tuple[Dict[str, object], Dict[str, object]]:
    financials = company.get("financials") if isinstance(company.get("financials"), Mapping) else {}
    for item in financials.get("annual") or []:
        if not isinstance(item, Mapping):
            continue
        assessment = financial_report_contract_assessment(item, "annual")
        if assessment.get("eligible"):
            return dict(item), assessment
    return {}, {"eligible": False, "reason": "verified-annual-report-missing"}


def _driver(
    symbol: str,
    row: Mapping[str, object],
    assessment: Mapping[str, object],
    *,
    driver_id: str,
    label: str,
    metric: str,
    unit: str,
    model_input: str,
    normalize_outflow: bool = False,
) -> Dict[str, object]:
    value = _finite(row.get(metric))
    if value is None:
        return {}
    report = row.get("reportContract") if isinstance(row.get("reportContract"), Mapping) else {}
    provenance = row.get("metricProvenance") if isinstance(row.get("metricProvenance"), Mapping) else {}
    metric_source = provenance.get(metric) if isinstance(provenance.get(metric), Mapping) else {}
    reported_value = value
    if normalize_outflow:
        value = abs(value)
    result = {
        "driverId": symbol + ":" + driver_id,
        "symbol": symbol,
        "label": label,
        "metric": metric,
        "value": value,
        "unit": _text(metric_source.get("currency")) or unit,
        "period": _text(assessment.get("period") or row.get("periodEnd") or row.get("period")),
        "scope": _text(metric_source.get("scope") or row.get("accountingScope")),
        "segment": _text(metric_source.get("segment")),
        "modelInput": model_input,
        "observationId": _text(report.get("observationId")),
        "sourceReferences": [dict(item) for item in report.get("sourceReferences") or [] if isinstance(item, Mapping)],
        "sourceLocation": {
            key: metric_source.get(key)
            for key in ("statement", "tag", "field", "line", "section")
            if metric_source.get(key) not in (None, "")
        },
        "validationState": "verified",
        "dependencyKey": "company-driver:" + symbol + ":" + driver_id,
    }
    if normalize_outflow:
        result.update({
            "reportedValue": reported_value,
            "valueConvention": "positive-outflow-magnitude",
            "normalizationFormula": "abs(reported cash-flow statement value)",
        })
    return result


def _explicit_exposures(company: Mapping[str, object]) -> list[Dict[str, object]]:
    raw = company.get("businessExposures")
    values = raw if isinstance(raw, list) else []
    result = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        exposure_type = _text(item.get("type")).lower()
        source_references = [
            dict(reference) for reference in item.get("sourceReferences") or []
            if isinstance(reference, Mapping)
            and _text(reference.get("datasetId"))
            and _text(reference.get("revisionId"))
        ]
        if exposure_type not in {"fx-revenue", "fx-cost", "fx-debt", "floating-rate-debt", "fixed-rate-debt"}:
            continue
        result.append({
            "exposureId": _text(item.get("exposureId")) or "company-exposure:" + _hash(item)[:20],
            "type": exposure_type,
            "currency": _text(item.get("currency")).upper(),
            "rateKind": _text(item.get("rateKind")).lower(),
            "sharePct": _finite(item.get("sharePct")),
            "amount": _finite(item.get("amount")),
            "unit": _text(item.get("unit")),
            "period": _text(item.get("period")),
            "hedgeState": _text(item.get("hedgeState")) or "unknown",
            "passThroughState": _text(item.get("passThroughState")) or "unknown",
            "sourceReferences": source_references,
            "validationState": "verified" if source_references else "assumption-only",
        })
    return sorted(result, key=lambda item: item["exposureId"])


def build_company_driver_map(
    symbol: object,
    company: Mapping[str, object],
    *,
    macro_context: Mapping[str, object] = None,
    fx_rates: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Build at most three verified primary drivers plus bounded macro links."""

    normalized_symbol = _text(symbol or company.get("symbol")).upper()
    annual, annual_assessment = _latest_verified_annual(company or {})
    candidates = [
        _driver(normalized_symbol, annual, annual_assessment, driver_id="revenue-growth", label="매출 성장", metric="revenueGrowthPct", unit="percent", model_input="revenue-growth"),
        _driver(normalized_symbol, annual, annual_assessment, driver_id="operating-margin", label="영업이익률", metric="operatingMarginPct", unit="percent", model_input="ebit-margin"),
        _driver(normalized_symbol, annual, annual_assessment, driver_id="reinvestment", label="설비투자 현금유출", metric="capitalExpenditure", unit=_text(annual.get("currency")) or "reported-currency", model_input="capital-expenditure", normalize_outflow=True),
    ]
    drivers = [item for item in candidates if item][:MAX_PRIMARY_DRIVERS]
    exposures = _explicit_exposures(company or {})
    macro = dict(macro_context or {})
    fx = dict(fx_rates or {})
    links = []
    unresolved = []

    for exposure in exposures:
        kind = exposure["type"]
        if kind.startswith("fx-"):
            currency = exposure.get("currency")
            pair_candidates = [
                key for key in fx
                if currency and currency in _text(key).upper()
            ]
            state = "linked" if pair_candidates else "source-observation-missing"
            links.append({
                "linkId": exposure["exposureId"] + ":fx",
                "exposureId": exposure["exposureId"],
                "macroKind": "fx",
                "sourceKeys": sorted(pair_candidates),
                "direction": "explicit-company-exposure",
                "impactTarget": "revenue" if kind == "fx-revenue" else "cost" if kind == "fx-cost" else "net-debt",
                "state": state,
            })
        elif kind in {"floating-rate-debt", "fixed-rate-debt"}:
            rate_series = macro.get("series") if isinstance(macro.get("series"), Mapping) else {}
            rate_kind = exposure.get("rateKind")
            candidates = [key for key in rate_series if not rate_kind or rate_kind in _text(key).lower()]
            state = "linked" if candidates else "source-observation-missing"
            links.append({
                "linkId": exposure["exposureId"] + ":rate",
                "exposureId": exposure["exposureId"],
                "macroKind": "interest-rate",
                "sourceKeys": sorted(candidates),
                "direction": "explicit-company-exposure",
                "impactTarget": "interest-expense",
                "state": state,
            })

    if fx and not any(item.get("macroKind") == "fx" for item in links):
        unresolved.append({
            "kind": "fx-company-impact",
            "reason": "company-currency-exposure-missing",
            "availableMarketFacts": sorted(_text(key) for key in fx),
        })
    if macro and not any(item.get("macroKind") == "interest-rate" for item in links):
        unresolved.append({
            "kind": "rate-company-impact",
            "reason": "company-debt-rate-exposure-missing",
            "availableMarketFacts": sorted(_text(key) for key in (macro.get("series") or {})),
        })

    material = {
        "contractVersion": COMPANY_DRIVER_MAP_VERSION,
        "symbol": normalized_symbol,
        "drivers": drivers,
        "exposures": exposures,
        "macroLinks": links,
        "unresolved": unresolved,
        "annualReportState": "verified" if annual_assessment.get("eligible") else "unavailable",
        "annualReportReason": _text(annual_assessment.get("reason")),
    }
    return {
        **material,
        "driverMapId": "company-driver-map:" + _hash(material)[:32],
        "materialFingerprint": "company-driver-material:" + _hash(material),
        "modelInputEligible": bool(drivers),
    }


__all__ = ["COMPANY_DRIVER_MAP_VERSION", "MAX_PRIMARY_DRIVERS", "build_company_driver_map"]
