from dataclasses import asdict, dataclass
from typing import Dict

from .fx import FxExposureContext, fx_exposure_context
from .portfolio import PortfolioSummary, Position
from .rates import InterestRateContext, interest_rate_context_from_signals


@dataclass(frozen=True)
class MacroContext:
    rates: InterestRateContext
    fx: FxExposureContext

    def to_facts(self) -> Dict[str, object]:
        facts: Dict[str, object] = {}
        facts.update(self.rates.to_facts())
        facts.update(self.fx.to_facts())
        return facts

    def to_dict(self) -> Dict[str, object]:
        return {
            "rates": asdict(self.rates),
            "fx": asdict(self.fx),
        }


def macro_context_for_position(
    position: Position,
    portfolio: PortfolioSummary,
    external_signals: Dict[str, object],
) -> MacroContext:
    return MacroContext(
        rates=interest_rate_context_from_signals(external_signals),
        fx=fx_exposure_context(position, portfolio, external_signals),
    )


def macro_context_facts(
    position: Position,
    portfolio: PortfolioSummary,
    external_signals: Dict[str, object],
) -> Dict[str, object]:
    return macro_context_for_position(position, portfolio, external_signals).to_facts()
