"""Descriptive event-window measurements, never causal or trade conclusions."""

import math
from datetime import datetime, timedelta, timezone


def exact_time(value):
    text = str(value or "")
    if "T" not in text:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except ValueError:
        return None


def price_observation(symbol, event_at, horizon_minutes, baseline, outcome, now=None):
    now = now or datetime.now(timezone.utc)
    event = exact_time(event_at)
    result = {"symbol": symbol, "horizonMinutes": horizon_minutes, "status": "missing", "label": "해당 구간의 시세 기록 부족"}
    if not event:
        return {**result, "status": "unknown-time", "label": "발표 시각 미확인"}
    target = event + timedelta(minutes=horizon_minutes)
    result.update(targetAt=target.isoformat().replace("+00:00", "Z"))
    if target > now:
        return {**result, "status": "pending", "label": "관측 시점 전"}
    def quote(row, before):
        row = row or {}
        source = exact_time(row.get("sourceAsOf"))
        received = exact_time(row.get("generatedAt"))
        try:
            price = float(row.get("currentPrice"))
        except (ValueError, TypeError):
            return None
        if row.get("symbol") != symbol or not source or not received or received > now or source > received or not math.isfinite(price) or price <= 0:
            return None
        if row.get("observationGranularity") not in {"3m", "15m", "1h"} or str(row.get("dataQuality") or "").lower() in {"mock", "synthetic", "invalid", "error"}:
            return None
        if before and not (event - timedelta(hours=24) <= source <= event and received <= event):
            return None
        if not before and not (target <= source <= min(now, target + timedelta(hours=3))):
            return None
        return {"price": price, "sourceAsOf": row["sourceAsOf"], "recordedAt": row["generatedAt"], "provider": str(row.get("provider") or ""), "currency": str(row.get("currency") or "")}
    before, after = quote(baseline, True), quote(outcome, False)
    if not before or not after or not before["currency"] or before["currency"] != after["currency"] or not before["provider"] or before["provider"] != after["provider"]:
        return result
    return {**result, "status": "observed", "label": "구간 전후 가격 관측", "baseline": before, "outcome": after,
            "priceChangePercent": round((after["price"] / before["price"] - 1) * 100, 4),
            "baselineAgeMinutes": round((event - exact_time(before["sourceAsOf"])).total_seconds() / 60, 1),
            "targetDelayMinutes": round((exact_time(after["sourceAsOf"]) - target).total_seconds() / 60, 1)}
