"""Reconcile material-event alert coverage and emit bounded operations events."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time
from typing import Dict, Mapping, Tuple

from ..domain.events import (
    INVESTMENT_ALERT_COVERAGE_CHANGED,
    DomainEvent,
    investment_alert_coverage_changed_event,
)
from ..domain.message_types import INVESTMENT_ALERT_COVERAGE
from ..domain.notifications import NotificationJob


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _int_setting(settings: Mapping[str, object], key: str, fallback: int, lower: int, upper: int) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(lower, min(upper, value))


def _parse_time(value: object):
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    current = value
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class InvestmentAlertCoverageService:
    """Own the operational SLO, never investment action semantics."""

    def __init__(
        self,
        store,
        deployment_provider,
        settings: Mapping[str, object] = None,
        now_provider=None,
        monotonic_provider=None,
    ):
        self.store = store
        self.deployment_provider = deployment_provider
        self.settings = dict(settings or {})
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.monotonic_provider = monotonic_provider or time.monotonic
        self.last_run_monotonic = 0.0
        self.last_result: Dict[str, object] = {"status": "not-run"}

    def enabled(self) -> bool:
        value = _text(self.settings.get("investmentAlertCoverageEnabled") or "1").lower()
        return value not in DISABLED_VALUES

    def deployment_id(self) -> str:
        value = self.deployment_provider() if callable(self.deployment_provider) else self.deployment_provider
        if isinstance(value, Mapping):
            return _text(
                value.get("deliveryDeploymentId")
                or value.get("delivery_deployment_id")
                or value.get("activeDeploymentId")
                or value.get("active_deployment_id")
            )
        return _text(value)

    def run_once(self, force: bool = False) -> Tuple[Dict[str, object], DomainEvent]:
        if not self.enabled():
            self.last_result = {"status": "disabled", "alertRequired": False}
            return dict(self.last_result), None
        interval = _int_setting(
            self.settings,
            "investmentAlertCoverageReconcileSeconds",
            60,
            10,
            3600,
        )
        monotonic_now = self.monotonic_provider()
        if (
            not force
            and self.last_run_monotonic
            and monotonic_now - self.last_run_monotonic < interval
        ):
            return {**self.last_result, "status": "interval-skipped"}, None
        self.last_run_monotonic = monotonic_now
        deployment_id = self.deployment_id()
        if not deployment_id:
            self.last_result = {
                "status": "deployment-unavailable",
                "alertRequired": False,
            }
            return dict(self.last_result), None
        current = self.now_provider()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        result = dict(self.store.reconcile(
            deployment_id,
            lookback_hours=_int_setting(
                self.settings, "investmentAlertCoverageLookbackHours", 24, 1, 168
            ),
            deadline_seconds=_int_setting(
                self.settings, "investmentAlertCoverageDeadlineSeconds", 300, 30, 7200
            ),
            starvation_min_candidates=_int_setting(
                self.settings, "investmentAlertCoverageStarvationMinCandidates", 8, 2, 1000
            ),
            now=current,
        ) or {})
        health = dict(result.get("health") or {})
        state = _text(health.get("state") or "unknown").lower()
        previous = dict(self.store.load_health_state(deployment_id) or {})
        previous_state = _text(previous.get("state") or "healthy").lower()
        previous_incident_open = bool(previous.get("incidentOpen"))
        previous_alerted = bool(_text(previous.get("lastAlertAt")))
        if state == "healthy":
            consecutive = 0
        elif state == previous_state:
            consecutive = int(previous.get("consecutiveObservations") or 0) + 1
        else:
            consecutive = 1
        required = _int_setting(
            self.settings,
            "investmentAlertCoverageConsecutiveObservations",
            3,
            1,
            30,
        )
        immediate = state == "critical" and int(health.get("failedEventCount") or 0) > 0
        incident_open = state in {"warning", "critical"}
        incident_id = _text(previous.get("incidentId")) if previous_incident_open else ""
        if incident_open and not incident_id:
            incident_id = "investment-alert-coverage:" + deployment_id + ":" + _utc_iso(current)
        alert_required = False
        alert_kind = ""
        if incident_open and (immediate or consecutive >= required):
            if not previous_alerted:
                alert_required = True
                alert_kind = "incident-start"
            elif state != previous_state:
                alert_required = True
                alert_kind = "state-changed"
            else:
                last_alert = _parse_time(previous.get("lastAlertAt"))
                reminder_minutes = _int_setting(
                    self.settings,
                    "investmentAlertCoverageReminderMinutes",
                    360,
                    30,
                    10080,
                )
                if last_alert and current - last_alert >= timedelta(minutes=reminder_minutes):
                    alert_required = True
                    alert_kind = "reminder"
        elif state == "healthy" and previous_incident_open:
            alert_required = True
            alert_kind = "recovered"
            incident_open = False

        payload = {
            **health,
            "status": str(result.get("status") or "ok"),
            "deploymentId": deployment_id,
            "previousState": previous_state,
            "incidentOpen": incident_open,
            "incidentId": incident_id,
            "consecutiveObservations": consecutive,
            "requiredConsecutiveObservations": required,
            "alertRequired": alert_required,
            "alertKind": alert_kind,
            "checkedAt": _utc_iso(current),
            "stateCounts": dict(result.get("stateCounts") or {}),
            "recordCount": int(result.get("recordCount") or 0),
        }
        if alert_required:
            payload["lastAlertAt"] = _utc_iso(current)
        elif previous.get("lastAlertAt"):
            payload["lastAlertAt"] = _text(previous.get("lastAlertAt"))
        self.store.save_health_state(deployment_id, payload)
        self.last_result = dict(payload)
        event = investment_alert_coverage_changed_event(payload) if alert_required else None
        return payload, event


class InvestmentAlertCoverageNotificationEnqueuer:
    def __init__(self, queue):
        self.queue = queue

    def handle(self, event: DomainEvent) -> None:
        if not event or event.name != INVESTMENT_ALERT_COVERAGE_CHANGED:
            return
        payload = dict(event.payload or {})
        if not payload.get("alertRequired"):
            return
        recovered = _text(payload.get("alertKind")) == "recovered"
        state = _text(payload.get("state") or "unknown")
        title = "투자 알림 커버리지 정상 복구" if recovered else "투자 알림 커버리지 점검 필요"
        lines = [
            "<b>" + ("✅" if recovered else "⚠️") + " 운영 알림 · " + title + "</b>",
            "",
            "• 상태: " + state + " (이전 " + _text(payload.get("previousState") or "없음") + ")",
            "• 중요 사건: " + str(int(payload.get("materialEventCount") or 0)) + "건",
            "• 종료 확인: " + str(int(payload.get("terminalEventCount") or 0)) + "건 · "
            + str(payload.get("terminalCoveragePct") or 0) + "%",
            "• 제한시간 초과: " + str(int(payload.get("overdueEventCount") or 0)) + "건",
            "• 처리 실패: " + str(int(payload.get("failedEventCount") or 0)) + "건",
            "• 판단 후보: " + str(int(payload.get("candidateEventCount") or 0))
            + "건 · 전달 " + str(int(payload.get("deliveredCandidateCount") or 0)) + "건",
            "• 이유: " + _text(payload.get("reason")),
            "• 확인시각: " + _text(payload.get("checkedAt")),
        ]
        if payload.get("policyStarvation"):
            lines.insert(-2, "• 정책 점검: 중요 후보가 연속 종료됐지만 사용자 전달 결과가 없습니다.")
        context = {
            **payload,
            "title": title,
            "body": "\n".join(lines),
            "readableMessage": "\n".join(lines),
            "severity": "INFO" if recovered else "ALERT",
            "operationsOnly": True,
        }
        incident_id = _text(payload.get("incidentId") or event.event_id)
        dedupe_key = "investment-alert-coverage:" + incident_id + ":" + _text(payload.get("alertKind"))
        self.queue.enqueue(NotificationJob.create(
            context["readableMessage"],
            account_id="",
            account_label="운영",
            message_type=INVESTMENT_ALERT_COVERAGE,
            source_event_id=event.event_id,
            source_event_name=event.name,
            dedupe_key=dedupe_key,
            context=context,
        ))
