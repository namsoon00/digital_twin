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


DRIVER_DCF_INPUT_VERSION = "driver-dcf-input-evidence-v4-consensus-validation"
DRIVER_DCF_ASSUMPTION_VERSION = "driver-dcf-shadow-assumptions-v3-market-currency"
DRIVER_DCF_FINANCIAL_EVIDENCE_VERSION = "driver-dcf-financial-evidence-v1"
DRIVER_DCF_ASSUMPTION_REVIEW_VERSION = "driver-dcf-assumption-review-v1"
DRIVER_DCF_CONSENSUS_EVIDENCE_VERSION = "driver-dcf-consensus-evidence-v1"
DRIVER_DCF_MARKET_EVIDENCE_VERSION = "driver-dcf-market-evidence-v1"
MAX_CONSENSUS_REVENUE_GROWTH_PCT = 100.0
MIN_CONSENSUS_REVENUE_GROWTH_PCT = -80.0

FINANCIAL_INPUT_METRICS = (
    "revenue", "operatingIncome", "pretaxIncome", "taxProvision", "interestExpense",
    "depreciationAmortization", "capitalExpenditure", "changeInWorkingCapital",
    "stockBasedCompensation", "cash", "totalDebt", "weightedAverageSharesDiluted",
)
OFFICIAL_FINANCIAL_DATASETS = {
    "sec.company_facts",
    "opendart.company_facts",
    "opendart.xbrl_facts",
    "public-data.kr-company-financials",
}
RISK_FREE_SERIES_BY_CURRENCY = {
    "USD": {"seriesId": "DGS10", "datasetId": "fred.macro", "label": "미국 10년 국채금리"},
    "KRW": {"seriesId": "KRGB10Y", "datasetId": "ecos.macro", "label": "한국 10년 국고채금리"},
}
MARKET_BETA_BENCHMARK_BY_CURRENCY = {
    "USD": "SPY",
    "KRW": "^KS11",
}


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


def _source_references(
    lineage: Mapping[str, object],
    symbol: str,
    currency: str,
    price_subjects=(),
) -> list[Dict[str, object]]:
    accepted = {
        *OFFICIAL_FINANCIAL_DATASETS,
        "yfinance.fundamental", "yfinance.analyst", "yfinance.price", "fred.macro", "ecos.macro",
    }
    macro_dataset = str((RISK_FREE_SERIES_BY_CURRENCY.get(currency) or {}).get("datasetId") or "")
    accepted_price_subjects = {
        _text(item).upper() for item in [symbol, *list(price_subjects or [])] if _text(item)
    }
    result = {}
    for item in (lineage or {}).values():
        if not isinstance(item, Mapping):
            continue
        dataset_id = _text(item.get("datasetId"))
        subject = _text(item.get("subjectKey")).upper()
        revision_id = _text(item.get("revisionId"))
        if dataset_id not in accepted or not revision_id:
            continue
        if dataset_id in {"fred.macro", "ecos.macro"} and dataset_id != macro_dataset:
            continue
        if dataset_id == "yfinance.price" and subject not in accepted_price_subjects:
            continue
        if dataset_id not in {"fred.macro", "ecos.macro"} and subject != symbol:
            if dataset_id != "yfinance.price":
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


def _annual_candidates(company: Mapping[str, object]) -> list[Dict[str, object]]:
    financials = company.get("financials") if isinstance(company.get("financials"), Mapping) else {}
    values = [
        *(financials.get("annual") or []),
        *(company.get("valuationFinancialCandidates") or []),
    ]
    result = {}
    for row in values:
        if not isinstance(row, Mapping):
            continue
        assessment = financial_report_contract_assessment(row, "annual")
        if assessment.get("eligible"):
            report = row.get("reportContract") if isinstance(row.get("reportContract"), Mapping) else {}
            identity = _text(report.get("observationId")) or _digest(row)
            result[identity] = dict(row)
    return sorted(
        result.values(),
        key=lambda item: _text(item.get("periodEnd") or item.get("period")),
        reverse=True,
    )


