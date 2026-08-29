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
)
FLOW_SIGNAL_MAX_SOURCE_AGE_SECONDS = 4 * 24 * 60 * 60


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


def _investor_coverage(row: Mapping[str, object]) -> Dict[str, object]:
    coverage = row.get("marketSignalCoverage") if isinstance(row.get("marketSignalCoverage"), Mapping) else {}
    investor = coverage.get("investor") if isinstance(coverage.get("investor"), Mapping) else {}
    return dict(investor or {})


def _observed_flow_fields(row: Mapping[str, object]) -> set:
    investor = _investor_coverage(row)
    return {
        str(field)
        for field in (investor.get("observedFields") or investor.get("fields") or [])
        if str(field or "").strip()
    }


def _usable_flow_row(row: Mapping[str, object]) -> bool:
    investor = _investor_coverage(row)
    status = str(investor.get("status") or "").lower()
    if status in {"stale", "invalid", "missing", "empty", "unavailable", "error"}:
        return False
    observed = _observed_flow_fields(row)
    return set(FLOW_FIELDS).issubset(observed) and all(
        _first(row, camel, snake) is not None
        for camel, snake in (
            ("foreignNetVolume", "foreign_net_volume"),
            ("institutionNetVolume", "institution_net_volume"),
        )
    )


def _ordered_daily_rows(rows: Iterable[Mapping[str, object]]) -> List[Dict[str, object]]:
    """Collapse repeated intraday cumulative KIS values to one sample per day."""

    by_day: Dict[str, Dict[str, object]] = {}
    for raw in rows or []:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        stamp = _observed_at(row)
        day = str(row.get("marketSessionDate") or row.get("tradingDate") or stamp)[:10]
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
    usable = [row for row in rows if _usable_flow_row(row)]
    if not usable:
        return {}

    ratios = []
    smart_money_values = []
    value_basis = "volume"
    positive = 0
    negative = 0
    for row in usable:
        foreign_amount = _first(row, "foreignNetAmount", "foreign_net_amount")
        institution_amount = _first(row, "institutionNetAmount", "institution_net_amount")
        if foreign_amount not in (None, "") and institution_amount not in (None, ""):
            smart_money = _number(foreign_amount) + _number(institution_amount)
            denominator = max(1.0, abs(_number(_first(row, "tradingValue", "trading_value"))), abs(smart_money))
            value_basis = "amount"
        else:
            smart_money = (
                _number(_first(row, "foreignNetVolume", "foreign_net_volume"))
                + _number(_first(row, "institutionNetVolume", "institution_net_volume"))
            )
            volume = abs(_number(_first(row, "volume", "tradingVolume")))
            denominator = max(1.0, volume, abs(smart_money))
        ratio = smart_money / denominator
        smart_money_values.append(smart_money)
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
    investor_quality = _investor_coverage(latest)
    quality_text = " ".join(str(_first(latest, key) or "") for key in (
        "dataQuality", "freshnessStatus", "investorFlowDataState",
    )).lower() + " " + " ".join(str(investor_quality.get(key) or "") for key in (
        "status", "freshnessStatus", "latencyStatus",
    )).lower()
    stale = any(value in quality_text for value in ("stale", "invalid", "missing", "error", "unavailable"))
    observed_latest = _observed_flow_fields(latest)
    present_fields = sum(1 for field in FLOW_FIELDS if field in observed_latest)
    midpoint = max(1, len(smart_money_values) // 2)
    prior_values = smart_money_values[:midpoint]
    recent_values = smart_money_values[midpoint:] or prior_values
    prior_mean = mean(prior_values) if prior_values else 0.0
    recent_mean = mean(recent_values) if recent_values else 0.0
    latest_observed_at = _observed_at(latest)
    latest_stamp = parse_timestamp(latest_observed_at)
    source_age_seconds = None
    if cutoff and latest_stamp:
        source_age_seconds = max(0, int((cutoff - latest_stamp).total_seconds()))
    freshness_compatible = bool(
        not stale
        and (
            source_age_seconds is None
            or source_age_seconds <= FLOW_SIGNAL_MAX_SOURCE_AGE_SECONDS
        )
    )
    return {
        "sampleCount": len(usable),
        "coverageRatio": _bounded(len(usable) / max(1.0, float(minimum_samples))),
        "fieldCoverageRatio": _bounded(present_fields / float(len(FLOW_FIELDS))),
        "latestSmartMoneyVolumeRatio": latest_ratio,
        "meanSmartMoneyVolumeRatio": mean_ratio,
        "smartMoneyCumulative": round(sum(smart_money_values), 6),
        "smartMoneyAcceleration": round(recent_mean - prior_mean, 6),
        "flowValueBasis": value_basis,
        "flowSignPersistence": persistence,
        "dominantFlowSign": dominant_sign,
        "priceReturn": price_return,
        "tradeStrength": trade_strength,
        "bidAskImbalance": bid_ask,
        "volumeRatio": volume_ratio,
        "latestObservedAt": latest_observed_at,
        "marketSession": str(
            _first(latest, "marketSession", "market_session", "session") or ""
        ).lower(),
        "sourceAgeSeconds": source_age_seconds,
        "maximumSourceAgeSeconds": FLOW_SIGNAL_MAX_SOURCE_AGE_SECONDS,
        "freshnessCompatible": freshness_compatible,
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
    if metrics.get("freshnessCompatible") is False:
        reasons.append("source-age-exceeds-horizon-policy")
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
                market_session=str(metrics.get("marketSession") or ""),
                source_age_seconds=metrics.get("sourceAgeSeconds"),
                freshness_compatible=bool(metrics.get("freshnessCompatible", True)),
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
