"""Bind a selected volume ratio to its own observation, never the quote clock."""

from collections.abc import Mapping
import math

from digital_twin.modules.market_data.domain.volume_time_adjustment import parse_observed_at, volume_pace_snapshot


def position_volume_pace_snapshot(position, trading_value=None):
    def get(snake, camel):
        if isinstance(position, Mapping):
            return position.get(snake, position.get(camel))
        return getattr(position, snake, None)

    ratio = get("volume_ratio", "volumeRatio")
    coverage = get("market_signal_coverage", "marketSignalCoverage") or {}
    source = coverage.get("volume") or {}
    values = source.get("values") or {}
    try:
        matching = math.isfinite(float(ratio)) and math.isclose(
            float(ratio), float(values.get("volumeRatio")), rel_tol=1e-9, abs_tol=1e-9)
    except (TypeError, ValueError):
        matching = False
    at = source.get("sourceAsOf") if matching else ""
    timestamp_state = str(source.get("sourceTimestampState") or "missing")
    usable_clock = timestamp_state in {"provider-execution", "provider-candle", "provider-timestamp"}
    observed, fetched = parse_observed_at(at), parse_observed_at(source.get("fetchedAt"))
    if observed and fetched and observed > fetched:
        usable_clock = False
    result = volume_pace_snapshot(
        get("market", "market"), ratio, volume=get("volume", "volume"),
        trading_value=trading_value if trading_value is not None else get("trading_value", "tradingValue"),
        observed_at=at if usable_clock else "",
        measurement_scope=source.get("measurementScope", "") if matching else "",
        ratio_basis=source.get("ratioBasis", "") if matching else "",
        source_session=source.get("marketSession", "") if matching else "",
    )
    result.update({
        "volumePaceSourceAsOf": str(at or ""),
        "volumePaceSourceTimestampState": timestamp_state,
        "volumePaceProvider": str(source.get("provider") or "") if matching else "",
    })
    if matching:
        for source_key, fact_key in (("numeratorVolume", "volumeRatioNumerator"),
                                     ("denominatorVolume", "volumeRatioDenominator"),
                                     ("sampleCount", "volumeRatioSampleCount")):
            if source.get(source_key) is not None:
                result[fact_key] = source[source_key]
    unavailable = (source.get("status") in {"unavailable", "missing", "stale", "expired"}
                   or source.get("freshnessStatus") in {"stale", "expired"})
    if not matching or not usable_clock or unavailable:
        result.pop("timeAdjustedVolumeRatio", None)
        result.pop("expectedVolumeRatioNow", None)
        result.update({"volumePaceStatus": "unavailable", "volumePaceLabel": "거래량 근거 확인 필요",
                       "volumePaceBasis": "거래량 비율의 원천 값·기준 시각이 확인되지 않아 시간 보정을 하지 않음"})
    return result
