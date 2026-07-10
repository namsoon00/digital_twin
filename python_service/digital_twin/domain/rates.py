from dataclasses import asdict, dataclass
from typing import Dict

from .market_data import number, optional_number


@dataclass(frozen=True)
class InterestRateContext:
    dgs10: float = 0.0
    dgs2: float = 0.0
    dff: float = 0.0
    yield_spread_10y_2y: float = 0.0
    dgs10_delta_bp: float = 0.0
    dgs2_delta_bp: float = 0.0
    dff_delta_bp: float = 0.0
    yield_spread_delta_bp: float = 0.0
    has_dgs10_delta: bool = False
    has_dgs2_delta: bool = False
    has_dff_delta: bool = False
    has_yield_spread_delta: bool = False
    rate_regime: str = "neutral_rate"
    yield_curve_regime: str = "flat_or_unknown_curve"
    has_interest_rate_signals: bool = False
    has_interest_rate_delta_signal: bool = False
    has_macro_signals: bool = False

    def to_facts(self) -> Dict[str, object]:
        return {
            "macroYieldSpread10y2y": self.yield_spread_10y_2y,
            "macroDgs10": self.dgs10,
            "macroDgs2": self.dgs2,
            "macroDff": self.dff,
            "macroDgs10DeltaBp": self.dgs10_delta_bp,
            "macroDgs2DeltaBp": self.dgs2_delta_bp,
            "macroDffDeltaBp": self.dff_delta_bp,
            "macroYieldSpreadDeltaBp": self.yield_spread_delta_bp,
            "hasMacroDgs10Delta": self.has_dgs10_delta,
            "hasMacroDgs2Delta": self.has_dgs2_delta,
            "hasMacroDffDelta": self.has_dff_delta,
            "hasMacroYieldSpreadDelta": self.has_yield_spread_delta,
            "rateRegime": self.rate_regime,
            "yieldCurveRegime": self.yield_curve_regime,
            "hasInterestRateSignals": self.has_interest_rate_signals,
            "hasInterestRateDeltaSignal": self.has_interest_rate_delta_signal,
            "hasMacroSignals": self.has_macro_signals,
        }

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def rate_regime_for_dgs10(dgs10: float) -> str:
    value = number(dgs10)
    if value >= 4.5:
        return "high_rate"
    if value and value <= 3.0:
        return "low_rate"
    return "neutral_rate"


def yield_curve_regime_for_spread(spread: float) -> str:
    value = number(spread)
    if value < 0:
        return "inverted_curve"
    if value > 0:
        return "positive_curve"
    return "flat_or_unknown_curve"


def _series_delta_bp(item: Dict[str, object]):
    if not isinstance(item, dict):
        return None
    explicit = optional_number(item, [
        "deltaBp",
        "changeBp",
        "deltaBasisPoints",
        "changeBasisPoints",
    ])
    if explicit is not None:
        return explicit
    pct_point = optional_number(item, [
        "deltaPctPoint",
        "changePctPoint",
        "deltaPercentPoint",
        "changePercentPoint",
    ])
    if pct_point is not None:
        return pct_point * 100
    current_value = optional_number(item, ["value", "rate"])
    previous_value = optional_number(item, ["previousValue", "previousRate", "previous"])
    if current_value is not None and previous_value is not None:
        return (current_value - previous_value) * 100
    return None


def _macro_delta_bp(macro: Dict[str, object], current_key: str, explicit_keys):
    if not isinstance(macro, dict):
        return None
    explicit = optional_number(macro, list(explicit_keys))
    if explicit is not None:
        return explicit
    current_value = optional_number(macro, [current_key])
    previous_value = optional_number(macro, ["previous" + current_key[:1].upper() + current_key[1:]])
    if current_value is not None and previous_value is not None:
        return (current_value - previous_value) * 100
    return None


def interest_rate_context_from_signals(external_signals: Dict[str, object]) -> InterestRateContext:
    macro = external_signals.get("macro") if isinstance(external_signals, dict) and isinstance(external_signals.get("macro"), dict) else {}
    series = macro.get("series") if isinstance(macro.get("series"), dict) else {}
    dgs10_item = series.get("DGS10") if isinstance(series.get("DGS10"), dict) else {}
    dgs2_item = series.get("DGS2") if isinstance(series.get("DGS2"), dict) else {}
    dff_item = series.get("DFF") if isinstance(series.get("DFF"), dict) else {}
    dgs10 = number(dgs10_item.get("value")) if isinstance(dgs10_item, dict) else 0.0
    dgs2 = number(dgs2_item.get("value")) if isinstance(dgs2_item, dict) else 0.0
    dff = number(dff_item.get("value")) if isinstance(dff_item, dict) else 0.0
    dgs10_delta_bp = _series_delta_bp(dgs10_item)
    dgs2_delta_bp = _series_delta_bp(dgs2_item)
    dff_delta_bp = _series_delta_bp(dff_item)
    spread_present = macro.get("yieldSpread10y2y") not in (None, "")
    spread = number(macro.get("yieldSpread10y2y"))
    spread_delta_bp = _macro_delta_bp(macro, "yieldSpread10y2y", [
        "yieldSpread10y2yDeltaBp",
        "yieldSpreadDeltaBp",
        "spreadDeltaBp",
    ])
    has_delta = any(value is not None for value in [dgs10_delta_bp, dgs2_delta_bp, dff_delta_bp, spread_delta_bp])
    return InterestRateContext(
        dgs10=dgs10,
        dgs2=dgs2,
        dff=dff,
        yield_spread_10y_2y=spread,
        dgs10_delta_bp=dgs10_delta_bp or 0.0,
        dgs2_delta_bp=dgs2_delta_bp or 0.0,
        dff_delta_bp=dff_delta_bp or 0.0,
        yield_spread_delta_bp=spread_delta_bp or 0.0,
        has_dgs10_delta=dgs10_delta_bp is not None,
        has_dgs2_delta=dgs2_delta_bp is not None,
        has_dff_delta=dff_delta_bp is not None,
        has_yield_spread_delta=spread_delta_bp is not None,
        rate_regime=rate_regime_for_dgs10(dgs10),
        yield_curve_regime=yield_curve_regime_for_spread(spread),
        has_interest_rate_signals=bool(dgs10 or dgs2 or dff or spread_present),
        has_interest_rate_delta_signal=has_delta,
        has_macro_signals=bool(series or spread_present),
    )


def interest_rate_facts(external_signals: Dict[str, object]) -> Dict[str, object]:
    return interest_rate_context_from_signals(external_signals).to_facts()
