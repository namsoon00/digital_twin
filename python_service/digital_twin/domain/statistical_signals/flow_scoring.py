"""Bounded investor-flow diagnostics over immutable temporal features."""

from __future__ import annotations

import math
from statistics import mean
from typing import Dict, Iterable, List, Mapping

from ..market_time_series import parse_timestamp
from ..time_series_storage import TemporalFeatureSnapshot
from .contracts import ModelSignal, ModelSignalSnapshot, SignalEligibility
from ..hypothesis_catalog import hypothesis_family_definition
from .registry import DEFAULT_FLOW_SIGNAL_RELEASE_ID, model_release, signal_hypothesis_family


FLOW_FIELDS = (
    "foreignNetVolume",
    "institutionNetVolume",
    "tradeStrength",
    "bidAskImbalance",
)


def _number(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _bounded(value: object, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, _number(value)))


def _first(row: Mapping[str, object], *keys: str):
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _observed_at(row: Mapping[str, object]) -> str:
    return str(_first(row, "bucketAt", "bucket_at", "generatedAt", "observedAt", "observed_at") or "")


def _ordered_daily_rows(rows: Iterable[Mapping[str, object]]) -> List[Dict[str, object]]:
    """Collapse repeated intraday cumulative KIS values to one sample per day."""

    by_day: Dict[str, Dict[str, object]] = {}
    for raw in rows or []:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        stamp = _observed_at(row)
        day = stamp[:10] if len(stamp) >= 10 else stamp
        key = day or "undated"
        previous = by_day.get(key)
        if previous is None or _observed_at(previous) <= stamp:
            by_day[key] = row
    return sorted(by_day.values(), key=_observed_at)


def _flow_metrics(
    windows: Mapping[str, object],
    minimum_samples: int,
    cutoff_at: object = "",
) -> Dict[str, object]:
    source_rows = []
    for name in ("20D", "5D", "3D", "1D"):
        if isinstance(windows.get(name), list) and windows.get(name):
            source_rows = list(windows.get(name) or [])
            break
    cutoff = parse_timestamp(cutoff_at)
    bounded_rows = []
    for row in source_rows:
        stamp = parse_timestamp(_observed_at(row))
        if not cutoff or (stamp and stamp <= cutoff):
            bounded_rows.append(row)
    rows = _ordered_daily_rows(bounded_rows)
    usable = [
        row for row in rows
        if any(field in row and row.get(field) not in (None, "") for field in FLOW_FIELDS)
    ]
    if not usable:
        return {}

    ratios = []
    positive = 0
    negative = 0
    for row in usable:
        smart_money_value = _first(row, "smartMoneyNetVolume", "smart_money_net_volume")
        smart_money = _number(smart_money_value)
        if smart_money_value in (None, ""):
            smart_money = (
                _number(_first(row, "foreignNetVolume", "foreign_net_volume"))
                + _number(_first(row, "institutionNetVolume", "institution_net_volume"))
            )
        volume = abs(_number(_first(row, "volume", "tradingVolume")))
        denominator = max(1.0, volume, abs(smart_money))
        ratio = smart_money / denominator
        ratios.append(ratio)
        positive += int(ratio > 0)
        negative += int(ratio < 0)

    latest = usable[-1]
    latest_ratio = ratios[-1]
    mean_ratio = mean(ratios) if ratios else 0.0
    persistence = max(positive, negative) / max(1.0, float(len(ratios)))
    dominant_sign = 1.0 if positive > negative else -1.0 if negative > positive else 0.0
    prices = [
        _number(_first(row, "currentPrice", "current_price", "close", "closePrice"))
        for row in usable
    ]
    prices = [value for value in prices if value > 0]
    price_return = (prices[-1] / prices[0] - 1.0) if len(prices) >= 2 else 0.0
    trade_strength = _number(_first(latest, "tradeStrength", "trade_strength"))
    bid_ask = _number(_first(latest, "bidAskImbalance", "bid_ask_imbalance")) / 100.0
    volume_ratio = _number(_first(
        latest,
        "timeAdjustedVolumeRatio",
        "volumeRatio",
        "rawVolumeRatio",
    ))
    quality_text = " ".join(str(_first(latest, key) or "") for key in (
        "dataQuality", "freshnessStatus", "investorFlowDataState",
    )).lower()
    stale = any(value in quality_text for value in ("stale", "invalid", "missing", "error", "unavailable"))
    present_fields = sum(
        1 for field in FLOW_FIELDS
        if field in latest and latest.get(field) not in (None, "")
    )
    return {
        "sampleCount": len(usable),
        "coverageRatio": _bounded(len(usable) / max(1.0, float(minimum_samples))),
        "fieldCoverageRatio": _bounded(present_fields / float(len(FLOW_FIELDS))),
        "latestSmartMoneyVolumeRatio": latest_ratio,
        "meanSmartMoneyVolumeRatio": mean_ratio,
        "flowSignPersistence": persistence,
        "dominantFlowSign": dominant_sign,
        "priceReturn": price_return,
        "tradeStrength": trade_strength,
        "bidAskImbalance": bid_ask,
        "volumeRatio": volume_ratio,
        "latestObservedAt": _observed_at(latest),
        "stale": stale,
    }