def _latest_verified_annual(company: Mapping[str, object]):
    candidates = _annual_candidates(company)
    complete = [
        row for row in candidates
        if all(_finite(row.get(metric)) is not None for metric in FINANCIAL_INPUT_METRICS)
    ]
    if complete:
        row = complete[0]
        latest_period = _timestamp(row.get("periodEnd") or row.get("period"))
        official_complete = []
        for candidate in complete:
            provenance = candidate.get("metricProvenance") if isinstance(candidate.get("metricProvenance"), Mapping) else {}
            if all(
                isinstance(provenance.get(metric), Mapping) and bool(provenance[metric].get("official"))
                for metric in FINANCIAL_INPUT_METRICS
            ):
                official_complete.append(candidate)
        if official_complete:
            official = official_complete[0]
            official_period = _timestamp(official.get("periodEnd") or official.get("period"))
            if (
                latest_period is None
                or official_period is None
                or official_period >= latest_period
                or (latest_period - official_period).days <= 62
            ):
                row = official
        return row, dict(financial_report_contract_assessment(row, "annual"))
    if candidates:
        row = candidates[0]
        return row, dict(financial_report_contract_assessment(row, "annual"))
    return {}, {"eligible": False, "reason": "verified-annual-report-missing"}


def _row_source_references(row: Mapping[str, object]) -> list[Dict[str, object]]:
    report = row.get("reportContract") if isinstance(row.get("reportContract"), Mapping) else {}
    provider = _text(report.get("provider") or row.get("provider")).casefold()
    provenance = row.get("metricProvenance") if isinstance(row.get("metricProvenance"), Mapping) else {}
    reported_references = [item for item in report.get("sourceReferences") or [] if isinstance(item, Mapping)]
    has_official_provenance = any(
        isinstance(item, Mapping) and bool(item.get("official"))
        for item in provenance.values()
    )
    has_official_reference = any(
        _text(item.get("datasetId")) in OFFICIAL_FINANCIAL_DATASETS
        for item in reported_references
    )
    allowed = (
        OFFICIAL_FINANCIAL_DATASETS
        if bool(row.get("officialSource")) or has_official_provenance or has_official_reference
        or "sec" in provider or "dart" in provider
        else {"yfinance.fundamental"}
    )
    return [
        dict(item) for item in reported_references
        if isinstance(item, Mapping) and _text(item.get("datasetId")) in allowed
        and _text(item.get("revisionId"))
    ]


def _financial_evidence_contract(
    row: Mapping[str, object],
    assessment: Mapping[str, object],
    candidates: list[Mapping[str, object]],
) -> Dict[str, object]:
    provenance = row.get("metricProvenance") if isinstance(row.get("metricProvenance"), Mapping) else {}
    official_metrics = sorted(
        metric for metric in FINANCIAL_INPUT_METRICS
        if isinstance(provenance.get(metric), Mapping) and bool(provenance[metric].get("official"))
    )
    missing_metrics = sorted(metric for metric in FINANCIAL_INPUT_METRICS if _finite(row.get(metric)) is None)
    missing_official = sorted(set(FINANCIAL_INPUT_METRICS) - set(official_metrics))
    references = _row_source_references(row)
    official_references = [item for item in references if item.get("datasetId") in OFFICIAL_FINANCIAL_DATASETS]
    if len(official_metrics) == len(FINANCIAL_INPUT_METRICS) and official_references:
        source_class = "official-filing"
    elif official_metrics:
        source_class = "mixed"
    else:
        source_class = "secondary-aggregator"
    alternatives = []
    for candidate in candidates:
        candidate_provenance = candidate.get("metricProvenance") if isinstance(candidate.get("metricProvenance"), Mapping) else {}
        count = sum(
            1 for metric in FINANCIAL_INPUT_METRICS
            if isinstance(candidate_provenance.get(metric), Mapping) and candidate_provenance[metric].get("official")
        )
        if count:
            alternatives.append({
                "period": _text(candidate.get("periodEnd") or candidate.get("period")),
                "provider": _text(candidate.get("provider")),
                "officialMetricCount": count,
                "requiredMetricCount": len(FINANCIAL_INPUT_METRICS),
            })
    blockers = []
    if missing_metrics:
        blockers.append("financial-input-metrics-missing")
    if missing_official:
        blockers.append("official-financial-metric-coverage-incomplete")
    if not official_references:
        blockers.append("official-financial-source-revision-missing")
    material = {
        "contractVersion": DRIVER_DCF_FINANCIAL_EVIDENCE_VERSION,
        "status": "official-ready" if not blockers else "reference-only",
        "period": _text(assessment.get("period") or row.get("periodEnd") or row.get("period")),
        "provider": _text(row.get("provider")),
        "sourceClass": source_class,
        "requiredMetricCount": len(FINANCIAL_INPUT_METRICS),
        "officialMetricCount": len(official_metrics),
        "officialMetrics": official_metrics,
        "missingMetrics": missing_metrics,
        "missingOfficialMetrics": missing_official,
        "sourceReferences": references,
        "officialAlternatives": alternatives,
        "officialDecisionReady": not blockers,
        "blockingReasons": blockers,
    }
    return {**material, "evidenceId": "driver-dcf-financial-evidence:" + _digest(material)[:32]}


