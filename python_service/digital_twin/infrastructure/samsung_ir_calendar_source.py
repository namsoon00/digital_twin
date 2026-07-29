import html
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List

from ..domain.investment_calendar import utc_iso
from ..domain.official_calendar import OfficialCalendarEvent, kst_datetime
from .external_signal_utils import (
    DISABLED_SETTING_VALUES,
    default_text_fetcher,
    external_call_target,
    guarded_external_call,
    guarded_int_setting,
)


SAMSUNG_IR_EVENTS_URL = "https://www.samsung.com/global/ir/ir-events-presentations/events/"
MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_SETTING_VALUES


def parse_samsung_ir_earnings_events(markup: str, minimum_at: datetime = None) -> List[OfficialCalendarEvent]:
    text = html.unescape(str(markup or ""))
    pattern = re.compile(
        r"<dt[^>]*>\s*([1-4])Q(\d{2})\s+Earnings\s+Conference\s+Call\s*</dt>\s*"
        r"<dd[^>]*>\s*([A-Za-z]+)\s+(\d{1,2}),\s+(20\d{2}),\s+"
        r"(\d{1,2}):(\d{2})\s*([ap])\.m\.\s*KST\s*</dd>",
        flags=re.IGNORECASE,
    )
    cutoff = minimum_at.astimezone(timezone.utc) if minimum_at else None
    events = []
    seen = set()
    for match in pattern.finditer(text):
        quarter = int(match.group(1))
        fiscal_year = 2000 + int(match.group(2))
        month = MONTHS.get(match.group(3).casefold())
        if not month:
            continue
        day = int(match.group(4))
        calendar_year = int(match.group(5))
        hour = int(match.group(6))
        minute = int(match.group(7))
        if match.group(8).casefold() == "p" and hour < 12:
            hour += 12
        if match.group(8).casefold() == "a" and hour == 12:
            hour = 0
        starts = kst_datetime(calendar_year, month, day, str(hour).zfill(2) + ":" + str(minute).zfill(2))
        if cutoff and starts.astimezone(timezone.utc) < cutoff:
            continue
        event_id = "official-samsung-ir-earnings-005930-{}-q{}".format(fiscal_year, quarter)
        if event_id in seen:
            continue
        seen.add(event_id)
        events.append(OfficialCalendarEvent(
            event_id=event_id,
            title="삼성전자 {}년 {}분기 실적 발표".format(fiscal_year, quarter),
            event_type="earnings",
            starts_at=utc_iso(starts),
            timezone="Asia/Seoul",
            all_day=False,
            status="active",
            importance=95,
            symbols=["005930"],
            markets=["KOSPI"],
            source="Samsung Electronics IR",
            source_url=SAMSUNG_IR_EVENTS_URL,
            notes="삼성전자 공식 IR 이벤트 페이지에서 확인한 실적 콘퍼런스콜 일정입니다.",
            reminder_offsets_minutes=[1440, 180, 60, 0],
            payload={
                "autoDetected": True,
                "officialSource": True,
                "scheduleState": "confirmed",
                "reviewRequired": False,
                "sourceProvider": "Samsung Electronics IR",
                "sourceTrustState": "trusted",
                "calendarIdentity": "005930:earnings:{}-q{}".format(fiscal_year, quarter),
                "fiscalYear": fiscal_year,
                "fiscalQuarter": quarter,
                "dateSource": "samsung-ir-events",
                "detector": "official-issuer-ir-calendar-sync-v1",
            },
        ))
    return sorted(events, key=lambda event: event.starts_at)


class SamsungIrEarningsCalendarSource:
    def __init__(
        self,
        settings: Dict[str, object] = None,
        fetch_text: Callable[[str, Dict[str, str], float], str] = None,
        now: Callable[[], datetime] = None,
        guard_state: Dict[str, object] = None,
    ):
        self.settings = dict(settings or {})
        self.fetch_text = fetch_text or default_text_fetcher
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.guard_state = guard_state

    def enabled(self) -> bool:
        return (
            truthy(self.settings.get("investmentCalendarOfficialMacroSyncEnabled"), True)
            and truthy(self.settings.get("investmentCalendarOfficialEarningsSyncEnabled"), True)
        )

    def timeout_seconds(self) -> float:
        try:
            return max(1.0, min(float(self.settings.get("investmentCalendarOfficialMacroSyncTimeoutSeconds") or 8), 30.0))
        except (TypeError, ValueError):
            return 8.0

    def events(self) -> List[OfficialCalendarEvent]:
        if not self.enabled():
            return []
        headers = {"Accept": "text/html", "User-Agent": "DigitalTwin/1.0"}
        markup = guarded_external_call(
            self.settings,
            "Samsung IR calendar",
            external_call_target(SAMSUNG_IR_EVENTS_URL),
            lambda: self.fetch_text(SAMSUNG_IR_EVENTS_URL, headers, self.timeout_seconds()),
            state=self.guard_state,
            rate_limit_seconds=guarded_int_setting(
                self.settings,
                "investmentCalendarOfficialEarningsRateLimitSeconds",
                0,
                0,
                86400,
            ),
        )
        return parse_samsung_ir_earnings_events(
            markup,
            minimum_at=self.now().astimezone(timezone.utc) - timedelta(days=180),
        )
