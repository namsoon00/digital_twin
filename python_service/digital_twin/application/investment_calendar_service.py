import html
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List
from zoneinfo import ZoneInfo

from ..domain.events import (
    DomainEvent,
    investment_calendar_event_removed_event,
    investment_calendar_event_saved_event,
    investment_calendar_reminder_due_event,
    ontology_reasoning_requested_event,
)
from ..domain.investment_calendar import (
    DEFAULT_EVENT_TIMEZONE,
    EVENT_TYPE_LABELS,
    InvestmentCalendarEvent,
    InvestmentCalendarReminder,
    due_reminders_for_event,
    event_materiality_level,
    event_type_label,
    parse_utc,
    utc_iso,
)
from ..domain.investment_strategy_guidance import event_strategy_guidance, merge_strategy_context, strategy_message_lines
from ..domain.message_types import INVESTMENT_CALENDAR_REMINDER
from ..domain.notifications import NotificationJob
from ..domain.portfolio import utc_now_iso


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int = 1, upper: int = 100000) -> int:
    try:
        parsed = int(float(str((settings or {}).get(key) or "").strip()))
    except ValueError:
        parsed = fallback
    return max(lower, min(upper, parsed))


def clean_text(value: object, limit: int = 500) -> str:
    return " ".join(str(value or "").split()).strip()[:limit].rstrip()


def publish_event(publisher, event: DomainEvent) -> DomainEvent:
    if not publisher:
        return event
    if hasattr(publisher, "publish"):
        publisher.publish(event)
    else:
        publisher.handle(event)
    return event


def kst_text(value: object, timezone_name: str = DEFAULT_EVENT_TIMEZONE) -> str:
    parsed = parse_utc(value)
    if not parsed:
        return clean_text(value)
    zone = ZoneInfo(timezone_name or DEFAULT_EVENT_TIMEZONE)
    return parsed.astimezone(zone).strftime("%Y-%m-%d %H:%M ") + str(timezone_name or DEFAULT_EVENT_TIMEZONE)


