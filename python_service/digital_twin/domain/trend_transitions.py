from typing import Dict, Iterable, List

from .market_data import number
from .ontology_threshold_policy import default_ontology_threshold_policy
from .portfolio import Position


def state_number(payload: Dict[str, object], *keys: str) -> float:
    for key in keys:
        value = number((payload or {}).get(key))
        if value is not None:
            return float(value)
    return 0.0


def pct_delta(current: float, previous: float) -> float:
    if not current or not previous:
        return 0.0
    return (float(current) - float(previous)) / abs(float(previous)) * 100.0


def compact_state_point(state: Dict[str, object], symbol: str, source: str = "holding") -> Dict[str, object]:
    if not isinstance(state, dict):
        return {}
    symbol = str(symbol or "").upper().strip()
    bucket_name = "watchlist" if source == "watchlist" else "positions"
    payload = (state.get(bucket_name) or {}).get(symbol) or {}
    if not isinstance(payload, dict) and source != "holding":
        payload = (state.get("positions") or {}).get(symbol) or {}
    if not isinstance(payload, dict) or not payload:
        return {}
    return {
        "observedAt": str(state.get("generatedAt") or payload.get("updatedAt") or ""),
        "price": state_number(payload, "currentPrice", "current_price", "price"),
        "profitLossRate": state_number(payload, "profitLossRate", "profit_loss_rate"),
        "ma20Distance": state_number(payload, "ma20Distance", "ma20_distance"),
        "ma60Distance": state_number(payload, "ma60Distance", "ma60_distance"),
        "ma20Slope": state_number(payload, "ma20Slope", "ma20_slope"),
        "ma60Slope": state_number(payload, "ma60Slope", "ma60_slope"),
        "changeRate": state_number(payload, "changeRate", "change_rate", "priceChangeRate"),
        "volumeRatio": state_number(payload, "volumeRatio", "volume_ratio"),
        "source": source,
    }


def position_point(position: Position, source: str = "holding") -> Dict[str, object]:
    return {
        "observedAt": str(position.updated_at or ""),
        "price": float(number(position.current_price) or 0.0),
        "profitLossRate": float(number(position.profit_loss_rate) or 0.0),
        "ma20Distance": float(number(position.ma20_distance) or 0.0),
        "ma60Distance": float(number(position.ma60_distance) or 0.0),
        "ma20Slope": float(number(position.ma20_slope) or 0.0),
        "ma60Slope": float(number(position.ma60_slope) or 0.0),
        "changeRate": float(number(position.change_rate) or 0.0),
        "volumeRatio": float(number(position.volume_ratio) or 0.0),
        "source": source,
    }


