"""Observation quality shared by price-path arithmetic and model features."""

VALID_PRICE_PATH_FEATURE_VERSION = "temporal-features-v3-valid-price-path"


def temporal_observation_quality(row, *, prioritize_invalid=False):
    invalid_states = ("stale", "poor", "invalid", "missing", "unavailable", "error")
    explicit = " ".join(str((row.get(camel) if camel in row else row.get(snake)) or "")
                        for camel, snake in (("dataQuality", "data_quality"),
                                             ("freshnessStatus", "freshness_status"),
                                             ("quoteStatus", "quote_status"))).lower()
    if prioritize_invalid and any(state in explicit for state in invalid_states):
        return "stale"
    quality = " ".join(str((row.get(camel) if camel in row else row.get(snake)) or "") for camel, snake in (
        ("dataQuality", "data_quality"), ("freshnessStatus", "freshness_status"),
        ("quoteStatus", "quote_status"), ("marketSession", "market_session"),
    )).strip().lower()
    if any(state in quality for state in (
        "reference", "last-close", "last close", "previous-close", "previous close",
        "no-tick", "no tick", "market-closed", "market closed", "closed", "off-hours", "off hours",
    )):
        return "reference"
    if any(state in quality for state in invalid_states):
        return "stale"
    if any(state in quality for state in ("actual", "live", "fresh", "verified")):
        return "fresh"
    if any(state in quality for state in ("cached", "manual", "synthetic", "partial")):
        return "partial"
    return "unknown"


def uses_valid_price_path(feature_set_version):
    return str(feature_set_version or "") == VALID_PRICE_PATH_FEATURE_VERSION


def temporal_price_observation_usable(row, *, intraday=False):
    # A closed session cannot make an explicitly invalid measurement usable.
    quality = temporal_observation_quality(row, prioritize_invalid=True)
    return quality != "stale" and (not intraday or quality != "reference")
