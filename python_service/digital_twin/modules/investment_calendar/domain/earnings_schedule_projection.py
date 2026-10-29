"""Review-only calendar views. These rows are never stored as reasoning facts."""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .investment_calendar import parse_utc, utc_iso


def same_earnings_schedule(left, right, window_days=14):
    if left.get("eventType") != "earnings" or right.get("eventType") != "earnings":
        return False
    def symbols(row):
        return {str(value).upper().removesuffix(".KS").removesuffix(".KQ") for value in row.get("symbols") or []}
    if not symbols(left).intersection(symbols(right)):
        return False
    first, second = left.get("payload") or {}, right.get("payload") or {}
    phases = {first.get("schedulePhase"), second.get("schedulePhase")}
    if "conference-call" in phases and "earnings-release" in phases:
        return False
    periods = [(body.get("fiscalYear"), body.get("fiscalQuarter")) for body in (first, second)]
    if all(all(period) for period in periods):
        return tuple(map(str, periods[0])) == tuple(map(str, periods[1]))
    first_at, second_at = parse_utc(left.get("startsAt")), parse_utc(right.get("startsAt"))
    return bool(first_at and second_at and abs((first_at - second_at).total_seconds()) <= window_days * 86400)


def candidate_calendar_row(candidate):
    row = candidate.to_dict() if hasattr(candidate, "to_dict") else dict(candidate)
    body = dict(row.get("payload") or {})
    if row.get("status") != "pending" or row.get("eventType") != "earnings" or not parse_utc(row.get("startsAt")):
        return None
    # Prose mentioning a date is not a structured earnings schedule.
    if not (str(body.get("dateSource") or "").startswith("calendar.")
            or row.get("source", "").lower() == "yfinance"
            or body.get("detector") == "structured-calendar-fields"
            or body.get("officialSource")):
        return None
    date_only = bool(row.get("allDay") or body.get("timeState") in {"estimatedDefault", "dateOnly", "operationalDefault"})
    if date_only:
        try:
            day = datetime.strptime(str(row.get("localDate") or body.get("eventLocalDate") or row["startsAt"][:10]), "%Y-%m-%d").date()
            row["startsAt"] = utc_iso(datetime.combine(day, time.min, ZoneInfo(row.get("timezone") or "America/New_York")))
            row["localDate"] = day.isoformat()
        except (ValueError, KeyError, ZoneInfoNotFoundError):
            return None
    body.update(scheduleState="estimated", reviewRequired=True, candidatePresentation=True,
                officialSource=False, timeState="dateOnly" if date_only else body.get("timeState", "sourceProvided"))
    row.update(eventId="candidate:" + row["candidateId"], status="tentative", readOnly=True,
               allDay=date_only, reminderOffsetsMinutes=[], payload=body)
    row["releaseInformation"] = {"status": "unconfirmed", "statusLabel": "미확정 일정 · 공식 확인 필요",
                                 "comparison": {"reason": "공식 일정 확인 전에는 발표 결과와 연결하지 않습니다."}}
    return row


def earnings_candidate_projection(candidates, official_rows, *, from_at, to_at, symbol="", event_type="", now=None):
    if event_type and event_type != "earnings":
        return []
    now = now or datetime.now(timezone.utc)
    start, end = parse_utc(from_at), parse_utc(to_at)
    rows = [row for candidate in candidates if (row := candidate_calendar_row(candidate))]
    # Observation clock, not a database rewrite/review timestamp, selects the newest estimate.
    rows.sort(key=lambda row: ((row.get("payload") or {}).get("sourceObservedAt") or row.get("createdAt") or "", row["candidateId"]), reverse=True)
    latest = []
    for row in rows:
        if any(row.get("source") == other.get("source") and same_earnings_schedule(row, other) for other in latest):
            continue
        latest.append(row)
    result = []
    for row in latest:
        when = parse_utc(row["startsAt"])
        if not when or (start and when < start) or (end and when > end):
            continue
        if symbol and symbol not in (row.get("symbols") or []):
            continue
        # Old estimates remain in the review audit, never look like current work.
        if str(row.get("localDate") or when.date().isoformat()) < now.astimezone(ZoneInfo(row.get("timezone") or "UTC")).date().isoformat():
            continue
        if any((official.get("payload") or {}).get("officialSource") and official.get("status") == "active"
               and same_earnings_schedule(row, official) for official in official_rows):
            continue
        result.append(row)
    return result