def _annual_currency(row: Mapping[str, object]) -> str:
    provenance = row.get("metricProvenance") if isinstance(row.get("metricProvenance"), Mapping) else {}
    revenue = provenance.get("revenue") if isinstance(provenance.get("revenue"), Mapping) else {}
    return _text(revenue.get("currency") or row.get("currency")).upper()


def _risk_free_observation(
    macro: Mapping[str, object],
    currency: str,
) -> Dict[str, object]:
    specification = dict(RISK_FREE_SERIES_BY_CURRENCY.get(currency) or {})
    series = macro.get("series") if isinstance(macro.get("series"), Mapping) else {}
    series_id = _text(specification.get("seriesId"))
    row = series.get(series_id) if isinstance(series.get(series_id), Mapping) else {}
    return {
        **specification,
        "currency": currency,
        "value": _finite(row.get("value")),
        "date": _text(row.get("date") or row.get("observationDate") or row.get("sourceAsOf")),
        "provider": _text(row.get("provider")),
        "sourceSeriesCode": _text(row.get("sourceSeriesCode")),
        "sourceItemCode": _text(row.get("sourceItemCode")),
    }


def _market_assumption(
    currency: str,
    values: Mapping[str, object] | None,
    fallback: object,
) -> float:
    configured = _finite((values or {}).get(currency))
    fallback_value = _finite(fallback)
    return configured if configured is not None else float(fallback_value if fallback_value is not None else 0.0)


def _revenue_estimates(yfinance: Mapping[str, object]) -> Dict[str, Dict[str, object]]:
    info = yfinance.get("info") if isinstance(yfinance.get("info"), Mapping) else {}
    currency_values = [
        (info.get("financialCurrency"), "provider-financial-currency"),
    ]
    currency, currency_basis = next(
        ((_text(value).upper(), basis) for value, basis in currency_values if _text(value)),
        ("", "missing"),
    )
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
                "yearAgoRevenue": _finite(row.get("yearAgoRevenue")),
                "currency": _text(row.get("currency")).upper() or currency,
                "currencyBasis": "provider-estimate-currency" if _text(row.get("currency")) else currency_basis,
                "targetPeriodEnd": _text(row.get("targetPeriodEnd") or row.get("endDate")),
                "sourceAsOf": _text(row.get("sourceAsOf")),
                "provider": _text(yfinance.get("provider") or "yfinance"),
                "horizon": "FY1" if period == "0y" else "FY2",
            }
    return result


def _consensus_evidence(
    estimates: Mapping[str, Mapping[str, object]],
    *,
    annual_revenue: object,
    annual_currency: str,
) -> Dict[str, object]:
    """Validate analyst estimates before they can drive a valuation.

    The provider's financial currency is the unit contract for the estimate
    table.  Missing currency or an implausible step change fails closed because
    a unit mismatch can otherwise produce a plausible-looking DCF value.
    """

    base = _finite(annual_revenue)
    rows = []
    blockers = []
    previous = base
    for period, horizon in (("0y", "FY1"), ("+1y", "FY2")):
        estimate = estimates.get(period) if isinstance(estimates.get(period), Mapping) else {}
        value = _finite(estimate.get("value"))
        low = _finite(estimate.get("low"))
        high = _finite(estimate.get("high"))
        analyst_count = int(_finite(estimate.get("analystCount")) or 0)
        currency = _text(estimate.get("currency")).upper()
        growth_pct = ((value / previous) - 1.0) * 100.0 if value is not None and previous not in (None, 0) else None
        row_blockers = []
        if value is None or value <= 0:
            row_blockers.append(f"{horizon.lower()}-revenue-consensus-missing")
        if not currency:
            row_blockers.append("consensus-currency-missing")
        elif currency != annual_currency:
            row_blockers.append("consensus-currency-mismatch")
        if analyst_count <= 0:
            row_blockers.append(f"{horizon.lower()}-analyst-count-missing")
        if low is not None and value is not None and low > value:
            row_blockers.append(f"{horizon.lower()}-consensus-range-invalid")
        if high is not None and value is not None and high < value:
            row_blockers.append(f"{horizon.lower()}-consensus-range-invalid")
        if growth_pct is not None and (
            growth_pct > MAX_CONSENSUS_REVENUE_GROWTH_PCT
            or growth_pct < MIN_CONSENSUS_REVENUE_GROWTH_PCT
        ):
            row_blockers.append(f"{horizon.lower()}-revenue-consensus-growth-outlier")
        rows.append({
            "periodToken": period,
            "horizon": horizon,
            "value": value,
            "low": low,
            "high": high,
            "currency": currency,
            "currencyBasis": _text(estimate.get("currencyBasis")),
            "analystCount": analyst_count,
            "provider": _text(estimate.get("provider")),
            "growthPct": round(growth_pct, 6) if growth_pct is not None else None,
            "status": "validated" if not row_blockers else "blocked",
            "blockingReasons": sorted(set(row_blockers)),
        })
        blockers.extend(row_blockers)
        if value is not None and value > 0:
            previous = value
    material = {
        "contractVersion": DRIVER_DCF_CONSENSUS_EVIDENCE_VERSION,
        "status": "validated" if not blockers else "blocked",
        "currency": annual_currency,
        "rows": rows,
        "blockingReasons": sorted(set(blockers)),
    }
    return {**material, "evidenceId": "driver-dcf-consensus-evidence:" + _digest(material)[:32]}


