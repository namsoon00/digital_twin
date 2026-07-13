import html
import signal
import time
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


def _target_text(event: InvestmentCalendarEvent) -> str:
    return ", ".join(event.symbols or event.markets or ["전체 포트폴리오"])


def _symbol_watch_items(symbols: Iterable[str]) -> List[str]:
    items = []
    for symbol in [str(item or "").upper() for item in symbols or []]:
        if symbol == "TSLA":
            items.extend(["자동차 총마진/ASP", "에너지 저장 성장", "FSD·로보택시 일정 코멘트"])
        elif symbol == "AAPL":
            items.extend(["iPhone 및 서비스 매출", "중국 매출 흐름", "AI/칩 비용과 자사주 매입"])
        elif symbol == "NVDA":
            items.extend(["데이터센터 매출과 수주", "차세대 GPU 공급 제약", "총마진과 다음 분기 가이던스"])
    unique = []
    for item in items:
        if item not in unique:
            unique.append(item)
    return unique


def calendar_investment_guidance(event: InvestmentCalendarEvent) -> Dict[str, object]:
    target = _target_text(event)
    event_type = str(event.event_type or "")
    impact = "이벤트 결과가 " + target + "의 변동성, 뉴스 흐름, 포트폴리오 리스크 점검 우선순위에 영향을 줄 수 있습니다."
    watch_items = ["예상치 대비 실제 결과", "발표 직후 가격·거래량 반응", "기존 투자 가정과 달라진 점"]

    if event_type == "macro":
        impact = (
            "물가·성장·고용 지표는 금리 기대와 달러, 미국 장기금리를 통해 성장주 밸류에이션과 "
            + target
            + "의 단기 변동성에 영향을 줄 수 있습니다."
        )
        watch_items = ["컨센서스 대비 헤드라인/핵심 지표", "미국 2년·10년물 금리와 달러 반응", "프리마켓 및 장초반 거래량 변화"]
    elif event_type == "centralBank":
        impact = (
            "중앙은행 결정은 할인율과 위험선호를 바꾸며 "
            + target
            + "의 멀티플, 기술주 수급, 환율 민감도에 직접 영향을 줄 수 있습니다."
        )
        watch_items = ["성명서의 완화/긴축 톤 변화", "기자회견의 다음 회의 힌트", "2년물 금리·나스닥 선물·달러 동시 반응"]
    elif event_type == "earnings":
        impact = (
            "실적 이벤트는 "
            + target
            + "의 이익 추정치, 밸류에이션 프리미엄, 다음 분기 가이던스 재평가로 이어질 수 있습니다."
        )
        watch_items = ["매출/EPS의 시장 기대 대비 차이", "마진과 비용 구조", "다음 분기 또는 연간 가이던스"]
        watch_items.extend(_symbol_watch_items(event.symbols))
    elif event_type == "dividend":
        impact = "배당·권리 일정은 현금흐름, 배당락 가격 조정, 세후 수익률 점검에 영향을 줄 수 있습니다."
        watch_items = ["배당락일과 지급일", "예상 배당수익률", "배당락 이후 가격 회복 여부"]
    elif event_type == "disclosure":
        impact = "공시 이벤트는 기존 투자 가정, 리스크 요인, 단기 수급 판단에 영향을 줄 수 있습니다."
        watch_items = ["공시의 재무 영향", "일회성/반복성 여부", "시장 반응과 후속 정정 공시"]
    elif event_type == "shareholderMeeting":
        impact = "주주총회는 지배구조, 자본정책, 경영진 메시지 변화가 투자 심리에 영향을 줄 수 있습니다."
        watch_items = ["자본정책 변화", "사업전략 발언", "주주환원 및 이사회 안건"]
    elif event_type == "lockup":
        impact = "락업 해제는 잠재 매도 물량과 단기 수급 부담을 키울 수 있습니다."
        watch_items = ["해제 주식 수와 유통주식 대비 비율", "주요 보유자 매각 가능성", "거래량 급증 여부"]
    elif event_type == "portfolioReview":
        impact = "포트폴리오 점검 일정은 보유 비중, 리스크 노출, 현금 여력을 재확인하는 운영 기준점입니다."
        watch_items = ["종목별 비중 변화", "상관관계가 높은 노출", "현금·손절·추가매수 기준의 최신성"]

    return {
        "impact": clean_text(impact, 700),
        "watchItems": watch_items[:6],
    }


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
        guidance = calendar_investment_guidance(event)
        signals = []
        if event.importance >= 70:
            signals.append("important")
        if event.event_type in {"earnings", "macro", "centralBank", "disclosure", "portfolioReview"}:
            signals.append("actionable")
        if event.symbols or event.markets:
            signals.append("confirmingData")
        return {
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

    def reminder_message(self, reminder: InvestmentCalendarReminder, account=None) -> str:
        event = reminder.event
        target = _target_text(event)
        guidance = calendar_investment_guidance(event)
        lines = [
            "<b>투자 캘린더 알림</b>",
            html_line("이벤트", event.title),
            html_line("유형", event_type_label(event.event_type)),
            html_line("일정", kst_text(event.starts_at, event.timezone)),
            html_line("알림", offset_text(reminder.offset_minutes)),
            html_line("대상", target),
            html_line("중요도", str(event.importance) + "점"),
            html_line("투자 영향", guidance["impact"]),
            html_line("확인할 것", " / ".join(guidance["watchItems"])),
        ]
        if account:
            lines.append(html_line("계정", getattr(account, "label", "") or getattr(account, "account_id", "")))
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
    def __init__(self, service: InvestmentCalendarService):
        self.service = service

    def run_once(self) -> Dict[str, object]:
        return self.service.enqueue_due_reminders()

    def status(self) -> Dict[str, object]:
        return self.service.status()


class InvestmentCalendarScheduler:
    def __init__(self, runner: InvestmentCalendarRunner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(30, int(interval_seconds or 60))
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        print("Python investment calendar worker started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                result = self.runner.run_once()
                if result.get("dueCount") or result.get("queuedCount"):
                    print(
                        "Investment calendar "
                        + str(result.get("status"))
                        + " due="
                        + str(result.get("dueCount", 0))
                        + " queued="
                        + str(result.get("queuedCount", 0))
                    )
            except Exception as error:  # noqa: BLE001 - long-running calendar worker must continue after one cycle failure.
                print("Python investment calendar worker error: " + str(error))
            elapsed = time.monotonic() - started
            sleep_seconds = max(1.0, self.interval_seconds - elapsed)
            end_at = time.monotonic() + sleep_seconds
            while self.running and time.monotonic() < end_at:
                time.sleep(min(1.0, end_at - time.monotonic()))
