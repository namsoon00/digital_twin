"""Secret-free execution diagnostics and a fenced, shared recovery policy."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import subprocess


FAILURE_MESSAGES = {
    "quota": "AI 사용 한도 또는 잔액이 부족합니다.",
    "authentication": "AI 인증 정보를 갱신해야 합니다.",
    "rate-limit": "AI 제공처가 호출 속도를 제한했습니다.",
    "network": "AI 제공처 연결에 실패했습니다.",
    "provider-unavailable": "AI 제공처가 일시적으로 응답하지 못했습니다.",
    "timeout": "AI 실행 제한 시간을 초과했습니다.",
    "invalid-request": "AI 실행 요청 형식을 확인해야 합니다.",
    "process-failed": "AI 프로세스가 비정상 종료됐습니다. 제공처 원인은 확인되지 않았습니다.",
}


def instant(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def safe_diagnostic(value):
    """Revalidate at persistence boundaries; nested metrics are not a log sink."""
    category = value.get("category") if isinstance(value, dict) else None
    category = category if isinstance(category, str) and category in FAILURE_MESSAGES else "process-failed"
    signals = {"usage-limit", "authentication-error", "rate-limit", "invalid-request", "provider-unavailable",
               "connection-error", "provider-timeout", "execution-timeout", "nonzero-exit", "failed-turn"}
    result = {"version": "ai-execution-failure-v1", "category": category,
              "message": FAILURE_MESSAGES[category], "rawOutputRetained": False}
    if isinstance(value, dict):
        signal = value.get("signal")
        result["signal"] = signal if isinstance(signal, str) and signal in signals else "nonzero-exit"
        code = value.get("returnCode")
        if isinstance(code, int) and not isinstance(code, bool):
            result["returnCode"] = code
        digest = value.get("diagnosticHash")
        if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
            result["diagnosticHash"] = digest
    return result


class AIExecutionError(RuntimeError):
    def __init__(self, diagnostic):
        self.diagnostic = safe_diagnostic(diagnostic)
        self.code = "ai-execution:" + self.diagnostic["category"]
        super().__init__(self.diagnostic["message"])


class AIExecutionDeferred(RuntimeError):
    code = "ai-execution:recovery-wait"

    def __init__(self, retry_at, category="process-failed"):
        self.retry_at = retry_at
        self.category = category if category in FAILURE_MESSAGES else "process-failed"
        super().__init__(FAILURE_MESSAGES[self.category])

    def result(self):
        return {"status": "recovery-wait", "reason": self.code,
                "errorCategory": self.category, "nextCheckAt": self.retry_at}


def process_diagnostic(result):
    """Inspect error channels, retaining only allowlisted signals and a hash.

    Neither stdout, stderr, provider messages, URLs nor credentials are persisted.
    Successful model-authored content cannot masquerade as a provider error.
    """
    failed = bool(getattr(result, "returncode", 0))
    stderr = str(getattr(result, "stderr", "") or "")[-32768:]
    stdout = str(getattr(result, "stdout", "") or "")[-65536:]
    errors = []
    terminal = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        kind = event.get("type") if isinstance(event, dict) else None
        if not isinstance(kind, str):
            continue
        if kind in {"turn.failed", "turn.completed"}:
            terminal = kind
        if kind in {"turn.failed", "error"}:
            errors.append(json.dumps(event.get("error", event.get("message", "")), ensure_ascii=False))
    if not failed and (terminal == "turn.completed" or not errors):
        return None
    raw = "\n".join([stderr, *errors])
    lowered = raw.lower()
    signals = (
        ("quota", "usage-limit", r"insufficient_quota|usage[_ ]limit|quota exceeded|exceeded.{0,40}quota|insufficient.{0,20}(?:credit|balance)"),
        ("authentication", "authentication-error", r"invalid_api_key|authentication_error|unauthorized|token_expired|refresh_token_reused|refresh token.{0,60}(?:expired|reused|already used)|http(?: status)?[ :]+401"),
        ("rate-limit", "rate-limit", r"rate_limit_exceeded|rate limit|too many requests|http(?: status)?[ :]+429"),
        ("invalid-request", "invalid-request", r"invalid_request_error|invalid schema|unsupported (?:model|parameter)|unrecognized argument"),
        ("provider-unavailable", "provider-unavailable", r"service unavailable|server_error|internal server error|bad gateway|http(?: status)?[ :]+50[234]"),
        ("network", "connection-error", r"connection (?:reset|refused|failed)|error sending request|failed to connect|dns|network is unreachable|tls|certificate verify|stream disconnected"),
        ("timeout", "provider-timeout", r"timed out|timeout"),
    )
    category, signal = next(((category, signal) for category, signal, pattern in signals if re.search(pattern, lowered)),
                            ("process-failed", "nonzero-exit" if failed else "failed-turn"))
    return {"version": "ai-execution-failure-v1", "category": category, "signal": signal,
            "message": FAILURE_MESSAGES[category], "returnCode": getattr(result, "returncode", None),
            "diagnosticHash": hashlib.sha256(raw.encode()).hexdigest(), "rawOutputRetained": False}


def exception_diagnostic(error):
    if isinstance(error, AIExecutionError):
        return error.diagnostic
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return {"version": "ai-execution-failure-v1", "category": "timeout", "signal": "execution-timeout",
                "message": FAILURE_MESSAGES["timeout"], "rawOutputRetained": False}
    return None


def recovery_wait(state, now):
    blocked, probe = instant(state.get("blockedUntil")), instant(state.get("probeUntil"))
    due = max([value for value in (blocked, probe) if value and value > now], default=None)
    return AIExecutionDeferred(stamp(due), state.get("lastFailureCategory")) if due else None


def admit_execution(state, now, token):
    state = dict(state)
    wait = recovery_wait(state, now)
    if wait:
        state.update(deferredCount=int(state.get("deferredCount", 0)) + 1, lastDeferredAt=stamp(now))
        return state, None, wait
    probe = bool(state.get("blockedUntil"))
    if probe:
        state.update(probeToken=token, probeUntil=stamp(now + timedelta(minutes=15)))
    ticket = {"generation": int(state.get("generation", 0)), "failures": int(state.get("failures", 0)),
              "probeToken": token if probe else ""}
    return state, ticket, None


def finish_execution(state, ticket, now, outcome, diagnostic=None):
    state = dict(state)
    diagnostic = safe_diagnostic(diagnostic) if diagnostic else None
    if ticket["generation"] != int(state.get("generation", 0)):
        return state
    probe = ticket.get("probeToken")
    if state.get("probeToken") and state["probeToken"] != probe:
        return state
    if probe and (probe != state.get("probeToken") or instant(state.get("probeUntil")) <= now):
        return state
    if outcome == "success":
        # A slow success admitted before a newer failure cannot erase that failure.
        if not probe and ticket["failures"] != int(state.get("failures", 0)):
            return state
        state.update(failures=0, blockedUntil="", probeToken="", probeUntil="", lastSuccessAt=stamp(now))
        if probe:
            state["generation"] = int(state.get("generation", 0)) + 1
        return state
    if diagnostic and diagnostic["category"] != "invalid-request":
        failures = int(state.get("failures", 0)) + 1
        category = diagnostic["category"]
        state.update(failures=failures, lastFailureAt=stamp(now), lastFailureCategory=category,
                     lastFailure=diagnostic)
        if failures >= 3 or category in {"quota", "authentication"} or probe:
            delay = 900 if category in {"quota", "authentication"} else min(900, 60 * 2 ** min(4, max(0, failures - 3)))
            state.update(blockedUntil=stamp(now + timedelta(seconds=delay)), probeToken="", probeUntil="",
                         generation=int(state.get("generation", 0)) + 1)
    elif probe:
        # Cancellation, lost budget or an application error is not provider recovery.
        state.update(probeToken="", probeUntil="", generation=int(state.get("generation", 0)) + 1)
    return state


def execution_health(now, state, progress, calls, overdue, enabled=True, expired_leases=0):
    central = [row for row in calls if row["workload"] == "independent-observation"]
    completed = sum(int(row["count"]) for row in central if row["status"] == "completed")
    failed = sum(int(row["count"]) for row in central if row["status"] == "failed")
    wait = recovery_wait(state, now)
    status, reason = "idle", "예약된 관찰을 기다립니다."
    if not enabled:
        status, reason = "paused", "중앙 AI 관찰이 일시 중지돼 있습니다."
    elif wait:
        status, reason = "recovering" if state.get("probeToken") else "unavailable", "AI 실행 오류로 재시도를 기다립니다."
    elif expired_leases:
        status, reason = "delayed", "처리 권한이 만료된 관찰 작업의 복구를 기다립니다."
    elif overdue:
        status, reason = "delayed", "예정 시각을 20분 넘긴 관찰 작업이 있습니다."
    elif int(state.get("failures", 0)):
        status, reason = "degraded", "최근 AI 실행 오류가 있어 복구 확인이 필요합니다."
    elif failed:
        status, reason = ("recovering", "호출이 다시 성공하고 있습니다. 최근 오류 이후의 상태를 확인 중입니다.") if completed else ("degraded", "최근 1시간 중앙 AI 호출이 실패했습니다.")
    elif completed:
        last_check = instant(progress.get("lastObservationCheckAt"))
        if not last_check or now - last_check > timedelta(hours=1):
            status, reason = "awaiting-result", "모델 호출은 완료됐지만 최근 관찰 결과 저장은 아직 확인되지 않았습니다."
        else:
            status, reason = "healthy", "최근 중앙 AI 호출과 관찰 결과 저장이 확인됐습니다. 판단 품질은 별도 검증합니다."
    return {"version": "central-ai-operational-health-v1", "status": status, "reason": reason,
            "checkedAt": stamp(now), "callWindowMinutes": 60, "centralCallsCompleted": completed,
            "centralCallsFailed": failed, "centralFailureRate": round(failed / (completed + failed), 4) if completed + failed else None,
            "lastJudgmentAt": progress.get("lastJudgmentAt", ""), "lastObservationCheckAt": progress.get("lastObservationCheckAt", ""),
            "overdueObservationTasks": overdue, "expiredObservationLeases": expired_leases, "retryAt": wait.retry_at if wait else "",
            "sharedExecution": {key: state.get(key) for key in ("lastSuccessAt", "lastFailureAt", "lastFailure", "failures", "deferredCount", "blockedUntil", "probeUntil")}}
