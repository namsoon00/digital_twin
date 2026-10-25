"""Period-ordered, exact report views; collection clocks do not rank quarters."""
from datetime import datetime
import math

from .observation_evidence import canonical_json, source_clock
from .observation_report_view import report_metrics, report_view

VERSION = "business-evidence-reservation-v3"
METRICS = ("cashFlowBridge", "revenue", "operatingIncome", "netIncome", "operatingCashFlow",
           "capitalExpenditure", "changeInWorkingCapital", "freeCashFlow",
           "revenueGrowthPct", "operatingIncomeGrowthPct", "freeCashFlowGrowthPct",
           "cashConversionPct", "sharesOutstandingGrowthPct")


def period(row):
    try:
        return datetime.fromisoformat(str(row.get("periodEnd") or "").replace("Z", "+00:00")).date().isoformat()
    except (ValueError, TypeError):
        return ""


def ordered_reports(facts):
    rows = [row for row in facts if row.get("kind") == "company-financial-state"]
    # Stable ID tie-breaking, then actual reporting period, then source revision.
    rows.sort(key=lambda row: row["id"])
    rows.sort(key=lambda row: (period(row), source_clock(row)), reverse=True)
    return rows


def baseline(session, facts, question_documents, captured_at):
    cutoff = str(captured_at)[:10]
    reports = [row for row in ordered_reports(facts) if period(row) and period(row) <= cutoff
               and row.get("judgementEvidenceUsable") is not False
               and math.isfinite(source_clock(row))
               and source_clock(row) <= source_clock({"asOf": captured_at})]
    groups = [[row for row in reports if row.get("frequency") == frequency]
              for frequency in ("quarterly", "annual", "interim")]
    requirements = []
    for group in groups:
        if group:
            requirements.append({"frequency": group[0]["frequency"], "periodEnd": period(group[0]),
                "factIds": [row["id"] for row in group if period(row) == period(group[0])]})
    # One source per period in the initial baseline. Other providers and full
    # metrics remain readable; this is not a claim that scopes are comparable.
    pairs = []
    for group in groups:
        periods = set()
        for row in group:
            if period(row) not in periods:
                pairs.append(row)
                periods.add(period(row))
            if len(periods) == 2:
                break
    by_id = {row["id"]: row for row in facts}
    questions = [by_id[row["factId"]] for row in question_documents]
    others = sorted(facts, key=lambda row: (-source_clock(row), row["id"]))
    others = [row for row in others if row.get("documentVerified") or row.get("kind") == "company-relationship"][:4]
    selected, omitted, views, report_views, used = [], [], [], {}, 0
    seen = set()
    for row in [*questions, *pairs, *others]:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        metrics = [key for key in METRICS if key in report_metrics(row)][:12]
        if row.get("kind") == "company-financial-state" and metrics:
            view = report_view(row, metrics)
        elif row.get("historicalDocumentReference") or row.get("questionSourceReference"):
            view = session.read_document(row["id"])["facts"][0]
        else:
            view = row
        size = len(canonical_json(view).encode())
        if used + size > 32000:
            omitted.append(row["id"])
            continue
        selected.append(row["id"])
        used += size
        if view.get("evidenceView", {}).get("version") == "exact-report-metrics-v1":
            report_views[row["id"]] = metrics
        elif view is not row:
            views.append(row["id"])
    return {"version": VERSION, "factIds": selected, "omittedByBudget": omitted,
            "bytes": used, "viewFactIds": views, "reportViews": report_views,
            "reportRequirements": requirements,
            "policy": "report-period-descending-with-comparison-and-exact-metrics"}
