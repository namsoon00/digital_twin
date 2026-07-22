from datetime import datetime, timezone
from typing import Dict, Iterable, Tuple

from .market_data import clamp, number
from .market_hours import DEFAULT_MARKET_HOUR_SESSIONS, market_time, normalize_market_key, parse_hhmm, session_items
from .ontology_threshold_policy import default_ontology_threshold_policy

TRADING_VALUE_MISMATCH_THRESHOLD_PCT = default_ontology_threshold_policy().data_quality.trading_value_mismatch_pct


REGULAR_SESSION_CURVE: Tuple[Tuple[float, float], ...] = (
    (0.0, 0.0),
    (0.05, 0.12),
    (0.10, 0.20),
    (0.25, 0.36),
    (0.50, 0.55),
    (0.75, 0.72),
    (0.90, 0.86),
    (1.0, 1.0),
)

EXTENDED_SESSION_EXPECTED_SHARE = {
    "pre": 0.08,
    "after": 0.06,
}


def parse_observed_at(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def interpolate_curve(curve: Iterable[Tuple[float, float]], elapsed_ratio: float) -> float:
    ratio = clamp(elapsed_ratio, 0.0, 1.0)
    points = list(curve or [])
    if not points:
        return ratio
    previous_x, previous_y = points[0]
    for current_x, current_y in points[1:]:
        if ratio <= current_x:
            span = max(0.0001, current_x - previous_x)
            progress = (ratio - previous_x) / span
            return clamp(previous_y + (current_y - previous_y) * progress, 0.0, 1.0)
        previous_x, previous_y = current_x, current_y
    return clamp(points[-1][1], 0.0, 1.0)


def session_bounds(current: datetime, item: Dict[str, object]) -> Tuple[datetime, datetime]:
    open_hour, open_minute = parse_hhmm(item.get("openTime"))
    close_hour, close_minute = parse_hhmm(item.get("closeTime"))
    open_at = current.replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
    close_at = current.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
    return open_at, close_at


def expected_volume_ratio_for_session(session_key: str, elapsed_ratio: float) -> float:
    key = str(session_key or "").strip()
    if key == "regular":
        return interpolate_curve(REGULAR_SESSION_CURVE, elapsed_ratio)
    if key == "after":
        expected_share = EXTENDED_SESSION_EXPECTED_SHARE[key]
        return clamp(1.0 + expected_share * clamp(elapsed_ratio, 0.0, 1.0), 1.0, 1.0 + expected_share)
    expected_share = EXTENDED_SESSION_EXPECTED_SHARE.get(key)
    if expected_share:
        return clamp(expected_share * clamp(elapsed_ratio, 0.0, 1.0), 0.01, expected_share)
    return clamp(elapsed_ratio, 0.05, 1.0)


def trading_value_snapshot(
    current_price: object,
    volume: object,
    reported_trading_value: object = 0,
    mismatch_threshold_pct: float = TRADING_VALUE_MISMATCH_THRESHOLD_PCT,
) -> Dict[str, object]:
    price_value = number(current_price)
    volume_value = number(volume)
    reported_value = number(reported_trading_value)
    estimated_value = price_value * volume_value if price_value > 0 and volume_value > 0 else 0.0
    threshold = max(0.0, number(mismatch_threshold_pct) or TRADING_VALUE_MISMATCH_THRESHOLD_PCT)
    mismatch_pct = 0.0
    if reported_value > 0 and estimated_value > 0:
        mismatch_pct = abs(reported_value - estimated_value) / max(1.0, estimated_value) * 100.0
        if mismatch_pct <= threshold:
            return {
                "tradingValue": reported_value,
                "reportedTradingValue": reported_value,
                "estimatedTradingValue": estimated_value,
                "tradingValueQuality": "reported",
                "tradingValueBasis": "제공 거래대금이 가격×거래량 교차검증 범위 안에 있어 제공값을 사용",
                "tradingValueMismatchPct": round(mismatch_pct, 2),
                "tradingValueEstimated": False,
                "tradingValueReliable": True,
            }
        return {
            "tradingValue": estimated_value,
            "reportedTradingValue": reported_value,
            "estimatedTradingValue": estimated_value,
            "tradingValueQuality": "estimated_from_price_volume",
            "tradingValueBasis": "제공 거래대금이 가격×거래량과 크게 달라 가격×거래량 추정값을 사용",
            "tradingValueMismatchPct": round(mismatch_pct, 2),
            "tradingValueEstimated": True,
            "tradingValueReliable": False,
        }
    if reported_value > 0:
        return {
            "tradingValue": reported_value,
            "reportedTradingValue": reported_value,
            "estimatedTradingValue": estimated_value,
            "tradingValueQuality": "reported_without_cross_check",
            "tradingValueBasis": "가격 또는 거래량이 없어 제공 거래대금을 교차검증하지 못함",
            "tradingValueMismatchPct": 0.0,
            "tradingValueEstimated": False,
            "tradingValueReliable": False,
        }
    if estimated_value > 0:
        return {
            "tradingValue": estimated_value,
            "reportedTradingValue": 0.0,
            "estimatedTradingValue": estimated_value,
            "tradingValueQuality": "estimated_from_price_volume",
            "tradingValueBasis": "제공 거래대금이 없어 가격×거래량 추정값을 사용",
            "tradingValueMismatchPct": 0.0,
            "tradingValueEstimated": True,
            "tradingValueReliable": True,
        }
    return {
        "tradingValue": 0.0,
        "reportedTradingValue": reported_value,
        "estimatedTradingValue": estimated_value,
        "tradingValueQuality": "missing",
        "tradingValueBasis": "거래대금 산출에 필요한 가격 또는 거래량이 없음",
        "tradingValueMismatchPct": 0.0,
        "tradingValueEstimated": False,
        "tradingValueReliable": False,
    }


def volume_pace_snapshot(
    market: object,
    raw_volume_ratio: object,
    volume: object = 0,
    trading_value: object = 0,
    observed_at: object = "",
    now: datetime = None,
) -> Dict[str, object]:
    raw_ratio = number(raw_volume_ratio)
    # A volume pace is meaningful only at the source observation time. Falling
    # back to the server clock would make unchanged provider data produce a
    # different ABox and an unnecessary new inference cycle on every read.
    observed = parse_observed_at(observed_at) or now
    market_key = normalize_market_key(market)
    session = DEFAULT_MARKET_HOUR_SESSIONS.get(market_key)
    result: Dict[str, object] = {
        "volume": number(volume),
        "tradingValue": number(trading_value),
        "volumeRatio": raw_ratio,
        "rawVolumeRatio": raw_ratio,
        "volumePaceMarket": market_key,
    }
    if not session:
        result.update({
            "volumePaceStatus": "unknown",
            "volumePaceLabel": "시장 세션 미확인",
            "volumePaceBasis": "시장 시간 정보가 없어 원본 거래량 배율만 사용",
        })
        return result

    if not observed:
        result.update({
            "volumePaceStatus": "unavailable",
            "volumePaceLabel": "시간 보정 기준시각 없음",
            "volumePaceBasis": "원천 기준시각이 없어 원본 거래량 배율만 사용",
        })
        return result

    current = market_time(observed, str(session.get("timezone") or "UTC"))
    weekdays = session.get("weekdays") if isinstance(session.get("weekdays"), list) else [0, 1, 2, 3, 4]
    matched = None
    matched_open = None
    matched_close = None
    for item in session_items(session):
        open_at, close_at = session_bounds(current, item)
        if current.weekday() in weekdays and open_at <= current < close_at:
            matched = item
            matched_open = open_at
            matched_close = close_at
            break

    market_label = str(session.get("label") or market_key or "").strip()
    if not matched:
        result.update({
            "volumePaceStatus": "closed",
            "volumePaceLabel": market_label + " 장외",
            "volumePaceLocalTime": current.isoformat(),
            "volumePaceBasis": "장중 세션 밖이라 원본 거래량 배율만 표시",
        })
        return result

    session_key = str(matched.get("key") or "regular").strip()
    session_label = str(matched.get("label") or "정규장").strip()
    total_seconds = max(1.0, (matched_close - matched_open).total_seconds())
    elapsed_seconds = max(0.0, min(total_seconds, (current - matched_open).total_seconds()))
    elapsed_ratio = clamp(elapsed_seconds / total_seconds, 0.0, 1.0)
    expected_ratio = expected_volume_ratio_for_session(session_key, elapsed_ratio)
    adjusted_ratio = raw_ratio / expected_ratio if raw_ratio and expected_ratio else 0.0
    policy = default_ontology_threshold_policy().data_quality
    if adjusted_ratio >= policy.volume_pace_strong_ratio:
        label = "시간 대비 강함"
    elif adjusted_ratio >= policy.volume_pace_normal_ratio:
        label = "시간 대비 보통"
    elif adjusted_ratio > 0:
        label = "시간 대비 부족"
    else:
        label = "시간 보정 불가"

    result.update({
        "volumePaceStatus": "open",
        "volumePaceSession": session_key,
        "volumePaceSessionLabel": " ".join(part for part in [market_label, session_label] if part),
        "volumePaceLocalTime": current.isoformat(),
        "volumePaceElapsedPct": round(elapsed_ratio * 100, 1),
        "expectedVolumeRatioNow": round(expected_ratio, 3),
        "timeAdjustedVolumeRatio": round(adjusted_ratio, 3),
        "volumePaceLabel": label,
        "volumePaceBasis": "원본 거래량 배율을 현재 세션 경과율과 장중 U자형 기대 거래량 분포로 보정",
    })
    return result
