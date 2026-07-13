import hashlib
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .portfolio import utc_now_iso


DEFAULT_EVENT_TIMEZONE = "Asia/Seoul"
DEFAULT_REMINDER_OFFSETS_MINUTES = [1440, 60, 0]
ACTIVE_EVENT_STATUSES = {"active", "tentative"}

EVENT_TYPE_LABELS = {
    "earnings": "실적발표",
    "dividend": "배당/권리",
    "macro": "거시지표",
    "centralBank": "중앙은행",
    "disclosure": "공시",
    "shareholderMeeting": "주주총회",
    "lockup": "락업해제",
    "portfolioReview": "포트폴리오 점검",
    "custom": "사용자 이벤트",
}


def clean_text(value: object, limit: int = 500) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit].rstrip()


def bool_value(value: object, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return fallback
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return fallback


def clamp_int(value: object, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def normalize_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    return "".join(ch for ch in text if ch.isalnum() or ch in {".", "-", "_"})[:24]


def normalize_market(value: object) -> str:
    return re.sub(r"[^A-Z0-9_.-]", "", str(value or "").strip().upper())[:32]


def normalized_list(values: object, normalizer=None, limit: int = 100) -> List[str]:
    if isinstance(values, str):
        raw_values = values.split(",")
    elif isinstance(values, Iterable):
        raw_values = list(values)
    else:
        raw_values = []
    result = []
    for value in raw_values:
        normalized = normalizer(value) if normalizer else clean_text(value, 191)
        if normalized and normalized not in result:
            result.append(normalized)
        if len(result) >= limit:
            break
    return result


def event_timezone(name: object = ""):
    label = clean_text(name, 80) or DEFAULT_EVENT_TIMEZONE
    try:
        return ZoneInfo(label)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_EVENT_TIMEZONE)


def parse_event_datetime(value: object, timezone_name: str = DEFAULT_EVENT_TIMEZONE, all_day: bool = False):
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        text = text + "T00:00:00"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=event_timezone(timezone_name))
    if all_day:
        parsed = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    return parsed.astimezone(timezone.utc)


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: object):
    return parse_event_datetime(value, "UTC")


def reminder_offsets_from_payload(value: object) -> List[int]:
    if isinstance(value, str):
        raw_values = value.split(",")
    elif isinstance(value, Iterable):
        raw_values = list(value)
    else:
        raw_values = DEFAULT_REMINDER_OFFSETS_MINUTES
    offsets = []
    for raw in raw_values:
        offset = clamp_int(raw, 0, 43200, -1)
        if offset >= 0 and offset not in offsets:
            offsets.append(offset)
    return sorted(offsets, reverse=True) or list(DEFAULT_REMINDER_OFFSETS_MINUTES)


def event_type_label(event_type: str) -> str:
    return EVENT_TYPE_LABELS.get(str(event_type or "").strip(), EVENT_TYPE_LABELS["custom"])


def event_materiality_level(importance: int) -> str:
    if int(importance or 0) >= 85:
        return "critical"
    if int(importance or 0) >= 70:
        return "high"
    if int(importance or 0) >= 45:
        return "medium"
    return "low"


