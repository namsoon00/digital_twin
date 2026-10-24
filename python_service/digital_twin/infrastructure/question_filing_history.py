"""One provider-guarded issuer listing read within an existing source task."""
import re
from datetime import datetime, timezone

from digital_twin.modules.news_intelligence.contracts import (
    build_filing_history, needs_filing_history, required_report_periods,
)


def collect_filing_history(provider, sec, symbol, tasks, filings):
    periods = required_report_periods(tasks)
    if not needs_filing_history(tasks) and not periods:
        return {}
    cik = str(sec.get("cik") or "").zfill(10)
    if not re.fullmatch(r"\d{10}", cik):
        raise ValueError("filing-history-invalid-cik")
    candidates = [row for row in filings if row.get("filingDate") and row.get("url")]
    if not candidates and not periods:
        return {}
    terms = " ".join(str(task.get("question") or "") + " " + " ".join(task.get("queryTerms") or [])
                     + " " + " ".join((task.get("sourceFocus") or {}).get("missingRequirements") or [])
                     for task in tasks)
    dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", terms))
    matching = [row["filingDate"] for row in candidates if row["filingDate"] in dates]
    start = min(periods) if periods else min(matching) if matching else max(row["filingDate"] for row in candidates)
    url = "https://data.sec.gov/submissions/CIK" + cik + ".json"
    payload = provider.guarded_call("SEC EDGAR", "question-submissions:" + symbol,
                                   lambda: provider.fetch_json(url, provider.sec_headers()))
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return build_filing_history(payload, symbol, cik, start, observed, **({"report_periods": periods} if periods else {}))
