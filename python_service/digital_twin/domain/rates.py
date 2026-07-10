from dataclasses import asdict, dataclass
from typing import Dict

from .market_data import number


@dataclass(frozen=True)
class InterestRateContext:
    dgs10: float = 0.0
    dgs2: float = 0.0
    dff: float = 0.0
    yield_spread_10y_2y: float = 0.0
    rate_regime: str = "neutral_rate"
    yield_curve_regime: str = "flat_or_unknown_curve"
    has_interest_rate_signals: bool = False
    has_macro_signals: bool = False

    def to_facts(self) -> Dict[str, object]:
        return {
            "macroYieldSpread10y2y": self.yield_spread_10y_2y,
            "macroDgs10": self.dgs10,
            "macroDgs2": self.dgs2,
            "macroDff": self.dff,
            "rateRegime": self.rate_regime,
            "yieldCurveRegime": self.yield_curve_regime,
            "hasInterestRateSignals": self.has_interest_rate_signals,
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


def interest_rate_context_from_signals(external_signals: Dict[str, object]) -> InterestRateContext:
    macro = external_signals.get("macro") if isinstance(external_signals, dict) and isinstance(external_signals.get("macro"), dict) else {}
    series = macro.get("series") if isinstance(macro.get("series"), dict) else {}
    dgs10 = number((series.get("DGS10") or {}).get("value")) if isinstance(series.get("DGS10"), dict) else 0.0
    dgs2 = number((series.get("DGS2") or {}).get("value")) if isinstance(series.get("DGS2"), dict) else 0.0
    dff = number((series.get("DFF") or {}).get("value")) if isinstance(series.get("DFF"), dict) else 0.0
    spread_present = macro.get("yieldSpread10y2y") not in (None, "")
    spread = number(macro.get("yieldSpread10y2y"))
    return InterestRateContext(
        dgs10=dgs10,
        dgs2=dgs2,
        dff=dff,
        yield_spread_10y_2y=spread,
        rate_regime=rate_regime_for_dgs10(dgs10),
        yield_curve_regime=yield_curve_regime_for_spread(spread),
        has_interest_rate_signals=bool(dgs10 or dgs2 or dff or spread_present),
        has_macro_signals=bool(series or spread_present),
    )


def interest_rate_facts(external_signals: Dict[str, object]) -> Dict[str, object]:
    return interest_rate_context_from_signals(external_signals).to_facts()
