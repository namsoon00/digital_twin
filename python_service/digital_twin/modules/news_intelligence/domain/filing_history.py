"""Bounded SEC listing receipts. Listing coverage never proves filing contents."""
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import re

VERSION = "sec-filing-history-v1"
FORMS = ("10-Q", "10-Q/A", "10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A", "8-K", "8-K/A", "6-K", "6-K/A")
FIELDS = ("form", "filingDate", "reportDate", "accessionNumber", "primaryDocument")
MAX_ROWS = 24


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def build_filing_history(payload, symbol, cik, from_date, observed_at, *, report_periods=()):
    """Scan the complete recent array before applying a bounded output slice."""
    if (not re.fullmatch(r"\d{10}", cik) or not isinstance(payload, dict)
            or str(payload.get("cik", "")).lstrip("0") != cik.lstrip("0")
            or symbol not in payload.get("tickers", [])):
        raise ValueError("filing-history-issuer-mismatch")
    cutoff = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    start, end = date.fromisoformat(from_date), cutoff.date()
    if cutoff.tzinfo is None or start > end:
        raise ValueError("filing-history-invalid-window")
    if len(report_periods) > 8 or any(date.fromisoformat(value).isoformat() != value for value in report_periods):
        raise ValueError("filing-history-invalid-period-scope")
    filings = payload.get("filings") or {}
    recent = filings.get("recent") or {}
    forms = recent.get("form")
    if (not isinstance(forms, list) or not 1 <= len(forms) <= 10000
            or any(not isinstance(recent.get(key), list) or len(recent[key]) != len(forms) for key in FIELDS)):
        raise ValueError("filing-history-incomplete-columns")
    gaps, rows, dates, seen = [], [], [], set()
    for index, form in enumerate(forms):
        row = {key: recent[key][index] for key in FIELDS}
        filed = date.fromisoformat(row["filingDate"])
        dates.append(filed)
        if row["reportDate"]:
            date.fromisoformat(row["reportDate"])
        if (not isinstance(form, str) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", str(row["accessionNumber"]))
                or row["accessionNumber"] in seen):
            raise ValueError("filing-history-invalid-row")
        seen.add(row["accessionNumber"])
        if start <= filed <= end and form in FORMS:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:htm|html|txt)", str(row["primaryDocument"]), re.I):
                raise ValueError("filing-history-invalid-document")
            rows.append(row)
    archives = filings.get("files")
    if not isinstance(archives, list):
        gaps.append("archive-coverage-unknown")
    else:
        for archive in archives:
            try:
                first, last = date.fromisoformat(archive["filingFrom"]), date.fromisoformat(archive["filingTo"])
                if first > last:
                    raise ValueError("invalid archive interval")
                if first <= end and last >= start:
                    gaps.append("overlapping-archive-not-read")
            except (ValueError, KeyError, TypeError):
                gaps.append("archive-coverage-unknown")
    if min(dates) > start:
        gaps.append("recent-window-does-not-cover-start")
    if len(rows) > MAX_ROWS:
        gaps.append("filing-history-row-budget")
    rows.sort(key=lambda row: (row["filingDate"], row["accessionNumber"]), reverse=True)
    if report_periods:
        # A busy issuer's recent event filings must not crowd an explicitly
        # requested historical report out of the bounded listing receipt.
        reports, others = [], []
        for row in rows:
            (reports if row["reportDate"] in report_periods else others).append(row)
        reports.sort(key=lambda row: row["form"].removesuffix('/A') in {'8-K', '6-K'})
        first, seen, rest = [], set(), []
        for row in reports:
            if row["reportDate"] not in seen:
                first.append(row); seen.add(row["reportDate"])
            else:
                rest.append(row)
        rows = first + rest + others
    result = {"version": VERSION, "symbol": symbol, "cik": cik,
        "sourceUrl": "https://data.sec.gov/submissions/CIK" + cik + ".json",
        "observedAt": observed_at, "fromDate": from_date, "throughDate": end.isoformat(),
        "forms": list(FORMS), "rows": rows[:MAX_ROWS], "matchedRowCount": len(rows),
        "complete": not gaps, "coverageGaps": sorted(set(gaps)), "sourcePayloadHash": digest(payload),
        "limitation": "조회 범위의 제출 목록입니다. 정정 공시의 내용·비GAAP 수정·오류 정정 부재를 증명하지 않습니다. "
                      "complete=false이면 미조회 범위를 포함한 정정 공시 부재도 판단할 수 없습니다."}
    result["receiptHash"] = digest(result)
    return result


def verified_filing_history(value, symbol, cik):
    if not isinstance(value, dict) or value.get("version") != VERSION:
        return {}
    unsigned = {key: item for key, item in value.items() if key != "receiptHash"}
    if (value.get("symbol") != symbol or value.get("cik") != cik
            or value.get("sourceUrl") != "https://data.sec.gov/submissions/CIK" + cik + ".json"
            or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("sourcePayloadHash")))
            or value.get("receiptHash") != digest(unsigned) or len(value.get("rows", [])) > MAX_ROWS):
        return {}
    return deepcopy(value)


def history_material(value):
    """New records/coverage may reopen work; another observation clock may not."""
    return {key: deepcopy(value[key]) for key in ("version", "symbol", "cik", "corpCode", "sourceUrl",
        "fromDate", "forms", "reportPeriods", "rows", "matchedRowCount", "complete", "coverageGaps") if key in value}
