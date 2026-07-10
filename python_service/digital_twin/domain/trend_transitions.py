from typing import Dict, Iterable, List

from .market_data import number
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


def phase_from_points(points: List[Dict[str, object]]) -> Dict[str, object]:
    if not points:
        return {"phase": "unknown", "label": "이력 부족", "confidence": 0.0}
    current = points[-1]
    total_delta = pct_delta(float(current.get("price") or 0.0), float(points[0].get("price") or 0.0)) if len(points) >= 2 else float(current.get("changeRate") or 0.0)
    last_delta = pct_delta(float(current.get("price") or 0.0), float(points[-2].get("price") or 0.0)) if len(points) >= 2 else float(current.get("changeRate") or 0.0)
    ma20_distance = float(current.get("ma20Distance") or 0.0)
    ma60_distance = float(current.get("ma60Distance") or 0.0)
    ma20_slope = float(current.get("ma20Slope") or 0.0)
    trend_curve = ma20_slope - float(current.get("ma60Slope") or 0.0)
    confidence = min(0.95, 0.45 + len(points) * 0.08)

    if abs(total_delta) <= 1.0 and abs(last_delta) <= 0.8 and abs(ma20_distance) <= 2.0:
        return {"phase": "sideways", "label": "횡보", "confidence": confidence}
    if total_delta <= -2.0 or ma20_distance <= -4.0 or (last_delta < -1.2 and ma20_slope < 0):
        return {"phase": "falling", "label": "하락", "confidence": confidence}
    if total_delta >= 2.0 or ma20_distance >= 3.0 or (last_delta > 1.2 and ma20_slope >= 0):
        return {"phase": "rising", "label": "상승", "confidence": confidence}
    if trend_curve >= 0.6 or ma20_slope >= 0.4:
        return {"phase": "recovering", "label": "회복 시도", "confidence": confidence * 0.9}
    if trend_curve <= -0.6 or ma20_slope <= -0.4:
        return {"phase": "weakening", "label": "약화", "confidence": confidence * 0.9}
    return {"phase": "mixed", "label": "혼조", "confidence": confidence * 0.8}


def trend_transition_assessment(
    position: Position,
    history: Iterable[Dict[str, object]] = None,
    previous_state: Dict[str, object] = None,
    source: str = "holding",
) -> Dict[str, object]:
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
    current_phase = phase_from_points(points)
    previous_phase = phase_from_points(points[:-1]) if len(points) >= 2 else {"phase": "unknown", "label": "이력 부족", "confidence": 0.0}
    current = points[-1] if points else {}
    previous = points[-2] if len(points) >= 2 else {}
    last_delta = pct_delta(float(current.get("price") or 0.0), float(previous.get("price") or 0.0)) if previous else float(current.get("changeRate") or 0.0)
    ma20_delta = float(current.get("ma20Distance") or 0.0) - float(previous.get("ma20Distance") or 0.0) if previous else 0.0
    volume_ratio = float(current.get("volumeRatio") or 0.0)
    transition = {
        "transitionType": "none",
        "label": "의미 있는 추세 전이 없음",
        "polarity": "context",
        "score": 0.0,
        "riskImpact": 0.0,
        "supportImpact": 0.0,
        "relationHint": "",
    }
    prev = previous_phase.get("phase")
    curr = current_phase.get("phase")

    if prev == "falling" and curr in {"recovering", "rising", "sideways"} and (last_delta >= 0.8 or ma20_delta >= 1.2):
        score = 60 + min(18.0, max(last_delta, ma20_delta) * 4.0) + (8 if volume_ratio >= 1.2 else 0)
        transition.update({
            "transitionType": "falling_to_rebound",
            "label": "하락 후 반등/안정 전환",
            "polarity": "support",
            "score": min(100.0, score),
            "supportImpact": min(14.0, 6.0 + max(last_delta, ma20_delta) * 1.6),
            "relationHint": "INDICATES_REVERSAL",
        })
    elif prev == "sideways" and curr in {"rising", "recovering"} and (last_delta >= 1.0 or volume_ratio >= 1.4):
        score = 64 + min(18.0, max(last_delta, 0.0) * 4.0) + (8 if volume_ratio >= 1.4 else 0)
        transition.update({
            "transitionType": "sideways_to_breakout",
            "label": "횡보 후 상방 이탈",
            "polarity": "support",
            "score": min(100.0, score),
            "supportImpact": min(15.0, 7.0 + max(last_delta, 0.0) * 1.8),
            "relationHint": "BREAKS_CONSOLIDATION",
        })
    elif prev == "sideways" and curr in {"falling", "weakening"} and (last_delta <= -1.0 or ma20_delta <= -1.2):
        score = 64 + min(18.0, abs(min(last_delta, ma20_delta)) * 4.0) + (8 if volume_ratio >= 1.4 else 0)
        transition.update({
            "transitionType": "sideways_to_breakdown",
            "label": "횡보 후 하방 이탈",
            "polarity": "risk",
            "score": min(100.0, score),
            "riskImpact": min(16.0, 7.0 + abs(min(last_delta, ma20_delta)) * 1.8),
            "relationHint": "BREAKS_CONSOLIDATION",
        })
    elif prev == "rising" and curr in {"weakening", "falling", "sideways"} and (last_delta <= -1.0 or ma20_delta <= -1.2):
        score = 60 + min(18.0, abs(min(last_delta, ma20_delta)) * 4.0)
        transition.update({
            "transitionType": "rising_to_distribution",
            "label": "상승 후 탄력 둔화/분배",
            "polarity": "risk",
            "score": min(100.0, score),
            "riskImpact": min(15.0, 6.0 + abs(min(last_delta, ma20_delta)) * 1.6),
            "relationHint": "INDICATES_DECELERATION",
        })
    elif prev == "falling" and curr == "falling" and (last_delta <= -1.5 or ma20_delta <= -2.0):
        score = 62 + min(20.0, abs(min(last_delta, ma20_delta)) * 4.0)
        transition.update({
            "transitionType": "falling_acceleration",
            "label": "하락 가속",
            "polarity": "risk",
            "score": min(100.0, score),
            "riskImpact": min(18.0, 8.0 + abs(min(last_delta, ma20_delta)) * 1.8),
            "relationHint": "INDICATES_ACCELERATION",
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
        **transition,
    }