def _eligibility(metrics: Mapping[str, object], release) -> SignalEligibility:
    reasons = []
    if int(metrics.get("sampleCount") or 0) < int(release.minimum_samples or 1):
        reasons.append("minimum-independent-daily-sample-count-not-met")
    if _number(metrics.get("coverageRatio")) < float(release.minimum_coverage_ratio or 0):
        reasons.append("minimum-coverage-not-met")
    if _number(metrics.get("fieldCoverageRatio")) < 0.75:
        reasons.append("flow-field-coverage-insufficient")
    if bool(metrics.get("stale")):
        reasons.append("latest-observation-stale")
    if release.validation_status not in {"calibrated", "validated-deterministic"}:
        reasons.append("historical-replay-and-calibration-required")
    quality = "stale" if metrics.get("stale") else "sufficient" if len(reasons) == 1 else "insufficient"
    return SignalEligibility.create(
        "reference-only" if release.decision_eligibility == "reference-only"
        else "ineligible" if reasons
        else "conditional" if release.decision_eligibility == "conditional"
        else "eligible",
        reasons,
        data_quality=quality,
        validation_status=release.validation_status,
        decision_eligibility=release.decision_eligibility,
    )


def _positive(value: float, scale: float) -> float:
    return _bounded(max(0.0, value) / max(1e-8, scale))


def _scores(metrics: Mapping[str, object]) -> Dict[str, float]:
    latest = _number(metrics.get("latestSmartMoneyVolumeRatio"))
    average = _number(metrics.get("meanSmartMoneyVolumeRatio"))
    persistence = _number(metrics.get("flowSignPersistence"))
    dominant = _number(metrics.get("dominantFlowSign"))
    price_return = _number(metrics.get("priceReturn"))
    trade = (_number(metrics.get("tradeStrength")) - 100.0) / 40.0
    bid_ask = _number(metrics.get("bidAskImbalance"))
    positive_persistence = persistence if dominant > 0 else 0.0
    negative_persistence = persistence if dominant < 0 else 0.0
    accumulation = (
        0.30 * _positive(latest, 0.05)
        + 0.25 * _positive(average, 0.03)
        + 0.20 * positive_persistence
        + 0.15 * _positive(trade, 1.0)
        + 0.10 * _positive(bid_ask, 0.25)
    )
    distribution = (
        0.30 * _positive(-latest, 0.05)
        + 0.25 * _positive(-average, 0.03)
        + 0.20 * negative_persistence
        + 0.15 * _positive(-trade, 1.0)
        + 0.10 * _positive(-bid_ask, 0.25)
    )
    divergence = (
        0.55 * _positive(price_return, 0.08)
        + 0.30 * _positive(-average, 0.03)
        + 0.15 * negative_persistence
    ) if price_return > 0 and (average < 0 or latest < 0) else 0.0
    return {
        "flow-accumulation-support": round(_bounded(accumulation), 6),
        "flow-distribution-risk": round(_bounded(distribution), 6),
        "flow-price-divergence-risk": round(_bounded(divergence), 6),
    }


def score_flow_feature_snapshot(
    snapshot: TemporalFeatureSnapshot,
    release_id: object = DEFAULT_FLOW_SIGNAL_RELEASE_ID,
) -> ModelSignalSnapshot:
    release = model_release(release_id)
    signals = []
    latest_observed_at = ""
    for symbol, windows in sorted(dict(snapshot.windows or {}).items()):
        metrics = _flow_metrics(dict(windows or {}), release.minimum_samples, snapshot.as_of)
        if not metrics:
            continue
        latest_observed_at = max(latest_observed_at, str(metrics.get("latestObservedAt") or ""))
        eligibility = _eligibility(metrics, release)
        confidence = _bounded(
            _number(metrics.get("coverageRatio"))
            * _number(metrics.get("fieldCoverageRatio"))
            * (0.6 if metrics.get("stale") else 1.0)
        )
        for signal_type, score in _scores(metrics).items():
            hypothesis_family_id = signal_hypothesis_family(signal_type)
            hypothesis_family = hypothesis_family_definition(hypothesis_family_id)
            signals.append(ModelSignal.create(
                signal_type=signal_type,
                signal_family=release.model_family,
                subject_id=symbol,
                horizon="20D",
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
                input_features=metrics,
                probability=None,
                hypothesis_family_id=hypothesis_family_id,
                outcome_metric=hypothesis_family.outcome_metric if hypothesis_family else "",
                knowledge_cutoff_at=str(snapshot.as_of or metrics.get("latestObservedAt") or ""),
                uncertainty_status=(
                    "score-only" if release.validation_status == "validated-deterministic"
                    else "uncalibrated"
                ),
            ))
    return ModelSignalSnapshot.create(
        account_id=snapshot.account_id,
        as_of=snapshot.as_of or latest_observed_at,
        source_feature_snapshot_id=snapshot.snapshot_id,
        feature_set_version=snapshot.feature_set_version,
        model_release_id=release.release_id,
        signals=signals,
        subjects=snapshot.symbols,
    )
