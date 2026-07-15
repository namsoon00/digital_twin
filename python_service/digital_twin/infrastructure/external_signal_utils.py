import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from ..domain.market_data import number



JsonFetcher = Callable[[str, Dict[str, str]], object]
DISABLED_SETTING_VALUES = {"0", "false", "no", "off", "disabled"}
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "apikey",
    "api_key",
    "appkey",
    "appsecret",
    "authorization",
    "client_id",
    "client_secret",
    "crtfc_key",
    "key",
    "secret",
    "secretkey",
    "token",
}
GLOBAL_EXTERNAL_API_GUARD_STATE: Dict[str, object] = {}


def default_json_fetcher(url: str, headers: Dict[str, str] = None, timeout: float = 12.0) -> Dict[str, object]:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=max(0.5, float(timeout or 12.0))) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def parse_iso(value: str):
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def symbol_list(raw: str) -> List[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def symbol_assignments(raw: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    normalized = str(raw or "").replace(";", "\n")
    for line in normalized.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        separator = "=" if "=" in stripped else ":" if ":" in stripped else "," if "," in stripped else ""
        if not separator:
            continue
        key, raw_value = stripped.split(separator, 1)
        key = key.strip().upper()
        value = raw_value.strip()
        if not key or not key.replace("_", "").isalnum() or not value:
            continue
        values[key] = value
    return values


def percent_text(value: object) -> float:
    return number(str(value or "").replace("%", ""))


def sanitize_sensitive_text(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = re.sub(r"(?i)(apikey=)[^&\s]+", r"\1***", text)
    text = re.sub(r"(?i)(api[_ -]?key(?: is|:)?\s+)[A-Za-z0-9+/=_-]{8,}", r"\1***", text)
    text = re.sub(r"[A-Za-z0-9+/=_-]{48,}", "***", text)
    return text


def api_error_text(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        reason = str(error.reason or "").strip()
        return sanitize_sensitive_text("HTTP " + str(error.code) + (" " + reason if reason else ""))[:120]
    if isinstance(error, urllib.error.URLError):
        return sanitize_sensitive_text("URL error " + str(error.reason or error))[:120]
    return sanitize_sensitive_text(str(error or type(error).__name__))[:120]


def root_api_error(error: Exception) -> Exception:
    current = error
    seen = set()
    while getattr(current, "__cause__", None) is not None and id(current.__cause__) not in seen:
        seen.add(id(current))
        current = current.__cause__
    return current


def retryable_api_error(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return int(error.code or 0) in {408, 409, 425, 429, 500, 502, 503, 504}
    return isinstance(error, (urllib.error.URLError, TimeoutError, OSError))


class ExternalCircuitOpen(RuntimeError):
    pass


class ExternalRateLimited(RuntimeError):
    pass


class ExternalApiGuard:
    def __init__(
        self,
        state: Dict[str, object],
        sleep: Callable[[float], None] = None,
        now: Callable[[], datetime] = None,
    ):
        self.state = state
        self.sleep = sleep or time.sleep
        self.now = now or (lambda: datetime.now(timezone.utc))

    def entry(self, key: str) -> Dict[str, object]:
        raw = self.state.get(key)
        if isinstance(raw, dict):
            return raw
        entry: Dict[str, object] = {}
        self.state[key] = entry
        return entry

    def call(
        self,
        key: str,
        label: str,
        fetch: Callable[[], object],
        attempts: int,
        rate_limit_seconds: int,
        failure_threshold: int,
        cooldown_minutes: int,
        retry_delay_seconds: float = 0.25,
        shared_rate_limit_key: str = "",
        shared_rate_limit_seconds: int = 0,
        shared_rate_limit_label: str = "",
    ):
        entry = self.entry(key)
        shared_entry = self.entry(shared_rate_limit_key) if shared_rate_limit_key else None
        now = self.now()
        opened_until = parse_iso(str(entry.get("openedUntil") or ""))
        if opened_until and opened_until > now:
            raise ExternalCircuitOpen("circuit open until " + opened_until.isoformat().replace("+00:00", "Z"))
        last_request_at = parse_iso(str(entry.get("lastRequestAt") or ""))
        if rate_limit_seconds and last_request_at and now - last_request_at < timedelta(seconds=rate_limit_seconds):
            raise ExternalRateLimited("local rate limit active")
        if shared_entry is not None:
            shared_last_request_at = parse_iso(str(shared_entry.get("lastRequestAt") or ""))
            if (
                shared_rate_limit_seconds
                and shared_last_request_at
                and now - shared_last_request_at < timedelta(seconds=shared_rate_limit_seconds)
            ):
                label_suffix = " (" + shared_rate_limit_label + ")" if shared_rate_limit_label else ""
                raise ExternalRateLimited("local rate limit active" + label_suffix)

        last_error: Exception = RuntimeError("unknown error")
        max_attempts = max(1, int(attempts or 1))
        for attempt in range(max_attempts):
            try:
                result = fetch()
                entry["lastRequestAt"] = now.isoformat().replace("+00:00", "Z")
                entry["failures"] = 0
                entry["lastError"] = ""
                entry["openedUntil"] = ""
                if shared_entry is not None:
                    shared_entry["lastRequestAt"] = now.isoformat().replace("+00:00", "Z")
                    shared_entry["lastLabel"] = label
                return result
            except Exception as error:  # noqa: BLE001 - external adapters normalize vendor failures.
                last_error = error
                if attempt + 1 >= max_attempts or not retryable_api_error(error):
                    break
                self.sleep(retry_delay_seconds * (attempt + 1))

        failures = int(number(entry.get("failures")) or 0) + 1
        entry["lastRequestAt"] = now.isoformat().replace("+00:00", "Z")
        entry["failures"] = failures
        entry["lastError"] = api_error_text(last_error)
        if shared_entry is not None:
            shared_entry["lastRequestAt"] = now.isoformat().replace("+00:00", "Z")
            shared_entry["lastLabel"] = label
            shared_entry["lastError"] = api_error_text(last_error)
        if failures >= max(1, int(failure_threshold or 1)):
            entry["openedUntil"] = (now + timedelta(minutes=max(1, int(cooldown_minutes or 1)))).isoformat().replace("+00:00", "Z")
        raise RuntimeError(label + " 실패 · " + api_error_text(last_error)) from last_error


def guarded_int_setting(settings: Dict[str, object], key: str, fallback: int, minimum: int = 0, maximum: int = 100000) -> int:
    try:
        raw = settings.get(key) if isinstance(settings, dict) else None
        value = fallback if str(raw or "").strip() == "" else int(number(raw))
    except (TypeError, ValueError):
        value = fallback
    return max(minimum, min(maximum, value))


def external_call_target(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or ""))
    target = (parsed.netloc + parsed.path).strip("/") or str(url or "").split("?", 1)[0]
    query_pairs = []
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=False):
        normalized = str(key or "").strip().lower()
        if normalized in SENSITIVE_QUERY_KEYS or "secret" in normalized or "token" in normalized:
            continue
        if value in (None, ""):
            continue
        query_pairs.append((key, value))
    if query_pairs:
        target += "?" + urllib.parse.urlencode(query_pairs[:8])
    return target[:180]


def guard_key(source: str, target: str) -> str:
    raw = (str(source or "external") + ":" + str(target or "request")).lower()
    return "".join(ch if ch.isalnum() else "-" for ch in raw).strip("-")[:180]


def guarded_external_call(
    settings: Dict[str, object],
    source: str,
    target: str,
    fetch: Callable[[], object],
    state: Optional[Dict[str, object]] = None,
    sleep: Callable[[float], None] = None,
    now: Callable[[], datetime] = None,
    attempts: Optional[int] = None,
    rate_limit_seconds: int = 0,
    failure_threshold: Optional[int] = None,
    cooldown_minutes: Optional[int] = None,
    retry_delay_seconds: float = 0.25,
):
    configured = settings if isinstance(settings, dict) else {}
    guard = ExternalApiGuard(
        state if state is not None else GLOBAL_EXTERNAL_API_GUARD_STATE,
        sleep=sleep,
        now=now,
    )
    return guard.call(
        guard_key(source, target),
        str(source or "External API") + " " + str(target or "request"),
        fetch,
        attempts=attempts if attempts is not None else guarded_int_setting(configured, "externalApiRetryAttempts", 2, 1, 10),
        rate_limit_seconds=max(0, int(rate_limit_seconds or 0)),
        failure_threshold=failure_threshold if failure_threshold is not None else guarded_int_setting(configured, "externalApiCircuitFailures", 2, 1, 50),
        cooldown_minutes=cooldown_minutes if cooldown_minutes is not None else guarded_int_setting(configured, "externalApiCircuitCooldownMinutes", 30, 1, 1440),
        retry_delay_seconds=retry_delay_seconds,
    )
