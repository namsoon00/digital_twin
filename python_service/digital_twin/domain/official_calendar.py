import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

from .investment_calendar import utc_iso


KST = timezone(timedelta(hours=9))
DEFAULT_BOK_POLICY_DECISION_TIME_KST = "09:00"


def clean_text(value: object, limit: int = 500) -> str:
    return " ".join(str(value or "").split()).strip()[:limit].rstrip()


def parse_kst_time(value: object, fallback: str = DEFAULT_BOK_POLICY_DECISION_TIME_KST) -> tuple:
    text = clean_text(value or fallback, 20)
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        match = re.fullmatch(r"(\d{1,2})", text)
    if not match:
        text = fallback
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    hour = int(match.group(1)) if match else 9
    minute = int(match.group(2)) if match and match.lastindex and match.lastindex >= 2 else 0
    return max(0, min(hour, 23)), max(0, min(minute, 59))


def kst_datetime(year: int, month: int, day: int, time_text: object = DEFAULT_BOK_POLICY_DECISION_TIME_KST) -> datetime:
    hour, minute = parse_kst_time(time_text)
    return datetime(int(year), int(month), int(day), hour, minute, tzinfo=KST)


@dataclass(frozen=True)
class OfficialCalendarEvent:
    event_id: str
    title: str
    event_type: str
    starts_at: str
    timezone: str = "Asia/Seoul"
    all_day: bool = False
    status: str = "active"
    importance: int = 90
    symbols: List[str] = field(default_factory=list)
    markets: List[str] = field(default_factory=list)
    source: str = ""
    source_url: str = ""
    notes: str = ""
    reminder_offsets_minutes: List[int] = field(default_factory=lambda: [1440, 180, 60, 0])
    payload: Dict[str, object] = field(default_factory=dict)

    def to_calendar_payload(self, account_ids: Iterable[str] = None) -> Dict[str, object]:
        return {
            "eventId": self.event_id,
            "title": self.title,
            "eventType": self.event_type,
            "startsAt": self.starts_at,
            "timezone": self.timezone,
            "allDay": self.all_day,
            "status": self.status,
            "importance": self.importance,
            "symbols": list(self.symbols or []),
            "markets": list(self.markets or []),
            "accountIds": [clean_text(item, 191) for item in account_ids or [] if clean_text(item, 191)],
            "source": self.source,
            "sourceUrl": self.source_url,
            "notes": self.notes,
            "reminderOffsetsMinutes": list(self.reminder_offsets_minutes or []),
            "payload": dict(self.payload or {}),
        }


def bok_policy_decision_event(
    year: int,
    month: int,
    day: int,
    source_url: str,
    time_kst: object = DEFAULT_BOK_POLICY_DECISION_TIME_KST,
    weekday: str = "",
) -> OfficialCalendarEvent:
    starts = kst_datetime(year, month, day, time_kst)
    date_key = starts.strftime("%Y%m%d")
    time_text = clean_text(time_kst or DEFAULT_BOK_POLICY_DECISION_TIME_KST, 20)
    weekday_text = clean_text(weekday, 10)
    note_parts = [
        "한국은행 통화정책방향 결정회의 공식 일정입니다.",
        "기준금리 결정과 총재 기자간담회가 국내 금리, 원화, 금융주·성장주 밸류에이션에 영향을 줄 수 있습니다.",
        "공식 페이지가 회의일 중심으로 제공되어 " + time_text + " KST를 알림 기준 시각으로 사용합니다.",
    ]
    return OfficialCalendarEvent(
        event_id="official-bok-policy-decision-" + date_key,
        title="한국은행 기준금리 결정 금융통화위원회",
        event_type="centralBank",
        starts_at=utc_iso(starts),
        timezone="Asia/Seoul",
        all_day=False,
        status="active",
        importance=100,
        symbols=[],
        markets=["KR"],
        source="Bank of Korea",
        source_url=source_url,
        notes=" ".join(note_parts),
        reminder_offsets_minutes=[1440, 180, 60, 0],
        payload={
            "officialSource": True,
            "sourceProvider": "BOK",
            "country": "KR",
            "centralBank": "Bank of Korea",
            "meetingType": "monetaryPolicyDecision",
            "policyRateDecisionExpected": True,
            "defaultTimeKst": time_text,
            "weekday": weekday_text,
            "dateSource": "bok-policy-decision-page",
            "detector": "official-calendar-sync-v1",
        },
    )
