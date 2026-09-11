"""Ordered registry for deterministic, instrument-specific valuation models."""

from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, List

from digital_twin.modules.instruments.contracts import instrument_profile_for_position
from digital_twin.modules.market_data.contracts import number
from digital_twin.modules.portfolio.domain.portfolio import Position
from digital_twin.modules.portfolio.domain.valuation.models import apply_review_override, bitcoin_proxy_ai_valuation_row, current_price_anchor_ai_valuation_row, external_fundamental_ai_valuation_row, growth_quality_ai_valuation_row, preferred_income_ai_valuation_row, semiconductor_cycle_ai_valuation_row, truthy


ValuationEvaluator = Callable[[Position, Dict[str, object], Dict[str, object]], Dict[str, object]]


@dataclass(frozen=True)
class ValuationModelDefinition:
    model_id: str
    family: str
    priority: int
    archetypes: FrozenSet[str]
    evaluator: ValuationEvaluator

    def supports(self, instrument_archetypes: FrozenSet[str]) -> bool:
        return bool(self.archetypes.intersection(instrument_archetypes))


DEFAULT_VALUATION_MODEL_REGISTRY = (
    ValuationModelDefinition(
        model_id="preferred-income-yield",
        family="income",
        priority=10,
        archetypes=frozenset({"PreferredIncome", "BitcoinSensitiveIncome"}),
        evaluator=preferred_income_ai_valuation_row,
    ),
    ValuationModelDefinition(
        model_id="bitcoin-treasury-nav",
        family="digital-asset-treasury",
        priority=20,
        archetypes=frozenset({"BitcoinProxy"}),
        evaluator=bitcoin_proxy_ai_valuation_row,
    ),
    ValuationModelDefinition(
        model_id="semiconductor-cycle-earnings",
        family="semiconductor",
        priority=30,
        archetypes=frozenset({"SemiconductorHBM", "SemiconductorCyclical"}),
        evaluator=semiconductor_cycle_ai_valuation_row,
    ),
    ValuationModelDefinition(
        model_id="growth-quality-earnings",
        family="growth",
        priority=40,
        archetypes=frozenset({"PlatformGrowth", "MegaCapQuality", "AIGrowth", "HighVolatilityGrowth"}),
        evaluator=growth_quality_ai_valuation_row,
    ),
)


def registered_valuation_model_rows(
    position: Position,
    external_signals: Dict[str, object],
    settings: Dict[str, object],
) -> List[Dict[str, object]]:
    """Select one primary model, then use explicit fallbacks when necessary."""

    settings = settings if isinstance(settings, dict) else {}
    if not truthy(settings.get("aiValuationAutoProposalEnabled"), True):
        return []
    profile = instrument_profile_for_position(position, settings)
    archetypes = frozenset(profile.archetypes or [])
    rows: List[Dict[str, object]] = []
    for definition in sorted(DEFAULT_VALUATION_MODEL_REGISTRY, key=lambda item: item.priority):
        if not definition.supports(archetypes):
            continue
        row = definition.evaluator(position, external_signals or {}, settings)
        if row:
            rows.append(_tag_model(row, definition))
            break
    if not rows:
        row = external_fundamental_ai_valuation_row(position, external_signals or {}, settings)
        if row and number(row.get("expectedEPS")):
            rows.append(_tag_fallback(row, "generic-fundamental-earnings", "fundamental", 90))
    if not rows and truthy(settings.get("aiValuationCurrentPriceAnchorEnabled"), False):
        row = current_price_anchor_ai_valuation_row(position, settings)
        if row:
            rows.append(_tag_fallback(row, "current-price-reference", "reference-only", 100))
    reviewed = [apply_review_override(row, settings) for row in rows]
    return [row for row in reviewed if str(row.get("activeStatus") or "").strip() != "rejected"]


def _tag_model(row: Dict[str, object], definition: ValuationModelDefinition) -> Dict[str, object]:
    return {
        **row,
        "valuationModelId": definition.model_id,
        "valuationModelFamily": definition.family,
        "valuationModelPriority": definition.priority,
        "valuationModelOrigin": "deterministic",
    }


def _tag_fallback(row: Dict[str, object], model_id: str, family: str, priority: int) -> Dict[str, object]:
    return {
        **row,
        "valuationModelId": model_id,
        "valuationModelFamily": family,
        "valuationModelPriority": priority,
        "valuationModelOrigin": "deterministic",
    }
