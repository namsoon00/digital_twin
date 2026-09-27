from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from digital_twin.modules.model_registry.contracts import default_ontology_threshold_policy


KST = timezone(timedelta(hours=9))


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_timestamp(value: object) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _regular_session_elapsed_minutes(observed_at: object) -> Optional[float]:
    observed = _parse_timestamp(observed_at)
    if observed is None:
        return None
    local = observed.astimezone(KST)
    if local.weekday() >= 5:
        return None
    opened = local.replace(hour=9, minute=0, second=0, microsecond=0)
    closed = local.replace(hour=15, minute=30, second=0, microsecond=0)
    if not opened <= local <= closed:
        return None
    return max(0.0, (local - opened).total_seconds() / 60.0)


def _same_kst_date(left: object, right: object) -> bool:
    left_at = _parse_timestamp(left)
    right_at = _parse_timestamp(right)
    return bool(left_at and right_at and left_at.astimezone(KST).date() == right_at.astimezone(KST).date())


def previous_trade_strength_confirmed(
    previous: Dict[str, object],
    observed_at: object,
    stale_repeat_count: int = 3,
) -> bool:
    previous = previous if isinstance(previous, dict) else {}
    if str(previous.get("status") or "") != "available":
        return False
    if str(previous.get("marketSession") or "") != "regular":
        return False
    if not _same_kst_date(previous.get("fetchedAt") or previous.get("sourceAsOf"), observed_at):
        return False
    if int(_number(previous.get("unchangedCount"))) >= max(1, int(stale_repeat_count or 3)):
        return False
    explicit = previous.get("judgementEvidenceUsable")
    if explicit is not None:
        return bool(explicit)
    return bool(previous.get("aiUsableAsStrongEvidence"))


def trade_strength_quality_snapshot(
    *,
    trade_strength: object,
    coverage: Dict[str, object],
    observed_at: object,
    buy_volume: object = 0,
    sell_volume: object = 0,
    cumulative_volume: object = 0,
    changed_since_previous: Optional[bool] = None,
    previous_coverage: Dict[str, object] = None,
    stale_repeat_count: int = 3,
) -> Dict[str, object]:
    """Classify whether a raw trade-strength observation is decision-usable.

    Trade strength is already a buy/sell execution ratio, so it must not be
    multiplied by an intraday time factor. Time is used only to decide whether
    the cumulative sample is mature and demonstrably updated.
    """

    coverage = coverage if isinstance(coverage, dict) else {}
    previous_coverage = previous_coverage if isinstance(previous_coverage, dict) else {}
    policy = default_ontology_threshold_policy().data_quality
    opening_minutes = max(0.0, _number(policy.trade_strength_opening_confirmation_minutes))
    stale_limit = max(1, int(stale_repeat_count or policy.trade_strength_unchanged_stale_count))
    strength = _number(trade_strength)
    sample_count = max(0.0, _number(buy_volume) + _number(sell_volume))
    volume = max(0.0, _number(cumulative_volume))
    session = str(coverage.get("marketSession") or "").strip()
    status = str(coverage.get("status") or "").strip()
    transport = str(coverage.get("transport") or "").strip().lower()
    timestamp_state = str(coverage.get("sourceTimestampState") or "").strip()
    elapsed_minutes = _regular_session_elapsed_minutes(observed_at)
    unchanged_count = int(_number(coverage.get("unchangedCount")))
    opening_confirmed = elapsed_minutes is not None and elapsed_minutes >= opening_minutes
    previous_confirmed = previous_trade_strength_confirmed(
        previous_coverage,
        observed_at,
        stale_repeat_count=stale_limit,
    )
    change_confirmed = changed_since_previous is True
    sample_observed = sample_count > 0 or volume > 0

    state = "unavailable"
    reason = "체결강도 값이 없어 판단 근거로 사용하지 않습니다."
    usable = False
    if strength <= 0 or status not in {"available", "stale"}:
        state = "unavailable"
    elif session != "regular":
        state = "market-close-reference"
        reason = "정규장 밖의 체결강도는 최근 장 마감 참고값이며 실시간 매수·매도 근거로 사용하지 않습니다."
    elif not opening_confirmed:
        state = "opening-sample-pending"
        reason = "장 시작 후 " + str(int(opening_minutes)) + "분 동안은 체결 표본이 작아 체결강도를 참고값으로만 봅니다."
    elif status == "stale" or unchanged_count >= stale_limit:
        state = "stale-repeat"
        reason = "같은 체결강도가 " + str(max(unchanged_count, stale_limit)) + "회 이상 반복되어 갱신 지연 가능성이 있습니다."
    elif transport == "websocket":
        usable = sample_observed
        state = "confirmed-live" if usable else "sample-pending"
        reason = (
            "실시간 체결 프레임과 누적 체결 표본이 확인됐습니다."
            if usable
            else "실시간 프레임은 수신했지만 누적 체결 표본을 확인하지 못했습니다."
        )
    elif timestamp_state in {"queried-at-fallback", "websocket-received", ""}:
        usable = sample_observed and (change_confirmed or previous_confirmed)
        state = "confirmed-polled-change" if usable else "change-confirmation-pending"
        reason = (
            "공급자 시각은 없지만 같은 거래일의 누적 체결값 갱신이 확인됐습니다."
            if usable
            else "조회시각만 있는 REST 값이라 누적 체결값이 실제로 바뀌는지 확인할 때까지 참고값으로만 봅니다."
        )
    else:
        usable = sample_observed
        state = "confirmed-provider-time" if usable else "sample-pending"
        reason = (
            "공급자 기준시각과 누적 체결 표본이 확인됐습니다."
            if usable
            else "공급자 기준시각은 있지만 누적 체결 표본을 확인하지 못했습니다."
        )

    return {
        "tradeStrengthQualityState": state,
        "tradeStrengthQualityReason": reason,
        "tradeStrengthSessionElapsedMinutes": round(elapsed_minutes, 2) if elapsed_minutes is not None else None,
        "tradeStrengthOpeningConfirmationMinutes": round(opening_minutes, 2),
        "tradeStrengthSampleCount": round(sample_count, 2),
        "tradeStrengthSampleState": "execution-volume" if sample_count > 0 else "cumulative-volume" if volume > 0 else "missing",
        "tradeStrengthObservedChangeConfirmed": bool(change_confirmed),
        "tradeStrengthPreviousConfirmationRetained": bool(previous_confirmed and not change_confirmed and usable),
        "tradeStrengthDecisionUsable": bool(usable),
        "judgementEvidenceUsable": bool(usable),
        "aiUsableAsStrongEvidence": bool(usable),
    }


def trade_strength_quality_properties(coverage: Dict[str, object]) -> Dict[str, object]:
    coverage = coverage if isinstance(coverage, dict) else {}
    ccnl = coverage.get("ccnl") if isinstance(coverage.get("ccnl"), dict) else {}
    keys = (
        "tradeStrengthQualityState",
        "tradeStrengthQualityReason",
        "tradeStrengthSessionElapsedMinutes",
        "tradeStrengthOpeningConfirmationMinutes",
        "tradeStrengthSampleCount",
        "tradeStrengthSampleState",
        "tradeStrengthObservedChangeConfirmed",
        "tradeStrengthPreviousConfirmationRetained",
        "tradeStrengthDecisionUsable",
        "judgementEvidenceUsable",
        "aiUsableAsStrongEvidence",
        "marketSession",
        "marketSessionLabel",
        "sourceTimestampState",
        "unchangedCount",
    )
    return {key: ccnl.get(key) for key in keys if ccnl.get(key) not in (None, "")}
