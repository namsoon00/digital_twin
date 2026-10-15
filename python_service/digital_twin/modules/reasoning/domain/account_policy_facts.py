"""The existing ABox representation of percentage allocation facts."""

import math


def native_account_weight_pct(value):
    """Match the builder's two-decimal representation, never a rule tolerance."""
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return round(value, 2) if math.isfinite(value) else None
