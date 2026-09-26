"""Build an auditable shadow DCF input bundle from retained company facts.

The builder never turns a missing forecast or capital item into zero.  Observed
financials and consensus are kept separate from versioned forecast assumptions,
so the first operational bundle remains reference-only until those assumptions
are reviewed and the model release is approved.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Dict, Mapping

from digital_twin.modules.news_intelligence.contracts import financial_report_contract_assessment


DRIVER_DCF_INPUT_VERSION = "driver-dcf-input-evidence-v1"
DRIVER_DCF_ASSUMPTION_VERSION = "driver-dcf-shadow-assumptions-v1"


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _finite(value: object):
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _timestamp(value: object):
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _latest_timestamp(values) -> str:
    parsed = [(item, _timestamp(item)) for item in values]
    valid = [(item, stamp) for item, stamp in parsed if stamp is not None]
    return max(valid, key=lambda item: item[1])[0] if valid else ""


def _source_references(lineage: Mapping[str, object], symbol: str) -> list[Dict[str, object]]:
    accepted = {"yfinance.fundamental", "yfinance.analyst", "fred.macro"}
    result = {}
    for item in (lineage or {}).values():
        if not isinstance(item, Mapping):
            continue
        dataset_id = _text(item.get("datasetId"))
        subject = _text(item.get("subjectKey")).upper()
        revision_id = _text(item.get("revisionId"))
        if dataset_id not in accepted or not revision_id:
            continue
        if dataset_id != "fred.macro" and subject != symbol:
            continue
        row = {
            "datasetId": dataset_id,
            "subjectKey": subject,
            "revisionId": revision_id,
            "providerRevision": _text(item.get("providerRevision") or item.get("sourceRevision")),
            "payloadHash": _text(item.get("payloadHash")),
            "sourceSchemaVersion": _text(item.get("sourceSchemaVersion")),
            "sourceAsOf": _text(item.get("sourceAsOf")),
            "fetchedAt": _text(item.get("fetchedAt")),
        }
        result[(dataset_id, subject, revision_id)] = {key: value for key, value in row.items() if value}
    return [result[key] for key in sorted(result)]


def _latest_verified_annual(company: Mapping[str, object]):
    financials = company.get("financials") if isinstance(company.get("financials"), Mapping) else {}
    for row in financials.get("annual") or []:
        if not isinstance(row, Mapping):
            continue
        assessment = financial_report_contract_assessment(row, "annual")
        if assessment.get("eligible"):
            return dict(row), dict(assessment)
    return {}, {"eligible": False, "reason": "verified-annual-report-missing"}


def _annual_currency(row: Mapping[str, object]) -> str:
    provenance = row.get("metricProvenance") if isinstance(row.get("metricProvenance"), Mapping) else {}
    revenue = provenance.get("revenue") if isinstance(provenance.get("revenue"), Mapping) else {}
    return _text(revenue.get("currency") or row.get("currency")).upper()


def _revenue_estimates(yfinance: Mapping[str, object]) -> Dict[str, Dict[str, object]]:
    result = {}
    for row in yfinance.get("revenueEstimate") or []:
        if not isinstance(row, Mapping):
            continue
        period = _text(row.get("period")).lower()
        value = _finite(row.get("avg"))
        if period in {"0y", "+1y"} and value is not None and value > 0:
            result[period] = {
                "value": value,
                "low": _finite(row.get("low")),
                "high": _finite(row.get("high")),
                "analystCount": int(_finite(row.get("numberOfAnalysts")) or 0),
                "growth": _finite(row.get("growth")),
            }
    return result


def _blocked(symbol: str, reasons, *, observed=None, references=None) -> Dict[str, object]:
    material = {
        "contractVersion": DRIVER_DCF_INPUT_VERSION,
        "status": "blocked",
        "symbol": symbol,
        "missingInputs": sorted(set(reasons)),
        "observedInputs": dict(observed or {}),
        "sourceReferences": list(references or []),
        "modelApprovalState": "not-registered",
        "decisionEligible": False,
    }
    return {**material, "readinessId": "driver-dcf-readiness:" + _digest(material)[:32]}


def build_driver_dcf_input_bundle(
    symbol: object,
    company: Mapping[str, object],
    *,
    overview: Mapping[str, object] = None,
    yfinance: Mapping[str, object] = None,
    macro: Mapping[str, object] = None,
    lineage: Mapping[str, object] = None,
    valuation_at: object = "",
    equity_risk_premium_pct: float = 5.0,
    terminal_growth_pct: float = 2.5,
) -> Dict[str, object]:
    """Return a shadow-only DCF bundle plus an explicit readiness contract."""

    normalized_symbol = _text(symbol).upper()
    overview = dict(overview or {})
    yfinance = dict(yfinance or {})
    macro = dict(macro or {})
    annual, annual_assessment = _latest_verified_annual(company or {})
    references = _source_references(lineage or {}, normalized_symbol)
    estimates = _revenue_estimates(yfinance)
    series = macro.get("series") if isinstance(macro.get("series"), Mapping) else {}
    dgs10 = series.get("DGS10") if isinstance(series.get("DGS10"), Mapping) else {}

    observed = {
        "annualPeriod": _text(annual_assessment.get("period") or annual.get("periodEnd") or annual.get("period")),
        "annualProvider": _text(annual.get("provider")),
        "currency": _annual_currency(annual),
        "revenue": _finite(annual.get("revenue")),
        "operatingIncome": _finite(annual.get("operatingIncome")),
        "pretaxIncome": _finite(annual.get("pretaxIncome")),
        "taxProvision": _finite(annual.get("taxProvision")),
        "interestExpense": _finite(annual.get("interestExpense")),
        "depreciationAmortization": _finite(annual.get("depreciationAmortization")),
        "capitalExpenditureReported": _finite(annual.get("capitalExpenditure")),
        "changeInWorkingCapitalCashFlow": _finite(annual.get("changeInWorkingCapital")),
        "stockBasedCompensation": _finite(annual.get("stockBasedCompensation")),
        "cash": _finite(annual.get("cash")),
        "debt": _finite(annual.get("totalDebt")),
        "dilutedShares": _finite(annual.get("weightedAverageSharesDiluted")),
        "marketCapitalization": _finite(overview.get("marketCapitalization")),
        "beta": _finite(overview.get("beta")),
        "riskFreeRatePct": _finite(dgs10.get("value")),
        "fy1RevenueConsensus": (estimates.get("0y") or {}).get("value"),
        "fy2RevenueConsensus": (estimates.get("+1y") or {}).get("value"),
    }
    required = {
        "revenue": "base-revenue-missing",
        "operatingIncome": "operating-income-missing",
        "pretaxIncome": "pretax-income-missing",
        "taxProvision": "tax-provision-missing",
        "interestExpense": "interest-expense-missing",
        "depreciationAmortization": "depreciation-amortization-missing",
        "capitalExpenditureReported": "capital-expenditure-missing",
        "changeInWorkingCapitalCashFlow": "working-capital-change-missing",
        "stockBasedCompensation": "stock-based-compensation-missing",
        "cash": "cash-missing",
        "debt": "debt-missing",
        "dilutedShares": "diluted-share-count-missing",
        "marketCapitalization": "market-capitalization-missing",
        "beta": "beta-missing",
        "riskFreeRatePct": "matching-risk-free-rate-missing",
        "fy1RevenueConsensus": "fy1-revenue-consensus-missing",
        "fy2RevenueConsensus": "fy2-revenue-consensus-missing",
    }
    reasons = []
    if not annual_assessment.get("eligible"):
        reasons.append(_text(annual_assessment.get("reason")) or "verified-annual-report-missing")
    if observed["currency"] != "USD":
        reasons.append("pilot-valuation-currency-not-supported")
    if not any(item.get("datasetId") == "yfinance.fundamental" for item in references):
        reasons.append("fundamental-source-revision-missing")
    if not any(item.get("datasetId") == "yfinance.analyst" for item in references):
        reasons.append("analyst-source-revision-missing")
    if not any(item.get("datasetId") == "fred.macro" for item in references):
        reasons.append("macro-source-revision-missing")
    for key, reason in required.items():
        value = observed.get(key)
        if value is None or (key in {"revenue", "dilutedShares", "marketCapitalization", "beta", "riskFreeRatePct", "fy1RevenueConsensus", "fy2RevenueConsensus"} and value <= 0):
            reasons.append(reason)
    if reasons:
        return _blocked(normalized_symbol, reasons, observed=observed, references=references)

    revenue = observed["revenue"]
    operating_margin_pct = observed["operatingIncome"] / revenue * 100.0
    if observed["pretaxIncome"] <= 0:
        return _blocked(normalized_symbol, ["non-positive-pretax-income"], observed=observed, references=references)
    tax_rate_pct = observed["taxProvision"] / observed["pretaxIncome"] * 100.0
    if tax_rate_pct < 0 or tax_rate_pct > 100:
        return _blocked(normalized_symbol, ["effective-tax-rate-out-of-range"], observed=observed, references=references)

    depreciation_ratio = observed["depreciationAmortization"] / revenue
    capex_ratio = abs(observed["capitalExpenditureReported"]) / revenue
    # Cash-flow statements report the cash contribution from working-capital
    # movements. FCFF subtracts the economic investment, which has the
    # opposite sign.
    nwc_investment_ratio = -observed["changeInWorkingCapitalCashFlow"] / revenue
    risk_free = observed["riskFreeRatePct"]
    beta = observed["beta"]
    cost_of_equity = risk_free + beta * float(equity_risk_premium_pct)
    debt_cost = abs(observed["interestExpense"]) / observed["debt"] * 100.0 if observed["debt"] > 0 else risk_free
    equity = observed["marketCapitalization"]
    debt = observed["debt"]
    capital = equity + debt
    wacc_pct = (equity / capital * cost_of_equity) + (debt / capital * debt_cost * (1.0 - tax_rate_pct / 100.0))

    fy1 = observed["fy1RevenueConsensus"]
    fy2 = observed["fy2RevenueConsensus"]
    fy2_growth_pct = (fy2 / fy1 - 1.0) * 100.0
    projection_revenues = [fy1, fy2]
    fade_steps = 3
    previous = fy2
    for step in range(1, fade_steps + 1):
        growth = fy2_growth_pct + (float(terminal_growth_pct) - fy2_growth_pct) * step / fade_steps
        previous *= 1.0 + growth / 100.0
        projection_revenues.append(previous)

    projection_years = []
    for year, projected_revenue in enumerate(projection_revenues, start=1):
        projection_years.append({
            "year": year,
            "revenue": round(projected_revenue, 4),
            "ebitMarginPct": round(operating_margin_pct, 8),
            "taxRatePct": round(tax_rate_pct, 8),
            "depreciationAmortization": round(projected_revenue * depreciation_ratio, 4),
            "capitalExpenditure": round(projected_revenue * capex_ratio, 4),
            "changeInWorkingCapital": round(projected_revenue * nwc_investment_ratio, 4),
            "currency": observed["currency"],
            "periodFraction": 1.0,
            "revenueBasis": "analyst-consensus" if year <= 2 else "versioned-growth-fade-assumption",
        })

    assumptions = [
        {"id": "fy1-revenue-consensus", "value": fy1, "unit": observed["currency"], "status": "observed"},
        {"id": "fy2-revenue-consensus", "value": fy2, "unit": observed["currency"], "status": "observed"},
        {"id": "equity-risk-premium", "value": float(equity_risk_premium_pct), "unit": "percent", "status": "candidate"},
        {"id": "wacc", "value": round(wacc_pct, 8), "unit": "percent", "status": "candidate"},
        {"id": "terminal-growth", "value": float(terminal_growth_pct), "unit": "percent", "status": "candidate"},
        {"id": "years-3-to-5-growth-fade", "value": fy2_growth_pct, "unit": "percent-start", "status": "candidate"},
        {"id": "constant-operating-margin", "value": round(operating_margin_pct, 8), "unit": "percent", "status": "candidate"},
        {"id": "constant-reinvestment-ratios", "value": True, "unit": "policy", "status": "candidate"},
        {"id": "preferred-equity-zero", "value": 0, "unit": observed["currency"], "status": "candidate"},
        {"id": "non-controlling-interest-zero", "value": 0, "unit": observed["currency"], "status": "candidate"},
    ]
    source_dates = [item.get("fetchedAt") or item.get("sourceAsOf") for item in references]
    effective_valuation_at = _text(valuation_at) or _latest_timestamp(source_dates)
    input_bundle = {
        "contractVersion": DRIVER_DCF_INPUT_VERSION,
        "assumptionVersion": DRIVER_DCF_ASSUMPTION_VERSION,
        "symbol": normalized_symbol,
        "currency": observed["currency"],
        "valuationAt": effective_valuation_at,
        "knowledgeCutoffAt": _latest_timestamp(source_dates),
        "quoteAsOf": _text(overview.get("fetchedAt")),
        "modelApplicability": "non-financial-company",
        "modelApprovalState": "shadow",
        "baseRevenue": revenue,
        "waccPct": round(wacc_pct, 8),
        "terminalGrowthPct": float(terminal_growth_pct),
        "cash": observed["cash"],
        "debt": observed["debt"],
        "preferredEquity": 0.0,
        "nonControllingInterest": 0.0,
        "nonOperatingAssets": 0.0,
        "dilutedShares": observed["dilutedShares"],
        "sbcPolicy": "expense-remains-in-ebit-and-existing-dilution-in-weighted-average-share-count",
        "maxTerminalValueSharePct": 85.0,
        "reverseGrowthSearchBracketPct": [0.0, 100.0],
        "sourceReferences": references,
        "assumptions": assumptions,
        "projectionYears": projection_years,
        "observedInputs": observed,
        "calculationNotes": {
            "workingCapitalNormalization": "economic investment = -provider cash-flow change in working capital",
            "waccFormula": "E/(D+E)*costOfEquity + D/(D+E)*costOfDebt*(1-taxRate)",
            "costOfEquityFormula": "matching 10Y Treasury + beta * candidate equity risk premium",
            "futureYears": "FY1/FY2 consensus followed by a versioned fade to terminal growth",
        },
    }
    material = {
        "contractVersion": DRIVER_DCF_INPUT_VERSION,
        "status": "ready-for-shadow",
        "symbol": normalized_symbol,
        "inputBundleId": "driver-dcf-input:" + _digest(input_bundle)[:32],
        "missingInputs": [],
        "observedInputs": observed,
        "sourceReferences": references,
        "assumptionReviewState": "required",
        "modelApprovalState": "shadow",
        "decisionEligible": False,
    }
    return {
        **material,
        "readinessId": "driver-dcf-readiness:" + _digest(material)[:32],
        "input": {**input_bundle, "inputBundleId": material["inputBundleId"]},
    }


__all__ = [
    "DRIVER_DCF_ASSUMPTION_VERSION",
    "DRIVER_DCF_INPUT_VERSION",
    "build_driver_dcf_input_bundle",
]
