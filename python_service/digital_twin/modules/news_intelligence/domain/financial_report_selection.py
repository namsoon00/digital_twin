"""Select complete report observations, never splice numbers across sources."""
from collections import defaultdict
from copy import deepcopy

from .financial_reporting import bind_financial_report_contract, financial_report_contract_assessment, reporting_period_end


def report_family(row):
    report = row.get("reportContract") or {}
    return (str(report.get("provider") or row.get("provider") or "").lower(),
            str(row.get("frequency") or ""), tuple(sorted(report.get("scope") or [])),
            tuple(sorted(report.get("currencies") or [])))


def report_authority(row):
    provider = report_family(row)[0]
    # Issuer statements have detail absent from governmental summary feeds.
    if "dart" in provider or "sec edgar" in provider:
        return 3
    if "금융위원회" in provider or "공공데이터포털" in provider:
        return 2
    return 1


def report_end(row):
    value = reporting_period_end(row.get("periodEnd") or row.get("period"))
    return value.toordinal() if value else 0


def observation_order(row):
    report = row.get("reportContract") or {}
    observed = max((str(ref.get("fetchedAt") or "") for ref in report.get("sourceReferences") or []), default="")
    return (observed, len(row.get("metricProvenance") or {}), str(report.get("observationId") or ""))


def retained_reports(rows, normalize=lambda rows: rows):
    """Keep bounded source/scope histories, with one revision per actual period.

    The immutable external revisions remain the full history. This read model
    retains competing providers instead of losing them when a series is chosen.
    """
    by_period = {}
    for row in rows:
        if not isinstance(row, dict) or not financial_report_contract_assessment(row).get("eligible"):
            continue
        key = (*report_family(row), report_end(row))
        if key not in by_period or observation_order(row) >= observation_order(by_period[key]):
            by_period[key] = row
    families = defaultdict(list)
    for row in by_period.values():
        families[report_family(row)].append(row)
    result = []
    for key in sorted(families):
        series = sorted(families[key], key=report_end, reverse=True)[:8 if key[1] == "quarterly" else 4]
        result.extend(normalize(deepcopy(series)))
    return deepcopy(result)


def selected_reports(rows, frequency):
    families = defaultdict(list)
    for row in rows:
        if row.get("frequency") == frequency:
            families[report_family(row)].append(row)
    if not families:
        return []
    # A vendor's calendar month-end must not outrank an issuer's fiscal date.
    family = max(families, key=lambda key: (
        report_authority(families[key][0]), max(map(report_end, families[key])),
        max(observation_order(row) for row in families[key]), key,
    ))
    return deepcopy(sorted(families[family], key=report_end, reverse=True))


def bind_summary_report_lineage(fragment, reference):
    """Replace a collector-local hash with its committed external revision."""
    if reference.get("datasetId") != "public-data.kr-company-financials":
        return fragment
    result = deepcopy(fragment)
    symbol = str(reference.get("subjectKey") or "").upper()
    company = (result.get("companyKnowledge") or {}).get(symbol)
    if not isinstance(company, dict):
        return result
    company["sourceReferences"] = [dict(reference)]
    for frequency, reports in (company.get("financials") or {}).items():
        company["financials"][frequency] = [bind_financial_report_contract(row, [reference]) for row in reports]
    return result
