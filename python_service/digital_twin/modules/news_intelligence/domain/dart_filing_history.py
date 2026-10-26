"""Issuer-bound DART listing receipts, separate from statement verification."""
from copy import deepcopy
from datetime import date, datetime
import re

from .dart_question_filings import period_end
from .filing_history import digest

VERSION = "dart-filing-history-v1"
MAX_ROWS = 24


def build_history(pages, symbol, corp, start, observed_at, periods=()):
    if not re.fullmatch(r"\d{6}", symbol) or not re.fullmatch(r"\d{8}", corp):
        raise ValueError("filing-history-issuer-mismatch")
    cutoff = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    first, last = date.fromisoformat(start), cutoff.date()
    if cutoff.tzinfo is None or first > last or not 1 <= len(pages) <= 2:
        raise ValueError("filing-history-invalid-window")
    if len(periods) > 8 or any(date.fromisoformat(value).isoformat() != value for value in periods):
        raise ValueError("filing-history-invalid-period-scope")
    gaps, rows, seen = set(), [], set()
    total_pages, total_count, scanned = None, None, 0
    for number, payload in enumerate(pages, 1):
        if payload.get("status") == "013" and len(pages) == 1:
            total_pages, total_count = 0, 0
            continue
        if payload.get("status") != "000" or int(payload.get("page_no", 0)) != number:
            raise ValueError("filing-history-invalid-page")
        page_total, count = int(payload.get("total_page", 0)), int(payload.get("total_count", -1))
        if (page_total < number or count < 0 or not isinstance(payload.get("list"), list)
                or not 1 <= len(payload["list"]) <= 100):
            raise ValueError("filing-history-invalid-page")
        if total_pages is not None and (page_total, count) != (total_pages, total_count):
            gaps.add("listing-changed-during-pagination")
        total_pages, total_count = page_total, count
        for source in payload["list"]:
            scanned += 1
            receipt, stamp = str(source.get("rcept_no", "")), str(source.get("rcept_dt", ""))
            filed = date.fromisoformat(stamp[:4] + "-" + stamp[4:6] + "-" + stamp[6:8])
            if (source.get("corp_code") != corp or not re.fullmatch(r"\d{14}", receipt)
                    or receipt[:8] != stamp or not first <= filed <= last or receipt in seen):
                raise ValueError("filing-history-invalid-row")
            seen.add(receipt)
            title = str(source.get("report_nm") or "")
            period = period_end(title)
            if period and period > filed.isoformat():
                raise ValueError("filing-history-invalid-report-period")
            if period and (not periods or period in periods):
                rows.append({"receiptNo": receipt, "publishedAt": filed.isoformat(), "reportName": title,
                    "periodEnd": period, "sourceUrl": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt,
                    "amendment": "정정" in title, "remark": str(source.get("rm") or "")[:120]})
    if total_pages is None or total_pages > len(pages) or scanned != total_count:
        gaps.add("listing-page-budget-or-incomplete")
    if len(rows) > MAX_ROWS:
        gaps.add("filing-history-row-budget")
    rows.sort(key=lambda row: row["receiptNo"], reverse=True)
    value = {"version": VERSION, "symbol": symbol, "corpCode": corp,
        "sourceUrl": "https://opendart.fss.or.kr/api/list.json", "fromDate": start,
        "throughDate": last.isoformat(), "observedAt": observed_at, "reportPeriods": list(periods),
        "rows": rows[:MAX_ROWS], "matchedRowCount": len(rows), "scannedRowCount": scanned,
        "pageCount": len(pages), "complete": not gaps, "coverageGaps": sorted(gaps),
        "sourcePayloadHash": digest(pages),
        "limitation": "지정 발행사·조회 기간의 정기공시 제출 이력입니다. 정정 내용이나 재무표의 기간·단위·연결범위 일치를 입증하지 않습니다. complete=false이면 정정 부재나 최종본을 단정할 수 없습니다."}
    value["summary"] = ("조회 기간 " + start + " ~ " + last.isoformat() + " · "
        + ("조회 범위의 제출 목록 확인" if value["complete"] else "제출 목록 일부 미확인") + "\n"
        + "\n".join(row["publishedAt"] + " · " + row["reportName"] + " · 접수 " + row["receiptNo"]
                    for row in value["rows"]) + "\n" + value["limitation"])
    value["receiptHash"] = digest(value)
    return value


def verified_history(value, symbol, corp):
    if not isinstance(value, dict) or value.get("version") != VERSION:
        return {}
    unsigned = {key: item for key, item in value.items() if key != "receiptHash"}
    if (value.get("symbol") != symbol or value.get("corpCode") != corp
            or not re.fullmatch(r"\d{8}", corp)
            or value.get("sourceUrl") != "https://opendart.fss.or.kr/api/list.json"
            or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("sourcePayloadHash")))
            or value.get("receiptHash") != digest(unsigned) or not isinstance(value.get("rows"), list)
            or len(value["rows"]) > MAX_ROWS):
        return {}
    return deepcopy(value)
