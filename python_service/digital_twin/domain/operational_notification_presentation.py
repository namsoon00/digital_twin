"""Status-first presentation rules for operations notifications."""

from dataclasses import dataclass
from typing import Dict, Iterable, Optional

from .message_types import (
    EXTERNAL_DATA_CONNECTION,
    MONITOR_CONNECTION,
    MONITOR_HEARTBEAT,
    ONTOLOGY_INFERENCE_MISSING,
    ONTOLOGY_REASONING_QUEUE,
    OPERATIONAL_STORAGE_CAPACITY,
    OPERATOR_REASONING_REPORT,
    WORK_HANDOFF,
    is_operations_delivery_message_type,
)


SYSTEM_ERROR = "systemError"
HEALTHY_STATES = {"healthy", "idle", "ok", "normal", "recovered"}
FAILED_STATES = {"failed", "critical", "blocked", "error", "unhealthy"}
DEGRADED_STATES = {"degraded", "stale", "warning", "warn", "retrying", "circuit-open"}
PREVIOUS_ALERT_STATES = FAILED_STATES | DEGRADED_STATES | {"delayed"}
AUTH_HTTP_STATUS_CODES = {401, 403}
RATE_LIMIT_HTTP_STATUS_CODES = {429}
AUTH_MARKERS = (
    "unauthorized",
    "forbidden",
    "authentication",
    "authorization",
    "invalid token",
    "token expired",
    "access denied",
    "allowlisted ip",
    "ip allowlist",
    "not allowed ip",
    "인증 실패",
    "권한",
    "허용되지 않은 ip",
)
RATE_LIMIT_MARKERS = (
    "too many requests",
    "rate limit",
    "rate-limit",
    "throttl",
    "호출 제한",
)
FAILURE_MARKERS = (
    " failed",
    " failure",
    " error",
    " unavailable",
    " timeout",
    "exception",
    "실패",
    "오류",
    "중단",
)


@dataclass(frozen=True)
class OperationalNotificationPresentation:
    icon: str
    tone: str
    state: str

    @property
    def badge(self) -> str:
        return self.icon + " 운영 알림"

    def to_context(self) -> Dict[str, object]:
        return {
            "operationalIcon": self.icon,
            "operationalTone": self.tone,
            "operationalState": self.state,
            "operationalBadge": self.badge,
        }


def value_dict(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, dict) else {}


def message_type_from(message_type: object, context: Dict[str, object]) -> str:
    return str(
        message_type
        or context.get("messageType")
        or context.get("message_type")
        or context.get("rule")
        or ""
    ).strip()


def context_layers(context: Dict[str, object]) -> Iterable[Dict[str, object]]:
    values = value_dict(context)
    yield values
    for key in ["pipelineHealth", "queueDelayHealth", "metadata", "error", "connection"]:
        nested = value_dict(values.get(key))
        if nested:
            yield nested


def first_value(context: Dict[str, object], *keys: str) -> object:
    for layer in context_layers(context):
        for key in keys:
            value = layer.get(key)
            if value not in (None, "", [], {}):
                return value
    return ""


def all_text_values(context: Dict[str, object]) -> str:
    values = []
    keys = [
        "state",
        "apiStatus",
        "reasonCode",
        "observedReasonCode",
        "errorCode",
        "errorStatus",
        "statusCode",
        "httpStatus",
        "errorType",
        "message",
        "errorMessage",
        "reason",
        "detail",
        "title",
        "rawLines",
        "connectionIssues",
    ]
    for layer in context_layers(context):
        for key in keys:
            value = layer.get(key)
            if isinstance(value, (list, tuple, set)):
                values.extend(str(item or "") for item in value)
            elif value not in (None, "", {}, []):
                values.append(str(value))
    return " ".join(item for item in values if item).lower()


def status_codes(context: Dict[str, object]) -> set:
    codes = set()
    for key in ["errorStatus", "statusCode", "httpStatus", "status"]:
        value = first_value(context, key)
        try:
            code = int(float(str(value)))
        except (TypeError, ValueError):
            continue
        if 100 <= code <= 599:
            codes.add(code)
    text = all_text_values(context)
    for code in [401, 403, 429]:
        if str(code) in text:
            codes.add(code)
    return codes


def contains_any(text: str, markers: Iterable[str]) -> bool:
    return any(marker in text for marker in markers)


def is_authentication_issue(context: Dict[str, object]) -> bool:
    return bool(status_codes(context) & AUTH_HTTP_STATUS_CODES) or contains_any(all_text_values(context), AUTH_MARKERS)


def is_rate_limited(context: Dict[str, object]) -> bool:
    return bool(status_codes(context) & RATE_LIMIT_HTTP_STATUS_CODES) or contains_any(all_text_values(context), RATE_LIMIT_MARKERS)


def normalized_state(context: Dict[str, object]) -> str:
    return str(first_value(context, "state", "apiStatus", "status") or "").strip().lower()


def previous_state(context: Dict[str, object]) -> str:
    return str(first_value(context, "previousState", "recoveredFromState") or "").strip().lower()