def _history_prices(payload: Mapping[str, object]) -> Dict[str, float]:
    result = {}
    for row in payload.get("history") or []:
        if not isinstance(row, Mapping):
            continue
        date = _text(row.get("Date") or row.get("date") or row.get("index"))[:10]
        price = _finite(row.get("Adj Close"))
        if price is None:
            price = _finite(row.get("Close"))
        if date and price is not None and price > 0:
            result[date] = price
    return result


def calculate_historical_market_beta(
    equity_yfinance: Mapping[str, object],
    benchmark_yfinance: Mapping[str, object],
    *,
    benchmark_symbol: str,
    minimum_samples: int = 60,
) -> Dict[str, object]:
    """Calculate beta from aligned observed daily returns with an audit trace."""

    equity_prices = _history_prices(equity_yfinance or {})
    benchmark_prices = _history_prices(benchmark_yfinance or {})

    def returns(prices):
        values = {}
        previous = None
        for date in sorted(prices):
            current = prices[date]
            if previous is not None and previous > 0:
                values[date] = current / previous - 1.0
            previous = current
        return values

    equity_returns = returns(equity_prices)
    benchmark_returns = returns(benchmark_prices)
    dates = sorted(set(equity_returns).intersection(benchmark_returns))
    blockers = []
    if len(dates) < max(2, int(minimum_samples)):
        blockers.append("beta-return-samples-insufficient")
    beta = None
    if not blockers:
        xs = [benchmark_returns[date] for date in dates]
        ys = [equity_returns[date] for date in dates]
        x_mean = sum(xs) / len(xs)
        y_mean = sum(ys) / len(ys)
        variance_sum = sum((value - x_mean) ** 2 for value in xs)
        if variance_sum <= 0:
            blockers.append("benchmark-return-variance-zero")
        else:
            beta = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / variance_sum
            if not math.isfinite(beta) or beta < -3.0 or beta > 5.0:
                beta = None
                blockers.append("beta-out-of-range")
    material = {
        "contractVersion": DRIVER_DCF_MARKET_EVIDENCE_VERSION,
        "status": "calculated" if beta is not None and not blockers else "blocked",
        "method": "covariance-of-aligned-daily-returns-divided-by-benchmark-variance",
        "benchmarkSymbol": _text(benchmark_symbol).upper(),
        "sampleCount": len(dates),
        "minimumSampleCount": max(2, int(minimum_samples)),
        "windowStart": dates[0] if dates else "",
        "windowEnd": dates[-1] if dates else "",
        "beta": round(beta, 8) if beta is not None else None,
        "blockingReasons": blockers,
    }
    return {**material, "evidenceId": "driver-dcf-market-beta:" + _digest(material)[:32]}


