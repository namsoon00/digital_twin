from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict

from .data_freshness import parse_datetime


ACTIVE_QUEUE_STATES = {"delayed", "critical"}


def integer(value: object) -> int:
    try:
        return max(0, int(float(str(value or "0"))))
    except (TypeError, ValueError):
        return 0


def value_dict(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, dict) else {}


def elapsed_minutes(timestamp: object, now: datetime) -> int:
    parsed = parse_datetime(timestamp)
    if not parsed:
        return 0
    return max(0, int((now - parsed.astimezone(timezone.utc)).total_seconds() // 60))


@dataclass(frozen=True)
class OntologyReasoningQueueHealth:
    state: str
    candidate_state: str
    reason_code: str
    reason: str
    checked_at: str
    state_since: str
    first_observed_at: str
    oldest_request_at: str
    oldest_request_age_minutes: int
    raw_pending_count: int
    mailbox_pending_entry_count: int
    pending_symbol_count: int
    overdue_pending_symbol_count: int
    queue_mode: str
    fairness_drain_active: bool
    backpressure_active: bool
    blocked: bool
    consecutive_delayed_observations: int
    required_consecutive_observations: int
    retry_after_seconds: int
    previous_state: str = ""
    state_changed: bool = False
    alert_required: bool = False

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "state": payload["state"],
            "candidateState": payload["candidate_state"],
            "reasonCode": payload["reason_code"],
            "reason": payload["reason"],
            "checkedAt": payload["checked_at"],
            "stateSince": payload["state_since"],
            "firstObservedAt": payload["first_observed_at"],
            "oldestRequestAt": payload["oldest_request_at"],
            "oldestRequestAgeMinutes": payload["oldest_request_age_minutes"],
            "rawPendingCount": payload["raw_pending_count"],
            "mailboxPendingEntryCount": payload["mailbox_pending_entry_count"],
            "pendingSymbolCount": payload["pending_symbol_count"],
            "overduePendingSymbolCount": payload["overdue_pending_symbol_count"],
            "queueMode": payload["queue_mode"],
            "fairnessDrainActive": payload["fairness_drain_active"],
            "backpressureActive": payload["backpressure_active"],
            "blocked": payload["blocked"],
            "consecutiveDelayedObservations": payload["consecutive_delayed_observations"],
            "requiredConsecutiveObservations": payload["required_consecutive_observations"],
            "retryAfterSeconds": payload["retry_after_seconds"],
            "previousState": payload["previous_state"],
            "stateChanged": payload["state_changed"],
            "alertRequired": payload["alert_required"],
        }


def evaluate_ontology_reasoning_queue_health(
    snapshot: Dict[str, object],
    previous: Dict[str, object] = None,
    warning_age_minutes: int = 30,
    critical_age_minutes: int = 90,
    warning_pending_count: int = 100,
    critical_pending_count: int = 200,
    warning_overdue_symbols: int = 3,
    critical_overdue_symbols: int = 8,
    required_consecutive_observations: int = 3,
    now: datetime = None,
) -> OntologyReasoningQueueHealth:
    """Classify scheduler pressure without changing its scheduling policy.

    Request age is the primary signal. Counts are only a secondary signal so
    a short-lived burst does not page operations by itself.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    checked_at = current.isoformat().replace("+00:00", "Z")
    source = value_dict(snapshot)
    previous = value_dict(previous)
    dispatch = value_dict(source.get("queueDispatch"))
    queue_health = value_dict(source.get("queueHealth"))
    mailbox = value_dict(source.get("mailbox"))

    result_status = str(source.get("status") or "").strip().lower()
    enabled_value = source.get("enabled")
    enabled = result_status != "disabled" and enabled_value is not False
    oldest_request_at = str(
        dispatch.get("oldestRequestAt")
        or source.get("oldestRequestAt")
        or ""
    ).strip()
    oldest_request_age_minutes = elapsed_minutes(oldest_request_at, current)
    raw_pending_count = integer(source.get("rawRequestCount") or source.get("rawPendingCount"))
    mailbox_pending_entry_count = integer(
        source.get("mailboxPendingEntryCount")
        or mailbox.get("pendingEntryCount")
    )
    pending_symbols = source.get("pendingSymbols")
    pending_symbol_count = integer(dispatch.get("pendingSymbolCount") or source.get("pendingSymbolCount"))
    if not pending_symbol_count and isinstance(pending_symbols, (list, tuple, set)):
        pending_symbol_count = len({str(item or "").strip() for item in pending_symbols if str(item or "").strip()})
    overdue_pending_symbol_count = integer(
        dispatch.get("overduePendingSymbolCount")
        or source.get("overduePendingSymbolCount")
    )
    queue_mode = str(dispatch.get("mode") or source.get("queueMode") or "waiting").strip() or "waiting"
    fairness_drain_active = bool(dispatch.get("fairnessDrainActive") or source.get("fairnessDrainActive"))
    backpressure_active = bool(dispatch.get("backpressureActive") or source.get("backpressureActive"))
    retry_after_seconds = integer(source.get("retryAfterSeconds"))
    blocked = bool(
        str(queue_health.get("status") or "").strip().lower() == "blocked"
        or result_status == "circuit-open"
        or value_dict(source.get("storageGuard")).get("ready") is False
        or value_dict(source.get("executionTimeoutGuard")).get("status") == "open"
    )
    deferred_reason = str(source.get("deferredReason") or queue_health.get("reason") or "").strip()

    warning_age_minutes = max(1, int(warning_age_minutes or 1))
    critical_age_minutes = max(warning_age_minutes, int(critical_age_minutes or warning_age_minutes))
    warning_pending_count = max(1, int(warning_pending_count or 1))
    critical_pending_count = max(warning_pending_count, int(critical_pending_count or warning_pending_count))
    warning_overdue_symbols = max(1, int(warning_overdue_symbols or 1))
    critical_overdue_symbols = max(warning_overdue_symbols, int(critical_overdue_symbols or warning_overdue_symbols))
    required_consecutive_observations = max(1, int(required_consecutive_observations or 1))

    if not enabled:
        candidate_state = "disabled"
        reason_code = "reasoning-disabled"
        reason = "데이터 변경 추론이 비활성화되어 대기열 지연을 감시하지 않습니다."
    elif blocked:
        candidate_state = "critical"
        reason_code = "queue-blocked"
        reason = deferred_reason or "추론 실행이 차단되어 대기 요청을 처리할 수 없습니다."
    elif oldest_request_age_minutes >= critical_age_minutes:
        candidate_state = "critical"
        reason_code = "oldest-request-critical"
        reason = "가장 오래된 추론 요청이 심각 지연 기준을 넘었습니다."
    elif overdue_pending_symbol_count >= critical_overdue_symbols:
        candidate_state = "critical"
        reason_code = "overdue-symbols-critical"
        reason = "대기 한도를 넘긴 종목 수가 심각 기준을 넘었습니다."
    elif raw_pending_count >= critical_pending_count and pending_symbol_count >= 2:
        candidate_state = "critical"
        reason_code = "request-count-critical"
        reason = "처리 대기 요청과 대상 종목 수가 심각 기준을 넘었습니다."
    elif oldest_request_age_minutes >= warning_age_minutes:
        candidate_state = "delayed"
        reason_code = "oldest-request-delayed"
        reason = "가장 오래된 추론 요청이 지연 기준을 넘었습니다."
    elif overdue_pending_symbol_count >= warning_overdue_symbols:
        candidate_state = "delayed"
        reason_code = "overdue-symbols-delayed"
        reason = "대기 한도를 넘긴 종목이 누적되고 있습니다."
    elif raw_pending_count >= warning_pending_count and pending_symbol_count >= 2:
        candidate_state = "delayed"
        reason_code = "request-count-delayed"
        reason = "처리 대기 요청과 대상 종목 수가 지연 기준을 넘었습니다."
    else:
        candidate_state = "healthy"
        reason_code = "queue-healthy"
        reason = "추론 요청이 설정한 지연 기준 안에서 처리되고 있습니다."

    previous_state = str(previous.get("state") or "").strip()
    previous_candidate = str(previous.get("candidateState") or previous_state or "").strip()
    previous_streak = integer(previous.get("consecutiveDelayedObservations"))
    if candidate_state in ACTIVE_QUEUE_STATES:
        consecutive = previous_streak + 1 if previous_candidate in ACTIVE_QUEUE_STATES else 1
        # An old request, an overdue symbol, or a blocked executor needs an
        # immediate operator signal. A request-count burst still needs repeat
        # observations because it may be coalesced away on the next turn.
        immediate_critical = reason_code in {
            "queue-blocked",
            "oldest-request-critical",
            "overdue-symbols-critical",
        }
        confirmed = immediate_critical or consecutive >= required_consecutive_observations
        state = candidate_state if confirmed else "healthy"
        first_observed_at = (
            str(previous.get("firstObservedAt") or checked_at)
            if previous_candidate in ACTIVE_QUEUE_STATES
            else checked_at
        )
        if not confirmed:
            reason += " 연속 관측 " + str(consecutive) + "/" + str(required_consecutive_observations) + "회로 확인 중입니다."
    else:
        consecutive = 0
        state = candidate_state
        first_observed_at = checked_at

    state_changed = state != previous_state
    state_since = checked_at if state_changed else str(previous.get("stateSince") or checked_at)
    alert_required = state_changed and (state in ACTIVE_QUEUE_STATES or previous_state in ACTIVE_QUEUE_STATES)
    return OntologyReasoningQueueHealth(
        state=state,
        candidate_state=candidate_state,
        reason_code=reason_code,
        reason=reason,
        checked_at=checked_at,
        state_since=state_since,
        first_observed_at=first_observed_at,
        oldest_request_at=oldest_request_at,
        oldest_request_age_minutes=oldest_request_age_minutes,
        raw_pending_count=raw_pending_count,
        mailbox_pending_entry_count=mailbox_pending_entry_count,
        pending_symbol_count=pending_symbol_count,
        overdue_pending_symbol_count=overdue_pending_symbol_count,
        queue_mode=queue_mode,
        fairness_drain_active=fairness_drain_active,
        backpressure_active=backpressure_active,
        blocked=blocked,
        consecutive_delayed_observations=consecutive,
        required_consecutive_observations=required_consecutive_observations,
        retry_after_seconds=retry_after_seconds,
        previous_state=previous_state,
        state_changed=state_changed,
        alert_required=alert_required,
    )