def is_recovery(context: Dict[str, object], state: str) -> bool:
    alert_kind = str(first_value(context, "alertKind") or "").strip().lower()
    signals = first_value(context, "notificationSignals")
    signal_values = {str(item or "").strip() for item in signals} if isinstance(signals, (list, tuple, set)) else set()
    return (
        alert_kind == "recovered"
        or bool({"connectionRecovered", "queueDelayRecovered"} & signal_values)
        or (state in HEALTHY_STATES and previous_state(context) in PREVIOUS_ALERT_STATES)
    )


def current_connection_text(context: Dict[str, object]) -> str:
    raw_lines = first_value(context, "rawLines")
    if isinstance(raw_lines, str):
        lines = raw_lines.splitlines()
    elif isinstance(raw_lines, (list, tuple, set)):
        lines = [str(line or "") for line in raw_lines]
    else:
        return ""
    for line in lines:
        text = line.strip().lstrip("-• ").strip()
        lowered = text.lower()
        if lowered.startswith("current "):
            return text[8:].strip().lower()
        if text.startswith("현재 "):
            return text[3:].strip().lower()
    return ""


def presentation(icon: str, tone: str, state: str) -> OperationalNotificationPresentation:
    return OperationalNotificationPresentation(icon=icon, tone=tone, state=state)


def operational_notification_presentation(
    message_type: object = "",
    context: Dict[str, object] = None,
) -> Optional[OperationalNotificationPresentation]:
    """Return one deterministic icon for an operations message, if applicable.

    Structured state fields are authoritative. Text markers only preserve
    useful classification for legacy connection and exception payloads that do
    not yet expose an HTTP status or failure category.
    """
    values = value_dict(context)
    key = message_type_from(message_type, values)
    operations_context = str(values.get("deliveryAudience") or "").strip().lower() == "operations"
    if key != SYSTEM_ERROR and not is_operations_delivery_message_type(key) and not operations_context:
        return None

    if key == WORK_HANDOFF:
        return presentation("📦", "handoff", "completed")
    if key == OPERATOR_REASONING_REPORT:
        return presentation("🛠️", "maintenance", "report")
    if key == SYSTEM_ERROR:
        if is_authentication_issue(values):
            return presentation("🔐", "authentication", "authentication-error")
        if is_rate_limited(values):
            return presentation("⚠️", "degraded", "rate-limited")
        return presentation("🚨", "critical", "failed")

    if key == ONTOLOGY_REASONING_QUEUE:
        state = normalized_state(values)
        reason_code = str(first_value(values, "reasonCode") or "").strip().lower()
        if is_recovery(values, state):
            return presentation("✅", "recovered", "recovered")
        if state == "critical" or reason_code == "queue-blocked":
            return presentation("🚨", "critical", state or "critical")
        if state == "delayed":
            return presentation("⏳", "delayed", state)
        if state == "disabled":
            return presentation("ℹ️", "info", state)
        return presentation("ℹ️", "info", state or "unknown")

    if key == OPERATIONAL_STORAGE_CAPACITY:
        state = normalized_state(values)
        if is_recovery(values, state):
            return presentation("✅", "recovered", "recovered")
        if state == "critical":
            return presentation("🚨", "critical", state)
        if state in {"limited", "warning"}:
            return presentation("⚠️", "degraded", state)
        return presentation("💾", "info", state or "healthy")

    if key == ONTOLOGY_INFERENCE_MISSING:
        return presentation("⚠️", "degraded", "inference-missing")

    if key == MONITOR_HEARTBEAT:
        state = normalized_state(values)
        if state in FAILED_STATES or contains_any(all_text_values(values), FAILURE_MARKERS):
            return presentation("🚨", "critical", state or "failed")
        return presentation("💓", "info", state or "healthy")

    if key in {EXTERNAL_DATA_CONNECTION, MONITOR_CONNECTION}:
        state = normalized_state(values)
        current = current_connection_text(values)
        if current:
            current_context = {"message": current}
            if is_authentication_issue(current_context):
                return presentation("🔐", "authentication", "authentication-error")
            if contains_any(current, FAILURE_MARKERS):
                return presentation("🚨", "critical", "failed")
            if contains_any(all_text_values(values), FAILURE_MARKERS):
                return presentation("✅", "recovered", "recovered")
        if is_authentication_issue(values):
            return presentation("🔐", "authentication", "authentication-error")
        if is_recovery(values, state):
            return presentation("✅", "recovered", "recovered")
        if state in FAILED_STATES:
            return presentation("🚨", "critical", state)
        if state in DEGRADED_STATES or is_rate_limited(values) or contains_any(all_text_values(values), FAILURE_MARKERS):
            return presentation("⚠️", "degraded", state or "degraded")
        icon = "🔌" if key == MONITOR_CONNECTION else "🛰️"
        return presentation(icon, "info", state or "healthy")

    return presentation("ℹ️", "info", normalized_state(values) or "unknown")


def operational_message_start_badge(context: Dict[str, object], default_badge: str) -> str:
    item = operational_notification_presentation(context=context)
    return item.badge if item else str(default_badge or "").strip()


def operational_presentation_context(context: Dict[str, object]) -> Dict[str, object]:
    values = dict(context or {})
    item = operational_notification_presentation(context=values)
    if item:
        values.update(item.to_context())
    return values
