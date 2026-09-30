"""Bounded official report history and exact baselines for later report checks."""

from .financial_reporting import financial_report_contract_assessment, _finite
from digital_twin.modules.market_data.contracts import parse_datetime


ANNUAL_HISTORY_VERSION = "annual-financial-history-v1"
HISTORY_METRICS = (
    "revenue", "operatingIncome", "netIncome", "operatingCashFlow",
    "capitalExpenditure", "freeCashFlow", "pretaxIncome", "taxProvision",
    "stockBasedCompensation", "changeInWorkingCapital",
)
REPORT_WATCH_FIELDS = {
    "financial.annual." + field: field
    for field in ("revenue", "netIncome", "operatingCashFlow", "freeCashFlow")
}


def annual_financial_history(company, symbol=""):
    financials = company.get("financials") or company.get("latestFinancials") or {}
    symbol = str(symbol or company.get("symbol") or "").upper()
    result = []
    for row in sorted(financials.get("annual") or [], key=lambda r: str(r.get("periodEnd") or r.get("period") or ""), reverse=True):
        if not financial_report_contract_assessment(row, "annual").get("eligible") or not row.get("officialSource"):
            continue
        contract = row.get("reportContract") or {}
        refs = contract.get("sourceReferences") or []
        if not symbol or not refs or any(str(ref.get("subjectKey") or "").upper() != symbol for ref in refs):
            continue
        clocks = [parse_datetime(ref.get("fetchedAt")) for ref in refs]
        if not all(clocks):
            continue
        values, bases = {}, {}
        for field in HISTORY_METRICS:
            value = _finite(row.get(field))
            source = (row.get("metricProvenance") or {}).get(field) or {}
            basis = {key: source.get(key) for key in ("provider", "currency", "scope", "durationBasis")}
            if value is None or not all(basis.values()) or basis["durationBasis"] != "annual" or not source.get("official"):
                continue
            if str(source.get("period") or "") != contract.get("periodEnd"):
                continue
            values[field] = value
            bases[field] = {**basis, "sourceUrl": source.get("sourceUrl") or "", "derived": bool(source.get("derived"))}
            if field == "changeInWorkingCapital":
                bases[field]["componentCoverage"] = "selected-reported-components-not-complete-cash-flow-bridge"
        if values:
            result.append({
                "version": ANNUAL_HISTORY_VERSION, "symbol": symbol,
                "periodEnd": contract["periodEnd"], "periodStart": contract.get("periodStart") or "",
                "reportObservationId": contract["observationId"],
                "publishedAt": contract.get("publishedAt") or "",
                "observedAt": max(clocks).isoformat().replace("+00:00", "Z"),
                "filingIds": contract.get("filingIds") or [],
                "sourceReferences": refs, "values": values, "metricBasis": bases,
            })
        if len(result) >= 3:
            break
    return result


def annual_financial_observations(company, symbol="", cutoff_at=""):
    history = (company.get("financialEvidence") or {}).get("annualHistory")
    history = history if isinstance(history, list) else annual_financial_history(company, symbol)
    cutoff = parse_datetime(cutoff_at)
    symbol = str(symbol or company.get("symbol") or "").upper()
    if not cutoff or not history:
        return {}
    # Do not silently rebase onto an older report because the newest lacks a metric.
    report = max(history, key=lambda row: str(row.get("periodEnd") or ""))
    published = parse_datetime(report.get("publishedAt"))
    observed = parse_datetime(report.get("observedAt"))
    period = parse_datetime(report.get("periodEnd"))
    if (report.get("version") != ANNUAL_HISTORY_VERSION or report.get("symbol") != symbol
            or not all((published, observed, period)) or period > published
            or max(published, observed) > cutoff or not report.get("reportObservationId")):
        return {}
    return {
        field: {**{key: report[key] for key in (
            "symbol", "periodStart", "periodEnd", "reportObservationId", "publishedAt", "observedAt", "sourceReferences",
        )}, "metric": metric, "value": report["values"][metric],
            "basis": dict(report["metricBasis"][metric])}
        for field, metric in REPORT_WATCH_FIELDS.items()
        if _finite((report.get("values") or {}).get(metric)) is not None and metric in (report.get("metricBasis") or {})
    }
