"""Question-scoped DART receipt selection; titles do not establish table scope."""
from calendar import monthrange
from datetime import date
import re


def period_end(title):
    match = re.search(r"(?:사업|반기|분기)보고서\s*\(\s*((?:19|20)\d{2})[.\-/](\d{1,2})\s*\)", str(title))
    if not match:
        return ""
    year, month = map(int, match.groups())
    try:
        return date(year, month, monthrange(year, month)[1]).isoformat()
    except ValueError:
        return ""


def select_dart_question_filings(rows, tasks, cutoff):
    from .collection_scope import required_report_periods
    required = required_report_periods(tasks)
    text = " ".join(str(task.get("question", "")) + " " + " ".join(task.get("queryTerms") or []) for task in tasks)
    years = {int(value) for value in re.findall(r"(?:19|20)\d{2}", text)}
    annual = bool(re.search(r"연간|연차|사업보고서|annual", text, re.I))
    latest = {}
    for source in rows:
        receipt = str(source.get("receiptNo") or source.get("rcept_no") or "")
        published = str(source.get("receiptDate") or source.get("rcept_dt") or "").replace("-", "")
        title = str(source.get("reportName") or source.get("report_nm") or "")
        end = period_end(title)
        try:
            day = date.fromisoformat(published[:4] + "-" + published[4:6] + "-" + published[6:8])
        except ValueError:
            continue
        if (not re.fullmatch(r"\d{14}", receipt) or receipt[:8] != published or not end
                or day.isoformat() > cutoff[:10] or end > cutoff[:10]
                or (required and end not in required)
                or (not required and years and int(end[:4]) not in years)
                or (not required and annual and "사업보고서" not in title)):
            continue
        row = {**source, "receiptNo": receipt, "receiptDate": published, "reportName": title,
               "reportPeriodEnd": end}
        # A later receipt for the same period may be a correction. Keep its
        # exact identity; do not merge conflicting originals or assert scope.
        if end not in latest or receipt > latest[end]["receiptNo"]:
            latest[end] = row
    selected = sorted(latest.values(), key=lambda row: (row["reportPeriodEnd"], row["receiptNo"]), reverse=True)
    if not required and len(selected) > 1 and re.search(r"전년|동기|year.over.year|prior.year|yoy", text, re.I):
        anchor = date.fromisoformat(selected[0]["reportPeriodEnd"])
        peer = next((row for row in selected[1:] if 330 <= (anchor-date.fromisoformat(row["reportPeriodEnd"])).days <= 400), None)
        if peer:
            selected = [selected[0], peer] + [row for row in selected[1:] if row is not peer]
    return selected[:2]