def _market_evidence(
    overview: Mapping[str, object],
    yfinance: Mapping[str, object],
    benchmark_yfinance: Mapping[str, object],
    *,
    benchmark_symbol: str,
    annual_currency: str,
    diluted_shares: object,
) -> Dict[str, object]:
    info = yfinance.get("info") if isinstance(yfinance.get("info"), Mapping) else {}
    fast_info = yfinance.get("fastInfo") if isinstance(yfinance.get("fastInfo"), Mapping) else {}
    history_metadata = yfinance.get("historyMetadata") if isinstance(yfinance.get("historyMetadata"), Mapping) else {}
    quote = yfinance.get("quote") if isinstance(yfinance.get("quote"), Mapping) else {}
    market_currency = _text(
        overview.get("currency") or info.get("currency") or fast_info.get("currency")
        or history_metadata.get("currency")
    ).upper()
    currency_compatible = not market_currency or market_currency == annual_currency
    current_price = next((value for value in (
        _finite(overview.get("currentPrice")), _finite(quote.get("price")),
        _finite(fast_info.get("lastPrice")), _finite(info.get("currentPrice")),
        _finite(info.get("regularMarketPrice")),
    ) if value is not None and value > 0), None)
    direct_market_cap = next((value for value in (
        _finite(overview.get("marketCapitalization")), _finite(fast_info.get("marketCap")),
        _finite(info.get("marketCap")),
    ) if value is not None and value > 0), None)
    shares = _finite(diluted_shares)
    market_cap = direct_market_cap
    market_cap_basis = "provider-reported" if direct_market_cap is not None else ""
    if market_cap is None and current_price is not None and shares is not None and shares > 0:
        market_cap = current_price * shares
        market_cap_basis = "derived-current-price-times-official-diluted-shares"
    provider_beta = next((value for value in (
        _finite(overview.get("beta")), _finite(info.get("beta")),
    ) if value is not None), None)
    beta_evidence = calculate_historical_market_beta(
        yfinance,
        benchmark_yfinance,
        benchmark_symbol=benchmark_symbol,
    ) if provider_beta is None else {
        "contractVersion": DRIVER_DCF_MARKET_EVIDENCE_VERSION,
        "status": "observed",
        "method": "provider-reported",
        "benchmarkSymbol": "",
        "sampleCount": 0,
        "beta": provider_beta,
        "blockingReasons": [],
    }
    beta = provider_beta if provider_beta is not None else _finite(beta_evidence.get("beta"))
    blockers = []
    if not currency_compatible:
        blockers.append("market-input-currency-mismatch")
        market_cap = None
    if market_cap is None:
        blockers.append("market-capitalization-missing")
    if beta is None:
        blockers.extend(beta_evidence.get("blockingReasons") or ["beta-missing"])
    material = {
        "contractVersion": DRIVER_DCF_MARKET_EVIDENCE_VERSION,
        "status": "validated" if not blockers else "blocked",
        "currency": market_currency,
        "annualCurrency": annual_currency,
        "currencyCompatible": currency_compatible,
        "currentPrice": current_price,
        "marketCapitalization": round(market_cap, 4) if market_cap is not None else None,
        "marketCapitalizationBasis": market_cap_basis,
        "beta": beta,
        "betaEvidence": beta_evidence,
        "blockingReasons": sorted(set(blockers)),
    }
    return {**material, "evidenceId": "driver-dcf-market-evidence:" + _digest(material)[:32]}