def offset_text(offset_minutes: int) -> str:
    minutes = int(offset_minutes or 0)
    if minutes <= 0:
        return "시작 시각"
    if minutes % 1440 == 0:
        return "D-" + str(minutes // 1440)
    if minutes % 60 == 0:
        return "H-" + str(minutes // 60)
    return str(minutes) + "분 전"


def severity_for_importance(importance: int) -> str:
    value = int(importance or 0)
    if value >= 85:
        return "ALERT"
    if value >= 60:
        return "WATCH"
    return "INFO"


def html_line(label: str, value: object) -> str:
    text = clean_text(value, 1000)
    if not text:
        return ""
    return "• <b>" + html.escape(label, quote=False) + "</b>: " + html.escape(text, quote=False)


def unique_delivery_accounts(accounts: Iterable[object]) -> List[object]:
    selected = []
    seen = set()
    for account in accounts or []:
        key = (
            str(getattr(account, "notify_provider", "") or "").lower(),
            getattr(account, "telegram_bot_token", "") or "",
            getattr(account, "telegram_chat_id", "") or "",
        )
        if not key[1] and not key[2]:
            key = ("account", getattr(account, "account_id", "") or "")
        if key in seen:
            continue
        seen.add(key)
        selected.append(account)
    return selected


class InvestmentCalendarService:
    def __init__(
        self,
        repository,
        account_repository=None,
        notification_queue=None,
        settings: Dict[str, object] = None,
        event_publisher=None,
    ):
        self.repository = repository
        self.account_repository = account_repository
        self.notification_queue = notification_queue
        self.settings = dict(settings or {})
        self.event_publisher = event_publisher

    def enabled(self) -> bool:
        return truthy(self.settings.get("investmentCalendarEnabled"), True)

    def reminder_lookback_minutes(self) -> int:
        return int_setting(self.settings, "investmentCalendarReminderLookbackMinutes", 180, 1, 1440)

    def default_window_days(self) -> int:
        return int_setting(self.settings, "investmentCalendarDefaultWindowDays", 45, 1, 366)

    def list_events(self, query: Dict[str, object] = None) -> Dict[str, object]:
        query = query if isinstance(query, dict) else {}
        now = datetime.now(timezone.utc)
        from_at = clean_text(query.get("from") or query.get("fromAt") or "")
        to_at = clean_text(query.get("to") or query.get("toAt") or "")
        if not from_at:
            from_at = utc_iso(now - timedelta(days=7))
        if not to_at:
            to_at = utc_iso(now + timedelta(days=self.default_window_days()))
        limit = int_setting(query, "limit", 200, 1, 500)
        events = self.repository.list(
            from_at=from_at,
            to_at=to_at,
            status=clean_text(query.get("status") or ""),
            symbol=clean_text(query.get("symbol") or "").upper(),
            event_type=clean_text(query.get("eventType") or query.get("event_type") or ""),
            limit=limit,
        )
        return {
            "generatedAt": utc_now_iso(),
            "events": [event.to_dict() for event in events],
            "summary": self.repository.summary(),
            "eventTypes": [{"type": key, "label": label} for key, label in EVENT_TYPE_LABELS.items()],
            "from": from_at,
            "to": to_at,
            "limit": limit,
        }

    def save_event(self, payload: Dict[str, object]) -> Dict[str, object]:
        event = InvestmentCalendarEvent.from_payload(payload if isinstance(payload, dict) else {})
        saved = self.repository.upsert(event)
        source_event = publish_event(self.event_publisher, investment_calendar_event_saved_event(saved))
        if saved.material_for_reasoning() and saved.symbols:
            publish_event(self.event_publisher, ontology_reasoning_requested_event(
                source_event,
                "investment-calendar-update",
                saved.symbols,
                changed_count=len(saved.symbols),
                observed_count=1,
                fact_types=["InvestmentCalendarEvent"],
                reason="투자 캘린더의 종목 연결 이벤트를 온톨로지 ABox 사실로 반영하고 관련 규칙 추론을 갱신합니다.",
                materiality_assessments=[{
                    "eventId": saved.event_id,
                    "eventType": saved.event_type,
                    "importance": saved.importance,
                    "materialityLevel": event_materiality_level(saved.importance),
                    "passed": saved.importance >= 70,
                }],
            ))
        return {"event": saved.to_dict(), "eventId": source_event.event_id}

    def delete_event(self, event_id: str) -> Dict[str, object]:
        normalized = clean_text(event_id, 191)
        if not normalized:
            raise ValueError("eventId는 필요합니다.")
        removed = self.repository.delete(normalized)
        event = publish_event(self.event_publisher, investment_calendar_event_removed_event(normalized))
        return {"removed": bool(removed), "eventId": normalized, "domainEventId": event.event_id}

    def due_reminders(self, now_at: datetime = None) -> List[InvestmentCalendarReminder]:
        now_at = now_at or datetime.now(timezone.utc)
        lookback = self.reminder_lookback_minutes()
        candidates = self.repository.reminder_candidates(now_at=utc_iso(now_at), lookback_minutes=lookback)
        reminders: List[InvestmentCalendarReminder] = []
        for event in candidates:
            reminders.extend(due_reminders_for_event(event, now_at=now_at, lookback_minutes=lookback))
        return reminders

    def accounts_for_event(self, event: InvestmentCalendarEvent) -> List[object]:
        if not self.account_repository:
            return [None]
        accounts = list(self.account_repository.load_all() if hasattr(self.account_repository, "load_all") else self.account_repository.load())
        if event.account_ids:
            selected = [account for account in accounts if getattr(account, "account_id", "") in event.account_ids]
            return selected or [None]
        selected = unique_delivery_accounts([account for account in accounts if getattr(account, "enabled", True)])
        return selected or [None]

    def reminder_context(self, reminder: InvestmentCalendarReminder, account=None) -> Dict[str, object]:
        event = reminder.event
        symbol = event.symbols[0] if event.symbols else ""
        guidance = event_strategy_guidance(event.event_type, event.symbols, event.markets, account=account)
        signals = []
        if event.importance >= 70:
            signals.append("important")
        if event.event_type in {"earnings", "macro", "centralBank", "disclosure", "portfolioReview"}:
            signals.append("actionable")
        if event.symbols or event.markets:
            signals.append("confirmingData")
        context = {
            "messageType": INVESTMENT_CALENDAR_REMINDER,
            "title": "투자 캘린더: " + event.title,
            "body": self.reminder_message(reminder, account),
            "severity": severity_for_importance(event.importance),
            "symbol": symbol,
            "rawSymbol": symbol,
            "symbols": list(event.symbols or []),
            "markets": list(event.markets or []),
            "eventId": event.event_id,
            "eventType": event.event_type,
            "eventTypeLabel": event_type_label(event.event_type),
            "startsAt": event.starts_at,
            "startsAtText": kst_text(event.starts_at, event.timezone),
            "dueAt": reminder.due_at,
            "dueAtText": kst_text(reminder.due_at, event.timezone),
            "reminderOffsetMinutes": reminder.offset_minutes,
            "reminderOffsetText": offset_text(reminder.offset_minutes),
            "importance": event.importance,
            "materialityLevel": event_materiality_level(event.importance),
            "source": event.source,
            "sourceUrl": event.source_url,
            "notes": event.notes,
            "investmentImpact": guidance["impact"],
            "watchItems": list(guidance["watchItems"]),
            "notificationSignals": signals,
            "investmentCalendar": reminder.to_dict(),
            "investmentCalendarGuidance": guidance,
            "accountId": getattr(account, "account_id", "") if account else "",
            "accountLabel": getattr(account, "label", "") if account else "",
        }
        return merge_strategy_context(context, account)

    def reminder_message(self, reminder: InvestmentCalendarReminder, account=None) -> str:
        event = reminder.event
        guidance = event_strategy_guidance(event.event_type, event.symbols, event.markets, account=account)
        lines = [
            "<b>투자 캘린더 알림</b>",
            html_line("이벤트", event.title),
            html_line("유형", event_type_label(event.event_type)),
            html_line("일정", kst_text(event.starts_at, event.timezone)),
            html_line("알림", offset_text(reminder.offset_minutes)),
            html_line("대상", guidance["target"]),
            html_line("중요도", str(event.importance) + "점"),
            html_line("투자 영향", guidance["impact"]),
            html_line("확인할 것", " / ".join(guidance["watchItems"])),
        ]
        if account:
            lines.append(html_line("계정", getattr(account, "label", "") or getattr(account, "account_id", "")))
            lines.extend(html.escape(line, quote=False) for line in strategy_message_lines(merge_strategy_context({}, account)))
        if event.notes:
            lines.append(html_line("메모", event.notes))
        if event.source_url:
            url = html.escape(event.source_url, quote=True)
            lines.append('• <b>출처</b>: <a href="' + url + '">열기</a>')
        return "\n".join(line for line in lines if line)

    def enqueue_due_reminders(self, now_at: datetime = None) -> Dict[str, object]:
        if not self.enabled():
            return {"status": "disabled", "dueCount": 0, "queuedCount": 0}
        reminders = self.due_reminders(now_at)
        if not reminders:
            return {"status": "idle", "dueCount": 0, "queuedCount": 0}
        source_event = publish_event(self.event_publisher, investment_calendar_reminder_due_event(reminders))
        queued = 0
        suppressed = 0
        for reminder in reminders:
            for account in self.accounts_for_event(reminder.event):
                context = self.reminder_context(reminder, account)
                job = NotificationJob.create(
                    context["body"],
                    account_id=context["accountId"],
                    account_label=context["accountLabel"],
                    message_type=INVESTMENT_CALENDAR_REMINDER,
                    source_event_id=source_event.event_id,
                    source_event_name=source_event.name,
                    dedupe_key=":".join([
                        reminder.reminder_key,
                        context["accountId"] or "global",
                    ]),
                    context=context,
                )
                if self.notification_queue and self.notification_queue.enqueue(job):
                    queued += 1
                else:
                    suppressed += 1
        return {
            "status": "ok",
            "dueCount": len(reminders),
            "queuedCount": queued,
            "suppressedCount": suppressed,
            "eventIds": sorted(set(reminder.event.event_id for reminder in reminders)),
        }

    def status(self) -> Dict[str, object]:
        return {
            "enabled": self.enabled(),
            "summary": self.repository.summary(),
            "dueCount": len(self.due_reminders()),
            "reminderLookbackMinutes": self.reminder_lookback_minutes(),
        }


class InvestmentCalendarRunner:
    def __init__(self, service: InvestmentCalendarService, official_sync_service=None):
        self.service = service
        self.official_sync_service = official_sync_service

    def run_once(self) -> Dict[str, object]:
        sync_result = {}
        if self.official_sync_service:
            sync_result = self.official_sync_service.run_due()
        result = self.service.enqueue_due_reminders()
        if sync_result and sync_result.get("status") != "not-due":
            result["officialCalendarSync"] = sync_result
        return result

    def status(self) -> Dict[str, object]:
        result = self.service.status()
        if self.official_sync_service:
            result["officialCalendarSync"] = {
                "enabled": self.official_sync_service.enabled(),
                "due": self.official_sync_service.due(),
                "intervalSeconds": self.official_sync_service.interval_seconds(),
            }
        return result
