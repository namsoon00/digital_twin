import hashlib
import re
import sys
import threading
import time
from typing import Callable, Dict

from ..domain.events import system_error_reported_event
from .external_signal_utils import sanitize_sensitive_text
from .notifications import notifier_for_operations
from .settings import SECRET_SETTING_KEYS, runtime_settings, utc_now


DEFAULT_ERROR_ALERT_COOLDOWN_SECONDS = 300
MAX_ERROR_MESSAGE_CHARS = 1600
ERROR_SECRET_SETTING_KEYS = set(SECRET_SETTING_KEYS) | {
    "tossAccountSeq",
    "telegramChatId",
    "operationsTelegramChatId",
}


def sanitize_operational_error_text(value: object, settings: Dict[str, object] = None) -> str:
    text = sanitize_sensitive_text(value).strip()
    for key, secret in dict(settings or {}).items():
        if key not in ERROR_SECRET_SETTING_KEYS:
            continue
        raw_secret = str(secret or "").strip()
        if len(raw_secret) >= 3:
            text = text.replace(raw_secret, "***")
    text = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+", r"\1***", text)
    text = re.sub(r"(?i)((?:token|secret|password|chat[_ -]?id|client[_ -]?id)\s*[:=]\s*)[^\s,;]+", r"\1***", text)
    return text[:MAX_ERROR_MESSAGE_CHARS].strip()


class OperationalErrorReporter:
    def __init__(
        self,
        notifier_factory: Callable[[], object] = None,
        event_publisher: Callable[[object], None] = None,
        settings_provider: Callable[[], Dict[str, object]] = None,
        monotonic_provider: Callable[[], float] = None,
        cooldown_seconds: int = DEFAULT_ERROR_ALERT_COOLDOWN_SECONDS,
    ):
        self.notifier_factory = notifier_factory or notifier_for_operations
        self.event_publisher = event_publisher or self.publish_event
        self.settings_provider = settings_provider or runtime_settings
        self.monotonic_provider = monotonic_provider or time.monotonic
        self.cooldown_seconds = max(0, int(cooldown_seconds or 0))
        self._states: Dict[str, Dict[str, object]] = {}
        self._lock = threading.Lock()

    def settings(self) -> Dict[str, object]:
        try:
            value = self.settings_provider()
        except Exception:  # noqa: BLE001 - reporting must continue when settings storage is unavailable.
            value = {}
        return dict(value or {}) if isinstance(value, dict) else {}

    def publish_event(self, event) -> None:
        try:
            from .event_bus import default_event_bus

            default_event_bus().publish(event)
        except Exception:  # noqa: BLE001 - event persistence must not block the direct operations alert.
            return

    def report(self, component: str, error: Exception, stage: str = "") -> Dict[str, object]:
        settings = self.settings()
        error_type = type(error).__name__ or "Exception"
        message = sanitize_operational_error_text(str(error) or error_type, settings) or error_type
        component_text = sanitize_operational_error_text(component, settings) or "system"
        stage_text = sanitize_operational_error_text(stage, settings)
        fingerprint_seed = "|".join([component_text, error_type, message])
        fingerprint = hashlib.sha256(fingerprint_seed.encode("utf-8")).hexdigest()[:20]
        now_value = self.monotonic_provider()

        with self._lock:
            state = self._states.setdefault(fingerprint, {"lastSentAt": None, "repeatCount": 0})
            state["repeatCount"] = int(state.get("repeatCount") or 0) + 1
            previous_sent_at = state.get("lastSentAt")
            should_send = previous_sent_at is None or now_value - float(previous_sent_at) >= self.cooldown_seconds
            occurrence_count = int(state["repeatCount"])

        event = system_error_reported_event(component_text, error_type, message, fingerprint, occurrence_count)
        try:
            self.event_publisher(event)
        except Exception:  # noqa: BLE001 - custom event publishers are optional observability hooks.
            pass

        if not should_send:
            return {
                "sent": False,
                "suppressed": True,
                "fingerprint": fingerprint,
                "occurrenceCount": occurrence_count,
                "message": message,
            }

        lines = [
            "🚨 시스템 오류",
            "• 구성요소: " + component_text,
            "• 오류 유형: " + error_type,
            "• 오류 내용: " + message,
        ]
        if stage_text:
            lines.append("• 단계: " + stage_text)
        if occurrence_count > 1:
            lines.append("• 이전 발송 이후 같은 오류 발생: " + str(occurrence_count) + "회")
        lines.append("• 발생 시각: " + utc_now())
        lines.append("• 오류 식별자: " + fingerprint)

        delivered = False
        delivery_reason = ""
        try:
            result = self.notifier_factory().send("\n".join(lines))
            delivered = bool(getattr(result, "delivered", False))
            delivery_reason = sanitize_operational_error_text(getattr(result, "reason", ""), settings)
        except Exception as notification_error:  # noqa: BLE001 - reporting must never stop the failing worker.
            delivery_reason = sanitize_operational_error_text(notification_error, settings)

        if delivered:
            with self._lock:
                state = self._states.setdefault(fingerprint, {"lastSentAt": None, "repeatCount": 0})
                state["lastSentAt"] = now_value
                state["repeatCount"] = 0

        return {
            "sent": delivered,
            "suppressed": False,
            "fingerprint": fingerprint,
            "occurrenceCount": occurrence_count,
            "message": message,
            "deliveryReason": delivery_reason,
        }


_DEFAULT_REPORTER = None


def operational_error_reporter() -> OperationalErrorReporter:
    global _DEFAULT_REPORTER
    if _DEFAULT_REPORTER is None:
        _DEFAULT_REPORTER = OperationalErrorReporter()
    return _DEFAULT_REPORTER


def report_runtime_error(reporter, component: str, error: Exception, stage: str = "") -> Dict[str, object]:
    active_reporter = reporter or operational_error_reporter()
    try:
        result = active_reporter.report(component, error, stage)
    except Exception as reporting_error:  # noqa: BLE001 - preserve the original worker recovery path.
        print("Operational error reporting failed: " + sanitize_operational_error_text(reporting_error), flush=True)
        return {"sent": False, "reportingFailed": True}
    if not result.get("sent") and not result.get("suppressed") and result.get("deliveryReason"):
        print("Operational error alert delivery failed: " + str(result["deliveryReason"]), flush=True)
    return result


def install_unhandled_error_reporter(component: str, reporter: OperationalErrorReporter = None) -> None:
    active_reporter = reporter or operational_error_reporter()
    previous_sys_hook = sys.excepthook

    def sys_hook(error_type, error, traceback):
        if not issubclass(error_type, (KeyboardInterrupt, SystemExit)):
            report_runtime_error(active_reporter, component, error, "unhandled")
        previous_sys_hook(error_type, error, traceback)

    sys.excepthook = sys_hook
    if not hasattr(threading, "excepthook"):
        return
    previous_thread_hook = threading.excepthook

    def thread_hook(args):
        if not issubclass(args.exc_type, (KeyboardInterrupt, SystemExit)):
            report_runtime_error(active_reporter, component + " thread", args.exc_value, "unhandled")
        previous_thread_hook(args)

    threading.excepthook = thread_hook