def _blocked(
    symbol: str,
    reasons,
    *,
    observed=None,
    references=None,
    financial_evidence=None,
    exposure_readiness=None,
) -> Dict[str, object]:
    material = {
        "contractVersion": DRIVER_DCF_INPUT_VERSION,
        "status": "blocked",
        "symbol": symbol,
        "missingInputs": sorted(set(reasons)),
        "observedInputs": dict(observed or {}),
        "sourceReferences": list(references or []),
        "financialEvidence": dict(financial_evidence or {}),
        "exposureReadiness": dict(exposure_readiness or {}),
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
    benchmark_yfinance_by_symbol: Mapping[str, object] = None,
    macro: Mapping[str, object] = None,
    lineage: Mapping[str, object] = None,
    exposure_readiness: Mapping[str, object] = None,
    valuation_at: object = "",
    equity_risk_premium_pct: float = 5.0,
    terminal_growth_pct: float = 2.5,
    equity_risk_premium_pct_by_currency: Mapping[str, object] = None,
    terminal_growth_pct_by_currency: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Return a shadow-only DCF bundle plus an explicit readiness contract."""

    normalized_symbol = _text(symbol).upper()
    overview = dict(overview or {})
    yfinance = dict(yfinance or {})
    macro = dict(macro or {})
    annual, annual_assessment = _latest_verified_annual(company or {})
    candidates = _annual_candidates(company or {})
    financial_evidence = _financial_evidence_contract(annual, annual_assessment, candidates)
    currency = _annual_currency(annual)
    benchmark_symbol = MARKET_BETA_BENCHMARK_BY_CURRENCY.get(currency, "")
    benchmark_yfinance_by_symbol = dict(benchmark_yfinance_by_symbol or {})
    benchmark_yfinance = (
        benchmark_yfinance_by_symbol.get(benchmark_symbol)
        if isinstance(benchmark_yfinance_by_symbol.get(benchmark_symbol), Mapping)
        else {}
    )
    market_equity_risk_premium_pct = _market_assumption(
        currency, equity_risk_premium_pct_by_currency, equity_risk_premium_pct,
    )
    market_terminal_growth_pct = _market_assumption(
        currency, terminal_growth_pct_by_currency, terminal_growth_pct,
    )
    risk_free_observation = _risk_free_observation(macro, currency)
    risk_free_dataset = _text(risk_free_observation.get("datasetId"))
    lineage_references = _source_references(
        lineage or {}, normalized_symbol, currency, price_subjects=[benchmark_symbol],
    )
    financial_references = list(financial_evidence.get("sourceReferences") or [])
    auxiliary_references = [
        item for item in lineage_references
        if item.get("datasetId") in {"yfinance.analyst", "yfinance.price", risk_free_dataset}
    ]
    references = {
        (_text(item.get("datasetId")), _text(item.get("revisionId"))): dict(item)
        for item in [*financial_references, *auxiliary_references]
        if _text(item.get("datasetId")) and _text(item.get("revisionId"))
    }
    references = [references[key] for key in sorted(references)]
    estimates = _revenue_estimates(yfinance)
    consensus_evidence = _consensus_evidence(
        estimates,
        annual_revenue=annual.get("revenue"),
        annual_currency=currency,
    )
    market_evidence = _market_evidence(
        overview,
        yfinance,
        benchmark_yfinance,
        benchmark_symbol=benchmark_symbol,
        annual_currency=currency,
        diluted_shares=annual.get("weightedAverageSharesDiluted"),
    )

    observed = {
        "annualPeriod": _text(annual_assessment.get("period") or annual.get("periodEnd") or annual.get("period")),
        "annualProvider": _text(annual.get("provider")),
        "currency": currency,
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
        "marketCapitalization": _finite(market_evidence.get("marketCapitalization")),
        "marketCapitalizationBasis": _text(market_evidence.get("marketCapitalizationBasis")),
        "marketEvidence": market_evidence,
        "beta": _finite(market_evidence.get("beta")),
        "riskFreeRatePct": risk_free_observation.get("value"),
        "riskFreeSeriesId": risk_free_observation.get("seriesId"),
        "riskFreeDatasetId": risk_free_dataset,
        "riskFreeLabel": risk_free_observation.get("label"),
        "riskFreeProvider": risk_free_observation.get("provider"),
        "riskFreeObservationDate": risk_free_observation.get("date"),
        "fy1RevenueConsensus": (estimates.get("0y") or {}).get("value"),
        "fy2RevenueConsensus": (estimates.get("+1y") or {}).get("value"),
        "consensusCurrency": _text((consensus_evidence.get("rows") or [{}])[0].get("currency")).upper(),
        "consensusEvidence": consensus_evidence,
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
    if observed["currency"] not in RISK_FREE_SERIES_BY_CURRENCY:
        reasons.append("valuation-currency-not-supported")
    if not any(item.get("datasetId") in {"yfinance.fundamental", *OFFICIAL_FINANCIAL_DATASETS} for item in references):
        reasons.append("financial-source-revision-missing")
    if not any(item.get("datasetId") == "yfinance.analyst" for item in references):
        reasons.append("analyst-source-revision-missing")
    if risk_free_dataset and not any(item.get("datasetId") == risk_free_dataset for item in references):
        reasons.append("macro-source-revision-missing")
    reasons.extend(consensus_evidence.get("blockingReasons") or [])
    reasons.extend(market_evidence.get("blockingReasons") or [])
    for key, reason in required.items():
        value = observed.get(key)
        if value is None or (key in {"revenue", "dilutedShares", "marketCapitalization", "riskFreeRatePct", "fy1RevenueConsensus", "fy2RevenueConsensus"} and value <= 0):
            reasons.append(reason)
    if reasons:
        return _blocked(normalized_symbol, reasons, observed=observed, references=references,
                        financial_evidence=financial_evidence, exposure_readiness=exposure_readiness)

    revenue = observed["revenue"]
    operating_margin_pct = observed["operatingIncome"] / revenue * 100.0
    if observed["pretaxIncome"] <= 0:
        return _blocked(normalized_symbol, ["non-positive-pretax-income"], observed=observed, references=references,
                        financial_evidence=financial_evidence, exposure_readiness=exposure_readiness)
    tax_rate_pct = observed["taxProvision"] / observed["pretaxIncome"] * 100.0
    if tax_rate_pct < 0 or tax_rate_pct > 100:
        return _blocked(normalized_symbol, ["effective-tax-rate-out-of-range"], observed=observed, references=references,
                        financial_evidence=financial_evidence, exposure_readiness=exposure_readiness)

    depreciation_ratio = observed["depreciationAmortization"] / revenue
    capex_ratio = abs(observed["capitalExpenditureReported"]) / revenue
    # Cash-flow statements report the cash contribution from working-capital
    # movements. FCFF subtracts the economic investment, which has the
    # opposite sign.
    nwc_investment_ratio = -observed["changeInWorkingCapitalCashFlow"] / revenue
    risk_free = observed["riskFreeRatePct"]
    beta = observed["beta"]
    cost_of_equity = risk_free + beta * market_equity_risk_premium_pct
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
        growth = fy2_growth_pct + (market_terminal_growth_pct - fy2_growth_pct) * step / fade_steps
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
        {"id": "fy1-revenue-consensus", "value": fy1, "unit": observed["currency"], "status": "observed", "reviewState": "not-required", "evidenceClass": "analyst-consensus", "materiality": "high"},
        {"id": "fy2-revenue-consensus", "value": fy2, "unit": observed["currency"], "status": "observed", "reviewState": "not-required", "evidenceClass": "analyst-consensus", "materiality": "high"},
        {"id": "risk-free-rate", "value": risk_free, "unit": "percent", "status": "observed", "reviewState": "not-required", "evidenceClass": "official-sovereign-yield", "materiality": "high", "seriesId": observed["riskFreeSeriesId"], "datasetId": observed["riskFreeDatasetId"], "observationDate": observed["riskFreeObservationDate"]},
        {"id": "equity-risk-premium", "value": market_equity_risk_premium_pct, "unit": "percent", "currency": observed["currency"], "status": "candidate", "reviewState": "pending", "evidenceClass": "policy-assumption", "materiality": "high"},
        {"id": "wacc", "value": round(wacc_pct, 8), "unit": "percent", "status": "candidate", "reviewState": "pending", "evidenceClass": "derived-with-policy-input", "materiality": "high"},
        {"id": "terminal-growth", "value": market_terminal_growth_pct, "unit": "percent", "currency": observed["currency"], "status": "candidate", "reviewState": "pending", "evidenceClass": "policy-assumption", "materiality": "high"},
        {"id": "years-3-to-5-growth-fade", "value": fy2_growth_pct, "unit": "percent-start", "status": "candidate", "reviewState": "pending", "evidenceClass": "forecast-policy", "materiality": "high"},
        {"id": "constant-operating-margin", "value": round(operating_margin_pct, 8), "unit": "percent", "status": "candidate", "reviewState": "pending", "evidenceClass": "forecast-policy", "materiality": "high"},
        {"id": "constant-reinvestment-ratios", "value": True, "unit": "policy", "status": "candidate", "reviewState": "pending", "evidenceClass": "forecast-policy", "materiality": "high"},
        {"id": "preferred-equity-zero", "value": 0, "unit": observed["currency"], "status": "candidate", "reviewState": "pending", "evidenceClass": "unverified-zero-balance", "materiality": "medium"},
        {"id": "non-controlling-interest-zero", "value": 0, "unit": observed["currency"], "status": "candidate", "reviewState": "pending", "evidenceClass": "unverified-zero-balance", "materiality": "medium"},
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
        "terminalGrowthPct": market_terminal_growth_pct,
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
        "financialEvidence": financial_evidence,
        "exposureReadiness": dict(exposure_readiness or {}),
        "assumptions": assumptions,
        "projectionYears": projection_years,
        "observedInputs": observed,
        "consensusEvidence": consensus_evidence,
        "marketEvidence": market_evidence,
        "calculationNotes": {
            "workingCapitalNormalization": "economic investment = -provider cash-flow change in working capital",
            "waccFormula": "E/(D+E)*costOfEquity + D/(D+E)*costOfDebt*(1-taxRate)",
            "costOfEquityFormula": "matching-currency 10Y sovereign yield + beta * candidate equity risk premium",
            "futureYears": "FY1/FY2 consensus followed by a versioned fade to terminal growth",
        },
    }
    approval_material = {
        "assumptionVersion": DRIVER_DCF_ASSUMPTION_VERSION,
        "symbol": normalized_symbol,
        "currency": observed["currency"],
        "modelApplicability": input_bundle["modelApplicability"],
        "sbcPolicy": input_bundle["sbcPolicy"],
        "maxTerminalValueSharePct": input_bundle["maxTerminalValueSharePct"],
        "reverseGrowthSearchBracketPct": input_bundle["reverseGrowthSearchBracketPct"],
        "projectionPolicy": [
            {
                "year": item.get("year"),
                "revenueBasis": item.get("revenueBasis"),
                "periodFraction": item.get("periodFraction"),
            }
            for item in projection_years
        ],
        "assumptionPolicy": [
            {
                "id": item.get("id"),
                "unit": item.get("unit"),
                "evidenceClass": item.get("evidenceClass"),
                "materiality": item.get("materiality"),
                **({"policyValue": item.get("value")} if item.get("id") in {
                    "equity-risk-premium", "terminal-growth", "constant-reinvestment-ratios",
                    "preferred-equity-zero", "non-controlling-interest-zero",
                } else {}),
            }
            for item in assumptions
            if item.get("reviewState") == "pending"
        ],
        "calculationPolicy": input_bundle["calculationNotes"],
    }
    approval_material_fingerprint = "driver-dcf-approval-material:" + _digest(approval_material)
    input_bundle["approvalMaterialFingerprint"] = approval_material_fingerprint
    input_bundle_id = "driver-dcf-input:" + _digest(input_bundle)[:32]
    pending_assumptions = [item for item in assumptions if item.get("reviewState") == "pending"]
    review_material = {
        "contractVersion": DRIVER_DCF_ASSUMPTION_REVIEW_VERSION,
        "subjectInputBundleId": input_bundle_id,
        "state": "required" if pending_assumptions else "complete",
        "approvalScope": "symbol-assumption-policy-version",
        "automaticApprovalAllowed": False,
        "assumptionVersion": DRIVER_DCF_ASSUMPTION_VERSION,
        "pendingCount": len(pending_assumptions),
        "requiredAssumptionIds": [item["id"] for item in pending_assumptions],
        "promotionBlockers": [
            *(["official-financial-evidence-incomplete"] if not financial_evidence.get("officialDecisionReady") else []),
            *(["assumptions-not-reviewed"] if pending_assumptions else []),
            "model-release-not-approved",
        ],
        "items": assumptions,
    }
    assumption_review = {
        **review_material,
        "reviewId": "driver-dcf-assumption-review:" + _digest(review_material)[:32],
    }
    input_bundle["assumptionReview"] = assumption_review
    input_bundle["inputBundleId"] = input_bundle_id
    material = {
        "contractVersion": DRIVER_DCF_INPUT_VERSION,
        "status": "ready-for-shadow",
        "symbol": normalized_symbol,
        "inputBundleId": input_bundle_id,
        "approvalMaterialFingerprint": approval_material_fingerprint,
        "missingInputs": [],
        "observedInputs": observed,
        "consensusEvidence": consensus_evidence,
        "marketEvidence": market_evidence,
        "sourceReferences": references,
        "financialEvidence": financial_evidence,
        "exposureReadiness": dict(exposure_readiness or {}),
        "assumptionReview": assumption_review,
        "assumptionReviewState": "required",
        "modelApprovalState": "shadow",
        "decisionEligible": False,
    }
    return {
        **material,
        "readinessId": "driver-dcf-readiness:" + _digest(material)[:32],
        "input": input_bundle,
    }


__all__ = [
    "DRIVER_DCF_ASSUMPTION_VERSION",
    "DRIVER_DCF_ASSUMPTION_REVIEW_VERSION",
    "DRIVER_DCF_FINANCIAL_EVIDENCE_VERSION",
    "DRIVER_DCF_CONSENSUS_EVIDENCE_VERSION",
    "DRIVER_DCF_INPUT_VERSION",
    "DRIVER_DCF_MARKET_EVIDENCE_VERSION",
    "MARKET_BETA_BENCHMARK_BY_CURRENCY",
    "build_driver_dcf_input_bundle",
    "calculate_historical_market_beta",
]
