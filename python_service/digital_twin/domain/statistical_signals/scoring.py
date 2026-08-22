"""Pure, bounded statistical scoring over immutable temporal windows."""

from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from ..time_series_storage import TemporalFeatureSnapshot
from .contracts import ModelSignal, ModelSignalSnapshot, SignalEligibility
from ..hypothesis_catalog import hypothesis_family_definition
from .registry import DEFAULT_PRICE_SIGNAL_RELEASE_ID, model_release, signal_hypothesis_family


WINDOW_MINIMUM_SAMPLES = {
    "1D": 2,
    "3D": 3,
    "5D": 4,
    "20D": 5,
}


def _number(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _bounded(value: object, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, _number(value)))


def _first(row: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _ordered_rows(rows: Iterable[Mapping[str, object]]) -> List[Dict[str, object]]:
    result = [dict(item) for item in rows or [] if isinstance(item, Mapping)]
    return sorted(
        result,
        key=lambda row: str(_first(row, "bucketAt", "bucket_at", "generatedAt", "observed_at") or ""),
    )


def _prices(rows: Sequence[Mapping[str, object]]) -> List[float]:
    values = []
    for row in rows or []:
        value = _number(_first(row, "currentPrice", "current_price", "close", "closePrice"))
        if value > 0:
            values.append(value)
    return values


def _returns(prices: Sequence[float]) -> List[float]:
    return [
        (current / previous) - 1.0
        for previous, current in zip(prices, prices[1:])
        if previous > 0 and current > 0
    ]


def _change(start: float, end: float) -> float:
    return ((end / start) - 1.0) if start > 0 and end > 0 else 0.0


def _linear_slope_ratio(prices: Sequence[float]) -> float:
    if len(prices) < 2:
        return 0.0
    center_x = (len(prices) - 1) / 2.0
    center_y = mean(prices)
    denominator = sum((index - center_x) ** 2 for index in range(len(prices)))
    if denominator <= 0 or center_y <= 0:
        return 0.0
    slope = sum(
        (index - center_x) * (price - center_y)
        for index, price in enumerate(prices)
    ) / denominator
    return slope / center_y


def _window_metrics(rows: Iterable[Mapping[str, object]], minimum_samples: int) -> Dict[str, object]:
    ordered = _ordered_rows(rows)
    prices = _prices(ordered)
    returns = _returns(prices)
    volatility = pstdev(returns) if len(returns) >= 2 else 0.0
    split = max(1, len(prices) // 2)
    prior_return = _change(prices[0], prices[split]) if len(prices) >= 2 else 0.0
    recent_return = _change(prices[split], prices[-1]) if len(prices) >= 2 else 0.0
    latest = ordered[-1] if ordered else {}
    qualities = " ".join([
        str(_first(latest, "dataQuality", "data_quality") or ""),
        str(_first(latest, "freshnessStatus", "freshness_status") or ""),
    ]).lower()
    stale = any(value in qualities for value in ("stale", "invalid", "missing", "error", "unavailable"))
    peak = max(prices) if prices else 0.0
    trough = min(prices) if prices else 0.0
    return {
        "sampleCount": len(prices),
        "coverageRatio": _bounded(len(prices) / max(1.0, float(minimum_samples))),
        "priceReturn": _change(prices[0], prices[-1]) if prices else 0.0,
        "currentPrice": prices[-1] if prices else 0.0,
        "priorReturn": prior_return,
        "recentReturn": recent_return,
        "velocityChange": recent_return - prior_return,
        "realizedVolatility": volatility,
        "slopeRatio": _linear_slope_ratio(prices),
        "drawdown": _change(peak, prices[-1]) if prices else 0.0,
        "rebound": _change(trough, prices[-1]) if prices else 0.0,
        "ma20Distance": _number(_first(latest, "ma20Distance", "ma20_distance")) / 100.0,
        "ma60Distance": _number(_first(latest, "ma60Distance", "ma60_distance")) / 100.0,
        "latestObservedAt": str(_first(latest, "bucketAt", "bucket_at", "generatedAt", "observed_at") or ""),
        "stale": stale,
    }


def _signal_eligibility(metrics: Mapping[str, object], release) -> SignalEligibility:
    reasons = []
    sample_count = int(metrics.get("sampleCount") or 0)
    coverage = _number(metrics.get("coverageRatio"))
    if sample_count < int(release.minimum_samples or 1):
        reasons.append("minimum-sample-count-not-met")
    if coverage < float(release.minimum_coverage_ratio or 0):
        reasons.append("minimum-coverage-not-met")
    if bool(metrics.get("stale")):
        reasons.append("latest-observation-stale")
    if release.validation_status != "calibrated":
        reasons.append("historical-replay-and-calibration-required")
    quality = "stale" if metrics.get("stale") else "sufficient" if not reasons[:2] else "insufficient"
    return SignalEligibility.create(
        "reference-only" if release.decision_eligibility == "reference-only" else "ineligible" if reasons else "eligible",
        reasons,
        data_quality=quality,
        validation_status=release.validation_status,
        decision_eligibility=release.decision_eligibility,
    )


def _normalized_positive(value: float, scale: float) -> float:
    return _bounded(max(0.0, value) / max(1e-8, scale))


def _score_components(metrics: Mapping[str, object]) -> Dict[str, float]:
    volatility = max(0.005, _number(metrics.get("realizedVolatility")))
    price_return = _number(metrics.get("priceReturn"))
    recent = _number(metrics.get("recentReturn"))
    velocity = _number(metrics.get("velocityChange"))
    slope = _number(metrics.get("slopeRatio"))
    drawdown = _number(metrics.get("drawdown"))
    rebound = _number(metrics.get("rebound"))
    ma20 = _number(metrics.get("ma20Distance"))
    ma60 = _number(metrics.get("ma60Distance"))
    trend_support = (
        0.30 * _normalized_positive(price_return, volatility * 4.0)
        + 0.25 * _normalized_positive(slope, volatility)
        + 0.25 * _normalized_positive(ma20, max(0.02, volatility * 2.0))
        + 0.20 * _normalized_positive(ma60, max(0.03, volatility * 3.0))
    )
    trend_break = (
        0.30 * _normalized_positive(-recent, volatility * 2.0)
        + 0.25 * _normalized_positive(-velocity, volatility * 3.0)
        + 0.25 * _normalized_positive(-ma20, max(0.02, volatility * 2.0))
        + 0.20 * _normalized_positive(-drawdown, max(0.03, volatility * 3.0))
    )
    downside_acceleration = (
        0.55 * _normalized_positive(-velocity, volatility * 3.0)
        + 0.25 * _normalized_positive(-recent, volatility * 2.0)
        + 0.20 * _normalized_positive(-slope, volatility)
    )
    recovery = (
        0.35 * _normalized_positive(rebound, max(0.03, volatility * 3.0))
        + 0.30 * _normalized_positive(recent, volatility * 2.0)
        + 0.20 * _normalized_positive(velocity, volatility * 3.0)
        + 0.15 * _normalized_positive(ma20, max(0.02, volatility * 2.0))
    )
    return {
        "price-trend-continuation-support": round(_bounded(trend_support), 6),
        "price-trend-break-risk": round(_bounded(trend_break), 6),
        "price-downside-acceleration-risk": round(_bounded(downside_acceleration), 6),
        "price-recovery-support": round(_bounded(recovery), 6),
    }


def _combined_metrics(windows: Mapping[str, object]) -> Dict[str, object]:
    metrics = {
        key: _window_metrics(windows.get(key) or [], WINDOW_MINIMUM_SAMPLES[key])
        for key in WINDOW_MINIMUM_SAMPLES
        if isinstance(windows.get(key), list)
    }
    primary = metrics.get("20D") or metrics.get("5D") or metrics.get("3D") or metrics.get("1D") or {}
    short = metrics.get("5D") or metrics.get("3D") or primary
    if not primary:
        return {}
    combined = dict(primary)
    for key in ("recentReturn", "velocityChange", "realizedVolatility", "slopeRatio", "rebound", "drawdown"):
        if key in short:
            combined[key] = short[key]
    combined["coverageRatio"] = min(
        _number(primary.get("coverageRatio")),
        _number(short.get("coverageRatio")),
    )
    combined["sampleCount"] = max(
        int(primary.get("sampleCount") or 0),
        int(short.get("sampleCount") or 0),
    )
    combined["stale"] = bool(primary.get("stale") or short.get("stale"))
    combined["windowMetrics"] = metrics
    return combined


def score_temporal_feature_snapshot(
    snapshot: TemporalFeatureSnapshot,
    release_id: object = DEFAULT_PRICE_SIGNAL_RELEASE_ID,
) -> ModelSignalSnapshot:
    release = model_release(release_id)
    signals = []
    latest_observed_at = ""
    for symbol, windows in sorted(dict(snapshot.windows or {}).items()):
        metrics = _combined_metrics(dict(windows or {}))
        if not metrics:
            continue
        latest_observed_at = max(latest_observed_at, str(metrics.get("latestObservedAt") or ""))
        eligibility = _signal_eligibility(metrics, release)
        scores = _score_components(metrics)
        compact_features = {
            key: value
            for key, value in metrics.items()
            if key != "windowMetrics"
        }
        compact_features["windowMetrics"] = {
            key: {
                field: value
                for field, value in value.items()
                if field in {
                    "sampleCount", "coverageRatio", "priceReturn", "recentReturn",
                    "currentPrice",
                    "velocityChange", "realizedVolatility", "slopeRatio", "drawdown",
                    "rebound", "ma20Distance", "ma60Distance", "latestObservedAt", "stale",
                }
            }
            for key, value in dict(metrics.get("windowMetrics") or {}).items()
        }
        confidence = _bounded(
            _number(metrics.get("coverageRatio"))
            * (0.6 if metrics.get("stale") else 1.0)
        )
        for signal_type, score in scores.items():
            hypothesis_family_id = signal_hypothesis_family(signal_type)
            hypothesis_family = hypothesis_family_definition(hypothesis_family_id)
            signals.append(ModelSignal.create(
                signal_type=signal_type,
                signal_family="price-path-statistics",
                subject_id=symbol,
                horizon="5D" if signal_type != "price-downside-acceleration-risk" else "3D",
                polarity="risk" if signal_type.endswith("risk") else "support",
                score=score,
                confidence=confidence,
                observed_at=str(metrics.get("latestObservedAt") or snapshot.as_of),
                source_feature_snapshot_id=snapshot.snapshot_id,
                feature_set_version=snapshot.feature_set_version,
                model_release_id=release.release_id,
                sample_count=metrics.get("sampleCount") or 0,
                coverage_ratio=metrics.get("coverageRatio") or 0,
                eligibility=eligibility,
                input_features=compact_features,
                probability=None,
                hypothesis_family_id=hypothesis_family_id,
                outcome_metric=hypothesis_family.outcome_metric if hypothesis_family else "",
                knowledge_cutoff_at=str(metrics.get("latestObservedAt") or snapshot.as_of),
                uncertainty_status="uncalibrated",
            ))
    return ModelSignalSnapshot.create(
        account_id=snapshot.account_id,
        as_of=latest_observed_at or snapshot.as_of,
        source_feature_snapshot_id=snapshot.snapshot_id,
        feature_set_version=snapshot.feature_set_version,
        model_release_id=release.release_id,
        signals=signals,
        subjects=snapshot.symbols,
    )
