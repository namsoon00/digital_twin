"""Canonical company facts for the ontology KnowledgeWorld.

Provider payloads remain durable in the external-signal MySQL cache.  This
module extracts a bounded, source-aware company view for the live ABox.  It
may calculate accounting ratios from reported facts, but it never classifies
an investment as attractive or risky; that remains a TypeDB RuleBox concern.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


COMPANY_KNOWLEDGE_VERSION = "company-knowledge-v1"
COMPANY_KNOWLEDGE_CACHE_VERSION = "company-knowledge-cache-v2"
COMPANY_VALUATION_CONTEXT_VERSION = "company-valuation-context-v1"

# Operational revision precision only. These values suppress a new company
# projection for an insignificant provider rounding change; TypeDB RuleBox
# still owns every investment threshold and receives the unrounded facts.
VALUATION_MATERIAL_REVISION_DIGITS = {
    "peRatio": 1,
    "forwardPE": 1,
    "pbr": 1,
    "pegRatio": 1,
    "bookValue": 2,
    "trailingEPS": 2,
    "returnOnEquity": 3,
    "returnOnAssets": 3,
    "returnOnEquityPct": 1,
    "returnOnAssetsPct": 1,
    "enterpriseToEbitda": 1,
    "dividendYield": 3,
    "dividendYieldPct": 1,
    "beta": 2,
}

COMPANY_VALUATION_RULE_ID_FRAGMENTS = (
    "quality_valuation",
    "valuation_stretch",
    "value_trap",
    "unsupported_rerating",
    "forward_expectation",
)

PROVIDER_PRIORITY = {
    "opendart": 100,
    "sec edgar": 100,
    "kis open api": 90,
    "alpha vantage": 70,
    "yfinance": 60,
}

STATEMENT_ALIASES = {
    "revenue": ("total revenue", "operating revenue", "revenue"),
    "grossProfit": ("gross profit",),
    "operatingIncome": ("operating income", "operating profit"),
    "netIncome": ("net income common stockholders", "net income", "net income loss"),
    "totalAssets": ("total assets", "assets"),
    "totalLiabilities": ("total liabilities net minority interest", "total liabilities", "liabilities"),
    "equity": ("stockholders equity", "total equity gross minority interest", "stockholders' equity", "equity"),
    "cash": ("cash cash equivalents and short term investments", "cash and cash equivalents", "cash"),
    "totalDebt": ("total debt",),
    "operatingCashFlow": ("operating cash flow", "cash flow from continuing operating activities"),
    "capitalExpenditure": ("capital expenditure", "capital expenditures"),
    "freeCashFlow": ("free cash flow",),
    "sharesOutstanding": ("ordinary shares number", "share issued", "shares issued"),
}

DART_ACCOUNT_ALIASES = {
    "revenue": ("매출액", "영업수익", "수익(매출액)"),
    "grossProfit": ("매출총이익",),
    "operatingIncome": ("영업이익", "영업이익(손실)"),
    "netIncome": ("당기순이익", "당기순이익(손실)"),
    "totalAssets": ("자산총계",),
    "totalLiabilities": ("부채총계",),
    "equity": ("자본총계",),
    "cash": ("현금및현금성자산",),
    "operatingCashFlow": ("영업활동으로인한현금흐름", "영업활동 현금흐름"),
}


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", "", _clean(value).lower())


def optional_number(value: object) -> Optional[float]:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if value in {"", "-", "--", "N/A", "nan", "None"}:
            return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return result


def _nonempty(value: object) -> bool:
    return value not in (None, "", [], {}) and not (isinstance(value, float) and value != value)


def provider_priority(value: object) -> int:
    text = _clean(value).lower()
    return next((score for name, score in PROVIDER_PRIORITY.items() if name in text), 0)


def merge_company_overview_rows(*rows: Mapping[str, object]) -> Dict[str, object]:
    """Merge fields independently so an empty provider row cannot mask data.

    The highest-priority non-empty provider owns a conflicting field.  This is
    especially important for Korean PER/PBR/EPS/BPS where the broker snapshot
    is more current than a partially populated global profile.
    """

    result: Dict[str, object] = {}
    owners: Dict[str, Tuple[int, str]] = {}
    providers: List[str] = []
    for source in rows:
        if not isinstance(source, Mapping):
            continue
        provider = _clean(source.get("provider") or source.get("source"))
        if provider and provider not in providers:
            providers.append(provider)
        priority = provider_priority(provider)
        for field, value in source.items():
            if field in {"fieldSources", "sourceProviders"} or not _nonempty(value):
                continue
            current_priority = owners.get(str(field), (-1, ""))[0]
            if field not in result or priority >= current_priority:
                result[str(field)] = value
                owners[str(field)] = (priority, provider)
    if providers:
        result["sourceProviders"] = providers
    result["fieldSources"] = {
        field: provider
        for field, (_priority, provider) in owners.items()
        if provider and field not in {"provider", "source"}
    }
    return result


def _period_sort_key(value: object) -> Tuple[int, str]:
    text = _clean(value)
    digits = re.sub(r"[^0-9]", "", text)
    # Interim reports often use ``start ~ end``.  The reporting boundary is
    # the final date, while a normal point-in-time period still has eight
    # digits and therefore follows the same path.
    return (int(digits[-8:] or 0), text)


def latest_source_as_of(values: Iterable[object]) -> str:
    """Return the newest provider timestamp across date-only and ISO values."""

    candidates = [_clean(value) for value in values if _clean(value)]
    if not candidates:
        return ""

    def rank(value: str) -> Tuple[float, str]:
        try:
            if re.fullmatch(r"\d{8}", value):
                parsed = datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)
            else:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
            return (parsed.timestamp(), value)
        except (TypeError, ValueError, OverflowError):
            return (0.0, value)

    return max(candidates, key=rank)


def _statement_metric_rows(rows: object) -> Dict[str, Mapping[str, object]]:
    result: Dict[str, Mapping[str, object]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        metric = _key(row.get("metric"))
        values = row.get("values") if isinstance(row.get("values"), Mapping) else {}
        if metric and values:
            result[metric] = values
    return result


def _metric_values(metrics: Mapping[str, Mapping[str, object]], aliases: Sequence[str]) -> Mapping[str, object]:
    alias_keys = [_key(alias) for alias in aliases]
    for alias in alias_keys:
        if alias in metrics:
            return metrics[alias]
    for metric, values in metrics.items():
        if any(alias and alias in metric for alias in alias_keys):
            return values
    return {}


def statement_periods(rows_by_statement: Mapping[str, object]) -> List[Dict[str, object]]:
    metric_sets = {
        statement: _statement_metric_rows(rows_by_statement.get(statement))
        for statement in ("incomeStatement", "balanceSheet", "cashFlow")
    }
    values_by_field: Dict[str, Mapping[str, object]] = {}
    for field, aliases in STATEMENT_ALIASES.items():
        statements = ("incomeStatement",) if field in {"revenue", "grossProfit", "operatingIncome", "netIncome"} else (
            ("cashFlow",) if field in {"operatingCashFlow", "capitalExpenditure", "freeCashFlow"} else ("balanceSheet",)
        )
        for statement in statements:
            values = _metric_values(metric_sets.get(statement, {}), aliases)
            if values:
                values_by_field[field] = values
                break
    periods = sorted(
        {str(period) for values in values_by_field.values() for period in values},
        key=_period_sort_key,
        reverse=True,
    )[:4]
    result = []
    for period in periods:
        facts = {
            field: optional_number(values.get(period))
            for field, values in values_by_field.items()
        }
        facts = {field: value for field, value in facts.items() if value is not None}
        if facts:
            result.append({"period": period, **facts})
    return result


def dart_statement_periods(rows: object) -> List[Dict[str, object]]:
    grouped: Dict[str, Dict[str, object]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        account = _key(row.get("account_nm") or row.get("accountName"))
        if not account:
            continue
        for field, aliases in DART_ACCOUNT_ALIASES.items():
            if account not in {_key(alias) for alias in aliases}:
                continue
            for period_field, amount_field in (
                ("thstrm_dt", "thstrm_amount"),
                ("frmtrm_dt", "frmtrm_amount"),
                ("bfefrmtrm_dt", "bfefrmtrm_amount"),
            ):
                period = _clean(row.get(period_field))
                value = optional_number(row.get(amount_field))
                if period and value is not None:
                    grouped.setdefault(period, {"period": period, "provider": "OpenDART"})[field] = value
    return [
        grouped[period]
        for period in sorted(grouped, key=_period_sort_key, reverse=True)[:4]
        if len(grouped[period]) > 1
    ]


def _safe_ratio(numerator: object, denominator: object, scale: float = 1.0) -> Optional[float]:
    left = optional_number(numerator)
    right = optional_number(denominator)
    if left is None or right in (None, 0):
        return None
    return round(left / right * scale, 4)


def _growth(current: object, previous: object) -> Optional[float]:
    return _safe_ratio((optional_number(current) or 0) - (optional_number(previous) or 0), abs(optional_number(previous) or 0), 100.0) if optional_number(previous) not in (None, 0) else None


def _ratio_percent(value: object) -> Optional[float]:
    numeric = optional_number(value)
    if numeric is None:
        return None
    # yfinance and Alpha Vantage use decimal ratios while broker datasets may
    # already expose percentages. Keep the canonical company contract explicit.
    return round(numeric * 100.0 if abs(numeric) <= 2.0 else numeric, 4)


def _dividend_yield_contract(
    overview: Mapping[str, object],
    info: Mapping[str, object],
) -> Tuple[Optional[float], Optional[float], str]:
    raw = optional_number(_overview_value(overview, info, "dividendYield"))
    if raw is None:
        return None, None, ""
    provider = _clean(overview.get("provider")).lower()
    source_unit = _clean(overview.get("dividendYieldUnit")).lower()
    if source_unit not in {"ratio", "percent"}:
        source_unit = "percent" if provider == "yfinance" else "ratio"
    ratio = raw / 100.0 if source_unit == "percent" else raw
    return round(ratio, 8), round(ratio * 100.0, 4), source_unit


def _normalize_company_knowledge_row(row: Mapping[str, object]) -> Dict[str, object]:
    """Upgrade cached company rows to the canonical valuation-unit contract."""

    result = dict(row or {})
    valuation = dict(result.get("valuation") or {}) if isinstance(result.get("valuation"), Mapping) else {}
    units = dict(result.get("valuationUnits") or {}) if isinstance(result.get("valuationUnits"), Mapping) else {}
    if valuation and not units.get("dividendYield"):
        overview_providers = {
            _clean(item.get("provider")).lower()
            for item in result.get("provenance", [])
            if isinstance(item, Mapping) and _clean(item.get("scope")).lower() == "overview"
        }
        if overview_providers == {"yfinance"}:
            raw = optional_number(valuation.get("dividendYield"))
            if raw is not None:
                valuation["dividendYield"] = round(raw / 100.0, 8)
                valuation["dividendYieldPct"] = round(raw, 4)
                units = {
                    **units,
                    "dividendYield": "ratio",
                    "dividendYieldPct": "percent",
                    "dividendYieldSourceUnit": "percent",
                }
    result["valuation"] = valuation
    if units:
        result["valuationUnits"] = units
    return result


def enrich_financial_periods(periods: List[Dict[str, object]], info: Mapping[str, object] = None) -> List[Dict[str, object]]:
    result = [dict(row) for row in periods if isinstance(row, Mapping)]
    info = dict(info or {}) if isinstance(info, Mapping) else {}
    if not result and any(_nonempty(info.get(key)) for key in ("totalRevenue", "netIncomeToCommon", "totalAssets")):
        result.append({
            "period": _clean(info.get("mostRecentQuarter") or "latest-provider-snapshot"),
            "revenue": optional_number(info.get("totalRevenue")),
            "grossProfit": optional_number(info.get("grossProfits")),
            "operatingIncome": optional_number(info.get("operatingIncome")),
            "netIncome": optional_number(info.get("netIncomeToCommon")),
            "cash": optional_number(info.get("totalCash")),
            "totalDebt": optional_number(info.get("totalDebt")),
            "operatingCashFlow": optional_number(info.get("operatingCashflow")),
            "freeCashFlow": optional_number(info.get("freeCashflow")),
            "sharesOutstanding": optional_number(info.get("sharesOutstanding")),
        })
    for index, row in enumerate(result):
        if row.get("freeCashFlow") is None and row.get("operatingCashFlow") is not None and row.get("capitalExpenditure") is not None:
            capex = optional_number(row.get("capitalExpenditure")) or 0.0
            row["freeCashFlow"] = round((optional_number(row.get("operatingCashFlow")) or 0.0) + capex, 4) if capex < 0 else round((optional_number(row.get("operatingCashFlow")) or 0.0) - capex, 4)
        row["grossMarginPct"] = _safe_ratio(row.get("grossProfit"), row.get("revenue"), 100.0)
        row["operatingMarginPct"] = _safe_ratio(row.get("operatingIncome"), row.get("revenue"), 100.0)
        row["netMarginPct"] = _safe_ratio(row.get("netIncome"), row.get("revenue"), 100.0)
        row["cashConversionPct"] = _safe_ratio(row.get("operatingCashFlow"), row.get("netIncome"), 100.0)
        row["freeCashFlowMarginPct"] = _safe_ratio(row.get("freeCashFlow"), row.get("revenue"), 100.0)
        row["debtToEquityPct"] = _safe_ratio(row.get("totalDebt"), row.get("equity"), 100.0)
        row["liabilitiesToAssetsPct"] = _safe_ratio(row.get("totalLiabilities"), row.get("totalAssets"), 100.0)
        if index + 1 < len(result):
            previous = result[index + 1]
            row["revenueGrowthPct"] = _growth(row.get("revenue"), previous.get("revenue"))
            row["operatingIncomeGrowthPct"] = _growth(row.get("operatingIncome"), previous.get("operatingIncome"))
            row["netIncomeGrowthPct"] = _growth(row.get("netIncome"), previous.get("netIncome"))
            row["freeCashFlowGrowthPct"] = _growth(row.get("freeCashFlow"), previous.get("freeCashFlow"))
            row["sharesOutstandingGrowthPct"] = _growth(row.get("sharesOutstanding"), previous.get("sharesOutstanding"))
        for key in list(row):
            if row.get(key) is None:
                row.pop(key, None)
    return result


def _sec_fact_periods(facts: Mapping[str, object]) -> List[Dict[str, object]]:
    rows: Dict[str, Dict[str, object]] = {}
    for field, value in dict(facts or {}).items():
        if field == "entityName" or not isinstance(value, Mapping):
            continue
        period = _clean(value.get("end") or value.get("filed"))
        amount = optional_number(value.get("value"))
        if period and amount is not None:
            rows.setdefault(period, {"period": period, "provider": "SEC EDGAR"})[str(field)] = amount
    return [rows[key] for key in sorted(rows, key=_period_sort_key, reverse=True)[:4]]


def _compact_executives(yfinance: Mapping[str, object], dart: Mapping[str, object]) -> List[Dict[str, object]]:
    result: List[Dict[str, object]] = []
    seen = set()
    info = yfinance.get("info") if isinstance(yfinance.get("info"), Mapping) else {}
    candidates: List[Tuple[Mapping[str, object], str]] = []
    for row in info.get("companyOfficers") if isinstance(info.get("companyOfficers"), list) else []:
        if isinstance(row, Mapping):
            candidates.append((row, "yfinance"))
    for row in dart.get("executives") if isinstance(dart.get("executives"), list) else []:
        if isinstance(row, Mapping):
            candidates.append((row, "OpenDART"))
    for row, provider in candidates:
        name = _clean(row.get("name") or row.get("nm"))
        title = _clean(row.get("title") or row.get("ofcps") or row.get("position"))
        if not name:
            continue
        key = (_key(name), _key(title))
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "name": name[:120],
            "title": title[:160],
            "role": _clean(row.get("role") or row.get("chrg_job") or row.get("responsibility"))[:200],
            "registeredExecutive": _clean(row.get("rgist_exctv_at"))[:20],
            "tenureEnd": _clean(row.get("tenure_end_on"))[:40],
            "provider": provider,
        })
        if len(result) >= 16:
            break
    return result


def _overview_value(overview: Mapping[str, object], info: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if _nonempty(overview.get(key)):
            return overview.get(key)
        if _nonempty(info.get(key)):
            return info.get(key)
    return None


def build_company_knowledge(
    symbol: object,
    *,
    overview: Mapping[str, object] = None,
    yfinance: Mapping[str, object] = None,
    sec_filing: Mapping[str, object] = None,
    dart_disclosure: Mapping[str, object] = None,
) -> Dict[str, object]:
    symbol = _clean(symbol).upper()
    overview = dict(overview or {}) if isinstance(overview, Mapping) else {}
    yfinance = dict(yfinance or {}) if isinstance(yfinance, Mapping) else {}
    sec_filing = dict(sec_filing or {}) if isinstance(sec_filing, Mapping) else {}
    dart_disclosure = dict(dart_disclosure or {}) if isinstance(dart_disclosure, Mapping) else {}
    info = yfinance.get("info") if isinstance(yfinance.get("info"), Mapping) else {}

    annual = statement_periods(yfinance)
    official_periods = dart_statement_periods(dart_disclosure.get("financialStatements"))
    dart_basis = dart_disclosure.get("financialStatementBasis") if isinstance(dart_disclosure.get("financialStatementBasis"), Mapping) else {}
    report_code = _clean(dart_basis.get("reportCode"))
    interim = official_periods if official_periods and report_code not in {"", "11011"} else []
    if official_periods and not interim:
        annual = official_periods
    elif sec_filing.get("facts"):
        sec_periods = _sec_fact_periods(sec_filing.get("facts") or {})
        if sec_periods:
            annual = sec_periods
    annual = enrich_financial_periods(annual, info)
    quarterly = enrich_financial_periods(statement_periods({
        "incomeStatement": yfinance.get("quarterlyIncomeStatement"),
        "balanceSheet": yfinance.get("quarterlyBalanceSheet"),
        "cashFlow": yfinance.get("quarterlyCashFlow"),
    }), info)
    interim = enrich_financial_periods(interim)
    executives = _compact_executives(yfinance, dart_disclosure)
    company = dart_disclosure.get("company") if isinstance(dart_disclosure.get("company"), Mapping) else {}
    company_name = _clean(
        company.get("corp_name")
        or company.get("corpName")
        or overview.get("name")
        or info.get("longName")
        or sec_filing.get("companyName")
        or symbol
    )
    latest_candidates = [
        rows[0]
        for rows in (annual, interim, quarterly)
        if rows and isinstance(rows[0], Mapping)
    ]
    latest = dict(max(
        latest_candidates,
        key=lambda row: _period_sort_key(row.get("period")),
    )) if latest_candidates else {}
    officer_rows = info.get("companyOfficers") if isinstance(info.get("companyOfficers"), list) else []
    first_officer = officer_rows[0] if officer_rows and isinstance(officer_rows[0], Mapping) else {}
    profile = {
        "companyName": company_name,
        "ceoName": _clean(company.get("ceo_nm") or overview.get("ceoName") or first_officer.get("name")),
        "sector": _clean(_overview_value(overview, info, "sector")),
        "industry": _clean(_overview_value(overview, info, "industry")),
        "website": _clean(company.get("hm_url") or info.get("website")),
        "establishedDate": _clean(company.get("est_dt")),
        "fiscalYearEndMonth": _clean(company.get("acc_mt")),
        "marketCapitalization": optional_number(_overview_value(overview, info, "marketCapitalization", "marketCap")),
    }
    dividend_yield, dividend_yield_pct, dividend_source_unit = _dividend_yield_contract(overview, info)
    valuation = {
        "peRatio": optional_number(_overview_value(overview, info, "peRatio", "trailingPE")),
        "forwardPE": optional_number(_overview_value(overview, info, "forwardPE")),
        "pbr": optional_number(_overview_value(overview, info, "pbr", "priceToBook")),
        "pegRatio": optional_number(_overview_value(overview, info, "pegRatio", "trailingPegRatio")),
        "bookValue": optional_number(_overview_value(overview, info, "bookValue", "bps")),
        "trailingEPS": optional_number(_overview_value(overview, info, "trailingEPS", "epsTrailingTwelveMonths", "trailingEps")),
        "returnOnEquity": optional_number(_overview_value(overview, info, "returnOnEquity")),
        "returnOnAssets": optional_number(_overview_value(overview, info, "returnOnAssets")),
        "returnOnEquityPct": _ratio_percent(_overview_value(overview, info, "returnOnEquity")),
        "returnOnAssetsPct": _ratio_percent(_overview_value(overview, info, "returnOnAssets")),
        "enterpriseToEbitda": optional_number(_overview_value(overview, info, "enterpriseToEbitda")),
        "dividendYield": dividend_yield,
        "dividendYieldPct": dividend_yield_pct,
        "beta": optional_number(_overview_value(overview, info, "beta")),
    }
    ownership = {
        "institutionalOwnershipPct": (_safe_ratio(info.get("heldPercentInstitutions"), 1, 100.0) if optional_number(info.get("heldPercentInstitutions")) is not None else None),
        "insiderOwnershipPct": (_safe_ratio(info.get("heldPercentInsiders"), 1, 100.0) if optional_number(info.get("heldPercentInsiders")) is not None else None),
    }
    capital = {
        "sharesOutstanding": optional_number(latest.get("sharesOutstanding") or info.get("sharesOutstanding")),
        "floatShares": optional_number(info.get("floatShares")),
        "sharesShort": optional_number(info.get("sharesShort")),
        "totalDebt": optional_number(latest.get("totalDebt") or info.get("totalDebt")),
        "cash": optional_number(latest.get("cash") or info.get("totalCash")),
    }
    sources = []
    for provider, as_of, scope in (
        (overview.get("provider"), overview.get("fetchedAt"), "overview"),
        (
            (yfinance.get("provider") or "yfinance") if yfinance else "",
            yfinance.get("collectedAt"),
            "statements-governance",
        ),
        (sec_filing.get("provider"), (sec_filing.get("latestFiling") or {}).get("filingDate") if isinstance(sec_filing.get("latestFiling"), Mapping) else "", "official-filing"),
        (dart_disclosure.get("provider"), dart_disclosure.get("receiptDate"), "official-filing-company"),
    ):
        if _clean(provider):
            sources.append({"provider": _clean(provider), "asOf": _clean(as_of), "scope": scope})
    coverage_fields = {
        "financialPeriods": len(annual) + len(interim) + len(quarterly),
        "executives": len(executives),
        "valuationFields": len([value for value in valuation.values() if value is not None]),
        "capitalFields": len([value for value in capital.values() if value is not None]),
        "officialSource": any(provider_priority(item.get("provider")) >= 100 for item in sources),
    }
    official_coverage = {
        "profile": bool(company),
        "financials": bool(official_periods or sec_filing.get("facts")),
        "governance": any(
            provider_priority(item.get("provider")) >= 100
            for item in executives
            if isinstance(item, Mapping)
        ),
        "capital": provider_priority(latest.get("provider")) >= 100,
        # Market multiples still come from market-data vendors even when an
        # official filing is present elsewhere in the same company packet.
        "valuation": False,
        "filings": bool(sec_filing.get("latestFiling") or dart_disclosure.get("receiptNo")),
    }
    missing = []
    if not annual and not interim and not quarterly:
        missing.append("financial-statements")
    if not executives and not profile.get("ceoName"):
        missing.append("executive-governance")
    if coverage_fields["valuationFields"] < 2:
        missing.append("valuation-metrics")
    if coverage_fields["capitalFields"] < 2:
        missing.append("capital-structure")
    data_state = "sufficient" if coverage_fields["financialPeriods"] >= 2 and coverage_fields["valuationFields"] >= 2 else ("partial" if sources else "unavailable")
    payload = {
        "schemaVersion": COMPANY_KNOWLEDGE_VERSION,
        "symbol": symbol,
        "companyName": company_name,
        "profile": {key: value for key, value in profile.items() if _nonempty(value)},
        "valuation": {key: value for key, value in valuation.items() if value is not None},
        "financials": {"annual": annual, "interim": interim, "quarterly": quarterly},
        "governance": {"executives": executives, "executiveCount": len(executives)},
        "ownership": {key: value for key, value in ownership.items() if value is not None},
        "capital": {key: value for key, value in capital.items() if value is not None},
        "valuationUnits": {
            "dividendYield": "ratio",
            "dividendYieldPct": "percent",
            "dividendYieldSourceUnit": dividend_source_unit,
        } if dividend_yield is not None else {},
        "provenance": sources,
        "coverage": {
            **coverage_fields,
            "officialCoverage": official_coverage,
            "dataState": data_state,
            "missing": missing,
        },
    }
    revision_payload = _revision_payload(payload)
    revision_source = json.dumps(revision_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["factRevision"] = hashlib.sha256(revision_source.encode("utf-8")).hexdigest()[:20]
    payload["materialRevision"] = _material_revision(payload)
    return payload if sources or annual or quarterly else {}


def _revision_payload(payload: Mapping[str, object]) -> Dict[str, object]:
    result = dict(payload or {})
    result.pop("factRevision", None)
    result.pop("materialRevision", None)
    result["provenance"] = [
        {"provider": item.get("provider"), "scope": item.get("scope")}
        for item in result.get("provenance", [])
        if isinstance(item, Mapping)
    ]
    return result


def _material_revision(payload: Mapping[str, object]) -> str:
    result = _revision_payload(payload)
    profile = dict(result.get("profile") or {}) if isinstance(result.get("profile"), Mapping) else {}
    # Market capitalization follows the quote and is already represented by
    # MarketWorld. It must not create a second company-fact reasoning turn.
    profile.pop("marketCapitalization", None)
    result["profile"] = profile
    valuation = result.get("valuation") if isinstance(result.get("valuation"), Mapping) else {}
    result["valuation"] = {
        field: (
            round(optional_number(value), VALUATION_MATERIAL_REVISION_DIGITS[field])
            if field in VALUATION_MATERIAL_REVISION_DIGITS and optional_number(value) is not None
            else value
        )
        for field, value in valuation.items()
    }
    source = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]


def merge_company_knowledge_rows(*rows: Mapping[str, object]) -> Dict[str, object]:
    """Merge account-independent company facts without losing fresh ratios."""

    valid = [_normalize_company_knowledge_row(row) for row in rows if isinstance(row, Mapping) and row]
    if not valid:
        return {}
    result: Dict[str, object] = {
        "schemaVersion": COMPANY_KNOWLEDGE_VERSION,
        "symbol": _clean(next((row.get("symbol") for row in reversed(valid) if row.get("symbol")), "")).upper(),
    }
    for row in valid:
        if _nonempty(row.get("companyName")):
            result["companyName"] = row.get("companyName")
        for section in ("profile", "valuation", "ownership", "capital"):
            incoming = row.get(section) if isinstance(row.get(section), Mapping) else {}
            current = result.get(section) if isinstance(result.get(section), Mapping) else {}
            result[section] = {
                **current,
                **{key: value for key, value in incoming.items() if _nonempty(value)},
            }
        incoming_units = row.get("valuationUnits") if isinstance(row.get("valuationUnits"), Mapping) else {}
        if incoming_units:
            result["valuationUnits"] = {
                **(result.get("valuationUnits") or {}),
                **incoming_units,
            }
        incoming_financials = row.get("financials") if isinstance(row.get("financials"), Mapping) else {}
        financials = result.get("financials") if isinstance(result.get("financials"), Mapping) else {}
        for frequency in ("annual", "interim", "quarterly"):
            incoming_periods = incoming_financials.get(frequency) if isinstance(incoming_financials.get(frequency), list) else []
            current_periods = financials.get(frequency) if isinstance(financials.get(frequency), list) else []
            if len(incoming_periods) >= len(current_periods):
                financials[frequency] = [dict(item) for item in incoming_periods if isinstance(item, Mapping)]
        result["financials"] = financials
        incoming_governance = row.get("governance") if isinstance(row.get("governance"), Mapping) else {}
        incoming_executives = incoming_governance.get("executives") if isinstance(incoming_governance.get("executives"), list) else []
        governance = result.get("governance") if isinstance(result.get("governance"), Mapping) else {}
        current_executives = governance.get("executives") if isinstance(governance.get("executives"), list) else []
        if len(incoming_executives) >= len(current_executives):
            governance = {
                **governance,
                **incoming_governance,
                "executives": [dict(item) for item in incoming_executives if isinstance(item, Mapping)],
            }
        result["governance"] = governance

    provenance_by_key = {}
    for row in valid:
        for item in row.get("provenance", []) if isinstance(row.get("provenance"), list) else []:
            if not isinstance(item, Mapping):
                continue
            key = (_clean(item.get("provider")).lower(), _clean(item.get("scope")).lower())
            if not key[0]:
                continue
            previous = provenance_by_key.get(key) or {}
            if _clean(item.get("asOf")) >= _clean(previous.get("asOf")):
                provenance_by_key[key] = dict(item)
    result["provenance"] = list(provenance_by_key.values())

    financials = result.get("financials") if isinstance(result.get("financials"), Mapping) else {}
    executives = (result.get("governance") or {}).get("executives") if isinstance(result.get("governance"), Mapping) else []
    valuation = result.get("valuation") if isinstance(result.get("valuation"), Mapping) else {}
    capital = result.get("capital") if isinstance(result.get("capital"), Mapping) else {}
    period_count = sum(len(financials.get(frequency) or []) for frequency in ("annual", "interim", "quarterly"))
    missing = []
    if not period_count:
        missing.append("financial-statements")
    if not executives and not (result.get("profile") or {}).get("ceoName"):
        missing.append("executive-governance")
    if len(valuation) < 2:
        missing.append("valuation-metrics")
    if len(capital) < 2:
        missing.append("capital-structure")
    result["coverage"] = {
        "financialPeriods": period_count,
        "executives": len(executives or []),
        "valuationFields": len(valuation),
        "capitalFields": len(capital),
        "officialSource": any(provider_priority(item.get("provider")) >= 100 for item in result["provenance"]),
        "dataState": "sufficient" if period_count >= 2 and len(valuation) >= 2 else ("partial" if result["provenance"] else "unavailable"),
        "missing": missing,
    }
    revision_source = json.dumps(_revision_payload(result), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result["factRevision"] = hashlib.sha256(revision_source.encode("utf-8")).hexdigest()[:20]
    result["materialRevision"] = _material_revision(result)
    return result


def company_knowledge_by_symbol(
    external_signals: Mapping[str, object],
    symbols: Iterable[object],
) -> Dict[str, Dict[str, object]]:
    source = dict(external_signals or {}) if isinstance(external_signals, Mapping) else {}
    result = {}
    for raw_symbol in symbols or []:
        symbol = _clean(raw_symbol).upper()
        if not symbol:
            continue
        payload = build_company_knowledge(
            symbol,
            overview=(source.get("companyOverviews") or {}).get(symbol, {}) if isinstance(source.get("companyOverviews"), Mapping) else {},
            yfinance=(source.get("yfinanceData") or {}).get(symbol, {}) if isinstance(source.get("yfinanceData"), Mapping) else {},
            sec_filing=(source.get("secFilings") or {}).get(symbol, {}) if isinstance(source.get("secFilings"), Mapping) else {},
            dart_disclosure=(source.get("dartDisclosures") or {}).get(symbol, {}) if isinstance(source.get("dartDisclosures"), Mapping) else {},
        )
        if payload:
            result[symbol] = payload
    return result


def company_prompt_context(
    external_signals: Mapping[str, object],
    symbol: object,
) -> Dict[str, object]:
    """Return the bounded company facts that may accompany an AI decision.

    The complete provider payload remains in MySQL and the bounded live ABox.
    A notification only needs the latest comparable period for each reporting
    frequency and a small governance summary. These are facts, not a Python
    investment classification; the AI prompt requires an active TypeDB company
    rule before using them as an action driver.
    """

    normalized_symbol = _clean(symbol).upper()
    groups = external_signals.get("companyKnowledge") if isinstance(external_signals, Mapping) else {}
    payload = groups.get(normalized_symbol) if isinstance(groups, Mapping) else {}
    if not isinstance(payload, Mapping) or not payload:
        return {}
    payload = _normalize_company_knowledge_row(payload)

    def section(name: str, fields: Sequence[str]) -> Dict[str, object]:
        source = payload.get(name) if isinstance(payload.get(name), Mapping) else {}
        return {
            field: source.get(field)
            for field in fields
            if _nonempty(source.get(field))
        }

    financials = payload.get("financials") if isinstance(payload.get("financials"), Mapping) else {}
    latest_financials: Dict[str, List[Dict[str, object]]] = {}
    financial_fields = (
        "period",
        "revenue",
        "revenueGrowthPct",
        "grossProfit",
        "operatingIncome",
        "operatingIncomeGrowthPct",
        "operatingMarginPct",
        "netIncome",
        "netIncomeGrowthPct",
        "netMarginPct",
        "totalAssets",
        "totalLiabilities",
        "equity",
        "debtToEquityPct",
        "cash",
        "totalDebt",
        "operatingCashFlow",
        "capitalExpenditure",
        "freeCashFlow",
        "sharesOutstanding",
        "sharesGrowthPct",
    )
    for frequency in ("annual", "interim", "quarterly"):
        rows = financials.get(frequency) if isinstance(financials.get(frequency), list) else []
        latest = next((row for row in rows if isinstance(row, Mapping)), None)
        if latest:
            latest_financials[frequency] = [{
                field: latest.get(field)
                for field in financial_fields
                if _nonempty(latest.get(field))
            }]

    governance = payload.get("governance") if isinstance(payload.get("governance"), Mapping) else {}
    executives = []
    for item in governance.get("executives", []) if isinstance(governance.get("executives"), list) else []:
        if not isinstance(item, Mapping):
            continue
        row = {
            field: item.get(field)
            for field in ("name", "title", "role", "age", "yearBorn", "since", "pay")
            if _nonempty(item.get(field))
        }
        if row:
            executives.append(row)
        if len(executives) >= 5:
            break

    provenance = [
        {
            field: item.get(field)
            for field in ("provider", "scope", "asOf")
            if _nonempty(item.get(field))
        }
        for item in payload.get("provenance", [])[:6]
        if isinstance(item, Mapping)
    ] if isinstance(payload.get("provenance"), list) else []

    result = {
        "schemaVersion": payload.get("schemaVersion") or COMPANY_KNOWLEDGE_VERSION,
        "symbol": normalized_symbol,
        "companyName": payload.get("companyName") or normalized_symbol,
        "factRevision": payload.get("factRevision"),
        "materialRevision": payload.get("materialRevision"),
        "judgmentUse": "active-company-rule-only",
        "profile": section("profile", (
            "companyName", "ceoName", "sector", "industry", "establishedDate",
            "fiscalYearEndMonth", "marketCapitalization",
        )),
        "valuation": section("valuation", (
            "peRatio", "forwardPE", "pbr", "pegRatio", "bookValue", "trailingEPS",
            "returnOnEquity", "returnOnEquityPct", "returnOnAssets", "returnOnAssetsPct",
            "enterpriseToEbitda", "dividendYield", "dividendYieldPct", "beta",
        )),
        "latestFinancials": latest_financials,
        "governance": {
            "executiveCount": governance.get("executiveCount"),
            "executives": executives,
        },
        "ownership": section("ownership", ("institutionalOwnershipPct", "insiderOwnershipPct")),
        "capital": section("capital", ("sharesOutstanding", "floatShares", "sharesShort", "totalDebt", "cash")),
        "coverage": section("coverage", (
            "financialPeriods", "executives", "valuationFields", "capitalFields",
            "officialSource", "dataState", "missing",
        )),
        "provenance": provenance,
    }
    return {
        key: value
        for key, value in result.items()
        if _nonempty(value)
    }


def company_valuation_context(
    external_signals: Mapping[str, object],
    symbol: object,
    *,
    price_as_of: object = "",
    currency: object = "",
) -> Dict[str, object]:
    """Build a bounded display and AI contract from canonical company facts."""

    company = company_prompt_context(external_signals, symbol)
    valuation = company.get("valuation") if isinstance(company.get("valuation"), Mapping) else {}
    if not valuation:
        return {}
    latest_financials = company.get("latestFinancials") if isinstance(company.get("latestFinancials"), Mapping) else {}
    period_candidates = []
    for frequency in ("annual", "interim", "quarterly"):
        rows = latest_financials.get(frequency) if isinstance(latest_financials.get(frequency), list) else []
        row = next((item for item in rows if isinstance(item, Mapping)), None)
        if row and _clean(row.get("period")):
            period_candidates.append((_period_sort_key(row.get("period")), frequency, _clean(row.get("period"))))
    _rank, reporting_frequency, reporting_period = max(period_candidates, default=((0, ""), "", ""), key=lambda item: item[0])

    provenance = company.get("provenance") if isinstance(company.get("provenance"), list) else []
    providers = list(dict.fromkeys(
        _clean(item.get("provider"))
        for item in provenance
        if isinstance(item, Mapping) and _clean(item.get("provider"))
    ))
    providers.sort(key=provider_priority, reverse=True)
    providers = providers[:6]
    source_as_of_values = [
        _clean(item.get("asOf"))
        for item in provenance
        if isinstance(item, Mapping) and _clean(item.get("asOf"))
    ]
    coverage = company.get("coverage") if isinstance(company.get("coverage"), Mapping) else {}
    trailing_eps = optional_number(valuation.get("trailingEPS"))
    pe_ratio = optional_number(valuation.get("peRatio"))
    per_status = (
        "not-meaningful-loss"
        if trailing_eps is not None and trailing_eps < 0
        else "not-meaningful-zero-earnings" if trailing_eps == 0
        else "available" if pe_ratio is not None and pe_ratio > 0
        else "missing"
    )
    return {
        "schemaVersion": COMPANY_VALUATION_CONTEXT_VERSION,
        "symbol": _clean(symbol).upper(),
        "companyName": company.get("companyName"),
        "companyFactRevision": company.get("factRevision"),
        "companyMaterialRevision": company.get("materialRevision"),
        "decisionRole": "reference",
        "metrics": dict(valuation),
        "metricCount": len(valuation),
        "currency": _clean(currency),
        "reportingBasis": {
            "period": reporting_period,
            "frequency": reporting_frequency,
        },
        "priceAsOf": _clean(price_as_of),
        "sourceAsOf": latest_source_as_of(source_as_of_values),
        "sourceProviders": providers,
        "dataState": coverage.get("dataState") or "partial",
        "officialSource": bool(coverage.get("officialSource")),
        "missing": list(coverage.get("missing") or [])[:8],
        "perStatus": per_status,
        "judgmentUse": "active-company-valuation-rule-only",
    }


def active_company_valuation_rule_ids(rows: object) -> List[str]:
    result = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, Mapping):
            continue
        rule_id = _clean(item.get("ruleId") or item.get("rule_id"))
        action_group = _clean(item.get("actionGroup") or item.get("action_group")).lower()
        if not rule_id.startswith("graph.company."):
            continue
        if action_group == "valuation" or any(fragment in rule_id for fragment in COMPANY_VALUATION_RULE_ID_FRAGMENTS):
            result.append(rule_id)
    return list(dict.fromkeys(result))
