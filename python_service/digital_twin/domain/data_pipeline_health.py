from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List

from .data_freshness import parse_datetime
ALERT_STATES = {"degraded", "failed", "stale"}


def integer(value: object) -> int:
    try:
        return max(0, int(float(str(value or "0"))))
    except (TypeError, ValueError):
        return 0


def provider_health_rows(statuses: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[str, Dict[str, object]] = {}
    for status in statuses or []:
        if not isinstance(status, dict):
            continue
        source = str(status.get("source") or status.get("provider") or "unknown").strip() or "unknown"
        row = grouped.setdefault(source, {
            "source": source,
            "requestCount": 0,
            "successCount": 0,
            "failureCount": 0,
            "itemCount": 0,
            "candidateCount": 0,
            "bodyMissingCount": 0,
            "sourceBlockedCount": 0,
            "relevanceRejectedCount": 0,
            "messages": [],
        })
        row["requestCount"] += 1
        if status.get("ok") is False:
            row["failureCount"] += 1
            message = str(status.get("message") or status.get("reason") or "provider request failed").strip()
            if message and message not in row["messages"]:
                row["messages"].append(message[:180])
        else:
            row["successCount"] += 1
        row["itemCount"] += integer(status.get("count"))
        row["candidateCount"] += integer(status.get("candidateCount"))
        row["bodyMissingCount"] += integer(status.get("bodyMissingCount"))
        row["sourceBlockedCount"] += integer(status.get("sourceBlockedCount"))
        row["relevanceRejectedCount"] += integer(status.get("preliminaryRejectedCount"))
        row["relevanceRejectedCount"] += integer(status.get("finalRelevanceRejectedCount"))
    return list(grouped.values())


def elapsed_minutes(timestamp: object, now: datetime) -> float:
    parsed = parse_datetime(timestamp)
    if not parsed:
        return 0.0
    return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 60.0)


@dataclass(frozen=True)
class DataPipelineHealth:
    pipeline: str
    state: str
    reason_code: str
    reason: str
    checked_at: str
    state_since: str
    first_observed_at: str
    last_non_zero_at: str
    consecutive_zero_runs: int
    target_count: int
    fetched_count: int
    saved_count: int
    provider_failure_count: int
    provider_candidate_count: int
    provider_rows: List[Dict[str, object]] = field(default_factory=list)
    previous_state: str = ""
    state_changed: bool = False
    alert_required: bool = False

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "pipeline": payload["pipeline"],
            "state": payload["state"],
            "reasonCode": payload["reason_code"],
            "reason": payload["reason"],
            "checkedAt": payload["checked_at"],
            "stateSince": payload["state_since"],
            "firstObservedAt": payload["first_observed_at"],
            "lastNonZeroAt": payload["last_non_zero_at"],
            "consecutiveZeroRuns": payload["consecutive_zero_runs"],
            "targetCount": payload["target_count"],
            "fetchedCount": payload["fetched_count"],
            "savedCount": payload["saved_count"],
            "providerFailureCount": payload["provider_failure_count"],
            "providerCandidateCount": payload["provider_candidate_count"],
            "providers": payload["provider_rows"],
            "previousState": payload["previous_state"],
            "stateChanged": payload["state_changed"],
            "alertRequired": payload["alert_required"],
        }


def evaluate_news_collection_health(
    result: Dict[str, object],
    previous: Dict[str, object] = None,
    blocked_warning_streak: int = 3,
    stale_after_minutes: int = 180,
    now: datetime = None,
) -> DataPipelineHealth:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    checked_at = current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    previous = dict(previous or {})
    previous_state = str(previous.get("state") or "")
    first_observed_at = str(previous.get("firstObservedAt") or checked_at)
    fetched_count = integer(result.get("fetchedCount"))
    saved_count = integer(result.get("savedCount"))
    target_count = integer(result.get("targetCount"))
    zero_runs = 0 if fetched_count else integer(previous.get("consecutiveZeroRuns")) + 1
    last_non_zero_at = checked_at if fetched_count else str(previous.get("lastNonZeroAt") or "")
    providers = provider_health_rows(result.get("statuses") or [])
    provider_failures = sum(integer(row.get("failureCount")) for row in providers)
    provider_successes = sum(integer(row.get("successCount")) for row in providers)
    provider_candidates = sum(integer(row.get("candidateCount")) for row in providers)
    body_missing_count = sum(integer(row.get("bodyMissingCount")) for row in providers)
    source_blocked_count = sum(integer(row.get("sourceBlockedCount")) for row in providers)
    body_failure_count = max(body_missing_count, source_blocked_count)
    body_failure_ratio = (body_failure_count / provider_candidates) if provider_candidates else 0.0
    baseline_at = last_non_zero_at or first_observed_at
    zero_age_minutes = elapsed_minutes(baseline_at, current)

    if str(result.get("status") or "") == "disabled":
        state, reason_code, reason = "disabled", "collection-disabled", "뉴스 수집 기능이 비활성화되어 있습니다."
    elif not target_count:
        state, reason_code, reason = "idle", "no-targets", "수집 대상 보유·관심종목이 없어 대기 중입니다."
    elif provider_failures and not provider_successes:
        state, reason_code, reason = "failed", "all-providers-failed", "구성된 뉴스 공급자 요청이 모두 실패했습니다."
    elif provider_failures:
        state, reason_code, reason = "degraded", "partial-provider-failure", "일부 뉴스 공급자 요청이 실패해 나머지 공급자 데이터만 사용합니다."
    elif fetched_count:
        state, reason_code, reason = "healthy", "fresh-evidence-collected", "신선도와 품질 기준을 통과한 뉴스 근거를 수집했습니다."
    elif (
        provider_candidates
        and body_failure_count
        and body_failure_ratio >= 0.5
        and zero_runs >= max(1, int(blocked_warning_streak or 1))
    ):
        state, reason_code, reason = "degraded", "article-body-unavailable", "뉴스 후보의 절반 이상에서 원문 본문을 확보하지 못하는 상태가 반복되고 있습니다."
    elif zero_age_minutes >= max(1, int(stale_after_minutes or 1)):
        state, reason_code, reason = "stale", "coverage-stale", "품질 기준을 통과한 최신 뉴스가 허용된 공백 시간 동안 수집되지 않았습니다."
    elif provider_candidates:
        state, reason_code, reason = "idle", "candidates-filtered", "공급자는 정상이며 후보가 종목 관련성·본문·신선도 품질 기준에서 제외되었습니다."
    else:
        state, reason_code, reason = "idle", "no-new-evidence", "공급자 장애는 없으며 현재 새로 반영할 투자 관련 뉴스가 없습니다."

    state_changed = state != previous_state
    state_since = checked_at if state_changed else str(previous.get("stateSince") or checked_at)
    alert_required = state_changed and (state in ALERT_STATES or previous_state in ALERT_STATES)
    return DataPipelineHealth(
        pipeline="newsCollection",
        state=state,
        reason_code=reason_code,
        reason=reason,
        checked_at=checked_at,
        state_since=state_since,
        first_observed_at=first_observed_at,
        last_non_zero_at=last_non_zero_at,
        consecutive_zero_runs=zero_runs,
        target_count=target_count,
        fetched_count=fetched_count,
        saved_count=saved_count,
        provider_failure_count=provider_failures,
        provider_candidate_count=provider_candidates,
        provider_rows=providers,
        previous_state=previous_state,
        state_changed=state_changed,
        alert_required=alert_required,
    )