@dataclass
class InvestmentCalendarEvent:
    event_id: str
    title: str
    event_type: str
    starts_at: str
    ends_at: str = ""
    timezone: str = DEFAULT_EVENT_TIMEZONE
    all_day: bool = False
    status: str = "active"
    importance: int = 60
    symbols: List[str] = field(default_factory=list)
    markets: List[str] = field(default_factory=list)
    account_ids: List[str] = field(default_factory=list)
    source: str = "manual"
    source_url: str = ""
    notes: str = ""
    reminder_offsets_minutes: List[int] = field(default_factory=lambda: list(DEFAULT_REMINDER_OFFSETS_MINUTES))
    payload: Dict[str, object] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = ""

    @classmethod
    def from_payload(cls, payload: Dict[str, object]):
        payload = payload if isinstance(payload, dict) else {}
        event_id = clean_text(payload.get("eventId") or payload.get("event_id") or "", 191) or uuid.uuid4().hex
        timezone_name = clean_text(payload.get("timezone") or DEFAULT_EVENT_TIMEZONE, 80)
        all_day = bool_value(payload.get("allDay") if "allDay" in payload else payload.get("all_day"), False)
        starts_at = parse_event_datetime(payload.get("startsAt") or payload.get("starts_at"), timezone_name, all_day)
        if not starts_at:
            raise ValueError("startsAt은 ISO 날짜 또는 날짜시간이어야 합니다.")
        ends_at = parse_event_datetime(payload.get("endsAt") or payload.get("ends_at"), timezone_name, all_day)
        title = clean_text(payload.get("title"), 255)
        if not title:
            raise ValueError("title은 필요합니다.")
        event_type = clean_text(payload.get("eventType") or payload.get("event_type") or "custom", 64) or "custom"
        if event_type not in EVENT_TYPE_LABELS:
            event_type = "custom"
        created_at = clean_text(payload.get("createdAt") or payload.get("created_at"), 40) or utc_now_iso()
        payload_body = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        return cls(
            event_id=event_id,
            title=title,
            event_type=event_type,
            starts_at=utc_iso(starts_at),
            ends_at=utc_iso(ends_at) if ends_at else "",
            timezone=timezone_name or DEFAULT_EVENT_TIMEZONE,
            all_day=all_day,
            status=clean_text(payload.get("status") or "active", 32) or "active",
            importance=clamp_int(payload.get("importance"), 0, 100, 60),
            symbols=normalized_list(payload.get("symbols"), normalize_symbol, 100),
            markets=normalized_list(payload.get("markets"), normalize_market, 50),
            account_ids=normalized_list(payload.get("accountIds") if "accountIds" in payload else payload.get("account_ids"), lambda value: clean_text(value, 191), 50),
            source=clean_text(payload.get("source") or "manual", 120) or "manual",
            source_url=clean_text(payload.get("sourceUrl") or payload.get("source_url"), 1000),
            notes=clean_text(payload.get("notes"), 2000),
            reminder_offsets_minutes=reminder_offsets_from_payload(
                payload.get("reminderOffsetsMinutes")
                if "reminderOffsetsMinutes" in payload
                else payload.get("reminder_offsets_minutes")
            ),
            payload=payload_body,
            created_at=created_at,
            updated_at=clean_text(payload.get("updatedAt") or payload.get("updated_at"), 40),
        )

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        return cls.from_payload(payload)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "eventId": payload["event_id"],
            "title": payload["title"],
            "eventType": payload["event_type"],
            "eventTypeLabel": event_type_label(payload["event_type"]),
            "startsAt": payload["starts_at"],
            "endsAt": payload["ends_at"],
            "timezone": payload["timezone"],
            "allDay": payload["all_day"],
            "status": payload["status"],
            "importance": payload["importance"],
            "materialityLevel": event_materiality_level(payload["importance"]),
            "symbols": list(payload["symbols"] or []),
            "markets": list(payload["markets"] or []),
            "accountIds": list(payload["account_ids"] or []),
            "source": payload["source"],
            "sourceUrl": payload["source_url"],
            "notes": payload["notes"],
            "reminderOffsetsMinutes": list(payload["reminder_offsets_minutes"] or []),
            "payload": dict(payload["payload"] or {}),
            "createdAt": payload["created_at"],
            "updatedAt": payload["updated_at"],
        }

    def starts_datetime(self):
        return parse_utc(self.starts_at)

    def reminder_due_at(self, offset_minutes: int) -> str:
        starts = self.starts_datetime()
        if not starts:
            return ""
        return utc_iso(starts - timedelta(minutes=max(0, int(offset_minutes or 0))))

    def active(self) -> bool:
        return str(self.status or "").strip() in ACTIVE_EVENT_STATUSES

    def material_for_reasoning(self) -> bool:
        return self.active() and (self.importance >= 70 or bool(self.symbols))


@dataclass(frozen=True)
class InvestmentCalendarReminder:
    event: InvestmentCalendarEvent
    offset_minutes: int
    due_at: str

    @property
    def reminder_key(self) -> str:
        token = ":".join([
            self.event.event_id,
            self.event.starts_at,
            str(self.offset_minutes),
        ])
        return "investment-calendar:" + hashlib.sha1(token.encode("utf-8")).hexdigest()[:24]

    def to_dict(self) -> Dict[str, object]:
        payload = self.event.to_dict()
        payload.update({
            "offsetMinutes": int(self.offset_minutes or 0),
            "dueAt": self.due_at,
            "reminderKey": self.reminder_key,
        })
        return payload


def due_reminders_for_event(
    event: InvestmentCalendarEvent,
    now_at: datetime = None,
    lookback_minutes: int = 180,
) -> List[InvestmentCalendarReminder]:
    if not event.active():
        return []
    now_at = now_at or datetime.now(timezone.utc)
    if now_at.tzinfo is None:
        now_at = now_at.replace(tzinfo=timezone.utc)
    starts_at = event.starts_datetime()
    if not starts_at:
        return []
    cutoff = now_at.astimezone(timezone.utc) - timedelta(minutes=max(1, int(lookback_minutes or 180)))
    reminders = []
    for offset in event.reminder_offsets_minutes or []:
        due_at = starts_at - timedelta(minutes=max(0, int(offset or 0)))
        if cutoff <= due_at <= now_at.astimezone(timezone.utc):
            reminders.append(InvestmentCalendarReminder(event, int(offset or 0), utc_iso(due_at)))
    return reminders
