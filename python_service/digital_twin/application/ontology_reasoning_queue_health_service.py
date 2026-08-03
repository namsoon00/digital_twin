from datetime import datetime, timezone
from typing import Dict, Tuple

from ..domain.events import (
    ONTOLOGY_REASONING_QUEUE_HEALTH_CHANGED,
    DomainEvent,
    ontology_reasoning_queue_health_changed_event,
)
from ..domain.ontology_reasoning_queue_health import (
    ACTIVE_QUEUE_STATES,
    INCIDENT_QUEUE_STATES,
    evaluate_ontology_reasoning_queue_health,
)
from ..domain.message_types import ONTOLOGY_REASONING_QUEUE
from ..domain.notifications import NotificationJob
from ..domain.data_freshness import parse_datetime


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int, upper: int) -> int:
    try:
        parsed = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        parsed = fallback
    return max(lower, min(upper, parsed))


def utc_now_iso(now: datetime = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class OntologyReasoningQueueHealthService:
    """Persist queue pressure observations and emit bounded operational events."""

    state_key = "queueDelayHealth"

    def __init__(self, store=None, settings: Dict[str, object] = None, now_provider=None):
        self.store = store
        self.settings = dict(settings or {})
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def enabled(self) -> bool:
        value = str(self.settings.get("ontologyReasoningQueueAlertEnabled", "1")).strip().lower()
        return value not in DISABLED_VALUES

    def previous(self) -> Dict[str, object]:
        if not self.store or not hasattr(self.store, "load"):
            return {}
        try:
            payload = self.store.load()
        except Exception:  # noqa: BLE001 - telemetry must not interrupt reasoning.
            return {}
        state = payload.get(self.state_key) if isinstance(payload, dict) else {}
        return dict(state or {}) if isinstance(state, dict) else {}

    def save(self, health: Dict[str, object]) -> None:
        if not self.store or not hasattr(self.store, "load") or not hasattr(self.store, "save"):
            return
        try:
            payload = dict(self.store.load() or {})
            payload[self.state_key] = dict(health or {})
            self.store.save(payload)
        except Exception:  # noqa: BLE001 - queue telemetry remains best-effort.
            return

    def reminder_due(self, previous: Dict[str, object], now: datetime) -> bool:
        last_alert = parse_datetime(previous.get("lastAlertAt"))
        if not last_alert:
            return False
        interval = int_setting(
            self.settings,
            "ontologyReasoningQueueAlertReminderMinutes",
            60,
            5,
            10080,
        )
        return (now - last_alert.astimezone(timezone.utc)).total_seconds() >= interval * 60

    def incident_identifier(self, payload: Dict[str, object], previous: Dict[str, object]) -> str:
        """Keep one delivery record from detection through queue drain.

        The queue observer may move from delayed/critical to draining before
        the outbound job is delivered. A stable incident ID lets the delivery
        worker record that the operator actually saw the original incident.
        """
        existing = str(previous.get("incidentId") or "").strip()
        if existing:
            return existing
        started_at = str(payload.get("firstObservedAt") or payload.get("checkedAt") or "").strip()
        return "ontology-reasoning-queue:" + (started_at or utc_now_iso(self.now_provider()))

    @staticmethod
    def incident_was_open(previous: Dict[str, object]) -> bool:
        return bool(previous.get("incidentOpen")) or str(previous.get("state") or "").strip() in INCIDENT_QUEUE_STATES

    def record_notification_delivery(self, job: NotificationJob, outcome: str, reason: str = "") -> None:
        """Record the outbound result used to decide whether recovery is useful.

        A recovery message is only meaningful after at least one delayed or
        critical queue message reached operations. Suppressed/failed jobs are
        retained as audit metadata but deliberately do not unlock recovery.
        """
        if str(getattr(job, "message_type", "") or "") != ONTOLOGY_REASONING_QUEUE:
            return
        context = getattr(job, "context", {})
        context = dict(context or {}) if isinstance(context, dict) else {}
        health = context.get("queueDelayHealth")
        health = dict(health or {}) if isinstance(health, dict) else {}
        alert_kind = str(health.get("alertKind") or "").strip().lower()
        incident_id = str(health.get("incidentId") or "").strip()
        if not incident_id or alert_kind == "recovered":
            return

        previous = self.previous()
        if str(previous.get("incidentId") or "").strip() != incident_id or not previous.get("incidentOpen"):
            return

        current = self.now_provider()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        stamp = utc_now_iso(current)
        payload = dict(previous)
        payload.update({
            "lastAlertDeliveryState": str(outcome or "unknown"),
            "lastAlertDeliveryAt": stamp,
            "lastAlertDeliveryReason": str(reason or "")[:500],
            "lastAlertJobId": str(getattr(job, "job_id", "") or ""),
        })
        if str(outcome or "").strip().lower() == "done":
            payload.update({
                "activeAlertDeliveredAt": stamp,
                "activeAlertJobId": str(getattr(job, "job_id", "") or ""),
                "activeAlertDeliveryState": "done",
                # Reminders are paced from an actual delivery, never an
                # enqueue time or a suppressed job.
                "lastAlertAt": stamp,
            })
        self.save(payload)

    def observe(
        self,
        snapshot: Dict[str, object],
        previous: Dict[str, object] = None,
        now: datetime = None,
    ) -> Dict[str, object]:
        """Evaluate current queue health without persisting or emitting an event."""
        prior = dict(previous or {}) if isinstance(previous, dict) else self.previous()
        current = now or self.now_provider()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        health = evaluate_ontology_reasoning_queue_health(
            snapshot,
            prior,
            warning_age_minutes=int_setting(self.settings, "ontologyReasoningQueueWarningAgeMinutes", 30, 1, 10080),
            critical_age_minutes=int_setting(self.settings, "ontologyReasoningQueueCriticalAgeMinutes", 90, 1, 10080),
            warning_pending_count=int_setting(self.settings, "ontologyReasoningQueueWarningPendingCount", 100, 1, 100000),
            critical_pending_count=int_setting(self.settings, "ontologyReasoningQueueCriticalPendingCount", 200, 1, 100000),
            warning_overdue_symbols=int_setting(self.settings, "ontologyReasoningQueueWarningOverdueSymbols", 3, 1, 10000),
            critical_overdue_symbols=int_setting(self.settings, "ontologyReasoningQueueCriticalOverdueSymbols", 8, 1, 10000),
            required_consecutive_observations=int_setting(self.settings, "ontologyReasoningQueueConsecutiveObservations", 3, 1, 120),
            stall_minutes=int_setting(self.settings, "ontologyReasoningQueueNoProgressMinutes", 15, 1, 10080),
            now=current,
        )
        return health.to_dict()

    def record(self, snapshot: Dict[str, object]) -> Tuple[Dict[str, object], DomainEvent]:
        previous = self.previous()
        current = self.now_provider()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        payload = self.observe(snapshot, previous=previous, now=current)
        state = str(payload.get("state") or "").strip()
        previous_state = str(previous.get("state") or "").strip()
        active_state = state in ACTIVE_QUEUE_STATES
        incident_open = state in INCIDENT_QUEUE_STATES
        had_incident = self.incident_was_open(previous)
        should_alert = bool(payload.get("alertRequired")) if active_state else False
        alert_kind = "state-changed" if should_alert else ""

        if incident_open:
            incident_started_at = str(
                previous.get("incidentStartedAt")
                or (previous.get("firstObservedAt") if had_incident else payload.get("firstObservedAt"))
                or payload.get("checkedAt")
                or ""
            ).strip()
            previous_peak_state = str(previous.get("incidentHighestState") or "").strip()
            incident_peak_state = (
                "critical"
                if state == "critical" or previous_peak_state == "critical"
                else "delayed"
                if state == "delayed" or previous_peak_state == "delayed"
                else ""
            )
            payload.update({
                "incidentOpen": True,
                "incidentId": self.incident_identifier(payload, previous) if had_incident else (
                    "ontology-reasoning-queue:" + incident_started_at
                ),
                "incidentStartedAt": incident_started_at,
                "incidentHighestState": incident_peak_state,
                "activeAlertDeliveredAt": str(previous.get("activeAlertDeliveredAt") or ""),
                "activeAlertJobId": str(previous.get("activeAlertJobId") or ""),
                "activeAlertDeliveryState": str(previous.get("activeAlertDeliveryState") or ""),
                "lastAlertDeliveryState": str(previous.get("lastAlertDeliveryState") or ""),
                "lastAlertDeliveryAt": str(previous.get("lastAlertDeliveryAt") or ""),
                "lastAlertDeliveryReason": str(previous.get("lastAlertDeliveryReason") or ""),
            })
            if active_state and not should_alert and self.reminder_due(previous, current.astimezone(timezone.utc)):
                should_alert = True
                alert_kind = "reminder"
        elif state == "healthy" and had_incident:
            incident_started_at = str(
                previous.get("incidentStartedAt")
                or previous.get("firstObservedAt")
                or ""
            ).strip()
            incident_started = parse_datetime(incident_started_at)
            duration_minutes = 0
            if incident_started:
                duration_minutes = max(
                    0,
                    int((current.astimezone(timezone.utc) - incident_started.astimezone(timezone.utc)).total_seconds() // 60),
                )
            active_alert_delivered_at = str(previous.get("activeAlertDeliveredAt") or "").strip()
            payload.update({
                "incidentOpen": False,
                "incidentId": str(previous.get("incidentId") or "").strip(),
                "incidentStartedAt": incident_started_at,
                "incidentDurationMinutes": duration_minutes,
                "incidentHighestState": str(previous.get("incidentHighestState") or previous_state or ""),
                "activeAlertDeliveredAt": active_alert_delivered_at,
                "activeAlertJobId": str(previous.get("activeAlertJobId") or ""),
                "activeAlertDeliveryState": str(previous.get("activeAlertDeliveryState") or ""),
            })
            if active_alert_delivered_at:
                should_alert = True
                alert_kind = "recovered"
                payload.update({
                    "recoveredFromState": str(previous.get("incidentHighestState") or previous_state),
                    "recoverySuppressedReason": "",
                })
            else:
                payload["recoverySuppressedReason"] = "active-alert-not-delivered"
        if not self.enabled():
            should_alert = False
            alert_kind = ""
        payload["alertRequired"] = should_alert
        if alert_kind:
            payload["alertKind"] = alert_kind
            payload["lastAlertAttemptAt"] = utc_now_iso(current)
        elif previous.get("lastAlertAt"):
            payload["lastAlertAt"] = str(previous.get("lastAlertAt"))
        self.save(payload)
        event = ontology_reasoning_queue_health_changed_event(payload) if should_alert else None
        return payload, event


class OntologyReasoningQueueHealthNotificationEnqueuer:
    """Create one global operations message per queue-health event."""

    def __init__(self, queue, settings: Dict[str, object] = None):
        self.queue = queue
        self.settings = dict(settings or {})

    def enabled(self) -> bool:
        value = str(self.settings.get("ontologyReasoningQueueAlertEnabled", "1")).strip().lower()
        return value not in DISABLED_VALUES

    def handle(self, event: DomainEvent) -> None:
        if event.name != ONTOLOGY_REASONING_QUEUE_HEALTH_CHANGED or not self.enabled():
            return
        payload = dict(event.payload or {})
        if not payload.get("alertRequired"):
            return
        context = self.context(payload, event)
        job = NotificationJob.create(
            context["readableMessage"],
            account_id="",
            account_label="운영",
            message_type=ONTOLOGY_REASONING_QUEUE,
            source_event_id=event.event_id,
            source_event_name=event.name,
            # Event IDs are stable for one dispatch but differ for the bounded
            # reminder events, so an hourly reminder is not mistaken for the
            # original state-transition notification.
            dedupe_key="ontology-reasoning-queue:" + event.event_id,
            context=context,
        )
        self.queue.enqueue(job)

    def context(self, payload: Dict[str, object], event: DomainEvent) -> Dict[str, object]:
        state = str(payload.get("state") or "unknown")
        previous = str(payload.get("previousState") or "없음")
        recovered = str(payload.get("alertKind") or "") == "recovered"
        labels = {
            "delayed": "지연 감지",
            "critical": "심각 지연",
            "draining": "처리 진행",
            "healthy": "정상 복구",
        }
        title = "온톨로지 추론 요청 대기 " + labels.get(state, "상태 변경")
        oldest = str(payload.get("oldestRequestAt") or "없음")
        age = int(payload.get("oldestRequestAgeMinutes") or 0)
        raw_pending = int(payload.get("rawPendingCount") or 0)
        effective_pending = int(payload.get("effectivePendingCount") or raw_pending)
        overdue_events = int(payload.get("overduePendingEventCount") or 0)
        overdue_symbols = int(payload.get("overduePendingSymbolCount") or 0)
        pending_line = (
            "• 유효 대기: " + str(effective_pending) + "건"
            + " · 대상 종목: " + str(int(payload.get("pendingSymbolCount") or 0)) + "개"
            + " · 대기 한도 초과: 이벤트 " + str(overdue_events) + "건 / 종목 " + str(overdue_symbols) + "개"
        )
        if raw_pending > effective_pending:
            pending_line += " · 원천 이벤트: " + str(raw_pending) + "건(최신 상태로 압축됨)"
        lines = [
            "[운영] " + title,
            "• 상태: " + state + " (이전 " + previous + ")",
            "• 가장 오래된 요청: " + oldest + (" · " + str(age) + "분 대기" if oldest != "없음" else ""),
            pending_line,
            "• 처리 모드: " + str(payload.get("queueMode") or "waiting"),
            "• 이유: " + str(payload.get("reason") or ""),
            "• 확인시각: " + str(payload.get("checkedAt") or event.occurred_at),
        ]
        last_progress_at = str(payload.get("lastProgressAt") or "").strip()
        if last_progress_at:
            progress_age = int(payload.get("progressAgeMinutes") or 0)
            lines.insert(4, "• 최근 처리 진행: " + last_progress_at + " · " + str(progress_age) + "분 전")
        if recovered:
            duration = int(payload.get("incidentDurationMinutes") or 0)
            started_at = str(payload.get("incidentStartedAt") or "").strip()
            recovery_line = "• 해소: " + (str(payload.get("recoveredFromState") or previous) or "지연") + " 상태가 정상 처리로 복구되었습니다."
            if duration:
                recovery_line += " 감지 후 " + str(duration) + "분 지속됐습니다."
            if started_at:
                recovery_line += " 시작: " + started_at
            lines.insert(2, recovery_line)
        if payload.get("fairnessDrainActive"):
            reservation = payload.get("eventFairnessReservation")
            reservation = dict(reservation or {}) if isinstance(reservation, dict) else {}
            if payload.get("eventFairnessReservationActive"):
                lines.insert(
                    5,
                    "• 우선 처리: 오래된 이벤트 예약 슬롯 처리 중"
                    + (" · " + str(reservation.get("symbol") or "") if reservation.get("symbol") else ""),
                )
            else:
                lines.insert(5, "• 우선 처리: 대기 한도 초과 종목을 먼저 비우는 중")
        if payload.get("backpressureActive"):
            lines.insert(5, "• 처리 조절: 이전 실행 시간이 길어 처리 간격을 늘린 상태")
        if int(payload.get("retryAfterSeconds") or 0) > 0:
            lines.insert(5, "• 다음 재시도: 약 " + str(int(payload.get("retryAfterSeconds") or 0)) + "초 후")
        text = "\n".join(lines)
        return {
            "messageType": ONTOLOGY_REASONING_QUEUE,
            "accountId": "",
            "accountLabel": "운영",
            "deliveryAudience": "operations",
            "deliveryChannel": "operationsTelegram",
            "displayTarget": "온톨로지 추론 대기열",
            "title": title,
            "rawTitle": title,
            "body": text,
            "readableMessage": text,
            "telegramMessage": text,
            "rawLines": text,
            "queueDelayHealth": payload,
            "eventGeneratedAt": event.occurred_at,
            "notificationSignals": ["operations", "queueDelayRecovered" if recovered else "queueDelay", "actionable"],
            "criteria": ["가장 오래된 요청 대기 시간", "대기 요청·종목 수", "대기 한도 초과 이벤트·종목", "추론 실행 차단 상태"],
        }