def normalize_points(points: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    normalized: List[Dict[str, object]] = []
    seen = set()
    for point in points or []:
        if not isinstance(point, dict):
            continue
        price = float(number(point.get("price")) or 0.0)
        if not price:
            continue
        key = str(point.get("observedAt") or "") + ":" + str(round(price, 6))
        if key in seen:
            continue
        seen.add(key)
        normalized.append({
            "observedAt": str(point.get("observedAt") or ""),
            "price": price,
            "profitLossRate": round(float(number(point.get("profitLossRate")) or 0.0), 4),
            "ma20Distance": round(float(number(point.get("ma20Distance")) or 0.0), 4),
            "ma60Distance": round(float(number(point.get("ma60Distance")) or 0.0), 4),
            "ma20Slope": round(float(number(point.get("ma20Slope")) or 0.0), 4),
            "ma60Slope": round(float(number(point.get("ma60Slope")) or 0.0), 4),
            "changeRate": round(float(number(point.get("changeRate")) or 0.0), 4),
            "volumeRatio": round(float(number(point.get("volumeRatio")) or 0.0), 4),
            "source": str(point.get("source") or ""),
        })
    return normalized[-6:]


def phase_from_points(points: List[Dict[str, object]], threshold_policy=None) -> Dict[str, object]:
    policy = threshold_policy or default_ontology_threshold_policy().temporal_pattern
    if not points:
        return {"phase": "unknown", "label": "이력 부족", "dataState": "insufficient"}
    current = points[-1]
    total_delta = pct_delta(float(current.get("price") or 0.0), float(points[0].get("price") or 0.0)) if len(points) >= 2 else float(current.get("changeRate") or 0.0)
    last_delta = pct_delta(float(current.get("price") or 0.0), float(points[-2].get("price") or 0.0)) if len(points) >= 2 else float(current.get("changeRate") or 0.0)
    ma20_distance = float(current.get("ma20Distance") or 0.0)
    ma60_distance = float(current.get("ma60Distance") or 0.0)
    ma20_slope = float(current.get("ma20Slope") or 0.0)
    trend_curve = ma20_slope - float(current.get("ma60Slope") or 0.0)
    data_state = "sufficient" if len(points) >= 3 else "partial"

    if abs(total_delta) <= policy.sideways_total_delta_pct and abs(last_delta) <= policy.sideways_last_delta_pct and abs(ma20_distance) <= policy.sideways_ma20_distance_pct:
        return {"phase": "sideways", "label": "횡보", "dataState": data_state}
    if total_delta <= policy.falling_total_delta_pct or ma20_distance <= policy.falling_ma20_distance_pct or (last_delta < policy.falling_last_delta_pct and ma20_slope < 0):
        return {"phase": "falling", "label": "하락", "dataState": data_state}
    if total_delta >= policy.rising_total_delta_pct or ma20_distance >= policy.rising_ma20_distance_pct or (last_delta > policy.rising_last_delta_pct and ma20_slope >= 0):
        return {"phase": "rising", "label": "상승", "dataState": data_state}
    if trend_curve >= policy.recovery_curve_pct or ma20_slope >= policy.recovery_ma20_slope_pct:
        return {"phase": "recovering", "label": "회복 시도", "dataState": data_state}
    if trend_curve <= policy.weakening_curve_pct or ma20_slope <= policy.weakening_ma20_slope_pct:
        return {"phase": "weakening", "label": "약화", "dataState": data_state}
    return {"phase": "mixed", "label": "혼조", "dataState": data_state}


def trend_transition_assessment(
    position: Position,
    history: Iterable[Dict[str, object]] = None,
    previous_state: Dict[str, object] = None,
    source: str = "holding",
    threshold_policy=None,
) -> Dict[str, object]:
    policy = threshold_policy or default_ontology_threshold_policy().temporal_pattern
    symbol = str(position.symbol or "").upper().strip()
    raw_points = [
        compact_state_point(state, symbol, source)
        for state in (history or [])
        if isinstance(state, dict)
    ]
    if previous_state:
        raw_points.append(compact_state_point(previous_state, symbol, source))
    raw_points.append(position_point(position, source))
    points = normalize_points(raw_points)
    current_phase = phase_from_points(points, policy)
    previous_phase = phase_from_points(points[:-1], policy) if len(points) >= 2 else {"phase": "unknown", "label": "이력 부족", "dataState": "insufficient"}
    current = points[-1] if points else {}
    previous = points[-2] if len(points) >= 2 else {}
    last_delta = pct_delta(float(current.get("price") or 0.0), float(previous.get("price") or 0.0)) if previous else float(current.get("changeRate") or 0.0)
    ma20_delta = float(current.get("ma20Distance") or 0.0) - float(previous.get("ma20Distance") or 0.0) if previous else 0.0
    volume_ratio = float(current.get("volumeRatio") or 0.0)
    transition = {
        "transitionType": "none",
        "label": "의미 있는 추세 전이 없음",
        "polarity": "context",
        "evidenceRole": "context",
        "reviewLevel": "normal",
        "changeState": "unchanged",
        "dataState": "sufficient" if len(points) >= 3 else "partial",
        "relationHint": "",
    }
    prev = previous_phase.get("phase")
    curr = current_phase.get("phase")

    if prev == "falling" and curr in {"recovering", "rising", "sideways"} and (last_delta >= policy.rebound_last_delta_pct or ma20_delta >= policy.rebound_ma20_delta_pct):
        transition.update({
            "transitionType": "falling_to_rebound",
            "label": "하락 후 반등/안정 전환",
            "polarity": "support",
            "evidenceRole": "support",
            "reviewLevel": "check",
            "changeState": "improving",
            "relationHint": "INDICATES_REVERSAL",
        })
    elif prev == "sideways" and curr in {"rising", "recovering"} and (last_delta >= policy.breakout_last_delta_pct or volume_ratio >= policy.breakout_volume_ratio):
        transition.update({
            "transitionType": "sideways_to_breakout",
            "label": "횡보 후 상방 이탈",
            "polarity": "support",
            "evidenceRole": "support",
            "reviewLevel": "act" if volume_ratio >= policy.breakout_volume_ratio else "check",
            "changeState": "new-condition",
            "relationHint": "BREAKS_CONSOLIDATION",
        })
    elif prev == "sideways" and curr in {"falling", "weakening"} and (last_delta <= policy.breakdown_last_delta_pct or ma20_delta <= policy.breakdown_ma20_delta_pct):
        transition.update({
            "transitionType": "sideways_to_breakdown",
            "label": "횡보 후 하방 이탈",
            "polarity": "risk",
            "evidenceRole": "risk",
            "reviewLevel": "act" if volume_ratio >= policy.breakout_volume_ratio else "check",
            "changeState": "worsening",
            "relationHint": "BREAKS_CONSOLIDATION",
        })
    elif prev == "rising" and curr in {"weakening", "falling", "sideways"} and (last_delta <= policy.distribution_last_delta_pct or ma20_delta <= policy.distribution_ma20_delta_pct):
        transition.update({
            "transitionType": "rising_to_distribution",
            "label": "상승 후 탄력 둔화/분배",
            "polarity": "risk",
            "evidenceRole": "risk",
            "reviewLevel": "check",
            "changeState": "worsening",
            "relationHint": "INDICATES_DECELERATION",
        })
    elif prev == "falling" and curr == "falling" and (last_delta <= policy.falling_acceleration_last_delta_pct or ma20_delta <= policy.falling_acceleration_ma20_delta_pct):
        transition.update({
            "transitionType": "falling_acceleration",
            "label": "하락 가속",
            "polarity": "risk",
            "evidenceRole": "risk",
            "reviewLevel": "immediate" if volume_ratio >= policy.confirmation_volume_ratio else "act",
            "changeState": "worsening",
            "relationHint": "INDICATES_ACCELERATION",
        })
    elif transition["transitionType"] == "none" and curr in {"falling", "weakening"}:
        ma20_distance = float(current.get("ma20Distance") or 0.0)
        ma60_distance = float(current.get("ma60Distance") or 0.0)
        if ma20_distance <= policy.path_risk_ma20_distance_pct or ma60_distance <= policy.path_risk_ma60_distance_pct:
            transition.update({
                "transitionType": "current_path_risk_confirmation",
                "label": "현재 경로 위험 확인",
                "polarity": "risk",
                "evidenceRole": "risk",
                "reviewLevel": "act" if volume_ratio >= policy.confirmation_volume_ratio else "check",
                "changeState": "new-condition",
                "relationHint": "INDICATES_WEAKENING",
            })
    elif transition["transitionType"] == "none" and curr in {"recovering", "rising"}:
        if last_delta >= policy.path_support_last_delta_pct and volume_ratio >= policy.path_support_volume_ratio:
            transition.update({
                "transitionType": "current_path_support_confirmation",
                "label": "현재 경로 우호 확인",
                "polarity": "support",
                "evidenceRole": "support",
                "reviewLevel": "check",
                "changeState": "improving",
                "relationHint": "INDICATES_REVERSAL",
            })

    return {
        "symbol": symbol,
        "points": points,
        "pointCount": len(points),
        "previousPhase": previous_phase,
        "currentPhase": current_phase,
        "lastPriceDeltaPct": round(last_delta, 3),
        "ma20DistanceDeltaPct": round(ma20_delta, 3),
        "volumeRatio": round(volume_ratio, 3),
        "thresholdPolicyId": policy.policy_id,
        "thresholdPolicyVersion": policy.version,
        "thresholdPolicySource": policy.source,
        **transition,
    }
