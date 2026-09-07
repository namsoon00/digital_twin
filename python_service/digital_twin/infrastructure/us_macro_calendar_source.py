import html
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from ..domain.investment_calendar import utc_iso
from ..domain.official_calendar import OfficialCalendarEvent, clean_text, parse_kst_time
from .external_signal_utils import (
    DISABLED_SETTING_VALUES,
    default_json_fetcher,
    default_text_fetcher,
    external_call_target,
    guarded_external_call,
    guarded_int_setting,
)


FED_FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BLS_RELEASE_CALENDAR_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
BEA_RELEASE_CALENDAR_URL = "https://www.bea.gov/news/schedule"
FRED_RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/releases/dates"
EASTERN = ZoneInfo("America/New_York")
MONTH_NUMBERS = {
    name.lower(): index
    for index, name in enumerate(
        [
            "",
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
    )
    if name
}


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_SETTING_VALUES


def timeout_seconds(settings: Dict[str, object]) -> float:
    try:
        return max(1.0, min(float((settings or {}).get("investmentCalendarOfficialMacroSyncTimeoutSeconds") or 8), 30.0))
    except (TypeError, ValueError):
        return 8.0


def eastern_datetime(year: int, month: int, day: int, time_text: object) -> datetime:
    hour, minute = parse_kst_time(time_text, "08:30")
    return datetime(int(year), int(month), int(day), hour, minute, tzinfo=EASTERN)


def source_rate_limit(settings: Dict[str, object]) -> int:
    return guarded_int_setting(
        settings,
        "investmentCalendarOfficialMacroSyncRateLimitSeconds",
        600,
        0,
        86400,
    )


def fetch_official_text(
    settings: Dict[str, object],
    provider: str,
    url: str,
    fetch_text: Callable[[str, Dict[str, str], float], str],
    guard_state: Dict[str, object] = None,
    accept: str = "text/html",
) -> str:
    headers = {
        "Accept": accept,
        "User-Agent": "OrbitAlpha/1.0 (official investment calendar sync)",
    }
    return guarded_external_call(
        settings,
        provider,
        external_call_target(url),
        lambda: fetch_text(url, headers, timeout_seconds(settings)),
        state=guard_state,
        rate_limit_seconds=source_rate_limit(settings),
    )


def parse_fomc_meeting_events(
    markup: str,
    source_url: str = FED_FOMC_CALENDAR_URL,
    decision_time_et: object = "14:00",
    years: Iterable[int] = None,
) -> List[OfficialCalendarEvent]:
    content = html.unescape(str(markup or ""))
    headings = list(re.finditer(r"<a\b[^>]*>\s*(20\d{2})\s+FOMC Meetings\s*</a>", content, flags=re.IGNORECASE))
    selected_years = {int(year) for year in years or [] if int(year or 0)}
    events: List[OfficialCalendarEvent] = []
    for index, heading in enumerate(headings):
        year = int(heading.group(1))
        if selected_years and year not in selected_years:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        block = content[heading.end():end]
        meetings = re.finditer(
            r"fomc-meeting__month[^>]*>\s*<strong>\s*([^<]+?)\s*</strong>.*?"
            r"fomc-meeting__date[^>]*>\s*([^<]+?)\s*</div>",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        for meeting in meetings:
            month_name = clean_text(meeting.group(1), 20)
            month = MONTH_NUMBERS.get(month_name.lower(), 0)
            day_values = [int(value) for value in re.findall(r"\d{1,2}", meeting.group(2) or "")]
            if not month or not day_values:
                continue
            start_day = day_values[0]
            decision_day = day_values[-1]
            decision_month = month
            decision_year = year
            if len(day_values) > 1 and decision_day < start_day:
                decision_month += 1
                if decision_month > 12:
                    decision_month = 1
                    decision_year += 1
            try:
                starts = eastern_datetime(decision_year, decision_month, decision_day, decision_time_et)
            except ValueError:
                continue
            projection_release = "*" in str(meeting.group(2) or "")
            date_key = starts.strftime("%Y%m%d")
            events.append(
                OfficialCalendarEvent(
                    event_id="official-fed-fomc-" + date_key,
                    title="미국 연준 FOMC 기준금리 결정",
                    event_type="centralBank",
                    starts_at=utc_iso(starts),
                    timezone="America/New_York",
                    importance=100,
                    markets=["US"],
                    source="Federal Reserve",
                    source_url=source_url,
                    notes=(
                        "연방공개시장위원회 공식 회의 일정입니다. 기준금리 결정과 성명, 기자회견이 "
                        "미국 금리·달러·주식 밸류에이션에 영향을 줄 수 있습니다. 공식 일정 페이지가 "
                        "발표 시각을 제공하지 않아 14:00 ET를 알림 기준으로 사용합니다."
                    ),
                    reminder_offsets_minutes=[1440, 180, 60, 0],
                    payload={
                        "autoDetected": True,
                        "officialSource": True,
                        "scheduleState": "confirmed",
                        "reviewRequired": False,
                        "timeState": "operationalDefault",
                        "reminderEnabled": True,
                        "sourceProvider": "FED",
                        "country": "US",
                        "centralBank": "Federal Reserve",
                        "meetingType": "fomcPolicyDecision",
                        "policyRateDecisionExpected": True,
                        "economicProjectionsExpected": projection_release,
                        "defaultTimeEt": clean_text(decision_time_et or "14:00", 20),
                        "dateSource": "federal-reserve-fomc-calendar",
                        "detector": "official-calendar-sync-v2",
                    },
                )
            )
    return sorted({event.event_id: event for event in events}.values(), key=lambda item: item.starts_at)


def unfold_ical_lines(value: str) -> List[str]:
    unfolded: List[str] = []
    for raw_line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += raw_line[1:]
        else:
            unfolded.append(raw_line)
    return unfolded


def ical_text(value: object) -> str:
    return clean_text(
        str(value or "")
        .replace("\\n", " ")
        .replace("\\N", " ")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\"),
        800,
    )


def parse_ical_datetime(key: str, value: str) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        for date_format in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%MZ"):
            try:
                return datetime.strptime(raw, date_format).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    for date_format in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
        try:
            parsed = datetime.strptime(raw, date_format)
            return parsed.replace(tzinfo=EASTERN)
        except ValueError:
            continue
    return None


def parse_bls_release_events(
    calendar_text: str,
    source_url: str = BLS_RELEASE_CALENDAR_URL,
) -> List[OfficialCalendarEvent]:
    lines = unfold_ical_lines(calendar_text)
    records: List[Dict[str, Tuple[str, str]]] = []
    current: Dict[str, Tuple[str, str]] = None
    for line in lines:
        if line.strip().upper() == "BEGIN:VEVENT":
            current = {}
            continue
        if line.strip().upper() == "END:VEVENT":
            if current is not None:
                records.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        raw_key, raw_value = line.split(":", 1)
        normalized_key = raw_key.split(";", 1)[0].upper()
        current[normalized_key] = (raw_key, raw_value)

    events: List[OfficialCalendarEvent] = []
    for record in records:
        summary = ical_text((record.get("SUMMARY") or ("", ""))[1])
        normalized_summary = summary.lower()
        if "consumer price index" in normalized_summary:
            indicator = "cpi"
            title = "미국 소비자물가지수(CPI) 발표"
            importance = 100
        elif "employment situation" in normalized_summary:
            indicator = "employment"
            title = "미국 고용보고서 발표"
            importance = 100
        else:
            continue
        dt_key, dt_value = record.get("DTSTART") or ("", "")
        starts = parse_ical_datetime(dt_key, dt_value)
        if not starts:
            continue
        date_key = starts.astimezone(EASTERN).strftime("%Y%m%d")
        description = ical_text((record.get("DESCRIPTION") or ("", ""))[1])
        events.append(
            OfficialCalendarEvent(
                event_id="official-bls-" + indicator + "-" + date_key,
                title=title,
                event_type="macro",
                starts_at=utc_iso(starts),
                timezone="America/New_York",
                importance=importance,
                markets=["US"],
                source="U.S. Bureau of Labor Statistics",
                source_url=source_url,
                notes="BLS 공식 발표 일정입니다. 실제치와 시장 예상치의 차이가 금리·달러·주식시장에 영향을 줄 수 있습니다.",
                reminder_offsets_minutes=[1440, 180, 60, 0],
                payload={
                    "autoDetected": True,
                    "officialSource": True,
                    "scheduleState": "confirmed",
                    "reviewRequired": False,
                    "timeState": "official",
                    "reminderEnabled": True,
                    "sourceProvider": "BLS",
                    "country": "US",
                    "indicator": indicator,
                    "officialTitle": summary,
                    "officialDescription": description,
                    "dateSource": "bls-release-calendar-ical",
                    "detector": "official-calendar-sync-v2",
                },
            )
        )
    return sorted({event.event_id: event for event in events}.values(), key=lambda item: item.starts_at)


def parse_fred_bls_release_events(
    payload: Dict[str, object],
    source_url: str = "https://fred.stlouisfed.org/releases/calendar",
) -> List[OfficialCalendarEvent]:
    rows = payload.get("release_dates") if isinstance(payload, dict) else []
    events: List[OfficialCalendarEvent] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        release_name = clean_text(row.get("release_name"), 300)
        release_id = str(row.get("release_id") or "").strip()
        normalized_name = release_name.lower()
        if release_id == "10" or normalized_name == "consumer price index":
            indicator = "cpi"
            title = "미국 소비자물가지수(CPI) 발표"
        elif release_id == "50" or normalized_name == "employment situation":
            indicator = "employment"
            title = "미국 고용보고서 발표"
        else:
            continue
        try:
            released_on = datetime.strptime(str(row.get("date") or ""), "%Y-%m-%d")
            starts = datetime(
                released_on.year,
                released_on.month,
                released_on.day,
                8,
                30,
                tzinfo=EASTERN,
            )
        except ValueError:
            continue
        date_key = starts.strftime("%Y%m%d")
        events.append(
            OfficialCalendarEvent(
                event_id="official-bls-" + indicator + "-" + date_key,
                title=title,
                event_type="macro",
                starts_at=utc_iso(starts),
                timezone="America/New_York",
                importance=100,
                markets=["US"],
                source="Federal Reserve Bank of St. Louis FRED",
                source_url=source_url,
                notes=(
                    "BLS 직접 일정 조회가 불가능할 때 FRED가 배포하는 원천기관 발표일로 보완한 일정입니다. "
                    "발표 시각은 BLS 핵심 지표의 08:30 ET 기준을 사용합니다."
                ),
                reminder_offsets_minutes=[1440, 180, 60, 0],
                payload={
                    "autoDetected": True,
                    "officialSource": True,
                    "scheduleState": "confirmed",
                    "reviewRequired": False,
                    "timeState": "operationalDefault",
                    "reminderEnabled": True,
                    "sourceProvider": "FRED",
                    "underlyingSourceProvider": "BLS",
                    "country": "US",
                    "indicator": indicator,
                    "officialTitle": release_name,
                    "fredReleaseId": release_id,
                    "dateSource": "fred-release-dates-from-source",
                    "defaultTimeEt": "08:30",
                    "detector": "official-calendar-sync-v2",
                },
            )
        )
    return sorted({event.event_id: event for event in events}.values(), key=lambda item: item.starts_at)


def strip_markup(value: object, limit: int = 1000) -> str:
    return clean_text(re.sub(r"<[^>]+>", " ", html.unescape(str(value or ""))), limit)


def bea_release_kind(title: str) -> Tuple[str, str, int]:
    normalized = clean_text(title, 800).lower()
    if re.search(r"\bgdp\s*\((?:advance|second|third) estimate\)", normalized) or normalized.startswith(
        "gross domestic product"
    ):
        return "gdp", "미국 국내총생산(GDP) 발표", 98
    if normalized.startswith("personal income and outlays"):
        return "pce", "미국 개인소득·소비지출(PCE) 발표", 98
    return "", "", 0


def parse_bea_release_events(
    markup: str,
    source_url: str = BEA_RELEASE_CALENDAR_URL,
) -> List[OfficialCalendarEvent]:
    content = html.unescape(str(markup or ""))
    year_match = re.search(r">\s*Year\s+(20\d{2})\s*<", content, flags=re.IGNORECASE)
    if not year_match:
        return []
    year = int(year_match.group(1))
    events: List[OfficialCalendarEvent] = []
    for row_match in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", content, flags=re.IGNORECASE | re.DOTALL):
        row = row_match.group(1)
        date_match = re.search(r'class=["\'][^"\']*release-date[^"\']*["\'][^>]*>(.*?)</div>', row, flags=re.IGNORECASE | re.DOTALL)
        time_match = re.search(r'<small\b[^>]*class=["\'][^"\']*text-muted[^"\']*["\'][^>]*>(.*?)</small>', row, flags=re.IGNORECASE | re.DOTALL)
        title_match = re.search(r'<td\b[^>]*class=["\'][^"\']*release-title[^"\']*["\'][^>]*>(.*?)</td>', row, flags=re.IGNORECASE | re.DOTALL)
        if not date_match or not time_match or not title_match:
            continue
        date_text = strip_markup(date_match.group(1), 40)
        time_text = strip_markup(time_match.group(1), 20)
        official_title = strip_markup(title_match.group(1), 800)
        indicator, title, importance = bea_release_kind(official_title)
        if not indicator:
            continue
        try:
            date_value = datetime.strptime(date_text + " " + str(year), "%B %d %Y")
            time_value = datetime.strptime(time_text, "%I:%M %p")
            starts = datetime(
                year,
                date_value.month,
                date_value.day,
                time_value.hour,
                time_value.minute,
                tzinfo=EASTERN,
            )
        except ValueError:
            continue
        date_key = starts.strftime("%Y%m%d")
        events.append(
            OfficialCalendarEvent(
                event_id="official-bea-" + indicator + "-" + date_key,
                title=title,
                event_type="macro",
                starts_at=utc_iso(starts),
                timezone="America/New_York",
                importance=importance,
                markets=["US"],
                source="U.S. Bureau of Economic Analysis",
                source_url=source_url,
                notes=(
                    "BEA 공식 발표 일정입니다. GDP 또는 PCE 실제치와 시장 예상치의 차이가 성장·물가 기대, "
                    "금리와 주식시장에 영향을 줄 수 있습니다."
                ),
                reminder_offsets_minutes=[1440, 180, 60, 0],
                payload={
                    "autoDetected": True,
                    "officialSource": True,
                    "scheduleState": "confirmed",
                    "reviewRequired": False,
                    "timeState": "official",
                    "reminderEnabled": True,
                    "sourceProvider": "BEA",
                    "country": "US",
                    "indicator": indicator,
                    "officialTitle": official_title,
                    "dateSource": "bea-release-schedule",
                    "detector": "official-calendar-sync-v2",
                },
            )
        )
    return sorted({event.event_id: event for event in events}.values(), key=lambda item: item.starts_at)


class FederalReserveFomcCalendarSource:
    def __init__(self, settings=None, fetch_text=None, now=None, guard_state=None):
        self.settings = dict(settings or {})
        self.fetch_text = fetch_text or default_text_fetcher
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.guard_state = guard_state

    def enabled(self) -> bool:
        return truthy(self.settings.get("investmentCalendarOfficialMacroSyncEnabled"), True) and truthy(
            self.settings.get("investmentCalendarFedFomcEnabled"), True
        )

    def years(self) -> List[int]:
        current_year = self.now().astimezone(EASTERN).year
        return [current_year, current_year + 1]

    def events(self) -> List[OfficialCalendarEvent]:
        if not self.enabled():
            return []
        markup = fetch_official_text(
            self.settings,
            "Federal Reserve Calendar",
            FED_FOMC_CALENDAR_URL,
            self.fetch_text,
            self.guard_state,
        )
        return parse_fomc_meeting_events(
            markup,
            decision_time_et=self.settings.get("investmentCalendarFedDecisionTimeEt") or "14:00",
            years=self.years(),
        )


class BlsMacroReleaseCalendarSource:
    def __init__(self, settings=None, fetch_text=None, fetch_json=None, now=None, guard_state=None):
        self.settings = dict(settings or {})
        self.fetch_text = fetch_text or default_text_fetcher
        self.fetch_json = fetch_json or default_json_fetcher
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.guard_state = guard_state

    def enabled(self) -> bool:
        return truthy(self.settings.get("investmentCalendarOfficialMacroSyncEnabled"), True) and truthy(
            self.settings.get("investmentCalendarBlsMacroEnabled"), True
        )

    def fred_fallback_enabled(self) -> bool:
        return truthy(self.settings.get("investmentCalendarBlsFredFallbackEnabled"), True) and bool(
            str(self.settings.get("fredApiKey") or "").strip()
        )

    def fred_fallback_events(self) -> List[OfficialCalendarEvent]:
        now_at = self.now().astimezone(timezone.utc)
        query = urllib.parse.urlencode({
            "api_key": str(self.settings.get("fredApiKey") or "").strip(),
            "file_type": "json",
            "realtime_start": now_at.date().isoformat(),
            "realtime_end": (now_at + timedelta(days=370)).date().isoformat(),
            "include_release_dates_with_no_data": "true",
            "limit": "1000",
            "sort_order": "asc",
        })
        url = FRED_RELEASE_DATES_URL + "?" + query
        payload = guarded_external_call(
            self.settings,
            "FRED Release Calendar",
            external_call_target(url),
            lambda: self.fetch_json(
                url,
                {"Accept": "application/json", "User-Agent": "OrbitAlpha/1.0"},
                timeout=timeout_seconds(self.settings),
            ),
            state=self.guard_state,
            rate_limit_seconds=source_rate_limit(self.settings),
        )
        return parse_fred_bls_release_events(payload)

    def events(self) -> List[OfficialCalendarEvent]:
        if not self.enabled():
            return []
        try:
            calendar_text = fetch_official_text(
                self.settings,
                "BLS Calendar",
                BLS_RELEASE_CALENDAR_URL,
                self.fetch_text,
                self.guard_state,
                accept="text/calendar",
            )
            events = parse_bls_release_events(calendar_text)
            if events:
                return events
        except Exception:
            if not self.fred_fallback_enabled():
                raise
        if self.fred_fallback_enabled():
            return self.fred_fallback_events()
        return []


class BeaMacroReleaseCalendarSource:
    def __init__(self, settings=None, fetch_text=None, guard_state=None):
        self.settings = dict(settings or {})
        self.fetch_text = fetch_text or default_text_fetcher
        self.guard_state = guard_state

    def enabled(self) -> bool:
        return truthy(self.settings.get("investmentCalendarOfficialMacroSyncEnabled"), True) and truthy(
            self.settings.get("investmentCalendarBeaMacroEnabled"), True
        )

    def events(self) -> List[OfficialCalendarEvent]:
        if not self.enabled():
            return []
        markup = fetch_official_text(
            self.settings,
            "BEA Calendar",
            BEA_RELEASE_CALENDAR_URL,
            self.fetch_text,
            self.guard_state,
        )
        return parse_bea_release_events(markup)
